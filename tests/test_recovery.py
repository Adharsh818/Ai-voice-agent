"""
Doctor-unavailability recovery calls (plan 5.11, Day 5 exit criteria):

- a block produces an accurate preview, and nothing new can be booked in it;
- approved calls offer only valid slots;
- accept -> atomic reschedule + calendar outbox row;
- decline / no answer -> task, appointment flagged NEEDS RESCHEDULE;
- a stale appointment is skipped;
- an inbound call during a campaign pauses the runner;
plus the call script: identity before details, wrong person, preference
first, recap-heard rule, several appointments, cancel, honesty line, the
reason category never spoken, lifting a block stops the campaign.
"""

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta

import clock
import config
import db
import outbound
import scheduling
import seed_demo
from dialogue import recovery
from unittest.mock import patch

NOW = datetime(2026, 10, 6, 10, 0)            # a Tuesday, clinic time
PRIYA = "+919876543210"
RAVI = "+919812345678"


class RecoveryClinic:
    """The DEMO catalog (4 branches, 8 doctors, no appointments) in a throwaway database, clock frozen."""

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

    def run(self, fn, *args, **kwargs):
        return self.db.run_sync(fn, *args, **kwargs)

    def doctor(self, spoken: str) -> int:
        return self.run(lambda c: c.execute("SELECT id FROM doctors WHERE spoken_name = ?", (spoken,)).fetchone()[0])

    def book(self, doctor: str, start: datetime, name: str, phone: str, service: str = "Consultation",
             caller: str = None) -> str:
        result = self.run(scheduling.book, service=service, doctor_id=self.doctor(doctor),
                          start=clock.localize(start), patient_name=name, caller_name=caller or name, phone=phone,
                          idem_key=f"test-{name}-{start.isoformat()}")
        assert result.ok, result.code
        return result.appointment_id

    def block(self, doctor: str, start: datetime, end: datetime, reason: str = "illness") -> int:
        return self.run(outbound.create_block, doctor_id=self.doctor(doctor), start=clock.localize(start),
                        end=clock.localize(end), reason=reason, actor="test")

    def appt(self, appointment_id: str) -> dict:
        return self.run(scheduling.get_appointment, appointment_id)


def _day(d: int, h: int, m: int = 0) -> datetime:
    return datetime(2026, 10, d, h, m)


class FakeGate:
    def __init__(self):
        self.call_id = None

    @property
    def busy(self):
        return self.call_id is not None

    def try_acquire(self, kind, call_id):
        if self.busy:
            return False
        self.call_id = call_id
        return True

    def release(self, call_id):
        if self.call_id == call_id:
            self.call_id = None


class AsyncDB:
    """outbound.Runner's database: run() on the TempClinic's thread."""

    def __init__(self, clinic):
        self.clinic = clinic

    async def run(self, fn, *args, **kwargs):
        return self.clinic.run(fn, *args, **kwargs)


class Call:
    """Drive dialogue.recovery like call_session does, recording what Emma said."""

    def __init__(self, clinic, job_id: int):
        job = clinic.run(outbound.get_job, job_id)
        rows = [clinic.run(lambda c, a=a: dict(c.execute(outbound._APPT_SQL + " WHERE a.id = ?", (a,)).fetchone()))
                for a in job["appointment_ids"]]
        block = clinic.run(outbound.campaign_block, job["campaign_id"])
        self.ctx = recovery.new_context(call_id="testcall", job_id=job_id, phone_e164=job["phone_e164"],
                                        appointments=rows, block=block)
        self.clinic = clinic
        self.lines = []
        self.checks = []

    def say(self, text: str = "", heard: bool = True) -> recovery.Reply:
        self.ctx.last_reply_heard = heard
        reply = recovery.process_turn(self.ctx, text, run=self.clinic.run,
                                      progress=lambda event, **d: self.checks.append(d.get("phrase")))
        self.lines.append(reply.text)
        return reply


def _campaign(clinic, block_id, appointment_ids):
    campaign_id = clinic.run(outbound.start_campaign, block_id=block_id, appointment_ids=appointment_ids)
    jobs = clinic.run(lambda c: [r["id"] for r in c.execute(
        "SELECT id FROM outbound_jobs WHERE campaign_id = ? ORDER BY id", (campaign_id,))])
    return campaign_id, jobs


