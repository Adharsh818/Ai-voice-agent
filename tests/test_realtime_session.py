"""
The live call session on fakes (no network): barge-in and backchannels, the
echo filter, duplicate turns, the walkie-talkie fixes (typing beat overlap,
streamed sentences, synthesis ahead, filler/opener), the checking line, the
recap-heard rule, an outcome the caller talked over, the silence ladder, the
call-length limit, lost speech recognition and clinic keyterms.
"""

import asyncio
import sqlite3
import time
import unittest

import ai_engine
import call_session
import config
import phrases
import turn_detector
from call_session import clinic_keyterms, is_backchannel, looks_like_echo

from test_realtime_support import (FakeTTS, PlayingTransport, engine, make_session, patched, result,
                                   settle, wait_until)

ASK = "We have Monday at five or Tuesday at ten. Which suits you?"


def no_beat():
    """The caller's words need no note-taking beat (keeps timings exact)."""
    return patched(ai_engine, expects_information=lambda s, text: False)


class Recorder:
    """A fake engine: records what it was asked and answers from a function."""

    def __init__(self, reply="Sure.", **extra):
        self.calls: list[str] = []
        self.reply, self.extra = reply, extra

    async def __call__(self, text, s, progress=None):
        self.calls.append(text)
        return result(self.reply, **self.extra)


class BackchannelAndEchoRuleTests(unittest.TestCase):
    def test_listening_noises_and_acknowledgements_are_backchannels(self):
        for text in ["yeah", "Okay.", "mm-hmm", "Right.", "got it", "yes please", "Okay, no problem.",
                     "Sure, take your time.", "no worries", "thank you so much"]:
            with self.subTest(text=text):
                self.assertTrue(is_backchannel(text))
        for text in ["no", "wait", "Yes, Monday at 5.", "Can you repeat that?", "No, Tuesday.",
                     "okay so I wanted to ask", "sure but what about parking"]:
            with self.subTest(text=text):
                self.assertFalse(is_backchannel(text))

    def test_a_caller_repeating_emmas_words_is_not_an_echo(self):
        emma = "So that's Monday at 5 with Doctor Rao. Shall I book it?"
        self.assertTrue(looks_like_echo("Monday at 5 with Doctor Rao", emma))
        self.assertTrue(looks_like_echo("Shall I book it?", emma))
        for heard in ["Yes, Monday at 5.", "yes", "Monday at 6 please", "No, Doctor Iyer."]:
            with self.subTest(heard=heard):
                self.assertFalse(looks_like_echo(heard, emma))


