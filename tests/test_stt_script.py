"""The STT test script must be scorable: its own reference text yields its expected values."""

import asyncio
import json
import unittest
from datetime import date
from pathlib import Path

import phones
import tier0
from dateparse import parse_when

SCRIPT = json.loads((Path(__file__).parent / "data" / "stt_script.json").read_text(encoding="utf-8"))
TODAY = date(2026, 9, 30)


def hm(t):
    return t.strftime("%H:%M")


class ScriptConsistencyTests(unittest.TestCase):
    def test_thirty_numbered_lines(self):
        self.assertEqual([line["id"] for line in SCRIPT["lines"]], list(range(1, 31)))

    def test_reference_text_parses_to_expected_values(self):
        for line in SCRIPT["lines"]:
            exp = line["expect"]
            w = parse_when(line["say"], today=TODAY, expecting=line.get("expecting"))
            with self.subTest(line=line["id"], say=line["say"]):
                if "date_kind" in exp:
                    self.assertIsNotNone(w.date, w.issues)
                    self.assertEqual(w.date.kind, exp["date_kind"])
                if "time" in exp:
                    self.assertEqual((w.time.kind, hm(w.time.start)), ("exact", exp["time"]))
                if "time_window" in exp:
                    self.assertEqual((w.time.kind, hm(w.time.start), hm(w.time.end)),
                                     ("window", *exp["time_window"]))
                if "time_ambiguous" in exp:
                    self.assertEqual([hm(c) for c in w.time.candidates], exp["time_ambiguous"])
                if "phone" in exp:
                    digits = tier0.normalize_spoken_digits(line["say"])
                    self.assertEqual(phones.to_e164(digits), exp["phone"])


class ListenOnlyTests(unittest.TestCase):
    def test_listen_only_sessions_never_greet_or_reply(self):
        from call_session import CallSession, Services

        class FakeTransport:
            async def send_audio(self, *_): pass
            async def send_event(self, *_): pass
            async def flush(self, *_): pass
            async def close(self): pass

        class FakeSTT:
            async def connect(self, **_): pass
            async def send_audio(self, *_): pass
            async def close(self): pass

        async def run():
            session = CallSession(FakeTransport(), Services(stt_factory=lambda **_: FakeSTT()), listen_only=True)
            await session.start()
            await session._on_utterance_end("my name is Priya", 1.0, "speech_final")
            await asyncio.sleep(0)
            turns = session.turn_id
            await session.close()
            return turns

        self.assertEqual(asyncio.run(run()), 0)


if __name__ == "__main__":
    unittest.main()
