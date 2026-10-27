import os
from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()

# Clinic Information. One deployment per clinic: set these in .env for another clinic.
CLINIC_NAME = os.getenv("CLINIC_NAME", "Pearl Dental Clinic").strip()          # written: dashboard, calendars, SMS
CLINIC_SHORT_NAME = os.getenv("CLINIC_SHORT_NAME", "Pearl Dental").strip()     # what Emma says on the phone
CLINIC_CITY = os.getenv("CLINIC_CITY", "Bengaluru").strip()
CLINIC_TIMEZONE = os.getenv("CLINIC_TIMEZONE", "Asia/Kolkata")

# Agent persona / greeting (docs/NORTH_STAR.md, decisions R1 and R2).
# A short natural greeting, rotated; no disclaimers. Emma never volunteers that
# she is automated, and never claims to be human: if a caller sincerely asks,
# she says HONEST_LINE and carries straight on helping.
# Every greeting names Emma and the clinic and asks one question (owner, 6 Oct:
# "it should always mention its name Emma"); at most 12 words (R1). An open offer of help, as
# real front-desk scripts do (owner, 25 Oct): "Would you like to book?" had cancelling callers
# start with "No...".
GREETINGS = [
    f"Hi, this is Emma at {CLINIC_SHORT_NAME}. How can I help?",
    f"{CLINIC_SHORT_NAME}, Emma speaking. How can I help?",
    f"Hi, this is Emma at {CLINIC_SHORT_NAME}. How may I help you?",
    f"{CLINIC_SHORT_NAME}, this is Emma. How can I help you?",
]
# Said instead when it suits the clinic clock (4 am-12, before 4 pm, after 4 pm;
# none between midnight and 4 am, phrases.next_greeting).
TIME_GREETINGS = {
    "morning": f"Good morning, this is Emma at {CLINIC_SHORT_NAME}. How can I help?",
    "afternoon": f"Good afternoon, Emma at {CLINIC_SHORT_NAME} here. How can I help?",
    "evening": f"Good evening, this is Emma at {CLINIC_SHORT_NAME}. How can I help?",
}
# Owner, 12 Oct: warm, slightly playful, never sounding as if she was hiding it (was "Yeah, you caught me").
HONEST_LINE = "Good question! I'm Emma, the clinic's virtual receptionist."

# Browser demo realism (R1, R5, R6). No background bed: only an occasional door,
# chair or footsteps while Emma's line is active, and typing (static/ambience.js).
PHONE_LINE_EFFECT = os.getenv("PHONE_LINE_EFFECT", "true").lower() == "true"
AMBIENCE_ENABLED = os.getenv("AMBIENCE_ENABLED", "true").lower() == "true"
AMBIENCE_EVENT_DB = float(os.getenv("AMBIENCE_EVENT_DB", "-34"))    # door / chair / footsteps peak
TYPING_SFX = os.getenv("TYPING_SFX", "true").lower() == "true"
# "Type instead" on the talk page and the patient phone (a call typed instead of spoken). Off
# everywhere (owner, 27 Oct): callers talk to Emma. TYPED_CALLS=true brings the button back.
TYPED_CALLS = os.getenv("TYPED_CALLS", "false").lower() == "true"
# After the caller gives something to write down (name, number, date...), Emma
# types for a moment before answering, as a receptionist would. Min/max ms.
# The beat overlaps the engine's work: the reply waits max(engine time, beat),
# so an LLM turn that already took longer gets no extra pause (walkie-talkie fix).
TYPING_BEAT_MS = (int(os.getenv("TYPING_BEAT_MIN_MS", "150")), int(os.getenv("TYPING_BEAT_MAX_MS", "300")))
# A soft breath before sentences at least this long (words); 0 turns it off.
BREATH_BEFORE_WORDS = int(os.getenv("BREATH_BEFORE_WORDS", "20"))

# Clinic Working Hours
CLOSED_DAYS = [6]                  # Sunday (6)
CLINIC_START_HOUR = 7              # 7:00 AM
CLINIC_END_HOUR = 21               # 9:00 PM
LUNCH_START_HOUR = 14              # 2:00 PM
LUNCH_START_MIN = 0
LUNCH_END_HOUR = 14                # 2:30 PM
LUNCH_END_MIN = 30

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

