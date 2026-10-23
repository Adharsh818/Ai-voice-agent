"""
Staff takeover from the dashboard (plan 5.10): Take over, typed lines spoken,
Hand back to Emma, End call + task.
"""

import asyncio
import time
import unittest

import call_session
from test_realtime_support import engine, make_session, result, settle, wait_until


class Recorder:
    def __init__(self, reply="Sure."):
        self.calls = []
        self.reply = reply

    async def __call__(self, text, s, progress=None):
        self.calls.append(text)
        return result(self.reply)


class TakeoverTests(unittest.TestCase):
    def test_take_over_say_hand_back(self):
        fake = Recorder("Lovely, Monday at 5 it is.")

        async def run():
            s, t = make_session()
            s._last_reply = "What day suits you?"
            self.assertTrue(await s.take_over())
            await wait_until(lambda: any(c in call_session.TAKEOVER_LINES for c in t.emma_captions()))
            self.assertFalse(await s.take_over())                      # already taken over
            # The caller talks: captioned and recorded, but the engine doesn't answer.
            await s._start_turn("Can I come on Monday?", time.perf_counter())
            await settle(0.05)
            self.assertEqual(fake.calls, [])
            self.assertTrue(await s.operator_say("Yes, Monday at 5 is free."))
            await wait_until(lambda: "Yes, Monday at 5 is free." in t.emma_captions())
            await settle(0.05)
            roles = [(role, text) for role, text, _ in s.recorder.turns]
            self.assertIn(("caller", "Can I come on Monday?"), roles)
            self.assertIn(("operator", "Yes, Monday at 5 is free."), roles)
            self.assertTrue(await s.hand_back())
            await wait_until(lambda: any(c.startswith(tuple(call_session.HAND_BACK_LINES)) for c in t.emma_captions()))
            back = [c for c in t.emma_captions() if c.startswith(tuple(call_session.HAND_BACK_LINES))][0]
            self.assertTrue(back.endswith("What day suits you?"))        # Emma picks up where she was
            self.assertFalse(await s.operator_say("too late"))         # Emma has it again
            await s._start_turn("Thanks!", time.perf_counter())
            await wait_until(lambda: fake.calls == ["Thanks!"])
            await s.close()
            return fake.calls

        with engine(fake):
            self.assertEqual(asyncio.run(run()), ["Thanks!"])

    def end_by_staff(self, phone):
        made = []

        async def run():
            s, t = make_session()

            async def create_task(kind, priority, phone, note):
                made.append((kind, priority, phone, note))

            s._create_task = create_task
            s._caller_phone = lambda: phone
            self.assertTrue(await s.end_by_staff("Patient wants Dr Rao only"))
            await wait_until(lambda: s.closed, timeout=5)
            return s, t

        with engine(Recorder()):
            s, t = asyncio.run(run())
        self.assertEqual(s.outcome, "ended_by_staff")
        self.assertTrue(t.of_type("bye"))
        return made, t.emma_captions()

    def test_end_by_staff_says_goodbye_makes_a_task_and_ends(self):
        made, captions = self.end_by_staff("+919876543210")
        self.assertEqual(made, [("callback", "high", "+919876543210", "Patient wants Dr Rao only")])
        self.assertIn(call_session.STAFF_GOODBYE_LINES[0], captions)

    def test_end_by_staff_without_a_number_promises_no_call_back(self):
        # A talk-page call that never got as far as a number: a callback task nobody could act on,
        # and "someone will call you back", would both be wrong (APPLE_DESIGN_PLAN follow-ups, 22 Oct).
        made, captions = self.end_by_staff(None)
        self.assertEqual(made, [])
        self.assertIn(call_session.STAFF_GOODBYE_NO_NUMBER_LINES[0], captions)
        self.assertFalse(any("call you back" in c for c in captions))


class TypedCallEndTests(unittest.TestCase):
    """End call on a typed call: Emma says goodbye before the line closes (APPLE_DESIGN_PLAN follow-ups)."""

    def end(self, typed):
        async def run():
            s, t = make_session()
            await s.on_control({"type": "hello", "v": 2, "typed": typed})
            await s.on_control({"type": "end", "goodbye": True})
            await wait_until(lambda: s.closed, timeout=5)
            return s, t

        with engine(Recorder()):
            return asyncio.run(run())

    def test_a_typed_call_hears_goodbye_then_ends(self):
        s, t = self.end(typed=True)
        self.assertTrue(any(c in call_session.prompts.VARIANTS["close"] for c in t.emma_captions()))
        self.assertTrue(t.of_type("bye"))
        self.assertTrue(s.outcome.startswith("abandoned"))          # the caller ended it, as before

    def test_a_voice_call_ends_at_once(self):
        s, t = self.end(typed=False)
        self.assertFalse(any(c in call_session.prompts.VARIANTS["close"] for c in t.emma_captions()))


if __name__ == "__main__":
    unittest.main()
