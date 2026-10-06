"""
Appointment rules, slot search, holds and the book / reschedule / cancel
transactions (docs/IMPLEMENTATION_PLAN.md, sections 4 and 5.1).

Every function takes a sqlite3 connection first, so the server runs them on
the database thread (`await db.get_db().run(scheduling.book, ...)`) and tests
call them directly.

A start time is bookable for (doctor, service) only if all of these hold, and
the first one that fails is the reason reported (REASON_ORDER):

    OFF_GRID           starts on :00 or :30
    TOO_SOON           at least BOOKING_LEAD_MIN from now (emergencies: EMERGENCY_LEAD_MIN)
    BEYOND_HORIZON     within BOOKING_HORIZON_DAYS
    CLOSED_DAY         not a Sunday
    CLOSURE            not a closure date for the doctor's branch
    OUTSIDE_HOURS      the whole appointment fits in 07:00-21:00
    LUNCH              it does not overlap the 14:00-14:30 lunch break
    DOCTOR_NO_SERVICE  the doctor performs the service
    DOCTOR_OFF         the whole appointment fits one of the doctor's working rules
    DOCTOR_BLOCKED     the doctor is not blocked (unavailability) over it
    TAKEN              every 30-minute cell it touches is free (or held by this same call)

Transactions re-run every rule at commit time, so a block, closure or booking
that appeared mid-call is always respected. The slot_claims primary key is the
final guard against a double booking.
"""

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Iterable, Optional

import clock
import config
import db
import phones
from dateparse import DateConstraint, TimeConstraint

GRID = timedelta(minutes=config.SLOT_GRID_MIN)
OPEN = time(config.CLINIC_START_HOUR, 0)
CLOSE = time(config.CLINIC_END_HOUR, 0)
LUNCH = (time(config.LUNCH_START_HOUR, config.LUNCH_START_MIN),
         time(config.LUNCH_END_HOUR, config.LUNCH_END_MIN))

REASON_ORDER = [
    "OFF_GRID", "TOO_SOON", "BEYOND_HORIZON", "CLOSED_DAY", "CLOSURE", "OUTSIDE_HOURS",
    "LUNCH", "DOCTOR_NO_SERVICE", "DOCTOR_OFF", "DOCTOR_BLOCKED", "TAKEN",
]
# Reasons that apply to every doctor alike; worth telling the caller.
CLINIC_WIDE = {"OFF_GRID", "TOO_SOON", "BEYOND_HORIZON", "CLOSED_DAY", "OUTSIDE_HOURS", "LUNCH"}

# ---------------------------------------------------------------- types


@dataclass(frozen=True)
class Slot:
    doctor_id: int
    doctor: str            # spoken name, e.g. "Dr Rao"
    branch_id: int
    branch: str
    service_id: int
    service: str
    start: datetime        # clinic-local, timezone-aware
    end: datetime


@dataclass
class Result:
    ok: bool
    code: str              # OK, or why not (TAKEN, TOO_SOON, STALE, ...)
    appointment_id: Optional[str] = None
    appointment: Optional[dict] = None
    replayed: bool = False


@dataclass
class Suggestion:
    kind: str                              # exact | alternatives | none
    slots: list = field(default_factory=list)
    scope: str = ""                        # same_day | in_window | same_day_other_time | in_range | later_days
    requested: Optional[datetime] = None   # the exact time asked for, if any
    reasons: list = field(default_factory=list)  # why the exact request failed
    searched_until: Optional[date] = None


# ---------------------------------------------------------------- helpers


def _at(day: date, t: time) -> datetime:
    return datetime.combine(day, t, tzinfo=clock.TZ)


def _now(now: Optional[datetime]) -> datetime:
    return clock.localize(now) if now is not None else clock.now()


def cells(start: datetime, duration_min: int) -> list[datetime]:
    """The 30-minute grid cells an appointment touches: 45 and 60 minutes both take two."""
    end = start + timedelta(minutes=duration_min)
    out, cell = [], start
    while cell < end:
        out.append(cell)
        cell += GRID
    return out


def _hm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def norm_name(name: str) -> str:
    return " ".join("".join(ch for ch in (name or "").lower() if ch.isalnum() or ch.isspace()).split())


