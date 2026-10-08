"""
One-way Google Calendar mirror of the appointments database (plan 5.9).

SQLite is the source of truth. Every booking, move or cancellation writes a
sync_outbox row in the same transaction (scheduling._outbox). This worker reads
the appointment's *current* state and makes the branch calendar match it:

    booked / completed / no_show   upsert the event
    needs_reschedule               upsert with a "NEEDS RESCHEDULE: " title prefix
    cancelled                      delete the event

Because it mirrors state rather than replaying changes, three quick edits to
one appointment become one calendar write, and order never matters.

Duplicates are impossible: the event id is deterministic ("emma" + the
appointment's uuid hex), so a retried insert gets 409 and becomes a patch.
A delete treats 404/410 as done. appointments.calendar_event_id holds the
event's full address, "<calendar id>/<event id>", because a reschedule can move
an appointment to another branch's calendar and the old event must go.

Failures back off 5 s, 30 s, 2 min, 10 min, then hourly; after 12 attempts the
row is marked failed and shows on the dashboard's System page with Retry.

Events carry the minimum: service, doctor, branch, the patient's first name,
the last 4 digits of the phone and the appointment id. Emma never reads the
calendar during a call, so a Google outage can't block or double-book anything.

The Google client uses a service account (secrets/google-service-account.json).
Without the key file or the library the worker stays off and /health says why.
Tests use FakeCalendar, which behaves like the API (409 on a reused id, 410 on
deleting twice).
"""

import asyncio
import logging
import os
import re
from datetime import timedelta
from typing import Optional

import clock
import config
import db
import events

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
BACKOFF_S = [5, 30, 120, 600, 3600]
MAX_ATTEMPTS = 12
NO_CALENDAR_RETRY_S = 60
REQUEST_TIMEOUT_S = 15
NEEDS_RESCHEDULE_PREFIX = "NEEDS RESCHEDULE: "
CALENDAR_NAME = "Pearl Dental — {branch}"
# What the branch calendars were called until 6 Oct; tools/setup_calendars.py renames them.
LEGACY_CALENDAR_NAME = "Pearl Dental — {branch} (DEMO)"
CALENDAR_DESCRIPTION = ("Appointments mirrored one way from the Pearl Dental dashboard; "
                        "change them there, not here.")


class CalendarError(Exception):
    """A calendar API failure; `status` is the HTTP status when there was one."""

    def __init__(self, status: Optional[int], message: str = ""):
        super().__init__(f"HTTP {status}: {message}" if status else message)
        self.status = status


# ---------------------------------------------------------------- clients
class CalendarClient:
    """What the worker and tools/setup_calendars.py need from a calendar service (all blocking)."""

    def insert(self, calendar_id: str, event_id: str, body: dict) -> None: ...
    def patch(self, calendar_id: str, event_id: str, body: dict) -> None: ...
    def delete(self, calendar_id: str, event_id: str) -> None: ...

    # Admin, for tools/setup_calendars.py
    def calendar_exists(self, calendar_id: str) -> bool: ...
    def find_calendar(self, summary: str) -> Optional[str]: ...
    def create_calendar(self, summary: str, description: str, time_zone: str) -> str: ...
    def calendar_info(self, calendar_id: str) -> dict: ...
    def rename_calendar(self, calendar_id: str, summary: str, description: str) -> None: ...
    def readers(self, calendar_id: str) -> set: ...
    def add_reader(self, calendar_id: str, email: str) -> None: ...


