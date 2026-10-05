"""
Staff tasks: the promise behind every "someone from the clinic will call you".

Invariant 7 of the plan: Emma never promises a callback that doesn't exist. Any
flow that hands something to a person (an emergency, a caller who insists on
staff, a recovery call nobody answered) creates a row here in the same turn,
and the dashboard's Tasks page is where staff see and close it.

Like scheduling.py, every function takes a sqlite3 connection first and runs on
the database thread:

    task_id = await db.get_db().run(tasks.create_task, kind="callback",
                                    phone_e164="+919876543210", note="Wants a person")

A task can be created inside a transaction the caller already opened (for
example together with a booking), or on its own; either way it is committed
with that work. The dashboard is told straight away and re-reads the list, so
a task whose transaction rolled back simply never appears.
"""

import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

import db
import events
import phones

logger = logging.getLogger(__name__)

# The CHECK constraints in migrations/001_init.sql.
KINDS = ("callback", "emergency", "red_flag", "recovery_failed", "escalation", "language", "abandoned")
PRIORITIES = ("urgent", "high", "normal")
_PRIORITY_ORDER = "CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 ELSE 2 END"


@contextmanager
def _write(conn):
    """Join the caller's open transaction, or run in one of our own."""
    if conn.in_transaction:
        yield conn
    else:
        with db.transaction(conn):
            yield conn


def _stamp(value) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return db.utc_str(value)
    db.parse_utc(str(value))                  # already a *_utc string: check it
    return str(value)


def _audit(conn, action: str, task_id: int, actor: str, after: Optional[str], correlation_id=None):
    conn.execute("INSERT INTO audit_events (ts, actor, action, entity, entity_id, after_json, correlation_id) "
                 "VALUES (?, ?, ?, 'task', ?, ?, ?)",
                 (db.now_str(), actor, action, str(task_id), after, correlation_id))


def create_task(conn, *, kind: str, priority: str = "normal", phone_e164: Optional[str] = None,
                note: str = "", call_id: Optional[str] = None, appointment_id: Optional[str] = None,
                due_at=None) -> int:
    """
    Record a task for staff and return its id. `phone_e164` may be any form
    phones.to_e164 understands; `due_at` is an aware datetime or a *_utc string.
    Raises ValueError for an unknown kind or priority (a programming error).
    """
    if kind not in KINDS:
        raise ValueError(f"unknown task kind {kind!r}")
    if priority not in PRIORITIES:
        raise ValueError(f"unknown task priority {priority!r}")
    phone = (phones.to_e164(phone_e164) or phone_e164) if phone_e164 else None
    with _write(conn):
        task_id = conn.execute(
            "INSERT INTO tasks (kind, priority, call_id, appointment_id, phone_e164, note, status, created_at, due_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)",
            (kind, priority, call_id, appointment_id, phone, (note or "").strip(), db.now_str(), _stamp(due_at)),
        ).lastrowid
        _audit(conn, "create", task_id, "emma" if call_id else "system", None, call_id)
    logger.info("task %d created: %s (%s)", task_id, kind, priority)
    events.publish({"type": "task_created", "call_id": call_id, "task_id": task_id, "kind": kind,
                    "priority": priority})
    return task_id


def _row(row) -> dict:
    out = dict(row)
    out["phone_display"] = phones.national(out["phone_e164"]) if out.get("phone_e164") else ""
    return out


def get_task(conn, task_id: int) -> Optional[dict]:
    row = conn.execute("SELECT t.* FROM tasks t WHERE t.id = ?", (task_id,)).fetchone()
    return _row(row) if row else None


def list_tasks(conn, status: Optional[str] = "open", limit: int = 500) -> list[dict]:
    """Tasks with that status ("open", "done", or None / "all"), most urgent and oldest first."""
    sql, args = "SELECT t.* FROM tasks t", []
    if status and status != "all":
        sql += " WHERE t.status = ?"
        args.append(status)
    sql += f" ORDER BY t.status = 'done', {_PRIORITY_ORDER}, t.created_at, t.id LIMIT ?"
    args.append(limit)
    return [_row(r) for r in conn.execute(sql, args).fetchall()]


def mark_done(conn, task_id: int, done_by: str) -> bool:
    """Close a task; False if there is no open task with that id."""
    with _write(conn):
        changed = conn.execute("UPDATE tasks SET status = 'done', done_by = ? WHERE id = ? AND status = 'open'",
                               (done_by, task_id)).rowcount
        if changed:
            _audit(conn, "done", task_id, done_by, None)
    if changed:
        events.publish({"type": "task_updated", "call_id": None, "task_id": task_id, "status": "done"})
    return bool(changed)


def reopen(conn, task_id: int, actor: str) -> bool:
    """Undo a mistaken "done" (the dashboard's toggle); False if the task isn't done."""
    with _write(conn):
        changed = conn.execute("UPDATE tasks SET status = 'open', done_by = NULL WHERE id = ? AND status = 'done'",
                               (task_id,)).rowcount
        if changed:
            _audit(conn, "reopen", task_id, actor, None)
    if changed:
        events.publish({"type": "task_updated", "call_id": None, "task_id": task_id, "status": "open"})
    return bool(changed)


def open_counts(conn) -> dict:
    """Open tasks per priority, for the dashboard header."""
    counts = {p: 0 for p in PRIORITIES}
    for row in conn.execute("SELECT priority, COUNT(*) AS n FROM tasks WHERE status = 'open' GROUP BY priority"):
        counts[row["priority"]] = row["n"]
    return counts
