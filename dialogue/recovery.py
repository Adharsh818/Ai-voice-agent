"""
Emma's side of a doctor-unavailability recovery call (plan 5.11, Day 5).

Emma rings the patient because their doctor can't make it. The call follows a
receptionist's script, with every decision made in Python:

    identity    "Hi, is this Priya? It's Emma from Pearl Dental."
                Nothing about the appointment is said until they confirm.
                Wrong person: ask them to have Priya call the clinic, no details.
                Suspicious ("is this a scam?"): invite them to call the clinic
                directly, then end.
    preference  what changed (never why: the block's reason category is not
                spoken), then the patient's preference first (decision 7):
                the same doctor another day, another doctor at the same
                branch, another branch, or whatever is earliest
    offer       the nearest valid slot, then up to two alternatives; a day or
                time they name is searched instead
    recap       one summary; only a clear yes to a recap they heard in full
                moves the appointment (scheduling.reschedule, atomic and
                idempotent per job and appointment)
    next        several affected appointments are handled one by one

At any point they may keep it on hold for the front desk (pending), cancel,
ask for a person, ask not to be called again, or ask if Emma is a bot (the
honesty line, then carry on). Whatever is left unresolved at hang-up is
flagged NEEDS RESCHEDULE with a staff task (outbound.finish_job), so nothing
falls through. When there is no valid slot Emma never improvises.

Understanding is Tier-0 only (dialogue.match, dateparse): outbound answers are
short and predictable, so the call never waits on the model.
"""

import logging
import random
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time as time_of_day, timedelta
from types import SimpleNamespace
from typing import Callable, Optional

import clock
import config
import dateparse
import prompts
import scheduling
from dialogue import match

logger = logging.getLogger(__name__)

SEARCH_DAYS = 21          # how far ahead the preference searches look
OFFER_COUNT = 3           # the nearest slot, then up to two alternatives


# ---------------------------------------------------------------- state


@dataclass
class Appt:
    id: str
    version: int
    service: str
    service_id: int
    doctor_id: int
    doctor: str
    branch_id: int
    branch: str
    start: datetime
    patient_name: str
    caller_name: str

    @classmethod
    def from_row(cls, row: dict) -> "Appt":
        import db
        return cls(id=row["id"], version=row["version"], service=row["service"], service_id=row["service_id"],
                   doctor_id=row["doctor_id"], doctor=row["doctor"], branch_id=row["branch_id"],
                   branch=row["branch"], start=db.local(row["start_utc"]),
                   patient_name=row.get("patient_name") or "", caller_name=row.get("caller_name") or "")


@dataclass
class RecoveryContext:
    """Per-call state; call_session treats it like any engine state."""
    call_id: str
    job_id: int
    phone_e164: str
    appts: list                                   # Appt, soonest first
    block_start: Optional[datetime] = None
    block_end: Optional[datetime] = None
    block_id: Optional[int] = None
    state: str = "greeting"                       # see the module docstring
    idx: int = 0                                  # the appointment being handled
    preference: dict = field(default_factory=dict)  # kind, branch_id, branch
    when: Optional[dateparse.When] = None         # a day / time the patient asked for
    offers: list = field(default_factory=list)    # scheduling.Slot
    offered: int = 0                              # how many of `offers` have been said
    chosen: Optional[scheduling.Slot] = None
    tries: dict = field(default_factory=dict)     # unclear answers per state
    results: dict = field(default_factory=dict)   # appointment id -> rescheduled | cancelled | pending
    outcome: Optional[str] = None                 # the job's outcome once the call ends
    note: Optional[str] = None
    retry_at: Optional[datetime] = None           # "call me after 6": when the patient asked to be called back
    last_line: str = ""
    used: set = field(default_factory=set)
    branches: Optional[list] = None               # SimpleNamespace(id, name, area), loaded on first use
    history: list = field(default_factory=list)
    greeted: bool = False
    closed_conversation: bool = False
    last_reply_heard: bool = True
    keep_transcript: bool = True
    is_recovery: bool = True

    @property
    def goal(self) -> str:                         # call_session's "abandoned@<where>"
        return f"recovery_{self.state}"

    @property
    def phone(self) -> str:
        return self.phone_e164

    @property
    def current(self) -> Optional[Appt]:
        return self.appts[self.idx] if self.idx < len(self.appts) else None

    @property
    def contact_first(self) -> str:
        first = self.appts[0] if self.appts else None
        name = (first.caller_name or first.patient_name) if first else ""
        return (name or "").split(" ")[0]

    def unresolved(self) -> list:
        return [a.id for a in self.appts if self.results.get(a.id) not in ("rescheduled", "cancelled")]


def new_context(*, call_id: str, job_id: int, phone_e164: str, appointments: list,
                block: Optional[dict] = None) -> RecoveryContext:
    import db
    ctx = RecoveryContext(call_id=call_id, job_id=job_id, phone_e164=phone_e164,
                          appts=[Appt.from_row(a) for a in appointments])
    if block:
        ctx.block_id = block.get("id")
        ctx.block_start = db.local(block["start_utc"])
        ctx.block_end = db.local(block["end_utc"])
    return ctx


@dataclass
class Reply:
    text: str
    action: Optional[str] = None                   # rescheduled | cancelled
    expect: str = "open"


# ---------------------------------------------------------------- understanding

_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    text = (text or "").lower().replace("’", "'")
    return _WS.sub(" ", re.sub(r"[^a-z0-9' :]", " ", text)).strip()


def _has(t: str, *patterns: str) -> bool:
    return any(re.search(p, t) for p in patterns)


_DNC = (r"\b(don't|do not|dont|never|stop) (call|calling|ring|ringing|phone)\w*\b.*\b(me|again|number)\b",
        r"\bstop calling\b", r"\bremove (my|this) number\b", r"\btake me off\b", r"\bnot call me again\b")
