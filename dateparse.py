"""
What a caller said about *when* -> constraints the scheduler can search.

    parse_when("next Monday around 5")          date exact 2026-10-05, time exact 17:00
    parse_when("sometime next week, evenings")   date range Mon-Sat, time window 16:00-21:00
    parse_when("7")                               time ambiguous (07:00 or 19:00) -> ask
    parse_when("the 3rd")  (on 30 Sep)            date exact 2026-10-03 (next month, not next year)
    parse_when("05/10")                           5 October: numeric dates are always day-first

The result is a When with an optional DateConstraint, an optional
TimeConstraint and a list of Issues (typed problems such as PAST or
OUTSIDE_HOURS) for the dialogue to explain. Nothing here knows about doctors,
bookings or lunch; the scheduler applies those rules. Everything is relative to
clock.today(), i.e. the clinic's date, never the server's.

Rules (docs/IMPLEMENTATION_PLAN.md, section 5.2):
- Weekdays: "Monday", "next Monday", "coming Monday" = the next Monday after
  today. "This Monday" said on a Monday = today. "Monday after next",
  "next to next Monday" = one week later. "Next week Monday" = the Monday of
  next calendar week. The full date is always read back, so a caller can correct it.
- A bare day number ("the 3rd", "26th") = the next date with that day number,
  skipping months that lack it; an explicit month that lacks it is INVALID_DAY.
- Bare hours: 1-6 -> PM, 9-11 -> AM, 12 -> noon, 7 and 8 -> ambiguous (ask),
  unless "morning"/"evening" or am/pm settles it.
"""

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from typing import Optional

import clock
import config

# ---------------------------------------------------------------- result types


@dataclass(frozen=True)
class DateConstraint:
    start: date
    end: date                        # inclusive
    kind: str = "exact"              # exact | range | set | earliest
    only: tuple = ()                 # kind == "set": the allowed dates

    @property
    def exact(self) -> bool:
        return self.kind == "exact"

    def dates(self):
        if self.kind == "set":
            yield from self.only
            return
        day = self.start
        while day <= self.end:
            yield day
            day += timedelta(days=1)


@dataclass(frozen=True)
class TimeConstraint:
    kind: str                        # exact | window | ambiguous | any
    start: Optional[time] = None     # exact time, or window start
    end: Optional[time] = None       # window end (exclusive)
    candidates: tuple = ()           # kind == "ambiguous": e.g. (07:00, 19:00)
    label: str = ""                  # the words that produced it

    @property
    def on_grid(self) -> bool:
        return self.kind == "exact" and self.start.minute % config.SLOT_GRID_MIN == 0


@dataclass(frozen=True)
class Issue:
    code: str                        # PAST | SUNDAY | BEYOND_HORIZON | INVALID_DAY | OUTSIDE_HOURS
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class When:
    date: Optional[DateConstraint] = None
    time: Optional[TimeConstraint] = None
    issues: tuple = ()

    @property
    def empty(self) -> bool:
        return self.date is None and self.time is None and not self.issues

    def issue(self, code: str) -> Optional[Issue]:
        return next((i for i in self.issues if i.code == code), None)


# ---------------------------------------------------------------- vocabulary

WEEKDAYS = {
    "monday": 0, "tuesday": 1, "tues": 1, "wednesday": 2, "weds": 2, "thursday": 3,
    "thurs": 3, "friday": 4, "saturday": 5, "sunday": 6,
}
MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4,
    "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9, "october": 10, "oct": 10, "november": 11,
    "nov": 11, "december": 12, "dec": 12,
}
_WD = "(?:" + "|".join(sorted(WEEKDAYS, key=len, reverse=True)) + ")"
_MON = "(?:" + "|".join(sorted(MONTHS, key=len, reverse=True)) + ")"

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}
_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
    "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13,
    "fourteenth": 14, "fifteenth": 15, "sixteenth": 16, "seventeenth": 17,
    "eighteenth": 18, "nineteenth": 19, "twentieth": 20, "thirtieth": 30,
}

