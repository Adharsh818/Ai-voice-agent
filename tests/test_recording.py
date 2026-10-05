"""Transcripts: call records, turn numbering, don't-keep requests, the 30-day purge and staff deletion."""

import asyncio
import json
import logging
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import clock
import config
import db
import events
import recording
import tasks
from recording import CallRecorder
from support import TempClinic

NOW = datetime(2026, 10, 5, 9, 0)


class RecorderTestCase(unittest.TestCase):
    def setUp(self):
        self.clinic = TempClinic(now=NOW)
        self.clinic.__enter__()
        self.db = self.clinic.db
        events.reset()

    def tearDown(self):
        self.clinic.__exit__(None, None, None)
        events.reset()

    def rows(self, sql, *args):
        return self.db.run_sync(lambda conn: [dict(r) for r in conn.execute(sql, args)])

    def turns(self, call_id):
        return self.rows("SELECT * FROM call_turns WHERE call_id = ? ORDER BY turn, rowid", call_id)

    def call(self, call_id):
        return self.rows("SELECT * FROM calls WHERE id = ?", call_id)[0]


class CallRecorderTests(RecorderTestCase):
    def test_a_call_is_stored_with_its_turns_and_outcome(self):
        rec = CallRecorder("c1")
        rec.start()
        rec.turn("emma", "Hi, this is Emma at Pearl Dental, how can I help?", {"tier": -1})
        rec.turn("caller", "I'd like a cleaning on Monday")
        rec.turn("emma", "Sure. Morning or evening?",
                 {"tier": 1, "goal_before": "service", "goal_after": "time", "latency": {"perceived_ms": 1210.0},
                  "entities": {"service": "Teeth Cleaning"}, "action": None})
        rec.end("booked", caller_phone="98765 43210")

        call = self.call("c1")
        self.assertEqual((call["direction"], call["outcome"], call["recording_consent"]), ("inbound", "booked", 1))
        self.assertEqual(call["started_at"], db.utc_str(clock.now()))
        self.assertEqual(call["ended_at"], db.utc_str(clock.now()))
        self.assertEqual(call["caller_phone_e164"], "+919876543210")
        self.assertEqual(call["purge_after"], db.utc_str(clock.now() + timedelta(days=config.TRANSCRIPT_RETENTION_DAYS)))

        turns = self.turns("c1")
        self.assertEqual([(t["turn"], t["role"]) for t in turns], [(0, "emma"), (1, "caller"), (1, "emma")])
        reply = turns[2]
        self.assertEqual((reply["tier"], reply["state_before"], reply["state_after"]), (1, "service", "time"))
        self.assertEqual(json.loads(reply["latency_json"]), {"perceived_ms": 1210.0})
        self.assertEqual(json.loads(reply["entities_json"]), {"entities": {"service": "Teeth Cleaning"}})
        self.assertIsNone(turns[1]["entities_json"])

    def test_turn_numbers_follow_exchanges(self):
        rec = CallRecorder("c2")
        for role in ["emma", "caller", "emma", "emma", "user", "assistant", "caller", "caller"]:
            rec.turn(role, "x")
        rec.end("ended")
        self.assertEqual([(t["turn"], t["role"]) for t in self.turns("c2")],
                         [(0, "emma"), (1, "caller"), (1, "emma"), (2, "emma"), (3, "caller"), (3, "emma"),
                          (4, "caller"), (5, "caller")])

    def test_unknown_roles_are_ignored_and_turn_before_start_opens_the_call(self):
        rec = CallRecorder("c3", direction="outbound")
        with self.assertLogs("recording", level="WARNING"):
            rec.turn("robot", "beep")
        rec.turn("caller", "Hello?")
        rec.end(None)
        self.assertEqual(self.call("c3")["direction"], "outbound")
        self.assertEqual(self.call("c3")["outcome"], "ended")
        self.assertEqual([t["role"] for t in self.turns("c3")], ["caller"])

    def test_start_and_end_count_once(self):
        rec = CallRecorder("c4")
        rec.start()
        rec.start()
        rec.end("booked")
        rec.end("abandoned")
        self.assertEqual(self.call("c4")["outcome"], "booked")
        self.assertEqual(len(self.rows("SELECT * FROM calls")), 1)

    def test_dont_keep_request_blanks_the_transcript_at_hang_up(self):
        rec = CallRecorder("c5")
        rec.turn("emma", "Hi, Pearl Dental.")
        rec.turn("caller", "My name is Rahul, please don't keep my details",
                 {"entities": {"name": "Rahul"}})
        rec.end("booked", keep_transcript=False)
        rec.turn("emma", "Bye now.")                  # anything after is stored blank too
        self.assertEqual(self.call("c5")["recording_consent"], 0)
        turns = self.turns("c5")
        self.assertEqual(len(turns), 3)
        self.assertTrue(all(t["text"] is None and t["entities_json"] is None for t in turns))

    def test_async_writes_keep_their_order_and_never_block(self):
        async def scenario():
            rec = CallRecorder("c6")
            rec.start()
            for i in range(30):
                rec.turn("caller" if i % 2 else "emma", f"line {i}")
            rec.end("completed")
            await rec.flush()

        asyncio.run(scenario())
        turns = self.turns("c6")
        self.assertEqual([t["text"] for t in turns], [f"line {i}" for i in range(30)])
        self.assertEqual(self.call("c6")["outcome"], "completed")

    def test_never_raises_into_the_call(self):
        rec = CallRecorder("c7")
        with self.assertLogs("recording", level="WARNING"),                 patch.object(recording.db, "get_db", side_effect=RuntimeError("disk on fire")):
            rec.start()
            rec.turn("caller", "hello")
            rec.end("ended")
        # A write that fails inside the database is swallowed too.
        broken = CallRecorder("c8")
        broken.started = True                         # no calls row: the turn violates its foreign key
        with self.assertLogs("recording", level="WARNING"):
            broken.turn("caller", "hello", {"weird": object()})
        self.assertEqual(self.rows("SELECT * FROM call_turns WHERE call_id = 'c8'"), [])

    def test_transcript_text_never_reaches_the_logs(self):
        secret = "my card is under the mat"
        with self.assertLogs(level="DEBUG") as logs:
            rec = CallRecorder("c9")
            rec.turn("caller", secret)
            rec.end("ended")
            broken = CallRecorder("c10")
            broken.started = True
            broken.turn("caller", secret)
        self.assertNotIn(secret, "\n".join(logs.output))

    def test_events_reach_the_live_panel(self):
        async def scenario():
            async with events.subscribe() as stream:
                rec = CallRecorder("c11")
                rec.start()
                rec.turn("caller", "Hi", {"tier": 0, "goal_after": "name"})
                rec.end("abandoned")
                await rec.flush()
                return [await stream.get(timeout=1) for _ in range(3)]

        started, turn, ended = asyncio.run(scenario())
        self.assertEqual((started["type"], started["call_id"]), ("call_started", "c11"))
        self.assertEqual((turn["type"], turn["role"], turn["text"], turn["meta"]["goal_after"]),
                         ("call_turn", "caller", "Hi", "name"))
        self.assertEqual((ended["type"], ended["outcome"], ended["kept"]), ("call_ended", "abandoned", True))


