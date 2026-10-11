"""caption_pacer.py: Emma's lines reach the Live tab as the caller hears them (owner, 11 Oct)."""

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from caption_pacer import CaptionPacer  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class PacerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.out = []
        self.clock = Clock()
        self.p = CaptionPacer(lambda turn, text: self.out.append((turn, text)), clock=self.clock)

    async def test_nothing_shows_before_the_caller_hears_it(self):
        self.p.add(1, 0, "Let me just check that for you.")
        self.p.add(1, 2500, "Let me just check that for you. Monday at 10 is free.")
        await asyncio.sleep(0.01)
        self.assertEqual(self.out, [])                      # audio queued, not yet playing
        self.p.started(1)
        await asyncio.sleep(0.01)
        self.assertEqual(self.out, [(1, "Let me just check that for you.")])

    async def test_later_sentences_wait_for_their_point_in_the_audio(self):
        self.p.started(2)
        self.p.add(2, 0, "Sure.")
        self.p.add(2, 40, "Sure. Which day suits you?")
        await asyncio.sleep(0.01)
        self.assertEqual(self.out, [(2, "Sure.")])
        self.clock.now += 0.05                              # 50 ms of the turn played
        await asyncio.sleep(0.06)
        self.assertEqual(self.out[-1], (2, "Sure. Which day suits you?"))

    async def test_an_interruption_shows_only_what_was_heard(self):
        self.p.add(3, 0, "We're open till 8.")
        self.p.add(3, 1800, "We're open till 8. Would you like to book?")
        self.p.started(3)
        await asyncio.sleep(0.01)
        self.p.finish(3, played_ms=900)
        self.p.add(3, 2000, "too late")                     # nothing more for a finished turn
        self.assertEqual(self.out, [(3, "We're open till 8.")])

    async def test_the_end_of_the_turn_releases_the_rest(self):
        self.p.add(4, 0, "Okay.")
        self.p.add(4, 5000, "Okay. You're booked.")
        self.p.started(4)
        self.p.finish(4)
        await asyncio.sleep(0.01)
        self.assertEqual(self.out[-1], (4, "Okay. You're booked."))

    async def test_a_new_turn_closes_the_one_it_replaced(self):
        self.p.add(5, 0, "Our Jayanagar branch")
        self.p.add(5, 3000, "Our Jayanagar branch is on 4th Block.")
        self.p.started(5)
        await asyncio.sleep(0.01)
        self.clock.now += 1.0                               # the caller spoke; turn 6 replaced turn 5
        self.p.add(6, 0, "Sorry, go ahead.")
        self.p.started(6)
        await asyncio.sleep(0.01)
        self.assertEqual(self.out, [(5, "Our Jayanagar branch"), (6, "Sorry, go ahead.")])

    async def test_hang_up_publishes_only_what_was_heard(self):
        self.p.add(7, 0, "Thanks for calling.")
        self.p.add(7, 4000, "Thanks for calling. Take care.")
        self.p.started(7)
        await asyncio.sleep(0.01)
        self.clock.now += 1.0
        self.p.close()
        self.assertEqual(self.out, [(7, "Thanks for calling.")])


sys.path.insert(0, os.path.dirname(__file__))


class SessionTests(unittest.TestCase):
    """Through a real CallSession: the phone reports playback late, the Live tab waits for it."""

    def test_live_tab_follows_playback_not_the_reply_text(self):
        import time

        import ai_engine
        import events
        from test_realtime_support import FakeTTS, PlayingTransport, engine, make_session, patched, result, \
            wait_until

        published = []
        tts = FakeTTS(ms=300)

        async def streaming(text, s, progress=None, on_sentence=None):
            progress("llm_start")
            await on_sentence("Sure, Monday evening works.")
            return result("Sure, Monday evening works. What time suits you?", tier=1, spoken_count=1)

        async def run():
            s, t = make_session(transport=PlayingTransport(auto=False), tts=tts)
            await s._start_turn("Can I come Monday evening?", time.perf_counter())
            await wait_until(lambda: s._turn_task.done(), timeout=2)
            tid = s.turn_id
            before = list(published)                       # all audio sent, none of it heard yet
            await t.report(tid, "started")
            await asyncio.sleep(0.05)
            first = list(published)
            await asyncio.sleep(0.4)                       # past the first sentence's 300 ms
            second = list(published)
            await s.close()
            return tid, before, first, second

        def capture(event):
            if event.get("type") == "caption" and event.get("who") == "emma":
                published.append((event.get("turn"), event["text"]))

        with engine(streaming), patched(ai_engine, expects_information=lambda s, x: False), \
                patched(events, publish=capture):
            tid, before, first, second = asyncio.run(run())
        self.assertEqual(before, [])
        self.assertEqual(first, [(tid, "Sure, Monday evening works.")])
        self.assertEqual(second[-1], (tid, "Sure, Monday evening works. What time suits you?"))
        self.assertTrue(all(turn == tid for turn, _ in second))     # one line, updated in place


if __name__ == "__main__":
    unittest.main()
