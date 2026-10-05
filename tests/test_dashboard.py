"""
Dashboard: login and sessions, every API behind the login, appointments, CSV,
tasks, calls, system page, live events (SSE), the call gate and /health.
"""

import asyncio
import csv
import io
import json
import threading
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import auth
import calendar_sync
import clock
import config
import dashboard
import db
import events
import recording
import scheduling
import server
import tasks
from support import TempClinic

NOW = datetime(2026, 10, 5, 8, 0)          # Monday 08:00 IST
PASSWORD = "correct horse battery"
FAST_HASH = auth.hash_password(PASSWORD, n=2 ** 12)      # quick to verify in tests


def tomorrow(hh, mm=0):
    return clock.localize(datetime.combine(NOW.date() + timedelta(days=1), datetime.min.time())).replace(hour=hh, minute=mm)


# ---------------------------------------------------------------- auth unit tests
class PasswordHashTests(unittest.TestCase):
    def test_round_trip_and_format(self):
        stored = auth.hash_password("s3cret-password", n=2 ** 12)
        self.assertTrue(stored.startswith("scrypt:4096:8:1:"))
        self.assertNotIn("$", stored)                            # .env loaders expand "$"
        self.assertTrue(auth.verify_password("s3cret-password", stored))
        self.assertFalse(auth.verify_password("s3cret-passwore", stored))
        self.assertFalse(auth.verify_password("", stored))
        self.assertNotEqual(stored, auth.hash_password("s3cret-password", n=2 ** 12))   # salted

    def test_malformed_hashes_never_verify(self):
        for bad in ["", "plaintext", "scrypt:1000:8:1:abc:def", "bcrypt:4096:8:1:c2FsdHNhbHQ:a2V5a2V5a2V5a2V5",
                    "scrypt:4096:8:1:!!:??", "scrypt:4096:8"]:
            self.assertFalse(auth.verify_password("x", bad), bad)

    def test_status_explains_why_the_dashboard_is_locked(self):
        with patch.object(config, "DASHBOARD_PASSWORD_HASH", ""):
            self.assertEqual(auth.status()["configured"], False)
            self.assertIn("hash_password.py", auth.status()["reason"])
        with patch.object(config, "DASHBOARD_PASSWORD_HASH", "hunter2"):
            self.assertIn("not a valid", auth.status()["reason"])
        with patch.object(config, "DASHBOARD_PASSWORD_HASH", FAST_HASH):
            self.assertTrue(auth.configured())


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.patch = patch.object(config, "DASHBOARD_PASSWORD_HASH", FAST_HASH)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_signed_sessions_expire_and_resist_tampering(self):
        token = auth.issue_session(now=1_000_000)
        self.assertEqual(auth.read_session(token, now=1_000_100)["sub"], "staff")
        self.assertIsNone(auth.read_session(token, now=1_000_000 + config.DASHBOARD_SESSION_HOURS * 3600 + 1))
        payload, signature = token.rsplit(".", 1)
        forged = auth._b64(json.dumps({"sub": "staff", "exp": 9_999_999_999, "sid": "x"}).encode())
        self.assertIsNone(auth.read_session(f"{forged}.{signature}", now=1_000_100))
        self.assertIsNone(auth.read_session(payload + ".AAAA", now=1_000_100))
        self.assertIsNone(auth.read_session("garbage", now=1_000_100))
        # A mangled cookie with non-ASCII characters is just "not logged in", never an error.
        self.assertIsNone(auth.read_session(payload + ".sïgnature", now=1_000_100))
        self.assertIsNone(auth.read_session("pàyload." + signature, now=1_000_100))

    def test_logout_revokes_and_a_new_password_logs_everyone_out(self):
        token = auth.issue_session()
        other = auth.issue_session()
        auth.revoke(token)
        self.assertIsNone(auth.read_session(token))
        self.assertIsNotNone(auth.read_session(other))
        with patch.object(config, "DASHBOARD_PASSWORD_HASH", auth.hash_password("another password", n=2 ** 12)):
            self.assertIsNone(auth.read_session(other))

    def test_login_limiter_locks_out_then_forgives(self):
        limiter = auth.LoginLimiter(max_failures=3, window_s=60)
        for t in (0, 1, 2):
            self.assertEqual(limiter.retry_after("ip", now=t), 0)
            limiter.failure("ip", now=t)
        self.assertGreater(limiter.retry_after("ip", now=3), 0)
        self.assertEqual(limiter.retry_after("other", now=3), 0)
        self.assertEqual(limiter.retry_after("ip", now=63), 0)
        # Failures spread wider than the window don't add up.
        for t in (100, 200, 300):
            limiter.failure("slow", now=t)
        self.assertEqual(limiter.retry_after("slow", now=301), 0)


