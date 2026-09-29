"""
Google Cloud Text-to-Speech Engine.

Uses the Google Cloud Text-to-Speech API to synthesize natural-sounding speech.
This replaces the former ElevenLabs-based tts_engine.py.

Prerequisites:
    1. Enable the Cloud Text-to-Speech API in your GCP project.
    2. Create a service account with the "Cloud Text-to-Speech Client" role.
    3. Download the service account key JSON and set the environment variable:
           GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json

Features:
    - LINEAR16 (WAV) output for Asterisk compatibility
    - MP3 output for browser/WebSocket pipeline
    - Configurable voice (WaveNet, Neural2, Standard)
    - Speaking rate and pitch control
    - Async interface for use in FastAPI/asyncio services
"""

import asyncio
import logging
from typing import Optional

import config

logger = logging.getLogger(__name__)

try:
    from google.cloud import texttospeech
    _GOOGLE_TTS_AVAILABLE = True
except ImportError:
    _GOOGLE_TTS_AVAILABLE = False
    logger.warning("google-cloud-texttospeech not installed. Google TTS will be unavailable.")


class GoogleTTS:
    """
    Text-to-Speech using Google Cloud TTS API.

    Usage:
        tts = GoogleTTS()
        audio_bytes = await tts.synthesize(text)           # returns MP3 bytes
        wav_bytes   = await tts.synthesize_wav(text)       # returns WAV/LINEAR16 bytes
        await tts.close()
    """

    def __init__(
        self,
        language_code: str = None,
        voice_name: str = None,
        speaking_rate: float = None,
        pitch: float = None,
    ):
        """
        Args:
            language_code: BCP-47 language tag, e.g. "en-IN", "en-US".
            voice_name: Google TTS voice name, e.g. "en-IN-Wavenet-D".
                        See https://cloud.google.com/text-to-speech/docs/voices
            speaking_rate: Speaking rate in [0.25, 4.0]. 1.0 is normal.
            pitch: Voice pitch in semitones [-20.0, 20.0]. 0.0 is normal.
        """
        self.language_code = language_code or config.GOOGLE_TTS_LANGUAGE_CODE
        self.voice_name = voice_name or config.GOOGLE_TTS_VOICE_NAME
        self.speaking_rate = speaking_rate if speaking_rate is not None else config.GOOGLE_TTS_SPEAKING_RATE
        self.pitch = pitch if pitch is not None else config.GOOGLE_TTS_PITCH

        self._client: Optional["texttospeech.TextToSpeechAsyncClient"] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> "texttospeech.TextToSpeechAsyncClient":
        """Lazy-initialize the async TTS client."""
        if self._client is None:
            if not _GOOGLE_TTS_AVAILABLE:
                raise RuntimeError("google-cloud-texttospeech is not installed.")
            self._client = texttospeech.TextToSpeechAsyncClient()
        return self._client

    def _build_request(
        self, text: str, audio_encoding: "texttospeech.AudioEncoding", sample_rate_hertz: int = None
    ) -> dict:
        """Build the TTS synthesis request parameters."""
        params = dict(
            input=texttospeech.SynthesisInput(text=text),
            voice=texttospeech.VoiceSelectionParams(
                language_code=self.language_code,
                name=self.voice_name,
            ),
            audio_config=texttospeech.AudioConfig(
                audio_encoding=audio_encoding,
                speaking_rate=self.speaking_rate,
                pitch=self.pitch,
                **({"sample_rate_hertz": sample_rate_hertz} if sample_rate_hertz else {}),
            ),
        )
        return params

    # ------------------------------------------------------------------
    # Public async interface
    # ------------------------------------------------------------------

    async def synthesize(self, text: str) -> Optional[bytes]:
        """
        Synthesize text to MP3 audio bytes (for browser/WebSocket pipeline).

        Args:
            text: The text to synthesize.

        Returns:
            MP3 audio bytes, or None on failure.
        """
        if not text or not text.strip():
            return None

        try:
            client = self._get_client()
            params = self._build_request(text, texttospeech.AudioEncoding.MP3)
            response = await client.synthesize_speech(**params)
            logger.debug(
                "Google TTS synthesized MP3: %d chars → %d bytes",
                len(text),
                len(response.audio_content),
            )
            return response.audio_content
        except Exception as e:
            logger.error("Google TTS MP3 synthesis error: %s", e)
            return None

    async def synthesize_wav(self, text: str, sample_rate: int = 8000) -> Optional[bytes]:
        """
        Synthesize text to LINEAR16 (WAV) audio bytes.

        Used by the Asterisk AGI pipeline which requires raw PCM audio.
        Asterisk typically uses 8000 Hz (G.711 mu-law/a-law), but you can pass
        16000 Hz for wideband channels.

        Args:
            text: The text to synthesize.
            sample_rate: Target sample rate in Hz (default: 8000 for Asterisk).

        Returns:
            Raw LINEAR16 PCM bytes (no WAV header), or None on failure.
        """
        if not text or not text.strip():
            return None

        try:
            client = self._get_client()
            params = self._build_request(
                text,
                texttospeech.AudioEncoding.LINEAR16,
                sample_rate_hertz=sample_rate,
            )
            response = await client.synthesize_speech(**params)
            logger.debug(
                "Google TTS synthesized LINEAR16 @%dHz: %d chars → %d bytes",
                sample_rate,
                len(text),
                len(response.audio_content),
            )
            return response.audio_content
        except Exception as e:
            logger.error("Google TTS WAV synthesis error: %s", e)
            return None

    async def close(self) -> None:
        """Close the TTS client and release resources."""
        if self._client:
            try:
                await self._client.transport.close()
            except Exception:
                pass
            self._client = None
        logger.info("Google TTS client closed")
