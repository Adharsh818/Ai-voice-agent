"""Calendar sync: state-based outbox worker against a fake Google Calendar, backoff, Retry, and calendar setup."""

import asyncio
import importlib.util
import os
import re
import tempfile
import unittest
from datetime import datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch

import calendar_sync
import clock
import config
import db
import events
import scheduling
from calendar_sync import CalendarError, CalendarWorker, FakeCalendar
from support import TempClinic

NOW = datetime(2026, 10, 5, 8, 0)          # Monday 08:00 IST
CAL_A = "branch-a@group.calendar.google.com"
CAL_B = "branch-b@group.calendar.google.com"
ROOT = Path(__file__).resolve().parent.parent


def at(day_offset, hh, mm=0):
    return datetime.combine(NOW.date() + timedelta(days=day_offset), time(hh, mm), tzinfo=clock.TZ)


def load_setup_tool():
    spec = importlib.util.spec_from_file_location("setup_calendars", ROOT / "tools" / "setup_calendars.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SyncTestCase(unittest.TestCase):
    def setUp(self):
        self.clinic = TempClinic(now=NOW)
        self.clinic.__enter__()
        self.db = self.clinic.db
        self.db.run_sync(self._second_branch)
        self.cal = FakeCalendar()
        self.worker = CalendarWorker(self.cal, database=self.db)
        self.n = 0
        events.reset()

    def tearDown(self):
        self.clinic.__exit__(None, None, None)
        events.reset()

    @staticmethod
    def _second_branch(conn):
        with db.transaction(conn):
            conn.execute("UPDATE branches SET calendar_id = ? WHERE id = 1", (CAL_A,))
            conn.execute("INSERT INTO branches (id, name, area, calendar_id) VALUES (2, 'Jayanagar', 'Bengaluru', ?)",
                         (CAL_B,))
            conn.execute("INSERT INTO doctors (id, name, spoken_name, gender, branch_id) "
                         "VALUES (2, 'Dr. Other', 'Dr Other', 'male', 2)")
            conn.execute("INSERT INTO doctor_services SELECT 2, id FROM services")
            for wd in range(6):
                conn.execute("INSERT INTO availability_rules (doctor_id, weekday, start_time, end_time) "
                             "VALUES (2, ?, '07:00', '21:00')", (wd,))

    # ------------------------------------------------------------ helpers
    def book(self, start=None, doctor=1, name="Priya Sharma", phone="9876543210"):
        self.n += 1
        result = self.db.run_sync(scheduling.book, service="Consultation", doctor_id=doctor,
                                  start=start or at(1, 10), patient_name=name, phone=phone, idem_key=f"b{self.n}")
        self.assertTrue(result.ok, result.code)
        return result.appointment_id

    def sync(self, now=None):
        return asyncio.run(self.worker.run_once(now=now))

    def outbox(self, appointment_id):
        rows = self.db.run_sync(lambda conn: [dict(r) for r in conn.execute(
            "SELECT * FROM sync_outbox WHERE appointment_id = ?", (appointment_id,))])
        return rows[0] if rows else None

    def appt(self, appointment_id):
        return self.db.run_sync(lambda conn: dict(conn.execute(
            "SELECT * FROM appointments WHERE id = ?", (appointment_id,)).fetchone()))

    def event(self, calendar_id, appointment_id):
        return self.cal.live(calendar_id).get((calendar_id, calendar_sync.event_id_for(appointment_id)))


class EventContentTests(unittest.TestCase):
    APPT = {"id": "0f1e2d3c4b5a69788796a5b4c3d2e1f0", "status": "booked", "version": 3,
            "start_utc": "2026-10-06T04:30:00Z", "end_utc": "2026-10-06T05:00:00Z",
            "caller_phone_e164": "+919876543210", "patient_name": "Priya Sharma", "service": "Consultation",
            "doctor": "Dr. Meera Rao", "branch": "Nagarbhavi"}

    def test_event_id_is_deterministic_and_valid_for_google(self):
        event_id = calendar_sync.event_id_for(self.APPT["id"])
        self.assertEqual(event_id, "emma" + self.APPT["id"])
        self.assertEqual(event_id, calendar_sync.event_id_for(self.APPT["id"]))
        self.assertRegex(event_id, r"^[0-9a-v]{5,1024}$")
        self.assertEqual(calendar_sync.event_id_for("0F1E2D3C-4B5A-6978-8796-A5B4C3D2E1F0"), event_id)

    def test_event_carries_only_the_minimum(self):
        body = calendar_sync.event_body(self.APPT)
        text = body["summary"] + "\n" + body["description"]
        for needed in ["Consultation", "Dr. Meera Rao", "Nagarbhavi", "Priya", "3210", self.APPT["id"]]:
            self.assertIn(needed, text)
        self.assertNotIn("Sharma", text)                # first name only
        self.assertNotIn("98765", text)                 # last 4 digits only
        self.assertEqual(body["start"], {"dateTime": "2026-10-06T10:00:00+05:30", "timeZone": config.CLINIC_TIMEZONE})
        self.assertEqual(body["end"]["dateTime"], "2026-10-06T10:30:00+05:30")
        self.assertEqual(body["status"], "confirmed")
        self.assertFalse(body["summary"].startswith("NEEDS RESCHEDULE"))

    def test_flagged_appointments_get_the_prefix(self):
        body = calendar_sync.event_body(dict(self.APPT, status="needs_reschedule"))
        self.assertTrue(body["summary"].startswith("NEEDS RESCHEDULE: Consultation"))


class WorkerTests(SyncTestCase):
    def test_a_booking_appears_once_and_the_outbox_clears(self):
        appointment_id = self.book()
        self.assertEqual(self.sync(), 1)
        event = self.event(CAL_A, appointment_id)
        self.assertIsNotNone(event)
        self.assertEqual(event["start"]["dateTime"], at(1, 10).isoformat())
        self.assertIsNone(self.outbox(appointment_id))
        appt = self.appt(appointment_id)
        self.assertEqual(appt["calendar_event_id"], f"{CAL_A}/emma{appointment_id}")
        self.assertEqual(appt["calendar_synced_version"], 1)
        self.assertEqual(self.sync(), 0)                # nothing left to do

    def test_a_retried_insert_becomes_a_patch_not_a_duplicate(self):
        appointment_id = self.book()
        # The first attempt reached Google, but its answer was lost.
        self.cal.insert(CAL_A, calendar_sync.event_id_for(appointment_id), {"summary": "stale"})
        self.sync()
        self.assertEqual(len(self.cal.live(CAL_A)), 1)
        self.assertIn("Consultation", self.event(CAL_A, appointment_id)["summary"])
        self.assertIn(("patch", CAL_A, calendar_sync.event_id_for(appointment_id)), self.cal.calls)

    def test_reschedule_moves_the_event(self):
        appointment_id = self.book()
        self.sync()
        self.db.run_sync(scheduling.reschedule, appointment_id, doctor_id=1, start=at(2, 11), idem_key="r1")
        self.sync()
        self.assertEqual(len(self.cal.live(CAL_A)), 1)
        self.assertEqual(self.event(CAL_A, appointment_id)["start"]["dateTime"], at(2, 11).isoformat())
        self.assertEqual(self.appt(appointment_id)["calendar_synced_version"], 2)

    def test_moving_to_another_branch_moves_calendars(self):
        appointment_id = self.book()
        self.sync()
        self.db.run_sync(scheduling.reschedule, appointment_id, doctor_id=2, start=at(1, 12), idem_key="r2")
        self.sync()
        self.assertIsNone(self.event(CAL_A, appointment_id))
        self.assertIsNotNone(self.event(CAL_B, appointment_id))
        self.assertEqual(self.appt(appointment_id)["calendar_event_id"], f"{CAL_B}/emma{appointment_id}")

    def test_cancel_deletes_and_repeated_deletes_are_fine(self):
        appointment_id = self.book()
        self.sync()
        self.db.run_sync(scheduling.cancel, appointment_id, idem_key="c1")
        self.sync()
        self.assertIsNone(self.event(CAL_A, appointment_id))
        self.assertIsNone(self.outbox(appointment_id))
        self.assertIsNone(self.appt(appointment_id)["calendar_event_id"])
        # Deleting an event that is already gone (410) or never existed (404) counts as done.
        self.db.run_sync(calendar_sync.retry, appointment_id, "staff")
        self.sync()
        self.assertIsNone(self.outbox(appointment_id))
        never_synced = self.book(start=at(3, 9))
        self.db.run_sync(scheduling.cancel, never_synced, idem_key="c2")
        self.sync()
        self.assertIsNone(self.outbox(never_synced))

    def test_rebooking_restores_a_deleted_event(self):
        appointment_id = self.book()
        self.sync()
        self.cal.delete(CAL_A, calendar_sync.event_id_for(appointment_id))     # someone deleted it in Google
        self.db.run_sync(calendar_sync.retry, appointment_id, "staff")
        self.db.run_sync(lambda conn: conn.execute(
            "INSERT INTO sync_outbox (appointment_id, due_at) VALUES (?, ?)", (appointment_id, db.now_str())))
        self.sync()
        self.assertEqual(self.event(CAL_A, appointment_id)["status"], "confirmed")

    def test_needs_reschedule_gets_the_prefix(self):
        appointment_id = self.book()
        self.sync()

        def flag(conn):
            with db.transaction(conn):
                conn.execute("UPDATE appointments SET status = 'needs_reschedule', version = version + 1 "
                             "WHERE id = ?", (appointment_id,))
                scheduling._outbox(conn, appointment_id, clock.now())

        self.db.run_sync(flag)
        self.sync()
        self.assertTrue(self.event(CAL_A, appointment_id)["summary"].startswith("NEEDS RESCHEDULE: "))

    def test_backoff_schedule_and_giving_up_after_twelve_attempts(self):
        appointment_id = self.book()
        self.cal.fail_next(503, times=calendar_sync.MAX_ATTEMPTS)
        now = clock.now()
        waits = []
        for attempt in range(1, calendar_sync.MAX_ATTEMPTS + 1):
            with self.assertLogs("calendar_sync", level="WARNING"):
                self.assertEqual(self.sync(now=now), 1)
            row = self.outbox(appointment_id)
            self.assertEqual(row["attempts"], attempt)
            self.assertIn("503", row["last_error"])
            due = db.parse_utc(row["due_at"])
            waits.append(round((due - now.astimezone(due.tzinfo)).total_seconds()))
            self.assertEqual(self.sync(now=due - timedelta(seconds=1)), 0)      # not due yet
            now = due
        self.assertEqual(waits[:6], [5, 30, 120, 600, 3600, 3600])
        self.assertEqual(self.outbox(appointment_id)["status"], "failed")
        self.assertEqual(self.sync(now=now + timedelta(days=1)), 0)             # failed rows wait for Retry
        self.assertIsNone(self.event(CAL_A, appointment_id))

        self.assertEqual(self.db.run_sync(calendar_sync.retry_failed, "staff"), 1)
        row = self.outbox(appointment_id)
        self.assertEqual((row["status"], row["attempts"], row["last_error"]), ("pending", 0, None))
        self.sync()
        self.assertIsNotNone(self.event(CAL_A, appointment_id))
        audit = self.db.run_sync(lambda conn: conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action = 'calendar_retry'").fetchone()[0])
        self.assertEqual(audit, 1)

    def test_a_branch_without_a_calendar_waits_without_using_attempts(self):
        self.db.run_sync(lambda conn: conn.execute("UPDATE branches SET calendar_id = NULL WHERE id = 1"))
        appointment_id = self.book()
        self.sync()
        row = self.outbox(appointment_id)
        self.assertEqual((row["attempts"], row["status"]), (0, "pending"))
        self.assertIn("setup_calendars", row["last_error"])
        self.assertEqual(self.cal.calls, [])
        self.db.run_sync(lambda conn: conn.execute("UPDATE branches SET calendar_id = ? WHERE id = 1", (CAL_A,)))
        self.sync(now=clock.now() + timedelta(seconds=calendar_sync.NO_CALENDAR_RETRY_S))
        self.assertIsNotNone(self.event(CAL_A, appointment_id))

    def test_a_change_during_sync_gets_its_own_pass(self):
        appointment_id = self.book()
        worker_db = self.db

        class ChangesMidSync(FakeCalendar):
            moved = False

            def insert(self, calendar_id, event_id, body):
                super().insert(calendar_id, event_id, body)
                if not self.moved:              # a staff edit lands while Google is answering
                    self.moved = True
                    worker_db.run_sync(scheduling.reschedule, appointment_id, doctor_id=1, start=at(2, 15),
                                       idem_key="mid")

        self.worker = CalendarWorker(ChangesMidSync(), database=self.db)
        self.cal = self.worker.client
        self.sync()
        row = self.outbox(appointment_id)
        self.assertIsNotNone(row)                       # kept for the newer version
        self.assertEqual(self.appt(appointment_id)["calendar_synced_version"], 1)
        self.sync()
        self.assertIsNone(self.outbox(appointment_id))
        self.assertEqual(self.event(CAL_A, appointment_id)["start"]["dateTime"], at(2, 15).isoformat())
        self.assertEqual(self.appt(appointment_id)["calendar_synced_version"], 2)

    def test_the_dashboard_hears_about_each_sync(self):
        appointment_id = self.book()

        async def scenario():
            async with events.subscribe() as stream:
                await self.worker.run_once()
                return await stream.get(timeout=1)

        event = asyncio.run(scenario())
        self.assertEqual((event["type"], event["appointment_id"], event["status"]), ("sync", appointment_id, "synced"))

    def test_enqueue_all_requeues_current_and_future_bookings(self):
        a = self.book()
        b = self.book(start=at(2, 9))
        self.sync()
        self.assertEqual(self.db.run_sync(calendar_sync.enqueue_all), 2)
        self.assertIsNotNone(self.outbox(a))
        self.assertIsNotNone(self.outbox(b))
        counts = self.db.run_sync(calendar_sync.outbox_counts)
        self.assertEqual(counts, {"pending": 2, "failed": 0})
        rows = self.db.run_sync(calendar_sync.outbox_rows)
        self.assertEqual({r["service"] for r in rows}, {"Consultation"})


class WorkerLoopTests(SyncTestCase):
    def test_run_loop_drains_the_outbox_and_wakes_on_notify(self):
        appointment_id = self.book()
        self.worker.poll_s = 30

        async def scenario():
            task = asyncio.create_task(self.worker.run())
            for _ in range(100):
                await asyncio.sleep(0.02)
                if self.event(CAL_A, appointment_id):
                    break
            second = await self.db.run(scheduling.book, service="Consultation", doctor_id=1, start=at(4, 10),
                                       patient_name="Ravi", phone="9123456789", idem_key="loop2")
            self.worker.notify()                         # no 30 s wait
            for _ in range(100):
                await asyncio.sleep(0.02)
                if self.event(CAL_A, second.appointment_id):
                    break
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return second.appointment_id

        second_id = asyncio.run(scenario())
        self.assertIsNotNone(self.event(CAL_A, appointment_id))
        self.assertIsNotNone(self.event(CAL_A, second_id))


class ClientSetupTests(unittest.TestCase):
    def tearDown(self):
        calendar_sync.stop_worker()

    def test_disabled_missing_or_broken_key_keeps_the_worker_off(self):
        with patch.object(config, "CALENDAR_SYNC_ENABLED", False):
            client, reason = calendar_sync.build_client()
            self.assertIsNone(client)
            self.assertIn("CALENDAR_SYNC_ENABLED", reason)
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "nope.json")
            with patch.object(config, "GOOGLE_SERVICE_ACCOUNT_FILE", missing):
                client, reason = calendar_sync.build_client()
                self.assertIsNone(client)
                self.assertIn("no service-account key", reason)

                async def start():
                    return calendar_sync.start_worker()

                self.assertIsNone(asyncio.run(start()))
                self.assertEqual(calendar_sync.status()["enabled"], False)
                self.assertIn("no service-account key", calendar_sync.status()["reason"])
            broken = os.path.join(tmp, "key.json")
            Path(broken).write_text('{"type": "service_account"}', encoding="utf-8")
            with patch.object(config, "GOOGLE_SERVICE_ACCOUNT_FILE", broken):
                client, reason = calendar_sync.build_client()
                self.assertIsNone(client)
                self.assertRegex(reason, r"could not be loaded \(\w+\)")

    def test_a_relative_key_path_is_relative_to_the_project(self):
        with patch.object(config, "GOOGLE_SERVICE_ACCOUNT_FILE", os.path.join("secrets", "key.json")):
            self.assertEqual(calendar_sync.key_path(), os.path.join(config.BASE_DIR, "secrets", "key.json"))
            client, reason = calendar_sync.build_client()
            self.assertIsNone(client)
            self.assertIn(os.path.join("secrets", "key.json"), reason)
        absolute = os.path.join(tempfile.gettempdir(), "elsewhere.json")
        with patch.object(config, "GOOGLE_SERVICE_ACCOUNT_FILE", absolute):
            self.assertEqual(calendar_sync.key_path(), absolute)

    def test_google_http_errors_become_calendar_errors(self):
        import httplib2
        from googleapiclient.errors import HttpError

        class Request:
            def __init__(self, status):
                self.status = status

            def execute(self):
                raise HttpError(httplib2.Response({"status": self.status}), b'{"error": {"message": "x"}}')

        for status in (404, 409, 410, 503):
            with self.assertRaises(CalendarError) as ctx:
                calendar_sync.GoogleCalendar._run(Request(status))
            self.assertEqual(ctx.exception.status, status)


class SetupCalendarsToolTests(unittest.TestCase):
    def setUp(self):
        self.clinic = TempClinic(now=NOW)
        self.clinic.__enter__()
        self.db = self.clinic.db
        self.db.run_sync(lambda conn: conn.execute(
            "INSERT INTO branches (id, name, area) VALUES (2, 'Jayanagar', 'Bengaluru')"))
        self.tool = load_setup_tool()
        self.cal = FakeCalendar()

    def tearDown(self):
        self.clinic.__exit__(None, None, None)

    def branches(self):
        return self.db.run_sync(lambda conn: {r["name"]: r["calendar_id"] for r in conn.execute(
            "SELECT name, calendar_id FROM branches")})

    def test_creates_shares_and_stores_then_is_idempotent(self):
        first = self.db.run_sync(self.tool.setup, self.cal, "Demo.Pearl@gmail.com")
        self.assertEqual([(r["branch"], r["created"], r["shared"]) for r in first],
                         [(config.DEFAULT_BRANCH, True, True), ("Jayanagar", True, True)])
        stored = self.branches()
        self.assertEqual(len(self.cal.calendars), 2)
        for name, calendar_id in stored.items():
            calendar = self.cal.calendars[calendar_id]
            self.assertEqual(calendar["summary"], f"Pearl Dental — {name} (DEMO)")
            self.assertEqual(calendar["timeZone"], config.CLINIC_TIMEZONE)
            self.assertEqual(calendar["readers"], {"demo.pearl@gmail.com"})

        second = self.db.run_sync(self.tool.setup, self.cal, "demo.pearl@gmail.com")
        self.assertEqual([(r["created"], r["shared"]) for r in second], [(False, False), (False, False)])
        self.assertEqual(len(self.cal.calendars), 2)
        self.assertEqual(self.branches(), stored)

    def test_finds_an_existing_calendar_by_name_when_the_id_was_lost(self):
        self.db.run_sync(self.tool.setup, self.cal, "")
        self.db.run_sync(lambda conn: conn.execute("UPDATE branches SET calendar_id = 'gone@x'"))
        report = self.db.run_sync(self.tool.setup, self.cal, "")
        self.assertFalse(any(r["created"] for r in report))
        self.assertEqual(len(self.cal.calendars), 2)
        self.assertTrue(all(re.match(r"fake\d@", cid) for cid in self.branches().values()))

    def test_dry_run_changes_nothing(self):
        report = self.db.run_sync(self.tool.setup, self.cal, "demo.pearl@gmail.com", True)
        self.assertEqual({r["calendar_id"] for r in report}, {"(would create)"})
        self.assertEqual(self.cal.calendars, {})
        self.assertEqual(set(self.branches().values()), {None})


if __name__ == "__main__":
    unittest.main()
