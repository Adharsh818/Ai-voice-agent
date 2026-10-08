"""
The R2 turn pipeline (dialogue/engine.py) through the ai_engine facade, on
the real DEMO catalog (DemoClinic) with the model faked (nlu.FakeNLU) or
down. Tier-0, apply, the workflows, policy and the pre-written lines are the
real ones, so these are whole calls: what the caller hears, what lands in
the database, and the facade contract the call session relies on
(docs/R2_DESIGN.md, sections 5, 8.1 and 15).

Covers the acceptance checks of the E1 brief: pickle round-trip, M3 loop
breaking on a 25-turn call, the steer cadence and the capability question,
cancel at the recap, on_sentence streaming with one model call per LLM
turn, a full booking with the backend unusable, and listening_hint /
expects_information.
"""

import asyncio
import pickle
import unittest
from datetime import datetime

import ai_engine
import config
import facts
import nlu
import speech
from dialogue import engine, policy
from dialogue.context import CallContext, Goal, Intent, new_context
from dialogue.testing import DemoClinic

# Thursday 1 Oct 2026, 10:00 (tomorrow is Friday the 2nd).
NOW = datetime(2026, 10, 1, 10, 0)
PHONE = "9845012345"
E164 = "+919845012345"


class Call:
    """One call through ai_engine.async_process_turn, as call_session drives it."""

    def __init__(self, call_id="r2-call"):
        self.s = new_context(call_id)
        self.events = []
        self.streamed = []
        self.results = []
        self.lines = []

    def progress(self, event, **data):
        self.events.append((event, data))

    async def _on_sentence(self, sentence):
        self.streamed.append(sentence)

    def say(self, text, heard=True):
        """One caller turn; returns the TurnResult (the reply is in .text)."""
        self.s.last_reply_heard = heard
        self.streamed = []
        result = asyncio.run(ai_engine.async_process_turn(text, self.s, self.progress,
                                                          on_sentence=self._on_sentence))
        self.results.append(result)
        self.lines.append(result.text)
        return result

    def run(self, *turns):
        for text in turns:
            self.say(text)
        return self.results[-1]


class EngineCase(unittest.TestCase):
    """Each test gets a fresh DEMO clinic, a frozen clock and the model down unless it installs a FakeNLU."""

    def setUp(self):
        self.clinic = DemoClinic(now=NOW)
        self.clinic.__enter__()
        facts.clear_cache()
        self.backend = nlu.use_backend(nlu.FakeNLU(usable=False))
        self.backend.__enter__()

    def tearDown(self):
        self.backend.__exit__(None, None, None)
        facts.clear_cache()
        self.clinic.__exit__(None, None, None)

    def model(self, script):
        """Swap in a FakeNLU for the rest of the test; returns it (its .calls count model requests)."""
        self.backend.__exit__(None, None, None)
        fake = nlu.FakeNLU(script)
        self.backend = nlu.use_backend(fake)
        self.backend.__enter__()
        return fake

    def booked_rows(self):
        return self.clinic.query("SELECT * FROM appointments WHERE caller_phone_e164 = ? AND status = 'booked'",
                                 E164)

    def to_ask_when(self, call):
        """Drive a booking on Tier-0 to the point where Emma asks when (ASK_WHEN)."""
        call.run("", "I want to book a cleaning", "My name is Priya", PHONE, "yes", "Indiranagar")
        self.assertEqual(call.s.pending, Goal.ASK_WHEN, call.lines)


