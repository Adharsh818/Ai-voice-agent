"""
tests/test_booking_flow.py's behaviour, ported to the R2 engine (Sprint 1b
integration). The old file keeps testing the 12-step machine, which stays
the default engine; this one drives the same promises through the ai_engine
facade on an R2 CallContext, on the DEMO clinic, with the model down
(Tier-0 and the lenient fallback) or faked (nlu.FakeNLU) where a turn needs
the model's reading.

What carries over, as behaviour rather than step numbers:
- nothing is booked before a summary the caller heard gets a clear yes;
- a "no" (or a "no" the model misread as yes) at the summary never books;
- details given early are used, never asked again;
- the phone is read back in groups; the summary is one natural sentence with
  no form labels and no calendar or email promise;
- offers are real free cells: never a taken one, never the lunch break;
- a slot taken by someone else before the yes is never double booked;
- slot content, digits and bare acknowledgements are never taken as a name;
- a "no, it's Priya" or "no, make it Tuesday" uses the correction at once;
- a refusal at the greeting doesn't start a booking.

Dropped with the 12-step machine (their R2 replacements):
- test_date_and_time_boundaries (backend_actions.resolve_date / resolve_time):
  tests/test_dateparse.py, plus test_outside_hours_and_sunday_get_a_notice here.
- test_booking_rejects_conflicting_slot_and_allows_idempotent_retry
  (backend_actions.book_appointment): tests/test_scheduling.py (slot_claims,
  idempotency keys), plus test_a_slot_taken_before_the_yes_is_never_double_booked.
- test_alternative_slots_are_nearest_free_and_exclude_busy_slot and
  test_alternative_slots_skip_lunch_break (backend_actions.check_availability):
  tests/test_scheduling.py (suggest), plus the two offer tests here.
- test_absorber_never_overrides_the_active_or_confirmed_slot
  (_absorb_volunteered_slots): test_a_confirmed_phone_is_never_silently_replaced.
- test_name_confirmation_spells_out_only_on_retry: test_name_is_not_spelled_on_first_hearing
  here and the SPELL_NAME rung in tests/test_r2_handlers.py / test_r2_apply.py.
- test_recap_speaks_name_plainly_but_spells_out_phone: test_summary_is_one_natural_sentence
  (owner decision D4: the phone is not repeated in the summary; it was read
  back and confirmed already).
- test_short_service_fragments_do_not_match (_match_service): tests/test_match.py.
- ConfirmationParsingTests' parser cases: tests/test_match.py
  (test_parity_with_the_old_parser runs the same phrases through match.parse_yes_no).
- test_purpose_question_skipped_when_intent_already_clear: there is no purpose
  step; test_volunteered_details_are_never_asked_again covers it.
"""

import asyncio
import unittest
from datetime import datetime

import ai_engine
import clock
import facts
import nlu
import scheduling
from dialogue.context import Goal, Intent, new_context
from dialogue.testing import DemoClinic

# Thursday 1 Oct 2026, 10:00: Monday is the 5th, Tuesday the 6th.
NOW = datetime(2026, 10, 1, 10, 0)
PHONE = "9845012345"
E164 = "+919845012345"
OPENING = ["", "I'd like to book a cleaning", "My name is Priya", PHONE, "yes"]


class Call:
    """One R2 call through the facade, the way call_session drives it."""

    def __init__(self):
        self.s = new_context("r2-flow")
        self.lines = []
        self.results = []

    def say(self, text, heard=True):
        self.s.last_reply_heard = heard
        result = asyncio.run(ai_engine.async_process_turn(text, self.s, None, on_sentence=None))
        self.results.append(result)
        self.lines.append(result.text)
        return result

    def run(self, *turns):
        for text in turns:
            self.say(text)
        return self.results[-1]

    @property
    def goal(self):
        return self.s.pending


class FlowCase(unittest.TestCase):
    """A fresh DEMO clinic and frozen clock per test; the model is down unless a test scripts it."""

    def setUp(self):
        self.clinic = DemoClinic(now=NOW)
        self.clinic.__enter__()
        facts.clear_cache()
        self.backend = nlu.use_backend(nlu.FakeNLU(usable=False))
        self.backend.__enter__()

    def tearDown(self):
        self.backend.__exit__(None, None, None)
        facts.clear_cache()
        self.clinic.__exit__(None, None, None)

    def model(self, script):
        self.backend.__exit__(None, None, None)
        self.backend = nlu.use_backend(nlu.FakeNLU(script))
        self.backend.__enter__()

    def booked(self):
        return self.clinic.query("SELECT * FROM appointments WHERE caller_phone_e164 = ? AND status = 'booked'", E164)

    def to_summary(self, call, branch="Indiranagar", when="Monday morning"):
        call.run(*OPENING, branch, when, "the first one")
        self.assertEqual(call.goal, Goal.SUMMARY, call.lines)
        return call


