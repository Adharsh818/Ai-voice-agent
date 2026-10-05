"""
Reply validators V1-V8 (docs/R2_DESIGN.md, section 8.2): every rule has a
sentence it must drop and a natural sentence it must let through, because a
validator that drops good replies swaps one robotic line for another.
"""

import unittest

from dialogue.validate import (
    Allowed, MAX_REPLY_WORDS, SIMILARITY_LIMIT, check_sentence, check_shape, numbers_in, similarity,
)


def allowed(**kw) -> Allowed:
    base = dict(
        numbers=frozenset({"400", "1000", "1500", "5000", "8000", "5", "30", "7", "9"}),
        doctors=frozenset({"dr rao", "dr. meera rao", "dr shetty"}),
        branches=frozenset({"indiranagar", "whitefield", "nagarbhavi"}),
        person_names=frozenset({"priya"}),
        days=frozenset({"monday", "saturday", "october"}),
        prices={"consultation": frozenset({"400"}), "cleaning": frozenset({"1000", "1500"}),
                "root canal": frozenset({"5000", "8000"})},
    )
    base.update(kw)
    return Allowed(**base)


class FactsV1Tests(unittest.TestCase):
    def ok(self, sentence, **kw):
        verdict = check_sentence(sentence, allowed(**kw), part="say")
        self.assertTrue(verdict.ok, f"{sentence!r} dropped: {verdict.rule} {verdict.detail}")

    def drop(self, sentence, rule="V1", **kw):
        verdict = check_sentence(sentence, allowed(**kw), part="say")
        self.assertFalse(verdict.ok, f"{sentence!r} passed")
        self.assertEqual(verdict.rule, rule, verdict.detail)

    def test_an_invented_price_is_dropped(self):
        # Z4: the KB says a consultation is 400.
        self.drop("A consultation is ₹900.")
        self.drop("A consultation is nine hundred rupees.")
        self.ok("A consultation is ₹400.")
        self.ok("A consultation is four hundred rupees.")

    def test_numbers_in_words_and_formats(self):
        self.ok("A cleaning is usually between 1,000 and 1,500 rupees.")
        self.drop("It takes about forty five minutes.")
        self.ok("We're open from 7 till 9.")
        self.ok("Which one works better for you?")          # "one" as a pronoun is not a number
        self.drop("We have one hundred chairs.")

    def test_weekdays_and_months_must_be_in_the_brief(self):
        self.ok("We're open on Saturday too.")
        self.drop("We're open on Sunday too.")
        self.ok("May I take your name?")                    # the verb, not the month
        self.drop("We have a slot on 5 May.")

    def test_doctors_must_be_real_or_named_by_the_caller(self):
        self.ok("Dr Rao is at Indiranagar.")
        self.ok("Dr. Rao is lovely with nervous patients.")
        self.drop("Dr Kapoor is at Indiranagar.")
        self.ok("We don't have a Dr Sharma here.", person_names=frozenset({"priya", "sharma"}))

    def test_branches_must_be_real(self):
        self.ok("Our Whitefield branch has parking.")
        self.drop("Our Koramangala branch has parking.")
        self.ok("Pearl Dental Clinic has four branches.", numbers=frozenset({"4"}))

    def test_names_must_have_been_heard(self):
        self.ok("Thanks, Priya.")
        self.drop("Thanks, Rahul.")
        self.ok("Sure, Monday.")

    def test_price_must_match_its_service(self):
        # V1b: 1,500 is a real number (a cleaning) but not a consultation's price.
        self.drop("A consultation is 1,500 rupees.", rule="V1b")
        self.ok("A root canal is usually 5,000 to 8,000 rupees.")
        self.drop("A root canal is usually 1,000 rupees.", rule="V1b")