class FakeCalendar(CalendarClient):
    """In-memory calendar service with the API's quirks, and injectable failures."""

    def __init__(self):
        self.events: dict = {}          # (calendar_id, event_id) -> body (status "cancelled" once deleted)
        self.calendars: dict = {}       # calendar_id -> {"summary", "description", "timeZone", "readers"}
        self.calls: list = []           # (operation, calendar_id, event_id)
        self._failures: list = []       # (operation or None, status)

    def fail_next(self, status: Optional[int] = 503, operation: Optional[str] = None, times: int = 1):
        self._failures.extend([(operation, status)] * times)

    def _maybe_fail(self, operation: str):
        for i, (op, status) in enumerate(self._failures):
            if op in (None, operation):
                del self._failures[i]
                raise CalendarError(status, f"injected {operation} failure")

    def live(self, calendar_id: Optional[str] = None) -> dict:
        """Events that exist (not deleted), optionally in one calendar."""
        return {k: v for k, v in self.events.items()
                if v.get("status") != "cancelled" and (calendar_id is None or k[0] == calendar_id)}

    def insert(self, calendar_id, event_id, body):
        self.calls.append(("insert", calendar_id, event_id))
        self._maybe_fail("insert")
        if (calendar_id, event_id) in self.events:     # Google keeps deleted ids too
            raise CalendarError(409, "The requested identifier already exists.")
        self.events[(calendar_id, event_id)] = dict(body, id=event_id)

    def patch(self, calendar_id, event_id, body):
        self.calls.append(("patch", calendar_id, event_id))
        self._maybe_fail("patch")
        if (calendar_id, event_id) not in self.events:
            raise CalendarError(404, "Not Found")
        self.events[(calendar_id, event_id)].update(body)

    def delete(self, calendar_id, event_id):
        self.calls.append(("delete", calendar_id, event_id))
        self._maybe_fail("delete")
        event = self.events.get((calendar_id, event_id))
        if event is None:
            raise CalendarError(404, "Not Found")
        if event.get("status") == "cancelled":
            raise CalendarError(410, "Resource has been deleted")
        event["status"] = "cancelled"

    def calendar_exists(self, calendar_id):
        return calendar_id in self.calendars

    def find_calendar(self, summary):
        return next((cid for cid, c in self.calendars.items() if c["summary"] == summary), None)

    def create_calendar(self, summary, description, time_zone):
        cid = f"fake{len(self.calendars) + 1}@group.calendar.google.com"
        self.calendars[cid] = {"summary": summary, "description": description, "timeZone": time_zone,
                               "readers": set()}
        return cid

    def calendar_info(self, calendar_id):
        c = self.calendars[calendar_id]
        return {"summary": c["summary"], "description": c["description"]}

    def rename_calendar(self, calendar_id, summary, description):
        self.calls.append(("rename", calendar_id, None))
        self.calendars[calendar_id].update(summary=summary, description=description)

    def readers(self, calendar_id):
        return set(self.calendars[calendar_id]["readers"])

    def add_reader(self, calendar_id, email):
        self.calendars[calendar_id]["readers"].add(email.lower())