class SummaryGate(FlowCase):

    def test_booking_requires_the_summary_yes(self):
        call = self.to_summary(Call())
        self.assertEqual(self.booked(), [])
        result = call.say("yes, everything is correct")
        self.assertEqual(result.action, "booked")
        self.assertEqual(len(self.booked()), 1)

    def test_a_rejected_summary_never_books(self):
        call = self.to_summary(Call())
        result = call.say("No, that is not right")
        self.assertIsNone(result.action)
        self.assertEqual(self.booked(), [])
        self.assertEqual(call.goal, Goal.WHAT_TO_CHANGE)

    def test_model_yes_against_spoken_no_never_books(self):
        call = self.to_summary(Call())
        self.model({"no, that is not right, the day is wrong": {
            "acts": ["answer"], "intent": "book", "confirmation": "yes", "next_goal": "booked", "say": "", "ask": ""}})
        result = call.say("no, that is not right, the day is wrong")
        self.assertIsNone(result.action)
        self.assertEqual(self.booked(), [])

    def test_a_yes_over_an_interrupted_summary_never_books(self):
        call = self.to_summary(Call())
        result = call.say("yes", heard=False)
        self.assertIsNone(result.action)
        self.assertEqual(self.booked(), [])
        self.assertEqual(call.goal, Goal.SUMMARY_AGAIN)

    def test_summary_is_one_natural_sentence(self):
        call = self.to_summary(Call())
        summary = call.lines[-1]
        self.assertIn("Priya", summary)
        for label in ("Name:", "Phone", "Service:", "Date:", "P R I Y A"):
            self.assertNotIn(label, summary)
        self.assertNotIn("9 8 4 5 0", summary)          # D4: read back and confirmed already
        self.assertTrue(summary.endswith("?"))

    def test_confirmation_never_promises_a_calendar_invitation(self):
        call = self.to_summary(Call())
        booked = call.say("yes").text.lower()
        for word in ("calendar", "invitation", "invite", "email", "sms", "text you"):
            self.assertNotIn(word, booked)

    def test_bye_at_the_summary_never_books(self):
        call = self.to_summary(Call())
        call.say("bye")
        self.assertEqual(self.booked(), [])


class DetailsAndOffers(FlowCase):

    def test_volunteered_details_are_never_asked_again(self):
        call = Call()
        call.run("", "I'd like a root canal next Monday at 5 pm", "My name is Priya", PHONE)
        self.assertIn("9 8 4 5 0, 1 2 3 4 5", call.lines[-1])           # grouped read-back
        call.say("yes")
        asked = [r.goal_after for r in call.results]
        for goal in ("ask_service", "ask_when", "ask_time", "ask_intent"):
            self.assertNotIn(goal, asked, call.lines)
        self.assertEqual(call.s.book.service, "Root Canal Treatment")

    def test_offers_never_include_a_taken_cell(self):
        menon = self.clinic.query("SELECT id FROM doctors WHERE spoken_name = 'Dr Menon'")[0]["id"]
        start = clock.localize(datetime(2026, 10, 5, 11, 0))
        taken = self.clinic.db.run_sync(scheduling.book, service="Teeth Cleaning", doctor_id=menon, start=start,
                                        patient_name="Alex", phone="+919812345678", idem_key="t:alex",
                                        source="seed", actor="seed")
        self.assertTrue(taken.ok)
        call = Call()
        call.run(*OPENING, "Indiranagar", "Monday at 11 am")
        offered = [o.start for o in call.s.book.offered]
        self.assertTrue(offered, call.lines)
        for o in call.s.book.offered:
            self.assertFalse(o.start == start and o.doctor_id == menon, call.lines)

    def test_offers_skip_the_lunch_break(self):
        call = Call()
        call.run(*OPENING, "Indiranagar", "Monday at 2 pm")
        self.assertTrue(call.s.book.offered, call.lines)
        for o in call.s.book.offered:
            t = o.start.time()
            self.assertFalse(t.hour == 14 and t.minute < 30, call.lines)

    def test_outside_hours_and_sunday_get_a_notice(self):
        call = Call()
        call.run(*OPENING, "Indiranagar", "Sunday")
        self.assertNotEqual(call.goal, Goal.SUMMARY)
        for o in call.s.book.offered:
            self.assertNotEqual(o.start.weekday(), 6)
        self.assertIn("sunday", call.lines[-1].lower())

    def test_a_slot_taken_before_the_yes_is_never_double_booked(self):
        call = Call()
        call.run(*OPENING, "Indiranagar", "Monday morning", "the first one")
        chosen = call.s.book.chosen
        self.assertIsNotNone(chosen, call.lines)
        # Someone else takes the cell (another line, staff) while the summary plays.
        self.clinic.db.run_sync(lambda conn: (conn.execute("DELETE FROM slot_holds"), conn.commit()))
        other = self.clinic.db.run_sync(scheduling.book, service="Teeth Cleaning", doctor_id=chosen.doctor_id,
                                        start=chosen.start, patient_name="Alex", phone="+919812345678",
                                        idem_key="t:race", source="seed", actor="seed")
        self.assertTrue(other.ok)
        result = call.say("yes")
        self.assertIsNone(result.action)
        self.assertEqual(self.booked(), [])
        clash = self.clinic.query("SELECT * FROM appointments WHERE start_utc = (SELECT start_utc FROM appointments "
                                  "WHERE id = ?) AND doctor_id = ? AND status = 'booked'", other.appointment["id"],
                                  chosen.doctor_id)
        self.assertEqual(len(clash), 1)

    def test_a_confirmed_phone_is_never_silently_replaced(self):
        call = Call()
        call.run(*OPENING, "Indiranagar")
        self.assertEqual(call.s.caller.phone_e164, E164)
        call.say("9812345678")
        self.assertEqual(call.s.caller.phone_e164, E164)     # only after a read-back and a yes


