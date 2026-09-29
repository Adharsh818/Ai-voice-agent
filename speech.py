"""
Turning Emma's reply text into audio as fast as possible.

- split_sentences / speakable: cut a reply into sentences and rewrite display
  text for the ear ("05:00 PM" -> "5 PM", "06 October" -> "6 October").
- PromptCache: pre-rendered PCM for fixed sentences, on disk and in memory.
- Speaker: plays a reply sentence by sentence. Cached sentences go out
  immediately; every uncached run of sentences is sent to live TTS up front,
  so its synthesis overlaps with the cached audio already playing.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# 200 ms of 16 kHz PCM16 per transport frame: small enough that a barge-in
# never waits behind a large send, large enough to keep message counts low.
SEND_CHUNK_BYTES = 6400
MAX_LIVE_SEGMENTS = 4  # ElevenLabs allows 5 concurrent contexts per socket

_ABBREVIATIONS = ("Dr", "Mr", "Mrs", "Ms", "St")
_MONTHS = ("January|February|March|April|May|June|July|August|September|"
           "October|November|December")


def split_sentences(text: str) -> list[str]:
    """Split on sentence-ending punctuation followed by a capital or digit."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[\"'A-Z0-9])", text)
    merged: list[str] = []
    for part in parts:
        if merged and re.search(rf"\b(?:{'|'.join(_ABBREVIATIONS)})\.$", merged[-1]):
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return merged


def speakable(text: str) -> str:
    """Rewrite display text so TTS reads it naturally."""
    out = text
    # 05:00 PM -> 5 PM, 05:30 PM -> 5:30 PM, 7:00 AM -> 7 AM
    out = re.sub(r"\b0?(\d{1,2}):00\s*([AP]M)\b", r"\1 \2", out)
    out = re.sub(r"\b0(\d):(\d{2})\s*([AP]M)\b", r"\1:\2 \3", out)
    # 06 October -> 6 October
    out = re.sub(rf"\b0(\d) ({_MONTHS})\b", r"\1 \2", out)
    # Em/en dashes read as awkward pauses or get voiced; use a comma.
    out = re.sub(r"\s*[—–]\s*", ", ", out)
    return re.sub(r"\s+", " ", out).strip()


class PromptCache:
    """Pre-rendered PCM keyed by voice, model, format, settings and exact text."""

    def __init__(self, root: str, voice_id: str, model_id: str, output_format: str,
                 voice_settings: Optional[dict] = None):
        self._signature = json.dumps(
            [voice_id, model_id, output_format, voice_settings or {}], sort_keys=True
        )
        self.dir = os.path.join(root, "tts", hashlib.sha1(self._signature.encode()).hexdigest()[:12])
        self._mem: dict[str, bytes] = {}
        os.makedirs(self.dir, exist_ok=True)

    @staticmethod
    def normalize(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip())

    def key(self, text: str) -> str:
        return hashlib.sha1(f"{self._signature}|{self.normalize(text)}".encode()).hexdigest()

    def _path(self, text: str) -> str:
        return os.path.join(self.dir, f"{self.key(text)}.pcm")

    def get(self, text: str) -> Optional[bytes]:
        norm = self.normalize(text)
        if not norm:
            return None
        pcm = self._mem.get(norm)
        if pcm is None:
            try:
                with open(self._path(norm), "rb") as fh:
                    pcm = fh.read()
                self._mem[norm] = pcm
            except OSError:
                return None
        return pcm

    def put(self, text: str, pcm: bytes):
        norm = self.normalize(text)
        if not norm or not pcm:
            return
        tmp = self._path(norm) + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(pcm)
        os.replace(tmp, self._path(norm))
        self._mem[norm] = pcm

    async def warm(self, phrases, synth, concurrency: int = 3) -> tuple[int, int]:
        """Render every sentence of `phrases` that is not cached yet."""
        sentences = []
        for phrase in phrases:
            for sentence in split_sentences(phrase):
                spoken = speakable(sentence)
                if spoken not in sentences:
                    sentences.append(spoken)
        missing = [s for s in sentences if self.get(s) is None]
        sem = asyncio.Semaphore(concurrency)

        async def render(sentence):
            async with sem:
                try:
                    pcm = await synth.synthesize(sentence)
                    if pcm:
                        self.put(sentence, pcm)
                except Exception as exc:
                    logger.warning("Could not pre-render %r: %s", sentence, exc)

        if missing:
            logger.info("Pre-rendering %d of %d prompt sentences (%d chars)",
                        len(missing), len(sentences), sum(len(s) for s in missing))
            await asyncio.gather(*(render(s) for s in missing))
        rendered = sum(1 for s in sentences if self.get(s) is not None)
        logger.info("Prompt cache ready: %d/%d sentences", rendered, len(sentences))
        return rendered, len(sentences)