class GoogleCalendar(CalendarClient):
    """Calendar API v3 with service-account credentials (no browser login, no refresh-token expiry)."""

    def __init__(self, service):
        self.service = service

    @classmethod
    def from_service_account(cls, path: str) -> "GoogleCalendar":
        import httplib2
        from google.oauth2 import service_account
        from google_auth_httplib2 import AuthorizedHttp
        from googleapiclient.discovery import build

        credentials = service_account.Credentials.from_service_account_file(path, scopes=SCOPES)
        # A timeout, so a hung request stalls only this worker, briefly.
        http = AuthorizedHttp(credentials, http=httplib2.Http(timeout=REQUEST_TIMEOUT_S))
        # The bundled (static) discovery document: building needs no network.
        return cls(build("calendar", "v3", http=http, cache_discovery=False))

    @staticmethod
    def _run(request):
        from googleapiclient.errors import HttpError

        try:
            return request.execute()
        except HttpError as exc:
            status = int(getattr(exc.resp, "status", 0) or 0) or None
            raise CalendarError(status, getattr(exc, "reason", "") or "calendar API error") from None
        except (OSError, TimeoutError) as exc:
            raise CalendarError(None, f"network error ({type(exc).__name__})") from None

    def insert(self, calendar_id, event_id, body):
        self._run(self.service.events().insert(calendarId=calendar_id, body=dict(body, id=event_id),
                                               sendUpdates="none"))

    def patch(self, calendar_id, event_id, body):
        self._run(self.service.events().patch(calendarId=calendar_id, eventId=event_id, body=body,
                                              sendUpdates="none"))

    def delete(self, calendar_id, event_id):
        self._run(self.service.events().delete(calendarId=calendar_id, eventId=event_id, sendUpdates="none"))

    def calendar_exists(self, calendar_id):
        try:
            self._run(self.service.calendars().get(calendarId=calendar_id))
            return True
        except CalendarError as exc:
            if exc.status in (404, 410):
                return False
            raise

    def find_calendar(self, summary):
        token = None
        while True:
            page = self._run(self.service.calendarList().list(pageToken=token, minAccessRole="owner"))
            for item in page.get("items", []):
                if item.get("summary") == summary:
                    return item["id"]
            token = page.get("nextPageToken")
            if not token:
                return None

    def create_calendar(self, summary, description, time_zone):
        created = self._run(self.service.calendars().insert(
            body={"summary": summary, "description": description, "timeZone": time_zone}))
        return created["id"]

    def calendar_info(self, calendar_id):
        cal = self._run(self.service.calendars().get(calendarId=calendar_id))
        return {"summary": cal.get("summary", ""), "description": cal.get("description", "")}

    def rename_calendar(self, calendar_id, summary, description):
        self._run(self.service.calendars().patch(calendarId=calendar_id,
                                                 body={"summary": summary, "description": description}))

    def readers(self, calendar_id):
        rules =self._run(self.service.acl().list(calendarId=calendar_id)).get("items", [])
        return {r["scope"]["value"].lower() for r in rules
                if r.get("scope", {}).get("type") == "user" and r.get("role") in ("reader", "writer", "owner")}

    def add_reader(self, calendar_id, email):
        self._run(self.service.acl().insert(
            calendarId=calendar_id, sendNotifications=True,
            body={"role": "reader", "scope": {"type": "user", "value": email}}))


def key_path() -> str:
    """The service-account key file; a relative path in .env is relative to the project, not the cwd."""
    path = os.path.expanduser(config.GOOGLE_SERVICE_ACCOUNT_FILE)
    return path if os.path.isabs(path) else os.path.join(config.BASE_DIR, path)


def build_client() -> tuple:
    """(client, None) if Calendar sync can run, else (None, why not). Touches no network."""
    if not config.CALENDAR_SYNC_ENABLED:
        return None, "turned off (CALENDAR_SYNC_ENABLED=false)"
    path = key_path()
    if not os.path.isfile(path):
        shown = os.path.relpath(path, config.BASE_DIR) if path.startswith(config.BASE_DIR) else path
        return None, f"no service-account key at {shown} (see README, Google Calendar)"
    try:
        import google.oauth2.service_account  # noqa: F401
        import google_auth_httplib2  # noqa: F401
        import googleapiclient.discovery  # noqa: F401
    except ImportError:
        return None, "google-api-python-client is not installed (pip install -r requirements.txt)"
    try:
        return GoogleCalendar.from_service_account(path), None
    except Exception as exc:
        # Type only: the message could quote the key file.
        return None, f"service-account key could not be loaded ({type(exc).__name__})"


# ---------------------------------------------------------------- what the calendar should show
def event_id_for(appointment_id: str) -> str:
    """Deterministic Google event id (base32hex alphabet: 0-9 and a-v)."""
    return "emma" + re.sub(r"[^0-9a-v]", "", appointment_id.lower())


def _split_address(address: Optional[str]) -> tuple:
    if not address or "/" not in address:
        return None, None
    calendar_id, event_id = address.rsplit("/", 1)
    return calendar_id, event_id


