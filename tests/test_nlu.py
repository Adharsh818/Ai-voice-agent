"""
The one streamed model call per LLM turn (docs/R2_DESIGN.md, sections 6, 13
and 14): from_json, the incremental StreamParser, NLUStream deadlines and
cancellation, the harness ReaderBackend, and llm.GeminiNLU's streamed
request with its one-retry-before-the-first-token rule. No network: Gemini
is a fake client.
"""

import asyncio
import json
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import llm
import nlu
from dialogue.context import Emergency, Goal, Intent
from speech import split_sentences

SCHEMA = nlu.build_schema(("Teeth Cleaning", "Root Canal Treatment"), ("Indiranagar", "Whitefield"),
                          ("Dr Rao", "Dr Shetty"))
OBJ = {
    "acts": ["answer", "question"], "intent": "book", "name": "Priya \"P\" Rao", "phone_digits": "98765 43210",
    "service": "Teeth Cleaning", "next_goal": "ask_phone",
    "say": "Sure, Dr. Rao is in on Monday. A cleaning is ₹400 — nice! Okay.",
    "ask": "And what's the best number for you?",
}


def run(coro):
    return asyncio.run(coro)


def brief_for(words: str, expect: str = "open", goal: str = "ask_intent"):
    contents = f"{nlu.EXPECT_PREFIX}{expect}\n{nlu.GOAL_PREFIX}{goal}\n\nTHE CALLER JUST SAID:\n<<<{words}>>>"
    return SimpleNamespace(system="system", contents=contents, schema=SCHEMA)


async def consume(stream):
    head = await stream.head()
    says = [s async for s in stream.sentences("say")]
    ask = await stream.text("ask")
    await stream.aclose()
    return head, says, ask


class FromJsonTests(unittest.TestCase):
    def test_types_enums_and_unknown_keys(self):
        u = nlu.from_json({
            "acts": ["answer", "dance", "answer"], "intent": "none", "emergency": "huge", "confirmation": "YES",
            "correction": "true", "age": True, "choice_index": 42, "date_iso_hint": "2026-13-40",
            "phone_digits": "(789) 937-7462", "name_spelled": "a-d-h-a-r-s-h", "doctor_gender": "lady",
            "next_goal": "teleport", "say": 5, "ask": "null", "hallucinated_key": "boo", "service": "Teeth Cleaning",
        }, raw_text="words", schema=SCHEMA)
        self.assertEqual(u.acts, ["answer"])
        self.assertIsNone(u.intent)                         # "none" = no intent expressed
        self.assertEqual(u.emergency, Emergency.NONE)
        self.assertEqual(u.confirmation, "yes")
        self.assertTrue(u.correction)
        self.assertIsNone(u.age)
        self.assertIsNone(u.choice_index)
        self.assertIsNone(u.date_iso_hint)
        self.assertEqual(u.phone_digits, "7899377462")
        self.assertEqual(u.name_spelled, "ADHARSH")
        self.assertIsNone(u.doctor_gender)
        self.assertIsNone(u.next_goal)
        self.assertEqual((u.say, u.ask), ("", ""))
        self.assertFalse(hasattr(u, "hallucinated_key"))
        self.assertEqual(u.service, "Teeth Cleaning")
        self.assertEqual((u.source, u.raw_text), ("llm", "words"))

    def test_a_hallucinated_doctor_is_dropped(self):
        # Z4: not in the enum -> never a catalog doctor; the name survives as the caller's phrase.
        u = nlu.from_json({"acts": ["info"], "intent": "book", "doctor": "Dr Sharma", "branch": "Koramangala",
                           "service": "Whitening"}, schema=SCHEMA)
        self.assertIsNone(u.doctor)
        self.assertEqual(u.doctor_phrase, "Dr Sharma")
        self.assertIsNone(u.branch)
        self.assertIsNone(u.service)
        self.assertEqual(u.service_phrase, "Whitening")

    def test_catalog_names_are_canonicalised(self):
        u = nlu.from_json({"acts": ["info"], "doctor": "dr. rao", "branch": "WHITEFIELD"}, schema=SCHEMA)
        self.assertEqual((u.doctor, u.branch), ("Dr Rao", "Whitefield"))

    def test_phone_digit_words(self):
        self.assertEqual(nlu.digits_only("nine eight double four five, 0 1 2 3 4"), "9844501234")
        self.assertEqual(nlu.digits_only("98450 12345"), "9845012345")
        self.assertIsNone(nlu.digits_only("none"))

    def test_intent_and_goal_values(self):
        u = nlu.from_json({"acts": ["info"], "intent": "Cancel", "next_goal": "ask_phone", "emergency": "urgent"})
        self.assertEqual((u.intent, u.next_goal, u.emergency), (Intent.CANCEL, Goal.ASK_PHONE, Emergency.URGENT))


