"""
The one place the harness touches the dialogue engine.

Everything else in harness/ sees a conversation as caller words in, Emma's
words out, plus the database. This module turns that into engine calls and
back, so when the 12-step engine is replaced only this file changes.

It codes against the engine facade agreed for the rebuild and falls back to
today's engine wherever a new name is missing:

    ai_engine.new_session(call_id)        else ai_engine.SessionState()
    async_process_turn(..., on_sentence)  passed only if the engine accepts it
    TurnResult.action                     else inferred from the database diff
    TurnResult.goal_before / goal_after   else None (step_before / step_after today)
    ai_engine.listening_hint(s)           else what Emma's last line asked (harness/lines.py)
    ai_engine.expects_information(s, t)   recorded as the typing beat when present
    s.last_reply_heard                    set before every turn (False after a simulated barge-in)

Engines (`engine=`):
    None       whatever ai_engine.new_session() hands out (config.R2_ENGINE; off by default)
    "legacy"   today's 12-step machine (ai_engine.SessionState), whatever the flag says
    "r2"       the R2 engine (a dialogue CallContext), whatever the flag says; ai_engine
               routes a turn by the session's type, so both run side by side

Modes:
    offline    the fake NLU (harness/fake_nlu.py) replaces the Gemini request and
               any other model call fails fast, so nothing touches the network
    nlu_down   no NLU at all: the engine's own "model is down" path
    live       real Gemini with the .env key; each model call is recorded so a
               quota or timeout failure is never blamed on the dialogue

Offline mode plugs the fake NLU into both engines at once, so a run never
depends on which one ai_engine routes to: today's through a patched
ai_engine.async_extract_entities_with_llm, and the R2 one through
ai_engine.install_test_nlu(reader) when it exists (reader(text, expect) ->
fake_nlu reading dict; returns an uninstall callable or a context manager),
else through nlu.use_backend with a backend that streams the fake reading in
the R2 schema (docs/R2_DESIGN.md section 15).

Each turn record also carries the R2 engine's own view when there is one:
TurnResult.entities, and the newest TurnTrace from s.trace (tier, acts,
dropped validators, fallback). In live mode a turn whose trace says the model
fell back counts as a failed model call (llm_ok False), like a 429.

Silence never reaches the engine: on a real call the call session's silence
ladder speaks ("Are you still there?", then "I can't hear you", then a
goodbye), so silence_turn() reproduces that ladder instead.
"""

import asyncio
import dataclasses
import inspect
import json
import logging
import os
import time
from contextlib import ExitStack, contextmanager
from datetime import date, datetime, time as dtime
from enum import Enum
from typing import Optional

import ai_engine
import config
import llm
import phrases

from harness import fake_nlu, lines
from harness import world as world_mod

logger = logging.getLogger(__name__)

MODES = ("offline", "live", "nlu_down")
# Live runs score the conversation, not the speed, and every tools/converse.py
# command is a fresh process with a cold Gemini client (first head about 2.3 s
# on 5 Oct, against the call's 1.6 s budget). So live mode gives the model's
# head this long, and the whole reply LIVE_REPLY_TIMEOUT_S (config.GEMINI_TIMEOUT
# is 2.5 s), before the turn falls back; engine time per turn is still recorded,
# so the report shows the real latency.
LIVE_HEAD_DEADLINE_S = 4.0
LIVE_REPLY_TIMEOUT_S = 6.0
ENGINES = (None, "legacy", "r2")
# A free-tier key allows only a handful of requests a minute, and the R2
# engine's one streamed request per turn has no time to wait out a 429 (its
# head deadline is seconds). So a live run can pace itself: each worker
# process starts a caller turn at most once every HARNESS_LIVE_INTERVAL_S
# seconds (0, the default, means no pacing), and after a turn that hit the
# quota it pauses for the first back-off before the next one. The waits fall
# before the turn's clock starts, so engine_ms stays the engine's own time.
LIVE_MIN_INTERVAL_S = float(os.getenv("HARNESS_LIVE_INTERVAL_S", "0") or 0)
_live_next_turn = 0.0                   # process-wide time.monotonic() the next live turn may start

# The call session's silence ladder (call_session.py). Copied as a fallback so
# the harness still runs while that module is being edited.
_STILL_THERE = ["Are you still there?", "Hello, are you still with me?", "Sorry, are you still there?"]
_CANT_HEAR = ["I'm not hearing anything on the line. If you're there, just say hello.",
              "I can't hear you at the moment. If you're there, could you say something?"]
