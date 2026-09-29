"""
Asterisk AGI (Asterisk Gateway Interface) Script for Pearl Dental Clinic — Emma Voice Agent.

This script is called by Asterisk when an inbound call arrives. It implements
the full voice pipeline:
    Asterisk  ──audio──►  Google STT  ──text──►  AI Engine  ──text──►  Google TTS  ──audio──►  Asterisk

Requirements:
    • A running Asterisk PBX (Linux/Docker/WSL — Asterisk does NOT run on Windows natively).
    • The `asterisk` package (pip install asterisk).
    • Google Cloud credentials configured via GOOGLE_APPLICATION_CREDENTIALS.
    • GEMINI_API_KEY in environment / .env file.

Asterisk dialplan (extensions.conf) example:
    [from-internal]
    exten => 1000,1,Answer()
    exten => 1000,n,AGI(asterisk_agi.py)
    exten => 1000,n,Hangup()

How the AGI protocol works:
    1. Asterisk launches this script as a subprocess when the dialplan hits AGI().
    2. Asterisk communicates via stdin/stdout using a text protocol.
    3. The script sends AGI commands (e.g. STREAM FILE, RECORD FILE) and reads responses.
    4. Audio is recorded to /tmp files and read back by Asterisk.

NOTE: For a production deployment, consider replacing the file-based record/play
approach with a real-time audio bridge using Asterisk's EAGI (Extended AGI) or
the Asterisk ARI (Asterisk REST Interface) with a WebSocket media bridge.
"""

import asyncio
import logging
import os
import sys
import tempfile
import wave
from pathlib import Path

# Ensure project root is on path (when called by Asterisk from a different cwd)
sys.path.insert(0, str(Path(__file__).parent))

import config
from ai_engine import async_get_ai_response, SessionState
from google_stt_engine import GoogleSTT
from google_tts_engine import GoogleTTS

logging.basicConfig(
    filename="/tmp/emma_agi.log",  # Asterisk runs on Linux; log to /tmp
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("asterisk-agi")

# ---------------------------------------------------------------------------
# AGI Protocol helpers
# ---------------------------------------------------------------------------

class AGI:
    """
    Minimal Asterisk Gateway Interface protocol implementation.

    Reads the initial AGI environment from stdin, then provides methods
    to send AGI commands and read responses.
    """

    def __init__(self):
        self.env: dict[str, str] = {}
        self._read_env()

    def _read_env(self):
        """Read the AGI environment variables sent by Asterisk at startup."""
        while True:
            line = sys.stdin.readline().strip()
            if not line:
                break  # Blank line signals end of env block
            if ":" in line:
                key, _, value = line.partition(":")
                self.env[key.strip()] = value.strip()

        logger.info("AGI env loaded: callerid=%s", self.env.get("agi_callerid", "unknown"))

    def _send(self, command: str) -> str:
        """Send an AGI command and return the response line."""
        sys.stdout.write(command + "\n")
        sys.stdout.flush()
        response = sys.stdin.readline().strip()
        logger.debug("AGI >> %s  |  << %s", command, response)
        return response

    def answer(self) -> str:
        """Answer the call."""
        return self._send("ANSWER")

    def hangup(self) -> str:
        """Hang up the call."""
        return self._send("HANGUP")

    def verbose(self, message: str, level: int = 1) -> str:
        """Log a message to the Asterisk console."""
        return self._send(f'VERBOSE "{message}" {level}')

    def stream_file(self, filename: str, escape_digits: str = "") -> str:
        """
        Play a sound file to the caller.

        Args:
            filename: Path to file WITHOUT extension (Asterisk appends format).
            escape_digits: DTMF digits that interrupt playback.
        """
        return self._send(f'STREAM FILE {filename} "{escape_digits}"')

    def record_file(
        self,
        filename: str,
        format: str = "wav",
        escape_digits: str = "#",
        timeout_ms: int = 5000,
        silence_sec: int = 2,
    ) -> str:
        """
        Record audio from the caller into a file.

        Args:
            filename: Output path WITHOUT extension.
            format: Audio format (wav, gsm, etc.).
            escape_digits: DTMF digits that stop recording.
            timeout_ms: Maximum recording duration in milliseconds.
            silence_sec: Stop recording after this many seconds of silence.
        """
        return self._send(
            f"RECORD FILE {filename} {format} \"{escape_digits}\" "
            f"{timeout_ms} s={silence_sec}"
        )

    def get_variable(self, name: str) -> str:
        """Get an Asterisk channel variable."""
        response = self._send(f"GET VARIABLE {name}")
        # Response format: "200 result=1 (value)"
        if "(" in response and ")" in response:
            return response.split("(")[1].rstrip(")")
        return ""


# ---------------------------------------------------------------------------
# Audio utilities
# ---------------------------------------------------------------------------

def read_wav_as_pcm(wav_path: str, target_sample_rate: int = 8000) -> bytes:
    """
    Read a WAV file and return raw LINEAR16 PCM bytes.
    Resamples to target_sample_rate if necessary.
    """
    with wave.open(wav_path, "rb") as wf:
        n_channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        frame_rate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    # Convert to mono if stereo
    if n_channels == 2:
        import numpy as np
        samples = np.frombuffer(raw, dtype=np.int16).reshape(-1, 2)
        raw = samples.mean(axis=1).astype(np.int16).tobytes()

    # Resample if needed
    if frame_rate != target_sample_rate:
        try:
            import numpy as np
            from scipy.signal import resample_poly
            from math import gcd
            samples = np.frombuffer(raw, dtype=np.int16)
            g = gcd(target_sample_rate, frame_rate)
            up, down = target_sample_rate // g, frame_rate // g
            resampled = resample_poly(samples, up, down).astype(np.int16)
            raw = resampled.tobytes()
        except ImportError:
            logger.warning("numpy/scipy not available — skipping resample")

    return raw


def write_pcm_as_wav(pcm_bytes: bytes, wav_path: str, sample_rate: int = 8000):
    """Write raw LINEAR16 PCM bytes to a WAV file that Asterisk can play."""
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)   # 16-bit = 2 bytes per sample
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)