def get_service(conn, service) -> Optional[sqlite3.Row]:
    """A service by id or by (case-insensitive) name."""
    if isinstance(service, int):
        return conn.execute("SELECT * FROM services WHERE id = ? AND active = 1", (service,)).fetchone()
    return conn.execute("SELECT * FROM services WHERE lower(name) = lower(?) AND active = 1",
                        (str(service),)).fetchone()


def get_branch(conn, branch) -> Optional[sqlite3.Row]:
    if isinstance(branch, int):
        return conn.execute("SELECT * FROM branches WHERE id = ? AND active = 1", (branch,)).fetchone()
    return conn.execute("SELECT * FROM branches WHERE lower(name) = lower(?) AND active = 1",
                        (str(branch),)).fetchone()


def _doctors(conn, service_id: int, branch_ids=None, doctor_id=None, gender=None) -> list[sqlite3.Row]:
    sql = ("SELECT d.*, b.name AS branch_name FROM doctors d JOIN branches b ON b.id = d.branch_id "
           "JOIN doctor_services ds ON ds.doctor_id = d.id "
           "WHERE d.active = 1 AND b.active = 1 AND ds.service_id = ?")
    args: list = [service_id]
    if branch_ids:
        sql += f" AND d.branch_id IN ({','.join('?' * len(branch_ids))})"
        args += list(branch_ids)
    if doctor_id is not None:
        sql += " AND d.id = ?"
        args.append(doctor_id)
    if gender:
        sql += " AND d.gender = ?"
        args.append(gender)
    return conn.execute(sql + " ORDER BY d.branch_id, d.id", args).fetchall()


class _Context:
    """Everything the rules need for some doctors over a date range, loaded in one go."""

    def __init__(self, conn, doctor_ids, first: date, last: date, now: datetime,
                 call_id=None, ignore_appointment=None):
        self.doctor_ids = list(doctor_ids)
        marks = ",".join("?" * len(self.doctor_ids)) or "NULL"
        lo, hi = db.utc_str(_at(first, time(0, 0))), db.utc_str(_at(last + timedelta(days=1), time(0, 0)))
        now_s = db.utc_str(now)

        self.rules: dict = {}
        for r in conn.execute(f"SELECT * FROM availability_rules WHERE doctor_id IN ({marks})", self.doctor_ids):
            self.rules.setdefault((r["doctor_id"], r["weekday"]), []).append((_hm(r["start_time"]), _hm(r["end_time"])))
        self.services = {(r["doctor_id"], r["service_id"]) for r in conn.execute(
            f"SELECT * FROM doctor_services WHERE doctor_id IN ({marks})", self.doctor_ids)}
        self.branch_of = {r["id"]: r["branch_id"] for r in conn.execute(
            f"SELECT id, branch_id FROM doctors WHERE id IN ({marks})", self.doctor_ids)}

        self.taken: set = set()
        for r in conn.execute(
            f"SELECT c.doctor_id, c.cell_start_utc, c.appointment_id, h.call_id, h.expires_at "
            f"FROM slot_claims c LEFT JOIN slot_holds h ON h.id = c.hold_id "
            f"WHERE c.doctor_id IN ({marks}) AND c.cell_start_utc >= ? AND c.cell_start_utc < ?",
            self.doctor_ids + [lo, hi],
        ):
            if r["appointment_id"] is not None:
                if r["appointment_id"] == ignore_appointment:
                    continue
            elif r["expires_at"] <= now_s or (call_id is not None and r["call_id"] == call_id):
                continue                                  # expired hold, or this call's own hold
            self.taken.add((r["doctor_id"], r["cell_start_utc"]))

        self.blocks: dict = {}
        for r in conn.execute(
            f"SELECT doctor_id, start_utc, end_utc FROM blocked_times WHERE lifted_at IS NULL "
            f"AND doctor_id IN ({marks}) AND end_utc > ? AND start_utc < ?", self.doctor_ids + [lo, hi],
        ):
            self.blocks.setdefault(r["doctor_id"], []).append((db.parse_utc(r["start_utc"]), db.parse_utc(r["end_utc"])))

        self.closures = {(r["date"], r["branch_id"]) for r in conn.execute(
            "SELECT date, branch_id FROM closures WHERE date BETWEEN ? AND ?", (first.isoformat(), last.isoformat()))}

    def reasons(self, doctor_id: int, service_id: int, duration: int, start: datetime,
                now: datetime, lead_min: int) -> list[str]:
        out = []
        end = start + timedelta(minutes=duration)
        day = start.date()
        if start.minute % config.SLOT_GRID_MIN or start.second:
            out.append("OFF_GRID")
        if start < now + timedelta(minutes=lead_min):
            out.append("TOO_SOON")
        if day > now.date() + timedelta(days=config.BOOKING_HORIZON_DAYS):
            out.append("BEYOND_HORIZON")
        if day.weekday() in config.CLOSED_DAYS:
            out.append("CLOSED_DAY")
        branch = self.branch_of.get(doctor_id)
        if (day.isoformat(), None) in self.closures or (day.isoformat(), branch) in self.closures:
            out.append("CLOSURE")
        if start < _at(day, OPEN) or end > _at(day, CLOSE):
            out.append("OUTSIDE_HOURS")
        if start < _at(day, LUNCH[1]) and end > _at(day, LUNCH[0]):
            out.append("LUNCH")
        if (doctor_id, service_id) not in self.services:
            out.append("DOCTOR_NO_SERVICE")
        if not any(_at(day, rs) <= start and end <= _at(day, re_) for rs, re_ in self.rules.get((doctor_id, day.weekday()), [])):
            out.append("DOCTOR_OFF")
        if any(bs < end and start < be for bs, be in self.blocks.get(doctor_id, [])):
            out.append("DOCTOR_BLOCKED")
        if any((doctor_id, db.utc_str(c)) in self.taken for c in cells(start, duration)):
            out.append("TAKEN")
        return out


