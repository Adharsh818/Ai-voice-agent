"""
dialogue/apply.py on hand-built contexts and Understandings: the
confirmation disagreement rule, names (implicit confirmation, fuzzy match,
spell-back after one miss), phone accumulation and read-back, catalog checks
for service, branch and doctor, dates through dateparse (a time alone never
becomes a date), corrections with and without a cue, intent switches that
carry name and phone, and "cancel" at the summary (docs/R2_DESIGN.md,
sections 10.4 and 12). Real DEMO catalog; apply never touches the database.
"""

import asyncio
import unittest
from datetime import date, datetime

import facts
from dateparse import DateConstraint
from dialogue import apply as applier
from dialogue.context import (
    Act, CallContext, FieldState, Goal, Intent, OfferedSlot, Understanding, new_context,
)
from dialogue.runtime import Runtime
from dialogue.testing import DemoClinic

NOW = datetime(2026, 10, 1, 10, 0)             # a Thursday
E164 = "+919845012345"


class ApplyCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.clinic = DemoClinic(now=NOW)
        cls.clinic.__enter__()
        facts.clear_cache()
        cls.catalog = asyncio.run(facts.get_catalog())

    @classmethod
    def tearDownClass(cls):
        facts.clear_cache()
        cls.clinic.__exit__(None, None, None)

    def setUp(self):
        self.rt = Runtime(call_id="apply", db=None, catalog=self.catalog, kb=facts.Knowledge())

    def ctx(self, pending=None, intent=Intent.BOOK) -> CallContext:
        ctx = new_context("apply")
        ctx.intent = intent
        ctx.pending = pending
        return ctx

    def identified(self, pending=None) -> CallContext:
        ctx = self.ctx(pending)
        ctx.caller.name, ctx.caller.name_state = "Priya", FieldState.HEARD
        ctx.caller.names_heard = ["Priya"]
        ctx.caller.phone_e164, ctx.caller.phone_state = E164, FieldState.CONFIRMED
        return ctx

    def apply(self, ctx, text="", **fields) -> list:
        u = Understanding(acts=fields.pop("acts", [Act.ANSWER.value]), raw_text=text, **fields)
        return applier.apply(ctx, u, self.rt)

    def lines(self, notices) -> list:
        return [n.line for n in notices]


class Confirmation(ApplyCase):

    def test_parser_disagreement_means_ask_again(self):
        self.assertIsNone(applier.resolve_confirmation(Understanding(confirmation="yes"), "no, not that"))
        self.assertEqual(applier.resolve_confirmation(Understanding(), "yes please"), "yes")
        self.assertEqual(applier.resolve_confirmation(Understanding(confirmation="no"), "nope"), "no")
        self.assertEqual(applier.resolve_confirmation(Understanding(confirmation="yes"), "go for it"), "yes")


