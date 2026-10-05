import os
from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()

# Clinic Information
CLINIC_NAME = "Pearl Dental Clinic"
CLINIC_LOCATION = "Nagarbhavi"
CLINIC_TIMEZONE = os.getenv("CLINIC_TIMEZONE", "Asia/Kolkata")

# Agent persona / greeting
# DISCLOSE_AI: Emma states up front that she is the clinic's automated assistant.
# On by default (approved plan): naturalness comes from responsiveness and voice
# quality, never from concealment. Outbound calls must always disclose.
DISCLOSE_AI = os.getenv("DISCLOSE_AI", "true").lower() == "true"
CLINIC_GREETING = os.getenv(
    "CLINIC_GREETING", "Pearl Dental, this is Emma. How can I help you today?"
)
GREETING = (
    "Pearl Dental, this is Emma, the clinic's automated assistant. How can I help you today?"
    if DISCLOSE_AI else CLINIC_GREETING
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

# Gemini model for the real-time pipeline. Keep this a Flash-Lite model you have
# verified: server.py checks it at startup and, if the check fails, runs the call
# on the deterministic Tier-0 path and templates instead of a dead model.
# gemini-3.5-flash-lite + thinking MINIMAL measured ~0.8-1.0 s per NLU turn.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
# Hard per-turn budget for the NLU call, including one bounded retry.
# Retried once on this model when the primary is overloaded (503). Tested
# alongside the primary; leave empty to retry on the primary itself.
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-flash-lite-latest")
GEMINI_TIMEOUT = float(os.getenv("GEMINI_TIMEOUT", "2.5"))
# Thinking control: a level (MINIMAL/LOW/...), an integer token budget, or empty
# to send nothing (models differ in which form they accept).
GEMINI_THINKING = os.getenv("GEMINI_THINKING", "MINIMAL").strip()

# Tier-0: resolve short, unambiguous turns (yes/no, digits, a service, a date)
# deterministically without an LLM round trip.
TIER0_ENABLED = os.getenv("TIER0_ENABLED", "true").lower() == "true"

# Deepgram STT — used by the real-time pipeline (call_session.py)
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
DEEPGRAM_MODEL = os.getenv("DEEPGRAM_MODEL", "nova-3")
DEEPGRAM_LANGUAGE = os.getenv("DEEPGRAM_LANGUAGE", "en-IN")
DEEPGRAM_ENDPOINTING_MS = int(os.getenv("DEEPGRAM_ENDPOINTING_MS", "200"))
DEEPGRAM_UTTERANCE_END_MS = int(os.getenv("DEEPGRAM_UTTERANCE_END_MS", "1000"))  # Deepgram minimum
DEEPGRAM_KEYTERMS = [
    t.strip() for t in os.getenv(
        "DEEPGRAM_KEYTERMS",
        "Pearl Dental,Nagarbhavi,Indiranagar,Jayanagar,Whitefield,root canal,"
        "Invisalign,braces,extraction,consultation,check-up,cleaning,filling",
    ).split(",") if t.strip()
]

# ElevenLabs TTS — used by the real-time pipeline (speech.py)
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")  # "Rachel"
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5")
# Raw 16 kHz PCM: no MP3 framing or MediaSource buffering in the browser, exact
# flushes on barge-in, and the same format the telephony path will use.
ELEVENLABS_OUTPUT_FORMAT = os.getenv("ELEVENLABS_OUTPUT_FORMAT", "pcm_16000")
TTS_SAMPLE_RATE = 16000
# "ws" = multi-context WebSocket (one socket per call); "http" = streaming POST.
TTS_TRANSPORT = os.getenv("TTS_TRANSPORT", "ws").lower()

# Turn-taking / latency tuning
FILLER_AFTER_MS = int(os.getenv("FILLER_AFTER_MS", "450"))
BARGE_IN_ENABLED = os.getenv("BARGE_IN_ENABLED", "true").lower() == "true"
BARGE_IN_MIN_WORDS = int(os.getenv("BARGE_IN_MIN_WORDS", "2"))

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
MOCK_DB_PATH = os.getenv(
    "EMMA_MOCK_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "mock_db.json")
)

# Runtime directories (git-ignored)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.getenv("EMMA_CACHE_DIR", os.path.join(BASE_DIR, "cache"))
LOG_DIR = os.getenv("EMMA_LOG_DIR", os.path.join(BASE_DIR, "logs"))

# Versioned clinic knowledge. Only entries marked "verified": true are ever
# spoken; anything else is escalated to staff rather than guessed.
CLINIC_FACTS_PATH = os.path.join(BASE_DIR, "clinic_facts.json")