class FacadeContract(EngineCase):

    def test_flag_off_keeps_the_old_engine(self):
        saved = config.R2_ENGINE
        config.R2_ENGINE = False                    # the default (.env.example); the suite also runs with it on
        try:
            self.assertIsInstance(ai_engine.new_session("x"), ai_engine.SessionState)
        finally:
            config.R2_ENGINE = saved
        self.assertIsNone(ai_engine.listening_hint(ai_engine.SessionState()))
        r = ai_engine.TurnResult("Hi.", tier=0)
        self.assertEqual((r.action, r.goal_before, r.goal_after, r.spoken_count), (None, None, None, 0))

    def test_flag_on_hands_out_a_call_context(self):
        saved = config.R2_ENGINE
        config.R2_ENGINE = True
        try:
            s = ai_engine.new_session("abc")
        finally:
            config.R2_ENGINE = saved
        self.assertIsInstance(s, CallContext)
        self.assertEqual(s.call_id, "abc")
        self.assertTrue(s.keep_transcript)          # call_session reads it at hang-up

    def test_install_test_nlu_reads_the_expect_line(self):
        heard = []

        def reader(words, expect):
            heard.append((words, expect))
            return None                             # the model is down: the fallback answers

        with ai_engine.install_test_nlu(reader):
            call = Call()
            result = call.run("", "what are your timings?")
        self.assertEqual(heard, [("what are your timings?", "open")])
        self.assertEqual(result.tier, 2)
        self.assertTrue(result.text)

    def test_greeting_then_closed(self):
        call = Call()
        greeting = call.say("")
        self.assertEqual(greeting.tier, -1)
        self.assertEqual(greeting.goal_after, Goal.GREET.value)
        self.assertTrue(call.s.greeted)
        call.say("bye")
        self.assertTrue(call.s.closed_conversation)
        after = call.say("hello?")
        self.assertEqual(after.tier, -1)
        self.assertTrue(after.text)

    def test_pickle_round_trip_mid_call(self):
        call = Call()
        call.run("", "I want to book a cleaning", "My name is Priya", "9845")
        self.assertEqual(call.s.caller.phone_buffer, "9845")
        clone = pickle.loads(pickle.dumps(call.s))
        self.assertEqual(clone, call.s)
        call.run("012345", "yes", "Indiranagar", "tomorrow morning")
        clone = pickle.loads(pickle.dumps(call.s))
        self.assertEqual(clone, call.s)             # held offers, constraints, prompt memory, trace

    def test_listening_hint_and_expects_information(self):
        call = Call()
        call.run("", "I want to book a cleaning", "My name is Priya")
        self.assertEqual(ai_engine.listening_hint(call.s)["expect"], "phone")
        call.say("9845")
        self.assertEqual(ai_engine.listening_hint(call.s), {"expect": "phone", "digits_so_far": 4})
        call.say("012345")
        self.assertEqual(ai_engine.listening_hint(call.s), {"expect": "yes_no", "digits_so_far": 0})
        self.assertFalse(ai_engine.expects_information(call.s, "yes"))
        self.assertFalse(ai_engine.expects_information(call.s, "yeah that's right"))
        call.say("yes")
        self.assertTrue(ai_engine.expects_information(call.s, "Indiranagar please"))
        self.assertFalse(ai_engine.expects_information(call.s, "where is that branch?"))

    def test_us_formatted_digits_from_deepgram(self):
        call = Call()
        call.run("", "I want to book a cleaning", "My name is Priya", "(984) 501-2345")
        self.assertEqual(call.s.caller.phone_e164, E164)
        self.assertEqual(call.s.pending, Goal.CONFIRM_PHONE)

    def test_trace_and_history(self):
        call = Call()
        call.run("", "I want to book a cleaning", "My name is Priya")
        self.assertEqual([t.turn for t in call.s.trace], [1, 2])
        self.assertEqual(call.s.trace[-1].goal_after, Goal.ASK_PHONE.value)
        self.assertEqual(call.s.history[-2], {"role": "user", "content": "My name is Priya"})
        self.assertEqual(call.s.last_emma, call.lines[-1])