_SCAM = (r"\bscam\w*\b", r"\bfraud\w*\b", r"\bhow (do|can|would) i know\b", r"\bhow did you get (my|this) number\b",
         r"\bis this (real|legit|genuine)\b", r"\bspam\b", r"\bprove\b")
_STAFF = (r"\b(talk|speak) (to|with) (a |an |the |some ?one|somebody|someone|person|human|staff|reception|"
          r"front desk|manager|doctor|real)", r"\breal person\b", r"\b(the )?front desk\b(?! will)",
          r"\breception(ist)?\b", r"\bhuman\b", r"\bsomeone from (the )?(clinic|front desk|reception)\b")
_BUSY = (r"\b(i'm|i am|im) (busy|driving|in a meeting|at work|outside)\b", r"\bcall (me )?(back )?later\b",
         r"\bcan('t| not)? talk (right )?now\b", r"\bnot a good time\b", r"\bcall back\b",
         r"\b(call|ring) (me )?(back )?(after|in|at|tomorrow|tonight|this evening|this afternoon)\b")
_WHO = (r"\bwho('s| is) (this|calling|speaking|it)\b", r"\bwho are you\b", r"\bsorry,? who\b", r"^who\b",
        r"\bwhere (are you )?calling from\b", r"\bwhich (clinic|company|hospital)\b")
_REPEAT = (r"^(sorry|pardon|what|huh|come again)\??$", r"\b(say|repeat) (that|it) again\b", r"\bcome again\b",
           r"\bdidn't (catch|hear|get) (that|you|it)\b", r"\bcan you repeat\b", r"\bone more time\b")
_WAIT = (r"^(hold on|wait|one (sec|second|minute|moment)|just a (sec|second|minute|moment))\b",
         r"\b(hold on|give me a (sec|second|minute|moment))\b")
_BYE = (r"^(ok(ay)? )?(bye|goodbye|bye bye|thanks bye|thank you bye)\b",)
_WHY = (r"\bwhy\b", r"\bwhat happened\b", r"\bis (he|she|the doctor) (ok|okay|alright|sick|ill)\b",
        r"\bwhat's wrong\b")
_CANCEL = (r"\bcancel\b", r"\b(don't|do not|dont) need (it|the appointment|that) (any ?more)?",
           r"\bforget (about )?it\b", r"\bcall it off\b")
_PENDING = (r"\b(let me|i'll|i will|i need to|have to) (think|check|get back|call (you )?back|see)\b",
            r"\b(keep|leave|put) it (on hold|pending|for now|as it is)\b", r"\bon hold\b", r"\bnot (right )?now\b",
            r"\bi'll (decide|let you know|call)\b", r"\blater\b")
_SAME_DOCTOR = (r"\bsame doctor\b", r"\b(wait|stick|stay) (for|with) (him|her|them|the doctor|dr)\b",
                r"\b(only|just) (him|her|dr \w+)\b", r"\bkeep (the same doctor|dr \w+|him|her)\b",
                r"\banother day\b", r"\bdifferent day\b", r"\bother day\b", r"\b(him|her) (please|only)\b")
_OTHER_DOCTOR = (r"\b(another|different|other|new) (doctor|dentist|dr)\b", r"\bsomeone else\b",
                 r"\banyone else\b", r"\bsame (time|day)\b")
_ANY_DOCTOR = (r"\bany (doctor|dentist|dr)\b", r"\banyone\b", r"\bdoesn't matter who\b",
               r"\b(don't|do not) mind (who|which doctor)\b", r"\bwhichever doctor\b")
_OTHER_BRANCH = (r"\b(another|different|other) (branch|location|clinic|centre|center|place)\b",
                 r"\bcloser (branch|clinic|one)\b")
_EARLIEST = (r"\b(earliest|soonest|first available|as soon as|asap|whichever is (sooner|earlier|first))\b",
             r"\bwhatever('s| is) (earliest|first|available|free)\b", r"\b(either|anything|any) (is|works|one)\b",
             r"\b(don't|do not) mind\b", r"\bwhatever (you have|works)\b", r"\bany time\b")
_NEGATE_START = re.compile(r"^(no|nope|nah|not really)\b")


def _robot_question(raw: str) -> bool:
    try:
        import tier0
        t = _norm(raw)
        return bool(tier0._ROBOT_RE.search(t)) and not tier0._HUMAN_RE.search(t) and len(t.split()) <= 12  # noqa: SLF001
    except Exception:                       # pragma: no cover - tier0 always imports in the app
        return False


def _yes_no(raw: str) -> Optional[str]:
    t = _norm(raw)
    if re.search(r"\b(speaking|that's me|this is (she|he|her|him|me)|it's me|yes it is|yeah it is|"
                 r"go ahead|sounds good|perfect|that works|works for me|that's fine|fine|done|book it|"
                 r"please do|do that|let's do (it|that))\b", t):
        if not _NEGATE_START.match(t):
            return "yes"
    return match.parse_yes_no(raw)


def _when(raw: str) -> Optional[dateparse.When]:
    try:
        w = dateparse.parse_when(raw)
    except Exception as exc:                  # a parser bug must not end the call
        logger.warning("recovery: date parse failed on %r: %s", raw, exc)
        return None
    if w.empty or (w.date is None and (w.time is None or w.time.kind == "any")):
        return None
    if w.time is not None and w.time.kind == "ambiguous":
        # Clinic hours make "at 5" an evening; take the reading inside 9-to-9.
        cand = [c for c in w.time.candidates if 9 <= c.hour < 21] or list(w.time.candidates)
        if cand:
            w = dateparse.When(w.date, dateparse.TimeConstraint("exact", cand[0], label=w.time.label), w.issues)
    return w


def _branch_rows(conn) -> list:
    return [SimpleNamespace(id=r["id"], name=r["name"], area=r["area"] or "")
            for r in conn.execute("SELECT id, name, area FROM branches ORDER BY id").fetchall()]


