"""
Turning Emma's reply text into audio as fast as possible.

- split_sentences / speakable: cut a reply into sentences and rewrite display
  text for the ear ("05:00 PM" -> "5 PM", "06 October" -> "6 October").
- PromptCache: pre-rendered PCM for fixed sentences, on disk and in memory.
- Speaker: plays a reply sentence by sentence. Cached sentences go out
  immediately; every uncached run of sentences is sent to live TTS up front,
  so its synthesis overlaps with the cached audio already playing. When the
  engine streams a reply sentence by sentence, prepare() starts synthesising
  each one the moment it arrives, while the previous one is still playing.
- The Speaker keeps a per-turn timeline of what it sent (text, offset and
  length in ms). With the browser's "playback started" time that tells the
  call session what Emma was saying at any moment: echo detection, trimming an
  interrupted reply to what was heard, and whether a recap was heard in full.
- strip_opener: after a filler ("Okay.") the reply must not start "Okay, ..."
  again; that doubled word was one cause of "she keeps repeating words".
"""

import array
import asyncio
import hashlib
import json
import logging
import math
import os
import random
import re
import time
from dataclasses import dataclass
from typing import Optional

import config
import logredact

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


_OPENER_RE = re.compile(r"^\s*(okay|ok|sure|right|alright|all right|mm-hmm|mhm|mm)\s*[,.!]+\s*", re.I)


def strip_opener(text: str) -> str:
    """Drop a leading "Okay," / "Sure." when a filler already said it ("Okay." alone -> "")."""
    match = _OPENER_RE.match(text or "")
    if not match:
        return text
    rest = text[match.end():].strip()
    return rest[:1].upper() + rest[1:] if rest else ""


def drop_repeats(sentences: list[str], previous: str = "") -> list[str]:
    """Remove a sentence that repeats the one just before it word for word."""
    out, last = [], _plain(previous)
    for sentence in sentences:
        plain = _plain(sentence)
        if plain and plain == last:
            continue
        out.append(sentence)
        last = plain or last
    return out


