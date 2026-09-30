"""Scheduling engine: every rule, search/offer policy, holds, and the booking transactions."""

import os
import tempfile
import threading
import unittest
from datetime import date, datetime, time, timedelta
from unittest.mock import patch

import clock
import db
import scheduling as sch
import seed_demo
from dateparse import DateConstraint, TimeConstraint

NOW = datetime(2026, 10, 5, 8, 0)          # Monday 08:00 IST
MON = date(2026, 10, 5)
TUE = date(2026, 10, 6)
SUN = date(2026, 10, 11)


def at(day, hh, mm=0):
    return datetime.combine(day, time(hh, mm), tzinfo=clock.TZ)


def make_clinic(conn):
    """Two branches; a full-time doctor and a mornings-only consultant at A; one full-time at B."""
    with db.transaction(conn):
        # Statement by statement: executescript() would commit the open transaction.
        for sql in [
            "INSERT INTO branches (id, name, area) VALUES (1, 'Nagarbhavi', 'Nagarbhavi'), (2, 'Jayanagar', 'Jayanagar')",
            "INSERT INTO services (id, name, duration_min) VALUES "
            "(1, 'Consultation', 30), (2, 'Tooth Filling', 45), (3, 'Root Canal Treatment', 60)",
            "INSERT INTO doctors (id, name, spoken_name, gender, branch_id) VALUES "
            "(1, 'Dr. All Day', 'Dr Day', 'male', 1), (2, 'Dr. Mornings', 'Dr Morning', 'female', 1), "
            "(3, 'Dr. Branch B', 'Dr Bee', 'male', 2)",
            "INSERT INTO doctor_services VALUES (1, 1), (1, 2), (1, 3), (2, 1), (3, 1), (3, 2), (3, 3)",
        ]:
            conn.execute(sql)
        for wd in range(6):
            conn.execute("INSERT INTO availability_rules (doctor_id, weekday, start_time, end_time) VALUES (1, ?, '07:00', '21:00')", (wd,))
            conn.execute("INSERT INTO availability_rules (doctor_id, weekday, start_time, end_time) VALUES (2, ?, '09:00', '13:00')", (wd,))
            conn.execute("INSERT INTO availability_rules (doctor_id, weekday, start_time, end_time) VALUES (3, ?, '07:00', '21:00')", (wd,))


class EngineTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "emma.db")
        self.conn = db.connect(self.path)
        db.migrate(self.conn)
        make_clinic(self.conn)
        self.frozen = clock.frozen(NOW)
        self.frozen.__enter__()
        self.n = 0

    def tearDown(self):
        self.frozen.__exit__(None, None, None)
        self.conn.close()
        self.tmp.cleanup()

    def book(self, start, doctor=1, service="Consultation", phone=None, name=None, **kw):
        self.n += 1
        return sch.book(self.conn, service=service, doctor_id=doctor, start=start,
                        patient_name=name or f"Patient {self.n}", phone=phone or f"98765{self.n:05d}",
                        idem_key=kw.pop("idem_key", f"k{self.n}"), **kw)

    def why(self, start, doctor=1, service="Consultation", **kw):
        return sch.validate(self.conn, service=service, doctor_id=doctor, start=start, **kw)