class BargeInTests(unittest.TestCase):
    def test_backchannels_while_emma_speaks_never_interrupt_her(self):
        async def run():
            s, t = make_session(PlayingTransport(), tts=FakeTTS(ms=2000))
            s.turn_id = 1
            speaking = asyncio.ensure_future(s._speak(1, ASK, s._timer(1, "")))
            self.assertTrue(await wait_until(s._emma_audible))
            for heard in ["yeah", "okay sure", "mm hmm", "okay, no problem"]:
                await s._on_transcript(heard, False)
            await s._on_utterance_end("Yeah.", None, "speech_final")
            await settle(0.05)
            flushed_by_backchannel = list(t.flushed)
            await s._on_transcript("wait, can I", False)
            await settle()
            await speaking
            await s.close()
            return flushed_by_backchannel, t.flushed, s.turn_id

        before, after, turn_id = asyncio.run(run())
        self.assertEqual(before, [])
        self.assertEqual(after, [1])          # real words stop her
        self.assertEqual(turn_id, 1)          # and no turn was made of the "yeah"

    def test_hello_over_the_greeting_does_not_stop_it(self):
        fake = Recorder("Hi, this is Emma at Pearl Dental, how can I help?", tier=-1)

        async def run():
            s, t = make_session(PlayingTransport(), tts=FakeTTS(ms=1500))
            greeting = asyncio.ensure_future(s._greet())
            await wait_until(s._emma_audible, timeout=2)
            await s._on_transcript("Hello?", False)
            await s._on_utterance_end("Hello?", None, "speech_final")
            await settle(0.05)
            await greeting
            await s.close()
            return t.flushed, fake.calls

        with engine(fake):
            flushed, calls = asyncio.run(run())
        self.assertEqual(flushed, [])
        self.assertEqual(calls, [""])            # only the greeting itself; "Hello?" is no turn

    def test_a_yes_during_her_question_is_the_answer_once_she_finishes(self):
        fake = Recorder("Great.")

        async def run():
            s, t = make_session(PlayingTransport(), tts=FakeTTS(ms=600))
            s.turn_id = 1
            await s._speak(1, ASK, s._timer(1, ""))
            # The question starts about 70% of the way into the reply.
            await asyncio.sleep(0.5)
            await s._on_utterance_end("Yes.", None, "speech_final")
            heard_before_end = list(fake.calls)
            await wait_until(lambda: fake.calls, timeout=2)
            await s.close()
            return heard_before_end, fake.calls, t.flushed

        with engine(fake), no_beat():
            before, calls, flushed = asyncio.run(run())
        self.assertEqual(before, [])          # she was not cut off
        self.assertEqual(calls, ["Yes."])
        self.assertEqual(flushed, [])

    def test_her_own_voice_is_ignored_but_a_repeated_answer_is_heard(self):
        fake = Recorder("Lovely.")

        async def run():
            s, t = make_session(PlayingTransport(), tts=FakeTTS(ms=3000))
            s.turn_id = 1
            await s._speak(1, "So that's Monday at 5 with Doctor Rao. Shall I book it?", s._timer(1, ""))
            await wait_until(s._emma_audible)
            await s._on_utterance_end("So that's Monday at 5", None, "speech_final")
            await settle(0.05)
            echo_calls, echo_flushed = list(fake.calls), list(t.flushed)
            await s._on_utterance_end("Yes, Monday at 5.", None, "speech_final")
            await wait_until(lambda: fake.calls, timeout=2)
            await s.close()
            return echo_calls, echo_flushed, fake.calls, t.flushed

        with engine(fake), no_beat():
            echo_calls, echo_flushed, calls, flushed = asyncio.run(run())
        self.assertEqual((echo_calls, echo_flushed), ([], []))
        self.assertEqual(calls, ["Yes, Monday at 5."])
        self.assertEqual(flushed, [1])

    def test_echo_is_only_possible_while_her_voice_is_audible(self):
        fake = Recorder("Okay.")

        async def run():
            s, t = make_session(PlayingTransport(), tts=FakeTTS(ms=150))
            s.turn_id = 1
            await s._speak(1, "Monday at 5 works.", s._timer(1, ""))
            await wait_until(lambda: 1 in t.finished, timeout=2)
            await asyncio.sleep(call_session.ECHO_TAIL_S + 0.2)
            # The caller's words span the last 0.1 s of call audio (Deepgram word times).
            s.clock.add(32000)
            await s._on_utterance_end("Monday at 5 works.", 1.0, "speech_final", start_sec=0.9)
            await wait_until(lambda: fake.calls, timeout=2)
            await s.close()
            return fake.calls

        with engine(fake), no_beat():
            self.assertEqual(asyncio.run(run()), ["Monday at 5 works."])

    def test_nothing_counts_as_echo_before_her_audio_is_heard(self):
        async def run():
            s, t = make_session(PlayingTransport(auto=False), tts=FakeTTS(ms=2000))
            await t.report(0, "started")            # the page reports playback (as after the greeting)
            await t.report(0, "ended")
            s.turn_id = 1
            await s._speak(1, ASK, s._timer(1, ""))
            audible = s._emma_audible()
            await s._on_transcript("Monday at five", False)
            await s.close()
            return audible, t.flushed

        audible, flushed = asyncio.run(run())
        self.assertFalse(audible)
        self.assertEqual(flushed, [])        # no barge-in check against audio not yet playing


