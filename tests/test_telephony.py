"""
Phone calls through Asterisk (phase D): AudioSocket framing, the phone
transport's playout clock and playback reports, keypad digits, a whole call
against a simulated Asterisk over TCP, the dialplan's HTTP endpoints, AMI
Originate against a simulated manager, the recovery runner's dialer, and the
engine's caller ID and live transfer.
"""

import asyncio
import os
import struct
import sys
import time
import unittest
import uuid
from unittest import mock

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")    # before numpy: see phone_audio.py
import numpy as np  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))

import ai_engine  # noqa: E402
import ami  # noqa: E402
import audiosocket  # noqa: E402
import config  # noqa: E402
import facts  # noqa: E402
import nlu  # noqa: E402
import outbound  # noqa: E402
import phone_audio  # noqa: E402
from audiosocket import (KIND_DTMF, KIND_HANGUP, KIND_SLIN, KIND_UUID, PhoneCall, PhoneTransport,  # noqa: E402
                         frame, read_frame)
from dialogue.context import FieldState, Goal  # noqa: E402
from dialogue.testing import DemoClinic  # noqa: E402
from test_r2_engine import NOW, Call  # noqa: E402
from test_realtime_support import FakeTTS, engine, make_session, patched, result, wait_until  # noqa: E402

MS16 = 32          # bytes per ms at 16 kHz


class _Writer:
    """A StreamWriter stand-in that keeps what was written, with arrival times."""

    def __init__(self):
        self.frames = []
        self.closed = False

        class _T:
            @staticmethod
            def get_write_buffer_size():
                return 0
        self.transport = _T()

    def write(self, data):
        self.frames.append((time.perf_counter(), data))

    async def drain(self):
        pass

    def is_closing(self):
        return self.closed

    def close(self):
        self.closed = True


class _Session:
    """Just what PhoneTransport talks to: on_control receives the playback reports."""

    def __init__(self):
        self.reports = []
        self.texts = []
        self.interrupts = []
        self.closed = False
        self.s = None

    async def on_control(self, msg):
        if msg.get("type") == "playback":
            self.reports.append(msg)
        elif msg.get("type") == "text":
            self.texts.append(msg["text"])

    async def interrupt(self, reason):
        self.interrupts.append(reason)
        return True


def tone(ms, freq=440, rate=16000, amp=8000):
    t = np.arange(int(rate * ms / 1000))
    return (amp * np.sin(2 * np.pi * freq * t / rate)).astype("<i2").tobytes()


class FramingTests(unittest.IsolatedAsyncioTestCase):
    async def test_frames_round_trip(self):
        reader = asyncio.StreamReader()
        key = uuid.uuid4()
        reader.feed_data(frame(KIND_UUID, key.bytes) + frame(KIND_SLIN, b"\x01\x02" * 160) + frame(KIND_DTMF, b"5"))
        reader.feed_eof()
        self.assertEqual(await read_frame(reader), (KIND_UUID, key.bytes))
        kind, payload = await read_frame(reader)
        self.assertEqual((kind, len(payload)), (KIND_SLIN, 320))
        self.assertEqual(await read_frame(reader), (KIND_DTMF, b"5"))
        self.assertEqual(await read_frame(reader), (KIND_HANGUP, b""))     # closed socket = hang-up
        self.assertEqual(frame(KIND_SLIN, b"ab")[:3], struct.pack(">BH", 0x10, 2))

    def test_registry(self):
        reg = audiosocket.Registry()
        key = reg.register_inbound("9845022222")
        self.assertEqual(len(key), 36)
        self.assertEqual(reg.take(key).caller_id, "9845022222")
        self.assertIsNone(reg.take(key))                                    # used once
        self.assertEqual(reg.pop_next(key), "hangup")
        reg.set_next(key, "transfer")
        self.assertEqual(reg.pop_next(key), "transfer")
        stale = reg.register_inbound(None)
        reg.pending[stale].created -= reg.TTL_S + 1
        self.assertIsNone(reg.take(stale))


