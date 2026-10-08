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


if __name__ == "__main__":
    unittest.main()
