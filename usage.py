"""
What each call costs (the audit, 11 Oct: "cost metering per call").

The call session starts a meter (begin); the services add to it from inside
the call's tasks (add), wherever they are, through a context variable; at
hang-up the totals are priced with the rates in config and stored on the call
(calls.usage_json, shown on the dashboard). Estimates, not invoices: Gemini
tokens are counted as characters / 4, Deepgram by call length (streaming bills
the open socket), ElevenLabs by live characters (lines played from the prompt
cache cost nothing).
"""

import logging
from contextvars import ContextVar
from typing import Optional

import config

logger = logging.getLogger(__name__)

_meter: ContextVar[Optional[dict]] = ContextVar("call_usage", default=None)

KINDS = ("gemini_requests", "gemini_in_tokens", "gemini_out_tokens", "tts_chars")


def begin(call_id: str) -> dict:
    """Start this call's meter; tasks created afterwards in this context add to it."""
    meter = {"call_id": call_id, **{k: 0 for k in KINDS}}
    _meter.set(meter)
    return meter


def current() -> Optional[dict]:
    return _meter.get()


def add(kind: str, amount: float = 1) -> None:
    """Count usage for the call in progress (nothing outside a call, e.g. pre-rendering at start-up)."""
    meter = _meter.get()
    if meter is not None and kind in meter:
        meter[kind] += amount


def tokens(text: str) -> int:
    """A rough token count for Gemini (about four characters per token)."""
    return (len(text or "") + 3) // 4


def price(meter: dict, seconds: float) -> dict:
    """Totals and an estimated cost in US dollars, for calls.usage_json."""
    minutes = max(seconds, 0) / 60
    costs = {
        "stt": minutes * config.COST_DEEPGRAM_USD_PER_MIN,
        "tts": meter.get("tts_chars", 0) / 1000 * config.COST_ELEVENLABS_USD_PER_1K_CHARS,
        "llm": (meter.get("gemini_in_tokens", 0) * config.COST_GEMINI_USD_PER_M_IN
                + meter.get("gemini_out_tokens", 0) * config.COST_GEMINI_USD_PER_M_OUT) / 1_000_000,
    }
    out = {k: int(meter.get(k, 0)) for k in KINDS}
    out.update(seconds=round(seconds, 1), cost_usd={k: round(v, 5) for k, v in costs.items()},
               total_usd=round(sum(costs.values()), 5))
    return out


def save(conn, call_id: str, summary: dict) -> None:
    """Store a call's usage (db thread). Called once, at hang-up."""
    import json
    import db
    with db.transaction(conn):
        conn.execute("UPDATE calls SET usage_json = ? WHERE id = ?", (json.dumps(summary), call_id))