class Bookings(EngineCase):

    def test_full_booking_with_the_model_down(self):
        # M10 / degradation: Tier-0, the lenient fallback and pre-written lines carry a whole booking.
        call = Call()
        self.to_ask_when(call)
        call.run("tomorrow morning")
        self.assertEqual(call.s.pending, Goal.OFFER_SLOTS, call.lines)
        call.run("the first one")
        self.assertEqual(call.s.pending, Goal.SUMMARY, call.lines)
        self.assertEqual(self.booked_rows(), [])
        result = call.say("yes")
        self.assertEqual(result.action, "booked")
        self.assertEqual(len(self.booked_rows()), 1)
        self.assertEqual(call.s.outcome, "booked")
        call.say("no thanks")
        self.assertTrue(call.s.closed_conversation)
        self.assertEqual(len(self.booked_rows()), 1)
        for a, b in zip(call.lines, call.lines[1:]):
            self.assertNotEqual(a, b)

    def test_cancel_at_the_recap_drops_and_bye_never_books(self):
        # HANDOFF section 5: cancel at recap -> loop -> "Bye" -> booked. Now: DROPPED, then a plain close (Z1, Z5).
        call = Call()
        self.to_ask_when(call)
        call.run("tomorrow morning", "the first one")
        self.assertEqual(call.s.pending, Goal.SUMMARY)
        result = call.say("no, cancel it")
        self.assertEqual(result.goal_after, Goal.DROPPED.value, call.lines)
        self.assertIsNone(result.action)
        self.assertEqual(self.booked_rows(), [])
        self.assertEqual(call.s.intent, Intent.NONE)
        self.assertIsNone(call.s.book.chosen)
        holds = self.clinic.query("SELECT * FROM holds WHERE call_id = ?", call.s.call_id) \
            if self.clinic.query("SELECT name FROM sqlite_master WHERE name = 'holds'") else []
        self.assertEqual(holds, [])
        call.say("bye")
        self.assertTrue(call.s.closed_conversation)
        self.assertEqual(self.booked_rows(), [])

    def test_cancel_intent_from_the_model_at_the_recap(self):
        call = Call()
        self.to_ask_when(call)
        call.run("tomorrow morning", "the first one")
        self.model({"actually i want to cancel my appointment": {
            "acts": ["correction"], "intent": "cancel", "next_goal": "dropped", "say": "", "ask": ""}})
        result = call.say("actually I want to cancel my appointment")
        self.assertEqual(result.goal_after, Goal.DROPPED.value, call.lines)
        self.assertEqual(self.booked_rows(), [])
        call.say("yes")                             # "did you want to cancel one you already have?"
        self.assertEqual(call.s.intent, Intent.CANCEL)
        self.assertEqual(call.s.caller.phone_e164, E164)   # carried: never asked again
        self.assertNotEqual(call.s.pending, Goal.ASK_PHONE)

    def test_summary_cut_off_is_said_again_not_booked(self):
        call = Call()
        self.to_ask_when(call)
        call.run("tomorrow morning", "the first one")
        result = call.say("yes", heard=False)       # barged in before the summary finished
        self.assertIsNone(result.action)
        self.assertEqual(self.booked_rows(), [])
        self.assertEqual(call.s.pending, Goal.SUMMARY_AGAIN, call.lines)
        self.assertEqual(call.say("yes").action, "booked")

    def test_red_flag_needs_no_model(self):
        fake = self.model({})
        call = Call()
        result = call.run("", "my face is swollen up to my eye and I can't breathe properly")
        self.assertEqual(result.goal_after, Goal.RED_FLAG.value)
        self.assertTrue(call.s.closed_conversation)
        self.assertEqual(fake.calls, [])

    def test_fragment_is_merged_with_the_next_turn(self):
        call = Call()
        call.say("")
        result = call.say("I need to book a cleaning for my")
        self.assertEqual(result.goal_after, Goal.GO_ON.value)
        self.assertEqual(call.s.fragment, "I need to book a cleaning for my")
        self.assertEqual(call.s.intent, Intent.NONE)        # never taken as an answer
        fake = self.model({"i need to book a cleaning for my son, he's eight": {
            "acts": ["info"], "intent": "book", "service": "Teeth Cleaning", "for_someone_else": True,
            "relation": "son", "age": 8, "next_goal": "ask_name", "say": "", "ask": ""}})
        call.say("son, he's eight")
        self.assertEqual(len(fake.calls), 1)                # the merged sentence went to the model whole
        self.assertEqual(call.s.fragment, "")
        self.assertEqual(call.s.history[-2]["content"], "I need to book a cleaning for my son, he's eight")
        self.assertEqual(call.s.book.service, "Teeth Cleaning")
        self.assertEqual(call.s.book.relation, "son")


