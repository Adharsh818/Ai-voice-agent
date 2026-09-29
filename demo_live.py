"""
Interactive browser demo for the Emma dialogue state machine.

Drives the real `ai_engine` code offline — `_basic_entity_fallback` stands in for
Gemini, so no API key or GCP credentials are needed. Every reply you see comes
from `_handle_conversation_step`, the same function the WebSocket server and the
Asterisk AGI call.

The `mode` switch is the point of the demo:

  fixed   — the code as it is now.
  legacy  — the confirmation parser and name screen as they were BEFORE the
            Phase 3 fixes, monkeypatched over the same state machine. This is a
            verbatim reconstruction of the two substring-matching YES/NO lists
            that checked "yes" before "no", which is what let a rejected recap
            book an appointment.

Run it with:  ./.venv/Scripts/python.exe demo_live.py
Then open http://127.0.0.1:8100
"""

import asyncio
import contextlib
import os
import subprocess
import sys
import tempfile
import threading
import uuid

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai_engine
import backend_actions
import config

HERE = os.path.dirname(os.path.abspath(__file__))
config.USE_MOCK_APIS = True

# ---------------------------------------------------------------------------
# Verbatim reconstruction of the pre-fix logic, for the A/B switch.
# ---------------------------------------------------------------------------

_LEGACY_YES = [
    "yes", "yeah", "yep", "yup", "yea", "sure", "correct", "right",
    "that's right", "thats right", "affirmative", "ok", "okay", "exactly",
    "absolutely", "definitely", "of course", "certainly", "that's correct",
    "thats correct", "confirmed", "go ahead", "proceed", "that is correct",
    "that is right", "sounds good", "looks good", "perfect", "great", "fine",
    "work", "works", "do that", "please do", "cool", "alright", "all right",
]
_LEGACY_NO = [
    "no", "nope", "nah", "wrong", "incorrect", "not right", "not correct",
    "that's wrong", "thats wrong", "that's incorrect", "thats incorrect",
    "change it", "different", "mistake", "error", "don't", "dont", "cancel",
]


def _legacy_parse_confirmation(user_text):
    """The old logic: unanchored substring match, yes tested before no."""
    text = (user_text or "").lower().strip()
    if any(w in text for w in _LEGACY_YES):
        return "yes"
    if any(w in text for w in _LEGACY_NO):
        return "no"
    return None


def _legacy_looks_like_name(text, strict=False):
    """The old logic: any utterance of two or more characters is a name."""
    return len((text or "").strip()) >= 2


_FIXED = (ai_engine._parse_confirmation, ai_engine._looks_like_name)
_LEGACY = (_legacy_parse_confirmation, _legacy_looks_like_name)


@contextlib.contextmanager
def engine_mode(mode, db_path):
    """Swap in one parser pair and one mock database for the duration of a turn."""
    parse, looks = _LEGACY if mode == "legacy" else _FIXED
    old_parse = ai_engine._parse_confirmation
    old_looks = ai_engine._looks_like_name
    old_db = config.MOCK_DB_PATH
    ai_engine._parse_confirmation = parse
    ai_engine._looks_like_name = looks
    config.MOCK_DB_PATH = db_path
    try:
        yield
    finally:
        ai_engine._parse_confirmation = old_parse
        ai_engine._looks_like_name = old_looks
        config.MOCK_DB_PATH = old_db


async def _offline_nlu(text, s=None):
    """Stand in for Gemini. Honours whichever parser `engine_mode` installed."""
    return ai_engine._basic_entity_fallback(text)


# `config.MOCK_DB_PATH` and the patched module attributes are process-global, so
# one turn at a time. A demo has one user; correctness beats concurrency here.
_LOCK = threading.Lock()
_TMP = tempfile.mkdtemp(prefix="emma-demo-")


class Session:
    def __init__(self, mode="fixed"):
        self.mode = mode
        self.state = ai_engine.SessionState()
        self.db = os.path.join(_TMP, f"{uuid.uuid4().hex}.json")
        self.turns = []


SESSIONS: dict[str, Session] = {}