def _branch(ctx: "RecoveryContext", raw: str, run, exclude: Optional[int] = None) -> Optional[tuple]:
    """(id, name) of the one branch the patient named, other than `exclude`."""
    if ctx.branches is None:
        try:
            ctx.branches = run(_branch_rows)
        except Exception as exc:
            logger.warning("recovery: branches unavailable: %s", exc)
            ctx.branches = []
    m = match.match_branch(raw, ctx.branches)
    if not m.value:
        return None
    row = next((b for b in ctx.branches if b.name == m.value), None)
    if row is None or row.id == exclude:
        return None
    return row.id, row.name


# ---------------------------------------------------------------- lines


def _pick(ctx: RecoveryContext, options) -> str:
    options = list(options)
    fresh = [o for o in options if o not in ctx.used] or options
    line = random.choice(fresh)
    ctx.used.add(line)
    return line


def _day(d) -> str:
    return prompts.speak_day(d)


def _slot_words(start: datetime) -> str:
    return prompts.speak_slot(start)


def _service(a: Appt) -> str:
    return prompts.speak_service(a.service) or "appointment"


def _whose(ctx: RecoveryContext, a: Appt) -> str:
    """ "your" for the person on the phone, "Aarav's" for a child or relative they booked for."""
    patient = (a.patient_name or "").split(" ")[0]
    caller = (a.caller_name or "").split(" ")[0]
    if patient and caller and patient.lower() != caller.lower():
        return f"{patient}'s"
    return "your"


def _what(ctx: RecoveryContext, a: Appt) -> str:
    """ "your consultation with Dr Rao on Monday the 12th at 5" """
    service = _service(a)
    service = re.sub(r"^(a|an) ", "", service)
    return f"{_whose(ctx, a)} {service} with {a.doctor} {_day(a.start)} at {prompts.speak_time(a.start)}" \
        if _day(a.start) in ("today", "tomorrow") else \
        f"{_whose(ctx, a)} {service} with {a.doctor} on {_day(a.start)} at {prompts.speak_time(a.start)}"


def _unavailable(ctx: RecoveryContext, a: Appt) -> str:
    start, end = ctx.block_start, ctx.block_end
    if start is None or end is None or start.date() == (end - timedelta(seconds=1)).date():
        day = _day(a.start)
        return f"{a.doctor} isn't available {day}" if day in ("today", "tomorrow") \
            else f"{a.doctor} isn't available on {day.split(' the ')[0]}"
    last = (end - timedelta(seconds=1)).date()
    return f"{a.doctor} isn't available from {_day(start.date())} to {_day(last)}"


def _preference_question(ctx: RecoveryContext, a: Appt) -> str:
    return _pick(ctx, [
        f"Would you like to see {a.doctor} on another day, or another doctor at {a.branch} around the same time?",
        f"Would you prefer to wait for {a.doctor} on a different day, or see one of our other doctors at "
        f"{a.branch} around the same time?",
    ])


def _offer_line(ctx: RecoveryContext, slot: scheduling.Slot, first: bool) -> str:
    a = ctx.current
    who = slot.doctor if a is None or slot.doctor_id != a.doctor_id else slot.doctor
    where = f" at {slot.branch}" if a is not None and slot.branch_id != a.branch_id else ""
    when = _slot_words(slot.start)
    if first:
        return _pick(ctx, [
            f"The nearest I have is {when} with {who}{where}. Would that work?",
            f"I can do {when} with {who}{where}. How does that sound?",
        ])
    return f"There's {when} with {who}{where}. Would that suit you?"


def _alternatives_line(ctx: RecoveryContext, slots: list) -> str:
    """ "I could also do 1:30 or 2:30 tomorrow with Dr Shetty." Shared day / doctor are said once."""
    a = ctx.current
    where = lambda s: f" at {s.branch}" if a is not None and s.branch_id != a.branch_id else ""
    same_doctor = len({(s.doctor_id, s.branch_id) for s in slots}) == 1
    same_day = len({s.start.date() for s in slots}) == 1
    if same_doctor and same_day:
        times = prompts.speak_list([prompts.speak_time(s.start) for s in slots], "or")
        day = _day(slots[0].start)
        day = day if day in ("today", "tomorrow") else f"on {day}"
        body = f"{times} {day} with {slots[0].doctor}{where(slots[0])}"
    elif same_doctor:
        body = f"{prompts.speak_list([_slot_words(s.start) for s in slots], 'or')} with {slots[0].doctor}{where(slots[0])}"
    else:
        body = prompts.speak_list([f"{_slot_words(s.start)} with {s.doctor}{where(s)}" for s in slots], "or")
    if len(slots) == 1:
        return f"I could also do {body}. Would that be better?"
    return f"I could also do {body}. Would either of those suit you?"


def _recap_line(ctx: RecoveryContext, slot: scheduling.Slot) -> str:
    a = ctx.current
    service = re.sub(r"^(a|an) ", "", _service(a))
    where = f" at {slot.branch}" if slot.branch_id != a.branch_id else ""
    return _pick(ctx, [
        f"So that's {_whose(ctx, a)} {service} with {slot.doctor}{where}, {_slot_words(slot.start)}. "
        f"Shall I move it?",
        f"Just to confirm, I'll move {_whose(ctx, a)} {service} to {_slot_words(slot.start)} with "
        f"{slot.doctor}{where}. Is that okay?",
    ])


# ---------------------------------------------------------------- search


