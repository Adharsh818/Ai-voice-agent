"""
Deepgram STT Engine — real-time streaming Speech-to-Text.

Raw WebSocket to Deepgram's streaming API (no SDK). PCM in, JSON events out:

    on_speech_started(timestamp)        VAD: the caller started speaking (barge-in cue)
    on_transcript(text, is_final)       live caption; interim text confirms a barge-in
    on_utterance_end(text, end_sec, source, start_sec=None)
                                        the caller finished a turn; `end_sec` and
                                        `start_sec` are call-audio times of the last
                                        and first word, so the real "caller stopped
                                        talking" moment is known; `source` says which
                                        event ended it: "speech_final" (endpointing
                                        silence) or "utterance_end" (the slower backstop)
    on_connection_lost()                reconnecting failed; the call cannot hear

Audio that arrives while the socket is not open (the greeting plays while it
connects, or Deepgram dropped mid-call) is buffered, up to 5 s, not dropped. On
an unexpected close the socket is reopened with backoff (0.25 / 0.5 / 1 s) and
the buffer replayed; after three failed attempts on_connection_lost fires so
the call can end gracefully (docs/IMPLEMENTATION_PLAN.md 5.7).

Deepgram's word timestamps restart at zero on every new stream, so each stream
records where in the call's audio it began and every reported time is shifted
by that offset: the call session's AudioClock stays correct across reconnects.

An utterance is never reported twice: finals whose words end no later than the
last utterance already reported (a speech_final followed by an UtteranceEnd for
the same audio, or audio replayed after a reconnect) are ignored.

A KeepAlive is sent whenever no audio has gone out for a few seconds so
Deepgram never closes an idle call.

smart_format is on, so numbers arrive formatted US-style ("(789) 937-7462");
every digit is kept, and the engine reads digits, not the formatting.
"""

import asyncio
import inspect
import json
import logging
import re
import time
from typing import Callable, Optional
from urllib.parse import urlencode

import websockets

logger = logging.getLogger(__name__)

KEEPALIVE_AFTER_S = 4.0
MAX_BUFFER_S = 5.0                       # audio kept while the socket is down
RECONNECT_BACKOFF_S = (0.25, 0.5, 1.0)   # one attempt after each delay
MAX_KEYTERMS = 50                        # well inside Deepgram's keyterm limit
# Background noise can keep Deepgram from ever sending speech_final, and its
# UtteranceEnd backstop then waits for real quiet too (5-6 s measured on 1 Oct).
# When the caller's words have stopped changing for this long, end the turn
# ourselves with what was heard.
WATCHDOG_S = 1.0
# 1-5 Oct voice tests: Deepgram sent speech_final on only about a third of
# turns, so most ended on this watchdog, 1.4-2.4 s after the caller's last
# word even for a plain "yes". The call session can say how long the words
# heard so far deserve (watchdog_for(text) -> seconds): a complete answer to
# Emma's question ends sooner; anything unfinished keeps the full wait.
WATCHDOG_MIN_S = 0.3
# With a voice detector (quiet_for), the watchdog ends a turn only once the
# caller has actually been quiet for the estimate above and the words have
# stopped changing for a moment; noise that never lets the line go quiet ends
# it after WATCHDOG_MAX_S of unchanged words. The 6 Oct replay of the owner's
# test lines: words-only timing cut 5-10 of 30 lines in half.
WATCHDOG_STABLE_S = 0.25
WATCHDOG_MAX_S = 2.0
# Deepgram's words trail the audio: the line can go quiet before the last word
# has been recognised ("I need a root" ... "canal"). With voice_until (the audio
# position where the caller last made a sound) the watchdog also waits until
# the words reach that point, give or take this much.
WORDS_COVER_MARGIN_S = 0.3


def _term_key(term: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (term or "").lower())


def _term_key_words(text: str) -> list:
    return [w for w in (_term_key(t) for t in (text or "").split()) if w]


def _accepts_start(callback) -> bool:
    try:
        params = inspect.signature(callback).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == "start_sec" or p.kind is p.VAR_KEYWORD for p in params)


