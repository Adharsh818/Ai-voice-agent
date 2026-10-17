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


class CompareToolTests(unittest.TestCase):
    """tools/tts_compare.py (17 Oct): the same Emma lines in each voice, timed, with a listening page."""

    def test_both_voices_speak_every_line_and_the_page_plays_them(self):
        import base64
        import os
        import sys
        import tempfile
        import wave
        from pathlib import Path
        from types import SimpleNamespace
        from unittest import mock

        import config
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
        import tts_compare

        sarvam_bodies = []

        def handler(request: httpx.Request):
            if request.url.host == "api.sarvam.ai":
                body = json.loads(request.content)
                sarvam_bodies.append((request.headers["api-subscription-key"], body))
                if body["speaker"] == "nobody":
                    return httpx.Response(400, json={"error": {"message": "speaker not compatible"}})
                wav = tts_compare._wav(b"\x02\x00" * 160)
                return httpx.Response(200, json={"request_id": "r", "audios": [base64.b64encode(wav).decode()]})
            return httpx.Response(200, content=b"\x01\x00" * 800)

        async def run(args, out):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await tts_compare.compare(args, out, client)

        with tempfile.TemporaryDirectory() as d, mock.patch.object(config, "ELEVENLABS_API_KEY", "k"), \
                mock.patch.dict(os.environ, {"SARVAM_API_KEY": "sk"}):
            args = SimpleNamespace(skip_elevenlabs=False, sarvam_model="bulbul:v3", speakers=["priya", "nobody"])
            voices = asyncio.run(run(args, Path(d)))
            page = tts_compare._page(voices, Path(d)).read_text(encoding="utf-8")
            files = sorted(p.name for p in Path(d).glob("*.wav"))
            with wave.open(str(Path(d) / files[0])) as w:
                rate = w.getframerate()

        lines = len(tts_compare.LINES)
        self.assertEqual(len(voices), 3)
        eleven = voices[f"ElevenLabs {config.ELEVENLABS_MODEL}"]
        self.assertTrue(all(r["first_s"] is not None and r["file"] for r in eleven))
        self.assertEqual(len(files), 2 * lines)                      # the refused speaker saved nothing
        self.assertEqual(rate, 16000)
        self.assertIn("speaker not compatible", voices["Sarvam bulbul:v3 nobody"][0]["error"])
        key, body = sarvam_bodies[0]
        self.assertEqual((key, body["model"], body["speaker"], body["target_language_code"]),
                         ("sk", "bulbul:v3", "priya", "en-IN"))
        self.assertEqual(page.count("<audio"), 2 * lines)


if __name__ == "__main__":
    unittest.main()
