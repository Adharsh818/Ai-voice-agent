"""
Fakes for the real-time tests (no network): transport, prompt cache, TTS, STT
and engine stand-ins, plus a helper that shrinks the turn detector's waits so
hold-and-merge tests run in milliseconds.
"""

import asyncio
import contextlib
import time

import ai_engine
import turn_detector
from tts_elevenlabs import PCMStream

MS = 32   # bytes of 16 kHz PCM16 per millisecond


class FakeTransport:
    """Records everything the call session sends; playback is reported by the test."""

    def __init__(self):
        self.events: list[dict] = []
        self.audio: dict[int, int] = {}      # turn -> bytes sent
        self.flushed: list[int] = []
        self.closed = False

    async def send_audio(self, turn_id, pcm):
        self.audio[turn_id] = self.audio.get(turn_id, 0) + len(pcm)

    async def send_event(self, event):
        self.events.append(event)

    async def flush(self, turn_id):
        self.flushed.append(turn_id)

    async def close(self):
        self.closed = True

    def of_type(self, kind):
        return [e for e in self.events if e.get("type") == kind]

    def emma_captions(self):
        return [e["text"] for e in self.events if e.get("type") == "caption" and e.get("who") == "emma"]


class PlayingTransport(FakeTransport):
    """
    Like the talk page: reports playback "started" when a turn's first audio
    arrives, "ended" once its audio has played (after audio_done), and
    "interrupted" with played_ms on a flush. Reports arrive asynchronously,
    as they do over the WebSocket. auto=False leaves "started" to the test.
    """

    def __init__(self, session=None, auto=True):
        super().__init__()
        self.session = session
        self.auto = auto
        self.started: dict[int, float] = {}
        self.finished: set[int] = set()
        self._tasks: set = set()

    def _later(self, coro):
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def report(self, turn_id, event, played_ms=None):
        msg = {"type": "playback", "turn": turn_id, "event": event}
        if played_ms is not None:
            msg["played_ms"] = played_ms
        if event == "started":
            self.started[turn_id] = time.perf_counter()
        else:
            self.finished.add(turn_id)
        await self.session.on_control(msg)

    async def send_audio(self, turn_id, pcm):
        first = turn_id not in self.audio
        await super().send_audio(turn_id, pcm)
        if first and self.auto:
            self.started[turn_id] = time.perf_counter()
            self._later(self.report(turn_id, "started"))

    async def send_event(self, event):
        await super().send_event(event)
        if event.get("type") == "turn" and event.get("phase") == "audio_done":
            self._later(self._play_out(event["turn"]))

    async def _play_out(self, turn_id):
        await asyncio.sleep(0)
        began = self.started.get(turn_id)
        if began is None or turn_id in self.finished:
            return
        length_s = self.audio.get(turn_id, 0) / MS / 1000
        await asyncio.sleep(max(0.0, began + length_s - time.perf_counter()))
        if turn_id not in self.finished:
            await self.report(turn_id, "ended", round(length_s * 1000))

    async def flush(self, turn_id):
        await super().flush(turn_id)
        began = self.started.get(turn_id)
        if began is not None and turn_id not in self.finished:
            self.finished.add(turn_id)
            played = round((time.perf_counter() - began) * 1000)
            self._later(self.report(turn_id, "interrupted", played))

    async def close(self):
        await super().close()
        for task in list(self._tasks):
            if task is not asyncio.current_task():
                task.cancel()


class FakeCache:
    """A prompt cache holding a few fixed phrases (each `ms` long)."""

    def __init__(self, phrases=(), ms=400):
        self.pcm = {p: bytes(MS * ms) for p in phrases}

    def get(self, text):
        return self.pcm.get(text)


class FakeTTS:
    """Live TTS: every sentence is `ms` long; records when each stream was opened."""

    def __init__(self, ms=600, delay=0.0):
        self.ms, self.delay = ms, delay
        self.opened: list[tuple[str, float]] = []

    async def open_stream(self, text):
        self.opened.append((text, time.perf_counter()))
        stream = PCMStream()

        async def pump():
            if self.delay:
                await asyncio.sleep(self.delay)
            stream.feed(bytes(MS * self.ms))
            stream.finish()

        task = asyncio.ensure_future(pump())
        stream._on_cancel = task.cancel
        return stream