# Everyday phrases whose number words are not numbers.
_IDIOMS = [
    (r"\b(?:no|any|some|every)\s*one\b", " "),
    (r"\b(?:which|that|this|the|a)\s+one\b", " "),
    (r"\bone\s+of\b", " "),
    (r"\b(?:first|second|third|last)\s+(?:one|option|slot|choice)\b", " "),
    (r"\b(?:one|a|just\s+a)\s+(?:second|sec|minute|moment)\b", " "),
    (r"\bfirst\s+thing(?:\s+in\s+the\s+morning)?\b", " early morning "),
]

# Time-of-day windows (clinic-local, end exclusive). Longer phrases first.
_WINDOWS = [
    ("early morning", time(7, 0), time(9, 0)),
    ("late morning", time(10, 0), time(12, 0)),
    ("early afternoon", time(12, 0), time(14, 0)),
    ("late afternoon", time(15, 0), time(17, 0)),
    ("early evening", time(16, 0), time(18, 0)),
    ("late evening", time(19, 0), time(21, 0)),
    ("after lunch", time(14, 30), time(17, 0)),
    ("before lunch", time(7, 0), time(14, 0)),
    ("lunch time", time(12, 30), time(14, 0)),
    ("lunchtime", time(12, 30), time(14, 0)),
    ("after work", time(18, 0), time(21, 0)),
    ("after office", time(18, 0), time(21, 0)),
    ("after school", time(16, 0), time(21, 0)),
    ("tonight", time(18, 0), time(21, 0)),
    ("morning", time(7, 0), time(12, 0)),
    ("afternoon", time(12, 0), time(16, 0)),
    ("evening", time(16, 0), time(21, 0)),
    ("night", time(18, 0), time(21, 0)),
]

_ANY_TIME = re.compile(r"\b(?:any\s*time|whenever|flexible|any\s+slot|doesn'?t\s+matter|don'?t\s+mind)\b")
_EARLIEST = re.compile(
    r"\b(?:earliest|as\s+soon\s+as\s+possible|asap|soonest|first\s+available|next\s+available|"
    r"as\s+early\s+as\s+possible|any\s*day|whenever)\b"
)

# ---------------------------------------------------------------- normalisation


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _words_to_digits(text: str) -> str:
    """'twenty-fifth of october at four thirty' -> '25th of october at 4 30'."""
    words = re.split(r"(\s+)", text)
    out, i = [], 0
    while i < len(words):
        w = words[i]
        nxt = words[i + 2] if i + 2 < len(words) else ""
        if w in _TENS:
            if nxt in _UNITS and 1 <= _UNITS[nxt] <= 9:
                out.append(str(_TENS[w] + _UNITS[nxt]))
                i += 3
                continue
            if nxt in _ORDINALS and _ORDINALS[nxt] <= 9:
                out.append(_ordinal(_TENS[w] + _ORDINALS[nxt]))
                i += 3
                continue
            out.append(str(_TENS[w]))
        elif w in _UNITS:
            out.append(str(_UNITS[w]))
        elif w in _ORDINALS:
            out.append(_ordinal(_ORDINALS[w]))
        else:
            out.append(w)
        i += 1
    return "".join(out)


def normalize(text: str) -> str:
    t = (text or "").lower().replace("’", "'")
    # Only the dotted forms: collapsing "a m" would also turn "I am" into a meridiem.
    t = re.sub(r"\ba\.m\b\.?", "am", t)
    t = re.sub(r"\bp\.m\b\.?", "pm", t)
    # Punctuation first, so "five." and "sixth," are whole words for the number step.
    t = re.sub(r"[,;!?\"]", " ", t)
    t = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", t)          # keep only time dots (5.30)
    for pattern, repl in _IDIOMS:
        t = re.sub(pattern, repl, t)
    # Keep digit dashes ("05-10") but split word dashes ("twenty-five").
    t = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", t)
    t = _words_to_digits(t)
    t = re.sub(r"(\d)(am|pm)\b", r"\1 \2", t)
    t = re.sub(r"\bo\s*'?\s*clock\b", " oclock", t)
    return re.sub(r"\s+", " ", t).strip()


# ---------------------------------------------------------------- dates


def _next_weekday(today: date, wd: int, include_today: bool = False) -> date:
    ahead = (wd - today.weekday()) % 7
    if ahead == 0 and not include_today:
        ahead = 7
    return today + timedelta(days=ahead)


