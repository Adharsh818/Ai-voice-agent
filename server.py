"""
Emma voice server — the browser test harness for the real-time call pipeline.

    GET  /            the single-circle talk page
    WS   /ws/voice    one call: 16 kHz PCM up, Emma's PCM + events down
    GET  /health      key presence, model verification, prompt-cache status,
                      Calendar sync, dashboard login, transcript retention
    GET  /metrics     perceived-latency p50/p95 by tier
    /dashboard/...    the staff dashboard (dashboard.py; login required)

All call logic lives in call_session.CallSession; this module only adapts the
browser WebSocket to its Transport protocol and owns process-wide warm
resources (HTTP pool, prompt cache, Gemini client) and background jobs (the
Calendar sync worker, the transcript purge).

Call gate: one call at a time (decision Q16). A second /ws/voice connection
gets {"type":"busy"} and is closed; the slot is always released in `finally`.
Every call gets a CallRecorder (recording.py) for its transcript, and every
event sent to the talk page is mirrored to the dashboard's live panel
(events.py).

WebSocket protocol v2
---------------------
Browser -> server
    binary                         PCM16 mono 16 kHz, 20 ms frames
    {"type":"hello","v":2}
    {"type":"vad","speaking":true}  local energy cue (browser ducks Emma itself)
    {"type":"playback","turn":n,"event":"started"|"ended"|"interrupted","played_ms":x}
    {"type":"text","text":"..."}    typed input for testing
    {"type":"end"}
Server -> browser
    binary  [0x01][turn u32 LE][PCM16 16 kHz]   Emma's audio for turn n
    {"type":"state","state":"thinking"|"listening"}
    {"type":"caption","who":"user"|"emma","text":"...","final":bool}
    {"type":"turn","turn":n,"phase":"start"|"audio_done"}
    {"type":"stop","turn":n}        flush playback of turn n and older now
    {"type":"metrics",...}  {"type":"error","message":"..."}  {"type":"bye"}
    {"type":"busy","message":"..."}  another call is active; the socket closes (1013)
"""

import asyncio
import json
import logging
import struct
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import auth
import calendar_sync
import clock
import config
import dashboard
import db
import events
import llm
import logredact
import outbound
import phrases
import recording
import tts_piper
from call_session import CallSession, Services
from latency import LatencyLog
from speech import PromptCache
from stt_deepgram import DeepgramSTT
from tts_elevenlabs import ElevenLabsStreamTTS, ElevenLabsTTS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logredact.install()  # mask phone numbers in every log line
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("google_genai").setLevel(logging.WARNING)
logger = logging.getLogger("voice-server")

AUDIO_FRAME = 0x01
BUSY_MESSAGE = "Emma is on another call right now. Please try again in a minute."
# Talk-page events the dashboard's live panel also shows (not audio, not sound effects).
MIRRORED_EVENTS = {"caption", "state", "turn", "metrics", "error", "bye"}


class CallGate:
    """
    The one global call slot (plan 5.7): idle, inbound or outbound. The talk
    page, dashboard test calls and (from Day 5) outbound recovery jobs all
    acquire it, so Emma is never on two calls at once. Acquire and release are
    synchronous: on one event loop there is no await between the check and the
    claim, so two sockets can't both win.
    """

    def __init__(self):
        self.kind: Optional[str] = None
        self.call_id: Optional[str] = None
        self.since: Optional[str] = None

    @property
    def busy(self) -> bool:
        return self.call_id is not None

    def try_acquire(self, kind: str, call_id: str) -> bool:
        if self.busy:
            return False
        self.kind, self.call_id, self.since = kind, call_id, db.utc_str(clock.now())
        return True

    def release(self, call_id: str) -> bool:
        """Free the slot if `call_id` holds it (a stale release can't free someone else's call)."""
        if self.call_id != call_id:
            return False
        self.kind = self.call_id = self.since = None
        return True

    def status(self) -> dict:
        return {"busy": self.busy, "kind": self.kind, "call_id": self.call_id, "since": self.since}