class RetentionTests(RecorderTestCase):
    def record_call(self, call_id, when):
        with clock.frozen(when):
            rec = CallRecorder(call_id)
            rec.turn("caller", "I'm Priya, 98765 43210", {"entities": {"name": "Priya"}, "latency": {"nlu_ms": 800}})
            rec.end("booked")

    def test_purge_blanks_text_older_than_retention_only(self):
        self.record_call("old", NOW - timedelta(days=31))
        self.record_call("recent", NOW - timedelta(days=29))
        blanked = self.db.run_sync(recording.purge)
        self.assertEqual(blanked, 1)
        old, recent = self.turns("old")[0], self.turns("recent")[0]
        self.assertIsNone(old["text"])
        self.assertIsNone(old["entities_json"])
        self.assertEqual(json.loads(old["latency_json"]), {"nlu_ms": 800})    # timings carry no personal text
        self.assertEqual(self.call("old")["outcome"], "booked")                 # the call record stays
        self.assertEqual(recent["text"], "I'm Priya, 98765 43210")
        self.assertEqual(self.db.run_sync(recording.purge), 0)                  # idempotent
        self.assertEqual(recording.status()["last_purge"], db.utc_str(clock.now()))

    def test_purge_follows_the_retention_setting(self):
        self.record_call("week", NOW - timedelta(days=8))
        with patch.object(config, "TRANSCRIPT_RETENTION_DAYS", 7):
            self.assertEqual(self.db.run_sync(recording.purge), 1)

    def test_purge_loop_runs_at_startup(self):
        self.record_call("old", NOW - timedelta(days=40))

        async def scenario():
            task = asyncio.create_task(recording.purge_loop())
            for _ in range(50):
                await asyncio.sleep(0.02)
                if self.turns("old")[0]["text"] is None:
                    break
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())
        self.assertIsNone(self.turns("old")[0]["text"])

    def test_staff_delete_blanks_one_call_and_is_audited(self):
        self.record_call("a", NOW - timedelta(hours=1))
        self.record_call("b", NOW - timedelta(hours=1))
        self.db.run_sync(lambda conn: conn.execute("UPDATE calls SET caller_phone_e164 = '+919876543210'"))
        self.assertEqual(self.db.run_sync(recording.delete_call_data, "a", "staff"), 1)
        self.assertIsNone(self.turns("a")[0]["text"])
        self.assertIsNone(self.call("a")["caller_phone_e164"])
        self.assertEqual(self.call("a")["recording_consent"], 0)
        self.assertIsNotNone(self.turns("b")[0]["text"])
        audit = self.rows("SELECT * FROM audit_events WHERE action = 'delete_call_data'")
        self.assertEqual((audit[0]["actor"], audit[0]["entity"], audit[0]["entity_id"]), ("staff", "call", "a"))
        self.assertNotIn("Priya", json.dumps(audit))
        self.assertIsNone(self.db.run_sync(recording.delete_call_data, "nope", "staff"))


