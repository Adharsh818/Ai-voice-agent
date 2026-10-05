"""Shared test fixtures: a throwaway SQLite clinic behind db.get_db()."""

import os
import tempfile
from datetime import datetime

import clock
import config
import db

SERVICES = [
    ("General Check-up", 30), ("Consultation", 30), ("Teeth Cleaning", 30), ("Tooth Filling", 45),
    ("Tooth Extraction", 45), ("Root Canal Treatment", 60), ("Braces", 30), ("Invisalign", 30),
    ("Pediatric Dentistry", 30),
]


class TempClinic:
    """
    Point the app at a fresh database with one branch (config.DEFAULT_BRANCH)
    and one doctor who does every service Monday-Saturday 07:00-21:00, so tests
    see only the clinic-wide rules. Optionally freeze the clinic clock.

        with TempClinic(now=datetime(2026, 8, 20, 9, 0)):
            ...
    """

    def __init__(self, now: datetime = None):
        self.now = now
        self._frozen = None

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (config.DB_PATH, config.DEMO_SEED_ON_EMPTY)
        db.reset()
        config.DB_PATH = os.path.join(self.tmp.name, "emma.db")
        config.DEMO_SEED_ON_EMPTY = False
        self.db = db.get_db()
        self.db.run_sync(self._catalog)
        if self.now is not None:
            self._frozen = clock.frozen(self.now)
            self._frozen.__enter__()
        return self

    @staticmethod
    def _catalog(conn):
        with db.transaction(conn):
            conn.execute("INSERT INTO branches (id, name, area) VALUES (1, ?, 'Bengaluru')", (config.DEFAULT_BRANCH,))
            conn.execute("INSERT INTO doctors (id, name, spoken_name, gender, branch_id) "
                         "VALUES (1, 'Dr. Test', 'Dr Test', 'female', 1)")
            for i, (name, minutes) in enumerate(SERVICES, start=1):
                conn.execute("INSERT INTO services (id, name, duration_min) VALUES (?, ?, ?)", (i, name, minutes))
                conn.execute("INSERT INTO doctor_services VALUES (1, ?)", (i,))
            for wd in range(6):
                conn.execute("INSERT INTO availability_rules (doctor_id, weekday, start_time, end_time) "
                             "VALUES (1, ?, '07:00', '21:00')", (wd,))

    def __exit__(self, *exc):
        if self._frozen is not None:
            self._frozen.__exit__(*exc)
        db.reset()
        config.DB_PATH, config.DEMO_SEED_ON_EMPTY = self.saved
        self.tmp.cleanup()
        return False
