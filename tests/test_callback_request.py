"""
A caller who asks to be called back, or for a branch's own phone number (27 Oct, A7).

With the model down, "I'd like someone from the clinic to call me back about my bill" only got
"What can I do for you?" again. Now the rules catch it and Emma takes the number for a callback
task straight away. The branch lines are never read out (owner's sign-off, A7): Emma says so and
offers the same callback.
"""

import unittest

import tier0
from dialogue import match
from dialogue.context import Goal, Intent
from test_owner_calls_26oct import OwnerCall26Case
from test_tier0_v2 import view


class RulesTests(unittest.TestCase):
    def acts(self, said, pending=Goal.ASK_INTENT):
        u = tier0.understand(said, view(pending, intent=Intent.NONE))
        return u.acts if u is not None else []

    def test_callback_requests_are_caught(self):
        for said in ("I'd like someone from the clinic to call me back about my bill.",
                     "Can someone call me back please?",
                     "Could you have the doctor call me about my reports?",
                     "I need a callback about my payment.",
                     "Just ring me back later."):
            self.assertEqual(self.acts(said), ["callback_request"], said)

    def test_branch_number_questions_are_caught(self):
        for said in ("What's the Whitefield branch number?", "Can I get the clinic's phone number?",
                     "What is the contact number for the Jayanagar branch?",
                     "Can I call the Indiranagar clinic directly?"):
            self.assertEqual(self.acts(said), ["callback_request"], said)

    def test_ordinary_turns_are_not(self):
        for said in ("Whitefield branch, no, Jayanagar.", "My number is 98450 22222.",
                     "I'll call you back later.", "I want to book a cleaning."):
            self.assertNotIn("callback_request", self.acts(said), said)
        self.assertIsNone(match.BRANCH_PHONE_RE.search("my phone number is the same"))

    def test_while_she_takes_the_number_it_stays_with_the_number(self):
        self.assertEqual(self.acts("Please just have someone call me back.", Goal.ASK_PHONE), ["unclear"])


class CallTests(OwnerCall26Case):
    def tasks(self, call):
        return self.clinic.query("SELECT * FROM tasks")

    def test_bill_callback_with_the_model_down_makes_a_task(self):
        call, reply = self.call("I'd like someone from the clinic to call me back about my bill.")
        self.assertNotIn("what can I do", reply.lower())
        self.assertRegex(reply, r"9 8 4 5 0|calling from|number")
        call.run("Yes.")
        tasks = self.tasks(call)
        self.assertEqual(len(tasks), 1)
        self.assertIn("bill", " ".join(str(v) for v in dict(tasks[0]).values()))

    def test_branch_number_is_never_read_out_and_a_callback_is_offered(self):
        call, reply = self.call("What's the Whitefield branch number?")
        self.assertRegex(reply, r"(direct line|own number).*call you back")
        self.assertNotIn("PLACEHOLDER", reply)
        call.run("Yes.")
        self.assertEqual(len(self.tasks(call)), 1)

    def test_on_the_website_she_asks_for_the_number_then_makes_the_task(self):
        from test_r2_engine import Call
        call = Call()
        call.run("")
        reply = call.run("Can someone from the clinic call me back about my bill?").text
        self.assertIn("number", reply)
        call.run("98450 22222.")
        call.run("Yes.")
        tasks = self.tasks(call)
        self.assertEqual(len(tasks), 1)
        self.assertIn("bill", " ".join(str(v) for v in dict(tasks[0]).values()))


if __name__ == "__main__":
    unittest.main()
