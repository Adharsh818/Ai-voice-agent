"""
Reading Emma's lines without knowing the engine.

The simulated caller has to answer what Emma actually asked, and the metrics
have to know which question she asked, which values she read back and whether
she claimed an action. Both work from her words alone, so they keep working
when the 12-step engine is replaced: nothing here imports ai_engine.

Everything is heuristic and deliberately forgiving about wording ("What day
suits you?", "Which day works?", "When would you like to come in?" are all a
date question). The clinic vocabulary (services, aliases, branches) lives here
too, because the fake NLU reads the caller's side with the same words.
"""

import re
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from typing import Optional

import clock

# ---------------------------------------------------------------- clinic vocabulary

SERVICES = [
    "General Check-up", "Consultation", "Teeth Cleaning", "Tooth Filling", "Root Canal Treatment",
    "Tooth Extraction", "Braces", "Invisalign", "Pediatric Dentistry",
]

# Spoken phrase -> canonical service. Longest phrases are tried first, so
# "root canal" wins over "canal" and "wisdom tooth" over "tooth".
SERVICE_ALIASES = {
    "general check-up": "General Check-up", "general checkup": "General Check-up",
    "general check up": "General Check-up", "check-up": "General Check-up", "checkup": "General Check-up",
    "check up": "General Check-up", "routine check": "General Check-up", "dental check": "General Check-up",
    "consultation": "Consultation", "consult": "Consultation", "toothache": "Consultation",
    "tooth ache": "Consultation", "tooth pain": "Consultation", "see the dentist": "Consultation",
    "see a dentist": "Consultation", "see the doctor": "Consultation",
    "teeth cleaning": "Teeth Cleaning", "cleaning": "Teeth Cleaning", "cleaned": "Teeth Cleaning",
    "scaling": "Teeth Cleaning", "polishing": "Teeth Cleaning",
    "tooth filling": "Tooth Filling", "filling": "Tooth Filling", "cavity": "Tooth Filling",
    "cavities": "Tooth Filling", "filled": "Tooth Filling",
    "root canal": "Root Canal Treatment", "route canal": "Root Canal Treatment", "rct": "Root Canal Treatment",
    "extraction": "Tooth Extraction", "pull out": "Tooth Extraction", "pulled out": "Tooth Extraction",
    "pulled": "Tooth Extraction", "removed": "Tooth Extraction", "remove a tooth": "Tooth Extraction",
    "wisdom tooth": "Tooth Extraction",
    "braces": "Braces", "orthodontic": "Braces",
    "invisalign": "Invisalign", "clear aligners": "Invisalign", "aligners": "Invisalign",
    "pediatric": "Pediatric Dentistry", "paediatric": "Pediatric Dentistry", "kids dentist": "Pediatric Dentistry",
    "child dentist": "Pediatric Dentistry", "children's dentist": "Pediatric Dentistry",
    "for my kid": "Pediatric Dentistry", "for my child": "Pediatric Dentistry",
    "for my son": "Pediatric Dentistry", "for my daughter": "Pediatric Dentistry",
}
_ALIAS_ORDER = sorted(SERVICE_ALIASES, key=len, reverse=True)

# Things callers ask for that the clinic talks about but does not book directly.
UNKNOWN_SERVICES = {
    "whitening": "teeth whitening", "implant": "dental implant", "crown": "crown", "veneer": "veneers",
    "denture": "dentures", "bridge": "bridge",
}

BRANCHES = ["Nagarbhavi", "Indiranagar", "Jayanagar", "Whitefield"]
BRANCH_ALIASES = {
    "nagarbhavi": "Nagarbhavi", "nagarabhavi": "Nagarbhavi", "nagar bhavi": "Nagarbhavi",
    "indiranagar": "Indiranagar", "indira nagar": "Indiranagar",
    "jayanagar": "Jayanagar", "jaya nagar": "Jayanagar",
    "whitefield": "Whitefield", "white field": "Whitefield",
}

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
    "oct": 10, "nov": 11, "dec": 12,
}
WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
_MON = "|".join(sorted(MONTHS, key=len, reverse=True))
_WD = "|".join(WEEKDAYS)