def _search(conn, ctx: RecoveryContext) -> list:
    """Up to OFFER_COUNT valid slots for the current appointment, nearest to what the patient wants first."""
    a = ctx.current
    pref = ctx.preference or {"kind": "earliest"}
    kind = pref.get("kind", "earliest")
    today = clock.today()
    horizon = today + timedelta(days=config.BOOKING_HORIZON_DAYS)
    branch_ids = None if kind == "anywhere" else [pref.get("branch_id") or a.branch_id]
    doctor_id = a.doctor_id if kind == "same_doctor" else None
    exclude_doctor = a.doctor_id if kind in ("other_doctor",) else None
    window = None
    near: Optional[datetime] = a.start
    w = ctx.when
    if w is not None and w.date is not None:
        dates = [d for d in w.date.dates() if today <= d <= horizon]
    else:
        first = max(today, a.start.date() - timedelta(days=7))
        dates = [first + timedelta(days=i) for i in range(SEARCH_DAYS) if first + timedelta(days=i) <= horizon]
    if w is not None and w.time is not None:
        if w.time.kind == "exact" and w.time.start is not None:
            base = dates[0] if dates else a.start.date()
            near = clock.localize(datetime.combine(base, w.time.start))
        elif w.time.kind == "window" and w.time.start is not None:
            window = (w.time.start, w.time.end or scheduling.CLOSE)
            near = None
    if kind == "earliest" and (w is None or w.time is None):
        near = None
    if w is not None and w.date is not None and (w.time is None or w.time.kind != "exact"):
        # "Anything on Saturday?" keeps the original time of day as the target.
        near = clock.localize(datetime.combine(dates[0], a.start.time())) if dates and window is None and \
            kind != "earliest" else near
    if not dates:
        return []
    slots = scheduling.find_slots(conn, service=a.service_id, dates=dates, branch_ids=branch_ids,
                                  doctor_id=doctor_id, window=window, near=near, limit=OFFER_COUNT + 6,
                                  call_id=ctx.call_id, ignore_appointment=a.id)
    out = []
    for s in slots:
        if exclude_doctor is not None and s.doctor_id == exclude_doctor:
            continue
        if s.doctor_id == a.doctor_id and s.start == a.start:
            continue
        out.append(s)
        if len(out) >= OFFER_COUNT:
            break
    return out


# ---------------------------------------------------------------- the turn


Runner = Callable  # db runner: run(fn, *args) -> result (sync); see process_turn


def process_turn(ctx: RecoveryContext, text: str, run=None, progress=None) -> Reply:
    """
    One patient turn ("" = the greeting). `run(fn, *args, **kwargs)` executes a
    database function synchronously (tests pass a TempClinic's db.run_sync; the
    server passes the same through ai_engine). `progress("before_action",
    phrase=...)` lets the call play "let me have a look" with typing before a search.
    """
    emit = progress or (lambda *a, **k: None)
    run = run or _default_run
    text = (text or "").strip()
    if ctx.closed_conversation:
        return Reply("Thanks again, bye!")
    if not ctx.greeted:
        ctx.greeted = True
        ctx.state = "identity"
        first = ctx.contact_first
        line = _pick(ctx, [f"Hi, is this {first}? It's Emma from Pearl Dental.",
                           f"Hello, am I speaking with {first}? This is Emma calling from Pearl Dental."]) \
            if first else "Hi, it's Emma from Pearl Dental. Who am I speaking with?"
        return _say(ctx, line, "yes_no")
    if not text:
        return _say(ctx, ctx.last_line or "Hello?", "open")

    t = _norm(text)

    # ---- things that can happen at any point
    if _robot_question(text):
        return _say(ctx, f"{config.HONEST_LINE} {_question_for(ctx)}", _expect(ctx))
    if _has(t, *_DNC):
        run(_set_dnc, ctx.phone_e164)
        ctx.note = "Asked not to be called again."
        return _close(ctx, "do_not_call", "Of course, I've made a note not to call this number again. "
                                          "Sorry to have bothered you. Take care, bye.")
    if _has(t, *_SCAM):
        ctx.note = "Wasn't sure the call was genuine; invited to call the clinic directly."
        return _close(ctx, "suspicious", "That's a fair question. You're very welcome to hang up and call Pearl "
                                         "Dental directly on the number on our website, and the front desk will "
                                         "help you. Take care, bye.")
    if _has(t, *_REPEAT) and len(t.split()) <= 6:
        return _say(ctx, ctx.last_line, _expect(ctx))
    if _has(t, *_WAIT) and len(t.split()) <= 6:
        return _say(ctx, _pick(ctx, ["Sure, take your time.", "No problem, I'll wait."]), _expect(ctx))

    if ctx.state == "identity":
        return _identity(ctx, text, t, run, emit)
    if ctx.state in ("preference", "branch"):
        return _preference(ctx, text, t, run, emit)
    if ctx.state == "offer":
        return _offer(ctx, text, t, run, emit)
    if ctx.state == "recap":
        return _recap(ctx, text, t, run, emit)
    if ctx.state == "confirm_cancel":
        return _confirm_cancel(ctx, text, t, run, emit)
    if ctx.state == "next":
        return _next(ctx, text, t, run, emit)
    return _say(ctx, ctx.last_line, "open")


def _default_run(fn, *args, **kwargs):
    import db
    return db.get_db().run_sync(fn, *args, **kwargs)


def _say(ctx: RecoveryContext, line: str, expect: str = "open", action: Optional[str] = None) -> Reply:
    line = _WS.sub(" ", line).strip()
    ctx.last_line = line
    ctx.history.append({"role": "assistant", "content": line})
    return Reply(line, action=action, expect=expect)


def _expect(ctx: RecoveryContext) -> str:
    return {"identity": "yes_no", "recap": "yes_no", "confirm_cancel": "yes_no", "next": "yes_no",
            "offer": "choice"}.get(ctx.state, "open")


