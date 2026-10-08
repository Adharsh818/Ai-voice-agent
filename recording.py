"""
Call records and transcripts (plan 5.8). No audio is ever recorded (decision R3).

One CallRecorder per call writes a `calls` row and one `call_turns` row per
caller or Emma turn:

    rec = CallRecorder(call_id)            # direction="outbound" for recovery calls
    rec.start()
    rec.turn("caller", "I'd like a cleaning on Monday")
    rec.turn("emma", "Sure, Monday works. Morning or evening?",
             {"tier": 1, "goal_before": "service", "goal_after": "time", "latency": {...}})
    rec.end("booked")                      # keep_transcript=False blanks the text

The recorder is called from the live call, so it never blocks and never
raises: writes are queued onto the database thread in order, and any failure
is logged (without the text) and swallowed. The transcript stays in SQLite and
on the dashboard only; it is never written to a log file.

Retention: purge() blanks transcript text (and the extracted details, which
hold names and numbers) for calls older than TRANSCRIPT_RETENTION_DAYS. It runs
at startup and every TRANSCRIPT_PURGE_HOURS (purge_loop). Call rows, outcomes,
tiers and latencies stay: they carry no personal text. Staff can blank one
call on request (delete_call_data, audited), and a caller who asks not to be
kept has their transcript blanked when the call ends.
"""

import asyncio
import json
import logging
from contextlib import contextmanager
from datetime import timedelta
from typing import Optional

import clock
import config
import db
import events
import phones

logger = logging.getLogger(__name__)

ROLES = ("caller", "emma", "operator")
# The engine's history uses chat roles; accept those too.
_ROLE_ALIASES = {"user": "caller", "assistant": "emma", "staff": "operator"}
# meta keys stored in their own columns; everything else goes into entities_json.
_COLUMN_KEYS = {"tier", "goal_before", "goal_after", "state_before", "state_after", "step_before",
                "step_after", "latency"}

_last_purge: dict = {"at": None, "turns": 0}


@contextmanager
def _write(conn):
    """Join the caller's open transaction, or run in one of our own."""
    if conn.in_transaction:
        yield conn
    else:
        with db.transaction(conn):
            yield conn


def _json(value) -> Optional[str]:
    if value in (None, {}, []):
        return None
    return json.dumps(value, default=str, ensure_ascii=False)


# ---------------------------------------------------------------- database steps (db thread)
def _insert_call(conn, call_id, direction, started_at, purge_after):
    with _write(conn):
        conn.execute("INSERT OR IGNORE INTO calls (id, direction, started_at, recording_consent, purge_after) "
                     "VALUES (?, ?, ?, 1, ?)", (call_id, direction, started_at, purge_after))


