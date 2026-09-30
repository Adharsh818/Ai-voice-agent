"""Day 0 baseline: clinic clock, log redaction, socket origin check, latency diagnostics, dev capture."""

import asyncio
import io
import json
import logging
import os
import tempfile
import unittest
import wave
from datetime import date, datetime
from unittest.mock import patch

import backend_actions
import clock
import config
import llm
import logredact
from capture import CallCapture
from latency import LatencyLog, TurnTimer


class ClinicClockTests(unittest.TestCase):
    def test_frozen_naive_time_is_clinic_local(self):
        with clock.frozen(datetime(2026, 9, 30, 20, 50)):
            self.assertEqual(clock.now().utcoffset().total_seconds(), 5.5 * 3600)
            self.assertEqual(clock.now().hour, 20)

    def test_today_follows_the_clinic_not_utc(self):
        # 00:30 IST on 1 Oct is still 30 Sep in UTC; callers mean 1 Oct.
        with clock.frozen(datetime(2026, 10, 1, 0, 30)):
            self.assertEqual(clock.today(), date(2026, 10, 1))
            self.assertEqual(backend_actions.resolve_date("tomorrow")[1], "2026-10-02")

    def test_frozen_restores_previous_value_when_nested(self):
        with clock.frozen(datetime(2026, 1, 1, 9, 0)):
            with clock.frozen(datetime(2026, 6, 1, 9, 0)):
                self.assertEqual(clock.today(), date(2026, 6, 1))
            self.assertEqual(clock.today(), date(2026, 1, 1))
        self.assertIsNotNone(clock.now().tzinfo)