class WordingV2Tests(unittest.TestCase):
    def test_bot_wording_is_dropped(self):
        for sentence in ("I'm an AI assistant.", "I'm just a bot, but I can help.", "This is an automated system.",
                         "I'm a virtual assistant for the clinic.", "As a language model I can't say.",
                         "I'm not a real person, but I can help."):
            verdict = check_sentence(sentence, allowed(), part="say")
            self.assertEqual((verdict.ok, verdict.rule), (False, "V2"), sentence)

    def test_bot_wording_allowed_only_on_the_honesty_turn(self):
        self.assertTrue(check_sentence("I'm the clinic's virtual receptionist.", allowed(honesty_turn=True),
                                       part="say").ok)

    def test_dental_words_are_not_bot_words(self):
        for sentence in ("The X-ray machine is quick and painless.", "Our dental assistant will meet you at the desk.",
                         "Cleaning is part of the maintenance program.", "It's not a real worry at all."):
            self.assertTrue(check_sentence(sentence, allowed(), part="say").ok, sentence)

    def test_handoff_is_dropped(self):
        for sentence in ("Let me connect you to a real person.", "I'll transfer you to the front desk.",
                         "You can speak to our staff about that."):
            self.assertEqual(check_sentence(sentence, allowed(), part="say").rule, "V2", sentence)

    def test_callback_promise_needs_a_task(self):
        sentence = "The team will call you back shortly."
        self.assertEqual(check_sentence(sentence, allowed(), part="say").rule, "V2")
        self.assertTrue(check_sentence(sentence, allowed(callback_task=True), part="say").ok)
        for sentence in ("Let me have someone call you.", "I'll get the doctor's team to ring you today.",
                         "Someone can call you about that."):
            self.assertEqual(check_sentence(sentence, allowed(), part="say").rule, "V2", sentence)
        self.assertTrue(check_sentence("And what should I call you?", allowed(), part="ask").ok)

    def test_doctor_deflection_only_when_clinical(self):
        # Owner feedback 3 / M4.
        sentence = "The doctor will discuss that at your visit."
        verdict = check_sentence(sentence, allowed(), part="say")
        self.assertEqual((verdict.ok, verdict.rule), (False, "V2"))
        self.assertTrue(check_sentence(sentence, allowed(clinical=True), part="say").ok)
        self.assertEqual(check_sentence("You'd best ask the doctor about that.", allowed(), part="say").rule, "V2")
        self.assertTrue(check_sentence("Please bring any old X-rays to your visit.", allowed(), part="say").ok)


class ClaimsV3Tests(unittest.TestCase):
    def test_outcome_claims_need_a_commit(self):
        # Z2
        for sentence, action in (("I've booked that for you.", "booked"), ("You're all set.", "booked"),
                                 ("Your appointment has been cancelled.", "cancelled"),
                                 ("I've moved it to Monday.", "rescheduled"), ("That's confirmed.", "booked")):
            verdict = check_sentence(sentence, allowed(), part="say")
            self.assertEqual((verdict.ok, verdict.rule), (False, "V3"), sentence)
            self.assertTrue(check_sentence(sentence, allowed(committed=action), part="say").ok, sentence)

    def test_the_wrong_outcome_is_still_dropped(self):
        self.assertEqual(check_sentence("Your appointment has been cancelled.", allowed(committed="booked"),
                                        part="say").rule, "V3")

    def test_policy_talk_is_not_a_claim(self):
        for sentence in ("There's no fee if you need to cancel.", "Saturday slots get booked up quickly.",
                         "Shall I book that for you?"):
            self.assertTrue(check_sentence(sentence, allowed(), part="ask").ok, sentence)


class MedicalV6Tests(unittest.TestCase):
    def test_medicine_and_diagnosis_are_dropped(self):
        for sentence in ("Take 500 mg paracetamol.", "You probably have an infection.", "You'll need a root canal.",
                         "Sounds like an abscess.", "A painkiller should help until then."):
            verdict = check_sentence(sentence, allowed(numbers=frozenset({"500"})), part="say")
            self.assertEqual((verdict.ok, verdict.rule), (False, "V6"), sentence)

    def test_general_explanations_pass(self):
        for sentence in ("A root canal cleans out the inside of the tooth.",
                         "It's done under local anaesthetic, so most people only feel some pressure."):
            self.assertTrue(check_sentence(sentence, allowed(), part="say").ok, sentence)


