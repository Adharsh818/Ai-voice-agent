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
Deepgram ends an utterance -> turn_detector.TurnDetector decides from the words
and what Emma is listening for whether the caller has finished ("yes" and a
full phone number commit at once; "and...", "Don't", half a number wait up to
~2 s and merge with what follows) -> a turn task runs
ai_engine.async_process_turn and speaks the reply. A turn moves through three
phases:

    "nlu"     still understanding; a newer utterance cancels it and the two
              texts are merged into one turn (nothing has been mutated yet)
    "commit"  the engine is changing state, or Emma has already started
              answering; it always completes
    "speak"   audio is streaming; a confirmed barge-in cancels it

Speaking without the walkie-talkie gap: when the engine accepts on_sentence,
each reply sentence is queued to the speaker the moment it is final, and its
synthesis starts at once (up to two sentences ahead), so TTS runs during the
typing beat and while the previous sentence plays. The note-taking typing
beat overlaps the engine's work (the reply waits max(engine time, a short
beat)). "Let me just check that for you." (or the engine's varied line) plays
with typing before a slow action: at once with today's engine, after the
model's streamed sentences with a streaming one. A filler plays only if an
LLM turn is still thinking after FILLER_AFTER_MS, and the reply then drops an
opener that would repeat it ("Okay." ... "Okay, ...").

Barge-in and echo: while Emma's audio is audible (the browser's playback
started/ended reports), caller speech that is not an echo of what she was
saying at that moment stops her, flushes the player and trims her history
entry to what was actually heard. Backchannels ("yeah", "okay", "mm-hmm",
"sure, take your time") never stop her, nor does "Hello?" over her greeting;
a "yes" said during her closing question is kept as the answer once she
finishes. Echo is only possible while her voice is audible, and needs a
near-exact match, so a caller repeating her words ("yes, Monday at 5") is
never dropped. s.last_reply_heard tells the engine whether her previous turn
played to the end, for the recap-heard rule. If the caller talks over the
checking line before a booking, change or cancellation, the outcome they
missed is said before anything else (it is never lost).

Also here: the silence ladder, the 15-minute call limit, a graceful end when
speech recognition cannot be restored, Deepgram keyterms from the clinic
database, and the lines Emma speaks for those (one marked section below).

Silence belongs to this module's ladder, for both engines: the engine is only
ever called with "" for the greeting, never mid-call, so the R2 engine's own
silence handler (for callers that drive it directly) and this ladder never
both answer the same pause.
"""

import asyncio
import inspect
import json
import logging
import random
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

import ai_engine
import config
import phones
import phrases
import turn_detector
import vad
from capture import CallCapture
from latency import AudioClock, LatencyLog, TurnTimer
from speech import Speaker, PromptCache, drop_repeats, split_sentences, strip_opener

logger = logging.getLogger("call")

FINAL_PLAYBACK_TIMEOUT_S = 20
# A hang-up during a booking, move or cancellation waits this long for it to finish.
COMMIT_ON_HANGUP_S = 5.0
# Pause after "Let me just check that for you." while typing plays (ms).
CHECK_PAUSE_MS = (900, 1600)
# Longest note-taking typing burst; it stops as soon as Emma starts speaking.
TYPING_MAX_MS = 3000
# Approximate speaking rate, used when no timeline says how much was heard.
CHARS_PER_SECOND = 15

# Echo: Emma's voice can reach the mic this long after her playback ends, and
# the words heard are compared with what she said around that moment.
ECHO_TAIL_S = 0.4
ECHO_WINDOW_SLACK_S = 0.8
# A politeness word this soon after a committed turn ("yes" ... "please") adds nothing.
REPLY_PENDING_S = 2.5
# The caller counts as talking this long after their last recognised word.
CALLER_ACTIVE_S = 1.5
# Silence ladder (plan 5.7): a step after this much silence; "hold on" stretches it.
SILENCE_STEP_S = 8
HOLD_ON_STEP_S = 30
# Maximum call length, with a heads-up before the end.
MAX_CALL_S = 15 * 60
WRAP_UP_AT_S = 13 * 60
WATCH_INTERVAL_S = 0.25

# A streamed reply's checking line waits for the model's own sentences ("Sure,
# Monday evening." comes first, R2_DESIGN 5); if the engine is still busy after
# this long, the line starts anyway so the caller never hears dead air.
CHECKING_FALLBACK_MS = 600
# An outcome the caller has not heard (they spoke over "Let me just check")
# is said on its own once the line has been quiet this long.
OWED_AFTER_S = 1.2

_URGENT_WORDS = {"no", "wait", "stop", "sorry", "hello", "hey", "hold", "excuse"}
_BACKCHANNEL_WORDS = {
    "yeah", "yes", "yep", "yup", "ya", "yah", "okay", "ok", "mm", "mmm", "hmm", "hm", "mhm",
    "uh", "huh", "right", "sure", "alright", "fine", "cool", "great", "good", "nice", "perfect",
    "correct", "oh", "ah", "thanks", "please", "exactly", "true", "absolutely", "definitely",
}
_BACKCHANNEL_PHRASES = {"got it", "i see", "thank you", "all right", "go on", "makes sense",
                        "that's right", "uh huh", "mm hmm"}
# What callers say while Emma looks something up ("Sure, take your time"): an
# acknowledgement, never a reason to stop her. Up to four words with a lead-in.
_ACKNOWLEDGEMENTS = _BACKCHANNEL_PHRASES | {
    "no problem", "no worries", "take your time", "no rush", "sure thing", "thank you so much",
    "thanks a lot", "go ahead",
}


# =============================================================================
# Lines the call session speaks itself: silence, call length, lost audio.
# Kept in one place so they can move to prompts.py with the rest of Emma's
# words. Short, warm, varied receptionist phrasing (docs/NORTH_STAR.md); no
# line is repeated in a call while another variant is left.
# =============================================================================
STILL_THERE_LINES = [
    "Are you still there?",
    "Hello, are you still with me?",
    "Sorry, are you still there?",
]
CANT_HEAR_LINES = [
    "I'm not hearing anything on the line. If you're there, just say hello.",
    "I can't hear you at the moment. If you're there, could you say something?",
]
SILENCE_GOODBYE_LINES = [
    "I think we've lost each other. Do call us back whenever you're ready. Bye for now.",
    "Seems the line's gone quiet, so I'll let you go. Call us back anytime. Take care.",
]
WRAP_UP_LINES = [
    "Just so you know, I'll need to wrap up in a couple of minutes.",
    "Just a heads-up, I'll have to wrap up shortly.",
]
LONG_CALL_GOODBYE_CALLBACK_LINES = [
    "I'm sorry, I have to let you go now. We'll call you back to finish this off. Take care, bye!",
]
LONG_CALL_GOODBYE_LINES = [
    "I'm sorry, I have to let you go now. Do call us back and we'll pick this up. Take care, bye!",
]
CANT_HEAR_CALLBACK_LINES = [
    "Sorry, I'm having trouble hearing you on this line. We'll call you right back. Bye for now.",
]
CANT_HEAR_GOODBYE_LINES = [
    "Sorry, I'm having trouble hearing you on this line. Could you give us a call back in a minute? Bye for now.",
]
# Staff takeover from the dashboard (plan 5.10).
TAKEOVER_LINES = [
    "One moment, a member of our team is taking over.",
    "Just a moment, I'm passing you to a colleague here.",
]
HAND_BACK_LINES = [
    "Thanks for waiting.",
    "Sorry about that, I'm back with you.",
]
STAFF_GOODBYE_LINES = [
    "I'm sorry, I need to end the call here. Someone from the team will call you back shortly. Bye for now.",
]
# Worth pre-rendering with the other fixed prompts (phrases.all_phrases(extra=...)).
CACHEABLE_LINES = (STILL_THERE_LINES + CANT_HEAR_LINES + SILENCE_GOODBYE_LINES + WRAP_UP_LINES
                   + LONG_CALL_GOODBYE_CALLBACK_LINES + LONG_CALL_GOODBYE_LINES
                   + CANT_HEAR_CALLBACK_LINES + CANT_HEAR_GOODBYE_LINES)
# =============================================================================


def _plain_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", (text or "").lower().replace("’", "'"))


def is_backchannel(text: str) -> bool:
    """
    "yeah", "okay", "mm-hmm", "right", "got it": at most two words of listening
    noises, or an acknowledgement such as "okay, no problem" or "sure, take
    your time" (said while Emma checks something; it asks nothing of her).
    """
    words = _plain_words(text)
    if not words or len(words) > 4:
        return False
    if len(words) <= 2 and (" ".join(words) in _BACKCHANNEL_PHRASES
                            or all(w in _BACKCHANNEL_WORDS for w in words)):
        return True
    rest = list(words)
    while rest and rest[0] in _BACKCHANNEL_WORDS:
        rest.pop(0)
    return bool(rest) and " ".join(rest) in _ACKNOWLEDGEMENTS


_HELLO_WORDS = {"hello", "hi", "hey", "hallo", "hai", "helo", "there", "emma"}


def is_hello(text: str) -> bool:
    """"Hello?", "Hi there": what callers say as the line opens, not a request."""
    words = _plain_words(text)
    return 0 < len(words) <= 3 and all(w in _HELLO_WORDS for w in words) and words[0] != "there"


_ANSWER_WORDS = {"yes", "yeah", "yep", "yup", "no", "nope", "nah", "ok", "okay", "sure",
                 "correct", "right", "wrong", "not", "change", "cancel", "five", "four", "six", "seven",
                 "eight", "nine", "ten", "eleven", "twelve", "one", "two", "three"}


def _pairs_in_order(words: list, emma_words: list) -> int:
    """How many of the heard word pairs Emma also said, in that order."""
    pairs = set(zip(emma_words, emma_words[1:]))
    return sum(1 for p in zip(words, words[1:]) if p in pairs)


def looks_like_echo(heard: str, emma: str, greeting: bool = False) -> bool:
    """
    `heard` is (almost) exactly what Emma was saying: her voice leaking back
    through the caller's microphone. Short phrases must match word for word;
    longer ones need 80% of their words and 60% of their word pairs in order,
    so "yes, Monday at 5" after "...Monday at 5. Shall I book it?" is the
    caller answering, not an echo.
    """
    words, emma_words = _plain_words(heard), _plain_words(emma)
    if not words or not emma_words:
        return False
    vocab = set(emma_words)
    coverage = sum(1 for w in words if w in vocab) / len(words)
    if (len(words) >= 2 and coverage >= (0.6 if len(words) >= 4 else 0.75)
            and _pairs_in_order(words, emma_words) * 2 >= len(words) - 1
            and not (set(words) & _ANSWER_WORDS) and not any(ch.isdigit() for ch in heard)):
        # Mostly her own words while she was audible: her voice through the
        # speakers. This lenient rule is what worked before 1 Oct; real answers
        # that repeat her words ("yes, Monday at 5") carry a yes/no or a number.
        return True
    if greeting and len(words) >= 2 and coverage >= 0.5 and _pairs_in_order(words, emma_words) >= 2:
        # Speaker echo of the greeting comes back garbled ("I'm Emma from her"
        # for "I'm Emma from Pearl Dental", 1 Oct). A caller's real reply to
        # "how can I help?" almost never repeats half of the greeting's words.
        return True
    if len(words) <= 2:
        return coverage == 1.0
    if coverage < 0.8:
        return False
    pairs = set(zip(emma_words, emma_words[1:]))
    heard_pairs = list(zip(words, words[1:]))
    return sum(1 for p in heard_pairs if p in pairs) / len(heard_pairs) >= 0.6


# Indian number phrasing ("double nine", "triple zero"): boosted with the clinic's words.
SPOKEN_NUMBER_TERMS = ["double", "triple"]


def keyterm_key(term: str) -> str:
    """"check-up", "check up" and "checkup" are one keyterm to the recogniser."""
    return re.sub(r"[^a-z0-9]+", "", (term or "").lower())


def clinic_keyterms(conn) -> list[str]:
    """
    Words the recogniser should expect on this clinic's calls, from the
    database: doctors as callers say them ("Dr Rao"), branches, services and
    their short spoken forms ("check-up", "root canal", "scaling"), plus the
    Indian number words. Spelling variants of one term are sent once, so the
    keyterm budget goes on different words.
    """
    terms = [row[0] for row in conn.execute("SELECT spoken_name FROM doctors WHERE active = 1")]
    terms += [row[0] for row in conn.execute("SELECT name FROM branches WHERE active = 1")]
    for name, aliases in conn.execute("SELECT name, aliases_json FROM services WHERE active = 1"):
        terms.append(name)
        try:
            terms += [a for a in json.loads(aliases or "[]") if len(a.split()) <= 2]
        except (TypeError, ValueError):
            pass
    terms += SPOKEN_NUMBER_TERMS
    seen, out = set(), []
    for term in terms:
        key = keyterm_key(term)
        if key and key not in seen:
            seen.add(key)
            out.append(term.strip())
    return out


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _loggable(text: str) -> str:
    """The caller's words for the console log, or only their length when LOG_CALLER_TEXT is off."""
    return text if config.LOG_CALLER_TEXT else f"<{len(text or '')} chars>"