def ordered_text(obj, ascii_only=False) -> str:
    return json.dumps({k: obj[k] for k in nlu.KEY_ORDER if k in obj}, ensure_ascii=ascii_only)


def parse_in_chunks(text: str, size: int):
    parser, sentences = nlu.StreamParser(), []
    for i in range(0, len(text), size):
        parser.feed(text[i:i + size])
        sentences += parser.pop_sentences("say")
    parser.finish()
    sentences += parser.pop_sentences("say")
    return parser, sentences


class StreamParserTests(unittest.TestCase):
    def test_chunk_size_never_changes_the_result(self):
        for ascii_only in (False, True):                    # \u escapes everywhere in the second pass
            text = ordered_text(OBJ, ascii_only)
            results = set()
            for size in range(1, 14):
                parser, sentences = parse_in_chunks(text, size)
                results.add((json.dumps(parser.head, sort_keys=True), tuple(sentences), parser.value("ask")))
            self.assertEqual(len(results), 1, results)
            head, sentences, ask = json.loads(next(iter(results))[0]), next(iter(results))[1], next(iter(results))[2]
            self.assertNotIn("say", head)
            self.assertEqual(head["name"], 'Priya "P" Rao')
            self.assertEqual(list(sentences), split_sentences(OBJ["say"]))
            self.assertEqual(ask, OBJ["ask"])

    def test_head_is_ready_when_say_starts(self):
        parser = nlu.StreamParser()
        text = ordered_text(OBJ)
        cut = text.index('"say"') + len('"say"')
        parser.feed(text[:cut - 1])
        self.assertIsNone(parser.head)
        parser.feed(text[cut - 1:cut])
        self.assertEqual(parser.head["intent"], "book")

    def test_split_escapes(self):
        parser = nlu.StreamParser()
        for chunk in ('{"acts": ["answer"], "say": "Price \\', 'u20', 'b9400 and \\', '"ok', '\\" \\ud83d', '\\ude00."}'):
            parser.feed(chunk)
        self.assertEqual(parser.value("say"), 'Price ₹400 and "ok" \U0001F600.')
        self.assertTrue(parser.closed("say"))

    def test_dr_is_not_a_sentence_end(self):
        parser = nlu.StreamParser()
        parser.feed('{"acts": [], "say": "Dr. Rao is in. ')
        self.assertEqual(parser.pop_sentences("say"), [])       # not final until the next sentence starts
        parser.feed('See you')
        self.assertEqual(parser.pop_sentences("say"), ["Dr. Rao is in."])
        parser.feed(' soon."}')
        self.assertEqual(parser.pop_sentences("say"), ["See you soon."])

    def test_closing_quote_ends_the_last_sentence(self):
        parser = nlu.StreamParser()
        parser.feed('{"acts": [], "say": "Sure, the 2nd')
        self.assertEqual(parser.pop_sentences("say"), [])
        parser.feed('."')
        self.assertEqual(parser.pop_sentences("say"), ["Sure, the 2nd."])

    def test_finish_is_tolerant(self):
        parser = nlu.StreamParser()
        parser.feed('Here you go:\n```json\n' + ordered_text(OBJ) + '\n```\nHope that helps!')
        self.assertEqual(parser.finish()["intent"], "book")
        self.assertEqual(parser.head["intent"], "book")

    def test_reply_first_means_head_waits_for_finish(self):
        parser = nlu.StreamParser()
        parser.feed('{"say": "Sure.", "ask": "", "acts": ["answer"], "intent": "book"}')
        self.assertIsNone(parser.head)                      # the model ignored the key order
        parser.finish()
        self.assertEqual(parser.head, {"acts": ["answer"], "intent": "book"})

    def test_cut_off_stream_keeps_the_head_not_the_half_reply(self):
        parser = nlu.StreamParser()
        parser.feed('{"acts": ["answer"], "intent": "book", "say": "Sure, Mon')
        self.assertEqual(parser.finish()["intent"], "book")
        self.assertFalse(parser.closed("say"))
        self.assertEqual(parser.pop_sentences("say"), [])

    def test_garbage(self):
        parser = nlu.StreamParser()
        parser.feed("I'm sorry, I can't help with that.")
        self.assertEqual(parser.finish(), {})
        self.assertIsNone(parser.head)