@asynccontextmanager
async def lifespan(app: FastAPI):
    missing = [name for name, value in (
        ("GEMINI_API_KEY", config.GEMINI_API_KEY),
        ("DEEPGRAM_API_KEY", config.DEEPGRAM_API_KEY),
        ("ELEVENLABS_API_KEY", config.ELEVENLABS_API_KEY),
    ) if not value]
    if missing:
        logger.warning("Missing API keys: %s. Voice features will be degraded.", ", ".join(missing))

    app.state.http = httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=5.0),
        limits=httpx.Limits(max_connections=8, max_keepalive_connections=8),
    )
    app.state.http_tts = ElevenLabsTTS(
        api_key=config.ELEVENLABS_API_KEY,
        voice_id=config.ELEVENLABS_VOICE_ID,
        model_id=config.ELEVENLABS_MODEL,
        output_format=config.ELEVENLABS_OUTPUT_FORMAT,
        client=app.state.http,
    )
    app.state.cache = PromptCache(
        config.CACHE_DIR, config.ELEVENLABS_VOICE_ID, config.ELEVENLABS_MODEL,
        config.ELEVENLABS_OUTPUT_FORMAT, app.state.http_tts.voice_settings,
    )
    app.state.latency = LatencyLog(config.LOG_DIR)
    # Local backup voice for when ElevenLabs fails or runs out (loaded below, in the background).
    app.state.backup_tts = tts_piper.from_config()
    # The appointments database: migrated on start, DEMO-seeded when empty.
    app.state.db = await asyncio.to_thread(db.get_db)
    app.state.cache_ready = False
    if not auth.configured():
        logger.warning("Dashboard locked: %s", auth.status()["reason"])

    # Verify the model (also warms its TLS connection) and pre-render prompts
    # in the background; the server accepts calls immediately either way.
    async def warm():
        await llm.get_nlu().verify_model()
        if config.ELEVENLABS_API_KEY:
            await app.state.cache.warm(phrases.all_phrases(), app.state.http_tts)
        app.state.cache_ready = True

    background = [
        asyncio.create_task(warm()),
        asyncio.create_task(llm.get_nlu().keep_verified()),
        # Blank transcripts past the retention period now and every few hours.
        asyncio.create_task(recording.purge_loop()),
    ]
    if app.state.backup_tts is not None:
        background.append(asyncio.create_task(app.state.backup_tts.warm()))
    # Mirror appointments to Google Calendar, if a service-account key is set up.
    calendar_task = calendar_sync.start_worker()
    if calendar_task is not None:
        background.append(calendar_task)
    # Doctor-unavailability recovery calls (outbound.py): one job at a time, through the call gate.
    app.state.runner = outbound.Runner(app.state.db, app.state.gate)
    outbound.set_runner(app.state.runner)
    background.append(asyncio.create_task(app.state.runner.run()))
    logger.info("Emma is listening on http://%s:%d", config.SERVER_HOST, config.SERVER_PORT)
    if config.SERVER_HOST not in ("127.0.0.1", "localhost", "::1"):
        logger.warning("SERVER_HOST=%s exposes Emma to the network; keep it on 127.0.0.1 "
                       "unless it is behind TLS and a login.", config.SERVER_HOST)
    yield
    app.state.runner.stop()
    outbound.set_runner(None)
    for task in background:
        task.cancel()
    await asyncio.gather(*background, return_exceptions=True)
    calendar_sync.stop_worker()
    await app.state.http.aclose()
    await asyncio.to_thread(db.reset)


app = FastAPI(title="Pearl Dental Clinic — Emma Voice Agent", lifespan=lifespan)
app.state.gate = CallGate()
app.state.sessions = {}          # call_id -> CallSession, for the dashboard's takeover controls
STATIC_DIR = Path(__file__).parent / "static"

# The dashboard shows patient data: no framing, no inline script, no caching of API answers.
DASHBOARD_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
}