_SILENCE_GOODBYE = ["I think we've lost each other. Do call us back whenever you're ready. Bye for now.",
                    "Seems the line's gone quiet, so I'll let you go. Call us back anytime. Take care."]


def _silence_lines() -> tuple:
    try:
        import call_session
        return (list(call_session.STILL_THERE_LINES), list(call_session.CANT_HEAR_LINES),
                list(call_session.SILENCE_GOODBYE_LINES))
    except Exception:                         # mid-edit elsewhere, or renamed: use the copies
        return _STILL_THERE, _CANT_HEAR, _SILENCE_GOODBYE


class OfflineLLM:
    """
    Stands in for llm.get_nlu() when nothing may reach the network: every
    request "fails", exactly as an unavailable model does, and is counted.
    Unknown methods (a streaming call in the new engine, say) fail the same way.
    """

    model = "offline"
    available = False
    keys: list = []

    def __init__(self):
        self.calls = 0

    @property
    def usable(self) -> bool:
        return False

    async def generate_json(self, *args, **kwargs):
        self.calls += 1
        return None

    async def verify_model(self, *args, **kwargs):
        return False

    async def keep_verified(self, *args, **kwargs):
        return None

    def status(self) -> dict:
        return {"model": "offline", "verified": False, "keys": []}

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        async def _unavailable(*args, **kwargs):
            self.calls += 1
            return None
        return _unavailable