class DuplicateAndMergeTests(unittest.TestCase):
    def test_speech_final_then_utterance_end_for_the_same_words_is_one_turn(self):
        fake = Recorder("Lovely.")

        async def run():
            s, t = make_session()
            s.s.step = 9                            # "Shall I book it?": a yes commits at once
            await s._on_utterance_end("Yes please.", 1.4, "speech_final")
            await s._on_utterance_end("Yes please.", 1.4, "utterance_end")
            await settle(0.1)
            await s.close()
            return fake.calls

        with engine(fake), no_beat():
            self.assertEqual(asyncio.run(run()), ["Yes please."])

    def test_a_fragment_still_being_understood_merges_with_the_next_words(self):
        calls = []

        async def slow(text, s, progress=None):
            calls.append(text)
            await asyncio.sleep(0.2)            # still "nlu": nothing mutated yet
            return result("Weekday mornings are quietest.")

        async def run():
            s, t = make_session()
            now = time.perf_counter()
            await s._start_turn("What's the best", now)
            await asyncio.sleep(0.05)
            await s._start_turn("time to come in?", now)
            await wait_until(lambda: s._turn_task.done(), timeout=2)
            await s.close()
            return calls, [turn for turn in s.recorder.turns if turn[0] == "caller"]

        with engine(slow), no_beat():
            calls, recorded = asyncio.run(run())
        self.assertEqual(calls, ["What's the best", "What's the best time to come in?"])
        self.assertEqual([text for _, text, _ in recorded], ["What's the best time to come in?"])


class WalkieTalkieTests(unittest.TestCase):
    def test_typing_beat_overlaps_synthesis_and_an_instant_engine_waits_only_the_beat(self):
        tts = FakeTTS(ms=100, delay=0.15)

        async def run():
            s, t = make_session(tts=tts)
            started = time.perf_counter()
            await s._start_turn("9876543210", started)
            await wait_until(lambda: s._turn_task.done(), timeout=2)
            timer = s._timers[s.turn_id]
            await s.close()
            return started, timer

        with engine(Recorder("Thanks, got it.")), patched(ai_engine, expects_information=lambda s, x: True), \
                patched(config, TYPING_BEAT_MS=(300, 300)):
            started, timer = asyncio.run(run())
        self.assertLess(tts.opened[0][1] - started, 0.1)          # TTS started during the beat
        first_audio = timer.first_audio_sent - started
        self.assertGreaterEqual(first_audio, 0.28)
        self.assertLess(first_audio, 0.42)                         # not beat + TTS time
        self.assertGreater(timer.pause_ms, 200)

    def test_a_slow_turn_gets_no_extra_typing_pause(self):
        async def slow(text, s, progress=None):
            await asyncio.sleep(0.5)
            return result("Thanks, got it.", tier=1)

        async def run():
            s, t = make_session(tts=FakeTTS(ms=100))
            await s._start_turn("my name is Priya", time.perf_counter())
            await wait_until(lambda: s._turn_task.done(), timeout=2)
            timer = s._timers[s.turn_id]
            await s.close()
            return timer

        with engine(slow), patched(ai_engine, expects_information=lambda s, x: True), \
                patched(config, TYPING_BEAT_MS=(400, 400)):
            timer = asyncio.run(run())
        self.assertIsNone(timer.pause_ms)
        self.assertLess(timer.first_audio_sent - timer.reply_ready, 0.08)

    def test_streamed_first_sentence_is_spoken_before_the_engine_finishes_and_only_once(self):
        tts = FakeTTS(ms=100)

        async def streaming(text, s, progress=None, on_sentence=None):
            progress("llm_start")
            await asyncio.sleep(0.05)
            await on_sentence("Sure, Monday evening works.")
            await asyncio.sleep(0.3)
            return result("Sure, Monday evening works. What time suits you?", tier=1, spoken_count=1)

        async def run():
            s, t = make_session(tts=tts)
            await s._start_turn("Can I come Monday evening?", time.perf_counter())
            await wait_until(lambda: s._turn_task.done(), timeout=2)
            timer = s._timers[s.turn_id]
            await s.close()
            return timer, t.emma_captions()

        with engine(streaming), no_beat():
            timer, captions = asyncio.run(run())
        self.assertLess(timer.first_audio_sent, timer.reply_ready)
        self.assertTrue(timer.streamed)
        self.assertEqual([text for text, _ in tts.opened], ["Sure, Monday evening works.", "What time suits you?"])
        self.assertEqual(captions[-1], "Sure, Monday evening works. What time suits you?")
        self.assertFalse(timer.filler)                 # a sentence was ready: no "Okay." first

    def test_streamed_sentences_are_synthesised_ahead_of_playback(self):
        tts = FakeTTS(ms=300, delay=0.1)

        async def streaming(text, s, progress=None, on_sentence=None):
            await on_sentence("We're open till eight on weekdays.")
            await on_sentence("On Saturdays it's nine to two.")
            return result("We're open till eight on weekdays. On Saturdays it's nine to two. "
                          "Would you like to book?", spoken_count=2)

        async def run():
            s, t = make_session(tts=tts)
            await s._start_turn("What are your timings?", time.perf_counter())
            await wait_until(lambda: s._turn_task.done(), timeout=3)
            await s.close()

        with engine(streaming), no_beat():
            asyncio.run(run())
        opened = [when for _, when in tts.opened]
        self.assertEqual(len(opened), 3)
        self.assertLess(opened[1] - opened[0], 0.05)  # the second was not waiting for the first to play

    def test_no_okay_okay_after_a_filler(self):
        async def thinking(text, s, progress=None):
            progress("llm_start")
            await asyncio.sleep(0.2)
            return result("Okay, Monday at five works. Shall I book it?", tier=1)

        async def run():
            s, t = make_session(cache=phrases.FILLERS, tts=FakeTTS(ms=50))
            await s._start_turn("Is Monday at five free?", time.perf_counter())
            await wait_until(lambda: s._turn_task.done(), timeout=2)
            timer = s._timers[s.turn_id]
            await s.close()
            return timer, t.emma_captions()

        with engine(thinking), no_beat(), patched(config, FILLER_AFTER_MS=30):
            timer, captions = asyncio.run(run())
        self.assertTrue(timer.filler)
        self.assertEqual(captions[-1], "Monday at five works. Shall I book it?")


