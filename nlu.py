"""
The one model call per LLM turn (docs/R2_DESIGN.md, sections 6 and 7).

A single streamed Gemini request with structured output returns, in this key
order: what the caller meant (acts, intent, entities...), the model's guess
at Emma's next goal, then her proposed reply split into `say` (acknowledge /
answer, no question) and `ask` (at most one question toward next_goal).

Because the understanding keys come first, Python has them before the reply
starts streaming: it applies them, computes its own goal, and only then lets
the reply through, sentence by sentence as each one completes and passes the
validators. Nothing here decides anything; every value is raw until
apply.py validates it.

Never a second model call in a turn: one request, at most one retry and only
before any token arrived (llm.py's budget rules), then the no-model fallback.

Backends stream raw text chunks of the JSON object: GeminiBackend (the real
model, llm.GeminiNLU.generate_json_stream), FakeNLU (scripted objects) and
ReaderBackend (the harness's fake reader). All three go through the same
StreamParser, so tests exercise the exact path a live call takes.

Owner in Sprint 1b: E2 (with llm.py streaming, dialogue/brief.py and
dialogue/validate.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from typing import AsyncIterator, Optional, Protocol

from dialogue.context import Act, Emergency, Goal, Intent, Understanding

logger = logging.getLogger(__name__)

# Budgets (seconds). The head must arrive quickly or the turn falls back; the
# whole reply must finish within config.GEMINI_TIMEOUT. config.NLU_HEAD_DEADLINE_S
# overrides the design's 1.6 s: Flash-Lite's first token alone took 1.1-2.1 s
# with the full brief in the Sprint 1b integration runs, so live testing may
# need about 2.2 s until the brief is cached or trimmed.
def _head_deadline_default() -> float:
    try:
        import config
        return float(getattr(config, "NLU_HEAD_DEADLINE_S", 1.6))
    except Exception:                               # pragma: no cover
        return 1.6


HEAD_DEADLINE_S = _head_deadline_default()

# Key order the model must follow (response schema propertyOrdering).
HEAD_KEYS = (
    "acts", "intent", "emergency", "confirmation", "correction",
    "name", "name_spelled", "phone_digits", "for_someone_else", "patient_name", "relation", "age",
    "service", "service_phrase", "branch", "branch_any", "doctor", "doctor_phrase", "doctor_gender",
    "date_phrase", "time_phrase", "date_iso_hint", "choice_index", "reject_options",
    "appt_date_phrase", "cancel_reason", "question", "faq_ids", "clinical", "wants_callback",
    "next_goal",
)
REPLY_KEYS = ("say", "ask")
KEY_ORDER = HEAD_KEYS + REPLY_KEYS


def build_schema(services: tuple = (), branches: tuple = (), doctors: tuple = ()) -> dict:
    """
    The Gemini response schema (OpenAPI subset, as google-genai accepts it).
    Catalog names become enums so the model can only pick real ones; a doctor
    it doesn't recognise goes in doctor_phrase. Only acts, intent, next_goal,
    say and ask are required: optional keys are omitted when empty, which
    keeps the head short and the first reply sentence early.
    """
    def s(**kw):
        return {"type": "string", **kw}

    def enum(values, nullable=True):
        return {"type": "string", "enum": list(values), "nullable": nullable}

    props = {
        "acts": {"type": "array", "items": enum([a.value for a in Act], nullable=False)},
        "intent": enum([i.value for i in Intent], nullable=False),
        "emergency": enum([e.value for e in Emergency], nullable=False),
        "confirmation": enum(["yes", "no"]),
        "correction": {"type": "boolean"},
        "name": s(nullable=True),
        "name_spelled": s(nullable=True),
        "phone_digits": s(nullable=True, description="digits only"),
        "for_someone_else": {"type": "boolean", "nullable": True},
        "patient_name": s(nullable=True),
        "relation": s(nullable=True),
        "age": {"type": "integer", "nullable": True},
        "service": enum(services) if services else s(nullable=True),
        "service_phrase": s(nullable=True),
        "branch": enum(branches) if branches else s(nullable=True),
        "branch_any": {"type": "boolean"},
        "doctor": enum(doctors) if doctors else s(nullable=True),
        "doctor_phrase": s(nullable=True),
        "doctor_gender": enum(["female", "male"]),
        "date_phrase": s(nullable=True),
        "time_phrase": s(nullable=True),
        "date_iso_hint": s(nullable=True, description="YYYY-MM-DD"),
        "choice_index": {"type": "integer", "nullable": True},
        "reject_options": {"type": "boolean"},
        "appt_date_phrase": s(nullable=True),
        "cancel_reason": s(nullable=True),
        "question": s(nullable=True),
        "faq_ids": {"type": "array", "items": s()},
        "clinical": {"type": "boolean"},
        "wants_callback": {"type": "boolean", "nullable": True},
        "next_goal": enum([g.value for g in Goal], nullable=False),
        "say": s(),
        "ask": s(),
    }
    return {
        "type": "object",
        "properties": props,
        "required": ["acts", "intent", "next_goal", "say", "ask"],
        "propertyOrdering": list(KEY_ORDER),
    }


_NULLS = {"null", "none", "", "undefined", "n/a", "na", "unknown"}
_MAX_TEXT = 120                     # a name or phrase longer than this is the model rambling
_DIGIT_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "nought": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
_REPEATS = {"double": 2, "triple": 3}


def _text(value, limit: int = _MAX_TEXT) -> Optional[str]:
    if not isinstance(value, str):
        return None
    clean = " ".join(value.split())
    if clean.lower() in _NULLS or len(clean) > limit:
        return None
    return clean


def _flag(value, default=False):
    """A JSON boolean (or its string form); anything else is the default."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return default


