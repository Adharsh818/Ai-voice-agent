"""Phase E operations: database backups (tools/backup.py) and log rotation (latency.LatencyLog)."""

import contextlib
import io
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
import clock  # noqa: E402
import config  # noqa: E402
import crash_drill  # noqa: E402
import db  # noqa: E402
import scheduling  # noqa: E402
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


class CrashDrillTests(unittest.TestCase):
    """tools/crash_drill.py: the writer is killed for real; the checks must also catch a broken database."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = crash_drill.prepare(Path(self.tmp.name), None)
        self.conn = db.connect(self.path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def op(self, call_id="drill-t-1"):
        days = [clock.now().date() + timedelta(days=d) for d in range(2, 10)]   # some days the clinic is closed
        slot = scheduling.find_slots(self.conn, service="Consultation", dates=days, limit=1)[0]
        return {"kind": "book", "idem": f"{call_id}:x", "call_id": call_id, "service": slot.service,
                "doctor_id": slot.doctor_id, "start": slot.start.isoformat(), "name": "Drill Patient",
                "phone": "+919812345678"}

    def test_killed_writers_leave_a_consistent_database(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = crash_drill.main(["--rounds", "3", "--seed", "5"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("RESULT: PASS", out.getvalue())

    def test_a_request_committed_before_the_kill_is_replayed_on_retry(self):
        op = self.op()
        self.assertTrue(crash_drill._apply(self.conn, op).ok)        # committed, never reported
        problems, outcome = crash_drill.check_round(self.conn, [], op)
        self.assertEqual(problems, [])
        self.assertIn("committed before the kill", outcome)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM appointments WHERE created_by_call_id = ?",
                                           (op["call_id"],)).fetchone()[0], 1)

    def test_a_half_written_request_is_reported(self):
        op = self.op()
        self.conn.execute("INSERT INTO actions VALUES (?, 'book', ?, '2026-10-06T00:00:00Z')",
                          (op["idem"], json.dumps({"ok": True, "code": "OK"})))
        problems, _ = crash_drill.check_round(self.conn, [], op)
        self.assertTrue(any("half written" in p for p in problems), problems)

    def test_a_lost_cancellation_is_reported(self):
        appt = self.conn.execute("SELECT id FROM appointments WHERE status = 'booked' LIMIT 1").fetchone()[0]
        done = [{"kind": "cancel", "ok": True, "idem": "drill-t-2:x", "appointment_id": appt}]
        problems, _ = crash_drill.check_round(self.conn, done, None)
        self.assertTrue(any("missing" in p for p in problems) and any("lost" in p for p in problems), problems)


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
