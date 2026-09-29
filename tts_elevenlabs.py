"""
ElevenLabs streaming Text-to-Speech (Flash v2.5), raw PCM output.

Two transports behind one interface — `await tts.open_stream(text)` returns a
PCMStream, an async iterator of 16-bit little-endian mono PCM chunks that can be
cancelled mid-flight:

- ElevenLabsStreamTTS: the multi-context WebSocket. One socket per call, one
  context per utterance; a barge-in closes only the interrupted context, so the
  next reply starts without reconnecting.
- ElevenLabsTTS: the HTTP streaming endpoint. Used as the fallback and to
  pre-render cached prompts.

PCM (not MP3) because the browser can play it with no decoder or MediaSource
buffering, flushes are exact on barge-in, and the telephony path uses the same
format (pcm_8000 for 8 kHz AudioSocket).
"""

import asyncio
import base64
import itertools
import json
import logging
from typing import Callable, Optional

import httpx
import websockets

logger = logging.getLogger(__name__)

ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1/text-to-speech"
ELEVENLABS_WS_URL = "wss://api.elevenlabs.io/v1/text-to-speech"

# Voice tuning for a natural, warm receptionist: moderate stability keeps
# intonation lively; similarity keeps the base voice's character.
DEFAULT_VOICE_SETTINGS = {
    "stability": 0.5,
    "similarity_boost": 0.75,
    "style": 0.3,
    "use_speaker_boost": True,
}


class PCMStream:
    """One utterance's audio: an async iterator fed by a TTS producer."""

    def __init__(self, on_cancel: Optional[Callable[[], None]] = None):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._on_cancel = on_cancel
        self._carry = b""
        self.cancelled = False
        self.done = False

    def feed(self, data: bytes):
        if self.cancelled or self.done or not data:
            return
        data = self._carry + data
        # Keep chunks sample-aligned: a 16-bit sample must never be split.
        if len(data) % 2:
            data, self._carry = data[:-1], data[-1:]
        else:
            self._carry = b""
        if data:
            self._queue.put_nowait(data)

    def finish(self):
        if not self.done:
            self.done = True
            self._queue.put_nowait(None)

    def cancel(self):
        if self.cancelled:
            return
        self.cancelled = True
        if self._on_cancel:
            try:
                self._on_cancel()
            except Exception as exc:  # cancellation must never raise into barge-in
                logger.debug("TTS cancel hook failed: %s", exc)
        self.finish()

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        item = await self._queue.get()
        if item is None or self.cancelled:
            raise StopAsyncIteration
        return item


class ElevenLabsTTS:
    """HTTP streaming TTS: text in, PCM chunks out."""

    def __init__(
        self,
        api_key: str,
        voice_id: str = "EXAVITQu4vr4xnSDxMaL",  # "Rachel"
        model_id: str = "eleven_flash_v2_5",
        output_format: str = "pcm_16000",
        client: Optional[httpx.AsyncClient] = None,
        voice_settings: Optional[dict] = None,
    ):
        self.api_key = api_key
        self.voice_id = voice_id
        self.model_id = model_id
        self.output_format = output_format
        self.voice_settings = voice_settings or dict(DEFAULT_VOICE_SETTINGS)
        # A shared, already-warm client (created at server startup) saves the
        # TLS handshake on every utterance.
        self._client = client
        self._owns_client = client is None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(15.0, connect=5.0),
                limits=httpx.Limits(max_connections=8, max_keepalive_connections=8),
            )
            self._owns_client = True
        return self._client

    async def synthesize_stream(self, text: str):
        """Yield raw audio chunks for `text` as ElevenLabs produces them."""
        if not text or not text.strip() or not self.api_key:
            return
        client = await self._get_client()
        url = f"{ELEVENLABS_API_URL}/{self.voice_id}/stream"
        headers = {"xi-api-key": self.api_key, "Content-Type": "application/json"}
        payload = {"text": text, "model_id": self.model_id, "voice_settings": self.voice_settings}
        params = {"output_format": self.output_format}
        try:
            async with client.stream("POST", url, headers=headers, json=payload, params=params) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    logger.error("ElevenLabs HTTP %d: %s", response.status_code,
                                 body.decode("utf-8", errors="replace")[:300])
                    return
                async for chunk in response.aiter_bytes(chunk_size=3200):
                    if chunk:
                        yield chunk
        except httpx.TimeoutException:
            logger.error("ElevenLabs TTS timeout for text: %.50s...", text)
        except httpx.HTTPError as exc:
            logger.error("ElevenLabs HTTP error: %s", exc)

    async def synthesize(self, text: str) -> Optional[bytes]:
        """Complete audio for `text` (used to pre-render cached prompts)."""
        chunks = [chunk async for chunk in self.synthesize_stream(text)]
        audio = b"".join(chunks)
        if len(audio) % 2:
            audio = audio[:-1]
        return audio or None

    async def open_stream(self, text: str) -> PCMStream:
        stream = PCMStream()

        async def pump():
            try:
                async for chunk in self.synthesize_stream(text):
                    stream.feed(chunk)
            finally:
                stream.finish()

        task = asyncio.create_task(pump())
        stream._on_cancel = task.cancel
        return stream

    async def close(self) -> None:
        if self._owns_client and self._client and not self._client.is_closed:
            await self._client.aclose()
        self._client = None