def _question_for(ctx: RecoveryContext) -> str:
    """The question still waiting, said again after a side answer."""
    a = ctx.current
    if ctx.state == "identity":
        return f"Am I speaking with {ctx.contact_first}?" if ctx.contact_first else "Who am I speaking with?"
    if ctx.state == "preference" and a is not None:
        return _preference_question(ctx, a)
    if ctx.state == "branch":
        return "Which branch would suit you best?"
    if ctx.state == "offer" and ctx.offers:
        return "Would any of those times work for you?"
    if ctx.state == "recap" and ctx.chosen is not None:
        return "Shall I go ahead and move it?"
    if ctx.state == "confirm_cancel":
        return "Shall I cancel it?"
    return ""


def _tick(ctx: RecoveryContext) -> int:
    ctx.tries[ctx.state] = ctx.tries.get(ctx.state, 0) + 1
    return ctx.tries[ctx.state]


def _set_dnc(conn, phone_e164):
    import outbound
    outbound.set_do_not_call(conn, phone_e164)


def _close(ctx: RecoveryContext, outcome: str, line: str, action: Optional[str] = None) -> Reply:
    ctx.outcome = outcome
    ctx.state = "closed"
    ctx.closed_conversation = True
    return _say(ctx, line, "open", action)


def _side_exits(ctx: RecoveryContext, t: str, run) -> Optional[Reply]:
    """Requests that end the call with the appointments left for the front desk (after identity)."""
    a = ctx.current
    if _has(t, *_STAFF):
        ctx.note = "Asked to speak to someone at the clinic about the new time."
        return _close(ctx, "staff", _pick(ctx, [
            "Of course. I'll ask the front desk to give you a call today to sort it out. Thanks, bye.",
            "Sure, I'll have someone from the front desk call you back today. Take care, bye."]))
    if _has(t, *_BUSY):
        ctx.retry_at, when = _callback_time(t)
        ctx.note = "Busy when called; asked for a call back" + (f" {when}." if when else ".")
        if when:
            return _close(ctx, "busy", _pick(ctx, [f"No problem at all, we'll call you back {when}. Bye for now.",
                                                   f"Sure, we'll give you a ring {when}. Take care."]))
        return _close(ctx, "busy", "No problem at all. We'll call you back a bit later. Bye for now.")
    if _has(t, *_BYE) and len(t.split()) <= 4:
        ctx.note = "Ended the call before a new time was agreed."
        return _close(ctx, "pending", "Okay, I'll ask the front desk to call you about a new time. Bye.")
    if a is not None and _has(t, *_CANCEL) and not _has(t, r"\bdon't cancel\b", r"\bnot cancel\b"):
        ctx.state = "confirm_cancel"
        return _say(ctx, f"Okay. Just to check, shall I cancel {_what(ctx, a)}?", "yes_no")
    if a is not None and _has(t, *_PENDING) and not _when(t):
        return _pending(ctx, run)
    return None


def _pending(ctx: RecoveryContext, run) -> Reply:
    a = ctx.current
    ctx.results[a.id] = "pending"
    ctx.note = "Wanted to think about a new time; keep it on hold."
    lead = _pick(ctx, ["No problem, I'll keep it on hold, and the front desk will call you to find a time.",
                       "That's fine. I'll put it on hold and someone from the front desk will call you to fix a new time."])
    return _advance(ctx, run, lead)


# -------------------------------------------------- identity


def _identity(ctx, text, t, run, emit) -> Reply:
    first = ctx.contact_first
    if _has(t, *_WHO):
        if _tick(ctx) > 2:
            return _wrong_person(ctx)
        return _say(ctx, f"It's Emma, from Pearl Dental. Am I speaking with {first}?" if first
                    else "It's Emma, from Pearl Dental.", "yes_no")
    yn = _yes_no(text)
    named_other = re.search(r"\b(this is|it's|i'm|im|my name is) (his|her|their|the) ", t) or \
        re.search(r"\b(wrong number|not here|isn't here|is not here|not available|she's out|he's out|"
                  r"no one by that name|nobody by that name)\b", t)
    said_name = first and re.search(rf"\b{re.escape(first.lower())}\b", t)
    if yn == "yes" and not named_other or (said_name and yn != "no" and not named_other):
        return _explain(ctx)
    if yn == "no" or named_other:
        return _wrong_person(ctx)
    if _tick(ctx) > 2:
        return _wrong_person(ctx)
    return _say(ctx, f"Sorry, I just want to make sure I've got the right person. Is this {first}?" if first
                else "Sorry, who am I speaking with?", "yes_no")


def _wrong_person(ctx: RecoveryContext) -> Reply:
    first = ctx.contact_first
    ctx.note = "Someone else answered; no details shared. Please call the patient."
    line = (f"Oh, sorry to bother you. Could you ask {first} to give Pearl Dental a call when they get a chance? "
            f"Thanks so much, bye." if first else "Sorry to bother you. Thanks, bye.")
    return _close(ctx, "wrong_person", line)


def _explain(ctx: RecoveryContext) -> Reply:
    a = ctx.current
    ctx.state = "preference"
    ctx.tries.pop("preference", None)
    first = ctx.contact_first
    thanks = _pick(ctx, [f"Thanks, {first}." if first else "Thanks.", f"Hi {first}." if first else "Hi."])
    explain = f"I'm calling about {_what(ctx, a)}. I'm afraid {_unavailable(ctx, a)}, so we'll need to move it."
    if len(ctx.appts) > 1:
        explain += f" I'll help with your other appointment with {a.doctor} right after this one."
    return _say(ctx, f"{thanks} {explain} {_preference_question(ctx, a)}", "open")


# -------------------------------------------------- preference