def _lead(emergency: bool) -> int:
    return config.EMERGENCY_LEAD_MIN if emergency else config.BOOKING_LEAD_MIN


def _slot(doc: sqlite3.Row, svc: sqlite3.Row, start: datetime) -> Slot:
    return Slot(doc["id"], doc["spoken_name"], doc["branch_id"], doc["branch_name"], svc["id"], svc["name"],
                start, start + timedelta(minutes=svc["duration_min"]))


# ---------------------------------------------------------------- validation and search


def validate(conn, *, service, doctor_id: int, start: datetime, now=None, emergency=False,
             call_id=None, ignore_appointment=None) -> list[str]:
    """Why `start` cannot be booked with this doctor, in REASON_ORDER; [] if it can."""
    now = _now(now)
    start = clock.localize(start)
    svc = get_service(conn, service)
    if svc is None:
        return ["UNKNOWN_SERVICE"]
    ctx = _Context(conn, [doctor_id], start.date(), start.date(), now, call_id, ignore_appointment)
    return ctx.reasons(doctor_id, svc["id"], svc["duration_min"], start, now, _lead(emergency))


def find_slots(conn, *, service, dates: Iterable[date], branch_ids=None, doctor_id=None, gender=None,
               window: Optional[tuple] = None, near: Optional[datetime] = None, near_time: Optional[time] = None,
               limit: int = 2, now=None, emergency=False, call_id=None, ignore_appointment=None,
               distinct_times: bool = True, patient: Optional[tuple] = None) -> list[Slot]:
    """
    Valid slots on `dates`, starting inside `window` (start, end) if given.
    patient: (phone_e164, name_norm): skip times that overlap an appointment
    that patient already has (book() would refuse them as PATIENT_CONFLICT).

    Ordering: with `near`, closest to that moment (earlier wins ties); with
    `near_time`, closest to that time of day, then earliest date; otherwise
    earliest first. With distinct_times, two doctors free at the same moment
    count once, so the caller hears two different times.
    """
    now = _now(now)
    svc = get_service(conn, service)
    dates = sorted(set(dates))
    if svc is None or not dates:
        return []
    doctors = _doctors(conn, svc["id"], branch_ids, doctor_id, gender)
    if not doctors:
        return []
    ctx = _Context(conn, [d["id"] for d in doctors], dates[0], dates[-1], now, call_id, ignore_appointment)
    lead = _lead(emergency)
    w_start, w_end = window or (OPEN, CLOSE)
    found: list[Slot] = []
    busy = _patient_busy(conn, patient, dates[0], dates[-1]) if patient else []
    length = timedelta(minutes=svc["duration_min"])

    def pick(candidates):
        if near is not None:
            candidates.sort(key=lambda s: (abs((s.start - near).total_seconds()), s.start))
        elif near_time is not None:
            target = near_time.hour * 60 + near_time.minute
            candidates.sort(key=lambda s: (abs(s.start.hour * 60 + s.start.minute - target), s.start))
        else:
            candidates.sort(key=lambda s: s.start)
        chosen, seen = [], set()
        for s in candidates:
            if distinct_times and s.start in seen:
                continue
            seen.add(s.start)
            chosen.append(s)
            if len(chosen) >= limit:
                break
        return chosen

    for day in dates:
        t = _at(day, OPEN)
        while t < _at(day, CLOSE):
            if w_start <= t.time() < w_end and not _overlaps(busy, t, t + length):
                for doc in doctors:
                    if not ctx.reasons(doc["id"], svc["id"], svc["duration_min"], t, now, lead):
                        found.append(_slot(doc, svc, t))
            t += GRID
        # Earliest-first searches can stop as soon as they have enough.
        if near is None and near_time is None and len({s.start for s in found}) >= limit:
            break
    return pick(found)


