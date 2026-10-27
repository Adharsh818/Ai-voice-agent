"""Sarvam as the turn's second model (llm.SarvamNLU, llm.stream_turn; 19 Oct)."""

import asyncio
import json
import time
import unittest
from unittest import mock

import httpx

import config
import llm
import nlu


def sse(*chunks):
    lines = [f"data: {json.dumps({'choices': [{'delta': {'content': c}}]})}" for c in chunks]
    return ("\n\n".join(lines + ["data: [DONE]"]) + "\n\n").encode()


def sarvam_with(handler):
    client = llm.SarvamNLU(key="test-key", model="sarvam-105b-conversations", url="https://sarvam.test/v1/chat")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


async def collect(agen):
    return "".join([chunk async for chunk in agen])


class FakeModel:
    """A stand-in for GeminiNLU / SarvamNLU: yields `chunks`, or nothing (a failed request)."""

    def __init__(self, chunks=(), usable=True, key="k"):
        self.chunks, self._usable, self.key, self.calls = list(chunks), usable, key, []

    @property
    def usable(self):
        return self._usable

    async def generate_json_stream(self, contents, system, *, schema=None, deadline=None, max_tokens=400, retry=True):
        self.calls.append(retry)
        for chunk in self.chunks:
            yield chunk


class SchemaTests(unittest.TestCase):
    def test_gemini_schema_becomes_plain_json_schema(self):
        schema = nlu.build_schema(services=("Teeth Cleaning",), branches=("Jayanagar",), doctors=("Dr Rao",))
        out = llm.json_schema(schema)
        props = out["properties"]
        self.assertEqual(props["name"]["type"], ["string", "null"])
        self.assertIn(None, props["branch"]["enum"])                  # nullable enum takes null
        self.assertNotIn(None, props["intent"]["enum"])               # required enum doesn't
        self.assertEqual(list(props), list(schema["properties"]))     # key order kept (head first)
        self.assertNotIn("nullable", json.dumps(out))


class SarvamStreamTests(unittest.TestCase):
    def test_streams_the_reply_with_reasoning_off_and_the_schema(self):
        seen = {}

        def handler(request):
            seen["body"] = json.loads(request.content)
            seen["key"] = request.headers.get("api-subscription-key")
            return httpx.Response(200, content=sse('{"intent":', '"book"}'))

        client = sarvam_with(handler)
        text = asyncio.run(collect(client.generate_json_stream(
            "caller words", "system", schema=nlu.build_schema(), deadline=time.monotonic() + 5)))
        self.assertEqual(json.loads(text), {"intent": "book"})
        self.assertEqual(seen["key"], "test-key")
        self.assertIsNone(seen["body"]["reasoning_effort"])
        self.assertTrue(seen["body"]["stream"])
        self.assertEqual(seen["body"]["response_format"]["type"], "json_schema")

    def test_an_error_yields_nothing_and_cools_down(self):
        client = sarvam_with(lambda request: httpx.Response(503, content=b'{"error":"overloaded"}'))
        text = asyncio.run(collect(client.generate_json_stream("x", "s", deadline=time.monotonic() + 5)))
        self.assertEqual(text, "")
        self.assertFalse(client.usable)                               # skipped for a while
        self.assertEqual(client.failures, 1)

    def test_no_key_means_unusable(self):
        self.assertFalse(llm.SarvamNLU(key="").usable)


