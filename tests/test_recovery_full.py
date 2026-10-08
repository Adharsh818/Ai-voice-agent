"""
C-full recovery calls (plan 13, done 6 Oct): retries, a patient's "call me back
after 6", pause/resume, per-patient calling hours, campaign reporting, and the
upgrade of a database made before these columns existed.
"""

import asyncio
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime

import clock
import config
import db
import outbound
from dialogue import recovery
from test_recovery import (AsyncDB, Call, FakeGate, PRIYA, RAVI, RecoveryClinic, _campaign, _claim, _day)


def runner_for(c, timeout=0.05):
    return outbound.Runner(AsyncDB(c), FakeGate(), ring_timeout_s=timeout, window_check=lambda: True)


class CallBackTests(unittest.TestCase):
    def test_call_me_after_6_is_booked_as_the_next_try(self):
        with RecoveryClinic() as c:
            a1 = c.book("Dr Rao", _day(7, 15), "Priya Sharma", PRIYA)
            block_id = c.block("Dr Rao", _day(7, 9), _day(7, 17))
            _, jobs = _campaign(c, block_id, [a1])
            self.assertEqual(_claim(c, jobs[0])[0], "ring")
            call = Call(c, jobs[0])
            call.say("")
            call.say("yes")
            bye = call.say("I'm driving right now, call me after 6.")
            self.assertIn("after 6", bye.text)
            self.assertTrue(call.ctx.closed_conversation)
            result = recovery.call_result(call.ctx)
            self.assertEqual(result["outcome"], "busy")
            self.assertEqual(datetime.fromisoformat(result["retry_at"]).hour, 18)

            # The runner books that as the next try, even past the usual number of attempts.
            runner = runner_for(c, timeout=5)

            async def go():
                c.run(lambda conn: conn.execute("UPDATE outbound_jobs SET status = 'queued', attempts = 3"))
                step = asyncio.create_task(runner.step())
                for _ in range(100):
                    if runner.ringing:
                        break
                    await asyncio.sleep(0.01)
                ring = runner.answer(runner.ringing.claimed.job_id, runner.ringing.token)
                ring.connected.set()
                ring.result = result
                ring.call_done.set()
                return await step

            self.assertEqual(asyncio.run(go()), "retry")
            job = c.run(outbound.get_job, jobs[0])
            self.assertEqual(job["status"], "queued")
            self.assertEqual(clock.localize(db.local(job["next_attempt_at"])).hour, 18)
            self.assertGreater(job["max_attempts"], job["attempts"])
            self.assertEqual(c.run(lambda conn: conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]), 0)

    def test_call_back_times_are_understood(self):
        with clock.frozen(datetime(2026, 10, 6, 11, 0)):
            cases = {"call me in an hour": (12, 0), "can you call me in 20 minutes": (11, 20),
                     "I'm busy, call me after 6": (18, 0), "call me tomorrow morning": (7, 0)}
            for said, (h, m) in cases.items():
                at, spoken = recovery._callback_time(said)
                self.assertEqual((at.hour, at.minute), (h, m), said)
                self.assertTrue(spoken, said)
            self.assertEqual(recovery._callback_time("call me later"), (None, ""))

    def test_declined_is_still_one_attempt_and_a_task(self):
        # The demo's recovery scene: Rahul declines, staff get a task at once.
        with RecoveryClinic() as c:
            a1 = c.book("Dr Rao", _day(7, 12), "Rahul Verma", RAVI)
            block_id = c.block("Dr Rao", _day(7, 9), _day(7, 17))
            _, jobs = _campaign(c, block_id, [a1])
            runner = runner_for(c, timeout=5)

            async def go():
                step = asyncio.create_task(runner.step())
                for _ in range(100):
                    if runner.ringing:
                        break
                    await asyncio.sleep(0.01)
                runner.decline(runner.ringing.claimed.job_id, runner.ringing.token)
                return await step

            self.assertEqual(asyncio.run(go()), "declined")
            self.assertEqual(c.run(outbound.get_job, jobs[0])["status"], "failed")
            self.assertEqual(c.appt(a1)["status"], "needs_reschedule")


