"""
Sprint 1b integration fixes, found by the 200-call simulated round on the R2
engine (harness_runs/*-integ200*). One test per root cause in that round's
failure catalogue, so none of them comes back:

- "Wednesday the 14th" was read as the next Wednesday (the 7th): the wrong
  day was offered, verification failed and reschedules dead-ended.
- A caller who insists on a day that is full at their branch heard the same
  two later slots again and again; now the other branches are tried for
  that day, once, and the booking moves there if they pick it.
- "No, it should be 11:30" at the summary, when 11:30 is what they asked for
  and it wasn't free, looped on "what should I change?".
- Restating the booking at "what should I change?" looped too.
- A goodbye at the end of a sentence was answered with another question.
- After a callback was arranged, the booking went on offering slots.
- "..., no?" (an Indian-English tag question) was a "no" to the read-back.
- "Sunday morning would be best" was taken as a cut-off sentence.
- A declined callback after failed verification, or small talk read as a
  "no" at a cancel confirmation, closed the call on "anything else?" while
  the caller kept asking for the change.
- "Can you cancel that one instead?" during the phone read-back rejected
  the number.
- An overlapping appointment for the same patient re-asked the duplicate
  question on a loop instead of saying it clashes.

The harness's own scoring fixes (a talked-over line said again is not an M3
loop; "Ali" is not in "Invisalign" for Z7; plural service names) are tested
at the end.
"""

import asyncio
import unittest
from datetime import date, datetime

import ai_engine
import dateparse
from dialogue import apply as applier
from dialogue import match
from dialogue.context import Act, FieldState, Goal, Intent, Understanding, new_context
from dialogue.testing import DemoClinic
from harness import lines as hlines
from harness import metrics
from test_r2_book import NOW, U, BookTestCase

MONDAY = date(2026, 10, 5)


def _close_branch(clinic, day: date, branch_id: int = 1):
    """Nagarbhavi (branch 1) closed for the day: every slot there is gone."""
    clinic.query("INSERT INTO closures (date, branch_id, reason) VALUES (?, ?, 'test')", day.isoformat(), branch_id)
    clinic.db.run_sync(lambda conn: conn.commit())


class DateTests(unittest.TestCase):
    def test_a_weekday_with_a_day_number_is_that_date(self):
        today = date(2026, 10, 1)                                  # a Thursday
        for text, want in (("Wednesday the 14th", "2026-10-14"), ("Friday the 9th", "2026-10-09"),
                           ("Monday, the 5th at 3", "2026-10-05"), ("Thursday 15th", "2026-10-15"),
                           ("monday 2nd", "2026-11-02")):
            with self.subTest(text=text):
                when = dateparse.parse_when(text, today=today)
                self.assertEqual(when.date.start.isoformat(), want)

    def test_the_old_and_new_dates_of_a_move_stay_apart(self):
        when = dateparse.parse_when("Friday the 9th", today=date(2026, 10, 1))
        self.assertEqual(when.date.start, date(2026, 10, 9))


class ListeningTests(unittest.TestCase):
    def test_a_tag_question_is_not_a_no(self):
        self.assertIsNone(match.parse_yes_no("It's been raining a lot today, no?"))
        self.assertEqual(match.parse_yes_no("No."), "no")
        self.assertEqual(match.parse_yes_no("No?"), "no")

    def test_would_be_best_is_a_finished_sentence(self):
        self.assertFalse(match.is_fragment("Sunday morning would be best", "date"))
        self.assertTrue(match.is_fragment("What's the best", "open"))

    def test_small_talk_with_not_is_no_answer(self):
        u = Understanding(acts=[Act.CHITCHAT.value], source="llm")
        self.assertIsNone(applier.resolve_confirmation(u, "Hope you're not too busy today", Goal.CONFIRM_CANCEL))
        u = Understanding(acts=[Act.CHITCHAT.value], source="llm")
        self.assertEqual(applier.resolve_confirmation(u, "No, not today", Goal.CONFIRM_CANCEL), "no")

    def test_cancel_instead_during_the_read_back_is_not_a_no_to_the_number(self):
        u = Understanding(intent=Intent.CANCEL, source="llm")
        text = "Actually, I already have an appointment. Can you cancel that one instead?"
        self.assertIsNone(applier.resolve_confirmation(u, text, Goal.CONFIRM_PHONE))
        u = Understanding(intent=Intent.CANCEL, source="llm")
        self.assertEqual(applier.resolve_confirmation(u, "No, cancel it", Goal.SUMMARY), "no")