class StreamTurnTests(unittest.TestCase):
    def run_turn(self, first, second, primary="gemini", backup="sarvam"):
        with mock.patch.object(llm, "get_nlu", lambda: first), mock.patch.object(llm, "get_sarvam", lambda: second), \
                mock.patch.object(config, "NLU_PRIMARY", primary), mock.patch.object(config, "NLU_BACKUP", backup):
            return asyncio.run(collect(llm.stream_turn("c", "s", deadline=time.monotonic() + 5)))

    def test_sarvam_backup_is_off_unless_asked_for(self):
        # 19 Oct: the no-model fallback beat Sarvam on the scenarios, so a key alone doesn't turn it on.
        gemini, sarvam = FakeModel([]), FakeModel(['{"b":2}'])
        self.assertEqual(self.run_turn(gemini, sarvam, backup=""), "")
        self.assertEqual(sarvam.calls, [])
        self.assertEqual(gemini.calls, [True])                        # Gemini keeps its own retry

    def test_backup_only_when_the_primary_gave_nothing(self):
        gemini, sarvam = FakeModel(['{"a":1}']), FakeModel(['{"b":2}'])
        self.assertEqual(self.run_turn(gemini, sarvam), '{"a":1}')
        self.assertEqual(sarvam.calls, [])
        gemini, sarvam = FakeModel([]), FakeModel(['{"b":2}'])
        self.assertEqual(self.run_turn(gemini, sarvam), '{"b":2}')
        self.assertEqual(gemini.calls, [False])                       # one Gemini request, its own retry off

    def test_sarvam_first_when_it_is_the_primary(self):
        gemini, sarvam = FakeModel(['{"a":1}']), FakeModel(['{"b":2}'])
        self.assertEqual(self.run_turn(gemini, sarvam, primary="sarvam"), '{"b":2}')
        self.assertEqual(gemini.calls, [])

    def test_without_a_sarvam_key_gemini_keeps_its_own_retry(self):
        gemini, sarvam = FakeModel(['{"a":1}']), FakeModel(['{"b":2}'], key="")
        self.assertEqual(self.run_turn(gemini, sarvam), '{"a":1}')
        self.assertEqual(gemini.calls, [True])

    def test_gemini_down_goes_straight_to_sarvam(self):
        gemini, sarvam = FakeModel(['{"a":1}'], usable=False), FakeModel(['{"b":2}'])
        with mock.patch.object(llm, "get_nlu", lambda: gemini), mock.patch.object(llm, "get_sarvam", lambda: sarvam), \
                mock.patch.object(config, "NLU_BACKUP", "sarvam"):
            self.assertTrue(llm.turn_usable())
        self.assertEqual(self.run_turn(gemini, sarvam), '{"b":2}')


if __name__ == "__main__":
    unittest.main()


class NvidiaTests(unittest.TestCase):
    """NVIDIA's hosted models as the turn's first model (llm.NvidiaNLU; NLU_PRIMARY=nvidia, 25 Oct)."""

    def test_bearer_key_no_sarvam_only_fields(self):
        seen = {}

        def handler(request):
            seen["body"] = json.loads(request.content)
            seen["auth"] = request.headers.get("authorization")
            seen["sarvam_key"] = request.headers.get("api-subscription-key")
            return httpx.Response(200, content=sse('{"intent":', '"book"}'))

        client = llm.NvidiaNLU(key="nv-test", model="minimaxai/minimax-m3.1-flash", url="https://nvidia.test/v1/chat")
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        text = asyncio.run(collect(client.generate_json_stream(
            "caller words", "system", schema=nlu.build_schema(), deadline=time.monotonic() + 5)))
        self.assertEqual(json.loads(text), {"intent": "book"})
        self.assertEqual(seen["auth"], "Bearer nv-test")
        self.assertIsNone(seen["sarvam_key"])
        self.assertNotIn("reasoning_effort", seen["body"])
        self.assertEqual(seen["body"]["model"], "minimaxai/minimax-m3.1-flash")

    def test_nvidia_first_when_it_is_the_primary_and_gemini_retries(self):
        gemini, nvidia = FakeModel(['{"a":1}']), FakeModel([])
        with mock.patch.object(llm, "get_nlu", lambda: gemini), mock.patch.object(llm, "get_nvidia", lambda: nvidia), \
                mock.patch.object(config, "NLU_PRIMARY", "nvidia"), mock.patch.object(config, "NLU_BACKUP", ""):
            text = asyncio.run(collect(llm.stream_turn("c", "s", deadline=time.monotonic() + 5)))
        self.assertEqual(text, '{"a":1}')                             # NVIDIA gave nothing: Gemini's answer
        self.assertEqual(nvidia.calls, [False])


class CerebrasTests(unittest.TestCase):
    def test_bearer_key_and_low_reasoning_for_gpt_oss(self):
        seen = {}

        def handler(request):
            seen["body"] = json.loads(request.content)
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, content=sse('{"intent":"book"}'))

        client = llm.CerebrasNLU(key="cb-test", model="gpt-oss-120b", url="https://cerebras.test/v1/chat")
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        text = asyncio.run(collect(client.generate_json_stream("c", "s", schema=nlu.build_schema(),
                                                               deadline=time.monotonic() + 5)))
        self.assertEqual(json.loads(text), {"intent": "book"})
        self.assertEqual(seen["auth"], "Bearer cb-test")
        self.assertEqual(seen["body"]["reasoning_effort"], "low")
