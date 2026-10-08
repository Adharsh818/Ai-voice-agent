"""
Phone calls through Asterisk's AudioSocket (docs/TELEPHONY.md; plan section
13, phase D).

    softphone --SIP--> Asterisk --AudioSocket TCP (loopback)--> AudioSocketServer
                                                                  |
                                       PhoneTransport <-> CallSession (as the browser)

AudioSocket frames: type (1 byte) + payload length (2 bytes, big-endian) +
payload. The call's UUID comes first; then signed-linear audio both ways
(8 kHz, 20 ms = 320 bytes; 16 kHz frames are accepted too), keypad digits, and
a hang-up from either side.

PhoneTransport is CallSession's Transport for a phone line. The browser plays
Emma's audio itself and reports when each turn starts, ends or is cut off; on
the phone nothing reports back, so the transport does what the browser's
player and ambience.js do:

- a playout clock sends one 20 ms frame every 20 ms in real time, so at most a
  frame is ever queued in Asterisk and a barge-in flush is near-instant;
- a turn starts once 60 ms of it is buffered (or all of it has arrived), a
  newer turn replaces what's left of an older one, and the clock reports
  playback started / ended / interrupted with played_ms, which the session
  uses for barge-in, echo, the recap-heard rule and the silence ladder;
- typing and the occasional door or chair are mixed in (phone_audio.LineSounds);
- 16 kHz to 8 kHz on the way out, 8 kHz to 16 kHz on the way in.

Keypad digits become one caller turn (a phone number keyed in), sent after "#"
or DTMF_TIMEOUT_S of quiet; "*" clears them; the first key stops Emma talking.

Registry links the Asterisk side to Emma's: the dialplan registers an inbound
call (and its caller ID) over HTTP before AudioSocket() and gets the UUID to
use; a recovery call registers its UUID before Originate. When the socket
closes, the dialplan asks where the call goes next ("transfer" puts it through
to the front desk when Emma promised that).
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
import uuid as uuid_mod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

import config
import events
import phone_audio

logger = logging.getLogger("audiosocket")

KIND_HANGUP = 0x00
KIND_UUID = 0x01
KIND_DTMF = 0x03
KIND_SLIN = 0x10        # 8 kHz signed-linear, mono
KIND_SLIN16 = 0x12      # 16 kHz signed-linear (newer Asterisk builds)
KIND_ERROR = 0xFF

FRAME_S = 0.02
VOICE_FRAME_BYTES = phone_audio.FRAME * 2          # 20 ms at 16 kHz
PREBUFFER_BYTES = int(0.06 * phone_audio.RATE) * 2
LATE_RESET_S = 0.1                                  # the clock fell this far behind: don't burst to catch up
HANGUP_TAIL_S = 0.3                                 # let the last syllable reach the caller before hanging up
MIRRORED_EVENTS = {"caption", "state", "turn", "metrics", "error", "bye"}


def frame(kind: int, payload: bytes = b"") -> bytes:
    return struct.pack(">BH", kind, len(payload)) + payload


async def read_frame(reader: asyncio.StreamReader) -> tuple:
    """(kind, payload); (KIND_HANGUP, b"") when the socket closes."""
    try:
        head = await reader.readexactly(3)
        kind, length = struct.unpack(">BH", head)
        payload = await reader.readexactly(length) if length else b""
    except (asyncio.IncompleteReadError, ConnectionError):
        return KIND_HANGUP, b""
    return kind, payload


# ------------------------------------------------------------------ registry

@dataclass
class PendingCall:
    kind: str                          # "inbound" | "outbound"
    caller_id: Optional[str] = None    # inbound: CALLERID(num) as Asterisk had it
    ring: Any = None                   # outbound: outbound.Ringing
    created: float = field(default_factory=time.monotonic)


class Registry:
    """UUIDs the dialplan and Originate hand to AudioSocket, and where each call goes after Emma."""

    TTL_S = 120

    def __init__(self):
        self.pending: dict = {}
        self.next_route: dict = {}

    def _sweep(self):
        now = time.monotonic()
        for key in [k for k, p in self.pending.items() if now - p.created > self.TTL_S]:
            self.pending.pop(key, None)
        for key in [k for k, (_, at) in self.next_route.items() if now - at > self.TTL_S]:
            self.next_route.pop(key, None)

    def register_inbound(self, caller_id: Optional[str]) -> str:
        self._sweep()
        key = str(uuid_mod.uuid4())
        self.pending[key] = PendingCall("inbound", caller_id=(caller_id or "").strip() or None)
        return key

    def register_outbound(self, ring) -> str:
        self._sweep()
        key = str(uuid_mod.uuid4())
        self.pending[key] = PendingCall("outbound", ring=ring)
        return key

    def take(self, key: str) -> Optional[PendingCall]:
        self._sweep()
        return self.pending.pop(key, None)

    def set_next(self, key: str, route: str):
        self.next_route[key] = (route, time.monotonic())

    def pop_next(self, key: str) -> str:
        route = self.next_route.pop(key, None)
        return route[0] if route else "hangup"


registry = Registry()


# ------------------------------------------------------------------ transport

class PhoneTransport:
    """CallSession's Transport over one AudioSocket connection (see the module notes)."""

    def __init__(self, writer: asyncio.StreamWriter, call_id: str, key: str,
                 sounds: Optional[phone_audio.LineSounds] = None):
        self.writer = writer
        self.call_id = call_id
        self.key = key
        self.session = None
        self.sounds = sounds or phone_audio.LineSounds()
        self.open = True
        self.hung_up = False                     # the caller (Asterisk) ended the call
        self._down = phone_audio.Downsampler()
        self._turn = 0
        self._buf = bytearray()
        self._started = False
        self._end_marked = False
        self._played = 0                         # samples of the current turn sent
        self._min_turn = 0
        self._reports: asyncio.Queue = asyncio.Queue()
        self._tasks: list = []
        self.frames_sent = 0

    def attach(self, session):
        """Start the playout clock for `session` (its on_control receives the playback reports)."""
        self.session = session
        self._tasks = [asyncio.create_task(self._clock()), asyncio.create_task(self._report_loop())]

    # ---- Transport
    async def send_audio(self, turn_id: int, pcm: bytes):
        if turn_id < self._min_turn or turn_id < self._turn or not pcm:
            return
        if turn_id > self._turn:
            self._reset(turn_id)                 # a newer turn replaces what's left (playback-worklet.js)
        self._buf += pcm

    async def send_event(self, event: dict):
        kind = event.get("type")
        if kind in MIRRORED_EVENTS:
            events.publish({**event, "call_id": self.call_id})
        if kind == "turn" and event.get("phase") == "audio_done" and event.get("turn") == self._turn:
            self._end_marked = True
        elif kind == "sfx" and event.get("name") == "typing":
            self.sounds.typing(event.get("after_ms") or 0, event.get("duration_ms") or 1500,
                               turn=event.get("turn") or 0, until_speech=bool(event.get("until_speech")))

    async def flush(self, turn_id: int):
        self._min_turn = max(self._min_turn, turn_id + 1)
        had_audio = self._started or bool(self._buf)
        turn, played = self._turn, self._played_ms()
        self._buf.clear()
        self._started = self._end_marked = False
        self.sounds.stop_typing()
        self.sounds.speaking(False)
        if had_audio:
            self._report(turn, "interrupted", played)

    async def close(self):
        if not self.open:
            return
        self.open = False
        state = getattr(self.session, "s", None)
        if getattr(state, "transfer_requested", False):
            registry.set_next(self.key, "transfer")
        for task in self._tasks:
            task.cancel()
        if not self.hung_up:
            try:
                # A moment of silence first, so the last syllable isn't clipped by the hang-up.
                silence = frame(KIND_SLIN, b"\x00" * (VOICE_FRAME_BYTES // 2))
                for _ in range(int(HANGUP_TAIL_S / FRAME_S)):
                    self.writer.write(silence)
                    await asyncio.sleep(FRAME_S)
                self.writer.write(frame(KIND_HANGUP))
                await self.writer.drain()
            except (ConnectionError, RuntimeError):
                pass
        try:
            self.writer.close()
        except Exception:
            pass

    # ---- playout
    def _reset(self, turn_id: int):
        self._turn = turn_id
        self._buf.clear()
        self._started = self._end_marked = False
        self._played = 0

    def _played_ms(self) -> int:
        return round(self._played / phone_audio.RATE * 1000)

    def _report(self, turn: int, event: str, played_ms: Optional[int] = None):
        msg = {"type": "playback", "turn": turn, "event": event}
        if played_ms is not None:
            msg["played_ms"] = played_ms
        self._reports.put_nowait(msg)

    async def _report_loop(self):
        """Playback reports reach the session in order, without holding up the clock."""
        while True:
            msg = await self._reports.get()
            try:
                await self.session.on_control(msg)
            except Exception as exc:
                logger.warning("[%s] playback report failed: %s", self.call_id, exc)

    def next_frame(self) -> bytes:
        """One 20 ms frame of Emma's side at 16 kHz (voice + line sounds), advancing playback."""
        voice = None
        ready = self._started or self._end_marked or len(self._buf) >= PREBUFFER_BYTES
        if ready and self._buf:
            voice = bytes(self._buf[:VOICE_FRAME_BYTES])
            del self._buf[:VOICE_FRAME_BYTES]
            if not self._started:
                self._started = True
                self.sounds.speech_started(self._turn)
                self._report(self._turn, "started")
            self._played += len(voice) // 2
        if self._started and self._end_marked and not self._buf:
            self._report(self._turn, "ended", self._played_ms())
            self._started = self._end_marked = False
            self.sounds.speaking(False)
        return phone_audio.mix(voice, self.sounds.frame())

    async def _clock(self):
        loop = asyncio.get_running_loop()
        due = loop.time()
        try:
            while self.open and not self.hung_up and not self.writer.is_closing():
                pcm = self._down.process(self.next_frame())
                self.writer.write(frame(KIND_SLIN, pcm))
                self.frames_sent += 1
                if self.writer.transport.get_write_buffer_size() > 64 * 1024:
                    await self.writer.drain()
                due += FRAME_S
                now = loop.time()
                if now - due > LATE_RESET_S:
                    due = now                    # the loop stalled: carry on from now, no burst
                await asyncio.sleep(max(0.0, due - now))
        except asyncio.CancelledError:
            raise
        except (ConnectionError, RuntimeError) as exc:
            logger.info("[%s] phone line closed while sending: %s", self.call_id, exc)
            self.open = False


# ------------------------------------------------------------------ one call

class PhoneCall:
    """Feeds one connection's caller audio and keypad digits to the session until either side hangs up."""

    def __init__(self, reader: asyncio.StreamReader, transport: PhoneTransport):
        self.reader = reader
        self.t = transport
        self._up = phone_audio.Upsampler()
        self._digits = ""
        self._digit_timer: Optional[asyncio.Task] = None
        self.rate: Optional[int] = None

    async def run(self, session):
        while not session.closed:
            kind, payload = await read_frame(self.reader)
            if kind == KIND_SLIN:
                self.rate = self.rate or 8000
                await session.on_audio(self._up.process(payload))
            elif kind == KIND_SLIN16:
                self.rate = self.rate or 16000
                await session.on_audio(payload)
            elif kind == KIND_DTMF:
                await self._key(session, payload[:1].decode("ascii", "ignore"))
            elif kind == KIND_HANGUP:
                self.t.hung_up = True
                break
            elif kind == KIND_ERROR:
                logger.warning("[%s] Asterisk reported an AudioSocket error (%s)", self.t.call_id, payload.hex())
                self.t.hung_up = True
                break
        if self._digit_timer is not None:
            self._digit_timer.cancel()

    async def _key(self, session, key: str):
        if not key:
            return
        if not self._digits and key not in "*#":
            await session.interrupt("keypad")    # the caller started keying: Emma stops talking
        if self._digit_timer is not None:
            self._digit_timer.cancel()
            self._digit_timer = None
        if key == "*":
            self._digits = ""
        elif key == "#":
            await self._send_digits(session)
        elif key.isdigit():
            self._digits += key
            self._digit_timer = asyncio.create_task(self._digits_after_quiet(session))

    async def _digits_after_quiet(self, session):
        await asyncio.sleep(config.DTMF_TIMEOUT_S)
        self._digit_timer = None
        await self._send_digits(session)

    async def _send_digits(self, session):
        digits, self._digits = self._digits, ""
        if digits:
            await session.on_control({"type": "text", "text": digits})


# ------------------------------------------------------------------ server

OnCall = Callable[[str, asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


class AudioSocketServer:
    """
    Accepts AudioSocket connections on loopback: reads the UUID frame, then
    hands the connection to on_call(uuid, reader, writer), which runs the call.
    """

    def __init__(self, on_call: OnCall, host: Optional[str] = None, port: Optional[int] = None):
        self.on_call = on_call
        self.host = host or config.AUDIOSOCKET_HOST
        self.port = config.AUDIOSOCKET_PORT if port is None else port
        self.server: Optional[asyncio.base_events.Server] = None

    async def start(self):
        self.server = await asyncio.start_server(self._handle, self.host, self.port)
        self.port = self.server.sockets[0].getsockname()[1]
        logger.info("AudioSocket listening on %s:%d", self.host, self.port)

    async def stop(self):
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            kind, payload = await asyncio.wait_for(read_frame(reader), timeout=5)
            if kind != KIND_UUID or len(payload) != 16:
                logger.warning("AudioSocket connection without a UUID (type %#x); closed", kind)
                writer.close()
                return
            await self.on_call(str(uuid_mod.UUID(bytes=payload)), reader, writer)
        except asyncio.TimeoutError:
            writer.close()
        except Exception as exc:
            logger.error("AudioSocket call failed: %s", exc, exc_info=True)
            try:
                writer.write(frame(KIND_HANGUP))
                writer.close()
            except Exception:
                pass
