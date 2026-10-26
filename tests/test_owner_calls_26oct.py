"""
The owner's phone call of 26 Oct (6cb96ca2), replayed as Emma heard it, with the model down.

The line lost about half the caller's audio, so Emma heard "Indiranjani" for Indiranagar,
"Start" for Thor and "I want at 8PM" for "I want at eight AM", and booked 8 in the evening
at Nagarbhavi. She now checks what doesn't add up, as a receptionist on a bad line would:
- a branch answer clearly nearest one branch is that branch;
- a word that isn't a name is asked again;
- "8PM" after the caller asked for the morning is "8 in the morning or the evening?";
- "AM." when the summary says 8 in the evening moves it to 8 in the morning;
- "I want that eight" picks 8;
- when the branch question runs out, she says she'll look at every branch.
"""

import unittest
from datetime import datetime
from types import SimpleNamespace

import ai_engine
import nlu
import prompts
from dialogue import match
from dialogue.testing import DemoClinic
from test_r2_engine import Call


class OwnerCall26Case(unittest.TestCase):
    NOW = datetime(2026, 10, 26, 11, 28)           # Monday morning, as on the day

    def setUp(self):
        self.clinic = DemoClinic(now=self.NOW)
        self.clinic.__enter__()
        self.backend = nlu.use_backend(nlu.FakeNLU(usable=False))
        self.backend.__enter__()

    def tearDown(self):
        self.backend.__exit__(None, None, None)
        self.clinic.__exit__(None, None, None)

    def call(self, *lines):
        call = Call()
        ai_engine.phone_line(call.s, "9845022222", can_transfer=True)
        for line in ("",) + lines:
            reply = call.run(line).text
        return call, reply


class BranchHeardBadlyTests(unittest.TestCase):
    BRANCHES = [SimpleNamespace(name=n, area="") for n in ("Nagarbhavi", "Indiranagar", "Jayanagar", "Whitefield")]

    def test_an_answer_nearest_one_branch_is_that_branch(self):
        for heard in ("Indiranjani.", "Indranagar.", "Indranager", "Indira"):
            self.assertEqual(match.match_branch(heard, self.BRANCHES, asked=True).value, "Indiranagar", heard)

    def test_words_that_are_not_near_a_branch_stay_unmatched(self):
        for heard in ("me.", "India", "And then another is uncomfortable doing"):
            self.assertIsNone(match.match_branch(heard, self.BRANCHES, asked=True).value, heard)

    def test_outside_the_branch_question_the_loose_match_is_off(self):
        self.assertIsNone(match.match_branch("Indiranjani.", self.BRANCHES).value)


class NameHeardBadlyTests(OwnerCall26Case):
    def test_an_everyday_word_is_asked_again(self):
        _, reply = self.call("I want to book appointment", "Start")
        self.assertNotIn("Start", reply)
        self.assertIn("name", reply.lower())

    def test_the_name_said_again_is_taken(self):
        _, reply = self.call("I want to book appointment", "Start", "Thor.")
        self.assertIn("Thor", reply)

    def test_pizza_is_not_a_name(self):
        self.assertIsNone(match.clean_name("Pizza."))
        self.assertEqual(match.clean_name("Peter."), "Peter")


class AmPmHeardBadlyTests(OwnerCall26Case):
    TO_OFFER = ("I want to book a check-up.", "Peter.", "Yes.", "Indiranagar.", "Tomorrow.", "Morning.")

    def test_8pm_after_asking_for_the_morning_is_checked(self):
        _, reply = self.call(*self.TO_OFFER, "I want at 8PM.")
        self.assertRegex(reply, r"8 in the morning,? or (?:in )?the evening")
        self.assertNotIn("Nagarbhavi", reply)

    def test_morning_then_books_8_in_the_morning_where_asked(self):
        call, reply = self.call(*self.TO_OFFER, "I want at 8PM.", "Morning.")
        self.assertIn("8 in the morning", reply)
        self.assertIn("Indiranagar", reply)
        self.assertIn("booked", call.run("Yes.").text)

    def test_the_whole_call_as_heard_ends_at_8_in_the_morning_at_indiranagar(self):
        call, reply = self.call("Yeah.", "I want to book appointment", "Start", "Thor.", "Yeah. It's good.",
                                "I'm fine with it.", "A routine checkup.", "Indiranjani.", "Tomorrow?",
                                "It's the damn.", "Morning.", "I want a", "I want at 8PM.", "Morning.", "Yes.")
        self.assertIn("8 in the morning", reply)
        self.assertIn("Indiranagar", reply)
        self.assertNotIn("evening", reply)

    def test_8_in_the_evening_said_in_full_is_taken(self):
        _, reply = self.call(*self.TO_OFFER, "At 8 in the evening.")
        self.assertNotRegex(reply, r"morning,? or (?:in )?the evening")
        self.assertIn("8 in the evening", reply)

    def test_am_at_what_to_change_moves_to_the_morning(self):
        call, reply = self.call("I want to book a check-up.", "Peter.", "Yes.", "Indiranagar.", "Tomorrow.",
                                "Evening.", "At 8 in the evening.", "Yes.")
        self.assertIn("8 in the evening", reply)
        call.run("No.")
        reply = call.run("AM.").text
        self.assertIn("8 in the morning", reply)

    def test_part_of_day_fixes(self):
        import tier0
        for said, part in (("AM.", "morning"), ("a.m.", "morning"), ("No, morning.", "morning"),
                           ("Not evening, morning", "morning"), ("PM", "evening"), ("in the evening", "evening")):
            self.assertEqual(tier0._part_of_day_fix(said), part, said)
        for said in ("Book it.", "Morning or evening", "I want a different day"):
            self.assertIsNone(tier0._part_of_day_fix(said), said)