def norm(text: str) -> str:
    """Lower case, curly quotes straightened, whitespace collapsed."""
    return re.sub(r"\s+", " ", (text or "").lower().replace("’", "'").replace("‘", "'")).strip()


def split_sentences(text: str) -> list[str]:
    """Sentences, keeping their end punctuation. Decimal points and "Dr." do not split."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", text)
    out = []
    for part in parts:
        if out and re.search(r"\b(?:Dr|Mr|Mrs|Ms)\.$", out[-1]):
            out[-1] = f"{out[-1]} {part}"
        else:
            out.append(part)
    return [p.strip() for p in out if p.strip()]


def similarity_key(text: str) -> str:
    """What two lines are compared on: words only, lower case, no punctuation."""
    return " ".join(re.findall(r"[a-z0-9']+", norm(text)))


# ---------------------------------------------------------------- values in a line

def services_in(text: str) -> list[str]:
    """Canonical services named in `text`, in order of first mention."""
    low = norm(text)
    found: list[tuple[int, str]] = []
    taken: list[tuple[int, int]] = []
    for canon in SERVICES:
        for m in re.finditer(re.escape(canon.lower()), low):
            found.append((m.start(), canon))
            taken.append((m.start(), m.end()))
    for alias in _ALIAS_ORDER:
        for m in re.finditer(rf"(?<![a-z]){re.escape(alias)}(?:e?s)?(?![a-z])", low):     # plurals: "cleanings"
            if any(a <= m.start() < b for a, b in taken):
                continue
            found.append((m.start(), SERVICE_ALIASES[alias]))
            taken.append((m.start(), m.end()))
    out = []
    for _, canon in sorted(found):
        if canon not in out:
            out.append(canon)
    return out


def unknown_services_in(text: str) -> list[str]:
    low = norm(text)
    return [label for key, label in UNKNOWN_SERVICES.items() if re.search(rf"\b{key}", low)]


def branches_in(text: str) -> list[str]:
    low = norm(text)
    hits = []
    for alias, canon in BRANCH_ALIASES.items():
        m = re.search(rf"\b{re.escape(alias)}\b", low)
        if m:
            hits.append((m.start(), canon))
    out = []
    for _, canon in sorted(hits):
        if canon not in out:
            out.append(canon)
    return out


def doctors_in(text: str) -> list[str]:
    """Doctor surnames mentioned as "Dr X" / "Doctor X" / "Dr. First Last"."""
    out = []
    for m in re.finditer(r"\b(?:Dr\.?|Doctor)\s+([A-Z][a-z]+)(?:\s+([A-Z][a-z]+))?", text or ""):
        surname = m.group(2) or m.group(1)
        if surname not in out:
            out.append(surname)
    return out


_SPACED_DIGITS = re.compile(r"(?<!\d)(\d(?:[\s,.-]{1,3}\d){9,11})(?!\d)")
_GROUPED_DIGITS = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?(\d{5}[\s-]?\d{5}|\(\d{3}\)\s?\d{3}-\d{4}|\d{3}[\s-]\d{3}[\s-]\d{4})(?!\d)")


def phone_in(text: str) -> Optional[str]:
    """A 10-digit phone number read out in `text` ("9 8 4 5 0, 1 2 3 4 5" or "98450 12345")."""
    for m in _SPACED_DIGITS.finditer(text or ""):
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) == 12 and digits.startswith("91"):
            digits = digits[2:]
        if len(digits) == 10:
            return digits
    m = _GROUPED_DIGITS.search(text or "")
    if m:
        digits = re.sub(r"\D", "", m.group(1))
        return digits if len(digits) == 10 else None
    return None


def _strip_phone(text: str) -> str:
    text = _SPACED_DIGITS.sub(" ", text or "")
    return _GROUPED_DIGITS.sub(" ", text)


_TIME_RE = re.compile(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?\s?m\.?|p\.?\s?m\.?)(?![a-z])", re.IGNORECASE)
_OCLOCK_RE = re.compile(r"\b(\d{1,2})\s*o'?\s*clock\b", re.IGNORECASE)
_WORD_TIME_RE = re.compile(r"\b(\d{1,2})(?:[:.](\d{2}))?\s+in the\s+(morning|afternoon|evening)\b", re.IGNORECASE)
# A bare clock time the way a receptionist says it ("at 10", "10:30 or 11",
# "till 5"): only after a word that introduces a time and before a pause or a
# word that can follow one, so "1 or 2 visits" and "the 5th" are never read.
_BARE_TIME_RE = re.compile(
    r"\b(?:at|or|and|till|until|to|from|by|around|between)\s+(\d{1,2})(?::(\d{2}))?"
    r"(?=\s*(?:[,.?!;]|$|\s(?:with|on|at|or|and|for|tomorrow|today|this|next|works?|would|is|if|then)\b))",
    re.IGNORECASE)


def _bare_hour(h: int) -> Optional[int]:
    """The 24-hour reading of a bare "5" or "10": the one inside clinic hours (prompts.speak_time's rule)."""
    import config
    start, end = config.CLINIC_START_HOUR, config.CLINIC_END_HOUR
    if h == 12:
        return 12
    if not 1 <= h <= 11:
        return None
    if start <= h < end:
        return h
    return h + 12 if start <= h + 12 < end else None


def times_in(text: str) -> list[time]:
    """Clock times said in `text`, in order ("05:00 PM", "5:30 pm", "11 in the morning", "noon")."""
    text = _strip_phone(text)
    hits: list[tuple[int, time]] = []
    for m in _TIME_RE.finditer(text):
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        pm = m.group(3).lower().startswith("p")
        if 1 <= h <= 12 and mi < 60:
            h = (h % 12) + (12 if pm else 0)
            hits.append((m.start(), time(h, mi)))
    for m in _WORD_TIME_RE.finditer(text):
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        if 1 <= h <= 12 and mi < 60:
            if m.group(3).lower() != "morning" and h < 12:
                h += 12
            hits.append((m.start(), time(h, mi)))
    for m in _BARE_TIME_RE.finditer(text):
        h, mi = _bare_hour(int(m.group(1))), int(m.group(2) or 0)
        if h is not None and mi < 60:
            hits.append((m.start(1), time(h, mi)))
    for m in _OCLOCK_RE.finditer(text):
        h = int(m.group(1))
        if 1 <= h <= 12:
            hits.append((m.start(), time(h + 12 if h < 7 else h % 24, 0)))
    if re.search(r"\b(noon|midday)\b", text, re.IGNORECASE):
        hits.append((re.search(r"\b(noon|midday)\b", text, re.IGNORECASE).start(), time(12, 0)))
    out = []
    for _, t in sorted(hits, key=lambda x: x[0]):
        if t not in out:
            out.append(t)
    return out


def _upcoming(today: date, month: int, day: int) -> Optional[date]:
    for year in (today.year, today.year + 1):
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if d >= today:
            return d
    return None


def _upcoming_weekday_date(today: date, day: int, weekday: int) -> Optional[date]:
    """The first date from today on with this day of the month falling on this weekday (else the first one)."""
    first = None
    for k in range(13):
        year, month = today.year + (today.month - 1 + k) // 12, (today.month - 1 + k) % 12 + 1
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if d < today:
            continue
        if d.weekday() == weekday:
            return d
        first = first or d
    return first


def dates_in(text: str, today: Optional[date] = None) -> list[date]:
    """Calendar dates said in `text`, in order ("Monday the 05 October", "5th of October", "tomorrow")."""
    today = today or clock.today()
    low = norm(_strip_phone(text))
    hits: list[tuple[int, date]] = []
    for m in re.finditer(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MON})\b", low):
        d = _upcoming(today, MONTHS[m.group(2)], int(m.group(1)))
        if d:
            hits.append((m.start(), d))
    for m in re.finditer(rf"\b({_MON})\s+(?:the\s+)?(\d{{1,2}})(?:st|nd|rd|th)?\b", low):
        d = _upcoming(today, MONTHS[m.group(1)], int(m.group(2)))
        if d and not any(abs(pos - m.start()) < 12 for pos, _ in hits):
            hits.append((m.start(), d))
    for m in re.finditer(rf"\b({_WD})\s+the\s+(\d{{1,2}})(?:st|nd|rd|th)\b", low):
        # "Monday the 5th" (how Emma says a day): the next date with that number on that weekday.
        d = _upcoming_weekday_date(today, int(m.group(2)), WEEKDAYS[m.group(1)])
        if d and not any(abs(pos - m.start()) < 12 for pos, _ in hits):
            hits.append((m.start(), d))
    for word, offset in (("day after tomorrow", 2), ("tomorrow", 1), ("today", 0)):
        m = re.search(rf"\b{word}\b", low)
        if m and not any(pos <= m.start() < pos + len(word) + 10 for pos, _ in hits if word == "tomorrow"):
            hits.append((m.start(), today + timedelta(days=offset)))
            if word == "day after tomorrow":
                low = low.replace("day after tomorrow", " " * len("day after tomorrow"))
    dated = [pos for pos, _ in hits]
    for m in re.finditer(rf"\b(?:(this|next|coming)\s+)?({_WD})\b", low):
        # A bare weekday counts on its own ("it's this Monday, move it to the day
        # after tomorrow"), unless it only leads a fuller date ("Monday the 5th").
        if any(0 <= pos - m.start() <= 24 for pos in dated):
            continue
        ahead = (WEEKDAYS[m.group(2)] - today.weekday()) % 7
        if ahead == 0 and m.group(1) != "this":
            ahead = 7
        hits.append((m.start(), today + timedelta(days=ahead)))
    out = []
    for _, d in sorted(hits, key=lambda x: x[0]):
        if d not in out:
            out.append(d)
    return out