def _claim(clinic, job_id):
    verdict, claimed = clinic.run(outbound.claim_job, job_id, "testcall")
    return verdict, claimed


class BlockAndPreviewTests(unittest.TestCase):
    def test_preview_lists_only_booked_appointments_in_the_block_grouped_by_phone(self):
        with RecoveryClinic() as c:
            a1 = c.book("Dr Rao", _day(7, 11), "Priya Sharma", PRIYA)
            a2 = c.book("Dr Rao", _day(7, 15), "Aarav Sharma", PRIYA, caller="Priya Sharma")
            a3 = c.book("Dr Rao", _day(7, 12), "Ravi Kumar", RAVI)
            c.book("Dr Rao", _day(8, 11), "Outside Block", "+919800000001")          # another day
            c.book("Dr Shetty", _day(7, 16), "Other Doctor", "+919800000002")        # another doctor
            cancelled = c.book("Dr Rao", _day(7, 10), "Was Cancelled", "+919800000003")
            c.run(scheduling.cancel, cancelled, idem_key="x")
            block_id = c.block("Dr Rao", _day(7, 9), _day(7, 17))
            preview = c.run(outbound.preview, block_id)
            self.assertEqual(preview["count"], 3)
            ids = {a["id"] for g in preview["groups"] for a in g["appointments"]}
            self.assertEqual(ids, {a1, a2, a3})
            self.assertEqual(len(preview["groups"]), 2)
            priya = next(g for g in preview["groups"] if len(g["appointments"]) == 2)
            self.assertNotIn("9876543210", priya["phone_masked"])        # masked on screen
            # Nothing was called and nothing changed yet.
            self.assertEqual(c.appt(a1)["status"], "booked")

    def test_block_is_effective_at_once(self):
        with RecoveryClinic() as c:
            c.block("Dr Rao", _day(7, 9), _day(7, 17))
            why = c.run(scheduling.validate, service="Consultation", doctor_id=c.doctor("Dr Rao"),
                        start=clock.localize(_day(7, 11)))
            self.assertIn("DOCTOR_BLOCKED", why)

    def test_bad_blocks_are_refused(self):
        with RecoveryClinic() as c:
            with self.assertRaises(outbound.RecoveryError):
                c.block("Dr Rao", _day(7, 17), _day(7, 9))
            with self.assertRaises(outbound.RecoveryError):
                c.block("Dr Rao", _day(7, 9), _day(7, 17), reason="gossip")


class CampaignTests(unittest.TestCase):
    def test_one_job_per_phone_with_a_version_snapshot(self):
        with RecoveryClinic() as c:
            a1 = c.book("Dr Rao", _day(7, 11), "Priya Sharma", PRIYA)
            a2 = c.book("Dr Rao", _day(7, 15), "Aarav Sharma", PRIYA, caller="Priya Sharma")
            a3 = c.book("Dr Rao", _day(7, 12), "Ravi Kumar", RAVI)
            block_id = c.block("Dr Rao", _day(7, 9), _day(7, 17))
            _, jobs = _campaign(c, block_id, [a1, a2, a3])
            self.assertEqual(len(jobs), 2)
            job = c.run(outbound.get_job, jobs[0])
            self.assertEqual(job["appointment_ids"], [a1, a2])
            self.assertEqual(job["snapshot"], {a1: 1, a2: 1})
            with self.assertRaises(outbound.RecoveryError):          # not twice
                _campaign(c, block_id, [a1])

    def test_stale_appointment_is_skipped(self):
        with RecoveryClinic() as c:
            a1 = c.book("Dr Rao", _day(7, 11), "Priya Sharma", PRIYA)
            block_id = c.block("Dr Rao", _day(7, 9), _day(7, 17))
            _, jobs = _campaign(c, block_id, [a1])
            # Staff moved it from the dashboard before Emma got to it.
            moved = c.run(scheduling.reschedule, a1, doctor_id=c.doctor("Dr Shetty"),
                          start=clock.localize(_day(7, 16)), idem_key="dash-move")
            self.assertTrue(moved.ok)
            verdict, outcome = _claim(c, jobs[0])
            self.assertEqual((verdict, outcome), ("skipped", "stale"))
            self.assertEqual(c.run(outbound.get_job, jobs[0])["status"], "skipped")
            self.assertEqual(c.appt(a1)["doctor"], "Dr Shetty")        # left alone

    def test_do_not_call_is_skipped(self):
        with RecoveryClinic() as c:
            a1 = c.book("Dr Rao", _day(7, 11), "Priya Sharma", PRIYA)
            block_id = c.block("Dr Rao", _day(7, 9), _day(7, 17))
            _, jobs = _campaign(c, block_id, [a1])
            c.run(outbound.set_do_not_call, PRIYA)
            self.assertEqual(_claim(c, jobs[0]), ("skipped", "do_not_call"))

    def test_stop_and_lift_cancel_queued_jobs_only(self):
        with RecoveryClinic() as c:
            a1 = c.book("Dr Rao", _day(7, 11), "Priya Sharma", PRIYA)
            a3 = c.book("Dr Rao", _day(7, 12), "Ravi Kumar", RAVI)
            block_id = c.block("Dr Rao", _day(7, 9), _day(7, 17))
            campaign_id, jobs = _campaign(c, block_id, [a1, a3])
            self.assertEqual(_claim(c, jobs[0])[0], "ring")             # first call in progress
            result = c.run(outbound.lift_block, block_id)
            self.assertEqual(result["jobs_stopped"], 1)
            self.assertEqual(c.run(outbound.get_job, jobs[0])["status"], "ringing")    # finishes
            self.assertEqual(c.run(outbound.get_job, jobs[1])["outcome"], "block_lifted")
            why = c.run(scheduling.validate, service="Consultation", doctor_id=c.doctor("Dr Rao"),
                        start=clock.localize(_day(7, 11, 30)))
            self.assertNotIn("DOCTOR_BLOCKED", why)