class FullDayTests(BookTestCase):
    def test_insisting_on_a_full_day_tries_the_other_branches_once(self):
        _close_branch(self.clinic, MONDAY)
        call = self.call().given()
        call.turn(U(service="General Check-up", branch="Nagarbhavi", date_phrase="Monday", time_phrase="5 pm"))
        self.assertEqual(call.goal, Goal.OFFER_SLOTS)
        self.assertTrue(all(s.start.date() != MONDAY for s in call.ctx.book.offered))
        call.turn(U(date_phrase="Monday the 5th"))
        self.assertEqual(call.plan.line, "offer.other_branch", call.lines)
        offered = call.ctx.book.offered
        self.assertTrue(offered and all(s.start.date() == MONDAY and s.branch != "Nagarbhavi" for s in offered))
        self.assertIn(offered[0].branch, call.text)
        self.assertIn("Nagarbhavi", call.text)
        call.turn(U(choice_index=1))
        self.assertEqual(call.goal, Goal.SUMMARY)
        self.assertEqual(call.ctx.book.branch, offered[0].branch)
        call.turn(confirmation="yes")
        self.assertEqual(call.result.action, "booked")
        row = self.clinic.query("SELECT b.name FROM appointments a JOIN doctors d ON d.id = a.doctor_id "
                                "JOIN branches b ON b.id = d.branch_id")[0]
        self.assertEqual(row["name"], offered[0].branch)

    def test_a_day_full_everywhere_is_said_in_new_words(self):
        for branch_id in (1, 2, 3, 4):
            _close_branch(self.clinic, MONDAY, branch_id)
        call = self.call().given()
        call.turn(U(service="General Check-up", branch="Nagarbhavi", date_phrase="Monday", time_phrase="5 pm"))
        first = call.text
        call.turn(U(date_phrase="Monday"))
        self.assertEqual(call.plan.line, "offer.full_everywhere")
        self.assertNotEqual(call.text, first)
        call.turn(U(date_phrase="Monday"))                         # once per day: no new search, no new wording
        self.assertNotEqual(call.plan.line, "offer.other_branch")


class SummaryTests(BookTestCase):
    def test_restating_the_booking_at_what_to_change_says_the_summary_again(self):
        call = self.call().given()
        self.to_summary(call)
        call.turn(confirmation="no")
        self.assertEqual(call.goal, Goal.WHAT_TO_CHANGE)
        call.turn(U(intent=Intent.BOOK, service="General Check-up", acts=[Act.ANSWER.value]))
        self.assertIn(call.goal, (Goal.SUMMARY, Goal.SUMMARY_AGAIN))
        call.turn(confirmation="yes")
        self.assertEqual(call.result.action, "booked")

    def test_repeating_the_unfree_time_at_the_summary_searches_again(self):
        call = self.call().given()
        call.turn(U(service="General Check-up", branch="Nagarbhavi", date_phrase="Monday", time_phrase="2 pm"))
        self.assertEqual(call.goal, Goal.OFFER_SLOTS)                # 2 pm is lunch: other times are offered
        call.turn(U(choice_index=1))
        self.assertEqual(call.goal, Goal.SUMMARY)
        call.turn(U(time_phrase="2 pm"), confirmation="no")
        self.assertIn("time.taken", call.notice_ids())
        self.assertEqual(call.goal, Goal.OFFER_SLOTS)
        self.assertIsNone(call.ctx.book.chosen)


def _run(lines, reader=lambda text, expect: None, setup=None):
    """Whole calls through the facade on a fresh DEMO clinic, the model down unless a reader is given."""
    async def go():
        with DemoClinic(now=NOW) as clinic:
            if setup:
                setup(clinic)
            with ai_engine.install_test_nlu(reader):
                s = new_context("integration")
                out = [await ai_engine.async_process_turn("", s)]
                for line in lines:
                    out.append(await ai_engine.async_process_turn(line, s))
                return s, out, clinic
    return asyncio.run(go())


class CallTests(unittest.TestCase):
    def test_a_goodbye_at_the_end_closes_the_call(self):
        s, out, _ = _run(["I'd like to book a cleaning", "Priya", "9845012345", "yes",
                          "This isn't working, I'll just come in person. Bye."])
        self.assertTrue(s.closed_conversation)
        self.assertNotIn("?", out[-1].text)

    def test_after_a_callback_the_booking_stops(self):
        s, out, clinic = _run(["I'd like to book a cleaning", "Priya", "9845012345", "yes",
                               "Can I speak to a real person?", "No, I want a real person.", "yes"])
        if s.tasks_created:
            self.assertNotEqual(s.intent, Intent.BOOK)
            self.assertNotIn(s.pending, (Goal.ASK_BRANCH, Goal.ASK_WHEN, Goal.OFFER_SLOTS))