def mentions_time(text: str, t: time) -> bool:
    return t in times_in(text)


def mentions_date(text: str, d: date, today: Optional[date] = None) -> bool:
    return d in dates_in(text, today)


def summarises(line: str, start, service: Optional[str] = None, today: Optional[date] = None) -> bool:
    """
    Is `line` a summary of the action at `start` (a datetime)? It must say the
    day and the time, and name the service or ask for the go-ahead ("Shall I
    book it?"). An offer of the same slot ("I could do 10 AM on Monday, would
    that work?") is not a summary, so a booking straight after one is caught.
    """
    if not (mentions_time(line, start.time()) and mentions_date(line, start.date(), today)):
        return False
    if service and service in services_in(line):
        return True
    return bool(_COMMIT_Q.search(norm(_question_sentence(line))))


def mentions_name(text: str, name: str) -> bool:
    """True when the first name (or any part of a one-word name) is in `text`."""
    parts = [p for p in re.findall(r"[a-z]+", norm(name)) if len(p) > 1]
    return bool(parts) and re.search(rf"\b{re.escape(parts[0])}\b", norm(text)) is not None


# ---------------------------------------------------------------- caller yes / no

_AFFIRMATIVE_IDIOMS = ("no problem", "no worries", "not a problem", "no issue", "why not")
_NEG = re.compile(
    r"\b(no|nope|nah|not|don'?t|doesn'?t|isn'?t|wasn'?t|won'?t|can'?t|cannot|wrong|incorrect|"
    r"never ?mind|rather not)\b"
)
_AFF = re.compile(
    r"\b(yes|yeah|yep|yup|yea|sure|correct|right|ok|okay|exactly|absolutely|definitely|certainly|"
    r"of course|go ahead|please do|sounds good|perfect|great|fine|works|that works|confirm(?:ed)?|"
    r"alright|all right|done|book it|go for it)\b"
)


