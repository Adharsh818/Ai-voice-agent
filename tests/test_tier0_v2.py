"""
tier0.understand: Tier-0 v2 for the R2 engine (docs/R2_DESIGN.md, section 6).

Strict mode must answer the short, unambiguous turns without the model and
return None for anything mixed, long or questioning; lenient mode is the
no-model fallback and always returns something. The old fast_entities is
covered by tests/test_booking_flow.py and stays untouched until integration.
"""

import time
import unittest
from datetime import datetime

import clock
import tier0
from dialogue import match
from dialogue.context import Act, Emergency, Expect, Goal, GOAL_SPECS, Intent, OfferedSlot, Tier0View
from test_match import demo_catalog

CATALOG = demo_catalog()
NOW = datetime(2026, 10, 1, 10, 0)          # a Thursday, as in dialogue.testing.DemoClinic


def view(pending=None, expect=None, **kw) -> Tier0View:
    """A Tier0View for Emma's last goal; expect defaults to the goal's GOAL_SPECS column."""
    if expect is None:
        expect = GOAL_SPECS[pending].expect if pending else Expect.OPEN
    kw.setdefault("intent", Intent.BOOK)
    return Tier0View(expect=expect, pending=pending, catalog=CATALOG, **kw)


def slot(day: int, hour: int, minute: int = 0) -> OfferedSlot:
    start = datetime(2026, 10, day, hour, minute)
    return OfferedSlot(1, "Dr Rao", 1, "Nagarbhavi", 3, "Teeth Cleaning", start, start, spoken="")


OFFER = view(Goal.OFFER_SLOTS, offered=(slot(5, 17), slot(5, 17, 30)))


class Tier0Case(unittest.TestCase):
    def setUp(self):
        self._clock = clock.frozen(NOW)
        self._clock.__enter__()

    def tearDown(self):
        self._clock.__exit__(None, None, None)

    def u(self, text, v, **kw):
        return tier0.understand(text, v, **kw)


