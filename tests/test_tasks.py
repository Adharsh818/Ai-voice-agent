"""Staff tasks: create, list in priority order, done/reopen, audit and dashboard events."""

import asyncio
import unittest
from datetime import datetime, timedelta

import clock
import db
import events
import tasks
from support import TempClinic

NOW = datetime(2026, 10, 5, 9, 0)


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.clinic = TempClinic(now=NOW)
        self.clinic.__enter__()
        self.db = self.clinic.db
        events.reset()

    def tearDown(self):
        self.clinic.__exit__(None, None, None)
        events.reset()

    def run_db(self, fn, *args, **kwargs):
        return self.db.run_sync(fn, *args, **kwargs)

    def test_create_returns_id_and_normalises_the_phone(self):
        task_id = self.run_db(tasks.create_task, kind="callback", phone_e164="98765 43210", note=" Wants a person ",
                              call_id="c1")
        task = self.run_db(tasks.get_task, task_id)
        self.assertEqual(task["kind"], "callback")
        self.assertEqual(task["priority"], "normal")
        self.assertEqual(task["phone_e164"], "+919876543210")
        self.assertEqual(task["phone_display"], "9876543210")
        self.assertEqual(task["note"], "Wants a person")
        self.assertEqual(task["status"], "open")
        self.assertEqual(task["created_at"], db.utc_str(clock.now()))

    def test_unknown_kind_or_priority_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            self.run_db(tasks.create_task, kind="lunch")
        with self.assertRaises(ValueError):
            self.run_db(tasks.create_task, kind="callback", priority="asap")

    def test_due_at_accepts_a_datetime_or_a_utc_string(self):
        due = clock.now() + timedelta(hours=1)
        a = self.run_db(tasks.create_task, kind="callback", due_at=due)
        b = self.run_db(tasks.create_task, kind="callback", due_at="2026-10-05T05:30:00Z")
        self.assertEqual(self.run_db(tasks.get_task, a)["due_at"], db.utc_str(due))
        self.assertEqual(self.run_db(tasks.get_task, b)["due_at"], "2026-10-05T05:30:00Z")
        with self.assertRaises(ValueError):
            self.run_db(tasks.create_task, kind="callback", due_at="tomorrow")

    def test_list_is_most_urgent_first_then_oldest(self):
        normal = self.run_db(tasks.create_task, kind="callback")
        urgent = self.run_db(tasks.create_task, kind="emergency", priority="urgent")
        high = self.run_db(tasks.create_task, kind="escalation", priority="high")
        self.assertEqual([t["id"] for t in self.run_db(tasks.list_tasks)], [urgent, high, normal])
        self.assertEqual(self.run_db(tasks.open_counts), {"urgent": 1, "high": 1, "normal": 1})

    def test_done_and_reopen(self):
        task_id = self.run_db(tasks.create_task, kind="callback")
        self.assertTrue(self.run_db(tasks.mark_done, task_id, "staff"))
        self.assertFalse(self.run_db(tasks.mark_done, task_id, "staff"))      # already done
        self.assertEqual(self.run_db(tasks.list_tasks, "open"), [])
        done = self.run_db(tasks.list_tasks, "done")
        self.assertEqual((done[0]["id"], done[0]["done_by"]), (task_id, "staff"))
        self.assertTrue(self.run_db(tasks.reopen, task_id, "staff"))
        task = self.run_db(tasks.get_task, task_id)
        self.assertEqual((task["status"], task["done_by"]), ("open", None))
        self.assertEqual(len(self.run_db(tasks.list_tasks, "all")), 1)
        self.assertFalse(self.run_db(tasks.mark_done, 999, "staff"))

    def test_every_change_is_audited(self):
        task_id = self.run_db(tasks.create_task, kind="red_flag", priority="urgent", call_id="c9")
        self.run_db(tasks.mark_done, task_id, "staff")
        rows = self.run_db(lambda conn: [dict(r) for r in conn.execute(
            "SELECT actor, action, correlation_id FROM audit_events WHERE entity = 'task' ORDER BY id")])
        self.assertEqual([(r["actor"], r["action"]) for r in rows], [("emma", "create"), ("staff", "done")])
        self.assertEqual(rows[0]["correlation_id"], "c9")

    def test_joins_a_transaction_the_caller_already_opened(self):
        def together(conn):
            with db.transaction(conn):
                tasks.create_task(conn, kind="callback", note="first")
                raise RuntimeError("the booking failed")

        with self.assertRaises(RuntimeError):
            self.run_db(together)
        self.assertEqual(self.run_db(tasks.list_tasks, "all"), [])      # rolled back with it

    def test_dashboard_hears_about_new_and_closed_tasks(self):
        async def scenario():
            async with events.subscribe() as stream:
                task_id = await self.db.run(tasks.create_task, kind="emergency", priority="urgent", call_id="c2")
                created = await stream.get(timeout=1)
                await self.db.run(tasks.mark_done, task_id, "staff")
                updated = await stream.get(timeout=1)
                return task_id, created, updated

        task_id, created, updated = asyncio.run(scenario())
        self.assertEqual((created["type"], created["task_id"], created["priority"], created["call_id"]),
                         ("task_created", task_id, "urgent", "c2"))
        self.assertEqual((updated["type"], updated["status"]), ("task_updated", "done"))


if __name__ == "__main__":
    unittest.main()
