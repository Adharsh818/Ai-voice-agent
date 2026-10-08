"""
Fixes from the 22 calls on demo day (8 Oct 2026, most by phone). Gemini was slow
that day, so many replies came from the fallback path; each test is one of those
calls reduced to its cause, using what the caller actually said.
"""

import asyncio
import unittest

import ai_engine
import tier0
from dialogue import context, engine
from dialogue.context import Act, Expect, Goal, Intent, Tier0View
from dialogue.match import HEAR_CHECK_RE, clean_name, looks_like_question
from dialogue.testing import DemoClinic
from test_r2_engine import Call, EngineCase, PHONE


def _understand(text, pending, expect, intent=Intent.BOOK):
    with DemoClinic():
        async def go():
            rt = await engine.build_runtime(context.new_context("x"))
            return tier0.understand(text, Tier0View(expect=expect, pending=pending, intent=intent,
                                                    catalog=rt.catalog), lenient=True)
        return asyncio.run(go())


class NameTests(unittest.TestCase):
    def test_what_the_caller_is_doing_is_not_a_new_name(self):
        # "Did you want to change the name to Talking I'M?" (asked three times), "...to Another One?"
        for said in ("Yeah. I'm talking. I'm not... Are you an AI?", "This is another one."):
            for goal in (Goal.OFFER_SLOTS, Goal.DUPLICATE_CHECK, Goal.ANYTHING_ELSE):
                u = _understand(said, goal, Expect.CHOICE)
                self.assertIsNone(u.name, (said, goal))

    def test_an_introduction_is_still_a_name(self):
        self.assertEqual(_understand("Hi, this is Priya Sharma.", Goal.GREET, Expect.OPEN).name, "Priya Sharma")
        self.assertEqual(_understand("Yes, my name is Rahul, I want a check-up.", Goal.ASK_SERVICE,
                                     Expect.OPEN).name, "Rahul")

    def test_numbers_and_doings_are_not_names(self):
        for said in ("Seven.", "Another one", "Talking I'm", "I'm asking"):
            self.assertIsNone(clean_name(said), said)
        self.assertEqual(clean_name("Fleming"), "Fleming")          # a surname, not a doing word

    def test_a_name_said_twice_is_one_name(self):
        self.assertEqual(clean_name("Jimmy. Jimmy."), "Jimmy")
        self.assertEqual(clean_name("Priya Sharma, Priya Sharma"), "Priya Sharma")
        self.assertEqual(clean_name("Anil Anil Kumar"), "Anil Anil Kumar")   # not an exact repeat


class DuplicateBookingTests(EngineCase):
    def booked(self):
        c = Call()
        c.run("", "I want to book a cleaning", "My name is Priya", PHONE, "yes", "Indiranagar", "Monday",
              "morning", "The first one", "Yes")
        self.assertEqual(c.s.pending, Goal.BOOKED, c.lines)
        return c

    def test_book_it_again_after_booking_is_not_a_second_booking(self):
        c = self.booked()
        reply = c.say("Okay. Book it.").text
        self.assertNotIn("already has an appointment", reply)
        self.assertEqual(len(self.booked_rows()), 1)

    def test_change_that_one_is_understood(self):
        self.booked()
        c = Call("second")
        c.run("", "I want to book a cleaning", "My name is Priya", PHONE, "yes")
        self.assertEqual(c.s.pending, Goal.DUPLICATE_CHECK, c.lines)
        c.say("I wanted to change it.")
        self.assertEqual(c.s.intent, Intent.RESCHEDULE)
        self.assertNotEqual(c.s.pending, Goal.DUPLICATE_CHECK)

    def test_another_one_is_understood(self):
        self.booked()
        c = Call("third")
        c.run("", "I want to book a cleaning", "My name is Priya", PHONE, "yes")
        c.say("I want another one.")
        self.assertEqual(c.s.intent, Intent.BOOK)
        self.assertTrue(c.s.book.duplicate_ok)
        self.assertNotIn(c.s.pending, (Goal.DUPLICATE_CHECK, Goal.CALLBACK_OFFER))