@dataclass
class Services:
    """Shared, warm resources handed to every call by the server."""
    stt_factory: Callable          # (**callbacks) -> DeepgramSTT-like
    tts: object = None             # per-call live TTS (multi-context WebSocket)
    fallback_tts: object = None    # shared HTTP TTS
    backup_tts: object = None      # local Piper voice, when ElevenLabs fails or is silent
    cache: Optional[PromptCache] = None
    latency: Optional[LatencyLog] = None
    # () -> list of recognition keyterms (sync or async); None reads the clinic database.
    keyterms: Optional[Callable] = None


@dataclass
class _Part:
    kind: str                       # "text" | "checking"
    text: str
    segments: Optional[list] = None  # synthesis already started (Speaker.prepare)


class _ReplyVoice:
    """
    Speaks one turn's reply in order as its parts become ready: each sentence
    the engine streams through on_sentence, the checking line, then whatever
    is left of TurnResult.text. Parts are queued, so the engine never waits on
    audio. Each part is polished (opener and repeat rules) the moment it is
    queued and its synthesis starts at once, up to PREPARE_AHEAD parts ahead,
    so live TTS runs during the typing beat and while the previous sentence
    plays. Nothing is said before the beat has passed.
    """

    PREPARE_AHEAD = 2

    def __init__(self, session: "CallSession", tid: int, timer: TurnTimer, started: float, beat_ms: int):
        self.session = session
        self.tid = tid
        self.timer = timer
        self.started_at = started
        self.beat_ms = beat_ms
        self.delivered: list[str] = []
        self._said = ""                         # polished text queued so far
        self._parts: deque = deque()
        self._more = asyncio.Event()
        self._finished = False
        self._checking_phrase: Optional[str] = None   # waiting for the streamed sentences
        self._checking_timer: Optional[asyncio.Task] = None
        self._task: Optional[asyncio.Task] = None

    @property
    def started(self) -> bool:
        return self._task is not None

    @property
    def checking_pending(self) -> bool:
        return self._checking_phrase is not None

    def _put(self, part: _Part):
        if part.kind == "text":
            part.text = self.session._polish(self.tid, part.text, self.timer, said=self._said)
            if not part.text:
                return
            self._said = f"{self._said} {part.text}".strip()
        self._parts.append(part)
        self._prepare_ahead()
        self._more.set()
        if self._task is None:
            self._task = self.session._spawn(self._run())

    def _prepare_ahead(self):
        ready = 0
        for part in self._parts:
            if part.kind != "text":
                continue
            if part.segments is None:
                if ready >= self.PREPARE_AHEAD:
                    break
                part.segments = self.session.speaker.prepare(part.text)
            ready += 1

    def _answering(self):
        # Emma has started answering: a new utterance must no longer restart the turn.
        if self.session._turn_phase == "nlu" and self.session._turn_task is not None:
            self.session._turn_phase = "commit"

    def put_text(self, text: str):
        """Queue reply text that is not a streamed sentence (an owed outcome)."""
        if text and text.strip():
            self._answering()
            self._put(_Part("text", text))

    def checking(self, phrase: Optional[str] = None, defer: bool = False):
        """
        "Let me just check that for you." before a slow action. With a streaming
        engine it waits until the engine returns, so the model's own sentences
        come first, unless the engine is still busy after CHECKING_FALLBACK_MS.
        """
        self._answering()
        phrase = (phrase or "").strip() or phrases.CHECKING
        if not defer:
            self._put(_Part("checking", phrase))
            return
        self._checking_phrase = phrase
        if self._checking_timer is None:
            self._checking_timer = self.session._spawn(self._checking_soon())

    async def _checking_soon(self):
        await asyncio.sleep(CHECKING_FALLBACK_MS / 1000)
        self._release_checking()

    def _release_checking(self):
        if self._checking_phrase is not None:
            phrase, self._checking_phrase = self._checking_phrase, None
            self._put(_Part("checking", phrase))

    async def on_sentence(self, sentence: str):
        sentence = (sentence or "").strip()
        if not sentence or self.tid != self.session.turn_id:
            return
        self.delivered.append(sentence)
        self._answering()
        self._put(_Part("text", sentence))

    async def finish(self, remaining: str):
        """Queue the rest of the reply and wait until everything has been sent."""
        if self._checking_timer is not None:
            self._checking_timer.cancel()
        self._release_checking()
        if remaining and remaining.strip():
            self._put(_Part("text", remaining))
        self._finished = True
        self._more.set()
        if self._task is None:
            return
        try:
            await self._task
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise

    def cancel(self):
        if self._checking_timer is not None:
            self._checking_timer.cancel()
        self._checking_phrase = None
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._discard()

    def _discard(self):
        """Stop synthesis of parts that will never be played."""
        while self._parts:
            self.session.speaker.discard(self._parts.popleft().segments)

    async def _run(self):
        s = self.session
        try:
            await s._pre_speech_pause(self.tid, self.timer, self.started_at, self.beat_ms)
            while True:
                if not self._parts:
                    if self._finished:
                        break
                    self._more.clear()
                    await self._more.wait()
                    continue
                part = self._parts.popleft()
                self._prepare_ahead()
                if not s._may_speak(self.tid):
                    s.speaker.discard(part.segments)
                    continue
                if part.kind == "checking":
                    await s._checking_pause(self.tid, self.timer, part.text)
                else:
                    await s._speak_part(self.tid, part.text, self.timer, part.segments)
            if s._may_speak(self.tid):
                await s._end_speech(self.tid, self.timer)
        finally:
            self._discard()