class PauseAndHoursTests(unittest.TestCase):
    def test_pause_holds_the_queue_and_resume_carries_on(self):
        with RecoveryClinic() as c:
            a1 = c.book("Dr Rao", _day(7, 11), "Priya Sharma", PRIYA)
            block_id = c.block("Dr Rao", _day(7, 9), _day(7, 17))
            campaign_id, jobs = _campaign(c, block_id, [a1])
            c.run(outbound.pause_campaign, campaign_id)
            self.assertIsNone(c.run(outbound.next_job))
            self.assertEqual(c.run(outbound.waiting_summary)["paused_campaigns"], 1)
            c.run(outbound.resume_campaign, campaign_id)
            self.assertEqual(c.run(outbound.next_job)["id"], jobs[0])
            c.run(outbound.stop_campaign, campaign_id)
            with self.assertRaises(outbound.RecoveryError):
                c.run(outbound.pause_campaign, campaign_id)

    def test_a_patients_own_hours_hold_their_call(self):
        with RecoveryClinic() as c:                                   # clock: 10:00
            a1 = c.book("Dr Rao", _day(7, 11), "Priya Sharma", PRIYA)
            a2 = c.book("Dr Rao", _day(7, 12), "Ravi Kumar", RAVI)
            block_id = c.block("Dr Rao", _day(7, 9), _day(7, 17))
            _, jobs = _campaign(c, block_id, [a1, a2])
            c.run(outbound.set_call_window, PRIYA, "18:00", None)
            self.assertEqual(c.run(outbound.next_job)["phone_e164"], RAVI)   # Priya waits until 18:00
            nxt = c.run(outbound.next_callable, PRIYA, clock.now())
            self.assertEqual((nxt.hour, nxt.minute), (18, 0))
            with clock.frozen(datetime(2026, 10, 6, 18, 30)):
                self.assertTrue(c.run(outbound.callable_now, PRIYA))
            with clock.frozen(datetime(2026, 10, 6, 20, 30)):            # after the clinic's window
                nxt = c.run(outbound.next_callable, PRIYA, clock.now())
                self.assertEqual((nxt.day, nxt.hour), (7, 18))
            with self.assertRaises(outbound.RecoveryError):
                c.run(outbound.set_call_window, PRIYA, "19:00", "18:00")
            self.assertEqual(c.run(outbound.list_call_windows)[0]["call_after"], "18:00")

    def test_sundays_are_skipped(self):
        with RecoveryClinic():
            # Saturday 20:30 -> Monday 09:00 (the clinic is closed on Sundays)
            with clock.frozen(datetime(2026, 10, 10, 20, 30)):
                conn = db.get_db()
                nxt = conn.run_sync(outbound.next_callable, None, clock.now())
                self.assertEqual((nxt.weekday(), nxt.hour), (0, 9))


class ReportTests(unittest.TestCase):
    def test_summary_and_csv(self):
        with RecoveryClinic() as c:
            a1 = c.book("Dr Rao", _day(7, 15), "Priya Sharma", PRIYA)
            a2 = c.book("Dr Rao", _day(7, 12), "Ravi Kumar", RAVI)
            block_id = c.block("Dr Rao", _day(7, 9), _day(7, 17))
            campaign_id, jobs = _campaign(c, block_id, [a1, a2])
            priya, ravi = sorted(jobs, key=lambda j: c.run(outbound.get_job, j)["phone_e164"] != PRIYA)
            # Priya moves on the call; Ravi's job fails.
            self.assertEqual(_claim(c, priya)[0], "ring")
            call = Call(c, priya)
            for said in ("", "yes", "any doctor is fine", "yes", "yes"):
                call.say(said)
            c.run(outbound.finish_job, priya, status="done", outcome="rescheduled", unresolved=[])
            c.run(outbound.finish_job, ravi, status="failed", outcome="declined", unresolved=[a2])
            campaign = c.run(outbound.list_campaigns)[0]
            self.assertEqual(campaign["summary"]["appointments"]["moved"], 1)
            self.assertEqual(campaign["summary"]["appointments"]["needs_reschedule"], 1)
            self.assertEqual(campaign["summary"]["calls"]["needs_staff"], 1)
            rows = c.run(outbound.campaign_rows, campaign_id)
            self.assertEqual({r["patient"] for r in rows}, {"Priya Sharma", "Ravi Kumar"})
            self.assertTrue(all("9876543210" not in r["phone"] for r in rows))   # masked


class MigrationTests(unittest.TestCase):
    def test_a_database_from_before_upgrades_in_place(self):
        tmp = tempfile.TemporaryDirectory()
        path = os.path.join(tmp.name, "old.db")
        conn = db.connect(path)
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
        script = open(os.path.join(os.path.dirname(db.__file__), "migrations", "001_init.sql"), encoding="utf-8").read()
        conn.executescript("BEGIN;" + script + "INSERT INTO schema_migrations VALUES ('001_init', 'x'); COMMIT;")
        conn.execute("INSERT INTO contact_prefs (phone_e164, do_not_call, updated_at) VALUES ('+919876543210', 1, 'x')")
        conn.commit()
        self.assertEqual(db.migrate(conn), ["002_recovery_full"])
        row = conn.execute("SELECT do_not_call, call_after FROM contact_prefs").fetchone()
        self.assertEqual((row["do_not_call"], row["call_after"]), (1, None))      # kept, new column empty
        self.assertEqual(db.migrate(conn), [])                                      # once only
        conn.close()
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