@dataclass
class _Segment:
    text: str
    pcm: Optional[bytes] = None
    stream: object = None


class Speaker:
    """Plays replies for one call through a transport, cache first, live TTS second."""

    def __init__(self, transport, cache: Optional[PromptCache], tts=None, fallback_tts=None):
        self.transport = transport
        self.cache = cache
        self.tts = tts
        self.fallback_tts = fallback_tts
        self.lock = asyncio.Lock()
        self._live: list = []

    def _cached(self, text: str) -> Optional[bytes]:
        return self.cache.get(text) if self.cache else None

    def plan(self, text: str) -> list[_Segment]:
        """Cached sentences stand alone; consecutive uncached ones merge into one live segment."""
        segments: list[_Segment] = []
        for sentence in split_sentences(text):
            spoken = speakable(sentence)
            pcm = self._cached(spoken)
            if pcm is None and segments and segments[-1].pcm is None:
                segments[-1].text = f"{segments[-1].text} {spoken}"
            else:
                segments.append(_Segment(spoken, pcm))
        if sum(1 for s in segments if s.pcm is None) > MAX_LIVE_SEGMENTS:
            segments = [_Segment(" ".join(s.text for s in segments))]
        return segments

    async def _open(self, text: str):
        for engine in (self.tts, self.fallback_tts):
            if engine is None:
                continue
            try:
                return await asyncio.wait_for(engine.open_stream(text), timeout=3.0)
            except Exception as exc:
                logger.warning("TTS stream open failed on %s: %s", type(engine).__name__, exc)
        return None

    async def _send(self, turn_id: int, pcm: bytes, timer, source: str):
        for i in range(0, len(pcm), SEND_CHUNK_BYTES):
            if timer is not None and timer.first_audio_sent is None:
                timer.first_audio_sent = time.perf_counter()
                timer.first_audio_source = source
            await self.transport.send_audio(turn_id, pcm[i:i + SEND_CHUNK_BYTES])

    async def speak(self, turn_id: int, text: str, timer=None) -> bool:
        """Play `text` for `turn_id`. Returns True if any audio was sent."""
        async with self.lock:
            segments = self.plan(text)
            sent = False
            try:
                # Open every live stream first so all synthesis runs in parallel.
                for seg in segments:
                    if seg.pcm is None:
                        seg.stream = await self._open(seg.text)
                        if seg.stream is not None:
                            self._live.append(seg.stream)
                for seg in segments:
                    if seg.pcm is not None:
                        await self._send(turn_id, seg.pcm, timer, "cache")
                        sent = True
                    elif seg.stream is not None:
                        async for chunk in seg.stream:
                            await self._send(turn_id, chunk, timer, "live")
                            sent = True
                    else:
                        logger.error("No TTS available for: %.60s", seg.text)
            finally:
                for seg in segments:
                    if seg.stream is not None:
                        seg.stream.cancel()
                        if seg.stream in self._live:
                            self._live.remove(seg.stream)
            return sent

    async def play_cached(self, turn_id: int, text: str, timer=None, source: str = "filler") -> bool:
        """Play a pre-rendered phrase now, if it is cached and nothing else is playing."""
        pcm = self._cached(speakable(text))
        if pcm is None or self.lock.locked():
            return False
        async with self.lock:
            await self._send(turn_id, pcm, timer, source)
        return True

    def cancel(self):
        """Barge-in: stop generating everything still in flight."""
        for stream in list(self._live):
            stream.cancel()
        self._live.clear()
