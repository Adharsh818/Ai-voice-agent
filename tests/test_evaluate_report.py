"""tools/evaluate.py: browser and phone calls reported apart (11 Oct)."""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import audiosocket  # noqa: E402
import evaluate  # noqa: E402
from latency import TurnTimer  # noqa: E402


def row(call_id, tier=0, perceived=1000.0, channel=None):
    r = {"ts": 1791447927.0, "call_id": call_id, "turn": 2, "tier": tier, "perceived_ms": perceived,
         "endpoint_ms": 500.0, "filler": False, "barge_in": False}
    if channel:
        r["channel"] = channel
    return r


class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "emma.db")
        conn = sqlite3.connect(self.db)
        conn.executescript("CREATE TABLE calls (id TEXT, caller_phone_e164 TEXT);"
                           "CREATE TABLE call_turns (call_id TEXT, role TEXT, text TEXT);")
        conn.executemany("INSERT INTO calls VALUES (?, ?)", [
            ("line", "+919845022222"),          # the phone line's caller ID
            ("asked", "+917899377462"),         # caller gave another number after the caller-ID question
            ("web", "+917899377462")])
        conn.executemany("INSERT INTO call_turns VALUES (?, ?, ?)", [
            ("asked", "emma", "Jimmy, got it. Is the number you're calling from the best one to reach you on?"),
            ("web", "emma", "And what's the best number to reach you on?")])
        conn.commit()
        conn.close()
        self.turns = Path(self.tmp.name) / "turns.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def test_old_phone_calls_found_by_caller_id_or_the_caller_id_question(self):
        with mock.patch.dict(os.environ, {"TELEPHONY_CALLER_NUMBER": "9845022222"}):
            self.assertEqual(evaluate.old_phone_calls(self.db), {"line", "asked"})

    def test_real_calls_split_by_channel(self):
        rows = [row("line"), row("asked", tier=1, perceived=2500.0), row("web"),
                row("new-phone", channel="phone"), row("new-web", channel="browser")]
        self.turns.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"TELEPHONY_CALLER_NUMBER": "9845022222"}), \
                mock.patch.object(evaluate, "TURNS", self.turns):
            ids = evaluate.old_phone_calls(self.db)
            phone = evaluate.real_calls(None, "phone", ids)
            browser = evaluate.real_calls(None, "browser", ids)
            both = evaluate.real_calls(None)
        self.assertEqual((phone["calls"], phone["turns"], len(phone["model"])), (3, 3, 1))
        self.assertEqual((browser["calls"], browser["turns"]), (2, 2))
        self.assertEqual(both["turns"], 5)

    def test_report_has_a_section_per_channel(self):
        rows = [row("line"), row("web")]
        self.turns.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"TELEPHONY_CALLER_NUMBER": "9845022222"}), \
                mock.patch.object(evaluate, "TURNS", self.turns):
            ids = evaluate.old_phone_calls(self.db)
            calls = {ch: evaluate.real_calls(None, ch, ids) for ch in ("browser", "phone")}
        text = evaluate.render({"scenarios": None, "sims": []}, calls, None, {"ran": 0, "failed": 0, "drills": []}, None)
        self.assertIn("### Browser calls", text)
        self.assertIn("### Phone calls", text)
        self.assertIn("1 replies in 1 phone calls through Asterisk", text)


class TurnLogChannelTests(unittest.TestCase):
    def test_turn_record_carries_the_channel(self):
        self.assertEqual(TurnTimer(call_id="c1", turn=1, channel="phone").to_record()["channel"], "phone")
        self.assertIsNone(TurnTimer(call_id="c1", turn=1).to_record()["channel"])

    def test_transports_name_their_channel(self):
        self.assertEqual(audiosocket.PhoneTransport.channel, "phone")
        import server
        self.assertEqual(server.BrowserTransport.channel, "browser")


if __name__ == "__main__":
    unittest.main()
