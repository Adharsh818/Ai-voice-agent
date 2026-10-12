"""
Doctors and their weekly hours, edited from the dashboard (the audit, 11 Oct,
S3 "config without code"). Every function runs on the database thread.

A rota is, per weekday (Monday = 0), a list of shifts ("09:00", "13:00"): what
availability_rules holds, so split shifts survive. Shifts sit on the 30-minute
booking grid inside the clinic's day (scheduling.OPEN to CLOSE).

Changing hours never moves or cancels a booking: the bookings that fall
outside the new hours are returned, for staff to move (Recovery tab), as with
closures. The catalog Emma speaks from is reloaded at once (facts.clear_cache).
"""

import json
import re
from datetime import datetime, timedelta
from typing import Optional

import clock
import db
import scheduling

_SHIFT = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*(?:-|–|to)\s*(\d{1,2})(?::(\d{2}))?\s*$")
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class AdminError(ValueError):
    """A request the dashboard shows back to staff as is."""


def _hhmm(h: str, m: Optional[str]) -> str:
    hour, minute = int(h), int(m or 0)
    if hour > 23:
        raise AdminError(f"{hour} isn't an hour of the day; use the 24-hour clock, like 09:00 or 20:00.")
    if minute not in (0, 30):
        raise AdminError("Times go in half hours, like 09:00 or 16:30.")
    return f"{hour:02d}:{minute:02d}"


def parse_day(text: str) -> list:
    """ "09:00-13:00, 16:00-20:00" -> [("09:00", "13:00"), ("16:00", "20:00")]; "" or "off" -> []."""
    text = (text or "").strip().lower()
    if text in ("", "off", "closed", "-"):
        return []
    shifts = []
    for part in text.split(","):
        m = _SHIFT.match(part)
        if not m:
            raise AdminError(f"Couldn't read \"{part.strip()}\". Write shifts like 09:00-13:00, 16:00-20:00.")
        shifts.append((_hhmm(m.group(1), m.group(2)), _hhmm(m.group(3), m.group(4))))
    return check_shifts(shifts)


def check_shifts(shifts: list) -> list:
    opening, closing = scheduling.OPEN.strftime("%H:%M"), scheduling.CLOSE.strftime("%H:%M")
    shifts = sorted((str(a), str(b)) for a, b in shifts)
    for start, end in shifts:
        if start >= end:
            raise AdminError(f"{start}-{end}: a shift must end after it starts.")
        if start < opening or end > closing:
            raise AdminError(f"{start}-{end}: the clinic's day is {opening} to {closing}.")
    for (_, end), (start, _) in zip(shifts, shifts[1:]):
        if start < end:
            raise AdminError("Two shifts on one day overlap.")
    return shifts


def _rota(conn, doctor_id: int) -> dict:
    out = {wd: [] for wd in range(7)}
    for r in conn.execute("SELECT weekday, start_time, end_time FROM availability_rules WHERE doctor_id = ? "
                          "ORDER BY weekday, start_time", (doctor_id,)):
        out[r["weekday"]].append((r["start_time"], r["end_time"]))
    return out


def rota_text(shifts: list) -> str:
    return ", ".join(f"{a}-{b}" for a, b in shifts) if shifts else "off"


def list_doctors(conn) -> dict:
    """Every doctor (inactive ones too) with branch, services and weekly hours; and what can be chosen."""
    services = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM services WHERE active = 1 ORDER BY id")}
    does: dict = {}
    for r in conn.execute("SELECT doctor_id, service_id FROM doctor_services"):
        if r["service_id"] in services:
            does.setdefault(r["doctor_id"], []).append(services[r["service_id"]])
    doctors = []
    for r in conn.execute("SELECT d.id, d.name, d.spoken_name, d.gender, d.active, d.branch_id, b.name AS branch "
                          "FROM doctors d JOIN branches b ON b.id = d.branch_id ORDER BY d.active DESC, b.id, d.id"):
        rota = _rota(conn, r["id"])
        doctors.append({**dict(r), "active": bool(r["active"]), "services": does.get(r["id"], []),
                        "rota": {DAYS[wd]: rota_text(rota[wd]) for wd in range(7)}})
    branches = [r["name"] for r in conn.execute("SELECT name FROM branches WHERE active = 1 ORDER BY id")]
    return {"doctors": doctors, "branches": branches, "services": list(services.values()), "days": list(DAYS),
            "day_hours": [scheduling.OPEN.strftime("%H:%M"), scheduling.CLOSE.strftime("%H:%M")]}


