"""
dialogue/match.py: the deterministic matchers Tier-0 and apply.py share
(docs/R2_DESIGN.md, sections 6 and 12; HANDOFF section 5 accent evidence).
"""

import unittest

from dialogue import match
from facts import Branch, Catalog, Doctor, Service

GENERAL = ("General Check-up", "Consultation", "Teeth Cleaning", "Tooth Filling", "Tooth Extraction")


def demo_catalog() -> Catalog:
    """The DEMO clinic's structure, built by hand from the facts dataclasses (seed_demo.py)."""
    services = (
        Service(1, "General Check-up", "a check-up", 30, False, ("check up", "checkup", "check-up", "general check")),
        Service(2, "Consultation", "a consultation", 30, True,
                ("consultation", "consult", "see the doctor", "toothache", "tooth pain")),
        Service(3, "Teeth Cleaning", "a cleaning", 30, False, ("cleaning", "scaling", "polishing")),
        Service(4, "Tooth Filling", "a filling", 45, False, ("filling", "cavity")),
        Service(5, "Tooth Extraction", "an extraction", 45, False,
                ("extraction", "pull out", "pulled out", "remove a tooth", "wisdom tooth")),
        Service(6, "Root Canal Treatment", "a root canal", 60, False, ("root canal", "rct")),
        Service(7, "Braces", "braces", 30, True, ("braces", "orthodontic")),
        Service(8, "Invisalign", "Invisalign", 30, True, ("invisalign", "clear aligners", "aligners")),
        Service(9, "Pediatric Dentistry", "a children's appointment", 30, False,
                ("pediatric", "paediatric", "kids dentist", "child dentist")),
    )
    branches = (
        Branch(1, "Nagarbhavi", "Nagarbhavi, Bengaluru"),
        Branch(2, "Indiranagar", "Indiranagar, Bengaluru"),
        Branch(3, "Jayanagar", "Jayanagar, Bengaluru"),
        Branch(4, "Whitefield", "Whitefield, Bengaluru"),
    )
    doctors = (
        Doctor(1, "Dr. Meera Rao", "Dr Rao", "female", "Nagarbhavi", 1, GENERAL),
        Doctor(2, "Dr. Arjun Shetty", "Dr Shetty", "male", "Nagarbhavi", 1,
               ("Root Canal Treatment", "Tooth Filling", "Consultation", "General Check-up")),
        Doctor(3, "Dr. Kavya Iyer", "Dr Iyer", "female", "Indiranagar", 2, ("Braces", "Invisalign", "Consultation")),
        Doctor(4, "Dr. Rahul Menon", "Dr Menon", "male", "Indiranagar", 2, GENERAL + ("Root Canal Treatment",)),
        Doctor(5, "Dr. Sneha Kulkarni", "Dr Kulkarni", "female", "Jayanagar", 3,
               ("Pediatric Dentistry", "General Check-up", "Teeth Cleaning", "Consultation")),
        Doctor(6, "Dr. Vikram Nair", "Dr Nair", "male", "Jayanagar", 3, GENERAL + ("Root Canal Treatment",)),
        Doctor(7, "Dr. Ananya Reddy", "Dr Reddy", "female", "Whitefield", 4,
               ("General Check-up", "Consultation", "Teeth Cleaning", "Tooth Filling", "Pediatric Dentistry")),
        Doctor(8, "Dr. Farhan Ali", "Dr Ali", "male", "Whitefield", 4,
               ("Tooth Extraction", "Braces", "Invisalign", "Consultation")),
    )
    return Catalog(branches, doctors, services)


CATALOG = demo_catalog()

