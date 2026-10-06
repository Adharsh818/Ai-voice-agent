"""Phase E operations: database backups (tools/backup.py) and log rotation (latency.LatencyLog)."""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import backup  # noqa: E402
import config  # noqa: E402
from dialogue.testing import DemoClinic  # noqa: E402
from latency import LatencyLog, TurnTimer  # noqa: E402


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.clinic = DemoClinic(appointments=True)
        self.clinic.__enter__()
        self.tmp = tempfile.TemporaryDirectory()
        self.dest = Path(self.tmp.name) / "backups"

    def tearDown(self):
        self.clinic.__exit__(None, None, None)
        self.tmp.cleanup()

    def count(self, path):
        conn = sqlite3.connect(path)
        try:
            return conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0]
        finally:
            conn.close()

    def test_backup_while_open_is_complete_and_healthy(self):
        live = config.DB_PATH                       # DemoClinic keeps it open, like a running Emma
        copy = backup.backup(live, self.dest)
        self.assertEqual(backup.check(str(copy)), [])
        self.assertEqual(self.count(copy), self.count(live))
        self.assertGreater(self.count(copy), 0)

    def test_only_the_newest_copies_are_kept(self):
        start = datetime(2026, 10, 1, 2, 0)
        for day in range(5):
            backup.backup(config.DB_PATH, self.dest, keep=3, now=start + timedelta(days=day))
        names = sorted(p.name for p in self.dest.glob("emma-*.db"))
        self.assertEqual(names, ["emma-20261003-0200.db", "emma-20261004-0200.db", "emma-20261005-0200.db"])

    def test_restore_brings_the_copy_back(self):
        copy = backup.backup(config.DB_PATH, self.dest)
        target = os.path.join(self.tmp.name, "restored.db")
        with open(target, "wb") as fh:
            fh.write(b"")                               # an empty file standing in for a damaged database
        kept = backup.restore(copy, target)
        self.assertTrue(kept.exists())
        self.assertEqual(self.count(target), self.count(copy))

    def test_check_finds_a_booking_without_its_slot_claims(self):
        copy = backup.backup(config.DB_PATH, self.dest)
        conn = sqlite3.connect(copy)
        conn.execute("DELETE FROM slot_claims WHERE appointment_id = (SELECT id FROM appointments "
                     "WHERE status = 'booked' LIMIT 1)")
        conn.commit()
        conn.close()
        self.assertTrue(any("slot claims" in p for p in backup.check(str(copy))))


class RotationTests(unittest.TestCase):
    def test_turn_log_rolls_over_and_keeps_n_files(self):
        with tempfile.TemporaryDirectory() as d:
            log = LatencyLog(d, max_bytes=600, backups=2)
            for turn in range(40):
                timer = TurnTimer(call_id="c1", turn=turn)
                log.add(timer)
            files = sorted(p.name for p in Path(d).iterdir())
            self.assertEqual(files, ["turns.jsonl", "turns.jsonl.1", "turns.jsonl.2"])
            for name in files:
                self.assertLessEqual(os.path.getsize(os.path.join(d, name)), 600 + 400)
                for line in Path(d, name).read_text(encoding="utf-8").splitlines():
                    json.loads(line)                    # whole records only, never split


if __name__ == "__main__":
    unittest.main()
