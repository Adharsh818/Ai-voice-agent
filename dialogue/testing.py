"""
Test fixtures for the R2 engine: a throwaway clinic seeded with the real DEMO
catalog (4 branches, 8 doctors, 9 services), so branch-aware booking is tested
against the same structure the owner talks to. tests/support.TempClinic stays
for the single-branch tests of the old engine.

    from dialogue.testing import DemoClinic

    with DemoClinic(now=datetime(2026, 10, 1, 10, 0)):          # a Thursday
        ...                                                     # db.get_db() is the temp clinic

    with DemoClinic(now=..., appointments=True):                # plus ~40 sample bookings
        ...
"""

import os
import tempfile
from datetime import datetime
from typing import Optional

import clock
import config
import db
import seed_demo

# A weekday morning inside the demo week, so "tomorrow" and "next Monday" are bookable.
DEFAULT_NOW = datetime(2026, 10, 1, 10, 0)


class DemoClinic:
    """Point db.get_db() at a fresh DEMO-seeded database and freeze the clinic clock."""

    def __init__(self, now: Optional[datetime] = DEFAULT_NOW, appointments: bool = False):
        self.now = now
        self.appointments = appointments
        self._frozen = None

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (config.DB_PATH, config.DEMO_SEED_ON_EMPTY)
        db.reset()
        config.DB_PATH = os.path.join(self.tmp.name, "emma.db")
        config.DEMO_SEED_ON_EMPTY = False
        if self.now is not None:
            self._frozen = clock.frozen(self.now)
            self._frozen.__enter__()
        self.db = db.get_db()
        self.db.run_sync(seed_demo.seed, appointments=self.appointments, now=self.now)
        return self

    def __exit__(self, *exc):
        if self._frozen is not None:
            self._frozen.__exit__(*exc)
        db.reset()
        config.DB_PATH, config.DEMO_SEED_ON_EMPTY = self.saved
        self.tmp.cleanup()
        return False

    def query(self, sql: str, *args) -> list:
        """Rows as dicts, for assertions on appointments, holds and tasks."""
        def _q(conn):
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
        return self.db.run_sync(_q)

    def appointments_for(self, phone_e164: str) -> list:
        return self.query("SELECT * FROM appointments WHERE caller_phone_e164 = ? ORDER BY start_utc", phone_e164)