class CheckingLineTests(unittest.TestCase):
    def kinds(self, speaker, tid):
        return [(kind, text) for _, _, text, kind in speaker.timeline.get(tid, [])]

    def test_old_engine_checks_while_the_action_runs_then_answers(self):
        async def booking(text, s, progress=None):
            progress("before_action")
            progress("commit")
            await asyncio.sleep(0.05)
            return result("You're all booked for Monday at five.")

        async def run():
            s, t = make_session(cache=[phrases.CHECKING], tts=FakeTTS(ms=50), cache_ms=100)
            await s._start_turn("Yes.", time.perf_counter())
            await wait_until(lambda: s._turn_task.done(), timeout=3)
            await s.close()
            return self.kinds(s.speaker, s.turn_id)

        with engine(booking), no_beat(), patched(call_session, CHECK_PAUSE_MS=(50, 60)):
            kinds = asyncio.run(run())
        self.assertEqual([k for k, _ in kinds], ["checking", "pause", "reply"])
        self.assertEqual(kinds[0][1], phrases.CHECKING)

    def test_streamed_sentence_comes_before_the_engines_own_checking_phrase(self):
        phrase = "One moment, let me look."

        async def r2(text, s, progress=None, on_sentence=None):
            progress("commit")
            progress("before_action", phrase=phrase)
            await on_sentence("Sure, Monday at five.")
            await asyncio.sleep(0.05)
            return result("Sure, Monday at five. You're all set.", spoken_count=1, action="booked")

        async def run():
            s, t = make_session(cache=[phrase], tts=FakeTTS(ms=50), cache_ms=100)
            await s._start_turn("Yes, book it.", time.perf_counter())
            await wait_until(lambda: s._turn_task.done(), timeout=3)
            await s.close()
            return self.kinds(s.speaker, s.turn_id), s.outcome

        with engine(r2), no_beat(), patched(call_session, CHECK_PAUSE_MS=(50, 60)):
            kinds, outcome = asyncio.run(run())
        self.assertEqual(kinds, [("reply", "Sure, Monday at five."), ("checking", phrase), ("pause", ""),
                                 ("reply", "You're all set.")])
        self.assertEqual(outcome, "booked")

    def test_checking_starts_anyway_if_the_engine_is_still_busy(self):
        async def busy(text, s, progress=None, on_sentence=None):
            progress("commit")
            progress("before_action")
            await asyncio.sleep(0.4)
            return result("Monday at five is free. Shall I book it?")

        async def run():
            s, t = make_session(cache=[phrases.CHECKING], tts=FakeTTS(ms=50), cache_ms=100)
            await s._start_turn("Monday at five", time.perf_counter())
            await asyncio.sleep(0.25)
            checking_early = [k for k, _ in self.kinds(s.speaker, s.turn_id)]
            await wait_until(lambda: s._turn_task.done(), timeout=3)
            await s.close()
            return checking_early, [k for k, _ in self.kinds(s.speaker, s.turn_id)]

        with engine(busy), no_beat(), patched(call_session, CHECK_PAUSE_MS=(50, 60), CHECKING_FALLBACK_MS=100):
            early, final = asyncio.run(run())
        self.assertIn("checking", early)
        self.assertEqual(final, ["checking", "pause", "reply"])


