"""
Audio for phone calls (audiosocket.py): resampling at the line's edge and the
clinic sounds mixed into Emma's side of the call.

The rest of Emma (Deepgram, the voice activity meter, the audio clock, the
voice and its prompt cache) works at 16 kHz, as the browser does. A phone
line through AudioSocket is 8 kHz signed-linear, so audio is converted here,
at the edge, with stateful low-pass filters (no clicks between 20 ms frames,
no aliasing):

    Downsampler  16 kHz -> 8 kHz   Emma's voice and sounds, to the caller
    Upsampler     8 kHz -> 16 kHz  the caller, to the recogniser

LineSounds is the server-side twin of static/ambience.js (docs/NORTH_STAR.md;
decisions R4-R6): there is no background bed. Typing plays when Emma writes
something down or checks the diary; an occasional door, chair or footsteps is
heard only while her line is open (she is speaking or typing), like a headset
with a noise gate. On the phone nothing mixes these in for her, so the
playout clock in audiosocket.PhoneTransport asks for one 20 ms frame at a time.

Sounds come from static/ambience/manifest.json (the owner-approved files),
decoded once to 16 kHz mono with ffmpeg into cache/phone_sounds/. Without
ffmpeg, typing is synthesised (filtered clicks, as the browser does) and the
movement sounds are skipped.
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# 20 ms frames need no thread pool, and OpenBLAS's default one reserves memory
# per core (on 6 Oct it failed to start with 2 GB free).
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import numpy as np  # noqa: E402
from scipy import signal  # noqa: E402

logger = logging.getLogger("phone_audio")

RATE = 16000                       # Emma's working rate
FRAME = RATE // 50                 # 20 ms at 16 kHz: 320 samples

# ------------------------------------------------------------------ resampling

# One low-pass for both directions: telephone band ends at 3.4 kHz, and 8 kHz
# audio has nothing above 4 kHz.
_TAPS = signal.firwin(63, 3500, fs=RATE)


def _to_float(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype="<i2").astype(np.float64)


def _to_pcm(x: np.ndarray) -> bytes:
    return np.clip(np.round(x), -32768, 32767).astype("<i2").tobytes()


class Downsampler:
    """16 kHz -> 8 kHz, continuous across chunks of any length."""

    def __init__(self):
        self._zi = np.zeros(len(_TAPS) - 1)
        self._phase = 0                  # index in the next chunk of the first sample to keep

    def process(self, pcm: bytes) -> bytes:
        x = _to_float(pcm)
        if not len(x):
            return b""
        y, self._zi = signal.lfilter(_TAPS, 1.0, x, zi=self._zi)
        out = y[self._phase::2]
        self._phase = (self._phase - len(x)) % 2
        return _to_pcm(out)


class Upsampler:
    """8 kHz -> 16 kHz, continuous across chunks."""

    def __init__(self):
        self._zi = np.zeros(len(_TAPS) - 1)

    def process(self, pcm: bytes) -> bytes:
        x = _to_float(pcm)
        if not len(x):
            return b""
        z = np.zeros(len(x) * 2)
        z[0::2] = x * 2.0                # zero-stuffing halves the level; the filter restores it
        y, self._zi = signal.lfilter(_TAPS, 1.0, z, zi=self._zi)
        return _to_pcm(y)


# ------------------------------------------------------------------ sounds

@dataclass
class Sound:
    samples: np.ndarray                # float, -1..1, 16 kHz mono
    peak: float

    @property
    def seconds(self) -> float:
        return len(self.samples) / RATE


@dataclass
class SoundBank:
    movement: list = field(default_factory=list)
    typing: list = field(default_factory=list)


def _sound(raw: bytes) -> Optional[Sound]:
    samples = np.frombuffer(raw[: len(raw) - len(raw) % 2], dtype="<i2").astype(np.float32) / 32768.0
    if not len(samples):
        return None
    return Sound(samples, float(np.max(np.abs(samples))) or 1e-6)


def load_bank(static_dir: Path, cache_dir: Path, ffmpeg: Optional[str] = None) -> SoundBank:
    """The manifest's sounds at 16 kHz mono, decoding each once (ffmpeg) into cache_dir. Never raises."""
    bank = SoundBank()
    try:
        manifest = json.loads((static_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("phone sounds: no manifest (%s); typing is synthesised", exc)
        return bank
    ffmpeg = ffmpeg or shutil.which("ffmpeg")
    cache_dir.mkdir(parents=True, exist_ok=True)
    for group in ("movement", "typing"):
        for name in manifest.get(group) or ():
            target = cache_dir / (Path(name).stem + ".s16")
            if not target.exists():
                if not ffmpeg:
                    logger.warning("phone sounds: ffmpeg not found, %s skipped", name)
                    continue
                try:
                    subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(static_dir / name), "-ac", "1",
                                    "-ar", str(RATE), "-f", "s16le", str(target)],
                                   check=True, timeout=60, capture_output=True)
                except (OSError, subprocess.SubprocessError) as exc:
                    logger.warning("phone sounds: could not decode %s: %s", name, exc)
                    target.unlink(missing_ok=True)
                    continue
            sound = _sound(target.read_bytes())
            if sound is not None:
                getattr(bank, group).append(sound)
    logger.info("phone sounds: %d movement, %d typing", len(bank.movement), len(bank.typing))
    return bank


