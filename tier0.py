"""
Tier-0: resolve a caller turn without an LLM round trip.

Most turns in a booking call are short answers to the question Emma just asked:
"yes", a phone number, "root canal", "next Monday", "5 pm". Those are resolved
here in well under a millisecond instead of ~1 s of Gemini latency.

The rules are deliberately conservative. Tier-0 only ever fills the slot the
current step is asking for (plus the yes/no), and returns None — "send this turn
to the LLM" — for anything that looks like a question, a long answer, or a mixed
answer such as "yes, but change the date". Returning None is always safe; a
wrong non-None answer is not, so when in doubt the rule says None.

The returned dict has exactly the shape of ai_engine._basic_entity_fallback, so
the state machine cannot tell which tier produced it.
"""

import re

import ai_engine
import backend_actions

MAX_WORDS = 8
MAX_CONFIRM_WORDS = 5

_QUESTION_RE = re.compile(
    r"\b(what|where|which|who|why|how|when|do you|does|can you|could you|is there|"
    r"are you|price|prices|cost|costs|charge|fee|fees|insurance|address|located|"
    r"location|parking|open|hours)\b"
)
_TIME_WORD_RE = re.compile(
    r"\b(\d{1,2}(:\d{2})?\s*(am|pm|a\.m\.|p\.m\.)|\d{1,2}\s*o'?clock|morning|afternoon|"
    r"evening|noon|night|am|pm)\b"
)
_DATE_WORD_RE = re.compile(
    r"\b(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"week|january|february|march|april|may|june|july|august|september|october|"
    r"november|december|\d{1,2}(st|nd|rd|th))\b"
)
_BOOKING_PHRASE_RE = re.compile(
    r"^(yes,? )?(hi,? |hello,? )?(i('d| would) like to |i want to |i wanna |i need to |"
    r"can i |could i |i'm calling to |i am calling to )?(book|schedule|make|fix|get)"
    r"( an| a| my)? (appointment|booking|visit)( please)?$"
)
_CLOSING_RE = re.compile(r"\b(nothing|that's all|thats all|that is all|no thanks|bye|goodbye)\b")
_NAME_INTRO_RE = re.compile(
    r"^(?:my name is|my name's|i am|i'm|this is|it's|it is|call me)\s+([a-z][a-z .'-]*)$"
)
_FILLER_RE = re.compile(r"\b(uh+|um+|hmm+|er|please|maybe|on|at|around|about|for|the|let's say|lets say)\b")

# Spoken aliases -> canonical service. A single unambiguous hit is required.
SERVICE_ALIASES = {
    "root canal": "Root Canal Treatment",
    "cleaning": "Teeth Cleaning",
    "scaling": "Teeth Cleaning",
    "check up": "General Check-up",
    "checkup": "General Check-up",
    "check-up": "General Check-up",
    "consultation": "Consultation",
    "consult": "Consultation",
    "filling": "Tooth Filling",
    "cavity": "Tooth Filling",
    "extraction": "Tooth Extraction",
    "pull out": "Tooth Extraction",
    "pulled out": "Tooth Extraction",
    "braces": "Braces",
    "invisalign": "Invisalign",
    "pediatric": "Pediatric Dentistry",
    "paediatric": "Pediatric Dentistry",
}

_DIGIT_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3",
    "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
_MULTIPLIERS = {"double": 2, "triple": 3}


def normalize_spoken_digits(text: str) -> str:
    """
    Turn a spoken number into its digits: "nine eight double seven 6 five" ->
    "987765". "double"/"triple" repeat the next digit, as Indian callers say it.
    Non-number words are dropped; an empty string means no digits were spoken.
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


def match_service_alias(text: str):
    """Canonical service if exactly one service is named, else None."""
    lower = (text or "").lower()
    hits = {canon for alias, canon in SERVICE_ALIASES.items()
            if re.search(rf"\b{re.escape(alias)}\b", lower)}
    if len(hits) == 1:
        return hits.pop()
    return ai_engine._match_service(text) if not hits else None


def looks_like_question(user_text: str) -> bool:
    """Cheap question detector, used when the LLM is unavailable."""
    lower = (user_text or "").lower().replace("’", "'")
    return "?" in lower or bool(_QUESTION_RE.search(lower))


def _empty() -> dict:
    return {
        "patient_name": None, "phone_number": None, "dental_service": None,
        "appointment_date": None, "appointment_time": None, "confirmation": None,
        "user_query": None,
    }


def _strip_fillers(text: str) -> str:
    return re.sub(r"\s+", " ", _FILLER_RE.sub(" ", text)).strip(" ,.")


def _awaiting_yes_no(s) -> bool:
    """True when Emma's last question was a yes/no confirmation."""
    return (
        (s.step == 2 and bool(s.temp_name))
        or (s.step == 4 and bool(s.temp_phone))
        or (s.step == 5 and bool(s.temp_service))
        or (s.step == 7 and bool(s.temp_date))
        or (s.step == 8 and bool(s.temp_time))
        or s.step in (6, 9, 11)
    )


