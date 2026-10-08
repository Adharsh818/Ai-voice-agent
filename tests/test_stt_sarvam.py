"""stt_sarvam.SarvamSTT, the comparison adapter (R3.4): same callbacks as DeepgramSTT."""

import asyncio
import json
import unittest
from urllib.parse import parse_qs, urlsplit

import stt_sarvam
from test_realtime_stt import Dialer
from test_realtime_support import patched, settle


class SarvamAdapterTests(unittest.TestCase):
    def test_protocol_and_callbacks(self):
        dialer = Dialer()
        partials, finals, started = [], [], []

        async def on_transcript(text, is_final):
            (finals if is_final else partials).append(text)

        ends = []

        async def on_end(text, end_sec, source, start_sec=None):
            ends.append((text, end_sec, source, start_sec))

        async def on_start(_):
            started.append(True)

        async def run():
            stt = stt_sarvam.SarvamSTT(api_key="k", on_transcript=on_transcript, on_utterance_end=on_end,
                                       on_speech_started=on_start, watchdog_for=lambda t: 1.0)
            stt.add_keyterms(["Jayanagar", "Dr Rao"])
            await stt.send_audio(b"\x01\x00" * 160)              # before the socket opens: kept
            await stt.connect(sample_rate=16000)
            sock = dialer.sockets[0]
            sock.push({"event": "vad.speech_start"})
            sock.push({"event": "transcript.partial", "text": "I need a"})
            sock.push({"event": "transcript.final", "text": "I need a root canal.", "start_s": 0.4, "end_s": 1.9})
            await settle(0.05)
            await stt.close()
            return sock

        with patched(stt_sarvam.websockets, connect=dialer):
            sock = asyncio.run(run())
        query = parse_qs(urlsplit(dialer.urls[0]).query)
        self.assertEqual(query["model"], ["saaras:v4"])
        self.assertEqual(query["language_code"], ["en-IN"])
        self.assertEqual(json.loads(query["keyterms"][0]), ["Jayanagar", "Dr Rao"])
        sent = [json.loads(m) for m in sock.sent]
        self.assertEqual(sent[0]["event"], "audio_input")          # the early audio went first
        self.assertEqual(sent[-1], {"event": "end"})
        self.assertEqual(partials, ["I need a"])
        self.assertEqual(finals, ["I need a root canal."])
        self.assertEqual(ends, [("I need a root canal.", 1.9, "speech_final", 0.4)])
        self.assertEqual(started, [True])


if __name__ == "__main__":
    unittest.main()