class NLUStreamTests(unittest.TestCase):
    def test_fake_nlu_end_to_end_at_any_chunk_size(self):
        results = set()
        for size in range(1, 14):
            fake = nlu.FakeNLU({"book me in": dict(OBJ)}, chunk_size=size)
            with nlu.use_backend(fake):
                head, says, ask = run(self._go(brief_for("Book me in")))
            results.add((head.intent, head.phone_digits, head.say, tuple(says), ask))
        self.assertEqual(results, {(Intent.BOOK, "9876543210", "", tuple(split_sentences(OBJ["say"])), OBJ["ask"])})

    async def _go(self, brief):
        stream = await nlu.understand_stream(brief)
        return await consume(stream)

    def test_unusable_backend_gives_no_head_at_once(self):
        fake = nlu.FakeNLU({"hi": dict(OBJ)}, usable=False)
        with nlu.use_backend(fake):
            started = time.monotonic()
            head, says, ask = run(self._go(brief_for("hi")))
        self.assertIsNone(head)
        self.assertEqual((says, ask), ([], ""))
        self.assertLess(time.monotonic() - started, 0.1)
        self.assertEqual(fake.calls, [])                    # no request at all

    def test_model_down_for_a_turn(self):
        with nlu.use_backend(nlu.FakeNLU({})):
            head, says, ask = run(self._go(brief_for("anything")))
        self.assertIsNone(head)

    def test_head_deadline(self):
        # T2 / degradation: a model that hasn't produced the head in time costs no more than the deadline.
        fake = nlu.FakeNLU({"hi": dict(OBJ)}, chunk_size=4, delay_s=0.05)
        with nlu.use_backend(fake), mock.patch.object(nlu, "HEAD_DEADLINE_S", 0.3):
            started = time.monotonic()
            head = run(self._head_only(brief_for("hi")))
            elapsed = time.monotonic() - started
        self.assertIsNone(head)
        self.assertLess(elapsed, 0.3 + 0.2)

    async def _head_only(self, brief):
        stream = await nlu.understand_stream(brief)
        head = await stream.head()
        self.assertTrue(stream.ended)                       # the request was closed
        return head

    def test_default_head_deadline_is_the_design_budget(self):
        self.assertEqual(nlu.HEAD_DEADLINE_S, 1.6)

    def test_stalled_reply_falls_back_at_the_total_deadline(self):
        class Stall:
            usable = True

            async def stream(self, system, contents, schema, deadline):
                yield '{"acts": ["answer"], "intent": "book", "say": "Sure, Monday works. And'
                await asyncio.sleep(5)
                yield ' more."}'

        with nlu.use_backend(Stall()), mock.patch("config.GEMINI_TIMEOUT", 0.4):
            started = time.monotonic()
            head, says, ask = run(self._go(brief_for("x")))
            elapsed = time.monotonic() - started
        self.assertEqual(head.intent, Intent.BOOK)
        self.assertEqual(says, ["Sure, Monday works."])     # only what was final
        self.assertEqual(ask, "")
        self.assertLess(elapsed, 0.9)

    def test_aclose_cancels_the_request(self):
        state = {}

        class Slow:
            usable = True

            async def stream(self, system, contents, schema, deadline):
                state["deadline_left"] = deadline - time.monotonic()
                try:
                    yield '{"acts": ["answer"], "intent": "none", "say": "'
                    await asyncio.sleep(5)
                    yield 'x"}'
                finally:
                    state["closed"] = True

        async def go():
            stream = await nlu.understand_stream(brief_for("x"))
            head = await stream.head()
            await stream.aclose()
            return head

        with nlu.use_backend(Slow()):
            started = time.monotonic()
            head = run(go())
        self.assertIsNotNone(head)
        self.assertTrue(state.get("closed"))
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertLessEqual(state["deadline_left"], 2.6)   # the backend sees the absolute turn deadline

    def test_a_backend_error_is_a_fallback_not_a_crash(self):
        class Broken:
            usable = True

            async def stream(self, system, contents, schema, deadline):
                raise RuntimeError("boom")
                yield ""                                    # pragma: no cover

        with nlu.use_backend(Broken()), mock.patch.object(nlu.logger, "warning") as warned:
            head, says, ask = run(self._go(brief_for("x")))
        warned.assert_called_once()
        self.assertIsNone(head)