class RuleTests(EngineTestCase):
    def test_a_normal_slot_is_valid(self):
        self.assertEqual(self.why(at(MON, 10)), [])

    def test_off_grid(self):
        self.assertIn("OFF_GRID", self.why(at(MON, 10, 15)))

    def test_lead_time_and_past(self):
        self.assertEqual(self.why(at(MON, 9, 30)), ["TOO_SOON"])        # 1.5 h from now
        self.assertEqual(self.why(at(MON, 10)), [])                     # exactly 2 h
        self.assertEqual(self.why(at(MON, 7)), ["TOO_SOON"])            # already past
        self.assertEqual(self.why(at(MON, 8, 30), emergency=True), [])  # emergencies: 30 min
        self.assertEqual(self.why(at(date(2026, 10, 3), 10)), ["TOO_SOON"])  # last Saturday

    def test_horizon(self):
        self.assertEqual(self.why(at(date(2026, 12, 4), 10)), [])       # day 60
        self.assertEqual(self.why(at(date(2026, 12, 5), 10)), ["BEYOND_HORIZON"])

    def test_sunday(self):
        self.assertEqual(self.why(at(SUN, 10)), ["CLOSED_DAY", "DOCTOR_OFF"])

    def test_closure_is_per_branch_or_clinic_wide(self):
        with db.transaction(self.conn):
            self.conn.execute("INSERT INTO closures (date, branch_id, reason) VALUES (?, 1, 'Training')", (TUE.isoformat(),))
        self.assertEqual(self.why(at(TUE, 10), doctor=1), ["CLOSURE"])
        self.assertEqual(self.why(at(TUE, 10), doctor=3), [])
        with db.transaction(self.conn):
            self.conn.execute("INSERT INTO closures (date, branch_id, reason) VALUES (?, NULL, 'Holiday')", (TUE.isoformat(),))
        self.assertEqual(self.why(at(TUE, 10), doctor=3), ["CLOSURE"])

    def test_whole_appointment_must_fit_clinic_hours(self):
        self.assertEqual(self.why(at(MON, 20, 30), service="Consultation"), [])
        self.assertEqual(self.why(at(MON, 20), service="Root Canal Treatment"), [])
        self.assertEqual(self.why(at(MON, 20, 30), service="Root Canal Treatment"), ["OUTSIDE_HOURS", "DOCTOR_OFF"])

    def test_lunch_overlap_uses_the_real_duration(self):
        self.assertEqual(self.why(at(MON, 13, 30)), [])
        self.assertEqual(self.why(at(MON, 13, 30), service="Tooth Filling"), ["LUNCH"])
        self.assertEqual(self.why(at(MON, 14)), ["LUNCH"])
        self.assertEqual(self.why(at(MON, 14, 30)), [])

    def test_doctor_hours_and_services(self):
        self.assertEqual(self.why(at(MON, 12, 30), doctor=2), [])
        self.assertEqual(self.why(at(MON, 13), doctor=2), ["DOCTOR_OFF"])
        self.assertEqual(self.why(at(MON, 10), doctor=2, service="Root Canal Treatment"), ["DOCTOR_NO_SERVICE"])

    def test_blocked_time_until_lifted(self):
        with db.transaction(self.conn):
            cur = self.conn.execute(
                "INSERT INTO blocked_times (doctor_id, start_utc, end_utc, reason_category, created_by, created_at) "
                "VALUES (1, ?, ?, 'illness', 'staff', ?)", (db.utc_str(at(MON, 12)), db.utc_str(at(MON, 16)), db.now_str()))
        self.assertEqual(self.why(at(MON, 11, 30), service="Root Canal Treatment"), ["DOCTOR_BLOCKED"])
        self.assertEqual(self.why(at(MON, 16)), [])
        with db.transaction(self.conn):
            self.conn.execute("UPDATE blocked_times SET lifted_at = ? WHERE id = ?", (db.now_str(), cur.lastrowid))
        self.assertEqual(self.why(at(MON, 12)), [])

    def test_a_45_minute_booking_takes_two_cells(self):
        self.assertTrue(self.book(at(MON, 10), service="Tooth Filling").ok)
        self.assertEqual(self.why(at(MON, 10)), ["TAKEN"])
        self.assertEqual(self.why(at(MON, 10, 30)), ["TAKEN"])
        self.assertEqual(self.why(at(MON, 11)), [])
        self.assertEqual(self.why(at(MON, 9, 30), service="Root Canal Treatment"), ["TOO_SOON", "TAKEN"])