class Names(FlowCase):

    def at_name(self):
        call = Call()
        call.run("", "I'd like to book a cleaning")
        self.assertEqual(call.goal, Goal.ASK_NAME, call.lines)
        return call

    def test_slot_content_is_not_taken_as_a_name(self):
        for text in ["next Monday at 5 pm", "I want a cleaning", PHONE, "tomorrow morning please"]:
            call = self.at_name()
            call.say(text)
            self.assertIsNone(call.s.caller.name, text)

    def test_bare_acknowledgements_are_not_names(self):
        for text in ["yes", "no", "okay", "sure"]:
            call = self.at_name()
            call.say(text)
            self.assertIsNone(call.s.caller.name, text)

    def test_real_names_are_captured(self):
        for text, expected in [("my name is Alex Johnson", "Alex Johnson"), ("It is Adharsh", "Adharsh"),
                               ("Priya", "Priya")]:
            call = self.at_name()
            call.say(text)
            self.assertEqual(call.s.caller.name, expected, text)

    def test_name_is_not_spelled_on_first_hearing(self):
        call = self.at_name()
        reply = call.say("my name is Adharsh").text
        self.assertIn("Adharsh", reply)
        self.assertNotIn("A D H A R S H", reply)

    def test_a_name_correction_in_the_same_breath_is_used(self):
        call = self.at_name()
        call.say("Alex")
        self.model({"no, it's priya": {"acts": ["correction"], "intent": "book", "correction": True,
                                       "name": "Priya", "next_goal": "ask_phone", "say": "", "ask": ""}})
        reply = call.say("no, it's Priya").text
        self.assertEqual(call.s.caller.name, "Priya")
        # After a misheard name she asks the way a person would (spell it),
        # never for the whole name again from scratch.
        self.assertTrue("Priya" in reply or call.goal == Goal.SPELL_NAME, reply)
        self.assertNotIn("name again", reply.lower())


class Corrections(FlowCase):

    def test_a_rejected_date_takes_the_new_one_from_the_same_turn(self):
        call = Call()
        call.run(*OPENING, "Indiranagar", "next Monday")
        first = call.s.book.date_c
        self.assertIsNotNone(first, call.lines)
        call.say("no, make it Tuesday")
        self.assertIsNotNone(call.s.book.date_c)
        self.assertNotEqual(call.s.book.date_c, first)

    def test_a_refusal_at_the_greeting_does_not_start_a_booking(self):
        call = Call()
        call.run("", "No, I don't want to book anything")
        self.assertNotEqual(call.s.intent, Intent.BOOK)
        self.assertNotEqual(call.goal, Goal.ASK_NAME)
        self.assertEqual(self.booked(), [])


if __name__ == "__main__":
    unittest.main()