class RecapHeardTests(unittest.TestCase):
    RECAP = "So that's a cleaning on Monday at five with Doctor Rao. Shall I book it?"

    def play(self, interrupt_after=None, ms=1000):
        async def run():
            s, t = make_session(PlayingTransport(), tts=FakeTTS(ms=ms))
            s.turn_id = 1
            await s._speak(1, self.RECAP, s._timer(1, ""))
            while_playing = s.s.last_reply_heard
            if interrupt_after is None:
                await wait_until(lambda: 1 in t.finished, timeout=3)
                await settle(0.02)
            else:
                await asyncio.sleep(interrupt_after)
                await s.interrupt("caller speech")
                await settle(0.05)
            heard = s.s.last_reply_heard
            await s.close()
            return while_playing, heard

        return asyncio.run(run())

    def test_heard_to_the_end(self):
        self.assertEqual(self.play(), (False, True))

    def test_cut_off_during_the_details_is_not_heard(self):
        self.assertEqual(self.play(interrupt_after=0.2), (False, False))

    def test_cut_off_only_in_the_closing_question_still_counts(self):
        self.assertEqual(self.play(interrupt_after=0.85), (False, True))


class OwedOutcomeTests(unittest.TestCase):
    def test_an_outcome_talked_over_is_said_before_the_next_answer(self):
        calls = []

        async def eng(text, s, progress=None):
            calls.append(text)
            if len(calls) == 1:
                progress("before_action")
                progress("commit")
                await asyncio.sleep(0.3)
                return result("You're all booked for Monday at five.", action="booked")
            return result("There's free parking at the back.")

        async def run():
            s, t = make_session(PlayingTransport(), cache=[phrases.CHECKING], tts=FakeTTS(ms=100),
                                cache_ms=1500)
            await s._start_turn("Yes.", time.perf_counter())
            await wait_until(s._emma_audible, timeout=2)          # "Let me just check that for you."
            await s.on_control({"type": "text", "text": "Do you have parking there?"})
            await wait_until(lambda: len(calls) == 2 and s._turn_task.done(), timeout=3)
            await settle(0.05)
            await s.close()
            return t.emma_captions(), s.outcome

        with engine(eng), no_beat():
            captions, outcome = asyncio.run(run())
        self.assertEqual(calls, ["Yes.", "Do you have parking there?"])
        self.assertEqual(captions[-1], "You're all booked for Monday at five. There's free parking at the back.")
        self.assertEqual(outcome, "booked")