class DeepgramSTT:
    """
    Usage:
        stt = DeepgramSTT(api_key="...", on_transcript=..., on_utterance_end=...)
        await stt.connect(sample_rate=16000)
        await stt.send_audio(pcm_bytes)
        await stt.close()
    """

    DEEPGRAM_WS_URL = "wss://api.deepgram.com/v1/listen"

    def __init__(
        self,
        api_key: str,
        on_transcript: Optional[Callable] = None,
        on_utterance_end: Optional[Callable] = None,
        on_speech_started: Optional[Callable] = None,
        on_connection_lost: Optional[Callable] = None,
        model: str = "nova-3",
        language: str = "en-IN",
        endpointing_ms: int = 200,
        utterance_end_ms: int = 1000,
        watchdog_s: float = WATCHDOG_S,
        keyterms: Optional[list] = None,
        watchdog_for: Optional[Callable[[str], float]] = None,
        quiet_for: Optional[Callable[[], float]] = None,
        voice_until: Optional[Callable[[], Optional[float]]] = None,
    ):
        self.api_key = api_key
        self.on_transcript = on_transcript
        self.on_utterance_end = on_utterance_end
        self.on_speech_started = on_speech_started
        self.on_connection_lost = on_connection_lost
        self.model = model
        self.language = language
        self.endpointing_ms = endpointing_ms
        self.utterance_end_ms = utterance_end_ms
        self.watchdog_s = watchdog_s
        self.watchdog_for = watchdog_for
        self.quiet_for = quiet_for
        self.voice_until = voice_until
        self.keyterms = list(keyterms or [])
        self._pass_start = bool(on_utterance_end) and _accepts_start(on_utterance_end)

        self._ws = None
        self._receive_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._current_utterance = ""
        self._utterance_start: Optional[float] = None
        self._last_word_end: Optional[float] = None
        self._last_emitted_end: Optional[float] = None
        self._interim_tail = ""           # words heard but not yet final
        self._interim_end: Optional[float] = None
        self._last_change = time.monotonic()
        self._watchdog_task: Optional[asyncio.Task] = None
        self._buffer: list[bytes] = []
        self._buffer_bytes = 0
        self._sample_rate = 16000
        self._audio_bytes = 0          # call audio handed to send_audio so far
        self._offset_sec = 0.0         # call time at which the current stream began
        self._last_send = time.monotonic()
        self._closing = False
        self.is_connected = False
        self.failed = False            # gave up reconnecting
        self.reconnects = 0

    # ------------------------------------------------------------------ setup
    def add_keyterms(self, terms) -> None:
        """
        Extra recognition hints (doctor, branch, service names), applied at the
        next connect. "check-up", "check up" and "checkup" count as one term.
        """
        seen = {_term_key(t) for t in self.keyterms}
        for term in terms or ():
            term = (term or "").strip()
            key = _term_key(term)
            if key and key not in seen and len(self.keyterms) < MAX_KEYTERMS:
                seen.add(key)
                self.keyterms.append(term)

    def _url(self, sample_rate: int) -> str:
        params = [
            ("model", self.model),
            ("language", self.language),
            ("encoding", "linear16"),
            ("sample_rate", sample_rate),
            ("channels", 1),
            ("punctuate", "true"),
            ("smart_format", "true"),
            ("interim_results", "true"),
            ("endpointing", self.endpointing_ms),
            ("utterance_end_ms", self.utterance_end_ms),
            ("vad_events", "true"),
        ]
        if self.model.startswith("nova-3"):
            params += [("keyterm", term) for term in self.keyterms[:MAX_KEYTERMS]]
        return f"{self.DEEPGRAM_WS_URL}?{urlencode(params)}"

    @property
    def _bytes_per_sec(self) -> int:
        return self._sample_rate * 2

    async def connect(self, sample_rate: int = 16000):
        """Open WebSocket to Deepgram streaming API (retrying in the background on failure)."""
        self._sample_rate = sample_rate
        if not self.api_key:
            logger.warning("No Deepgram API key — STT disabled")
            return
        if not await self._open():
            self._schedule_reconnect("connect failed")

    async def _open(self) -> bool:
        try:
            ws = await websockets.connect(
                self._url(self._sample_rate),
                additional_headers={"Authorization": f"Token {self.api_key}"},
                ping_interval=20,
                ping_timeout=10,
                close_timeout=2,
            )
        except Exception as e:
            logger.error("Failed to connect to Deepgram: %s", e)
            return False
        self._ws = ws
        self.is_connected = True
        # The first audio this stream hears is the oldest buffered chunk.
        self._offset_sec = (self._audio_bytes - self._buffer_bytes) / self._bytes_per_sec
        self._receive_task = asyncio.create_task(self._receive_loop(ws))
        self._keepalive_task = asyncio.create_task(self._keepalive_loop(ws))
        if self.watchdog_s and (self._watchdog_task is None or self._watchdog_task.done()):
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        logger.info("Deepgram STT connected (model=%s, language=%s, endpointing=%d ms, keyterms=%d)",
                    self.model, self.language, self.endpointing_ms, len(self.keyterms))
        # Replay what the caller said while the socket was opening or down.
        buffered, self._buffer, self._buffer_bytes = self._buffer, [], 0
        for chunk in buffered:
            if not await self._send_now(chunk):
                break
        return True

    # ------------------------------------------------------------------ audio in
    def _keep(self, audio_bytes: bytes):
        self._buffer.append(audio_bytes)
        self._buffer_bytes += len(audio_bytes)
        limit = int(MAX_BUFFER_S * self._bytes_per_sec)
        while self._buffer_bytes > limit and self._buffer:
            self._buffer_bytes -= len(self._buffer.pop(0))

    async def _send_now(self, audio_bytes: bytes) -> bool:
        try:
            await self._ws.send(audio_bytes)
            self._last_send = time.monotonic()
            return True
        except Exception as e:
            logger.warning("Error sending audio to Deepgram: %s", e)
            self.is_connected = False
            self._keep(audio_bytes)
            self._schedule_reconnect("send failed")
            return False

    async def send_audio(self, audio_bytes: bytes):
        """Send raw PCM audio bytes to Deepgram (buffered while not connected)."""
        if not audio_bytes:
            return
        self._audio_bytes += len(audio_bytes)
        if self._ws is not None and self.is_connected:
            await self._send_now(audio_bytes)
        elif not self._closing and not self.failed:
            self._keep(audio_bytes)

    async def _keepalive_loop(self, ws):
        try:
            while self.is_connected and ws is self._ws:
                await asyncio.sleep(1.0)
                if time.monotonic() - self._last_send > KEEPALIVE_AFTER_S and ws is self._ws:
                    await ws.send(json.dumps({"type": "KeepAlive"}))
                    self._last_send = time.monotonic()
        except (asyncio.CancelledError, websockets.exceptions.ConnectionClosed):
            pass
        except Exception as e:
            logger.debug("Deepgram keepalive stopped: %s", e)

    # ------------------------------------------------------------------ reconnect
    def _schedule_reconnect(self, reason: str):
        if self._closing or self.failed or not self.api_key:
            return
        if self._reconnect_task is not None and not self._reconnect_task.done():
            return
        self._reconnect_task = asyncio.create_task(self._reconnect(reason))

    async def _reconnect(self, reason: str):
        logger.warning("Deepgram connection lost (%s); reconnecting", reason)
        old, self._ws, self.is_connected = self._ws, None, False
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
        if old is not None:
            try:
                await asyncio.wait_for(old.close(), timeout=0.5)
            except Exception:
                pass
        for attempt, delay in enumerate(RECONNECT_BACKOFF_S, start=1):
            await asyncio.sleep(delay)
            if self._closing:
                return
            if await self._open():
                self.reconnects += 1
                logger.info("Deepgram reconnected on attempt %d", attempt)
                return
        self.failed = True
        self._buffer, self._buffer_bytes = [], 0
        logger.error("Deepgram reconnect failed after %d attempts", len(RECONNECT_BACKOFF_S))
        if self.on_connection_lost is not None:
            try:
                await self.on_connection_lost()
            except Exception as e:
                logger.error("on_connection_lost failed: %s", e, exc_info=True)

    # ------------------------------------------------------------------ events out
    async def _receive_loop(self, ws=None):
        """Background task: receive and process Deepgram messages."""
        ws = ws or self._ws
        try:
            async for message in ws:
                try:
                    data = json.loads(message)
                    msg_type = data.get("type", "")

                    if msg_type == "Results":
                        await self._handle_result(data)
                    elif msg_type == "UtteranceEnd":
                        await self._handle_utterance_end()
                    elif msg_type == "SpeechStarted":
                        if self.on_speech_started:
                            await self.on_speech_started(data.get("timestamp"))
                    elif msg_type == "Error":
                        logger.error("Deepgram error: %s", data.get("description", ""))
                except json.JSONDecodeError:
                    logger.warning("Non-JSON message from Deepgram")
                except Exception as e:
                    logger.error("Error processing Deepgram message: %s", e, exc_info=True)

        except websockets.exceptions.ConnectionClosed as e:
            if not self._closing:
                logger.info("Deepgram connection closed: %s", e)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Deepgram receive loop error: %s", e)
        finally:
            if ws is self._ws:
                self.is_connected = False
                if not self._closing:
                    self._schedule_reconnect("socket closed")

    def _drop_reported_words(self, words: list, transcript: str) -> tuple:
        """
        A result that starts with words already handed over (the watchdog sent
        "I need a root" from the interim; the final is "I need a root canal.")
        keeps only the new words, so the caller's sentence isn't heard twice.
        """
        if not words or self._last_emitted_end is None:
            return words, transcript
        # A word is new if it starts after what was handed over ended. Deepgram's
        # final timings differ from the interim's by tens of ms, so comparing ends
        # let "that?" through twice ("Sorry. Can you repeat that?" | "that?").
        cut = self._last_emitted_end - 0.1
        keep = [w for w in words if w.get("start") is None or w["start"] + self._offset_sec > cut]
        if len(keep) == len(words):
            return words, transcript
        text = " ".join((w.get("punctuated_word") or w.get("word") or "") for w in keep).strip()
        return keep, text

    def _already_reported(self, words: list) -> bool:
        """True when these words end no later than the last utterance already reported."""
        if not words or self._last_emitted_end is None:
            return False
        end = words[-1].get("end")
        return end is not None and end + self._offset_sec <= self._last_emitted_end + 0.15

    async def _handle_result(self, data: dict):
        """Process a Deepgram Results message."""
        alternatives = data.get("channel", {}).get("alternatives", [])
        if not alternatives:
            return
        transcript = alternatives[0].get("transcript", "").strip()
        if not transcript:
            return
        words = alternatives[0].get("words") or []
        if self._already_reported(words):
            logger.debug("Deepgram re-sent audio already reported: %r", transcript)
            return
        words, transcript = self._drop_reported_words(words, transcript)
        if not transcript:
            return

        is_final = data.get("is_final", False)
        speech_final = data.get("speech_final", False)

        previous = f"{self._current_utterance} {self._interim_tail}".strip()
        if is_final:
            self._interim_tail, self._interim_end = "", None
            # Accumulate final transcript segments
            self._current_utterance = f"{self._current_utterance} {transcript}".strip()
            if words:
                if self._utterance_start is None and words[0].get("start") is not None:
                    self._utterance_start = words[0]["start"] + self._offset_sec
                if words[-1].get("end") is not None:
                    self._last_word_end = words[-1]["end"] + self._offset_sec
        else:
            self._interim_tail = transcript
            if words and words[-1].get("end") is not None:
                self._interim_end = words[-1]["end"] + self._offset_sec
        if f"{self._current_utterance} {self._interim_tail}".strip() != previous:
            self._last_change = time.monotonic()

        # Live caption (and, while Emma is talking, the barge-in signal).
        if self.on_transcript:
            display_text = (
                f"{self._current_utterance} {transcript}".strip()
                if not is_final
                else self._current_utterance
            )
            await self.on_transcript(display_text, is_final)

        # speech_final: Deepgram's endpointer says the utterance is complete.
        if is_final and speech_final:
            await self._emit_utterance("speech_final")

    async def _handle_utterance_end(self):
        """Deepgram's silence-based UtteranceEnd: the backstop when speech_final never came."""
        await self._emit_utterance("utterance_end")

    async def _watchdog_loop(self):
        """End a turn whose words stopped changing, when Deepgram's own signals stall."""
        try:
            while not self._closing:
                await asyncio.sleep(0.1)
                pending = self._current_utterance or self._interim_tail
                if pending and self._watchdog_due(time.monotonic() - self._last_change):
                    await self._emit_utterance("watchdog")
        except asyncio.CancelledError:
            pass

    def _watchdog_due(self, stable_s: float) -> bool:
        """Words unchanged for `stable_s`: is the caller done?"""
        if self.quiet_for is None:
            return stable_s >= self._watchdog_limit()
        if stable_s >= WATCHDOG_MAX_S:
            return True
        try:
            quiet = float(self.quiet_for())
        except Exception as exc:             # a broken detector falls back to words only
            logger.debug("quiet_for failed: %s", exc)
            return stable_s >= self._watchdog_limit()
        if not (stable_s >= WATCHDOG_STABLE_S and quiet >= self._watchdog_limit()):
            return False
        return self._words_cover_voice()

    def _words_cover_voice(self) -> bool:
        """The recognised words reach the point where the caller's voice stopped."""
        if self.voice_until is None:
            return True
        try:
            voice_end = self.voice_until()
        except Exception:
            return True
        words_end = max((x for x in (self._last_word_end, self._interim_end) if x is not None), default=None)
        if voice_end is None or words_end is None:
            return True
        return words_end >= voice_end - WORDS_COVER_MARGIN_S

    def _watchdog_limit(self) -> float:
        """Seconds of unchanged words that end the turn: the session's estimate for these words, if any."""
        if self.watchdog_for is None:
            return self.watchdog_s
        text = f"{self._current_utterance} {self._interim_tail}".strip()
        try:
            limit = float(self.watchdog_for(text))
        except Exception as exc:              # a bad estimate must never stall the turn
            logger.debug("watchdog_for failed: %s", exc)
            return self.watchdog_s
        return min(self.watchdog_s, max(WATCHDOG_MIN_S, limit))

    async def _emit_utterance(self, source: str):
        if source == "watchdog" and self._interim_tail:
            # Words Deepgram hasn't finalised yet count too; their final copy is
            # dropped later by _already_reported (same word end times).
            self._current_utterance = f"{self._current_utterance} {self._interim_tail}".strip()
            if self._interim_end is not None:
                self._last_word_end = self._interim_end
        self._interim_tail, self._interim_end = "", None
        text = self._current_utterance.strip()
        end_sec, start_sec = self._last_word_end, self._utterance_start
        self._current_utterance = ""
        self._utterance_start = None
        if not text:
            return
        if self._repeats_last(text):
            logger.debug("Deepgram repeated the end of the last utterance: %r", text)
            return
        if end_sec is not None:
            self._last_emitted_end = end_sec
        self._last_emitted = (_term_key_words(text), time.monotonic())
        if self.on_utterance_end:
            if self._pass_start:
                await self.on_utterance_end(text, end_sec, source, start_sec=start_sec)
            else:
                await self.on_utterance_end(text, end_sec, source)

    def _repeats_last(self, text: str) -> bool:
        """One or two words that only repeat the end of what was just handed over ("that?")."""
        last = getattr(self, "_last_emitted", None)
        words = _term_key_words(text)
        if not last or not words or len(words) > 2 or time.monotonic() - last[1] > 1.5:
            return False
        return last[0][-len(words):] == words

    def reset_utterance(self):
        """Reset the current utterance buffer."""
        self._current_utterance = ""
        self._utterance_start = None
        self._interim_tail, self._interim_end = "", None

    async def close(self):
        """Close the Deepgram connection."""
        self._closing = True
        for task in (self._watchdog_task, self._reconnect_task, self._keepalive_task, self._receive_task):
            if task and task is not asyncio.current_task():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        if self._ws:
            try:
                await self._ws.send(json.dumps({"type": "CloseStream"}))
                await self._ws.close()
            except Exception:
                pass

        self.is_connected = False
        logger.info("Deepgram STT closed")
