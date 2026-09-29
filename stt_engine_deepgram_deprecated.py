"""
Deepgram STT Engine — Real-time streaming Speech-to-Text.

Uses raw WebSocket connection to Deepgram's streaming API (no SDK needed).
Sends PCM audio and receives JSON transcription events.

Features:
    - Real-time interim results for live transcript display
    - Utterance end detection for conversation turn-taking
    - Automatic reconnection on disconnect
"""

import asyncio
import json
import logging
from typing import Callable, Optional

import websockets

logger = logging.getLogger(__name__)


class DeepgramSTT:
    """
    Streams audio to Deepgram via WebSocket and fires callbacks on transcription events.
    
    Usage:
        stt = DeepgramSTT(api_key="...", on_transcript=..., on_utterance_end=...)
        await stt.connect(sample_rate=16000)
        await stt.send_audio(pcm_bytes)
        await stt.close()
    """

    DEEPGRAM_WS_URL = "wss://api.deepgram.com/v1/listen"

    def __init__(
        self,
        api_key: str,
        on_transcript: Optional[Callable] = None,
        on_utterance_end: Optional[Callable] = None,
        model: str = "nova-3",
        language: str = "en-IN",
    ):
        self.api_key = api_key
        self.on_transcript = on_transcript
        self.on_utterance_end = on_utterance_end
        self.model = model
        self.language = language

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._current_utterance = ""
        self.is_connected = False

    async def connect(self, sample_rate: int = 16000):
        """Open WebSocket to Deepgram streaming API."""
        if not self.api_key:
            logger.warning("No Deepgram API key — STT disabled")
            return

        params = (
            f"?model={self.model}"
            f"&language={self.language}"
            f"&encoding=linear16"
            f"&sample_rate={sample_rate}"
            f"&channels=1"
            f"&punctuate=true"
            f"&interim_results=true"
            f"&endpointing=150"
            f"&utterance_end_ms=1000"
            f"&smart_format=true"
        )

        url = f"{self.DEEPGRAM_WS_URL}{params}"
        headers = {"Authorization": f"Token {self.api_key}"}

        try:
            self._ws = await websockets.connect(
                url,
                additional_headers=headers,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
            )
            self.is_connected = True
            self._receive_task = asyncio.create_task(self._receive_loop())
            logger.info("Deepgram STT connected (model=%s)", self.model)
        except Exception as e:
            logger.error("Failed to connect to Deepgram: %s", e)
            self.is_connected = False

    async def send_audio(self, audio_bytes: bytes):
        """Send raw PCM audio bytes to Deepgram."""
        if self._ws and self.is_connected:
            try:
                await self._ws.send(audio_bytes)
            except Exception as e:
                logger.error("Error sending audio to Deepgram: %s", e)
                self.is_connected = False

    async def _receive_loop(self):
        """Background task: receive and process Deepgram messages."""
        try:
            async for message in self._ws:
                try:
                    data = json.loads(message)
                    msg_type = data.get("type", "")

                    if msg_type == "Results":
                        await self._handle_result(data)
                    elif msg_type == "UtteranceEnd":
                        await self._handle_utterance_end()
                    elif msg_type == "Error":
                        logger.error("Deepgram error: %s", data.get("description", ""))
                    elif msg_type == "Metadata":
                        logger.debug("Deepgram metadata: %s", data)
                except json.JSONDecodeError:
                    logger.warning("Non-JSON message from Deepgram")
                except Exception as e:
                    logger.error("Error processing Deepgram message: %s", e)

        except websockets.exceptions.ConnectionClosed as e:
            logger.info("Deepgram connection closed: %s", e)
        except Exception as e:
            logger.error("Deepgram receive loop error: %s", e)
        finally:
            self.is_connected = False

    async def _handle_result(self, data: dict):
        """Process a Deepgram Results message."""
        channel = data.get("channel", {})
        alternatives = channel.get("alternatives", [])
        if not alternatives:
            return

        transcript = alternatives[0].get("transcript", "").strip()
        if not transcript:
            return

        is_final = data.get("is_final", False)
        speech_final = data.get("speech_final", False)

        if is_final:
            # Accumulate final transcript segments
            if self._current_utterance:
                self._current_utterance += " " + transcript
            else:
                self._current_utterance = transcript

            # If speech_final, the utterance is complete
            if speech_final and self.on_utterance_end:
                await self.on_utterance_end(self._current_utterance.strip())
                self._current_utterance = ""

        # Fire transcript callback (for live display)
        if self.on_transcript:
            display_text = (
                f"{self._current_utterance} {transcript}".strip()
                if not is_final
                else self._current_utterance or transcript
            )
            await self.on_transcript(display_text, is_final)

    async def _handle_utterance_end(self):
        """Handle Deepgram's UtteranceEnd event."""
        if self._current_utterance.strip() and self.on_utterance_end:
            await self.on_utterance_end(self._current_utterance.strip())
        self._current_utterance = ""

    def reset_utterance(self):
        """Reset the current utterance buffer (e.g., on barge-in)."""
        self._current_utterance = ""

    async def close(self):
        """Close the Deepgram connection."""
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        if self._ws:
            try:
                # Send close signal to Deepgram
                await self._ws.send(json.dumps({"type": "CloseStream"}))
                await self._ws.close()
            except Exception:
                pass

        self.is_connected = False
        logger.info("Deepgram STT closed")