def yes_no(text: str) -> Optional[str]:
    """
    "yes", "no" or None for a caller's words. Negation is checked first, so
    "that's not right" is a no; a few idioms ("no problem") are a yes. A long
    sentence that merely contains "okay" is not a clear yes: the word has to
    lead the utterance or the utterance has to be short.
    """
    low = norm(text)
    if not low:
        return None
    if any(i in low for i in _AFFIRMATIVE_IDIOMS):
        rest = low
        for i in _AFFIRMATIVE_IDIOMS:
            rest = rest.replace(i, " ")
        return None if _NEG.search(rest) else "yes"
    words = re.findall(r"[a-z']+", low)
    if _NEG.search(low):
        return "no"
    m = _AFF.search(low)
    if m and (len(words) <= 6 or m.start() <= 12):
        return "yes"
    return None


def is_clear_yes(text: str) -> bool:
    """A yes with nothing that changes it ("yes but...", "yes, change the date")."""
    low = norm(text)
    return yes_no(text) == "yes" and not re.search(r"\b(but|change|instead|wait|actually|hold on)\b", low)


# ---------------------------------------------------------------- what Emma claims

# "you're booked", "I've cancelled it", "that's moved": a statement that the
# action happened. Questions ("Shall I book it?") never count.
_CLAIMS = {
    "booked": re.compile(
        r"\b(you'?re (all )?(booked|set|confirmed)|you are (all )?(booked|set|confirmed)|i'?ve booked|i have booked|"
        r"(it'?s|that'?s|is|has been|have been) (now )?(booked|confirmed)|booked (you|it|that) in|"
        r"booking is (confirmed|done)|all booked|appointment is (booked|confirmed|fixed))\b"),
    "cancelled": re.compile(
        r"\b(i'?ve cancell?ed|i have cancell?ed|(it'?s|that'?s|is|has been|have been) (now )?cancell?ed|"
        r"cancell?ed (it|that|your)|appointment is cancell?ed|all cancell?ed)\b"),
    "moved": re.compile(
        r"\b(i'?ve (moved|rescheduled|changed)|i have (moved|rescheduled|changed)|"
        r"(it'?s|that'?s|is|has been|have been) (now )?(moved|rescheduled|changed)|"
        r"(moved|rescheduled) (it|that|you|your)|appointment is (now )?(moved|rescheduled))\b"),
}