class RunnerTests(unittest.TestCase):
    def _runner(self, c, gate=None, timeout=0.05):
        return outbound.Runner(AsyncDB(c), gate or FakeGate(), ring_timeout_s=timeout, window_check=lambda: True)

    def test_no_answer_is_one_attempt_then_a_task_and_needs_reschedule(self):
        with RecoveryClinic() as c:
            a1 = c.book("Dr Rao", _day(7, 11), "Priya Sharma", PRIYA)
            block_id = c.block("Dr Rao", _day(7, 9), _day(7, 17))
            _, jobs = _campaign(c, block_id, [a1])
            runner = self._runner(c)
            self.assertEqual(asyncio.run(runner.step()), "no_answer")
            job = c.run(outbound.get_job, jobs[0])
            self.assertEqual((job["status"], job["outcome"], job["attempts"]), ("failed", "no_answer", 1))
            self.assertEqual(c.appt(a1)["status"], "needs_reschedule")
            tasks = c.run(lambda conn: [dict(r) for r in conn.execute("SELECT * FROM tasks")])
            self.assertEqual([t["kind"] for t in tasks], ["recovery_failed"])
            self.assertIsNone(asyncio.run(runner.step()))                # nothing left: no second attempt
            outbox = c.run(lambda conn: conn.execute("SELECT COUNT(*) FROM sync_outbox WHERE appointment_id = ?",
                                                     (a1,)).fetchone()[0])
            self.assertEqual(outbox, 1)                                   # Calendar shows NEEDS RESCHEDULE

    def test_decline_makes_a_task(self):
        with RecoveryClinic() as c:
            a1 = c.book("Dr Rao", _day(7, 11), "Priya Sharma", PRIYA)
            block_id = c.block("Dr Rao", _day(7, 9), _day(7, 17))
            _, jobs = _campaign(c, block_id, [a1])
            runner = self._runner(c, timeout=5)

            async def go():
                step = asyncio.create_task(runner.step())
                for _ in range(100):
                    if runner.ringing:
                        break
                    await asyncio.sleep(0.01)
                ring = runner.ringing
                self.assertTrue(runner.decline(ring.claimed.job_id, ring.token))
                return await step

            self.assertEqual(asyncio.run(go()), "declined")
            job = c.run(outbound.get_job, jobs[0])
            self.assertEqual((job["status"], job["outcome"]), ("failed", "declined"))
            self.assertEqual(c.appt(a1)["status"], "needs_reschedule")

    def test_inbound_call_pauses_the_campaign(self):
        with RecoveryClinic() as c:
            a1 = c.book("Dr Rao", _day(7, 11), "Priya Sharma", PRIYA)
            block_id = c.block("Dr Rao", _day(7, 9), _day(7, 17))
            _, jobs = _campaign(c, block_id, [a1])
            gate = FakeGate()
            gate.try_acquire("inbound", "caller1")
            runner = self._runner(c, gate)
            self.assertEqual(asyncio.run(runner.step()), "paused")
            self.assertEqual(c.run(outbound.get_job, jobs[0])["status"], "queued")
            self.assertIn("another call", runner.paused_reason)

    def test_outside_the_calling_window_waits(self):
        with RecoveryClinic() as c:
            a1 = c.book("Dr Rao", _day(7, 11), "Priya Sharma", PRIYA)
            block_id = c.block("Dr Rao", _day(7, 9), _day(7, 17))
            _campaign(c, block_id, [a1])
            runner = outbound.Runner(AsyncDB(c), FakeGate(), ring_timeout_s=0.05, window_check=lambda: False)
            self.assertEqual(asyncio.run(runner.step()), "paused")
        with clock.frozen(datetime(2026, 10, 6, 21, 30)):
            self.assertFalse(outbound.in_calling_window())
        with clock.frozen(datetime(2026, 10, 6, 10, 0)):
            self.assertTrue(outbound.in_calling_window())

    def test_answered_call_result_is_recorded(self):
        with RecoveryClinic() as c:
            a1 = c.book("Dr Rao", _day(7, 11), "Priya Sharma", PRIYA)
            block_id = c.block("Dr Rao", _day(7, 9), _day(7, 17))
            _, jobs = _campaign(c, block_id, [a1])
            runner = self._runner(c, timeout=5)

            async def go():
                step = asyncio.create_task(runner.step())
                for _ in range(100):
                    if runner.ringing:
                        break
                    await asyncio.sleep(0.01)
                ring = runner.answer(runner.ringing.claimed.job_id, runner.ringing.token)
                ring.connected.set()
                ring.result = {"outcome": "rescheduled", "unresolved": []}
                ring.call_done.set()
                return await step

            self.assertEqual(asyncio.run(go()), "rescheduled")
            job = c.run(outbound.get_job, jobs[0])
            self.assertEqual((job["status"], job["outcome"]), ("done", "rescheduled"))


