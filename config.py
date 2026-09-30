import os
from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()

# Clinic Information
CLINIC_NAME = "Pearl Dental Clinic"
CLINIC_LOCATION = "Nagarbhavi"
CLINIC_TIMEZONE = os.getenv("CLINIC_TIMEZONE", "Asia/Kolkata")

# Agent persona / greeting (docs/NORTH_STAR.md, decisions R1 and R2).
# A short natural greeting, rotated; no disclaimers. Emma never volunteers that
# she is automated, and never claims to be human: if a caller sincerely asks,
# she says HONEST_LINE and carries straight on helping.
GREETINGS = [
    "Hi, this is Emma at Pearl Dental, how can I help?",
    "Hello, Pearl Dental. How can I help you?",
    "Pearl Dental, Emma here. Go ahead.",
    "Hi, I'm Emma from Pearl Dental. How can I help you?",
]
HONEST_LINE = "Yeah, you caught me, I'm the clinic's virtual receptionist."

# Browser demo realism (R1, R5, R6). No background bed: only an occasional door,
# chair or footsteps while Emma's line is active, and typing (static/ambience.js).
PHONE_LINE_EFFECT = os.getenv("PHONE_LINE_EFFECT", "true").lower() == "true"
AMBIENCE_ENABLED = os.getenv("AMBIENCE_ENABLED", "true").lower() == "true"
AMBIENCE_EVENT_DB = float(os.getenv("AMBIENCE_EVENT_DB", "-34"))    # door / chair / footsteps peak
TYPING_SFX = os.getenv("TYPING_SFX", "true").lower() == "true"
# After the caller gives something to write down (name, number, date...), Emma
# types for a moment before answering, as a receptionist would. Min/max ms.
TYPING_BEAT_MS = (int(os.getenv("TYPING_BEAT_MIN_MS", "650")), int(os.getenv("TYPING_BEAT_MAX_MS", "1000")))
# A soft breath before sentences at least this long (words); 0 turns it off.
BREATH_BEFORE_WORDS = int(os.getenv("BREATH_BEFORE_WORDS", "20"))

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

# Google Calendar is a one-way mirror of SQLite, written by a background worker
# with a service account (Day 4). Nothing reads it during a call, and Google
# Sheets is not used (a CSV export replaces it).

# LLM Config — Gemini
# One key only. Rotating several free-tier keys to stretch quota is not allowed
# by the provider's terms; quota pressure is handled by Tier-0 and the breaker.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# While the startup model check is failing, re-check this often (seconds), so a
# transient quota or network blip at startup does not disable Gemini for good.
GEMINI_REVERIFY_S = float(os.getenv("GEMINI_REVERIFY_S", "60"))

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

# Server settings. Loopback by default: the voice socket spends API credit and
# (from Day 4) the dashboard shows patient data, so nothing is exposed to the
# network unless SERVER_HOST is set deliberately, behind TLS and a login.
SERVER_HOST = os.getenv("SERVER_HOST", "127.0.0.1")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8000"))
# Extra browser origins allowed to open /ws/voice, comma-separated
# (e.g. "https://emma.example.org"). The page's own origin is always allowed.
ALLOWED_ORIGINS = [o.strip().rstrip("/") for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]

# Development only: save each caller's raw audio (16 kHz mono WAV) plus the
# utterances Deepgram heard, for the STT comparison and the replay harness.
# Off by default — these files contain people's voices.
DEV_CAPTURE_AUDIO = os.getenv("DEV_CAPTURE_AUDIO", "false").lower() == "true"

# Runtime directories (git-ignored)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Appointments store: SQLite in WAL mode, the single source of truth.
DB_PATH = os.getenv("EMMA_DB_PATH", os.path.join(BASE_DIR, "data", "emma.db"))
# Seed the DEMO clinic (4 branches, 8 doctors, sample appointments) into an empty database.
DEMO_SEED_ON_EMPTY = os.getenv("DEMO_SEED_ON_EMPTY", "true").lower() == "true"

# Scheduling policy (decisions Q4, Q5, Q9, Q14)
SLOT_GRID_MIN = 30                      # appointments start on :00 and :30
BOOKING_HORIZON_DAYS = int(os.getenv("BOOKING_HORIZON_DAYS", "60"))
BOOKING_LEAD_MIN = int(os.getenv("BOOKING_LEAD_MIN", "120"))      # earliest start = now + 2 h
EMERGENCY_LEAD_MIN = int(os.getenv("EMERGENCY_LEAD_MIN", "30"))   # urgent same-day slots
HOLD_TTL_S = int(os.getenv("HOLD_TTL_S", "300"))                  # offered slots are held 5 min
MAX_FUTURE_APPOINTMENTS_PER_PHONE = int(os.getenv("MAX_FUTURE_APPOINTMENTS_PER_PHONE", "3"))
# Branch used by the current single-branch dialogue until the Day 2 workflows land.
DEFAULT_BRANCH = os.getenv("DEFAULT_BRANCH", "Nagarbhavi")
CACHE_DIR = os.getenv("EMMA_CACHE_DIR", os.path.join(BASE_DIR, "cache"))
LOG_DIR = os.getenv("EMMA_LOG_DIR", os.path.join(BASE_DIR, "logs"))
CAPTURE_DIR = os.getenv("EMMA_CAPTURE_DIR", os.path.join(BASE_DIR, "captures"))

# Versioned clinic knowledge. Only entries marked "verified": true are ever
# spoken; anything else is escalated to staff rather than guessed.
CLINIC_FACTS_PATH = os.path.join(BASE_DIR, "clinic_facts.json")