# Sarvam (an Indian provider) as the second model for the turn's one request (llm.SarvamNLU).
# A different company from Gemini, so it isn't overloaded at the same moments (17 Oct: Gemini
# and its own fallback model both answered 503). With reasoning off, sarvam-105b-conversations
# answered a short structured request in ~0.36 s against Gemini Flash-Lite's ~1.25 s (19 Oct).
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "")
SARVAM_MODEL = os.getenv("SARVAM_MODEL", "sarvam-105b-conversations")
SARVAM_URL = os.getenv("SARVAM_URL", "https://api.sarvam.ai/v1/chat/completions")
# NVIDIA's hosted models (build.nvidia.com), OpenAI-shaped: NLU_PRIMARY=nvidia. Free for development.
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "minimaxai/minimax-m3.1-flash")
NVIDIA_URL = os.getenv("NVIDIA_URL", "https://integrate.api.nvidia.com/v1/chat/completions")
# Cerebras's hosted models (cloud.cerebras.ai), OpenAI-shaped: NLU_PRIMARY=cerebras. A free trial tier.
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "")
CEREBRAS_MODEL = os.getenv("CEREBRAS_MODEL", "gpt-oss-120b")
CEREBRAS_URL = os.getenv("CEREBRAS_URL", "https://api.cerebras.ai/v1/chat/completions")
# Which model takes the turn first: "gemini" (default), "sarvam", "nvidia" or "cerebras". Gemini, when it
# has a key, is the turn's one retry (only before any token arrived; llm.py budget rules).
NLU_PRIMARY = os.getenv("NLU_PRIMARY", "gemini").strip().lower()
# Sarvam as Gemini's retry only when asked for ("sarvam"). Off by default: on the 53 scenarios
# (19 Oct) Sarvam with Gemini's brief re-asked answered questions 38/405 times and looped in 6/52
# calls, while the no-model fallback re-asked 3/387 and never looped, so a Gemini failure is
# better caught by the fallback until the brief is tuned for Sarvam.
NLU_BACKUP = os.getenv("NLU_BACKUP", "").strip().lower()

# Tier-0: resolve short, unambiguous turns (yes/no, digits, a service, a date)
# deterministically without an LLM round trip.
TIER0_ENABLED = os.getenv("TIER0_ENABLED", "true").lower() == "true"