def _next_day_number(today: date, day: int) -> Optional[date]:
    """The next date on or after today whose day-of-month is `day`, skipping short months."""
    year, month = today.year, today.month
    for _ in range(14):
        if day <= calendar.monthrange(year, month)[1]:
            candidate = date(year, month, day)
            if candidate >= today:
                return candidate
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return None


def _month_day(today: date, month: int, day: int, year: Optional[int]):
    """(date, None) or (None, Issue). Without a year: this year if still ahead, else next year."""
    y = year if year is not None else today.year
    if day < 1 or day > calendar.monthrange(y, month)[1]:
        return None, Issue("INVALID_DAY", {"month": month, "day": day})
    d = date(y, month, day)
    if year is None and d < today:
        if day > calendar.monthrange(y + 1, month)[1]:
            return None, Issue("INVALID_DAY", {"month": month, "day": day})
        d = date(y + 1, month, day)
    return d, None


def _year(raw: Optional[str]) -> Optional[int]:
    if not raw:
        return None
    y = int(raw)
    return 2000 + y if y < 100 else y


def _exact_date(d):
    return DateConstraint(d, d) if d else None


def _find_date(t: str, today: date, expecting: Optional[str]):
    """
    Returns (DateConstraint | None, Issue | None, matched_text). Patterns are
    tried from most to least specific; the first match wins. matched_text is
    removed before looking for a time, so "the 5th at 5" is not read twice.
    """
    horizon_end = today + timedelta(days=config.BOOKING_HORIZON_DAYS)

    # 1. numeric day/month[/year], always day-first ("05/10" = 5 October).
    #    Not "5-6 pm", which is a time range.
    m = re.search(r"(?<![\d:.])(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b(?!\s*(?:am|pm|oclock))", t)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), _year(m.group(3))
        if not 1 <= month <= 12:
            return None, Issue("INVALID_DAY", {"month": month, "day": day}), m.group(0)
        d, issue = _month_day(today, month, day, year)
        return _exact_date(d), issue, m.group(0)

    # 2. "5th of October [2026]", "5 oct". A bare "5 may" needs a suffix, "of" or
    #    a year, so "at 4 may work" is not read as the 4th of May.
    for m in re.finditer(rf"\b(\d{{1,2}})(st|nd|rd|th)?\s+(of\s+)?({_MON})\b(?:\s+(\d{{4}}))?", t):
        if m.group(4) == "may" and not (m.group(2) or m.group(3) or m.group(5)):
            continue
        day, month, year = int(m.group(1)), MONTHS[m.group(4)], _year(m.group(5))
        d, issue = _month_day(today, month, day, year)
        return _exact_date(d), issue, m.group(0)

    # 3. "October 5th [2026]", "oct the 5th"
    for m in re.finditer(rf"\b({_MON})\s+(the\s+)?(\d{{1,2}})(st|nd|rd|th)?\b"
                         rf"(?!\s*(?:am|pm|oclock|:|\.\d))(?:\s+(\d{{4}}))?", t):
        if m.group(1) == "may" and not (m.group(2) or m.group(4) or m.group(5)):
            continue
        month, day, year = MONTHS[m.group(1)], int(m.group(3)), _year(m.group(5))
        d, issue = _month_day(today, month, day, year)
        return _exact_date(d), issue, m.group(0)

    # 4. "the 2nd week of October", "last week of next month"
    m = re.search(rf"\b(1st|2nd|3rd|4th|last)\s+week\s+of\s+(next\s+month|this\s+month|{_MON})\b", t)
    if m:
        target = m.group(2)
        if target.startswith("next"):
            y, mo = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        elif target.startswith("this"):
            y, mo = today.year, today.month
        else:
            mo = MONTHS[target]
            y = today.year if mo >= today.month else today.year + 1
        last_day = calendar.monthrange(y, mo)[1]
        if m.group(1) == "last":
            first = date(y, mo, last_day - 6)
        else:
            first = date(y, mo, 1 + 7 * (int(m.group(1)[0]) - 1))
        return DateConstraint(first, min(first + timedelta(days=6), date(y, mo, last_day)), "range"), None, m.group(0)

    # 5. relative days. "this evening" / "tonight" keep their window word for the time.
    m = re.search(r"\byesterday\b", t)
    if m:
        return _exact_date(today - timedelta(days=1)), None, m.group(0)
    m = re.search(r"\bday\s+after\s+(?:tomorrow|tmrw)\b", t)
    if m:
        return _exact_date(today + timedelta(days=2)), None, m.group(0)
    m = re.search(r"\b(?:tomorrow|tmrw|tomorow|tommorow|tommorrow)\b", t)
    if m:
        return _exact_date(today + timedelta(days=1)), None, m.group(0)
    m = re.search(r"\b(?:today|right\s+now|immediately|same\s+day)\b", t)
    if m:
        return _exact_date(today), None, m.group(0)
    m = re.search(r"\b(this)\s+(?:morning|afternoon|evening)\b|\btonight\b", t)
    if m:
        return _exact_date(today), None, m.group(1) or ""

    m = re.search(r"\b(?:in|after)\s+(\d{1,2})\s+(days?|weeks?)\b|\b(\d{1,2})\s+(days?|weeks?)\s+from\s+(?:now|today)\b", t)
    if m:
        n = int(m.group(1) or m.group(3))
        unit = m.group(2) or m.group(4)
        return _exact_date(today + timedelta(days=n * (7 if unit.startswith("week") else 1))), None, m.group(0)
    m = re.search(r"\bin\s+a\s+week\b|\ba\s+week\s+from\s+(?:now|today)\b", t)
    if m:
        return _exact_date(today + timedelta(days=7)), None, m.group(0)
    m = re.search(r"\bin\s+a\s+(couple\s+of|few)\s+days\b", t)
    if m:
        last = 3 if m.group(1).startswith("couple") else 4
        return DateConstraint(today + timedelta(days=2), today + timedelta(days=last), "range"), None, m.group(0)

    # 6. weekdays
    m = re.search(rf"\bnext\s+to\s+next\s+({_WD})\b|\b({_WD})\s+after\s+next\b", t)
    if m:
        d = _next_weekday(today, WEEKDAYS[m.group(1) or m.group(2)]) + timedelta(days=7)
        return _exact_date(d), None, m.group(0)
    m = re.search(rf"\bnext\s+week\s+({_WD})\b|\b({_WD})\s+(?:of\s+)?next\s+week\b", t)
    if m:
        wd = WEEKDAYS[m.group(1) or m.group(2)]
        d = today - timedelta(days=today.weekday()) + timedelta(days=7 + wd)
        return _exact_date(d), None, m.group(0)
    found = list(re.finditer(rf"\b(this|next|coming|upcoming|on)?\s*({_WD})\b", t))
    if found:
        dates = sorted({_next_weekday(today, WEEKDAYS[mm.group(2)], include_today=mm.group(1) == "this")
                        for mm in found})
        span = t[found[0].start():found[-1].end()]
        if len(dates) == 1:
            return _exact_date(dates[0]), None, span
        return DateConstraint(dates[0], dates[-1], "set", tuple(dates)), None, span

    # 7. weeks, weekends, months
    monday = today - timedelta(days=today.weekday())
    m = re.search(r"\b(?:week\s+after\s+next|the\s+week\s+after)\b", t)
    if m:
        return DateConstraint(monday + timedelta(days=14), monday + timedelta(days=19), "range"), None, m.group(0)
    m = re.search(r"\bnext\s+weekend\b", t)
    if m:
        return _exact_date(monday + timedelta(days=12)), None, m.group(0)
    m = re.search(r"\bnext\s+week\b", t)
    if m:
        return DateConstraint(monday + timedelta(days=7), monday + timedelta(days=12), "range"), None, m.group(0)
    m = re.search(r"\b(?:this\s+)?weekend\b", t)
    if m:
        return _exact_date(_next_weekday(today, 5, include_today=True)), None, m.group(0)
    m = re.search(r"\b(?:end\s+of\s+(?:the|this)\s+week|later\s+this\s+week|this\s+week)\b", t)
    if m:
        if today.weekday() == 6:                     # on Sunday, "this week" is the coming one
            start, end = today + timedelta(days=1), today + timedelta(days=6)
        else:
            end = monday + timedelta(days=5)
            if m.group(0).startswith("end"):
                start = max(today, monday + timedelta(days=4))
            elif m.group(0).startswith("later"):
                start = today + timedelta(days=1)
            else:
                start = today
        return DateConstraint(start, max(start, end), "range"), None, m.group(0)
    m = re.search(r"\bend\s+of\s+(?:the|this)\s+month\b", t)
    if m:
        last = date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
        return DateConstraint(max(today, last - timedelta(days=6)), last, "range"), None, m.group(0)
    m = re.search(r"\b(?:beginning|start|early)\s+(?:of\s+)?next\s+month\b|\bnext\s+month\b", t)
    if m:
        y, mo = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        first = date(y, mo, 1)
        last = date(y, mo, calendar.monthrange(y, mo)[1]) if m.group(0).startswith("next") else first + timedelta(days=6)
        return DateConstraint(first, last, "range"), None, m.group(0)

    # 8. "earliest", "any day", "as soon as possible"
    m = _EARLIEST.search(t)
    if m:
        return DateConstraint(today, horizon_end, "earliest"), None, ""

    # 9. a day number with an ordinal suffix ("the 26th"), or a bare number when
    #    the question was about the date ("Which date?" -> "26")
    m = re.search(r"(?<![\d:.])(\d{1,2})(?:st|nd|rd|th)\b", t)
    if not m and expecting == "date":
        m = re.fullmatch(r"(?:on\s+)?(?:the\s+)?(\d{1,2})", t.strip())
    if m:
        day = int(m.group(1))
        if not 1 <= day <= 31:
            return None, Issue("INVALID_DAY", {"day": day}), m.group(0)
        return _exact_date(_next_day_number(today, day)), None, m.group(0)

    return None, None, ""