def _int(value, low: int, high: int) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, int) and low <= value <= high:
        return value
    return None


def _enum(cls, value):
    if isinstance(value, str):
        try:
            return cls(value.strip().lower())
        except ValueError:
            return None
    return None


def digits_only(value) -> Optional[str]:
    """
    The digits in a phone value, in order. The model is asked for digits
    only, but it (and Deepgram's smart_format) may write "(789) 937-7462",
    "98450 12345" or "nine eight double four": punctuation is ignored and
    digit words count, as in turn_detector.spoken_digits.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        return None
    tokens = re.findall(r"[a-z]+|\d", value.lower())
    digits, repeat = [], 1
    for tok in tokens:
        if tok in _REPEATS:
            repeat = _REPEATS[tok]
            continue
        digit = tok if tok.isdigit() else _DIGIT_WORDS.get(tok)
        if digit is None:
            repeat = 1
            continue
        digits.append(digit * repeat)
        repeat = 1
    out = "".join(digits)
    return out if 0 < len(out) <= 15 else None


def _canon(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _catalog_value(value, allowed: Optional[list]) -> tuple:
    """
    (canonical, leftover) for a catalog enum key. With the schema's enum the
    value must be one of them (case and punctuation aside: "Dr. Rao" is
    "Dr Rao"); a name that isn't is returned as leftover so the caller's
    words aren't lost (an unknown doctor becomes doctor_phrase).
    """
    text = _text(value)
    if text is None:
        return None, None
    if not allowed:
        return text, None
    key = _canon(text)
    for name in allowed:
        if _canon(name) == key:
            return name, None
    return None, text


def _schema_enum(schema: Optional[dict], key: str) -> Optional[list]:
    try:
        values = schema["properties"][key].get("enum")
    except (KeyError, TypeError, AttributeError):
        return None
    return list(values) if values else None


def from_json(data: dict, raw_text: str = "", schema: Optional[dict] = None) -> Understanding:
    """
    A parsed model object -> Understanding. Unknown keys are dropped; enums,
    digits, lengths and types are checked (bad values become None), so
    nothing malformed reaches apply.py. source = "llm".

    `schema` (the build_schema dict the request used) supplies the catalog
    enums: Gemini honours them, but a service, branch or doctor outside them
    is dropped here as well (Z4). An unknown doctor's name moves to
    doctor_phrase and an unknown service to service_phrase, which is where
    the model should have put them, so Emma can still say "we don't have a
    Dr Sharma". intent "none" means no intent was expressed: None.
    """
    u = Understanding(source="llm", raw_text=raw_text or "")
    if not isinstance(data, dict):
        return u
    acts = data.get("acts")
    if isinstance(acts, str):
        acts = [acts]
    if isinstance(acts, list):
        seen = []
        for act in acts:
            value = _enum(Act, act)
            if value is not None and value.value not in seen:
                seen.append(value.value)
        u.acts = seen
    intent = _enum(Intent, data.get("intent"))
    u.intent = None if intent in (None, Intent.NONE) else intent
    u.emergency = _enum(Emergency, data.get("emergency")) or Emergency.NONE
    confirmation = _text(data.get("confirmation"))
    u.confirmation = confirmation.lower() if confirmation and confirmation.lower() in ("yes", "no") else None
    u.correction = _flag(data.get("correction"))

    u.name = _text(data.get("name"), 60)
    spelled = _text(data.get("name_spelled"), 60)
    letters = re.sub(r"[^A-Za-z]", "", spelled or "").upper()
    u.name_spelled = letters or None
    u.phone_digits = digits_only(data.get("phone_digits"))
    u.for_someone_else = _flag(data.get("for_someone_else"), None)
    u.patient_name = _text(data.get("patient_name"), 60)
    u.relation = _text(data.get("relation"), 30)
    u.age = _int(data.get("age"), 0, 120)

    u.service, unknown_service = _catalog_value(data.get("service"), _schema_enum(schema, "service"))
    u.service_phrase = _text(data.get("service_phrase")) or unknown_service
    u.branch, _unknown_branch = _catalog_value(data.get("branch"), _schema_enum(schema, "branch"))
    u.branch_any = _flag(data.get("branch_any"))
    u.doctor, unknown_doctor = _catalog_value(data.get("doctor"), _schema_enum(schema, "doctor"))
    u.doctor_phrase = _text(data.get("doctor_phrase"), 60) or unknown_doctor
    gender = _text(data.get("doctor_gender"))
    u.doctor_gender = gender.lower() if gender and gender.lower() in ("female", "male") else None
    u.date_phrase = _text(data.get("date_phrase"))
    u.time_phrase = _text(data.get("time_phrase"))
    hint = _text(data.get("date_iso_hint"), 10)
    try:
        u.date_iso_hint = date.fromisoformat(hint).isoformat() if hint else None
    except ValueError:
        u.date_iso_hint = None
    u.choice_index = _int(data.get("choice_index"), 1, 10)
    u.reject_options = _flag(data.get("reject_options"))

    u.appt_date_phrase = _text(data.get("appt_date_phrase"))
    u.cancel_reason = _text(data.get("cancel_reason"), 200)
    u.question = _text(data.get("question"), 200)
    faq = data.get("faq_ids")
    if isinstance(faq, list):
        u.faq_ids = [f for f in (_text(x, 60) for x in faq) if f][:5]
    u.clinical = _flag(data.get("clinical"))
    u.wants_callback = _flag(data.get("wants_callback"), None)

    u.next_goal = _enum(Goal, data.get("next_goal"))
    u.say = _text(data.get("say"), 600) or ""
    u.ask = _text(data.get("ask"), 300) or ""
    return u


# ---------------------------------------------------------------- streaming


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[\"'A-Z0-9])")
_ABBREVIATION_END = re.compile(r"\b(?:Dr|Mr|Mrs|Ms|St)\.$")


class StreamParser:
    """
    Incremental reader for the streamed JSON object.

    feed(chunk) as text arrives. `head` becomes a complete dict as soon as
    the "say" key starts (every key before it is closed by then). The string
    values of "say" and "ask" are unescaped as they stream, and
    pop_sentences(key) returns each sentence once it is final: ended by . ! ?
    followed by whitespace or the closing quote (speech.split_sentences rules,
    so "Dr. Rao" is not a sentence end). If the model ignores the key order,
    head stays None until finish() parses the whole object.

    It is a character-level state machine whose state survives between
    chunks, so where the network splits the text (inside a key, mid-escape,
    between the two halves of a surrogate pair) changes nothing.
    """

    def __init__(self):
        self.buffer = ""
        self.head: Optional[dict] = None
        self.done = False
        self._stack: list = []              # open brackets: "{" / "["
        self._in_string = False
        self._escape = False
        self._unicode: Optional[str] = None  # hex digits of a \\uXXXX being read
        self._high: Optional[int] = None     # a high surrogate waiting for its pair
        self._expect_key = False
        self._string_is_key = False
        self._string_start = 0
        self._key_chars: list = []
        self._key: Optional[str] = None
        self._target: Optional[str] = None   # the reply key whose value is streaming
        self._values: dict = {}              # reply key -> decoded text so far
        self._closed: set = set()            # reply keys whose string value has ended
        self._released: dict = {}            # reply key -> characters already given out as sentences
        self._final: Optional[dict] = None

    # -- feeding ---------------------------------------------------------------
    def feed(self, chunk: str) -> None:
        if not chunk or self.done:
            return
        base = len(self.buffer)
        self.buffer += chunk
        for offset, ch in enumerate(chunk):
            self._step(ch, base + offset)

    def _step(self, ch: str, pos: int) -> None:
        if self._in_string:
            self._string_char(ch, pos)
            return
        depth = len(self._stack)
        if ch == '"':
            if depth == 0:
                return                              # prose before the object
            self._in_string = True
            self._string_is_key = depth == 1 and self._expect_key
            self._string_start = pos
            self._key_chars = []
            if not self._string_is_key and depth == 1 and self._key in REPLY_KEYS:
                self._target = self._key
                self._values.setdefault(self._key, "")
            return
        if ch in "{[":
            if depth == 0 and ch != "{":
                return
            self._stack.append(ch)
            if len(self._stack) == 1:
                self._expect_key = True
            return
        if ch in "}]":
            if self._stack:
                self._stack.pop()
                if not self._stack:
                    self.done = True
            return
        if depth == 1:
            if ch == ",":
                self._expect_key = True
            elif ch == ":":
                self._expect_key = False

    def _string_char(self, ch: str, pos: int) -> None:
        if self._unicode is not None:
            self._unicode += ch
            if len(self._unicode) == 4:
                try:
                    code = int(self._unicode, 16)
                except ValueError:
                    code = 0xFFFD
                self._unicode = None
                self._emit_code(code)
            return
        if self._escape:
            self._escape = False
            if ch == "u":
                self._unicode = ""
                return
            self._emit(_ESCAPES.get(ch, ch))
            return
        if ch == "\\":
            self._escape = True
            return
        if ch == '"':
            self._in_string = False
            self._flush_high()
            if self._string_is_key:
                self._key = "".join(self._key_chars)
                if self._key in REPLY_KEYS and self.head is None:
                    self._make_head(self._string_start)
            elif self._target is not None:
                self._closed.add(self._target)
                self._target = None
            return
        self._emit(ch)

    def _emit_code(self, code: int) -> None:
        if 0xD800 <= code <= 0xDBFF:
            self._flush_high()
            self._high = code
            return
        if 0xDC00 <= code <= 0xDFFF and self._high is not None:
            combined = 0x10000 + ((self._high - 0xD800) << 10) + (code - 0xDC00)
            self._high = None
            self._emit(chr(combined))
            return
        self._flush_high()
        self._emit(chr(code) if not 0xD800 <= code <= 0xDFFF else "�")

    def _flush_high(self) -> None:
        if self._high is not None:
            self._high = None
            self._emit("�")

    def _emit(self, text: str) -> None:
        if self._high is not None and text:
            self._high = None
            text = "�" + text
        if self._string_is_key:
            self._key_chars.append(text)
        elif self._target is not None:
            self._values[self._target] += text

    def _make_head(self, key_quote: int) -> None:
        """Everything before the first reply key is the understanding: close it and parse it."""
        start = self.buffer.find("{")
        body = self.buffer[start:key_quote].rstrip().rstrip(",")
        try:
            data = json.loads(body + "}")
        except (json.JSONDecodeError, ValueError):
            return                              # malformed: finish() gets another chance
        # Without acts or intent the model put the reply first: wait for the whole object.
        if isinstance(data, dict) and ("acts" in data or "intent" in data):
            self.head = data

    # -- reading ---------------------------------------------------------------
    def value(self, key: str) -> str:
        """The decoded text of a reply key so far ("" if it hasn't started)."""
        return self._values.get(key, "")

    def closed(self, key: str) -> bool:
        """True once the key's string value has ended (it is complete)."""
        return key in self._closed

    def pop_sentences(self, key: str) -> list:
        text = self._values.get(key, "")
        start = self._released.get(key, 0)
        pending = text[start:]
        out, last = [], 0
        for match in _SENTENCE_END.finditer(pending):
            candidate = pending[last:match.start()]
            if _ABBREVIATION_END.search(candidate):
                continue                            # "Dr. Rao": the sentence goes on
            if candidate.strip():
                out.append(" ".join(candidate.split()))
            last = match.end()
        if key in self._closed:
            rest = " ".join(pending[last:].split())
            if rest:
                out.extend(_split_sentences(rest))
            last = len(pending)
        self._released[key] = start + last
        return out

    def finish(self) -> dict:
        """The whole object (tolerant of fences / trailing prose, like llm.parse_json_object); {} if unusable."""
        if self._final is not None:
            return self._final
        import llm
        data = llm.parse_json_object(self.buffer)
        complete = bool(data)
        if not data:
            data = self._repair()
        self._final = data if isinstance(data, dict) else {}
        if self._final:
            if self.head is None:
                self.head = {k: v for k, v in self._final.items() if k not in REPLY_KEYS}
            for key in REPLY_KEYS:
                value = self._final.get(key)
                if key not in self._values and isinstance(value, str):
                    self._values[key] = value
                if complete and key in self._values:
                    self._closed.add(key)
        return self._final

    def _repair(self) -> dict:
        """A stream cut off mid-object: close what is open and keep the head; a cut-off reply stays unfinished."""
        start = self.buffer.find("{")
        if start < 0:
            return {}
        text = self.buffer[start:]
        if self._in_string:
            text = re.sub(r"\\(u[0-9a-fA-F]{0,3})?$", "", text) + '"'
        text = text.rstrip().rstrip(",:")
        for bracket in reversed(self._stack):
            text += "}" if bracket == "{" else "]"
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "/": "/", "\\": "\\", '"': '"'}


