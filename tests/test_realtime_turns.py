"""Adaptive end-of-turn (plan R3.2): what counts as a finished turn, and holding/merging."""

import asyncio
import time
import unittest

import ai_engine
import turn_detector
from turn_detector import TurnDetector, classify, hint_from_state

from test_realtime_support import fast_holds


class ClassifyTests(unittest.TestCase):
    def kind(self, text, expect="open", so_far=0):
        return classify(text, {"expect": expect, "digits_so_far": so_far}).kind

    def test_the_owners_fragments_are_never_complete(self):
        # Every fragment from the 30 Sep - 1 Oct calls, with what Emma was asking at the time.
        for text, expect in [("What's the best", "open"), ("Don't", "yes_no"), ("But", "yes_no"),
                             ("Tell me what can you", "open"), ("Cancel the com", "yes_no"),
                             ("Tell me how good you", "open"), ("November 22, at", "date")]:
            with self.subTest(text=text):
                verdict = classify(text, {"expect": expect})
                self.assertIn(verdict.kind, ("unsure", "unfinished"), verdict)
                self.assertGreaterEqual(verdict.silence_ms, 1200)

    def test_complete_answers_to_the_question_commit_at_once(self):
        for text, expect in [("yes", "yes_no"), ("Yeah, go ahead.", "yes_no"), ("No thanks.", "yes_no"),
                             ("(789) 937-7462", "phone"),
                             ("nine eight four five zero double one two three four", "phone"),
                             ("Next Monday.", "date"), ("the 22nd", "date"), ("5 PM.", "time"),
                             ("five thirty", "time"), ("My name is Priya.", "name"),
                             ("Priya Sharma", "name"), ("The second one.", "choice"),
                             ("hold on", "phone"), ("Thank you, bye.", "open")]:
            with self.subTest(text=text):
                verdict = classify(text, {"expect": expect})
                self.assertEqual((verdict.kind, verdict.silence_ms), ("complete", 0), verdict)

    def test_clearly_unfinished_speech_waits(self):
        for text in ["I want to book an appointment and", "so", "my number is", "Can I get the",
                     "it's for my", "um", "Yes, but", "Can you"]:
            with self.subTest(text=text):
                self.assertEqual(self.kind(text), "unfinished")

    def test_half_a_phone_number_waits_longest(self):
        self.assertEqual(self.kind("98450", "phone"), "digits")
        self.assertEqual(self.kind("nine eight four five zero", "phone"), "digits")
        self.assertEqual(self.kind("12345", "phone", so_far=5), "complete")      # the rest of it
        self.assertEqual(self.kind("0 98450 12345", "phone"), "complete")          # 11 with the 0
        self.assertEqual(self.kind("098450 1234", "phone"), "digits")
        self.assertEqual(self.kind("my mobile number is 98450"), "digits")         # announced, any step
        self.assertEqual(classify("98450", {"expect": "phone"}).silence_ms, 2000)

    def test_open_answers_get_a_short_natural_pause(self):
        self.assertEqual(self.kind("I'd like a cleaning"), "likely")
        self.assertEqual(self.kind("How are you?"), "likely")
        self.assertEqual(self.kind("Hello."), "default")          # usually followed by the request
        self.assertEqual(self.kind("Okay."), "default")           # after "How can I help?"
        self.assertEqual(self.kind("Sorry?"), "complete")         # asked to repeat
        self.assertEqual(self.kind("Sorry"), "default")           # usually starts a correction

    def test_clinic_names_are_not_mistaken_for_cut_off_words(self):
        self.assertEqual(self.kind("Tomorrow with Nair", "open"), "unsure")
        turn_detector.add_vocabulary(["Dr Nair", "Whitefield"])
        try:
            self.assertEqual(self.kind("Tomorrow with Nair", "open"), "likely")
        finally:
            turn_detector._KNOWN_WORDS.difference_update({"dr", "nair", "whitefield"})
        self.assertNotEqual(self.kind("Dr Iyer", "choice"), "unsure")      # a surname after "Dr"

    def test_engine_enum_expect_values_are_accepted(self):
        class Expect(str):
            value = "phone"
        self.assertEqual(classify("98450", {"expect": Expect("phone")}).kind, "digits")

    def test_hint_from_the_old_step_engine(self):
        s = ai_engine.SessionState()
        self.assertEqual(hint_from_state(s)["expect"], "open")
        s.step, s.temp_phone = 4, ""
        self.assertEqual(hint_from_state(s)["expect"], "phone")
        s.temp_phone = "9876543210"
        self.assertEqual(hint_from_state(s)["expect"], "yes_no")
        s.step = 9
        self.assertEqual(hint_from_state(s)["expect"], "yes_no")
        s.step, s.temp_date = 7, ""
        self.assertEqual(hint_from_state(s)["expect"], "date")
        self.assertEqual(hint_from_state(object())["expect"], "open")