class _LogCatcher(logging.Handler):
    """Collects warnings from the llm module during one turn (timeouts, quota)."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record):
        try:
            self.messages.append(record.getMessage())
        except Exception:
            pass


def _error_code(exc) -> Optional[int]:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    try:
        return int(code)
    except (TypeError, ValueError):
        return None


class EngineAdapter:
    """One conversation's connection to the engine. Not shared between conversations."""

    def __init__(self, mode: str = "offline", *, call_id: Optional[str] = None, quota_retries: int = 2,
                 quota_backoff_s: tuple = (10.0, 30.0), engine: Optional[str] = None):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if engine not in ENGINES:
            raise ValueError(f"engine must be one of {ENGINES}")
        self.mode = mode
        self.engine = engine
        self.engine_used: Optional[str] = None       # "r2" | "legacy", set by the runner from the session
        self.call_id = call_id
        self.last_emma = ""
        self.quota_retries = quota_retries
        self.quota_backoff_s = quota_backoff_s
        self.quota_exhausted = False
        self._consecutive_quota = 0
        self._expect_now: Optional[str] = None
        self._turn_llm: dict = {}
        self._catcher: Optional[_LogCatcher] = None
        self._last_exc = None

    # -- what the engine offers ---------------------------------------------
    @staticmethod
    def features() -> dict:
        params = inspect.signature(ai_engine.async_process_turn).parameters
        return {
            "new_session": callable(getattr(ai_engine, "new_session", None)),
            "listening_hint": callable(getattr(ai_engine, "listening_hint", None)),
            "expects_information": callable(getattr(ai_engine, "expects_information", None)),
            "on_sentence": "on_sentence" in params,
            "install_test_nlu": callable(getattr(ai_engine, "install_test_nlu", None)),
            "turn_result_action": "action" in getattr(ai_engine.TurnResult, "__dataclass_fields__", {}),
        }

    def new_session(self):
        """A per-call context: ai_engine.new_session() when present, else today's SessionState."""
        # The greeting rotation remembers the last greeting process-wide; forget
        # it so a seeded conversation greets the same way in any process.
        if hasattr(phrases, "_last_greeting"):
            phrases._last_greeting = None
        if self.engine == "r2":
            from dialogue.context import new_context
            return new_context(self.call_id)
        if self.engine == "legacy":
            return ai_engine.SessionState()
        factory = getattr(ai_engine, "new_session", None)
        if callable(factory):
            try:
                return factory(call_id=self.call_id)
            except TypeError:
                return factory()
        return ai_engine.SessionState()

    # -- installing the NLU for this mode -----------------------------------
    @contextmanager
    def installed(self):
        """Put the mode's NLU in place for the duration (and take it out again)."""
        saved_nlu = llm._nlu
        saved_extract = ai_engine.async_extract_entities_with_llm
        saved_ikp = llm._is_key_problem
        uninstall = None
        llm_logger = logging.getLogger("llm")
        saved_level = llm_logger.level
        stack = ExitStack()
        try:
            if self.mode in ("offline", "nlu_down"):
                llm._nlu = OfflineLLM()
            r2 = _r2_nlu_module()
            if self.mode == "offline":
                # Both engines get the fake NLU, today's through its extract
                # function and the R2 one through install_test_nlu (or nlu's
                # backend switch), so a run never depends on which engine
                # ai_engine routes to (config.R2_ENGINE).
                ai_engine.async_extract_entities_with_llm = self._legacy_extract
                hook = getattr(ai_engine, "install_test_nlu", None)
                if callable(hook) and (r2 is None or _reader_mapping_ready(r2)):
                    uninstall = hook(self._generic_reader)
                    if hasattr(uninstall, "__enter__"):
                        uninstall.__enter__()
                elif r2 is not None:
                    stack.enter_context(r2.use_backend(_R2FakeBackend(self)))
            if self.mode == "nlu_down" and r2 is not None:
                stack.enter_context(r2.use_backend(_R2DownBackend()))
            if self.mode == "live":
                if r2 is not None and hasattr(r2, "HEAD_DEADLINE_S"):
                    saved_head = r2.HEAD_DEADLINE_S
                    r2.HEAD_DEADLINE_S = max(saved_head, LIVE_HEAD_DEADLINE_S)
                    stack.callback(setattr, r2, "HEAD_DEADLINE_S", saved_head)
                stack.callback(setattr, config, "GEMINI_TIMEOUT", config.GEMINI_TIMEOUT)
                config.GEMINI_TIMEOUT = max(config.GEMINI_TIMEOUT, LIVE_REPLY_TIMEOUT_S)
                llm._nlu = None                      # a fresh client bound to this event loop
                self._wrap_live(llm.get_nlu())

                def recording_ikp(exc):
                    self._last_exc = exc
                    return saved_ikp(exc)
                llm._is_key_problem = recording_ikp
                self._catcher = _LogCatcher()
                # The catcher needs llm's warnings even when the CLI quiets the console.
                llm_logger.setLevel(logging.WARNING)
                llm_logger.addHandler(self._catcher)
            yield self
        finally:
            stack.close()
            if uninstall is not None:
                if hasattr(uninstall, "__exit__"):
                    uninstall.__exit__(None, None, None)
                elif callable(uninstall):
                    uninstall()
            ai_engine.async_extract_entities_with_llm = saved_extract
            llm._is_key_problem = saved_ikp
            llm._nlu = saved_nlu
            llm_logger.setLevel(saved_level)
            if self._catcher is not None:
                llm_logger.removeHandler(self._catcher)
                self._catcher = None

    def _generic_reader(self, text, expect=None):
        """For a new engine's install_test_nlu: the fake reading plus a grounded answer."""
        reading = fake_nlu.read(text, expect=expect or self._expect_now)
        self._note_fake_call()
        out = reading.as_dict()
        out["answer"] = self._answer_for(reading.question) if reading.question else None
        return out

    def _note_fake_call(self):
        self._turn_llm.update(called=True, ok=True, source="fake")

    @staticmethod
    def _answer_for(question: str) -> Optional[str]:
        """
        The grounded answer, or None when the facts don't cover it: the R2
        engine then says a verified fact or its honest "not sure" line, and
        today's engine falls back to its own escalation line, exactly as each
        does when the real model has no answer.
        """
        text, _fact_id = fake_nlu.answer(question)
        if text == "@HONEST@":
            return config.HONEST_LINE
        return text or None

    async def _legacy_extract(self, user_text, s=None):
        """Today's engine: the fake reading in the dict shape of the Gemini NLU request."""
        if not user_text or not user_text.strip():
            return {}
        reading = fake_nlu.read(user_text, expect=self._expect_now)
        self._note_fake_call()
        conf = reading.yes_no
        if conf is None and reading.expect == "yes_no" and reading.intent == "cancel":
            conf = "no"      # what the model does with "I'd like to cancel" after "Shall I book it?"
        question = reading.question
        # A question that only names a service or a day ("How much is a root
        # canal?", "What time do you open on Saturday?") is a pure question to
        # the model too: user_query set, no booking details.
        pure = reading.intent == "question"
        return {
            "patient_name": reading.name,
            "phone_number": reading.phone,
            "dental_service": None if pure else (reading.service or reading.service_phrase),
            "appointment_date": None if pure else reading.date_phrase,
            "appointment_time": None if pure else reading.time_phrase,
            "confirmation": conf,
            "user_query": question,
            # Today's NLU prompt has the model answer with the escalation line when
            # the facts don't cover a question; the fake does the same for it.
            "answer": (self._answer_for(question) or getattr(ai_engine, "ESCALATION_LINE", None))
            if question else None,
        }

    def _wrap_live(self, nlu):
        """Record every Gemini request; wait and retry on a 429 instead of failing the turn."""
        original = nlu.generate_json

        async def generate_json(contents, system, max_tokens=200):
            rec = self._turn_llm
            rec["called"] = True
            rec["source"] = "gemini"
            rec["calls"] = rec.get("calls", 0) + 1
            attempt = 0
            while True:
                self._last_exc = None
                if self._catcher is not None:
                    self._catcher.messages.clear()
                result = await original(contents, system, max_tokens)
                if result is not None:
                    self._consecutive_quota = 0
                    if rec.get("ok") is None:
                        rec["ok"] = True
                    return result
                code = _error_code(self._last_exc)
                messages = " ".join(self._catcher.messages) if self._catcher else ""
                quota = code == 429 or "RESOURCE_EXHAUSTED" in messages or " 429" in messages
                if quota and attempt < self.quota_retries and not self.quota_exhausted:
                    wait = self.quota_backoff_s[min(attempt, len(self.quota_backoff_s) - 1)]
                    rec["quota_wait_ms"] = rec.get("quota_wait_ms", 0) + int(wait * 1000)
                    for key in getattr(nlu, "keys", []):
                        key.cooldown_until = 0.0
                    await asyncio.sleep(wait)
                    attempt += 1
                    continue
                rec["ok"] = False
                if quota:
                    self._consecutive_quota += 1
                    if self._consecutive_quota >= 3:
                        self.quota_exhausted = True
                    rec["error"] = "quota"
                elif not getattr(nlu, "usable", True):
                    rec["error"] = "unavailable (no key, or the model check failed)"
                elif "timed out" in messages:
                    rec["error"] = "timeout"
                else:
                    rec["error"] = f"error {code}" if code else "failed"
                return None

        nlu.generate_json = generate_json

        original_stream = getattr(nlu, "generate_json_stream", None)
        if callable(original_stream):
            # The R2 engine streams one structured reply per turn. A stream that
            # yields nothing (or raises) is a failed model call; there is no
            # retry here because the engine's own budget rules own that.
            async def generate_json_stream(*args, **kwargs):
                rec = self._turn_llm
                rec["called"] = True
                rec["source"] = "gemini"
                rec["calls"] = rec.get("calls", 0) + 1
                if self._catcher is not None:
                    self._catcher.messages.clear()
                chunks = 0
                try:
                    async for chunk in original_stream(*args, **kwargs):
                        chunks += 1
                        yield chunk
                except Exception as exc:
                    rec["ok"] = False
                    rec["error"] = "quota" if _error_code(exc) == 429 else f"{type(exc).__name__}"
                    raise
                if chunks:
                    if rec.get("ok") is None:
                        rec["ok"] = True
                else:
                    messages = " ".join(self._catcher.messages) if self._catcher else ""
                    rec["ok"] = False
                    rec["error"] = ("quota" if "429" in messages or "RESOURCE_EXHAUSTED" in messages
                                    else "timeout" if "timed out" in messages else "no output")

            nlu.generate_json_stream = generate_json_stream

    # -- one turn -------------------------------------------------------------
    def expect(self, s) -> str:
        """What Emma is waiting for: the engine's listening hint, else read off her last line."""
        hint = getattr(ai_engine, "listening_hint", None)
        if callable(hint):
            try:
                value = hint(s) or {}
                if value.get("expect"):
                    return value["expect"]
            except Exception as exc:          # a broken hint must not stop the run
                logger.debug("listening_hint failed: %s", exc)
        return lines.classify_ask(self.last_emma).expect

    async def _pace(self):
        """Live runs: wait for this process's next turn slot, then free the keys a 429 cooled down."""
        global _live_next_turn
        wait = _live_next_turn - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
            for key in getattr(llm.get_nlu(), "keys", []):
                key.cooldown_until = 0.0
        _live_next_turn = time.monotonic() + LIVE_MIN_INTERVAL_S

    async def turn(self, s, text: str, *, heard_previous: bool = True) -> dict:
        """
        One caller turn ("" = silence, or the greeting on the first call).
        Returns Emma's reply and everything measurable about the turn.
        """
        text = text or ""
        self._expect_now = self.expect(s)
        try:
            s.last_reply_heard = bool(heard_previous)
        except AttributeError:
            pass
        typing = None
        beat = getattr(ai_engine, "expects_information", None)
        if callable(beat) and text:
            try:
                typing = bool(beat(s, text))
            except Exception:
                typing = None
        before = world_mod.current_snapshot()
        trace_before = _last_trace(s)
        self._turn_llm = {"called": False, "ok": None, "source": None, "error": None, "quota_wait_ms": 0}
        events: list[str] = []
        streamed: list[str] = []

        def progress(event, **_data):
            events.append(event)

        async def on_sentence(sentence):
            streamed.append(sentence)

        if self.mode == "live" and text:
            await self._pace()
        kwargs = {"progress": progress}
        if self.features()["on_sentence"] or is_r2_session(s):
            kwargs["on_sentence"] = on_sentence
        started = time.perf_counter()
        engine_error = None
        result = None
        try:
            result = await ai_engine.async_process_turn(text, s, **kwargs)
        except Exception as exc:              # recorded as a crash, the call carries on like call_session does
            engine_error = f"{type(exc).__name__}: {exc}"
            logger.error("engine turn failed: %s", engine_error, exc_info=True)
        engine_ms = (time.perf_counter() - started) * 1000
        if self.mode == "live" and self._turn_llm.get("error") == "quota":
            global _live_next_turn
            _live_next_turn = max(_live_next_turn, time.monotonic() + self.quota_backoff_s[0])
        change = world_mod.changes_since(before)

        reply = getattr(result, "text", None) if result is not None else None
        if reply is None:
            reply = phrases.ERROR_REPLY
        reported = getattr(result, "action", None) if result is not None else None
        inferred = _infer_action(change)
        rec = self._turn_llm
        trace_after = _last_trace(s)
        trace = plain(trace_after) if trace_after is not None and trace_after is not trace_before else None
        if trace and trace.get("fallback") and self.mode == "live" and rec.get("ok") is not False:
            # The R2 engine ran this turn without the model (quota, timeout, breaker open).
            rec["called"] = True
            rec["ok"] = False
            rec["error"] = rec.get("error") or "fallback (model unavailable or too slow)"
        hint_after = None
        hint = getattr(ai_engine, "listening_hint", None)
        if callable(hint):
            try:
                hint_after = hint(s)
            except Exception:
                hint_after = None
        stream_ok = None
        if streamed:
            sentences = lines.split_sentences(reply)
            stream_ok = sentences[:len(streamed)] == [x.strip() for x in streamed]
        self.last_emma = reply
        return {
            "caller": text,
            "emma": reply,
            "heard_previous": bool(heard_previous),
            "expect": self._expect_now,
            "tier": getattr(result, "tier", None),
            "nlu_ms": round(getattr(result, "nlu_ms", 0.0) or 0.0, 1),
            "engine_ms": round(engine_ms - rec.get("quota_wait_ms", 0), 1),
            "quota_wait_ms": rec.get("quota_wait_ms", 0),
            "llm_called": bool(rec.get("called")),
            "llm_ok": rec.get("ok"),
            "llm_source": rec.get("source"),
            "llm_error": rec.get("error"),
            "step_before": getattr(result, "step_before", None),
            "step_after": getattr(result, "step_after", None),
            "goal_before": getattr(result, "goal_before", None),
            "goal_after": getattr(result, "goal_after", None),
            "action": reported or inferred,
            "action_reported": reported,
            "db_change": _compact(change),
            "closed": bool(getattr(s, "closed_conversation", False)),
            "typing_beat": typing,
            "events": events,
            "streamed": streamed,
            "stream_ok": stream_ok,
            "spoken_count": getattr(result, "spoken_count", None),
            "hint_after": plain(hint_after),
            "entities": plain(getattr(result, "entities", None)) or None,
            "trace": trace,
            "engine_error": engine_error,
        }

    def silence_turn(self, s, step: int, *, rng=None, said: tuple = ()) -> dict:
        """
        The caller said nothing for a whole silence step (`step` 1, 2, 3 in a
        row). Mirrors call_session's ladder: "Are you still there?" plus her
        last short question, then "I can't hear you", then a goodbye that ends
        the call. The engine is not called, exactly as on a real call. Like
        call_session._pick, a variant already heard in this call (`said`: her
        earlier lines) is used again only when every variant has been.
        """
        still, cant, bye = _silence_lines()

        def pick(options):
            fresh = [o for o in options if not any(o in line for line in said)] or options
            return rng.choice(fresh) if rng is not None else fresh[0]
        if step <= 1:
            sentences = lines.split_sentences(self.last_emma)
            question = sentences[-1] if sentences and sentences[-1].endswith("?") \
                and len(sentences[-1].split()) <= 16 and sentences[-1] not in still else ""
            reply = f"{pick(still)} {question}".strip()
        elif step == 2:
            reply = pick(cant)
        else:
            reply = pick(bye)
        expect = self.expect(s)
        self.last_emma = reply
        empty = {"booked": [], "cancelled": [], "rescheduled": [], "tasks": []}
        return {
            "caller": "", "emma": reply, "heard_previous": True, "expect": expect, "tier": None, "nlu_ms": 0.0,
            "engine_ms": 0.0, "quota_wait_ms": 0, "llm_called": False, "llm_ok": None, "llm_source": None,
            "llm_error": None, "step_before": getattr(s, "step", None), "step_after": getattr(s, "step", None),
            "goal_before": None, "goal_after": None, "action": None, "action_reported": None, "db_change": empty,
            "closed": step >= 3, "typing_beat": None, "events": ["silence"], "streamed": [], "stream_ok": None,
            "spoken_count": None, "hint_after": None, "entities": None, "trace": None, "engine_error": None,
            "silence_step": step,
        }

    # -- the result -----------------------------------------------------------
    @staticmethod
    def outcome(baseline: dict) -> dict:
        """What the whole call changed in the database (booked / cancelled / rescheduled / tasks)."""
        change = world_mod.changes_since(baseline)
        out = _compact(change)
        kinds = [k for k in ("booked", "cancelled", "rescheduled") if out[k]]
        out["kind"] = kinds[0] if len(kinds) == 1 else ("mixed" if kinds else "none")
        return out


