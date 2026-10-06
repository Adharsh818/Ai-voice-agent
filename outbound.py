"""
Doctor-unavailability recovery calls, demo grade (plan 5.11, Day 5).

Staff block a doctor for a time range; Emma then calls the affected patients
one by one to move their appointments. The pieces, in the order staff meet them:

    create_block(conn, ...)      the block is effective at once: no new booking
                                 can land in it (scheduling.validate checks it)
    preview(conn, block_id)      the booked appointments it affects, grouped by
                                 phone; nothing is called yet
    start_campaign(conn, ...)    the appointments staff ticked become one job per
                                 phone, each with a snapshot of the appointment
                                 versions it was created from
    stop_campaign / lift_block   queued jobs stop; a call in progress finishes;
                                 appointments already moved stay moved

The Runner (one per server) works through queued jobs one at a time. Before a
job it waits for the call gate (an inbound call pauses the campaign), the
calling window (RECOVERY_CALL_WINDOW) and checks do-not-call and staleness: an
appointment whose version no longer matches the snapshot was changed by staff
or the patient since, and is left alone. Then the logged-in /patient page
rings ("Incoming call from Pearl Dental"). Declined, or no answer within
RECOVERY_RING_TIMEOUT_S: one attempt only, the appointments are flagged
NEEDS RESCHEDULE and staff get a recovery_failed task. Answered: the page
opens /ws/outbound and the call runs on dialogue.recovery.

No real phone network is involved: the demo rings a browser tab (decision 3).
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Callable, Optional

import clock
import config
import db
import events
import phones
import tasks

logger = logging.getLogger(__name__)

REASONS = ("illness", "emergency", "training", "personal", "other")
ACTIVE_JOB_STATES = ("ringing", "in_call")


class RecoveryError(ValueError):
    """A staff request that can't be carried out; the message is shown on the dashboard."""


# ---------------------------------------------------------------- helpers


def _audit(conn, actor: str, action: str, entity: str, entity_id, after=None):
    conn.execute("INSERT INTO audit_events (ts, actor, action, entity, entity_id, after_json) "
                 "VALUES (?, ?, ?, ?, ?, ?)",
                 (db.now_str(), actor, action, entity, str(entity_id),
                  json.dumps(after, default=str) if after is not None else None))


def _window() -> tuple:
    """RECOVERY_CALL_WINDOW "09:00-20:00" -> (time(9), time(20))."""
    raw = (getattr(config, "RECOVERY_CALL_WINDOW", "09:00-20:00") or "").strip()
    try:
        a, b = raw.split("-")
        start = time.fromisoformat(a.strip())
        end = time.fromisoformat(b.strip())
        return start, end
    except ValueError:
        logger.warning("RECOVERY_CALL_WINDOW %r is not HH:MM-HH:MM; using 09:00-20:00", raw)
        return time(9), time(20)


def in_calling_window(now: Optional[datetime] = None) -> bool:
    now = clock.localize(now) if now is not None else clock.now()
    start, end = _window()
    return start <= now.time() < end


def calling_window_text() -> str:
    start, end = _window()
    return f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}"


def do_not_call(conn, phone_e164: str) -> bool:
    row = conn.execute("SELECT do_not_call FROM contact_prefs WHERE phone_e164 = ?", (phone_e164,)).fetchone()
    return bool(row and row["do_not_call"])


def set_do_not_call(conn, phone_e164: str, actor: str = "emma"):
    with db.transaction(conn):
        conn.execute("INSERT INTO contact_prefs (phone_e164, do_not_call, updated_at) VALUES (?, 1, ?) "
                     "ON CONFLICT (phone_e164) DO UPDATE SET do_not_call = 1, updated_at = excluded.updated_at",
                     (phone_e164, db.now_str()))
        _audit(conn, actor, "do_not_call_add", "contact", phones.masked(phone_e164))


def _hm(value) -> Optional[time]:
    if value in (None, ""):
        return None
    try:
        return time.fromisoformat(str(value).strip())
    except ValueError:
        return None


def patient_window(conn, phone_e164: str) -> tuple:
    """(call_after, call_before) for this number (times or None): its own calling hours, if staff set any."""
    row = conn.execute("SELECT call_after, call_before FROM contact_prefs WHERE phone_e164 = ?",
                       (phone_e164,)).fetchone()
    return (_hm(row["call_after"]), _hm(row["call_before"])) if row else (None, None)


def set_call_window(conn, phone_e164: str, after: Optional[str], before: Optional[str], actor: str = "staff"):
    """Staff set (or clear, with None) when this number may be called. Raises RecoveryError on bad times."""
    a, b = _hm(after), _hm(before)
    if (after and a is None) or (before and b is None):
        raise RecoveryError("Times must look like 18:00.")
    if a and b and a >= b:
        raise RecoveryError("'Call after' must be earlier than 'call before'.")
    with db.transaction(conn):
        conn.execute("INSERT INTO contact_prefs (phone_e164, do_not_call, updated_at, call_after, call_before) "
                     "VALUES (?, 0, ?, ?, ?) ON CONFLICT (phone_e164) DO UPDATE SET call_after = excluded.call_after, "
                     "call_before = excluded.call_before, updated_at = excluded.updated_at",
                     (phone_e164, db.now_str(), a.strftime("%H:%M") if a else None,
                      b.strftime("%H:%M") if b else None))
        _audit(conn, actor, "call_window_set", "contact", phones.masked(phone_e164),
               {"after": after or None, "before": before or None})


