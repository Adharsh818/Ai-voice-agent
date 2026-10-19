"""
Sarvam streaming Text-to-Speech (Bulbul v4 Flash), raw PCM output, for Emma's voice (owner, 20 Oct:
Sarvam replaces ElevenLabs as the voice; the brain and the listening stay as they are).

Same interface as tts_elevenlabs.ElevenLabsTTS, so speech.Speaker and the prompt cache use it
unchanged: `await tts.open_stream(text)` returns a PCMStream of 16-bit little-endian mono PCM at
`sample_rate`, and `await tts.synthesize(text)` returns the whole clip (cache pre-rendering).

open_stream waits for Sarvam's answer before it returns: a refusal (credits used up, 429, 5xx,
a bad voice name) raises, so the Speaker moves on to the next voice (ElevenLabs, then Piper)
for that sentence instead of playing silence. Measured 20 Oct from this PC: first audio p50
~0.28 s over HTTP streaming (ElevenLabs HTTP ~0.49 s).
"""

import asyncio
import logging
from typing import Optional

import httpx

import logredact
import usage
from tts_elevenlabs import PCMStream

logger = logging.getLogger(__name__)

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech/stream"


class SarvamTTSError(RuntimeError):
    """Sarvam refused or failed the request before any audio (the Speaker tries the next voice)."""


class SarvamTTS:
    """HTTP streaming TTS: text in, PCM chunks out."""

    def __init__(self, api_key: str, speaker: str = "ishita_en_customer", model: str = "bulbul:v4-flash",
                 language: str = "en-IN", sample_rate: int = 16000, pace: float = 1.0,
                 temperature: float = 0.6, client: Optional[httpx.AsyncClient] = None, url: str = SARVAM_TTS_URL):
        self.api_key = api_key
        self.speaker = speaker
        self.model = model
        self.language = language
        self.sample_rate = sample_rate
        self.pace = pace
        self.temperature = temperature
        self.url = url
        self._client = client
        self._owns_client = client is None

    # The prompt cache keys its files on these (speech.PromptCache): a new voice renders anew.
    @property
    def voice_id(self) -> str:
        return f"sarvam:{self.speaker}"

    @property
    def model_id(self) -> str:
        return self.model

    @property
    def output_format(self) -> str:
        return f"linear16_{self.sample_rate}"

    @property
    def voice_settings(self) -> dict:
        return {"pace": self.pace, "temperature": self.temperature, "language": self.language}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0),
                                             limits=httpx.Limits(max_connections=8, max_keepalive_connections=8))
            self._owns_client = True
        return self._client

    def _payload(self, text: str) -> dict:
        return {"text": text, "model": self.model, "speaker": self.speaker, "language_code": self.language,
                "output_audio_codec": "linear16", "speech_sample_rate": self.sample_rate,
                "pace": self.pace, "temperature": self.temperature}

    async def _send(self, text: str) -> httpx.Response:
        """Start the request; return the streaming response once Sarvam said yes, else raise SarvamTTSError."""
        client = await self._get_client()
        request = client.build_request("POST", self.url, json=self._payload(text),
                                       headers={"api-subscription-key": self.api_key})
        try:
            response = await client.send(request, stream=True)
        except httpx.HTTPError as exc:
            raise SarvamTTSError(f"network: {type(exc).__name__}") from exc
        if response.status_code != 200:
            body = (await response.aread()).decode("utf-8", errors="replace")[:300]
            await response.aclose()
            raise SarvamTTSError(f"HTTP {response.status_code}: {body}")
        usage.add("tts_chars", len(text.strip()))
        return response

    async def synthesize_stream(self, text: str):
        """Yield raw PCM chunks for `text` as Sarvam produces them (raises SarvamTTSError before any audio)."""
        if not text or not text.strip() or not self.api_key:
            return
        response = await self._send(text)
        try:
            async for chunk in response.aiter_bytes(chunk_size=3200):
                if chunk:
                    yield chunk
        except httpx.HTTPError as exc:
            logger.error("Sarvam TTS stream broke off (%s) for: %s...", type(exc).__name__,
                         logredact.mask_phones(text)[:50])
        finally:
            await response.aclose()

    async def synthesize(self, text: str) -> Optional[bytes]:
        """Complete audio for `text` (used to pre-render cached prompts)."""
        chunks = [chunk async for chunk in self.synthesize_stream(text)]
        audio = b"".join(chunks)
        if len(audio) % 2:
            audio = audio[:-1]
        return audio or None

    async def open_stream(self, text: str) -> PCMStream:
        if not text or not text.strip() or not self.api_key:
            raise SarvamTTSError("nothing to say" if self.api_key else "no SARVAM_API_KEY")
        try:
            response = await self._send(text)
        except SarvamTTSError as exc:
            logger.warning("Sarvam TTS refused (%s); the next voice takes this sentence", exc)
            raise
        stream = PCMStream()

        async def pump():
            try:
                async for chunk in response.aiter_bytes(chunk_size=3200):
                    stream.feed(chunk)
            except httpx.HTTPError as exc:
                logger.error("Sarvam TTS stream broke off: %s", type(exc).__name__)
            finally:
                await response.aclose()
                stream.finish()

        task = asyncio.create_task(pump())
        stream._on_cancel = task.cancel
        return stream

    async def close(self) -> None:
        if self._owns_client and self._client and not self._client.is_closed:
            await self._client.aclose()
        self._client = None