def _preference(ctx, text, t, run, emit) -> Reply:
    a = ctx.current
    exit_ = _side_exits(ctx, t, run)
    if exit_ is not None:
        return exit_
    if _has(t, *_WHY) and not _when(text):
        return _say(ctx, f"Something's come up and {a.doctor} can't be in, I'm sorry about that. "
                         f"{_preference_question(ctx, a)}", "open")

    when = _when(text)
    branch = _branch(ctx, text, run)
    pref: Optional[dict] = None
    if branch is not None and branch[0] != a.branch_id:
        pref = {"kind": "other_branch", "branch_id": branch[0], "branch": branch[1]}
    elif _has(t, *_OTHER_BRANCH):
        ctx.state = "branch"
        _branch(ctx, "", run)
        names = [b.name for b in ctx.branches or [] if b.id != a.branch_id]
        listed = f" We have {prompts.speak_list(names, 'and')}." if names else ""
        return _say(ctx, f"Sure.{listed} Which would suit you best?", "open")
    elif _has(t, *_EARLIEST):
        pref = {"kind": "earliest"}
    elif _has(t, *_OTHER_DOCTOR):
        pref = {"kind": "other_doctor"}
    elif _has(t, *_ANY_DOCTOR):
        pref = {"kind": "any_doctor"}
    elif _has(t, *_SAME_DOCTOR) or _mentions_doctor(t, a):
        pref = {"kind": "same_doctor"}
    elif ctx.state == "branch" and branch is not None:
        pref = {"kind": "earliest_branch", "branch_id": branch[0], "branch": branch[1]}

    if pref is None and when is not None:
        pref = {"kind": "any_doctor"}
    if pref is None:
        yn = _yes_no(text)
        n = _tick(ctx)
        if n >= 3 or (n >= 2 and yn == "yes"):
            pref = {"kind": "earliest"}
        elif yn == "no" and n >= 2:
            return _pending(ctx, run)
        else:
            if ctx.state == "branch":
                return _say(ctx, "Sorry, which branch was that?", "open")
            return _say(ctx, _pick(ctx, [
                f"Sorry, I didn't quite catch that. Shall I keep {a.doctor}, or would any doctor at {a.branch} be okay?",
                f"No problem. I can look for {a.doctor} on another day, or the earliest time with any doctor at "
                f"{a.branch}. Which would you prefer?"]), "open")
    ctx.preference = pref
    ctx.when = when
    return _find_and_offer(ctx, run, emit)


def _mentions_doctor(t: str, a: Appt) -> bool:
    surname = a.doctor.split(" ")[-1].lower()
    return bool(surname) and re.search(rf"\b{re.escape(surname)}\b", t) is not None


# When nothing fits the patient's preference, the nearest thing that does exist,
# in this order (another doctor may not offer the service; a doctor may be away for weeks).
_FALLBACKS = (
    ("any_doctor", "I don't have anything quite like that, but I can do"),
    ("earliest", "Nothing's free around that time, but the earliest I have is"),
    ("anywhere", "Nothing's free at {branch} soon, but I can do"),
)


def _find_and_offer(ctx: RecoveryContext, run, emit, lead: str = "") -> Reply:
    emit("before_action", phrase=_pick(ctx, ["Let me have a look.", "Let me just check.", "One moment, let me check."]))
    slots = run(_search, ctx)
    if not slots and ctx.when is None:
        a = ctx.current
        tried = ctx.preference.get("kind")
        for kind, bridge in _FALLBACKS:
            if kind == tried:
                continue
            ctx.preference = {"kind": kind}
            slots = run(_search, ctx)
            if slots:
                first = slots[0]
                where = f" at {first.branch}" if first.branch_id != a.branch_id else ""
                ctx.offers, ctx.offered, ctx.chosen = slots, 1, None
                ctx.tries.pop("offer", None)
                ctx.state = "offer"
                line = (f"{bridge.format(branch=a.branch)} {_slot_words(first.start)} with {first.doctor}{where}. "
                        f"Would that work?")
                return _say(ctx, f"{lead} {line}", "choice")
    ctx.offers, ctx.offered, ctx.chosen = slots, 0, None
    ctx.tries.pop("offer", None)
    if not slots:
        if ctx.when is not None:
            ctx.state = "preference"
            ctx.when = None
            return _say(ctx, f"{lead} I'm sorry, I don't have anything then. Is there another day that would "
                             f"work, or shall I just find the earliest?".strip(), "open")
        return _no_slots(ctx, run, lead)
    ctx.state = "offer"
    ctx.offered = 1
    return _say(ctx, f"{lead} {_offer_line(ctx, slots[0], first=True)}", "choice")


def _no_slots(ctx: RecoveryContext, run, lead: str = "") -> Reply:
    a = ctx.current
    ctx.results[a.id] = "pending"
    ctx.note = "No valid alternative found in the next few weeks."
    line = (f"{lead} I'm sorry, I can't see anything suitable in the next few weeks. I'll keep it on hold and the "
            f"front desk will call you to sort out a time.")
    return _advance(ctx, run, line.strip())


# -------------------------------------------------- offer


def _pick_offer(ctx: RecoveryContext, text: str, t: str) -> Optional[scheduling.Slot]:
    said = ctx.offers[:max(ctx.offered, 1)]
    if not said:
        return None
    ordinals = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "last": len(said) - 1,
                "earlier": 0, "later": len(said) - 1}
    m = re.search(r"\b(first|1st|second|2nd|third|3rd|last|earlier|later) (one|option|slot|time)?\b", t)
    if m and (m.group(2) or m.group(1) in ("earlier", "later", "last")):
        i = ordinals[m.group(1)]
        return said[i] if i < len(said) else None
    when = _when(text)
    if when is None:
        return None
    hits = []
    for s in said:
        ok = True
        if when.date is not None and s.start.date() not in set(when.date.dates()):
            ok = False
        if when.time is not None and when.time.kind == "exact" and when.time.start is not None \
                and s.start.time() != when.time.start:
            ok = False
        if when.time is not None and when.time.kind == "window" and when.time.start is not None:
            end = when.time.end or scheduling.CLOSE
            if not (when.time.start <= s.start.time() < end):
                ok = False
        if ok:
            hits.append(s)
    return hits[0] if len(hits) == 1 else None


