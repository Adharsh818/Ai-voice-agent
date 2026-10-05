"""
Staff dashboard (plan 5.10): a static page under /dashboard that talks to the
JSON API below and to a server-sent event stream for the live call.

Everything here needs the staff login (auth.py) except the login page itself,
its auth status and the static code under /dashboard/static (no data there).
Every page is labelled DEMO: the clinic data is fictitious (decision Q2).

    /dashboard/                  the app (Live · Appointments · Tasks · Calls · System)
    /dashboard/login             the login page
    /dashboard/api/...           JSON; state-changing calls are POST from the same origin
    /dashboard/api/events        live events (SSE), see events.py

Appointment changes go through scheduling.py, exactly like Emma's, so every
rule, the audit trail and the Calendar outbox apply to staff edits too.
No template engine or htmx: FastAPI + static HTML + vanilla JS, works offline.
"""

import asyncio
import csv
import io
import json
import logging
import re
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

import auth
import calendar_sync
import clock
import config
import db
import events
import phones
import recording
import scheduling
import tasks

logger = logging.getLogger("dashboard")

STATIC_DIR = Path(__file__).parent / "static" / "dashboard"
HEARTBEAT_S = 15.0

router = APIRouter()
staff = Depends(auth.require_staff)


def _database():
    return db.get_db()


async def _run(fn, *args, **kwargs):
    return await _database().run(fn, *args, **kwargs)


async def _body(request: Request) -> dict:
    try:
        data = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Expected a JSON body.")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object.")
    return data


def _audit(conn, actor: str, action: str, entity: str, entity_id: str, after=None):
    """Staff actions that change or export data leave a trail (no personal text in it)."""
    with db.transaction(conn):
        conn.execute("INSERT INTO audit_events (ts, actor, action, entity, entity_id, after_json) "
                     "VALUES (?, ?, ?, ?, ?, ?)",
                     (db.now_str(), actor, action, entity, entity_id,
                      json.dumps(after) if after is not None else None))


# ---------------------------------------------------------------- pages
@router.get("/dashboard", include_in_schema=False)
async def dashboard_root():
    return RedirectResponse("/dashboard/", status_code=307)


@router.get("/dashboard/", include_in_schema=False)
async def dashboard_page(request: Request):
    if not auth.configured() or auth.current_user(request) is None:
        return RedirectResponse("/dashboard/login", status_code=303)
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/dashboard/login", include_in_schema=False)
async def login_page(request: Request):
    if auth.configured() and auth.current_user(request) is not None:
        return RedirectResponse("/dashboard/", status_code=303)
    return FileResponse(STATIC_DIR / "login.html")


# ---------------------------------------------------------------- login / logout
@router.get("/dashboard/api/auth")
async def auth_status(request: Request):
    state = auth.status()
    return {"configured": state["configured"], "reason": state["reason"],
            "logged_in": state["configured"] and auth.current_user(request) is not None}


@router.post("/dashboard/api/login")
async def login(request: Request):
    state = auth.status()
    if not state["configured"]:
        raise HTTPException(status_code=503, detail="Dashboard locked: " + state["reason"])
    if not auth.same_origin(request.headers):
        raise HTTPException(status_code=403, detail="Cross-site request refused.")
    key = auth.client_key(request)
    wait = auth.limiter.retry_after(key)
    if wait:
        return JSONResponse({"detail": f"Too many attempts. Try again in {wait} seconds."}, status_code=429,
                            headers={"Retry-After": str(wait)})
    password = str((await _body(request)).get("password") or "")
    # scrypt takes tens of milliseconds: keep it off the event loop a live call uses.
    ok = await asyncio.to_thread(auth.verify_password, password, config.DASHBOARD_PASSWORD_HASH)
    if not ok:
        auth.limiter.failure(key)
        await _run(_audit, "anonymous", "login_failed", "dashboard", key)
        return JSONResponse({"detail": "That password isn't right."}, status_code=401)
    auth.limiter.success(key)
    await _run(_audit, "staff", "login", "dashboard", key)
    response = JSONResponse({"ok": True})
    auth.set_session_cookie(response, auth.issue_session())
    return response


@router.post("/dashboard/api/logout")
async def logout(request: Request):
    if not auth.same_origin(request.headers):
        raise HTTPException(status_code=403, detail="Cross-site request refused.")
    auth.revoke(request.cookies.get(auth.COOKIE_NAME))
    response = JSONResponse({"ok": True})
    auth.clear_session_cookie(response)
    return response


