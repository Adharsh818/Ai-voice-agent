"""
Messages to patients (docs/NOTIFICATIONS.md; owner, 11 Oct: built ready to
plug in, no provider account yet).

What a real clinic sends on WhatsApp or SMS: a confirmation when an
appointment is booked, moved or cancelled, and a reminder the day before.
scheduling.py queues them in the same transaction as the change itself
(table patient_messages), so a message can never describe a booking that
didn't happen; a worker sends what is due.

Senders (config.NOTIFY_SENDER):
- "log" (default): nothing leaves the machine; the message is written to
  the log (phone masked) and marked "logged". What every message would
  have said is on the dashboard.
- "twilio": WhatsApp or SMS through Twilio's Messages API, with
  TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM. In India, SMS needs
  DLT-registered templates and a sender ID first; WhatsApp needs approved
  templates for messages outside a 24-hour reply window (docs/NOTIFICATIONS.md).

A number on the do-not-call list gets no messages; a moved or cancelled
appointment's pending reminder is dropped.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

import clock
import config
import db
import logredact

logger = logging.getLogger(__name__)

KINDS = ("booked", "moved", "cancelled", "reminder")
MAX_ATTEMPTS = 5

# Short, fixed wording with a few variables: what DLT and WhatsApp template approval need.
TEMPLATES = {
    "booked": "Hi {name}, your {service} at {clinic}, {branch} is booked for {when} with {doctor}. "
              "To change it, just call us. See you then!",
    "moved": "Hi {name}, your {service} at {clinic}, {branch} is now on {when} with {doctor}.",
    "cancelled": "Hi {name}, your {service} at {clinic}, {branch} on {when} has been cancelled. "
                 "Call us any time to book again.",
    "reminder": "Reminder from {clinic}: {name}, your {service} is on {when} at our {branch} branch with {doctor}. "
                "Please bring any old X-rays and your list of medicines.",
}


def _when(start_utc: str) -> str:
    local = db.local(start_utc)
    return local.strftime("%a %d %b, %I:%M %p").replace(" 0", " ").replace("AM", "am").replace("PM", "pm")


def render(kind: str, appt: dict) -> str:
    return TEMPLATES[kind].format(
        name=(appt.get("patient_name") or "there").split()[0], service=(appt.get("service") or "appointment").lower(),
        clinic=config.CLINIC_NAME, branch=appt.get("branch") or "", doctor=appt.get("doctor") or "the doctor",
        when=_when(appt["start_utc"]))


def _blocked(conn, phone: str) -> bool:
    row = conn.execute("SELECT do_not_call FROM contact_prefs WHERE phone_e164 = ?", (phone,)).fetchone()
    return bool(row and row[0])


def enqueue(conn, appt: Optional[dict], kind: str, now: datetime) -> None:
    """
    Queue the message for a change (inside the caller's transaction). Also
    keeps the reminder in step: one for a booked or moved appointment far
    enough ahead, none for a cancelled or moved-away time.
    """
    if not config.NOTIFY_ENABLED or appt is None or kind not in KINDS:
        return
    phone = appt.get("caller_phone_e164")
    if not phone or _blocked(conn, phone):
        return
    stamp = db.utc_str(now)
    channel = config.NOTIFY_CHANNEL
    conn.execute("UPDATE patient_messages SET status = 'skipped', last_error = 'appointment changed' "
                 "WHERE appointment_id = ? AND kind = 'reminder' AND status = 'pending'", (appt["id"],))
    conn.execute("INSERT INTO patient_messages (appointment_id, kind, channel, phone_e164, body, due_at, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)", (appt["id"], kind, channel, phone, render(kind, appt), stamp, stamp))
    if kind in ("booked", "moved"):
        start = db.parse_utc(appt["start_utc"])
        due = start - timedelta(hours=config.REMINDER_HOURS_BEFORE)
        if due > now + timedelta(hours=1):
            conn.execute("INSERT INTO patient_messages (appointment_id, kind, channel, phone_e164, body, due_at, "
                         "created_at) VALUES (?, 'reminder', ?, ?, ?, ?, ?)",
                         (appt["id"], channel, phone, render("reminder", appt), db.utc_str(due), stamp))


# ---------------------------------------------------------------- senders

class LogSender:
    """Sends nothing: logs the message (phone masked) so the setup can be checked end to end."""
    name = "log"

    async def send(self, channel: str, to: str, body: str) -> tuple:
        logger.info("[message %s to %s] %s", channel, logredact.mask_phones(to), body)
        return "logged", None


class TwilioSender:
    """WhatsApp or SMS through Twilio's REST API (Messages resource)."""
    name = "twilio"
    URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"

    def __init__(self, sid: str, token: str, sender: str, client=None):
        self.sid, self.token, self.sender = sid, token, sender
        self.client = client

    async def send(self, channel: str, to: str, body: str) -> tuple:
        import httpx
        prefix = "whatsapp:" if channel == "whatsapp" else ""
        data = {"From": f"{prefix}{self.sender}", "To": f"{prefix}{to}", "Body": body}
        client = self.client or httpx.AsyncClient(timeout=15)
        try:
            response = await client.post(self.URL.format(sid=self.sid), data=data, auth=(self.sid, self.token))
        finally:
            if self.client is None:
                await client.aclose()
        if response.status_code >= 300:
            raise RuntimeError(f"Twilio HTTP {response.status_code}: {response.text[:200]}")
        return "sent", (response.json() or {}).get("sid")


def build_sender():
    """(sender, reason it's the log sender or None)."""
    if config.NOTIFY_SENDER == "twilio":
        if config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN and config.TWILIO_FROM:
            return TwilioSender(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN, config.TWILIO_FROM), None
        return LogSender(), "NOTIFY_SENDER=twilio but TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM are not set"
    return LogSender(), "log only (no provider set up)"


# ---------------------------------------------------------------- the worker

def _due(conn, now: datetime, limit: int = 20) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM patient_messages WHERE status = 'pending' AND due_at <= ? ORDER BY due_at, id LIMIT ?",
        (db.utc_str(now), limit))]


