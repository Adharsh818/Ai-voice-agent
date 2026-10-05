"""
The conversation regression suite (docs/SUCCESS_CRITERIA.md section 3, layer 1).

Every scripted scenario in harness/scenarios.py runs offline here: a fresh
DEMO-seeded clinic, the clock frozen at Thursday 1 Oct 2026 10:00, the fake
NLU instead of Gemini, so each one is deterministic and takes well under a
second. A scenario passes when its expected properties hold (the database
outcome, phrases that must or must not be said, metrics with no findings),
never on Emma's exact wording, so these survive the engine rewrite.

Every scenario runs twice:

- On the 12-step machine (RegressionScenarios, CatalogueScenarios), still the
  default engine (config.R2_ENGINE off). Scenarios it fails are marked
  expectedFailure with the bug that breaks them; a fix there shows up as an
  unexpected success, and its expectedFailure is removed (the "flip").
- On the R2 engine (R2Scenarios), as plain tests: every HANDOFF section 5
  call and every catalogue edge case must pass there. This is the gate for
  turning R2_ENGINE on.

Both are pinned to their engine, so the suite means the same whichever way
R2_ENGINE is set.

    python -m harness run scenarios                the 12-step runs with full transcripts and a report
    python -m harness run scenarios --engine r2    the same on the R2 engine
"""

import unittest

from harness import report, runner
from harness import scenarios as sc


class _ScenarioCase(unittest.TestCase):
    ENGINE = "legacy"

    def check(self, scenario_id: str):
        record = runner.run_scenario(scenario_id, engine=self.ENGINE)
        if record["passed"]:
            return
        failed = "; ".join(report._check_text(c) for c in record["checks"] if not c["ok"])
        tail = "\n".join(f"  {t['n']:>2} caller: {t.get('caller') or '(silence)'}\n     emma:   {t['emma']}"
                         for t in record["turns"][-4:])
        self.fail(f"{scenario_id} ({record['title']}): {failed}\nlast turns:\n{tail}")


class RegressionScenarios(_ScenarioCase):
    """The failing calls from the 30 Sep - 1 Oct voice tests (docs/HANDOFF.md section 5), plus invariants."""

    # Known bug: step 6 location refusal has no exit (audit problem 2): the same branch line repeats
    @unittest.expectedFailure
    def test_reg_location_no_loop(self):
        self.check("reg_location_no_loop")

    # Known bug: no intent switch at the recap: 'cancel' is taken as a field correction and a later 'Yeah' books
    # it
    @unittest.expectedFailure
    def test_reg_cancel_at_recap(self):
        self.check("reg_cancel_at_recap")

    # Known bug: bookings go to DEFAULT_BRANCH Nagarbhavi, which has no braces doctor, so step 10 loops on 'What
    # other time'
    @unittest.expectedFailure
    def test_reg_braces_nagarbhavi(self):
        self.check("reg_braces_nagarbhavi")

    # Known bug: backend_actions.resolve_date('5PM') falls through to today's date
    @unittest.expectedFailure
    def test_reg_time_fragment_at_date(self):
        self.check("reg_time_fragment_at_date")

    # Known bug: no capability answer: meta questions get the escalation line plus 'Would you like to book a
    # visit?'
    @unittest.expectedFailure
    def test_reg_meta_how_can_you_help(self):
        self.check("reg_meta_how_can_you_help")

    # Known bug: meta questions fall through to the escalation line (no 'who I am' answer)
    @unittest.expectedFailure
    def test_reg_meta_who_are_you(self):
        self.check("reg_meta_who_are_you")

    # Known bug: no clinic overview answer: the escalation line is spoken instead
    @unittest.expectedFailure
    def test_reg_meta_about_clinic(self):
        self.check("reg_meta_about_clinic")

    # Known bug: no capability answer for meta questions
    @unittest.expectedFailure
    def test_reg_meta_what_can_you_help_with(self):
        self.check("reg_meta_what_can_you_help_with")

    # Known bug: with the NLU down every question gets ESCALATION_LINE instead of a knowledge-base answer
    @unittest.expectedFailure
    def test_reg_price_model_down(self):
        self.check("reg_price_model_down")

    def test_reg_price_root_canal(self):
        self.check("reg_price_root_canal")

    # Known bug: no general (non-medical) dental knowledge: anything outside clinic_facts gets the doctor line
    @unittest.expectedFailure
    def test_reg_visit_question(self):
        self.check("reg_visit_question")

    # Known bug: a fragment is treated as a complete question and gets the escalation line
    @unittest.expectedFailure
    def test_reg_fragment_whats_the_best(self):
        self.check("reg_fragment_whats_the_best")

    # Known bug: fragments and meta questions get the escalation line
    @unittest.expectedFailure
    def test_reg_fragment_tell_me(self):
        self.check("reg_fragment_tell_me")

    # Known bug: no intent switch at the recap: cancelling the booking in progress is impossible
    @unittest.expectedFailure
    def test_reg_fragment_cancel_at_recap(self):
        self.check("reg_fragment_cancel_at_recap")

    # Known bug: every off-topic answer re-asks the pending question, so the same question repeats (criterion 3)
    @unittest.expectedFailure
    def test_reg_repeated_steer(self):
        self.check("reg_repeated_steer")

    def test_reg_deepgram_phone_format(self):
        self.check("reg_deepgram_phone_format")

    def test_reg_double_digits_phone(self):
        self.check("reg_double_digits_phone")

    def test_reg_simple_booking(self):
        self.check("reg_simple_booking")

    def test_reg_bot_question(self):
        self.check("reg_bot_question")


