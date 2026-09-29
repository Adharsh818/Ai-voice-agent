"""
Google Cloud Speech-to-Text Engine — Real-time Streaming STT.

Uses the Google Cloud Speech-to-Text v1 streaming GRPC API to transcribe
audio in real time. This replaces the former Deepgram-based stt_engine.py.

Prerequisites:
    1. Enable the Cloud Speech-to-Text API in your GCP project.
    2. Create a service account with the "Cloud Speech Client" role.
    3. Download the service account key JSON and set the environment variable:
           GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json

Features:
    - Real-time interim results for live transcript display
    - Automatic utterance-end detection via single-utterance mode
    - Configurable sample rate and language
    - Clean async interface matching the old DeepgramSTT API surface
"""

import asyncio
import logging
import queue
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

try:
    from google.cloud import speech
    _GOOGLE_SPEECH_AVAILABLE = True
except ImportError:
    _GOOGLE_SPEECH_AVAILABLE = False
    logger.warning("google-cloud-speech not installed. Google STT will be unavailable.")


class GoogleSTT:
    """
    Streams audio to Google Cloud Speech-to-Text and fires callbacks on results.

    The Google Cloud Speech streaming API is synchronous/blocking at the gRPC layer,
    so this class runs the blocking stream in a background thread and bridges
    results back to asyncio via a queue.

    Usage:
        stt = GoogleSTT(on_transcript=..., on_utterance_end=..., sample_rate=16000)
        await stt.start()
        await stt.send_audio(pcm_bytes)   # raw LINEAR16 PCM
        await stt.close()
    """

    def __init__(
        self,
        on_transcript: Optional[Callable] = None,
        on_utterance_end: Optional[Callable] = None,
        sample_rate: int = 16000,
        language_code: str = "en-IN",
        single_utterance: bool = False,
    ):
        """
        Args:
            on_transcript: Async callback(text: str, is_final: bool) fired on each result.
            on_utterance_end: Async callback(text: str) fired when a complete utterance is detected.
            sample_rate: Audio sample rate in Hz (must match input audio).
            language_code: BCP-47 language tag (e.g. "en-IN", "en-US").
            single_utterance: If True, streaming stops after the first complete utterance.
        """
        self.on_transcript = on_transcript
        self.on_utterance_end = on_utterance_end
        self.sample_rate = sample_rate
        self.language_code = language_code
        self.single_utterance = single_utterance

        self._audio_queue: queue.Queue[Optional[bytes]] = queue.Queue()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._current_utterance = ""
        self.is_connected = False

    # ------------------------------------------------------------------
    # Public async interface
    # ------------------------------------------------------------------

    async def start(self):
        """Start the background streaming thread."""
        if not _GOOGLE_SPEECH_AVAILABLE:
            logger.warning("Google STT unavailable — google-cloud-speech not installed.")
            return

        self._loop = asyncio.get_running_loop()
        self._thread = threading.Thread(target=self._stream_thread, daemon=True)
        self._thread.start()
        self.is_connected = True
        logger.info(
            "Google STT started (lang=%s, rate=%d Hz)", self.language_code, self.sample_rate
        )

    async def send_audio(self, audio_bytes: bytes):
        """Queue raw PCM audio bytes for streaming to Google STT."""
        if self.is_connected:
            self._audio_queue.put(audio_bytes)

    def reset_utterance(self):
        """Reset the current utterance buffer (e.g. on barge-in)."""
        self._current_utterance = ""

    async def close(self):
        """Stop streaming and clean up the background thread."""
        self.is_connected = False
        # Signal the generator to stop
        self._audio_queue.put(None)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("Google STT closed")

    # ------------------------------------------------------------------
    # Background streaming thread
    # ------------------------------------------------------------------

    def _audio_generator(self):
        """Generator that yields audio chunks from the queue (runs in thread)."""
        while True:
            chunk = self._audio_queue.get()
            if chunk is None:
                # Sentinel — stop streaming
                return
            yield speech.StreamingRecognizeRequest(audio_content=chunk)

    def _stream_thread(self):
        """
        Blocking thread that runs the Google gRPC streaming call.
        Results are dispatched back to the asyncio event loop.
        """
        try:
            client = speech.SpeechClient()

            recognition_config = speech.RecognitionConfig(
                encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=self.sample_rate,
                language_code=self.language_code,
                enable_automatic_punctuation=True,
                model="latest_long",
                use_enhanced=True,
            )

            streaming_config = speech.StreamingRecognitionConfig(
                config=recognition_config,
                interim_results=True,
                single_utterance=self.single_utterance,
            )

            responses = client.streaming_recognize(
                streaming_config,
                self._audio_generator(),
            )

            for response in responses:
                if not self.is_connected:
                    break
                for result in response.results:
                    if not result.alternatives:
                        continue

                    transcript = result.alternatives[0].transcript.strip()
                    is_final = result.is_final

                    if is_final:
                        # Accumulate into current utterance
                        if self._current_utterance:
                            self._current_utterance += " " + transcript
                        else:
                            self._current_utterance = transcript

                    # Fire interim/final transcript callback
                    if self.on_transcript and transcript:
                        display_text = (
                            self._current_utterance + " " + transcript
                            if not is_final and self._current_utterance
                            else self._current_utterance or transcript
                        ).strip()
                        asyncio.run_coroutine_threadsafe(
                            self.on_transcript(display_text, is_final),
                            self._loop,
                        )

                    # Detect end of utterance
                    if is_final and (self.single_utterance or result.is_final):
                        if self._current_utterance.strip() and self.on_utterance_end:
                            asyncio.run_coroutine_threadsafe(
                                self.on_utterance_end(self._current_utterance.strip()),
                                self._loop,
                            )
                            self._current_utterance = ""

                # If single-utterance mode, the stream ends after one utterance
                if response.speech_event_type == (
                    speech.StreamingRecognizeResponse.SpeechEventType.END_OF_SINGLE_UTTERANCE
                ):
                    if self._current_utterance.strip() and self.on_utterance_end:
                        asyncio.run_coroutine_threadsafe(
                            self.on_utterance_end(self._current_utterance.strip()),
                            self._loop,
                        )
                        self._current_utterance = ""
                    break

        except Exception as e:
            if self.is_connected:  # Only log if not intentionally closed
                logger.error("Google STT stream error: %s", e)
        finally:
            self.is_connected = False
            logger.debug("Google STT stream thread exited")