def suggest(conn, *, service, date_c: DateConstraint, time_c: Optional[TimeConstraint] = None,
            branch_ids=None, doctor_id=None, gender=None, now=None, emergency=False, call_id=None,
            ignore_appointment=None, limit: int = 2, later_days: int = 7,
            same_time_later: bool = False, patient: Optional[tuple] = None) -> Suggestion:
    """
    What to offer for a caller's request (plan 5.1 "Offer policy"):
    the exact slot if it is free; otherwise the nearest slots that day; for a
    window or range, the earliest slots inside it; failing all that, the
    earliest slots on up to `later_days` following days, around the same time.

    same_time_later: the caller has asked for this exact day and time again
    after hearing the nearest times: the second offer becomes that time on
    the next day that has it ("5's taken on Tuesday, but Wednesday has 5").
    patient: (phone_e164, name_norm): never offer a time that patient is already booked at.
    """
    if time_c is not None and time_c.kind == "ambiguous":
        raise ValueError("resolve AM/PM with the caller before searching")
    now = _now(now)
    common = dict(service=service, branch_ids=branch_ids, doctor_id=doctor_id, gender=gender, now=now,
                  emergency=emergency, call_id=call_id, ignore_appointment=ignore_appointment, limit=limit,
                  patient=patient)
    exact_time = time_c.start if time_c is not None and time_c.kind == "exact" else None
    window = (time_c.start, time_c.end) if time_c is not None and time_c.kind == "window" else None
    dates = list(date_c.dates())

    if date_c.exact and exact_time is not None:
        day = date_c.start
        requested = _at(day, exact_time)
        svc = get_service(conn, service)
        doctors = _doctors(conn, svc["id"], branch_ids, doctor_id, gender) if svc else []
        reason_sets = []
        clash = bool(svc) and patient is not None and _overlaps(
            _patient_busy(conn, patient, day, day), requested, requested + timedelta(minutes=svc["duration_min"]))
        for doc in ([] if clash else doctors):
            why = validate(conn, service=svc["id"], doctor_id=doc["id"], start=requested, now=now,
                           emergency=emergency, call_id=call_id, ignore_appointment=ignore_appointment)
            if not why:
                return Suggestion("exact", [_slot(doc, svc, requested)], "same_day", requested)
            reason_sets.append(set(why))
        shared = set.intersection(*reason_sets) & CLINIC_WIDE if reason_sets else set()
        reasons = [r for r in REASON_ORDER if r in shared] or ["TAKEN"]
        slots = find_slots(conn, dates=[day], near=requested, **common)
        later = [day + timedelta(days=i) for i in range(1, later_days + 1)]
        if slots:
            if same_time_later and limit > 1:
                same_time =[s for s in find_slots(conn, dates=later, near_time=exact_time, **{**common, "limit": 1})
                             if s.start.time() == exact_time]
                if same_time:
                    slots = [slots[0], same_time[0]]
            return Suggestion("alternatives", slots, "same_day", requested, reasons)
        slots = find_slots(conn, dates=later, near_time=exact_time, **common)
        return Suggestion("alternatives" if slots else "none", slots, "later_days", requested, reasons,
                          searched_until=later[-1])

    if exact_time is not None:                       # a range of days at one time ("next week at 5")
        slots = find_slots(conn, dates=dates, near_time=exact_time, **common)
        if slots:
            kind = "exact" if slots[0].start.time() == exact_time else "alternatives"
            return Suggestion(kind, slots, "in_range")
    else:
        slots = find_slots(conn, dates=dates, window=window, **common)
        if slots:
            return Suggestion("alternatives", slots, "in_window" if window else "in_range")
        if window is not None:
            slots = find_slots(conn, dates=dates, near_time=window[0], **common)
            if slots:
                return Suggestion("alternatives", slots, "same_day_other_time")

    last = dates[-1] if dates else now.date()
    later = [last + timedelta(days=i) for i in range(1, later_days + 1)]
    slots = find_slots(conn, dates=later, window=window, near_time=exact_time, **common)
    if not slots and window is not None:
        slots = find_slots(conn, dates=later, near_time=window[0], **common)
    return Suggestion("alternatives" if slots else "none", slots, "later_days", searched_until=later[-1])