class CatalogueScenarios(_ScenarioCase):
    """One scenario per edge case (docs/SUCCESS_CRITERIA.md section 3, docs/HANDOFF.md step 1)."""

    # Known bug: no capability answer for meta questions
    @unittest.expectedFailure
    def test_cat_meta_mid_booking(self):
        self.check("cat_meta_mid_booking")

    # Known bug: chit-chat gets the escalation (doctor) line
    @unittest.expectedFailure
    def test_cat_chitchat_then_book(self):
        self.check("cat_chitchat_then_book")

    # Known bug: an off-topic comment gets the doctor line and is consumed as a failed date answer
    @unittest.expectedFailure
    def test_cat_offtopic_mid_booking(self):
        self.check("cat_offtopic_mid_booking")

    # Known bug: a refusal is treated as a misheard number ('I think I missed a digit')
    @unittest.expectedFailure
    def test_cat_refuse_phone(self):
        self.check("cat_refuse_phone")

    # Known bug: non-answers get a canned re-ask instead of help choosing
    @unittest.expectedFailure
    def test_cat_nonanswer_date(self):
        self.check("cat_nonanswer_date")

    # Known bug: a change to an already-confirmed date is ignored (only the current step's slot is read)
    @unittest.expectedFailure
    def test_cat_change_mind_date(self):
        self.check("cat_change_mind_date")

    # Known bug: no intent switching and no cancel flow in the 12-step engine
    @unittest.expectedFailure
    def test_cat_intent_switch_book_to_cancel(self):
        self.check("cat_intent_switch_book_to_cancel")

    def test_cat_out_of_order(self):
        self.check("cat_out_of_order")

    def test_cat_multi_detail(self):
        self.check("cat_multi_detail")

    def test_cat_correction_phone(self):
        self.check("cat_correction_phone")

    def test_cat_correction_name(self):
        self.check("cat_correction_name")

    # Known bug: unknown services get a fixed treatment list instead of the whitening fact and a consultation
    # offer
    @unittest.expectedFailure
    def test_cat_unknown_service_whitening(self):
        self.check("cat_unknown_service_whitening")

    # Known bug: unknown services get a fixed treatment list instead of the implant fact and a consultation offer
    @unittest.expectedFailure
    def test_cat_unknown_service_implant(self):
        self.check("cat_unknown_service_implant")

    # Known bug: no branch awareness: braces can't be booked, and Emma never mentions the branches that do them
    @unittest.expectedFailure
    def test_cat_branch_mismatch_braces(self):
        self.check("cat_branch_mismatch_braces")

    # Known bug: Nagarbhavi has no pediatric doctor and the patient-vs-caller name isn't handled
    @unittest.expectedFailure
    def test_cat_family_pediatric(self):
        self.check("cat_family_pediatric")

    # Known bug: after the silence ladder the same name question comes a third time, word for word
    @unittest.expectedFailure
    def test_cat_silence(self):
        self.check("cat_silence")

    def test_cat_bot_question_mid_flow(self):
        self.check("cat_bot_question_mid_flow")

    # Known bug: a person request gets the doctor line instead of an offer to help
    @unittest.expectedFailure
    def test_cat_person_request(self):
        self.check("cat_person_request")

    # Known bug: a person request gets the doctor line, the second 'no, a human' closes the call, and no callback
    # task is made
    @unittest.expectedFailure
    def test_cat_person_insist(self):
        self.check("cat_person_insist")

    # Known bug: no emergency path, and the 'can't' in 'I can't open my mouth' reads as a no at the greeting,
    # which closes the call
    @unittest.expectedFailure
    def test_cat_emergency_red_flag(self):
        self.check("cat_emergency_red_flag")

    # Known bug: no same-day urgent booking: 'as soon as possible' can't be turned into a slot
    @unittest.expectedFailure
    def test_cat_emergency_pain(self):
        self.check("cat_emergency_pain")

    # Known bug: no cancel flow, and 'cancel' at the greeting reads as a no, which closes the call
    @unittest.expectedFailure
    def test_cat_cancel_verified(self):
        self.check("cat_cancel_verified")

    # Known bug: no reschedule flow in the 12-step engine
    @unittest.expectedFailure
    def test_cat_reschedule_verified(self):
        self.check("cat_reschedule_verified")

    # Known bug: no reschedule flow; 'prepone' isn't understood
    @unittest.expectedFailure
    def test_cat_prepone(self):
        self.check("cat_prepone")

    # Known bug: no check flow: the question gets the doctor line and the call closes on 'No'
    @unittest.expectedFailure
    def test_cat_check_appointment(self):
        self.check("cat_check_appointment")

    # Known bug: doctor names are ignored: no 'there's no Dr Sharma' and no offer of the doctors who are there
    @unittest.expectedFailure
    def test_cat_dr_sharma(self):
        self.check("cat_dr_sharma")

    # Known bug: 'morning' is read as 7 AM, which no Nagarbhavi doctor works, and the offered slot is booked
    # without a summary (Z1)
    @unittest.expectedFailure
    def test_cat_indian_phrasing(self):
        self.check("cat_indian_phrasing")

    def test_cat_sunday_request(self):
        self.check("cat_sunday_request")

    def test_cat_lunch_time(self):
        self.check("cat_lunch_time")

    def test_cat_wrong_number(self):
        self.check("cat_wrong_number")

    # Known bug: every answer re-asks 'Would you like to book a visit?', so it repeats three times
    @unittest.expectedFailure
    def test_cat_price_shopper(self):
        self.check("cat_price_shopper")

    # Known bug: 'Saturday morning' becomes 7 AM (no doctor then), and '9 in the morning' isn't understood when
    # picking an offered slot (step 10 only matches '9 am')
    @unittest.expectedFailure
    def test_cat_hours_then_book(self):
        self.check("cat_hours_then_book")

    # Known bug: an offered alternative is booked the moment it's picked, with no summary and no yes (Z1)
    @unittest.expectedFailure
    def test_cat_taken_slot(self):
        self.check("cat_taken_slot")

    # Known bug: s.last_reply_heard is ignored: a yes over an interrupted summary books
    @unittest.expectedFailure
    def test_cat_barge_in_summary(self):
        self.check("cat_barge_in_summary")


