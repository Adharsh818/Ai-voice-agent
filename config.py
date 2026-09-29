import os
from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()

# Clinic Information
CLINIC_NAME = "Pearl Dental Clinic"
CLINIC_LOCATION = "Nagarbhavi"
CLINIC_TIMEZONE = os.getenv("CLINIC_TIMEZONE", "Asia/Kolkata")

# Agent persona / greeting
# DISCLOSE_AI: when True, Emma states up front that she is automated. Default off
# so she is presented as a person (the greeting no longer says "virtual assistant").
DISCLOSE_AI = os.getenv("DISCLOSE_AI", "False").lower() == "true"
CLINIC_GREETING = os.getenv(
    "CLINIC_GREETING", "Pearl Dental, this is Emma — how can I help?"
)

# Clinic Working Hours
WORKING_DAYS = [0, 1, 2, 3, 4, 5]  # Monday (0) to Saturday (5)
CLOSED_DAYS = [6]                  # Sunday (6)
CLINIC_START_HOUR = 7              # 7:00 AM
CLINIC_END_HOUR = 21               # 9:00 PM
LUNCH_START_HOUR = 14              # 2:00 PM
LUNCH_START_MIN = 0
LUNCH_END_HOUR = 14                # 2:30 PM
LUNCH_END_MIN = 30

# Services Available
ALLOWED_SERVICES = [
    "General Check-up",
    "Consultation",
    "Teeth Cleaning",
    "Tooth Filling",
    "Root Canal Treatment",
    "Tooth Extraction",
    "Braces",
    "Invisalign",
    "Pediatric Dentistry"
]

# API Configuration
USE_MOCK_APIS = os.getenv("USE_MOCK_APIS", "True").lower() == "true"
CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "token.json")
CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
SPREADSHEET_ID = os.getenv("GOOGLE_SPREADSHEET_ID", "")

# LLM Config — Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Optional comma-separated key pool for round-robin rotation across free-tier
# quota (wired up in Phase 2). A single GEMINI_API_KEY still works on its own.
GEMINI_API_KEYS = [k.strip() for k in os.getenv("GEMINI_API_KEYS", "").split(",") if k.strip()]
if GEMINI_API_KEY and GEMINI_API_KEY not in GEMINI_API_KEYS:
    GEMINI_API_KEYS.insert(0, GEMINI_API_KEY)
elif not GEMINI_API_KEY and GEMINI_API_KEYS:
    GEMINI_API_KEY = GEMINI_API_KEYS[0]

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini" if GEMINI_API_KEY else "ollama")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")

# Gemini model for the real-time pipeline (server.py / ai_engine.py).
# NOTE: gemini-2.0-flash was shut down by Google; gemini-3.1-flash-lite is a
# current GA low-latency model. Override via GEMINI_MODEL in .env if desired
# (e.g. gemini-3.5-flash-lite).
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
GEMINI_TIMEOUT = float(os.getenv("GEMINI_TIMEOUT", "8.0"))  # seconds per LLM call

# Deepgram STT — used by the web/WebSocket pipeline (server.py)
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")

# ElevenLabs TTS — used by the web/WebSocket pipeline (server.py)
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")  # "Rachel"
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5")

# Google Cloud Speech-to-Text — used by the Asterisk AGI pipeline (asterisk_agi.py)
# Set GOOGLE_APPLICATION_CREDENTIALS to path of your service account JSON.
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")

# Google Cloud Text-to-Speech voice settings — used by asterisk_agi.py
# See: https://cloud.google.com/text-to-speech/docs/voices
GOOGLE_TTS_LANGUAGE_CODE = os.getenv("GOOGLE_TTS_LANGUAGE_CODE", "en-IN")
GOOGLE_TTS_VOICE_NAME = os.getenv("GOOGLE_TTS_VOICE_NAME", "en-IN-Wavenet-D")
GOOGLE_TTS_SPEAKING_RATE = float(os.getenv("GOOGLE_TTS_SPEAKING_RATE", "1.0"))
GOOGLE_TTS_PITCH = float(os.getenv("GOOGLE_TTS_PITCH", "0.0"))

# Asterisk AGI configuration
ASTERISK_AGI_PORT = int(os.getenv("ASTERISK_AGI_PORT", "4573"))
ASTERISK_AGI_HOST = os.getenv("ASTERISK_AGI_HOST", "0.0.0.0")
ASTERISK_SAMPLE_RATE = int(os.getenv("ASTERISK_SAMPLE_RATE", "8000"))  # Asterisk default: 8000 Hz

# Server settings
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8000"))

# Local Mock Database Path
MOCK_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mock_db.json")