def list_call_windows(conn) -> list:
    rows = conn.execute("SELECT phone_e164, call_after, call_before FROM contact_prefs "
                        "WHERE call_after IS NOT NULL OR call_before IS NOT NULL ORDER BY updated_at DESC").fetchall()
    return [{"phone_masked": phones.masked(r["phone_e164"]), "call_after": r["call_after"],
             "call_before": r["call_before"]} for r in rows]


def _hours_for(conn, phone_e164: Optional[str]) -> tuple:
    """Today's callable hours for this number: the clinic window narrowed by the patient's own."""
    start, end = _window()
    if phone_e164:
        after, before = patient_window(conn, phone_e164)
        narrowed = (max(start, after) if after else start, min(end, before) if before else end)
        if narrowed[0] < narrowed[1]:
            start, end = narrowed
    return start, end


def callable_now(conn, phone_e164: Optional[str], now: Optional[datetime] = None) -> bool:
    now = clock.localize(now) if now is not None else clock.now()
    start, end = _hours_for(conn, phone_e164)
    return start <= now.time() < end


def next_callable(conn, phone_e164: Optional[str], earliest: datetime) -> datetime:
    """The first moment at or after `earliest` inside the clinic's and the patient's calling hours."""
    earliest = clock.localize(earliest)
    start, end = _hours_for(conn, phone_e164)
    day = earliest.date()
    for _ in range(8):
        opens = clock.localize(datetime.combine(day, start))
        closes = clock.localize(datetime.combine(day, end))
        if earliest < closes and day.weekday() != 6:          # the clinic is closed on Sundays
            return max(earliest, opens)
        day += timedelta(days=1)
        earliest = clock.localize(datetime.combine(day, start))
    return earliest


_APPT_SQL = (
    "SELECT a.id, a.version, a.status, a.start_utc, a.end_utc, a.caller_name, a.caller_phone_e164, "
    "a.doctor_id, a.branch_id, a.service_id, p.name AS patient_name, s.name AS service, s.duration_min, "
    "d.spoken_name AS doctor, b.name AS branch FROM appointments a JOIN patients p ON p.id = a.patient_id "
    "JOIN services s ON s.id = a.service_id JOIN doctors d ON d.id = a.doctor_id "
    "JOIN branches b ON b.id = a.branch_id"
)


def _appt_view(row) -> dict:
    out = dict(row)
    out["start"] = db.local(row["start_utc"]).isoformat()
    out["end"] = db.local(row["end_utc"]).isoformat()
    out["phone_masked"] = phones.masked(row["caller_phone_e164"])
    return out


# ---------------------------------------------------------------- blocks


def create_block(conn, *, doctor_id: int, start: datetime, end: datetime, reason: str,
                 note: Optional[str] = None, actor: str = "staff") -> int:
    """Block a doctor from `start` to `end` (clinic-local). Effective immediately."""
    if reason not in REASONS:
        raise RecoveryError(f"Reason must be one of: {', '.join(REASONS)}.")
    start, end = clock.localize(start), clock.localize(end)
    if end <= start:
        raise RecoveryError("The block must end after it starts.")
    if end <= clock.now():
        raise RecoveryError("That time range is already over.")
    with db.transaction(conn):
        doc = conn.execute("SELECT id, spoken_name FROM doctors WHERE id = ?", (doctor_id,)).fetchone()
        if doc is None:
            raise RecoveryError("Unknown doctor.")
        block_id = conn.execute(
            "INSERT INTO blocked_times (doctor_id, start_utc, end_utc, reason_category, note, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (doctor_id, db.utc_str(start), db.utc_str(end), reason, (note or "").strip() or None, actor,
             db.now_str())).lastrowid
        _audit(conn, actor, "block_create", "block", block_id,
               {"doctor_id": doctor_id, "start": start.isoformat(), "end": end.isoformat(), "reason": reason})
    events.publish({"type": "recovery", "call_id": None, "what": "block_created", "block_id": block_id})
    return block_id


