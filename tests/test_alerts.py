"""Alerts for staff (alerts.py): what counts as a problem, and that each one opens and clears once."""

import unittest
from datetime import datetime, timedelta

import alerts
import clock
import db

OK = {"gemini": True, "deepgram": True, "elevenlabs": True}


class AlertTests(unittest.TestCase):
    def setUp(self):
        alerts._open.clear()
        self.now = clock.localize(datetime(2026, 10, 12, 11, 0))

    def test_all_well_is_quiet(self):
        line = {"call_id": "c1", "since": db.utc_str(self.now - timedelta(minutes=5))}
        self.assertEqual(alerts.check(OK, 0, [line], self.now), [])

    def test_each_problem(self):
        stuck = {"call_id": "c9", "since": db.utc_str(self.now - timedelta(minutes=25))}
        found = alerts.check({**OK, "gemini": False, "deepgram": False}, 3, [stuck], self.now)
        self.assertEqual({a["key"]: a["level"] for a in found},
                         {"deepgram": "error", "gemini": "warn", "calendar": "warn", "line:c9": "error"})
        self.assertIn("25 minutes", next(a["text"] for a in found if a["key"] == "line:c9"))

    def test_open_and_clear_once(self):
        with self.assertLogs("alerts", "INFO") as logs:
            alerts.update(alerts.check({**OK, "elevenlabs": False}, 0, [], self.now), self.now)
            alerts.update(alerts.check({**OK, "elevenlabs": False}, 0, [], self.now), self.now + timedelta(minutes=5))
            self.assertEqual(len(alerts.current()), 1)
            self.assertEqual(alerts.current()[0]["since"], db.utc_str(self.now))      # first seen, not re-stamped
            alerts.update(alerts.check(OK, 0, [], self.now), self.now)
        self.assertEqual(alerts.current(), [])
        self.assertEqual(sum("ALERT" in m for m in logs.output), 1)
        self.assertEqual(sum("cleared" in m for m in logs.output), 1)


if __name__ == "__main__":
    unittest.main()