def event_body(appt: dict) -> dict:
    """The minimal event: service, doctor, branch, first name, phone's last 4 digits, appointment id."""
    first = (appt.get("patient_name") or "").split()
    first = first[0] if first else "Patient"
    last4 = (appt.get("caller_phone_e164") or "")[-4:]
    prefix = NEEDS_RESCHEDULE_PREFIX if appt["status"] == "needs_reschedule" else ""
    start, end = db.local(appt["start_utc"]), db.local(appt["end_utc"])
    return {
        "summary": f"{prefix}{appt['service']}: {first} ({last4})",
        "description": "\n".join([
            f"Service: {appt['service']}",
            f"Doctor: {appt['doctor']}",
            f"Branch: {appt['branch']}",
            f"Patient: {first}",
            f"Phone: ends {last4}",
            f"Appointment: {appt['id']}",
            "",
            "Mirrored from the Pearl Dental dashboard. Change it there; edits here are overwritten.",
        ]),
        "location": f"Pearl Dental, {appt['branch']}",
        "start": {"dateTime": start.isoformat(), "timeZone": config.CLINIC_TIMEZONE},
        "end": {"dateTime": end.isoformat(), "timeZone": config.CLINIC_TIMEZONE},
        "status": "confirmed",          # also restores an event deleted earlier (patch after 409)
        "reminders": {"useDefault": False},
        "extendedProperties": {"private": {"emma_appointment": appt["id"], "emma_version": str(appt["version"])}},
    }


# ---------------------------------------------------------------- outbox (db thread)
_LOAD_SQL = (
    "SELECT a.id, a.status, a.version, a.start_utc, a.end_utc, a.caller_phone_e164, a.calendar_event_id, "
    "p.name AS patient_name, s.name AS service, d.name AS doctor, b.name AS branch, b.calendar_id "
    "FROM appointments a JOIN patients p ON p.id = a.patient_id JOIN services s ON s.id = a.service_id "
    "JOIN doctors d ON d.id = a.doctor_id JOIN branches b ON b.id = a.branch_id WHERE a.id = ?"
)


def _due(conn, now_s: str, limit: int) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT appointment_id FROM sync_outbox WHERE status = 'pending' AND due_at <= ? "
        "ORDER BY due_at LIMIT ?", (now_s, limit))]


def _load(conn, appointment_id: str) -> Optional[dict]:
    row = conn.execute(_LOAD_SQL, (appointment_id,)).fetchone()
    return dict(row) if row else None


def _succeeded(conn, appointment_id: str, version: int, address: Optional[str]):
    with db.transaction(conn):
        conn.execute("UPDATE appointments SET calendar_event_id = ?, calendar_synced_version = ? WHERE id = ?",
                     (address, version, appointment_id))
        # If the appointment changed while we were talking to Google, its row
        # was reset for another pass: keep it.
        conn.execute("DELETE FROM sync_outbox WHERE appointment_id = ? AND "
                     "(SELECT version FROM appointments WHERE id = ?) = ?", (appointment_id, appointment_id, version))


def _failed(conn, appointment_id: str, version: int, error: str, now) -> Optional[str]:
    """Record a failed attempt and schedule the next; returns the row's new status."""
    with db.transaction(conn):
        row = conn.execute("SELECT o.attempts, a.version FROM sync_outbox o JOIN appointments a "
                           "ON a.id = o.appointment_id WHERE o.appointment_id = ?", (appointment_id,)).fetchone()
        if row is None or row["version"] != version:
            return None                       # changed mid-sync: the fresh row stands
        attempts = row["attempts"] + 1
        status = "failed" if attempts >= MAX_ATTEMPTS else "pending"
        delay = BACKOFF_S[min(attempts - 1, len(BACKOFF_S) - 1)]
        conn.execute("UPDATE sync_outbox SET attempts = ?, status = ?, last_error = ?, due_at = ? "
                     "WHERE appointment_id = ?",
                     (attempts, status, error[:300], db.utc_str(now + timedelta(seconds=delay)), appointment_id))
        return status


