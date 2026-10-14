"""
The conversation engine's facade (docs/R2_DESIGN.md, section 15).

The call session, the harness and the tools use only these names; the turn
itself runs on the R2 engine (dialogue/), or on dialogue.recovery for an
outbound recovery call:

    new_session(call_id)                    a fresh dialogue CallContext for one call
    phone_line(s, caller_id, can_transfer)  a phone call: the caller ID stands in for asking
    async_process_turn(text, s, progress, on_sentence) -> TurnResult
    listening_hint(s)                       what Emma is listening for (the turn detector)
    expects_information(s, text)            the typing beat (decision R6)
    install_test_nlu(reader)                tests and harness: a scripted model

The 12-step state machine that lived here until 12 Oct 2026 was retired; R2
had run every real call since 5 Oct.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

import phones
from dialogue.context import CallContext

logger = logging.getLogger(__name__)


@dataclass
class TurnResult:
    text: str
    tier: int                  # 0 = deterministic fast path, 1 = model, 2 = no-model fallback,
                               # -1 = no understanding needed (greeting)
    entities: dict = field(default_factory=dict)
    nlu_ms: float = 0.0
    step_before: int | str = 0     # the goal values (same as goal_before / goal_after)
    step_after: int | str = 0
    action: str | None = None      # "booked" | "rescheduled" | "cancelled", only after Python committed it
    goal_before: str | None = None
    goal_after: str | None = None
    spoken_count: int = 0          # leading sentences of text already delivered through on_sentence


async def async_process_turn(user_text, s, progress=None, on_sentence=None) -> TurnResult:
    """
    Process one caller turn ("" on the first turn = the greeting) and return
    Emma's reply with timing metadata.

    `progress(event)` lets the real-time layer react without polling:
      "llm_start"     Tier-0 declined; a model round trip is starting
      "before_action" the caller just confirmed; a booking is about to run
      "commit"        the engine is about to change the call's state; from here
                      the turn can no longer be cancelled and restarted

    `on_sentence` receives each validated reply sentence as soon as it is
    final, so the call session can start speaking before the turn ends.
    """
    if s is None:
        raise ValueError("async_process_turn requires a per-call context (new_session)")
    if getattr(s, "is_recovery", False):
        # An outbound recovery call (dialogue.recovery).
        return await _recovery_turn(user_text, s, progress)
    if not isinstance(s, CallContext):
        raise TypeError(f"async_process_turn needs a dialogue CallContext, not {type(s).__name__}")
    from dialogue import engine
    out = await engine.process_turn(s, user_text or "", progress=progress, on_sentence=on_sentence)
    return TurnResult(out.text, tier=out.tier, entities=out.entities, nlu_ms=out.nlu_ms,
                      step_before=out.goal_before or 0, step_after=out.goal_after or 0,
                      action=out.action, goal_before=out.goal_before, goal_after=out.goal_after,
                      spoken_count=out.spoken_count)


def new_session(call_id: str | None = None) -> CallContext:
    """A fresh per-call context."""
    from dialogue.context import new_context
    return new_context(call_id)


def phone_line(s, caller_id: str | None = None, can_transfer: bool = False) -> bool:
    """
    A call that came in over the phone (audiosocket.py). A caller ID that is a
    real phone number stands in for asking: Emma confirms it with "Is the
    number you're calling from the best one to reach you on?" instead of
    asking for digits. can_transfer lets a callback become a live transfer to
    the front desk. Returns whether the caller ID was taken.
    """
    if not isinstance(s, CallContext):
        return False
    from dialogue.context import FieldState
    s.can_transfer = bool(can_transfer)
    e164 = phones.to_e164(caller_id) if caller_id else None
    s.line_e164 = e164
    if not e164 or s.caller.phone_state != FieldState.EMPTY:
        return False
    s.caller.phone_e164, s.caller.phone_state, s.caller.phone_source = e164, FieldState.PENDING, "caller_id"
    return True


async def _recovery_turn(user_text, s, progress) -> TurnResult:
    """One turn of a recovery call. Its database work runs in a thread; progress events hop back to the loop."""
    import db
    from dialogue import recovery
    loop = asyncio.get_running_loop()

    def emit(event, **data):
        if progress is not None:
            loop.call_soon_threadsafe(lambda: progress(event, **data))

    state_before = s.state
    started = time.perf_counter()
    reply = await asyncio.to_thread(recovery.process_turn, s, user_text or "", db.get_db().run_sync, emit)
    return TurnResult(reply.text, tier=0 if user_text else -1, nlu_ms=(time.perf_counter() - started) * 1000,
                      step_before=state_before, step_after=s.state, action=reply.action,
                      goal_before=state_before, goal_after=s.state)


def listening_hint(s):
    """
    What Emma is listening for: {"expect": "phone|name|yes_no|date|time|choice|open|spelling",
    "digits_so_far": int}. A partial phone number gives expect="phone" with the
    digits buffered so far, so the turn detector waits for the rest. None for
    anything that is not a call context.
    """
    if getattr(s, "is_recovery", False):
        from dialogue import recovery
        return recovery.listening_hint(s)
    if isinstance(s, CallContext):
        from dialogue import policy
        return policy.listening_hint(s)
    return None


def expects_information(s, text: str) -> bool:
    """
    True when the caller has just given Emma something a receptionist would
    write down (a name, number, service, date, a first description of what
    they need, a correction at the summary): the call session then plays a
    short burst of typing before Emma answers (docs/NORTH_STAR.md, decision R6).
    """
    if getattr(s, "is_recovery", False):
        return False                      # Emma searches with "let me have a look" instead
    if isinstance(s, CallContext):
        from dialogue import policy
        return policy.expects_information(s, text)
    return False


def install_test_nlu(reader):
    """
    Tests and harness only: run the model call on reader(text, expect) ->
    reading dict instead of Gemini (nlu.ReaderBackend). Returns a context
    manager; a reader returning None simulates the model being down.
    """
    import nlu
    return nlu.use_backend(nlu.ReaderBackend(reader))