class ReaderBackendTests(unittest.TestCase):
    def test_a_phone_reading_becomes_digits(self):
        seen = {}

        def reader(text, expect):
            seen["args"] = (text, expect)
            return {"text": text, "phone": "98765 43210", "phone_valid": True, "expect": expect}

        backend = nlu.ReaderBackend(reader, chunk_size=5)

        async def go():
            with nlu.use_backend(backend):
                stream = await nlu.understand_stream(brief_for("my number is 98765 43210", "phone", "ask_phone"))
                return await consume(stream)

        head, says, ask = run(go())
        self.assertEqual(seen["args"], ("my number is 98765 43210", "phone"))
        self.assertEqual(head.phone_digits, "9876543210")
        self.assertEqual(head.acts, ["answer"])
        self.assertEqual(head.next_goal, Goal.ASK_PHONE)
        self.assertEqual((says, ask), ([], ""))

    def test_the_real_harness_reader(self):
        try:
            from harness import fake_nlu
        except Exception as exc:                            # the test track's module, mid-build
            self.skipTest(f"harness.fake_nlu unavailable: {exc}")
        reading = fake_nlu.read("my number is 98765 43210", expect="phone").as_dict()
        obj = nlu.reading_to_object(reading, "ask_phone")
        self.assertEqual(obj["phone_digits"], "9876543210")

    def test_a_reader_returning_none_is_the_model_down(self):
        async def go():
            with nlu.use_backend(nlu.ReaderBackend(lambda text, expect: None)):
                stream = await nlu.understand_stream(brief_for("hello"))
                return await stream.head()
        self.assertIsNone(run(go()))

    def test_mapping(self):
        r2o = nlu.reading_to_object
        self.assertEqual(r2o({"text": "yes", "yes_no": "yes"}, "confirm_phone")["confirmation"], "yes")
        q = r2o({"text": "what are your timings?", "question": "timings", "intent": "question", "answer": "We're open 7 to 9."},
                "ask_intent")
        self.assertEqual((q["acts"], q["intent"], q["say"], q["question"]),
                         (["question"], "info", "We're open 7 to 9.", "timings"))
        mid = r2o({"text": "what are your timings?", "question": "timings", "intent": "question"}, "ask_phone")
        self.assertEqual(mid["intent"], "none")             # a question never switches a workflow
        self.assertEqual(r2o({"text": "how can you help me", "question": "help", "intent": "question"}, None)["acts"],
                         ["capability"])
        self.assertEqual(r2o({"text": "is this a bot", "intent": "bot"})["acts"], ["robot_question"])
        self.assertEqual(r2o({"text": "get me a person", "intent": "human"})["acts"], ["wants_human"])
        emergency = r2o({"text": "my tooth is broken", "intent": "emergency"})
        self.assertEqual((emergency["emergency"], emergency["intent"]), ("urgent", "book"))
        self.assertEqual(r2o({"text": "bye", "intent": "end"})["acts"], ["end"])
        self.assertEqual(r2o({"text": "busy", "nonanswer": True}, "ask_when")["acts"], ["non_answer"])
        self.assertEqual(r2o({"text": "what's the best", "fragment": True})["acts"], ["fragment"])
        known = r2o({"text": "with dr rao", "doctor": "Dr Rao"}, "ask_when", doctors=("Dr Rao",))
        unknown = r2o({"text": "with dr sharma", "doctor": "Dr Sharma"}, "ask_when", doctors=("Dr Rao",))
        self.assertEqual((known.get("doctor"), unknown.get("doctor"), unknown.get("doctor_phrase")),
                         ("Dr Rao", None, "Dr Sharma"))
        child = r2o({"text": "for my son Arjun", "patient": "Arjun"}, "ask_patient")
        self.assertEqual((child["patient_name"], child["for_someone_else"]), ("Arjun", True))
        volunteered = r2o({"text": "a cleaning", "service": "Teeth Cleaning", "intent": "book"}, "ask_intent")
        self.assertEqual((volunteered["acts"], volunteered["intent"]), (["info"], "book"))
        self.assertEqual(r2o({"text": "hmm"})["acts"], ["unclear"])
        fixed = r2o({"text": "no, 98765 43211", "yes_no": "no", "phone": "9876543211"}, "confirm_phone")
        self.assertTrue(fixed["correction"])
        for obj in (q, mid, child, r2o({"text": ""}, None)):
            self.assertTrue({"acts", "intent", "next_goal", "say", "ask"} <= set(obj))