def _postpone(conn, appointment_id: str, error: str, now):
    """Not an attempt (nothing was sent): wait for the branch calendar to exist."""
    with db.transaction(conn):
        conn.execute("UPDATE sync_outbox SET last_error = ?, due_at = ? WHERE appointment_id = ?",
                     (error, db.utc_str(now + timedelta(seconds=NO_CALENDAR_RETRY_S)), appointment_id))


def _drop(conn, appointment_id: str):
    with db.transaction(conn):
        conn.execute("DELETE FROM sync_outbox WHERE appointment_id = ?", (appointment_id,))


def retry(conn, appointment_id: str, actor: str) -> bool:
    """Dashboard Retry: try this row again now, with a fresh attempt budget."""
    with db.transaction(conn):
        changed = conn.execute("UPDATE sync_outbox SET status = 'pending', attempts = 0, last_error = NULL, "
                               "due_at = ? WHERE appointment_id = ?", (db.now_str(), appointment_id)).rowcount
        if changed:
            conn.execute("INSERT INTO audit_events (ts, actor, action, entity, entity_id) "
                         "VALUES (?, ?, 'calendar_retry', 'appointment', ?)", (db.now_str(), actor, appointment_id))
    return bool(changed)


def retry_failed(conn, actor: str) -> int:
    """Dashboard "Retry all failed"."""
    ids = [r[0] for r in conn.execute("SELECT appointment_id FROM sync_outbox WHERE status = 'failed'")]
    return sum(retry(conn, appointment_id, actor) for appointment_id in ids)


def enqueue_all(conn) -> int:
    """Queue every current and future appointment for a fresh sync (after setting up calendars)."""
    now_s = db.now_str()
    with db.transaction(conn):
        return conn.execute(
            "INSERT INTO sync_outbox (appointment_id, due_at, attempts, status) "
            "SELECT id, ?, 0, 'pending' FROM appointments WHERE end_utc > ? AND status IN ('booked', 'needs_reschedule') "
            "ON CONFLICT (appointment_id) DO UPDATE SET due_at = excluded.due_at, attempts = 0, "
            "status = 'pending', last_error = NULL", (now_s, now_s)).rowcount


def outbox_counts(conn) -> dict:
    counts = {"pending": 0, "failed": 0}
    for row in conn.execute("SELECT status, COUNT(*) AS n FROM sync_outbox GROUP BY status"):
        counts[row["status"]] = row["n"]
    return counts


