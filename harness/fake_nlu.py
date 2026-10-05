"""
A deterministic, rule-based stand-in for the Gemini NLU.

Offline runs must be reproducible and free, so every caller turn is read here
instead of by the model: the same words always give the same reading, and no
network call is made. The reading is generic (a name, phone digits, a service,
a branch, a doctor, a date and time, yes/no, a question, an intent), not the
shape any particular engine wants; harness/engine_adapter.py maps it onto
whatever the engine under test expects.

It is meant to be a competent listener, roughly as good as the real model on
the phrasings the simulated caller uses, so that offline failures point at the
dialogue rather than at the fake. Where the real model's behaviour is part of a
known bug (the prompt tells it to answer only from clinic facts, so a meta
question like "how can you help?" gets the escalation line), `answer()` mirrors
that behaviour on purpose and says so.

The metrics use the same reader as an observer of the caller's side, in live
runs too: "did the caller already give a valid phone number?" has one answer
whatever NLU the engine used.
"""

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, time
from functools import lru_cache
from typing import Optional

import clock
import config
import dateparse

from harness import lines

# ---------------------------------------------------------------- the reading


@dataclass
class Reading:
    text: str
    expect: Optional[str] = None
    name: Optional[str] = None
    phone: Optional[str] = None          # the digits heard, possibly incomplete
    phone_valid: bool = False            # a full Indian mobile (10 digits, 6-9 first)
    service: Optional[str] = None        # canonical service
    service_phrase: Optional[str] = None # what they said, e.g. "teeth whitening" (unknown services too)
    branch: Optional[str] = None
    doctor: Optional[str] = None         # "Dr Sharma"
    date_phrase: Optional[str] = None    # a phrase date parsers accept ("05 October 2026", "next week")
    time_phrase: Optional[str] = None    # "05:00 PM", "evening"
    date: Optional[date] = None          # resolved exact date, when there is one
    time: Optional[time] = None          # resolved exact time, when there is one
    date_issue: Optional[str] = None     # SUNDAY, PAST, ... from dateparse
    yes_no: Optional[str] = None
    question: Optional[str] = None       # a real question (not booking information)
    intent: Optional[str] = None         # book cancel reschedule check question human bot emergency end
    nonanswer: bool = False              # "not sure", "I've been busy"
    fragment: bool = False               # looks cut off mid-sentence
    patient: Optional[str] = None        # booking for someone else: that person's name
    appt_date_phrase: Optional[str] = None  # the EXISTING appointment's date in "it's X, move it to Y"
    caller_name: Optional[str] = None    # the caller's own name when they also name the patient
    relation: Optional[str] = None       # "son", "mother": booking for family
    age: Optional[int] = None            # the patient's age ("he's 8", "8 years old")

    def as_dict(self) -> dict:
        out = asdict(self)
        out["date"] = self.date.isoformat() if self.date else None
        out["time"] = self.time.strftime("%H:%M") if self.time else None
        return out

    @property
    def slots(self) -> dict:
        """The booking details this turn validly supplied, keyed like the metrics' slots."""
        out = {}
        if self.name:
            out["name"] = self.name
        if self.phone_valid:
            out["phone"] = self.phone
        if self.service and self.intent != "question":
            out["service"] = self.service
        if self.branch and self.intent != "question":
            out["branch"] = self.branch
        if self.date and self.intent != "question":
            out["date"] = self.date.isoformat()
        if self.time and self.intent != "question":
            out["time"] = self.time.strftime("%H:%M")
        return out


# ---------------------------------------------------------------- phone digits

_DIGIT_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "nought": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
_MULTIPLIERS = {"double": 2, "triple": 3}
_TOKEN = re.compile(r"\d+|[a-z]+|[()+\-.,]")