class PhoneNumberTests(EngineCase):
    def cancel_call(self, caller_id="7899377462"):
        c = Call()
        ai_engine.phone_line(c.s, caller_id, can_transfer=True)
        c.say("")
        c.say("I'd like to cancel my appointment.")
        return c

    def test_dates_and_partial_numbers_are_not_collected_as_digits(self):
        for said in ("On Saturday, October 10 at 03:30PM,", "Oh, my phone number ends at zero zero three.",
                     "Phone ends at 0004."):
            u = _understand(said, Goal.PHONE_MORE, Expect.PHONE, Intent.CANCEL)
            self.assertFalse(u.phone_digits, said)
        self.assertEqual(_understand("nine two six one", Goal.PHONE_MORE, Expect.PHONE).phone_digits, "9261")

    def test_not_knowing_the_number_is_answered_with_why_it_is_needed(self):
        c = self.cancel_call()
        reply = c.say("I don't know. I only know the name.").text
        self.assertRegex(reply, r"(?i)number")
        self.assertNotRegex(reply, r"(?i)which number should i use|best number to reach you")
        self.assertEqual(c.s.pending, Goal.ASK_PHONE)

    def test_no_number_on_a_phone_call_ends_in_a_callback_not_a_hang_up(self):
        c = self.cancel_call()
        for said in ("I don't know. I only know the name.", "On Saturday, October 10 at 03:30PM,",
                     "Cancel the appointment under the name, Ishita.", "Okay.", "I don't have it."):
            c.say(said)
            if c.s.pending == Goal.CALLBACK_OFFER:
                break
        self.assertEqual(c.s.pending, Goal.CALLBACK_OFFER, c.lines)
        self.assertNotRegex(" ".join(c.lines), r"line isn't great")


class HearingTests(EngineCase):
    def test_can_you_hear_me_is_answered(self):
        for said in ("hello can you hear me", "can you hear me now", "are you there", "hello are you there"):
            self.assertTrue(HEAR_CHECK_RE.match(said), said)
        c = Call()
        c.run("", "I'd like to book an appointment.")
        reply = c.say("Hello? Can you hear me?").text
        self.assertRegex(reply, r"(?i)hear you|i'm here")
        self.assertNotRegex(reply, r"(?i)not sure|don't know")

    def test_asking_her_to_cancel_is_a_request_not_a_question(self):
        self.assertFalse(looks_like_question("Can you cancel the appointment?"))
        self.assertTrue(looks_like_question("Can you tell me the cancellation fee?"))


class ValidatorTests(unittest.TestCase):
    """What the model said on 8 Oct that must never reach the caller."""

    def setUp(self):
        from dialogue.validate import Allowed
        self.allowed = Allowed(numbers=frozenset({"7", "8"}), person_names=frozenset({"shetty", "jimmy"}))

    def check(self, sentence, **changes):
        from dialogue.validate import check_sentence
        allowed = self.allowed.for_turn(**changes) if changes else self.allowed
        return check_sentence(sentence, allowed, part="say")

    def test_a_change_claimed_but_not_made(self):
        self.assertEqual(self.check("Ah, seven instead of eight, got it.").rule, "V3")
        self.assertEqual(self.check("I've changed it to seven.").rule, "V3")
        self.assertTrue(self.check("I've changed it to seven.", committed="rescheduled").ok)
        self.assertTrue(self.check("Got it, morning.").ok)

    def test_an_invented_policy(self):
        v = self.check("Nothing serious, we just prefer a quick call if you can't make it so we can "
                       "give the slot to someone else.")
        self.assertEqual(v.rule, "V9")
        self.assertTrue(self.check("There's no fee to cancel.").ok)            # a real policy line
        allowed = self.allowed.for_turn(policies=frozenset({"policy.no_show"}))
        from dialogue.validate import check_sentence
        self.assertTrue(check_sentence("If you can't make it, just give us a call.", allowed, part="say").ok)

    def test_the_machinery_and_titles(self):
        self.assertEqual(self.check("Ah, just a bit of a mix-up with the speech-to-text.").rule, "V2")
        self.assertEqual(self.check("Got it, Mr Shetty.").rule, "V2")
        self.assertTrue(self.check("Got it, Jimmy.").ok)