def is_r2_session(s) -> bool:
    """An R2 CallContext (it streams through on_sentence even while config.R2_ENGINE is off)."""
    try:
        from dialogue.context import CallContext
    except Exception:
        return False
    return isinstance(s, CallContext)


def engine_label(s) -> str:
    """ "r2" or "legacy": which engine a session runs on (records and reports)."""
    return "r2" if is_r2_session(s) else "legacy"


def _last_trace(s):
    """The newest TurnTrace on an R2 context (s.trace), or None (today's SessionState has none)."""
    trace = getattr(s, "trace", None)
    if isinstance(trace, list) and trace:
        return trace[-1]
    return None


def plain(value):
    """Dataclasses, enums and containers as plain JSON-able data (enum members become their values)."""
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(plain(k)): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [plain(v) for v in value]
    if isinstance(value, (datetime, date, dtime)):
        return value.isoformat()
    return str(value)


def _infer_action(change: dict) -> Optional[str]:
    if change["booked"]:
        return "booked"
    if change["rescheduled"]:
        return "rescheduled"
    if change["cancelled"]:
        return "cancelled"
    return None


def _appt(a: dict) -> dict:
    return {k: a.get(k) for k in ("id", "patient", "phone", "service", "branch", "doctor", "start", "status")}


def _compact(change: dict) -> dict:
    return {
        "booked": [_appt(a) for a in change["booked"]],
        "cancelled": [_appt(a) for a in change["cancelled"]],
        "rescheduled": [{"id": r["after"]["id"], "from": r["before"]["start"], "to": r["after"]["start"],
                         "from_doctor": r["before"]["doctor"], **_appt(r["after"])} for r in change["rescheduled"]],
        "tasks": [{k: t.get(k) for k in ("id", "kind", "priority", "note")} for t in change["tasks"]],
    }