class StrictAnswers(Tier0Case):
    def test_yes_no(self):
        for text, conf in (("yes", "yes"), ("Yeah, that's right.", "yes"), ("correct", "yes"),
                           ("no", "no"), ("No, that's not right", "no"), ("nope", "no")):
            u = self.u(text, view(Goal.CONFIRM_PHONE))
            self.assertEqual(u.confirmation, conf, text)
            self.assertEqual(u.acts, ["answer"])
            self.assertEqual(u.source, "tier0")

    def test_yes_no_with_a_correction_goes_to_the_model(self):
        for text in ("No, it's 937", "no, it's Priya", "yes but change the date", "No, don't book it",
                     "yes, Tuesday works"):
            self.assertIsNone(self.u(text, view(Goal.CONFIRM_PHONE)), text)

    def test_digits_and_deepgram_formatting(self):
        u = self.u("(789) 937-7462", view(Goal.ASK_PHONE))
        self.assertEqual(u.phone_digits, "7899377462")
        u = self.u("nine eight double seven six five four three two one", view(Goal.ASK_PHONE))
        self.assertEqual(u.phone_digits, "9877654321")
        u = self.u("my number is +91 98765-43210", view(Goal.ASK_PHONE))
        self.assertEqual(u.phone_digits, "9876543210")

    def test_phone_in_two_turns(self):
        first = self.u("98765", view(Goal.ASK_PHONE))
        buf, e164 = match.accumulate_phone("", match.extract_digits(first.phone_digits))
        self.assertIsNone(e164)
        second = self.u("43210", view(Goal.PHONE_MORE, digits_so_far=buf))
        self.assertEqual(second.acts, ["answer"])
        buf, e164 = match.accumulate_phone(buf, match.extract_digits(second.phone_digits))
        self.assertEqual(e164, "+919876543210")

    def test_phone_with_other_words_goes_to_the_model(self):
        self.assertIsNone(self.u("the first one", view(Goal.ASK_PHONE)))
        self.assertIsNone(self.u("98765 and book me for Monday", view(Goal.ASK_PHONE)))

    def test_names(self):
        self.assertEqual(self.u("My name is Priya Sharma", view(Goal.ASK_NAME)).name, "Priya Sharma")
        self.assertEqual(self.u("Adharsh", view(Goal.ASK_NAME)).name, "Adharsh")
        self.assertEqual(self.u("Rohan", view(Goal.ASK_PATIENT)).patient_name, "Rohan")
        self.assertIsNone(self.u("Monday", view(Goal.ASK_NAME)))
        self.assertIsNone(self.u("for my son", view(Goal.ASK_NAME)))

    def test_spelling(self):
        u = self.u("A D H A R S H", view(Goal.SPELL_NAME))
        self.assertEqual(u.name_spelled, "Adharsh")
        u = self.u("a for apple, d, h, a, r, s, h", view(Goal.SPELL_NAME))
        self.assertEqual(u.name_spelled, "Adharsh")

    def test_service(self):
        v = view(Goal.ASK_SERVICE)
        self.assertEqual(self.u("a cleaning please", v).service, "Teeth Cleaning")
        self.assertEqual(self.u("route canal", v).service, "Root Canal Treatment")
        u = self.u("Chicken, chicken.", v)
        self.assertEqual(u.service, "General Check-up")
        u = self.u("tooth", v)
        self.assertIsNone(u.service)
        self.assertEqual(u.service_phrase, "tooth")
        u = self.u("whitening", v)
        self.assertIsNone(u.service)
        self.assertEqual(u.service_phrase, "whitening")

    def test_chicken_is_not_a_service_unless_asked(self):
        self.assertIsNone(self.u("chicken", view(Goal.ASK_BRANCH)))
        self.assertIsNone(self.u("Chicken, chicken.", view(Goal.ASK_INTENT, intent=Intent.NONE)))

    def test_branch(self):
        self.assertEqual(self.u("Indiranagar", view(Goal.ASK_BRANCH)).branch, "Indiranagar")
        self.assertEqual(self.u("white field branch please", view(Goal.ASK_BRANCH)).branch, "Whitefield")
        self.assertTrue(self.u("whichever is earliest", view(Goal.ASK_BRANCH)).branch_any)
        self.assertIsNone(self.u("near the metro", view(Goal.ASK_BRANCH)))

    def test_clarify_service_and_option_picks(self):
        v = view(Goal.CLARIFY_SERVICE, options=("Tooth Filling", "Tooth Extraction", "General Check-up"))
        self.assertEqual(self.u("a filling", v).service, "Tooth Filling")
        u = self.u("the second one", v)
        self.assertEqual((u.choice_index, u.service), (2, "Tooth Extraction"))

    def test_dates_and_times(self):
        u = self.u("next Monday", view(Goal.ASK_WHEN))
        self.assertEqual((u.date_phrase, u.time_phrase), ("next Monday", None))
        u = self.u("tomorrow evening", view(Goal.ASK_WHEN))
        self.assertEqual((u.date_phrase, u.time_phrase), ("tomorrow", "evening"))
        u = self.u("evening", view(Goal.ASK_TIME))
        self.assertEqual((u.date_phrase, u.time_phrase), (None, "evening"))
        u = self.u("in the morning", view(Goal.RESOLVE_AMPM))
        self.assertEqual(u.time_phrase, "in the morning")

    def test_a_time_alone_never_becomes_a_date(self):
        # The "November 22, at" ... "5PM" -> Thursday 1 Oct call.
        u = self.u("5PM", view(Goal.ASK_WHEN))
        self.assertEqual(u.time_phrase, "5PM")
        self.assertIsNone(u.date_phrase)
        self.assertEqual(self.u("November 22, at", view(Goal.ASK_WHEN)).acts, ["fragment"])

    def test_verification_date(self):
        u = self.u("the 5th", view(Goal.ASK_APPT_DATE, intent=Intent.CANCEL))
        self.assertEqual(u.appt_date_phrase, "the 5th")
        self.assertIsNone(u.date_phrase)

    def test_date_refusals_go_to_the_model(self):
        for text in ("Monday doesn't work", "not Monday", "Monday is my son's exam"):
            self.assertIsNone(self.u(text, view(Goal.ASK_WHEN)), text)

    def test_offer_picks(self):
        self.assertEqual(self.u("the first one", OFFER).choice_index, 1)
        self.assertEqual(self.u("the later one", OFFER).choice_index, 2)
        self.assertEqual(self.u("5:30", OFFER).choice_index, 2)
        self.assertEqual(self.u("5 is fine", OFFER).choice_index, 1)
        self.assertEqual(self.u("the 5:30 one", OFFER).choice_index, 2)
        self.assertEqual(self.u("five thirty works", OFFER).choice_index, 2)
        u = self.u("neither", OFFER)
        self.assertTrue(u.reject_options)
        self.assertEqual(u.confirmation, "no")
        self.assertIsNone(self.u("Monday", OFFER))                 # both offers are Monday: ask which
        u = self.u("Tuesday", OFFER)
        self.assertEqual((u.date_phrase, u.choice_index), ("Tuesday", None))

    def test_yes_to_a_single_offer_picks_it(self):
        one = view(Goal.OFFER_SLOTS, offered=(slot(5, 17),))
        self.assertEqual(self.u("yes", one).choice_index, 1)
        self.assertIsNone(self.u("yes", OFFER).choice_index)

    def test_age(self):
        self.assertEqual(self.u("she's seven", view(Goal.ASK_AGE)).age, 7)
        self.assertEqual(self.u("12 years old", view(Goal.ASK_AGE)).age, 12)
        self.assertIsNone(self.u("not sure", view(Goal.ASK_AGE)))

    def test_doctor_preferences(self):
        u = self.u("Dr Rao please", view(Goal.ASK_BRANCH))
        self.assertEqual(u.doctor, "Dr Rao")
        u = self.u("a lady doctor", view(Goal.ASK_INTENT))
        self.assertEqual((u.doctor_gender, u.acts), ("female", ["info"]))
        u = self.u("with Dr Sharma", view(Goal.ASK_BRANCH))
        self.assertEqual((u.doctor, u.doctor_phrase), (None, "Dr Sharma"))