def _check_date(dc: DateConstraint, today: date):
    """Apply the past / horizon / Sunday rules. Returns (DateConstraint | None, [Issue])."""
    horizon_end = today + timedelta(days=config.BOOKING_HORIZON_DAYS)
    closed = config.CLOSED_DAYS
    if dc.exact:
        d = dc.start
        if d < today:
            return None, [Issue("PAST", {"date": d})]
        if d > horizon_end:
            return None, [Issue("BEYOND_HORIZON", {"date": d, "last_date": horizon_end})]
        if d.weekday() in closed:
            return None, [Issue("SUNDAY", {"date": d})]
        return dc, []
    if dc.kind == "set":
        issues = [Issue("SUNDAY", {"date": d}) for d in dc.only if d.weekday() in closed]
        keep = tuple(d for d in dc.only if today <= d <= horizon_end and d.weekday() not in closed)
        if not keep:
            if any(d > horizon_end for d in dc.only):
                issues.append(Issue("BEYOND_HORIZON", {"last_date": horizon_end}))
            return None, issues
        if len(keep) == 1:
            return DateConstraint(keep[0], keep[0]), issues
        return DateConstraint(keep[0], keep[-1], "set", keep), issues
    if dc.end < today:
        return None, [Issue("PAST", {"date": dc.end})]
    if dc.start > horizon_end:
        return None, [Issue("BEYOND_HORIZON", {"date": dc.start, "last_date": horizon_end})]
    start, end = max(dc.start, today), min(dc.end, horizon_end)
    if start == end and dc.kind != "earliest":
        return _check_date(DateConstraint(start, start), today)
    return DateConstraint(start, end, dc.kind), []


