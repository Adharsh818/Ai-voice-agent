"""Regression tests for booking correctness without external API calls."""

import asyncio
import os
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

import backend_actions
import config
import ai_engine


async def local_nlu(text, s=None):
    return ai_engine._basic_entity_fallback(text)


def entities(**overrides):
    """A complete NLU result with only the named slots filled in."""
    base = {
        "patient_name": None,
        "phone_number": None,
        "dental_service": None,
        "appointment_date": None,
        "appointment_time": None,
        "confirmation": None,
        "user_query": None,
    }
    base.update(overrides)
    return base


class BookingFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_path = config.MOCK_DB_PATH
        self.old_mock = config.USE_MOCK_APIS
        config.MOCK_DB_PATH = os.path.join(self.temp_dir.name, "appointments.json")
        config.USE_MOCK_APIS = True

    def tearDown(self):
        config.MOCK_DB_PATH = self.old_path
        config.USE_MOCK_APIS = self.old_mock
        self.temp_dir.cleanup()

    def test_booking_requires_final_recap_confirmation(self):
        async def run_flow():
            session = ai_engine.SessionState()
            # Purpose is asked before the phone number (greeting, name, purpose,
            # phone, service, location, date, time).
            turns = [
                "", "yes", "my name is Alex Johnson", "yes",
                "I need to book a root canal",
                "9876543210", "yes",
                "root canal", "yes", "yes",
                "next Monday", "yes", "5 pm", "yes",
            ]
            for turn in turns:
                await ai_engine.async_get_ai_response(turn, session)
            self.assertEqual(session.step, 9)
            self.assertFalse(session.booking_confirmed)

            await ai_engine.async_get_ai_response("yes, everything is correct", session)
            self.assertTrue(session.booking_confirmed)
            self.assertEqual(session.step, 11)

        with patch.object(ai_engine, "async_extract_entities_with_llm", local_nlu):
            asyncio.run(run_flow())

    def test_mock_booking_rejects_conflicting_slot_and_allows_idempotent_retry(self):
        self.assertTrue(backend_actions.book_appointment(
            "Alex", "9876543210", "Consultation", "2026-08-22", "05:00 PM"
        )[0])
        self.assertFalse(backend_actions.book_appointment(
            "Blair", "8765432109", "Consultation", "2026-08-22", "05:00 PM"
        )[0])
        self.assertTrue(backend_actions.book_appointment(
            "Alex", "9876543210", "Consultation", "2026-08-22", "05:00 PM"
        )[0])

    def test_date_and_time_boundaries(self):
        self.assertEqual(backend_actions.resolve_time("8:30 pm")[1], "08:30 PM")
        self.assertIsNone(backend_actions.resolve_time("9 pm")[0])
        self.assertIsNone(backend_actions.resolve_date("1 January 2020", base_date=date(2026, 1, 1))[0])
        self.assertEqual(
            backend_actions.resolve_date("next monday", base_date=date(2026, 8, 21))[1],
            "2026-08-24",
        )

    def test_alternative_slots_are_nearest_free_and_exclude_busy_slot(self):
        # 2026-08-24 is a Monday (working day). Book 5:00 PM, then request it.
        backend_actions.book_appointment(
            "Alex", "9876543210", "Consultation", "2026-08-24", "05:00 PM"
        )
        available, alts = backend_actions.check_availability("2026-08-24", "05:00 PM")
        self.assertFalse(available)
        # The busy slot must never be re-offered, and alternatives are the
        # nearest free slots (not a hardcoded 5 PM / 6 PM pair).
        self.assertNotIn("05:00 PM", alts)
        self.assertEqual(alts, ["04:30 PM", "05:30 PM"])

    def test_alternative_slots_skip_lunch_break(self):
        # Book 1:30 PM; the 2:00 PM neighbour falls in the lunch break and must
        # not be offered as an alternative.
        backend_actions.book_appointment(
            "Alex", "9876543210", "Consultation", "2026-08-24", "01:30 PM"
        )
        available, alts = backend_actions.check_availability("2026-08-24", "01:30 PM")
        self.assertFalse(available)
        self.assertNotIn("02:00 PM", alts)
        self.assertEqual(alts, ["01:00 PM", "12:30 PM"])

    def test_confirmation_message_hides_calendar_invite_in_mock_mode(self):
        config.USE_MOCK_APIS = True
        mock_msg = ai_engine._confirmation_message(
            "Root Canal Treatment", "Monday, 24 August 2026", "05:00 PM"
        )
        self.assertNotIn("Google Calendar", mock_msg)

        config.USE_MOCK_APIS = False
        real_msg = ai_engine._confirmation_message(
            "Root Canal Treatment", "Monday, 24 August 2026", "05:00 PM"
        )
        self.assertIn("Google Calendar", real_msg)

    def test_volunteered_slots_are_confirmed_never_reasked(self):
        s = ai_engine.SessionState()
        s.step = 3  # purpose step; the name is already settled
        s.name = s.temp_name = "Alex Johnson"
        s.name_confirmed = True

        # Caller volunteers service, date and time in a single breath.
        reply = ai_engine._handle_conversation_step(
            "I'd like a root canal next Monday at 5 pm",
            entities(
                dental_service="root canal",
                appointment_date="next Monday",
                appointment_time="5 pm",
            ),
            s,
        )
        self.assertEqual(s.temp_service, "Root Canal Treatment")
        self.assertEqual(s.temp_time, "05:00 PM")
        self.assertTrue(s.temp_date)
        # Purpose understood; the only unknown left is the phone number.
        self.assertEqual(s.step, 4)
        self.assertIn("mobile number", reply)

        reply = ai_engine._handle_conversation_step(
            "9876543210", entities(phone_number="9876543210"), s
        )
        self.assertIn("9 8 7 6 5 4 3 2 1 0", reply)

        yes = entities(confirmation="yes")
        reply = ai_engine._handle_conversation_step("yes", yes, s)
        self.assertEqual(s.step, 5)
        self.assertIn("Root Canal Treatment", reply)
        self.assertNotIn("Which dental service", reply)

        reply = ai_engine._handle_conversation_step("yes", yes, s)
        self.assertEqual(s.step, 6)  # location

        reply = ai_engine._handle_conversation_step("yes", yes, s)
        self.assertEqual(s.step, 7)
        self.assertNotIn("Which date would you prefer", reply)

        reply = ai_engine._handle_conversation_step("yes", yes, s)
        self.assertEqual(s.step, 8)
        self.assertIn("05:00 PM", reply)
        self.assertNotIn("What time works best", reply)

        reply = ai_engine._handle_conversation_step("yes", yes, s)
        self.assertEqual(s.step, 9)
        self.assertIn("Is everything correct?", reply)

    def test_purpose_question_skipped_when_intent_already_clear(self):
        s = ai_engine.SessionState()
        s.step = 2
        s.temp_name = "Alex"
        reply = ai_engine._handle_conversation_step(
            "yes, I need a teeth cleaning",
            entities(confirmation="yes", dental_service="teeth cleaning"),
            s,
        )
        self.assertTrue(s.name_confirmed)
        self.assertEqual(s.purpose, "booking")
        self.assertEqual(s.step, 4)
        self.assertNotIn("How may I help you today", reply)

    def test_absorber_never_overrides_the_active_or_confirmed_slot(self):
        # At the phone step, the phone is the active slot: the step itself must
        # validate it rather than the absorber silently pre-filling it.
        s = ai_engine.SessionState()
        s.step = 4
        ai_engine._absorb_volunteered_slots(entities(phone_number="9876543210"), s)
        self.assertEqual(s.temp_phone, "")

        # A confirmed slot is never rewritten by a later stray extraction.
        s2 = ai_engine.SessionState()
        s2.step = 3
        s2.temp_service = "Braces"
        s2.service = "Braces"
        s2.service_confirmed = True
        ai_engine._absorb_volunteered_slots(entities(dental_service="root canal"), s2)
        self.assertEqual(s2.temp_service, "Braces")

    def test_name_confirmation_spells_out_only_on_retry(self):
        s = ai_engine.SessionState()
        s.step = 2
        first = ai_engine._handle_conversation_step(
            "my name is Adharsh", entities(patient_name="Adharsh"), s
        )
        self.assertIn("Adharsh", first)
        self.assertNotIn("A D H A R S H", first)

        retry = ai_engine._handle_conversation_step("hmm", entities(), s)
        self.assertIn("A D H A R S H", retry)

    def test_recap_speaks_name_plainly_but_spells_out_phone(self):
        s = ai_engine.SessionState()
        s.name = "Adharsh"
        s.phone = "7899377462"
        s.service = "Root Canal Treatment"
        s.date_str = "2026-08-24"
        s.time_str = "05:00 PM"
        recap = ai_engine._recap_message(s)
        self.assertIn("Name: Adharsh.", recap)
        self.assertNotIn("A D H A R S H", recap)
        self.assertIn("7 8 9 9 3 7 7 4 6 2", recap)

    def test_short_service_fragments_do_not_match(self):
        self.assertEqual(ai_engine._match_service("root canal"), "Root Canal Treatment")
        self.assertEqual(ai_engine._match_service("Braces"), "Braces")
        self.assertIsNone(ai_engine._match_service("a"))
        self.assertIsNone(ai_engine._match_service(""))
        self.assertIsNone(ai_engine._match_service(None))