class ElevenLabsStreamTTS:
    """Multi-context WebSocket TTS: one socket per call, one context per utterance."""

    # Contexts close after this much inactivity (the API maximum is 180 s).
    INACTIVITY_TIMEOUT_S = 180

    def __init__(
        self,
        api_key: str,
        voice_id: str = "EXAVITQu4vr4xnSDxMaL",
        model_id: str = "eleven_flash_v2_5",
        output_format: str = "pcm_16000",
        voice_settings: Optional[dict] = None,
    ):
        self.api_key = api_key
        self.voice_id = voice_id
        self.model_id = model_id
        self.output_format = output_format
        self.voice_settings = voice_settings or dict(DEFAULT_VOICE_SETTINGS)
        self._ws = None
        self._receiver: Optional[asyncio.Task] = None
        self._streams: dict[str, PCMStream] = {}
        self._ids = itertools.count(1)
        self._connect_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and self._receiver is not None and not self._receiver.done()

    async def connect(self):
        async with self._connect_lock:
            if self.is_connected:
                return
            url = (
                f"{ELEVENLABS_WS_URL}/{self.voice_id}/multi-stream-input"
                f"?model_id={self.model_id}&output_format={self.output_format}"
                f"&inactivity_timeout={self.INACTIVITY_TIMEOUT_S}"
                # auto_mode: we always send complete sentences and flush, so the
                # server-side chunk schedule would only add buffering delay.
                f"&auto_mode=true"
            )
            self._ws = await websockets.connect(
                url,
                additional_headers={"xi-api-key": self.api_key},
                max_size=16 * 1024 * 1024,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=2,
            )
            self._receiver = asyncio.create_task(self._receive_loop())
            logger.info("ElevenLabs WebSocket connected")

    async def _send(self, message: dict):
        async with self._send_lock:
            await self._ws.send(json.dumps(message))

    async def open_stream(self, text: str) -> PCMStream:
        if not self.is_connected:
            await self.connect()
        cid = f"u{next(self._ids)}"
        stream = PCMStream(on_cancel=lambda: self._close_context(cid))
        self._streams[cid] = stream
        try:
            await self._send({"text": " ", "context_id": cid, "voice_settings": self.voice_settings})
            await self._send({"text": text.strip() + " ", "context_id": cid})
            await self._send({"context_id": cid, "flush": True})
            # Closing right after the flush lets the server finish this audio and
            # then report isFinal, which ends the stream.
            await self._send({"context_id": cid, "close_context": True})
        except Exception:
            self._streams.pop(cid, None)
            stream.finish()
            raise
        return stream

    def _close_context(self, cid: str):
        """Barge-in: drop the context's remaining audio without a reconnect."""
        if self._streams.pop(cid, None) is not None and self.is_connected:
            asyncio.create_task(self._safe_send({"context_id": cid, "close_context": True}))

    async def _safe_send(self, message: dict):
        try:
            await self._send(message)
        except Exception as exc:
            logger.debug("ElevenLabs send failed: %s", exc)

    async def _receive_loop(self):
        try:
            async for raw in self._ws:
                data = json.loads(raw)
                cid = data.get("contextId") or data.get("context_id")
                stream = self._streams.get(cid)
                if stream is None:
                    continue  # a cancelled context still draining
                if data.get("audio"):
                    stream.feed(base64.b64decode(data["audio"]))
                if data.get("isFinal") or data.get("is_final"):
                    stream.finish()
                    self._streams.pop(cid, None)
                if data.get("error") or data.get("message") and not data.get("audio"):
                    logger.warning("ElevenLabs WS message: %s", str(data)[:200])
        except websockets.exceptions.ConnectionClosed as exc:
            logger.info("ElevenLabs WebSocket closed: %s", exc)
        except Exception as exc:
            logger.error("ElevenLabs WebSocket receive error: %s", exc)
        finally:
            for stream in list(self._streams.values()):
                stream.finish()
            self._streams.clear()

    async def close(self):
        if self._ws is not None:
            try:
                await self._send({"close_socket": True})
                await self._ws.close()
            except Exception:
                pass
        if self._receiver:
            self._receiver.cancel()
        self._ws = None
        self._receiver = None