def fast_entities(user_text: str, s):
    """Entities for this turn, or None when the turn needs the LLM."""
    text = (user_text or "").strip()
    if not text:
        return None
    lower = text.lower().replace("’", "'")
    if "?" in lower or _QUESTION_RE.search(lower):
        return None
    words = re.findall(r"[a-z0-9']+", lower)
    if not words:
        return None

    # The phone step comes before the length cap: a number spoken as words
    # ("nine eight double seven ...") easily runs past MAX_WORDS.
    if s.step == 4 and not s.temp_phone:
        ok, cleaned = backend_actions.validate_phone(normalize_spoken_digits(lower))
        if ok:
            ent = _empty()
            ent["phone_number"] = cleaned
            return ent
        return None

    if len(words) > MAX_WORDS:
        return None

    conf = ai_engine._parse_confirmation(text)
    # "oh" and "one" are everyday words, so spoken digits only count in a run.
    has_digits = any(ch.isdigit() for ch in lower) or len(normalize_spoken_digits(lower)) >= 3
    has_slot_content = (
        has_digits or bool(_TIME_WORD_RE.search(lower)) or bool(_DATE_WORD_RE.search(lower))
        or match_service_alias(lower) is not None
    )
    ent = _empty()

    # Step 11 closing: "nothing, thanks" / "that's all" / "bye".
    if s.step == 11 and _CLOSING_RE.search(lower) and not has_slot_content:
        ent["confirmation"] = "no"
        return ent

    # Step 10 alternatives: the state machine parses the pick from the raw text;
    # Tier-0 only needs to vouch that the turn is a simple pick or yes/no.
    if s.step == 10 and s.offering_alternatives:
        if re.search(r"\b(first|second|earlier|later|former|latter)\b", lower) or (
            conf and len(words) <= MAX_CONFIRM_WORDS
        ) or _TIME_WORD_RE.search(lower):
            ent["confirmation"] = conf
            return ent
        return None

    if _awaiting_yes_no(s):
        # A bare yes/no only. "No, it's Priya" or "yes but Tuesday" carry a
        # correction the LLM must extract.
        if conf and len(words) <= MAX_CONFIRM_WORDS and not has_slot_content:
            if conf == "no" and s.step == 2 and len(words) > 1 and not re.fullmatch(
                r"(no|nope|nah)(,)?( that's| that is| it's| it is)?( not)?( right| correct| wrong)?", lower
            ):
                return None  # probably "no, it's <name>"
            ent["confirmation"] = conf
            return ent
        return None

    if s.step in (1, 3):
        if _BOOKING_PHRASE_RE.match(lower.strip(" .!")):
            return ent  # the state machine reads the booking keyword itself
        if s.step == 1 and conf == "yes" and len(words) <= 4 and not has_slot_content:
            ent["confirmation"] = "yes"
            return ent
        return None

    if s.step == 2:  # no candidate name yet
        m = _NAME_INTRO_RE.match(lower.strip(" .!"))
        if m:
            name = m.group(1).strip(" .")
            if 1 <= len(name.split()) <= 4 and ai_engine._looks_like_name(name, strict=True):
                ent["patient_name"] = name.title()
                return ent
        return None

    if s.step == 5:  # no candidate service yet
        service = match_service_alias(lower)
        if service and not _DATE_WORD_RE.search(lower) and not _TIME_WORD_RE.search(lower):
            ent["dental_service"] = service
            return ent
        return None

    if s.step == 7:  # no candidate date yet
        if _TIME_WORD_RE.search(lower) or match_service_alias(lower):
            return None  # mixed answer: let the LLM split date from time/service
        phrase = _strip_fillers(lower)
        if phrase and len(phrase.split()) <= 4:
            _d, resolved, _err = backend_actions.resolve_date(phrase)
            if resolved:
                ent["appointment_date"] = phrase
                return ent
        return None

    if s.step == 8:  # no candidate time yet
        if _DATE_WORD_RE.search(lower):
            return None
        phrase = _strip_fillers(lower)
        if phrase and len(phrase.split()) <= 3:
            _t, resolved, _err = backend_actions.resolve_time(phrase)
            if resolved:
                ent["appointment_time"] = phrase
                return ent
        return None

    return None