# ---------------------------------------------------------------- the R2 engine's NLU backend

_CAPABILITY = ("how can you help", "what can you", "what all can you", "who are you", "who am i speaking",
               "what do you do", "what exactly do you", "about the clinic", "about your clinic", "tell me what you")
_CHITCHAT = ("how's your day", "how is your day", "how are you", "raining", "weather", "hope you're", "parking the car")


def _r2_nlu_module():
    """
    nlu.py with its backend switch, or None. Installing a backend is harmless
    while today's engine runs (it never asks nlu), so it is installed whenever
    the switch exists, and the R2 engine finds it the moment the flag flips.
    """
    try:
        import nlu
    except Exception:
        return None
    return nlu if callable(getattr(nlu, "use_backend", None)) else None


def _reader_mapping_ready(nlu_module) -> bool:
    """
    install_test_nlu streams nlu.reading_to_object(reading); until that mapping
    is written (it raises NotImplementedError in the stub) the harness streams
    its own r2_object() instead, so offline runs keep working mid-build.
    """
    mapping = getattr(nlu_module, "reading_to_object", None)
    if not callable(mapping):
        return False
    try:
        mapping({"text": ""}, None)
    except NotImplementedError:
        return False
    except Exception:                         # written; it just doesn't like an empty reading
        return True
    return True


