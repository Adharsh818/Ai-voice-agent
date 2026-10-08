"""
Deepgram robustness (plan 5.7) on a fake socket: audio buffered while the
socket is down (at most 5 s) and replayed, reconnect with backoff, a single
"connection lost" after three failures, word times kept on the call's clock
across streams, and clinic keyterms without spelling variants.
"""

import asyncio
import json
import time
import unittest
from urllib.parse import parse_qs, urlsplit

import stt_deepgram
from stt_deepgram import DeepgramSTT

from test_realtime_support import patched, settle, wait_until

SECOND = 32000      # bytes of 16 kHz PCM16


class FakeSocket:
    def __init__(self):
        self.sent: list = []
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.closed = False

    async def send(self, data):
        if self.closed:
            raise ConnectionError("socket closed")
        self.sent.append(data)

    async def close(self):
        self.closed = True
        self.inbox.put_nowait(None)

    def drop(self):
        """Deepgram goes away mid-call."""
        self.closed = True
        self.inbox.put_nowait(None)

    def push(self, message: dict):
        self.inbox.put_nowait(json.dumps(message))

    def audio(self) -> list:
        return [m for m in self.sent if isinstance(m, bytes)]

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.inbox.get()
        if item is None:
            raise StopAsyncIteration
        return item


class Dialer:
    """websockets.connect stand-in: fails the first `fail` attempts, then hands out sockets."""

    def __init__(self, fail=0, fail_after_first=False):
        self.fail, self.fail_after_first = fail, fail_after_first
        self.sockets: list[FakeSocket] = []
        self.urls: list[str] = []

    async def __call__(self, url, **_):
        self.urls.append(url)
        if self.fail_after_first and self.sockets:
            raise OSError("Deepgram unreachable")
        if self.fail > 0:
            self.fail -= 1
            raise OSError("Deepgram unreachable")
        sock = FakeSocket()
        self.sockets.append(sock)
        return sock


def final(text, start, end, speech_final=True):
    return {"type": "Results", "is_final": True, "speech_final": speech_final,
            "channel": {"alternatives": [{"transcript": text,
                                          "words": [{"start": start, "end": end}]}]}}


def fast_backoff():
    return patched(stt_deepgram, RECONNECT_BACKOFF_S=(0.01, 0.02, 0.03))


