"""
The clinic's clock.

Every "today", "tomorrow" and "is this slot in the past?" decision goes through
here, so it is always answered in the clinic's timezone (Asia/Kolkata) rather
than the server's. On a UTC VM, date.today() is still "yesterday" for callers
until 05:30 IST — exactly when early-morning calls come in.

Tests freeze the clock:

    with clock.frozen(datetime(2026, 9, 30, 20, 50)):
        ...   # naive datetimes are taken as clinic-local time
"""

from contextlib import contextmanager
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

import config

TZ = ZoneInfo(config.CLINIC_TIMEZONE)

_frozen: Optional[datetime] = None


def now() -> datetime:
    """Current time as a timezone-aware datetime in the clinic's timezone."""
    if _frozen is not None:
        return _frozen
    return datetime.now(TZ)


def today() -> date:
    """Today's date in the clinic's timezone."""
    return now().date()


def localize(dt: datetime) -> datetime:
    """Attach the clinic timezone to a naive datetime; convert an aware one."""
    return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)


def freeze(dt: datetime):
    """Pin now() to `dt` (naive = clinic-local). Prefer the `frozen` context manager."""
    global _frozen
    _frozen = localize(dt)


def unfreeze():
    global _frozen
    _frozen = None


@contextmanager
def frozen(dt: datetime):
    previous = _frozen
    freeze(dt)
    try:
        yield
    finally:
        globals()["_frozen"] = previous