# The yes/no answers the retired 12-step machine's parser gave (match.parse_yes_no
# was copied from it unchanged); pinned so the behaviour can't drift.
YES_NO_CASES = [
    ('No, that is not right', 'no'),
    ("no, that's not correct", 'no'),
    ('that is incorrect', 'no'),
    ('definitely not', 'no'),
    ('not right now', 'no'),
    ('nope, change it', 'no'),
    ("that's the wrong number", 'no'),
    ('no problem', 'yes'),
    ('no worries', 'yes'),
    ('not a problem', 'yes'),
    ('why not', 'yes'),
    ('no problem, that is all correct', 'yes'),
    ('no problem with the name but the date is wrong', None),
    ('alright', 'yes'),
    ('I want to book', None),
    ('Root Canal Treatment', None),
    ('Next Monday', None),
    ('my name is Adharsh', None),
    ('', None),
    (None, None),
    ('yes', 'yes'),
    ('Yes.', 'yes'),
    ('yeah', 'yes'),
    ('yep', 'yes'),
    ('yup', 'yes'),
    ('sure', 'yes'),
    ('okay', 'yes'),
    ('ok', 'yes'),
    ('correct', 'yes'),
    ("that's right", 'yes'),
    ('absolutely', 'yes'),
    ('of course', 'yes'),
    ('go ahead', 'yes'),
    ('sounds good', 'yes'),
    ('perfect', 'yes'),
    ('fine', 'yes'),
    ('that works', 'yes'),
    ('please do', 'yes'),
    ('no', 'no'),
    ('No.', 'no'),
    ('nope', 'no'),
    ('nah', 'no'),
    ('not really', 'no'),
    ('wrong', 'no'),
    ("that's not it", 'no'),
    ('cancel it', 'no'),
    ('change the date', 'no'),
    ("I don't think so", 'no'),
    ('never mind', 'no'),
    ("it isn't", 'no'),
    ('yes, but change the date', 'no'),
    ("can't wait", 'yes'),
    ('no doubt', 'yes'),
    ('No issue at all', 'yes'),
    ('haan', None),
    ('mm-hmm', None),
    ('right, right', 'yes'),
    ('book it', None),
    ('Yeah sure, go ahead', 'yes'),
    ('thats it', 'yes'),
    ('That’s not correct', 'no'),
    ('all right then', 'yes'),
    ("no, it's Priya", 'no'),
    ('nothing else', 'no'),
]


class YesNoTests(unittest.TestCase):
    def test_pinned_answers(self):
        self.assertGreaterEqual(len(YES_NO_CASES), 60)
        for text, expected in YES_NO_CASES:
            self.assertEqual(match.parse_yes_no(text), expected, repr(text))


class DigitTests(unittest.TestCase):
    def test_deepgram_us_formatting(self):
        run = match.extract_digits("(789) 937-7462")
        self.assertEqual(run.digits, "7899377462")
        self.assertTrue(run.complete)
        self.assertEqual(match.extract_digits("98450 12345").digits, "9845012345")

    def test_country_code_and_trunk_prefix_are_stripped(self):
        self.assertEqual(match.extract_digits("+91 98765-43210").digits, "9876543210")
        self.assertEqual(match.extract_digits("plus nine one nine eight seven six five four three two one zero").digits,
                         "9876543210")
        self.assertEqual(match.extract_digits("09876543210").digits, "9876543210")
        self.assertTrue(match.extract_digits("080 2345 6789").complete)       # Bengaluru landline

    def test_double_triple_and_tens(self):
        run = match.extract_digits("nine eight double seven six five four three two one")
        self.assertEqual(run.digits, "9877654321")
        self.assertTrue(run.complete)
        self.assertEqual(match.extract_digits("triple zero").digits, "000")
        self.assertEqual(match.extract_digits("nine eight seventy six five four three two one zero").digits,
                         "9876543210")
        self.assertEqual(match.extract_digits("ninety-eight").digits, "98")
        self.assertEqual(match.extract_digits("double 9").digits, "99")

    def test_oh_counts_only_inside_a_run(self):
        self.assertEqual(match.extract_digits("nine oh two").digits, "902")
        self.assertEqual(match.extract_digits("oh okay").digits, "")
        self.assertEqual(match.extract_digits("oh, nine eight").digits, "98")

    def test_partial_and_too_many(self):
        self.assertFalse(match.extract_digits("98765").complete)
        self.assertTrue(match.extract_digits("9876543210 98765").too_many)
        self.assertEqual(match.extract_digits("my number is").digits, "")

    def test_two_turns_accumulate_to_a_complete_number(self):
        buf, e164 = match.accumulate_phone("", match.extract_digits("98765"))
        self.assertEqual((buf, e164), ("98765", None))
        buf, e164 = match.accumulate_phone(buf, match.extract_digits("43210"))
        self.assertEqual(e164, "+919876543210")
        self.assertEqual(buf, "9876543210")

    def test_spoken_groups_across_three_turns(self):
        buf = ""
        for chunk in ("nine eight seven", "six five four", "three two one zero"):
            buf, e164 = match.accumulate_phone(buf, match.extract_digits(chunk))
        self.assertEqual(e164, "+919876543210")

    def test_a_whole_number_replaces_the_buffer(self):
        buf, e164 = match.accumulate_phone("789", match.extract_digits("(789) 937-7462"))
        self.assertEqual((buf, e164), ("7899377462", "+917899377462"))

    def test_more_than_twelve_digits_resets(self):
        self.assertEqual(match.accumulate_phone("98765432", match.extract_digits("109876")), ("", None))
        self.assertEqual(match.accumulate_phone("", match.extract_digits("1234567890123")), ("", None))

    def test_prefix_spoken_first_then_the_number(self):
        buf, e164 = match.accumulate_phone("", match.extract_digits("zero"))
        buf, e164 = match.accumulate_phone(buf, match.extract_digits("98765 43210"))
        self.assertEqual(e164, "+919876543210")

    def test_no_digits_keeps_the_buffer(self):
        self.assertEqual(match.accumulate_phone("98765", match.extract_digits("sorry")), ("98765", None))


