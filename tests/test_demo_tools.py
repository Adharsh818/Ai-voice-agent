"""tools/demo_reset.py and tools/preflight.py (Day 7)."""

import os
import sys
import tempfile
import unittest
from datetime import date, datetime

import calendar_sync
import clock
import config
import db
import scheduling
import seed_demo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import demo_reset  # noqa: E402


class DemoResetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "emma.db")
        self.frozen = clock.frozen(datetime(2026, 10, 7, 9, 0))
        self.frozen.__enter__()

    def tearDown(self):
        self.frozen.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_fixtures_are_bookable_around_the_demo_date(self):
        conn = db.connect(self.path)
        db.migrate(conn)
        seed_demo.seed_catalog(conn)
        made = demo_reset.book_fixtures(conn, date(2026, 10, 8))
        self.assertTrue(all(ok for *_, ok, _code in made), made)
        rao = [m for m in made if m[4] == "Dr Rao"]
        self.assertEqual({m[5].date() for m in rao}, {date(2026, 10, 9)})       # the recovery day
        seed_demo.seed_appointments(conn)                                         # random ones fit around them
        conn.close()

    def test_only_emmas_events_are_removed(self):
        conn = db.connect(self.path)
        db.migrate(conn)
        seed_demo.seed_catalog(conn)
        demo_reset.book_fixtures(conn, date(2026, 10, 8))
        fake = calendar_sync.FakeCalendar()
        with db.transaction(conn):
            conn.execute("UPDATE branches SET calendar_id = 'cal-' || id")
        ids = [r["id"] for r in conn.execute("SELECT id, branch_id FROM appointments")]
        with db.transaction(conn):
            conn.execute("UPDATE appointments SET calendar_event_id = 'x' WHERE id = ?", (ids[0],))
        events = demo_reset.created_events(conn)
        self.assertEqual(len(events), 1)
        cal, event = events[0]
        fake.insert(cal, event, {"summary": "Consultation"})
        fake.insert(cal, "someone-elses-event", {"summary": "Staff meeting"})
        removed, gone, failed = demo_reset.clear_calendar(fake, events + [(cal, "emma-already-gone")])
        self.assertEqual((removed, gone, failed), (1, 1, 0))
        self.assertIn((cal, "someone-elses-event"), fake.live(cal))
        conn.close()

    def test_refuses_while_emma_is_running(self):
        saved = demo_reset.server_running
        demo_reset.server_running = lambda: True
        try:
            sys.argv = ["demo_reset.py"]
            self.assertEqual(demo_reset.main(), 1)
        finally:
            demo_reset.server_running = saved


if __name__ == "__main__":
    unittest.main()