class R2Scenarios(_ScenarioCase):
    """
    Every scenario on the R2 engine, as a plain test (one test_<scenario id>
    each, added below). The HANDOFF section 5 calls (location "No" loop,
    cancel at the recap, braces at Nagarbhavi, the time-only fragment, meta
    and price questions, fragments) are the reg_* ones.
    """
    ENGINE = "r2"


def _add_r2_tests():
    for scenario in sc.SCENARIOS:
        def test(self, scenario_id=scenario.id):
            self.check(scenario_id)
        test.__name__ = f"test_{scenario.id}"
        test.__doc__ = f"{scenario.title} (R2 engine)"
        setattr(R2Scenarios, test.__name__, test)


_add_r2_tests()


class ScenarioBookkeeping(unittest.TestCase):
    """Keeps this file and harness/scenarios.py in step."""

    def _methods(self) -> dict:
        out = {}
        for case in (RegressionScenarios, CatalogueScenarios):
            for name in dir(case):
                if name.startswith("test_"):
                    out[name[len("test_"):]] = getattr(case, name)
        return out

    def test_every_scenario_has_a_test(self):
        missing = [s.id for s in sc.SCENARIOS if s.id not in self._methods()]
        self.assertEqual(missing, [], "add a test method for each new scenario")

    def test_r2_runs_every_scenario_as_a_plain_test(self):
        for s in sc.SCENARIOS:
            method = getattr(R2Scenarios, f"test_{s.id}", None)
            with self.subTest(scenario=s.id):
                self.assertIsNotNone(method)
                self.assertFalse(getattr(method, "__unittest_expecting_failure__", False))

    def test_expected_failures_name_a_bug(self):
        """A scenario with a known bug is an expected failure, and an expected failure names its bug."""
        methods = self._methods()
        for s in sc.SCENARIOS:
            expecting = bool(getattr(methods.get(s.id), "__unittest_expecting_failure__", False))
            with self.subTest(scenario=s.id):
                self.assertEqual(expecting, bool(s.bug),
                                 "flip the expectedFailure and the scenario's `bug` together")


if __name__ == "__main__":
    unittest.main()
