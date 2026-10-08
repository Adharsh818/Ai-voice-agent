"""
Sarvam AI streaming speech-to-text (saaras), behind the same callbacks as
stt_deepgram.DeepgramSTT, for the R3.4 recogniser comparison (tools/replay.py
--provider sarvam). Not used on calls: Deepgram Nova-3 is Emma's recogniser.

Protocol (docs.sarvam.ai, Realtime Streaming, checked 6 Oct 2026):
    wss://api.sarvam.ai/speech-to-text-realtime/ws?language_code=en-IN&model=saaras:v4&...
    header  api-subscription-key: <SARVAM_API_KEY>
    send    {"event": "audio_input", "audio": "<base64 linear16 PCM>"} ... {"event": "end"}
    receive vad.speech_start, transcript.partial {text}, transcript.final {text, start_s, end_s},
            vad.speech_end, error {code, is_fatal, message}, session.end

Sarvam ends each utterance with its own voice detector (silence_duration_ms),
so every final becomes an utterance end ("speech_final"); the call session's
turn detector then applies the same holds it applies to Deepgram.
"""

import asyncio
import base64
import json
import logging
import time
from typing import Callable, Optional
from urllib.parse import urlencode

import websockets

logger = logging.getLogger(__name__)

URL = "wss://api.sarvam.ai/speech-to-text-realtime/ws"
MAX_KEYTERMS = 50


class SarvamSTT:
    def __init__(self, api_key: str, on_transcript: Optional[Callable] = None,
                 on_utterance_end: Optional[Callable] = None, on_speech_started: Optional[Callable] = None,
                 on_connection_lost: Optional[Callable] = None, model: str = "saaras:v4",
                 language: str = "en-IN", stream_type: str = "fast", silence_ms: int = 500,
                 keyterms: Optional[list] = None, **_ignored):
        self.api_key = api_key
        self.on_transcript = on_transcript
        self.on_utterance_end = on_utterance_end
        self.on_speech_started = on_speech_started
        self.on_connection_lost = on_connection_lost
        self.model = model
        self.language = language
        self.stream_type = stream_type
        self.silence_ms = silence_ms
        self.keyterms = list(keyterms or [])
        self.is_connected = False
        self._ws = None
        self._receive_task: Optional[asyncio.Task] = None
        self._closing = False
        self._pending: list = []                  # audio sent before the socket opened
        self._sent_sec = 0.0
        self.errors: list = []

    def add_keyterms(self, terms):
        for term in terms or ():
            if term and term not in self.keyterms:
                self.keyterms.append(term)

    async def connect(self, sample_rate: int = 16000):
        params = {"language_code": self.language, "model": self.model, "stream_type": self.stream_type,
                  "sample_rate": sample_rate, "encoding": "linear16", "endpointing": "vad",
                  "silence_duration_ms": self.silence_ms, "return_timestamps": "true"}
        terms = [t[:64] for t in self.keyterms][:MAX_KEYTERMS]
        if terms and self.model == "saaras:v4":
            params["keyterms"] = json.dumps(terms)
        try:
            self._ws = await websockets.connect(f"{URL}?{urlencode(params)}",
                                                additional_headers={"api-subscription-key": self.api_key},
                                                max_size=None)
        except Exception as exc:
            logger.error("Sarvam STT connect failed: %s", exc)
            self.errors.append(f"connect: {exc}")
            return
        self.is_connected = True
        self._receive_task = asyncio.create_task(self._receive())
        logger.info("Sarvam STT connected (model=%s, language=%s, keyterms=%d)", self.model, self.language,
                    len(terms))
        for chunk in self._pending:
            await self._send(chunk)
        self._pending = []

    async def _send(self, pcm: bytes):
        try:
            await self._ws.send(json.dumps({"event": "audio_input",
                                            "audio": base64.b64encode(pcm).decode("ascii")}))
        except Exception as exc:
            if not self._closing:
                self.errors.append(f"send: {exc}")
                self.is_connected = False

    async def send_audio(self, pcm: bytes):
        if not pcm:
            return
        if self._ws is None or not self.is_connected:
            if not self._closing:
                self._pending.append(pcm)
            return
        await self._send(pcm)

    async def _receive(self):
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                event = msg.get("event") or msg.get("type")
                if event == "vad.speech_start" and self.on_speech_started:
                    await self.on_speech_started(None)
                elif event == "transcript.partial":
                    text = (msg.get("text") or "").strip()
                    if text and self.on_transcript:
                        await self.on_transcript(text, False)
                elif event == "transcript.final":
                    text = (msg.get("text") or "").strip()
                    if not text:
                        continue
                    if self.on_transcript:
                        await self.on_transcript(text, True)
                    if self.on_utterance_end:
                        end, start = msg.get("end_s"), msg.get("start_s")
                        await self.on_utterance_end(text, end, "speech_final", start_sec=start)
                elif event == "error":
                    self.errors.append(f"{msg.get('code')}: {msg.get('message')}")
                    logger.error("Sarvam STT error %s: %s", msg.get("code"), msg.get("message"))
        except websockets.exceptions.ConnectionClosed as exc:
            if not self._closing:
                self.errors.append(f"closed: {exc}")
                logger.warning("Sarvam STT connection closed: %s", exc)
        finally:
            self.is_connected = False

    async def close(self):
        self._closing = True
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"event": "end"}))
                await asyncio.sleep(0.5)
                await self._ws.close()
            except Exception:
                pass
        if self._receive_task is not None:
            self._receive_task.cancel()
