"""
MANAGE workflow (dialogue/manage.py) on the real DEMO catalog: verification
(phone + name + appointment date, nothing revealed before it passes), check,
cancel and reschedule, and the race codes scheduling can return at commit.

Calls are driven turn by turn on a hand-built CallContext inside
DemoClinic(appointments=True), the way the engine will drive them:
manage.advance() runs, manage.next_goal() decides the reply, and a stand-in
for policy.note_turn records the goal as pending. Every planned line and
notice is rendered through prompts.render, so a param a line can't use fails
here. No model, no pipeline: E1's engine is not needed.
"""

import asyncio
import pickle
import random
import unittest
from datetime import datetime, timedelta

import clock
import prompts
import scheduling
from dialogue import manage
from dialogue.context import ActionResult, FieldState, Goal, Intent, Understanding, new_context
from dialogue.runtime import Runtime
from dialogue.testing import DemoClinic

E164 = "+919876543210"
OTHER_E164 = "+919812345678"           # a number with no appointments at all
# Thursday 1 Oct 2026, 10:00: Monday is the 5th, Wednesday the 7th.
NOW = datetime(2026, 10, 1, 10, 0)
MONDAY = datetime(2026, 10, 5)


def U(**kw) -> Understanding:
    return Understanding(**kw)


def doctor_id(clinic, spoken: str) -> int:
    return clinic.query("SELECT id FROM doctors WHERE spoken_name = ?", spoken)[0]["id"]


def book_appointment(clinic, *, service="Teeth Cleaning", doctor="Dr Rao", day=MONDAY, hours=(11, 12, 10, 14),
                     name="Priya Sharma", phone=E164, now=None) -> dict:
    """A real booking on a free cell near the first of `hours` (the seed's sample bookings may hold some)."""
    doc = doctor_id(clinic, doctor)
    for hour in hours:
        for minute in (0, 30):
            start = clock.localize(day.replace(hour=hour, minute=minute))
            res = clinic.db.run_sync(scheduling.book, service=service, doctor_id=doc, start=start,
                                     patient_name=name, phone=phone, idem_key=f"test:{name}:{start.isoformat()}",
                                     source="seed", actor="seed", now=now)
            if res.ok:
                return res.appointment
    raise AssertionError(f"no free {service} slot for {doctor} on {day:%a %d %b}")


class Call:
    """One simulated MANAGE call: the engine's steps 6-9 with manage as the only workflow."""

    def __init__(self, clinic, intent: Intent, call_id="call-m1"):
        self.clinic = clinic
        self.ctx = new_context(call_id)
        self.ctx.intent = intent
        self.events = []
        self.rt = Runtime(call_id=call_id, db=clinic.db, catalog=None, kb=None,
                          progress=lambda event, **data: self.events.append((event, data)))
        self.plan = None
        self.result = None
        self.text = ""
        self.notices = []

    def given(self, phone=E164, name="Priya"):
        """A caller whose number was read back and confirmed (carried over from earlier in the call)."""
        c = self.ctx.caller
        c.phone_e164, c.phone_state = phone, FieldState.CONFIRMED
        if name:
            c.name, c.name_state = name, FieldState.HEARD
        return self

    def turn(self, u: Understanding = None, confirmation=None, heard=True):
        u = u or Understanding()
        if confirmation is None:
            confirmation = u.confirmation
        ctx = self.ctx
        ctx.turn += 1
        ctx.last_reply_heard = heard
        self.events.clear()
        self.rt.events = []
        self.result = asyncio.run(manage.advance(ctx, u, confirmation, self.rt))
        self.plan = manage.next_goal(ctx)
        self.notices = [prompts.render(n.line, ctx.prompts, n.params) for n in self.result.notices]
        self.text = prompts.render(self.plan.line, ctx.prompts, self.plan.params) if self.plan else ""
        if self.plan is not None:                     # the stand-in for policy.note_turn
            ctx.stats(self.plan.goal).asked += 1
            ctx.pending = self.plan.goal
        return self

    @property
    def goal(self):
        return self.plan.goal if self.plan else None

    def said(self) -> str:
        return " ".join(self.notices + [self.text])

    def verified(self, date_phrase="Monday"):
        """Phone confirmed, name known: give the appointment's date."""
        self.turn()
        assert self.goal == Goal.ASK_APPT_DATE, self.goal
        return self.turn(U(appt_date_phrase=date_phrase, raw_text=date_phrase))