class Names(ApplyCase):

    def test_first_name_is_confirmed_implicitly(self):
        ctx = self.ctx(Goal.ASK_NAME)
        notices = self.apply(ctx, "My name is Priya", name="Priya")
        self.assertEqual((ctx.caller.name, ctx.caller.name_state), ("Priya", FieldState.HEARD))
        self.assertEqual(self.lines(notices), ["ack.name"])

    def test_a_misheard_repeat_is_the_same_person(self):
        ctx = self.ctx(Goal.ASK_PHONE)
        ctx.caller.name, ctx.caller.name_state, ctx.caller.names_heard = "Adharsh", FieldState.HEARD, ["Adharsh"]
        self.apply(ctx, "Adashar", name="Adashar")
        self.assertEqual(ctx.caller.name, "Adharsh")
        self.assertEqual(ctx.change_proposal, {})

    def test_one_correction_asks_for_spelling_then_takes_the_letters(self):
        ctx = self.ctx(Goal.ASK_PHONE)
        ctx.caller.name, ctx.caller.name_state = "Ada", FieldState.HEARD
        self.apply(ctx, "no, my name is Adharsh", name="Adharsh", acts=[Act.CORRECTION.value])
        self.assertEqual(ctx.caller.name_misses, 1)
        self.assertIn(Goal.SPELL_NAME, self.rt.raised)
        ctx.pending = Goal.SPELL_NAME
        notices = self.apply(ctx, "A D H A R S H", name_spelled="A D H A R S H")
        self.assertEqual((ctx.caller.name, ctx.caller.name_state), ("Adharsh", FieldState.CONFIRMED))
        self.assertEqual(self.lines(notices), ["ack.spelled"])

    def test_unprompted_different_name_is_checked_first(self):
        ctx = self.identified(Goal.ASK_BRANCH)
        self.apply(ctx, "Rahul", name="Rahul")
        self.assertEqual(ctx.caller.name, "Priya")
        self.assertEqual(ctx.change_proposal["field"], "name")

    def test_third_failure_keeps_it_flagged(self):
        ctx = self.ctx(Goal.ASK_NAME)
        ctx.caller.name, ctx.caller.name_state, ctx.caller.name_misses = "Ada", FieldState.HEARD, 2
        self.apply(ctx, "no, it's Adharsh", name="Adharsh")
        self.assertEqual(ctx.caller.name_state, FieldState.UNVERIFIED)


class Phone(ApplyCase):

    def test_digits_accumulate_across_turns_then_need_a_yes(self):
        ctx = self.ctx(Goal.ASK_PHONE)
        self.apply(ctx, "9845", phone_digits="9845")
        self.assertEqual(ctx.caller.phone_buffer, "9845")
        self.assertIsNone(ctx.caller.phone_e164)
        ctx.pending = Goal.PHONE_MORE
        self.apply(ctx, "012345", phone_digits="012345")
        self.assertEqual((ctx.caller.phone_e164, ctx.caller.phone_state), (E164, FieldState.PENDING))
        self.assertEqual(ctx.caller.phone_buffer, "")
        ctx.pending = Goal.CONFIRM_PHONE
        self.apply(ctx, "yes", confirmation="yes")
        self.assertEqual(ctx.caller.phone_state, FieldState.CONFIRMED)

    def test_us_formatting_and_a_rejected_read_back(self):
        ctx = self.ctx(Goal.ASK_PHONE)
        self.apply(ctx, "(984) 501-2345", phone_digits="(984) 501-2345")
        self.assertEqual(ctx.caller.phone_e164, E164)
        ctx.pending = Goal.CONFIRM_PHONE
        self.apply(ctx, "no", confirmation="no")
        self.assertEqual((ctx.caller.phone_e164, ctx.caller.phone_state, ctx.caller.phone_misses),
                         (None, FieldState.EMPTY, 1))

    def test_too_many_digits_resets(self):
        ctx = self.ctx(Goal.ASK_PHONE)
        notices = self.apply(ctx, "98450123456789", phone_digits="98450123456789")
        self.assertIn("phone.too_many", self.lines(notices))
        self.assertEqual(ctx.caller.phone_buffer, "")


