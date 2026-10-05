"""
An isolated clinic for one conversation.

Each conversation gets its own SQLite file seeded with the DEMO clinic
(seed_demo.seed: 4 branches, 8 doctors, 9 services, about 40 bookings),
config.DB_PATH pointed at it, and the clinic clock frozen at a chosen moment
(default Thursday 1 Oct 2026, 10:00 IST), so "tomorrow" and the 2-hour lead
time mean the same thing in every run. The DB swap follows tests/support.py
TempClinic: db.reset() before and after, DEMO_SEED_ON_EMPTY off while inside.

Seeding goes through scheduling.book() for every sample appointment, so it is
done once per (moment, process) into a template file and copied with SQLite's
backup API for each world. Worlds are safe one after another in one process,
and in separate processes in parallel (every file lives in its own temp dir).

    with World() as w:
        baseline = w.snapshot()
        ...                      # run the call
        outcome = w.outcome(baseline)
"""

import atexit
import os
import shutil
import sqlite3
import tempfile
from datetime import date, datetime, timedelta
from typing import Optional

import clock
import config
import db
import phones
import seed_demo

DEFAULT_NOW = datetime(2026, 10, 1, 10, 0)     # Thursday, clinic-local

_templates: dict[str, str] = {}
_template_dir: Optional[str] = None


def parse_now(value) -> datetime:
    """A naive clinic-local datetime from None, a datetime or an ISO string."""
    if value is None:
        return DEFAULT_NOW
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is None else clock.localize(value).replace(tzinfo=None)
    return parse_now(datetime.fromisoformat(str(value)))


def _cleanup_templates():
    if _template_dir and os.path.isdir(_template_dir):
        shutil.rmtree(_template_dir, ignore_errors=True)


def _template(now: datetime) -> str:
    """A seeded DB file for `now`, built once per process."""
    global _template_dir
    key = now.isoformat()
    path = _templates.get(key)
    if path and os.path.exists(path):
        return path
    if _template_dir is None:
        _template_dir = tempfile.mkdtemp(prefix="emma-harness-tpl-")
        atexit.register(_cleanup_templates)
    path = os.path.join(_template_dir, f"template-{len(_templates)}.db")
    conn = db.connect(path)
    try:
        db.migrate(conn)
        with clock.frozen(now):
            seed_demo.seed(conn, now=now)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    _templates[key] = path
    return path


def _copy_db(src: str, dst: str):
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    source = sqlite3.connect(src)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


class World:
    """
    Point the app at a seeded clinic and freeze the clock; undo both on exit.

    `path` keeps the database somewhere persistent (tools/converse.py resumes a
    conversation across processes); without it a temp dir is used and removed.
    `seed=False` opens an existing file as it is.
    """

    def __init__(self, now=None, *, path: Optional[str] = None, seed: bool = True):
        self.now = parse_now(now)
        self.path = path
        self.seed = seed
        self._tmp: Optional[tempfile.TemporaryDirectory] = None
        self._frozen = None
        self._saved = None

    # -- context -------------------------------------------------------------
    def __enter__(self) -> "World":
        self._saved = (config.DB_PATH, config.DEMO_SEED_ON_EMPTY)
        db.reset()
        if self.path is None:
            self._tmp = tempfile.TemporaryDirectory(prefix="emma-world-", ignore_cleanup_errors=True)
            self.path = os.path.join(self._tmp.name, "emma.db")
        if self.seed and not os.path.exists(self.path):
            _copy_db(_template(self.now), self.path)
        config.DB_PATH = self.path
        config.DEMO_SEED_ON_EMPTY = False
        self._frozen = clock.frozen(self.now)
        self._frozen.__enter__()
        self.db = db.get_db()
        return self

    def __exit__(self, *exc):
        try:
            db.reset()
        finally:
            if self._frozen is not None:
                self._frozen.__exit__(*exc)
            config.DB_PATH, config.DEMO_SEED_ON_EMPTY = self._saved
            if self._tmp is not None:
                self._tmp.cleanup()
        return False

    def run(self, fn, *args, **kwargs):
        """fn(conn, ...) on the app's database thread."""
        return db.get_db().run_sync(fn, *args, **kwargs)

    # -- reading the clinic --------------------------------------------------
    def catalog(self) -> dict:
        """Branches, doctors and which services each branch offers (for Z4 / Z6)."""
        def read(conn):
            branches = [r["name"] for r in conn.execute("SELECT name FROM branches WHERE active = 1 ORDER BY id")]
            services = [r["name"] for r in conn.execute("SELECT name FROM services WHERE active = 1 ORDER BY id")]
            doctors = []
            for r in conn.execute("SELECT d.id, d.name, d.spoken_name, b.name AS branch FROM doctors d "
                                  "JOIN branches b ON b.id = d.branch_id WHERE d.active = 1 ORDER BY d.id"):
                offered = [x["name"] for x in conn.execute(
                    "SELECT s.name FROM services s JOIN doctor_services ds ON ds.service_id = s.id "
                    "WHERE ds.doctor_id = ? ORDER BY s.id", (r["id"],))]
                doctors.append({"name": r["name"], "spoken": r["spoken_name"], "branch": r["branch"],
                                "services": offered})
            branch_services = {b: sorted({s for d in doctors if d["branch"] == b for s in d["services"]})
                               for b in branches}
            patients = [r["name"] for r in conn.execute("SELECT name FROM patients ORDER BY id")]
            return {"branches": branches, "services": services, "doctors": doctors,
                    "branch_services": branch_services, "patients": patients}
        return self.run(read)

    def snapshot(self) -> dict:
        return self.run(_snapshot)

    def outcome(self, baseline: dict) -> dict:
        """What the call changed in the database since `baseline` (a snapshot())."""
        return self.run(_outcome, baseline)

    def appointments(self) -> list[dict]:
        return self.run(_appointments)

    def pick_card(self, index: Optional[int] = None, rng=None) -> Optional[dict]:
        """
        A real seeded appointment a caller can cancel or reschedule: at least a
        day ahead, the only future booking on its phone number. `index` or
        `rng` chooses among them deterministically.
        """
        rows = [a for a in self.appointments() if a["status"] == "booked"]
        start_min = clock.now().replace(tzinfo=None) + timedelta(days=1)
        by_phone: dict[str, int] = {}
        for a in rows:
            by_phone[a["phone"]] = by_phone.get(a["phone"], 0) + 1
        choices = [a for a in rows if datetime.fromisoformat(a["start"]) >= start_min and by_phone[a["phone"]] == 1]
        if not choices:
            return None
        if index is None:
            index = rng.randrange(len(choices)) if rng is not None else 0
        return card_for(choices[index % len(choices)])