class StrictGlobals(Tier0Case):
    def test_capability_questions(self):
        for text in ("how can you help me?", "Tell me what can you help me with", "What do you do?",
                     "Who are you?", "Hi, how can you help?", "what all can you do"):
            u = self.u(text, view(Goal.GREET, intent=Intent.NONE))
            self.assertEqual(u.acts, ["capability"], text)

    def test_openers(self):
        v = view(Goal.GREET, intent=Intent.NONE)
        self.assertEqual(self.u("Hi, I'd like to book an appointment", v).intent, Intent.BOOK)
        u = self.u("I want to book a root canal", v)
        self.assertEqual((u.intent, u.service), (Intent.BOOK, "Root Canal Treatment"))
        self.assertEqual(self.u("I want to cancel my appointment", v).intent, Intent.CANCEL)
        self.assertEqual(self.u("Can I reschedule my appointment?", v).intent, Intent.RESCHEDULE)
        self.assertEqual(self.u("when is my appointment", v).intent, Intent.CHECK)
        self.assertEqual(self.u("I need a cleaning", v).intent, Intent.BOOK)

    def test_cancel_at_the_summary(self):
        # The 1 Oct cancel-at-recap call: never a booking (Z5).
        u = self.u("So I would like to cancel.", view(Goal.SUMMARY))
        self.assertEqual(u.intent, Intent.CANCEL)
        self.assertIsNone(u.confirmation)

    def test_bye_never_says_yes(self):
        for text in ("Bye.", "okay bye", "thanks, bye"):
            u = self.u(text, view(Goal.SUMMARY))
            self.assertEqual(u.acts, ["end"], text)
            self.assertNotEqual(u.confirmation, "yes", text)
        u = self.u("no that's all, thanks", view(Goal.ANYTHING_ELSE))
        self.assertEqual((u.acts, u.confirmation), (["end"], "no"))
        self.assertEqual(self.u("thank you", view(Goal.BOOKED)).acts, ["end"])
        self.assertEqual(self.u("thank you", view(Goal.ASK_NAME)).acts, ["chitchat"])

    def test_meta_patterns(self):
        cases = {
            "sorry?": "repeat", "come again": "repeat", "can you repeat that": "repeat",
            "hold on one second": "wait", "just a minute": "wait", "let me check my calendar": "wait",
            "are you a bot?": "robot_question", "Am I talking to a real person?": "robot_question",
            "can I talk to a real person": "wants_human", "connect me to the receptionist": "wants_human",
            "Hindi please": "other_language", "mm-hmm": "backchannel",
        }
        for text, act in cases.items():
            u = self.u(text, view(Goal.ASK_NAME if act != "backchannel" else Goal.ANSWER_ONLY))
            self.assertEqual(u.acts, [act], text)

    def test_backchannel_is_a_yes_when_a_yes_was_asked(self):
        self.assertEqual(self.u("okay", view(Goal.CONFIRM_PHONE)).confirmation, "yes")
        self.assertEqual(self.u("okay", view(Goal.ANSWER_ONLY)).acts, ["backchannel"])

    def test_fragments(self):
        for text in ("What's the best", "Tell me what can you", "Cancel the com", "But", "Don't"):
            self.assertEqual(self.u(text, view(Goal.ASK_INTENT)).acts, ["fragment"], text)

    def test_emergencies(self):
        u = self.u("I can't breathe properly and my face is swollen", view(Goal.ASK_NAME))
        self.assertEqual(u.emergency, Emergency.RED_FLAG)
        u = self.u("I have severe tooth pain", view(Goal.GREET, intent=Intent.NONE))
        self.assertEqual(u.emergency, Emergency.URGENT)
        u = self.u("no swelling, just a check-up", view(Goal.ASK_SERVICE))
        self.assertEqual((u.emergency, u.service, u.confirmation), (Emergency.NONE, "General Check-up", None))
        self.assertEqual(u.raw_text, "no swelling, just a check-up")
        self.assertEqual(self.u("no pain as such, just a cleaning", view(Goal.ASK_SERVICE)).service, "Teeth Cleaning")
        self.assertIsNone(self.u("no pain", view(Goal.ASK_SERVICE)))


