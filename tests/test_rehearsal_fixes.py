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


if __name__ == "__main__":
    unittest.main()
