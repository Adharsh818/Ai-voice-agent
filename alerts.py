"""
Alerts for staff (the audit, 11 Oct: "alerting on degraded health, failed
sync, stuck lines"). Every ALERT_INTERVAL_S the server checks:

- a speech service key missing, or the language model unavailable (Emma keeps
  answering on her own lines, but staff should know);
- calendar sync stalled: appointments waiting over 30 minutes past their due time;
- a call line held longer than any call can last (call_session.MAX_CALL_S):
  a stuck line turns real callers away as "busy".

Open alerts show as a banner on every dashboard tab (/dashboard/api/overview)
and are logged once when they start and once when they clear.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

import clock
import db

logger = logging.getLogger(__name__)

ALERT_INTERVAL_S = 300
SYNC_STALL_MIN = 30
STUCK_LINE_MIN = 20                  # calls end by 15.5 minutes (call_session.MAX_CALL_S + 30 s)

_open: dict = {}                     # key -> {"key", "level", "text", "since"}


def check(health: dict, overdue_sync: int, lines: list, now: Optional[datetime] = None) -> list[dict]:
    """The problems right now (pure: the loop gathers the inputs). level: "error" or "warn"."""
    now = now or clock.now()
    out = []
    for service, label in (("deepgram", "Speech recognition (Deepgram)"), ("elevenlabs", "Emma's voice (ElevenLabs)")):
        if health.get(service) is False:
            out.append({"key": service, "level": "error",
                        "text": f"{label} isn't set up or isn't answering; calls can't work properly."})
    if health.get("gemini") is False:
        out.append({"key": "gemini", "level": "warn",
                    "text": "The language model is unavailable; Emma is answering with her own lines only."})
    if overdue_sync:
        out.append({"key": "calendar", "level": "warn",
                    "text": f"Google Calendar sync is stuck: {overdue_sync} appointment(s) waiting over "
                            f"{SYNC_STALL_MIN} minutes."})
    for line in lines:
        try:
            since = datetime.fromisoformat(str(line.get("since")).replace("Z", "+00:00"))
        except ValueError:
            continue
        minutes = int((now - since).total_seconds() // 60)
        if minutes >= STUCK_LINE_MIN:
            out.append({"key": f"line:{line.get('call_id')}", "level": "error",
                        "text": f"A call line has been busy for {minutes} minutes; if nobody is on that call, "
                                "restart Emma so callers aren't turned away."})
    return out


def update(found: list[dict], now: Optional[datetime] = None) -> list[dict]:
    """Remember what's open; log changes. Returns the open alerts."""
    now = now or clock.now()
    keys = {a["key"] for a in found}
    for key in [k for k in _open if k not in keys]:
        logger.info("Alert cleared: %s", _open.pop(key)["text"])
    for alert in found:
        if alert["key"] not in _open:
            logger.warning("ALERT: %s", alert["text"])
            _open[alert["key"]] = {**alert, "since": db.utc_str(now)}
        else:
            _open[alert["key"]].update(text=alert["text"], level=alert["level"])
    return current()


def current() -> list[dict]:
    return sorted(_open.values(), key=lambda a: (a["level"] != "error", a["since"]))


def overdue_sync(conn, now: Optional[datetime] = None) -> int:
    """Calendar outbox rows still pending SYNC_STALL_MIN after they were due (db thread)."""
    cut = db.utc_str((now or clock.now()) - timedelta(minutes=SYNC_STALL_MIN))
    return conn.execute("SELECT COUNT(*) FROM sync_outbox WHERE status = 'pending' AND due_at < ?",
                        (cut,)).fetchone()[0]


async def loop(health, database, gate, calendar_on, interval_s: float = ALERT_INTERVAL_S):
    """Server background task: health() -> the /health services dict; gate: the CallGate."""
    while True:
        try:
            overdue = await database.run(overdue_sync) if calendar_on() else 0
            update(check(await health(), overdue, gate.status().get("calls") or []))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Alert check failed (%s)", type(exc).__name__)
        await asyncio.sleep(max(interval_s, 30))
