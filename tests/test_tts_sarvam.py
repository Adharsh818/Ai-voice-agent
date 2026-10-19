"""Sarvam as Emma's voice (tts_sarvam.SarvamTTS; owner, 20 Oct): PCM out, a refusal hands the sentence on."""

import asyncio
import json
import unittest
from unittest import mock

import httpx

import config
from speech import PromptCache, Speaker
from tts_elevenlabs import PCMStream
from tts_sarvam import SarvamTTS, SarvamTTSError


def sarvam(handler, **kw):
    return SarvamTTS(api_key="test-key", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), **kw)


class Transport:
    def __init__(self):
        self.audio = 0

    async def send_audio(self, turn_id, pcm):
        self.audio += len(pcm)


class OtherVoice:
    """Stands in for ElevenLabs: says whatever it is given."""
    ready = True

    def __init__(self):
        self.said = []

    async def open_stream(self, text):
        self.said.append(text)
        stream = PCMStream()
        stream.feed(bytes(3200))
        stream.finish()
        return stream


async def drain(stream):
    return b"".join([chunk async for chunk in stream])


class SarvamTTSTests(unittest.TestCase):
    def test_streams_linear16_pcm_with_the_chosen_voice(self):
        seen = {}

        def handler(request):
            seen.update(json.loads(request.content))
            seen["key"] = request.headers.get("api-subscription-key")
            return httpx.Response(200, content=bytes(6401))

        tts = sarvam(handler, speaker="neha_en_customer")
        pcm = asyncio.run(drain_open(tts, "Sure, one moment."))
        self.assertEqual(len(pcm), 6400)                         # sample-aligned (the odd byte is held back)
        self.assertEqual(seen["key"], "test-key")
        self.assertEqual((seen["speaker"], seen["model"], seen["output_audio_codec"], seen["speech_sample_rate"]),
                         ("neha_en_customer", "bulbul:v4-flash", "linear16", 16000))

    def test_a_refusal_raises_before_any_audio(self):
        tts = sarvam(lambda request: httpx.Response(402, content=b'{"error":"insufficient credits"}'))
        with self.assertRaises(SarvamTTSError):
            asyncio.run(tts.open_stream("Hello"))

    def test_a_refused_sentence_goes_to_the_next_voice(self):
        # Sarvam out of credits: ElevenLabs (the fallback) says it, and audio still reaches the caller.
        tts = sarvam(lambda request: httpx.Response(429, content=b'{"error":"rate limited"}'))
        other, transport = OtherVoice(), Transport()
        speaker = Speaker(transport, None, tts, other, None)
        asyncio.run(speaker.speak(1, "Which day works best for you?"))
        self.assertEqual(other.said, ["Which day works best for you?"])
        self.assertEqual(transport.audio, 3200)

    def test_pre_rendering_waits_out_a_rate_limit(self):
        answers = [httpx.Response(429, content=b'{"error":"Rate limit exceeded"}'), httpx.Response(200, content=bytes(3200))]
        tts = sarvam(lambda request: answers.pop(0))
        with mock.patch.object(SarvamTTS, "RENDER_RETRY_WAITS_S", (0.0,)):
            self.assertEqual(len(asyncio.run(tts.synthesize("No worries."))), 3200)

    def test_the_prompt_cache_is_keyed_on_the_sarvam_voice(self):
        a = sarvam(lambda r: httpx.Response(200), speaker="ishita_en_customer")
        b = sarvam(lambda r: httpx.Response(200), speaker="neha_en_customer")
        cache_a = PromptCache("unused", a.voice_id, a.model_id, a.output_format, a.voice_settings)
        cache_b = PromptCache("unused", b.voice_id, b.model_id, b.output_format, b.voice_settings)
        self.assertNotEqual(cache_a.key("Hello"), cache_b.key("Hello"))   # a new voice never plays old audio


async def drain_open(tts, text):
    return await drain(await tts.open_stream(text))


class WiringTests(unittest.TestCase):
    def test_services_put_sarvam_first_and_elevenlabs_second(self):
        import server
        state = mock.Mock()
        state.sarvam_tts = sarvam(lambda r: httpx.Response(200))
        state.http_tts = OtherVoice()
        with mock.patch.object(config, "ELEVENLABS_API_KEY", "x"):
            services = server._services(state)
        self.assertIs(services.tts, state.sarvam_tts)
        self.assertIs(services.fallback_tts, state.http_tts)


if __name__ == "__main__":
    unittest.main()