class Composition(EngineCase):

    def test_the_same_notice_is_said_once(self):
        # apply.py and book.py can both refuse a branch; the caller hears it once (feedback 1).
        call = Call()
        call.run("", "I need braces", "Rahul", PHONE, "yes")
        result = call.say("Nagarbhavi")
        self.assertEqual(result.text.lower().count("nagarbhavi"), 1, result.text)

    def test_compose_order_say_notices_ask(self):
        from dialogue.context import GoalPlan, Notice
        call = Call()
        call.say("")
        plan = GoalPlan(Goal.ASK_WHEN, "ask.when")
        sentences = engine.compose(call.s, plan, [Notice("ack.name", {"name": "Priya"}, covered_by=("Priya",))],
                                   ["Thanks, Priya."], None)
        self.assertEqual(sentences[0], "Thanks, Priya.")
        self.assertEqual(len(sentences), 2)                # the name notice was covered by the say
        self.assertTrue(sentences[-1].endswith("?"))


class Streaming(EngineCase):

    def test_on_sentence_gets_the_say_prefix_and_one_call_per_llm_turn(self):
        call = Call()
        self.to_ask_when(call)
        fake = self.model({
            "is there parking there?": {
                "acts": ["question"], "intent": "none", "next_goal": "ask_when",
                "say": "Yes, there's parking near the clinic. It's easy to find.", "ask": ""},
            "does it hurt?": {
                "acts": ["question"], "intent": "none", "next_goal": "ask_when",
                "say": "A cleaning doesn't usually hurt.", "ask": ""},
        })
        llm_turns = 0
        for text in ("is there parking there?", "does it hurt?"):
            result = call.say(text)
            self.assertEqual(result.tier, 1)
            llm_turns += 1
            self.assertGreater(result.spoken_count, 0, result.text)
            self.assertEqual(call.streamed, speech.split_sentences(result.text)[:result.spoken_count])
            self.assertTrue(call.s.trace[-1].used_model_say)
        self.assertEqual(len(fake.calls), llm_turns)
        self.assertIn(("llm_start", {}), call.events)

    def test_tier0_turns_never_call_the_model(self):
        fake = self.model({})
        call = Call()
        self.to_ask_when(call)
        self.assertEqual(fake.calls, [])
        self.assertTrue(all(r.spoken_count == 0 for r in call.results))

    def test_model_ask_is_used_only_when_goals_agree(self):
        call = Call()
        self.to_ask_when(call)
        self.model({
            "how long does it take?": {
                "acts": ["question"], "intent": "none", "next_goal": "ask_when",
                "say": "A cleaning takes about half an hour.", "ask": "What day suits you best?"},
            "and is it painful?": {
                "acts": ["question"], "intent": "none", "next_goal": "ask_branch",
                "say": "It's usually quite comfortable.", "ask": "Which branch would you like?"},
        })
        first = call.say("how long does it take?")
        self.assertTrue(call.s.trace[-1].used_model_ask, first.text)
        self.assertTrue(first.text.endswith("What day suits you best?"))
        second = call.say("and is it painful?")
        self.assertFalse(call.s.trace[-1].used_model_ask)
        self.assertNotIn("branch", second.text.lower())

    def test_invented_price_is_dropped_not_spoken(self):
        call = Call()
        self.to_ask_when(call)
        self.model({"how much is it?": {
            "acts": ["question"], "intent": "none", "next_goal": "ask_when",
            "say": "A cleaning costs 99999 rupees.", "ask": ""}})
        result = call.say("how much is it?")
        self.assertNotIn("99999", result.text)
        self.assertTrue(call.s.trace[-1].dropped)
        self.assertTrue(result.text)                # the fallback fact or an honest line still answers