def _split_sentences(text: str) -> list:
    """speech.split_sentences, imported lazily so nlu stays importable without the audio stack."""
    try:
        from speech import split_sentences
    except Exception:                               # pragma: no cover - speech is always present
        return [text]
    return split_sentences(text)


class NLUStream:
    """
    One in-flight model call for one turn, as the engine consumes it:

        stream = await nlu.understand_stream(brief)
        u = await stream.head()                    # Understanding (say/ask empty) or None -> fallback
        ... apply u, compute the goal ...
        async for sentence in stream.sentences("say"): ...   # final sentences only
        ask = await stream.text("ask")             # the whole ask ("" if it never came)
        await stream.aclose()                      # stop reading; cancels the request

    Every await respects the turn deadline; on timeout or error the pending
    value is None / empty and the engine uses its fallback lines.

    The head has HEAD_DEADLINE_S from the start of the request; the whole
    reply has config.GEMINI_TIMEOUT. A head that misses its deadline closes
    the request: the turn has fallen back by then, and a reply written for an
    understanding Python never used would not fit.
    """

    def __init__(self, backend=None, brief=None, *, head_deadline_s: Optional[float] = None,
                 total_s: Optional[float] = None):
        self.brief = brief
        self.parser = StreamParser()
        self.started = time.monotonic()
        self.head_deadline = self.started + (HEAD_DEADLINE_S if head_deadline_s is None else head_deadline_s)
        self.deadline = self.started + (_total_budget() if total_s is None else total_s)
        self.head_deadline = min(self.head_deadline, self.deadline)
        self.head_ms: Optional[float] = None       # time to a usable head (logs, TurnTrace.nlu_ms)
        self.error: Optional[str] = None            # why the stream ended early, for logs
        self._ended = backend is None
        self._changed = asyncio.Event()
        self._head: Optional[Understanding] = None
        self._head_done = False
        self._task: Optional[asyncio.Task] = None
        if backend is not None:
            self._task = asyncio.ensure_future(self._pump(backend))

    @property
    def ended(self) -> bool:
        return self._ended

    async def _pump(self, backend) -> None:
        brief = self.brief
        agen = None
        try:
            agen = backend.stream(brief.system, brief.contents, brief.schema, self.deadline)
            iterator = agen.__aiter__()
            while True:
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    self.error = "timeout"
                    break
                try:
                    chunk = await asyncio.wait_for(iterator.__anext__(), remaining)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    self.error = "timeout"
                    break
                if chunk:
                    self.parser.feed(chunk)
                    self._changed.set()
        except asyncio.CancelledError:
            self.error = self.error or "closed"
            raise
        except Exception as exc:                    # a backend bug must never break the turn
            self.error = f"{type(exc).__name__}: {str(exc)[:120]}"
            logger.warning("NLU stream failed: %s", self.error)
        finally:
            if agen is not None and hasattr(agen, "aclose"):
                try:
                    await agen.aclose()
                except BaseException:               # closing a cancelled request may raise anything
                    pass
            self.parser.finish()
            self._ended = True
            self._changed.set()

    async def _wait(self, ready, until: float) -> bool:
        """Wait until ready() is true, the stream ended, or `until` passed. Returns ready()."""
        while not ready() and not self._ended:
            remaining = until - time.monotonic()
            if remaining <= 0:
                return ready()
            self._changed.clear()
            try:
                await asyncio.wait_for(self._changed.wait(), remaining)
            except asyncio.TimeoutError:
                return ready()
        return ready()

    async def head(self) -> Optional[Understanding]:
        if self._head_done:
            return self._head
        await self._wait(lambda: self.parser.head is not None, self.head_deadline)
        data = self.parser.head
        if data is None and self._ended:
            self.parser.finish()
            data = self.parser.head
        self._head_done = True
        if not data:
            if not self._ended:
                logger.info("NLU head missed its %.1fs deadline; using the fallback", HEAD_DEADLINE_S)
                await self.aclose()
            return None
        raw = caller_words(getattr(self.brief, "contents", "") or "")
        self._head = from_json(data, raw_text=raw, schema=getattr(self.brief, "schema", None))
        self._head.say, self._head.ask = "", ""
        self.head_ms = (time.monotonic() - self.started) * 1000
        return self._head

    async def sentences(self, key: str) -> AsyncIterator[str]:
        parser = self.parser
        while True:
            for sentence in parser.pop_sentences(key):
                yield sentence
            if parser.closed(key) or self._ended:
                for sentence in parser.pop_sentences(key):
                    yield sentence
                return
            await self._wait_change()
            if time.monotonic() >= self.deadline and not parser.closed(key):
                return                              # stalled: what's final was given, the rest falls back

    async def _wait_change(self) -> None:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0 or self._ended:
            return
        self._changed.clear()
        try:
            await asyncio.wait_for(self._changed.wait(), remaining)
        except asyncio.TimeoutError:
            pass

    async def text(self, key: str) -> str:
        await self._wait(lambda: self.parser.closed(key), self.deadline)
        return " ".join(self.parser.value(key).split()) if self.parser.closed(key) else ""

    async def aclose(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except BaseException:
                pass
        self._ended = True
        self._changed.set()


def _total_budget() -> float:
    try:
        import config
        return float(config.GEMINI_TIMEOUT)
    except Exception:                               # pragma: no cover
        return 2.5


def _backend_usable(backend) -> bool:
    try:
        return bool(backend.usable)
    except Exception:
        return False


async def understand_stream(brief) -> NLUStream:
    """
    Start the model call for this turn's brief (dialogue.brief.Brief) on the
    current backend. Returns immediately; the request runs in the background.
    With the model unusable (no key, failed check, breaker open) the stream's
    head() returns None at once, so a dead model never costs the caller a wait.
    """
    backend = get_backend()
    if not _backend_usable(backend):
        stream = NLUStream(None, brief)
        stream.error = "unusable"
        return stream
    return NLUStream(backend, brief)


# ---------------------------------------------------------------- backends


class Backend(Protocol):
    """
    Raw text chunks of one structured, streamed response. `deadline` is an
    absolute time.monotonic() value: the backend must not run past it (the
    stream also enforces it from outside).
    """

    def stream(self, system: str, contents: str, schema: dict, deadline: float) -> AsyncIterator[str]:
        ...

    @property
    def usable(self) -> bool:
        ...


class GeminiBackend:
    """The real backend: llm.get_nlu().generate_json_stream(...), one request with the key and breaker rules."""

    @property
    def usable(self) -> bool:
        import llm
        return llm.get_nlu().usable

    def stream(self, system: str, contents: str, schema: dict, deadline: float) -> AsyncIterator[str]:
        import llm
        return llm.get_nlu().generate_json_stream(contents, system, schema=schema, deadline=deadline)


@dataclass
class FakeNLU:
    """
    Deterministic backend for tests and harness scenarios (no network).

    `script` maps the caller's words (lower-cased, stripped) to the object the
    model would return, or is a list consumed one per call. A value of None
    simulates the model being down for that turn. Objects are serialised in
    KEY_ORDER and streamed in small chunks, so the streaming path is
    exercised exactly as with Gemini.

        with nlu.use_backend(nlu.FakeNLU({"how can you help?": {
                "acts": ["capability"], "intent": "info", "next_goal": "capability",
                "say": "I can tell you about the clinic and book appointments.",
                "ask": "What would you like to know?"}})):
            ...
    """
    script: object = field(default_factory=dict)
    chunk_size: int = 12
    delay_s: float = 0.0
    calls: list = field(default_factory=list)    # (system, contents) per call, for assertions
    usable: bool = True

    def _next(self, contents: str):
        if isinstance(self.script, list):
            return self.script.pop(0) if self.script else None
        return self.script.get(caller_words(contents).lower())

    async def stream(self, system: str, contents: str, schema: dict, deadline: float):
        self.calls.append((system, contents))
        obj = self._next(contents)
        if obj is None:
            return
        ordered = {k: obj[k] for k in KEY_ORDER if k in obj}
        text = json.dumps(ordered, ensure_ascii=False)
        for i in range(0, len(text), self.chunk_size):
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            yield text[i:i + self.chunk_size]


# Lines dialogue/brief.py always puts in the per-turn contents, in this exact
# form, so test backends can read them without parsing prose (the model reads
# them too): "EXPECT: phone" (an Expect value) and "GOAL: ask_phone" (the
# plan_hint goal value). The caller's words come last, between <<< and >>>.
EXPECT_PREFIX = "EXPECT: "
GOAL_PREFIX = "GOAL: "


def brief_field(contents: str, prefix: str) -> Optional[str]:
    """The value of a "PREFIX value" line in a brief's contents, or None."""
    for line in contents.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip() or None
    return None


def caller_words(contents: str) -> str:
    """The caller's words from a brief's contents (between the last <<< and the next >>>)."""
    if "<<<" not in contents:
        return contents.strip()
    return contents.rsplit("<<<", 1)[-1].split(">>>", 1)[0].strip()


def reading_to_object(reading: dict, goal: Optional[str] = None, doctors: tuple = ()) -> dict:
    """
    Map a harness reading (harness/fake_nlu.Reading.as_dict(), plus "answer")
    onto the model's JSON object, so harness scenarios run through the same
    streaming path as Gemini. The mapping:

        yes_no -> confirmation          phone -> phone_digits (digits only)
        name, service, service_phrase, branch, date_phrase, time_phrase -> same keys
        doctor -> doctor if it is a catalog doctor's spoken name, else doctor_phrase
        patient -> patient_name + for_someone_else=True
        intent: book/cancel/reschedule/check -> intent; question -> acts
            ["question"] + intent "info" (when nothing else is under way);
            human -> acts ["wants_human"]; bot -> ["robot_question"];
            emergency -> emergency "urgent"; end -> ["end"]
        nonanswer -> acts ["non_answer"]   fragment -> acts ["fragment"]
        details present -> acts ["answer"] (or ["info"] if nothing was asked)
        question -> question            answer -> say (validated like any say)
        next_goal = goal (the brief's GOAL line), ask = ""

    `doctors` are the catalog's spoken names (ReaderBackend passes the
    schema's doctor enum); without them every doctor goes to doctor_phrase
    and Python's own catalog match decides. A capability question ("how can
    you help") is acts ["capability"], an emergency also means booking, and
    "no" together with a detail is a correction ("no, 937 not 837").
    "Nothing else under way" means the brief's goal is a conversation goal
    (greet, ask_intent, answer_only, offer_help, capability, anything_else).

    Unknown keys are ignored; the result always has acts, intent, next_goal,
    say and ask. Pure.
    """
    reading = reading if isinstance(reading, dict) else {}

    def get(key):
        value = reading.get(key)
        return value if value not in ("", None) else None

    text = (get("text") or "").lower()
    intent_word = get("intent")
    idle = goal in (None, "", *_IDLE_GOALS)
    acts = []
    if reading.get("fragment"):
        acts.append(Act.FRAGMENT.value)
    if intent_word == "bot":
        acts.append(Act.ROBOT_QUESTION.value)
    elif intent_word == "human":
        acts.append(Act.WANTS_HUMAN.value)
    if intent_word == "end":
        acts.append(Act.END.value)
    if reading.get("nonanswer"):
        acts.append(Act.NON_ANSWER.value)
    question = get("question")
    if (question or intent_word == "question") and intent_word != "bot":
        acts.append(Act.CAPABILITY.value if _CAPABILITY_RE.search(text) else Act.QUESTION.value)

    doctor, doctor_phrase = None, None
    if get("doctor"):
        known = {_canon(d): d for d in doctors or ()}
        doctor = known.get(_canon(get("doctor")))
        doctor_phrase = None if doctor else get("doctor")
    patient = get("patient")
    phone = digits_only(get("phone")) if get("phone") else None
    details = {
        "name": get("caller_name") or (get("name") if not (patient and get("name") == patient) else None),
        "phone_digits": phone,
        "for_someone_else": True if patient or get("relation") else None,
        "relation": get("relation"),
        "age": get("age") if isinstance(get("age"), int) else None,
        "patient_name": patient,
        "service": get("service"),
        "service_phrase": get("service_phrase"),
        "branch": get("branch"),
        "doctor": doctor,
        "doctor_phrase": doctor_phrase,
        "date_phrase": get("date_phrase"),
        "time_phrase": get("time_phrase"),
        "date_iso_hint": get("date") if isinstance(get("date"), str) else None,
        "appt_date_phrase": get("appt_date_phrase"),
    }
    confirmation = get("yes_no") if get("yes_no") in ("yes", "no") else None
    has_details = any(v is not None for v in details.values())
    if has_details or confirmation:
        acts.append(Act.INFO.value if idle and not confirmation else Act.ANSWER.value)
    if not acts:
        acts.append(Act.UNCLEAR.value)

    intent = {"book": "book", "cancel": "cancel", "reschedule": "reschedule", "check": "check",
              "emergency": "book"}.get(intent_word or "", "none")
    if intent_word == "question" and idle:
        intent = "info"
    obj = {
        "acts": acts,
        "intent": intent,
        "emergency": "urgent" if intent_word == "emergency" else "none",
        "confirmation": confirmation,
        "correction": True if confirmation == "no" and has_details else None,
        **details,
        "question": question,
        "next_goal": goal or None,
        "say": get("answer") or "",
        "ask": "",
    }
    return {k: v for k, v in obj.items() if v is not None or k in ("acts", "intent", "next_goal", "say", "ask")}


_IDLE_GOALS = ("greet", "ask_intent", "answer_only", "offer_help", "capability", "anything_else")
_CAPABILITY_RE = re.compile(
    r"\b(how can you help|how (can|could) you help|what (can|do|could) you do|who are you|what are you able to"
    r"|what all can you)\b")


def _serialise(obj: dict) -> str:
    """The object in KEY_ORDER, as the model is told to write it."""
    ordered = {k: obj[k] for k in KEY_ORDER if k in obj}
    return json.dumps(ordered, ensure_ascii=False)


@dataclass
class ReaderBackend:
    """
    A backend driven by a reader function: reader(text, expect) -> reading
    dict (the harness's fake NLU). Streams reading_to_object(...) in
    KEY_ORDER exactly like FakeNLU. A reader returning None simulates the
    model being down for that turn. ai_engine.install_test_nlu(reader) wraps
    this in use_backend(), which is how harness/engine_adapter.py plugs its
    fake NLU into the R2 engine.
    """
    reader: object = None
    chunk_size: int = 12
    calls: list = field(default_factory=list)
    usable: bool = True

    async def stream(self, system: str, contents: str, schema: dict, deadline: float):
        self.calls.append((system, contents))
        if self.reader is None:
            return
        reading = self.reader(caller_words(contents), brief_field(contents, EXPECT_PREFIX))
        if asyncio.iscoroutine(reading):
            reading = await reading
        if reading is None:
            return
        doctors = tuple(_schema_enum(schema, "doctor") or ())
        text = _serialise(reading_to_object(reading, brief_field(contents, GOAL_PREFIX), doctors))
        for i in range(0, len(text), max(1, self.chunk_size)):
            yield text[i:i + self.chunk_size]


_backend: Optional[object] = None


def get_backend():
    """The process-wide backend (Gemini unless a test swapped it)."""
    global _backend
    if _backend is None:
        _backend = GeminiBackend()
    return _backend


def set_backend(backend) -> None:
    global _backend
    _backend = backend


@contextmanager
def use_backend(backend):
    """Swap the backend for a block (tests, harness): `with nlu.use_backend(FakeNLU(...)):`."""
    global _backend
    previous = _backend
    _backend = backend
    try:
        yield backend
    finally:
        _backend = previous
