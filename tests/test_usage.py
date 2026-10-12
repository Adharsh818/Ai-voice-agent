"""Cost metering per call (usage.py): counted inside the call, priced at hang-up, shown on the dashboard."""

import asyncio
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

import clock
import config
import db
import usage


class MeterTests(unittest.TestCase):
    def test_tasks_of_the_call_add_to_its_meter_and_nothing_else_does(self):
        usage.add("tts_chars", 500)                          # outside a call: pre-rendering at start-up

        async def call(call_id, chars):
            meter = usage.begin(call_id)

            async def speak():
                usage.add("tts_chars", chars)
                usage.add("gemini_requests")
            await asyncio.gather(asyncio.create_task(speak()), asyncio.create_task(speak()))
            return meter

        async def both():
            return await asyncio.gather(call("a", 10), call("b", 7))
        a, b = asyncio.run(both())
        self.assertEqual((a["tts_chars"], a["gemini_requests"]), (20, 2))
        self.assertEqual((b["tts_chars"], b["gemini_requests"]), (14, 2))

    def test_price(self):
        meter = {"gemini_requests": 4, "gemini_in_tokens": 1_000_000, "gemini_out_tokens": 0, "tts_chars": 1000}
        with patch.multiple(config, COST_DEEPGRAM_USD_PER_MIN=0.01, COST_ELEVENLABS_USD_PER_1K_CHARS=0.1,
                            COST_GEMINI_USD_PER_M_IN=0.2, COST_GEMINI_USD_PER_M_OUT=0.4):
            out = usage.price(meter, 120)
        self.assertEqual(out["cost_usd"], {"stt": 0.02, "tts": 0.1, "llm": 0.2})
        self.assertAlmostEqual(out["total_usd"], 0.32)
        self.assertEqual(out["seconds"], 120)


class DashboardCostTests(unittest.TestCase):
    def test_cost_per_call_on_the_performance_panel(self):
        import dashboard
        with tempfile.TemporaryDirectory() as tmp, clock.frozen(datetime(2026, 10, 12, 10, 0)), \
                patch.object(config, "LOG_DIR", tmp):
            conn = db.connect(os.path.join(tmp, "t.db"))
            db.migrate(conn)
            for call_id, channel, total in (("c1", "phone", 0.03), ("c2", "phone", 0.05), ("c3", "browser", 0.02)):
                conn.execute("INSERT INTO calls (id, direction, started_at, channel) VALUES (?, 'inbound', ?, ?)",
                             (call_id, db.utc_str(clock.now()), channel))
                conn.commit()
                usage.save(conn, call_id, {"total_usd": total})
            out = dashboard._performance(conn)
            conn.close()
        self.assertEqual(out["channels"]["phone"]["metered_calls"], 2)
        self.assertAlmostEqual(out["channels"]["phone"]["cost_per_call_usd"], 0.04)
        self.assertAlmostEqual(out["channels"]["browser"]["cost_total_usd"], 0.02)


if __name__ == "__main__":
    unittest.main()