# ---------------------------------------------------------------- holds


def _purge_expired(conn, now: datetime):
    conn.execute("DELETE FROM slot_holds WHERE expires_at <= ?", (db.utc_str(now),))


def hold(conn, slot: Slot, call_id: str, *, now=None, emergency=False, ttl_s: Optional[int] = None) -> Optional[str]:
    """Hold an offered slot for this call; None if it is no longer free."""
    now = _now(now)
    ttl = config.HOLD_TTL_S if ttl_s is None else ttl_s
    hold_id = uuid.uuid4().hex
    try:
        with db.transaction(conn):
            _purge_expired(conn, now)
            svc = get_service(conn, slot.service_id)
            if validate(conn, service=svc["id"], doctor_id=slot.doctor_id, start=slot.start, now=now,
                        emergency=emergency, call_id=call_id):
                return None
            # Re-holding a slot this call already holds: replace the old hold.
            conn.execute("DELETE FROM slot_holds WHERE call_id = ? AND doctor_id = ? AND start_utc = ?",
                         (call_id, slot.doctor_id, db.utc_str(slot.start)))
            conn.execute("INSERT INTO slot_holds (id, doctor_id, service_id, start_utc, end_utc, call_id, expires_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (hold_id, slot.doctor_id, svc["id"], db.utc_str(slot.start), db.utc_str(slot.end), call_id,
                          db.utc_str(now + timedelta(seconds=ttl))))
            conn.executemany("INSERT INTO slot_claims (doctor_id, cell_start_utc, hold_id) VALUES (?, ?, ?)",
                             [(slot.doctor_id, db.utc_str(c), hold_id) for c in cells(slot.start, svc["duration_min"])])
    except sqlite3.IntegrityError:
        return None
    return hold_id


def release_holds(conn, call_id: str, keep: Iterable[str] = ()) -> int:
    keep = list(keep)
    with db.transaction(conn):
        sql = "DELETE FROM slot_holds WHERE call_id = ?"
        if keep:
            sql += f" AND id NOT IN ({','.join('?' * len(keep))})"
        return conn.execute(sql, [call_id] + keep).rowcount


def refresh_holds(conn, call_id: str, *, now=None, ttl_s: Optional[int] = None) -> int:
    now = _now(now)
    ttl = config.HOLD_TTL_S if ttl_s is None else ttl_s
    with db.transaction(conn):
        return conn.execute("UPDATE slot_holds SET expires_at = ? WHERE call_id = ? AND expires_at > ?",
                            (db.utc_str(now + timedelta(seconds=ttl)), call_id, db.utc_str(now))).rowcount


# ---------------------------------------------------------------- appointments


_APPOINTMENT_SQL = (
    "SELECT a.*, p.name AS patient_name, s.name AS service, s.duration_min, d.spoken_name AS doctor, "
    "b.name AS branch FROM appointments a JOIN patients p ON p.id = a.patient_id "
    "JOIN services s ON s.id = a.service_id JOIN doctors d ON d.id = a.doctor_id "
    "JOIN branches b ON b.id = a.branch_id"
)