# ---------------------------------------------------------------- times

_OPEN = time(config.CLINIC_START_HOUR, 0)
_CLOSE = time(config.CLINIC_END_HOUR, 0)

# A spoken clock time: "5", "5:30", "5.30", "4 30". Groups: hour, minute (colon), minute (space).
_HM = r"(\d{1,2})(?:[:.](\d{2})|\s+([0-5]\d)(?!\d))?"
# The same with an optional meridiem as a fourth group.
_CLOCK = _HM + r"(?:\s*(am|pm)\b)?"


def _clock(m: re.Match, first: int = 1):
    """(hour, minute, meridiem or None) from four consecutive groups starting at `first`."""
    h, mi_colon, mi_space, mer = (m.group(first + k) for k in range(4))
    return int(h), int(mi_colon or mi_space or 0), mer


def _candidates(h: int, mi: int, meridiem: Optional[str]):
    """Plausible clock times for a spoken hour, applying the bare-hour rule; [] if impossible."""
    if h > 23 or mi > 59:
        return []
    if meridiem and 1 <= h <= 12:
        return [time(0 if h == 12 else h, mi)] if meridiem == "am" else [time(12 if h == 12 else h + 12, mi)]
    if h == 0 or h >= 13:
        return [time(h, mi)]
    if 1 <= h <= 6:
        return [time(h + 12, mi)]
    if h in (7, 8):
        return [time(h, mi), time(h + 12, mi)]
    return [time(h, mi)]                             # 9-11 AM, 12 noon