class TimeTests(EngineCase):
    def test_an_hour_after_asking_for_the_morning_is_the_morning(self):
        # "8 or 8:30 in the morning?" -> "Eight. I'll come at eight." -> "morning or evening?" (twice)
        c = Call()
        c.run("", "I want to book a cleaning", "My name is Priya", PHONE, "yes", "Indiranagar", "Monday", "morning")
        reply = c.say("Eight. I'll come at eight.").text
        self.assertNotEqual(c.s.pending, Goal.RESOLVE_AMPM, reply)
        self.assertIn("8 in the morning", reply)

    def test_a_time_said_right_after_booking_moves_that_booking(self):
        # Booked 8:00, then "Seven": the model said "seven instead of eight, got it" and changed nothing.
        c = Call()
        c.run("", "I want to book a cleaning", "My name is Priya", PHONE, "yes", "Indiranagar", "Monday",
              "morning", "Eight", "Yes")
        self.assertEqual(c.s.pending, Goal.BOOKED, c.lines)
        reply = c.say("Seven").text
        self.assertEqual(c.s.pending, Goal.CONFIRM_RESCHEDULE, reply)
        self.assertIn("7 in the morning", reply)
        self.assertEqual(c.say("Yes").action, "rescheduled")
        rows = self.booked_rows()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["start_utc"].endswith("01:30:00Z"), rows[0]["start_utc"])   # 7:00 IST


class FlowTests(EngineCase):
    def to_branch(self, c):
        c.run("", "I want to book a check-up", "My name is Barat", PHONE, "yes")
        self.assertEqual(c.s.pending, Goal.ASK_BRANCH, c.lines)

    def test_whenever_available_keeps_the_day_given(self):
        c = Call()
        self.to_branch(c)
        c.say("Tomorrow.")
        reply = c.say("Whenever it's available. Morning only.").text
        self.assertNotEqual(c.s.pending, Goal.CONFIRM_CHANGE, reply)
        self.assertNotIn("earliest you can", reply)
        self.assertEqual(c.s.book.date_c.kind, "exact")

    def test_a_time_inside_the_morning_is_not_a_change(self):
        c = Call()
        self.to_branch(c)
        c.say("Tomorrow morning.")
        reply = c.say("Ten o'clock.").text
        self.assertNotEqual(c.s.pending, Goal.CONFIRM_CHANGE, reply)

    def test_the_ai_question_inside_a_longer_turn(self):
        c = Call()
        c.run("", "I want to book a cleaning", "My name is Priya", PHONE, "yes", "Indiranagar", "Monday",
              "morning", "The first one", "Yes")
        reply = c.say("Yeah. I'm talking. I'm not... Are you an AI?").text
        self.assertIn("virtual receptionist", reply)
        self.assertNotEqual(c.s.pending, Goal.CLOSE, reply)

    def test_asking_a_price_after_booking_is_not_a_change(self):
        c = Call()
        c.run("", "I want to book a check-up", "My name is Barat", PHONE, "yes", "Indiranagar", "Monday",
              "morning", "The first one", "Yes")
        reply = c.say("What is the consultation fees?").text
        self.assertNotRegex(reply, r"(?i)change the visit")
        self.assertNotEqual(c.s.pending, Goal.CONFIRM_CHANGE, reply)


if __name__ == "__main__":
    unittest.main()
