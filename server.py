import asyncio
import base64
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import config
from stt_engine_deepgram_deprecated import DeepgramSTT
from tts_engine_elevenlabs_deprecated import ElevenLabsTTS
from ai_engine import async_get_ai_response, SessionState
# NOTE: Google STT/TTS engines (google_stt_engine.py, google_tts_engine.py) are
# used exclusively by the Asterisk AGI pipeline (asterisk_agi.py).

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("voice-server")


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app):
    """Startup/shutdown lifecycle."""
    # --- Startup ---
    missing = []
    if not config.GEMINI_API_KEY:
        missing.append("GEMINI_API_KEY")
    if not config.DEEPGRAM_API_KEY:
        missing.append("DEEPGRAM_API_KEY")
    if not config.ELEVENLABS_API_KEY:
        missing.append("ELEVENLABS_API_KEY")

    if missing:
        logger.warning(
            "⚠️  Missing API keys: %s — set them in .env file. "
            "Voice features will be degraded.",
            ", ".join(missing),
        )
    else:
        logger.info("✅ All API keys configured")

    logger.info(
        "🚀 Server starting on http://%s:%d",
        config.SERVER_HOST,
        config.SERVER_PORT,
    )
    yield
    # --- Shutdown ---
    logger.info("Server shutting down")


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------
app = FastAPI(title="Pearl Dental Clinic — Emma Voice Agent", lifespan=lifespan)

# Serve static frontend files
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)


