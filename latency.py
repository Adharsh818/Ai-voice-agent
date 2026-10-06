"""
Per-turn latency instrumentation.

The number that matters is *perceived* latency: from the moment the caller
stopped speaking to the moment Emma's voice is audible. Both ends are measured
rather than guessed:

- AudioClock maps Deepgram's word timestamps (seconds into the audio stream)
  back to the wall-clock time that audio reached the server.
- The browser reports when a turn's first sample actually starts playing.

Every turn is appended to logs/turns.jsonl and kept in memory for /metrics.
The summary splits the wait into its parts so a change can be measured before
and after: end-of-turn detection (endpoint_ms, by detection event and by the
turn detector's verdict), first audio sent, the deliberate typing pause
(pause_ms), and whether the first sentence was streamed before the engine
finished.
"""

import bisect
import json
import logging
import os
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


class AudioClock:
    """Maps a position in the caller's audio stream to the wall-clock time it arrived."""

    def __init__(self, sample_rate: int = 16000, bytes_per_sample: int = 2, horizon: int = 3000):
        self.bytes_per_second = sample_rate * bytes_per_sample
        self._bytes = 0
        self._secs: deque = deque(maxlen=horizon)
        self._walls: deque = deque(maxlen=horizon)

    def add(self, nbytes: int, wall: Optional[float] = None):
        self._bytes += nbytes
        self._secs.append(self._bytes / self.bytes_per_second)
        self._walls.append(time.perf_counter() if wall is None else wall)

    @property
    def seconds(self) -> float:
        """How much caller audio has arrived so far."""
        return self._bytes / self.bytes_per_second

    def wall_at(self, audio_sec: Optional[float]) -> Optional[float]:
        """Wall time at which audio position `audio_sec` had arrived, or None."""
        if audio_sec is None or not self._secs:
            return None
        secs = list(self._secs)
        i = bisect.bisect_left(secs, audio_sec)
        if i >= len(secs):
            return self._walls[-1]
        # The chunk ending at secs[i] contains audio_sec; interpolate within it.
        return self._walls[i] - (secs[i] - audio_sec)


@dataclass
class TurnTimer:
    call_id: str
    turn: int
    user_text: str = ""
    tier: Optional[int] = None
    step_before: Optional[int] = None
    step_after: Optional[int] = None
    goal_before: Optional[str] = None       # the redesigned engine's goals (step_* for the old one)
    goal_after: Optional[str] = None
    detect: Optional[str] = None            # turn detector verdict, e.g. "complete:yes_no"
    user_end: Optional[float] = None        # caller stopped speaking (wall clock)
    # How the end of the caller's turn was detected. endpoint_ms (user_end ->
    # committed) splits into stt_ms (user_end -> the STT's end-of-utterance
    # event: endpointing silence + recognizer delay) and hold_ms (that event ->
    # committed: our own wait for a trailing word or the rest of a phone number).
    endpoint_source: Optional[str] = None   # speech_final | utterance_end | text
    stt_event: Optional[float] = None       # the STT's end-of-utterance event arrived
    committed: Optional[float] = None       # turn handed to the engine
    nlu_ms: Optional[float] = None
    reply_ready: Optional[float] = None     # reply text available
    first_audio_sent: Optional[float] = None
    first_audio_source: Optional[str] = None  # cache | live | filler
    audible: Optional[float] = None         # browser reported playback start
    barge_in: bool = False
    barge_in_ms: Optional[float] = None
    filler: bool = False
    pause_ms: Optional[float] = None        # deliberate pause (typing beat), part of perceived_ms
    streamed: bool = False                  # first sentence spoken before the engine finished
    logged: bool = field(default=False, repr=False)

    def _ms(self, a, b):
        return None if a is None or b is None else round((b - a) * 1000, 1)

    def to_record(self) -> dict:
        return {
            "ts": round(time.time(), 3),
            "call_id": self.call_id,
            "turn": self.turn,
            "tier": self.tier,
            "step_before": self.step_before,
            "step_after": self.step_after,
            "goal_before": self.goal_before,
            "goal_after": self.goal_after,
            "endpoint_ms": self._ms(self.user_end, self.committed),
            "endpoint_source": self.endpoint_source,
            "detect": self.detect,
            "stt_ms": self._ms(self.user_end, self.stt_event),
            "hold_ms": self._ms(self.stt_event, self.committed),
            "nlu_ms": None if self.nlu_ms is None else round(self.nlu_ms, 1),
            "engine_ms": self._ms(self.committed, self.reply_ready),
            "first_audio_ms": self._ms(self.user_end, self.first_audio_sent),
            "first_audio_source": self.first_audio_source,
            "perceived_ms": self._ms(self.user_end, self.audible),
            "filler": self.filler,
            "pause_ms": self.pause_ms,
            "streamed": self.streamed,
            "barge_in": self.barge_in,
            "barge_in_ms": None if self.barge_in_ms is None else round(self.barge_in_ms, 1),
        }