def _still_wanted(conn, msg: dict) -> bool:
    """A reminder only for an appointment that's still booked at that time."""
    if msg["kind"] != "reminder":
        return True
    row = conn.execute("SELECT status, start_utc FROM appointments WHERE id = ?", (msg["appointment_id"],)).fetchone()
    return bool(row and row["status"] == "booked" and row["start_utc"] > db.utc_str(clock.now()))


def _mark(conn, msg_id: int, status: str, now: datetime, provider_id=None, error=None, retry_at=None):
    with db.transaction(conn):
        conn.execute("UPDATE patient_messages SET status = ?, attempts = attempts + 1, sent_at = ?, provider_id = ?, "
                     "last_error = ?, due_at = COALESCE(?, due_at) WHERE id = ?",
                     (status, db.utc_str(now) if status in ("sent", "logged") else None, provider_id, error,
                      db.utc_str(retry_at) if retry_at else None, msg_id))


async def send_due(database, sender, now: Optional[datetime] = None) -> int:
    """Send every message that is due; returns how many were handled."""
    now = now or clock.now()
    messages = await database.run(_due, now)
    for msg in messages:
        if not await database.run(_still_wanted, msg):
            await database.run(_mark, msg["id"], "skipped", now, None, "appointment no longer booked")
            continue
        try:
            status, provider_id = await sender.send(msg["channel"], msg["phone_e164"], msg["body"])
            await database.run(_mark, msg["id"], status, now, provider_id)
        except Exception as exc:                                  # noqa: BLE001 - retried with back-off
            failed = msg["attempts"] + 1 >= MAX_ATTEMPTS
            await database.run(_mark, msg["id"], "failed" if failed else "pending", now, None, str(exc)[:300],
                               None if failed else now + timedelta(minutes=2 ** (msg["attempts"] + 1)))
            logger.warning("message %s to %s failed: %s", msg["id"], logredact.mask_phones(msg["phone_e164"]), exc)
    return len(messages)


_status: dict = {"sender": "off", "reason": None}


async def run_worker(database, sender, poll_s: float = 15.0):
    while True:
        try:
            await send_due(database, sender)
        except Exception as exc:                                  # noqa: BLE001 - never stop the loop
            logger.warning("message worker: %s", exc)
        await asyncio.sleep(poll_s)


def start_worker(database) -> Optional[asyncio.Task]:
    """Start sending (server lifespan); None when messages are switched off."""
    if not config.NOTIFY_ENABLED:
        _status.update(sender="off", reason="NOTIFY_ENABLED=false")
        return None
    sender, reason = build_sender()
    _status.update(sender=sender.name, reason=reason)
    logger.info("Patient messages: %s%s", sender.name, f" ({reason})" if reason else "")
    return asyncio.create_task(run_worker(database, sender))


def status() -> dict:
    return dict(_status, channel=config.NOTIFY_CHANNEL)


def recent(conn, limit: int = 30) -> list:
    """The latest messages for the dashboard (phone shown nationally)."""
    import phones
    rows = [dict(r) for r in conn.execute(
        "SELECT id, appointment_id, kind, channel, phone_e164, body, status, attempts, due_at, sent_at, last_error "
        "FROM patient_messages ORDER BY id DESC LIMIT ?", (limit,))]
    for r in rows:
        r["phone_display"] = phones.national(r.pop("phone_e164"))
    return rows


def counts(conn) -> dict:
    out = {s: 0 for s in ("pending", "sent", "logged", "failed", "skipped")}
    for r in conn.execute("SELECT status, COUNT(*) AS n FROM patient_messages GROUP BY status"):
        out[r["status"]] = r["n"]
    return out