class RecoveryCallTests(unittest.TestCase):
    def _call(self, c, appts, reason="illness"):
        block_id = c.block("Dr Rao", _day(7, 9), _day(7, 17), reason=reason)
        _, jobs = _campaign(c, block_id, appts)
        self.assertEqual(_claim(c, jobs[0])[0], "ring")
        return Call(c, jobs[0])

    def test_happy_path_another_doctor_same_branch(self):
        with RecoveryClinic() as c:
            a1 = c.book("Dr Rao", _day(7, 15), "Priya Sharma", PRIYA)
            call = self._call(c, [a1])
            greeting = call.say("")
            self.assertIn("Priya", greeting.text)
            self.assertNotIn("Dr Rao", greeting.text)                # nothing before identity
            explain = call.say("Yes, speaking.")
            self.assertIn("Dr Rao", explain.text)
            self.assertIn("isn't available", explain.text)
            offer = call.say("Another doctor is fine.")
            self.assertTrue(call.checks)                              # "let me have a look" + typing
            self.assertEqual(call.ctx.state, "offer")
            for slot in call.ctx.offers:                             # only valid slots, never Dr Rao
                self.assertNotEqual(slot.doctor, "Dr Rao")
                self.assertEqual(c.run(scheduling.validate, service=slot.service_id, doctor_id=slot.doctor_id,
                                       start=slot.start, ignore_appointment=a1), [])
            self.assertIn("Dr Shetty", offer.text)
            recap = call.say("Yes, that works.")
            self.assertEqual(call.ctx.state, "recap")
            self.assertRegex(recap.text, r"Shall I move it\?|Is that okay\?")
            self.assertIn("Dr Shetty", recap.text)
            done = call.say("Yes please.")
            self.assertEqual(done.action, "rescheduled")
            self.assertTrue(call.ctx.closed_conversation)
            moved = c.appt(a1)
            self.assertEqual((moved["status"], moved["doctor"]), ("booked", "Dr Shetty"))
            self.assertEqual(moved["version"], 2)
            outbox = c.run(lambda conn: conn.execute("SELECT COUNT(*) FROM sync_outbox WHERE appointment_id = ?",
                                                     (a1,)).fetchone()[0])
            self.assertEqual(outbox, 1)
            self.assertEqual(recovery.call_result(call.ctx)["outcome"], "rescheduled")
            self.assertEqual(recovery.call_result(call.ctx)["unresolved"], [])

    def test_same_doctor_preference_offers_only_that_doctor_on_other_days(self):
        with RecoveryClinic() as c:
            a1 = c.book("Dr Rao", _day(7, 15), "Priya Sharma", PRIYA)
            call = self._call(c, [a1])
            call.say("")
            call.say("yes")
            call.say("I'd rather wait for Dr Rao.")
            self.assertTrue(call.ctx.offers)
            for slot in call.ctx.offers:
                self.assertEqual(slot.doctor, "Dr Rao")
                self.assertNotEqual(slot.start.date(), _day(7, 0).date())

    def test_named_day_is_searched(self):
        with RecoveryClinic() as c:
            a1 = c.book("Dr Rao", _day(7, 15), "Priya Sharma", PRIYA)
            call = self._call(c, [a1])
            call.say("")
            call.say("yes")
            call.say("Do you have something on Friday evening?")
            self.assertTrue(call.ctx.offers)
            for slot in call.ctx.offers:
                self.assertEqual(slot.start.date(), _day(9, 0).date())
                self.assertGreaterEqual(slot.start.hour, 16)

    def test_no_to_all_offers_then_on_hold(self):
        with RecoveryClinic() as c:
            a1 = c.book("Dr Rao", _day(7, 15), "Priya Sharma", PRIYA)
            call = self._call(c, [a1])
            call.say("")
            call.say("yes")
            call.say("any doctor")
            alt = call.say("No, that doesn't work.")
            self.assertIn("could also do", alt.text)
            call.say("No, neither.")
            self.assertEqual(call.ctx.state, "preference")
            bye = call.say("Let me think about it and call you back.")
            self.assertTrue(call.ctx.closed_conversation)
            result = recovery.call_result(call.ctx)
            self.assertEqual(result["outcome"], "pending")
            self.assertEqual(result["unresolved"], [a1])
            self.assertIn("front desk", " ".join(call.lines))
            self.assertEqual(c.appt(a1)["status"], "booked")          # finish_job flags it, not the dialogue

    def test_wrong_person_hears_no_details(self):
        with RecoveryClinic() as c:
            a1 = c.book("Dr Rao", _day(7, 15), "Priya Sharma", PRIYA)
            call = self._call(c, [a1])
            call.say("")
            reply = call.say("No, this is her husband.")
            self.assertTrue(call.ctx.closed_conversation)
            self.assertEqual(call.ctx.outcome, "wrong_person")
            for word in ("Dr Rao", "consultation", "Wednesday", "appointment", "2"):
                self.assertNotIn(word, reply.text)

    def test_who_is_this_then_scam_question(self):
        with RecoveryClinic() as c:
            a1 = c.book("Dr Rao", _day(7, 15), "Priya Sharma", PRIYA)
            call = self._call(c, [a1])
            call.say("")
            who = call.say("Sorry, who is this?")
            self.assertIn("Pearl Dental", who.text)
            self.assertNotIn("Dr Rao", who.text)
            scam = call.say("How do I know this isn't a scam?")
            self.assertTrue(call.ctx.closed_conversation)
            self.assertEqual(call.ctx.outcome, "suspicious")
            self.assertIn("call Pearl Dental directly", scam.text)

    def test_recap_heard_rule(self):
        with RecoveryClinic() as c:
            a1 = c.book("Dr Rao", _day(7, 15), "Priya Sharma", PRIYA)
            call = self._call(c, [a1])
            call.say("")
            call.say("yes")
            call.say("whatever is earliest")
            call.say("yes")
            self.assertEqual(call.ctx.state, "recap")
            again = call.say("yes", heard=False)                      # talked over the recap
            self.assertEqual(call.ctx.state, "recap")
            self.assertIsNone(again.action)
            self.assertEqual(c.appt(a1)["version"], 1)
            self.assertEqual(call.say("yes").action, "rescheduled")

    def test_two_appointments_are_handled_one_by_one(self):
        with RecoveryClinic() as c:
            a1 = c.book("Dr Rao", _day(7, 11), "Priya Sharma", PRIYA)
            a2 = c.book("Dr Rao", _day(7, 15), "Aarav Sharma", PRIYA, service="Teeth Cleaning",
                        caller="Priya Sharma")
            call = self._call(c, [a1, a2])
            call.say("")
            explain = call.say("yes")
            self.assertIn("other appointment", explain.text)
            call.say("any doctor is fine")
            call.say("yes")
            moved = call.say("yes")
            self.assertEqual(moved.action, "rescheduled")
            self.assertIn("Aarav's cleaning", moved.text)               # the child's visit, named
            self.assertEqual(call.ctx.state, "next")
            call.say("yes please")
            self.assertEqual(call.ctx.state, "offer")
            call.say("yes")
            last = call.say("yes")
            self.assertEqual(last.action, "rescheduled")
            self.assertTrue(call.ctx.closed_conversation)
            self.assertEqual({c.appt(a1)["version"], c.appt(a2)["version"]}, {2})

    def test_cancel(self):
        with RecoveryClinic() as c:
            a1 = c.book("Dr Rao", _day(7, 15), "Priya Sharma", PRIYA)
            call = self._call(c, [a1])
            call.say("")
            call.say("yes")
            ask = call.say("Actually just cancel it, I don't need it anymore.")
            self.assertEqual(call.ctx.state, "confirm_cancel")
            self.assertIn("cancel", ask.text)
            done = call.say("Yes.")
            self.assertEqual(done.action, "cancelled")
            self.assertEqual(c.appt(a1)["status"], "cancelled")
            self.assertEqual(recovery.call_result(call.ctx)["outcome"], "cancelled")

    def test_honesty_line_then_carries_on(self):
        with RecoveryClinic() as c:
            a1 = c.book("Dr Rao", _day(7, 15), "Priya Sharma", PRIYA)
            call = self._call(c, [a1])
            call.say("")
            reply = call.say("Wait, am I talking to a robot?")
            self.assertIn(config.HONEST_LINE, reply.text)
            self.assertIn("Priya", reply.text)                         # and asks again who it is
            self.assertFalse(call.ctx.closed_conversation)

    def test_do_not_call_request(self):
        with RecoveryClinic() as c:
            a1 = c.book("Dr Rao", _day(7, 15), "Priya Sharma", PRIYA)
            call = self._call(c, [a1])
            call.say("")
            call.say("yes")
            call.say("Please don't call me again.")
            self.assertEqual(call.ctx.outcome, "do_not_call")
            self.assertTrue(c.run(outbound.do_not_call, PRIYA))

    def test_reason_category_is_never_spoken(self):
        with RecoveryClinic() as c:
            a1 = c.book("Dr Rao", _day(7, 15), "Priya Sharma", PRIYA)
            call = self._call(c, [a1], reason="illness")
            call.say("")
            call.say("yes")
            call.say("Why, what happened?")
            call.say("ok another doctor then")
            text = " ".join(call.lines).lower()
            for word in ("ill", "sick", "illness", "training", "personal", "emergency"):
                self.assertNotRegex(text, rf"\b{word}\b")

    def test_hang_up_mid_call_leaves_it_for_staff(self):
        with RecoveryClinic() as c:
            a1 = c.book("Dr Rao", _day(7, 15), "Priya Sharma", PRIYA)
            call = self._call(c, [a1])
            call.say("")
            call.say("yes")
            result = recovery.call_result(call.ctx)
            self.assertEqual(result["outcome"], "abandoned")
            self.assertEqual(result["unresolved"], [a1])

    def test_lines_do_not_repeat_word_for_word(self):
        with RecoveryClinic() as c:
            a1 = c.book("Dr Rao", _day(7, 15), "Priya Sharma", PRIYA)
            call = self._call(c, [a1])
            call.say("")
            call.say("yes")
            call.say("hmm")
            call.say("not sure")
            self.assertNotEqual(call.lines[-1], call.lines[-2])