def _percentile(values, p):
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (k - lo), 1)


class LatencyLog:
    def __init__(self, directory: Optional[str], keep: int = 1000, max_bytes: Optional[int] = None,
                 backups: Optional[int] = None):
        self.path = os.path.join(directory, "turns.jsonl") if directory else None
        self.records: deque = deque(maxlen=keep)
        # Rotation (phase E): turns.jsonl -> turns.jsonl.1 ... .N once it passes max_bytes.
        import config
        self.max_bytes = int(config.LOG_ROTATE_MB * 1024 * 1024) if max_bytes is None else max_bytes
        self.backups = config.LOG_KEEP if backups is None else backups
        if directory:
            os.makedirs(directory, exist_ok=True)

    def _rotate(self):
        """Roll turns.jsonl over when it has grown past max_bytes (newest backup is .1)."""
        try:
            if self.max_bytes <= 0 or os.path.getsize(self.path) < self.max_bytes:
                return
        except OSError:
            return
        for i in range(self.backups - 1, 0, -1):
            older = f"{self.path}.{i}"
            if os.path.exists(older):
                os.replace(older, f"{self.path}.{i + 1}")
        os.replace(self.path, f"{self.path}.1")
        stale = f"{self.path}.{self.backups + 1}"
        if os.path.exists(stale):
            os.remove(stale)

    def add(self, timer: TurnTimer) -> Optional[dict]:
        if timer.logged:
            return None
        timer.logged = True
        record = timer.to_record()
        self.records.append(record)
        if self.path:
            try:
                self._rotate()
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record) + "\n")
            except OSError as exc:
                logger.warning("Could not write latency log: %s", exc)
        logger.info(
            "[%s] turn %d tier=%s perceived=%sms first_audio=%sms (%s) nlu=%sms",
            record["call_id"], record["turn"], record["tier"], record["perceived_ms"],
            record["first_audio_ms"], record["first_audio_source"], record["nlu_ms"],
        )
        return record

    def summary(self) -> dict:
        def values(rows, key):
            return [r[key] for r in rows if r.get(key) is not None]

        def stats(rows):
            perceived = values(rows, "perceived_ms")
            first_audio = values(rows, "first_audio_ms")
            return {
                "turns": len(rows),
                "perceived_p50_ms": _percentile(perceived, 50),
                "perceived_p95_ms": _percentile(perceived, 95),
                "first_audio_p50_ms": _percentile(first_audio, 50),
                "first_audio_p95_ms": _percentile(first_audio, 95),
                "pause_p50_ms": _percentile(values(rows, "pause_ms"), 50),
                "paused_turns": sum(1 for r in rows if r.get("pause_ms")),
                "streamed_turns": sum(1 for r in rows if r.get("streamed")),
                "barge_in_p50_ms": _percentile(values(rows, "barge_in_ms"), 50),
            }

        def detection(rows):
            """End-of-turn wait by the turn detector's verdict (complete, unfinished, ...)."""
            out = {}
            kinds = sorted({(r.get("detect") or "").split(":")[0] for r in rows if r.get("detect")})
            for kind in kinds:
                subset = [r for r in rows if (r.get("detect") or "").split(":")[0] == kind]
                out[kind] = {"turns": len(subset),
                             "endpoint_p50_ms": _percentile(values(subset, "endpoint_ms"), 50)}
            return out

        def endpointing(rows):
            """Where the wait for the end of the caller's speech goes, per detection event."""
            out = {}
            for source in sorted({r.get("endpoint_source") for r in rows if r.get("endpoint_source")}):
                subset = [r for r in rows if r.get("endpoint_source") == source]
                out[source] = {
                    "turns": len(subset),
                    **{f"{k}_p50_ms": _percentile([r[k] for r in subset if r.get(k) is not None], 50)
                       for k in ("endpoint_ms", "stt_ms", "hold_ms")},
                }
            return out

        rows = [r for r in self.records if r["tier"] is not None and r["tier"] >= 0]
        return {
            "all": stats(rows),
            "tier0": stats([r for r in rows if r["tier"] == 0]),
            "tier1": stats([r for r in rows if r["tier"] == 1]),
            "endpointing": endpointing(rows),
            "detection": detection(rows),
        }