class SilenceAndLengthTests(unittest.TestCase):
    def ladder(self, **state):
        async def run():
            s, t = make_session()
            s._last_reply = "Which day suits you?"
            for key, value in state.items():
                setattr(s, key, value)
            watcher = asyncio.ensure_future(s._watch())
            await wait_until(lambda: s.closed, timeout=4)
            watcher.cancel()
            return t.emma_captions(), s.outcome, t.closed

        with patched(call_session, SILENCE_STEP_S=0.15, WATCH_INTERVAL_S=0.02):
            return asyncio.run(run())

    def test_still_there_then_cant_hear_then_goodbye(self):
        captions, outcome, closed = self.ladder()
        self.assertEqual(len(captions), 3)
        self.assertTrue(captions[0].endswith(" Which day suits you?"))    # the question, asked again
        self.assertTrue(any(captions[0].startswith(line) for line in call_session.STILL_THERE_LINES))
        self.assertIn(captions[1], call_session.CANT_HEAR_LINES)
        self.assertIn(captions[2], call_session.SILENCE_GOODBYE_LINES)
        self.assertEqual((outcome, closed), ("silence", True))

    def test_the_ladder_pauses_while_the_caller_talks_and_hold_on_stretches_it(self):
        async def run():
            s, t = make_session()
            watcher = asyncio.ensure_future(s._watch())
            for _ in range(10):                       # caller talking for 0.5 s
                s._last_voice = time.perf_counter()
                await asyncio.sleep(0.05)
            while_talking = list(t.emma_captions())
            s._hold_on = True
            s._last_voice = None
            s._ladder_reset(time.perf_counter())
            await asyncio.sleep(0.35)
            during_hold = list(t.emma_captions())
            await wait_until(lambda: t.emma_captions(), timeout=2)
            watcher.cancel()
            await s.close()
            return while_talking, during_hold, t.emma_captions()

        with patched(call_session, SILENCE_STEP_S=0.15, HOLD_ON_STEP_S=0.6, WATCH_INTERVAL_S=0.02):
            talking, holding, after = asyncio.run(run())
        self.assertEqual((talking, holding), ([], []))
        self.assertEqual(len(after), 1)

    def test_long_call_wraps_up_then_ends_with_a_callback(self):
        tasks = []

        async def run():
            s, t = make_session()
            s.s.temp_phone = "9876543210"

            async def create_task(kind, priority, phone, note):
                tasks.append((kind, priority, phone))

            s._create_task = create_task
            s._started_at = time.perf_counter()
            watcher = asyncio.ensure_future(s._watch())
            await wait_until(lambda: s.closed, timeout=3)
            watcher.cancel()
            return t.emma_captions(), s.outcome

        with patched(call_session, WRAP_UP_AT_S=0.1, MAX_CALL_S=0.4, WATCH_INTERVAL_S=0.02, SILENCE_STEP_S=60):
            captions, outcome = asyncio.run(run())
        self.assertIn(captions[0], call_session.WRAP_UP_LINES)
        self.assertIn(captions[-1], call_session.LONG_CALL_GOODBYE_CALLBACK_LINES)
        self.assertEqual(outcome, "max_length")
        self.assertEqual(tasks, [("callback", "high", "+919876543210")])

    def test_lost_speech_recognition_ends_the_call_kindly(self):
        async def run(phone):
            s, t = make_session()
            made = []

            async def create_task(kind, priority, number, note):
                made.append(kind)

            s._create_task = create_task
            if phone:
                s.s.temp_phone = phone
            await s._on_stt_lost()
            await wait_until(lambda: s.closed, timeout=2)
            return t.emma_captions(), s.outcome, made

        captions, outcome, made = asyncio.run(run(None))
        self.assertIn(captions[-1], call_session.CANT_HEAR_GOODBYE_LINES)
        self.assertEqual((outcome, made), ("stt_failure", []))
        captions, outcome, made = asyncio.run(run("9876543210"))
        self.assertIn(captions[-1], call_session.CANT_HEAR_CALLBACK_LINES)
        self.assertEqual(made, ["callback"])

    def test_session_lines_sound_like_a_receptionist(self):
        banned = ["automated", "virtual", "system", "please hold", "connect you", "transfer",
                  "error", "beep", "technical"]
        for line in call_session.CACHEABLE_LINES:
            with self.subTest(line=line):
                self.assertFalse(any(word in line.lower() for word in banned))
                self.assertLessEqual(len(line.split()), 24)


class EngineContractTests(unittest.TestCase):
    def test_new_session_listening_hint_and_streaming_are_used_when_present(self):
        class Ctx:
            closed_conversation = False
            history = []

        def new_session(call_id=None):
            ctx = Ctx()
            ctx.call_id = call_id
            return ctx

        with patched(ai_engine, new_session=new_session,
                     listening_hint=lambda s: {"expect": "phone", "digits_so_far": 5}):
            s, t = make_session()
            self.assertEqual(s.s.call_id, "test")
            self.assertTrue(s.s.last_reply_heard)
            self.assertEqual(s._listening_hint(), {"expect": "phone", "digits_so_far": 5})

    def test_todays_engine_still_works_without_the_new_functions(self):
        s, t = make_session()
        self.assertIsInstance(s.s, ai_engine.SessionState)
        s.s.step, s.s.temp_phone = 4, ""
        self.assertEqual(s._listening_hint()["expect"], "phone")

        def broken(_s):
            raise RuntimeError("engine mid-rewrite")

        with patched(ai_engine, listening_hint=broken):
            self.assertEqual(s._listening_hint()["expect"], "phone")

        async def streaming(text, s, progress=None, on_sentence=None):
            return result("ok")

        async def plain(text, s, progress=None):
            return result("ok")

        with engine(plain):
            self.assertFalse(s._streams_sentences())
        with engine(streaming):
            self.assertTrue(s._streams_sentences())