def _insert_turn(conn, call_id, turn, role, text, tier, before, after, entities, latency, ts):
    with _write(conn):
        conn.execute("INSERT OR REPLACE INTO call_turns (call_id, turn, role, text, tier, state_before, state_after, "
                     "entities_json, latency_json, ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (call_id, turn, role, text, tier, before, after, entities, latency, ts))


def _finish_call(conn, call_id, ended_at, outcome, keep, workflow, phone):
    with _write(conn):
        conn.execute("UPDATE calls SET ended_at = COALESCE(ended_at, ?), outcome = ?, recording_consent = ?, "
                     "workflow = COALESCE(?, workflow), caller_phone_e164 = COALESCE(?, caller_phone_e164) "
                     "WHERE id = ?", (ended_at, outcome, int(keep), workflow, phone, call_id))
        if not keep:
            conn.execute("UPDATE call_turns SET text = NULL, entities_json = NULL WHERE call_id = ?", (call_id,))
    return None


def _guarded(conn, fn, call_id, *args):
    try:
        return fn(conn, call_id, *args)
    except Exception as exc:
        # Type only: the message could quote transcript text.
        logger.warning("[%s] transcript write failed (%s)", call_id, type(exc).__name__)
        return None


class CallRecorder:
    """Writes one call's record and transcript. Every method is safe to call from the live call."""

    def __init__(self, call_id: str, direction: str = "inbound"):
        self.call_id = call_id
        self.direction = direction if direction in ("inbound", "outbound") else "inbound"
        self.started = False
        self.ended = False
        self.keep_transcript = True
        self.turns_recorded = 0
        self._turn = -1
        self._roles_at_turn: set = set()
        self._pending: set = set()

    # ------------------------------------------------------------ queueing
    def _submit(self, fn, *args):
        try:
            database = db.get_db()
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is None:
                database.run_sync(_guarded, fn, self.call_id, *args)
                return
            # Database.run submits to its single thread as soon as the task first
            # runs, and tasks start in creation order, so writes keep their order.
            task = loop.create_task(database.run(_guarded, fn, self.call_id, *args))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)
        except Exception as exc:
            logger.warning("[%s] transcript write not queued (%s)", self.call_id, type(exc).__name__)

    async def flush(self):
        """Wait for every queued write (tests, and before reading the call back)."""
        while self._pending:
            await asyncio.gather(*list(self._pending), return_exceptions=True)

    # ------------------------------------------------------------ the call
    def start(self):
        """Open the call record. Safe to call twice."""
        if self.started:
            return
        self.started = True
        try:
            now = clock.now()
            purge_after = db.utc_str(now + timedelta(days=config.TRANSCRIPT_RETENTION_DAYS))
            self._submit(_insert_call, self.direction, db.utc_str(now), purge_after)
            events.publish({"type": "call_started", "call_id": self.call_id, "direction": self.direction})
        except Exception as exc:
            logger.warning("[%s] call record not started (%s)", self.call_id, type(exc).__name__)

    def _next_turn(self, role: str) -> int:
        # A caller turn opens a new exchange; Emma's reply shares its number.
        # A role speaking twice in one exchange (a silence prompt) opens another.
        if self._turn < 0 or role == "caller" or role in self._roles_at_turn:
            self._turn += 1
            self._roles_at_turn = set()
        self._roles_at_turn.add(role)
        return self._turn

    def turn(self, role: str, text: str, meta: Optional[dict] = None):
        """Store one turn. `meta` may carry tier, goal_before/goal_after, latency, entities, action."""
        try:
            role = _ROLE_ALIASES.get(role, role)
            if role not in ROLES:
                logger.warning("[%s] ignored transcript turn with role %r", self.call_id, role)
                return
            if not self.started:
                self.start()
            meta = dict(meta or {})
            number = self._next_turn(role)
            keep = self.keep_transcript
            text = (text or "").strip() if keep else None
            tier = meta.get("tier")
            before = meta.get("goal_before", meta.get("state_before", meta.get("step_before")))
            after = meta.get("goal_after", meta.get("state_after", meta.get("step_after")))
            extra = {k: v for k, v in meta.items() if k not in _COLUMN_KEYS and v is not None}
            self._submit(_insert_turn, number, role, text,
                         tier if isinstance(tier, int) else None,
                         None if before is None else str(before), None if after is None else str(after),
                         _json(extra) if keep else None, _json(meta.get("latency")), db.now_str())
            self.turns_recorded += 1
            events.publish({"type": "call_turn", "call_id": self.call_id, "turn": number, "role": role,
                            "text": text, "meta": json.loads(_json(meta) or "{}")})
        except Exception as exc:
            logger.warning("[%s] transcript turn not stored (%s)", self.call_id, type(exc).__name__)

    def end(self, outcome: Optional[str], keep_transcript: bool = True, *, workflow: Optional[str] = None,
            caller_phone: Optional[str] = None):
        """
        Close the call record. keep_transcript=False (the caller asked not to be
        kept) blanks every turn's text now. Only the first call counts.
        """
        if self.ended:
            return
        self.ended = True
        try:
            if not self.started:
                self.start()
            self.keep_transcript = bool(keep_transcript) and self.keep_transcript
            phone = phones.to_e164(caller_phone) if caller_phone else None
            self._submit(_finish_call, db.now_str(), outcome or "ended", self.keep_transcript, workflow, phone)
            logger.info("[%s] call record closed: %d turns, outcome=%s%s", self.call_id, self.turns_recorded,
                        outcome or "ended", "" if self.keep_transcript else ", transcript not kept")
            events.publish({"type": "call_ended", "call_id": self.call_id, "outcome": outcome or "ended",
                            "kept": self.keep_transcript})
        except Exception as exc:
            logger.warning("[%s] call record not closed (%s)", self.call_id, type(exc).__name__)


# ---------------------------------------------------------------- retention
def purge(conn, now=None, retention_days: Optional[int] = None) -> int:
    """Blank transcript text of calls that started more than the retention period ago. Returns turns blanked."""
    now = clock.localize(now) if now is not None else clock.now()
    days = config.TRANSCRIPT_RETENTION_DAYS if retention_days is None else retention_days
    cutoff = db.utc_str(now - timedelta(days=days))
    with _write(conn):
        blanked = conn.execute(
            "UPDATE call_turns SET text = NULL, entities_json = NULL "
            "WHERE (text IS NOT NULL OR entities_json IS NOT NULL) "
            "AND call_id IN (SELECT id FROM calls WHERE started_at < ?)", (cutoff,)).rowcount
    _last_purge.update(at=db.utc_str(now), turns=blanked)
    return blanked