def _row_dict(row: sqlite3.Row) -> dict:
    out = dict(row)
    out["start"] = db.local(row["start_utc"]).isoformat()
    out["end"] = db.local(row["end_utc"]).isoformat()
    return out


def get_appointment(conn, appointment_id: str) -> Optional[dict]:
    row = conn.execute(_APPOINTMENT_SQL + " WHERE a.id = ?", (appointment_id,)).fetchone()
    return _row_dict(row) if row else None


def future_appointments(conn, phone: str, *, now=None) -> list[dict]:
    """Booked appointments after now on this caller number, soonest first."""
    e164 = phones.to_e164(phone) or phone
    rows = conn.execute(_APPOINTMENT_SQL + " WHERE a.caller_phone_e164 = ? AND a.status = 'booked' "
                        "AND a.start_utc > ? ORDER BY a.start_utc", (e164, db.utc_str(_now(now)))).fetchall()
    return [_row_dict(r) for r in rows]


def _replay(conn, idem_key: str) -> Optional[Result]:
    row = conn.execute("SELECT result_json FROM actions WHERE idempotency_key = ?", (idem_key,)).fetchone()
    if row is None:
        return None
    result = Result(**json.loads(row["result_json"]))
    result.replayed = True
    return result


def _record(conn, idem_key: str, action: str, result: Result, now: datetime) -> Result:
    conn.execute("INSERT INTO actions (idempotency_key, action, result_json, created_at) VALUES (?, ?, ?, ?)",
                 (idem_key, action, json.dumps(asdict(result)), db.utc_str(now)))
    return result


def _audit(conn, *, actor, action, entity_id, before, after, correlation_id, now):
    conn.execute("INSERT INTO audit_events (ts, actor, action, entity, entity_id, before_json, after_json, correlation_id) "
                 "VALUES (?, ?, ?, 'appointment', ?, ?, ?, ?)",
                 (db.utc_str(now), actor, action, entity_id,
                  json.dumps(before) if before is not None else None,
                  json.dumps(after) if after is not None else None, correlation_id))


def _outbox(conn, appointment_id: str, now: datetime):
    """Tell the Calendar worker this appointment changed (it mirrors the current state)."""
    conn.execute("INSERT INTO sync_outbox (appointment_id, due_at, attempts, status) VALUES (?, ?, 0, 'pending') "
                 "ON CONFLICT (appointment_id) DO UPDATE SET due_at = excluded.due_at, attempts = 0, "
                 "status = 'pending', last_error = NULL", (appointment_id, db.utc_str(now)))


def _patient_busy(conn, patient: tuple, first: date, last: date) -> list:
    """[(start, end)] of the patient's booked appointments from `first` to `last` (local days)."""
    phone_e164, name_norm = patient
    lo, hi = db.utc_str(_at(first, time(0, 0))), db.utc_str(_at(last + timedelta(days=1), time(0, 0)))
    rows = conn.execute(
        "SELECT a.start_utc, a.end_utc FROM appointments a JOIN patients p ON p.id = a.patient_id "
        "WHERE p.phone_e164 = ? AND p.name_norm = ? AND a.status = 'booked' AND a.start_utc < ? AND a.end_utc > ?",
        (phone_e164, name_norm, hi, lo)).fetchall()
    return [(db.parse_utc(r["start_utc"]), db.parse_utc(r["end_utc"])) for r in rows]


def _overlaps(busy: list, start: datetime, end: datetime) -> bool:
    return any(b_start < end and b_end > start for b_start, b_end in busy)


def _patient_conflict(conn, phone_e164, name_norm, start, end, exclude_id=None) -> bool:
    row = conn.execute(
        "SELECT 1 FROM appointments a JOIN patients p ON p.id = a.patient_id "
        "WHERE p.phone_e164 = ? AND p.name_norm = ? AND a.status = 'booked' AND a.start_utc < ? AND a.end_utc > ? "
        "AND a.id IS NOT ?", (phone_e164, name_norm, db.utc_str(end), db.utc_str(start), exclude_id)).fetchone()
    return row is not None