# ---------------------------------------------------------------- overview and catalogue
def _overview(conn) -> dict:
    today = clock.today()
    lo = db.utc_str(datetime.combine(today, time(0, 0), tzinfo=clock.TZ))
    hi = db.utc_str(datetime.combine(today + timedelta(days=1), time(0, 0), tzinfo=clock.TZ))
    by_branch = {r["name"]: r["n"] for r in conn.execute(
        "SELECT b.name, COUNT(a.id) AS n FROM branches b LEFT JOIN appointments a ON a.branch_id = b.id "
        "AND a.status IN ('booked', 'needs_reschedule') AND a.start_utc >= ? AND a.start_utc < ? "
        "WHERE b.active = 1 GROUP BY b.id ORDER BY b.id", (lo, hi))}
    return {
        "today": today.isoformat(),
        "timezone": config.CLINIC_TIMEZONE,
        "appointments_today": sum(by_branch.values()),
        "appointments_today_by_branch": by_branch,
        "needs_reschedule": conn.execute("SELECT COUNT(*) FROM appointments WHERE status = 'needs_reschedule'")
                                .fetchone()[0],
        "tasks": tasks.open_counts(conn),
        "sync": calendar_sync.outbox_counts(conn),
        "calls_today": conn.execute("SELECT COUNT(*) FROM calls WHERE started_at >= ?", (lo,)).fetchone()[0],
        "demo": bool(conn.execute("SELECT COUNT(*) FROM branches WHERE is_demo = 1").fetchone()[0]),
    }


def _gate_status(request: Request) -> dict:
    gate = getattr(request.app.state, "gate", None)
    status = gate.status() if gate is not None else {"busy": False}
    session = _live_session(request, status.get("call_id"))
    status["staffed"] = bool(session is not None and session.staffed)
    status["controllable"] = session is not None
    return status


# ---------------------------------------------------------------- live call controls (plan 5.10)

def _live_session(request: Request, call_id: Optional[str]):
    sessions = getattr(request.app.state, "sessions", None) or {}
    return sessions.get(call_id) if call_id else None


def _session_or_404(request: Request, call_id: str):
    session = _live_session(request, call_id)
    if session is None or session.closed:
        raise HTTPException(status_code=404, detail="That call has ended.")
    return session


async def _control(request: Request, user: str, call_id: str, action: str, done: bool, mode: Optional[str] = None,
                   detail: Optional[dict] = None):
    if not done:
        raise HTTPException(status_code=409, detail="That isn't possible on this call right now.")
    await _run(_audit, user, action, "call", call_id, detail)
    if mode:
        events.publish({"type": "staff", "mode": mode, "call_id": call_id})
    return {"ok": True, "call": _gate_status(request)}


@router.post("/dashboard/api/live/{call_id}/takeover")
async def live_takeover(call_id: str, request: Request, user: str = staff):
    session = _session_or_404(request, call_id)
    return await _control(request, user, call_id, "call_takeover", await session.take_over(), "staff")