_NO_ROOM = re.compile(r"\b(?:morning|afternoon|evening|night|day|today|tomorrow|\d+(?:st|nd|rd|th)?),? is all booked\b|"
                      r"\bfully booked\b|\bbooked up\b")


def claims(text: str) -> set[str]:
    """Which actions Emma states as done in `text`: {"booked", "cancelled", "moved"}."""
    out = set()
    for sentence in split_sentences(text):
        if sentence.rstrip().endswith("?"):
            continue
        low = norm(sentence)
        if _NO_ROOM.search(low):
            continue             # "Tuesday morning is all booked, I'm afraid": no room, not a booking made
        for action, pattern in _CLAIMS.items():
            if pattern.search(low):
                out.add(action)
    return out


# ---------------------------------------------------------------- doctor deflection (M4)

DEFLECTION_PATTERNS = [
    r"doctor (can|will|could|would) (go through|discuss|explain|tell you|let you know|check|advise|talk you through|answer)",
    r"(discuss|go through|talk about|check) (that|this|it) with (the|your) doctor",
    r"at (your|the) (visit|appointment|consultation)\b",
    r"ask (the|your) doctor",
    r"doctor (is|would be) (the )?best (person|placed)",
    r"(best|better) (to )?(check|discuss|ask) with (the|a|your) doctor",
]
_DEFLECTION = [re.compile(p) for p in DEFLECTION_PATTERNS]


def deflection(text: str) -> Optional[str]:
    """The doctor-deflection phrase in `text`, if any ("the doctor can go through that ... at your visit")."""
    low = norm(text)
    for pattern in _DEFLECTION:
        m = pattern.search(low)
        if m:
            return m.group(0)
    return None


_CLINICAL = re.compile(
    r"\b(do i need|should i|is it (normal|serious|bad|safe)|what (medicine|tablet|painkiller)|"
    r"which (medicine|tablet)|diagnos|infection|antibiotic|prescri|swelling|swollen|bleeding|"
    r"what('s| is) wrong|why does it hurt|can i (eat|drink|take)|is it (an )?emergency|"
    r"do i have|could it be|pregnan|diabet|blood pressure)\b"
)