class ReadingTests(RecorderTestCase):
    def test_list_and_detail_for_the_dashboard(self):
        rec = CallRecorder("c1")
        rec.turn("emma", "Hi")
        rec.turn("caller", "Emergency, my face is swelling", {"entities": {"symptom": "swelling"}})
        rec.end("emergency", caller_phone="9876543210")
        self.db.run_sync(tasks.create_task, kind="emergency", priority="urgent", call_id="c1")
        later = CallRecorder("c2")
        later.turn("caller", "Hello")
        later.end("abandoned", keep_transcript=False)

        calls = self.db.run_sync(recording.list_calls)
        self.assertEqual({c["id"] for c in calls}, {"c1", "c2"})
        by_id = {c["id"]: c for c in calls}
        self.assertEqual((by_id["c1"]["turns"], by_id["c1"]["kept_turns"]), (2, 2))
        self.assertEqual((by_id["c2"]["turns"], by_id["c2"]["kept_turns"]), (1, 0))
        self.assertEqual(by_id["c1"]["phone_display"], "9876543210")

        detail = self.db.run_sync(recording.get_call, "c1")
        self.assertEqual([t["role"] for t in detail["turns"]], ["emma", "caller"])
        self.assertEqual(detail["turns"][1]["entities"], {"entities": {"symptom": "swelling"}})
        self.assertEqual([t["kind"] for t in detail["tasks"]], ["emergency"])
        self.assertEqual(detail["appointments"], [])
        self.assertIsNone(self.db.run_sync(recording.get_call, "missing"))


if __name__ == "__main__":
    logging.basicConfig()
    unittest.main()