def _both(h: int, mi: int, meridiem: Optional[str]):
    """Both AM and PM readings when no meridiem was said (used for ranges)."""
    if meridiem or h == 0 or h >= 13 or h > 23 or mi > 59:
        return _candidates(h, mi, meridiem)
    if h == 12:
        return [time(12, mi)]
    return [time(h, mi), time(h + 12, mi)]


def _in_hours(t: time) -> bool:
    return _OPEN <= t < _CLOSE


def _mins(t: time) -> int:
    return t.hour * 60 + t.minute


def _exact(h: int, mi: int, meridiem: Optional[str], label: str):
    """(TimeConstraint | None, Issue | None) for one spoken clock time."""
    cands = _candidates(h, mi, meridiem)
    if not cands:
        return None, None
    ok = [c for c in cands if _in_hours(c)]
    if not ok:
        return None, Issue("OUTSIDE_HOURS", {"time": cands[0]})
    if len(ok) == 1:
        return TimeConstraint("exact", ok[0], label=label), None
    return TimeConstraint("ambiguous", candidates=tuple(ok), label=label), None


def _window(start: time, end: time, label: str):
    start, end = max(start, _OPEN), min(end, _CLOSE)
    if start < end:
        return TimeConstraint("window", start, end, label=label), None
    return None, Issue("OUTSIDE_HOURS", {"time": start})


def _range(a, b, hint: Optional[str], label: str):
    """
    "between 4 and 6" -> 16:00-18:00. Every AM/PM reading of both ends is tried;
    the shortest range that overlaps clinic hours wins, then the one matching a
    morning/evening hint, then the earlier one.
    """
    a_mer = a[2] or (b[2] if b[2] and a[0] <= b[0] else None)
    pairs = []
    for s in _both(a[0], a[1], a_mer):
        for e in _both(b[0], b[1], b[2]):
            if s < e and max(s, _OPEN) < min(e, _CLOSE):
                hint_miss = hint is not None and (hint == "pm") != (s.hour >= 12)
                pairs.append((_mins(e) - _mins(s), hint_miss, _mins(s), s, e))
    if not pairs:
        return None, Issue("OUTSIDE_HOURS", {"time": _OPEN})
    *_, s, e = min(pairs)
    return _window(s, e, label)


def _meridiem_hint(t: str) -> Optional[str]:
    if re.search(r"\b(?:afternoon|evening|night|tonight|after\s+work|after\s+office|after\s+school)\b|\d\s*pm\b", t):
        return "pm"
    if re.search(r"\bmorning\b|\d\s*am\b", t):
        return "am"
    return None


