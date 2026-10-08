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


# The R2 lines (prompts.VARIANTS) are scanned as rendered wording, never the
# LineSpec.desc notes. On top of BANNED: anything that sounds like a product,
# a handoff, or the doctor as an escape route (feedback 3, M4, Z3).
R2_BANNED = BANNED + [
    r"\bai\b", r"\bvirtual\b", r"\bassistant\b", r"\bsystem\b", r"\btransfer", r"put you through",
    r"\bhuman\b", r"the doctor will go through", r"go through (that|it|this) with you", r"at your (visit|appointment)",
    r"please hold", r"your call is important", r"i am unable", r"\bkindly\b", r"\bsir\b", r"\bma'?am\b",
    r"\bdear\b", r"as per", r"\bplease confirm\b", r"\bprovide (me with )?your\b",
]


def _r2_lines():
    """(line id, rendered-shape text) for every variant, with the honesty notice set aside."""
    import prompts
    for line_id, variants in prompts.VARIANTS.items():
        for v in variants:
            if line_id == "honesty":
                continue
            yield line_id, v


def _brief_examples() -> list:
    """Emma's side of the model's style examples (dialogue/brief.py), if that module has them yet."""
    try:
        from dialogue import brief
    except Exception:                                     # another track's work in progress
        return []
    text = getattr(brief, "EXAMPLES_PROMPT", "") or ""
    return [m.group(2) for m in re.finditer(r'"(say|ask)":\s*"((?:[^"\\]|\\.)*)"', text) if m.group(2)]


# The "transfer" line (phone calls only: said after the caller insisted on a
# person twice and the callback task exists; pre-written, never the model's
# words) is the one place Emma may say she is putting someone through.
_TRANSFER_WORDS = {r"\btransfer", r"put you through", r"transfer you", r"connect you"}


class R2WordingTests(unittest.TestCase):
    def check(self, text: str, where: str, patterns=R2_BANNED):
        for pattern in patterns:
            self.assertIsNone(re.search(pattern, text, re.IGNORECASE), f"{pattern!r} in {where}: {text!r}")

    def test_every_variant_sounds_like_a_receptionist(self):
        for line_id, text in _r2_lines():
            if line_id == "clinical":                     # the one line allowed to mention the doctor's visit
                self.check(text, line_id, [p for p in R2_BANNED if "visit" not in p])
            elif line_id == "transfer":                   # the one line allowed to put a caller through
                self.check(text, line_id, [p for p in R2_BANNED if p not in _TRANSFER_WORDS])
            else:
                self.check(text, line_id)

    def test_variants_pass_the_reply_validators_wording_rules(self):
        # The model's words are held to validate.py's V2 / V6 lists; the
        # pre-written lines must clear the same bar (deflection only for
        # "clinical"; callback wording only once the task exists).
        try:
            from dialogue import validate
        except Exception as exc:                          # pragma: no cover - E2's module mid-build
            self.skipTest(f"dialogue.validate not importable: {exc}")
        for line_id, text in _r2_lines():
            patterns = list(validate.BOT_WORDS) + list(validate.HANDOFF_WORDS) + list(validate.MEDICAL)
            if line_id == "transfer":
                patterns = [p for p in patterns if p not in _TRANSFER_WORDS]
            if line_id != "clinical":
                patterns += list(validate.DEFLECTION)
            self.check(text, line_id, patterns)

    def test_virtual_receptionist_only_in_the_honesty_line(self):
        import prompts
        self.assertEqual(prompts.VARIANTS["honesty"], (config.HONEST_LINE,))
        for line_id, text in _r2_lines():
            self.assertNotIn("virtual", text.lower(), line_id)

    def test_one_question_at_most_and_short(self):
        import speech
        for line_id, text in _r2_lines():
            self.assertLessEqual(text.count("?"), 1, f"{line_id}: {text!r}")
            self.assertLessEqual(len(text.split()), 40, f"{line_id}: {text!r}")
            for sentence in speech.split_sentences(text):
                self.assertLessEqual(len(sentence.split()), 25, f"{line_id}: {sentence!r}")

    def test_no_form_style_recaps(self):
        for line_id, text in _r2_lines():
            self.assertIsNone(re.search(r"\b[A-Z][a-z]+( [A-Z][a-z]+)?:\s", text), f"{line_id}: {text!r}")

    def test_the_capability_line_matches_the_owners_example(self):
        # SUCCESS_CRITERIA criterion 4: clinic, services, doctors, prices and
        # timings, and book / change / cancel; then an open question.
        import prompts
        for text in prompts.VARIANTS["capability"]:
            low = text.lower()
            for word in ("clinic", "services", "doctors", "prices", "timings", "book", "change", "cancel"):
                self.assertIn(word, low, text)
            self.assertTrue(text.endswith("?"), text)
            self.assertNotIn("book an appointment?", low)          # never a booking push

    def test_knowledge_base_has_no_deflection_or_bot_wording(self):
        import facts
        kb = facts.load_knowledge()
        for fact in kb.facts:
            self.check(fact.text, fact.id)
        self.check(kb.unknown_line, "unknown_line")

    def test_the_models_style_examples_follow_the_same_rules(self):
        examples = _brief_examples()
        if not examples:
            self.skipTest("dialogue/brief.py has no EXAMPLES_PROMPT yet")
        for text in examples:
            self.check(text, "brief example")
            self.assertLessEqual(text.count("?"), 1, text)

    def test_the_r2_scanner_would_catch_a_regression(self):
        with self.assertRaises(AssertionError):
            self.check("The doctor will go through that with you.", "sample")
        with self.assertRaises(AssertionError):
            self.check("Let me transfer you to a real person.", "sample")
        with self.assertRaises(AssertionError):
            self.check("I'm an AI, I can't do that.", "sample")


class GreetingTests(unittest.TestCase):
    def test_greetings_are_short_and_never_repeat_back_to_back(self):
        for g in phrases.all_greetings():
            self.assertLessEqual(len(g.split()), 12, g)
            self.assertIn("Emma", g)                       # owner, 6 Oct: she always says her name
            self.assertIn("Pearl Dental", g)
            self.assertEqual(g.count("?"), 1, g)           # one question, not a menu
        seen = [phrases.next_greeting() for _ in range(40)]
        self.assertTrue(all(a != b for a, b in zip(seen, seen[1:])))
        self.assertGreater(len(set(seen)), 2)

    def test_first_turn_is_a_rotated_natural_greeting(self):
        import asyncio
        import ai_engine
        result = asyncio.run(ai_engine.async_process_turn("", ai_engine.SessionState()))
        self.assertIn(result.text, phrases.all_greetings())

    def test_first_turn_on_the_r2_engine_is_the_same_rotated_greeting(self):
        import asyncio
        import ai_engine
        from dialogue.context import new_context
        result = asyncio.run(ai_engine.async_process_turn("", new_context("greet")))
        self.assertIn(result.text, phrases.all_greetings())
        self.assertEqual(result.tier, -1)

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
        self.assertLess(body["ambience"]["event_db"], -30)         # subtle, never loud
        self.assertNotIn("murmur_db", body["ambience"])            # no background bed (owner, 1 Oct)

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
            session.s = ai_engine.SessionState()     # the 12-step machine's steps, whichever engine is on
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
        # The beat overlaps the engine's work: an instant engine waits out the
        # short beat (config.TYPING_BEAT_MS, as the realtime track set it), and no more.
        low, high = config.TYPING_BEAT_MS
        self.assertGreaterEqual(waited, low / 1000 - 0.02)
        self.assertLess(waited, high / 1000 + 0.2)

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
