"""
What one turn may touch besides the context: the database thread, the
catalog and knowledge snapshots, the progress callback and the clock.

Kept out of CallContext on purpose: none of it pickles, and none of it is
state of the call. The engine builds a Runtime per turn and hands it to
apply / workflows / globals, which never reach for module globals
themselves (tests pass their own).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

import clock

logger = logging.getLogger(__name__)


def _noop(event, **data):
    return None


@dataclass
class Runtime:
    call_id: str
    db: object                                   # db.Database (await db.run(fn, ...))
    catalog: object                              # facts.Catalog snapshot for this turn
    kb: object                                   # facts.Knowledge
    progress: Callable = _noop                   # progress(event, **data): llm_start / before_action / commit
    events: list = field(default_factory=list)   # progress events emitted this turn (tests, trace)
    # Set by apply.py, acted on by the engine right after it: apply is
    # synchronous and never touches the database, so a parked or dropped
    # draft only asks for its holds to be released.
    release_requested: bool = False
    # Goals apply.py raised this turn (DROPPED, SPELL_NAME...), which
    # policy.next_goal puts ahead of the checklist ("clarifications raised
    # this turn", docs/R2_DESIGN.md section 4).
    raised: list = field(default_factory=list)

    def now(self) -> datetime:
        return clock.now()

    def emit(self, event: str, **data) -> None:
        """Tell the call session what is happening; never raises into the turn."""
        self.events.append(event)
        try:
            self.progress(event, **data)
        except Exception:                        # a UI callback must never break a booking
            pass

    async def run(self, fn, *args, **kwargs):
        """fn(conn, *args, **kwargs) on the database thread (scheduling.py, tasks.py)."""
        return await self.db.run(fn, *args, **kwargs)

    @property
    def idem_prefix(self) -> str:
        return f"call:{self.call_id}"


def optional_module(name: str) -> Optional[object]:
    """
    Import a module another track may not have landed yet (tasks, events,
    recording). None if missing, so the engine degrades instead of crashing.
    """
    try:
        return __import__(name)
    except ImportError:
        return None


def safe_call(fn, *args, default=None, **kwargs):
    """
    fn(*args, **kwargs), or `default` if it raises. For helpers owned by
    other modules (matchers, speakable dates, the fact lookup): a bug or a
    module that has not landed yet must cost the caller a plainer sentence,
    never the call (invariant 6: no dead ends).
    """
    try:
        return fn(*args, **kwargs)
    except Exception as exc:                     # noqa: BLE001 (deliberately broad, see above)
        logger.debug("%s failed: %r", getattr(fn, "__qualname__", fn), exc)
        return default