def status(clinic, appointment_id: str) -> dict:
    return clinic.query("SELECT * FROM appointments WHERE id = ?", appointment_id)[0]


class Verification(unittest.TestCase):

    def test_phone_then_name_then_date(self):
        with DemoClinic(now=NOW, appointments=True) as clinic:
            appt = book_appointment(clinic)
            call = Call(clinic, Intent.CANCEL)
            self.assertEqual(call.turn().goal, Goal.ASK_PHONE)
            call.given(name=None)
            self.assertEqual(call.turn().goal, Goal.ASK_NAME)
            self.assertEqual(call.turn(U(name="Priya", raw_text="Priya")).goal, Goal.ASK_APPT_DATE)
            call.turn(U(appt_date_phrase="Monday", raw_text="it's on Monday"))
            self.assertTrue(call.ctx.manage.verified)
            self.assertEqual(call.ctx.manage.target.appointment_id, appt["id"])
            self.assertEqual(call.goal, Goal.CONFIRM_CANCEL)

    def test_callers_own_name_is_used_and_not_asked_again(self):
        with DemoClinic(now=NOW, appointments=True) as clinic:
            book_appointment(clinic)
            call = Call(clinic, Intent.CHECK).given(name="Priya")
            self.assertEqual(call.turn().goal, Goal.ASK_APPT_DATE)

    def test_first_name_and_stt_spelling_match(self):
        with DemoClinic(now=NOW, appointments=True) as clinic:
            book_appointment(clinic)
            for said in ("Priya", "Prya", "priya sharma"):
                call = Call(clinic, Intent.CHECK).given(name=said)
                call.verified()
                self.assertTrue(call.ctx.manage.verified, said)

    def test_a_date_said_up_front_verifies_at_once(self):
        with DemoClinic(now=NOW, appointments=True) as clinic:
            book_appointment(clinic)
            call = Call(clinic, Intent.CANCEL).given()
            call.turn(U(intent=Intent.CANCEL, date_phrase="Monday", raw_text="cancel my Monday appointment"))
            self.assertTrue(call.ctx.manage.verified)
            self.assertEqual(call.goal, Goal.CONFIRM_CANCEL)

    def _assert_reveals_nothing(self, call, appt):
        m = call.ctx.manage
        self.assertFalse(m.verified)
        self.assertEqual(m.matches, [])
        self.assertIsNone(m.target)
        spoken = appt_words = [appt["doctor"], appt["branch"], appt["service"], "Cleaning", "Nagarbhavi",
                               prompts.speak_time(datetime.fromisoformat(appt["start"]).time())]
        reply = call.said()
        params = repr(call.plan.params)
        state = repr(call.ctx)
        for word in spoken:
            self.assertNotIn(word, reply)
            self.assertNotIn(word, params)
        for word in appt_words[:3] + [appt["id"], appt["start"][:16]]:
            self.assertNotIn(word, state)
        self.assertNotIn(appt["start"][:13].replace("T", " "), state)

    def test_wrong_name_reveals_nothing(self):
        """Z7: the rows of a failed lookup never leave verify()."""
        with DemoClinic(now=NOW, appointments=True) as clinic:
            appt = book_appointment(clinic)
            call = Call(clinic, Intent.CANCEL).given(name="Rahul")
            call.verified("Monday")
            self.assertEqual(call.goal, Goal.VERIFY_FAILED)
            self.assertEqual(call.plan.line, "verify.failed")
            self._assert_reveals_nothing(call, appt)

    def test_wrong_date_reveals_nothing(self):
        with DemoClinic(now=NOW, appointments=True) as clinic:
            appt = book_appointment(clinic)
            call = Call(clinic, Intent.RESCHEDULE).given(name="Priya")
            call.verified("Tuesday")
            self.assertEqual(call.goal, Goal.VERIFY_FAILED)
            self._assert_reveals_nothing(call, appt)
            self.assertEqual(call.result.notices, [])
            self.assertIsNone(call.result.action)

    def test_failed_wording_is_the_same_with_or_without_appointments(self):
        with DemoClinic(now=NOW, appointments=True) as clinic:
            book_appointment(clinic)
            texts, plans = [], []
            for phone in (E164, OTHER_E164):
                call = Call(clinic, Intent.CANCEL).given(phone=phone, name="Rahul")
                call.verified("Monday")
                plans.append((call.goal, call.plan.line, call.plan.params, call.result.notices))
                texts.append(prompts.render(call.plan.line, new_context().prompts, call.plan.params,
                                            rng=random.Random(3)))
            self.assertEqual(plans[0], plans[1])
            self.assertEqual(texts[0], texts[1])

    def test_second_miss_offers_a_callback(self):
        with DemoClinic(now=NOW, appointments=True) as clinic:
            appt = book_appointment(clinic)
            call = Call(clinic, Intent.CANCEL).given(name="Rahul")
            call.verified("Monday")
            call.turn(U(appt_date_phrase="Tuesday", raw_text="maybe Tuesday"))
            self.assertEqual(call.ctx.manage.verify_attempts, 2)
            self.assertEqual(call.goal, Goal.CALLBACK_OFFER)
            self.assertEqual(call.plan.line, "verify.failed.final")
            self._assert_reveals_nothing(call, appt)

    def test_no_new_detail_means_no_second_lookup(self):
        with DemoClinic(now=NOW, appointments=True) as clinic:
            book_appointment(clinic)
            call = Call(clinic, Intent.CANCEL).given(name="Rahul")
            call.verified("Monday")
            call.turn(U(acts=["question"], raw_text="why not?"))
            self.assertEqual(call.ctx.manage.verify_attempts, 1)

    def test_corrected_name_after_a_miss_verifies(self):
        with DemoClinic(now=NOW, appointments=True) as clinic:
            book_appointment(clinic)
            call = Call(clinic, Intent.CHECK).given(name="Rahul")
            call.verified("Monday")
            call.turn(U(patient_name="Priya Sharma", raw_text="it's under Priya Sharma"))
            self.assertTrue(call.ctx.manage.verified)
            self.assertEqual(call.goal, Goal.STATE_APPOINTMENT)

    def test_several_matches_pick_by_time(self):
        with DemoClinic(now=NOW, appointments=True) as clinic:
            book_appointment(clinic)
            second = book_appointment(clinic, service="Tooth Filling", doctor="Dr Shetty", hours=(16, 17, 18))
            call = Call(clinic, Intent.CHECK).given()
            call.verified("Monday")
            self.assertEqual(call.goal, Goal.PICK_APPOINTMENT)
            self.assertIn("Dr Shetty", call.text)
            self.assertIn("Dr Rao", call.text)
            spoken_time = prompts.speak_time(datetime.fromisoformat(second["start"]).time())
            call.turn(U(time_phrase=spoken_time + " pm" if "pm" not in spoken_time else spoken_time,
                        raw_text="the evening one"))
            self.assertEqual(call.ctx.manage.target.appointment_id, second["id"])
            self.assertEqual(call.goal, Goal.STATE_APPOINTMENT)

    def test_several_matches_pick_by_choice(self):
        with DemoClinic(now=NOW, appointments=True) as clinic:
            first = book_appointment(clinic)
            book_appointment(clinic, service="Tooth Filling", doctor="Dr Shetty", hours=(16, 17, 18))
            call = Call(clinic, Intent.CHECK).given()
            call.verified("Monday")
            call.turn(U(choice_index=1, raw_text="the first one"))
            self.assertEqual(call.ctx.manage.target.appointment_id, first["id"])