def snapshot(s):
    """Everything the right-hand inspector renders."""
    return {
        "step": s.step,
        "purpose": s.purpose,
        "slots": [
            {"label": "Name", "step": 2, "temp": s.temp_name,
             "value": s.name, "confirmed": s.name_confirmed},
            {"label": "Phone", "step": 4, "temp": s.temp_phone,
             "value": s.phone, "confirmed": s.phone_confirmed},
            {"label": "Service", "step": 5, "temp": s.temp_service,
             "value": s.service, "confirmed": s.service_confirmed},
            {"label": "Location", "step": 6, "temp": "",
             "value": "Nagarbhavi" if s.location_confirmed else "",
             "confirmed": s.location_confirmed},
            {"label": "Date", "step": 7, "temp": s.temp_date,
             "value": s.date_str, "confirmed": s.date_confirmed},
            {"label": "Time", "step": 8, "temp": s.temp_time,
             "value": s.time_str, "confirmed": s.time_confirmed},
        ],
        "recap_confirmed": s.recap_confirmed,
        "booking_confirmed": s.booking_confirmed,
        "closed": s.closed_conversation,
        "alternatives": list(s.alternative_slots) if s.offering_alternatives else [],
        "name_capture_attempts": s.name_capture_attempts,
    }


def _run_turn(sess, text):
    """One caller turn through the real async entry point."""
    with _LOCK, engine_mode(sess.mode, sess.db):
        parsed = ai_engine._parse_confirmation(text) if text else None
        original = ai_engine.async_extract_entities_with_llm
        ai_engine.async_extract_entities_with_llm = _offline_nlu
        try:
            reply = asyncio.run(ai_engine.async_get_ai_response(text, sess.state))
        finally:
            ai_engine.async_extract_entities_with_llm = original
    return reply, parsed


app = FastAPI(title="Emma dialogue demo")
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "static", "demo.html"))


class ResetIn(BaseModel):
    mode: str = "fixed"


@app.post("/api/reset")
def reset(body: ResetIn):
    sid = uuid.uuid4().hex
    sess = Session(mode="legacy" if body.mode == "legacy" else "fixed")
    SESSIONS[sid] = sess
    reply, _ = _run_turn(sess, "")
    sess.turns.append({"who": "emma", "text": reply})
    return {"sid": sid, "mode": sess.mode, "reply": reply, "state": snapshot(sess.state)}


class SayIn(BaseModel):
    sid: str
    text: str


@app.post("/api/say")
def say(body: SayIn):
    sess = SESSIONS.get(body.sid)
    if sess is None:
        return JSONResponse({"error": "unknown session — press Restart"}, status_code=404)
    before = snapshot(sess.state)
    reply, parsed = _run_turn(sess, body.text)
    after = snapshot(sess.state)
    sess.turns.append({"who": "caller", "text": body.text})
    sess.turns.append({"who": "emma", "text": reply})
    return {
        "reply": reply,
        "state": after,
        "parsed_confirmation": parsed,
        "just_booked": after["booking_confirmed"] and not before["booking_confirmed"],
        "step_before": before["step"],
    }


# ---------------------------------------------------------------------------
# Scripted A/B scenarios: the same caller, both parsers, side by side.
# ---------------------------------------------------------------------------

_TO_RECAP = [
    "yes", "my name is Adharsh", "yes", "I need to book a root canal",
    "7899377462", "yes", "root canal", "yes", "yes",
    "next Monday", "yes", "5 pm", "yes",
]