def _offer(ctx, text, t, run, emit) -> Reply:
    a = ctx.current
    picked = _pick_offer(ctx, text, t)
    if picked is not None:
        return _to_recap(ctx, picked)
    yn = _yes_no(text)
    if yn == "yes" and ctx.offered == 1:
        return _to_recap(ctx, ctx.offers[0])
    if yn == "yes" and ctx.offered > 1:
        return _say(ctx, "Great, which one would you like?", "choice")
    exit_ = _side_exits(ctx, t, run)
    if exit_ is not None:
        return exit_
    when = _when(text)
    if when is not None:
        # "Anything on Saturday?" / "Do you have something after 5?": search that instead.
        ctx.when = when
        if ctx.preference.get("kind") in (None, "earliest"):
            ctx.preference = {**ctx.preference, "kind": "any_doctor"}
        return _find_and_offer(ctx, run, emit)
    branch = _branch(ctx, text, run, exclude=a.branch_id)
    if branch is not None:
        ctx.preference = {"kind": "other_branch", "branch_id": branch[0], "branch": branch[1]}
        ctx.when = None
        return _find_and_offer(ctx, run, emit)
    if _has(t, *_OTHER_DOCTOR) and ctx.preference.get("kind") == "same_doctor":
        ctx.preference = {"kind": "other_doctor"}
        return _find_and_offer(ctx, run, emit)
    if _has(t, *_SAME_DOCTOR) and ctx.preference.get("kind") != "same_doctor" or _mentions_doctor(t, a) \
            and ctx.preference.get("kind") != "same_doctor":
        ctx.preference = {"kind": "same_doctor"}
        return _find_and_offer(ctx, run, emit)
    if yn == "no" or _has(t, r"\b(none|neither|doesn't work|don't work|won't work|can't (make|do))\b"):
        more = ctx.offers[ctx.offered:]
        if more:
            ctx.offered = len(ctx.offers)
            return _say(ctx, _alternatives_line(ctx, more), "choice")
        ctx.state = "preference"
        ctx.tries["preference"] = 1
        return _say(ctx, _pick(ctx, [
            "No problem. Is there a day or time that would suit you better? Or I can keep it on hold for the "
            "front desk to sort out.",
            "That's okay. Tell me a day that works for you and I'll check, or I can put it on hold for now."]),
            "open")
    if _tick(ctx) >= 2:
        return _say(ctx, f"Sorry, would {_slot_words(ctx.offers[0].start)} work for you? Just say yes or no.",
                    "yes_no")
    return _say(ctx, "Sorry, which time would you like?" if ctx.offered > 1
                else f"Sorry, does {_slot_words(ctx.offers[0].start)} work for you?", "choice")


def _to_recap(ctx: RecoveryContext, slot: scheduling.Slot) -> Reply:
    ctx.chosen = slot
    ctx.state = "recap"
    ctx.tries.pop("recap", None)
    return _say(ctx, _recap_line(ctx, slot), "yes_no")


# -------------------------------------------------- recap and commit


def _recap(ctx, text, t, run, emit) -> Reply:
    a = ctx.current
    yn = _yes_no(text)
    if yn == "yes" and not ctx.last_reply_heard:
        # The recap was cut off: a yes to half a summary moves nothing (recap-heard rule).
        return _say(ctx, _recap_line(ctx, ctx.chosen), "yes_no")
    if yn == "yes":
        return _commit(ctx, run, emit)
    exit_ = _side_exits(ctx, t, run)
    if exit_ is not None:
        return exit_
    if yn == "no" or _when(text) is not None:
        ctx.state = "offer"
        when = _when(text)
        if when is not None:
            ctx.when = when
            return _find_and_offer(ctx, run, emit, lead="Okay.")
        others = [s for s in ctx.offers if s != ctx.chosen]
        if others:
            ctx.offers, ctx.offered = others, len(others)
            return _say(ctx, f"No problem. {_alternatives_line(ctx, others)}", "choice")
        ctx.state = "preference"
        return _say(ctx, "No problem. What day would suit you better?", "open")
    if _tick(ctx) >= 2:
        return _say(ctx, f"Sorry, shall I move it to {_slot_words(ctx.chosen.start)}? Yes or no is fine.", "yes_no")
    return _say(ctx, _recap_line(ctx, ctx.chosen), "yes_no")


def _do_reschedule(conn, ctx: RecoveryContext, slot: scheduling.Slot):
    a = ctx.current
    return scheduling.reschedule(conn, a.id, doctor_id=slot.doctor_id, start=slot.start,
                                 idem_key=f"recovery-job{ctx.job_id}-{a.id}", expected_version=a.version,
                                 call_id=ctx.call_id, actor="emma")


def _commit(ctx: RecoveryContext, run, emit) -> Reply:
    a, slot = ctx.current, ctx.chosen
    result = run(_do_reschedule, ctx, slot)
    if result.ok:
        ctx.results[a.id] = "rescheduled"
        when = _slot_words(slot.start)
        done = _pick(ctx, [f"Done, you're all set for {when} with {slot.doctor}.",
                           f"All done. That's {when} with {slot.doctor}."])
        if slot.branch_id != a.branch_id:
            done += f" That's at our {slot.branch} branch."
        return _advance(ctx, run, done, action="rescheduled")
    logger.info("recovery %s: reschedule of %s failed: %s", ctx.call_id, a.id, result.code)
    if result.code in ("STALE", "NOT_ACTIVE", "NOT_FOUND"):
        ctx.results[a.id] = "changed"
        return _advance(ctx, run, "Oh, it looks like that appointment has already been changed, so I'll leave it "
                                  "as it is.")
    ctx.offers = [s for s in ctx.offers if s != slot]
    return _find_and_offer(ctx, run, emit, lead="Oh, I'm sorry, that one's just been taken.")


# -------------------------------------------------- cancel