def _audit(conn, actor: str, action: str, doctor_id: int, before, after):
    conn.execute("INSERT INTO audit_events (ts, actor, action, entity, entity_id, before_json, after_json) "
                 "VALUES (?, ?, ?, 'doctor', ?, ?, ?)",
                 (db.now_str(), actor, action, str(doctor_id),
                  json.dumps(before) if before is not None else None, json.dumps(after) if after is not None else None))


def _doctor(conn, doctor_id: int):
    row = conn.execute("SELECT * FROM doctors WHERE id = ?", (doctor_id,)).fetchone()
    if row is None:
        raise AdminError("No such doctor.")
    return row


def _booked_outside(conn, doctor_id: int, rota: Optional[dict], now: datetime) -> list:
    """Upcoming bookings with this doctor that the hours `rota` (None: no hours at all) no longer cover."""
    out = []
    for a in conn.execute(scheduling._APPOINTMENT_SQL + " WHERE a.doctor_id = ? AND a.status = 'booked' "
                          "AND a.start_utc > ? ORDER BY a.start_utc", (doctor_id, db.utc_str(now))):
        a = dict(a)
        start, end = db.local(a["start_utc"]), db.local(a["end_utc"])
        shifts = (rota or {}).get(start.weekday(), [])
        fits = any(s <= start.strftime("%H:%M") and end.strftime("%H:%M") <= e for s, e in shifts)
        if not fits:
            out.append(scheduling._row_dict_safe(a))
    return out


def set_rota(conn, doctor_id: int, days: dict, actor: str, now=None) -> dict:
    """
    Replace a doctor's weekly hours. `days`: {"Mon": "09:00-13:00, 16:00-20:00", "Sun": "off", ...};
    a day left out keeps its hours. Returns the new rota and the bookings now outside it.
    """
    now = now or clock.now()
    _doctor(conn, doctor_id)
    before = _rota(conn, doctor_id)
    after = {wd: list(shifts) for wd, shifts in before.items()}
    for name, text in (days or {}).items():
        if name not in DAYS:
            raise AdminError(f"Unknown day {name!r}.")
        after[DAYS.index(name)] = parse_day(str(text))
    with db.transaction(conn):
        conn.execute("DELETE FROM availability_rules WHERE doctor_id = ?", (doctor_id,))
        conn.executemany("INSERT INTO availability_rules (doctor_id, weekday, start_time, end_time) VALUES (?, ?, ?, ?)",
                         [(doctor_id, wd, a, b) for wd, shifts in after.items() for a, b in shifts])
        _audit(conn, actor, "rota_set", doctor_id, {DAYS[wd]: rota_text(s) for wd, s in before.items()},
               {DAYS[wd]: rota_text(s) for wd, s in after.items()})
    _refresh()
    return {"rota": {DAYS[wd]: rota_text(s) for wd, s in after.items()},
            "affected": _booked_outside(conn, doctor_id, after, now)}