class ReconnectTests(unittest.TestCase):
    def test_audio_said_while_down_is_replayed_and_times_stay_on_the_call_clock(self):
        heard, lost = [], []
        dialer = Dialer()

        async def on_end(text, end_sec, source, start_sec=None):
            heard.append((text, round(end_sec, 3), round(start_sec, 3)))

        async def on_lost():
            lost.append(True)

        async def run():
            stt = DeepgramSTT(api_key="k", on_utterance_end=on_end, on_connection_lost=on_lost)
            await stt.connect()
            await stt.send_audio(b"a" * SECOND)              # 1 s heard by the first stream
            dialer.sockets[0].drop()
            await settle()
            await stt.send_audio(b"b" * (SECOND // 2))       # said while reconnecting
            await wait_until(lambda: len(dialer.sockets) == 2 and stt.is_connected, timeout=2)
            replayed = dialer.sockets[1].audio()
            # The new stream's clock starts at 1.0 s of call audio.
            dialer.sockets[1].push(final("yes please", 0.1, 0.4))
            await settle(0.05)
            reconnects = stt.reconnects
            await stt.close()
            return replayed, reconnects

        with patched(stt_deepgram.websockets, connect=dialer), fast_backoff():
            replayed, reconnects = asyncio.run(run())
        self.assertEqual(replayed, [b"b" * (SECOND // 2)])
        self.assertEqual(reconnects, 1)
        self.assertEqual(heard, [("yes please", 1.4, 1.1)])
        self.assertEqual(lost, [])

    def test_a_first_connect_failure_keeps_the_greeting_time_audio(self):
        dialer = Dialer(fail=1)

        async def run():
            stt = DeepgramSTT(api_key="k")
            await stt.connect()                           # fails; retries in the background
            await stt.send_audio(b"c" * 3200)
            await wait_until(lambda: dialer.sockets and stt.is_connected, timeout=2)
            audio = dialer.sockets[0].audio()
            await stt.close()
            return audio

        with patched(stt_deepgram.websockets, connect=dialer), fast_backoff():
            self.assertEqual(asyncio.run(run()), [b"c" * 3200])

    def test_three_failed_attempts_report_the_loss_once(self):
        lost = []
        dialer = Dialer(fail_after_first=True)

        async def on_lost():
            lost.append(True)

        async def run():
            stt = DeepgramSTT(api_key="k", on_connection_lost=on_lost)
            await stt.connect()
            dialer.sockets[0].drop()
            await wait_until(lambda: stt.failed, timeout=2)
            await settle(0.05)
            await stt.send_audio(b"d" * 3200)            # nothing to keep it for any more
            buffered = stt._buffer_bytes
            await stt.close()
            return buffered

        with patched(stt_deepgram.websockets, connect=dialer), fast_backoff():
            buffered = asyncio.run(run())
        self.assertEqual(lost, [True])
        self.assertEqual(len(dialer.urls), 1 + len(stt_deepgram.RECONNECT_BACKOFF_S))
        self.assertEqual(buffered, 0)

    def test_buffer_keeps_only_the_last_five_seconds(self):
        async def run():
            stt = DeepgramSTT(api_key="")                 # never connects: everything is buffered
            for _ in range(7):
                await stt.send_audio(b"e" * SECOND)
            return stt._buffer_bytes

        self.assertLessEqual(asyncio.run(run()), int(stt_deepgram.MAX_BUFFER_S * SECOND))

    def test_audio_already_reported_is_not_a_second_turn_after_a_replay(self):
        heard = []

        async def on_end(text, end_sec, source):
            heard.append((text, source))

        async def run():
            stt = DeepgramSTT(api_key="k", on_utterance_end=on_end)
            await stt._handle_result(final("my name is Priya", 0.2, 1.5))
            await stt._handle_result(final("my name is Priya", 0.2, 1.5, speech_final=False))
            await stt._handle_utterance_end()
            await stt._handle_result(final("and my number is", 2.0, 2.8, speech_final=False))
            await stt._handle_utterance_end()

        asyncio.run(run())
        self.assertEqual(heard, [("my name is Priya", "speech_final"), ("and my number is", "utterance_end")])


class KeytermTests(unittest.TestCase):
    def params(self, stt):
        return parse_qs(urlsplit(stt._url(16000)).query)

    def test_clinic_terms_are_added_once_per_spelling(self):
        stt = DeepgramSTT(api_key="k", keyterms=["Pearl Dental", "check-up"])
        stt.add_keyterms(["check up", "Checkup", "Dr Rao", "dr rao", "", "Nagarbhavi"])
        self.assertEqual(stt.keyterms, ["Pearl Dental", "check-up", "Dr Rao", "Nagarbhavi"])
        self.assertEqual(self.params(stt)["keyterm"], ["Pearl Dental", "check-up", "Dr Rao", "Nagarbhavi"])

    def test_keyterms_are_capped_and_only_sent_to_nova_3(self):
        stt = DeepgramSTT(api_key="k")
        stt.add_keyterms([f"term{i}" for i in range(stt_deepgram.MAX_KEYTERMS + 30)])
        self.assertEqual(len(stt.keyterms), stt_deepgram.MAX_KEYTERMS)
        older = DeepgramSTT(api_key="k", model="nova-2", keyterms=["Dr Rao"])
        self.assertNotIn("keyterm", self.params(older))

    def test_endpointing_setting_is_passed_through(self):
        for ms in (200, 400):
            stt = DeepgramSTT(api_key="k", endpointing_ms=ms)
            self.assertEqual(self.params(stt)["endpointing"], [str(ms)])
            self.assertEqual(self.params(stt)["smart_format"], ["true"])


if __name__ == "__main__":
    unittest.main()


def interim(text, start, end):
    return {"type": "Results", "is_final": False, "speech_final": False,
            "channel": {"alternatives": [{"transcript": text,
                                          "words": [{"start": start, "end": end}]}]}}


class WatchdogTests(unittest.TestCase):
    """Noise can stop speech_final and UtteranceEnd ever arriving (5-6 s waits on 1 Oct)."""

    def run_call(self, messages, wait=0.6):
        heard, dialer = [], Dialer()

        async def on_end(text, end_sec, source, start_sec=None):
            heard.append((text, source))

        async def run():
            stt = DeepgramSTT(api_key="k", on_utterance_end=on_end, watchdog_s=0.3)
            await stt.connect()
            for message in messages:
                dialer.sockets[0].push(message)
            await settle(wait)
            await stt.close()

        with patched(stt_deepgram.websockets, connect=dialer):
            asyncio.run(run())
        return heard

    def test_a_turn_without_speech_final_ends_once_the_words_stop_changing(self):
        heard = self.run_call([final("I want to book", 0.1, 0.9, speech_final=False)])
        self.assertEqual(heard, [("I want to book", "watchdog")])

    def test_unfinalised_words_are_included_and_their_late_final_is_not_repeated(self):
        heard = self.run_call([final("my number is", 0.1, 0.8, speech_final=False),
                               interim("nine eight four five", 0.9, 2.0)])
        self.assertEqual(heard, [("my number is nine eight four five", "watchdog")])
        late = self.run_call([interim("yes", 0.1, 0.4), ])
        self.assertEqual(late, [("yes", "watchdog")])

    def test_speech_final_still_ends_the_turn_first(self):
        heard = self.run_call([final("yes please", 0.1, 0.5)])
        self.assertEqual(heard, [("yes please", "speech_final")])

    def test_a_complete_answer_ends_sooner_than_an_unfinished_one(self):
        # 6 Oct latency pass: the session says how long these words deserve.
        def timed(text, watchdog_for):
            ended = {}

            async def on_end(t, end_sec, source, start_sec=None):
                ended["at"] = time.monotonic()

            async def run():
                dialer = Dialer()
                with patched(stt_deepgram.websockets, connect=dialer):
                    stt = DeepgramSTT(api_key="k", on_utterance_end=on_end, watchdog_s=1.0,
                                      watchdog_for=watchdog_for)
                    await stt.connect()
                    start = time.monotonic()
                    dialer.sockets[0].push(final(text, 0.1, 0.5, speech_final=False))
                    await settle(1.4)
                    await stt.close()
                    return ended["at"] - start

            return asyncio.run(run())

        quick = timed("yes", lambda t: 0.35)
        slow = timed("my number is", lambda t: 5.0)          # capped at watchdog_s
        broken = timed("yes", lambda t: 1 / 0)                # a failing estimate keeps the default
        self.assertLess(quick, 0.6)
        self.assertGreaterEqual(slow, 0.95)
        self.assertGreaterEqual(broken, 0.95)

    def test_the_session_estimate_follows_the_turn_detector(self):
        from test_realtime_support import make_session
        s, _ = make_session()
        s._listening_hint = lambda: {"expect": "yes_no"}
        self.assertEqual(s._watchdog_for("yes"), 0.35)
        self.assertEqual(s._watchdog_for("and"), 1.0)
        s._listening_hint = lambda: {"expect": "phone", "digits_so_far": 0}
        self.assertEqual(s._watchdog_for("nine eight four five"), 1.0)          # half a number
        self.assertEqual(s._watchdog_for("98450 12345"), 0.35)                  # all ten digits


class GreetingEchoTests(unittest.TestCase):
    """1 Oct voice test on laptop speakers: the greeting came back garbled and hijacked the call."""

    def test_garbled_greeting_echo_is_caught_but_real_replies_are_not(self):
        from call_session import looks_like_echo
        cases = [
            ("Hi, I'm Emma from Pearl Dental. How can I help you?", "I'm Emma from her.", True),
            ("Pearl Dental, Emma here. Go ahead.", "Dental Emma here.", True),
            ("Hi, I'm Emma from Pearl Dental. How can I help you?", "Hi Emma, I want to book", False),
            ("Hi, this is Emma at Pearl Dental, how can I help?", "this is Rahul", False),
            ("Hello, Pearl Dental. How can I help you?", "how can you help me", False),
        ]
        for greeting, heard, echo in cases:
            self.assertEqual(looks_like_echo(heard, greeting, greeting=True), echo, heard)


class SpeakerEchoTests(unittest.TestCase):
    """Mostly-Emma's-words while she speaks is her voice through the speakers (the pre-1 Oct rule)."""

    def test_lenient_echo_rule_keeps_real_answers(self):
        from call_session import looks_like_echo
        emma = "So that's a cleaning on Monday at 5. Shall I book it?"
        self.assertTrue(looks_like_echo("a cleaning on Monday", emma))
        self.assertTrue(looks_like_echo("shall I book it", emma))
        self.assertFalse(looks_like_echo("yes, Monday at 5", emma))
        self.assertFalse(looks_like_echo("no, Tuesday instead", emma))
        self.assertFalse(looks_like_echo("how can you help me", "Hello, Pearl Dental. How can I help you?"))


class HelloReplyTests(unittest.TestCase):
    def test_a_plain_hello_gets_a_greeting_not_a_booking_push(self):
        import ai_engine
        for text in ("Hi.", "Hello?", "Good morning"):
            s = ai_engine.SessionState()
            s.greeting_spoken = True
            self.assertEqual(ai_engine._handle_conversation_step(text, {}, s),
                             "Hi there! How can I help you today?")