def _db(db: float) -> float:
    return 10 ** (db / 20)


def synth_typing(seconds: float, rng: random.Random) -> np.ndarray:
    """Filtered clicks at a typist's rhythm, peaking near -10 dBFS (ambience.js _synthClicks)."""
    n = int(seconds * RATE)
    data = np.zeros(n)
    t = rng.uniform(0.02, 0.1)
    length = int(0.018 * RATE)
    decay = np.exp(-np.arange(length) / (length / 5))
    while t < seconds - 0.05:
        start = int(t * RATE)
        amp = rng.uniform(0.25, 0.6)
        end = min(n, start + length)
        noise = np.array([rng.uniform(-1, 1) for _ in range(end - start)])
        data[start:end] += amp * noise * decay[: end - start]
        t += rng.uniform(0.25, 0.45) if rng.random() < 0.12 else rng.uniform(0.07, 0.17)
    b, a = signal.butter(2, [2800 / 1.9, 2800 * 1.9], btype="bandpass", fs=RATE)
    return signal.lfilter(b, a, data)


# ------------------------------------------------------------------ the line

GATE_OPEN_TAU, GATE_HOLD, GATE_CLOSE_TAU = 0.03, 0.25, 0.12      # seconds (ambience.js GATE)
MOVEMENT_CHANCE, MOVEMENT_COOLDOWN = 0.35, 25.0                  # per line-open, seconds


@dataclass
class _Playing:
    samples: np.ndarray                # already scaled to its level
    start: int                         # line sample index it starts at
    fade_in: int
    fade_out: int
    end: int                           # line sample index it ends at
    stop_at: Optional[int] = None      # a quick fade-out was asked for (stop_typing)
    stop_len: int = 1
    turn: int = 0
    until_speech: bool = False

    def envelope(self, t0: int, n: int) -> np.ndarray:
        t = np.arange(t0, t0 + n)
        rel = t - self.start
        env = np.ones(n)
        if self.fade_in > 0:
            env = np.minimum(env, np.clip(rel / self.fade_in, 0, 1))
        if self.fade_out > 0:
            env = np.minimum(env, np.clip((self.end - t) / self.fade_out, 0, 1))
        if self.stop_at is not None:
            env = np.minimum(env, np.clip(1 - (t - self.stop_at) / self.stop_len, 0, 1))
        env[(rel < 0) | (t >= self.end)] = 0
        return env

    def chunk(self, t0: int, n: int) -> np.ndarray:
        rel = np.arange(t0, t0 + n) - self.start
        valid = (rel >= 0) & (rel < len(self.samples))
        out = np.zeros(n)
        out[valid] = self.samples[rel[valid]]
        return out * self.envelope(t0, n)

    def finished(self, t: int) -> bool:
        return t >= self.end or (self.stop_at is not None and t >= self.stop_at + self.stop_len)


