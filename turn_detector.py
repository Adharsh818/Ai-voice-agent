"""
When has the caller finished their turn?

Deepgram's endpointer only hears silence. At 200-400 ms it ends a turn on any
natural pause, which is how "What's the best", "Don't", "But", "Tell me what can
you", "Cancel the com" and "Tell me how good you" reached Emma as whole turns in
the 30 Sep - 1 Oct test calls, and why callers said she cut them off. A
receptionist listens to *what* was said as well as to the pause:

- "yes", a full 10-digit number, "next Monday", "5 pm", "my name is Priya"
  answer the question she just asked, so she replies at once;
- "and...", "my number is...", "98450..." (half a number), "Don't", "But" are
  clearly unfinished, so she waits, up to about 2 s while the caller carries on;
- anything else gets a short, natural pause.

classify(text, hint) -> Verdict
    how much silence after the caller's last word makes `text` a complete turn
hint_from_state(s) -> {"expect": ..., "digits_so_far": ...}
    what Emma is waiting for, derived from today's step engine; the redesigned
    engine provides ai_engine.listening_hint(s) instead
TurnDetector
    holds an utterance until that silence has passed, merges more speech into
    it, and hands the finished turn to the call session

Silence is measured from the moment the caller's last word ended (Deepgram word
timestamps mapped to wall time), not from when the STT event arrived, so the
same targets work with DEEPGRAM_ENDPOINTING_MS=200 or 400: a later event simply
leaves less to wait.
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# Silence after the caller's last word before the turn is committed, by verdict.
# The base (likely / default) is 600-800 ms: long enough for the breath a
# caller takes mid-request, short enough that a finished request feels answered.
SILENCE_MS = {
    "complete": 0,       # a complete answer to what Emma asked: reply now
    "likely": 600,       # a finished-sounding sentence
    "default": 800,      # no signal either way
    "unsure": 1200,      # probably more coming: a cut-off word, "what's the best"
    "unfinished": 1600,  # trailing "and", "the", "my number is", "Don't", "But"
    "digits": 2000,      # half a phone number; Indian callers pause between groups
}
# While the caller keeps talking (new interim words), a held turn waits at most
# this long after its utterance arrived; the next utterance merges into it.
HOLD_MAX_MS = 2000
# How recent the last interim words must be to count as "still talking".
ACTIVITY_GRACE_MS = 600

EXPECTS = ("phone", "name", "yes_no", "date", "time", "choice", "open", "spelling")


@dataclass
class Verdict:
    kind: str
    silence_ms: int
    reason: str

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.reason}"


def _verdict(kind: str, reason: str) -> Verdict:
    return Verdict(kind, SILENCE_MS[kind], reason)


# ------------------------------------------------------------------ vocabulary
_CONJUNCTIONS = {
    "and", "or", "but", "so", "because", "cause", "cuz", "coz", "if", "then", "than",
    "which", "whose", "while", "although", "though", "unless", "until", "since", "plus", "also",
    "like", "whether", "nor",
}
_DETERMINERS = {
    "the", "a", "an", "my", "your", "our", "his", "her", "their", "its", "this", "these",
    "those", "some", "any", "another", "every", "each", "which", "what",
}
_PREPOSITIONS = {
    "to", "for", "of", "with", "at", "in", "on", "from", "by", "about", "around", "after",
    "before", "between", "into", "near", "till", "via", "per", "as", "over", "under", "towards",
}
_AUXILIARIES = {
    "is", "are", "was", "were", "am", "be", "been", "being", "do", "does", "did", "can", "could",
    "will", "would", "should", "shall", "may", "might", "must", "have", "has", "had",
    "don't", "doesn't", "didn't", "can't", "cannot", "couldn't", "won't", "wouldn't",
    "shouldn't", "haven't", "hasn't", "hadn't", "isn't", "aren't", "wasn't", "weren't",
}
_SUBJECTS = {
    "i", "we", "they", "he", "she", "i'm", "i'd", "i'll", "i've", "we're", "we'd", "we'll",
    "they're", "it's", "that's", "there's", "what's", "whats", "where's", "who's", "how's",
    "here's", "let's",
}
_WH = {"what", "which", "how", "why", "where", "when", "who", "whom"}
_FILLERS = {"um", "umm", "ummm", "uh", "uhh", "uhm", "er", "erm", "ah", "eh", "hmm", "mm",
            "actually", "basically", "well", "like"}
# The last word leaves the sentence hanging: always wait.
_TRAILING = (_CONJUNCTIONS | _DETERMINERS | _PREPOSITIONS | _AUXILIARIES | _SUBJECTS | _WH
             | _FILLERS | {"next", "coming", "very", "really", "just"})

# Verbs that usually take an object: "I want to book", "can I get", "I need".
_OBJECT_VERBS = {
    "want", "wanted", "need", "needed", "get", "book", "make", "change", "tell", "give", "know",
    "ask", "check", "say", "bring", "move", "cancel", "reschedule", "prepone", "postpone",
    "confirm", "show", "find", "see", "keep", "take",
}
# A question ending on one of these is still missing its noun: "what's the best".
_NEEDS_NOUN = {
    "best", "cheapest", "earliest", "nearest", "latest", "fastest", "soonest", "first", "other",
    "same", "most", "least", "better", "cheaper", "earlier", "later", "closest", "good", "nice",
    "right", "usual", "last",
}

_YES = {"yes", "yeah", "yep", "yup", "ya", "yah", "yea", "sure", "okay", "ok", "correct", "right",
        "absolutely", "definitely", "perfect", "fine", "alright", "exactly", "please"}
_NO = {"no", "nope", "nah"}
# Words that may follow yes/no in a short, complete answer: "yes please",
# "no that's fine", "yeah go ahead", "okay sure".
_ANSWER_TAIL = _YES | _NO | {
    "thanks", "thank", "you", "that's", "thats", "it", "is", "it's", "go", "ahead", "great",
    "sounds", "good", "not", "really", "problem", "works", "do", "that", "all", "right", "so",
    "of", "course", "yes", "indeed", "totally", "sir", "ma'am", "madam",
}
_ANSWER_PHRASES = {
    "that's right", "thats right", "that's correct", "thats correct", "go ahead", "sounds good",
    "of course", "that works", "that's fine", "that's perfect", "not really", "not now",
    "please do", "do it", "book it", "that's it", "it is", "it's correct", "correct yes",
}

_HOLD_ON_RE = re.compile(
    r"\b(hold on|hang on|one (sec|second|minute|moment)|just a (sec|second|minute|moment)|"
    r"give me a (sec|second|minute|moment)|wait a (sec|second|minute|moment)|a moment please)\b"
)
_CLOSING_RE = re.compile(
    r"^(ok(ay)? |no |alright )?(thanks?( you)?( so much)?,? )?(bye( bye)?|goodbye|that's all|"
    r"that's it|thats all|nothing else|no thanks|no thank you|thank you|thanks|see you)"
    r"( bye)?$"
)
_REPEAT_RE = re.compile(
    r"^(come again|say (that|it) again( please)?|can you repeat( that)?|could you repeat( that)?|"
    r"repeat( that)?( please)?|sorry what|what did you say|sorry come again)$"
)
# A lone "sorry" / "what" is a repeat request only when asked as a question
# ("Sorry?"); otherwise it usually starts a correction ("Sorry, I meant Tuesday").
_LONE_REPEAT = {"sorry", "pardon", "what", "huh", "sorry what"}
# Words that settle a yes/no question even in an open context.
_CLEAR_YES_NO = {"yes", "yeah", "yep", "yup", "no", "nope", "nah"}
_GREETING_RE = re.compile(r"^((hello|hi|hey|hai|hallo)\s*)+(emma|there)?$")

_WEEKDAYS = "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
_MONTHS = ("january|february|march|april|may|june|july|august|september|october|november|"
           "december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec")
_ORDINAL_WORDS = ("first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|"
                  "twelfth|thirteenth|fourteenth|fifteenth|sixteenth|seventeenth|eighteenth|"
                  "nineteenth|twentieth|thirtieth")
_DATE_RE = re.compile(
    rf"\b({_WEEKDAYS}|today|tomorrow|tonight|weekend|next week|this week|"
    rf"\d{{1,2}}(st|nd|rd|th)|({_MONTHS}) \d{{1,2}}|\d{{1,2}} ({_MONTHS})|"
    rf"\d{{1,2}}/\d{{1,2}}|the ({_ORDINAL_WORDS}))\b"
)
_NUMBER_WORDS = ("one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve")
_TIME_RE = re.compile(
    rf"(\b\d{{1,2}}(:\d{{2}})?\s*(am|pm|a\.m\.?|p\.m\.?)|\b\d{{1,2}}:\d{{2}}\b|o'?clock|\bnoon\b|"
    rf"\bmidday\b|\bmorning\b|\bafternoon\b|\bevening\b|\bhalf past\b|\bquarter (past|to)\b|"
    rf"^(at |around |about )?({_NUMBER_WORDS}|\d{{1,2}})( ({_NUMBER_WORDS}|thirty|fifteen|forty five))?"
    rf"( (am|pm))?$)"
)
_CHOICE_RE = re.compile(
    r"\b(first|second|third|1st|2nd|3rd|last|former|latter|earlier one|later one|that one|"
    r"this one|either|any( one)?|whichever|the other one|both)\b"
)
_NAME_INTRO_RE = re.compile(
    r"^(?:(?:yes|yeah|ok|okay|hi|hello|so),? )?(?:my name is|my name's|name is|name's|this is|"
    r"i am|i'm|it's|it is|call me)\s+([a-z][a-z'.-]*(?:\s+[a-z][a-z'.-]*){0,3})$"
)
_PHONE_CUE_RE = re.compile(r"\b(number|mobile|phone|contact|whatsapp)\b")

# Common words of four letters or fewer. A short last word NOT in this list
# (and not a number) is probably a word cut off mid-way: "Cancel the com".
_COMMON_SHORT = set("""
a am an and any are as ask at away be been best big bit book both but by bye call came can
care cash come cost day days did do does done down each else even ever eye eyes face far
fee fees few fine five fix for four free from full gave get give go goes good got gum gums
had half has have he hear help her here hers him his hold home hour how i if in into is
it its just keep kid kids kind knew know last late left less let like line long look lot
lots love made make many may me mean meet mind mine miss more most much must my name near
need new next nice nine no none noon nope not now of off oh ok okay old on once one only
open or our out over paid pain pay plan pm am post pull put rao rate read real rest right
root said same saw say see seen she shot sick side sign six so some son soon sore stay step
still stop such sure take talk tell ten than that the them then they this time to told too
took tooth top try two up upi us use very visit wait walk want was way we week well went
were what when who whom why will wish with work yeah year yes yet you your yup yep ya hi
hey sir mam its it's i'm i'd i'll don't can't won't isn't that's what's let's we're you're
cold hot ache gap wire cap fill jaw lip lips tongue kit cost date days wed thu fri sat sun
mon tue tues thur thurs june july may aug sept oct nov dec jan feb mar apr ms mr mrs dr st
rd th nd x ray xray pls plz thx ah uh um hmm mm er erm eh ha huh wow
""".split())

_DIGIT_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
_MULTIPLIERS = {"double": 2, "triple": 3}


def _norm(text: str) -> str:
    text = (text or "").lower().replace("’", "'").replace("‘", "'")
    text = re.sub(r"[^a-z0-9':/ .-]+", " ", text)
    text = re.sub(r"(?<![a-z0-9])[.-]+|[.-]+(?![a-z0-9])", " ", text)  # stray dots/dashes
    return re.sub(r"\s+", " ", text).strip()


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:['.:/-][a-z0-9]+)*", _norm(text))


def spoken_digits(text: str) -> str:
    """
    Digits the caller said, in order: "nine eight double seven 6 five" -> "987765".
    Deepgram's smart_format writes numbers as "(789) 937-7462"; every digit counts.
    Kept here (not imported from tier0) so turn detection survives the engine redesign.
    """
    tokens = re.findall(r"[a-z]+|\d", (text or "").lower().replace("-", " "))
    digits, repeat = [], 1
    for tok in tokens:
        if tok in _MULTIPLIERS:
            repeat = _MULTIPLIERS[tok]
            continue
        digit = tok if tok.isdigit() else _DIGIT_WORDS.get(tok)
        if digit is None:
            repeat = 1
            continue
        digits.append(digit * repeat)
        repeat = 1
    return "".join(digits)


def _phone_digits_needed(digits: str, so_far: int) -> int:
    """A mobile number is 10 digits; a leading 0 or +91 adds one or two."""
    if so_far:
        return 10
    if digits.startswith("0"):
        return 11
    if digits.startswith("91") and len(digits) > 10:
        return 12
    return 10


# ------------------------------------------------------------------ rules
def is_hold_on(text: str) -> bool:
    """The caller asked Emma to wait ("hold on", "one second")."""
    return bool(_HOLD_ON_RE.search(_norm(text)))


def is_yes_no(text: str) -> bool:
    """A short, complete yes or no: "yes", "yeah sure", "no thanks", "that's right"."""
    words = _words(text)
    if not words or len(words) > 5:
        return False
    phrase = " ".join(words)
    if phrase in _ANSWER_PHRASES:
        return True
    return (words[0] in _YES or words[0] in _NO) and all(w in _ANSWER_TAIL for w in words[1:])


def _strong_unfinished(words: list[str]) -> Optional[str]:
    last = words[-1]
    tail2 = " ".join(words[-2:])
    tail3 = " ".join(words[-3:])
    if tail2 == "tell me" or tail3 in ("let me know", "can you tell"):
        return "tell_me"
    if last == "you" and len(words) >= 2:
        prev = words[-2]
        if prev in ("are", "were") and len(words) >= 3 and words[-3] in ("how", "who", "where"):
            return None                # "How are you", "Who are you": a whole question
        if prev in _AUXILIARIES or prev in _CONJUNCTIONS or prev in ("what", "if", "when", "where"):
            return "subject_you"
        if len(words) >= 3 and words[-3] == "how" and prev not in ("are", "about", "do", "did", "were"):
            return "how_adj_you"       # "Tell me how good you"
    if last in _TRAILING:
        # "right", "please" etc. are not in _TRAILING; "that's" / "it's" alone are.
        return f"trailing_{last}"
    return None


def _weak_unfinished(words: list[str], expect: str) -> Optional[str]:
    last = words[-1]
    if last in _OBJECT_VERBS:
        return "verb_needs_object"
    if last in _NEEDS_NOUN and (words[0] in _WH or words[0] in _SUBJECTS or len(words) <= 3):
        return "question_needs_noun"      # "What's the best"
    after_title = len(words) >= 2 and words[-2] in ("dr", "doctor", "mr", "mrs", "ms", "miss")
    if (expect not in ("name", "spelling") and not after_title and last.isalpha() and 2 <= len(last) <= 4
            and last not in _COMMON_SHORT and last not in _KNOWN_WORDS and not _DATE_RE.search(last)):
        return "cut_off_word"             # "Cancel the com"
    return None


# Clinic words added at call start (doctor, branch and service names), so a
# short surname such as "Iyer" is never mistaken for a cut-off word.
_KNOWN_WORDS: set = set()


def add_vocabulary(terms) -> None:
    for term in terms or ():
        _KNOWN_WORDS.update(_words(term))


def _complete_for(expect: str, text: str, words: list[str], so_far: int) -> Optional[str]:
    norm = " ".join(words)
    if expect == "phone":
        digits = spoken_digits(text)
        if digits and so_far + len(digits) >= _phone_digits_needed(digits, so_far):
            return "full_number"
        return None
    if expect == "date":
        return "date" if _DATE_RE.search(norm) else None
    if expect == "time":
        return "time" if _TIME_RE.search(norm) else None
    if expect == "choice":
        if _CHOICE_RE.search(norm) or _TIME_RE.search(norm) or _DATE_RE.search(norm):
            return "choice"
        return None
    if expect == "name":
        match = _NAME_INTRO_RE.match(norm)
        if match and not any(w in _TRAILING for w in match.group(1).split()):
            return "name_intro"
        if 2 <= len(words) <= 4 and all(w.isalpha() and w not in _TRAILING and w not in _YES
                                        for w in words):
            return "full_name"
        return None
    if expect == "spelling":
        return "spelling_done" if re.search(r"\b(that's it|that's all|thats it|done)$", norm) else None
    return None


def classify(text: str, hint: Optional[dict] = None) -> Verdict:
    """How long to wait after the caller's last word before `text` is their whole turn."""
    hint = hint or {}
    raw = hint.get("expect")
    expect = str(getattr(raw, "value", raw) or "open")    # the engine may pass its Expect enum
    expect = expect if expect in EXPECTS else "open"
    so_far = int(hint.get("digits_so_far") or 0)
    words = _words(text)
    if not words:
        return _verdict("complete", "empty")
    norm = " ".join(words)

    # Things Emma should answer at once, whatever she asked.
    if is_hold_on(norm):
        return _verdict("complete", "hold_on")
    if _CLOSING_RE.match(norm):
        return _verdict("complete", "closing")
    if _REPEAT_RE.match(norm) or (norm in _LONE_REPEAT and "?" in (text or "")):
        return _verdict("complete", "repeat_request")

    # Half a phone number, when she asked for one (or the caller announced one).
    digits = spoken_digits(text)
    if digits and (expect == "phone" or _PHONE_CUE_RE.search(norm)):
        if so_far + len(digits) < _phone_digits_needed(digits, so_far):
            return _verdict("digits", "partial_number")

    reason = _strong_unfinished(words)
    if reason:
        return _verdict("unfinished", reason)
    if is_yes_no(norm):
        # After an open question ("How can I help?") a bare "okay" or "sure"
        # usually leads into the request, so only a clear yes/no is final there.
        if expect != "open" or words[0] in _CLEAR_YES_NO:
            return _verdict("complete" if expect != "open" else "likely", "yes_no")
        return _verdict("default", "acknowledgement")
    reason = _complete_for(expect, text, words, so_far)
    if reason:
        return _verdict("complete", reason)
    if expect == "spelling":
        return _verdict("unfinished", "spelling")
    reason = _weak_unfinished(words, expect)
    if reason:
        return _verdict("unsure", reason)
    if _GREETING_RE.match(norm):
        return _verdict("default", "greeting")     # "Hello," is usually followed by the request
    if len(words) >= 3 or (expect == "name" and len(words) == 1 and words[0].isalpha()):
        return _verdict("likely", "sentence")
    return _verdict("default", "short")