def _find_time(t: str, expecting: Optional[str]):
    """Returns (TimeConstraint | None, Issue | None)."""
    hint = _meridiem_hint(t)

    # Ranges: "between 4 and 6", "from 10 to 12", "5-6 pm", "5 to 6 pm"
    m = re.search(rf"\b(?:between|from)\s+{_CLOCK}\s+(?:and|to|till|until|-)\s+{_CLOCK}", t)
    if not m:
        m = re.search(rf"(?<![\d/])\b{_CLOCK}\s*(?:-|to)\s*{_CLOCK}", t)
        if m and not (m.group(4) or m.group(8)):
            m = None                                 # "10 to 5" without am/pm may mean 4:50
    if m:
        return _range(_clock(m, 1), _clock(m, 5), hint, m.group(0))

    # One-sided: "after 5", "before 11 am", "by 4", "not before 10"
    m = re.search(rf"\b(not\s+before|not\s+after|after|before|by|till|until)\s+{_CLOCK}", t)
    if m:
        h, mi, mer = _clock(m, 2)
        cands = _candidates(h, mi, mer or hint)
        if cands:
            if m.group(1) in ("after", "not before"):
                # "after 7" at a clinic that opens at 7 AM means the evening.
                return _window(cands[-1], _CLOSE, m.group(0))
            for c in cands:                           # "by 8": the morning if that leaves any time
                if _mins(c) - _mins(_OPEN) >= config.SLOT_GRID_MIN:
                    return _window(_OPEN, c, m.group(0))
            return None, Issue("OUTSIDE_HOURS", {"time": cands[0]})

    # "half past 4", "quarter past 4", "quarter to 5"
    m = re.search(r"\b(half\s+past|half\s+passed|quarter\s+past|quarter\s+to)\s+(\d{1,2})\b(?:\s*(am|pm)\b)?", t)
    if m:
        h = int(m.group(2))
        if m.group(1).startswith("half"):
            mi = 30
        elif m.group(1).startswith("quarter past"):
            mi = 15
        else:
            h, mi = (h - 1 if h > 1 else 12), 45
        return _exact(h, mi, m.group(3) or hint, m.group(0))

    if re.search(r"\b(?:noon|midday)\b", t):
        return TimeConstraint("exact", time(12, 0), label="noon"), None
    if re.search(r"\bmidnight\b", t):
        return None, Issue("OUTSIDE_HOURS", {"time": time(0, 0)})

    # An explicit clock time, most specific first.
    not_date = r"(?!\s*(?:st|nd|rd|th|days?|weeks?|/|-))"
    patterns = [
        rf"(?<![\d/-])\b{_HM}\s*(am|pm)\b",                                     # 5 pm, 5:30 pm, 4 30 pm
        r"(?<![\d/-])\b(\d{1,2})[:.](\d{2})()()\b",                             # 5:30
        r"\b(\d{1,2})()()()\s+oclock\b",                                        # 5 o'clock
        rf"(?<![\d/-])\b{_HM}()\s+(?:in\s+the\s+)?(?:morning|afternoon|evening|night)\b",  # 5 in the evening
        rf"\b(?:morning|afternoon|evening|night)\s+(?:at\s+)?{_HM}()\b{not_date}",       # evening 5
        rf"\b(?:at|around|about|near|approx|approximately|say|like)\s+{_HM}()\b{not_date}",  # at 5
    ]
    for pattern in patterns:
        m = re.search(pattern, t)
        if m:
            h, mi, mer = _clock(m)
            return _exact(h, mi, mer or hint, m.group(0))

    # A bare number when the question was about the time ("What time?" -> "5", "4 30")
    if expecting == "time":
        m = re.fullmatch(_HM, t.strip())
        if m:
            h, mi, _ = _clock_hm(m)
            return _exact(h, mi, hint, m.group(0))

    for phrase, start, end in _WINDOWS:
        if re.search(rf"\b{phrase}s?\b", t):         # "evenings" too
            return TimeConstraint("window", start, end, label=phrase), None

    if _ANY_TIME.search(t) or _EARLIEST.search(t):
        return TimeConstraint("any", label="any time"), None
    return None, None


def _clock_hm(m: re.Match):
    return int(m.group(1)), int(m.group(2) or m.group(3) or 0), None


# ---------------------------------------------------------------- public API


def parse_when(text: str, *, today: Optional[date] = None, expecting: Optional[str] = None) -> When:
    """
    Find a date and/or a time in `text`.

    `expecting` is what Emma just asked about ("date" or "time"). It lets a bare
    number count ("26" as the 26th, "5" as 5 PM), which is too risky otherwise.
    """
    today = today or clock.today()
    t = normalize(text)
    if not t:
        return When()
    dc, date_issue, span = _find_date(t, today, expecting)
    issues = [date_issue] if date_issue else []
    if dc is not None:
        dc, more = _check_date(dc, today)
        issues.extend(more)
    rest = t.replace(span, " ", 1) if span else t
    tc, time_issue = _find_time(re.sub(r"\s+", " ", rest).strip(), expecting)
    if time_issue:
        issues.append(time_issue)
    return When(dc, tc, tuple(issues))