class Catalog(ApplyCase):

    def test_service_alias_and_unknown_treatment(self):
        ctx = self.identified(Goal.ASK_SERVICE)
        self.apply(ctx, "route canal", service_phrase="route canal")
        self.assertEqual(ctx.book.service, "Root Canal Treatment")
        ctx = self.identified(Goal.ASK_SERVICE)
        notices = self.apply(ctx, "whitening", service_phrase="whitening")
        self.assertEqual(ctx.book.service, "Consultation")
        self.assertIn("service.unknown", self.lines(notices))

    def test_ambiguous_service_keeps_the_options(self):
        ctx = self.identified(Goal.ASK_SERVICE)
        self.apply(ctx, "my tooth", service_phrase="tooth")
        self.assertIsNone(ctx.book.service)
        self.assertEqual(len(ctx.book.service_options), 3)

    def test_branch_without_the_service_is_refused(self):
        # Z6, the braces-at-Nagarbhavi loop: name the branches that do it instead.
        ctx = self.identified(Goal.ASK_BRANCH)
        ctx.book.service = "Braces"
        notices = self.apply(ctx, "Nagarbhavi", branch="Nagarbhavi")
        self.assertIsNone(ctx.book.branch)
        self.assertEqual(self.lines(notices), ["branch.no_service"])
        self.assertIn("Indiranagar", notices[0].params["branches"])

    def test_new_service_rechecks_the_branch(self):
        ctx = self.identified(Goal.ASK_SERVICE)
        ctx.book.branch = "Nagarbhavi"
        notices = self.apply(ctx, "braces", service="Braces")
        self.assertEqual(ctx.book.service, "Braces")
        self.assertIsNone(ctx.book.branch)
        self.assertIn("branch.no_service", self.lines(notices))

    def test_yes_to_the_earliest_branch_proposal(self):
        ctx = self.identified(Goal.ASK_BRANCH)
        ctx.book.service = "Braces"
        ctx.pending_params = {"_line": "ask.branch.choices"}
        self.apply(ctx, "yes please", confirmation="yes")
        self.assertTrue(ctx.book.branch_any)

    def test_unknown_doctor_names_who_is_there(self):
        ctx = self.identified(Goal.ASK_WHEN)
        ctx.book.service, ctx.book.branch = "Teeth Cleaning", "Nagarbhavi"
        notices = self.apply(ctx, "with Dr Sharma", doctor_phrase="Dr Sharma")
        self.assertEqual(self.lines(notices), ["doctor.unknown"])
        self.assertIn("Dr Rao", notices[0].params["doctors"])
        self.assertIsNone(ctx.book.doctor_id)

    def test_catalog_doctor_sets_the_branch(self):
        ctx = self.identified(Goal.ASK_BRANCH)
        ctx.book.service = "Teeth Cleaning"
        self.apply(ctx, "Dr Iyer please", doctor="Dr Iyer")
        self.assertEqual((ctx.book.doctor, ctx.book.branch), ("Dr Iyer", "Indiranagar"))


class When(ApplyCase):

    def booking(self, pending=Goal.ASK_WHEN):
        ctx = self.identified(pending)
        ctx.book.service, ctx.book.branch = "Teeth Cleaning", "Indiranagar"
        return ctx

    def test_date_and_time_together(self):
        ctx = self.booking()
        notices = self.apply(ctx, "tomorrow evening", date_phrase="tomorrow", time_phrase="evening")
        self.assertEqual(ctx.book.date_c.start, date(2026, 10, 2))
        self.assertEqual(ctx.book.time_c.kind, "window")
        self.assertIn("ack.when", self.lines(notices))

    def test_a_time_alone_never_becomes_a_date(self):
        # The "5PM -> Thursday 1 Oct" bug: the date stays missing and is asked for.
        ctx = self.booking()
        self.apply(ctx, "5 pm", time_phrase="5 pm")
        self.assertIsNone(ctx.book.date_c)
        self.assertIsNotNone(ctx.book.time_c)

    def test_sunday_is_a_notice(self):
        ctx = self.booking()
        notices = self.apply(ctx, "on Sunday", date_phrase="Sunday")
        self.assertIn("date.sunday", self.lines(notices))