@app.get("/")
async def serve_index():
    """Serve the main voice UI."""
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Voice Session
# ---------------------------------------------------------------------------
class VoiceSession:
    """
    Manages a single voice conversation session over WebSocket.

    Lifecycle:
        1. Client connects → session created
        2. Server sends greeting audio
        3. Client streams mic audio → Deepgram STT → transcript
        4. On utterance end → AI Engine → response text
        5. Response text → ElevenLabs TTS → audio → client
        6. Repeat until conversation closes or client disconnects
    """

    def __init__(self, websocket: WebSocket):
        self.ws = websocket
        self.session_id = str(uuid.uuid4())[:8]

        # Per-session conversation state (isolated from other sessions)
        self.session_state = SessionState()

        # Engines (initialized in run())
        self.stt: DeepgramSTT | None = None
        self.tts: ElevenLabsTTS | None = None

        # Barge-in control
        self.is_speaking = False
        self._cancel_tts = asyncio.Event()
        self._awaiting_playback_complete = False
        self._playback_finished = asyncio.Event()
        self._turn_started_at: float | None = None

        # Utterance processing queue
        self._utterance_queue: asyncio.Queue[str] = asyncio.Queue()

        logger.info("[%s] Session created", self.session_id)

    async def run(self):
        """Main session lifecycle."""
        try:
            # Initialize TTS engine (ElevenLabs — for WebSocket/browser pipeline)
            self.tts = ElevenLabsTTS(
                api_key=config.ELEVENLABS_API_KEY,
                voice_id=config.ELEVENLABS_VOICE_ID,
                model_id=config.ELEVENLABS_MODEL,
            )

            # Initialize STT before any audio is sent.  Otherwise microphone
            # frames buffered during the greeting are mistaken for caller speech.
            self.stt = DeepgramSTT(
                api_key=config.DEEPGRAM_API_KEY,
                on_transcript=self._on_transcript,
                on_utterance_end=self._on_utterance_end,
            )
            await self.stt.connect(sample_rate=16000)

            # Start utterance processor in background
            processor_task = asyncio.create_task(self._process_utterances())

            # Send the greeting only after the STT and processor are ready.
            await self._send_greeting()

            # Main receive loop: audio + control messages from client
            try:
                while True:
                    message = await self.ws.receive()

                    if message["type"] == "websocket.disconnect":
                        break

                    if "bytes" in message:
                        # Binary frame = raw PCM audio from client mic
                        audio_bytes = message["bytes"]
                        if audio_bytes:
                            # Barge-in detection: compute RMS energy to ensure user is actually speaking
                            if self.is_speaking:
                                try:
                                    import numpy as np
                                    samples = np.frombuffer(audio_bytes, dtype=np.int16)
                                    rms = np.sqrt(np.mean(samples.astype(np.float32) ** 2)) / 32768.0 if len(samples) > 0 else 0.0
                                except Exception:
                                    rms = 0.0

                                # Only cancel TTS if audio energy exceeds speech threshold (0.035)
                                if rms > 0.035:
                                    self._cancel_tts.set()
                                    self.is_speaking = False
                                    if self.stt:
                                        self.stt.reset_utterance()
                                    logger.info("[%s] Barge-in detected (RMS=%.3f)", self.session_id, rms)

                            # Forward to Deepgram STT
                            if self.stt and self.stt.is_connected:
                                await self.stt.send_audio(audio_bytes)

                    elif "text" in message:
                        # Text frame = JSON control message
                        try:
                            data = json.loads(message["text"])
                            await self._handle_control_message(data)
                        except json.JSONDecodeError:
                            pass

            except WebSocketDisconnect:
                logger.info("[%s] Client disconnected", self.session_id)

            # Clean up processor
            processor_task.cancel()
            try:
                await processor_task
            except asyncio.CancelledError:
                pass

        except Exception as e:
            logger.error("[%s] Session error: %s", self.session_id, e, exc_info=True)
        finally:
            await self._cleanup()

    async def _handle_control_message(self, data: dict):
        """Handle JSON control messages from client."""
        msg_type = data.get("type", "")

        if msg_type == "barge_in":
            if self.is_speaking:
                self._cancel_tts.set()
                self.is_speaking = False
                self._awaiting_playback_complete = False
                self._playback_finished.set()
                await self._send_json({"type": "status", "status": "listening"})
                logger.info("[%s] Barge-in (explicit)", self.session_id)

        elif msg_type == "playback_complete":
            # The client, not the server, knows when the caller has actually
            # heard the last byte of audio.
            if self._awaiting_playback_complete:
                self._awaiting_playback_complete = False
                self.is_speaking = False
                self._playback_finished.set()
                await self._send_json({"type": "status", "status": "listening"})

        elif msg_type == "text_input":
            # Fallback text input (if mic isn't available)
            text = data.get("text", "").strip()
            if text:
                await self._utterance_queue.put(text)

    async def _send_greeting(self):
        """Generate and send the initial greeting."""
        try:
            greeting = await async_get_ai_response("", session_state=self.session_state)
            logger.info("[%s] Greeting: %s", self.session_id, greeting[:80])
            await self._send_response(greeting)
        except Exception as e:
            logger.error("[%s] Greeting error: %s", self.session_id, e)

    async def _on_transcript(self, text: str, is_final: bool):
        """Callback: Deepgram sent a transcript chunk."""
        try:
            await self._send_json({
                "type": "transcript",
                "text": text,
                "is_final": is_final,
            })
        except Exception:
            pass

    async def _on_utterance_end(self, text: str):
        """Callback: Deepgram detected end of user utterance."""
        if text.strip():
            self._turn_started_at = time.perf_counter()
            logger.info("[%s] User said: %s", self.session_id, text)
            await self._utterance_queue.put(text)

    async def _process_utterances(self):
        """Background task: process complete utterances through AI engine."""
        while True:
            try:
                user_text = await self._utterance_queue.get()

                if not user_text.strip():
                    continue

                # Indicate processing
                await self._send_json({"type": "status", "status": "processing"})

                # Get AI response using per-session state
                try:
                    response = await async_get_ai_response(
                        user_text,
                        session_state=self.session_state,
                    )
                except Exception as e:
                    logger.error("[%s] AI engine error: %s", self.session_id, e)
                    response = "I'm sorry, I had trouble processing that. Could you please repeat?"

                logger.info("[%s] Emma: %s", self.session_id, response[:80])
                if self._turn_started_at is not None:
                    logger.info(
                        "[%s] turn latency (utterance end -> response text): %.0f ms",
                        self.session_id,
                        (time.perf_counter() - self._turn_started_at) * 1000,
                    )

                # Send response (text + audio)
                await self._send_response(response)

                # Check if conversation is closed
                if self.session_state.closed_conversation:
                    logger.info("[%s] Conversation closed", self.session_id)
                    try:
                        await asyncio.wait_for(self._playback_finished.wait(), timeout=30)
                    except asyncio.TimeoutError:
                        logger.warning("[%s] Timed out waiting for final playback", self.session_id)
                    try:
                        await self.ws.close()
                    except Exception:
                        pass
                    break

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[%s] Utterance processing error: %s", self.session_id, e)
                try:
                    await self._send_json({
                        "type": "error",
                        "message": "Sorry, something went wrong. Please try again.",
                    })
                except Exception:
                    pass

    async def _send_response(self, text: str):
        """Send AI response as text + streaming TTS audio chunks to client."""
        response_started_at = time.perf_counter()
        self._playback_finished.clear()
        # 1. Send response text for display
        await self._send_json({
            "type": "response_text",
            "text": text,
        })

        # 2. Synthesize audio via ElevenLabs TTS (streaming mode)
        if self.tts and config.ELEVENLABS_API_KEY:
            self.is_speaking = True
            self._awaiting_playback_complete = True
            self._cancel_tts.clear()

            try:
                await self._send_json({"type": "audio_start"})
                chunk_count = 0
                first_chunk_at: float | None = None
                async for chunk in self.tts.synthesize_stream(text):
                    if self._cancel_tts.is_set():
                        logger.info("[%s] TTS audio streaming cancelled by barge-in", self.session_id)
                        break
                    if chunk:
                        chunk_count += 1
                        if first_chunk_at is None:
                            first_chunk_at = time.perf_counter()
                            logger.info(
                                "[%s] TTS first byte: %.0f ms after response start",
                                self.session_id,
                                (first_chunk_at - response_started_at) * 1000,
                            )
                        b64_chunk = base64.b64encode(chunk).decode("utf-8")
                        await self._send_json({
                            "type": "audio_chunk",
                            "data": b64_chunk,
                            "format": "mp3",
                        })

                logger.debug("[%s] Sent %d streaming audio chunks", self.session_id, chunk_count)

            except Exception as e:
                logger.error("[%s] TTS streaming error: %s", self.session_id, e)
            finally:
                # Keep the speaking state until browser playback has ended or
                # the caller barges in.  Streaming bytes finishing is not the
                # same thing as audible playback finishing.
                pass

        # 3. Signal that all audio bytes have been sent. The browser sends
        # playback_complete when the audible response really ends.
        await self._send_json({"type": "audio_end"})
        if not (self.tts and config.ELEVENLABS_API_KEY):
            self.is_speaking = False
            self._awaiting_playback_complete = False
            self._playback_finished.set()
            await self._send_json({"type": "status", "status": "listening"})

    async def _send_json(self, data: dict):
        """Send a JSON message to the client."""
        try:
            await self.ws.send_json(data)
        except Exception:
            pass

    async def _cleanup(self):
        """Release all resources."""
        if self.stt:
            await self.stt.close()
        if self.tts:
            await self.tts.close()
        logger.info("[%s] Session cleaned up", self.session_id)


# ---------------------------------------------------------------------------
# WebSocket Endpoint
# ---------------------------------------------------------------------------
@app.websocket("/ws/voice")
async def voice_websocket(websocket: WebSocket):
    """Accept a voice session WebSocket connection."""
    await websocket.accept()
    session = VoiceSession(websocket)
    await session.run()


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    services = {
        "gemini": bool(config.GEMINI_API_KEY),
        "deepgram": bool(config.DEEPGRAM_API_KEY),
        "elevenlabs": bool(config.ELEVENLABS_API_KEY),
    }
    return {
        "status": "ok" if all(services.values()) else "degraded",
        **services,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=config.SERVER_HOST,
        port=config.SERVER_PORT,
        log_level="info",
    )