class NameTests(unittest.TestCase):
    def test_spelled_letters(self):
        self.assertEqual(match.join_spelled("A D H A R S H"), "Adharsh")
        self.assertEqual(match.join_spelled("A. D. H. A. R. S. H."), "Adharsh")
        self.assertEqual(match.join_spelled("it's P R I Y A"), "Priya")
        self.assertEqual(match.join_spelled("a for apple, d, h, a, r, double s"), "Adharss")
        self.assertEqual(match.join_spelled("K as in kite, A, V, Y, A"), "Kavya")
        self.assertEqual(match.join_spelled("W A double you"), "Waw")

    def test_not_a_spelling(self):
        for text in ("why are", "Priya", "my name is Priya", "yes", "", "A"):
            self.assertIsNone(match.join_spelled(text), text)

    def test_clean_name(self):
        self.assertEqual(match.clean_name("My name is priya sharma."), "Priya Sharma")
        self.assertEqual(match.clean_name("This is Rahul speaking"), "Rahul")
        self.assertEqual(match.clean_name("myself Adharsh"), "Adharsh")
        self.assertEqual(match.clean_name("K. Ramesh"), "K Ramesh")
        self.assertEqual(match.clean_name("d'souza"), "D'Souza")

    def test_clean_name_rejects_non_names(self):
        for text in ("yes", "Monday", "root canal", "98765", "But", "I want to book",
                     "my son", "next week please", "A B C", "one two three four five six seven"):
            self.assertIsNone(match.clean_name(text), text)

    def test_fuzzy_names_from_the_accent_calls(self):
        self.assertEqual(match.closest_name("Adashar", ["Adharsh"]), "Adharsh")
        self.assertEqual(match.closest_name("Adrish", ["Adharsh"]), "Adharsh")
        self.assertIsNone(match.closest_name("Bharat", ["Adharsh"]))
        self.assertIsNone(match.closest_name("Priya", []))

    def test_similarity(self):
        self.assertGreaterEqual(match.name_similarity("Priya Sharma", "priya sharma"), 0.99)
        self.assertGreaterEqual(match.name_similarity("Priya", "Priya Sharma"), match.NAME_MATCH_RATIO)
        self.assertLess(match.name_similarity("Rahul", "Rohan"), match.NAME_ECHO_RATIO)
        self.assertLess(match.name_similarity("Kavya", "Kavita"), match.NAME_MATCH_RATIO)
        self.assertEqual(match.name_similarity("", "Priya"), 0.0)


class ServiceTests(unittest.TestCase):
    S = CATALOG.services

    def test_aliases_and_names(self):
        self.assertEqual(match.match_service("I need a root canal", self.S).value, "Root Canal Treatment")
        self.assertEqual(match.match_service("Root Canal Treatment", self.S).value, "Root Canal Treatment")
        self.assertEqual(match.match_service("a check-up please", self.S).value, "General Check-up")
        self.assertEqual(match.match_service("check up", self.S).value, "General Check-up")
        self.assertEqual(match.match_service("cleaning", self.S).value, "Teeth Cleaning")
        self.assertEqual(match.match_service("fillings", self.S).value, "Tooth Filling")
        self.assertEqual(match.match_service("invisible braces", self.S).value, "Invisalign")
        self.assertEqual(match.match_service("tooth pain", self.S).value, "Consultation")
        self.assertEqual(match.match_service("my wisdom tooth", self.S).value, "Tooth Extraction")

    def test_stt_slips(self):
        self.assertEqual(match.match_service("route canal", self.S).value, "Root Canal Treatment")
        self.assertEqual(match.match_service("Chicken, chicken.", self.S, asked=True).value, "General Check-up")
        self.assertIsNone(match.match_service("chicken biryani", self.S).value)

    def test_tooth_alone_is_ambiguous(self):
        m = match.match_service("it's my tooth", self.S)
        self.assertIsNone(m.value)
        self.assertEqual(set(m.options), {"Tooth Filling", "Tooth Extraction", "General Check-up"})

    def test_two_services_are_ambiguous(self):
        m = match.match_service("cleaning and a filling", self.S)
        self.assertIsNone(m.value)
        self.assertEqual(set(m.options), {"Teeth Cleaning", "Tooth Filling"})

    def test_unknown_treatments(self):
        for text, phrase in (("teeth whitening", "teeth whitening"), ("an implant", "implant"),
                             ("veneers", "veneers")):
            m = match.match_service(text, self.S)
            self.assertIsNone(m.value, text)
            self.assertEqual(m.unknown_phrase, phrase)

    def test_nothing(self):
        self.assertEqual(match.match_service("next Monday", self.S), match.CatalogMatch(None))
        self.assertEqual(match.match_service("cleaning", ()), match.CatalogMatch(None))
        self.assertIsNone(match.match_service("I want to book", self.S).value)


