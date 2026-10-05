"""
Live call controls (plan 5.10, Day 4.2): staff take a call over from the
dashboard, speak typed lines in Emma's voice, hand it back, or end it with a
follow-up task. While staff have the call, Emma's engine and silence ladder
stand down but the caller is still transcribed.
"""

import asyncio
import time
import unittest

import call_session
import server
from test_dashboard import DashboardTestCase
from test_realtime_support import FakeTTS, engine, make_session, patched, result, settle, wait_until


def run(coro):
    return asyncio.run(coro)


class TakeoverSessionTests(unittest.TestCase):
    def test_staff_take_over_speak_and_hand_back(self):
        calls = []

        async def fake_engine(text, s, *args, **kwargs):
            calls.append(text)
            return result("Sure, which day suits you?")

        async def scenario():
            s, t = make_session()
            self.assertFalse(await s.say_for_staff("Too early"))         # only once staff have the call
            self.assertTrue(await s.take_over())
            self.assertTrue(s.staffed)
            await wait_until(lambda: any(c in call_session.TAKEOVER_LINES for c in t.emma_captions()))
            await s._start_turn("Is the doctor in today?", time.perf_counter())
            await settle(0.05)
            self.assertTrue(await s.say_for_staff("  Yes, Dr Rao is in   until six.  "))
            await wait_until(lambda: "Yes, Dr Rao is in until six." in t.emma_captions())
            operator = [e for e in t.of_type("caption") if e.get("by") == "operator"]
            self.assertTrue(await s.hand_back())
            await wait_until(lambda: any(c in call_session.HAND_BACK_LINES for c in t.emma_captions()))
            await s._start_turn("I'd like to book", time.perf_counter())
            await wait_until(lambda: calls)
            await s.close()
            return s, t, operator

        with engine(fake_engine):
            s, t, operator = run(scenario())
        self.assertEqual(calls, ["I'd like to book"])                  # the staffed turn never reached the engine
        self.assertEqual([e["text"] for e in operator], ["Yes, Dr Rao is in until six."])
        roles = [(role, text) for role, text, _ in s.recorder.turns]
        self.assertIn(("caller", "Is the doctor in today?"), roles)
        self.assertIn(("operator", "Yes, Dr Rao is in until six."), roles)
        staffed = [meta for role, text, meta in s.recorder.turns if text == "Is the doctor in today?"]
        self.assertTrue(staffed[0].get("staffed"))

    def test_staff_lines_play_in_the_order_typed_one_at_a_time(self):
        async def scenario():
            tts = FakeTTS(ms=120, delay=0.03)
            s, t = make_session(tts=tts)
            await s.take_over()
            for line in ("One.", "Two.", "Three."):
                await s.say_for_staff(line)
            await wait_until(lambda: "Three." in t.emma_captions())
            await s.close()
            return [c for c in t.emma_captions() if c in ("One.", "Two.", "Three.")], tts.opened

        captions, opened = run(scenario())
        self.assertEqual(captions, ["One.", "Two.", "Three."])
        texts = [text for text, _ in opened]
        self.assertEqual(texts[-3:], ["One.", "Two.", "Three."])
        starts = [at for _, at in opened[-3:]]
        self.assertTrue(all(b - a >= 0.02 for a, b in zip(starts, starts[1:])), starts)   # never all at once

    def test_the_silence_ladder_waits_while_staff_have_the_call(self):
        async def scenario():
            s, t = make_session()
            await s.take_over()
            await wait_until(lambda: t.emma_captions())
            watcher = asyncio.ensure_future(s._watch())
            await asyncio.sleep(0.5)
            during = [c for c in t.emma_captions() if c not in call_session.TAKEOVER_LINES]
            watcher.cancel()
            await s.close()
            return during

        with patched(call_session, SILENCE_STEP_S=0.1, WATCH_INTERVAL_S=0.02):
            self.assertEqual(run(scenario()), [])

    def test_ending_creates_a_task_says_goodbye_and_hangs_up(self):
        made = []

        async def scenario():
            s, t = make_session()
            s.s.temp_phone = "9876543210"

            async def create_task(kind, priority, phone, note):
                made.append((kind, priority, phone, note))

            s._create_task = create_task
            self.assertTrue(await s.end_for_staff("  wants the clinic manager "))
            await wait_until(lambda: s.closed)
            self.assertFalse(await s.end_for_staff("twice"))
            return t, s

        t, s = run(scenario())
        self.assertIn(t.emma_captions()[-1], call_session.STAFF_END_LINES)
        self.assertEqual((s.outcome, t.closed), ("ended_by_staff", True))
        self.assertEqual(made, [("escalation", "high", "+919876543210",
                                 "Call ended by staff from the dashboard. wants the clinic manager")])

    def test_staff_lines_sound_like_the_clinic(self):
        banned = ["automated", "virtual", "system", "please hold", "connect you", "transfer", "real person",
                  "error", "technical", "bot"]
        for line in call_session.STAFF_LINES:
            with self.subTest(line=line):
                self.assertFalse(any(word in line.lower() for word in banned))
                self.assertLessEqual(len(line.split()), 24)