class TimePickTests(OwnerCall26Case):
    def test_i_want_that_eight(self):
        _, reply = self.call("I want to book a check-up.", "Peter.", "Yes.", "Indiranagar.", "Tomorrow.",
                             "Morning.", "I want that eight.")
        self.assertIn("8 in the morning", reply)


class BranchRunsOutTests(OwnerCall26Case):
    def test_she_says_she_will_look_at_every_branch(self):
        call, reply = self.call("I want to book a check-up.", "Peter.", "Yes.")
        self.assertIn("Whitefield", reply)
        for heard in ("Mm.", "Something.", "me."):
            reply = call.run(heard).text
        self.assertRegex(reply, r"all our branches")


class SecondCallTests(OwnerCall26Case):
    """The owner's calls at 12:47 and 12:50 on 26 Oct (5a08a0ff, 1a5ba1e2), over 5 GHz."""

    TO_OFFER = ("Can I book an appointment?", "Adash.", "Yeah.", "A tooth cleaning.", "Indiranagar.",
                "Tomorrow.", "I want morning.")

    def test_a_branch_heard_badly_at_the_offer_is_checked_then_taken(self):
        call, reply = self.call(*self.TO_OFFER, "I want Nagar file.")
        self.assertIn("Nagarbhavi", reply)
        self.assertIn("?", reply)
        self.assertIn("Nagarbhavi", call.run("Yes.").text)
        self.assertEqual(call.s.book.branch, "Nagarbhavi")

    def test_a_near_branch_at_the_branch_question_is_said_back(self):
        _, reply = self.call("I want to book appointment.", "Aayush.", "Yeah.", "A check-up.", "Indiranjan.")
        self.assertIn("Indiranagar", reply.split(".")[0], reply)

    def test_a_branch_heard_clearly_is_not_said_back(self):
        _, reply = self.call("I want to book appointment.", "Aayush.", "Yeah.", "A check-up.", "Indiranagar.")
        self.assertNotIn("Indiranagar", reply)

    def test_the_readback_says_the_part_of_the_day(self):
        call, reply = self.call("I want to book appointment.", "Aayush.", "Yeah.", "A check-up.", "Nagarbhavi.",
                                "Tomorrow.", "5PM.")
        self.assertIn("tomorrow at 5 in the evening", reply)
        self.assertIn("tomorrow at 5 in the evening", call.run("Yeah.").text)

    def test_the_same_time_again_after_a_no_names_the_branches_that_could(self):
        call, reply = self.call(*self.TO_OFFER, "I want in the evening, five, actually.")
        self.assertIn("Jayanagar", reply)
        call.run("No.")
        reply = call.run("At five in the evening.").text
        self.assertRegex(reply, r"isn't possible at Indiranagar|Indiranagar can't do")
        self.assertNotIn("Jayanagar", reply)
        self.assertNotRegex(reply, r"^(Sure|Okay|Got it), (at )?5")


class ModelBranchTests(unittest.TestCase):
    SCHEMA = {"properties": {"branch": {"enum": ["Nagarbhavi", "Indiranagar", "Jayanagar", "Whitefield"]}}}

    def test_a_branch_the_caller_never_said_is_dropped(self):
        # "Ananya, please." came back from the model as Indiranagar, and Emma went on to "When's good?".
        self.assertIsNone(nlu.from_json({"branch": "Indiranagar"}, "Ananya, please.", self.SCHEMA).branch)

    def test_a_branch_backed_by_the_callers_words_is_kept(self):
        for raw in ("Indiranagar please", "Indiranjan", "the second one", "with Dr Rao", "the same branch"):
            self.assertEqual(nlu.from_json({"branch": "Indiranagar"}, raw, self.SCHEMA).branch, "Indiranagar", raw)


