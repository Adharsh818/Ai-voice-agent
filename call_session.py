"""
One live call with Emma, independent of how audio arrives.

The browser (/ws/voice) plugs in today and the Asterisk AudioSocket server
plugs in later through the same Transport protocol:

    async send_audio(turn_id, pcm)   Emma's PCM for a turn
    async send_event(dict)           state / caption / turn / metrics / bye
    async flush(turn_id)             stop playback of that turn now (barge-in)
    async close()

Turn lifecycle
--------------
Deepgram ends an utterance -> TurnDetector may hold briefly (half a phone
number, a trailing "and...") -> a turn task runs ai_engine.async_process_turn
and then speaks the reply. A turn moves through three phases:

    "nlu"     still understanding; a newer utterance cancels it and the two
              texts are merged into one turn (nothing has been mutated yet)
    "commit"  the state machine is running; it always completes
    "speak"   audio is streaming; a confirmed barge-in cancels it

Barge-in: while Emma is audible, a Deepgram transcript from the caller that is
not an echo of Emma's own words stops generation, flushes the caller's player
and trims Emma's history entry to what was actually heard. The caller's words
are never discarded; they become the next turn.
"""

import asyncio
import logging
import random
import re
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

import ai_engine
import config
import phrases
import tier0
from capture import CallCapture
from latency import AudioClock, LatencyLog, TurnTimer
from speech import Speaker, PromptCache

logger = logging.getLogger("call")

HOLD_PARTIAL_PHONE_MS = 1200
HOLD_TRAILING_WORD_MS = 600
HOLD_MAX_MS = 3000
FINAL_PLAYBACK_TIMEOUT_S = 20
# Approximate speaking rate, used to estimate how much of a reply was heard.
CHARS_PER_SECOND = 15

_TRAILING_RE = re.compile(r"\b(and|um+|uh+|so|but|because|is|my number is|it's)\s*[.,]?$")
_URGENT_WORDS = {"no", "wait", "stop", "sorry", "hello", "hey", "hold", "excuse"}


@dataclass
class Services:
    """Shared, warm resources handed to every call by the server."""
    stt_factory: Callable          # (**callbacks) -> DeepgramSTT-like
    tts: object = None             # per-call live TTS (multi-context WebSocket)
    fallback_tts: object = None    # shared HTTP TTS
    cache: Optional[PromptCache] = None
    latency: Optional[LatencyLog] = None