class Check(unittest.TestCase):

    def test_states_the_verified_appointment(self):
        with DemoClinic(now=NOW, appointments=True) as clinic:
            appt = book_appointment(clinic)
            call = Call(clinic, Intent.CHECK).given()
            call.verified()
            self.assertEqual(call.goal, Goal.STATE_APPOINTMENT)
            self.assertIn("Dr Rao", call.text)
            self.assertIn("Nagarbhavi", call.text)
            start = datetime.fromisoformat(appt["start"])
            self.assertIn(prompts.speak_slot(start, today=clock.today()), call.text)
            call.turn(U(confirmation="no", raw_text="no, that's it"))
            self.assertTrue(call.ctx.manage.done)
            self.assertEqual(call.ctx.outcome, "checked")
            self.assertIsNone(call.plan)          # policy says "anything else" / handlers close


class Cancel(unittest.TestCase):

    def _to_confirm(self, clinic, **kw):
        appt = book_appointment(clinic)
        call = Call(clinic, Intent.CANCEL).given(**kw)
        call.verified()
        self.assertEqual(call.goal, Goal.CONFIRM_CANCEL)
        return appt, call

    def test_cancel_flow(self):
        with DemoClinic(now=NOW, appointments=True) as clinic:
            appt, call = self._to_confirm(clinic)
            self.assertIn("Dr Rao", call.text)
            self.assertRegex(call.text.lower(), r"no (?:fee|charge|cost)|free")   # the variant varies
            # The clear yes to the summary is what cancels, on that turn: no
            # "may I ask why?" between the yes and the cancel (manage._advance_cancel).
            call.turn(U(confirmation="yes", raw_text="yes"))
            self.assertEqual(call.result.action, "cancelled")
            self.assertTrue(call.result.ok)
            row = status(clinic, appt["id"])
            self.assertEqual(row["status"], "cancelled")
            self.assertIsNone(row["cancel_reason"])
            self.assertEqual(call.ctx.stats(Goal.ASK_CANCEL_REASON).asked, 0)
            self.assertIn("cancelled", call.notices[0].lower())
            self.assertEqual(call.goal, Goal.OFFER_REBOOK)
            self.assertIn("before_action", [e for e, _ in call.events])
            self.assertEqual(call.ctx.outcome, "cancelled")

    def test_reason_given_earlier_commits_on_the_yes(self):
        with DemoClinic(now=NOW, appointments=True) as clinic:
            appt = book_appointment(clinic)
            call = Call(clinic, Intent.CANCEL).given()
            call.turn(U(intent=Intent.CANCEL, date_phrase="Monday", cancel_reason="feeling better",
                        raw_text="cancel Monday, I'm feeling better"))
            self.assertEqual(call.goal, Goal.CONFIRM_CANCEL)
            call.turn(U(confirmation="yes", raw_text="yes please"))
            self.assertEqual(call.result.action, "cancelled")
            self.assertEqual(status(clinic, appt["id"])["cancel_reason"], "feeling better")

    def test_reason_is_never_asked_after_the_cancel(self):
        """The reason is optional: kept when volunteered, never chased once it's cancelled."""
        with DemoClinic(now=NOW, appointments=True) as clinic:
            appt, call = self._to_confirm(clinic)
            call.turn(U(confirmation="yes", raw_text="yes"))
            self.assertEqual(call.result.action, "cancelled")
            self.assertEqual(call.goal, Goal.OFFER_REBOOK)
            call.turn(U(confirmation="no", raw_text="no"))
            self.assertIsNone(call.result.action)
            self.assertIsNone(status(clinic, appt["id"])["cancel_reason"])
            self.assertEqual(call.ctx.stats(Goal.ASK_CANCEL_REASON).asked, 0)

    def test_nothing_changes_without_a_clear_yes(self):
        """Z1 / Z2: no, a yes that wasn't heard, or a yes to something else never cancels."""
        with DemoClinic(now=NOW, appointments=True) as clinic:
            appt, call = self._to_confirm(clinic, name="Priya")
            call.turn(U(confirmation="yes", raw_text="yes"), heard=False)     # barged into the question
            self.assertFalse(call.ctx.manage.summary_heard)
            self.assertEqual(call.goal, Goal.CONFIRM_CANCEL)
            call.ctx.pending = Goal.ANSWER_ONLY                                # she asked nothing
            call.turn(U(confirmation="yes", raw_text="yes"))
            self.assertFalse(call.ctx.manage.summary_heard)
            call.turn(U(confirmation="no", raw_text="no, keep it"))
            self.assertIsNone(call.result.action)
            self.assertEqual(status(clinic, appt["id"])["status"], "booked")
            self.assertTrue(call.ctx.manage.done)
            self.assertIsNone(call.plan)

    def test_keep_it_at_the_confirmation_never_cancels(self):
        with DemoClinic(now=NOW, appointments=True) as clinic:
            appt, call = self._to_confirm(clinic)
            call.turn(U(confirmation="no", raw_text="actually don't cancel it, I'll come"))
            self.assertIsNone(call.result.action)
            self.assertEqual(status(clinic, appt["id"])["status"], "booked")
            self.assertTrue(call.ctx.manage.done)

    def test_repeated_commit_is_idempotent(self):
        with DemoClinic(now=NOW, appointments=True) as clinic:
            appt, call = self._to_confirm(clinic)
            call.turn(U(confirmation="yes", cancel_reason="busy", raw_text="yes, I'm busy"))
            self.assertEqual(call.result.action, "cancelled")
            again = asyncio.run(manage._commit_cancel(call.ctx, call.rt, ActionResult()))
            self.assertTrue(again.ok)
            self.assertEqual(again.code, "OK")
            audits = clinic.query("SELECT * FROM audit_events WHERE entity_id = ? AND action = 'cancel'", appt["id"])
            self.assertEqual(len(audits), 1)
            key = f"call:call-m1:cancel:{appt['id']}:{appt['version']}"
            self.assertEqual(len(clinic.query("SELECT * FROM actions WHERE idempotency_key = ?", key)), 1)
            self.assertEqual(status(clinic, appt["id"])["version"], appt["version"] + 1)

    def test_stale_reloads_and_asks_again(self):
        with DemoClinic(now=NOW, appointments=True) as clinic:
            appt, call = self._to_confirm(clinic)
            clinic.db.run_sync(lambda conn: conn.execute(
                "UPDATE appointments SET version = version + 1 WHERE id = ?", (appt["id"],)) and conn.commit())
            call.turn(U(confirmation="yes", cancel_reason="busy", raw_text="yes"))
            self.assertIsNone(call.result.action)
            self.assertEqual(call.result.code, "STALE")
            self.assertEqual(call.ctx.manage.target.version, appt["version"] + 1)
            self.assertEqual(call.goal, Goal.CONFIRM_CANCEL)
            self.assertEqual(status(clinic, appt["id"])["status"], "booked")
            call.turn(U(confirmation="yes", raw_text="yes"))
            self.assertEqual(call.result.action, "cancelled")

    def test_already_cancelled_by_staff(self):
        with DemoClinic(now=NOW, appointments=True) as clinic:
            appt, call = self._to_confirm(clinic)
            clinic.db.run_sync(scheduling.cancel, appt["id"], idem_key="staff-1", actor="staff")
            call.turn(U(confirmation="yes", cancel_reason="busy", raw_text="yes"))
            self.assertIsNone(call.result.action)
            self.assertIn("already", call.notices[0].lower())
            self.assertEqual(call.goal, Goal.OFFER_REBOOK)
            self.assertIsNone(call.ctx.manage.target)

    def test_rebook_after_cancel_keeps_the_service(self):
        with DemoClinic(now=NOW, appointments=True) as clinic:
            appt, call = self._to_confirm(clinic)
            call.turn(U(confirmation="yes", cancel_reason="busy", raw_text="yes"))
            self.assertEqual(call.goal, Goal.OFFER_REBOOK)
            call.turn(U(confirmation="yes", raw_text="yes please"))
            self.assertEqual(call.ctx.intent, Intent.BOOK)
            self.assertEqual(call.ctx.book.service, "Teeth Cleaning")
            self.assertEqual(call.ctx.caller.phone_state, FieldState.CONFIRMED)

    def test_rebook_is_offered_once(self):
        with DemoClinic(now=NOW, appointments=True) as clinic:
            appt, call = self._to_confirm(clinic)
            call.turn(U(confirmation="yes", cancel_reason="busy", raw_text="yes"))
            call.turn(U(confirmation="no", raw_text="no thanks"))
            self.assertIsNone(call.plan)
            call.ctx.pending = Goal.ANSWER_ONLY
            call.turn(U(acts=["question"], raw_text="where is the clinic?"))
            self.assertIsNone(call.plan)

    def test_cancel_today_is_still_allowed(self):
        with DemoClinic(now=NOW, appointments=True) as clinic:
            appt = book_appointment(clinic, day=datetime(2026, 10, 1), hours=(13, 14, 15),
                                    now=clock.localize(datetime(2026, 9, 30, 9, 0)))
            call = Call(clinic, Intent.CANCEL).given()
            call.verified("today")
            self.assertEqual(call.goal, Goal.CONFIRM_CANCEL)
            call.turn(U(confirmation="yes", cancel_reason="busy", raw_text="yes"))
            self.assertEqual(call.result.action, "cancelled")
            self.assertEqual(status(clinic, appt["id"])["status"], "cancelled")