def outbox_rows(conn, limit: int = 200) -> list[dict]:
    """Outbox rows for the System page, failed first."""
    rows = conn.execute(
        "SELECT o.*, a.status AS appointment_status, a.start_utc, s.name AS service, b.name AS branch "
        "FROM sync_outbox o LEFT JOIN appointments a ON a.id = o.appointment_id "
        "LEFT JOIN services s ON s.id = a.service_id LEFT JOIN branches b ON b.id = a.branch_id "
        "ORDER BY o.status = 'failed' DESC, o.due_at LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- the worker
class CalendarWorker:
    """Drains the outbox one appointment at a time; Google calls run in a thread."""

    def __init__(self, client: CalendarClient, database=None, poll_s: Optional[float] = None, batch: int = 20):
        self.client = client
        self._db = database
        self.poll_s = config.CALENDAR_POLL_S if poll_s is None else poll_s
        self.batch = batch
        self._wake: Optional[asyncio.Event] = None
        self.last_ok: Optional[str] = None
        self.last_error: Optional[str] = None

    @property
    def db(self):
        return self._db or db.get_db()

    def notify(self):
        """Look at the outbox now instead of at the next poll."""
        if self._wake is not None:
            self._wake.set()

    async def run(self):
        self._wake = asyncio.Event()
        logger.info("Calendar sync worker started")
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Calendar sync pass failed (%s)", type(exc).__name__)
            try:
                await asyncio.wait_for(self._wake.wait(), self.poll_s)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def run_once(self, now=None) -> int:
        """Sync every due row; returns how many were handled."""
        now = clock.localize(now) if now is not None else clock.now()
        ids = await self.db.run(_due, db.utc_str(now), self.batch)
        for appointment_id in ids:
            await self.sync_one(appointment_id, now)
        return len(ids)

    async def _call(self, method, *args):
        await asyncio.to_thread(method, *args)

    async def sync_one(self, appointment_id: str, now) -> str:
        """Make the calendar match this appointment. Returns synced | postponed | pending | failed | dropped."""
        appt = await self.db.run(_load, appointment_id)
        if appt is None:
            await self.db.run(_drop, appointment_id)
            return "dropped"
        event_id = event_id_for(appt["id"])
        old_calendar, _ = _split_address(appt["calendar_event_id"])
        target = appt["calendar_id"]
        try:
            if appt["status"] == "cancelled":
                calendar = old_calendar or target
                if calendar:
                    await self._delete(calendar, event_id)
                address = None
            else:
                if not target:
                    error = f"No Google calendar for {appt['branch']} yet (run tools/setup_calendars.py)"
                    await self.db.run(_postpone, appointment_id, error, now)
                    return "postponed"
                if old_calendar and old_calendar != target:
                    await self._delete(old_calendar, event_id)      # moved to another branch
                body = event_body(appt)
                try:
                    await self._call(self.client.insert, target, event_id, body)
                except CalendarError as exc:
                    if exc.status != 409:
                        raise
                    await self._call(self.client.patch, target, event_id, body)
                address = f"{target}/{event_id}"
        except Exception as exc:
            error = str(exc) if isinstance(exc, CalendarError) else f"{type(exc).__name__}"
            status = await self.db.run(_failed, appointment_id, appt["version"], error, now)
            self.last_error = error
            logger.warning("Calendar sync of %s failed (%s)%s", appointment_id, error,
                           "; giving up until Retry" if status == "failed" else "")
            events.publish({"type": "sync", "call_id": None, "appointment_id": appointment_id,
                            "status": status or "pending", "error": error})
            return status or "pending"
        await self.db.run(_succeeded, appointment_id, appt["version"], address)
        self.last_ok = db.utc_str(now)
        events.publish({"type": "sync", "call_id": None, "appointment_id": appointment_id, "status": "synced",
                        "error": None})
        return "synced"

    async def _delete(self, calendar_id: str, event_id: str):
        try:
            await self._call(self.client.delete, calendar_id, event_id)
        except CalendarError as exc:
            if exc.status not in (404, 410):
                raise


# ---------------------------------------------------------------- process-wide worker
_worker: Optional[CalendarWorker] = None
_status: dict = {"enabled": False, "reason": "not started"}


def start_worker() -> Optional[asyncio.Task]:
    """Start the worker if Calendar sync can run (server lifespan); None if it stays off."""
    global _worker
    client, reason = build_client()
    if client is None:
        _worker = None
        _status.update(enabled=False, reason=reason)
        logger.info("Calendar sync off: %s", reason)
        return None
    _worker = CalendarWorker(client)
    _status.update(enabled=True, reason=None)
    return asyncio.create_task(_worker.run())


def stop_worker():
    global _worker
    _worker = None
    _status.update(enabled=False, reason="stopped")


def notify():
    if _worker is not None:
        _worker.notify()


def status() -> dict:
    """For /health (no counts: those need the database, see outbox_counts)."""
    out = dict(_status)
    if _worker is not None:
        out.update(last_ok=_worker.last_ok, last_error=_worker.last_error)
    return out


def calendars_configured(conn) -> dict:
    """Which branches have a calendar (for /health and the System page)."""
    rows = conn.execute("SELECT name, calendar_id FROM branches WHERE active = 1 ORDER BY id").fetchall()
    return {r["name"]: bool(r["calendar_id"]) for r in rows}