class RecoveryApiTests(unittest.TestCase):
    """The dashboard's Recovery tab and the /patient phone, over HTTP."""

    def setUp(self):
        import auth
        import events
        import server
        from starlette.testclient import TestClient
        self.auth, self.server = auth, server
        self.clinic = RecoveryClinic().__enter__()
        self.password = "correct horse battery"
        self.patch = patch.object(config, "DASHBOARD_PASSWORD_HASH", auth.hash_password(self.password, n=2 ** 12))
        self.patch.start()
        auth.limiter.reset()
        events.reset()
        server.app.state.gate = server.CallGate()
        self.runner = outbound.Runner(AsyncDB(self.clinic), server.app.state.gate, window_check=lambda: True)
        outbound.set_runner(self.runner)
        self.client = TestClient(server.app)

    def tearDown(self):
        self.client.close()
        outbound.set_runner(None)
        self.patch.stop()
        self.auth.limiter.reset()
        self.clinic.__exit__(None, None, None)

    def login(self):
        response = self.client.post("/dashboard/api/login", json={"password": self.password})
        self.assertEqual(response.status_code, 200, response.text)

    def test_everything_needs_the_staff_login(self):
        self.assertEqual(self.client.get("/dashboard/api/recovery").status_code, 401)
        self.assertEqual(self.client.get("/patient/api/ring").status_code, 401)
        self.assertEqual(self.client.get("/patient", follow_redirects=False).status_code, 303)

    def test_block_preview_campaign_ring_decline(self):
        c = self.clinic
        a1 = c.book("Dr Rao", _day(7, 11), "Priya Sharma", PRIYA)
        self.login()
        page = self.client.get("/patient")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Patient phone", page.text)
        res = self.client.post("/dashboard/api/recovery/blocks", json={
            "doctor_id": c.doctor("Dr Rao"), "start": "2026-10-07T09:00", "end": "2026-10-07T17:00",
            "reason": "illness"})
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["preview"]["count"], 1)
        bad = self.client.post("/dashboard/api/recovery/blocks", json={
            "doctor_id": c.doctor("Dr Rao"), "start": "2026-10-07T17:00", "end": "2026-10-07T09:00",
            "reason": "illness"})
        self.assertEqual(bad.status_code, 400)
        res = self.client.post("/dashboard/api/recovery/campaigns",
                               json={"block_id": body["block_id"], "appointment_ids": [a1]})
        self.assertEqual(res.status_code, 200, res.text)
        overview = self.client.get("/dashboard/api/recovery").json()
        job = overview["campaigns"][0]["jobs"][0]
        self.assertEqual((job["status"], job["name"]), ("queued", "Priya Sharma"))
        self.assertNotIn("9876543210", job["phone_masked"])

        self.assertIsNone(self.client.get("/patient/api/ring").json()["ringing"])
        verdict, claimed = c.run(outbound.claim_job, job["id"], "c1")
        self.runner.ringing = outbound.Ringing(claimed, "c1", "secret-token")
        ring = self.client.get("/patient/api/ring").json()["ringing"]
        self.assertEqual((ring["job_id"], ring["to_name"]), (job["id"], "Priya"))
        wrong = self.client.post("/patient/api/decline", json={"job_id": job["id"], "token": "nope"})
        self.assertEqual(wrong.status_code, 409)
        ok = self.client.post("/patient/api/decline", json={"job_id": job["id"], "token": "secret-token"})
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(self.runner.ringing.declined)

    def test_outbound_socket_refuses_without_a_ringing_job(self):
        from starlette.websockets import WebSocketDisconnect
        self.login()
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("/ws/outbound?job=1&token=x") as ws:
                ws.receive_text()

    def test_stop_and_lift_over_http(self):
        c = self.clinic
        a1 = c.book("Dr Rao", _day(7, 11), "Priya Sharma", PRIYA)
        self.login()
        block_id = self.client.post("/dashboard/api/recovery/blocks", json={
            "doctor_id": c.doctor("Dr Rao"), "start": "2026-10-07T09:00", "end": "2026-10-07T17:00",
            "reason": "training"}).json()["block_id"]
        campaign_id = self.client.post("/dashboard/api/recovery/campaigns",
                                       json={"block_id": block_id, "appointment_ids": [a1]}).json()["campaign_id"]
        stop = self.client.post(f"/dashboard/api/recovery/campaigns/{campaign_id}/stop", json={})
        self.assertEqual(stop.json()["jobs_stopped"], 1)
        lift = self.client.post(f"/dashboard/api/recovery/blocks/{block_id}/lift", json={})
        self.assertTrue(lift.json()["ok"])
        self.assertEqual(self.client.get("/dashboard/api/recovery").json()["blocks"], [])


if __name__ == "__main__":
    unittest.main()