class LineSounds:
    """
    Emma's side of the line, one 20 ms frame at a time (see the module notes).

        sounds.speaking(True)                     her audio for a turn started
        sounds.speech_started(turn)               ... and note-taking typing for it stops
        sounds.typing(after_ms, duration_ms, turn=, until_speech=)
        sounds.stop_typing()
        frame = sounds.frame()                    float samples, or None when silent
    """

    def __init__(self, bank: Optional[SoundBank] = None, event_db: float = -34.0,
                 rng: Optional[random.Random] = None, enabled: bool = True):
        self.enabled = enabled          # AMBIENCE_ENABLED=false: no typing, no movement (as in the browser)
        self.bank = bank or SoundBank()
        self.event_db = event_db
        self.rng = rng or random.Random()
        self.t = 0                      # samples since the call started
        self.speaking_now = False
        self.typist: Optional[_Playing] = None
        self.fading: list = []          # typing being faded out
        self.movement: list = []
        self.pending_movement: Optional[int] = None
        self.last_movement = -10 ** 12
        self.line_open = False
        self.gate = 0.0
        self.gate_target = 0.0
        self.close_at: Optional[int] = None

    # ---- events
    def speaking(self, on: bool):
        self.speaking_now = on

    def speech_started(self, turn: int):
        if self.typist is not None and self.typist.until_speech and turn >= self.typist.turn:
            self.stop_typing(160)
        self.speaking(True)

    def typing(self, after_ms: float = 0, duration_ms: float = 1500, *, turn: int = 0, until_speech: bool = False):
        self.stop_typing(60)
        if not self.enabled:
            return
        start = self.t + int(max(0.0, after_ms) * RATE / 1000)
        seconds = max(0.3, duration_ms / 1000)
        if self.bank.typing:
            item = self.rng.choice(self.bank.typing)
            level = _db(self.event_db + 10) / item.peak       # the keyboard is right by the phone
            offset = int(self.rng.uniform(0, max(0.0, item.seconds - seconds)) * RATE)
            samples = item.samples[offset: offset + int(seconds * RATE)] * level
        else:
            samples = synth_typing(seconds, self.rng) * _db(self.event_db + 20)
        self.typist = _Playing(samples, start, int(0.06 * RATE), int(0.12 * RATE), start + len(samples),
                               turn=turn, until_speech=until_speech)

    def stop_typing(self, fade_ms: float = 120):
        typist, self.typist = self.typist, None
        if typist is None:
            return
        typist.stop_at, typist.stop_len = self.t, max(1, int(fade_ms * RATE / 1000))
        self.fading.append(typist)

    # ---- the gate
    def _typing_now(self) -> bool:
        return self.typist is not None and self.typist.start <= self.t < self.typist.end

    def _active(self) -> bool:
        return self.speaking_now or self._typing_now()

    def _refresh(self):
        if self._active():
            self.close_at = None
            if not self.line_open:
                self.line_open = True
                self.gate_target = 1.0
                self._maybe_movement()
        elif self.line_open:
            if self.close_at is None:
                self.close_at = self.t + int(GATE_HOLD * RATE)
            elif self.t >= self.close_at:
                self.close_at = None
                self.line_open = False
                self.gate_target = 0.0

    def _maybe_movement(self):
        if not self.enabled or not self.bank.movement or (self.t - self.last_movement) / RATE < MOVEMENT_COOLDOWN \
                or self.rng.random() > MOVEMENT_CHANCE:
            return
        self.last_movement = self.t
        self.pending_movement = self.t + int(self.rng.uniform(0.4, 2.0) * RATE)

    def _play_movement(self):
        item = self.rng.choice(self.bank.movement)
        long = item.seconds > 8
        seconds = self.rng.uniform(3, 6) if long else item.seconds
        offset = int(self.rng.uniform(0, item.seconds - seconds) * RATE) if long else 0
        level = _db(self.event_db + self.rng.uniform(-5, 0)) / item.peak
        samples = item.samples[offset: offset + int(seconds * RATE)] * level
        edge = int(min(0.3, seconds / 4) * RATE)
        self.movement.append(_Playing(samples, self.t, edge, edge, self.t + len(samples)))

    # ---- output
    def frame(self, n: int = FRAME) -> Optional[np.ndarray]:
        self._refresh()
        if self.pending_movement is not None and self.t >= self.pending_movement:
            self.pending_movement = None
            if self.line_open:
                self._play_movement()
        tau = (GATE_OPEN_TAU if self.gate_target > self.gate else GATE_CLOSE_TAU) * RATE
        gate = self.gate_target + (self.gate - self.gate_target) * np.exp(-np.arange(1, n + 1) / tau)
        self.gate = float(gate[-1])
        out = sum(m.chunk(self.t, n) for m in self.movement) * gate if self.movement else None
        for typing in [x for x in (self.typist, *self.fading) if x is not None]:
            chunk = typing.chunk(self.t, n)
            out = chunk if out is None else out + chunk
        self.t += n
        self.movement = [m for m in self.movement if not m.finished(self.t)]
        self.fading = [f for f in self.fading if not f.finished(self.t)]
        if self.typist is not None and self.typist.finished(self.t):
            self.typist = None
        if out is None or not np.any(out):
            return None
        return out * 32768.0


def mix(voice: Optional[bytes], sounds: Optional[np.ndarray], n: int = FRAME) -> bytes:
    """One frame of Emma's side at 16 kHz: her voice (or silence) plus the line's sounds."""
    x = np.zeros(n)
    if voice:
        v = _to_float(voice)
        x[: len(v)] += v[:n]
    if sounds is not None:
        x[: len(sounds)] += sounds[:n]
    return _to_pcm(x)


def tone_power(pcm: bytes, freq: float, rate: int) -> float:
    """Relative power of `freq` in pcm (tests: is a 1 kHz tone still a 1 kHz tone after resampling?)."""
    x = _to_float(pcm)
    if not len(x):
        return 0.0
    k = np.arange(len(x))
    c, s = np.cos(2 * math.pi * freq * k / rate), np.sin(2 * math.pi * freq * k / rate)
    return float((np.dot(x, c) ** 2 + np.dot(x, s) ** 2) / (np.dot(x, x) * len(x) + 1e-9))