# ---------------------------------------------------------------- llm.GeminiNLU streaming


class ApiError(Exception):
    def __init__(self, code):
        super().__init__(f"error {code}")
        self.code = code


class FakeModels:
    """Each plan is a list of chunks: str (text), float (sleep), Exception (raise there); or an Exception (fail to open)."""

    def __init__(self, plans):
        self.plans = list(plans)
        self.calls = []
        self.closed = 0

    async def generate_content_stream(self, *, model, contents, config):
        self.calls.append((model, config))
        plan = self.plans.pop(0)
        if isinstance(plan, Exception):
            raise plan
        models = self

        async def gen():
            try:
                for item in plan:
                    if isinstance(item, Exception):
                        raise item
                    if isinstance(item, float):
                        await asyncio.sleep(item)
                        continue
                    yield SimpleNamespace(text=item)
            finally:
                models.closed += 1
        return gen()

    async def generate_content(self, *, model, contents, config):
        self.calls.append((model, config))
        return SimpleNamespace(text='{"intent": "book", "name": "null"}')


def gemini(plans, timeout=2.0):
    client = GeminiFake(plans)
    g = llm.GeminiNLU(keys=["test-key"], model="primary", timeout=timeout)
    g.fallback_model = "fallback"
    g.keys[0].client = client
    return g, client.aio.models


class GeminiFake:
    def __init__(self, plans):
        self.aio = SimpleNamespace(models=FakeModels(plans))


async def collect(agen):
    return [chunk async for chunk in agen]