class ConfirmationParsingTests(unittest.TestCase):
    """A refusal must never be heard as consent, and vice versa."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_path = config.MOCK_DB_PATH
        self.old_mock = config.USE_MOCK_APIS
        config.MOCK_DB_PATH = os.path.join(self.temp_dir.name, "appointments.json")
        config.USE_MOCK_APIS = True

    def tearDown(self):
        config.MOCK_DB_PATH = self.old_path
        config.USE_MOCK_APIS = self.old_mock
        self.temp_dir.cleanup()

    def test_negation_wins_over_affirmative_words_inside_it(self):
        # Every one of these contains a word from the affirmative vocabulary
        # ("right", "correct", "definitely"), so a yes-first parser inverts them.
        for text in ["No, that is not right", "no, that's not correct",
                     "that is incorrect", "definitely not", "not right now",
                     "nope, change it", "that's the wrong number"]:
            self.assertEqual(ai_engine._parse_confirmation(text), "no", text)

    def test_affirmative_idioms_containing_no_are_not_refusals(self):
        for text in ["no problem", "no worries", "not a problem", "why not",
                     "no problem, that is all correct"]:
            self.assertEqual(ai_engine._parse_confirmation(text), "yes", text)

    def test_an_idiom_does_not_override_a_real_objection_beside_it(self):
        # "no problem" alone is consent, but not when the rest of the sentence
        # objects to something — that must fall through to "unclear".
        self.assertIsNone(
            ai_engine._parse_confirmation("no problem with the name but the date is wrong")
        )

    def test_word_boundaries_prevent_substring_false_positives(self):
        # "alright" must not match via "right"; "book" must not match via "ok".
        self.assertEqual(ai_engine._parse_confirmation("alright"), "yes")
        self.assertIsNone(ai_engine._parse_confirmation("I want to book"))
        self.assertIsNone(ai_engine._parse_confirmation("Root Canal Treatment"))
        self.assertIsNone(ai_engine._parse_confirmation("Next Monday"))
        self.assertIsNone(ai_engine._parse_confirmation("my name is Adharsh"))
        self.assertIsNone(ai_engine._parse_confirmation(""))
        self.assertIsNone(ai_engine._parse_confirmation(None))

    def test_rejected_recap_never_books(self):
        """The core safety invariant: no booking without an explicit yes."""
        s = ai_engine.SessionState()
        s.step = 9
        s.name = "Adharsh"
        s.phone = "9876543210"
        s.service = "Root Canal Treatment"
        s.date_str = "2026-08-24"
        s.time_str = "11:00 AM"

        with patch.object(backend_actions, "book_appointment") as booker:
            reply = ai_engine._handle_conversation_step(
                "No, that is not right",
                ai_engine._basic_entity_fallback("No, that is not right"),
                s,
            )
            booker.assert_not_called()
        self.assertEqual(s.step, 9)
        self.assertFalse(s.recap_confirmed)
        self.assertFalse(s.booking_confirmed)
        self.assertIn("incorrect", reply)

    def test_nlu_and_text_disagreement_is_treated_as_unclear(self):
        # NLU says yes, the words say no -> Emma re-asks instead of committing.
        s = ai_engine.SessionState()
        s.step = 6  # location step: a pure yes/no gate
        reply = ai_engine._handle_conversation_step(
            "no, that is not right", entities(confirmation="yes"), s
        )
        self.assertEqual(s.step, 6)
        self.assertFalse(s.location_confirmed)
        self.assertIn("Nagarbhavi", reply)

    def test_refusal_at_greeting_beats_a_booking_keyword(self):
        s = ai_engine.SessionState()
        text = "No, I don't want to book anything"
        reply = ai_engine._handle_conversation_step(
            text, ai_engine._basic_entity_fallback(text), s
        )
        self.assertTrue(s.closed_conversation)
        self.assertEqual(s.step, 1)
        self.assertIn("call you back", reply)


class NameCaptureTests(unittest.TestCase):
    def test_letter_free_answer_does_not_auto_confirm_the_name(self):
        # norm_response is "" for a spoken phone number, and "" is a substring of
        # every name — so the implicit-confirmation check used to accept it.
        s = ai_engine.SessionState()
        s.step = 2
        s.temp_name = "Adharsh"
        ai_engine._handle_conversation_step("9876543210", entities(), s)
        self.assertFalse(s.name_confirmed)
        self.assertEqual(s.step, 2)

    def test_slot_content_is_not_adopted_as_the_name(self):
        for text in ["next Monday at 5 pm", "I want a cleaning", "9876543210",
                     "tomorrow morning please"]:
            s = ai_engine.SessionState()
            s.step = 2
            reply = ai_engine._handle_conversation_step(text, entities(), s)
            self.assertEqual(s.temp_name, "", text)
            self.assertIn("full name", reply)

    def test_real_names_are_still_captured(self):
        for text, expected in [("my name is Alex Johnson", "Alex Johnson"),
                               ("It is Adharsh", "Adharsh"),
                               ("Priya", "Priya"),
                               ("Ravi Shankar Venkata Raghavan Iyer",
                                "Ravi Shankar Venkata Raghavan Iyer")]:
            s = ai_engine.SessionState()
            s.step = 2
            ai_engine._handle_conversation_step(text, entities(), s)
            self.assertEqual(s.temp_name, expected, text)

    def test_a_bare_acknowledgement_is_not_taken_as_a_name(self):
        for text in ["yes", "no", "okay", "sure"]:
            s = ai_engine.SessionState()
            s.step = 2
            ai_engine._handle_conversation_step(text, entities(), s)
            self.assertEqual(s.temp_name, "", text)

    def test_a_screened_real_name_still_gets_through(self):
        """The keyword screen is advisory: no caller may be trapped at the name step."""
        s = ai_engine.SessionState()
        s.step = 2
        for _ in range(3):
            reply = ai_engine._handle_conversation_step("Sunday Adebayo", entities(), s)
        self.assertEqual(s.temp_name, "Sunday Adebayo")
        self.assertIn("Sunday Adebayo", reply)

    def test_the_escape_hatch_never_accepts_digits_as_a_name(self):
        for text in ["9876543210", "next Monday at 5 pm"]:
            s = ai_engine.SessionState()
            s.step = 2
            for _ in range(5):
                ai_engine._handle_conversation_step(text, entities(), s)
            self.assertEqual(s.temp_name, "", text)

    def test_rejected_name_uses_the_correction_given_in_the_same_breath(self):
        s = ai_engine.SessionState()
        s.step = 2
        s.temp_name = "Alex"
        reply = ai_engine._handle_conversation_step(
            "no, it's Priya", entities(confirmation="no", patient_name="Priya"), s
        )
        self.assertEqual(s.temp_name, "Priya")
        self.assertIn("Priya", reply)
        self.assertNotIn("tell me your full name again", reply)

    def test_rejected_slot_does_not_reconfirm_the_same_value(self):
        # resolve_date("not Monday") returns Monday — the value just rejected.
        s = ai_engine.SessionState()
        s.step = 7
        s.temp_date = backend_actions.resolve_date("next monday")[1]
        reply = ai_engine._handle_conversation_step("not Monday", entities(confirmation="no"), s)
        self.assertEqual(s.temp_date, "")
        self.assertIn("Which date would you prefer", reply)

    def test_rejected_date_accepts_a_new_date_from_the_same_turn(self):
        s = ai_engine.SessionState()
        s.step = 7
        s.temp_date = backend_actions.resolve_date("next monday", base_date=date(2026, 8, 21))[1]
        ai_engine._handle_conversation_step("no, make it Tuesday", entities(confirmation="no"), s)
        self.assertTrue(s.temp_date)
        self.assertNotEqual(s.temp_date, "2026-08-24")


if __name__ == "__main__":
    unittest.main()
