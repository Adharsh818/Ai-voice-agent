"""
The owner's three hosted test calls of 25 Oct (17:19-17:25), replayed with the model down.

- Call 2: a number given in pieces got "Mm-hmm." twice, "I can hear you, yes. Mm-hmm.", then
  "Could you say it a few digits at a time?", and the caller said the whole number again.
  Now she says back the digits she holds and what's missing.
- Call 2: "I'll take 9" after 8 and 8:30 were offered went to a callback offer, although 9 was free.
- Call 3: a cancel stalled because the caller didn't remember the date; the branch or the
  treatment now stands in for it (owner's choice, 25 Oct).
"""

import unittest
from datetime import datetime

import nlu
import prompts
from dialogue.testing import DemoClinic
from test_r2_engine import Call


class OwnerCallCase(unittest.TestCase):
    NOW = datetime(2026, 10, 25, 17, 20)           # Sunday evening, as on the day

    def setUp(self):
        self.clinic = DemoClinic(now=self.NOW)
        self.clinic.__enter__()
        self.backend = nlu.use_backend(nlu.FakeNLU(usable=False))
        self.backend.__enter__()

    def tearDown(self):
        self.backend.__exit__(None, None, None)
        self.clinic.__exit__(None, None, None)


class NumberInPiecesTests(OwnerCallCase):
    def _to_the_number(self):
        call = Call()
        call.run("", "Can I book a check-up for Saturday morning?")
        reply = call.run("Rahul Mehta.").text
        self.assertIn("number", reply.lower())
        return call

    def test_the_second_piece_hears_what_she_has_and_what_is_missing(self):
        call = self._to_the_number()
        first = call.run("Nine eight four five zero, double three,").text
        self.assertIn("9 8 4 5 0, 3 3", first)
        self.assertIn("last 3 digits", first)
        second = call.run("Double two.").text
        self.assertIn("9 8 4 5 0, 3 3 2 2", second)
        self.assertIn("last digit", second)
        self.assertNotIn("few digits at a time", second)

    def test_hello_mid_number_says_the_digits_back(self):
        call = self._to_the_number()
        call.run("Nine eight four five zero, double three,")
        reply = call.run("Hello?").text
        self.assertIn("9 8 4 5 0, 3 3", reply)
        self.assertIn("last 3 digits", reply)

    def test_the_last_digit_completes_the_number(self):
        call = self._to_the_number()
        call.run("Nine eight four five zero, double three,")
        call.run("Double two.")
        reply = call.run("Three.").text
        self.assertIn("9 8 4 5 0, 3 3 2 2 3", reply)

    def test_one_digit_too_many_is_said_plainly(self):
        call = self._to_the_number()
        call.run("Nine eight four five zero, double three,")
        call.run("Double two.")
        reply = call.run("Three three").text
        self.assertIn(reply, [v for v in prompts.VARIANTS["phone.too_many"]])
        reply = call.run("98450 33223.").text
        self.assertIn("9 8 4 5 0, 3 3 2 2 3", reply)

    def test_so_far_params(self):
        self.assertEqual(prompts.phone_so_far("984503322"),
                         {"so_far": "9 8 4 5 0, 3 3 2 2", "need": "Just the last digit, please."})
        self.assertEqual(prompts.phone_so_far("98450")["need"], "Just the last 5 digits, please.")
        self.assertEqual(prompts.phone_so_far("080234")["need"], "Just the last 5 digits, please.")
        self.assertEqual(prompts.phone_so_far("12")["need"], "What's the rest of the number?")


class TimeNotOfferedTests(OwnerCallCase):
    def _offered(self):
        call = Call()
        for line in ("", "Can I book a check-up for Saturday morning?", "Rahul Mehta.", "98450 33333.", "Yes."):
            call.run(line)
        reply = call.run("Whitefield.").text
        self.assertIn("8 in the morning", reply)
        return call

    def test_ill_take_9_asks_for_9(self):
        reply = self._offered().run("I'll take 9").text
        self.assertIn("at 9", reply)
        self.assertNotIn("call", reply)

    def test_i_wanted_ten_asks_for_10(self):
        reply = self._offered().run("I wanted ten").text
        self.assertIn("at 10", reply)


class CancelWithoutDateTests(OwnerCallCase):
    """Phone + name + (the date, or the branch or treatment): still three checks (owner, 25 Oct)."""

    def setUp(self):
        super().setUp()
        call = Call()
        for line in ("", "I'd like to get my teeth cleaned on Monday afternoon.", "Neha Kapoor.", "98450 22222.",
                     "Yes.", "Indiranagar.", "The first one."):
            call.run(line)
        self.assertIn("booked", call.run("Yes, book it.").text)

    def _to_the_date(self, number="98450 22222", name="Neha Kapoor."):
        call = Call()
        for line in ("", "I want to cancel my appointment.", number, "Yes."):
            call.run(line)
        reply = call.run(name).text.lower()
        self.assertTrue("day" in reply or "date" in reply, reply)
        return call

    def test_dont_remember_asks_branch_or_treatment_and_the_branch_finds_it(self):
        call = self._to_the_date()
        reply = call.run("I don't remember which day.").text
        self.assertIn("branch", reply)
        self.assertNotIn("don't know that one", reply)
        reply = call.run("It's at Indiranagar.").text
        self.assertIn("tomorrow at 12", reply)
        self.assertIn("cancel", reply)
        self.assertIn("cancelled", call.run("Yes, cancel it.").text)

    def test_a_wrong_date_then_the_treatment_finds_it(self):
        call = self._to_the_date()
        reply = call.run("Day after tomorrow.").text
        self.assertIn("branch", reply)
        self.assertNotIn("Dr", reply)                       # nothing revealed on a miss (Z7)
        reply = call.run("It's a cleaning.").text
        self.assertIn("tomorrow at 12", reply)

    def test_the_wrong_branch_reveals_nothing(self):
        call = self._to_the_date()
        call.run("I don't remember which day.")
        reply = call.run("It's at Whitefield.").text
        self.assertNotIn("12", reply)
        self.assertNotIn("Dr", reply)

    def test_another_name_on_the_number_reveals_nothing(self):
        call = self._to_the_date(name="Rahul Mehta.")
        call.run("I don't remember which day.")
        reply = call.run("It's at Indiranagar.").text
        self.assertNotIn("12", reply)
        self.assertNotIn("Dr", reply)


if __name__ == "__main__":
    unittest.main()