class StrictNoneWhenUnsure(Tier0Case):
    def test_mixed_or_questioning_turns_go_to_the_model(self):
        cases = [
            ("tomorrow evening around 6 with Dr Sharma, is that okay?", view(Goal.ASK_WHEN)),
            ("tomorrow evening around 6 with Dr Sharma", view(Goal.ASK_WHEN)),
            ("what are your prices for cleaning", view(Goal.ASK_INTENT)),
            ("how much does a root canal cost", view(Goal.ASK_SERVICE)),
            ("I've been really busy lately", view(Goal.ASK_WHEN)),
            ("My name is Priya and my number is 98765 43210", view(Goal.ASK_NAME)),
            ("a cleaning tomorrow at Whitefield", view(Goal.ASK_SERVICE)),
            ("I want to book a cleaning for tomorrow morning", view(Goal.GREET, intent=Intent.NONE)),
            ("is parking available", view(Goal.ASK_BRANCH)),
            ("yes, but can we make it Tuesday instead", view(Goal.SUMMARY)),
            ("Which one is earlier?", OFFER),
            ("hmm I am not sure, let me think about what suits me best this week", view(Goal.ASK_WHEN)),
        ]
        for text, v in cases:
            self.assertIsNone(self.u(text, v), text)

    def test_empty(self):
        self.assertIsNone(self.u("", view(Goal.ASK_NAME)))
        self.assertIsNone(self.u("  ...  ", view(Goal.ASK_NAME)))

    def test_no_catalog_is_still_safe(self):
        v = Tier0View(expect=Expect.OPEN, pending=Goal.ASK_SERVICE, intent=Intent.BOOK)
        self.assertIsNone(self.u("a cleaning", v))
        self.assertEqual(self.u("yes", Tier0View(Expect.YES_NO, Goal.SUMMARY, Intent.BOOK)).confirmation, "yes")


