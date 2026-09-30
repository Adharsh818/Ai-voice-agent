"""
Emma voice server — the browser test harness for the real-time call pipeline.

    GET  /            the single-circle talk page
    WS   /ws/voice    one call: 16 kHz PCM up, Emma's PCM + events down
    GET  /health      key presence, model verification, prompt-cache status
    GET  /metrics     perceived-latency p50/p95 by tier

All call logic lives in call_session.CallSession; this module only adapts the
browser WebSocket to its Transport protocol and owns process-wide warm
resources (HTTP pool, prompt cache, Gemini client).

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
"""

import asyncio
import json
import logging
import struct
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import config
import llm
import logredact
import phrases
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

MAX_CONCURRENT_CALLS = 1  # approved scope for version 1
AUDIO_FRAME = 0x01


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
    app.state.active_calls = 0
    app.state.cache_ready = False

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
    ]
    logger.info("Emma is listening on http://%s:%d", config.SERVER_HOST, config.SERVER_PORT)
    if config.SERVER_HOST not in ("127.0.0.1", "localhost", "::1"):
        logger.warning("SERVER_HOST=%s exposes Emma to the network; keep it on 127.0.0.1 "
                       "unless it is behind TLS and a login.", config.SERVER_HOST)
    yield
    for task in background:
        task.cancel()
    await app.state.http.aclose()


app = FastAPI(title="Pearl Dental Clinic — Emma Voice Agent", lifespan=lifespan)
STATIC_DIR = Path(__file__).parent / "static"


@app.middleware("http")
async def revalidate_page_assets(request, call_next):
    """Browsers must revalidate the page and its scripts, so an update never runs stale JS."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class BrowserTransport:
    """CallSession's Transport over the browser WebSocket."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
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
    if state.active_calls >= MAX_CONCURRENT_CALLS:
        await ws.send_text(json.dumps({"type": "error", "message": "Emma is on another call. Try again shortly."}))
        await ws.close()
        return

    state.active_calls += 1
    transport = BrowserTransport(ws)
    session = CallSession(transport, _services(state))
    try:
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
        logger.error("[%s] session error: %s", session.call_id, exc, exc_info=True)
    finally:
        transport.open = False
        await session.close()
        state.active_calls -= 1


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
    }


@app.get("/metrics")
async def metrics():
    return {
        "latency": app.state.latency.summary(),
        "recent": list(app.state.latency.records)[-20:],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=config.SERVER_HOST, port=config.SERVER_PORT, log_level="info")
