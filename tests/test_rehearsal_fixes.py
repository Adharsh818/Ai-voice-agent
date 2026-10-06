"""Bugs found by running docs/DEMO_SCRIPT.md through the engine on 7 Oct (model down, Tier-0 only)."""

import asyncio
import unittest
from datetime import datetime

import clock
import dateparse
import tier0
from dialogue import context, engine
from dialogue.context import Expect, Goal, Intent, Tier0View
from dialogue.match import clean_name
from dialogue.testing import DemoClinic


class RehearsalFixTests(unittest.TestCase):
    def test_a_question_about_a_branch_does_not_choose_it(self):
        with DemoClinic():
            async def go():
                rt = await engine.build_runtime(context.new_context("x"))
                view = Tier0View(expect=Expect.NAME, pending=Goal.ASK_NAME, intent=Intent.BOOK, catalog=rt.catalog)
                asked = tier0.understand("Sorry, where is your Jayanagar branch?", view, lenient=True)
                chose = tier0.understand("Jayanagar is better for me.", view, lenient=True)
                return asked, chose
            asked, chose = asyncio.run(go())
        self.assertIsNone(asked.branch)
        self.assertTrue(asked.question)
        self.assertEqual(chose.branch, "Jayanagar")

    def test_the_patient_name_said_as_her_name_is(self):
        for said in ("Her name is Diya.", "She's called Diya.", "My daughter's name is Diya", "His name's Diya"):
            self.assertEqual(clean_name(said), "Diya", said)
        self.assertIsNone(clean_name("she is seven"))

    def test_a_reason_given_with_the_cancel_request_is_kept(self):
        with DemoClinic():
            async def go():
                rt = await engine.build_runtime(context.new_context("x"))
                view = Tier0View(expect=Expect.OPEN, pending=Goal.ASK_INTENT, intent=Intent.NONE, catalog=rt.catalog)
                return [tier0.understand(t, view, lenient=True) for t in (
                    "I want to cancel my appointment, I'm travelling that week.",
                    "Please cancel my appointment because something came up.",
                    "I want to cancel my appointment.")]
            with_reason, because, bare = asyncio.run(go())
        self.assertEqual(with_reason.cancel_reason, "I'm travelling that week")
        self.assertEqual(because.cancel_reason, "something came up")
        self.assertIsNone(bare.cancel_reason)
        self.assertEqual(bare.intent, Intent.CANCEL)

    def test_a_question_about_opening_hours_is_not_a_booking_day(self):
        with DemoClinic():
            async def go():
                rt = await engine.build_runtime(context.new_context("x"))
                view = Tier0View(expect=Expect.OPEN, pending=Goal.ASK_INTENT, intent=Intent.NONE, catalog=rt.catalog)
                return [tier0.understand(t, view, lenient=True) for t in (
                    "What are your timings on Saturday?", "Can I come on Saturday?")]
            hours, visit = asyncio.run(go())
        self.assertIsNone(hours.date_phrase)
        self.assertTrue(hours.question)
        self.assertIsNotNone(visit.date_phrase)

    def test_a_typed_call_skips_the_silence_ladder(self):
        from test_realtime_support import make_session

        async def go():
            s, _ = make_session()
            await s.on_control({"type": "hello", "v": 2, "typed": True})
            return s.typed
        self.assertTrue(asyncio.run(go()))

    def test_when_a_symptom_started_is_not_when_to_come(self):
        with clock.frozen(datetime(2026, 10, 8, 10, 0)):
            for said in ("I have really bad swelling and pain since last night.", "It's hurt since this morning",
                         "pain for two days ago"):
                self.assertTrue(dateparse.parse_when(said).empty, said)
            self.assertEqual(dateparse.parse_when("Can I come tonight?").time.label, "tonight")
            self.assertIsNotNone(dateparse.parse_when("last night it started, can I come Monday evening?").date)


from test_r2_engine import Call, EngineCase  # noqa: E402  (tests/ is on sys.path under discover)


class OpeningRequestTests(EngineCase):
    """6 Oct converse rehearsal: with the model slow or down, the opening request is said back."""

    def first_reply(self, text):
        return Call().say(text).text

    def test_the_request_is_said_back_with_its_details(self):
        reply = self.first_reply("Hi, I'd like to book a check-up for my daughter, she's seven. "
                                 "A lady doctor if possible, at Jayanagar.")
        self.assertRegex(reply, r"^(Sure|Of course|Okay), a check-up for your daughter at Jayanagar\. ")

    def test_the_day_is_folded_into_the_same_sentence(self):
        reply = self.first_reply("Hi, I'd like to get my teeth cleaned on Monday afternoon.")
        self.assertRegex(reply, r"^(Sure|Of course|Okay), a cleaning for Monday the 5th, in the afternoon\. ")
        self.assertEqual(reply.lower().count("monday"), 1, reply)

    def test_earliest_is_one_acknowledgement(self):
        reply = self.first_reply("Can I book a cleaning for the earliest you have?")
        self.assertRegex(reply, r"^(Sure|Of course|Okay), a cleaning as soon as we can\. ")
        self.assertNotIn("not sure", reply.lower())

    def test_not_said_when_another_line_already_answers_the_request(self):
        braces = self.first_reply("Hi, I need braces at Nagarbhavi.")
        self.assertTrue(braces.startswith("We don't do braces at Nagarbhavi"), braces)
        self.assertNotIn("braces at Nagarbhavi.", braces.split(". ", 1)[1])
        price = self.first_reply("Can I get the price for a cleaning?")
        self.assertEqual(price.lower().count("a cleaning"), 1, price)
        urgent = self.first_reply("Hi, I have really bad swelling and pain since last night.")
        self.assertTrue(urgent.startswith("Oh"), urgent)
        self.assertNotIn("consultation", urgent.lower())

    def test_only_on_the_opening_request(self):
        call = Call()
        call.say("Hi, I'd like to book an appointment.")
        reply = call.say("Neha Kapoor.").text
        self.assertNotRegex(reply, r"(Sure|Of course|Okay), a ")

    def test_asking_to_book_is_not_a_question_for_the_fact_lookup(self):
        from dialogue.match import looks_like_question
        self.assertFalse(looks_like_question("Hi, can I book a cleaning around 6 in the evening?"))
        self.assertFalse(looks_like_question("Could you book me in for a check-up?"))
        self.assertTrue(looks_like_question("Can I get a check-up, and where is your Jayanagar branch?"))
        self.assertTrue(looks_like_question("Can I get the price for a cleaning?"))
        self.assertTrue(looks_like_question("Can you tell me your timings?"))
        reply = self.first_reply("Hi, can I book a cleaning around 6 in the evening?")
        self.assertNotRegex(reply.lower(), r"not sure|don't know")


if __name__ == "__main__":
    unittest.main()