class Lenient(Tier0Case):
    def test_always_returns_and_marks_the_source(self):
        for text in ("", "blah blah", "yes", "how much is a root canal"):
            u = self.u(text, view(Goal.ASK_INTENT), lenient=True)
            self.assertIsNotNone(u, text)
            self.assertEqual(u.source, "fallback")
        self.assertEqual(self.u("blah blah", view(Goal.ASK_INTENT), lenient=True).acts, ["unclear"])

    def test_extracts_everything_it_can(self):
        u = self.u("tomorrow evening around 6 with Dr Sharma, is that okay?", view(Goal.ASK_WHEN), lenient=True)
        self.assertEqual(u.doctor_phrase, "Dr Sharma")
        self.assertEqual(u.date_phrase, "tomorrow")
        self.assertIsNotNone(u.time_phrase)
        self.assertIn("question", u.acts)
        # The date phrase carries no leftovers: "with Dr Rao please" is the doctor, not the date.
        u = self.u("next Monday at 5:30 pm with Dr Rao please", view(Goal.ASK_WHEN), lenient=True)
        self.assertEqual((u.time_phrase, u.doctor), ("5:30 pm", "Dr Rao"))
        self.assertNotIn("rao", u.date_phrase)
        self.assertIn("monday", u.date_phrase)
        u = self.u("my name is Rahul and I want a cleaning at Whitefield", view(Goal.ASK_NAME), lenient=True)
        self.assertEqual((u.name, u.service, u.branch), ("Rahul", "Teeth Cleaning", "Whitefield"))

    def test_a_question_is_flagged_for_the_fallback_lookup(self):
        u = self.u("how much is a root canal", view(Goal.ASK_INTENT), lenient=True)
        self.assertIn("question", u.acts)
        self.assertEqual(u.question, "how much is a root canal")
        self.assertEqual(u.service, "Root Canal Treatment")

    def test_time_only_stays_time_only(self):
        u = self.u("5PM", view(Goal.ASK_WHEN), lenient=True)
        self.assertEqual((u.date_phrase, u.time_phrase), (None, "5PM"))

    def test_yes_no_only_where_asked(self):
        self.assertIsNone(self.u("I don't know what time works for me really", view(Goal.ASK_NAME),
                                 lenient=True).confirmation)
        self.assertEqual(self.u("yes please go ahead and do it", view(Goal.SUMMARY), lenient=True).confirmation,
                         "yes")

    def test_corrected_number_during_the_read_back(self):
        u = self.u("no it's 789 937 7462", view(Goal.CONFIRM_PHONE), lenient=True)
        self.assertEqual(u.phone_digits, "7899377462")
        self.assertIsNone(u.confirmation)

    def test_globals_and_emergencies_still_apply(self):
        self.assertEqual(self.u("bye", view(Goal.SUMMARY), lenient=True).acts, ["end"])
        self.assertEqual(self.u("my jaw is broken", view(Goal.ASK_NAME), lenient=True).emergency,
                         Emergency.RED_FLAG)

    def test_intent_and_family(self):
        u = self.u("I want to cancel my appointment for my son", view(Goal.GREET, intent=Intent.NONE), lenient=True)
        self.assertEqual(u.intent, Intent.CANCEL)
        self.assertTrue(u.for_someone_else)
        self.assertEqual(u.relation, "son")


class CoverageAndSpeed(Tier0Case):
    # A simple booking with details given one at a time (SUCCESS_CRITERIA T5),
    # each caller turn paired with the goal Emma's previous reply had.
    SIMPLE_BOOKING = [
        ("Hi, I'd like to book an appointment", Goal.GREET),
        ("My name is Priya Sharma", Goal.ASK_NAME),
        ("nine eight seven six five", Goal.ASK_PHONE),
        ("four three two one zero", Goal.PHONE_MORE),
        ("yes, that's right", Goal.CONFIRM_PHONE),
        ("a cleaning", Goal.ASK_SERVICE),
        ("Indiranagar", Goal.ASK_BRANCH),
        ("next Monday", Goal.ASK_WHEN),
        ("evening", Goal.ASK_TIME),
        ("the first one", Goal.OFFER_SLOTS),
        ("yes", Goal.SUMMARY),
        ("no that's all, thanks", Goal.ANYTHING_ELSE),
        ("Actually, what's the parking like there?", Goal.ANYTHING_ELSE),
    ]

    def test_strict_handles_most_simple_booking_turns(self):
        handled = 0
        for text, goal in self.SIMPLE_BOOKING:
            v = view(goal, offered=(slot(5, 17), slot(5, 17, 30)) if goal == Goal.OFFER_SLOTS else (),
                     digits_so_far="98765" if goal == Goal.PHONE_MORE else "")
            handled += self.u(text, v) is not None
        self.assertGreaterEqual(handled / len(self.SIMPLE_BOOKING), 0.6)

    def test_under_five_ms_per_call(self):
        texts = ["tomorrow evening around 6 with Dr Sharma, is that okay?", "the first one", "yes",
                 "nine eight seven six five four three two one zero", "My name is Priya Sharma",
                 "I want to book a root canal", "how can you help me?", "Chicken, chicken."]
        views = [view(Goal.ASK_WHEN), OFFER, view(Goal.SUMMARY), view(Goal.ASK_PHONE), view(Goal.ASK_NAME),
                 view(Goal.GREET), view(Goal.GREET), view(Goal.ASK_SERVICE)]
        calls = 0
        start = time.perf_counter()
        for _ in range(20):
            for text in texts:
                for v in views:
                    tier0.understand(text, v)
                    tier0.understand(text, v, lenient=True)
                    calls += 2
        per_call_ms = (time.perf_counter() - start) * 1000 / calls
        self.assertLess(per_call_ms, 5.0)

    def test_acts_are_plain_strings(self):
        u = self.u("how can you help me?", view(Goal.GREET))
        self.assertTrue(u.has(Act.CAPABILITY))
        self.assertTrue(all(isinstance(a, str) for a in u.acts))


if __name__ == "__main__":
    unittest.main()