def looks_clinical(text: str) -> bool:
    """A genuinely clinical question (diagnosis, "do I need X", medication): the doctor line is fair there."""
    return bool(_CLINICAL.search(norm(text)))


# ---------------------------------------------------------------- what is Emma asking?

@dataclass
class Ask:
    """What Emma's line asks the caller for."""
    kind: str                    # name phone service branch date time confirm choice yes_no open
                                 # anything_else spelling closing statement
    slot: Optional[str] = None   # confirm: name phone service branch date time summary generic
    options: list = field(default_factory=list)
    subject: Optional[str] = None  # choice: time | branch | doctor | date | service
    question: str = ""
    phone: Optional[str] = None
    dates: list = field(default_factory=list)
    times: list = field(default_factory=list)
    services: list = field(default_factory=list)
    branches: list = field(default_factory=list)

    @property
    def expect(self) -> str:
        """The listening hint this ask implies (the engine facade's `listening_hint` vocabulary)."""
        return {
            "name": "name", "phone": "phone", "confirm": "yes_no", "yes_no": "yes_no", "date": "date",
            "time": "time", "choice": "choice", "spelling": "spelling",
        }.get(self.kind, "open")

    @property
    def is_slot_question(self) -> bool:
        """An open question for one booking detail (not a read-back)."""
        return self.kind in ("name", "phone", "service", "branch", "date", "time")


_CONFIRM_Q = re.compile(
    r"(is that right|did i get that right|is that correct|have i got that right|that'?s right\?|right\?$|"
    r"correct\?$|is that okay|okay for you|does that work|would that (still )?work|that okay\?|"
    r"sound good|sounds good\?|is that it\?|yeah\?$|shall i (go ahead|book|confirm|cancel|move)|"
    r"should i (go ahead|book|confirm|cancel|move)|can i (go ahead|book|confirm)|want me to (book|go ahead|cancel|move)|"
    r"ok to (book|go ahead)|okay to (book|go ahead)|go ahead and (book|cancel|move))"
)
_SLOT_Q = [
    ("spelling", re.compile(r"\b(spell|spelling)\b")),
    ("phone", re.compile(r"\b(number|mobile|phone|contact)\b")),
    ("name", re.compile(r"\b(your name|full name|the name|name please|name,? please|who (is|'s) (it|the appointment) for|patient'?s name|name for the)\b|\bname\b")),
    ("service", re.compile(r"(visit for|what'?s it for|what is it for|which treatment|what treatment|which service|what service|"
                           r"what (do you need|brings you|seems to be|kind of)|what (can we|should we) (do|book)|reason for|"
                           r"come in for|appointment for\?|what'?s the appointment for|what would you like done)")),
    ("branch", re.compile(r"\b(branch|location|which area|which clinic|where would you like|which of our|nearest)\b")),
    ("date", re.compile(r"\b(what day|which day|what date|which date|a date|the date|when would|when do you|when (is|are) (good|best)|"
                        r"day (would|works|suits)|this week or next|which week|when'?s (good|best|easiest)|a day that'?s|"
                        r"when suits|when works)\b")),
    ("time", re.compile(r"\b(what time|which time|a time|time (works|suits|would)|another time|other time|morning or|"
                        r"afternoon or|evening or|earlier or later|what hour)\b")),
]
# Asking for the go-ahead on an action: what ends a summary, never an offer.
_COMMIT_Q = re.compile(
    r"\b(shall|should|can|may) i (go ahead|book|confirm|cancel|move|change|reschedule|lock)\b|\bbook (it|that|this)\b|"
    r"\bgo ahead and (book|cancel|move|change)\b|\bwant me to (book|go ahead|cancel|move)\b|\b(ok|okay) to (book|go ahead)\b"
)
# An offered slot rather than a read-back of one the caller chose.
_OFFERISH = re.compile(r"\b(could do|can do|i have|i've got|how about|free at|available|nearest|closest|earliest|"
                       r"next (free|available)|instead)\b")