@app.middleware("http")
async def revalidate_page_assets(request, call_next):
    """Browsers must revalidate the page and its scripts, so an update never runs stale JS."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    elif path.startswith("/dashboard") or path.startswith("/patient"):
        response.headers.update(DASHBOARD_HEADERS)
        response.headers.setdefault("Cache-Control", "no-store" if "/api/" in path else "no-cache")
    return response


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/dashboard/static", StaticFiles(directory=str(STATIC_DIR / "dashboard")), name="dashboard-static")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(dashboard.router)


class BrowserTransport:
    """CallSession's Transport over the browser WebSocket; mirrors call events to the dashboard."""

    def __init__(self, ws: WebSocket, call_id: Optional[str] = None):
        self.ws = ws
        self.call_id = call_id
        self._lock = asyncio.Lock()
        self.open = True

    async def _send(self, message: dict):
        if not self.open:
            return
        async with self._lock:
            try:
                await self.ws.send(message)
            except Exception:
                self.open = False

    async def send_audio(self, turn_id: int, pcm: bytes):
        frame = struct.pack("<BI", AUDIO_FRAME, turn_id) + pcm
        await self._send({"type": "websocket.send", "bytes": frame})

    async def send_event(self, event: dict):
        if event.get("type") in MIRRORED_EVENTS:
            events.publish({**event, "call_id": self.call_id})
        await self._send({"type": "websocket.send", "text": json.dumps(event)})

    async def flush(self, turn_id: int):
        await self.send_event({"type": "stop", "turn": turn_id})

    async def close(self):
        if self.open:
            self.open = False
            try:
                await self.ws.close()
            except Exception:
                pass


def _services(app_state) -> Services:
    def stt_factory(**callbacks):
        return DeepgramSTT(
            api_key=config.DEEPGRAM_API_KEY,
            model=config.DEEPGRAM_MODEL,
            language=config.DEEPGRAM_LANGUAGE,
            endpointing_ms=config.DEEPGRAM_ENDPOINTING_MS,
            utterance_end_ms=config.DEEPGRAM_UTTERANCE_END_MS,
            keyterms=config.DEEPGRAM_KEYTERMS,
            **callbacks,
        )

    live_tts = None
    if config.ELEVENLABS_API_KEY and config.TTS_TRANSPORT == "ws":
        live_tts = ElevenLabsStreamTTS(
            api_key=config.ELEVENLABS_API_KEY,
            voice_id=config.ELEVENLABS_VOICE_ID,
            model_id=config.ELEVENLABS_MODEL,
            output_format=config.ELEVENLABS_OUTPUT_FORMAT,
            voice_settings=app_state.http_tts.voice_settings,
        )
    return Services(
        stt_factory=stt_factory,
        tts=live_tts,
        fallback_tts=app_state.http_tts if config.ELEVENLABS_API_KEY else None,
        backup_tts=getattr(app_state, "backup_tts", None),
        cache=app_state.cache,
        latency=app_state.latency,
    )


def origin_allowed(headers) -> bool:
    """
    Only this page (or an origin listed in ALLOWED_ORIGINS) may open a call.
    Browsers always send Origin on a WebSocket handshake, so without this any
    website open in the same browser could start calls on this machine and
    spend its API credit. Clients with no Origin at all are not browsers
    (tests, tools) and are not a cross-site risk.
    """
    origin = (headers.get("origin") or "").rstrip("/")
    if not origin:
        return True
    if origin in config.ALLOWED_ORIGINS:
        return True
    host = (headers.get("host") or "").lower()
    return bool(host) and urlsplit(origin).netloc.lower() == host


@app.websocket("/ws/voice")
async def voice_websocket(ws: WebSocket):
    if not origin_allowed(ws.headers):
        logger.warning("Rejected /ws/voice from origin %r", ws.headers.get("origin"))
        await ws.close(code=1008)
        return
    await ws.accept()
    state = ws.app.state
    gate: CallGate = state.gate
    call_id = uuid.uuid4().hex[:8]
    if not gate.try_acquire("inbound", call_id):
        logger.info("Refused a second call while %s (%s) is active", gate.call_id, gate.kind)
        try:
            await ws.send_text(json.dumps({"type": "busy", "message": BUSY_MESSAGE}))
            await ws.close(code=1013)      # "try again later"
        except Exception:
            pass
        return

    session = None
    transport = BrowserTransport(ws, call_id)
    try:
        # ?mode=listen records the STT test set: captions and capture, no replies.
        listen_only = ws.query_params.get("mode") == "listen" and config.DEV_CAPTURE_AUDIO
        session = CallSession(transport, _services(state), call_id=call_id, listen_only=listen_only)
        state.sessions[call_id] = session
        if not listen_only:
            _start_recording(session, call_id)
        await session.start()
        while not session.closed:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes"):
                await session.on_audio(message["bytes"])
            elif message.get("text"):
                try:
                    await session.on_control(json.loads(message["text"]))
                except json.JSONDecodeError:
                    pass
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception as exc:
        logger.error("[%s] session error: %s", call_id, exc, exc_info=True)
    finally:
        transport.open = False
        try:
            if session is not None:
                await session.close()
                _finish_recording(session)
        except Exception as exc:
            logger.error("[%s] error closing the call: %s", call_id, exc, exc_info=True)
        finally:
            state.sessions.pop(call_id, None)
            gate.release(call_id)
            # Anything booked, moved or cancelled on the call reaches Calendar
            # now rather than at the worker's next poll.
            calendar_sync.notify()