class SteerAndLoops(EngineCase):

    QUESTIONS = ("is there parking there?", "do you take cards?", "are you open on saturdays?",
                 "is the doctor experienced?")

    def test_steer_cadence_mid_booking(self):
        # M6 / criterion 4: four pure questions in a row; the pending ask comes back on 1 and 3 only, reworded.
        call = Call()
        self.to_ask_when(call)
        self.model({q: {"acts": ["question"], "intent": "none", "next_goal": "ask_when",
                        "say": "Yes, we do.", "ask": ""} for q in self.QUESTIONS})
        asks = []
        for text in self.QUESTIONS:
            result = call.say(text)
            sentences = speech.split_sentences(result.text)
            asks.append(sentences[-1] if sentences[-1].endswith("?") else None)
        self.assertIsNotNone(asks[0], call.lines)
        self.assertIsNone(asks[1], call.lines)
        self.assertIsNotNone(asks[2], call.lines)
        self.assertIsNone(asks[3], call.lines)
        self.assertNotEqual(asks[0], asks[2])
        self.assertEqual(call.s.book.date_c, None)  # questions never consume the slot
        call.model_free = self.model({})
        call.say("tomorrow morning")
        self.assertEqual(call.s.pending, Goal.OFFER_SLOTS, call.lines)

    def test_capability_question_never_pushes_a_booking(self):
        # Feedback 7: "How can you help?" -> what she can do, then an open question; no booking re-ask.
        call = Call()
        call.say("")
        result = call.say("how can you help me?")
        self.assertEqual(result.goal_after, Goal.CAPABILITY.value, result.text)
        self.assertNotIn("book a visit", result.text.lower())
        self.model({"do you have parking?": {"acts": ["question"], "intent": "info", "next_goal": "answer_only",
                                             "say": "Yes, there's parking near the clinic.", "ask": ""}})
        after = call.say("do you have parking?")
        self.assertNotRegex(after.text.lower(), r"would you like to book|shall i book")
        self.assertEqual(call.s.intent, Intent.INFO)

    def test_offer_help_limits_without_a_workflow(self):
        call = Call()
        call.say("")
        questions = [f"question number {i}?" for i in range(8)]
        self.model({q: {"acts": ["question"], "intent": "info", "next_goal": "answer_only",
                        "say": "Good question.", "ask": ""} for q in questions})
        goals = [call.say(q).goal_after for q in questions]
        offers = [i for i, g in enumerate(goals) if g == Goal.OFFER_HELP.value]
        self.assertLessEqual(len(offers), 3, goals)
        self.assertGreaterEqual(len(offers), 1, goals)
        self.assertTrue(all(b - a >= 2 for a, b in zip(offers, offers[1:])), goals)

    def test_non_answers_climb_the_ladder_and_exit(self):
        # Criterion 6: a non-answer goes straight to the choices rung, then the exit takes a default.
        call = Call()
        self.to_ask_when(call)
        self.model({"i've been really busy lately": {
            "acts": ["non_answer"], "intent": "none", "next_goal": "ask_when",
            "say": "No worries at all.", "ask": ""}})
        call.say("i've been really busy lately")
        self.assertEqual(call.s.pending, Goal.ASK_WHEN)
        self.assertEqual(call.s.pending_params["_rung"], 3, call.lines)
        self.assertTrue(call.s.pending_params["_line"].endswith(".choices"))

    def test_rungs_go_one_two_three_then_exit(self):
        call = Call()
        self.to_ask_when(call)
        rungs = [call.s.pending_params["_rung"]]
        for _ in range(3):
            call.say("hmm")
            if call.s.pending != Goal.ASK_WHEN:
                break
            rungs.append(call.s.pending_params["_rung"])
        self.assertEqual(rungs, [1, 2, 3], call.lines)
        self.assertNotEqual(call.s.pending, Goal.ASK_WHEN)   # the exit: earliest available
        self.assertIn(call.s.pending, (Goal.OFFER_SLOTS, Goal.NO_SLOTS), call.lines)

    def test_25_turn_call_never_repeats_a_line_back_to_back(self):
        # M3 / invariant 6: gibberish, silence-like fillers, refusals and non-answers in every goal.
        call = Call()
        call.say("")
        junk = ("hmm", "I don't know", "uh", "not sure", "whatever", "blah blah", "I've been busy", "no")
        script = ["I want to book a cleaning", "hmm", "My name is Priya", "uh", PHONE, "not sure", "yes",
                  "blah blah", "whatever", "I've been busy", "no", "hmm"]
        while len(script) < 25:
            script.append(junk[len(script) % len(junk)])
        for text in script:
            call.say(text)
            if call.s.closed_conversation:
                break
        for a, b in zip(call.lines, call.lines[1:]):
            self.assertNotEqual(a, b, call.lines)
        self.assertEqual(self.booked_rows(), [])     # never booked without a heard summary and a yes


if __name__ == "__main__":
    unittest.main()
