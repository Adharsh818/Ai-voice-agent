"""
Emma's lines on the dashboard's Live tab, in step with what the caller hears.

The session has a reply's text before its audio has played: a phone line plays
Emma's voice out in real time behind a buffer, and a long reply is spoken
sentence by sentence. Sent straight away, her line showed up seconds before the
caller heard it ("Let me check" still playing while the answer was on screen),
and each sentence of a reply arrived as another copy of the reply so far
(owner, 11 Oct).

The pacer holds each piece of a turn with its offset into that turn's audio and
publishes it once playback reaches it: playback start (the transport's
"started" report) plus the offset. When the caller interrupts, only the pieces
they heard are published; when the turn ends, anything still held goes out.
Every piece carries the turn's text so far and its turn number, so the
dashboard updates one line per turn instead of adding copies.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

KEEP_TURNS = 12


class CaptionPacer:
    def __init__(self, publish: Callable[[int, str], None], clock: Callable[[], float] = time.perf_counter):
        self.publish = publish                  # publish(turn, text_so_far)
        self.clock = clock
        self._pending: dict[int, list] = {}     # turn -> [(at_ms, text_so_far)], in offset order
        self._start: dict[int, float] = {}      # turn -> clock() when the caller started hearing it
        self._tasks: dict[int, asyncio.Task] = {}
        self._done: set = set()

    def add(self, turn: int, at_ms: float, text: str):
        """A piece of `turn` that starts `at_ms` into its audio; `text` is the turn's text up to here."""
        if turn in self._done or not text:
            return
        self._pending.setdefault(turn, []).append((max(0.0, at_ms), text))
        if turn in self._start:
            self._arm(turn)

    def started(self, turn: int):
        if turn in self._done or turn in self._start:
            return
        # A newer turn playing means the earlier ones are over, reported or not (a phone line
        # replaces a turn's leftover audio without a report): what was heard of them goes out first.
        for earlier in [t for t in self._pending if t < turn]:
            start = self._start.get(earlier)
            self.finish(earlier, (self.clock() - start) * 1000 if start is not None else None)
        self._start[turn] = self.clock()
        for old in [t for t in self._start if t < turn - KEEP_TURNS]:
            self._forget(old)
        self._arm(turn)

    def finish(self, turn: int, played_ms: Optional[float] = None):
        """The turn's audio ended (played_ms None: all of it) or stopped after played_ms."""
        task = self._tasks.pop(turn, None)
        if task is not None:
            task.cancel()
        heard = [text for at, text in self._pending.pop(turn, []) if played_ms is None or at <= played_ms]
        self._done.add(turn)
        if heard:
            self._emit(turn, heard[-1])

    def close(self):
        """The call ended: publish what the caller had heard by now, drop the rest."""
        for turn in list(self._pending):
            start = self._start.get(turn)
            self.finish(turn, (self.clock() - start) * 1000 if start is not None else -1)

    # ---- internals
    def _arm(self, turn: int):
        task = self._tasks.get(turn)
        if task is None or task.done():
            try:
                self._tasks[turn] = asyncio.get_running_loop().create_task(self._run(turn))
            except RuntimeError:                 # no loop (a synchronous caller): publish what's due
                self._release_due(turn)

    async def _run(self, turn: int):
        while self._pending.get(turn):
            at_ms = self._pending[turn][0][0]
            delay = self._start[turn] + at_ms / 1000 - self.clock()
            if delay > 0:
                await asyncio.sleep(delay)
            self._release_due(turn)

    def _release_due(self, turn: int):
        start = self._start.get(turn)
        if start is None:
            return
        elapsed_ms = (self.clock() - start) * 1000
        items = self._pending.get(turn, [])
        due = [text for at, text in items if at <= elapsed_ms + 1]
        if due:
            self._pending[turn] = items[len(due):]
            self._emit(turn, due[-1])

    def _emit(self, turn: int, text: str):
        try:
            self.publish(turn, text)
        except Exception as exc:
            logger.debug("caption publish failed: %s", exc)

    def _forget(self, turn: int):
        self._start.pop(turn, None)
        self._pending.pop(turn, None)
        task = self._tasks.pop(turn, None)
        if task is not None:
            task.cancel()
        self._done = {t for t in self._done if t >= turn}