class Corrections(ApplyCase):

    def dated(self, pending):
        ctx = self.identified(pending)
        ctx.book.service, ctx.book.branch = "Teeth Cleaning", "Indiranagar"
        ctx.book.date_c = DateConstraint(date(2026, 10, 2), date(2026, 10, 2))
        return ctx

    def test_without_a_cue_it_is_checked(self):
        ctx = self.dated(Goal.ASK_TIME)
        ctx.pending = Goal.CONFIRM_PHONE                    # a pending goal that isn't about the date
        self.apply(ctx, "Tuesday", date_phrase="Tuesday")
        self.assertEqual(ctx.book.date_c.start, date(2026, 10, 2))
        self.assertEqual(ctx.change_proposal["field"], "date")
        ctx.pending = Goal.CONFIRM_CHANGE
        notices = self.apply(ctx, "yes", confirmation="yes")
        self.assertEqual(ctx.book.date_c.start, date(2026, 10, 6))
        self.assertIn("correction.ack", self.lines(notices))
        self.assertEqual(ctx.change_proposal, {})

    def test_with_a_cue_it_is_applied_and_acknowledged(self):
        ctx = self.dated(Goal.CONFIRM_PHONE)
        notices = self.apply(ctx, "actually make it Tuesday", date_phrase="Tuesday")
        self.assertEqual(ctx.book.date_c.start, date(2026, 10, 6))
        self.assertIn("correction.ack", self.lines(notices))

    def test_any_detail_at_the_summary_is_a_correction(self):
        ctx = self.dated(Goal.SUMMARY)
        version = ctx.book.version
        self.apply(ctx, "Tuesday", date_phrase="Tuesday")
        self.assertEqual(ctx.book.date_c.start, date(2026, 10, 6))
        self.assertGreater(ctx.book.version, version)      # the summary must be heard again


class Switches(ApplyCase):

    def slot(self):
        start = datetime(2026, 10, 2, 9, 0)
        return OfferedSlot(1, "Dr Iyer", 2, "Indiranagar", 3, "Teeth Cleaning", start, start, hold_id="h1")

    def test_cancel_at_the_summary_drops_and_never_books(self):
        ctx = self.identified(Goal.SUMMARY)
        ctx.book.service, ctx.book.chosen = "Teeth Cleaning", self.slot()
        self.apply(ctx, "no, cancel it", confirmation="no")
        self.assertIn(Goal.DROPPED, self.rt.raised)
        self.assertEqual(ctx.intent, Intent.NONE)
        self.assertIsNone(ctx.book.chosen)
        self.assertTrue(self.rt.release_requested)
        self.assertEqual(ctx.caller.phone_e164, E164)

    def test_cancel_intent_at_the_summary_drops_too(self):
        ctx = self.identified(Goal.SUMMARY)
        ctx.book.service = "Teeth Cleaning"
        self.apply(ctx, "I want to cancel my appointment", intent=Intent.CANCEL)
        self.assertIn(Goal.DROPPED, self.rt.raised)
        self.assertEqual(ctx.intent, Intent.NONE)
        ctx.pending = Goal.DROPPED
        self.apply(ctx, "yes", confirmation="yes")         # "did you mean one you already have?"
        self.assertEqual(ctx.intent, Intent.CANCEL)
        self.assertEqual(ctx.caller.name, "Priya")

    def test_book_to_reschedule_parks_the_draft_and_carries_identity(self):
        ctx = self.identified(Goal.ASK_WHEN)
        ctx.book.service = "Teeth Cleaning"
        self.apply(ctx, "actually I need to move my other appointment", intent=Intent.RESCHEDULE)
        self.assertEqual(ctx.intent, Intent.RESCHEDULE)
        self.assertEqual(ctx.parked_book.service, "Teeth Cleaning")
        self.assertEqual(ctx.manage.action, Intent.RESCHEDULE)
        self.assertEqual((ctx.caller.name, ctx.caller.phone_e164), ("Priya", E164))
        self.apply(ctx, "and book that cleaning too", intent=Intent.BOOK)
        self.assertEqual(ctx.intent, Intent.BOOK)
        self.assertEqual(ctx.book.service, "Teeth Cleaning")
        self.assertIsNone(ctx.parked_book)

    def test_questions_never_switch_the_workflow(self):
        ctx = self.identified(Goal.ASK_WHEN)
        self.apply(ctx, "what are your hours?", acts=[Act.QUESTION.value], intent=Intent.INFO)
        self.assertEqual(ctx.intent, Intent.BOOK)


if __name__ == "__main__":
    unittest.main()
