"""
North Star guards (docs/NORTH_STAR.md): nothing Emma can say may sound like a
bot, a disclaimer, a form, or a reflexive handoff; greetings are short and
rotate; the honesty line is truthful and used only for that purpose.
"""

import ast
import re
import unittest
from pathlib import Path

import config
import phrases

ROOT = Path(__file__).resolve().parent.parent

BANNED = [
    r"automated", r"virtual assistant", r"\bai assistant", r"\bas an ai\b", r"language model", r"chat ?bot",
    r"\bbot\b", r"\brobot", r"real person", r"human being", r"transfer you", r"connect you",
    r"booking assistant", r"assist only", r"currently has one location", r"\bname:", r"phone number:",
    r"successfully confirmed", r"please say yes to confirm", r"thank you for choosing", r"at the moment, i can",
    r"virtual receptionist",          # allowed only in config.HONEST_LINE
]


def _spoken_strings(path: Path) -> list[str]:
    """String literals from `path` that can reach the caller: not docstrings, log calls or LLM instructions."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                skip.add(id(first.value))
        is_log = (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and isinstance(node.func.value, ast.Name) and node.func.value.id in ("logger", "logging"))
        # Text built for the LLM (its instructions and state description) is never spoken.
        is_prompt = (isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and re.search(r"_PROMPT$|^prompt$|_desc$|^context_section$", t.id)
            for t in node.targets))
        if is_log or is_prompt:
            skip.update(id(n) for n in ast.walk(node))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            out.append(node.value)
    return out


class NoRoboticSpeechTests(unittest.TestCase):
    def check(self, text: str, where: str):
        for pattern in BANNED:
            self.assertIsNone(re.search(pattern, text, re.IGNORECASE), f"{pattern!r} in {where}: {text!r}")

    def test_fixed_phrases_and_greetings(self):
        for text in phrases.all_phrases():
            if text != config.HONEST_LINE:
                self.check(text, "phrases")

    def test_every_spoken_string_in_the_dialogue(self):
        for module in ("ai_engine.py", "phrases.py"):
            for text in _spoken_strings(ROOT / module):
                self.check(text, module)

    def test_the_scanner_would_catch_a_regression(self):
        with self.assertRaises(AssertionError):
            self.check("Pearl Dental, this is Emma, the clinic's automated assistant.", "sample")
        with self.assertRaises(AssertionError):
            self.check("Name: Priya. Phone Number: 9 8 7.", "sample")


class GreetingTests(unittest.TestCase):
    def test_greetings_are_short_and_never_repeat_back_to_back(self):
        for g in config.GREETINGS:
            self.assertLessEqual(len(g.split()), 12, g)
        seen = [phrases.next_greeting() for _ in range(40)]
        self.assertTrue(all(a != b for a, b in zip(seen, seen[1:])))
        self.assertGreater(len(set(seen)), 2)

    def test_first_turn_is_a_rotated_natural_greeting(self):
        import asyncio
        import ai_engine
        result = asyncio.run(ai_engine.async_process_turn("", ai_engine.SessionState()))
        self.assertIn(result.text, config.GREETINGS)

    def test_honesty_line_is_truthful(self):
        # Emma never claims to be human: when sincerely asked, she says so plainly.
        self.assertIn("virtual receptionist", config.HONEST_LINE)
        self.assertNotRegex(config.HONEST_LINE.lower(), r"\b(human|real person|not a bot)\b")


class SoundRealismTests(unittest.TestCase):
    def test_client_config_exposes_phone_line_and_ambience(self):
        from starlette.testclient import TestClient
        import server
        body = TestClient(server.app).get("/client-config").json()
        self.assertEqual(set(body), {"phone_line", "ambience", "typing"})
        self.assertLess(body["ambience"]["murmur_db"], -30)        # subtle, never loud

    def test_checking_pauses_with_typing_before_the_answer(self):
        import asyncio
        import phrases
        from call_session import CallSession, Services

        class Cache:
            def get(self, text):
                return bytes(32 * 1400) if text == "Let me just check that for you." else None

        class Transport:
            def __init__(self):
                self.events, self.audio = [], 0
            async def send_audio(self, _tid, pcm): self.audio += len(pcm)
            async def send_event(self, event): self.events.append(event)
            async def flush(self, *_): pass
            async def close(self): pass

        async def run():
            t = Transport()
            session = CallSession(t, Services(stt_factory=lambda **_: None, cache=Cache()))
            session.turn_id = 1
            await session._checking_pause(1, session._timer(1, ""))
            return t

        t = asyncio.run(run())
        sfx = [e for e in t.events if e.get("type") == "sfx"]
        self.assertEqual(len(sfx), 1)
        self.assertEqual((sfx[0]["name"], sfx[0]["after_ms"]), ("typing", 1400 + 150))
        # The phrase (1.4 s) plus a 0.9-1.6 s pause were queued, in order, on the same turn.
        self.assertGreaterEqual(t.audio, 32 * (1400 + 900))
        self.assertLessEqual(t.audio, 32 * (1400 + 1600))
        self.assertEqual(phrases.CHECKING, "Let me just check that for you.")

    def test_typing_follows_information_not_yes_no_or_questions(self):
        import ai_engine
        s = ai_engine.SessionState()
        self.assertTrue(ai_engine.expects_information(s, "I'd like to book a cleaning next Monday"))
        self.assertFalse(ai_engine.expects_information(s, "hello"))
        s.step = 2
        self.assertTrue(ai_engine.expects_information(s, "my name is Priya Sharma"))
        s.temp_name = "Priya Sharma"                       # now Emma asked "did I get that right?"
        self.assertFalse(ai_engine.expects_information(s, "yes"))
        s.step, s.temp_phone = 4, ""
        self.assertTrue(ai_engine.expects_information(s, "nine eight seven six five, four three two one zero"))
        self.assertFalse(ai_engine.expects_information(s, "what are your timings?"))
        s.step = 9
        self.assertFalse(ai_engine.expects_information(s, "yes please"))
        self.assertTrue(ai_engine.expects_information(s, "no, the number is 9123456789"))

    def _run_turn(self, step, text, **state):
        """Run one CallSession turn with an instant engine; return (sfx events, seconds until Emma spoke)."""
        import asyncio
        import time
        import ai_engine
        from call_session import CallSession, Services

        class Transport:
            def __init__(self):
                self.events = []
            async def send_audio(self, *_): pass
            async def send_event(self, event): self.events.append(event)
            async def flush(self, *_): pass
            async def close(self): pass

        async def instant(_text, s, progress=None):
            return ai_engine.TurnResult("Okay.", tier=0)

        async def run():
            t = Transport()
            session = CallSession(t, Services(stt_factory=lambda **_: None))
            session.s.step = step
            for key, value in state.items():
                setattr(session.s, key, value)
            spoke = []

            async def fake_speak(tid, reply, timer):
                spoke.append(time.perf_counter())

            session._speak = fake_speak
            session.turn_id = 1
            started = time.perf_counter()
            original = ai_engine.async_process_turn
            ai_engine.async_process_turn = instant
            try:
                await session._run_turn(1, text, session._timer(1, text), None)
            finally:
                ai_engine.async_process_turn = original
            return [e for e in t.events if e.get("type") == "sfx"], spoke[0] - started

        return asyncio.run(run())

    def test_a_given_phone_number_gets_a_typing_beat_before_the_reply(self):
        sfx, waited = self._run_turn(4, "9876543210", temp_phone="")
        self.assertEqual(len(sfx), 1)
        self.assertEqual((sfx[0]["name"], sfx[0]["until_speech"], sfx[0]["turn"]), ("typing", True, 1))
        self.assertGreaterEqual(waited, 0.6)               # TYPING_BEAT_MS minimum is 650 ms
        self.assertLess(waited, 1.3)

    def test_a_plain_yes_is_answered_straight_away(self):
        sfx, waited = self._run_turn(6, "yes")
        self.assertEqual(sfx, [])
        self.assertLess(waited, 0.3)

    def test_breath_only_before_long_sentences_and_not_every_turn(self):
        from speech import Speaker, BREATHS
        sp = Speaker(transport=None, cache=None)
        long = " ".join(["word"] * 25)
        self.assertFalse(sp._wants_breath(1, "Short and sweet."))
        self.assertTrue(sp._wants_breath(1, long))
        self.assertFalse(sp._wants_breath(2, long))        # never two turns running
        self.assertTrue(sp._wants_breath(3, long))
        self.assertTrue(all(0.25 < len(b) / 32000 < 0.4 for b in BREATHS))


if __name__ == "__main__":
    unittest.main()
