# Emma — Pearl Dental voice receptionist

Emma answers calls for a four-branch dental clinic in Bengaluru. She talks in real time, books appointments and answers basic clinic questions. Every appointment decision is made by Python, never by the language model. The browser talk page is the supported way to call her today; phone calls through Asterisk come after the 8 October 2026 demo.

- **Speech-to-text:** Deepgram Nova-3 (streaming)
- **Language understanding:** Gemini Flash-Lite, with a deterministic fast path (`tier0.py`) for short answers
- **Text-to-speech:** ElevenLabs Flash v2.5 (streaming PCM), with pre-rendered prompts
- **Server:** FastAPI + WebSocket

The build plan, decisions and edge cases are in [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md). The phone path is described in [docs/TELEPHONY.md](docs/TELEPHONY.md).

## Quick start (Windows, Python 3.12)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env      # then fill in GEMINI_API_KEY, DEEPGRAM_API_KEY, ELEVENLABS_API_KEY
.\.venv\Scripts\python.exe server.py
```

Open http://localhost:8000, click the circle, allow the microphone and talk. Use a headset: speakers leak Emma's voice back into the microphone.

Without keys the server still starts, and `/health` reports what is missing. For testing without a microphone, open the browser console during a call and run `emma.say("I'd like to book a cleaning")`.

## Useful endpoints

| Path | What |
|---|---|
| `/` | Talk page |
| `/health` | API keys present, Gemini model check, prompt cache status |
| `/metrics` | Perceived latency p50/p95 by tier, and where end-of-speech time goes |

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Runtime folders (all git-ignored)

| Folder | Contents |
|---|---|
| `cache/` | Pre-rendered prompt audio, keyed by voice and settings |
| `logs/turns.jsonl` | Per-turn latency records (no caller text) |
| `captures/` | Caller audio + STT utterances when `DEV_CAPTURE_AUDIO=true` (development only) |

## Privacy

Emma discloses that she is an automated assistant. Phone numbers are masked in logs. Use paid or no-training provider tiers before handling any real patient data.