class R2CallTests(unittest.TestCase):
    """
    A whole call on the R2 engine through the call session (Sprint 1b
    integration): the real facade on a DEMO clinic with the model down, as
    R2_ENGINE=true runs it. The facade's import-time switch is reproduced by
    patching new_session and a streaming async_process_turn.
    """

    def test_a_booking_reaches_the_transcript_with_entities_and_action(self):
        import facts
        import nlu
        from datetime import datetime
        from dialogue.context import CallContext, new_context
        from dialogue.testing import DemoClinic

        real = ai_engine.async_process_turn
        checking = []

        async def r2(text, s, progress=None, on_sentence=None):
            def spy(event, **data):
                if event == "before_action":
                    checking.append(data.get("phrase"))
                if progress is not None:
                    progress(event, **data)
            return await real(text, s, spy, on_sentence=on_sentence)

        turns = ["I'd like to book a cleaning", "My name is Priya", "9845012345", "yes", "Indiranagar",
                 "Monday morning", "the first one", "yes please"]

        async def run():
            s, t = make_session()
            self.assertIsInstance(s.s, CallContext)
            self.assertTrue(s._streams_sentences())
            await s._greet()
            hints = []
            for text in turns:
                await s._start_turn(text, time.perf_counter())
                await wait_until(lambda: s._turn_task is not None and s._turn_task.done(), timeout=5)
                await settle(0.02)
                hints.append(s._listening_hint()["expect"])
            phone = s._caller_phone()
            await s.close()
            return s, t, hints, phone

        with DemoClinic(now=datetime(2026, 10, 1, 10, 0)) as clinic:
            facts.clear_cache()
            try:
                with nlu.use_backend(nlu.FakeNLU(usable=False)), no_beat(), \
                        patched(ai_engine, new_session=lambda call_id=None: new_context(call_id)), engine(r2):
                    s, t, hints, phone = asyncio.run(run())
            finally:
                facts.clear_cache()
            rows = clinic.query("SELECT * FROM appointments WHERE caller_phone_e164 = ? AND status = 'booked'",
                                "+919845012345")
        self.assertEqual(len(rows), 1)
        self.assertEqual(s.outcome, "booked")
        self.assertEqual(phone, "+919845012345")                 # read from ctx.caller (callback tasks)
        self.assertEqual(hints[1], "phone")                       # after the name, she listens for digits
        self.assertTrue(checking and all(checking))               # the engine's varied checking line
        emma = [(text, meta) for role, text, meta in s.recorder.turns if role == "emma"]
        self.assertEqual(emma[-1][1].get("action"), "booked")
        self.assertEqual(emma[-1][1].get("goal_before"), "summary")
        self.assertTrue(any(meta.get("entities") for _text, meta in emma))
        self.assertEqual(len([e for e in emma if e[1].get("action")]), 1)


class KeytermTests(unittest.TestCase):
    def test_clinic_words_reach_the_recogniser_and_the_turn_detector(self):
        async def run():
            s, t = make_session(keyterms=lambda: ["Dr Rao", "Nagarbhavi", "check-up"])
            s.listen_only = True
            await s.start()
            terms = list(s.stt.keyterms)
            await s.close()
            return terms

        try:
            terms = asyncio.run(run())
            self.assertEqual(terms, ["Dr Rao", "Nagarbhavi", "check-up"])
            self.assertIn("rao", turn_detector._KNOWN_WORDS)
        finally:
            turn_detector._KNOWN_WORDS.difference_update({"dr", "rao", "nagarbhavi", "check-up"})

    def test_keyterms_from_the_database_skip_spelling_variants(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript("""
            CREATE TABLE doctors (spoken_name TEXT, active INTEGER);
            CREATE TABLE branches (name TEXT, active INTEGER);
            CREATE TABLE services (name TEXT, aliases_json TEXT, active INTEGER);
            INSERT INTO doctors VALUES ('Dr Rao', 1), ('Dr Old', 0);
            INSERT INTO branches VALUES ('Nagarbhavi', 1);
            INSERT INTO services VALUES ('General Check-up', '["check up", "checkup", "check-up", "general dental check up"]', 1),
                                        ('Teeth Cleaning', '["scaling"]', 1);
        """)
        terms = clinic_keyterms(conn)
        self.assertEqual(terms, ["Dr Rao", "Nagarbhavi", "General Check-up", "check up", "Teeth Cleaning",
                                 "scaling", "double", "triple"])


if __name__ == "__main__":
    unittest.main()