def _do_cancel(conn, ctx: RecoveryContext):
    a = ctx.current
    return scheduling.cancel(conn, a.id, idem_key=f"recovery-cancel-job{ctx.job_id}-{a.id}",
                             reason="Doctor unavailable; patient chose to cancel on the recovery call",
                             expected_version=a.version, call_id=ctx.call_id, actor="emma")


def _confirm_cancel(ctx, text, t, run, emit) -> Reply:
    a = ctx.current
    yn = _yes_no(text)
    if yn == "yes":
        result = run(_do_cancel, ctx)
        if result.ok:
            ctx.results[a.id] = "cancelled"
            return _advance(ctx, run, _pick(ctx, [
                "Okay, that's cancelled. Whenever you'd like to rebook, just give us a call.",
                "Done, I've cancelled it. Just call us whenever you'd like to book again."]), action="cancelled")
        ctx.results[a.id] = "changed"
        return _advance(ctx, run, "It looks like that appointment has already been changed, so I'll leave it.")
    if yn == "no":
        ctx.state = "preference"
        return _say(ctx, f"Okay, let's find a new time then. {_preference_question(ctx, a)}", "open")
    if _tick(ctx) >= 2:
        return _pending(ctx, run)
    return _say(ctx, "Sorry, shall I cancel it? Yes or no is fine.", "yes_no")


# -------------------------------------------------- next appointment / close


def _advance(ctx: RecoveryContext, run, lead: str, action: Optional[str] = None) -> Reply:
    """After one appointment is settled: the next one, or a warm goodbye."""
    ctx.idx += 1
    ctx.offers, ctx.offered, ctx.chosen, ctx.when = [], 0, None, None
    a = ctx.current
    if a is not None:
        ctx.state = "next"
        return _say(ctx, f"{lead} There's also {_what(ctx, a)}. Shall I find something similar for that one?",
                    "yes_no", action)
    results = set(ctx.results.values())
    first = ctx.contact_first
    if results <= {"rescheduled", "cancelled", "changed"}:
        outcome = "rescheduled" if "rescheduled" in results else "cancelled" if "cancelled" in results else "stale"
        bye = _pick(ctx, [f"Sorry again for the change{', ' + first if first else ''}. Take care, bye!",
                          "Thanks for being so understanding. Bye for now!"])
    else:
        outcome = "pending"
        bye = "Thanks for your patience, and sorry again for the trouble. Bye!"
    return _close(ctx, outcome, f"{lead} {bye}", action)


def _next(ctx, text, t, run, emit) -> Reply:
    a = ctx.current
    exit_ = _side_exits(ctx, t, run)
    if exit_ is not None:
        return exit_
    yn = _yes_no(text)
    when = _when(text)
    if yn == "yes" or when is not None:
        ctx.when = when
        ctx.preference = ctx.preference or {"kind": "earliest"}
        return _find_and_offer(ctx, run, emit)
    if yn == "no":
        ctx.state = "preference"
        return _say(ctx, f"Okay. {_preference_question(ctx, a)}", "open")
    if _tick(ctx) >= 2:
        return _pending(ctx, run)
    return _say(ctx, "Sorry, shall I look for a similar time for that one too?", "yes_no")


# ---------------------------------------------------------------- facade hooks


def listening_hint(ctx: RecoveryContext) -> dict:
    return {"expect": _expect(ctx), "digits_so_far": 0}


_IN_RE = re.compile(r"\bin (?:an? |one )?(half an? hour|hour|(?:\d+|ten|fifteen|twenty|thirty|forty five) "
                    r"(?:minutes?|mins?|hours?))\b")
_NUMBER_WORDS = {"ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty five": 45}


def _callback_time(text: str) -> tuple:
    """
    "call me after 6" -> (today 18:00, "after 6"); "in an hour" -> (now + 1 h, "in about an hour");
    "tomorrow morning" -> (tomorrow 09:00, "tomorrow morning"). (None, "") when no time was given.
    The runner keeps the time inside the calling hours.
    """
    t = _norm(text)
    now = clock.now()
    m = _IN_RE.search(t)
    if m:
        what = m.group(1)
        if what.startswith("half"):
            minutes = 30
        elif what == "hour":
            minutes = 60
        else:
            amount, unit = what.rsplit(" ", 1)
            n = int(amount) if amount.isdigit() else _NUMBER_WORDS.get(amount, 0)
            minutes = n * 60 if unit.startswith("hour") else n
        if minutes:
            if minutes == 60:
                spoken = "in about an hour"
            elif minutes < 60:
                spoken = f"in about {minutes} minutes"
            else:
                spoken = f"in about {minutes // 60} hours"
            return now + timedelta(minutes=minutes), spoken
    w = _when(text)
    if w is None:
        return None, ""
    day = next(iter(w.date.dates())) if w.date is not None else now.date()
    hour = w.time.start if w.time is not None and w.time.start is not None else time_of_day(9)
    at = clock.localize(datetime.combine(day, hour))
    if at <= now:
        return None, ""
    if w.date is None and w.time is not None and w.time.kind == "exact":
        lead = "after" if re.search(r"\bafter\b", t) else "at"
        return at, f"{lead} {prompts.speak_time(w.time.start)}"
    return at, prompts.speak_when(w.date, w.time) or ""


def call_result(ctx: RecoveryContext) -> dict:
    """What the runner records for the job when the call ends (hang-up at any point included)."""
    unresolved = ctx.unresolved()
    if ctx.outcome is None:
        outcome = "abandoned" if ctx.state != "identity" else "hung_up"
        note = f"The call ended early ({ctx.state})."
    else:
        outcome, note = ctx.outcome, ctx.note
    if not ctx.greeted or ctx.state == "identity" and ctx.outcome is None:
        note = "Hung up before identity was confirmed; nothing was shared."
    return {"outcome": outcome, "unresolved": unresolved, "note": note,
            "results": dict(ctx.results), "retry_at": ctx.retry_at.isoformat() if ctx.retry_at else None}