class CallSession:
    def __init__(self, transport, services: Services, call_id: Optional[str] = None,
                 listen_only: bool = False):
        self.t = transport
        # Listen-only (dev capture): transcribe and record what the caller says,
        # but never greet or reply. Used to record the STT comparison set.
        self.listen_only = listen_only
        self.sv = services
        self.call_id = call_id or uuid.uuid4().hex[:8]
        self.s = ai_engine.SessionState()
        self.clock = AudioClock(16000)
        self.speaker = Speaker(transport, services.cache, services.tts, services.fallback_tts)
        self.stt = None
        self.capture: Optional[CallCapture] = None
        if config.DEV_CAPTURE_AUDIO:
            try:
                self.capture = CallCapture(config.CAPTURE_DIR, self.call_id)
            except OSError as exc:
                logger.warning("[%s] audio capture disabled: %s", self.call_id, exc)

        self.turn_id = 0
        self._turn_task: Optional[asyncio.Task] = None
        self._turn_phase: Optional[str] = None
        self._turn_text = ""
        self._timers: dict[int, TurnTimer] = {}

        self._speaking_turn: Optional[int] = None   # turn whose audio may be audible
        self._emma_text = ""
        self._playback_done: dict[int, asyncio.Event] = {}
        self._user_speaking = False
        self._speech_started_at: Optional[float] = None

        self._held_text = ""
        self._held_end: Optional[float] = None
        self._held_meta: tuple = ("text", None)   # (endpoint source, STT event time)
        self._hold_task: Optional[asyncio.Task] = None

        self._turns_since_filler = 99
        self._tasks: set[asyncio.Task] = set()
        self.closed = False

    # ------------------------------------------------------------------ setup
    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def start(self):
        self.stt = self.sv.stt_factory(
            on_transcript=self._on_transcript,
            on_utterance_end=self._on_utterance_end,
            on_speech_started=self._on_speech_started,
        )
        # The greeting plays from the prompt cache while both sockets open.
        if not self.listen_only:
            self._start_turn_task(self._greet())
        connects = [self.stt.connect(sample_rate=16000)]
        if self.sv.tts is not None and hasattr(self.sv.tts, "connect"):
            connects.append(self.sv.tts.connect())
        results = await asyncio.gather(*connects, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.warning("[%s] connect failed: %s", self.call_id, result)
        logger.info("[%s] call started", self.call_id)

    def _start_turn_task(self, coro):
        self._turn_task = self._spawn(coro)

    async def _greet(self):
        self.turn_id += 1
        tid = self.turn_id
        timer = self._timer(tid, "")
        self._turn_phase = "commit"
        result = await ai_engine.async_process_turn("", self.s)
        timer.tier, timer.reply_ready = result.tier, time.perf_counter()
        self._maybe_log(timer)
        self._turn_phase = "speak"
        await self._speak(tid, result.text, timer)

    # ------------------------------------------------------------ audio / control in
    async def on_audio(self, pcm: bytes):
        if self.closed or not pcm:
            return
        self.clock.add(len(pcm))
        if self.capture is not None:
            self.capture.audio(pcm)
        if self.stt is not None:
            await self.stt.send_audio(pcm)

    async def on_control(self, msg: dict):
        kind = msg.get("type")
        if kind == "playback":
            await self._on_playback(msg)
        elif kind == "text":
            text = (msg.get("text") or "").strip()
            if text:
                await self._send({"type": "caption", "who": "user", "text": text, "final": True})
                now = time.perf_counter()
                await self._accept_utterance(text, now, hold=False, source="text", received=now)
        elif kind == "end":
            await self.close()
        elif kind == "hello":
            logger.info("[%s] client protocol v%s", self.call_id, msg.get("v"))
        elif kind == "vad":
            # The browser ducks Emma locally; the server waits for real words.
            pass

    async def _on_playback(self, msg: dict):
        tid = msg.get("turn")
        event = msg.get("event")
        timer = self._timers.get(tid)
        if event == "started":
            if timer is not None and timer.audible is None:
                timer.audible = time.perf_counter()
                self._maybe_log(timer)
        elif event in ("ended", "interrupted"):
            if event == "interrupted":
                self._trim_history(tid, msg.get("played_ms"))
            if tid == self._speaking_turn:
                self._speaking_turn = None
                if not self.closed:
                    await self._send({"type": "state", "state": "listening"})
            done = self._playback_done.get(tid)
            if done:
                done.set()

    # ------------------------------------------------------------------ STT callbacks
    async def _on_speech_started(self, _timestamp):
        self._user_speaking = True
        self._speech_started_at = time.perf_counter()

    async def _on_transcript(self, text: str, is_final: bool):
        self._user_speaking = True
        await self._send({"type": "caption", "who": "user", "text": text, "final": is_final})
        if self._speaking_turn is not None and config.BARGE_IN_ENABLED and self._is_barge_in(text):
            await self.interrupt("caller speech")

    async def _on_utterance_end(self, text: str, end_sec, source: str = "speech_final"):
        received = time.perf_counter()
        self._user_speaking = False
        if self.capture is not None:
            self.capture.utterance(text, end_sec, source)
        if self._speaking_turn is not None and self._is_echo(text):
            logger.info("[%s] ignored echo of Emma's speech: %r", self.call_id, text)
            return
        end_wall = self.clock.wall_at(end_sec) or received
        await self._accept_utterance(text, end_wall, source=source, received=received)

    # ------------------------------------------------------------------ turn detection
    def _hold_ms(self, text: str) -> int:
        """How long to wait for more speech before treating `text` as a full turn."""
        if self.s.step == 4 and not self.s.temp_phone:
            digits = len(tier0.normalize_spoken_digits(text))
            if 0 < digits < 10:
                return HOLD_PARTIAL_PHONE_MS
        if _TRAILING_RE.search(text.lower().strip()):
            return HOLD_TRAILING_WORD_MS
        return 0

    async def _accept_utterance(self, text: str, end_wall: float, hold: bool = True,
                                source: str = "text", received: Optional[float] = None):
        if self.closed:
            return
        received = received or time.perf_counter()
        if self._hold_task is not None:
            self._hold_task.cancel()
            self._hold_task = None
            text = f"{self._held_text} {text}".strip()
            self._held_text = ""
        wait = self._hold_ms(text) if hold else 0
        if wait:
            self._held_text, self._held_end = text, end_wall
            self._held_meta = (source, received)
            self._hold_task = self._spawn(self._release_after(wait))
            return
        await self._start_turn(text, end_wall, source, received)

    async def _release_after(self, wait_ms: int):
        waited = 0
        try:
            while waited < wait_ms or (self._user_speaking and waited < HOLD_MAX_MS):
                await asyncio.sleep(0.1)
                waited += 100
        except asyncio.CancelledError:
            return
        text, end_wall = self._held_text, self._held_end
        source, received = self._held_meta
        self._held_text, self._hold_task = "", None
        if text:
            await self._start_turn(text, end_wall or time.perf_counter(), source, received)

    # ------------------------------------------------------------------ turns
    def _timer(self, tid: int, text: str) -> TurnTimer:
        timer = TurnTimer(call_id=self.call_id, turn=tid, user_text=text)
        self._timers[tid] = timer
        # Only recent turns can still receive playback reports.
        for old in [k for k in self._timers if k < tid - 10]:
            self._timers.pop(old, None)
        return timer

    async def _start_turn(self, text: str, end_wall: float, source: str = "text",
                          received: Optional[float] = None):
        if self.listen_only:
            logger.info("[%s] heard (%s): %s", self.call_id, source, text)
            return
        if self._speaking_turn is not None:
            await self.interrupt("new utterance")
        prev, phase = self._turn_task, self._turn_phase
        wait_for = None
        if prev is not None and not prev.done():
            if phase == "nlu":
                # Nothing mutated yet: restart as one turn with both utterances.
                prev.cancel()
                text = f"{self._turn_text} {text}".strip()
                logger.info("[%s] merged overlapping utterances: %r", self.call_id, text)
            elif phase == "commit":
                wait_for = prev  # let the state machine finish first

        self.turn_id += 1
        self._turn_text = text
        self._turns_since_filler += 1
        timer = self._timer(self.turn_id, text)
        timer.user_end = end_wall
        timer.endpoint_source = source
        timer.stt_event = received
        timer.committed = time.perf_counter()
        self._start_turn_task(self._run_turn(self.turn_id, text, timer, wait_for))

    async def _run_turn(self, tid: int, text: str, timer: TurnTimer, wait_for):
        if wait_for is not None:
            try:
                await wait_for
            except (asyncio.CancelledError, Exception):
                pass
        self._turn_phase = "nlu"
        logger.info("[%s] caller: %s", self.call_id, text)
        await self._send({"type": "state", "state": "thinking"})

        filler: list[asyncio.Task] = []

        def progress(event, **_):
            if event == "llm_start":
                filler.append(self._spawn(self._filler_after(tid, timer)))
            elif event == "before_action":
                # Not cancellable: it must play before the booking result.
                self._spawn(self._say_cached(tid, phrases.CHECKING, timer))
            elif event == "commit":
                self._turn_phase = "commit"

        try:
            result = await ai_engine.async_process_turn(text, self.s, progress)
        except asyncio.CancelledError:
            for task in filler:
                task.cancel()
            raise
        except Exception as exc:
            logger.error("[%s] turn failed: %s", self.call_id, exc, exc_info=True)
            result = ai_engine.TurnResult(phrases.ERROR_REPLY, tier=-1)

        for task in filler:
            if not task.done() and not self.speaker.lock.locked():
                task.cancel()
        timer.tier, timer.nlu_ms = result.tier, result.nlu_ms
        timer.step_before, timer.step_after = result.step_before, result.step_after
        timer.reply_ready = time.perf_counter()
        self._maybe_log(timer)

        if tid != self.turn_id:
            logger.info("[%s] turn %d superseded; not spoken", self.call_id, tid)
            self._log(timer)
            return
        self._turn_phase = "speak"
        await self._speak(tid, result.text, timer)
        if self.s.closed_conversation:
            self._spawn(self._finish_call(tid))

    async def _speak(self, tid: int, text: str, timer: TurnTimer):
        self._speaking_turn = tid
        self._emma_text = text
        self._playback_done.setdefault(tid, asyncio.Event())
        await self._send({"type": "caption", "who": "emma", "text": text, "final": True})
        await self._send({"type": "turn", "turn": tid, "phase": "start"})
        sent = await self.speaker.speak(tid, text, timer)
        await self._send({"type": "turn", "turn": tid, "phase": "audio_done"})
        if not sent and self._speaking_turn == tid:
            # Nothing reached the caller (no TTS configured or it failed).
            self._speaking_turn = None
            self._playback_done[tid].set()
            self._log(timer)
            await self._send({"type": "state", "state": "listening"})

    async def _filler_after(self, tid: int, timer: TurnTimer):
        """A short backchannel if an LLM turn is still thinking after FILLER_AFTER_MS."""
        await asyncio.sleep(config.FILLER_AFTER_MS / 1000)
        if (tid != self.turn_id or self._user_speaking or timer.first_audio_sent is not None
                or self._turns_since_filler < 3):
            return
        if await self._say_cached(tid, random.choice(phrases.FILLERS), timer):
            timer.filler = True
            self._turns_since_filler = 0

    async def _say_cached(self, tid: int, text: str, timer: TurnTimer) -> bool:
        if tid != self.turn_id:
            return False
        self._speaking_turn = tid
        self._emma_text = text
        self._playback_done.setdefault(tid, asyncio.Event())
        return await self.speaker.play_cached(tid, text, timer)

    async def _finish_call(self, tid: int):
        done = self._playback_done.get(tid)
        if done is not None:
            try:
                await asyncio.wait_for(done.wait(), timeout=FINAL_PLAYBACK_TIMEOUT_S)
            except asyncio.TimeoutError:
                logger.warning("[%s] timed out waiting for final playback", self.call_id)
        await self._send({"type": "bye"})
        await self.close()

    # ------------------------------------------------------------------ barge-in
    def _is_echo(self, text: str) -> bool:
        """Mostly Emma's own current words: her voice leaking back through the mic."""
        words = re.findall(r"[a-z']+", (text or "").lower())
        if len(words) < 2:
            return False
        emma_words = set(re.findall(r"[a-z']+", self._emma_text.lower()))
        return sum(1 for w in words if w in emma_words) / len(words) >= 0.6

    def _is_barge_in(self, text: str) -> bool:
        """Real caller speech worth stopping Emma for."""
        words = re.findall(r"[a-z']+", (text or "").lower())
        if not words:
            return False
        if len(words) < config.BARGE_IN_MIN_WORDS and words[0] not in _URGENT_WORDS:
            return False
        return not self._is_echo(text)

    async def interrupt(self, reason: str) -> bool:
        tid = self._speaking_turn
        if tid is None:
            return False
        started = self._speech_started_at or time.perf_counter()
        if self._turn_task is not None and not self._turn_task.done() and self._turn_phase == "speak":
            self._turn_task.cancel()
        self.speaker.cancel()
        await self.t.flush(tid)
        self._speaking_turn = None
        done = self._playback_done.get(tid)
        if done:
            done.set()
        timer = self._timers.get(tid)
        if timer is not None:
            timer.barge_in = True
            timer.barge_in_ms = (time.perf_counter() - started) * 1000
            self._log(timer)
        logger.info("[%s] barge-in on turn %d (%s)", self.call_id, tid, reason)
        await self._send({"type": "state", "state": "listening"})
        return True

    def _trim_history(self, tid, played_ms):
        """Keep only the part of the interrupted reply the caller actually heard."""
        if played_ms is None or not self.s.history:
            return
        last = self.s.history[-1]
        if last.get("role") != "assistant":
            return
        heard_chars = int(float(played_ms) / 1000 * CHARS_PER_SECOND)
        content = last.get("content", "")
        if heard_chars < len(content):
            cut = content[:heard_chars].rsplit(" ", 1)[0]
            last["content"] = f"{cut}... [interrupted by caller]"

    # ------------------------------------------------------------------ utils
    def _maybe_log(self, timer: TurnTimer):
        """Log a turn once it has both its engine result and its audible start."""
        if timer.audible is not None and (timer.reply_ready is not None or timer.tier == -1):
            self._log(timer)

    def _log(self, timer: TurnTimer):
        if self.sv.latency is None or timer.logged:
            return
        record = self.sv.latency.add(timer)
        if record is not None:
            self._spawn(self._send({"type": "metrics", **record}))

    async def _send(self, event: dict):
        if self.closed and event.get("type") not in ("bye",):
            return
        try:
            await self.t.send_event(event)
        except Exception as exc:
            logger.debug("[%s] send failed: %s", self.call_id, exc)

    async def close(self):
        if self.closed:
            return
        self.closed = True
        current = asyncio.current_task()
        for task in list(self._tasks):
            if task is not current:
                task.cancel()
        self.speaker.cancel()
        if self.capture is not None:
            self.capture.close()
        for engine in (self.stt, self.sv.tts):
            if engine is not None:
                try:
                    await engine.close()
                except Exception as exc:
                    logger.debug("[%s] close failed: %s", self.call_id, exc)
        try:
            await self.t.close()
        except Exception:
            pass
        logger.info("[%s] call closed", self.call_id)