_APPT_SQL = (
    "SELECT a.id, a.status, a.start_utc, a.end_utc, a.version, a.source, a.caller_phone_e164 AS phone, "
    "a.caller_name, p.name AS patient, s.name AS service, d.spoken_name AS doctor, d.name AS doctor_full, "
    "b.name AS branch FROM appointments a JOIN patients p ON p.id = a.patient_id "
    "JOIN services s ON s.id = a.service_id JOIN doctors d ON d.id = a.doctor_id "
    "JOIN branches b ON b.id = a.branch_id ORDER BY a.start_utc, a.id"
)


def _appt_dict(row) -> dict:
    out = dict(row)
    out["start"] = db.local(row["start_utc"]).replace(tzinfo=None).isoformat()
    out["end"] = db.local(row["end_utc"]).replace(tzinfo=None).isoformat()
    return out


def _appointments(conn) -> list[dict]:
    return [_appt_dict(r) for r in conn.execute(_APPT_SQL)]


def _snapshot(conn) -> dict:
    appts = {a["id"]: {"status": a["status"], "start": a["start"], "doctor": a["doctor"], "branch": a["branch"],
                       "version": a["version"]} for a in _appointments(conn)}
    return {"appointments": appts, "task_ids": [t["id"] for t in _tasks(conn)]}


def _tasks(conn) -> list[dict]:
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, kind, priority, phone_e164, note, status, call_id FROM tasks ORDER BY id")]
    except sqlite3.Error:
        return []


def current_snapshot() -> dict:
    """snapshot() of whatever database config.DB_PATH points at (the adapter's per-turn view)."""
    return db.get_db().run_sync(_snapshot)


def changes_since(before: dict) -> dict:
    """diff() between `before` and the database as it is now."""
    return db.get_db().run_sync(lambda conn: diff(before, _appointments(conn), _tasks(conn)))


def diff(before: dict, after_appts: list[dict], after_tasks: list[dict]) -> dict:
    """booked / cancelled / rescheduled appointments and new tasks between two states."""
    old = before.get("appointments", {})
    booked, cancelled, rescheduled = [], [], []
    for a in after_appts:
        prev = old.get(a["id"])
        if prev is None:
            if a["status"] == "booked":
                booked.append(a)
            continue
        if prev["status"] != "cancelled" and a["status"] == "cancelled":
            cancelled.append(a)
        elif a["status"] == "booked" and (prev["start"] != a["start"] or prev["doctor"] != a["doctor"]):
            rescheduled.append({"before": prev, "after": a})
    known = set(before.get("task_ids", []))
    tasks = [t for t in after_tasks if t["id"] not in known]
    return {"booked": booked, "cancelled": cancelled, "rescheduled": rescheduled, "tasks": tasks}


def _outcome(conn, baseline: dict) -> dict:
    out = diff(baseline, _appointments(conn), _tasks(conn))
    kinds = [k for k in ("booked", "cancelled", "rescheduled") if out[k]]
    out["kind"] = kinds[0] if len(kinds) == 1 else ("mixed" if kinds else "none")
    return out


def card_for(appt: dict) -> dict:
    """The private caller card for an existing appointment."""
    start = datetime.fromisoformat(appt["start"])
    national = phones.national(appt["phone"])
    return {
        "appointment_id": appt["id"],
        "patient": appt["patient"],
        "phone": national,
        "phone_spoken": f"{national[:5]} {national[5:]}",
        "date": start.date().isoformat(),
        "time": start.strftime("%H:%M"),
        "date_spoken": f"{start:%A} {start.day} {start:%B}",
        "time_spoken": _spoken_time(start),
        "service": appt["service"],
        "branch": appt["branch"],
        "doctor": appt["doctor"],
    }


def _spoken_time(dt: datetime) -> str:
    h = dt.hour % 12 or 12
    suffix = "am" if dt.hour < 12 else "pm"
    return f"{h}:{dt.minute:02d} {suffix}" if dt.minute else f"{h} {suffix}"


def weekday_date(today: date, weekday: int, weeks_ahead: int = 0) -> date:
    """The next `weekday` after today (plus whole weeks)."""
    ahead = (weekday - today.weekday()) % 7 or 7
    return today + timedelta(days=ahead + 7 * weeks_ahead)