class SearchTests(EngineTestCase):
    def test_nearest_free_alternatives_skip_the_busy_slot(self):
        self.book(at(MON, 17), doctor=1)            # a 30-minute booking in the 17:00 cell
        slots = sch.find_slots(self.conn, service="Root Canal Treatment", dates=[MON], branch_ids=[1],
                               near=at(MON, 17))
        # A 60-minute root canal at 16:30 would run into 17:00; 17:30 and 16:00 are the nearest that fit.
        self.assertEqual([s.start for s in slots], [at(MON, 17, 30), at(MON, 16)])

    def test_alternatives_skip_lunch(self):
        self.book(at(MON, 13, 30), doctor=1)
        slots = sch.find_slots(self.conn, service="Root Canal Treatment", dates=[MON], branch_ids=[1],
                               near=at(MON, 13, 30))
        self.assertEqual([s.start for s in slots], [at(MON, 12, 30), at(MON, 14, 30)])

    def test_two_doctors_free_at_once_count_as_one_time(self):
        slots = sch.find_slots(self.conn, service="Consultation", dates=[MON], branch_ids=[1], near=at(MON, 10))
        self.assertEqual([s.start for s in slots], [at(MON, 10), at(MON, 10, 30)])

    def test_gender_and_doctor_filters(self):
        slots = sch.find_slots(self.conn, service="Consultation", dates=[MON], gender="female")
        self.assertTrue(slots and all(s.doctor_id == 2 for s in slots))
        self.assertEqual(sch.find_slots(self.conn, service="Root Canal Treatment", dates=[MON], doctor_id=2), [])

    def test_earliest_first_within_a_window(self):
        slots = sch.find_slots(self.conn, service="Consultation", dates=[MON, TUE], window=(time(16), time(21)))
        self.assertEqual([s.start for s in slots], [at(MON, 16), at(MON, 16, 30)])


class SuggestTests(EngineTestCase):
    def suggest(self, day_c, time_c=None, **kw):
        return sch.suggest(self.conn, service=kw.pop("service", "Consultation"), date_c=day_c, time_c=time_c,
                           branch_ids=kw.pop("branch_ids", [1]), **kw)

    def test_exact_request_that_is_free(self):
        s = self.suggest(DateConstraint(MON, MON), TimeConstraint("exact", time(17)))
        self.assertEqual((s.kind, [x.start for x in s.slots]), ("exact", [at(MON, 17)]))

    def test_exact_request_that_is_taken_offers_nearest_that_day(self):
        self.book(at(MON, 17), doctor=1)
        s = self.suggest(DateConstraint(MON, MON), TimeConstraint("exact", time(17)))
        self.assertEqual(s.kind, "alternatives")
        self.assertEqual(s.scope, "same_day")
        self.assertEqual(s.reasons, ["TAKEN"])
        self.assertEqual([x.start for x in s.slots], [at(MON, 16, 30), at(MON, 17, 30)])

    def test_lunch_request_explains_lunch(self):
        s = self.suggest(DateConstraint(MON, MON), TimeConstraint("exact", time(14)))
        self.assertEqual(s.reasons, ["LUNCH"])
        self.assertEqual([x.start for x in s.slots], [at(MON, 13, 30), at(MON, 14, 30)])

    def test_off_grid_request_offers_the_grid_either_side(self):
        s = self.suggest(DateConstraint(MON, MON), TimeConstraint("exact", time(16, 15)))
        self.assertEqual(s.reasons, ["OFF_GRID"])
        self.assertEqual([x.start for x in s.slots], [at(MON, 16), at(MON, 16, 30)])

    def test_closed_day_moves_to_later_days_around_the_same_time(self):
        with db.transaction(self.conn):
            self.conn.execute("INSERT INTO closures (date, branch_id, reason) VALUES (?, NULL, 'Holiday')", (TUE.isoformat(),))
        s = self.suggest(DateConstraint(TUE, TUE), TimeConstraint("exact", time(17)))
        self.assertEqual((s.kind, s.scope), ("alternatives", "later_days"))
        self.assertEqual(s.slots[0].start, at(date(2026, 10, 7), 17))

    def test_window_on_a_day(self):
        s = self.suggest(DateConstraint(MON, MON), TimeConstraint("window", time(16), time(21)))
        self.assertEqual((s.scope, s.slots[0].start), ("in_window", at(MON, 16)))

    def test_range_at_one_time(self):
        self.book(at(MON, 17), doctor=1)
        s = self.suggest(DateConstraint(MON, TUE, "range"), TimeConstraint("exact", time(17)), branch_ids=[1])
        self.assertEqual(s.kind, "exact")
        self.assertEqual(s.slots[0].start, at(TUE, 17))

    def test_nothing_anywhere(self):
        with db.transaction(self.conn):
            self.conn.execute("UPDATE doctors SET active = 0")
        s = self.suggest(DateConstraint(MON, MON), TimeConstraint("exact", time(17)))
        self.assertEqual(s.kind, "none")
        self.assertEqual(s.searched_until, MON + timedelta(days=7))

    def test_ambiguous_time_must_be_resolved_first(self):
        with self.assertRaises(ValueError):
            self.suggest(DateConstraint(MON, MON), TimeConstraint("ambiguous", candidates=(time(7), time(19))))