class BranchDoctorTests(unittest.TestCase):
    def test_branches(self):
        B = CATALOG.branches
        self.assertEqual(match.match_branch("Indiranagar please", B).value, "Indiranagar")
        self.assertEqual(match.match_branch("indira nagar", B).value, "Indiranagar")
        self.assertEqual(match.match_branch("white field branch", B).value, "Whitefield")
        self.assertEqual(match.match_branch("Nagarabhavi", B).value, "Nagarbhavi")
        self.assertIsNone(match.match_branch("near the metro", B).value)
        self.assertEqual(match.match_branch("the Koramangala branch", B).unknown_phrase, "Koramangala")
        self.assertIsNone(match.match_branch("your main branch", B).unknown_phrase)
        self.assertEqual(set(match.match_branch("Jayanagar or Whitefield", B).options), {"Jayanagar", "Whitefield"})

    def test_doctors(self):
        D = CATALOG.doctors
        self.assertEqual(match.match_doctor("Dr Rao", D).value, "Dr Rao")
        self.assertEqual(match.match_doctor("doctor Meera please", D).value, "Dr Rao")
        self.assertEqual(match.match_doctor("Dr. Sneha Kulkarni", D).value, "Dr Kulkarni")
        self.assertEqual(match.match_doctor("with Dr Reddi", D).value, "Dr Reddy")
        self.assertEqual(match.match_doctor("Rao", D, allow_bare=True).value, "Dr Rao")
        self.assertIsNone(match.match_doctor("I'm Priya Nair", D).value)

    def test_unknown_doctor(self):
        m = match.match_doctor("tomorrow evening around 6 with Dr Sharma", CATALOG.doctors)
        self.assertIsNone(m.value)
        self.assertEqual(m.unknown_phrase, "Dr Sharma")

    def test_doctor_words_that_are_not_names(self):
        D = CATALOG.doctors
        for text in ("a doctor appointment", "the doctor is fine", "doctor's fees", "lady doctor please"):
            self.assertEqual(match.match_doctor(text, D), match.CatalogMatch(None), text)

    def test_gender(self):
        self.assertEqual(match.doctor_gender("a lady doctor please"), "female")
        self.assertEqual(match.doctor_gender("female dentist"), "female")
        self.assertEqual(match.doctor_gender("gents doctor"), "male")
        self.assertIsNone(match.doctor_gender("any doctor is fine"))
        self.assertIsNone(match.doctor_gender("it doesn't have to be a lady doctor"))


class FragmentTests(unittest.TestCase):
    def test_cut_offs_from_the_test_calls(self):
        for text in ("What's the best", "Tell me what can you", "Cancel the com", "But", "Don't",
                     "Tell me how good you", "November 22, at", "I want to book an appointment for my",
                     "Actually", "Um"):
            self.assertTrue(match.is_fragment(text, "open"), text)

    def test_complete_short_answers(self):
        self.assertFalse(match.is_fragment("yes", "yes_no"))
        self.assertFalse(match.is_fragment("Monday", "date"))
        self.assertFalse(match.is_fragment("Priya", "name"))
        self.assertFalse(match.is_fragment("98765", "phone"))
        self.assertFalse(match.is_fragment("nine eight seven six five", "phone"))
        self.assertFalse(match.is_fragment("A D H A R S H", "spelling"))
        for text in ("How are you", "thank you", "No, I don't", "Yes, I can", "the earliest",
                     "whichever is nearest", "cancel the one on Monday", "mm-hmm", "that's all"):
            self.assertFalse(match.is_fragment(text, "open"), text)

    def test_backchannels(self):
        for text in ("mm-hmm", "Mhmm.", "yeah", "Okay.", "right", "uh-huh", "got it", "okay okay"):
            self.assertTrue(match.is_backchannel(text), text)
        for text in ("yeah but Monday", "okay so what's the price", "Priya", "", "it"):
            self.assertFalse(match.is_backchannel(text), text)

    def test_questions(self):
        for text in ("how much is a root canal", "Where are you located", "Is there parking?",
                     "what are your timings", "do you take insurance"):
            self.assertTrue(match.looks_like_question(text), text)
        for text in ("yes", "next Monday", "Priya Sharma", "a cleaning please"):
            self.assertFalse(match.looks_like_question(text), text)


if __name__ == "__main__":
    unittest.main()
