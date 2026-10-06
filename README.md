# Emma — Pearl Dental voice receptionist

Emma answers calls for a four-branch dental clinic in Bengaluru. She talks in real time, books appointments and answers basic clinic questions. Every appointment decision is made by Python, never by the language model. The browser talk page is the supported way to call her today; phone calls through Asterisk come after the 8 October 2026 demo.

- **Speech-to-text:** Deepgram Nova-3 (streaming)
- **Language understanding:** Gemini Flash-Lite, with a deterministic fast path (`tier0.py`) for short answers
- **Text-to-speech:** ElevenLabs Flash v2.5 (streaming PCM), with pre-rendered prompts
- **Server:** FastAPI + WebSocket

The build plan, decisions and edge cases are in [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md). How it fits together: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Running the demo: [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) (`tools/demo_reset.py` with Emma stopped, then `tools/preflight.py` with her running). The phone path is described in [docs/TELEPHONY.md](docs/TELEPHONY.md).

## Quick start (Windows, Python 3.12)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env      # then fill in GEMINI_API_KEY, DEEPGRAM_API_KEY, ELEVENLABS_API_KEY
.\.venv\Scripts\python.exe tools\fetch_ambience.py   # the approved clinic sounds (CC0), about 11 MB
.\.venv\Scripts\python.exe server.py
```

Open http://localhost:8000, click the circle, allow the microphone and talk. Use a headset: speakers leak Emma's voice back into the microphone.

Without keys the server still starts, and `/health` reports what is missing. For testing without a microphone, open the browser console during a call and run `emma.say("I'd like to book a cleaning")`.

## Useful endpoints

| Path | What |
|---|---|
| `/` | Talk page (one call at a time: a second tab gets a "busy" answer) |
| `/dashboard` | Staff dashboard (login required, see below) |
| `/health` | API keys present, Gemini model check, prompt cache, call slot, Calendar sync, dashboard login, transcript retention |
| `/metrics` | Perceived latency p50/p95 by tier, and where end-of-speech time goes |

## Staff dashboard

Every page is labelled DEMO: the clinic, doctors and bookings are fictitious.

- **Live call:** captions as they happen, listening/thinking/speaking state, the conversation goal, tier and per-turn timings.
- **Appointments:** a day, upcoming, past or all; filter by branch, doctor or status, group by branch or doctor, search by name, phone or ID. Cancel, move and book by hand (through the same scheduling rules as Emma). CSV export (each export is recorded in the audit log).
- **Tasks:** every callback Emma promises, emergencies first, with a done toggle.
- **Calls:** history with transcripts (no audio is ever recorded) and a "Delete call data" button (audited).
- **System:** the Google Calendar outbox with failed rows and Retry, provider health, the do-not-call list and the audit log.

The dashboard stays locked until a password hash is set. Nothing is open without it, and `/health` says why it's locked.

```powershell
.\.venv\Scripts\python.exe tools\hash_password.py
```

It asks for a password (10+ characters) twice and prints two lines, `DASHBOARD_PASSWORD_HASH=...` and `DASHBOARD_SESSION_SECRET=...`. Paste both into `.env`, restart the server and open http://localhost:8000/dashboard. The password itself is never stored. Logins use an HttpOnly, SameSite=Strict cookie (12 hours by default), and five wrong passwords in five minutes lock that client out for five minutes.

## Google Calendar (one-way mirror)

SQLite is the source of truth. Each branch has a DEMO Google calendar that mirrors its appointments: bookings appear, moves follow, cancellations disappear, and flagged ones get a "NEEDS RESCHEDULE" prefix. Events carry only the service, doctor, branch, the patient's first name, the last 4 digits of the phone and the appointment ID. Emma never reads the calendar during a call, so a Google outage can't block a booking; failed writes retry (5 s, 30 s, 2 min, 10 min, then hourly) and after 12 attempts show on the System page with a Retry button.

One-time setup:

1. In a Google Cloud project, enable the **Google Calendar API**.
2. Create a **service account** (no roles needed), add a JSON key, and save it as `secrets\google-service-account.json` (git-ignored).
3. Put the demo Gmail in `.env` as `CALENDAR_SHARE_WITH=...`, then run:

   ```powershell
   .\.venv\Scripts\python.exe tools\setup_calendars.py
   ```

   It creates "Pearl Dental — <branch> (DEMO)" for each branch, owned by the service account, shares each one read-only with that Gmail, stores the IDs in the database and queues existing bookings for sync. It's safe to run again (`--dry-run` shows what it would do).
4. In the Gmail, accept the four sharing emails (or open the links the tool prints).
5. Restart the server. `/health` → `calendar.enabled` should be `true`.

Without the key file the server runs normally with the mirror off, and `/health` says why.

## Demo data

To start again from a fresh DEMO clinic (this deletes `data/emma.db`):

```powershell
.\.venv\Scripts\python.exe seed_demo.py --reset
```

Scheduling rules (hours, lunch, durations, 60-day horizon, 2-hour notice, doctor rotas, closures, holds) live in `scheduling.py`. Date and time phrases are parsed in `dateparse.py`.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Every external service is faked in the tests (Google Calendar included), so they need no keys or network.

## Runtime folders (all git-ignored)

| Folder | Contents |
|---|---|
| `data/emma.db` | Appointments (SQLite, WAL). Created on first start and filled with the DEMO clinic: 4 branches, 8 fictional doctors, sample bookings |
| `cache/` | Pre-rendered prompt audio, keyed by voice and settings |
| `logs/turns.jsonl` | Per-turn latency records (no caller text) |
| `captures/` | Caller audio + STT utterances when `DEV_CAPTURE_AUDIO=true` (development only) |
| `secrets/` | The Google service-account key (`google-service-account.json`) |

## Privacy

- **Honesty:** Emma never claims to be human. She doesn't volunteer that she's automated, but if a caller sincerely asks, she says so in one natural line and carries on helping.
- **No audio recording.** Call transcripts (text only) are kept in the database for the dashboard and are never written to log files. Text older than `TRANSCRIPT_RETENTION_DAYS` (30) is blanked at startup and every 6 hours. A caller who asks not to be kept has their transcript blanked when the call ends, and staff can delete one call's data on request. Appointment records are business records and stay.
- Phone numbers are masked in logs, and Calendar events show only the last 4 digits.
- Use paid or no-training provider tiers before handling any real patient data.