_OPEN_Q = re.compile(r"(how can i help|how may i help|what can i do for you|how can i help you|go ahead|what would you like|"
                     r"how can i assist|what can i help)")
_ANYTHING_ELSE = re.compile(r"\banything else\b|\bsomething else\b|\bany other (questions?|help)\b")
_CLOSING = re.compile(r"\b(bye|goodbye|take care|have a (good|great|nice) (day|one|evening)|see you (then|soon))\b")


_NOT_NAMES = {
    "i", "so", "sure", "okay", "ok", "great", "thanks", "sorry", "and", "is", "that", "right", "did", "at", "a", "an",
    "the", "yes", "no", "perfect", "lovely", "alright", "just", "to", "confirm", "emma", "pearl", "dental", "dr",
    "doctor", "got", "it", "for", "your", "name", "shall", "should", "would", "could", "can", "let", "me", "check",
}


def _names_a_person(question: str) -> bool:
    """A read-back of a name: a capitalised word that is not a date, service, branch or doctor."""
    for m in re.finditer(r"\b[A-Z][a-z]+\b", question or ""):
        word = m.group(0).lower()
        if word in _NOT_NAMES or word in MONTHS or word in WEEKDAYS or word in BRANCH_ALIASES:
            continue
        before = question[max(0, m.start() - 4):m.start()].lower()
        if "dr" in before or "doctor" in before:
            continue
        if any(word in s.lower().split() for s in SERVICES):
            continue
        return True
    return False


def _question_sentence(text: str) -> str:
    sentences = split_sentences(text)
    for s in reversed(sentences):
        if "?" in s:
            return s
    return sentences[-1] if sentences else ""