class Reschedule(unittest.TestCase):

    def _to_offer(self, clinic, when="Wednesday"):
        appt = book_appointment(clinic)
        call = Call(clinic, Intent.RESCHEDULE).given()
        call.verified()
        self.assertEqual(call.goal, Goal.ASK_NEW_WHEN)
        call.turn(U(date_phrase=when, raw_text=f"{when} please"))
        return appt, call

    def test_reschedule_flow(self):
        with DemoClinic(now=NOW, appointments=True) as clinic:
            appt, call = self._to_offer(clinic)
            self.assertEqual(call.goal, Goal.OFFER_NEW_SLOTS)
            offered = call.ctx.manage.offered
            self.assertTrue(offered)
            holds = {h["id"] for h in clinic.query("SELECT * FROM slot_holds WHERE call_id = ?", "call-m1")}
            for slot in offered:
                self.assertIn(slot.hold_id, holds)                # real, held slots (Z4)
                self.assertEqual(slot.branch_id, appt["branch_id"])
                self.assertEqual(slot.service_id, appt["service_id"])
                self.assertEqual(slot.start.date(), datetime(2026, 10, 7).date())
                # Two times on one day are said together ("Wednesday the 7th at 10 or 10:30").
                self.assertIn(prompts.speak_time(slot.start.time()), call.text)
            self.assertIn("Wednesday", call.text)
            self.assertIn("before_action", [e for e, _ in call.events])
            call.turn(U(choice_index=1, raw_text="the first one"))
            chosen = call.ctx.manage.chosen
            self.assertEqual(call.goal, Goal.CONFIRM_RESCHEDULE)
            old = prompts.speak_slot(datetime.fromisoformat(appt["start"]), today=clock.today())
            self.assertIn(old, call.text)
            self.assertIn(chosen.spoken, call.text)
            self.assertEqual(status(clinic, appt["id"])["start_utc"], clinic.query(
                "SELECT start_utc FROM appointments WHERE id = ?", appt["id"])[0]["start_utc"])
            call.turn(U(confirmation="yes", raw_text="yes go ahead"))
            self.assertEqual(call.result.action, "rescheduled")
            moved = scheduling.get_appointment.__wrapped__ if hasattr(scheduling.get_appointment, "__wrapped__") \
                else None
            row = clinic.db.run_sync(scheduling.get_appointment, appt["id"])
            self.assertEqual(datetime.fromisoformat(row["start"]), chosen.start)
            self.assertEqual(row["status"], "booked")
            self.assertIn(prompts.speak_slot(chosen.start, today=clock.today()), call.notices[0])   # M7
            self.assertEqual(call.ctx.outcome, "rescheduled")
            self.assertIsNone(call.plan)                          # policy: "anything else?"
            self.assertIsNone(moved)

    def test_no_at_the_confirmation_moves_nothing(self):
        with DemoClinic(now=NOW, appointments=True) as clinic:
            appt, call = self._to_offer(clinic)
            call.turn(U(choice_index=1, raw_text="first"))
            call.turn(U(confirmation="no", raw_text="no"))
            self.assertIsNone(call.result.action)
            self.assertEqual(status(clinic, appt["id"])["start_utc"], appt["start_utc"])
            self.assertEqual(call.ctx.manage.offer_rounds, 1)
            self.assertEqual(clinic.query("SELECT * FROM slot_holds WHERE call_id = ?", "call-m1"), [])
            self.assertEqual(call.goal, Goal.ASK_NEW_WHEN)

    def test_unheard_yes_moves_nothing(self):
        with DemoClinic(now=NOW, appointments=True) as clinic:
            appt, call = self._to_offer(clinic)
            call.turn(U(choice_index=1, raw_text="first"))
            call.turn(U(confirmation="yes", raw_text="yes"), heard=False)
            self.assertIsNone(call.result.action)
            self.assertEqual(status(clinic, appt["id"])["start_utc"], appt["start_utc"])
            self.assertEqual(call.goal, Goal.CONFIRM_RESCHEDULE)

    def test_same_time_gets_a_notice(self):
        with DemoClinic(now=NOW, appointments=True) as clinic:
            appt = book_appointment(clinic)
            start = datetime.fromisoformat(appt["start"])
            call = Call(clinic, Intent.RESCHEDULE).given()
            call.verified()
            call.turn(U(date_phrase="Monday", time_phrase=f"at {start.hour}:{start.minute:02d}",
                        raw_text="Monday same time"))
            self.assertIn("same_slot", [n.line for n in call.result.notices])
            self.assertTrue(all(s.start != start for s in call.ctx.manage.offered))

    def test_too_late_to_move_offers_a_callback(self):
        with DemoClinic(now=NOW, appointments=True) as clinic:
            book_appointment(clinic, day=datetime(2026, 10, 1), hours=(11, 12),
                             now=clock.localize(datetime(2026, 9, 30, 9, 0)))
            call = Call(clinic, Intent.RESCHEDULE).given()
            call.verified("today")
            self.assertTrue(call.ctx.manage.verified)
            self.assertEqual(call.goal, Goal.TOO_LATE)
            self.assertIn("call you", call.text)

    def test_taken_at_commit_offers_again(self):
        with DemoClinic(now=NOW, appointments=True) as clinic:
            appt, call = self._to_offer(clinic)
            call.turn(U(choice_index=1, raw_text="first"))
            chosen = call.ctx.manage.chosen
            # Someone else gets the cell between the offer and the yes (the hold expired).
            clinic.db.run_sync(lambda conn: conn.execute("DELETE FROM slot_holds") and conn.commit())
            other = clinic.db.run_sync(scheduling.book, service="Teeth Cleaning", doctor_id=chosen.doctor_id,
                                       start=chosen.start, patient_name="Someone Else", phone="+919800000001",
                                       idem_key="other-1")
            self.assertTrue(other.ok, other.code)
            call.turn(U(confirmation="yes", raw_text="yes"))
            self.assertIsNone(call.result.action)
            self.assertIn("slot.gone", [n.line for n in call.result.notices])
            self.assertEqual(status(clinic, appt["id"])["start_utc"], appt["start_utc"])
            self.assertEqual(call.goal, Goal.OFFER_NEW_SLOTS)
            self.assertTrue(all(s.start != chosen.start for s in call.ctx.manage.offered))

    def test_context_pickles_through_the_flow(self):
        with DemoClinic(now=NOW, appointments=True) as clinic:
            appt, call = self._to_offer(clinic)
            call.turn(U(choice_index=1, raw_text="first"))
            self.assertEqual(pickle.loads(pickle.dumps(call.ctx)), call.ctx)


if __name__ == "__main__":
    unittest.main()