class HoldTests(EngineTestCase):
    def slot(self, hh, doctor=1):
        return sch.find_slots(self.conn, service="Consultation", dates=[MON], doctor_id=doctor, near=at(MON, hh), limit=1)[0]

    def test_a_hold_keeps_the_slot_for_its_call_only(self):
        s = self.slot(11)
        self.assertIsNotNone(sch.hold(self.conn, s, "callA"))
        self.assertIsNone(sch.hold(self.conn, s, "callB"))
        self.assertEqual(self.why(s.start), ["TAKEN"])
        self.assertEqual(self.why(s.start, call_id="callA"), [])
        self.assertEqual(self.book(s.start, call_id="callB").code, "TAKEN")
        result = self.book(s.start, call_id="callA")
        self.assertTrue(result.ok)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM slot_holds").fetchone()[0], 0)

    def test_expired_holds_do_not_block(self):
        s = self.slot(11)
        sch.hold(self.conn, s, "callA", ttl_s=60)
        with clock.frozen(NOW + timedelta(minutes=2)):
            self.assertEqual(self.why(s.start), [])
            self.assertTrue(self.book(s.start, call_id="callB").ok)

    def test_release_and_refresh(self):
        a, b = self.slot(11), self.slot(12)
        keep = sch.hold(self.conn, a, "callA")
        sch.hold(self.conn, b, "callA")
        self.assertEqual(sch.release_holds(self.conn, "callA", keep=[keep]), 1)
        self.assertEqual(self.why(b.start), [])
        self.assertEqual(sch.refresh_holds(self.conn, "callA", ttl_s=900), 1)
        with clock.frozen(NOW + timedelta(minutes=10)):
            self.assertEqual(self.why(a.start), ["TAKEN"])


