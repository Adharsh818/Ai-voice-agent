"""
Day 6 fault drills (plan 6.2) that run without a network:

- the server is killed in the middle of a booking: on restart there is no
  half-written appointment, and retrying the same request books exactly once;
- killed just after the commit, before Emma could answer: the retry returns
  the stored result instead of a second appointment;
- killed while a recovery call was ringing or in progress: the job is closed
  with a task and the appointment flagged, nothing is left "ringing";
- the caller hangs up: offered slots are free at once, and a booking that was
  being written finishes and is recorded as booked_hangup.

The others are covered where the code lives: Gemini off (harness --nlu-down and
tests/test_r2_integration.py), ElevenLabs off (tests/test_piper_fallback.py),
Deepgram drop (tests/test_realtime_stt.py reconnect tests), Calendar off
(tests/test_calendar_sync.py retries).
"""

import asyncio
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from datetime import datetime

import clock
import config
import db
import outbound
import scheduling
import seed_demo
from test_realtime_support import engine, make_session, result

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = datetime(2026, 10, 6, 10, 0)
START = datetime(2026, 10, 7, 11, 0)

CHILD = textwrap.dedent("""
    import os, sys
    sys.path.insert(0, {root!r})
    import config
    config.DB_PATH, config.DEMO_SEED_ON_EMPTY = {db_path!r}, False
    import clock, db, scheduling
    from datetime import datetime
    mode = {mode!r}
    if mode == "during":
        # Die inside the transaction, after the appointment row and its slot claims
        # were written but before COMMIT: what a power cut or kill -9 would do.
        def die(*a, **k):
            os._exit(9)
        scheduling._outbox = die
    with clock.frozen(datetime(2026, 10, 6, 10, 0)):
        conn = db.connect(config.DB_PATH)
        r = scheduling.book(conn, service="Consultation", doctor_id=1, start=clock.localize(datetime(2026, 10, 7, 11, 0)),
                            patient_name="Priya Sharma", phone="9876543210", idem_key="call-x-book-1")
        print("booked", r.ok, flush=True)
    if mode == "after":
        os._exit(9)            # committed, but the process dies before anyone hears about it
""")


class Clinic:
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (config.DB_PATH, config.DEMO_SEED_ON_EMPTY)
        db.reset()
        config.DB_PATH = os.path.join(self.tmp.name, "emma.db")
        config.DEMO_SEED_ON_EMPTY = False
        self.db = db.get_db()
        self.db.run_sync(seed_demo.seed_catalog)
        self.frozen = clock.frozen(NOW)
        self.frozen.__enter__()
        return self

    def __exit__(self, *exc):
        self.frozen.__exit__(*exc)
        db.reset()
        config.DB_PATH, config.DEMO_SEED_ON_EMPTY = self.saved
        self.tmp.cleanup()

    def count(self, sql, *args):
        return self.db.run_sync(lambda c: c.execute(sql, args).fetchone()[0])

    def child(self, mode):
        script = CHILD.format(root=ROOT, db_path=config.DB_PATH, mode=mode)
        return subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)

    def retry(self):
        return self.db.run_sync(scheduling.book, service="Consultation", doctor_id=1, start=clock.localize(START),
                                patient_name="Priya Sharma", phone="9876543210", idem_key="call-x-book-1")


class KillMidBookingTests(unittest.TestCase):
    def test_killed_inside_the_transaction_leaves_nothing_and_the_retry_books_once(self):
        with Clinic() as c:
            db.reset()                                           # the child process owns the file now
            proc = c.child("during")
            self.assertEqual(proc.returncode, 9, proc.stderr)
            self.assertNotIn("booked", proc.stdout)
            c.db = db.get_db()                                   # "restart"
            self.assertEqual(c.count("SELECT COUNT(*) FROM appointments"), 0)
            self.assertEqual(c.count("SELECT COUNT(*) FROM slot_claims"), 0)
            self.assertEqual(c.count("SELECT COUNT(*) FROM sync_outbox"), 0)
            first = c.retry()
            self.assertTrue(first.ok, first.code)
            again = c.retry()
            self.assertTrue(again.replayed)
            self.assertEqual(c.count("SELECT COUNT(*) FROM appointments"), 1)

    def test_killed_after_the_commit_the_retry_is_replayed_not_doubled(self):
        with Clinic() as c:
            db.reset()
            proc = c.child("after")
            self.assertIn("booked True", proc.stdout, proc.stderr)
            c.db = db.get_db()
            self.assertEqual(c.count("SELECT COUNT(*) FROM appointments"), 1)
            retry = c.retry()
            self.assertTrue(retry.ok and retry.replayed)
            self.assertEqual(c.count("SELECT COUNT(*) FROM appointments"), 1)
            self.assertEqual(c.count("SELECT COUNT(*) FROM sync_outbox"), 1)


class RestartDuringRecoveryTests(unittest.TestCase):
    def test_a_job_left_ringing_is_closed_with_a_task(self):
        with Clinic() as c:
            booked = c.db.run_sync(scheduling.book, service="Consultation", doctor_id=1,
                                   start=clock.localize(START), patient_name="Priya Sharma", phone="9876543210",
                                   idem_key="b1")
            block_id = c.db.run_sync(outbound.create_block, doctor_id=1, start=clock.localize(datetime(2026, 10, 7, 9)),
                                     end=clock.localize(datetime(2026, 10, 7, 17)), reason="illness")
            c.db.run_sync(outbound.start_campaign, block_id=block_id, appointment_ids=[booked.appointment_id])
            job = c.db.run_sync(outbound.next_job)
            self.assertEqual(c.db.run_sync(outbound.claim_job, job["id"], "c1")[0], "ring")
            # The server dies here; on start the runner tidies up.
            self.assertEqual(c.db.run_sync(outbound.recover_interrupted), 1)
            job = c.db.run_sync(outbound.get_job, job["id"])
            self.assertEqual((job["status"], job["outcome"]), ("failed", "interrupted"))
            self.assertEqual(c.db.run_sync(scheduling.get_appointment, booked.appointment_id)["status"],
                             "needs_reschedule")
            self.assertEqual(c.count("SELECT COUNT(*) FROM tasks WHERE kind = 'recovery_failed'"), 1)


class HangUpTests(unittest.TestCase):
    def test_offered_slots_are_released_at_hang_up(self):
        with Clinic() as c:
            slot = c.db.run_sync(scheduling.find_slots, service="Consultation", dates=[START.date()], limit=1)[0]

            async def run():
                s, _ = make_session()
                self.assertIsNotNone(c.db.run_sync(scheduling.hold, slot, s.call_id))
                self.assertEqual(c.count("SELECT COUNT(*) FROM slot_holds"), 1)
                await s.close()

            with engine(lambda *a, **k: None):
                asyncio.run(run())
            self.assertEqual(c.count("SELECT COUNT(*) FROM slot_holds"), 0)
            self.assertEqual(c.count("SELECT COUNT(*) FROM slot_claims"), 0)

    def test_a_booking_being_written_at_hang_up_finishes_and_is_recorded(self):
        async def slow_booking(text, s, progress=None):
            progress("commit")
            await asyncio.sleep(0.3)                  # the database write
            return result("You're all booked.", action="booked")

        async def run():
            s, _ = make_session()
            await s._start_turn("Yes, book it.", time.perf_counter())
            await asyncio.sleep(0.1)
            self.assertEqual(s._turn_phase, "commit")
            await s.close()                           # the caller hangs up now
            return s.outcome

        with engine(slow_booking):
            self.assertEqual(asyncio.run(run()), "booked_hangup")


if __name__ == "__main__":
    unittest.main()
