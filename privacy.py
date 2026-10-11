"""
Personal data beyond transcripts (India's DPDP Act 2023; docs/REAL_CLINIC_GAPS.md S1, 11 Oct).

recording.purge() already blanks call transcripts after TRANSCRIPT_RETENTION_DAYS.
This module covers the rest:

- purge_personal(): the other places a name or number lingers, once they're
  no longer needed: idempotency records (`actions`, they repeat a booking's
  details), the before/after copies in the audit log, the text of messages
  already sent, and finished recovery jobs' snapshots. Run with the
  transcript purge.
- export_phone(): everything held about one phone number, for a patient who
  asks what the clinic has on them.
- erase_phone(): a patient's request to be forgotten. Refused while they
  still have an upcoming appointment (cancel it first). Appointments stay as
  business records, but without the name and number; a do-not-call flag is
  kept so the clinic keeps honouring it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Optional

import clock
import config
import db
import phones


def _cutoff(days: int, now=None) -> str:
    now = clock.localize(now) if now is not None else clock.now()
    return db.utc_str(now - timedelta(days=days))


def purge_personal(conn, now=None) -> dict:
    """Drop personal details that are past their use. Returns counts per kind."""
    days = config.PERSONAL_DATA_RETENTION_DAYS
    cut = _cutoff(days, now)
    audit_cut = _cutoff(config.AUDIT_DETAIL_RETENTION_DAYS, now)
    with db.transaction(conn):
        out = {
            "actions": conn.execute("DELETE FROM actions WHERE created_at < ?", (cut,)).rowcount,
            "audit_details": conn.execute(
                "UPDATE audit_events SET before_json = NULL, after_json = NULL "
                "WHERE ts < ? AND (before_json IS NOT NULL OR after_json IS NOT NULL)", (audit_cut,)).rowcount,
            "messages": conn.execute(
                "UPDATE patient_messages SET body = '' WHERE created_at < ? AND body != '' "
                "AND status IN ('sent', 'logged', 'skipped', 'failed')", (cut,)).rowcount,
            "recovery_snapshots": conn.execute(
                "UPDATE outbound_jobs SET snapshot_json = '{}' WHERE updated_at < ? AND snapshot_json != '{}' "
                "AND status IN ('done', 'failed', 'skipped')", (cut,)).rowcount,
        }
    return out


def _e164(phone: str) -> Optional[str]:
    return phones.to_e164(phone) if phone else None


def export_phone(conn, phone: str) -> Optional[dict]:
    """Everything held about this number (None if the number isn't valid)."""
    e164 = _e164(phone)
    if not e164:
        return None
    rows = lambda sql, *a: [dict(r) for r in conn.execute(sql, a)]
    calls = rows("SELECT id, direction, started_at, ended_at, outcome FROM calls WHERE caller_phone_e164 = ?", e164)
    for call in calls:
        call["transcript"] = rows("SELECT turn, role, text FROM call_turns WHERE call_id = ? AND text IS NOT NULL "
                                  "ORDER BY turn", call["id"])
    return {
        "phone": e164,
        "patients": rows("SELECT name, created_at FROM patients WHERE phone_e164 = ?", e164),
        "appointments": rows(
            "SELECT a.start_utc, a.status, s.name AS service, b.name AS branch, d.spoken_name AS doctor, "
            "p.name AS patient FROM appointments a JOIN services s ON s.id = a.service_id "
            "JOIN branches b ON b.id = a.branch_id JOIN doctors d ON d.id = a.doctor_id "
            "JOIN patients p ON p.id = a.patient_id WHERE a.caller_phone_e164 = ? ORDER BY a.start_utc", e164),
        "calls": calls,
        "tasks": rows("SELECT kind, status, note, created_at FROM tasks WHERE phone_e164 = ?", e164),
        "messages": rows("SELECT kind, status, body, due_at FROM patient_messages WHERE phone_e164 = ?", e164),
        "contact_preferences": rows("SELECT * FROM contact_prefs WHERE phone_e164 = ?", e164),
    }


def erase_phone(conn, phone: str, actor: str, now=None) -> dict:
    """
    Forget this number's personal details. {"ok": False, "reason": ...} when
    it can't be done (invalid number, or an upcoming appointment to cancel first).
    """
    e164 = _e164(phone)
    if not e164:
        return {"ok": False, "reason": "That isn't a valid phone number."}
    now_s = db.utc_str(clock.localize(now) if now is not None else clock.now())
    upcoming = conn.execute("SELECT COUNT(*) FROM appointments WHERE caller_phone_e164 = ? AND status IN "
                            "('booked', 'needs_reschedule') AND start_utc > ?", (e164, now_s)).fetchone()[0]
    if upcoming:
        return {"ok": False, "reason": f"This number has {upcoming} upcoming appointment(s). Cancel them first."}
    tag = "erased-" + hashlib.sha256(e164.encode()).hexdigest()[:12]
    with db.transaction(conn):
        appt_ids = [r[0] for r in conn.execute("SELECT id FROM appointments WHERE caller_phone_e164 = ?", (e164,))]
        call_ids = [r[0] for r in conn.execute("SELECT id FROM calls WHERE caller_phone_e164 = ?", (e164,))]
        counts = {"appointments": len(appt_ids), "calls": len(call_ids)}
        for call_id in call_ids:
            conn.execute("UPDATE call_turns SET text = NULL, entities_json = NULL WHERE call_id = ?", (call_id,))
        conn.execute("UPDATE calls SET caller_phone_e164 = NULL, recording_consent = 0 WHERE caller_phone_e164 = ?",
                     (e164,))
        conn.execute("UPDATE appointments SET caller_name = NULL, caller_phone_e164 = ? WHERE caller_phone_e164 = ?",
                     (tag, e164))
        for pid, in conn.execute("SELECT id FROM patients WHERE phone_e164 = ?", (e164,)).fetchall():
            conn.execute("UPDATE patients SET name = 'Erased', name_norm = ?, phone_e164 = ? WHERE id = ?",
                         (f"{tag}-{pid}", tag, pid))
        conn.execute("UPDATE tasks SET phone_e164 = NULL, note = '(erased on request)' WHERE phone_e164 = ?", (e164,))
        conn.execute("DELETE FROM patient_messages WHERE phone_e164 = ?", (e164,))
        conn.execute("UPDATE outbound_jobs SET phone_e164 = ?, snapshot_json = '{}' WHERE phone_e164 = ?", (tag, e164))
        conn.execute("DELETE FROM contact_prefs WHERE phone_e164 = ? AND do_not_call = 0", (e164,))
        import scheduling
        for appt_id in appt_ids:
            conn.execute("UPDATE audit_events SET before_json = NULL, after_json = NULL "
                         "WHERE entity = 'appointment' AND entity_id = ?", (appt_id,))
            scheduling._outbox(conn, appt_id, clock.now())       # the calendar copy loses the name too
        conn.execute("DELETE FROM actions WHERE result_json LIKE ?", (f"%{e164}%",))
        # The request itself is audited, without the number (only its hash tag).
        conn.execute("INSERT INTO audit_events (ts, actor, action, entity, entity_id, after_json) "
                     "VALUES (?, ?, 'privacy_erase', 'contact', ?, ?)", (now_s, actor, tag, json.dumps(counts)))
    return {"ok": True, **counts}