def _plain(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", (text or "").lower()))


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
    opening: Optional[asyncio.Future] = None   # live stream being opened ahead of playback


def _synth_breath(seed: int, rate: int = 16000) -> bytes:
    """
    A soft ~0.3 s inhale: band-limited noise under a rise-and-fall envelope,
    about -34 dBFS, so it reads as a quiet breath rather than a sound effect.
    """
    rng = random.Random(seed)
    n = int(rate * rng.uniform(0.26, 0.36))
    out = array.array("h")
    low = high = 0.0
    for i in range(n):
        white = rng.uniform(-1.0, 1.0)
        low += 0.18 * (white - low)          # remove the harshest highs
        high += 0.02 * (low - high)          # and the rumble: roughly 400-3000 Hz
        env = math.sin(math.pi * i / n) ** 1.6
        out.append(int(32767 * 0.02 * (low - high) * 3.2 * env))
    return out.tobytes()


BREATHS = [_synth_breath(seed) for seed in (11, 23, 37)]


class Speaker:
    """Plays replies for one call through a transport, cache first, live TTS second."""

    TIMELINE_TURNS = 12   # turns whose timeline is kept (playback reports arrive late)

    def __init__(self, transport, cache: Optional[PromptCache], tts=None, fallback_tts=None, backup_tts=None):
        self.transport = transport
        self.cache = cache
        self.tts = tts
        self.fallback_tts = fallback_tts
        # Piper, the local last resort. Once ElevenLabs has failed in a call,
        # the rest of the call goes straight to it rather than waiting on a
        # dead service every sentence.
        self.backup_tts = backup_tts
        self.degraded = False
        self.lock = asyncio.Lock()
        self._live: list = []
        self._opening: set = set()
        self._last_breath_turn = -10
        # turn -> [[offset_ms, length_ms, text, kind], ...] in the order sent;
        # kind is reply | filler | checking | pause | breath | line
        self.timeline: dict[int, list] = {}
        self._turn_ms: dict[int, float] = {}

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

    def _engines(self):
        backup = self.backup_tts if getattr(self.backup_tts, "ready", True) else None
        if self.degraded and backup is not None:
            return (backup,)
        return (self.tts, self.fallback_tts, backup)

    async def _open(self, text: str):
        for engine in self._engines():
            if engine is None:
                continue
            try:
                stream = await asyncio.wait_for(engine.open_stream(text), timeout=3.0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("TTS stream open failed on %s: %s", type(engine).__name__, exc)
                continue
            stream._engine = engine
            self._live.append(stream)
            return stream
        return None

    def prepare(self, text: str) -> list[_Segment]:
        """
        Plan `text` and start synthesising its uncached parts now. A streamed
        reply's next sentence is prepared while the previous one plays, so the
        caller hears no gap between sentences.
        """
        segments = self.plan(text)
        for seg in segments:
            if seg.pcm is None and (self.tts is not None or self.fallback_tts is not None
                                    or self.backup_tts is not None):
                seg.opening = asyncio.ensure_future(self._open(seg.text))
                self._opening.add(seg.opening)
                seg.opening.add_done_callback(self._opening.discard)
        return segments

    # ------------------------------------------------------------------ timeline
    def _begin(self, turn_id: int, text: str, kind: str):
        entries = self.timeline.setdefault(turn_id, [])
        entries.append([self._turn_ms.get(turn_id, 0.0), 0.0, text, kind])
        for old in [t for t in self.timeline if t < turn_id - self.TIMELINE_TURNS]:
            self.timeline.pop(old, None)
            self._turn_ms.pop(old, None)

    def sent_ms(self, turn_id: int) -> float:
        """Milliseconds of audio sent for a turn so far."""
        return self._turn_ms.get(turn_id, 0.0)

    def window_text(self, turn_id: int, start_ms: float, end_ms: float) -> str:
        """What Emma was saying between two points of a turn's playback."""
        parts = [text for offset, length, text, kind in self.timeline.get(turn_id, [])
                 if text and kind not in ("pause", "breath")
                 and offset <= end_ms and offset + length >= start_ms]
        return " ".join(parts)

    def spoken_text(self, turn_id: int) -> str:
        return self.window_text(turn_id, 0, float("inf"))

    def heard_fraction(self, turn_id: int, played_ms: float) -> float:
        """Share of the turn's reply text (not fillers or pauses) the caller heard."""
        heard = total = 0.0
        for offset, length, text, kind in self.timeline.get(turn_id, []):
            if kind != "reply" or not text:
                continue
            total += len(text)
            if length > 0:
                heard += len(text) * min(1.0, max(0.0, (played_ms - offset) / length))
        return 1.0 if total == 0 else heard / total

    def question_start_ms(self, turn_id: int) -> Optional[float]:
        """
        Where the turn's closing question starts in its playback, estimated from
        the sentence's share of its segment; None when the reply asks nothing.
        """
        replies = [e for e in self.timeline.get(turn_id, []) if e[3] in ("reply", "line") and e[2]]
        if not replies:
            return None
        offset, length, text, _ = replies[-1]
        sentences = split_sentences(text)
        if not sentences or not sentences[-1].rstrip().endswith("?"):
            return None
        before = len(" ".join(sentences[:-1]))
        return offset + (length * before / max(1, len(text)))

    # ------------------------------------------------------------------ sending
    async def _send(self, turn_id: int, pcm: bytes, timer, source: str):
        for i in range(0, len(pcm), SEND_CHUNK_BYTES):
            chunk = pcm[i:i + SEND_CHUNK_BYTES]
            if timer is not None and timer.first_audio_sent is None:
                timer.first_audio_sent = time.perf_counter()
                timer.first_audio_source = source
            ms = len(chunk) / 32          # 16 kHz PCM16: 32 bytes per ms
            self._turn_ms[turn_id] = self._turn_ms.get(turn_id, 0.0) + ms
            entries = self.timeline.get(turn_id)
            if entries:
                entries[-1][1] += ms
            await self.transport.send_audio(turn_id, chunk)

    async def play(self, turn_id: int, segments: list[_Segment], timer=None, kind: str = "reply") -> bool:
        """Send prepared segments in order. Returns True if any audio was sent."""
        async with self.lock:
            sent = False
            try:
                for seg in segments:
                    if seg.pcm is not None:
                        self._begin(turn_id, seg.text, kind)
                        await self._send(turn_id, seg.pcm, timer, "cache")
                        sent = True
                        continue
                    if seg.opening is not None and seg.stream is None:
                        try:
                            seg.stream = await seg.opening
                        except asyncio.CancelledError:
                            task = asyncio.current_task()
                            if task is not None and task.cancelling():
                                raise
                            continue              # cancel() stopped the synthesis (barge-in)
                    if seg.stream is None:
                        logger.error("No TTS available for: %s", logredact.mask_phones(seg.text)[:60])
                        continue
                    if self._wants_breath(turn_id, seg.text):
                        self._begin(turn_id, "", "breath")
                        await self._send(turn_id, random.choice(BREATHS), timer, "breath")
                    self._begin(turn_id, seg.text, kind)
                    got = False
                    async for chunk in seg.stream:
                        await self._send(turn_id, chunk, timer, "live")
                        sent = got = True
                    if (not got and not seg.stream.cancelled and self.backup_tts is not None
                            and getattr(seg.stream, "_engine", None) is not self.backup_tts
                            and getattr(self.backup_tts, "ready", False)):
                        # ElevenLabs answered with silence (its quota ran out on
                        # 1 Oct with no error): say it with the backup voice, and
                        # use the backup for the rest of the call.
                        logger.warning("Live TTS returned no audio; switching this call to the backup voice")
                        self.degraded = True
                        retry = await self._open(seg.text)
                        if retry is not None:
                            seg.stream = retry
                            async for chunk in retry:
                                await self._send(turn_id, chunk, timer, "backup")
                                sent = True
            finally:
                for seg in segments:
                    if seg.opening is not None and not seg.opening.done():
                        seg.opening.cancel()
                    if seg.stream is not None:
                        seg.stream.cancel()
                        if seg.stream in self._live:
                            self._live.remove(seg.stream)
            return sent

    async def speak(self, turn_id: int, text: str, timer=None, kind: str = "reply") -> bool:
        """Play `text` for `turn_id`. Returns True if any audio was sent."""
        return await self.play(turn_id, self.prepare(text), timer, kind)

    async def play_cached(self, turn_id: int, text: str, timer=None, source: str = "filler",
                          kind: str = "filler", wait: bool = False) -> bool:
        """
        Play a pre-rendered phrase, if it is cached. A filler (wait=False) is
        skipped when anything else is playing; a checking line waits its turn.
        """
        pcm = self._cached(speakable(text))
        if pcm is None or (self.lock.locked() and not wait):
            return False
        async with self.lock:
            self._begin(turn_id, text, kind)
            await self._send(turn_id, pcm, timer, source)
        return True

    def _wants_breath(self, turn_id: int, text: str) -> bool:
        """A breath before a long sentence: at most once per turn, never two turns running (R6)."""
        threshold = config.BREATH_BEFORE_WORDS
        if threshold <= 0 or len(text.split()) < threshold or turn_id - self._last_breath_turn <= 1:
            return False
        self._last_breath_turn = turn_id
        return True

    def cached_ms(self, text: str) -> int:
        """Length of a pre-rendered phrase in milliseconds (0 if not cached)."""
        pcm = self._cached(speakable(text))
        return int(len(pcm) / 32) if pcm else 0   # 16 kHz PCM16: 32 bytes per ms

    async def play_silence(self, turn_id: int, ms: int):
        """A deliberate pause inside a turn (keeps the turn's audio in order)."""
        async with self.lock:
            self._begin(turn_id, "", "pause")
            await self._send(turn_id, bytes(2) * (16 * max(0, ms)), None, "pause")

    def discard(self, segments: Optional[list]):
        """Stop synthesising prepared segments that will not be played."""
        for seg in segments or ():
            if seg.opening is not None and not seg.opening.done():
                seg.opening.cancel()
            elif seg.opening is not None and not seg.opening.cancelled() and seg.opening.exception() is None:
                seg.stream = seg.stream or seg.opening.result()
            if seg.stream is not None:
                seg.stream.cancel()
                if seg.stream in self._live:
                    self._live.remove(seg.stream)

    def cancel(self):
        """Barge-in: stop generating everything still in flight."""
        for task in list(self._opening):
            task.cancel()
        self._opening.clear()
        for stream in list(self._live):
            stream.cancel()
        self._live.clear()