class CallSession:
    def __init__(self, transport, services: Services, call_id: Optional[str] = None,
                 listen_only: bool = False, state=None):
        self.t = transport
        # Listen-only (dev capture): transcribe and record what the caller says,
        # but never greet or reply. Used to record the STT comparison set.
        self.listen_only = listen_only
        self.sv = services
        self.call_id = call_id or uuid.uuid4().hex[:8]
        # `state`: an engine state made by the caller (an outbound recovery call's
        # dialogue.recovery.RecoveryContext); otherwise a new inbound session.
        self.s = state if state is not None else self._new_session()
        self._set_heard(True)
        self.clock = AudioClock(16000)
        # Is the caller making sound right now (vad.py): ends turns on real
        # silence and keeps a held turn open while they are still talking.
        self.vad = vad.VoiceActivity()
        self._voice_audio_end: Optional[float] = None   # audio position (s) of the caller's last sound
        self.speaker = Speaker(transport, services.cache, services.tts, services.fallback_tts,
                               services.backup_tts)
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
        self._voice: Optional[_ReplyVoice] = None
        self._engine_fn = None
        self._engine_streams = False

        self._speaking_turn: Optional[int] = None   # turn whose audio may be audible
        self._last_spoken_turn: Optional[int] = None  # Emma's latest reply (for last_reply_heard)
        self._emma_text = ""
        self._reply_text: dict[int, str] = {}
        self._last_reply = ""                       # Emma's last full reply (for the silence ladder)
        self._playback_done: dict[int, asyncio.Event] = {}
        self._audible: dict[int, list] = {}         # turn -> [started wall, ended wall or None]
        self._playback_reports = False              # the transport reports playback (the browser does)
        self._muted: set[int] = set()               # turns cut off by a barge-in
        self._filler_turn: Optional[int] = None
        self._user_speaking = False
        self._speech_started_at: Optional[float] = None
        self._last_voice: Optional[float] = None
        self._last_utterance = ("", None, 0.0)      # (words, end_sec, received): duplicate guard
        self._last_commit_at = 0.0
        self._deferred: Optional[tuple] = None      # a backchannel answer waiting for Emma to finish
        self._prepared: dict[int, tuple] = {}       # turn -> (text, segments) synthesising ahead
        self._owed: Optional[str] = None            # an outcome reply the caller has not heard yet
        self._greeting_turn: Optional[int] = None

        self.detector = turn_detector.TurnDetector(self._on_turn_ready, hint=self._listening_hint,
                                                   spawn=self._spawn)
        self._turns_since_filler = 99
        self._tasks: set[asyncio.Task] = set()
        self._started_at = time.perf_counter()
        self._idle_since = self._started_at
        self._ladder_step = 0
        self._hold_on = False
        self._wrapped_up = False
        self._ending = False
        self._used_lines: set[str] = set()
        self._turn_extra: dict[int, dict] = {}       # per turn: the engine's entities and action (transcript)
        self.outcome: Optional[str] = None
        # Staff takeover from the dashboard (plan 5.10): while on, the caller's
        # words are only captioned and recorded, and staff's typed lines are spoken.
        self.operator = False
        self._operator_turns: set[int] = set()
        self._resume_question = ""
        # Transcript recorder (recording.CallRecorder). The server attaches one
        # before start() and closes it after close(); without one, start() makes
        # its own when the database is open, and close() ends that one.
        self.recorder = None
        self._own_recorder = False
        self.closed = False

    # ------------------------------------------------------------------ engine contract
    def _new_session(self):
        factory = getattr(ai_engine, "new_session", None)
        if factory is None:
            return ai_engine.SessionState()
        try:
            return factory(call_id=self.call_id)
        except TypeError:
            return factory()

    def _listening_hint(self) -> dict:
        hint_fn = getattr(ai_engine, "listening_hint", None)
        if hint_fn is not None:
            try:
                hint = hint_fn(self.s)
                if isinstance(hint, dict):
                    return hint
            except Exception as exc:
                logger.debug("[%s] listening_hint failed: %s", self.call_id, exc)
        return turn_detector.hint_from_state(self.s)

    def _expects_information(self, text: str) -> bool:
        check = getattr(ai_engine, "expects_information", None)
        try:
            return bool(check and check(self.s, text))
        except Exception as exc:
            logger.debug("[%s] expects_information failed: %s", self.call_id, exc)
            return False

    def _streams_sentences(self) -> bool:
        """Whether the engine accepts on_sentence (checked per function, so tests can patch it)."""
        fn = ai_engine.async_process_turn
        if fn is not self._engine_fn:
            self._engine_fn = fn
            try:
                self._engine_streams = "on_sentence" in inspect.signature(fn).parameters
            except (TypeError, ValueError):
                self._engine_streams = False
        return self._engine_streams

    def _set_heard(self, heard: bool):
        """s.last_reply_heard: did Emma's previous turn play to the end (recap-heard rule)."""
        try:
            self.s.last_reply_heard = heard
        except Exception:
            pass

    # ------------------------------------------------------------------ setup
    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def start(self):
        self._started_at = self._idle_since = time.perf_counter()
        self.stt = self.sv.stt_factory(
            on_transcript=self._on_transcript,
            on_utterance_end=self._on_utterance_end,
            on_speech_started=self._on_speech_started,
            on_connection_lost=self._on_stt_lost,
            watchdog_for=self._watchdog_for,
            quiet_for=self.vad.quiet_for,
            voice_until=lambda: self._voice_audio_end,
        )
        # The greeting plays from the prompt cache while both sockets open.
        if not self.listen_only:
            self._start_turn_task(self._greet())
            self._spawn(self._watch())
        await self._load_keyterms()
        connects = [self.stt.connect(sample_rate=16000)] if self.stt is not None else []
        if self.sv.tts is not None and hasattr(self.sv.tts, "connect"):
            connects.append(self.sv.tts.connect())
        results = await asyncio.gather(*connects, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.warning("[%s] connect failed: %s", self.call_id, result)
        if not self.listen_only:
            await self._open_recording()
        logger.info("[%s] call started", self.call_id)

    async def _load_keyterms(self):
        """Clinic vocabulary for the recogniser and the turn detector, on top of config."""
        if self.stt is None or not hasattr(self.stt, "add_keyterms"):
            return
        try:
            if self.sv.keyterms is not None:
                terms = await _maybe_await(self.sv.keyterms())
            else:
                import db       # never open a database from inside a call: use the server's
                database = getattr(db, "_db", None)
                terms = (await asyncio.wait_for(database.run(clinic_keyterms), timeout=0.5)
                         if database is not None else [])
        except Exception as exc:
            logger.warning("[%s] clinic keyterms unavailable: %s", self.call_id, exc)
            return
        self.stt.add_keyterms(terms)
        turn_detector.add_vocabulary(terms)

    # How long the recogniser's watchdog waits on unchanged words, by what they
    # are (turn_detector verdicts): a complete answer to Emma's question needs
    # little more silence; unfinished speech keeps the full second.
    WATCHDOG_BY_VERDICT = {"complete": 0.35, "likely": 0.6, "default": 0.8}   # seconds of quiet on the line

    def _watchdog_for(self, text: str) -> float:
        hint = self._listening_hint()
        key = (text, repr(sorted(hint.items())) if isinstance(hint, dict) else "")
        cached = getattr(self, "_watchdog_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        verdict = turn_detector.classify(text, hint)
        limit = self.WATCHDOG_BY_VERDICT.get(verdict.kind, 1.0)
        self._watchdog_cache = (key, limit)
        return limit

    def _start_turn_task(self, coro):
        self._turn_task = self._spawn(coro)

    async def _greet(self):
        self.turn_id += 1
        tid = self.turn_id
        self._greeting_turn = tid
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
        if self.vad.feed(pcm):
            self._voice_audio_end = self.clock.seconds
            if self.detector.holding:
                self.detector.activity()         # still talking: don't end the held turn yet
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
                self._last_voice = now
                await self.detector.utterance(text, now, "text", now, hold=False)
        elif kind == "end":
            if self.outcome is None and not self._ended_by_emma():
                self.outcome = self._abandoned()
            await self.close()
        elif kind == "hello":
            logger.info("[%s] client protocol v%s", self.call_id, msg.get("v"))
        elif kind == "vad":
            # The browser ducks Emma locally; the server waits for real words.
            pass

    async def _on_playback(self, msg: dict, assumed: bool = False):
        tid = msg.get("turn")
        event = msg.get("event")
        now = time.perf_counter()
        if not assumed:
            self._playback_reports = True
        timer = self._timers.get(tid)
        if event == "started":
            self._audible[tid] = [now, None]
            for old in [k for k in self._audible if isinstance(k, int) and isinstance(tid, int) and k < tid - 10]:
                self._audible.pop(old, None)
            if timer is not None and timer.audible is None:
                timer.audible = now
                self._maybe_log(timer)
        elif event in ("ended", "interrupted"):
            span = self._audible.get(tid)
            if span is not None and span[1] is None:
                span[1] = now
            if event == "interrupted":
                self._trim_history(tid, msg.get("played_ms"))
            if tid == self._last_spoken_turn:
                # The browser knows exactly how much played: refine the estimate.
                self._set_heard(event == "ended"
                                or self._heard_through_question(tid, played_ms=msg.get("played_ms")))
            if tid == self._speaking_turn:
                self._speaking_turn = None
                self._idle_since = now
                if not self.closed:
                    await self._send({"type": "state", "state": "listening"})
            done = self._playback_done.get(tid)
            if done:
                done.set()
            await self._release_deferred(tid)

    # ------------------------------------------------------------------ STT callbacks
    async def _on_speech_started(self, _timestamp):
        self._user_speaking = True
        self._speech_started_at = time.perf_counter()

    async def _on_transcript(self, text: str, is_final: bool):
        now = time.perf_counter()
        self._user_speaking = True
        self._last_voice = now
        self.detector.activity()
        await self._send({"type": "caption", "who": "user", "text": text, "final": is_final})
        if (self._speaking_turn is not None and config.BARGE_IN_ENABLED
                and self._speaking_turn != self._greeting_turn
                and self._emma_audible() and self._is_barge_in(text, now)):
            await self.interrupt("caller speech")

    async def _on_utterance_end(self, text: str, end_sec, source: str = "speech_final",
                                start_sec=None):
        received = time.perf_counter()
        self._user_speaking = False
        self._last_voice = received
        if self.capture is not None:
            self.capture.utterance(text, end_sec, source)
        if self._is_duplicate(text, end_sec, received):
            logger.info("[%s] ignored duplicate utterance (%s): %r", self.call_id, source, text)
            return
        end_wall = self.clock.wall_at(end_sec) or received
        start_wall = self.clock.wall_at(start_sec) if start_sec is not None else None
        self._ladder_reset(received)

        heard_over = self._audible_turn_during(start_wall, end_wall)
        if heard_over is not None and heard_over == self._greeting_turn and self._within_greeting(end_wall):
            # The greeting is 2-3 s long. On laptop speakers its echo comes back too
            # garbled to recognise ("I love it from her" for "I'm Emma from Pearl
            # Dental", 1 Oct), and cut her off mid-greeting. Anything said entirely
            # over it is ignored; a caller who keeps talking past it is heard.
            logger.info("[%s] ignored speech over the greeting: %r", self.call_id, text)
            return
        if heard_over is not None:
            if self._is_echo(text, heard_over, start_wall, end_wall):
                logger.info("[%s] ignored echo of Emma's speech: %r", self.call_id, text)
                return
            if heard_over == self._greeting_turn and is_hello(text):
                # "Hello?" as the line opens, over her greeting: she carries on, as a person would.
                logger.info("[%s] hello over the greeting; carrying on: %r", self.call_id, text)
                return
            if is_backchannel(text) and heard_over == self._speaking_turn:
                if self._question_playing(heard_over, end_wall):
                    # Said while she asks her question: the answer, once she finishes.
                    logger.info("[%s] answer during Emma's question, kept for after it: %r",
                                self.call_id, text)
                    self._deferred = (text, source, heard_over)
                else:
                    logger.info("[%s] backchannel while Emma speaks; not a barge-in: %r",
                                self.call_id, text)
                return
            if not is_backchannel(text) and self._speaking_turn is not None:
                await self.interrupt("caller speech")      # stop her before any hold
        elif is_backchannel(text) and self._reply_pending(received):
            logger.info("[%s] %r adds nothing to the turn being answered; ignored", self.call_id, text)
            return
        await self.detector.utterance(text, end_wall, source, received)

    async def _on_turn_ready(self, text, end_wall, source, received, verdict):
        await self._start_turn(text, end_wall, source, received, verdict)

    async def _release_deferred(self, tid):
        """Emma finished (or was stopped): a backchannel kept during her question becomes the turn."""
        if self._deferred is None or self._deferred[2] != tid:
            return
        text, source, _ = self._deferred
        self._deferred = None
        now = time.perf_counter()
        await self.detector.utterance(text, now, source, now, hold=False)

    def _is_duplicate(self, text: str, end_sec, received: float) -> bool:
        """The same words again for audio already reported (speech_final then UtteranceEnd)."""
        words = " ".join(_plain_words(text))
        last_words, last_end, last_at = self._last_utterance
        duplicate = (bool(words) and words == last_words and received - last_at < 3.0
                     and (end_sec is None or last_end is None or end_sec <= last_end + 0.05))
        if not duplicate:
            self._last_utterance = (words, end_sec, received)
        return duplicate

    def _reply_pending(self, now: float) -> bool:
        """A turn was just committed and its reply is still on its way."""
        running = self._turn_task is not None and not self._turn_task.done()
        return (now - self._last_commit_at < REPLY_PENDING_S and self._turn_phase in ("commit", "speak")
                and (running or self._speaking_turn is not None))

    # ------------------------------------------------------------------ turns
    def _timer(self, tid: int, text: str) -> TurnTimer:
        timer = TurnTimer(call_id=self.call_id, turn=tid, user_text=text)
        self._timers[tid] = timer
        # Only recent turns can still receive playback reports.
        for old in [k for k in self._timers if k < tid - 10]:
            self._timers.pop(old, None)
        return timer

    async def _start_turn(self, text: str, end_wall: float, source: str = "text",
                          received: Optional[float] = None, verdict=None):
        if self.closed:
            return
        if self.listen_only:
            logger.info("[%s] heard (%s%s): %s", self.call_id, source,
                        f", {verdict.label}" if verdict else "", _loggable(text))
            return
        if self._ending:
            logger.info("[%s] caller spoke while the call was ending: %s", self.call_id, _loggable(text))
            return
        if self.operator:
            # Staff have the call: the caller's words go to the transcript and the live panel only.
            self._record("caller", text, {"operator": True})
            self._ladder_reset(time.perf_counter())
            return
        if self._speaking_turn is not None:
            await self.interrupt("new utterance")
        self._deferred = None
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
        self._last_commit_at = time.perf_counter()
        self._hold_on = turn_detector.is_hold_on(text)
        self._ladder_reset(self._last_commit_at)
        timer = self._timer(self.turn_id, text)
        timer.user_end = end_wall
        timer.endpoint_source = source
        timer.stt_event = received
        timer.committed = self._last_commit_at
        timer.detect = verdict.label if verdict is not None else None
        self._start_turn_task(self._run_turn(self.turn_id, text, timer, wait_for))

    async def _run_turn(self, tid: int, text: str, timer: TurnTimer, wait_for):
        if wait_for is not None:
            try:
                await wait_for
            except (asyncio.CancelledError, Exception):
                pass
        self._turn_phase = "nlu"
        logger.info("[%s] caller: %s", self.call_id, _loggable(text))
        await self._send({"type": "state", "state": "thinking"})
        started = time.perf_counter()

        # A receptionist writes down what the caller just told her: a short burst
        # of typing before she answers. It runs while the engine works, so it
        # only adds time when the engine is quicker than the beat.
        beat_ms = 0
        if config.TYPING_SFX and self._expects_information(text):
            beat_ms = random.randint(*config.TYPING_BEAT_MS)
            await self._send({"type": "sfx", "name": "typing", "turn": tid, "after_ms": 120,
                              "duration_ms": TYPING_MAX_MS, "until_speech": True})

        voice = _ReplyVoice(self, tid, timer, started, beat_ms)
        self._voice = voice
        streams = self._streams_sentences()
        filler: list[asyncio.Task] = []
        if self._owed:
            # The outcome of the caller's last request, which they talked over:
            # it is said first, while the engine works on what they just said.
            owed, self._owed = self._owed, None
            voice.put_text(owed)

        def progress(event, **data):
            if event == "llm_start" and not beat_ms:     # typing already fills the pause
                filler.append(self._spawn(self._filler_after(tid, timer, voice)))
            elif event == "before_action":
                # "Let me just check that for you." with typing while the check runs.
                voice.checking(data.get("phrase"), defer=streams)
            elif event == "commit" and self._turn_phase == "nlu":
                self._turn_phase = "commit"

        kwargs = {"on_sentence": voice.on_sentence} if streams else {}
        try:
            try:
                result = await ai_engine.async_process_turn(text, self.s, progress, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("[%s] turn failed: %s", self.call_id, exc, exc_info=True)
                result = ai_engine.TurnResult("" if voice.delivered else phrases.ERROR_REPLY, tier=-1)

            for task in filler:
                if not task.done() and not self.speaker.lock.locked():
                    task.cancel()
            # Recorded once the turn has committed, so a fragment merged into
            # the next utterance is not stored as a turn of its own.
            self._record("caller", text, {"detect": timer.detect, "endpoint_source": timer.endpoint_source})
            timer.tier, timer.nlu_ms = result.tier, result.nlu_ms
            timer.step_before = getattr(result, "step_before", None)
            timer.step_after = getattr(result, "step_after", None)
            timer.goal_before = getattr(result, "goal_before", None)
            timer.goal_after = getattr(result, "goal_after", None)
            timer.streamed = bool(voice.delivered)
            timer.reply_ready = time.perf_counter()
            self._note_action(result)
            self._turn_extra[tid] = {"entities": getattr(result, "entities", None) or None,
                                     "action": getattr(result, "action", None)}
            for old in [k for k in self._turn_extra if k < tid - 10]:
                self._turn_extra.pop(old, None)          # turns that were never spoken
            self._maybe_log(timer)
            remaining = self._remaining_text(result.text or "", voice.delivered,
                                             getattr(result, "spoken_count", None))

            if tid != self.turn_id or tid in self._muted:
                logger.info("[%s] turn %d superseded; not spoken", self.call_id, tid)
                self._keep_owed(tid, result, remaining)
                voice.cancel()
                self._log(timer)
                return
            self._turn_phase = "speak"
            if voice.started or voice.checking_pending:
                await voice.finish(remaining)
            else:
                spoken = self._polish(tid, remaining, timer)
                self._prepare(tid, spoken)          # synthesis runs during the typing beat
                await self._pre_speech_pause(tid, timer, started, beat_ms)
                if not self._may_speak(tid):
                    logger.info("[%s] turn %d superseded while typing; not spoken", self.call_id, tid)
                    self._keep_owed(tid, result, remaining)
                    self.speaker.discard(self._prepared.pop(tid, (None, None))[1])
                    self._log(timer)
                    return
                await self._speak(tid, spoken, timer)
        except asyncio.CancelledError:
            for task in filler:
                task.cancel()
            voice.cancel()
            self.speaker.discard(self._prepared.pop(tid, (None, None))[1])
            raise
        if self.s.closed_conversation:
            self._spawn(self._finish_call(tid))

    def _keep_owed(self, tid: int, result, remaining: str):
        """
        A booking, change or cancellation committed but the caller spoke before
        hearing a word of the outcome (often over "Let me just check that for
        you"): keep it, so they are told before anything else.
        """
        if getattr(result, "action", None) and remaining.strip() and not self._reply_text.get(tid):
            self._owed = remaining.strip()
            logger.info("[%s] outcome of turn %d not heard yet; it will be said next", self.call_id, tid)

    def _prepare(self, tid: int, text: str):
        """Start synthesising a turn's reply now; _speak_part picks it up."""
        for old in [k for k in self._prepared if k < tid]:
            self.speaker.discard(self._prepared.pop(old)[1])
        if text:
            self._prepared[tid] = (text, self.speaker.prepare(text))

    @staticmethod
    def _remaining_text(text: str, delivered: list[str], spoken_count=None) -> str:
        """The part of TurnResult.text not already spoken through on_sentence."""
        if not delivered:
            return text
        norm = lambda t: re.sub(r"\s+", " ", t or "").strip()
        full, said = norm(text), norm(" ".join(delivered))
        if full.startswith(said):
            return full[len(said):].strip()
        count = spoken_count if isinstance(spoken_count, int) else len(delivered)
        return " ".join(split_sentences(text)[count:])

    def _polish(self, tid: int, text: str, timer: TurnTimer, said: Optional[str] = None) -> str:
        """
        Last touches before a reply part is spoken: no "Okay, ..." straight after
        a filler "Okay.", and no sentence repeated word for word back to back.
        `said` is what this turn has already queued (default: already spoken).
        """
        text = (text or "").strip()
        if not text:
            return ""
        said = self._reply_text.get(tid, "") if said is None else said
        if (timer.filler or self._filler_turn == tid) and not said:
            text = strip_opener(text)
        previous = split_sentences(said)
        kept = drop_repeats(split_sentences(text), previous[-1] if previous else "")
        return " ".join(kept)

    def _may_speak(self, tid: int) -> bool:
        return not self.closed and tid == self.turn_id and tid not in self._muted

    async def _pre_speech_pause(self, tid: int, timer: TurnTimer, started: float, beat_ms: int):
        """Let the typing be heard: nothing is said until beat_ms after the turn started."""
        if not beat_ms:
            return
        remaining = beat_ms / 1000 - (time.perf_counter() - started)
        if remaining > 0:
            timer.pause_ms = round(remaining * 1000)
            await asyncio.sleep(remaining)

    def _note_action(self, result):
        action = getattr(result, "action", None)
        if action:
            self.outcome = action
            # The Calendar event follows within a second instead of at the
            # worker's next poll (server.py also wakes it at hang-up).
            try:
                import calendar_sync
                calendar_sync.notify()
            except Exception as exc:
                logger.debug("[%s] calendar wake failed: %s", self.call_id, exc)

    async def _checking_pause(self, tid: int, timer: TurnTimer, phrase: Optional[str] = None):
        """
        "Let me just check that for you." (or the engine's varied checking line)
        then about a second of keyboard typing before the answer, the way a
        receptionist looks something up. The database answers in milliseconds,
        but an instant reply after "let me check" is exactly what gives a
        machine away (docs/NORTH_STAR.md).
        """
        phrase = phrase or phrases.CHECKING
        if not self.speaker.cached_ms(phrase) and not (self.sv.tts or self.sv.fallback_tts):
            phrase = phrases.CHECKING            # no live voice: the pre-rendered standard line
        phrase_ms = self.speaker.cached_ms(phrase)
        if phrase_ms:
            said = await self._say_cached(tid, phrase, timer, kind="checking", wait=True)
        else:
            # A varied line not in the prompt cache: live TTS, length estimated.
            self._speaking_turn, self._emma_text = tid, phrase
            self._playback_done.setdefault(tid, asyncio.Event())
            said = await self.speaker.speak(tid, phrase, timer, kind="checking")
            phrase_ms = int(len(phrase) / CHARS_PER_SECOND * 1000)
        if not said:
            return
        gap_ms = random.randint(CHECK_PAUSE_MS[0], CHECK_PAUSE_MS[1])
        if config.TYPING_SFX:
            await self._send({"type": "sfx", "name": "typing", "turn": tid, "after_ms": phrase_ms + 150,
                              "duration_ms": gap_ms - 250, "until_speech": False})
        await self.speaker.play_silence(tid, gap_ms)

    async def _speak(self, tid: int, text: str, timer: TurnTimer):
        """Speak a whole reply for a turn and mark the end of its audio."""
        if text and text.strip():
            await self._speak_part(tid, text, timer)
        await self._end_speech(tid, timer)

    async def _speak_part(self, tid: int, text: str, timer: TurnTimer, segments=None) -> bool:
        if segments is None:
            prepared_text, prepared = self._prepared.pop(tid, (None, None))
            if prepared is not None and prepared_text == text:
                segments = prepared
            else:
                self.speaker.discard(prepared)
        first = tid not in self._reply_text
        self._speaking_turn = tid
        self._reply_text[tid] = f"{self._reply_text.get(tid, '')} {text}".strip()
        self._emma_text = self._reply_text[tid]
        self._playback_done.setdefault(tid, asyncio.Event())
        for old in [k for k in self._reply_text if k < tid - 10]:
            self._reply_text.pop(old, None)
        if first:
            self._last_spoken_turn = tid
            self._set_heard(False)
            await self._send({"type": "turn", "turn": tid, "phase": "start"})
        await self._send({"type": "caption", "who": "emma", "text": self._reply_text[tid], "final": True})
        if segments is not None:
            return await self.speaker.play(tid, segments, timer)
        return await self.speaker.speak(tid, text, timer)

    async def _end_speech(self, tid: int, timer: TurnTimer):
        """All of a turn's audio has been sent; tidy up if none of it reached the caller."""
        if tid in self._muted or self.closed:
            return
        spoken = self._reply_text.get(tid, "")
        if spoken:
            self._last_reply = spoken
            role = "operator" if tid in self._operator_turns else "emma"
            self._record(role, spoken, {**self._turn_meta(timer), **self._turn_extra.pop(tid, {})})
        await self._send({"type": "turn", "turn": tid, "phase": "audio_done"})
        sent_ms = self.speaker.sent_ms(tid)
        if sent_ms <= 0 and self._speaking_turn in (tid, None):
            # Nothing reached the caller (no TTS configured or it failed).
            self._speaking_turn = None
            self._playback_done.setdefault(tid, asyncio.Event()).set()
            self._set_heard(True)
            self._idle_since = time.perf_counter()
            self._log(timer)
            await self._send({"type": "state", "state": "listening"})
        elif not self._playback_reports:
            self._spawn(self._assume_played(tid, sent_ms))

    async def _assume_played(self, tid: int, sent_ms: float):
        """
        A transport that never reports playback: treat the turn as played once its
        audio has had time to finish, so the silence ladder and barge-in rules
        still work. The browser's own reports take over as soon as one arrives.
        """
        await asyncio.sleep(sent_ms / 1000 + 0.3)
        if not self._playback_reports and not self.closed:
            await self._on_playback({"type": "playback", "turn": tid, "event": "ended"}, assumed=True)

    async def _filler_after(self, tid: int, timer: TurnTimer, voice: _ReplyVoice):
        """A short backchannel if an LLM turn is still thinking after FILLER_AFTER_MS."""
        await asyncio.sleep(config.FILLER_AFTER_MS / 1000)
        if (tid != self.turn_id or self._user_speaking or timer.first_audio_sent is not None
                or voice.started or self._turns_since_filler < 3):
            return
        self._filler_turn = tid
        if await self._say_cached(tid, random.choice(phrases.FILLERS), timer):
            timer.filler = True
            self._turns_since_filler = 0
        elif self._filler_turn == tid:
            self._filler_turn = None

    async def _say_cached(self, tid: int, text: str, timer: TurnTimer, kind: str = "filler",
                          wait: bool = False) -> bool:
        """A pre-rendered phrase now; a filler (wait=False) is skipped if anything else is playing."""
        if tid != self.turn_id:
            return False
        self._speaking_turn = tid
        self._emma_text = text
        self._playback_done.setdefault(tid, asyncio.Event())
        return await self.speaker.play_cached(tid, text, timer, kind=kind, wait=wait)

    async def _finish_call(self, tid: int):
        done = self._playback_done.get(tid)
        if done is not None:
            try:
                await asyncio.wait_for(done.wait(), timeout=FINAL_PLAYBACK_TIMEOUT_S)
            except asyncio.TimeoutError:
                logger.warning("[%s] timed out waiting for final playback", self.call_id)
        if self.outcome is None:
            self.outcome = "completed"
        await self._send({"type": "bye"})
        await self.close()

    # ------------------------------------------------------------------ barge-in
    def _emma_audible(self) -> bool:
        """Emma's current turn is playing on the caller's side right now."""
        tid = self._speaking_turn
        if tid is None:
            return False
        if not self._playback_reports:
            return True        # a transport without playback reports: assume audible while speaking
        span = self._audible.get(tid)
        return span is not None and span[1] is None

    def _within_greeting(self, end_wall: float) -> bool:
        """These words ended before the greeting had finished playing (plus a short tail)."""
        span = self._audible.get(self._greeting_turn)
        if span is None:
            return self._speaking_turn == self._greeting_turn
        ended = span[1]
        return ended is None or end_wall <= ended + ECHO_TAIL_S

    def _audible_turn_during(self, start_wall: Optional[float], end_wall: float) -> Optional[int]:
        """Emma's turn that was audible while the caller spoke these words, if any."""
        if not self._playback_reports:
            return self._speaking_turn
        start = start_wall if start_wall is not None else end_wall - 0.5
        for tid in sorted(self._audible, reverse=True):
            began, ended = self._audible[tid]
            if began <= end_wall and start <= (ended if ended is not None else float("inf")) + ECHO_TAIL_S:
                return tid
        return None

    def _is_echo(self, text: str, tid: Optional[int], start_wall: Optional[float], end_wall: float) -> bool:
        """What Emma was saying at that moment, heard back through the caller's mic."""
        if tid is None:
            return False
        span = self._audible.get(tid)
        emma = ""
        if span is not None:
            words = max(1, len(_plain_words(text)))
            start = start_wall if start_wall is not None else end_wall - 0.4 * words
            emma = self.speaker.window_text(tid, (start - span[0] - ECHO_WINDOW_SLACK_S) * 1000,
                                            (end_wall - span[0] + 0.3) * 1000)
        if not emma:
            emma = self.speaker.spoken_text(tid) or (self._emma_text if tid == self._speaking_turn else "")
        return looks_like_echo(text, emma, greeting=tid == self._greeting_turn)

    def _is_barge_in(self, text: str, now: float) -> bool:
        """Real caller speech worth stopping Emma for (from live interim words)."""
        words = _plain_words(text)
        if not words or is_backchannel(text):
            return False
        if self._speaking_turn == self._greeting_turn and is_hello(text):
            return False
        if len(words) < config.BARGE_IN_MIN_WORDS and words[0] not in _URGENT_WORDS:
            return False
        return not self._is_echo(text, self._speaking_turn, None, now)

    def _heard_through_question(self, tid: int, at_wall: Optional[float] = None, played_ms=None) -> bool:
        """
        Recap-heard rule (plan 5.6): an interrupted reply still counts as heard
        when every sentence before its closing question had finished playing.
        """
        start_ms = self.speaker.question_start_ms(tid)
        if start_ms is None:
            return False
        if played_ms is None:
            span = self._audible.get(tid)
            if span is None or at_wall is None:
                return False
            played_ms = (at_wall - span[0]) * 1000
        try:
            return float(played_ms) >= start_ms
        except (TypeError, ValueError):
            return False

    def _question_playing(self, tid: int, at_wall: float) -> bool:
        """Emma had reached her closing question when the caller said this."""
        span = self._audible.get(tid)
        start_ms = self.speaker.question_start_ms(tid)
        if span is None or start_ms is None:
            return False
        return (at_wall - span[0]) * 1000 >= start_ms

    async def interrupt(self, reason: str) -> bool:
        tid = self._speaking_turn
        if tid is None:
            return False
        now = time.perf_counter()
        started = self._speech_started_at or now
        self._muted.add(tid)
        if self._turn_task is not None and not self._turn_task.done() and self._turn_phase == "speak":
            self._turn_task.cancel()
        if self._voice is not None and self._voice.tid == tid:
            self._voice.cancel()
        self.speaker.cancel()
        await self.t.flush(tid)
        self._speaking_turn = None
        span = self._audible.get(tid)
        if span is not None and span[1] is None:
            span[1] = now
        done = self._playback_done.get(tid)
        if done:
            done.set()
        if tid == self._last_spoken_turn:
            self._set_heard(self._heard_through_question(tid, at_wall=now))
        timer = self._timers.get(tid)
        if timer is not None:
            timer.barge_in = True
            timer.barge_in_ms = (now - started) * 1000
            self._log(timer)
        logger.info("[%s] barge-in on turn %d (%s)", self.call_id, tid, reason)
        await self._send({"type": "state", "state": "listening"})
        return True

    def _trim_history(self, tid, played_ms):
        """Keep only the part of the interrupted reply the caller actually heard."""
        history = getattr(self.s, "history", None)
        if played_ms is None or not history:
            return
        last = history[-1]
        if not isinstance(last, dict) or last.get("role") != "assistant":
            return
        content = last.get("content", "")
        if tid in self.speaker.timeline:
            heard_chars = int(len(content) * self.speaker.heard_fraction(tid, float(played_ms)))
        else:
            heard_chars = int(float(played_ms) / 1000 * CHARS_PER_SECOND)
        if heard_chars < len(content):
            cut = content[:heard_chars].rsplit(" ", 1)[0]
            last["content"] = f"{cut}... [interrupted by caller]"

    # ------------------------------------------------------------------ silence, length, lost audio
    def _caller_active(self, now: Optional[float] = None) -> bool:
        now = now or time.perf_counter()
        if self._last_voice is not None and now - self._last_voice < CALLER_ACTIVE_S:
            return True
        return bool(self._user_speaking and self._speech_started_at is not None
                    and now - self._speech_started_at < 3.0)

    def _idle(self, now: float) -> bool:
        """Nobody is talking and nothing is being worked on: the line is silent."""
        if self._turn_task is not None and not self._turn_task.done():
            return False
        return not (self._speaking_turn is not None or self.detector.holding or self._caller_active(now))

    def _ladder_reset(self, now: float):
        self._ladder_step = 0
        self._idle_since = now

    async def _watch(self):
        """Silence ladder and call length, checked a few times a second."""
        try:
            while not self.closed:
                await asyncio.sleep(WATCH_INTERVAL_S)
                if self._ending or self.closed:
                    continue
                now = time.perf_counter()
                await self._check_call_length(now)
                if self._ending:
                    continue
                if not self._idle(now) or self.operator:
                    self._idle_since = now          # staff run their own pace while they have the call
                    continue
                if self._owed and now - self._idle_since >= OWED_AFTER_S:
                    owed, self._owed = self._owed, None
                    await self._say_line(owed)
                    continue
                step_s = HOLD_ON_STEP_S if self._hold_on else SILENCE_STEP_S
                if now - self._idle_since >= step_s:
                    self._idle_since = now
                    await self._silence_step()
        except asyncio.CancelledError:
            pass

    async def _silence_step(self):
        self._ladder_step += 1
        logger.info("[%s] silence step %d", self.call_id, self._ladder_step)
        if self._ladder_step == 1:
            question = self._last_question()
            line = self._pick(STILL_THERE_LINES)
            await self._say_line(f"{line} {question}".strip() if question else line)
        elif self._ladder_step == 2:
            await self._say_line(self._pick(CANT_HEAR_LINES))
        else:
            await self._say_line(self._pick(SILENCE_GOODBYE_LINES), then_close=True, outcome="silence")

    def _last_question(self) -> str:
        """Emma's last question, re-asked after "Are you still there?"."""
        sentences = split_sentences(self._last_reply)
        if sentences and sentences[-1].endswith("?") and sentences[-1] not in CACHEABLE_LINES:
            return sentences[-1] if len(sentences[-1].split()) <= 16 else ""
        return ""

    async def _check_call_length(self, now: float):
        elapsed = now - self._started_at
        if not self._wrapped_up and elapsed >= WRAP_UP_AT_S and self._idle(now):
            self._wrapped_up = True
            await self._say_line(self._pick(WRAP_UP_LINES))
        elif elapsed >= MAX_CALL_S and (self._idle(now) or elapsed >= MAX_CALL_S + 30):
            phone = self._caller_phone()
            if phone and self.outcome is None:
                await self._create_task("callback", "high", phone,
                                        "Call reached the 15-minute limit before it was finished.")
            lines = LONG_CALL_GOODBYE_CALLBACK_LINES if phone else LONG_CALL_GOODBYE_LINES
            await self.interrupt("call length")
            await self._say_line(self._pick(lines), then_close=True, outcome="max_length", final=True)

    async def _on_stt_lost(self):
        """Speech recognition could not be restored: say so kindly and end the call."""
        if self.closed or self._ending:
            return
        self._ending = True
        self.detector.cancel()
        phone = self._caller_phone()
        if phone:
            await self._create_task("callback", "high", phone,
                                    "Call dropped: Emma could not hear the caller (speech recognition failed).")
        # Let a reply already on its way finish first.
        if self._turn_task is not None and not self._turn_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._turn_task), timeout=8)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass
        lines = CANT_HEAR_CALLBACK_LINES if phone else CANT_HEAR_GOODBYE_LINES
        await self._say_line(self._pick(lines), then_close=True, outcome="stt_failure", final=True)

    # ------------------------------------------------------------------ staff takeover
    async def take_over(self) -> bool:
        """Staff take the call: Emma stops, says a colleague is taking over, and waits for typed lines."""
        if self.closed or self._ending or self.operator:
            return False
        if self._speaking_turn is not None:
            await self.interrupt("staff takeover")
        task = self._turn_task
        if task is not None and not task.done() and self._turn_phase == "nlu":
            task.cancel()                       # nothing committed yet: staff answer instead
        self.operator = True
        self._resume_question = self._last_question()     # what Emma asks again when she gets the call back
        await self._say_line(self._pick(TAKEOVER_LINES))
        return True

    async def operator_say(self, text: str) -> bool:
        """A line typed by staff, spoken on the call (and recorded as staff's)."""
        text = (text or "").strip()
        if not self.operator or self.closed or self._ending or not text:
            return False
        if self._speaking_turn is not None:
            await self.interrupt("staff line")
        await self._say_line(text)
        self._operator_turns.add(self.turn_id)
        return True

    async def hand_back(self) -> bool:
        """Emma takes the call back and picks up where it was."""
        if not self.operator or self.closed or self._ending:
            return False
        self.operator = False
        question = getattr(self, "_resume_question", "")
        line = self._pick(HAND_BACK_LINES)
        await self._say_line(f"{line} {question}".strip() if question else f"{line} How can I help?")
        return True

    async def end_by_staff(self, note: str = "") -> bool:
        """End the call politely and leave the front desk a callback task."""
        if self.closed or self._ending:
            return False
        if self._speaking_turn is not None:
            await self.interrupt("staff ended the call")
        await self._create_task("callback", "high", self._caller_phone(),
                                (note or "").strip() or "Staff ended the call from the dashboard; please call back.")
        await self._say_line(self._pick(STAFF_GOODBYE_LINES), then_close=True, outcome="ended_by_staff",
                             final=True)
        return True

    async def _say_line(self, text: str, then_close: bool = False, outcome: Optional[str] = None,
                        final: bool = False):
        """
        A line of Emma's own (silence ladder, call length, lost audio) as its own
        turn. A goodbye that the caller talks over does not end the call, unless
        it is final (the call cannot continue).
        """
        self.turn_id += 1
        tid = self.turn_id
        timer = self._timer(tid, "")
        timer.tier, timer.reply_ready = -1, time.perf_counter()
        if final:
            self._ending = True

        async def run():
            self._turn_phase = "speak"
            await self._speak(tid, text, timer)
            if then_close and (final or tid not in self._muted):
                self._ending = True
                self.outcome = outcome or self.outcome
                await self._finish_call(tid)

        self._start_turn_task(run())

    def _pick(self, lines: list[str]) -> str:
        fresh = [line for line in lines if line not in self._used_lines] or lines
        line = random.choice(fresh)
        self._used_lines.add(line)
        return line

    def _caller_phone(self) -> Optional[str]:
        """The caller's number in E.164 if the conversation has one."""
        candidates = [getattr(self.s, attr, None) for attr in ("phone_e164", "phone", "temp_phone")]
        caller = getattr(self.s, "caller", None)              # an R2 CallContext keeps it on ctx.caller
        if caller is not None:
            candidates.insert(0, getattr(caller, "phone_e164", None))
        slots = getattr(self.s, "slots", None)
        if isinstance(slots, dict):
            candidates += [slots.get("phone_e164"), slots.get("phone")]
        for value in candidates:
            e164 = phones.to_e164(str(value)) if value else None
            if e164:
                return e164
        return None

    async def _create_task(self, kind: str, priority: str, phone: Optional[str], note: str):
        """A staff task through tasks.py (backend track), if it and the database are available."""
        try:
            import db
            import tasks
            database = getattr(db, "_db", None)
            create = getattr(tasks, "create_task", None)
            if database is None or create is None:
                return None
            return await database.run(create, kind=kind, priority=priority, phone_e164=phone,
                                      note=note, call_id=self.call_id)
        except Exception as exc:
            logger.warning("[%s] could not create %s task: %s", self.call_id, kind, exc)
            return None

    def _ended_by_emma(self) -> bool:
        return bool(getattr(self.s, "closed_conversation", False)) or self._ending

    def _abandoned(self) -> str:
        where = getattr(self.s, "goal", None) or getattr(self.s, "step", None) or getattr(self.s, "pending", None)
        where = getattr(where, "value", where)                 # an R2 Goal is a str enum
        return f"abandoned@{where}" if where is not None else "abandoned"

    # ------------------------------------------------------------------ transcript
    @property
    def caller_phone(self) -> Optional[str]:
        """For the call record (server.py reads it when closing the transcript)."""
        return self._caller_phone()

    @property
    def keep_transcript(self) -> bool:
        """False once the caller asked not to be kept (the engine sets s.keep_transcript)."""
        return bool(getattr(self.s, "keep_transcript", True))

    async def _open_recording(self):
        """Our own transcript recorder when the server did not attach one and the database is open."""
        if self.recorder is not None:
            return
        try:
            import db
            import recording
            if getattr(db, "_db", None) is None or not hasattr(recording, "CallRecorder"):
                return
            self.recorder = recording.CallRecorder(self.call_id, direction="inbound")
            self._own_recorder = True
            await _maybe_await(self.recorder.start())
        except Exception as exc:
            logger.debug("[%s] transcript recording off: %s", self.call_id, exc)
            self.recorder, self._own_recorder = None, False

    def _record(self, role: str, text: str, meta: Optional[dict] = None):
        if self.recorder is None or not text:
            return
        try:
            result = self.recorder.turn(role, text, {k: v for k, v in (meta or {}).items() if v is not None})
            if inspect.isawaitable(result):
                self._spawn(result)
        except Exception as exc:
            logger.debug("[%s] transcript turn failed: %s", self.call_id, exc)

    @staticmethod
    def _turn_meta(timer: Optional[TurnTimer]) -> dict:
        if timer is None:
            return {}
        return {"tier": timer.tier, "goal_before": timer.goal_before, "goal_after": timer.goal_after,
                "step_before": timer.step_before, "step_after": timer.step_after}

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

    async def _release_holds(self):
        """Slots offered on this call are free again at once, not when their hold expires (plan 5.7)."""
        try:
            import db
            import scheduling
            database = getattr(db, "_db", None)     # never open a database from a call: use the server's
            if database is not None:
                freed = await asyncio.wait_for(database.run(scheduling.release_holds, self.call_id), timeout=2)
                if freed:
                    logger.info("[%s] released %d held slot(s) at hang-up", self.call_id, freed)
        except Exception as exc:
            logger.debug("[%s] hold release failed: %s", self.call_id, exc)

    async def close(self):
        if self.closed:
            return
        self.closed = True
        current = asyncio.current_task()
        # A hang-up while a booking, move or cancellation is being written lets
        # it finish (it is atomic either way) so the call records what happened.
        committing = (self._turn_task is not None and not self._turn_task.done()
                      and self._turn_task is not current and self._turn_phase == "commit")
        if committing:
            deadline = time.perf_counter() + COMMIT_ON_HANGUP_S
            while self._turn_phase == "commit" and not self._turn_task.done() and time.perf_counter() < deadline:
                await asyncio.sleep(0.05)
        for task in list(self._tasks):
            if task is not current:
                task.cancel()
        self.detector.cancel()
        self.speaker.cancel()
        if self.capture is not None:
            self.capture.close()
        if self.outcome in ("booked", "rescheduled", "cancelled") and committing:
            self.outcome = f"{self.outcome}_hangup"          # done, but the caller never heard it
        if self.outcome is None:
            self.outcome = "completed" if self._ended_by_emma() else self._abandoned()
        await self._release_holds()
        if self.recorder is not None and self._own_recorder:
            try:
                await _maybe_await(self.recorder.end(self.outcome, keep_transcript=self.keep_transcript,
                                                     caller_phone=self.caller_phone))
            except Exception as exc:
                logger.debug("[%s] transcript end failed: %s", self.call_id, exc)
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
        logger.info("[%s] call closed (%s)", self.call_id, self.outcome or "no outcome")
