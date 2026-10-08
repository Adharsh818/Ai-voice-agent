"""
Is the caller making sound right now? A small energy detector on the audio
Emma receives (16 kHz PCM16, 20 ms frames).

The 6 Oct replay of the owner's recorded test lines (tools/replay.py) showed
why transcript timing alone ends turns badly: Deepgram's words arrive 0.5-1 s
after they were spoken and in bursts, so "the words stopped changing" fires in
the middle of a sentence ("Can I move my cleaning" | "to Thursday afternoon"),
while the real end of a sentence is only noticed late. The microphone level
answers "still talking?" at once and to the frame.

VoiceActivity.feed(pcm) -> bool    this frame is voiced
VoiceActivity.quiet_for(now) -> s  seconds since the last voiced frame

The threshold follows the room: it sits a fixed ratio above a noise floor that
drops at once to quieter frames and rises slowly with steady noise (a fan,
traffic), so background noise doesn't count as talking for long.
"""

import array
import math
import sys
import time
from typing import Optional

ABS_MIN_RMS = 250.0          # about -42 dBFS: quieter than this is never speech
FLOOR_RATIO = 3.0            # speech is at least ~10 dB above the room's noise floor
FLOOR_RISE = 0.02            # per quiet frame: the floor follows the room within a second or two
FLOOR_RISE_LOUD = 0.001      # per loud frame: a fan that starts up stops counting as talk in ~20 s,
                             # while a few seconds of speech barely move the floor
START_FRAMES = 2             # 40 ms of sound before it counts (a click or a tap is not speech)
HANG_FRAMES = 4              # keep "voiced" through 80 ms dips between syllables


def frame_rms(pcm: bytes) -> float:
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


class VoiceActivity:
    def __init__(self, now=time.perf_counter):
        self.now = now
        self.floor: Optional[float] = None
        self.last_voice: Optional[float] = None
        self._run = 0
        self._hang = 0

    @property
    def threshold(self) -> float:
        return max(ABS_MIN_RMS, (self.floor or 0.0) * FLOOR_RATIO)

    def feed(self, pcm: bytes) -> bool:
        rms = frame_rms(pcm)
        loud = rms > self.threshold
        if self.floor is None or rms < self.floor:
            self.floor = rms
        else:
            self.floor += (rms - self.floor) * (FLOOR_RISE_LOUD if loud else FLOOR_RISE)
        self._run = self._run + 1 if loud else 0
        if self._run >= START_FRAMES:
            self._hang = HANG_FRAMES
        elif self._hang:
            self._hang -= 1
        voiced = self._run >= START_FRAMES or self._hang > 0
        if voiced:
            self.last_voice = self.now()
        return voiced

    def quiet_for(self, now: Optional[float] = None) -> float:
        """Seconds since the caller last made a sound (a large number before they ever did)."""
        if self.last_voice is None:
            return 1e9
        return (now if now is not None else self.now()) - self.last_voice