class ManageTests(unittest.TestCase):
    def _verified_cancel(self):
        s = new_context("m")
        s.intent = Intent.CANCEL
        s.manage.action = Intent.CANCEL
        s.manage.verified = True
        s.manage.done = True
        s.caller.phone_e164, s.caller.phone_state = "+919845012345", FieldState.CONFIRMED
        return s

    def test_restating_a_set_aside_cancel_picks_it_up_again(self):
        s = self._verified_cancel()
        with DemoClinic(now=NOW):
            applier.apply(s, Understanding(intent=Intent.CANCEL, source="llm",
                                           raw_text="I want to cancel my appointment"), None)
        self.assertFalse(s.manage.done)

    def test_a_cancel_already_made_is_not_reopened(self):
        s = self._verified_cancel()
        s.outcome = "cancelled"
        with DemoClinic(now=NOW):
            applier.apply(s, Understanding(intent=Intent.CANCEL, source="llm", raw_text="cancel it"), None)
        self.assertTrue(s.manage.done)

    def test_verification_is_tried_again_once_after_a_declined_callback(self):
        s = new_context("v")
        s.intent = Intent.RESCHEDULE
        s.manage.action = Intent.RESCHEDULE
        s.manage.done, s.manage.verify_attempts = True, 2
        s.stats(Goal.CALLBACK_OFFER).asked = 1
        with DemoClinic(now=NOW):
            applier.apply(s, Understanding(intent=Intent.RESCHEDULE, source="llm",
                                           raw_text="No, I want to move my existing appointment"), None)
        self.assertFalse(s.manage.done)
        self.assertEqual(s.manage.verify_attempts, 0)
        s.manage.done, s.manage.verify_attempts = True, 2
        s.stats(Goal.CALLBACK_OFFER).asked = 2                     # the second round: no third try
        with DemoClinic(now=NOW):
            applier.apply(s, Understanding(intent=Intent.RESCHEDULE, source="llm", raw_text="move it"), None)
        self.assertTrue(s.manage.done)


class ClashTests(BookTestCase):
    def test_an_overlapping_appointment_for_the_patient_is_called_a_clash(self):
        call = self.call().given()
        self.to_summary(call)
        slot = call.ctx.book.chosen
        call.ctx.book.duplicate_ok = True                          # they already said they want another one
        self.clinic.db.run_sync(__import__("scheduling").release_holds, call.rt.call_id)
        self.book_existing("Priya", slot.start.replace(tzinfo=None), doctor="Dr Shetty"
                           if slot.doctor == "Dr Rao" else "Dr Rao", key="clash")
        call.turn(confirmation="yes")
        self.assertIsNone(call.result.action)
        self.assertIn("patient.clash", call.notice_ids())
        self.assertNotEqual(call.goal, Goal.DUPLICATE_CHECK)
        clash = call.notices[call.notice_ids().index("patient.clash")]
        self.assertNotIn("Dr ", clash)                             # no detail of the other appointment (Z7)
        self.assertNotRegex(clash, r"[0-9]")


class HarnessScoringTests(unittest.TestCase):
    def test_plural_service_names_are_found(self):
        found = hlines.services_in("We do check-ups, cleanings, fillings, root canals, braces and Invisalign.")
        self.assertGreaterEqual(len(found), 5)

    def test_a_line_said_again_after_being_talked_over_is_not_a_loop(self):
        summary = "That's Saturday the 10th at 3, a consultation with Dr Nair. Shall I cancel it?"
        again = "I have Saturday the 10th at 3, a consultation with Dr Nair. Should I cancel it?"
        record = {"turns": [{"n": 0, "caller": "", "emma": "Hi"},
                            {"n": 1, "caller": "cancel it", "emma": summary},
                            {"n": 2, "caller": "yes", "emma": again, "heard_previous": False}]}
        record["now"] = NOW.isoformat()
        found = metrics._m3(record, metrics._Transcript(record))
        self.assertEqual([f for f in found if "near-repeat" in f["detail"]], [])
        record["turns"][2]["heard_previous"] = True                # heard, then said again: a loop
        found = metrics._m3(record, metrics._Transcript(record))
        self.assertTrue([f for f in found if "near-repeat" in f["detail"]])


if __name__ == "__main__":
    unittest.main()