class ResampleTests(unittest.TestCase):
    def test_round_trip_keeps_speech_band_and_length(self):
        down, up = phone_audio.Downsampler(), phone_audio.Upsampler()
        src = tone(1000, 1000)
        narrow = b"".join(down.process(src[i:i + 642]) for i in range(0, len(src), 642))   # odd chunks
        wide = b"".join(up.process(narrow[i:i + 320]) for i in range(0, len(narrow), 320))
        self.assertEqual((len(narrow), len(wide)), (len(src) // 2, len(src)))
        self.assertGreater(phone_audio.tone_power(narrow[2000:], 1000, 8000), 0.45)
        rms = np.sqrt(np.mean(np.frombuffer(wide, "<i2")[2000:].astype(float) ** 2))
        self.assertAlmostEqual(rms, 8000 / np.sqrt(2), delta=300)

    def test_no_aliasing(self):
        out = phone_audio.Downsampler().process(tone(1000, 6000))
        self.assertLess(np.sqrt(np.mean(np.frombuffer(out, "<i2")[200:].astype(float) ** 2)), 50)


class LineSoundTests(unittest.TestCase):
    def test_typing_plays_for_its_length_then_silence(self):
        import random
        keyboard = phone_audio.Sound(np.full(16000 * 5, 0.2, np.float32), 0.2)
        s = phone_audio.LineSounds(phone_audio.SoundBank(typing=[keyboard]), rng=random.Random(3))
        s.typing(0, 1000)
        frames = [s.frame() for _ in range(80)]
        self.assertEqual(sum(f is not None for f in frames), 50)        # 1 s of 20 ms frames
        self.assertIsNone(frames[-1])
        synth = phone_audio.LineSounds(phone_audio.SoundBank(), rng=random.Random(3))
        synth.typing(0, 1000)                                            # no recording: clicks instead
        self.assertTrue(any(synth.frame() is not None for _ in range(50)))

    def test_note_typing_stops_when_emma_speaks(self):
        import random
        s = phone_audio.LineSounds(phone_audio.SoundBank(), rng=random.Random(3))
        s.typing(0, 3000, turn=2, until_speech=True)
        [s.frame() for _ in range(10)]
        s.speech_started(2)
        tail = [s.frame() for _ in range(20)]
        self.assertTrue(all(f is None for f in tail[10:]))

    def test_nothing_while_the_line_is_closed_and_nothing_when_off(self):
        import random
        bank = phone_audio.SoundBank(movement=[phone_audio.Sound(np.ones(16000 * 10, np.float32) * 0.1, 0.1)])
        s = phone_audio.LineSounds(bank, rng=random.Random(1))
        self.assertTrue(all(s.frame() is None for _ in range(500)))         # no background bed
        off = phone_audio.LineSounds(bank, enabled=False)
        off.typing(0, 1000)
        off.speaking(True)
        self.assertTrue(all(off.frame() is None for _ in range(200)))


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.writer = _Writer()
        self.t = PhoneTransport(self.writer, "test", "key", phone_audio.LineSounds(enabled=False))
        self.session = _Session()
        self.t.attach(self.session)

    async def asyncTearDown(self):
        await self.t.close()

    async def test_a_turn_plays_in_real_time_and_reports_started_then_ended(self):
        await self.t.send_audio(1, tone(500))
        await self.t.send_event({"type": "turn", "turn": 1, "phase": "audio_done"})
        started = time.perf_counter()
        self.assertTrue(await wait_until(lambda: any(r["event"] == "ended" for r in self.session.reports), 2))
        took = time.perf_counter() - started
        self.assertGreater(took, 0.4)                         # paced, not dumped
        events = [r["event"] for r in self.session.reports]
        self.assertEqual(events, ["started", "ended"])
        self.assertEqual(self.session.reports[-1]["played_ms"], 500)
        audio = [d for _, d in self.writer.frames if d[0] == KIND_SLIN]
        self.assertTrue(all(len(d) == 3 + 320 for d in audio))            # 20 ms at 8 kHz

    async def test_flush_reports_how_much_was_heard(self):
        await self.t.send_audio(1, tone(2000))
        await asyncio.sleep(0.3)
        await self.t.flush(1)
        await asyncio.sleep(0.05)
        ev = self.session.reports[-1]
        self.assertEqual(ev["event"], "interrupted")
        self.assertTrue(150 <= ev["played_ms"] <= 400, ev)
        await self.t.send_audio(1, tone(200))                  # late audio of the flushed turn is dropped
        self.assertEqual(len(self.t._buf), 0)

    async def test_a_newer_turn_replaces_the_rest_of_an_older_one(self):
        await self.t.send_audio(1, tone(2000))
        await asyncio.sleep(0.1)
        await self.t.send_audio(2, tone(100))
        await self.t.send_event({"type": "turn", "turn": 2, "phase": "audio_done"})
        self.assertTrue(await wait_until(lambda: any(r["turn"] == 2 and r["event"] == "ended"
                                                     for r in self.session.reports), 1))

    async def test_transfer_route_is_set_before_the_socket_closes(self):
        self.session.s = type("S", (), {"transfer_requested": True})()
        await self.t.close()
        self.assertEqual(audiosocket.registry.pop_next("key"), "transfer")
        self.assertEqual(self.writer.frames[-1][1], frame(KIND_HANGUP))
        self.assertTrue(self.writer.closed)


class KeypadTests(unittest.IsolatedAsyncioTestCase):
    async def test_digits_are_one_turn_after_hash_and_stop_emma(self):
        session = _Session()
        call = PhoneCall(asyncio.StreamReader(), PhoneTransport(_Writer(), "t", "k"))
        for key in "98450*9845022222#":
            await call._key(session, key)
        self.assertEqual(session.texts, ["9845022222"])
        self.assertEqual(session.interrupts, ["keypad", "keypad"])

    async def test_digits_are_sent_after_a_quiet_spell(self):
        session = _Session()
        call = PhoneCall(asyncio.StreamReader(), PhoneTransport(_Writer(), "t", "k"))
        with patched(config, DTMF_TIMEOUT_S=0.05):
            for key in "123":
                await call._key(session, key)
            self.assertTrue(await wait_until(lambda: session.texts == ["123"], 1))


class SimulatedAsterisk:
    """The Asterisk side of one AudioSocket call: UUID, 8 kHz audio, keys, hang-up."""

    def __init__(self, port, key):
        self.port, self.key = port, key
        self.audio_frames = 0
        self.hung_up_by_emma = asyncio.Event()

    async def connect(self):
        self.reader, self.writer = await asyncio.open_connection("127.0.0.1", self.port)
        self.writer.write(frame(KIND_UUID, uuid.UUID(self.key).bytes))
        self._rx = asyncio.create_task(self._receive())

    async def _receive(self):
        while True:
            kind, _ = await read_frame(self.reader)
            if kind == KIND_SLIN:
                self.audio_frames += 1
            elif kind == KIND_HANGUP:
                self.hung_up_by_emma.set()
                return

    async def talk(self, ms):
        for _ in range(ms // 20):
            self.writer.write(frame(KIND_SLIN, tone(20, 300, rate=8000)))
            await asyncio.sleep(0.02)

    async def keys(self, digits):
        for d in digits:
            self.writer.write(frame(KIND_DTMF, d.encode()))
        await self.writer.drain()

    async def hang_up(self):
        self.writer.write(frame(KIND_HANGUP))
        await self.writer.drain()
        self.writer.close()


class WholeCallTests(unittest.IsolatedAsyncioTestCase):
    """A CallSession on fakes, reached through a real AudioSocketServer over TCP."""

    async def asyncSetUp(self):
        self.sessions = []
        self.turns = []

        async def on_call(key, reader, writer):
            transport = PhoneTransport(writer, "call", key, phone_audio.LineSounds(enabled=False))
            session, _ = make_session(transport=transport, tts=FakeTTS(ms=600))
            self.sessions.append(session)
            transport.attach(session)
            await session.start()
            await PhoneCall(reader, transport).run(session)
            await session.close()

        self.server = audiosocket.AudioSocketServer(on_call, host="127.0.0.1", port=0)
        await self.server.start()

    async def asyncTearDown(self):
        await self.server.stop()

    async def test_greeting_plays_keys_become_a_turn_and_the_caller_hangs_up(self):
        async def fake_engine(text, s, progress=None, **kw):
            self.turns.append(text)
            return result("Hello, this is Emma." if not text else "Thanks, got it.")

        with engine(fake_engine):
            phone = SimulatedAsterisk(self.server.port, str(uuid.uuid4()))
            await phone.connect()
            self.assertTrue(await wait_until(lambda: self.sessions, 2))
            session = self.sessions[0]
            # The greeting (0.6 s of fake voice) plays out at the line's pace and is reported played.
            self.assertTrue(await wait_until(lambda: 1 in session._audible and session._audible[1][1], 3))
            start, end = session._audible[1]
            self.assertGreater(end - start, 0.5)
            self.assertTrue(session._playback_reports)
            await phone.talk(200)
            await phone.keys("9845022222#")
            self.assertTrue(await wait_until(lambda: "9845022222" in self.turns, 3))
            await phone.hang_up()
            self.assertTrue(await wait_until(lambda: session.closed, 3))
        self.assertGreater(phone.audio_frames, 30)
        self.assertEqual(self.turns[0], "")

    async def test_emma_ending_the_call_hangs_up_the_line(self):
        async def fake_engine(text, s, progress=None, **kw):
            return result("Bye now.", closes_call=True)

        with engine(fake_engine):
            phone = SimulatedAsterisk(self.server.port, str(uuid.uuid4()))
            await phone.connect()
            self.assertTrue(await wait_until(lambda: self.sessions, 2))
            await self.sessions[0]._finish_call(1)
            self.assertTrue(await asyncio.wait_for(phone.hung_up_by_emma.wait(), 3))


class DialplanEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx
        import server
        self.server = server
        self.patch = patched(config, TELEPHONY_ENABLED=True, TELEPHONY_SECRET="s3cret")
        self.patch.__enter__()
        server.app.state.audiosocket = object()
        transport = httpx.ASGITransport(app=server.app, client=("127.0.0.1", 5555))
        self.client = httpx.AsyncClient(transport=transport, base_url="http://emma")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.server.app.state.audiosocket = None
        self.patch.__exit__(None, None, None)

    async def test_register_and_next(self):
        r = await self.client.post("/telephony/register", content="key=s3cret&from=%2B919845022222")
        self.assertEqual(r.status_code, 200)
        key = r.text
        self.assertEqual(audiosocket.registry.pending[key].caller_id, "+919845022222")
        audiosocket.registry.set_next(key, "transfer")
        r = await self.client.post("/telephony/next", content=f"key=s3cret&uuid={key}")
        self.assertEqual(r.text, "transfer")
        r = await self.client.post("/telephony/next", content=f"key=s3cret&uuid={key}")
        self.assertEqual(r.text, "hangup")

    async def test_busy_wrong_key_and_not_loopback(self):
        gate = self.server.app.state.gate
        self.assertTrue(gate.try_acquire("inbound", "other"))
        try:
            r = await self.client.post("/telephony/register", content="key=s3cret&from=1001")
            self.assertEqual(r.text, "busy")
        finally:
            gate.release("other")
        r = await self.client.post("/telephony/register", content="key=nope&from=1001")
        self.assertEqual(r.status_code, 403)
        import httpx
        remote = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.server.app, client=("10.0.0.5", 1)),
                                   base_url="http://emma")
        r = await remote.post("/telephony/register", content="key=s3cret&from=1001")
        await remote.aclose()
        self.assertEqual(r.status_code, 404)


class FakeManager:
    """Asterisk's manager port: login, then Originate answered with `reason`."""

    def __init__(self, reason="4", secret="pw"):
        self.reason, self.secret = reason, secret
        self.originates = []

    async def start(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()

    async def _handle(self, reader, writer):
        writer.write(b"Asterisk Call Manager/9.0.0\r\n")
        while True:
            try:
                msg = await ami._read_message(reader)
            except ami.AMIError:
                break
            action, aid = msg.get("Action"), msg.get("ActionID", "")
            if action == "Login":
                ok = msg.get("Secret") == self.secret
                writer.write(ami._message({"Response": "Success" if ok else "Error", "ActionID": aid,
                                           "Message": "Authentication accepted" if ok else "Authentication failed"}))
            elif action == "Originate":
                self.originates.append(msg)
                writer.write(ami._message({"Response": "Success", "ActionID": aid, "Message": "queued"}))
                writer.write(ami._message({"Event": "Newchannel", "Channel": "PJSIP/1001-0001"}))
                writer.write(ami._message({"Event": "OriginateResponse", "ActionID": aid,
                                           "Response": "Success" if self.reason == "4" else "Failure",
                                           "Reason": self.reason}))
            elif action == "Logoff":
                writer.close()
                break
            await writer.drain()


class AMITests(unittest.IsolatedAsyncioTestCase):
    async def originate(self, manager, **kw):
        return await ami.originate(channel="PJSIP/1001", context="emma-outbound", exten="s",
                                   variables={"EMMA_UUID": "abc"}, timeout_s=2, host="127.0.0.1",
                                   port=manager.port, user="emma", secret=kw.get("secret", "pw"))

    async def test_answered_declined_and_rang_out(self):
        for reason, answered, declined in (("4", True, False), ("5", False, True), ("3", False, False)):
            manager = FakeManager(reason)
            await manager.start()
            try:
                res = await self.originate(manager)
            finally:
                await manager.stop()
            self.assertEqual((res.answered, res.declined), (answered, declined), reason)
        self.assertEqual(manager.originates[0]["Channel"], "PJSIP/1001")
        self.assertEqual(manager.originates[0]["Variable"], "EMMA_UUID=abc")

    async def test_bad_login(self):
        manager = FakeManager()
        await manager.start()
        try:
            with self.assertRaises(ami.AMIError):
                await self.originate(manager, secret="wrong")
        finally:
            await manager.stop()


class DialerTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_reject_on_the_phone_declines_the_ring(self):
        claimed = outbound.Claimed(7, 1, "+919845022222", [{"id": "a1", "patient_name": "Priya"}], {})
        ring = outbound.Ringing(claimed, "c1", "tok")
        runner = outbound.Runner(database=None, gate=None, ring_timeout_s=5)
        runner.ringing = ring
        import server
        with mock.patch.object(ami, "originate", return_value=ami.OriginateResult(False, True, "5")):
            await server._ring_phone(ring, runner)
        self.assertTrue(ring.declined and ring.answered.is_set())
        key = next(k for k, p in audiosocket.registry.pending.items() if p.ring is ring)
        self.assertEqual(audiosocket.registry.take(key).kind, "outbound")


class CallerIdAndTransferTests(unittest.TestCase):
    """The engine side: caller ID instead of a read-back, and a live transfer instead of a callback promise."""

    def setUp(self):
        self.clinic = DemoClinic(now=NOW)
        self.clinic.__enter__()
        facts.clear_cache()
        self.backend = nlu.use_backend(nlu.FakeNLU(usable=False))

    def tearDown(self):
        nlu.use_backend(self.backend)
        self.clinic.__exit__(None, None, None)

    def call(self, caller_id="9845022222", transfer=True):
        c = Call()
        took = ai_engine.phone_line(c.s, caller_id, can_transfer=transfer)
        c.say("")
        return c, took

    def test_caller_id_is_confirmed_with_one_question(self):
        c, took = self.call()
        self.assertTrue(took)
        c.say("I'd like to book a cleaning tomorrow morning.")
        reply = c.say("Neha Kapoor.").text
        self.assertRegex(reply, r"number you're calling from")
        self.assertNotIn("9 8 4 5 0", reply)                   # no digit read-back
        c.say("Yes.")
        self.assertEqual((c.s.caller.phone_e164, c.s.caller.phone_state), ("+919845022222", FieldState.CONFIRMED))

    def test_no_to_caller_id_asks_for_the_other_number_without_counting_a_miss(self):
        c, _ = self.call()
        c.say("I'd like to book a cleaning tomorrow morning.")
        c.say("Neha Kapoor.")
        reply = c.say("No.").text
        self.assertIn("What's the best number", reply) if "best number" in reply else \
            self.assertIn("Which number", reply)
        self.assertEqual(c.s.caller.phone_misses, 0)
        reply = c.say("98450 33333.").text
        self.assertIn("9 8 4 5 0, 3 3 3 3 3", reply)            # a number they said is read back
        c.say("Yes.")
        self.assertEqual(c.s.caller.phone_e164, "+919845033333")

    def test_an_extension_or_hidden_number_is_not_a_caller_id(self):
        for cid in ("1001", "anonymous", None):
            c, took = self.call(cid)
            self.assertFalse(took, cid)
            self.assertEqual(c.s.caller.phone_state, FieldState.EMPTY)

    def test_insisting_on_a_person_puts_them_through_after_the_task_exists(self):
        c, _ = self.call()
        c.say("Can I speak to someone at the clinic?")
        reply = c.say("No, I want to talk to a person.").text
        self.assertIn("put you through to the front desk", reply.replace("putting you through", "put you through"))
        self.assertIn("this number", reply)
        self.assertTrue(c.s.transfer_requested and c.s.closed_conversation)
        self.assertEqual(c.s.outcome, "transferred")
        self.assertEqual(c.s.pending, Goal.TRANSFER)
        self.assertTrue(self.clinic.query("SELECT * FROM tasks WHERE kind = 'callback'"))

    def test_without_a_front_desk_it_is_the_callback_promise(self):
        c, _ = self.call(transfer=False)
        c.say("Can I speak to someone at the clinic?")
        c.say("No, I want to talk to a person.")
        reply = c.say("Yes.").text
        self.assertIn("call you back", reply)
        self.assertFalse(c.s.transfer_requested)


if __name__ == "__main__":
    unittest.main()
