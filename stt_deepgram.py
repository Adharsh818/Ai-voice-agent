"""
Deepgram STT Engine — real-time streaming Speech-to-Text.

Raw WebSocket to Deepgram's streaming API (no SDK). PCM in, JSON events out:

    on_speech_started(timestamp)        VAD: the caller started speaking (barge-in cue)
    on_transcript(text, is_final)       live caption; interim text confirms a barge-in
    on_utterance_end(text, end_sec)     the caller finished a turn; `end_sec` is the
                                        audio-stream time the last word ended, so the
                                        real "caller stopped talking" moment is known

Audio that arrives before the socket is open (the greeting plays while it
connects) is buffered, not dropped. A KeepAlive is sent whenever no audio has
gone out for a few seconds so Deepgram never closes an idle call.
"""

import asyncio
import json
import logging
import time
from typing import Callable, Optional
from urllib.parse import urlencode

import websockets

logger = logging.getLogger(__name__)

KEEPALIVE_AFTER_S = 4.0
MAX_PREBUFFER_BYTES = 16000 * 2 * 2  # 2 s of 16 kHz PCM16


class DeepgramSTT:
    """
    Usage:
        stt = DeepgramSTT(api_key="...", on_transcript=..., on_utterance_end=...)
        await stt.connect(sample_rate=16000)
        await stt.send_audio(pcm_bytes)
        await stt.close()
    """

    DEEPGRAM_WS_URL = "wss://api.deepgram.com/v1/listen"

    def __init__(
        self,
        api_key: str,
        on_transcript: Optional[Callable] = None,
        on_utterance_end: Optional[Callable] = None,
        on_speech_started: Optional[Callable] = None,
        model: str = "nova-3",
        language: str = "en-IN",
        endpointing_ms: int = 200,
        utterance_end_ms: int = 1000,
        keyterms: Optional[list] = None,
    ):
        self.api_key = api_key
        self.on_transcript = on_transcript
        self.on_utterance_end = on_utterance_end
        self.on_speech_started = on_speech_started
        self.model = model
        self.language = language
        self.endpointing_ms = endpointing_ms
        self.utterance_end_ms = utterance_end_ms
        self.keyterms = keyterms or []

        self._ws = None
        self._receive_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        self._current_utterance = ""
        self._last_word_end: Optional[float] = None
        self._prebuffer: list[bytes] = []
        self._prebuffer_bytes = 0
        self._last_send = time.monotonic()
        self._closing = False
        self.is_connected = False

    def _url(self, sample_rate: int) -> str:
        params = [
            ("model", self.model),
            ("language", self.language),
            ("encoding", "linear16"),
            ("sample_rate", sample_rate),
            ("channels", 1),
            ("punctuate", "true"),
            ("smart_format", "true"),
            ("interim_results", "true"),
            ("endpointing", self.endpointing_ms),
            ("utterance_end_ms", self.utterance_end_ms),
            ("vad_events", "true"),
        ]
        if self.model.startswith("nova-3"):
            params += [("keyterm", term) for term in self.keyterms]
        return f"{self.DEEPGRAM_WS_URL}?{urlencode(params)}"

    async def connect(self, sample_rate: int = 16000):
        """Open WebSocket to Deepgram streaming API."""
        if not self.api_key:
            logger.warning("No Deepgram API key — STT disabled")
            return
        try:
            self._ws = await websockets.connect(
                self._url(sample_rate),
                additional_headers={"Authorization": f"Token {self.api_key}"},
                ping_interval=20,
                ping_timeout=10,
                close_timeout=2,
            )
            self.is_connected = True
            self._receive_task = asyncio.create_task(self._receive_loop())
            self._keepalive_task = asyncio.create_task(self._keepalive_loop())
            logger.info("Deepgram STT connected (model=%s, language=%s)", self.model, self.language)
            # Flush what the caller said while the socket was opening.
            buffered, self._prebuffer, self._prebuffer_bytes = self._prebuffer, [], 0
            for chunk in buffered:
                await self.send_audio(chunk)
        except Exception as e:
            logger.error("Failed to connect to Deepgram: %s", e)
            self.is_connected = False

    async def send_audio(self, audio_bytes: bytes):
        """Send raw PCM audio bytes to Deepgram (buffered until connected)."""
        if self._ws and self.is_connected:
            try:
                await self._ws.send(audio_bytes)
                self._last_send = time.monotonic()
            except Exception as e:
                logger.error("Error sending audio to Deepgram: %s", e)
                self.is_connected = False
        elif not self._closing and self._ws is None:
            self._prebuffer.append(audio_bytes)
            self._prebuffer_bytes += len(audio_bytes)
            while self._prebuffer_bytes > MAX_PREBUFFER_BYTES:
                self._prebuffer_bytes -= len(self._prebuffer.pop(0))

    async def _keepalive_loop(self):
        try:
            while self.is_connected:
                await asyncio.sleep(1.0)
                if time.monotonic() - self._last_send > KEEPALIVE_AFTER_S and self._ws:
                    await self._ws.send(json.dumps({"type": "KeepAlive"}))
                    self._last_send = time.monotonic()
        except (asyncio.CancelledError, websockets.exceptions.ConnectionClosed):
            pass
        except Exception as e:
            logger.debug("Deepgram keepalive stopped: %s", e)

    async def _receive_loop(self):
        """Background task: receive and process Deepgram messages."""
        try:
            async for message in self._ws:
                try:
                    data = json.loads(message)
                    msg_type = data.get("type", "")

                    if msg_type == "Results":
                        await self._handle_result(data)
                    elif msg_type == "UtteranceEnd":
                        await self._handle_utterance_end()
                    elif msg_type == "SpeechStarted":
                        if self.on_speech_started:
                            await self.on_speech_started(data.get("timestamp"))
                    elif msg_type == "Error":
                        logger.error("Deepgram error: %s", data.get("description", ""))
                except json.JSONDecodeError:
                    logger.warning("Non-JSON message from Deepgram")
                except Exception as e:
                    logger.error("Error processing Deepgram message: %s", e, exc_info=True)

        except websockets.exceptions.ConnectionClosed as e:
            if not self._closing:
                logger.info("Deepgram connection closed: %s", e)
        except Exception as e:
            logger.error("Deepgram receive loop error: %s", e)
        finally:
            self.is_connected = False

    async def _handle_result(self, data: dict):
        """Process a Deepgram Results message."""
        alternatives = data.get("channel", {}).get("alternatives", [])
        if not alternatives:
            return
        transcript = alternatives[0].get("transcript", "").strip()
        if not transcript:
            return

        is_final = data.get("is_final", False)
        speech_final = data.get("speech_final", False)

        if is_final:
            # Accumulate final transcript segments
            self._current_utterance = f"{self._current_utterance} {transcript}".strip()
            words = alternatives[0].get("words") or []
            if words:
                self._last_word_end = words[-1].get("end")

        # Live caption (and, while Emma is talking, the barge-in signal).
        if self.on_transcript:
            display_text = (
                f"{self._current_utterance} {transcript}".strip()
                if not is_final
                else self._current_utterance
            )
            await self.on_transcript(display_text, is_final)

        # speech_final: Deepgram's endpointer says the utterance is complete.
        if is_final and speech_final:
            await self._emit_utterance()

    async def _handle_utterance_end(self):
        """Deepgram's silence-based UtteranceEnd: the backstop when speech_final never came."""
        await self._emit_utterance()

    async def _emit_utterance(self):
        text = self._current_utterance.strip()
        end_sec = self._last_word_end
        self._current_utterance = ""
        if text and self.on_utterance_end:
            await self.on_utterance_end(text, end_sec)

    def reset_utterance(self):
        """Reset the current utterance buffer."""
        self._current_utterance = ""

    async def close(self):
        """Close the Deepgram connection."""
        self._closing = True
        for task in (self._keepalive_task, self._receive_task):
            if task:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        if self._ws:
            try:
                await self._ws.send(json.dumps({"type": "CloseStream"}))
                await self._ws.close()
            except Exception:
                pass

        self.is_connected = False
        logger.info("Deepgram STT closed")
