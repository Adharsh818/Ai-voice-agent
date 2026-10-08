"""
A minimal Asterisk Manager Interface client: just enough to place a call
(Originate) and learn whether it was answered (docs/TELEPHONY.md).

Recovery calls use it to ring the patient's SIP phone; when the phone is
answered, Asterisk runs the [emma-outbound] dialplan, which connects the call
to Emma with AudioSocket using the UUID passed here. Each call opens its own
manager connection (login, Originate, wait, logoff), so there is no
long-lived state to go stale between campaigns.

    result = await originate(channel="PJSIP/1001", context="emma-outbound", exten="s",
                             variables={"EMMA_UUID": key}, timeout_s=28)
    result.answered / result.declined / result.reason

The manager port listens on loopback only (manager.conf); the user and
secret come from .env (AMI_USER, AMI_SECRET).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Optional

import config

logger = logging.getLogger("ami")

# OriginateResponse "Reason" codes (Asterisk's AST_CONTROL_* values).
REASON_NO_ANSWER = {"0", "1", "3"}       # failed / hung up before answer / rang out
REASON_ANSWERED = "4"
REASON_DECLINED = {"5", "8"}             # busy (a softphone's Reject sends 486) / congestion (603 Decline)


class AMIError(Exception):
    pass


@dataclass
class OriginateResult:
    answered: bool
    declined: bool
    reason: str                          # Asterisk's reason code, or why the request failed


def _message(fields: dict) -> bytes:
    return "".join(f"{k}: {v}\r\n" for k, v in fields.items()).encode() + b"\r\n"


async def _read_message(reader: asyncio.StreamReader) -> dict:
    """One manager message (Key: Value lines up to a blank line)."""
    out: dict = {}
    while True:
        line = await reader.readline()
        if not line:
            raise AMIError("manager connection closed")
        text = line.decode("utf-8", "replace").rstrip("\r\n")
        if not text:
            if out:
                return out
            continue
        key, _, value = text.partition(":")
        out.setdefault(key.strip(), value.strip())


async def _wait_for(reader: asyncio.StreamReader, action_id: str, *, event: Optional[str] = None) -> dict:
    while True:
        msg = await _read_message(reader)
        if msg.get("ActionID") != action_id:
            continue
        if event is None and "Response" in msg:
            return msg
        if event is not None and msg.get("Event") == event:
            return msg


async def originate(*, channel: str, context: str, exten: str, variables: Optional[dict] = None,
                    timeout_s: float = 28, caller_id: Optional[str] = None,
                    host: Optional[str] = None, port: Optional[int] = None,
                    user: Optional[str] = None, secret: Optional[str] = None) -> OriginateResult:
    """Ring `channel`; when answered, Asterisk runs context/exten. Waits for the answer or the failure."""
    host, port = host or config.AMI_HOST, port or config.AMI_PORT
    user, secret = user or config.AMI_USER, secret or config.AMI_SECRET
    if not user:
        raise AMIError("AMI_USER is not set")
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5)
    try:
        await asyncio.wait_for(reader.readline(), timeout=5)            # "Asterisk Call Manager/x.y"
        login_id = uuid.uuid4().hex
        writer.write(_message({"Action": "Login", "Username": user, "Secret": secret,
                               "Events": "call", "ActionID": login_id}))
        await writer.drain()
        reply = await asyncio.wait_for(_wait_for(reader, login_id), timeout=5)
        if reply.get("Response") != "Success":
            raise AMIError(f"login refused: {reply.get('Message', '')}")
        action_id = uuid.uuid4().hex
        fields = {"Action": "Originate", "Channel": channel, "Context": context, "Exten": exten,
                  "Priority": "1", "Timeout": str(int(timeout_s * 1000)), "Async": "true",
                  "ActionID": action_id}
        if caller_id:
            fields["CallerID"] = caller_id
        lines = _message(fields)[:-2]                                      # Variable may repeat
        for key, value in (variables or {}).items():
            lines += f"Variable: {key}={value}\r\n".encode()
        writer.write(lines + b"\r\n")
        await writer.drain()
        queued = await asyncio.wait_for(_wait_for(reader, action_id), timeout=5)
        if queued.get("Response") != "Success":
            return OriginateResult(False, False, f"refused: {queued.get('Message', '')}")
        done = await asyncio.wait_for(_wait_for(reader, action_id, event="OriginateResponse"),
                                      timeout=timeout_s + 10)
        reason = done.get("Reason", "")
        answered = done.get("Response") == "Success" or reason == REASON_ANSWERED
        return OriginateResult(answered, not answered and reason in REASON_DECLINED, reason)
    finally:
        try:
            writer.write(_message({"Action": "Logoff"}))
            await writer.drain()
        except Exception:
            pass
        writer.close()