def _claim(conn, doctor_id: int, start: datetime, duration: int, appointment_id: str, call_id):
    """Swap this call's holds on these cells for the appointment's own claims."""
    cell_keys = [db.utc_str(c) for c in cells(start, duration)]
    marks = ",".join("?" * len(cell_keys))
    if call_id is not None:
        conn.execute(f"DELETE FROM slot_holds WHERE call_id = ? AND id IN (SELECT hold_id FROM slot_claims "
                     f"WHERE doctor_id = ? AND cell_start_utc IN ({marks}) AND hold_id IS NOT NULL)",
                     [call_id, doctor_id] + cell_keys)
    conn.executemany("INSERT INTO slot_claims (doctor_id, cell_start_utc, appointment_id) VALUES (?, ?, ?)",
                     [(doctor_id, c, appointment_id) for c in cell_keys])


def book(conn, *, service, doctor_id: int, start: datetime, patient_name: str, phone: str, idem_key: str,
         caller_name: Optional[str] = None, source: str = "inbound", call_id: Optional[str] = None,
         patient_age: Optional[int] = None, name_unverified: bool = False, emergency: bool = False,
         actor: str = "emma", now=None) -> Result:
    """Create an appointment. Safe to retry with the same idem_key."""
    now = _now(now)
    start = clock.localize(start)
    try:
        with db.transaction(conn):
            replay = _replay(conn, idem_key)
            if replay:
                return replay
            _purge_expired(conn, now)
            e164 = phones.to_e164(phone)
            if e164 is None:
                return Result(False, "INVALID_PHONE")
            svc = get_service(conn, service)
            doc = conn.execute("SELECT d.*, b.name AS branch_name FROM doctors d JOIN branches b ON b.id = d.branch_id "
                               "WHERE d.id = ?", (doctor_id,)).fetchone()
            if svc is None or doc is None:
                return Result(False, "UNKNOWN_SERVICE" if svc is None else "UNKNOWN_DOCTOR")
            why = validate(conn, service=svc["id"], doctor_id=doctor_id, start=start, now=now,
                           emergency=emergency, call_id=call_id)
            if why:
                return Result(False, why[0])
            end = start + timedelta(minutes=svc["duration_min"])
            name_n = norm_name(patient_name)
            if _patient_conflict(conn, e164, name_n, start, end):
                return Result(False, "PATIENT_CONFLICT")
            future = conn.execute("SELECT COUNT(*) FROM appointments WHERE caller_phone_e164 = ? AND status = 'booked' "
                                  "AND start_utc > ?", (e164, db.utc_str(now))).fetchone()[0]
            if future >= config.MAX_FUTURE_APPOINTMENTS_PER_PHONE:
                return Result(False, "MAX_FUTURE")

            conn.execute("INSERT OR IGNORE INTO patients (name, name_norm, phone_e164, created_at) VALUES (?, ?, ?, ?)",
                         (patient_name.strip(), name_n, e164, db.utc_str(now)))
            patient_id = conn.execute("SELECT id FROM patients WHERE phone_e164 = ? AND name_norm = ?",
                                      (e164, name_n)).fetchone()[0]
            appointment_id = uuid.uuid4().hex
            stamp = db.utc_str(now)
            conn.execute(
                "INSERT INTO appointments (id, patient_id, caller_name, caller_phone_e164, service_id, doctor_id, branch_id, "
                "start_utc, end_utc, status, source, name_unverified, patient_age, created_by_call_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'booked', ?, ?, ?, ?, ?, ?)",
                (appointment_id, patient_id, caller_name, e164, svc["id"], doctor_id, doc["branch_id"],
                 db.utc_str(start), db.utc_str(end), source, int(name_unverified), patient_age, call_id, stamp, stamp))
            _claim(conn, doctor_id, start, svc["duration_min"], appointment_id, call_id)
            after = get_appointment(conn, appointment_id)
            _audit(conn, actor=actor, action="book", entity_id=appointment_id, before=None, after=after,
                   correlation_id=call_id, now=now)
            _outbox(conn, appointment_id, now)
            return _record(conn, idem_key, "book", Result(True, "OK", appointment_id, after), now)
    except sqlite3.IntegrityError:
        return Result(False, "TAKEN")


def _load_for_change(conn, appointment_id, expected_version, now, enforce_notice, emergency=False):
    """(appointment row dict, error code or None)."""
    appt = get_appointment(conn, appointment_id)
    if appt is None:
        return None, "NOT_FOUND"
    if appt["status"] not in ("booked", "needs_reschedule"):
        return appt, "NOT_ACTIVE"
    if expected_version is not None and appt["version"] != expected_version:
        return appt, "STALE"
    start = db.local(appt["start_utc"])
    if start <= now:
        return appt, "TOO_LATE"
    if enforce_notice and start < now + timedelta(minutes=_lead(emergency)):
        return appt, "TOO_LATE"
    return appt, None