class LogRedactionTests(unittest.TestCase):
    def test_phone_numbers_are_masked_to_last_four(self):
        cases = {
            "Just to confirm, your phone number is 9 8 7 6 5 4 3 2 1 0. Is that correct?":
                "Just to confirm, your phone number is ******3210. Is that correct?",
            "caller: my number is 9876543210": "caller: my number is ******3210",
            "call me on +91 98765 43210 please": "call me on ******3210 please",
            "98765 43210": "******3210",
        }
        for raw, expected in cases.items():
            self.assertEqual(logredact.mask_phones(raw), expected, raw)

    def test_dates_times_ids_and_measurements_are_left_alone(self):
        for text in ["booked for 2026-10-05 at 05:00 PM", "perceived=1191.4ms first_audio=2678.9ms",
                     "[aee01e5c] turn 23 tier=0", "10:30 AM", "step 8->9", "ts 1790703269.207"]:
            self.assertEqual(logredact.mask_phones(text), text, text)

    def test_filter_masks_formatted_log_records(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        test_logger = logging.getLogger("redaction-test")
        test_logger.addHandler(handler)
        test_logger.propagate = False
        try:
            logredact.install(test_logger)
            logredact.install(test_logger)  # idempotent
            self.assertEqual(sum(isinstance(f, logredact.RedactPhones) for f in handler.filters), 1)
            test_logger.warning("caller: %s", "it's 9 8 7 6 5 4 3 2 1 0")
        finally:
            test_logger.removeHandler(handler)
        self.assertIn("******3210", stream.getvalue())
        self.assertNotIn("9 8 7 6", stream.getvalue())


class OriginCheckTests(unittest.TestCase):
    def setUp(self):
        import server
        self.allowed = server.origin_allowed

    def test_same_origin_and_non_browser_clients_are_allowed(self):
        self.assertTrue(self.allowed({"host": "localhost:8000", "origin": "http://localhost:8000"}))
        self.assertTrue(self.allowed({"host": "localhost:8000"}))

    def test_other_sites_are_rejected(self):
        for origin in ["http://evil.example", "http://localhost:9999", "null"]:
            self.assertFalse(self.allowed({"host": "localhost:8000", "origin": origin}), origin)

    def test_configured_origins_are_allowed(self):
        with patch.object(config, "ALLOWED_ORIGINS", ["https://emma.example.org"]):
            self.assertTrue(self.allowed({"host": "10.0.0.5:8000", "origin": "https://emma.example.org/"}))

    def test_socket_from_another_site_is_closed_before_accept(self):
        from starlette.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect
        import server

        client = TestClient(server.app)  # no lifespan: nothing external starts
        with self.assertRaises(WebSocketDisconnect) as ctx:
            with client.websocket_connect("/ws/voice", headers={"origin": "http://evil.example"}):
                pass
        self.assertEqual(ctx.exception.code, 1008)


class LatencyDiagnosticsTests(unittest.TestCase):
    def test_endpoint_wait_is_split_into_stt_and_hold(self):
        timer = TurnTimer(call_id="c1", turn=3, tier=0, user_end=10.0, stt_event=10.25,
                          committed=10.6, endpoint_source="speech_final")
        record = timer.to_record()
        self.assertEqual(record["endpoint_ms"], 600.0)
        self.assertEqual(record["stt_ms"], 250.0)
        self.assertEqual(record["hold_ms"], 350.0)
        self.assertEqual(record["endpoint_source"], "speech_final")
        self.assertNotIn("user_text", record)

    def test_summary_breaks_down_endpointing_by_source(self):
        log = LatencyLog(None)
        for i, (source, stt) in enumerate([("speech_final", 0.3), ("utterance_end", 1.1),
                                           ("utterance_end", 1.2)]):
            log.add(TurnTimer(call_id="c", turn=i, tier=0, user_end=0.0, stt_event=stt,
                              committed=stt, endpoint_source=source))
        summary = log.summary()["endpointing"]
        self.assertEqual(summary["utterance_end"]["turns"], 2)
        self.assertEqual(summary["utterance_end"]["stt_ms_p50_ms"], 1150.0)
        self.assertEqual(summary["speech_final"]["hold_ms_p50_ms"], 0.0)


class DeepgramEndpointSourceTests(unittest.TestCase):
    def test_utterances_report_which_event_ended_them(self):
        from stt_deepgram import DeepgramSTT

        seen = []

        async def on_end(text, end_sec, source):
            seen.append((text, end_sec, source))

        async def run():
            stt = DeepgramSTT(api_key="", on_utterance_end=on_end)
            final = {"is_final": True, "speech_final": True, "channel": {"alternatives": [
                {"transcript": "yes please", "words": [{"end": 1.4}]}]}}
            await stt._handle_result(final)
            await stt._handle_result({**final, "speech_final": False})
            await stt._handle_utterance_end()

        asyncio.run(run())
        self.assertEqual(seen, [("yes please", 1.4, "speech_final"),
                                ("yes please", 1.4, "utterance_end")])


class CallSessionEndpointTests(unittest.TestCase):
    def test_turn_timer_records_detection_event_and_arrival(self):
        from call_session import CallSession, Services

        class FakeTransport:
            async def send_audio(self, *_): pass
            async def send_event(self, *_): pass
            async def flush(self, *_): pass
            async def close(self): pass

        async def run():
            session = CallSession(FakeTransport(), Services(stt_factory=lambda **_: None))

            async def no_turn(*_):
                return None

            session._run_turn = no_turn
            await session._on_utterance_end("I'd like a cleaning", None, "utterance_end")
            await asyncio.sleep(0)
            return session._timers[session.turn_id]

        timer = asyncio.run(run())
        self.assertEqual(timer.endpoint_source, "utterance_end")
        self.assertIsNotNone(timer.stt_event)
        self.assertGreaterEqual(timer.committed, timer.stt_event)


class CaptureTests(unittest.TestCase):
    def test_capture_writes_a_playable_wav_and_utterance_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap = CallCapture(tmp, "abc123")
            cap.audio(b"\x01\x00" * 16000)  # one second
            cap.utterance("my name is Priya", 0.9, "speech_final")
            cap.close()
            cap.audio(b"\x00\x00")  # after close: ignored, no error
            with wave.open(cap.path + ".wav", "rb") as wav:
                self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getnframes()), (16000, 1, 16000))
            with open(cap.path + ".jsonl", encoding="utf-8") as fh:
                line = json.loads(fh.readline())
            self.assertEqual(line["source"], "speech_final")
            self.assertEqual(line["received_at_sec"], 1.0)


class GeminiReverifyTests(unittest.TestCase):
    def test_single_configured_key_is_used(self):
        with patch.object(config, "GEMINI_API_KEY", "k1"):
            self.assertEqual([k.value for k in llm.GeminiNLU().keys], ["k1"])
        with patch.object(config, "GEMINI_API_KEY", ""):
            self.assertEqual(llm.GeminiNLU().keys, [])

    def test_failed_check_is_retried_until_it_recovers(self):
        nlu = llm.GeminiNLU(keys=["k"], model="m")
        nlu.available = False
        calls = []

        async def fake_verify(quiet=False):
            calls.append(quiet)
            nlu.available = len(calls) >= 2
            return nlu.available

        async def run():
            nlu.verify_model = fake_verify
            task = asyncio.create_task(nlu.keep_verified(interval=0.01))
            await asyncio.sleep(0.1)
            task.cancel()

        if llm.GENAI_AVAILABLE:
            asyncio.run(run())
            self.assertEqual(calls, [True, True])  # stops checking once available
            self.assertTrue(nlu.available)

    def test_unconfigured_gemini_does_not_loop(self):
        nlu = llm.GeminiNLU(keys=[], model="m")
        asyncio.run(asyncio.wait_for(nlu.keep_verified(interval=0.01), timeout=1))


if __name__ == "__main__":
    unittest.main()