class RepeatV7Tests(unittest.TestCase):
    def test_a_near_repeat_of_the_last_sentence_is_dropped(self):
        # Owner feedback 1 / M3.
        recent = ("Would you like to book an appointment?",)
        verdict = check_sentence("Okay, would you like to book an appointment then?", allowed(recent=recent), part="ask")
        self.assertEqual((verdict.ok, verdict.rule), (False, "V7"))
        self.assertTrue(check_sentence("What day suits you?", allowed(recent=recent), part="ask").ok)

    def test_a_notice_is_not_repeated(self):
        notices = ("We don't have a Dr Sharma, but Dr Rao and Dr Shetty are here.",)
        a = allowed(person_names=frozenset({"sharma"})).for_turn(notices=notices)
        self.assertEqual(check_sentence("We don't have a Dr Sharma, but Dr Rao and Dr Shetty are here.", a,
                                        part="say").rule, "V7")

    def test_similarity(self):
        self.assertGreaterEqual(similarity("Would you like to book?", "Okay, would you like to book that?"),
                                SIMILARITY_LIMIT)
        self.assertLess(similarity("What time works for you?", "What day works for you?"), SIMILARITY_LIMIT)
        self.assertEqual(similarity("Okay.", "Okay."), 1.0)
        self.assertEqual(similarity("", "Okay."), 0.0)


class LanguageV8Tests(unittest.TestCase):
    def test_other_scripts_and_markup_are_dropped(self):
        for sentence in ("नमस्ते, how can I help?", "ನಮಸ್ಕಾರ.", "See https://pearl.example for prices.",
                         '{"say": "hi"}', "**Sure**, Monday works."):
            verdict = check_sentence(sentence, allowed(), part="say")
            self.assertEqual((verdict.ok, verdict.rule), (False, "V8"), sentence)

    def test_rupee_sign_and_curly_quotes_pass(self):
        self.assertTrue(check_sentence("It’s ₹400 for a consultation.", allowed(), part="say").ok)


class ShapeV4V5Tests(unittest.TestCase):
    def test_say_has_no_question(self):
        self.assertEqual(check_shape(["Sure, Monday works?"], "").rule, "V4")
        self.assertTrue(check_shape(["Sure, Monday works."], "What time suits you?").ok)

    def test_one_question_only(self):
        self.assertEqual(check_shape(["Sure."], "What day? And what time?").rule, "V4")

    def test_lengths(self):
        self.assertEqual(check_shape(["One.", "Two.", "Three."], "").rule, "V5")
        long = " ".join(["word"] * 26) + "."
        self.assertEqual(check_shape([long], "").rule, "V5")
        twenty = " ".join(["word"] * 20) + "."
        self.assertEqual(check_shape([twenty, twenty], "And then?").rule, "V5")
        self.assertTrue(check_shape(["No worries, we'll find something that fits."],
                                    "Are you thinking this week or next?").ok)
        self.assertEqual(MAX_REPLY_WORDS, 40)


class NumbersInTests(unittest.TestCase):
    def test_formats(self):
        cases = {
            "₹1,500": {"1500"}, "four hundred rupees": {"400"}, "Monday the 5th at 5:30": {"5", "30"},
            "one and a half hours": {"1.5"}, "two thousand five hundred": {"2500"}, "twenty-five": {"25"},
            "a hundred rupees": {"100"}, "Rs. 400": {"400"}, "nine eight": {"9", "8"},
            "one lakh": {"100000"}, "no numbers here": set(),
        }
        for text, expected in cases.items():
            self.assertEqual(set(numbers_in(text)), expected, text)


if __name__ == "__main__":
    unittest.main()