def reschedule(conn, appointment_id: str, *, doctor_id: int, start: datetime, idem_key: str,
               expected_version: Optional[int] = None, call_id: Optional[str] = None, actor: str = "emma",
               enforce_notice: bool = True, now=None) -> Result:
    """
    Move an appointment (same service) to a new doctor/time, all or nothing:
    if anything fails, the original booking is exactly as it was.
    """
    now = _now(now)
    start = clock.localize(start)
    try:
        with db.transaction(conn):
            replay = _replay(conn, idem_key)
            if replay:
                return replay
            _purge_expired(conn, now)
            before, error = _load_for_change(conn, appointment_id, expected_version, now, enforce_notice)
            if error:
                return Result(False, error, appointment_id, before)
            if before["doctor_id"] == doctor_id and db.local(before["start_utc"]) == start:
                return Result(False, "SAME_SLOT", appointment_id, before)
            why = validate(conn, service=before["service_id"], doctor_id=doctor_id, start=start, now=now,
                           call_id=call_id, ignore_appointment=appointment_id)
            if why:
                return Result(False, why[0], appointment_id, before)
            end = start + timedelta(minutes=before["duration_min"])
            name_n = norm_name(before["patient_name"])
            if _patient_conflict(conn, before["caller_phone_e164"], name_n, start, end, exclude_id=appointment_id):
                return Result(False, "PATIENT_CONFLICT", appointment_id, before)
            branch_id = conn.execute("SELECT branch_id FROM doctors WHERE id = ?", (doctor_id,)).fetchone()[0]
            conn.execute("DELETE FROM slot_claims WHERE appointment_id = ?", (appointment_id,))
            _claim(conn, doctor_id, start, before["duration_min"], appointment_id, call_id)
            conn.execute("UPDATE appointments SET doctor_id = ?, branch_id = ?, start_utc = ?, end_utc = ?, "
                         "status = 'booked', affected_by_block_id = NULL, version = version + 1, updated_at = ? "
                         "WHERE id = ?", (doctor_id, branch_id, db.utc_str(start), db.utc_str(end),
                                          db.utc_str(now), appointment_id))
            after = get_appointment(conn, appointment_id)
            _audit(conn, actor=actor, action="reschedule", entity_id=appointment_id, before=before, after=after,
                   correlation_id=call_id, now=now)
            _outbox(conn, appointment_id, now)
            return _record(conn, idem_key, "reschedule", Result(True, "OK", appointment_id, after), now)
    except sqlite3.IntegrityError:
        return Result(False, "TAKEN", appointment_id)


def cancel(conn, appointment_id: str, *, idem_key: str, reason: Optional[str] = None,
           expected_version: Optional[int] = None, call_id: Optional[str] = None, actor: str = "emma",
           now=None) -> Result:
    """Cancel before the appointment starts (no fee, Q8). Frees its slot."""
    now = _now(now)
    with db.transaction(conn):
        replay = _replay(conn, idem_key)
        if replay:
            return replay
        before, error = _load_for_change(conn, appointment_id, expected_version, now, enforce_notice=False)
        if error == "NOT_ACTIVE" and before["status"] == "cancelled":
            return Result(True, "ALREADY_CANCELLED", appointment_id, before)
        if error:
            return Result(False, error, appointment_id, before)
        conn.execute("DELETE FROM slot_claims WHERE appointment_id = ?", (appointment_id,))
        conn.execute("UPDATE appointments SET status = 'cancelled', cancel_reason = ?, version = version + 1, "
                     "updated_at = ? WHERE id = ?", ((reason or "").strip() or None, db.utc_str(now), appointment_id))
        after = get_appointment(conn, appointment_id)
        _audit(conn, actor=actor, action="cancel", entity_id=appointment_id, before=before, after=after,
               correlation_id=call_id, now=now)
        _outbox(conn, appointment_id, now)
        return _record(conn, idem_key, "cancel", Result(True, "OK", appointment_id, after), now)
