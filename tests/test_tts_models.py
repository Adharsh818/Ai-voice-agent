"""ElevenLabs models (17 Oct): Flash v2.5 stays on text-to-speech; Eleven v4 Turbo speaks over text-to-dialogue."""

import asyncio
import json
import unittest

import httpx

import tts_elevenlabs
from tts_elevenlabs import ElevenLabsTTS, needs_dialogue


def run_tts(model_id: str):
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(200, content=b"\x01\x00" * 800)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            tts = ElevenLabsTTS(api_key="k", voice_id="voice1", model_id=model_id, client=client)
            return await tts.synthesize("Hi, this is Emma at Pearl Dental.")

    audio = asyncio.run(run())
    return audio, requests


class ElevenLabsModelTests(unittest.TestCase):
    def test_flash_uses_text_to_speech_with_the_voice_settings(self):
        audio, requests = run_tts("eleven_flash_v2_5")
        self.assertEqual(len(audio), 1600)
        self.assertEqual(requests[0].url.path, "/v1/text-to-speech/voice1/stream")
        body = json.loads(requests[0].content)
        self.assertEqual(body["model_id"], "eleven_flash_v2_5")
        self.assertEqual(body["voice_settings"], tts_elevenlabs.DEFAULT_VOICE_SETTINGS)
        self.assertEqual(requests[0].url.params["output_format"], "pcm_16000")

    def test_v4_turbo_uses_text_to_dialogue_with_one_voice(self):
        audio, requests = run_tts("eleven_v4_turbo")
        self.assertEqual(len(audio), 1600)
        self.assertEqual(requests[0].url.path, "/v1/text-to-dialogue/stream")
        body = json.loads(requests[0].content)
        self.assertEqual(body["model_id"], "eleven_v4_turbo")
        self.assertEqual(body["inputs"], [{"text": "Hi, this is Emma at Pearl Dental.", "voice_id": "voice1"}])
        self.assertEqual(requests[0].url.params["output_format"], "pcm_16000")

    def test_only_v4_turbo_needs_dialogue(self):
        self.assertTrue(needs_dialogue("eleven_v4_turbo"))
        for model in ("eleven_flash_v2_5", "eleven_v4", "eleven_multilingual_v2", ""):
            self.assertFalse(needs_dialogue(model), model)

    def test_a_v4_turbo_server_opens_no_tts_websocket(self):
        import config
        import server
        from types import SimpleNamespace
        from unittest import mock
        state = SimpleNamespace(http_tts=ElevenLabsTTS(api_key="k"), cache=None, backup_tts=None, latency=None)
        with mock.patch.multiple(config, ELEVENLABS_API_KEY="k", TTS_TRANSPORT="ws", ELEVENLABS_MODEL="eleven_v4_turbo"):
            self.assertIsNone(server._services(state).tts)
        with mock.patch.multiple(config, ELEVENLABS_API_KEY="k", TTS_TRANSPORT="ws", ELEVENLABS_MODEL="eleven_flash_v2_5"):
            self.assertIsNotNone(server._services(state).tts)


if __name__ == "__main__":
    unittest.main()