@router.post("/dashboard/api/live/{call_id}/say")
async def live_say(call_id: str, request: Request, user: str = staff):
    session = _session_or_404(request, call_id)
    text = str((await _body(request)).get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Type what Emma should say.")
    # The audit records that staff spoke, not the words (they are in the call transcript).
    return await _control(request, user, call_id, "call_operator_line", await session.say_for_staff(text),
                          detail={"chars": len(text)})


@router.post("/dashboard/api/live/{call_id}/handback")
async def live_hand_back(call_id: str, request: Request, user: str = staff):
    session = _session_or_404(request, call_id)
    return await _control(request, user, call_id, "call_hand_back", await session.hand_back(), "emma")


@router.post("/dashboard/api/live/{call_id}/end")
async def live_end(call_id: str, request: Request, user: str = staff):
    session = _session_or_404(request, call_id)
    note = str((await _body(request)).get("note") or "").strip()
    return await _control(request, user, call_id, "call_end_by_staff", await session.end_for_staff(note), "ended")


@router.get("/dashboard/api/overview")
async def overview(request: Request, user: str = staff):
    data = await _run(_overview)
    data["call"] = _gate_status(request)
    data["calendar"] = calendar_sync.status()
    return data


def _catalog(conn) -> dict:
    services = [dict(r) for r in conn.execute(
        "SELECT id, name, duration_min FROM services WHERE active = 1 ORDER BY id")]
    offers: dict = {}
    for r in conn.execute("SELECT doctor_id, service_id FROM doctor_services"):
        offers.setdefault(r["doctor_id"], []).append(r["service_id"])
    doctors = [dict(r, services=offers.get(r["id"], [])) for r in conn.execute(
        "SELECT d.id, d.name, d.spoken_name, d.gender, d.branch_id, b.name AS branch FROM doctors d "
        "JOIN branches b ON b.id = d.branch_id WHERE d.active = 1 ORDER BY d.branch_id, d.id")]
    branches = [dict(r, calendar=bool(r["calendar_id"])) for r in conn.execute(
        "SELECT id, name, area, calendar_id, is_demo FROM branches WHERE active = 1 ORDER BY id")]
    for b in branches:
        b.pop("calendar_id")
    return {"services": services, "doctors": doctors, "branches": branches}


@router.get("/dashboard/api/catalog")
async def catalog(user: str = staff):
    return await _run(_catalog)


# ---------------------------------------------------------------- appointments
_APPT_SQL = (
    "SELECT a.id, a.status, a.source, a.version, a.start_utc, a.end_utc, a.caller_name, a.caller_phone_e164, "
    "a.patient_age, a.name_unverified, a.cancel_reason, a.created_at, a.created_by_call_id, "
    "a.calendar_synced_version, p.name AS patient_name, s.id AS service_id, s.name AS service, "
    "d.id AS doctor_id, d.name AS doctor, b.id AS branch_id, b.name AS branch, b.is_demo "
    "FROM appointments a JOIN patients p ON p.id = a.patient_id JOIN services s ON s.id = a.service_id "
    "JOIN doctors d ON d.id = a.doctor_id JOIN branches b ON b.id = a.branch_id"
)
SCOPES = ("day", "upcoming", "past", "all")


def _appt(row) -> dict:
    out = dict(row)
    start, end = db.local(out["start_utc"]), db.local(out["end_utc"])
    out.update(start=start.isoformat(), end=end.isoformat(), date=start.date().isoformat(),
               time=start.strftime("%H:%M"), end_time=end.strftime("%H:%M"), weekday=start.strftime("%a"),
               phone_display=phones.national(out["caller_phone_e164"]),
               calendar_synced=out["calendar_synced_version"] == out["version"])
    return out


def query_appointments(conn, *, scope: str = "day", day: Optional[date] = None, days: int = 1,
                       branch_id: Optional[int] = None, doctor_id: Optional[int] = None,
                       status: Optional[str] = None, q: Optional[str] = None, limit: int = 2000) -> list[dict]:
    """The Appointments page: a day (or run of days), upcoming, past or all, with filters and search."""
    sql, args = _APPT_SQL + " WHERE 1 = 1", []
    now_s = db.now_str()
    if scope == "day":
        first = day or clock.today()
        sql += " AND a.start_utc >= ? AND a.start_utc < ?"
        args += [db.utc_str(datetime.combine(first, time(0, 0), tzinfo=clock.TZ)),
                 db.utc_str(datetime.combine(first + timedelta(days=max(1, min(days, 62))), time(0, 0),
                                             tzinfo=clock.TZ))]
    elif scope == "upcoming":
        sql += " AND a.start_utc >= ?"
        args.append(now_s)
    elif scope == "past":
        sql += " AND a.start_utc < ?"
        args.append(now_s)
    if branch_id:
        sql += " AND a.branch_id = ?"
        args.append(branch_id)
    if doctor_id:
        sql += " AND a.doctor_id = ?"
        args.append(doctor_id)
    if status:
        sql += " AND a.status = ?"
        args.append(status)
    term = (q or "").strip()
    if term:
        digits = re.sub(r"\D", "", term)
        clauses = ["lower(p.name) LIKE ?", "lower(COALESCE(a.caller_name, '')) LIKE ?", "a.id LIKE ?"]
        like = f"%{term.lower()}%"
        args += [like, like, f"{term.lower()}%"]
        if len(digits) >= 3:
            clauses.append("a.caller_phone_e164 LIKE ?")
            args.append(f"%{digits}%")
        sql += " AND (" + " OR ".join(clauses) + ")"
    sql += " ORDER BY a.start_utc" + (" DESC" if scope == "past" else "") + ", b.id, d.id LIMIT ?"
    args.append(limit)
    return [_appt(r) for r in conn.execute(sql, args).fetchall()]


def _filters(scope: str, day: Optional[str], days: int, branch_id, doctor_id, status, q) -> dict:
    if scope not in SCOPES:
        raise HTTPException(status_code=400, detail=f"scope must be one of {', '.join(SCOPES)}")
    try:
        parsed = date.fromisoformat(day) if day else None
    except ValueError:
        raise HTTPException(status_code=400, detail="day must be YYYY-MM-DD")
    if status and status not in ("booked", "cancelled", "needs_reschedule", "completed", "no_show"):
        raise HTTPException(status_code=400, detail="unknown status")
    return dict(scope=scope, day=parsed, days=days, branch_id=branch_id or None, doctor_id=doctor_id or None,
                status=status or None, q=q or None)


@router.get("/dashboard/api/appointments")
async def appointments(scope: str = "day", day: Optional[str] = None, days: int = 1,
                       branch_id: Optional[int] = None, doctor_id: Optional[int] = None,
                       status: Optional[str] = None, q: Optional[str] = None, user: str = staff):
    filters = _filters(scope, day, days, branch_id, doctor_id, status, q)
    return {"appointments": await _run(query_appointments, **filters)}


def _csv_cell(value) -> str:
    """Stop a caller-supplied name like "=HYPERLINK(...)" running as a spreadsheet formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


CSV_COLUMNS = [("Date", "date"), ("Start", "time"), ("End", "end_time"), ("Branch", "branch"),
               ("Doctor", "doctor"), ("Service", "service"), ("Patient", "patient_name"),
               ("Phone", "phone_display"), ("Status", "status"), ("Source", "source"),
               ("Appointment ID", "id")]


@router.get("/dashboard/api/appointments.csv")
async def appointments_csv(scope: str = "day", day: Optional[str] = None, days: int = 1,
                           branch_id: Optional[int] = None, doctor_id: Optional[int] = None,
                           status: Optional[str] = None, q: Optional[str] = None, user: str = staff):
    filters = _filters(scope, day, days, branch_id, doctor_id, status, q)
    rows = await _run(query_appointments, **filters)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([label for label, _ in CSV_COLUMNS])
    for row in rows:
        writer.writerow([_csv_cell(row[key]) for _, key in CSV_COLUMNS])
    # The audit trail records that an export happened and its scope, not its contents.
    described = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in filters.items() if v}
    await _run(_audit, user, "export_csv", "appointments", filters["scope"], {"rows": len(rows), **described})
    stamp = clock.today().isoformat()
    return Response(buffer.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="pearl-dental-DEMO-appointments-{stamp}.csv"',
                             "Cache-Control": "no-store"})


_FRIENDLY = {
    "TAKEN": "That time was just taken. Pick another.",
    "TOO_SOON": "That's too soon: bookings need at least 2 hours' notice.",
    "OFF_GRID": "Appointments start on the hour or half hour.",
    "BEYOND_HORIZON": "That's beyond the 60-day booking window.",
    "CLOSED_DAY": "The clinic is closed that day.",
    "CLOSURE": "That branch is closed that day.",
    "OUTSIDE_HOURS": "That's outside clinic hours.",
    "LUNCH": "That overlaps the lunch break.",
    "DOCTOR_NO_SERVICE": "That doctor doesn't do this service.",
    "DOCTOR_OFF": "The doctor isn't working then.",
    "DOCTOR_BLOCKED": "The doctor is unavailable then.",
    "STALE": "Someone changed this appointment a moment ago. Refresh and try again.",
    "NOT_FOUND": "That appointment doesn't exist.",
    "NOT_ACTIVE": "That appointment isn't active any more.",
    "TOO_LATE": "That appointment has already started.",
    "SAME_SLOT": "That's the time it already has.",
    "PATIENT_CONFLICT": "This patient already has an appointment then.",
    "MAX_FUTURE": "This number already has the maximum of 3 upcoming appointments.",
    "INVALID_PHONE": "That phone number isn't a valid Indian number.",
    "UNKNOWN_SERVICE": "Unknown service.",
    "UNKNOWN_DOCTOR": "Unknown doctor.",
}


def _result(result, action: str):
    body = {"ok": result.ok, "code": result.code, "message": None if result.ok else _FRIENDLY.get(result.code, result.code),
            "appointment_id": result.appointment_id}
    if result.ok:
        calendar_sync.notify()
        events.publish({"type": "appointment", "call_id": None, "appointment_id": result.appointment_id,
                        "action": action})
        return body
    return JSONResponse(body, status_code=409)


def _local_start(value) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        raise HTTPException(status_code=400, detail="start must be an ISO date-time like 2026-10-05T10:30")
    return clock.localize(parsed)


def _int(data: dict, key: str, required: bool = True) -> Optional[int]:
    value = data.get(key)
    if value in (None, ""):
        if required:
            raise HTTPException(status_code=400, detail=f"{key} is required")
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{key} must be a number")


@router.post("/dashboard/api/appointments/{appointment_id}/cancel")
async def cancel_appointment(appointment_id: str, request: Request, user: str = staff):
    data = await _body(request)
    result = await _run(scheduling.cancel, appointment_id,
                        idem_key=f"dash:{data.get('idem_key') or uuid.uuid4().hex}",
                        reason=(str(data.get("reason") or "").strip() or "Cancelled by staff"),
                        expected_version=_int(data, "version", required=False), actor=user)
    return _result(result, "cancelled")


@router.post("/dashboard/api/appointments")
async def book_appointment(request: Request, user: str = staff):
    data = await _body(request)
    name = str(data.get("patient_name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="patient_name is required")
    age = _int(data, "patient_age", required=False)
    result = await _run(scheduling.book, service=_int(data, "service_id"), doctor_id=_int(data, "doctor_id"),
                        start=_local_start(data.get("start")), patient_name=name, caller_name=name,
                        phone=str(data.get("phone") or ""), patient_age=age, source="dashboard", actor=user,
                        idem_key=f"dash:{data.get('idem_key') or uuid.uuid4().hex}")
    return _result(result, "booked")


@router.post("/dashboard/api/appointments/{appointment_id}/reschedule")
async def reschedule_appointment(appointment_id: str, request: Request, user: str = staff):
    data = await _body(request)
    result = await _run(scheduling.reschedule, appointment_id, doctor_id=_int(data, "doctor_id"),
                        start=_local_start(data.get("start")),
                        expected_version=_int(data, "version", required=False), actor=user,
                        enforce_notice=False, idem_key=f"dash:{data.get('idem_key') or uuid.uuid4().hex}")
    return _result(result, "rescheduled")


def _slots(conn, service_id: int, day: date, branch_id=None, doctor_id=None, ignore_appointment=None):
    found = scheduling.find_slots(conn, service=service_id, dates=[day],
                                  branch_ids=[branch_id] if branch_id else None, doctor_id=doctor_id,
                                  limit=200, distinct_times=False, ignore_appointment=ignore_appointment)
    return [{"start": s.start.strftime("%Y-%m-%dT%H:%M"), "time": s.start.strftime("%H:%M"),
             "doctor_id": s.doctor_id, "doctor": s.doctor, "branch_id": s.branch_id, "branch": s.branch}
            for s in found]


@router.get("/dashboard/api/slots")
async def slots(service_id: int, day: str, branch_id: Optional[int] = None, doctor_id: Optional[int] = None,
                ignore_appointment: Optional[str] = None, user: str = staff):
    try:
        parsed = date.fromisoformat(day)
    except ValueError:
        raise HTTPException(status_code=400, detail="day must be YYYY-MM-DD")
    return {"slots": await _run(_slots, service_id, parsed, branch_id, doctor_id, ignore_appointment)}


# ---------------------------------------------------------------- tasks
@router.get("/dashboard/api/tasks")
async def list_tasks(status: str = "open", user: str = staff):
    if status not in ("open", "done", "all"):
        raise HTTPException(status_code=400, detail="status must be open, done or all")
    return {"tasks": await _run(tasks.list_tasks, status)}


@router.post("/dashboard/api/tasks/{task_id}")
async def update_task(task_id: int, request: Request, user: str = staff):
    data = await _body(request)
    done = bool(data.get("done"))
    changed = await _run(tasks.mark_done if done else tasks.reopen, task_id, user)
    task = await _run(tasks.get_task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="No such task.")
    return {"ok": True, "changed": changed, "task": task}


# ---------------------------------------------------------------- calls and transcripts
@router.get("/dashboard/api/calls")
async def list_calls(limit: int = 50, offset: int = 0, user: str = staff):
    return {"calls": await _run(recording.list_calls, max(1, min(limit, 200)), max(0, offset))}


@router.get("/dashboard/api/calls/{call_id}")
async def get_call(call_id: str, user: str = staff):
    call = await _run(recording.get_call, call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="No such call.")
    return call


@router.post("/dashboard/api/calls/{call_id}/delete-data")
async def delete_call_data(call_id: str, user: str = staff):
    blanked = await _run(recording.delete_call_data, call_id, user)
    if blanked is None:
        raise HTTPException(status_code=404, detail="No such call.")
    return {"ok": True, "turns_blanked": blanked}


# ---------------------------------------------------------------- system
def _system(conn) -> dict:
    audit = [dict(r) for r in conn.execute(
        "SELECT id, ts, actor, action, entity, entity_id FROM audit_events ORDER BY id DESC LIMIT 60")]
    dnc = [dict(r, phone_display=phones.national(r["phone_e164"])) for r in conn.execute(
        "SELECT phone_e164, updated_at FROM contact_prefs WHERE do_not_call = 1 ORDER BY updated_at DESC")]
    return {
        "outbox": calendar_sync.outbox_rows(conn),
        "outbox_counts": calendar_sync.outbox_counts(conn),
        "calendars": calendar_sync.calendars_configured(conn),
        "do_not_call": dnc,
        "audit": audit,
    }


@router.get("/dashboard/api/system")
async def system(request: Request, user: str = staff):
    data = await _run(_system)
    data.update(calendar=calendar_sync.status(), retention=recording.status(), auth=auth.status(),
                events=events.stats(), call=_gate_status(request))
    return data


@router.post("/dashboard/api/sync/retry-failed")
async def retry_failed(user: str = staff):
    count = await _run(calendar_sync.retry_failed, user)
    calendar_sync.notify()
    return {"ok": True, "retried": count}


@router.post("/dashboard/api/sync/{appointment_id}/retry")
async def retry_sync(appointment_id: str, user: str = staff):
    if not await _run(calendar_sync.retry, appointment_id, user):
        raise HTTPException(status_code=404, detail="Nothing to retry for that appointment.")
    calendar_sync.notify()
    return {"ok": True}


def _set_dnc(conn, phone_e164: str, on: bool, actor: str):
    with db.transaction(conn):
        conn.execute("INSERT INTO contact_prefs (phone_e164, do_not_call, updated_at) VALUES (?, ?, ?) "
                     "ON CONFLICT (phone_e164) DO UPDATE SET do_not_call = excluded.do_not_call, "
                     "updated_at = excluded.updated_at", (phone_e164, int(on), db.now_str()))
        conn.execute("INSERT INTO audit_events (ts, actor, action, entity, entity_id) VALUES (?, ?, ?, 'contact', ?)",
                     (db.now_str(), actor, "do_not_call_add" if on else "do_not_call_remove",
                      phones.masked(phone_e164)))


@router.post("/dashboard/api/dnc")
async def add_dnc(request: Request, user: str = staff):
    phone = phones.to_e164(str((await _body(request)).get("phone") or ""))
    if phone is None:
        raise HTTPException(status_code=400, detail="That isn't a valid Indian phone number.")
    await _run(_set_dnc, phone, True, user)
    return {"ok": True, "phone_e164": phone}


@router.post("/dashboard/api/dnc/remove")
async def remove_dnc(request: Request, user: str = staff):
    phone = phones.to_e164(str((await _body(request)).get("phone") or ""))
    if phone is None:
        raise HTTPException(status_code=400, detail="That isn't a valid Indian phone number.")
    await _run(_set_dnc, phone, False, user)
    return {"ok": True}


# ---------------------------------------------------------------- live events (SSE)
def _sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"


async def sse_stream(request: Request, heartbeat_s: float = HEARTBEAT_S):
    """Replay the call in progress (if any), then stream new events with keep-alives."""
    async with events.subscribe() as stream:
        yield "retry: 3000\n\n"
        for event in events.current_call_events():
            yield _sse(event)
        while True:
            if await request.is_disconnected():
                break
            event = await stream.get(timeout=heartbeat_s)
            yield ": keep-alive\n\n" if event is None else _sse(event)


@router.get("/dashboard/api/events")
async def live_events(request: Request, user: str = staff):
    return StreamingResponse(sse_stream(request), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