# ---------------------------------------------------------------- HTTP tests
class DashboardTestCase(unittest.TestCase):
    """A fresh clinic, the dashboard password configured, and a TestClient without the lifespan."""

    def setUp(self):
        self.clinic = TempClinic(now=NOW)
        self.clinic.__enter__()
        self.db = self.clinic.db
        self.patches = [patch.object(config, "DASHBOARD_PASSWORD_HASH", FAST_HASH)]
        for p in self.patches:
            p.start()
        auth.limiter.reset()
        events.reset()
        server.app.state.gate = server.CallGate()
        self.client = TestClient(server.app)

    def tearDown(self):
        self.client.close()
        for p in self.patches:
            p.stop()
        auth.limiter.reset()
        events.reset()
        self.clinic.__exit__(None, None, None)

    def login(self, client=None):
        response = (client or self.client).post("/dashboard/api/login", json={"password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def rows(self, sql, *args):
        return self.db.run_sync(lambda conn: [dict(r) for r in conn.execute(sql, args)])

    def book(self, hh=10, name="Priya Sharma", phone="9876543210", key=None):
        result = self.db.run_sync(scheduling.book, service="Consultation", doctor_id=1, start=tomorrow(hh),
                                  patient_name=name, phone=phone, idem_key=key or f"t{hh}{name}")
        self.assertTrue(result.ok, result.code)
        return result.appointment_id


class LockedDashboardTests(DashboardTestCase):
    def setUp(self):
        super().setUp()
        self.locked = patch.object(config, "DASHBOARD_PASSWORD_HASH", "")
        self.locked.start()

    def tearDown(self):
        self.locked.stop()
        super().tearDown()

    def test_without_a_hash_everything_stays_locked(self):
        self.assertEqual(self.client.get("/dashboard/api/overview").status_code, 503)
        self.assertIn("DASHBOARD_PASSWORD_HASH", self.client.get("/dashboard/api/overview").json()["detail"])
        self.assertEqual(self.client.post("/dashboard/api/login", json={"password": ""}).status_code, 503)
        page = self.client.get("/dashboard/", follow_redirects=False)
        self.assertEqual((page.status_code, page.headers["location"]), (303, "/dashboard/login"))
        status = self.client.get("/dashboard/api/auth").json()
        self.assertEqual((status["configured"], status["logged_in"]), (False, False))
        self.assertIn("hash_password.py", status["reason"])


class LoginTests(DashboardTestCase):
    def test_login_sets_a_strict_http_only_cookie(self):
        self.assertEqual(self.client.get("/dashboard/api/overview").status_code, 401)
        cookie = self.login().headers["set-cookie"]
        self.assertIn(f"{auth.COOKIE_NAME}=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=strict", cookie)
        self.assertIn("Path=/", cookie)
        self.assertEqual(self.client.get("/dashboard/api/overview").status_code, 200)
        self.assertEqual(self.client.get("/dashboard/").status_code, 200)
        self.assertEqual(self.client.get("/dashboard/login", follow_redirects=False).status_code, 303)
        audit = self.rows("SELECT action FROM audit_events WHERE entity = 'dashboard'")
        self.assertEqual([a["action"] for a in audit], ["login"])

    def test_wrong_password_then_rate_limit(self):
        for _ in range(config.DASHBOARD_LOGIN_MAX_FAILURES):
            response = self.client.post("/dashboard/api/login", json={"password": "guess"})
            self.assertEqual(response.status_code, 401)
        blocked = self.client.post("/dashboard/api/login", json={"password": PASSWORD})
        self.assertEqual(blocked.status_code, 429)                 # even the right password waits
        self.assertGreater(int(blocked.headers["retry-after"]), 0)
        self.assertNotIn(auth.COOKIE_NAME, blocked.headers.get("set-cookie", ""))
        self.assertEqual(self.client.post("/dashboard/api/login", content=b"not json").status_code, 429)

    def test_logout_kills_the_session_even_for_a_copied_cookie(self):
        self.login()
        token = self.client.cookies.get(auth.COOKIE_NAME)
        self.assertEqual(self.client.post("/dashboard/api/logout").status_code, 200)
        thief = TestClient(server.app, cookies={auth.COOKIE_NAME: token})
        self.assertEqual(thief.get("/dashboard/api/overview").status_code, 401)
        thief.close()

    def test_cross_site_writes_are_refused(self):
        self.login()
        evil = {"origin": "http://evil.example"}
        self.assertEqual(self.client.post("/dashboard/api/dnc", json={"phone": "9876543210"}, headers=evil).status_code, 403)
        self.assertEqual(self.client.post("/dashboard/api/login", json={"password": PASSWORD}, headers=evil).status_code, 403)
        same = {"origin": "http://testserver"}
        self.assertEqual(self.client.post("/dashboard/api/dnc", json={"phone": "9876543210"}, headers=same).status_code, 200)

    def test_every_data_route_needs_the_login(self):
        public = {"/dashboard/api/auth", "/dashboard/api/login", "/dashboard/api/logout"}
        checked = 0
        for route in dashboard.router.routes:
            if not route.path.startswith("/dashboard/api/") or route.path in public:
                continue
            path = (route.path.replace("{appointment_id}", "abc").replace("{task_id}", "1")
                    .replace("{call_id}", "abc"))
            for method in route.methods:
                if method == "HEAD":
                    continue
                response = self.client.request(method, path, json={} if method == "POST" else None,
                                               params={"service_id": 1, "day": "2026-10-06"})
                self.assertEqual(response.status_code, 401, f"{method} {route.path}")
                checked += 1
        self.assertGreaterEqual(checked, 19)

    def test_dashboard_pages_get_protective_headers(self):
        response = self.client.get("/dashboard/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.login()
        self.assertEqual(self.client.get("/dashboard/api/overview").headers["cache-control"], "no-store")
        self.assertEqual(self.client.get("/dashboard/static/dashboard.js").status_code, 200)


class AppointmentApiTests(DashboardTestCase):
    def setUp(self):
        super().setUp()
        self.login()

    def test_lists_filter_and_search(self):
        a = self.book(10, "Priya Sharma", "9876543210")
        self.book(11, "Ravi Kumar", "9123456789")
        day = self.client.get("/dashboard/api/appointments", params={"day": tomorrow(0).date().isoformat()}).json()
        self.assertEqual([r["patient_name"] for r in day["appointments"]], ["Priya Sharma", "Ravi Kumar"])
        first = day["appointments"][0]
        self.assertEqual((first["time"], first["end_time"], first["branch"], first["phone_display"]),
                         ("10:00", "10:30", config.DEFAULT_BRANCH, "9876543210"))
        self.assertEqual(self.client.get("/dashboard/api/appointments").json()["appointments"], [])   # today is empty
        search = lambda q: [r["id"] for r in self.client.get(
            "/dashboard/api/appointments", params={"scope": "all", "q": q}).json()["appointments"]]
        self.assertEqual(search("priya"), [a])
        self.assertEqual(search("43210"), [a])
        self.assertEqual(search(a[:6]), [a])
        self.assertEqual(len(search("")), 2)
        upcoming = self.client.get("/dashboard/api/appointments", params={"scope": "upcoming", "status": "cancelled"})
        self.assertEqual(upcoming.json()["appointments"], [])
        self.assertEqual(self.client.get("/dashboard/api/appointments", params={"scope": "week"}).status_code, 400)
        self.assertEqual(self.client.get("/dashboard/api/appointments", params={"day": "5th"}).status_code, 400)

    def test_csv_export_is_audited_and_formula_safe(self):
        self.book(10, "=HYPERLINK(\"http://x\")", "9876543210")
        response = self.client.get("/dashboard/api/appointments.csv", params={"scope": "all"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("DEMO", response.headers["content-disposition"])
        rows = list(csv.reader(io.StringIO(response.text)))
        self.assertEqual(rows[0][:3], ["Date", "Start", "End"])
        self.assertTrue(rows[1][6].startswith("'="))
        audit = self.rows("SELECT * FROM audit_events WHERE action = 'export_csv'")
        self.assertEqual(len(audit), 1)
        self.assertEqual(json.loads(audit[0]["after_json"])["rows"], 1)
        self.assertNotIn("HYPERLINK", audit[0]["after_json"])

    def test_manual_book_goes_through_the_engine(self):
        body = {"service_id": 2, "doctor_id": 1, "start": tomorrow(10).strftime("%Y-%m-%dT%H:%M"),
                "patient_name": "Anita Rao", "phone": "98450 12345", "patient_age": "34", "idem_key": "k1"}
        response = self.client.post("/dashboard/api/appointments", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        appointment_id = response.json()["appointment_id"]
        again = self.client.post("/dashboard/api/appointments", json=body)            # a double click
        self.assertEqual(again.json()["appointment_id"], appointment_id)
        appt = self.rows("SELECT * FROM appointments WHERE id = ?", appointment_id)[0]
        self.assertEqual((appt["source"], appt["patient_age"]), ("dashboard", 34))
        self.assertEqual(self.rows("SELECT actor FROM audit_events WHERE entity_id = ?", appointment_id)[0]["actor"],
                         "staff")
        self.assertEqual(len(self.rows("SELECT * FROM sync_outbox WHERE appointment_id = ?", appointment_id)), 1)

        taken = self.client.post("/dashboard/api/appointments", json=dict(body, idem_key="k2", patient_name="Other",
                                                                          phone="9123456789"))
        self.assertEqual(taken.status_code, 409)
        self.assertEqual(taken.json()["code"], "TAKEN")
        self.assertIn("taken", taken.json()["message"])
        bad_phone = self.client.post("/dashboard/api/appointments", json=dict(body, idem_key="k3", phone="12345",
                                                                              start=tomorrow(12).strftime("%Y-%m-%dT%H:%M")))
        self.assertEqual(bad_phone.json()["code"], "INVALID_PHONE")
        self.assertEqual(self.client.post("/dashboard/api/appointments", json=dict(body, patient_name="")).status_code, 400)
        self.assertEqual(self.client.post("/dashboard/api/appointments", json=dict(body, start="soon")).status_code, 400)

    def test_cancel_and_reschedule_respect_versions(self):
        appointment_id = self.book(10)
        stale = self.client.post(f"/dashboard/api/appointments/{appointment_id}/cancel", json={"version": 7})
        self.assertEqual((stale.status_code, stale.json()["code"]), (409, "STALE"))
        moved = self.client.post(f"/dashboard/api/appointments/{appointment_id}/reschedule",
                                 json={"doctor_id": 1, "start": tomorrow(15).strftime("%Y-%m-%dT%H:%M"), "version": 1})
        self.assertEqual(moved.status_code, 200, moved.text)
        cancelled = self.client.post(f"/dashboard/api/appointments/{appointment_id}/cancel",
                                     json={"version": 2, "reason": "Patient called"})
        self.assertEqual(cancelled.status_code, 200)
        appt = self.rows("SELECT status, cancel_reason FROM appointments WHERE id = ?", appointment_id)[0]
        self.assertEqual((appt["status"], appt["cancel_reason"]), ("cancelled", "Patient called"))

    def test_slots_for_manual_booking(self):
        appointment_id = self.book(10)
        day = tomorrow(0).date().isoformat()
        slots = self.client.get("/dashboard/api/slots", params={"service_id": 2, "day": day}).json()["slots"]
        times = [s["time"] for s in slots]
        self.assertIn("09:30", times)
        self.assertNotIn("10:00", times)                            # booked
        self.assertNotIn("14:00", times)                            # lunch
        freed = self.client.get("/dashboard/api/slots", params={"service_id": 2, "day": day,
                                                                "ignore_appointment": appointment_id}).json()["slots"]
        self.assertIn("10:00", [s["time"] for s in freed])
        self.assertEqual(self.client.get("/dashboard/api/slots", params={"service_id": 2, "day": "x"}).status_code, 400)

    def test_catalog_and_overview(self):
        self.book(10)
        catalog = self.client.get("/dashboard/api/catalog").json()
        self.assertEqual([b["name"] for b in catalog["branches"]], [config.DEFAULT_BRANCH])
        self.assertEqual(len(catalog["services"]), 9)
        self.assertEqual(len(catalog["doctors"][0]["services"]), 9)
        overview = self.client.get("/dashboard/api/overview").json()
        self.assertEqual(overview["timezone"], config.CLINIC_TIMEZONE)
        self.assertEqual(overview["sync"], {"pending": 1, "failed": 0})
        self.assertEqual(overview["call"]["busy"], False)


class TaskCallSystemApiTests(DashboardTestCase):
    def setUp(self):
        super().setUp()
        self.login()

    def test_tasks_toggle(self):
        task_id = self.db.run_sync(tasks.create_task, kind="emergency", priority="urgent", phone_e164="9876543210")
        listed = self.client.get("/dashboard/api/tasks").json()["tasks"]
        self.assertEqual([(t["id"], t["priority"]) for t in listed], [(task_id, "urgent")])
        done = self.client.post(f"/dashboard/api/tasks/{task_id}", json={"done": True}).json()
        self.assertEqual((done["changed"], done["task"]["status"], done["task"]["done_by"]), (True, "done", "staff"))
        self.assertEqual(self.client.get("/dashboard/api/tasks").json()["tasks"], [])
        reopened = self.client.post(f"/dashboard/api/tasks/{task_id}", json={"done": False}).json()
        self.assertEqual(reopened["task"]["status"], "open")
        self.assertEqual(self.client.post("/dashboard/api/tasks/999", json={"done": True}).status_code, 404)
        self.assertEqual(self.client.get("/dashboard/api/tasks", params={"status": "later"}).status_code, 400)

    def test_calls_transcript_and_delete_data(self):
        rec = recording.CallRecorder("call1")
        rec.turn("emma", "Hi, Pearl Dental.")
        rec.turn("caller", "I'm Priya")
        rec.end("abandoned", caller_phone="9876543210")
        calls = self.client.get("/dashboard/api/calls").json()["calls"]
        self.assertEqual([c["id"] for c in calls], ["call1"])
        detail = self.client.get("/dashboard/api/calls/call1").json()
        self.assertEqual([t["text"] for t in detail["turns"]], ["Hi, Pearl Dental.", "I'm Priya"])
        deleted = self.client.post("/dashboard/api/calls/call1/delete-data", json={})
        self.assertEqual(deleted.json()["turns_blanked"], 2)
        detail = self.client.get("/dashboard/api/calls/call1").json()
        self.assertEqual([t["text"] for t in detail["turns"]], [None, None])
        self.assertEqual(detail["phone_display"], "")
        self.assertEqual(self.client.get("/dashboard/api/calls/nope").status_code, 404)
        self.assertEqual(self.client.post("/dashboard/api/calls/nope/delete-data", json={}).status_code, 404)

    def test_system_page_outbox_retry_and_do_not_call(self):
        appointment_id = self.book(10)
        self.db.run_sync(lambda conn: conn.execute(
            "UPDATE sync_outbox SET status = 'failed', attempts = 12, last_error = 'HTTP 403: forbidden'"))
        system = self.client.get("/dashboard/api/system").json()
        self.assertEqual(system["outbox_counts"], {"pending": 0, "failed": 1})
        self.assertEqual(system["outbox"][0]["last_error"], "HTTP 403: forbidden")
        self.assertEqual(system["retention"]["transcript_days"], config.TRANSCRIPT_RETENTION_DAYS)
        self.assertFalse(system["retention"]["audio_recorded"])
        self.assertTrue(system["auth"]["configured"])
        self.assertEqual(system["calendars"], {config.DEFAULT_BRANCH: False})
        self.assertEqual(self.client.post(f"/dashboard/api/sync/{appointment_id}/retry", json={}).status_code, 200)
        self.assertEqual(self.rows("SELECT status FROM sync_outbox")[0]["status"], "pending")
        self.assertEqual(self.client.post("/dashboard/api/sync/nope/retry", json={}).status_code, 404)
        self.db.run_sync(lambda conn: conn.execute("UPDATE sync_outbox SET status = 'failed'"))
        self.assertEqual(self.client.post("/dashboard/api/sync/retry-failed", json={}).json()["retried"], 1)

        self.assertEqual(self.client.post("/dashboard/api/dnc", json={"phone": "12"}).status_code, 400)
        self.client.post("/dashboard/api/dnc", json={"phone": "98765 43210"})
        dnc = self.client.get("/dashboard/api/system").json()["do_not_call"]
        self.assertEqual([d["phone_e164"] for d in dnc], ["+919876543210"])
        self.client.post("/dashboard/api/dnc/remove", json={"phone": "+919876543210"})
        self.assertEqual(self.client.get("/dashboard/api/system").json()["do_not_call"], [])
        audit = self.rows("SELECT entity_id FROM audit_events WHERE action LIKE 'do_not_call%'")
        self.assertEqual({a["entity_id"] for a in audit}, {"******3210"})         # masked in the audit trail


# ---------------------------------------------------------------- live events
class LiveStreamTests(DashboardTestCase):
    def test_closing_the_dashboard_tab_ends_the_stream_and_its_subscription(self):
        """Through the real app and its middleware: a closed tab must not leave a subscriber behind."""
        token = auth.issue_session()

        async def scenario():
            closed, first_chunk, sent = asyncio.Event(), asyncio.Event(), []
            requested = False

            async def receive():
                nonlocal requested
                if not requested:
                    requested = True
                    return {"type": "http.request", "body": b"", "more_body": False}
                await closed.wait()
                return {"type": "http.disconnect"}

            async def send(message):
                sent.append(message)
                if message["type"] == "http.response.body" and message.get("body"):
                    first_chunk.set()

            scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
                     "method": "GET", "scheme": "http", "path": "/dashboard/api/events",
                     "raw_path": b"/dashboard/api/events", "root_path": "", "query_string": b"",
                     "headers": [(b"host", b"testserver"), (b"cookie", f"{auth.COOKIE_NAME}={token}".encode())],
                     "client": ("127.0.0.1", 50000), "server": ("testserver", 80), "state": {}}
            app_task = asyncio.create_task(server.app(scope, receive, send))
            await asyncio.wait_for(first_chunk.wait(), 2)
            subscribed = events.stats()["subscribers"]
            closed.set()                                   # the tab closes
            await asyncio.wait_for(app_task, 2)            # well before the 15 s heartbeat
            return sent, subscribed

        sent, subscribed = asyncio.run(scenario())
        self.assertEqual(sent[0]["status"], 200)
        self.assertIn((b"content-type", b"text/event-stream; charset=utf-8"), sent[0]["headers"])
        self.assertEqual(subscribed, 1)
        self.assertEqual(events.stats()["subscribers"], 0)


class FakeRequest:
    def __init__(self):
        self.gone = False

    async def is_disconnected(self):
        return self.gone


class EventTests(unittest.TestCase):
    def setUp(self):
        events.reset()

    def tearDown(self):
        events.reset()

    def test_publish_is_non_blocking_and_drops_for_slow_subscribers(self):
        async def scenario():
            async with events.subscribe(size=3) as stream:
                for i in range(10):
                    events.publish({"type": "caption", "call_id": "c", "text": str(i)})
                events.publish({"no": "type"})                    # ignored
                got = [await stream.get(timeout=0.1) for _ in range(4)]
                return got, stream.dropped

        got, dropped = asyncio.run(scenario())
        self.assertEqual([g["text"] for g in got[:3]], ["0", "1", "2"])
        self.assertIsNone(got[3])
        self.assertEqual(dropped, 7)
        self.assertEqual(events.stats()["subscribers"], 0)

    def test_publish_from_another_thread_reaches_the_loop(self):
        async def scenario():
            async with events.subscribe() as stream:
                thread = threading.Thread(target=events.publish, args=({"type": "sync", "appointment_id": "a"},))
                thread.start()
                thread.join()
                return await stream.get(timeout=1)

        event = asyncio.run(scenario())
        self.assertEqual((event["type"], event["call_id"]), ("sync", None))

    def test_a_new_live_panel_gets_the_call_in_progress(self):
        events.publish({"type": "call_started", "call_id": "old"})
        events.publish({"type": "call_ended", "call_id": "old"})
        self.assertEqual(events.current_call_events(), [])
        events.publish({"type": "call_started", "call_id": "now"})
        events.publish({"type": "caption", "call_id": "now", "who": "user", "text": "hi", "final": True})
        events.publish({"type": "sync", "call_id": None})
        self.assertEqual([e["type"] for e in events.current_call_events()], ["call_started", "caption"])

    def test_a_long_call_is_replayed_from_its_start_without_interim_captions(self):
        events.publish({"type": "call_started", "call_id": "long"})
        for i in range(events.RECENT_SIZE * 2):            # the caller talks a lot: interims flood in
            events.publish({"type": "caption", "call_id": "long", "who": "user", "text": f"w{i}", "final": False})
        events.publish({"type": "caption", "call_id": "long", "who": "user", "text": "a cleaning", "final": True})
        events.publish({"type": "call_turn", "call_id": "other", "role": "emma", "text": "not this call"})
        replay = events.current_call_events()
        self.assertEqual([e["type"] for e in replay], ["call_started", "caption"])
        self.assertEqual(replay[1]["text"], "a cleaning")
        events.publish({"type": "call_ended", "call_id": "long"})
        self.assertEqual(events.current_call_events(), [])

    def test_sse_stream_replays_then_streams_with_heartbeats(self):
        events.publish({"type": "call_started", "call_id": "c1"})

        async def scenario():
            request = FakeRequest()
            stream = dashboard.sse_stream(request, heartbeat_s=0.05)
            chunks = [await stream.__anext__(), await stream.__anext__()]
            events.publish({"type": "state", "call_id": "c1", "state": "thinking"})
            chunks.append(await stream.__anext__())
            chunks.append(await stream.__anext__())              # nothing new: a keep-alive
            request.gone = True
            with self.assertRaises(StopAsyncIteration):
                await stream.__anext__()
            return chunks

        chunks = asyncio.run(scenario())
        self.assertTrue(chunks[0].startswith("retry:"))
        self.assertTrue(chunks[1].startswith("event: call_started\ndata: "))
        self.assertEqual(json.loads(chunks[2].split("data: ", 1)[1])["state"], "thinking")
        self.assertEqual(chunks[3], ": keep-alive\n\n")


# ---------------------------------------------------------------- call gate and /health
class FakeSession:
    """Stands in for CallSession: greets, then ends when the page sends {"type": "end"}."""

    fail_on_start = False

    def __init__(self, transport, services, call_id=None, listen_only=False):
        self.t = transport
        self.call_id = call_id
        self.closed = False
        self.s = SimpleNamespace(closed_conversation=True, phone="9876543210", phone_confirmed=True)

    async def start(self):
        if self.fail_on_start:
            raise ValueError("Deepgram exploded")     # RuntimeError would read as a disconnect
        await self.t.send_event({"type": "caption", "who": "emma", "text": "Hi, Pearl Dental.", "final": True})
        self.recorder.turn("emma", "Hi, Pearl Dental.")

    async def on_audio(self, pcm):
        pass

    async def on_control(self, msg):
        if msg.get("type") == "end":
            await self.close()

    async def close(self):
        self.closed = True


class CallGateTests(DashboardTestCase):
    def test_gate_holds_one_call_and_only_its_holder_releases_it(self):
        gate = server.CallGate()
        self.assertTrue(gate.try_acquire("inbound", "a"))
        self.assertFalse(gate.try_acquire("outbound", "b"))
        self.assertFalse(gate.release("b"))
        self.assertEqual(gate.status()["call_id"], "a")
        self.assertTrue(gate.release("a"))
        self.assertFalse(gate.busy)

    def test_a_second_caller_gets_busy_and_the_socket_closes(self):
        server.app.state.gate.try_acquire("inbound", "someone")
        with self.client.websocket_connect("/ws/voice") as ws:
            message = ws.receive_json()
            self.assertEqual(message["type"], "busy")
            self.assertTrue(message["message"])
            with self.assertRaises(WebSocketDisconnect) as ctx:
                ws.receive_json()
            self.assertEqual(ctx.exception.code, 1013)
        self.assertEqual(server.app.state.gate.call_id, "someone")          # the real call keeps its slot

    def test_a_call_is_recorded_mirrored_and_always_releases_the_gate(self):
        seen = []
        original = events.publish
        with patch.object(server, "CallSession", FakeSession), patch.object(server, "_services", lambda state: None), \
                patch.object(events, "publish", side_effect=lambda e: (seen.append(e), original(e))):
            with self.client.websocket_connect("/ws/voice") as ws:
                self.assertEqual(ws.receive_json()["type"], "caption")
                self.assertTrue(server.app.state.gate.busy)
                ws.send_json({"type": "end"})
            self.assertFalse(server.app.state.gate.busy)
            types = [e["type"] for e in seen]
            self.assertEqual(types[0], "call_started")
            self.assertIn("caption", types)
            ended = next(e for e in seen if e["type"] == "call_ended")
            self.assertEqual((ended["outcome"], ended["kept"]), ("completed", True))
            call_id = ended["call_id"]
            self.assertTrue(all(e["call_id"] == call_id for e in seen if e["type"] in ("caption", "call_started")))
            self.assertEqual(len(self.rows("SELECT * FROM calls WHERE id = ?", call_id)), 1)

            FakeSession.fail_on_start = True
            try:
                with self.assertLogs("voice-server", level="ERROR"):
                    with self.client.websocket_connect("/ws/voice"):
                        pass
            finally:
                FakeSession.fail_on_start = False
            self.assertFalse(server.app.state.gate.busy)

    def test_health_reports_calendar_dashboard_and_retention(self):
        server.app.state.db = self.db
        server.app.state.cache_ready = False
        nlu = SimpleNamespace(available=None, status=lambda: {"model": "stub"})
        with patch.object(server.llm, "get_nlu", return_value=nlu), \
                patch.object(config, "GOOGLE_SERVICE_ACCOUNT_FILE", "Z:/nowhere/key.json"):
            calendar_sync.stop_worker()
            health = self.client.get("/health").json()
        self.assertEqual(health["dashboard"]["auth_configured"], True)
        self.assertEqual(health["calendar"]["enabled"], False)
        self.assertIn("outbox", health["calendar"])
        self.assertEqual(health["calendar"]["branch_calendars"], {config.DEFAULT_BRANCH: False})
        self.assertEqual(health["retention"]["transcript_days"], config.TRANSCRIPT_RETENTION_DAYS)
        self.assertEqual(health["call"]["busy"], False)
        with patch.object(config, "DASHBOARD_PASSWORD_HASH", ""), patch.object(server.llm, "get_nlu", return_value=nlu):
            locked = self.client.get("/health").json()["dashboard"]
        self.assertEqual(locked["auth_configured"], False)
        self.assertIn("hash_password.py", locked["reason"])


if __name__ == "__main__":
    unittest.main()
