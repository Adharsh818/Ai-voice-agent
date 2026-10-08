"""
In-process publish/subscribe for the dashboard's live view (SSE).

Anything that happens on a call or in the back office is published here as a
small dict, and every open dashboard receives it over /dashboard/api/events:

    events.publish({"type": "caption", "call_id": "a1b2c3d4", "who": "user",
                    "text": "I'd like a cleaning", "final": False})

    async with events.subscribe() as stream:
        async for event in stream:
            ...

Rules that keep this safe for a live call:

- publish() never blocks and never raises. Each subscriber has a bounded
  queue; when a dashboard falls behind, its newest events are dropped (and
  counted) rather than slowing the call down.
- publish() may be called from any thread (the database thread creates tasks,
  the Calendar worker runs sync calls in threads): delivery hops onto each
  subscriber's own event loop.
- Every event has "type" and "call_id" (None for events not tied to a call).

Event types in use (see dashboard.js for how each is shown):

    call_started   {direction}                         (recording.CallRecorder.start)
    call_turn      {turn, role, text, meta}            (recording.CallRecorder.turn)
    call_ended     {outcome, kept}                     (recording.CallRecorder.end)
    caption        {who, text, final}                  (mirrored from the talk page protocol)
    state          {state}                             (listening | thinking)
    metrics        {turn, tier, perceived_ms, ...}     (latency.TurnTimer.to_record)
    task_created   {task_id, kind, priority}           (tasks.create_task)
    task_updated   {task_id, status}
    appointment    {appointment_id, action}            (dashboard edits)
    sync           {appointment_id, status, error}     (calendar_sync)
    call_data_deleted {}                               (recording.delete_call_data)
"""

import asyncio
import logging
from collections import deque
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)

QUEUE_SIZE = 500          # per dashboard; a live call produces a few events a second
RECENT_SIZE = 300         # recent history, for diagnostics and tests
# The call in progress is kept separately, so a dashboard opened late in a long
# call still gets all of it: interim captions (several a second while the
# caller talks) would push the start of the call out of RECENT_SIZE. Interim
# captions aren't kept here at all; the replay needs only what was settled.
CALL_REPLAY_SIZE = 2000


class _Subscriber:
    def __init__(self, loop: asyncio.AbstractEventLoop, size: int):
        self.loop = loop
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=size)
        self.dropped = 0

    def put(self, event: dict):
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped += 1


_subscribers: set = set()
_recent: deque = deque(maxlen=RECENT_SIZE)
_current: dict = {"call_id": None, "events": deque(maxlen=CALL_REPLAY_SIZE)}
_published = 0


def _remember(event: dict):
    """Keep the events of the call in progress (from call_started until call_ended)."""
    kind, call_id = event["type"], event.get("call_id")
    if kind == "call_started":
        _current["call_id"] = call_id
        _current["events"].clear()
        _current["events"].append(event)
    elif call_id is None or call_id != _current["call_id"]:
        return
    elif kind == "call_ended":
        _current["call_id"] = None
        _current["events"].clear()
    elif not (kind == "caption" and not event.get("final")):
        _current["events"].append(event)


def publish(event: dict) -> None:
    """Hand `event` to every subscriber. Non-blocking; never raises into the caller."""
    global _published
    try:
        if not isinstance(event, dict) or not event.get("type"):
            return
        event = dict(event)
        event.setdefault("call_id", None)
        _published += 1
        event["seq"] = _published          # lets a dashboard skip an event it already replayed
        _recent.append(event)
        _remember(event)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        for sub in list(_subscribers):
            if sub.loop is running:
                sub.put(event)
            else:
                try:
                    sub.loop.call_soon_threadsafe(sub.put, event)
                except RuntimeError:          # that loop has closed
                    _subscribers.discard(sub)
    except Exception as exc:                  # a dashboard problem must never reach a call
        logger.debug("event publish failed: %s", exc)


class _Stream:
    def __init__(self, sub: _Subscriber):
        self._sub = sub

    def __aiter__(self):
        return self

    async def __anext__(self) -> dict:
        return await self._sub.queue.get()

    async def get(self, timeout: Optional[float] = None) -> Optional[dict]:
        """The next event, or None after `timeout` seconds (lets SSE send heartbeats)."""
        try:
            return await asyncio.wait_for(self._sub.queue.get(), timeout)
        except asyncio.TimeoutError:
            return None

    @property
    def dropped(self) -> int:
        return self._sub.dropped


@asynccontextmanager
async def subscribe(size: int = QUEUE_SIZE) -> AsyncIterator[_Stream]:
    """Receive every event published from now on, until the block exits."""
    sub = _Subscriber(asyncio.get_running_loop(), size)
    _subscribers.add(sub)
    try:
        yield _Stream(sub)
    finally:
        _subscribers.discard(sub)
        if sub.dropped:
            logger.info("dashboard stream dropped %d events (slow subscriber)", sub.dropped)


def recent(call_id: Optional[str] = None) -> list[dict]:
    """Recently published events, oldest first; only one call's if call_id is given."""
    items = list(_recent)
    if call_id is not None:
        items = [e for e in items if e.get("call_id") == call_id]
    return items


def current_call_events() -> list[dict]:
    """The call in progress, from its start (no interim captions): what a newly opened live panel needs."""
    return list(_current["events"]) if _current["call_id"] is not None else []


def stats() -> dict:
    return {"subscribers": len(_subscribers), "published": _published}


def reset():
    """Forget subscribers and history (tests)."""
    global _published
    _subscribers.clear()
    _recent.clear()
    _current["call_id"] = None
    _current["events"].clear()
    _published = 0