class BookingTests(EngineTestCase):
    def test_booking_writes_appointment_claims_audit_and_outbox(self):
        r = self.book(at(MON, 10), service="Root Canal Treatment", name="Priya Sharma", phone="+91 98765 43210")
        self.assertTrue(r.ok, r.code)
        a = r.appointment
        self.assertEqual((a["patient_name"], a["caller_phone_e164"], a["doctor"], a["branch"]),
                         ("Priya Sharma", "+919876543210", "Dr Day", "Nagarbhavi"))
        self.assertEqual(a["start"], at(MON, 10).isoformat())
        count = lambda sql: self.conn.execute(sql, (r.appointment_id,)).fetchone()[0]
        self.assertEqual(count("SELECT COUNT(*) FROM slot_claims WHERE appointment_id = ?"), 2)
        self.assertEqual(count("SELECT COUNT(*) FROM audit_events WHERE entity_id = ?"), 1)
        self.assertEqual(count("SELECT COUNT(*) FROM sync_outbox WHERE appointment_id = ?"), 1)

    def test_retry_with_same_key_returns_the_original_booking(self):
        first = self.book(at(MON, 10), idem_key="call1:book", phone="9876543210", name="Asha")
        again = self.book(at(MON, 10), idem_key="call1:book", phone="9876543210", name="Asha")
        self.assertTrue(again.ok and again.replayed)
        self.assertEqual(again.appointment_id, first.appointment_id)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0], 1)

    def test_someone_else_cannot_take_a_booked_slot(self):
        self.assertTrue(self.book(at(MON, 10)).ok)
        self.assertEqual(self.book(at(MON, 10)).code, "TAKEN")

    def test_same_patient_cannot_be_in_two_places(self):
        self.assertTrue(self.book(at(MON, 10), doctor=1, name="Asha", phone="9876543210").ok)
        clash = self.book(at(MON, 10), doctor=3, name="asha", phone="98765 43210")
        self.assertEqual(clash.code, "PATIENT_CONFLICT")
        # A different family member on the same number can be seen at the same time.
        self.assertTrue(self.book(at(MON, 10), doctor=3, name="Ravi", phone="9876543210").ok)

    def test_at_most_three_future_appointments_per_number(self):
        for hh in (10, 11, 12):
            self.assertTrue(self.book(at(MON, hh), name=f"P{hh}", phone="9876543210").ok)
        self.assertEqual(self.book(at(MON, 15), name="P15", phone="9876543210").code, "MAX_FUTURE")

    def test_invalid_phone_and_rules_are_enforced_at_commit(self):
        self.assertEqual(self.book(at(MON, 10), phone="12345").code, "INVALID_PHONE")
        self.assertEqual(self.book(at(MON, 14)).code, "LUNCH")
        self.assertEqual(self.book(at(SUN, 10)).code, "CLOSED_DAY")

    def test_ten_callers_racing_for_one_slot_get_exactly_one_booking(self):
        barrier = threading.Barrier(10)
        results = []

        def race(i):
            conn = db.connect(self.path)
            barrier.wait()
            r = sch.book(conn, service="Consultation", doctor_id=1, start=at(MON, 10), patient_name=f"Racer {i}",
                         phone=f"98000000{i:02d}", idem_key=f"race{i}", now=NOW)
            results.append(r.code)
            conn.close()

        threads = [threading.Thread(target=race, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), ["OK"] + ["TAKEN"] * 9)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0], 1)

    def test_database_blocks_a_double_booking_even_if_the_checks_are_wrong(self):
        self.assertTrue(self.book(at(MON, 10)).ok)
        with patch.object(sch, "validate", return_value=[]):
            self.assertEqual(self.book(at(MON, 10)).code, "TAKEN")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0], 1)