# Deepgram STT — used by the real-time pipeline (call_session.py)
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
DEEPGRAM_MODEL = os.getenv("DEEPGRAM_MODEL", "nova-3")
DEEPGRAM_LANGUAGE = os.getenv("DEEPGRAM_LANGUAGE", "en-IN")
# Raw silence before Deepgram ends an utterance. turn_detector.py adds its own
# wait from the words (complete answers commit at once, "and..." waits), so 200
# and 400 both work; 300 is the recommended balance (fewer mid-number splits
# than 200, 100 ms quicker on "yes" than 400).
DEEPGRAM_ENDPOINTING_MS = int(os.getenv("DEEPGRAM_ENDPOINTING_MS", "300"))
DEEPGRAM_UTTERANCE_END_MS = int(os.getenv("DEEPGRAM_UTTERANCE_END_MS", "1000"))  # Deepgram minimum
DEEPGRAM_KEYTERMS = [
    t.strip() for t in os.getenv(
        "DEEPGRAM_KEYTERMS",
        f"{CLINIC_SHORT_NAME},Nagarbhavi,Indiranagar,Jayanagar,Whitefield,root canal,"
        "Invisalign,braces,extraction,consultation,check-up,cleaning,filling",
    ).split(",") if t.strip()
]
# Common Indian first names, added after the clinic's own words so they never push those out
# (8 Oct: "Adharsh" heard as "Adesh"). Deepgram allows 100 keyterms; set it empty to turn off.
DEEPGRAM_NAME_KEYTERMS = [
    t.strip() for t in os.getenv(
        "DEEPGRAM_NAME_KEYTERMS",
        "Adharsh,Aarav,Aditya,Arjun,Bharat,Deepak,Karthik,Kiran,Pankaj,Rahul,Rohan,Sandeep,Vihaan,Vikram,"
        "Ananya,Anika,Diya,Ishita,Kavya,Lakshmi,Meera,Neha,Pooja,Priya,Saanvi,Sneha,Sriranjani",
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

# Emma's voice: "elevenlabs" (default) or "sarvam" (owner, 20 Oct: Sarvam's Bulbul v4 Flash replaces
# ElevenLabs; the brain and the listening stay). With "sarvam", ElevenLabs (when it has a key) takes a
# sentence Sarvam refuses, then the offline Piper voice; cached sentences are rendered in Sarvam's voice.
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "elevenlabs").strip().lower()
SARVAM_TTS_SPEAKER = os.getenv("SARVAM_TTS_SPEAKER", "ishita_en_customer").strip()
SARVAM_TTS_MODEL = os.getenv("SARVAM_TTS_MODEL", "bulbul:v4-flash").strip()
SARVAM_TTS_PACE = float(os.getenv("SARVAM_TTS_PACE", "1.0"))

# Turn-taking / latency tuning
FILLER_AFTER_MS = int(os.getenv("FILLER_AFTER_MS", "450"))
BARGE_IN_ENABLED = os.getenv("BARGE_IN_ENABLED", "true").lower() == "true"
BARGE_IN_MIN_WORDS = int(os.getenv("BARGE_IN_MIN_WORDS", "2"))

# Server settings. Loopback by default: the voice socket spends API credit and
# (from Day 4) the dashboard shows patient data, so nothing is exposed to the
# network unless SERVER_HOST is set deliberately, behind TLS and a login.
SERVER_HOST = os.getenv("SERVER_HOST", "127.0.0.1")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8000"))
# Calls Emma takes at once (owner, 11 Oct: 3). Each uses its own speech-to-text and voice streams,
# so check the provider plans' concurrency limits. Recovery calls never take the last free line.
MAX_CONCURRENT_CALLS = int(os.getenv("MAX_CONCURRENT_CALLS", "3"))
# Extra browser origins allowed to open /ws/voice, comma-separated
# (e.g. "https://emma.example.org"). The page's own origin is always allowed.
# The browser talk page (/ws/voice) starts a real call that spends API credit. True for development;
# deploy/install.sh sets false, so only a logged-in staff member can use it (THREAT_MODEL T5).
TALK_PAGE_PUBLIC = os.getenv("TALK_PAGE_PUBLIC", "true").lower() == "true"
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
# Upcoming appointments per patient (name + number) and per number (a family shares one; 11 Oct audit:
# a family of four, or a root canal's sittings, hit the old limit of 3 per number).
# Minutes between one patient's appointments at two different branches (to get from one to the other).
TRAVEL_BUFFER_MIN = int(os.getenv("TRAVEL_BUFFER_MIN", "90"))
MAX_FUTURE_APPOINTMENTS_PER_PATIENT = int(os.getenv("MAX_FUTURE_APPOINTMENTS_PER_PATIENT", "3"))
MAX_FUTURE_APPOINTMENTS_PER_PHONE = int(os.getenv("MAX_FUTURE_APPOINTMENTS_PER_PHONE", "8"))
# Doctor-unavailability recovery calls (outbound.py, plan 5.11): clinic-local
# hours Emma may call patients, and how long the patient's phone rings.
RECOVERY_CALL_WINDOW = os.getenv("RECOVERY_CALL_WINDOW", "09:00-20:00")
RECOVERY_RING_TIMEOUT_S = float(os.getenv("RECOVERY_RING_TIMEOUT_S", "30"))
# Unanswered recovery calls are tried again this many times in all, this far apart
# (inside the calling window and the patient's own hours). A declined call isn't
# retried: the front desk follows it up.
RECOVERY_MAX_ATTEMPTS = max(1, int(os.getenv("RECOVERY_MAX_ATTEMPTS", "3")))
RECOVERY_RETRY_GAP_MIN = max(5, int(os.getenv("RECOVERY_RETRY_GAP_MIN", "120")))
# Branch used by the current single-branch dialogue until the Day 2 workflows land.
DEFAULT_BRANCH = os.getenv("DEFAULT_BRANCH", "Nagarbhavi")
CACHE_DIR = os.getenv("EMMA_CACHE_DIR", os.path.join(BASE_DIR, "cache"))
LOG_DIR = os.getenv("EMMA_LOG_DIR", os.path.join(BASE_DIR, "logs"))
CAPTURE_DIR = os.getenv("EMMA_CAPTURE_DIR", os.path.join(BASE_DIR, "captures"))

# Versioned clinic knowledge. Only entries marked "verified": true are ever
# spoken; anything else is escalated to staff rather than guessed.
CLINIC_FACTS_PATH = os.path.join(BASE_DIR, "clinic_facts.json")

# ---- Dashboard, transcripts and Calendar sync (Day 4, plan 5.8-5.10)
# Staff dashboard login. The value is a scrypt hash printed by
# `python tools/hash_password.py`; with no valid hash the dashboard stays locked
# (never open) and /health says why.
DASHBOARD_PASSWORD_HASH = os.getenv("DASHBOARD_PASSWORD_HASH", "").strip()
# Signs the session cookie. Empty = a random secret per process, so every
# restart logs staff out (safe, just less convenient).
DASHBOARD_SESSION_SECRET = os.getenv("DASHBOARD_SESSION_SECRET", "").strip()
DASHBOARD_SESSION_HOURS = float(os.getenv("DASHBOARD_SESSION_HOURS", "12"))
# Set true once the dashboard is served over HTTPS (the cookie is then Secure).
DASHBOARD_COOKIE_SECURE = os.getenv("DASHBOARD_COOKIE_SECURE", "false").lower() == "true"
# Failed logins allowed per client within the window before a lockout of the same length.
DASHBOARD_LOGIN_MAX_FAILURES = int(os.getenv("DASHBOARD_LOGIN_MAX_FAILURES", "5"))
DASHBOARD_LOGIN_WINDOW_S = int(os.getenv("DASHBOARD_LOGIN_WINDOW_S", "300"))
# The demo login (on the owner's resume) may start this many talk-page calls a day, all demo
# logins together, so a shared link can't spend the month's API credit. 0 = no limit.
DEMO_DAILY_CALL_LIMIT = int(os.getenv("DEMO_DAILY_CALL_LIMIT", "20"))

# Call transcripts (no audio is recorded, decision R3): text older than this is
# blanked by a purge that runs at startup and every TRANSCRIPT_PURGE_HOURS.
TRANSCRIPT_RETENTION_DAYS = int(os.getenv("TRANSCRIPT_RETENTION_DAYS", "30"))
# The rest of the personal data (privacy.py): idempotency records, sent message text, recovery snapshots,
# and (after longer) the before/after copies in the audit log.
PERSONAL_DATA_RETENTION_DAYS = int(os.getenv("PERSONAL_DATA_RETENTION_DAYS", "30"))
AUDIT_DETAIL_RETENTION_DAYS = int(os.getenv("AUDIT_DETAIL_RETENTION_DAYS", "180"))

# Cost per call (usage.py; dashboard "How Emma is doing"). Estimates in US dollars: set your plans' rates.
# Defaults: Deepgram Nova-3 streaming pay-as-you-go; ElevenLabs Flash v2.5 at 0.5 credit a character on
# the Creator plan; Gemini Flash-Lite list prices per million tokens.
COST_DEEPGRAM_USD_PER_MIN = float(os.getenv("COST_DEEPGRAM_USD_PER_MIN", "0.0077"))
COST_ELEVENLABS_USD_PER_1K_CHARS = float(os.getenv("COST_ELEVENLABS_USD_PER_1K_CHARS", "0.11"))
COST_GEMINI_USD_PER_M_IN = float(os.getenv("COST_GEMINI_USD_PER_M_IN", "0.10"))
COST_GEMINI_USD_PER_M_OUT = float(os.getenv("COST_GEMINI_USD_PER_M_OUT", "0.40"))
# Sarvam (at about ₹88 to the dollar): Gemma 4 at ₹35 / ₹90 per million tokens in / out; the Bulbul voice
# at about ₹2 per 1,000 characters (the 215-line cache, 6,698 characters, cost about ₹13).
COST_SARVAM_USD_PER_M_IN = float(os.getenv("COST_SARVAM_USD_PER_M_IN", "0.40"))
COST_SARVAM_USD_PER_M_OUT = float(os.getenv("COST_SARVAM_USD_PER_M_OUT", "1.02"))
COST_SARVAM_TTS_USD_PER_1K_CHARS = float(os.getenv("COST_SARVAM_TTS_USD_PER_1K_CHARS", "0.022"))
TRANSCRIPT_PURGE_HOURS = float(os.getenv("TRANSCRIPT_PURGE_HOURS", "6"))

# Google Calendar one-way mirror (calendar_sync.py). A service account owns one
# DEMO calendar per branch (tools/setup_calendars.py). Without the key file or
# the Google client library the worker stays off and /health says so.
CALENDAR_SYNC_ENABLED = os.getenv("CALENDAR_SYNC_ENABLED", "true").lower() == "true"
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_FILE", os.path.join(BASE_DIR, "secrets", "google-service-account.json"))
# The demo Gmail the branch calendars are shared with (read-only).
CALENDAR_SHARE_WITH = os.getenv("CALENDAR_SHARE_WITH", "").strip()
# How often the worker looks for due outbox rows when nothing wakes it (seconds).
CALENDAR_POLL_S = float(os.getenv("CALENDAR_POLL_S", "5"))

# Messages to patients (notify.py, docs/NOTIFICATIONS.md): confirmations and a reminder.
NOTIFY_ENABLED = os.getenv("NOTIFY_ENABLED", "true").lower() == "true"
NOTIFY_SENDER = os.getenv("NOTIFY_SENDER", "log").strip().lower()      # log | twilio
NOTIFY_CHANNEL = os.getenv("NOTIFY_CHANNEL", "whatsapp").strip().lower()  # whatsapp | sms
REMINDER_HOURS_BEFORE = float(os.getenv("REMINDER_HOURS_BEFORE", "24"))
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_FROM = os.getenv("TWILIO_FROM", "").strip()                     # +1415... or the WhatsApp sender number

# The conversation engine is R2 (dialogue/, docs/R2_DESIGN.md), the only one
# since the 12-step machine was retired on 12 Oct 2026; an old R2_ENGINE line in
# .env is ignored.
# R2: how long the streamed model reply may take to deliver its understanding
# (the "head") before the turn falls back to Tier-0 lenient (nlu.HEAD_DEADLINE_S).
NLU_HEAD_DEADLINE_S = float(os.getenv("NLU_HEAD_DEADLINE_S", "1.6"))
# Keep Gemini's streaming connection warm: one realistic request at startup and
# another this often while no call is on (0 = off). A cold first request took
# about 2.2 s and missed the head deadline; a warm one about 1.3 s.
NLU_KEEP_WARM_S = float(os.getenv("NLU_KEEP_WARM_S", "240"))
# The model's head start when the no-model reading already understands the turn
# clearly (a name, a number, a yes, a plain request); NLU_HEAD_DEADLINE_S is for the rest.
NLU_FAST_DEADLINE_S = float(os.getenv("NLU_FAST_DEADLINE_S", "0.8"))
# Caller words in the console log ("caller: ...", listen-only "heard ..."). Off by default;
# LOG_CALLER_TEXT=true only while developing, never with real patients (plan 5.8:
# transcripts never go to log files; they live in the database for 30 days).
LOG_CALLER_TEXT = os.getenv("LOG_CALLER_TEXT", "false").lower() == "true"     # off by default since 11 Oct

# Backup voice (plan 5.7): Piper runs locally (models/piper/<name>.onnx) and
# takes over when ElevenLabs fails or returns no audio (its quota ran out on
# 1 Oct and live sentences went silent). Empty turns the backup off.
PIPER_VOICE = os.getenv("PIPER_VOICE", "en_GB-cori-medium").strip()

# Phone calls through Asterisk (docs/TELEPHONY.md, plan section 13, phase D).
# Off by default: the browser talk page needs none of it. Asterisk (WSL2 Ubuntu)
# runs AudioSocket() to AUDIOSOCKET_HOST:AUDIOSOCKET_PORT, which stays on loopback.
TELEPHONY_ENABLED = os.getenv("TELEPHONY_ENABLED", "false").lower() == "true"
# Phone playout (audiosocket.py): hold this much of a live line before it starts, and refill this much if
# the stream runs dry mid-line (bursty mobile data). Cached lines arrive whole and don't wait.
# 26 Oct: 250 ms still ran dry about once a call on the hotspot (a break mid-sentence the caller hears);
# 400 ms costs a live line 0.15 s more before it starts.
PHONE_PREBUFFER_MS = int(os.getenv("PHONE_PREBUFFER_MS", "400"))
# Feed the speech recogniser silence in the caller's gaps: softphones send nothing while the caller is
# quiet, so Deepgram couldn't hear the end of a turn (25 Oct phone calls: 2.5-4.5 s to answer).
PHONE_FILL_SILENCE = os.getenv("PHONE_FILL_SILENCE", "true").lower() == "true"
PHONE_REBUFFER_MS = int(os.getenv("PHONE_REBUFFER_MS", "250"))
AUDIOSOCKET_HOST = os.getenv("AUDIOSOCKET_HOST", "127.0.0.1")
AUDIOSOCKET_PORT = int(os.getenv("AUDIOSOCKET_PORT", "9092"))
# Shared with the Asterisk dialplan (/telephony/register and /telephony/next); empty refuses them.
TELEPHONY_SECRET = os.getenv("TELEPHONY_SECRET", "").strip()
# The dialplan puts a caller through to this when Emma promises a transfer; empty
# keeps "the clinic will call you back" (the line Emma says depends on it).
TELEPHONY_FRONT_DESK = os.getenv("TELEPHONY_FRONT_DESK", "PJSIP/1002").strip()
# Keypad digits are sent as one caller turn after "#" or this much quiet.
DTMF_TIMEOUT_S = float(os.getenv("DTMF_TIMEOUT_S", "3"))
# Recovery calls ring a SIP phone through the Asterisk Manager Interface
# (Originate) as well as the /patient page; empty AMI_USER keeps them browser-only.
AMI_HOST = os.getenv("AMI_HOST", "127.0.0.1")
AMI_PORT = int(os.getenv("AMI_PORT", "5038"))
AMI_USER = os.getenv("AMI_USER", "").strip()
AMI_SECRET = os.getenv("AMI_SECRET", "").strip()
# The demo patient's phone (a softphone extension) that recovery calls ring.
TELEPHONY_PATIENT_PHONE = os.getenv("TELEPHONY_PATIENT_PHONE", "PJSIP/1001").strip()
# With a real SIP trunk (docs/TRUNK.md): how to dial a patient, {number} = their number as the trunk wants it
# ({e164} with +, {national} 10 digits, {zero} with a leading 0). Empty: recovery calls ring
# TELEPHONY_PATIENT_PHONE (the lab softphone).
TELEPHONY_TRUNK_DIAL = os.getenv("TELEPHONY_TRUNK_DIAL", "").strip()
# The number recovery calls show as their caller ID.
TELEPHONY_CLINIC_CID = os.getenv("TELEPHONY_CLINIC_CID", f'"{CLINIC_SHORT_NAME}" <08041234567>').strip()

# Log rotation (plan section 13, phase E). logs/turns.jsonl (per-turn timings)
# rolls over at LOG_ROTATE_MB, keeping LOG_KEEP old files. LOG_FILE (empty: off)
# adds a daily app log, phone numbers masked (logredact), kept LOG_KEEP days;
# on Linux under systemd the console log already goes to the journal.
LOG_ROTATE_MB = float(os.getenv("LOG_ROTATE_MB", "5"))
LOG_KEEP = int(os.getenv("LOG_KEEP", "14"))
LOG_FILE = os.getenv("LOG_FILE", "").strip()