def get_block(conn, block_id: int) -> Optional[dict]:
    row = conn.execute("SELECT bt.*, d.spoken_name AS doctor, d.branch_id, b.name AS branch FROM blocked_times bt "
                       "JOIN doctors d ON d.id = bt.doctor_id JOIN branches b ON b.id = d.branch_id "
                       "WHERE bt.id = ?", (block_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["start"] = db.local(row["start_utc"]).isoformat()
    out["end"] = db.local(row["end_utc"]).isoformat()
    return out


def list_blocks(conn, *, include_past: bool = False) -> list:
    sql = ("SELECT bt.id FROM blocked_times bt WHERE bt.lifted_at IS NULL"
           + ("" if include_past else " AND bt.end_utc > ?") + " ORDER BY bt.start_utc")
    args = () if include_past else (db.utc_str(clock.now()),)
    return [get_block(conn, r["id"]) for r in conn.execute(sql, args).fetchall()]


def affected_appointments(conn, block_id: int) -> list:
    """Booked, future appointments with the blocked doctor that overlap the block."""
    block = get_block(conn, block_id)
    if block is None:
        return []
    start_after = max(block["start_utc"], db.utc_str(clock.now()))
    rows = conn.execute(_APPT_SQL + " WHERE a.doctor_id = ? AND a.status = 'booked' AND a.start_utc < ? "
                        "AND a.end_utc > ? AND a.start_utc >= ? ORDER BY a.start_utc",
                        (block["doctor_id"], block["end_utc"], block["start_utc"], start_after)).fetchall()
    return [_appt_view(r) for r in rows]


def preview(conn, block_id: int) -> dict:
    """What a campaign for this block would do: affected appointments grouped by phone."""
    block = get_block(conn, block_id)
    if block is None:
        raise RecoveryError("Unknown block.")
    groups: dict = {}
    for appt in affected_appointments(conn, block_id):
        phone = appt["caller_phone_e164"]
        group = groups.setdefault(phone, {"phone_masked": appt["phone_masked"],
                                          "name": appt["caller_name"] or appt["patient_name"],
                                          "do_not_call": do_not_call(conn, phone), "appointments": []})
        group["appointments"].append(appt)
    return {"block": block, "groups": list(groups.values()),
            "count": sum(len(g["appointments"]) for g in groups.values())}


def lift_block(conn, block_id: int, actor: str = "staff") -> dict:
    """End a block early. Running campaigns for it stop; moved appointments stay moved."""
    with db.transaction(conn):
        row = conn.execute("SELECT id, lifted_at FROM blocked_times WHERE id = ?", (block_id,)).fetchone()
        if row is None:
            raise RecoveryError("Unknown block.")
        if row["lifted_at"]:
            return {"ok": True, "already": True}
        conn.execute("UPDATE blocked_times SET lifted_at = ? WHERE id = ?", (db.now_str(), block_id))
        _audit(conn, actor, "block_lift", "block", block_id)
        stopped = 0
        for c in conn.execute("SELECT id FROM outbound_campaigns WHERE block_id = ? AND status = 'running'",
                              (block_id,)).fetchall():
            stopped += _stop_jobs(conn, c["id"], "block_lifted")
            conn.execute("UPDATE outbound_campaigns SET status = 'stopped' WHERE id = ?", (c["id"],))
    events.publish({"type": "recovery", "call_id": None, "what": "block_lifted", "block_id": block_id})
    return {"ok": True, "jobs_stopped": stopped}


# ---------------------------------------------------------------- campaigns


def start_campaign(conn, *, block_id: int, appointment_ids: list, actor: str = "staff") -> int:
    """One job per phone for the ticked appointments, each with a version snapshot."""
    block = get_block(conn, block_id)
    if block is None:
        raise RecoveryError("Unknown block.")
    if block["lifted_at"]:
        raise RecoveryError("That block has been lifted.")
    wanted = {str(a) for a in appointment_ids or []}
    if not wanted:
        raise RecoveryError("Tick at least one appointment to call about.")
    affected = {a["id"]: a for a in affected_appointments(conn, block_id)}
    unknown = wanted - set(affected)
    if unknown:
        raise RecoveryError("Some of those appointments are no longer affected by this block. Refresh the preview.")
    with db.transaction(conn):
        busy = conn.execute(
            "SELECT j.appointment_ids_json FROM outbound_jobs j JOIN outbound_campaigns c ON c.id = j.campaign_id "
            "WHERE c.status = 'running' AND j.status IN ('queued', 'ringing', 'in_call')").fetchall()
        already = {a for r in busy for a in json.loads(r["appointment_ids_json"])}
        if wanted & already:
            raise RecoveryError("Some of those appointments are already in a running campaign.")
        campaign_id = conn.execute("INSERT INTO outbound_campaigns (block_id, status, created_by, created_at) "
                                   "VALUES (?, 'running', ?, ?)", (block_id, actor, db.now_str())).lastrowid
        by_phone: dict = {}
        for appt_id in sorted(wanted, key=lambda a: affected[a]["start_utc"]):
            by_phone.setdefault(affected[appt_id]["caller_phone_e164"], []).append(appt_id)
        for phone, ids in by_phone.items():
            snapshot = {a: affected[a]["version"] for a in ids}
            conn.execute("INSERT INTO outbound_jobs (campaign_id, phone_e164, appointment_ids_json, snapshot_json, "
                         "status, attempts, idempotency_key, updated_at, max_attempts) "
                         "VALUES (?, ?, ?, ?, 'queued', 0, ?, ?, ?)",
                         (campaign_id, phone, json.dumps(ids), json.dumps(snapshot),
                          f"recovery-{campaign_id}-{uuid.uuid4().hex[:8]}", db.now_str(),
                          config.RECOVERY_MAX_ATTEMPTS))
        _audit(conn, actor, "campaign_start", "campaign", campaign_id,
               {"block_id": block_id, "appointments": sorted(wanted), "jobs": len(by_phone)})
    events.publish({"type": "recovery", "call_id": None, "what": "campaign_started", "campaign_id": campaign_id})
    wake()
    return campaign_id


def _stop_jobs(conn, campaign_id: int, outcome: str) -> int:
    return conn.execute("UPDATE outbound_jobs SET status = 'skipped', outcome = ?, updated_at = ? "
                        "WHERE campaign_id = ? AND status = 'queued'",
                        (outcome, db.now_str(), campaign_id)).rowcount


def stop_campaign(conn, campaign_id: int, actor: str = "staff") -> dict:
    """Cancel the queued jobs. A call already ringing or in progress finishes."""
    with db.transaction(conn):
        row = conn.execute("SELECT status FROM outbound_campaigns WHERE id = ?", (campaign_id,)).fetchone()
        if row is None:
            raise RecoveryError("Unknown campaign.")
        stopped = _stop_jobs(conn, campaign_id, "stopped")
        if row["status"] == "running":
            conn.execute("UPDATE outbound_campaigns SET status = 'stopped' WHERE id = ?", (campaign_id,))
        _audit(conn, actor, "campaign_stop", "campaign", campaign_id, {"jobs_stopped": stopped})
    events.publish({"type": "recovery", "call_id": None, "what": "campaign_stopped", "campaign_id": campaign_id})
    return {"ok": True, "jobs_stopped": stopped}


def _finish_campaign_if_done(conn, campaign_id: int):
    left = conn.execute("SELECT COUNT(*) FROM outbound_jobs WHERE campaign_id = ? AND status IN "
                        "('queued', 'ringing', 'in_call')", (campaign_id,)).fetchone()[0]
    if not left:
        conn.execute("UPDATE outbound_campaigns SET status = 'completed' WHERE id = ? AND status = 'running'",
                     (campaign_id,))


def list_campaigns(conn, limit: int = 20) -> list:
    out = []
    for c in conn.execute("SELECT c.*, bt.doctor_id, d.spoken_name AS doctor FROM outbound_campaigns c "
                          "JOIN blocked_times bt ON bt.id = c.block_id JOIN doctors d ON d.id = bt.doctor_id "
                          "ORDER BY c.id DESC LIMIT ?", (limit,)).fetchall():
        item = dict(c)
        item["jobs"] = [job_view(conn, j["id"]) for j in conn.execute(
            "SELECT id FROM outbound_jobs WHERE campaign_id = ? ORDER BY id", (c["id"],)).fetchall()]
        item["summary"] = campaign_summary(item["jobs"])
        out.append(item)
    return out


def campaign_summary(jobs: list) -> dict:
    """What a campaign achieved: appointments by result, and calls by state."""
    appts = {"moved": 0, "cancelled": 0, "needs_reschedule": 0, "unchanged": 0}
    calls = {"waiting": 0, "retrying": 0, "done": 0, "needs_staff": 0, "skipped": 0}
    for j in jobs:
        if j["status"] == "queued":
            calls["retrying" if j.get("next_attempt_at") else "waiting"] += 1
        elif j["status"] in ("ringing", "in_call"):
            calls["waiting"] += 1
        elif j["status"] == "failed":
            calls["needs_staff"] += 1
        else:
            calls["done" if j["status"] == "done" else "skipped"] += 1
        for a in j["appointments"]:
            if a["status"] == "cancelled":
                appts["cancelled"] += 1
            elif a["status"] == "needs_reschedule":
                appts["needs_reschedule"] += 1
            elif a["status"] == "booked" and j["status"] in ("done", "failed") and                     a["version"] > (j.get("snapshot") or {}).get(a["id"], a["version"]):
                appts["moved"] += 1                    # changed since the campaign started: moved on the call
            else:
                appts["unchanged"] += 1
    return {"appointments": appts, "calls": calls}


def campaign_rows(conn, campaign_id: int) -> list:
    """One row per appointment called about, for the campaign's CSV report."""
    rows = []
    for j in conn.execute("SELECT id FROM outbound_jobs WHERE campaign_id = ? ORDER BY id", (campaign_id,)).fetchall():
        job = job_view(conn, j["id"])
        for a in job["appointments"]:
            rows.append({"job": job["id"], "patient": a["patient_name"], "phone": job["phone_masked"],
                         "call": job["status"], "result": job["outcome"] or "", "attempts": job["attempts"],
                         "next_attempt": job["next_attempt_at"] or "", "appointment_now": a["start"],
                         "doctor": a["doctor"], "branch": a["branch"], "service": a["service"],
                         "status": a["status"]})
    return rows


def get_job(conn, job_id: int) -> Optional[dict]:
    row = conn.execute("SELECT * FROM outbound_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["appointment_ids"] = json.loads(row["appointment_ids_json"])
    out["snapshot"] = json.loads(row["snapshot_json"])
    return out


def job_view(conn, job_id: int) -> Optional[dict]:
    """A job for the dashboard: masked phone, the contact's name, its appointments now."""
    job = get_job(conn, job_id)
    if job is None:
        return None
    appts = []
    for appt_id in job["appointment_ids"]:
        row = conn.execute(_APPT_SQL + " WHERE a.id = ?", (appt_id,)).fetchone()
        if row is not None:
            appts.append(_appt_view(row))
    return {"id": job["id"], "campaign_id": job["campaign_id"], "status": job["status"], "outcome": job["outcome"],
            "attempts": job["attempts"], "max_attempts": job.get("max_attempts") or 1,
            "next_attempt_at": db.local(job["next_attempt_at"]).isoformat() if job.get("next_attempt_at") else None,
            "call_id": job["call_id"], "updated_at": job["updated_at"], "snapshot": job["snapshot"],
            "phone_masked": phones.masked(job["phone_e164"]),
            "name": (appts[0]["caller_name"] or appts[0]["patient_name"]) if appts else None,
            "appointments": appts}


# ---------------------------------------------------------------- jobs (runner side)


@dataclass
class Claimed:
    """A job the runner is about to ring, with the appointments still worth calling about."""
    job_id: int
    campaign_id: int
    phone_e164: str
    appointments: list = field(default_factory=list)   # appointment dicts (_APPT_SQL), current
    snapshot: dict = field(default_factory=dict)


def next_job(conn, now: Optional[datetime] = None) -> Optional[dict]:
    """
    The next job to ring now: a queued job of a running, unpaused campaign,
    whose retry time (if any) has come and whose number may be called now.
    """
    now = clock.localize(now) if now is not None else clock.now()
    rows = conn.execute("SELECT j.id, j.phone_e164 FROM outbound_jobs j JOIN outbound_campaigns c "
                        "ON c.id = j.campaign_id WHERE c.status = 'running' AND c.paused_at IS NULL "
                        "AND j.status = 'queued' AND (j.next_attempt_at IS NULL OR j.next_attempt_at <= ?) "
                        "ORDER BY j.next_attempt_at IS NOT NULL, j.next_attempt_at, j.id",
                        (db.utc_str(now),)).fetchall()
    for r in rows:
        if callable_now(conn, r["phone_e164"], now):
            return get_job(conn, r["id"])
    return None


def has_queued(conn) -> bool:
    return next_job(conn) is not None


def waiting_summary(conn) -> dict:
    """Queued jobs that can't ring yet: how many, and when the next one may."""
    row = conn.execute("SELECT COUNT(*) AS n, MIN(j.next_attempt_at) AS next_at FROM outbound_jobs j "
                       "JOIN outbound_campaigns c ON c.id = j.campaign_id WHERE c.status = 'running' "
                       "AND j.status = 'queued'").fetchone()
    paused = conn.execute("SELECT COUNT(*) FROM outbound_campaigns WHERE status = 'running' "
                          "AND paused_at IS NOT NULL").fetchone()[0]
    return {"queued": row["n"], "next_retry": db.local(row["next_at"]).isoformat() if row["next_at"] else None,
            "paused_campaigns": paused}


def requeue_job(conn, job_id: int, *, outcome: str, retry_at: datetime, call_id: Optional[str] = None,
                extra_attempt: bool = False):
    """Try this job again at `retry_at` (an unanswered call, or a patient who asked to be called back)."""
    with db.transaction(conn):
        conn.execute("UPDATE outbound_jobs SET status = 'queued', outcome = ?, next_attempt_at = ?, "
                     "max_attempts = CASE WHEN ? THEN MAX(max_attempts, attempts + 1) ELSE max_attempts END, "
                     "updated_at = ? WHERE id = ?",
                     (outcome, db.utc_str(retry_at), 1 if extra_attempt else 0, db.now_str(), job_id))
    events.publish({"type": "recovery", "call_id": call_id, "what": "job_retry", "job_id": job_id,
                    "retry_at": retry_at.isoformat(), "outcome": outcome})


def pause_campaign(conn, campaign_id: int, actor: str = "staff") -> dict:
    """Hold the queued jobs (a call in progress finishes); resume_campaign carries on where it was."""
    with db.transaction(conn):
        row = conn.execute("SELECT status, paused_at FROM outbound_campaigns WHERE id = ?", (campaign_id,)).fetchone()
        if row is None:
            raise RecoveryError("Unknown campaign.")
        if row["status"] != "running":
            raise RecoveryError("Only a running campaign can be paused.")
        if not row["paused_at"]:
            conn.execute("UPDATE outbound_campaigns SET paused_at = ? WHERE id = ?", (db.now_str(), campaign_id))
            _audit(conn, actor, "campaign_pause", "campaign", campaign_id)
    events.publish({"type": "recovery", "call_id": None, "what": "campaign_paused", "campaign_id": campaign_id})
    return {"ok": True}


def resume_campaign(conn, campaign_id: int, actor: str = "staff") -> dict:
    with db.transaction(conn):
        row = conn.execute("SELECT status, paused_at FROM outbound_campaigns WHERE id = ?", (campaign_id,)).fetchone()
        if row is None:
            raise RecoveryError("Unknown campaign.")
        if row["paused_at"]:
            conn.execute("UPDATE outbound_campaigns SET paused_at = NULL WHERE id = ?", (campaign_id,))
            _audit(conn, actor, "campaign_resume", "campaign", campaign_id)
    events.publish({"type": "recovery", "call_id": None, "what": "campaign_resumed", "campaign_id": campaign_id})
    wake()
    return {"ok": True}


def claim_job(conn, job_id: int, call_id: str) -> tuple:
    """
    Check a queued job just before ringing: do-not-call, then staleness per
    appointment. Returns ("ring", Claimed) or ("skipped", outcome) after
    recording the skip.
    """
    with db.transaction(conn):
        job = get_job(conn, job_id)
        if job is None or job["status"] != "queued":
            return "skipped", "gone"
        block_lifted = conn.execute(
            "SELECT bt.lifted_at FROM outbound_campaigns c JOIN blocked_times bt ON bt.id = c.block_id "
            "WHERE c.id = ?", (job["campaign_id"],)).fetchone()
        if block_lifted and block_lifted["lifted_at"]:
            outcome = "block_lifted"
        elif do_not_call(conn, job["phone_e164"]):
            outcome = "do_not_call"
        else:
            fresh = []
            for appt_id in job["appointment_ids"]:
                row = conn.execute(_APPT_SQL + " WHERE a.id = ?", (appt_id,)).fetchone()
                if row is None or row["status"] != "booked" or row["version"] != job["snapshot"].get(appt_id):
                    logger.info("recovery job %s: appointment %s changed since the snapshot; skipped",
                                job_id, appt_id)
                    continue
                if db.local(row["start_utc"]) <= clock.now():
                    continue
                fresh.append(dict(row))
            if fresh:
                conn.execute("UPDATE outbound_jobs SET status = 'ringing', attempts = attempts + 1, call_id = ?, "
                             "updated_at = ? WHERE id = ?", (call_id, db.now_str(), job_id))
                return "ring", Claimed(job_id, job["campaign_id"], job["phone_e164"], fresh, job["snapshot"])
            outcome = "stale"
        conn.execute("UPDATE outbound_jobs SET status = 'skipped', outcome = ?, updated_at = ? WHERE id = ?",
                     (outcome, db.now_str(), job_id))
        _finish_campaign_if_done(conn, job["campaign_id"])
    events.publish({"type": "recovery", "call_id": None, "what": "job_skipped", "job_id": job_id,
                    "outcome": outcome})
    return "skipped", outcome


def mark_in_call(conn, job_id: int):
    with db.transaction(conn):
        conn.execute("UPDATE outbound_jobs SET status = 'in_call', updated_at = ? WHERE id = ? AND status = 'ringing'",
                     (db.now_str(), job_id))


def flag_needs_reschedule(conn, appointment_id: str, block_id: Optional[int] = None, actor: str = "emma",
                          call_id: Optional[str] = None) -> bool:
    """Booked -> needs_reschedule (Calendar shows NEEDS RESCHEDULE). False if it isn't booked any more."""
    with db.transaction(conn):
        row = conn.execute("SELECT status FROM appointments WHERE id = ?", (appointment_id,)).fetchone()
        if row is None or row["status"] != "booked":
            return False
        now = db.now_str()
        conn.execute("UPDATE appointments SET status = 'needs_reschedule', affected_by_block_id = COALESCE(?, "
                     "affected_by_block_id), version = version + 1, updated_at = ? WHERE id = ?",
                     (block_id, now, appointment_id))
        conn.execute("INSERT INTO sync_outbox (appointment_id, due_at, attempts, status) VALUES (?, ?, 0, 'pending') "
                     "ON CONFLICT (appointment_id) DO UPDATE SET due_at = excluded.due_at, attempts = 0, "
                     "status = 'pending', last_error = NULL", (appointment_id, now))
        _audit(conn, actor, "flag_needs_reschedule", "appointment", appointment_id, {"block_id": block_id})
    events.publish({"type": "appointment", "call_id": call_id, "appointment_id": appointment_id,
                    "action": "needs_reschedule"})
    return True


def campaign_block(conn, campaign_id: int) -> Optional[dict]:
    row = conn.execute("SELECT block_id FROM outbound_campaigns WHERE id = ?", (campaign_id,)).fetchone()
    return get_block(conn, row["block_id"]) if row else None


def finish_job(conn, job_id: int, *, status: str, outcome: str, note: Optional[str] = None,
               unresolved: Optional[list] = None, call_id: Optional[str] = None):
    """
    Close a job. `unresolved` appointment ids are flagged NEEDS RESCHEDULE and
    get one recovery_failed task between them, so nothing falls through.
    """
    job = get_job(conn, job_id)
    if job is None:
        return
    block = campaign_block(conn, job["campaign_id"])
    flagged = []
    for appt_id in unresolved or []:
        if flag_needs_reschedule(conn, appt_id, block["id"] if block else None, call_id=call_id):
            flagged.append(appt_id)
    if unresolved:
        tasks.create_task(conn, kind="recovery_failed", priority="high", phone_e164=job["phone_e164"],
                          call_id=call_id, appointment_id=unresolved[0],
                          note=note or f"Recovery call: {outcome.replace('_', ' ')}. "
                                       f"{len(unresolved)} appointment(s) still need a new time.")
    with db.transaction(conn):
        conn.execute("UPDATE outbound_jobs SET status = ?, outcome = ?, updated_at = ? WHERE id = ?",
                     (status, outcome, db.now_str(), job_id))
        _finish_campaign_if_done(conn, job["campaign_id"])
    events.publish({"type": "recovery", "call_id": call_id, "what": "job_finished", "job_id": job_id,
                    "status": status, "outcome": outcome})


def recover_interrupted(conn) -> int:
    """At startup: a job left ringing or in a call by a restart is closed as failed, with a task."""
    rows = conn.execute("SELECT id, appointment_ids_json FROM outbound_jobs WHERE status IN ('ringing', 'in_call')"
                        ).fetchall()
    for r in rows:
        still = [a for a in json.loads(r["appointment_ids_json"])
                 if (conn.execute("SELECT status FROM appointments WHERE id = ?", (a,)).fetchone() or {"status": ""}
                     )["status"] == "booked"]
        finish_job(conn, r["id"], status="failed", outcome="interrupted", unresolved=still,
                   note="Recovery call was cut off by a server restart.")
    return len(rows)


# ---------------------------------------------------------------- runner


class Ringing:
    """One job ringing the /patient page: the page answers or declines; the runner waits."""

    def __init__(self, claimed: Claimed, call_id: str, token: str):
        self.claimed = claimed
        self.call_id = call_id
        self.token = token
        self.answered = asyncio.Event()
        self.connected = asyncio.Event()          # the /patient page opened the call's socket
        self.declined = False
        self.call_done = asyncio.Event()
        self.result: Optional[dict] = None        # set by the call when it ends

    def public(self) -> dict:
        appts = self.claimed.appointments
        first = appts[0] if appts else {}
        return {"job_id": self.claimed.job_id, "call_id": self.call_id, "token": self.token,
                "from": config.CLINIC_NAME.replace(" Clinic", ""),
                "to_name": (first.get("caller_name") or first.get("patient_name") or "").split(" ")[0],
                "to_masked": phones.masked(self.claimed.phone_e164)}


_wake_event: Optional[asyncio.Event] = None
_wake_loop: Optional[asyncio.AbstractEventLoop] = None


def wake():
    """Start the next job now instead of at the runner's next poll (safe from any thread)."""
    event, loop = _wake_event, _wake_loop
    if event is None or loop is None or loop.is_closed():
        return
    try:
        loop.call_soon_threadsafe(event.set)
    except RuntimeError:
        pass


class Runner:
    """
    Works through queued recovery jobs, one at a time, for the life of the server.

        runner = Runner(database, gate)
        task = asyncio.create_task(runner.run())

    `gate` is server.CallGate (try_acquire / release / busy). The runner takes
    the gate as "outbound" while the patient's tab rings and keeps it for the
    call, so an inbound caller gets the busy message rather than talking over
    a ringing line; and it never starts a job while the gate is busy, which is
    how an inbound call pauses the campaign.
    """

    POLL_S = 2.0
    CONNECT_TIMEOUT_S = 20.0      # answer pressed -> the call's socket must open by then

    def __init__(self, database, gate, *, ring_timeout_s: Optional[float] = None,
                 window_check: Optional[Callable[[], bool]] = None):
        self.db = database
        self.gate = gate
        self.ring_timeout_s = (ring_timeout_s if ring_timeout_s is not None
                               else float(getattr(config, "RECOVERY_RING_TIMEOUT_S", 30)))
        self.window_check = window_check or in_calling_window
        self.ringing: Optional[Ringing] = None
        self.paused_reason: Optional[str] = None
        self.waiting: Optional[dict] = None
        self._stopped = False

    def status(self) -> dict:
        return {"ringing": self.ringing.public() if self.ringing else None, "paused": self.paused_reason,
                "window": calling_window_text(), "waiting": getattr(self, "waiting", None)}

    def stop(self):
        self._stopped = True
        wake()

    async def run(self):
        global _wake_event, _wake_loop
        _wake_event, _wake_loop = asyncio.Event(), asyncio.get_running_loop()
        try:
            await self.db.run(recover_interrupted)
        except Exception as exc:
            logger.warning("recovery: could not close interrupted jobs: %s", exc)
        while not self._stopped:
            try:
                await self.step()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("recovery runner step failed: %s", exc, exc_info=True)
            _wake_event.clear()
            try:
                await asyncio.wait_for(_wake_event.wait(), self.POLL_S)
            except asyncio.TimeoutError:
                pass

    def _pause(self, reason: Optional[str]):
        if reason != self.paused_reason:
            self.paused_reason = reason
            events.publish({"type": "recovery", "call_id": None, "what": "runner", "paused": reason})

    async def step(self) -> Optional[str]:
        """Run at most one job. Returns what happened (for tests), or None if idle."""
        job = await self.db.run(next_job)
        if job is None:
            self._pause(None)
            # Jobs may still be waiting: for a retry time, the calling hours or a paused campaign.
            self.waiting = await self.db.run(waiting_summary)
            return None
        self.waiting = None
        if self.gate.busy:
            self._pause("another call is in progress")
            return "paused"
        if not self.window_check():
            self._pause(f"outside the calling window ({calling_window_text()})")
            return "paused"
        self._pause(None)
        call_id = uuid.uuid4().hex[:8]
        if not self.gate.try_acquire("outbound", call_id):
            return "paused"
        try:
            verdict, claimed = await self.db.run(claim_job, job["id"], call_id)
            if verdict != "ring":
                return f"skipped:{claimed}"
            return await self._ring(claimed, call_id)
        finally:
            self.ringing = None
            self.gate.release(call_id)

    async def _ring(self, claimed: Claimed, call_id: str) -> str:
        ring = Ringing(claimed, call_id, uuid.uuid4().hex)
        self.ringing = ring
        events.publish({"type": "ring", "call_id": call_id, **ring.public()})
        logger.info("recovery job %s ringing (%s)", claimed.job_id, phones.masked(claimed.phone_e164))
        unresolved = [a["id"] for a in claimed.appointments]
        try:
            await asyncio.wait_for(ring.answered.wait(), self.ring_timeout_s)
        except asyncio.TimeoutError:
            pass
        if ring.declined or not ring.answered.is_set():
            outcome = "declined" if ring.declined else "no_answer"
            events.publish({"type": "ring_ended", "call_id": call_id, "job_id": claimed.job_id, "outcome": outcome})
            if outcome == "no_answer" and await self._retry(claimed, call_id, "no_answer"):
                return "retry"
            job = await self.db.run(get_job, claimed.job_id)
            tries = f"{job['attempts']} attempt{'s' if job and job['attempts'] != 1 else ''}" if job else "one attempt"
            await self.db.run(finish_job, claimed.job_id, status="failed", outcome=outcome, unresolved=unresolved,
                              call_id=call_id,
                              note=f"Recovery call {'declined' if ring.declined else 'not answered'} ({tries}). "
                                   f"{len(unresolved)} appointment(s) need a new time.")
            return outcome
        events.publish({"type": "ring_ended", "call_id": call_id, "job_id": claimed.job_id, "outcome": "answered"})
        await self.db.run(mark_in_call, claimed.job_id)
        try:
            await asyncio.wait_for(ring.connected.wait(), self.CONNECT_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.warning("recovery job %s answered but the call never connected", claimed.job_id)
            ring.result = {"outcome": "not_connected", "unresolved": unresolved,
                           "note": "Answered, but the call never connected."}
            ring.call_done.set()
        await ring.call_done.wait()
        result = ring.result or {"outcome": "abandoned", "unresolved": unresolved}
        if result.get("unresolved") and result.get("outcome") in ("busy", "not_connected"):
            # "I'm driving, call me after 6": the patient asked for a call back, so it's
            # booked even past the usual attempts; an unconnected call is just retried.
            asked = result.get("outcome") == "busy"
            when = None
            if result.get("retry_at"):
                try:
                    when = clock.localize(datetime.fromisoformat(result["retry_at"]))
                except (TypeError, ValueError):
                    when = None
            if await self._retry(claimed, call_id, result["outcome"], at=when, extra_attempt=asked):
                return "retry"
        failed = bool(result.get("unresolved")) and result.get("outcome") not in ("pending", "staff", "do_not_call",
                                                                                 "busy")
        await self.db.run(finish_job, claimed.job_id, status="failed" if failed else "done",
                          outcome=result.get("outcome") or "completed", unresolved=result.get("unresolved") or [],
                          call_id=call_id, note=result.get("note"))
        return result.get("outcome") or "completed"

    async def _retry(self, claimed: Claimed, call_id: str, outcome: str, at: Optional[datetime] = None,
                     extra_attempt: bool = False) -> bool:
        """Queue another attempt if one is left (or the patient asked for it); False when it's the last."""
        job = await self.db.run(get_job, claimed.job_id)
        if job is None:
            return False
        if not extra_attempt and job["attempts"] >= job.get("max_attempts", 1):
            return False
        earliest = at or clock.now() + timedelta(minutes=config.RECOVERY_RETRY_GAP_MIN)
        retry_at = await self.db.run(next_callable, claimed.phone_e164, earliest)
        await self.db.run(requeue_job, claimed.job_id, outcome=outcome, retry_at=retry_at, call_id=call_id,
                          extra_attempt=extra_attempt)
        logger.info("recovery job %s: %s, trying again at %s", claimed.job_id, outcome, retry_at.strftime("%a %H:%M"))
        return True

    # ------------------------------------------------ the /patient page's side
    def answer(self, job_id: int, token: str) -> Optional[Ringing]:
        ring = self.ringing
        if ring is None or ring.claimed.job_id != job_id or ring.token != token or ring.answered.is_set():
            return None
        ring.answered.set()
        return ring

    def decline(self, job_id: int, token: str) -> bool:
        ring = self.ringing
        if ring is None or ring.claimed.job_id != job_id or ring.token != token or ring.answered.is_set():
            return False
        ring.declined = True
        ring.answered.set()
        return True


_runner: Optional[Runner] = None


def set_runner(runner: Optional[Runner]):
    global _runner
    _runner = runner


def get_runner() -> Optional[Runner]:
    return _runner
