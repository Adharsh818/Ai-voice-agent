"""
Piper: a free local voice, the last link of the TTS chain (plan 5.7).

ElevenLabs WebSocket -> ElevenLabs HTTP -> Piper. Both ElevenLabs links share
one character quota, and when it ran out on 1 Oct ElevenLabs answered with
silence rather than an error, so every live sentence went quiet. Piper runs
on this machine (an ONNX model in models/piper/), needs no network and no
quota, and starts speaking about 0.6 s after it is asked.

The model speaks at 22.05 kHz; audio is resampled to the call's 16 kHz PCM
with a stateful resampler so chunk boundaries never click. Synthesis runs on
a worker thread and is fed into the same PCMStream type ElevenLabs uses, so
the Speaker cannot tell the engines apart.
"""

import asyncio
import audioop
import logging
import os
import threading
import time
from typing import Optional

import config
from tts_elevenlabs import PCMStream

logger = logging.getLogger(__name__)


class PiperTTS:
    def __init__(self, model_path: str, rate: int = config.TTS_SAMPLE_RATE):
        self.model_path = model_path
        self.rate = rate
        self.voice = None
        self.name = os.path.basename(model_path).rsplit(".onnx", 1)[0]
        self._lock = threading.Lock()   # one synthesis at a time on the shared model

    @property
    def ready(self) -> bool:
        return self.voice is not None

    def _load(self):
        from piper import PiperVoice
        started = time.perf_counter()
        voice = PiperVoice.load(self.model_path)
        # The first synthesis initialises onnxruntime (several seconds); do it
        # now so a call that needs the backup voice never waits for it.
        for _ in voice.synthesize("Okay."):
            pass
        self.voice = voice
        logger.info("Piper backup voice ready: %s (%.1f s)", self.name, time.perf_counter() - started)

    async def warm(self):
        """Load the model in the background at startup."""
        try:
            await asyncio.to_thread(self._load)
        except Exception as exc:
            logger.warning("Piper backup voice unavailable (%s): %s", self.name, exc)

    def _synthesize_into(self, text: str, stream: PCMStream, loop: asyncio.AbstractEventLoop):
        state = None
        with self._lock:
            try:
                for chunk in self.voice.synthesize(text):
                    if stream.cancelled:
                        return
                    pcm = chunk.audio_int16_bytes
                    if chunk.sample_rate != self.rate:
                        pcm, state = audioop.ratecv(pcm, 2, 1, chunk.sample_rate, self.rate, state)
                    loop.call_soon_threadsafe(stream.feed, pcm)
            except Exception as exc:
                logger.error("Piper synthesis failed: %s", exc)
            finally:
                loop.call_soon_threadsafe(stream.finish)

    async def open_stream(self, text: str) -> PCMStream:
        if not self.ready:
            raise RuntimeError("Piper voice not loaded")
        stream = PCMStream()
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, self._synthesize_into, text, stream, loop)
        return stream

    async def synthesize(self, text: str) -> Optional[bytes]:
        stream = await self.open_stream(text)
        return b"".join([chunk async for chunk in stream]) or None

    async def close(self):
        pass


def from_config() -> Optional[PiperTTS]:
    """The configured backup voice, or None when it is switched off or not downloaded."""
    name = config.PIPER_VOICE
    if not name:
        return None
    path = os.path.join(config.BASE_DIR, "models", "piper", f"{name}.onnx")
    if not os.path.exists(path):
        logger.warning("Piper backup voice %s not found at %s; no backup voice", name, path)
        return None
    return PiperTTS(path)