def hint_from_state(s) -> dict:
    """
    What Emma is listening for, from today's 12-step engine (ai_engine.SessionState).
    The redesigned engine exposes ai_engine.listening_hint(s); this is its fallback.
    """
    step = getattr(s, "step", None)
    if step is None:
        return {"expect": "open", "digits_so_far": 0}

    def pending(attr):
        return not getattr(s, attr, "")

    expect = "open"
    if step == 2:
        expect = "name" if pending("temp_name") else "yes_no"
    elif step == 4:
        expect = "phone" if pending("temp_phone") else "yes_no"
    elif step == 5:
        expect = "choice" if pending("temp_service") else "yes_no"
    elif step in (6, 9, 11):
        expect = "yes_no"          # branch check, recap, "anything else?"
    elif step == 7:
        expect = "date" if pending("temp_date") else "yes_no"
    elif step == 8:
        expect = "time" if pending("temp_time") else "yes_no"
    elif step == 10:
        expect = "choice" if getattr(s, "alternative_slots", None) else "time"
    return {"expect": expect, "digits_so_far": 0}


# ------------------------------------------------------------------ the detector
@dataclass
class _Held:
    text: str
    end_wall: float
    source: str
    received: float
    verdict: Verdict


class TurnDetector:
    """
    Holds each finished utterance until the caller has been silent long enough
    for what they said, then calls on_turn(text, end_wall, source, received, verdict).

    utterance()   an STT end-of-utterance event (or typed text with hold=False)
    activity()    the caller is audibly talking again (new interim words)
    cancel()      drop anything held (the call ended)
    """

    def __init__(self, on_turn: Callable[..., Awaitable], hint: Callable[[], dict] = dict,
                 spawn: Callable = asyncio.create_task, now: Callable[[], float] = time.perf_counter):
        self.on_turn = on_turn
        self.hint = hint
        self.spawn = spawn
        self.now = now
        self._held: Optional[_Held] = None
        self._task: Optional[asyncio.Task] = None
        self._last_activity: Optional[float] = None

    @property
    def holding(self) -> bool:
        return self._held is not None

    @property
    def held_text(self) -> str:
        return self._held.text if self._held else ""

    def activity(self):
        self._last_activity = self.now()

    def cancel(self):
        if self._task is not None:
            self._task.cancel()
        self._task = None
        self._held = None

    def _safe_hint(self) -> dict:
        try:
            return self.hint() or {}
        except Exception as exc:          # a broken hint must never stop the call
            logger.debug("listening hint failed: %s", exc)
            return {}

    async def utterance(self, text: str, end_wall: Optional[float] = None, source: str = "text",
                        received: Optional[float] = None, hold: bool = True):
        received = received if received is not None else self.now()
        end_wall = end_wall if end_wall is not None else received
        if self._held is not None:
            # More speech arrived while waiting: one turn with both parts.
            if self._task is not None:
                self._task.cancel()
                self._task = None
            text = f"{self._held.text} {text}".strip()
            self._held = None
        verdict = classify(text, self._safe_hint()) if hold else _verdict("complete", "typed")
        elapsed_ms = (self.now() - end_wall) * 1000
        if verdict.silence_ms - elapsed_ms <= 0:
            await self.on_turn(text, end_wall, source, received, verdict)
            return
        self._held = _Held(text, end_wall, source, received, verdict)
        self._task = self.spawn(self._wait(self._held))

    def _deadline(self, held: _Held) -> float:
        deadline = held.end_wall + held.verdict.silence_ms / 1000
        if self._last_activity is not None and self._last_activity > held.received:
            cap = max(deadline, held.received + HOLD_MAX_MS / 1000)
            deadline = max(deadline, min(self._last_activity + ACTIVITY_GRACE_MS / 1000, cap))
        return deadline

    async def _wait(self, held: _Held):
        try:
            while True:
                remaining = self._deadline(held) - self.now()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(0.05, remaining))
        except asyncio.CancelledError:
            return
        if self._held is not held:
            return
        # Detach before handing over, so an utterance arriving while the turn
        # starts does not cancel the task that is starting it.
        self._held, self._task = None, None
        await self.on_turn(held.text, held.end_wall, held.source, held.received, held.verdict)