class FakeLiveSession:
    """What the dashboard routes need from a CallSession."""

    def __init__(self):
        self.closed = False
        self.staffed = False
        self.said, self.ended = [], None

    async def take_over(self):
        self.staffed = True
        return True

    async def say_for_staff(self, text):
        if not self.staffed:
            return False
        self.said.append(text)
        return True

    async def hand_back(self):
        if not self.staffed:
            return False
        self.staffed = False
        return True

    async def end_for_staff(self, note=""):
        self.ended = note
        return True


class LiveControlApiTests(DashboardTestCase):
    def setUp(self):
        super().setUp()
        self.session = FakeLiveSession()
        server.app.state.gate.try_acquire("inbound", "c1")
        server.app.state.sessions = {"c1": self.session}

    def tearDown(self):
        server.app.state.sessions = {}
        super().tearDown()

    def post(self, path, body=None):
        return self.client.post(f"/dashboard/api/live/c1/{path}", json=body or {})

    def audit(self):
        return [r["action"] for r in self.rows("SELECT action FROM audit_events WHERE entity = 'call' ORDER BY id")]

    def test_every_control_needs_the_login(self):
        for path in ("takeover", "say", "handback", "end"):
            with self.subTest(path=path):
                self.assertEqual(self.post(path, {"text": "hi"}).status_code, 401)
        self.assertFalse(self.session.staffed)

    def test_take_over_speak_hand_back_and_end(self):
        self.login()
        self.assertFalse(self.client.get("/dashboard/api/overview").json()["call"]["staffed"])
        self.assertEqual(self.post("say", {"text": "Hello"}).status_code, 409)     # not taken over yet
        response = self.post("takeover")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["call"]["staffed"])
        self.assertEqual(self.post("say", {"text": "  "}).status_code, 400)
        self.assertEqual(self.post("say", {"text": "Dr Rao is in until six."}).status_code, 200)
        self.assertEqual(self.session.said, ["Dr Rao is in until six."])
        self.assertFalse(self.post("handback").json()["call"]["staffed"])
        self.assertEqual(self.post("end", {"note": "manager to call"}).status_code, 200)
        self.assertEqual(self.session.ended, "manager to call")
        self.assertEqual(self.audit(), ["call_takeover", "call_operator_line", "call_hand_back", "call_end_by_staff"])
        # The audit keeps that staff spoke, never the words.
        detail = self.rows("SELECT after_json FROM audit_events WHERE action = 'call_operator_line'")[0]
        self.assertNotIn("Rao", detail["after_json"])

    def test_staff_mode_reaches_the_live_panel(self):
        self.login()
        self.post("takeover")
        staff = [e for e in __import__("events").recent("c1") if e["type"] == "staff"]
        self.assertEqual([e["mode"] for e in staff], ["staff"])

    def test_an_ended_call_cannot_be_controlled(self):
        self.login()
        self.session.closed = True
        self.assertEqual(self.post("takeover").status_code, 404)
        self.assertEqual(self.client.post("/dashboard/api/live/nope/takeover", json={}).status_code, 404)

    def test_cross_site_control_is_refused(self):
        self.login()
        response = self.client.post("/dashboard/api/live/c1/takeover", json={},
                                    headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.session.staffed)


if __name__ == "__main__":
    unittest.main()