@unittest.skipUnless(llm.GENAI_AVAILABLE, "google-genai not installed")
class GeminiStreamTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(llm.logger, "warning")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_streams_chunks_with_the_schema(self):
        g, models = gemini([['{"acts": [', '"answer"]}']])
        chunks = run(collect(g.generate_json_stream("c", "s", schema=SCHEMA)))
        self.assertEqual("".join(chunks), '{"acts": ["answer"]}')
        model, config = models.calls[0]
        self.assertEqual(model, "primary")
        self.assertEqual(config.response_mime_type, "application/json")
        self.assertEqual(config.temperature, 0.0)
        self.assertIsNotNone(config.response_schema)
        self.assertEqual(config.response_schema.property_ordering[0], "acts")
        self.assertEqual(g.keys[0].requests, 1)

    def test_a_429_after_the_first_token_is_never_retried(self):
        g, models = gemini([['{"acts": ', ApiError(429)], [['{"never": 1}']]])
        chunks = run(collect(g.generate_json_stream("c", "s", schema=SCHEMA)))
        self.assertEqual(chunks, ['{"acts": '])
        self.assertEqual(len(models.calls), 1)
        self.assertGreater(g.keys[0].cooldown_until, time.monotonic())   # the key rests

    def test_one_retry_before_the_first_token(self):
        g, models = gemini([ApiError(503), ['{"ok": true}']])
        chunks = run(collect(g.generate_json_stream("c", "s", schema=SCHEMA)))
        self.assertEqual(chunks, ['{"ok": true}'])
        self.assertEqual([m for m, _ in models.calls], ["primary", "fallback"])

    def test_at_most_one_retry(self):
        g, models = gemini([ApiError(503), ApiError(503), ['{"never": 1}']])
        self.assertEqual(run(collect(g.generate_json_stream("c", "s", schema=SCHEMA))), [])
        self.assertEqual(len(models.calls), 2)

    def test_no_retry_without_budget(self):
        g, models = gemini([[0.5, ApiError(503)], ['{"never": 1}']], timeout=1.0)
        self.assertEqual(run(collect(g.generate_json_stream("c", "s", schema=SCHEMA))), [])
        self.assertEqual(len(models.calls), 1)               # 0.5 s left < MIN_RETRY_BUDGET_S

    def test_timeout_before_the_first_token(self):
        g, models = gemini([[5.0, '{"late": 1}'], ['{"never": 1}']], timeout=0.3)
        started = time.monotonic()
        self.assertEqual(run(collect(g.generate_json_stream("c", "s", schema=SCHEMA))), [])
        self.assertLess(time.monotonic() - started, 0.8)
        self.assertEqual(len(models.calls), 1)
        self.assertEqual(g.keys[0].failures, 1)

    def test_closing_the_iterator_closes_the_request(self):
        g, models = gemini([['{"a": ', 2.0, '1}']])

        async def go():
            agen = g.generate_json_stream("c", "s", schema=SCHEMA)
            first = await agen.__anext__()
            await agen.aclose()
            return first

        self.assertEqual(run(go()), '{"a": ')
        self.assertEqual(models.closed, 1)

    def test_unusable_yields_nothing(self):
        g, models = gemini([['{"never": 1}']])
        g.available = False
        self.assertEqual(run(collect(g.generate_json_stream("c", "s", schema=SCHEMA))), [])
        self.assertEqual(models.calls, [])

    def test_through_nlu_gemini_backend(self):
        g, models = gemini([[ordered_text(OBJ)[:30], ordered_text(OBJ)[30:]]])

        async def go():
            with mock.patch.object(llm, "_nlu", g), nlu.use_backend(nlu.GeminiBackend()):
                stream = await nlu.understand_stream(brief_for("x"))
                return await consume(stream)

        head, says, ask = run(go())
        self.assertEqual(head.service, "Teeth Cleaning")
        self.assertEqual(says, split_sentences(OBJ["say"]))
        self.assertEqual(ask, OBJ["ask"])

    def test_generate_json_is_unchanged(self):
        g, models = gemini([])
        self.assertEqual(run(g.generate_json("c", "s")), {"intent": "book", "name": None})
        _model, config = models.calls[0]
        self.assertIsNone(config.response_schema)


if __name__ == "__main__":
    unittest.main()