# ---------------------------------------------------------------- recovery calls (outbound.py)
# The demo's "patient phone" is a logged-in browser tab at /patient: it rings
# when the runner starts a job, and Answer opens /ws/outbound for the call.


@app.get("/patient", include_in_schema=False)
async def patient_page(request: Request):
    if not auth.configured() or auth.current_user(request) is None:
        return RedirectResponse("/dashboard/login", status_code=303)
    return FileResponse(STATIC_DIR / "patient.html")


@app.get("/patient/api/ring")
async def patient_ring(request: Request):
    auth.require_staff(request)
    runner = outbound.get_runner()
    ring = runner.ringing if runner is not None else None
    if ring is None or ring.answered.is_set():
        return {"ringing": None}
    return {"ringing": ring.public()}


@app.post("/patient/api/decline")
async def patient_decline(request: Request):
    auth.require_staff(request)
    data = await request.json()
    runner = outbound.get_runner()
    ok = runner is not None and runner.decline(int(data.get("job_id") or 0), str(data.get("token") or ""))
    if not ok:
        raise HTTPException(status_code=409, detail="That call isn't ringing any more.")
    return {"ok": True}


@app.websocket("/ws/outbound")
async def outbound_websocket(ws: WebSocket):
    """The answered recovery call: same protocol as /ws/voice, Emma speaks first."""
    if not origin_allowed(ws.headers) or auth.read_session(ws.cookies.get(auth.COOKIE_NAME)) is None:
        await ws.close(code=1008)
        return
    runner = outbound.get_runner()
    try:
        job_id = int(ws.query_params.get("job") or 0)
    except ValueError:
        job_id = 0
    ring = runner.answer(job_id, ws.query_params.get("token") or "") if runner is not None else None
    if ring is None:
        await ws.close(code=1008)
        return
    await ws.accept()
    ring.connected.set()
    from dialogue import recovery

    state = ws.app.state
    call_id = ring.call_id
    claimed = ring.claimed
    session = None
    ctx = None
    transport = BrowserTransport(ws, call_id)
    try:
        block = await state.db.run(outbound.campaign_block, claimed.campaign_id)
        ctx = recovery.new_context(call_id=call_id, job_id=claimed.job_id, phone_e164=claimed.phone_e164,
                                   appointments=claimed.appointments, block=block)
        session = CallSession(transport, _services(state), call_id=call_id, state=ctx)
        state.sessions[call_id] = session
        _start_recording(session, call_id, direction="outbound")
        await session.start()
        while not session.closed:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes"):
                await session.on_audio(message["bytes"])
            elif message.get("text"):
                try:
                    await session.on_control(json.loads(message["text"]))
                except json.JSONDecodeError:
                    pass
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception as exc:
        logger.error("[%s] recovery call error: %s", call_id, exc, exc_info=True)
    finally:
        transport.open = False
        try:
            if session is not None:
                await session.close()
                session.outcome = f"recovery_{ctx.outcome or 'abandoned'}" if ctx is not None else session.outcome
                _finish_recording(session)
        except Exception as exc:
            logger.error("[%s] error closing the recovery call: %s", call_id, exc, exc_info=True)
        finally:
            state.sessions.pop(call_id, None)
            if ctx is not None:
                ring.result = recovery.call_result(ctx)
            ring.call_done.set()
            calendar_sync.notify()