# ---------------------------------------------------------------------------
# Main AGI conversation loop
# ---------------------------------------------------------------------------

async def run_conversation(agi: AGI):
    """
    Async main loop: orchestrates the STT → AI → TTS pipeline for one call.
    """
    session_state = SessionState()
    tts = GoogleTTS(
        language_code=config.GOOGLE_TTS_LANGUAGE_CODE,
        voice_name=config.GOOGLE_TTS_VOICE_NAME,
        speaking_rate=config.GOOGLE_TTS_SPEAKING_RATE,
        pitch=config.GOOGLE_TTS_PITCH,
    )

    caller_id = agi.env.get("agi_callerid", "unknown")
    logger.info("Call started from %s", caller_id)
    agi.verbose(f"Emma AGI started for caller {caller_id}")

    # --- Step 1: Play greeting ---
    try:
        greeting_text = await async_get_ai_response("", session_state=session_state)
        logger.info("Greeting: %s", greeting_text[:100])

        wav_pcm = await tts.synthesize_wav(greeting_text, sample_rate=config.ASTERISK_SAMPLE_RATE)
        if wav_pcm:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                greeting_wav = f.name
            write_pcm_as_wav(wav_pcm, greeting_wav, sample_rate=config.ASTERISK_SAMPLE_RATE)
            # Play: strip .wav extension for Asterisk
            agi.stream_file(greeting_wav.replace(".wav", ""))
            os.unlink(greeting_wav)

    except Exception as e:
        logger.error("Greeting failed: %s", e)
        agi.verbose("Emma greeting failed, proceeding to listen")

    # --- Step 2: Conversation loop ---
    MAX_TURNS = 20  # Safety limit to prevent infinite calls

    for turn in range(MAX_TURNS):
        # --- Record caller audio ---
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            record_path = f.name

        record_path_no_ext = record_path.replace(".wav", "")

        agi.verbose(f"Turn {turn + 1}: listening for caller...")
        agi.record_file(
            filename=record_path_no_ext,
            format="wav",
            escape_digits="#",
            timeout_ms=8000,
            silence_sec=2,
        )

        # --- Transcribe with Google STT ---
        user_text = ""
        try:
            raw_wav = record_path

            if not os.path.exists(raw_wav):
                logger.warning("No audio recorded for turn %d", turn + 1)
                continue

            pcm_bytes = read_wav_as_pcm(raw_wav, target_sample_rate=16000)
            os.unlink(raw_wav)

            # Use GoogleSTT in single-utterance mode for file-based transcription
            transcript_result: list[str] = []

            async def on_utterance(text: str):
                transcript_result.append(text)

            stt = GoogleSTT(
                on_utterance_end=on_utterance,
                sample_rate=16000,
                language_code=config.GOOGLE_TTS_LANGUAGE_CODE,
                single_utterance=True,
            )
            await stt.start()

            # Feed audio in chunks (simulate streaming from file)
            CHUNK_SIZE = 3200  # 100ms at 16kHz
            for i in range(0, len(pcm_bytes), CHUNK_SIZE):
                chunk = pcm_bytes[i : i + CHUNK_SIZE]
                await stt.send_audio(chunk)
                await asyncio.sleep(0.05)

            # Wait briefly for the result then close
            await asyncio.sleep(0.5)
            await stt.close()

            if transcript_result:
                user_text = transcript_result[-1]

        except Exception as e:
            logger.error("STT error on turn %d: %s", turn + 1, e)

        if not user_text.strip():
            agi.verbose("Could not understand caller, asking to repeat")
            user_text = ""  # Let AI handle silence / reprompt

        logger.info("Turn %d — Caller said: %s", turn + 1, user_text)
        agi.verbose(f"Caller: {user_text[:80]}")

        # --- Get AI response ---
        try:
            response_text = await async_get_ai_response(
                user_text, session_state=session_state
            )
        except Exception as e:
            logger.error("AI engine error on turn %d: %s", turn + 1, e)
            response_text = "I'm sorry, I had trouble processing that. Could you please repeat?"

        logger.info("Turn %d — Emma: %s", turn + 1, response_text[:100])
        agi.verbose(f"Emma: {response_text[:80]}")

        # --- Synthesize and play response ---
        try:
            wav_pcm = await tts.synthesize_wav(response_text, sample_rate=config.ASTERISK_SAMPLE_RATE)
            if wav_pcm:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    response_wav = f.name
                write_pcm_as_wav(wav_pcm, response_wav, sample_rate=config.ASTERISK_SAMPLE_RATE)
                agi.stream_file(response_wav.replace(".wav", ""))
                os.unlink(response_wav)
        except Exception as e:
            logger.error("TTS playback error on turn %d: %s", turn + 1, e)

        # --- Check if conversation is done ---
        if session_state.closed_conversation:
            logger.info("Conversation closed by Emma after %d turns", turn + 1)
            break

    await tts.close()
    agi.verbose("Emma AGI completed")
    logger.info("Call ended, caller=%s", caller_id)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agi = AGI()
    agi.answer()

    try:
        asyncio.run(run_conversation(agi))
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error("Fatal AGI error: %s", e, exc_info=True)
    finally:
        agi.hangup()