def digit_runs(text: str) -> list[tuple[str, int, int]]:
    """
    Runs of spoken or written digits with their character span:
    "nine eight double four five, 0 1 2 3 4" -> ("9844501234", start, end).
    "double"/"triple" repeat the next digit, as Indian callers say numbers;
    brackets, dashes and commas inside a run are ignored, so Deepgram's
    US-style "(789) 937-7462" is one run.
    """
    low = (text or "").lower()
    runs, cur, start, end, repeat = [], [], None, None, 1
    for m in _TOKEN.finditer(low):
        tok = m.group(0)
        if tok.isdigit():
            digits = tok[0] * repeat + tok[1:]
        elif tok in _DIGIT_WORDS and not (tok in ("o", "oh") and not cur and not _next_is_digit(low, m.end())):
            digits = _DIGIT_WORDS[tok] * repeat
        elif tok in _MULTIPLIERS:
            repeat = _MULTIPLIERS[tok]
            if start is None:
                start = m.start()
            continue
        elif tok in "()+-.,":
            continue
        else:
            if cur:
                runs.append(("".join(cur), start, end))
            cur, start, end, repeat = [], None, None, 1
            continue
        if start is None:
            start = m.start()
        cur.append(digits)
        end = m.end()
        repeat = 1
    if cur:
        runs.append(("".join(cur), start, end))
    return runs


def _next_is_digit(low: str, pos: int) -> bool:
    m = _TOKEN.search(low, pos)
    return bool(m) and (m.group(0).isdigit() or m.group(0) in _DIGIT_WORDS or m.group(0) in _MULTIPLIERS)


def phone_from(text: str) -> tuple[Optional[str], bool, Optional[tuple[int, int]]]:
    """(digits, valid, span) for the longest run of 7+ digits, or (None, False, None)."""
    runs = [r for r in digit_runs(text) if len(r[0]) >= 7]
    if not runs:
        return None, False, None
    digits, start, end = max(runs, key=lambda r: len(r[0]))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    valid = len(digits) == 10 and digits[0] in "6789"
    return digits, valid, (start, end)


# ---------------------------------------------------------------- names

_NON_NAME = set("""
a an the and or but so to of for from with at on in by my me i i'm im it it's its is was be am are this that these those
here there hi hello hey yes yeah yep no nope ok okay sure fine good great well just actually really please thanks thank
sorry um uh hmm er like want wanted need needed would could can will shall should calling call speaking looking trying
going book booking booked appointment appointments schedule visit come coming see get got make fix cancel reschedule
move change prepone postpone check checkup check-up up cleaning clean filling root canal extraction braces invisalign
consultation pediatric teeth tooth dentist dental doctor dr clinic pain ache toothache emergency today tomorrow tonight
morning afternoon evening night noon week weekend next last monday tuesday wednesday thursday friday saturday sunday
january february march april may june july august september october november december am pm o'clock oclock
number phone mobile name full real person human bot robot someone somebody busy driving not sure also again back new
interested free available slot time date day earliest asap soon possible about around after before only one two
whatever anything nothing all any some what when where which who why how do does did have has had your you
son daughter kid child mother father mom dad wife husband brother sister mine his her he she he's she's they them him
we us
nagarbhavi indiranagar jayanagar whitefield branch location
""".split())
_INTRO = re.compile(
    r"\b(?:my name is|my name's|name is|the name's|the name is|this is|i am|i'm|im|it's|it is|call me|myself|name's)\s+(.+)",
    re.IGNORECASE,
)
_FAMILY = re.compile(
    r"\bfor my (son|daughter|kid|child|mother|father|mom|dad|mum|wife|husband|brother|sister|grandmother|grandfather)"
    r"[,]?\s+(?:his name is |her name is |he's |she's |named |called |who is )?([A-Za-z][a-z]+(?:\s+[A-Z][a-z]+)?)",
    re.IGNORECASE,
)
_LEAD_FILLERS = re.compile(r"^(?:(?:yeah|yes|sure|okay|ok|um+|uh+|so|well|it's|its|it is|this is|that's|thats|hi|hello)[,.!]?\s+)+", re.IGNORECASE)