class DetectorTests(unittest.TestCase):
    def run_detector(self, feed, hint=None):
        """feed(detector) -> coroutine; returns the turns handed over."""
        turns = []

        async def on_turn(text, end_wall, source, received, verdict):
            turns.append((text, verdict.kind))

        async def run():
            detector = TurnDetector(on_turn, hint=lambda: hint or {"expect": "open"})
            await feed(detector)
            await asyncio.sleep(0.2)
            return detector

        detector = asyncio.run(run())
        self.assertFalse(detector.holding)
        return turns

    def test_fragment_and_its_continuation_become_one_turn(self):
        async def feed(d):
            await d.utterance("What's the best", source="speech_final")
            await asyncio.sleep(0.02)
            await d.utterance("time to come in on Saturday?", source="speech_final")

        with fast_holds():
            turns = self.run_detector(feed)
        self.assertEqual(turns, [("What's the best time to come in on Saturday?", "likely")])

    def test_each_owner_fragment_is_merged_not_answered(self):
        pairs = [("Don't", "book it yet."), ("But", "can I come on Monday instead?"),
                 ("Tell me what can you", "do for me."), ("Cancel the com", "plete appointment."),
                 ("Tell me how good you", "are with kids.")]
        for first, rest in pairs:
            async def feed(d, first=first, rest=rest):
                await d.utterance(first)
                await asyncio.sleep(0.01)
                await d.utterance(rest)

            with self.subTest(first=first), fast_holds():
                turns = self.run_detector(feed, {"expect": "yes_no"})
                self.assertEqual(len(turns), 1, turns)
                self.assertEqual(turns[0][0], f"{first} {rest}")

    def test_partial_number_is_held_then_completed(self):
        async def feed(d):
            await d.utterance("98450")
            await asyncio.sleep(0.03)
            self.assertTrue(d.holding)
            await d.utterance("12345")

        with fast_holds():
            turns = self.run_detector(feed, {"expect": "phone"})
        self.assertEqual(turns, [("98450 12345", "complete")])

    def test_a_complete_answer_is_handed_over_without_waiting(self):
        seen = []

        async def on_turn(text, *_):
            seen.append(text)

        async def run():
            d = TurnDetector(on_turn, hint=lambda: {"expect": "yes_no"})
            await d.utterance("Yes please.")
            return list(seen)          # before any sleep

        self.assertEqual(asyncio.run(run()), ["Yes please."])

    def test_an_unfinished_turn_is_released_after_its_silence(self):
        async def feed(d):
            await d.utterance("I want to come on Monday and")

        with fast_holds():
            turns = self.run_detector(feed)
        self.assertEqual(turns, [("I want to come on Monday and", "unfinished")])

    def test_waits_count_from_the_last_word_so_endpointing_200_or_400_both_work(self):
        # "and..." needs 1600 ms of silence. With 200 ms endpointing the event
        # arrives ~250 ms after the last word, with 400 ms ~450 ms after: the
        # detector waits only for what is left.
        for arrived_after in (0.25, 0.45):
            clock = [100.0]
            spawned = []
            d = TurnDetector(lambda *a: None, hint=lambda: {"expect": "open"}, spawn=spawned.append,
                             now=lambda: clock[0])
            asyncio.run(d.utterance("and", end_wall=100.0 - arrived_after, received=100.0))
            for coro in spawned:
                coro.close()
            self.assertTrue(d.holding)
            remaining = d._deadline(d._held) - clock[0]
            self.assertAlmostEqual(remaining, 1.6 - arrived_after, places=3)
        # A complete answer never waits, whatever the endpointing.
        for arrived_after in (0.25, 0.45):
            handed = []

            async def on_turn(text, *_):
                handed.append(text)

            d = TurnDetector(on_turn, hint=lambda: {"expect": "yes_no"})
            asyncio.run(d.utterance("yes", end_wall=time.perf_counter() - arrived_after))
            self.assertEqual(handed, ["yes"])

    def test_still_talking_extends_the_hold_up_to_the_cap(self):
        clock = [50.0]
        d = TurnDetector(lambda *a: None, hint=lambda: {"expect": "open"}, spawn=lambda c: c.close(),
                         now=lambda: clock[0])
        asyncio.run(d.utterance("Hello.", end_wall=50.0, received=50.0))     # default: 800 ms
        base = d._deadline(d._held)
        clock[0] = 50.7
        d.activity()                                                          # new interim words
        self.assertGreater(d._deadline(d._held), base)
        clock[0] = 51.9
        d.activity()
        self.assertLessEqual(d._deadline(d._held), 50.0 + turn_detector.HOLD_MAX_MS / 1000 + 1e-6)

    def test_typed_text_is_never_held(self):
        async def feed(d):
            await d.utterance("and", hold=False)

        turns = self.run_detector(feed)
        self.assertEqual(turns, [("and", "complete")])


if __name__ == "__main__":
    unittest.main()