class RescheduleCancelTests(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.appt = self.book(at(MON, 10), service="Root Canal Treatment", name="Asha", phone="9876543210")
        self.assertTrue(self.appt.ok)
        self.id = self.appt.appointment_id

    def claims(self):
        return [r[0] for r in self.conn.execute(
            "SELECT cell_start_utc FROM slot_claims WHERE appointment_id = ? ORDER BY 1", (self.id,))]

    def test_reschedule_moves_claims_and_bumps_version(self):
        r = sch.reschedule(self.conn, self.id, doctor_id=3, start=at(TUE, 16), idem_key="mv1", expected_version=1)
        self.assertTrue(r.ok, r.code)
        self.assertEqual((r.appointment["branch"], r.appointment["version"]), ("Jayanagar", 2))
        self.assertEqual(self.claims(), [db.utc_str(at(TUE, 16)), db.utc_str(at(TUE, 16, 30))])
        self.assertEqual(self.why(at(MON, 10)), [])

    def test_failed_reschedule_leaves_the_original_untouched(self):
        self.book(at(TUE, 16), doctor=3)
        before = self.claims()
        r = sch.reschedule(self.conn, self.id, doctor_id=3, start=at(TUE, 16), idem_key="mv1")
        self.assertEqual(r.code, "TAKEN")
        appt = sch.get_appointment(self.conn, self.id)
        self.assertEqual((appt["start"], appt["version"], appt["status"]), (at(MON, 10).isoformat(), 1, "booked"))
        self.assertEqual(self.claims(), before)

    def test_moving_into_its_own_cells_works(self):
        r = sch.reschedule(self.conn, self.id, doctor_id=1, start=at(MON, 10, 30), idem_key="mv1")
        self.assertTrue(r.ok, r.code)
        self.assertEqual(self.claims(), [db.utc_str(at(MON, 10, 30)), db.utc_str(at(MON, 11))])

    def test_stale_version_same_slot_and_late_changes_are_refused(self):
        self.assertEqual(sch.reschedule(self.conn, self.id, doctor_id=1, start=at(TUE, 10), idem_key="a",
                                        expected_version=7).code, "STALE")
        self.assertEqual(sch.reschedule(self.conn, self.id, doctor_id=1, start=at(MON, 10), idem_key="b").code,
                         "SAME_SLOT")
        with clock.frozen(datetime(2026, 10, 5, 8, 30)):     # 1.5 h before the appointment
            self.assertEqual(sch.reschedule(self.conn, self.id, doctor_id=1, start=at(TUE, 10), idem_key="c").code,
                             "TOO_LATE")
            self.assertTrue(sch.reschedule(self.conn, self.id, doctor_id=1, start=at(TUE, 10), idem_key="d",
                                           enforce_notice=False, actor="staff:dashboard").ok)

    def test_reschedule_retry_is_harmless(self):
        first = sch.reschedule(self.conn, self.id, doctor_id=1, start=at(TUE, 10), idem_key="mv1")
        again = sch.reschedule(self.conn, self.id, doctor_id=1, start=at(TUE, 10), idem_key="mv1")
        self.assertTrue(again.replayed)
        self.assertEqual(again.appointment["version"], first.appointment["version"])

    def test_cancel_frees_the_slot_and_is_idempotent(self):
        r = sch.cancel(self.conn, self.id, idem_key="c1", reason="  travelling ", expected_version=1)
        self.assertTrue(r.ok)
        self.assertEqual((r.appointment["status"], r.appointment["cancel_reason"]), ("cancelled", "travelling"))
        self.assertEqual(self.claims(), [])
        self.assertEqual(self.why(at(MON, 10)), [])
        self.assertTrue(sch.cancel(self.conn, self.id, idem_key="c1").replayed)
        self.assertEqual(sch.cancel(self.conn, self.id, idem_key="c2").code, "ALREADY_CANCELLED")
        self.assertEqual(sch.reschedule(self.conn, self.id, doctor_id=1, start=at(TUE, 10), idem_key="m").code,
                         "NOT_ACTIVE")

    def test_cannot_cancel_after_it_started(self):
        with clock.frozen(datetime(2026, 10, 5, 10, 15)):
            self.assertEqual(sch.cancel(self.conn, self.id, idem_key="c1").code, "TOO_LATE")

    def test_future_appointments_lists_this_number_only(self):
        self.book(at(TUE, 10), phone="9123456789")
        found = sch.future_appointments(self.conn, "98765 43210")
        self.assertEqual([a["id"] for a in found], [self.id])
        self.assertEqual(sch.get_appointment(self.conn, "nope"), None)


class DatabaseAndSeedTests(unittest.TestCase):
    def test_migrations_are_idempotent_and_pragmas_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(os.path.join(tmp, "x.db"))
            self.assertEqual(db.migrate(conn), ["001_init"])
            self.assertEqual(db.migrate(conn), [])
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            conn.close()

    def test_demo_seed_is_consistent_and_runs_once(self):
        with tempfile.TemporaryDirectory() as tmp, clock.frozen(NOW):
            conn = db.connect(os.path.join(tmp, "x.db"))
            db.migrate(conn)
            self.assertTrue(seed_demo.seed_if_empty(conn))
            self.assertFalse(seed_demo.seed_if_empty(conn))
            count = lambda t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            self.assertEqual((count("branches"), count("doctors"), count("services")), (4, 8, 9))
            self.assertGreaterEqual(count("appointments"), 30)
            wrong = conn.execute(
                "SELECT COUNT(*) FROM (SELECT a.id FROM appointments a JOIN services s ON s.id = a.service_id "
                "LEFT JOIN slot_claims c ON c.appointment_id = a.id GROUP BY a.id "
                "HAVING COUNT(c.cell_start_utc) != (s.duration_min + 29) / 30)").fetchone()[0]
            self.assertEqual(wrong, 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM branches WHERE is_demo = 0").fetchone()[0], 0)
            conn.close()

    def test_database_thread_runs_calls_in_order(self):
        import asyncio
        with tempfile.TemporaryDirectory() as tmp:
            database = db.Database(os.path.join(tmp, "x.db"))
            database.run_sync(db.migrate)

            async def run():
                return await asyncio.gather(*(database.run(lambda c, i=i: i) for i in range(20)))

            self.assertEqual(asyncio.run(run()), list(range(20)))
            database.close()


if __name__ == "__main__":
    unittest.main()
