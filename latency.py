"""
Per-turn latency instrumentation.

The number that matters is *perceived* latency: from the moment the caller
stopped speaking to the moment Emma's voice is audible. Both ends are measured
rather than guessed:

- AudioClock maps Deepgram's word timestamps (seconds into the audio stream)
  back to the wall-clock time that audio reached the server.
- The browser reports when a turn's first sample actually starts playing.

Every turn is appended to logs/turns.jsonl and kept in memory for /metrics.
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
    user_end: Optional[float] = None        # caller stopped speaking (wall clock)
    committed: Optional[float] = None       # turn handed to the engine
    nlu_ms: Optional[float] = None
    reply_ready: Optional[float] = None     # reply text available
    first_audio_sent: Optional[float] = None
    first_audio_source: Optional[str] = None  # cache | live | filler
    audible: Optional[float] = None         # browser reported playback start
    barge_in: bool = False
    barge_in_ms: Optional[float] = None
    filler: bool = False
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
            "endpoint_ms": self._ms(self.user_end, self.committed),
            "nlu_ms": None if self.nlu_ms is None else round(self.nlu_ms, 1),
            "engine_ms": self._ms(self.committed, self.reply_ready),
            "first_audio_ms": self._ms(self.user_end, self.first_audio_sent),
            "first_audio_source": self.first_audio_source,
            "perceived_ms": self._ms(self.user_end, self.audible),
            "filler": self.filler,
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
    def __init__(self, directory: Optional[str], keep: int = 1000):
        self.path = os.path.join(directory, "turns.jsonl") if directory else None
        self.records: deque = deque(maxlen=keep)
        if directory:
            os.makedirs(directory, exist_ok=True)

    def add(self, timer: TurnTimer) -> Optional[dict]:
        if timer.logged:
            return None
        timer.logged = True
        record = timer.to_record()
        self.records.append(record)
        if self.path:
            try:
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
        def stats(rows):
            perceived = [r["perceived_ms"] for r in rows if r["perceived_ms"] is not None]
            barge = [r["barge_in_ms"] for r in rows if r["barge_in_ms"] is not None]
            return {
                "turns": len(rows),
                "perceived_p50_ms": _percentile(perceived, 50),
                "perceived_p95_ms": _percentile(perceived, 95),
                "barge_in_p50_ms": _percentile(barge, 50),
            }

        rows = [r for r in self.records if r["tier"] is not None and r["tier"] >= 0]
        return {
            "all": stats(rows),
            "tier0": stats([r for r in rows if r["tier"] == 0]),
            "tier1": stats([r for r in rows if r["tier"] == 1]),
        }