async def purge_loop(interval_h: Optional[float] = None):
    """Run purge() now and then every TRANSCRIPT_PURGE_HOURS, until cancelled."""
    interval = (config.TRANSCRIPT_PURGE_HOURS if interval_h is None else interval_h) * 3600
    while True:
        try:
            blanked = await db.get_db().run(purge)
            if blanked:
                logger.info("Transcript purge: blanked %d turns older than %d days",
                            blanked, config.TRANSCRIPT_RETENTION_DAYS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Transcript purge failed (%s)", type(exc).__name__)
        await asyncio.sleep(max(interval, 60))


def delete_call_data(conn, call_id: str, actor: str) -> Optional[int]:
    """
    Staff "Delete call data": blank this call's transcript, extracted details and
    caller number, and audit who did it. Returns turns blanked, None if no such call.
    Appointments made on the call are business records and stay.
    """
    with _write(conn):
        if conn.execute("SELECT 1 FROM calls WHERE id = ?", (call_id,)).fetchone() is None:
            return None
        blanked = conn.execute("UPDATE call_turns SET text = NULL, entities_json = NULL WHERE call_id = ?",
                               (call_id,)).rowcount
        conn.execute("UPDATE calls SET caller_phone_e164 = NULL, recording_consent = 0 WHERE id = ?", (call_id,))
        conn.execute("INSERT INTO audit_events (ts, actor, action, entity, entity_id, after_json, correlation_id) "
                     "VALUES (?, ?, 'delete_call_data', 'call', ?, ?, ?)",
                     (db.now_str(), actor, call_id, json.dumps({"turns_blanked": blanked}), call_id))
    events.publish({"type": "call_data_deleted", "call_id": call_id})
    return blanked


def status() -> dict:
    """For /health: the retention policy and the last purge."""
    return {
        "transcript_days": config.TRANSCRIPT_RETENTION_DAYS,
        "purge_every_hours": config.TRANSCRIPT_PURGE_HOURS,
        "audio_recorded": False,
        "last_purge": _last_purge["at"],
        "last_purge_turns": _last_purge["turns"],
    }


# ---------------------------------------------------------------- reading (dashboard)
def _loads(value):
    if not value:
        return None
    try:
        return json.loads(value)
    except ValueError:
        return None


def list_calls(conn, limit: int = 50, offset: int = 0) -> list[dict]:
    """Recent calls, newest first, with turn counts (no transcript text)."""
    rows = conn.execute(
        "SELECT c.*, COUNT(t.turn) AS turns, COALESCE(SUM(t.text IS NOT NULL), 0) AS kept_turns "
        "FROM calls c LEFT JOIN call_turns t ON t.call_id = c.id "
        "GROUP BY c.id ORDER BY c.started_at DESC, c.rowid DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["phone_display"] = phones.national(item["caller_phone_e164"]) if item["caller_phone_e164"] else ""
        out.append(item)
    return out


def get_call(conn, call_id: str) -> Optional[dict]:
    """One call with its transcript, the tasks it created and the appointments it made."""
    row = conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
    if row is None:
        return None
    call = dict(row)
    call["phone_display"] = phones.national(call["caller_phone_e164"]) if call["caller_phone_e164"] else ""
    call["turns"] = []
    for t in conn.execute("SELECT * FROM call_turns WHERE call_id = ? ORDER BY turn, rowid", (call_id,)):
        item = dict(t)
        item["entities"] = _loads(item.pop("entities_json"))
        item["latency"] = _loads(item.pop("latency_json"))
        call["turns"].append(item)
    call["tasks"] = [dict(r) for r in conn.execute(
        "SELECT id, kind, priority, status, created_at FROM tasks WHERE call_id = ? ORDER BY id", (call_id,))]
    call["appointments"] = [dict(r) for r in conn.execute(
        "SELECT a.id, a.status, a.start_utc, s.name AS service, b.name AS branch FROM appointments a "
        "JOIN services s ON s.id = a.service_id JOIN branches b ON b.id = a.branch_id "
        "WHERE a.created_by_call_id = ? ORDER BY a.start_utc", (call_id,))]
    return call