class RulesNotModelTests(unittest.TestCase):
    """Lines from the owner's 25-26 Oct calls that went to the model, now answered by rules."""

    def u(self, text, goal, intent=None, lenient=False):
        import tier0
        from dialogue.context import Intent
        from test_tier0_v2 import view
        return tier0.understand(text, view(goal, intent=intent or Intent.BOOK), lenient=lenient)

    def test_openers(self):
        from dialogue.context import Goal, Intent
        for said in ("Oh, I want to book appointment with you.", "Hey. I'd like to book a meeting with you."):
            self.assertEqual(self.u(said, Goal.GREET, Intent.NONE).intent, Intent.BOOK, said)

    def test_pickup_is_a_check_up_only_when_asked(self):
        from dialogue.context import Goal
        for said in ("Pickup.", "At pickup.", "I wanted a pickup."):
            self.assertEqual(self.u(said, Goal.ASK_SERVICE).service, "General Check-up", said)
        from dialogue import match
        self.assertIsNone(match.match_service("I'll pick up my son", []).value)

    def test_days_said_with_a_rising_tone_or_works_for_me(self):
        from dialogue.context import Goal
        self.assertEqual(self.u("Tomorrow?", Goal.ASK_WHEN).date_phrase, "Tomorrow")
        self.assertTrue(self.u("Friday works for me.", Goal.ASK_WHEN).date_phrase)
        self.assertIsNone(self.u("Is Friday?", Goal.ASK_WHEN))

    def test_the_same_number(self):
        from dialogue.context import Goal
        for said in ("I used this number.", "Use the same number.", "This number is fine.", "Same number."):
            self.assertEqual(self.u(said, Goal.CONFIRM_PHONE).confirmation, "yes", said)
        self.assertIsNone(self.u("I use another number.", Goal.CONFIRM_PHONE))

    def test_a_treatment_in_the_opening_line_is_a_booking_with_the_model_down(self):
        from dialogue.context import Goal, Intent
        u = self.u("Tomorrow evening around 6 with Dr Sharma, for a check-up.", Goal.GREET, Intent.NONE,
                   lenient=True)
        self.assertEqual((u.intent, u.service), (Intent.BOOK, "General Check-up"))


class LiveRunTests(OwnerCall26Case):
    """The full live scenario run of 26 Oct, 15:45."""

    def test_call_me_back_while_she_takes_the_number_asks_for_it_again(self):
        from dialogue.context import Goal, Intent
        import tier0
        from test_tier0_v2 import view
        u = tier0.understand("Please just have someone call me back.", view(Goal.ASK_PHONE, intent=Intent.NONE))
        self.assertEqual(u.acts, ["unclear"])

    def test_the_model_never_says_a_callback_cant_be_arranged(self):
        from dialogue import validate
        for said in ("I can't set up a callback from here.", "I'm unable to transfer you right now."):
            self.assertIsNotNone(validate._first(validate._CANT_DO, said.lower()), said)
        self.assertIsNone(validate._first(validate._CANT_DO, "i can't wait to see you."))

    def test_sunday_at_10_keeps_the_10(self):
        call, reply = self.call("Hi, I would like to book a cleaning.", "Priya Sharma.", "Yes.",
                                "Nagarbhavi works for me.", "Sunday at 10?")
        self.assertRegex(reply, r"closed on Sundays?")
        self.assertNotIn("got it", reply)
        self.assertNotRegex(reply, r"(^|\. )10\.")
        self.assertIn("10 in the morning", call.run("Monday.").text)


class EveningCallTests(OwnerCall26Case):
    """The owner's phone calls at 21:49-21:53 on 26 Oct (2161f569, 3babd623), Gemini too slow to help."""

    def test_theres_is_not_a_name_and_names_are_cased_right(self):
        self.assertIsNone(match.clean_name("Oh, there's"))
        self.assertEqual(match.clean_name("d'souza"), "D'Souza")
        self.assertEqual(match.clean_name("mary-anne"), "Mary-Anne")
        self.assertEqual(match.clean_name("Mister Viha's"), "Viha")       # website call, 26 Oct: "Okay, Mister."

    def test_garbled_words_while_collecting_get_the_question_not_not_sure(self):
        call, _ = self.call("I I want to book a appointment.", "Play others.", "Others.")
        reply = call.run("Aditya, how does it...").text
        self.assertNotIn("not sure", reply)
        for line in ("Aditya. Aditya.", "Yes.", "A checkup.", "Indiran order.", "Tomorrow?"):
            call.run(line)
        reply = call.run("My name?").text
        self.assertNotIn("not sure", reply)
        self.assertRegex(reply, r"time|morning|evening")

    def test_yes_to_anything_else_asks_what(self):
        call, _ = self.call("I want to book a check-up.", "Peter.", "Yes.", "Indiranagar.", "Tomorrow.",
                            "Morning.", "Seven in the morning.", "Yes.")
        reply = call.run("Yeah.").text
        self.assertIn(reply, prompts.VARIANTS["anything_else.yes"])


class SpokenTimeTests(unittest.TestCase):
    def test_clear_times(self):
        import prompts
        from datetime import time
        self.assertEqual(prompts.speak_time_clear(time(17, 0)), "5 in the evening")
        self.assertEqual(prompts.speak_time_clear(time(13, 30)), "1:30 in the afternoon")
        self.assertEqual(prompts.speak_time_clear(time(9, 0)), "9 in the morning")
        self.assertEqual(prompts.speak_time_clear(time(12, 0)), "12 noon")


if __name__ == "__main__":
    unittest.main()