def add_doctor(conn, *, name: str, spoken_name: str, gender: Optional[str], branch: str, services: list,
               days: dict, actor: str) -> dict:
    name, spoken = (name or "").strip(), (spoken_name or "").strip()
    if not name:
        raise AdminError("The doctor needs a name.")
    # What Emma says: "Dr Shetty", or the full name when another doctor is already "Dr Shetty"
    # (callers asking for "Dr Shetty" must mean one person).
    taken = {r[0].lower() for r in conn.execute("SELECT spoken_name FROM doctors WHERE active = 1")}
    bare = re.sub(r"^\s*dr\.?\s+", "", name, flags=re.I).strip()
    if spoken and spoken.lower() in taken:
        raise AdminError(f"Another doctor is already called {spoken}; give this one a different spoken name.")
    if not spoken:
        spoken = f"Dr {bare.split()[-1]}" if bare.split() else f"Dr {bare}"
        if spoken.lower() in taken:
            spoken = f"Dr {bare}"
        if spoken.lower() in taken:
            raise AdminError(f"Another doctor is already called {spoken}; give this one a spoken name.")
    if gender not in (None, "", "female", "male", "other"):
        raise AdminError("Gender is female, male or other.")
    row = conn.execute("SELECT id FROM branches WHERE name = ? AND active = 1", (branch,)).fetchone()
    if row is None:
        raise AdminError("Choose a branch.")
    branch_id = row[0]
    service_ids = _service_ids(conn, services)
    rota = {DAYS.index(d): parse_day(str(t)) for d, t in (days or {}).items() if d in DAYS}
    with db.transaction(conn):
        if conn.execute("SELECT 1 FROM doctors WHERE name = ? AND branch_id = ?", (name, branch_id)).fetchone():
            raise AdminError("That doctor is already at that branch.")
        doctor_id = conn.execute("INSERT INTO doctors (name, spoken_name, gender, branch_id) VALUES (?, ?, ?, ?)",
                                 (name, spoken, gender or None, branch_id)).lastrowid
        conn.executemany("INSERT INTO doctor_services (doctor_id, service_id) VALUES (?, ?)",
                         [(doctor_id, s) for s in service_ids])
        conn.executemany("INSERT INTO availability_rules (doctor_id, weekday, start_time, end_time) VALUES (?, ?, ?, ?)",
                         [(doctor_id, wd, a, b) for wd, shifts in rota.items() for a, b in shifts])
        _audit(conn, actor, "doctor_add", doctor_id, None,
               {"name": name, "branch": branch, "services": list(services or [])})
    _refresh()
    return {"id": doctor_id, "spoken_name": spoken}


def _service_ids(conn, names: list) -> list:
    known = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM services WHERE active = 1")}
    unknown = [n for n in names or [] if n not in known]
    if unknown:
        raise AdminError(f"Unknown service: {', '.join(unknown)}.")
    if not names:
        raise AdminError("Choose at least one service the doctor does.")
    return [known[n] for n in names]


def set_services(conn, doctor_id: int, services: list, actor: str) -> dict:
    _doctor(conn, doctor_id)
    ids = _service_ids(conn, services)
    before = [r[0] for r in conn.execute("SELECT s.name FROM doctor_services ds JOIN services s ON s.id = ds.service_id "
                                         "WHERE ds.doctor_id = ? ORDER BY s.id", (doctor_id,))]
    with db.transaction(conn):
        conn.execute("DELETE FROM doctor_services WHERE doctor_id = ?", (doctor_id,))
        conn.executemany("INSERT INTO doctor_services (doctor_id, service_id) VALUES (?, ?)",
                         [(doctor_id, s) for s in ids])
        _audit(conn, actor, "services_set", doctor_id, before, list(services))
    _refresh()
    return {"services": list(services)}


def set_active(conn, doctor_id: int, active: bool, actor: str, now=None) -> dict:
    """Take a doctor off (left the clinic) or back on. Refused while they have upcoming bookings."""
    now = now or clock.now()
    row = _doctor(conn, doctor_id)
    if not active:
        upcoming = _booked_outside(conn, doctor_id, None, now)
        if upcoming:
            raise AdminError(f"{row['spoken_name']} still has {len(upcoming)} upcoming booking(s). Block the doctor "
                             "on the Recovery tab so Emma moves them, then switch them off.")
    with db.transaction(conn):
        conn.execute("UPDATE doctors SET active = ? WHERE id = ?", (int(bool(active)), doctor_id))
        _audit(conn, actor, "doctor_on" if active else "doctor_off", doctor_id, None, None)
    _refresh()
    return {"active": bool(active)}


def _refresh():
    """Emma's catalog (who does what, when) is rebuilt on the next turn."""
    import facts
    facts.clear_cache()
