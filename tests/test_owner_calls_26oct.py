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


if __name__ == "__main__":
    unittest.main()