def _name_words(fragment: str, limit: int = 4) -> Optional[str]:
    words = []
    for w in re.findall(r"[A-Za-z][A-Za-z'-]*|[^A-Za-z\s]", fragment):
        if not re.match(r"[A-Za-z]", w) or w.lower() in _NON_NAME or len(words) >= limit:
            break
        words.append(w)
    if not words or len(" ".join(words)) < 2:
        return None
    return " ".join(w[:1].upper() + w[1:] for w in words)


def name_from(text: str, expect: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """(name, patient): the caller's name, and the patient's when booking for someone else."""
    patient = None
    fam = _FAMILY.search(text or "")
    if fam:
        candidate = _name_words(fam.group(2), limit=3)
        if candidate and candidate.split()[0].lower() not in _NON_NAME:
            patient = candidate
    name = None
    m = _INTRO.search(text or "")
    if m:
        name = _name_words(m.group(1))
        cue = m.group(0)[:len(m.group(0)) - len(m.group(1))].strip().lower()
        if name and cue in ("it's", "it is", "i am", "i'm", "im", "this is") and expect not in ("name", "spelling") \
                and not m.group(1)[:1].isupper():
            name = None          # "it's unbearable", "I'm calling about": not a name unless she asked for one
    if not name:
        m = re.match(r"^\s*([A-Za-z]+(?:\s+[A-Za-z]+)?)\s+here\b", text or "", re.IGNORECASE)
        if m:
            name = _name_words(m.group(1))
    if not name and expect == "spelling":
        letters = re.findall(r"\b([A-Za-z])\b", text or "")
        if len(letters) >= 2:
            name = "".join(letters).title()
    if not name and expect in ("name", "spelling"):
        bare = _LEAD_FILLERS.sub("", (text or "").strip()).strip(" .!,")
        bare = re.sub(r"\s+here$", "", bare, flags=re.IGNORECASE)
        words = bare.split()
        if 1 <= len(words) <= 4 and all(re.fullmatch(r"[A-Za-z][A-Za-z'-]*", w) for w in words) \
                and not any(w.lower() in _NON_NAME for w in words):
            name = " ".join(w[:1].upper() + w[1:] for w in words)
    return (patient or name), patient


_RELATION = re.compile(
    r"\bfor my (son|daughter|kid|child|mother|father|mom|dad|mum|wife|husband|brother|sister|grandmother|grandfather)\b",
    re.IGNORECASE)
_AGE = re.compile(
    r"\b(?:(?:he|she)(?:'s| is)|aged?|age is)\s+(?:only\s+|just\s+)?(\d{1,2})\b(?!\s*(?:am|pm|a\.m|p\.m|o'?clock|:))|"
    r"\b(\d{1,2})\s*(?:years?|yrs?)(?:\s+old)?\b",
    re.IGNORECASE)


_SELF_INTRO = re.compile(r"(?:my name is|my name's|i am|i'm|this is|myself)\s+([A-Za-z]+(?:\s+[A-Z][a-z]+)?)",
                         re.IGNORECASE)


def family_from(text: str, patient: Optional[str], expect: Optional[str] = None):
    """(relation, age, caller_name) for a family booking, as a listener hears them."""
    t = text or ""
    rel = _RELATION.search(t)
    relation = rel.group(1).lower() if rel else None
    age = None
    m = _AGE.search(t)
    if m:
        age = int(m.group(1) or m.group(2))
    elif expect == "age" and re.fullmatch(r"\s*(?:he'?s |she'?s )?(\d{1,2})\.?\s*", t):
        age = int(re.search(r"\d+", t).group(0))
    caller = None
    if patient:
        for intro in _SELF_INTRO.finditer(t):
            name = _name_words(intro.group(1))
            if name and name.split()[0].lower() != patient.split()[0].lower() \
                    and name.split()[0].lower() not in _NON_NAME:
                caller = name
    return relation, (age if age is not None and 0 < age < 120 else None), caller


# ---------------------------------------------------------------- intents and questions

_BOT = re.compile(
    r"\b(are you (a |an )?(bot|robot|machine|ai|computer|real|human|recording|real person|person)|"
    r"is this (a |an )?(bot|robot|machine|recording|ai|real person|computer|human)|"
    r"am i (talking|speaking) (to|with) (a |an )?(real|human|person|bot|machine|computer|robot|ai)|"
    r"you sound like a (bot|robot|machine))\b"
)
_HUMAN = re.compile(
    r"\b(talk|speak|connect|put me through|transfer)\b.*\b(person|human|someone|somebody|staff|manager|receptionist|"
    r"front desk|real one)\b|\b(i want|i need|let me|can i|could i)\b.*\b(real person|human)\b|\brather speak to\b|"
    r"\b(have|get) (someone|somebody|the team|a person) (to )?call me\b"
)
_EMERGENCY = re.compile(
    r"\b(swell|swollen|swelling|bleeding|bleed|severe|unbearable|excruciating|fever|accident|broke|broken|"
    r"knocked out|can'?t open my mouth|emergency|urgent|urgently|really bad pain|terrible pain|lot of pain|killing me)\b"
)
_CANCEL = re.compile(r"\b(cancel|cancell?ing|call off|won'?t be able to (make|come)|can'?t make it)\b")
_RESCHEDULE = re.compile(
    r"\b(reschedule|re-schedule|prepone|postpone|move (my|the|it|that)|change (my|the) (appointment|booking|slot)|"
    r"shift (my|the|it)|change it to|move it to|push (it|my))\b"
)
_CHECK = re.compile(r"\b(when is my|check my|confirm my|what time is my|do i have an?|details of my) (appointment|booking)\b|"
                    r"\b(when|what time|what day|which day) (is )?my (appointment|booking)\b")
_BOOK = re.compile(
    r"\b(book|booking|appointment|schedule|fix (an|one|a|my)|slot|come in|visit|see (the|a) (doctor|dentist)|"
    r"get (my teeth|a|an|it)|i need (a|an)|i want (a|an|to get)|can i come|could i come)\b"
)
_END = re.compile(r"\b(bye|goodbye|good bye|that'?s all|that is all|nothing else|no thanks|no, thanks|that'?ll be all|nothing)\b")
_Q_START = re.compile(
    r"^(what|what's|whats|where|where's|when|which|who|who's|why|how|how's|do you|does|did you|can you|could you|"
    r"is there|is it|are you|are there|will you|would you|should i|tell me|i want to know|i wanted to know|"
    r"may i know|any idea)\b"
)
_INFO = re.compile(
    r"\b(price|prices|pricing|cost|costs|charge|charges|fee|fees|rate|rates|how much|insurance|cashless|parking|park|"
    r"address|located|location|where are you|directions|timings|timing|hours|open|close|closing|emi|instalments?|upi|"
    r"pay|payment|bring|how long|hurt|hurts|painful|pain free|comfortable|sterili[sz]|hygiene|x-?ray|walk-?in|"
    r"languages?|kannada|hindi|whitening|implants?|who are you|about the clinic|about your clinic|about you|"
    r"what can you|how can you|what all|services|treatments|doctors|dentists|how good|the best|weather|how are you)\b"
)
_BOOKING_QUERY = re.compile(r"\b(available|free|slot|slots|openings?|can i (book|come|get)|could i (book|come|get)|is there (a|any) (slot|time))\b")
_STRONG_INFO = re.compile(
    r"\b(price|prices|pricing|cost|costs|how much|fees?|charges?|insurance|cashless|parking|address|timings?|"
    r"opening hours|emi|upi|directions)\b"
)
_TAG_QUESTION = re.compile(r",\s*(no|na|right|isn'?t it|correct)\?$")
_SMALL_TALK = re.compile(r"\b(raining|weather|how'?s your day|how is your day|how are you|hope you'?re|traffic)\b")
# "Can we make it Saturday instead?" changes a detail; it isn't a question to answer.
_CHANGE_REQUEST = re.compile(r"\b(make it|change it to|move it to|instead|can we do|could we do|shift it to)\b")
# An explicit request to book, as opposed to a question that merely names a
# service or branch ("How much is a root canal?" asks a price, it books nothing).
_BOOK_REQUEST = re.compile(
    r"\b(book|booking|schedule|an appointment|one appointment|fix (an|one|a) appointment|"
    r"i'?d like (a|an|to)|i would like (a|an|to)|i want (a|an|to)|i need (a|an|to)|can i (get|come|book)|"
    r"could i (get|come|book))\b"
)
_NONANSWER = re.compile(
    r"\b(not sure|don'?t know|no idea|let me think|i'?ve been (really |very )?busy|been (really |very )?busy|"
    r"haven'?t decided|whatever (works|you think|is fine)|you tell me|anything is fine|up to you)\b"
)
_FRAGMENT_END = {
    "the", "a", "an", "to", "and", "but", "or", "what's", "whats", "can", "you", "at", "for", "of", "my", "is", "with",
    "com", "tell", "what", "how", "so", "if", "i", "i'm", "your", "me", "on", "in", "don't", "dont", "best", "about",
}


def intent_of(low: str, has_booking_detail: bool) -> Optional[str]:
    if _BOT.search(low):
        return "bot"
    if _HUMAN.search(low):
        return "human"
    if _EMERGENCY.search(low):
        return "emergency"
    if _CANCEL.search(low):
        return "cancel"
    if _RESCHEDULE.search(low):
        return "reschedule"
    if _CHECK.search(low):
        return "check"
    words = re.findall(r"[a-z']+", low)
    if _END.search(low) and (len(words) <= 6 or not (_BOOK.search(low) or has_booking_detail)):
        return "end"
    if _BOOK.search(low) or has_booking_detail:
        return "book"
    return None


# ---------------------------------------------------------------- dates and times

_DATE_WORDS = re.compile(
    r"\b((?:next|this|coming)\s+week(?:end)?|weekend|as soon as possible|asap|earliest|any day|"
    r"(?:\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:" + "|".join(lines.MONTHS) + r"))|"
    r"(?:(?:" + "|".join(lines.MONTHS) + r")\s+\d{1,2}(?:st|nd|rd|th)?)|"
    r"(?:(?:next|this|coming)\s+)?(?:" + "|".join(lines.WEEKDAYS) + r"))\b"
)


def _when(text: str, expect: Optional[str], phone_span, today: date):
    """(date_phrase, time_phrase, date, time, issue) using the project's own date parser."""
    t = text or ""
    if phone_span:
        a, b = phone_span
        t = t[:a] + " " + t[b:]
    expecting = expect if expect in ("date", "time") else None
    when = dateparse.parse_when(t, today=today, expecting=expecting)
    date_phrase = time_phrase = None
    d = tm = None
    issue = when.issues[0].code if when.issues else None
    if when.date is not None:
        if when.date.exact:
            d = when.date.start
            date_phrase = d.strftime("%d %B %Y")
        else:
            m = _DATE_WORDS.search(lines.norm(t))
            date_phrase = m.group(0) if m else lines.norm(t)
    elif issue in ("SUNDAY", "PAST", "BEYOND_HORIZON", "INVALID_DAY"):
        m = _DATE_WORDS.search(lines.norm(t))
        if m:
            date_phrase = m.group(0)
    if when.time is not None:
        tc = when.time
        if tc.kind == "exact" and tc.start is not None:
            tm = tc.start
            time_phrase = tm.strftime("%I:%M %p")
        elif tc.label:
            time_phrase = tc.label
    elif issue == "OUTSIDE_HOURS":
        m = re.search(r"\d{1,2}(?::\d{2})?\s*(?:am|pm)?", lines.norm(t))
        time_phrase = m.group(0) if m else None
    return date_phrase, time_phrase, d, tm, issue


# ---------------------------------------------------------------- doctors

_DOCTOR = re.compile(r"\b(?:dr\.?|doctor)\s+([a-z]+)\b", re.IGNORECASE)


def doctor_from(text: str) -> Optional[str]:
    for m in _DOCTOR.finditer(text or ""):
        word = m.group(1)
        if word.lower() not in _NON_NAME and word.lower() not in ("will", "can", "is", "said", "told"):
            return f"Dr {word[:1].upper()}{word[1:].lower()}"
    return None


# ---------------------------------------------------------------- read()


def read(text: str, expect: Optional[str] = None, today: Optional[date] = None) -> Reading:
    """
    Everything a competent listener takes from one caller turn. `expect` is
    what Emma just asked ("name", "phone", "date", "time", "yes_no", "choice",
    "spelling", "open"): it lets a bare "Priya Sharma" count as a name and a
    bare "5" as 5 PM, exactly as a person would hear them.
    """
    today = today or clock.today()
    r = Reading(text=text or "", expect=expect)
    raw = (text or "").strip()
    if not raw:
        return r
    low = lines.norm(raw)

    r.phone, r.phone_valid, span = phone_from(raw)
    r.name, r.patient = name_from(raw, expect)
    r.relation, r.age, r.caller_name = family_from(raw, r.patient, expect)
    if r.name and r.phone and r.name.isdigit():
        r.name = None
    services = lines.services_in(raw)
    unknown = lines.unknown_services_in(raw)
    r.service = services[0] if services else None
    r.service_phrase = r.service or (unknown[0] if unknown else None)
    branches = lines.branches_in(raw)
    r.branch = branches[0] if branches else None
    r.doctor = doctor_from(raw)
    if r.doctor and r.name and r.doctor.split()[-1].lower() == r.name.split()[-1].lower():
        r.name = None
    r.date_phrase, r.time_phrase, r.date, r.time, r.date_issue = _when(raw, expect, span, today)
    if _SMALL_TALK.search(lines.norm(raw)):
        # "It's been raining a lot today, no?" mentions a day; it doesn't choose one.
        r.date_phrase = r.time_phrase = r.date = r.time = r.date_issue = None

    r.nonanswer = bool(_NONANSWER.search(low))
    r.yes_no = None if r.nonanswer else lines.yes_no(raw)
    if _TAG_QUESTION.search(low):
        r.yes_no = None                      # "It's been raining a lot, no?" is not a refusal
    has_detail = bool(r.service_phrase or r.date_phrase or r.time_phrase or r.branch or r.doctor)
    r.intent = intent_of(low, has_detail)
    if r.intent == "end" and r.yes_no is None and expect in ("yes_no",) and re.fullmatch(r"(no,? )?nothing\.?", low):
        r.yes_no = "no"

    words = re.findall(r"[a-z']+", low)
    wh_question = bool(_Q_START.search(low))
    info = bool(_INFO.search(low))
    # "Sunday at 10?" offers an answer with a rising tone; it asks nothing.
    is_q_form = wh_question or ("?" in raw and (info or len(words) > 5 or not has_detail))
    # A statement is only a question when it is short and plainly asks for
    # information ("Price for a cleaning"); "I can't open my mouth" is not about hours.
    asks = is_q_form or (bool(_STRONG_INFO.search(low)) and len(words) <= 8)
    booking_query = bool(_BOOKING_QUERY.search(low)) and (has_detail or r.intent in ("book", "reschedule"))
    change_request = bool(_CHANGE_REQUEST.search(low)) and has_detail
    if r.intent == "bot":
        r.question = raw
    elif asks and not booking_query and not change_request \
            and not (r.intent in ("cancel", "reschedule", "check") and not info):
        if not (r.intent == "book" and not info and is_q_form and re.match(r"^(can|could|may) i\b", low)):
            r.question = raw
    # A question that only mentions a service, branch or day asks about it; it
    # doesn't book it ("What time do you open on Saturday?"). "Sunday at 10?"
    # or "How about Monday?" answer Emma's question, so they keep their details.
    plain_question = wh_question and not re.match(r"^(what|how) about\b", low)
    if r.question and r.intent in (None, "book", "end") and not _BOOK_REQUEST.search(low) \
            and (plain_question or not (r.date_phrase or r.time_phrase)):
        r.intent = "question" if r.intent != "end" else r.intent

    if r.intent in ("reschedule", "cancel", "check"):
        _split_move(r, raw, expect, today)

    last = words[-1] if words else ""
    r.fragment = bool(words) and not re.search(r"[.!?]$", raw) and (
        last in _FRAGMENT_END or (len(words) == 1 and last in ("don't", "dont", "but", "and", "so", "what"))
    )
    return r


_MOVE_TO = re.compile(r"^(?P<old>.*?)[,.]?\s*(?:and\s+)?(?:i'?d like to|i want to|i wanna|can you|could you|please)?\s*"
                      r"(?:move|shift|change|prepone|postpone|reschedule|bring)\s+it\s+(?:forward\s+)?to\s+(?P<new>.+)$",
                      re.IGNORECASE)


def _split_move(r: Reading, raw: str, expect, today: date) -> None:
    """
    "It's this Monday, and I'd like to move it to Friday": the first date is the
    appointment's (verification), the second the new one, as a model reads it.
    """
    m = _MOVE_TO.match(raw)
    if not m or not m.group("old").strip():
        return
    old = _when(m.group("old"), "date", None, today)
    if not old[0]:
        return
    r.appt_date_phrase = old[0]
    r.date_phrase, r.time_phrase, r.date, r.time, r.date_issue = _when(m.group("new"), expect, None, today)


# ---------------------------------------------------------------- grounded answers


@lru_cache(maxsize=1)
def _facts() -> dict:
    try:
        with open(config.CLINIC_FACTS_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _fact(fact_id: str) -> Optional[str]:
    facts = _facts()
    if fact_id in ("hours", "pricing_policy", "insurance_policy"):
        entry = facts.get(fact_id) or {}
        return entry.get("text") if entry.get("verified") else None
    for entry in facts.get("facts", []):
        if entry.get("id") == fact_id and entry.get("verified"):
            return entry.get("text")
    return None


def _branch_fact(branch: str, key: str) -> Optional[str]:
    for entry in _facts().get("branches", []):
        if entry.get("name") == branch and entry.get("verified"):
            return entry.get(key)
    return None


_PRICE_FACT = {
    "General Check-up": "price.consultation", "Consultation": "price.consultation", "Teeth Cleaning": "price.cleaning",
    "Tooth Filling": "price.filling", "Root Canal Treatment": "price.root_canal", "Tooth Extraction": "price.extraction",
    "Braces": "price.braces", "Invisalign": "price.invisalign", "Pediatric Dentistry": "price.pediatric",
}


def answer(question: str) -> tuple[Optional[str], Optional[str]]:
    """
    (spoken answer, fact id) for a caller's question, grounded only in
    clinic_facts.json, the way the NLU prompt tells the model to answer.
    (None, None) means the facts don't cover it, and the real model then
    returns the escalation line. That is deliberately what happens today for
    meta questions ("how can you help?", "who are you?", "tell me about the
    clinic"): clinic_facts.json has no "what I can do" fact, which is one of
    the bugs in docs/HANDOFF.md section 5. "@HONEST@" marks the honesty line.
    """
    low = lines.norm(question)
    if _BOT.search(low):
        return "@HONEST@", "honest"
    services = lines.services_in(question)
    branches = lines.branches_in(question)
    if re.search(r"\b(price|prices|pricing|cost|costs|charge|charges|fee|fees|rate|rates|how much)\b", low):
        if re.search(r"\bwhitening\b", low):
            return _fact("price.whitening"), "price.whitening"
        if re.search(r"\bimplants?\b", low):
            return _fact("price.implant"), "price.implant"
        if re.search(r"\b(cancel|reschedul)", low):
            return _fact("policy.cancellation"), "policy.cancellation"
        if services:
            fid = _PRICE_FACT[services[0]]
            return _fact(fid), fid
        if re.search(r"\b(kid|kids|child|children|son|daughter)\b", low):
            return _fact("price.pediatric"), "price.pediatric"
        return _fact("pricing_policy"), "pricing_policy"
    if re.search(r"\b(whitening)\b", low):
        return _fact("price.whitening"), "price.whitening"
    if re.search(r"\b(implants?)\b", low):
        return _fact("price.implant"), "price.implant"
    if re.search(r"\b(emi|instalments?|installments?)\b", low):
        return _fact("payment.emi"), "payment.emi"
    if re.search(r"\b(upi|card|cash|pay|payment|gpay|paytm)\b", low):
        return _fact("payment.methods"), "payment.methods"
    if re.search(r"\b(insurance|cashless|claim)\b", low):
        return _fact("insurance_policy"), "insurance_policy"
    if re.search(r"\b(parking|park)\b", low):
        branch = branches[0] if branches else config.DEFAULT_BRANCH
        return _branch_fact(branch, "parking"), f"branch.{branch}.parking"
    if re.search(r"\b(address|located|location|where are you|where is|where's|directions|how do i get)\b", low):
        if branches:
            addr = _branch_fact(branches[0], "address")
            return (f"Our {branches[0]} branch is at {addr}." if addr else None), f"branch.{branches[0]}.address"
        names = [b.get("name") for b in _facts().get("branches", []) if b.get("verified")]
        if names:
            return f"We have branches in {', '.join(names[:-1])} and {names[-1]}.", "branches"
    if re.search(r"\b(timings?|hours|open|close|closing|sunday|saturday|what time do you|best time)\b", low):
        return _fact("hours"), "hours"
    if re.search(r"\b(bring|carry)\b", low):
        return _fact("visit.what_to_bring"), "visit.what_to_bring"
    if re.search(r"\bhow long\b|\bhow many (visits|sittings|sessions)\b|\bsittings\b", low):
        if "Root Canal Treatment" in services:
            return _fact("visit.root_canal_sittings"), "visit.root_canal_sittings"
        return _fact("visit.first_visit"), "visit.first_visit"
    if re.search(r"\b(x-?rays?)\b", low):
        return _fact("visit.xray"), "visit.xray"
    if re.search(r"\b(hurt|hurts|painful|pain free|comfortable|anaesthe|anesthe)\b", low):
        return _fact("visit.comfort"), "visit.comfort"
    if re.search(r"\b(sterili[sz]|hygiene|hygienic|clean instruments|disposable)\b", low):
        return _fact("visit.hygiene"), "visit.hygiene"
    if re.search(r"\b(walk-?ins?|walk in)\b", low):
        return _fact("policy.walk_in"), "policy.walk_in"
    if re.search(r"\b(languages?|kannada|hindi|tamil|telugu)\b", low):
        return _fact("clinic.languages"), "clinic.languages"
    if re.search(r"\b(kid|kids|child|children|minor)\b", low):
        return _fact("visit.children"), "visit.children"
    if re.search(r"\bemergenc", low):
        return _fact("policy.emergency"), "policy.emergency"
    if re.search(r"\b(what|which) (services|treatments)\b|\bwhat do you (do|offer)\b|\bdo you (do|offer)\b", low):
        return ("We do check-ups, cleanings, fillings, root canals, extractions, braces, Invisalign "
                "and children's dentistry."), "services"
    return None, None