def classify_ask(text: str, today: Optional[date] = None) -> Ask:
    """
    What Emma is asking in `text`. The last question in the line decides; the
    whole line is searched for the values she read back (a summary names the
    service, date, time and number in its first sentence, then asks "Shall I book it?").
    """
    today = today or clock.today()
    line = text or ""
    low = norm(line)
    q = _question_sentence(line)
    ql = norm(q)
    ask = Ask(kind="statement", question=q, phone=phone_in(line), dates=dates_in(line, today),
              times=times_in(line), services=services_in(line), branches=branches_in(line))
    has_q = "?" in line

    if not low:
        ask.kind = "statement"
        return ask
    if _CLOSING.search(low) and not has_q:
        ask.kind = "closing"
        return ask

    details = sum(bool(x) for x in (ask.dates, ask.times, ask.services))
    confirmish = bool(_CONFIRM_Q.search(ql)) or re.search(r"\b(book it|book that|go ahead|confirm)\b", ql)
    # A summary names what is being booked or asks for the go-ahead; a lone
    # offer ("I could do 10 AM on Monday, would that work?") is a choice.
    if has_q and details >= 2 and confirmish and (ask.services or _COMMIT_Q.search(ql)):
        ask.kind, ask.slot = "confirm", "summary"
        return ask
    if has_q and re.search(r"\bcancel\b", ql) and confirmish and (ask.dates or ask.times):
        ask.kind, ask.slot = "confirm", "summary"
        return ask

    if has_q and re.search(r"\b(could|can|would) you (please )?spell\b|\bhow do you spell\b|\bspell (it|that|your)\b", ql):
        ask.kind = "spelling"
        return ask
    # A spelled-out name read back: "Let me just check the spelling. P R I Y A. Is that right?"
    spelled = re.search(r"\b[A-Z](?:[ .-]+[A-Z]){2,}\b", line)
    if has_q and ("spelling" in low or spelled) and _CONFIRM_Q.search(ql):
        ask.kind, ask.slot = "confirm", "name"
        return ask

    # A phone read-back: "So that's 9 8 7 6 5, 4 3 2 1 0, right?"
    if has_q and ask.phone and (phone_in(q) or _CONFIRM_Q.search(ql)):
        ask.kind, ask.slot = "confirm", "phone"
        return ask
    # The number asked for again, a bit at a time: "Could you say it a few digits
    # at a time?", "Let's do it slowly, a few digits at a time. Go ahead." (not a
    # time question, and "Go ahead." alone reads as open).
    if re.search(r"\bdigits?\b", low) and not ask.phone:
        ask.kind = "phone"
        return ask

    # A choice between offers: "I could do 4:30 or 5:30, would either work?"
    q_times, q_branches = times_in(q), branches_in(q)
    q_services = services_in(q)
    if has_q and len(ask.times) >= 2 and not _COMMIT_Q.search(ql) \
            and re.search(r"\b(any good|which|one of those|either|instead|be better|do,? or)\b", ql) \
            and not re.search(r"\b(another|other|choose|different) time\b", ql):
        # "I could do 1:30 or 2:30 instead. Any good?" (the offer's times are in the line, the question is short)
        ask.kind, ask.subject, ask.options = "choice", "time", [t.strftime("%I:%M %p") for t in ask.times]
        return ask
    if has_q and re.search(r"\bor\b|\beither\b|\bwhich (one|suits|works|would you)\b", ql):
        line_times = ask.times
        if len(line_times) >= 2 or (len(q_times) >= 1 and re.search(r"\beither\b|\bor\b", ql) and len(line_times) >= 2):
            ask.kind, ask.subject, ask.options = "choice", "time", [t.strftime("%I:%M %p") for t in line_times]
            return ask
        if len(ask.branches) >= 2 and not re.search(r"\b(number|name)\b", ql):
            ask.kind, ask.subject, ask.options = "choice", "branch", list(ask.branches)
            return ask
        docs = doctors_in(line)
        if len(docs) >= 2:
            ask.kind, ask.subject, ask.options = "choice", "doctor", docs
            return ask
        if len(ask.dates) >= 2:
            ask.kind, ask.subject, ask.options = "choice", "date", [d.isoformat() for d in ask.dates]
            return ask
        if len(q_services) >= 2:
            ask.kind, ask.options = "service", q_services
            return ask
    if has_q and re.search(r"\bwould (either|one) of (those|these)|\bwould that work\b|\bdoes that work\b|\bhow about\b|\bhow does\b", ql) \
            and len(ask.times) == 1 and (not ask.dates or _OFFERISH.search(low)) and "is that right" not in ql:
        ask.kind, ask.subject, ask.options = "choice", "time", [ask.times[0].strftime("%I:%M %p")]
        return ask

    if has_q and _CONFIRM_Q.search(ql):
        ask.kind = "confirm"
        if q_branches or (ask.branches and re.search(r"\bbranch\b", ql)):
            ask.slot = "branch"
        elif q_times:
            ask.slot = "time"
        elif dates_in(q, today):
            ask.slot = "date"
        elif q_services:
            ask.slot = "service"
        elif _names_a_person(q):
            ask.slot = "name"
        elif ask.times:
            ask.slot = "time"
        elif ask.dates:
            ask.slot = "date"
        elif ask.services:
            ask.slot = "service"
        elif ask.branches:
            ask.slot = "branch"
        else:
            ask.slot = "generic"
        return ask

    service_q = dict(_SLOT_Q)["service"]
    if _ANYTHING_ELSE.search(ql) and not service_q.search(ql):      # "what's it for, a check-up or something else?"
        ask.kind = "anything_else"
        return ask

    if has_q or re.search(r"\b(may i|could i|can i) (have|get|take)\b|\bplease (tell|give)\b", ql):
        for kind, pattern in _SLOT_Q:
            if kind == "spelling":
                continue
            if pattern.search(ql):
                if kind == "name" and re.search(r"\b(number|mobile|phone)\b", ql):
                    continue
                ask.kind = kind
                if kind == "service" and len(q_services) >= 2:
                    ask.options = q_services
                return ask
        if _OPEN_Q.search(ql):
            ask.kind = "open"
            return ask
        if re.match(r"^(would you like|do you want|shall i|should i|can i|could i|is there|are you|do you|would it|is it|was it|did you)\b", ql):
            ask.kind = "yes_no"
            return ask
        ask.kind = "open" if has_q else "statement"
        return ask
    if _OPEN_Q.search(low):
        ask.kind = "open"
    return ask