def _start_recording(session, call_id: str, direction: str = "inbound"):
    """
    Give the call its transcript recorder. CallSession records the turns
    (session.recorder.turn(...)); if it made its own recorder, that one is used.
    """
    recorder = getattr(session, "recorder", None)
    if recorder is None:
        recorder = recording.CallRecorder(call_id, direction=direction)
        session.recorder = recorder
    recorder.start()


def _finish_recording(session):
    """
    Close the call record unless CallSession already did. The outcome and the
    caller's "don't keep my details" choice come from the session when it sets
    them (session.outcome / session.keep_transcript), else from the engine state.
    """
    recorder = getattr(session, "recorder", None)
    if recorder is None or getattr(recorder, "ended", False):
        return
    engine = getattr(session, "s", None)
    outcome = getattr(session, "outcome", None) or (
        "completed" if getattr(engine, "closed_conversation", False) else "abandoned")
    keep = getattr(session, "keep_transcript", None)
    if keep is None:
        keep = getattr(engine, "keep_transcript", True)
    phone = getattr(session, "caller_phone", None)
    if phone is None and getattr(engine, "phone_confirmed", False):
        phone = getattr(engine, "phone", None)
    recorder.end(outcome, keep_transcript=bool(keep), caller_phone=phone if isinstance(phone, str) else None)


@app.get("/client-config")
async def client_config():
    """Sound settings for the talk page (phone-line filter, clinic sound)."""
    return {
        "phone_line": config.PHONE_LINE_EFFECT,
        "ambience": {
            "enabled": config.AMBIENCE_ENABLED,
            "event_db": config.AMBIENCE_EVENT_DB,
        },
        "typing": config.TYPING_SFX,
    }


def _db_summary(conn) -> dict:
    count = lambda sql: conn.execute(sql).fetchone()[0]
    return {
        "branches": count("SELECT COUNT(*) FROM branches WHERE active = 1"),
        "doctors": count("SELECT COUNT(*) FROM doctors WHERE active = 1"),
        "upcoming_appointments": count(
            f"SELECT COUNT(*) FROM appointments WHERE status = 'booked' AND start_utc > '{db.now_str()}'"),
        "demo_data": bool(count("SELECT COUNT(*) FROM branches WHERE is_demo = 1")),
    }


@app.get("/health")
async def health():
    nlu = llm.get_nlu()
    services = {
        "gemini": bool(config.GEMINI_API_KEY) and nlu.available is not False,
        "deepgram": bool(config.DEEPGRAM_API_KEY),
        "elevenlabs": bool(config.ELEVENLABS_API_KEY),
    }
    return {
        "status": "ok" if all(services.values()) else "degraded",
        **services,
        "llm": nlu.status(),
        "prompt_cache_ready": app.state.cache_ready,
        "tier0": config.TIER0_ENABLED,
        "tts_transport": config.TTS_TRANSPORT,
        "database": await app.state.db.run(_db_summary),
        "call": app.state.gate.status(),
        "calendar": {**calendar_sync.status(), **await app.state.db.run(_calendar_summary)},
        "dashboard": _dashboard_summary(),
        "retention": recording.status(),
        "recovery": app.state.runner.status() if getattr(app.state, "runner", None) else None,
    }


def _calendar_summary(conn) -> dict:
    return {"outbox": calendar_sync.outbox_counts(conn), "branch_calendars": calendar_sync.calendars_configured(conn)}


def _dashboard_summary() -> dict:
    state = auth.status()
    return {"auth_configured": state["configured"], "reason": state["reason"], "path": "/dashboard"}


@app.get("/metrics")
async def metrics():
    return {
        "latency": app.state.latency.summary(),
        "recent": list(app.state.latency.records)[-20:],
    }


if __name__ == "__main__":
    import uvicorn

    # An open dashboard holds a never-ending event stream; without a bound,
    # uvicorn would wait for it forever on Ctrl+C or a restart.
    uvicorn.run(app, host=config.SERVER_HOST, port=config.SERVER_PORT, log_level="info",
                timeout_graceful_shutdown=5)