SCENARIOS = {
    "rejected_recap": {
        "title": "Caller rejects the final recap",
        "blurb": "Emma has read the recap back. The caller says the details are wrong. "
                 "Nothing may be booked without an explicit yes.",
        "prelude": _TO_RECAP,
        "attack": "No, that is not right",
        "expect": "Emma asks what to correct. No booking.",
        "watch": "booking_confirmed",
    },
    "declines_call": {
        "title": "Caller declines at the greeting",
        "blurb": "The first utterance contains the word \"book\", but it is a refusal. "
                 "A keyword must not override an explicit no.",
        "prelude": [],
        "attack": "No, I don't want to book anything",
        "expect": "Emma offers to call back later.",
        "watch": "closed",
    },
    "no_problem": {
        "title": "\"No problem\" is agreement",
        "blurb": "Emma asks whether the Nagarbhavi clinic is acceptable. "
                 "\"No problem\" starts with a negative word but means yes.",
        "prelude": ["yes", "my name is Adharsh", "yes", "I need a root canal",
                    "7899377462", "yes", "root canal", "yes"],
        "attack": "no problem",
        "expect": "Emma accepts and moves on to the date.",
        "watch": "step",
    },
    "name_capture": {
        "title": "A date is not a name",
        "blurb": "Emma asked for the caller's name and got a date and time instead.",
        "prelude": ["yes"],
        "attack": "next Monday at 5 pm",
        "expect": "Emma asks for the name again.",
        "watch": "name",
    },
    "refuses_alternative": {
        "title": "Caller refuses the offered slot",
        "blurb": "That Monday is almost fully booked, so Emma can offer only one "
                 "alternative. \"None of those work\" is a refusal, not an acceptance.",
        "prelude": _TO_RECAP + ["yes, that is all correct"],
        "attack": "None of those work",
        "expect": "Emma re-offers instead of booking the refused slot.",
        "watch": "booking_confirmed",
        # Fill the requested 5 PM slot and every nearby slot except 5:30 PM, so
        # `check_availability` returns a single alternative. That is the case the
        # bug needed: with one slot on the table, step 10 books it on a "yes".
        "busy_day": "05:00 PM",
        "leave_free": "05:30 PM",
    },
}


def _fill_the_day(mode, sess, wanted, leave_free):
    """Book `wanted` and all of its near neighbours except `leave_free`."""
    import datetime

    today = datetime.date.today()
    monday = today + datetime.timedelta(days=(0 - today.weekday()) % 7 or 7)
    date_str = monday.strftime("%Y-%m-%d")
    dt = datetime.datetime.strptime(f"{date_str} {wanted}", "%Y-%m-%d %I:%M %p")
    slots = [wanted] + [
        c.strftime("%I:%M %p") for c in backend_actions._candidate_alt_slots(dt)
    ]
    with _LOCK, engine_mode(mode, sess.db):
        for slot in slots:
            if slot != leave_free:
                backend_actions.book_appointment(
                    f"Existing Patient {slot}", "9876543210", "Consultation",
                    date_str, slot,
                )


def _play(mode, spec):
    """Run one scenario in one mode and return the transcript plus final state."""
    sess = Session(mode=mode)
    if spec.get("busy_day"):
        _fill_the_day(mode, sess, spec["busy_day"], spec["leave_free"])
    transcript = []
    greeting, _ = _run_turn(sess, "")
    transcript.append({"who": "emma", "text": greeting})
    for turn in spec["prelude"]:
        reply, _ = _run_turn(sess, turn)
        transcript.append({"who": "caller", "text": turn})
        transcript.append({"who": "emma", "text": reply})
    prelude_len = len(transcript)
    reply, parsed = _run_turn(sess, spec["attack"])
    transcript.append({"who": "caller", "text": spec["attack"], "attack": True})
    transcript.append({"who": "emma", "text": reply, "attack": True})
    return {
        "mode": mode,
        "transcript": transcript,
        "prelude_len": prelude_len,
        "parsed_confirmation": parsed,
        "state": snapshot(sess.state),
    }


@app.get("/api/scenarios")
def scenarios():
    return [
        {"key": k, "title": v["title"], "blurb": v["blurb"],
         "attack": v["attack"], "expect": v["expect"], "watch": v["watch"]}
        for k, v in SCENARIOS.items()
    ]


@app.post("/api/scenario/{key}")
def scenario(key: str):
    spec = SCENARIOS.get(key)
    if spec is None:
        return JSONResponse({"error": "unknown scenario"}, status_code=404)
    return {
        "key": key, "title": spec["title"], "blurb": spec["blurb"],
        "attack": spec["attack"], "expect": spec["expect"], "watch": spec["watch"],
        "fixed": _play("fixed", spec),
        "legacy": _play("legacy", spec),
    }


@app.get("/api/tests")
def tests():
    """Run the real regression suite and stream back its output."""
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=HERE, capture_output=True, text=True, timeout=180,
    )
    out = (proc.stdout + proc.stderr).replace("\r\n", "\n")
    lines = [l for l in out.split("\n") if "FutureWarning" not in l]
    return {"ok": proc.returncode == 0, "output": "\n".join(lines).strip()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8100, log_level="warning")