class FakeSTT:
    def __init__(self, **callbacks):
        self.callbacks = callbacks
        self.keyterms: list[str] = []
        self.connected = False
        self.audio = 0

    def add_keyterms(self, terms):
        self.keyterms += list(terms)

    async def connect(self, **_):
        self.connected = True

    async def send_audio(self, pcm):
        self.audio += len(pcm)

    async def close(self):
        self.connected = False


def result(text, tier=0, **extra):
    """A TurnResult carrying the redesigned engine's extra fields when given."""
    out = ai_engine.TurnResult(text, tier=tier)
    for key, value in extra.items():
        setattr(out, key, value)
    return out


_MISSING = object()


@contextlib.contextmanager
def patched(obj, **values):
    """Temporarily set attributes on a module or object (added ones are removed after)."""
    saved = {k: getattr(obj, k, _MISSING) for k in values}
    for k, v in values.items():
        setattr(obj, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is _MISSING:
                delattr(obj, k)
            else:
                setattr(obj, k, v)


@contextlib.contextmanager
def fast_holds(scale=0.05):
    """Shrink the turn detector's silence targets (1600 ms -> 80 ms at 0.05)."""
    saved = dict(turn_detector.SILENCE_MS)
    saved_max, saved_grace = turn_detector.HOLD_MAX_MS, turn_detector.ACTIVITY_GRACE_MS
    for kind in turn_detector.SILENCE_MS:
        turn_detector.SILENCE_MS[kind] = int(saved[kind] * scale)
    turn_detector.HOLD_MAX_MS = int(saved_max * scale)
    turn_detector.ACTIVITY_GRACE_MS = int(saved_grace * scale)
    try:
        yield
    finally:
        turn_detector.SILENCE_MS.update(saved)
        turn_detector.HOLD_MAX_MS, turn_detector.ACTIVITY_GRACE_MS = saved_max, saved_grace


@contextlib.contextmanager
def engine(fn):
    """Swap ai_engine.async_process_turn for a fake."""
    with patched(ai_engine, async_process_turn=fn):
        yield


async def settle(seconds=0.0):
    """Let spawned tasks run."""
    await asyncio.sleep(seconds)
    for _ in range(5):
        await asyncio.sleep(0)


async def wait_until(predicate, timeout=3.0, step=0.01):
    """Poll until predicate() is true; False on timeout."""
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        await asyncio.sleep(step)
    return bool(predicate())


class FakeRecorder:
    """Stands in for recording.CallRecorder: keeps (role, text, meta) in memory."""

    def __init__(self):
        self.turns: list[tuple] = []
        self.ended = None

    def start(self):
        pass

    def turn(self, role, text, meta=None):
        self.turns.append((role, text, meta or {}))

    def end(self, outcome, keep_transcript=True, **_):
        self.ended = (outcome, keep_transcript)


def make_session(transport=None, cache=(), tts=None, keyterms=None, cache_ms=400):
    """A CallSession on fakes: no network, no database, no transcript writes."""
    from call_session import CallSession, Services

    transport = transport if transport is not None else FakeTransport()
    services = Services(stt_factory=lambda **cb: FakeSTT(**cb), tts=tts,
                        cache=FakeCache(cache, ms=cache_ms) if cache else None,
                        keyterms=keyterms if keyterms is not None else (lambda: []))
    # These tests drive the call session on fakes of the engine and set the
    # 12-step machine's state (s.step) directly, so the session starts on it
    # even when the suite runs with R2_ENGINE on (a patched new_session wins).
    import config
    saved, config.R2_ENGINE = config.R2_ENGINE, False
    try:
        session = CallSession(transport, services, call_id="test")
    finally:
        config.R2_ENGINE = saved
    session.recorder = FakeRecorder()
    if isinstance(transport, PlayingTransport):
        transport.session = session
    return session, transport