def r2_object(reading: "fake_nlu.Reading", answer: Optional[str] = None) -> dict:
    """
    A fake reading in the R2 structured-output shape (nlu.HEAD_KEYS + say/ask).
    Only understanding is filled in; `ask` stays empty and `next_goal` null so
    the engine's own Python goal and fallback lines decide the question, which
    is what offline runs should exercise.
    """
    low = lines.norm(reading.text)
    acts = []
    if reading.fragment:
        acts.append("fragment")
    if reading.intent == "bot":
        acts.append("robot_question")
    elif reading.intent == "human":
        acts.append("wants_human")
    if reading.intent == "end":
        acts.append("end")
    if reading.nonanswer:
        acts.append("non_answer")
    if reading.question and reading.intent not in ("bot",):
        if any(p in low for p in _CAPABILITY):
            acts.append("capability")
        elif any(p in low for p in _CHITCHAT):
            acts.append("chitchat")
        else:
            acts.append("question")
    elif any(p in low for p in _CHITCHAT):
        acts.append("chitchat")
    if reading.slots or reading.yes_no or reading.service_phrase or reading.date_phrase or reading.time_phrase:
        acts.append("answer")
    if not acts:
        acts.append("unclear")
    intent = {"book": "book", "cancel": "cancel", "reschedule": "reschedule", "check": "check",
              "question": "info", "emergency": "book"}.get(reading.intent or "")
    red_flag = any(w in low for w in ("can't breathe", "cant breathe", "can't swallow", "swallowing", "spreading"))
    emergency = "red_flag" if red_flag else ("urgent" if reading.intent == "emergency" else "none")
    obj = {
        "acts": acts, "intent": intent, "emergency": emergency, "confirmation": reading.yes_no,
        "correction": bool(reading.yes_no == "no" and reading.slots),
        "name": reading.name if not reading.patient else None, "phone_digits": reading.phone,
        "for_someone_else": True if reading.patient else None, "patient_name": reading.patient,
        "service": reading.service, "service_phrase": reading.service_phrase, "branch": reading.branch,
        "doctor_phrase": reading.doctor, "date_phrase": reading.date_phrase, "time_phrase": reading.time_phrase,
        "date_iso_hint": reading.date.isoformat() if reading.date else None,
        "question": reading.question, "clinical": lines.looks_clinical(reading.text),
        "next_goal": None, "say": answer or "", "ask": "",
    }
    return {k: v for k, v in obj.items() if v not in (None, "") or k in ("acts", "intent", "next_goal", "say", "ask")}


class _R2FakeBackend:
    """Streams r2_object() for the caller's words in the brief, in small chunks like Gemini."""

    usable = True

    def __init__(self, adapter: EngineAdapter):
        self.adapter = adapter

    async def stream(self, system: str, contents: str, schema: dict, deadline: float):
        marker = "<<<"
        caller = contents.rsplit(marker, 1)[-1].split(">>>", 1)[0] if marker in contents else contents
        reading = fake_nlu.read(caller.strip(), expect=self.adapter._expect_now)
        self.adapter._note_fake_call()
        answer = None
        if reading.question:
            text, _fid = fake_nlu.answer(reading.question)
            answer = config.HONEST_LINE if text == "@HONEST@" else text
        payload = json.dumps(r2_object(reading, answer), ensure_ascii=False)
        for i in range(0, len(payload), 24):
            yield payload[i:i + 24]


class _R2DownBackend:
    """The model is down: unusable, streams nothing (the engine's fallback path)."""

    usable = False

    async def stream(self, system: str, contents: str, schema: dict, deadline: float):
        return
        yield  # pragma: no cover - makes this an async generator
