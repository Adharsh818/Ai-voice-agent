"""
dialogue/policy.py on hand-built contexts: the priority order, the M2
property (never ask for a detail the context already holds), the loop
breaker rungs and exits, the steer-back cadence, the OFFER_HELP limits,
listening_hint and expects_information (docs/R2_DESIGN.md, sections 4, 9
and 12). The DEMO catalog is real (DemoClinic); no model, no database writes.
"""

import asyncio
import random
import unittest
from datetime import date, datetime, time

import facts
from dateparse import DateConstraint, TimeConstraint
from dialogue import policy
from dialogue.context import (
    Act, CallContext, FieldState, Goal, GoalPlan, Intent, RUNG_CHOICES,
    RUNG_EXIT, RUNG_REPHRASE, Understanding, new_context,
)
from dialogue.testing import DemoClinic

NOW = datetime(2026, 10, 1, 10, 0)
TOMORROW = date(2026, 10, 2)
E164 = "+919845012345"


class PolicyCase(unittest.TestCase):

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

    def booking(self, **caller) -> CallContext:
        ctx = new_context("policy")
        ctx.intent = Intent.BOOK
        for k, v in caller.items():
            setattr(ctx.caller, k, v)
        return ctx

    def identified(self) -> CallContext:
        return self.booking(name="Priya", name_state=FieldState.HEARD, phone_e164=E164,
                            phone_state=FieldState.CONFIRMED)

    def plan(self, ctx, u=None, **kw) -> GoalPlan:
        return policy.next_goal(ctx, u, catalog=self.catalog, **kw)


class Priority(PolicyCase):

    def test_book_asks_name_then_phone_then_the_rest(self):
        ctx = self.booking()
        ctx.book.service = "Teeth Cleaning"                 # volunteered early: kept, not asked (D2)
        self.assertEqual(self.plan(ctx).goal, Goal.ASK_NAME)
        ctx.caller.name, ctx.caller.name_state = "Priya", FieldState.HEARD
        self.assertEqual(self.plan(ctx).goal, Goal.ASK_PHONE)
        ctx.caller.phone_buffer = "9845"
        self.assertEqual(self.plan(ctx).goal, Goal.PHONE_MORE)
        ctx.caller.phone_buffer, ctx.caller.phone_e164 = "", E164
        ctx.caller.phone_state = FieldState.PENDING
        plan = self.plan(ctx)
        self.assertEqual(plan.goal, Goal.CONFIRM_PHONE)
        self.assertTrue(plan.critical)
        self.assertTrue(plan.params["phone"])
        ctx.caller.phone_state = FieldState.CONFIRMED
        self.assertEqual(self.plan(ctx).goal, Goal.ASK_BRANCH)

    def test_manage_asks_phone_first(self):
        ctx = new_context("m")
        ctx.intent = Intent.CANCEL
        ctx.manage.action = Intent.CANCEL
        self.assertIn(self.plan(ctx).goal, (Goal.ASK_PHONE,))
        self.assertEqual(self.plan(ctx).line, "ask.phone.manage")

    def test_dropped_and_confirm_change_come_first(self):
        ctx = self.booking()
        self.assertEqual(self.plan(ctx, raised=(Goal.DROPPED,)).goal, Goal.DROPPED)
        ctx.change_proposal = {"field": "date", "value": None, "spoken": "Tuesday"}
        plan = self.plan(ctx)
        self.assertEqual(plan.goal, Goal.CONFIRM_CHANGE)
        self.assertEqual(plan.params["value"], "Tuesday")

    def test_capability_question_is_answered_for_what_it_is(self):
        ctx = self.identified()
        u = Understanding(acts=[Act.CAPABILITY.value], raw_text="how can you help?")
        plan = self.plan(ctx, u)
        self.assertEqual(plan.goal, Goal.CAPABILITY)
        who = Understanding(acts=[Act.CAPABILITY.value], raw_text="who are you?")
        self.assertEqual(self.plan(new_context(), who).line, "capability.who")

    def test_no_workflow_asks_intent_then_anything_else_after_an_outcome(self):
        ctx = new_context()
        self.assertEqual(self.plan(ctx).goal, Goal.ASK_INTENT)
        ctx.outcome = "booked"
        self.assertEqual(self.plan(ctx).goal, Goal.ANYTHING_ELSE)

    def test_missing_lists_the_checklist_without_filled_details(self):
        ctx = self.identified()
        ctx.book.service, ctx.book.branch = "Teeth Cleaning", "Indiranagar"
        todo = policy.missing(ctx)
        self.assertNotIn(Goal.ASK_NAME, todo)
        self.assertNotIn(Goal.ASK_PHONE, todo)
        self.assertNotIn(Goal.ASK_SERVICE, todo)
        self.assertNotIn(Goal.ASK_BRANCH, todo)
        self.assertEqual(todo[0], Goal.ASK_WHEN)
        self.assertEqual(todo[-1], Goal.SUMMARY)

    def test_plan_hint_is_a_plan_without_hearing_the_caller(self):
        ctx = self.booking(name="Priya", name_state=FieldState.HEARD)
        hint = policy.plan_hint(ctx, catalog=self.catalog)
        self.assertIsInstance(hint, GoalPlan)
        self.assertEqual(hint.goal, Goal.ASK_PHONE)


class NeverAskWhatIsHeld(PolicyCase):
    """M2 property: over randomized contexts, no ASK goal for a detail the context holds."""

    SERVICES = (None, "Teeth Cleaning", "Root Canal Treatment", "Braces Consultation", "Pediatric Dentistry")

    def random_context(self, rng: random.Random) -> CallContext:
        ctx = new_context(f"m2-{rng.random()}")
        ctx.intent = rng.choice((Intent.BOOK, Intent.BOOK, Intent.CANCEL, Intent.RESCHEDULE, Intent.CHECK))
        c, b, m = ctx.caller, ctx.book, ctx.manage
        if rng.random() < 0.6:
            c.name = rng.choice(("Priya", "Adharsh", "Rahul"))
            c.name_state = rng.choice((FieldState.HEARD, FieldState.CONFIRMED, FieldState.UNVERIFIED))
        phone = rng.choice(("none", "buffer", "pending", "confirmed"))
        if phone == "buffer":
            c.phone_buffer = "98450"
        elif phone in ("pending", "confirmed"):
            c.phone_e164 = E164
            c.phone_state = FieldState.PENDING if phone == "pending" else FieldState.CONFIRMED
        services = [s.name for s in self.catalog.services]
        if rng.random() < 0.6:
            b.service = rng.choice(services)
        if rng.random() < 0.5:
            branches = list(self.catalog.branches_offering(b.service)) if b.service else \
                [br.name for br in self.catalog.branches]
            b.branch = rng.choice(branches) if branches else None
        elif rng.random() < 0.2:
            b.branch_any = True
        if rng.random() < 0.5:
            b.date_c = DateConstraint(TOMORROW, TOMORROW)
            r = rng.random()
            if r < 0.3:
                b.time_c = TimeConstraint("window", time(9), time(12), label="morning")
            elif r < 0.45:
                b.time_c = TimeConstraint("ambiguous", candidates=(time(7), time(19)), label="7")
            elif r < 0.6:
                b.any_time = True
        if rng.random() < 0.2:
            b.for_someone_else = True
            b.relation = "son"
            if rng.random() < 0.5:
                b.patient_name = "Arjun"
        if rng.random() < 0.3:
            b.age = 8
        if ctx.intent != Intent.BOOK:
            m.action = ctx.intent
            if rng.random() < 0.5:
                m.patient_name = "Priya"
            if rng.random() < 0.5:
                m.appt_date = DateConstraint(TOMORROW, TOMORROW)
        ctx.pending = rng.choice(list(Goal))
        ctx.last_reply_heard = rng.random() < 0.8
        return ctx

    def test_property(self):
        rng = random.Random(20261008)
        for i in range(600):
            ctx = self.random_context(rng)
            if i % 2:
                # Half the cases past identity, so the booking and manage checklists get exercised too.
                ctx.caller.name, ctx.caller.name_state = "Priya", FieldState.HEARD
                ctx.caller.phone_e164, ctx.caller.phone_state, ctx.caller.phone_buffer = \
                    E164, FieldState.CONFIRMED, ""
            plan = self.plan(ctx)
            if plan.goal in policy.ASK_GOALS:
                self.assertFalse(policy.holds(ctx, plan.goal),
                                 f"case {i}: asked {plan.goal} while holding it ({ctx.intent}, {ctx.caller}, {ctx.book})")
            for goal in policy.missing(ctx):
                if goal in policy.ASK_GOALS:
                    self.assertFalse(policy.holds(ctx, goal), f"case {i}: missing() lists held {goal}")


class LoopBreaker(PolicyCase):

    def asked(self, ctx, goal):
        ctx.pending = goal
        ctx.pending_params = {"_line": policy.GOAL_LINES[goal], "_rung": 1}

    def test_rungs_climb_with_misses(self):
        ctx = self.identified()
        ctx.book.service, ctx.book.branch = "Teeth Cleaning", "Indiranagar"
        self.asked(ctx, Goal.ASK_WHEN)
        shrug = Understanding(acts=[Act.UNCLEAR.value], raw_text="hmm")
        rungs, lines = [], []
        for _ in range(3):
            plan = self.plan(ctx, shrug)
            rungs.append(plan.rung)
            lines.append(plan.line)
            policy.note_turn(ctx, plan, shrug)
        self.assertEqual(rungs, [RUNG_REPHRASE, RUNG_CHOICES, RUNG_EXIT])
        self.assertEqual(len(set(lines[:2])), 2)

    def test_non_answer_jumps_to_choices(self):
        ctx = self.identified()
        ctx.book.service, ctx.book.branch = "Teeth Cleaning", "Indiranagar"
        self.asked(ctx, Goal.ASK_WHEN)
        busy = Understanding(acts=[Act.NON_ANSWER.value], raw_text="I've been really busy")
        plan = self.plan(ctx, busy)
        self.assertEqual(plan.goal, Goal.ASK_WHEN)
        self.assertEqual(plan.rung, RUNG_CHOICES)
        self.assertEqual(policy.rung_for(ctx, Goal.ASK_WHEN, busy), RUNG_CHOICES)

    def test_questions_corrections_and_other_details_are_not_misses(self):
        ctx = self.identified()
        self.asked(ctx, Goal.ASK_SERVICE)
        for u in (Understanding(acts=[Act.QUESTION.value], question="parking?"),
                  Understanding(acts=[Act.CORRECTION.value], correction=True),
                  Understanding(acts=[Act.INFO.value], date_phrase="tomorrow"),
                  Understanding(acts=[Act.ANSWER.value], intent=Intent.CANCEL)):
            self.assertFalse(policy._is_miss(ctx, Goal.ASK_SERVICE, u), u)
        self.assertTrue(policy._is_miss(ctx, Goal.ASK_SERVICE, Understanding(acts=[Act.UNCLEAR.value])))

    def test_note_turn_records_pending_and_counts(self):
        ctx = self.identified()
        plan = self.plan(ctx)
        policy.note_turn(ctx, plan, None)
        self.assertEqual(ctx.pending, plan.goal)
        self.assertEqual(ctx.stats(plan.goal).asked, 1)
        self.assertEqual(ctx.pending_params["_line"], plan.line)
        answer_only = GoalPlan(Goal.ANSWER_ONLY, "unknown", steer=False)
        policy.note_turn(ctx, answer_only, Understanding(acts=[Act.QUESTION.value]))
        self.assertEqual(ctx.pending, Goal.ANSWER_ONLY)    # a later "yes" agrees to nothing (Z1)

    def test_exits(self):
        ctx = self.identified()
        ctx.book.service = "Teeth Cleaning"
        self.assertIsNone(policy.take_exit(ctx, GoalPlan(Goal.ASK_BRANCH, "ask.branch")))
        self.assertTrue(ctx.book.branch_any)
        self.assertIsNone(policy.take_exit(ctx, GoalPlan(Goal.ASK_WHEN, "ask.when")))
        self.assertEqual(ctx.book.date_c.kind, "earliest")
        self.assertTrue(ctx.book.any_time)
        close = policy.take_exit(ctx, GoalPlan(Goal.CONFIRM_PHONE, "confirm.phone"))
        self.assertEqual((close.goal, close.line, close.closes_call), (Goal.CLOSE, "phone.failed", True))
        callback = policy.take_exit(ctx, GoalPlan(Goal.ASK_SERVICE, "ask.service"))
        self.assertEqual(callback.goal, Goal.CALLBACK_OFFER)
        self.assertEqual(ctx.callback_reason, Goal.ASK_SERVICE.value)

    def test_a_declined_proposal_steps_back_instead_of_taking_it(self):
        # "Shall I just go with whichever branch is earliest?" "No." -> list the branches again, never the exit.
        ctx = self.identified()
        ctx.book.service = "Braces"
        ctx.pending = Goal.ASK_BRANCH
        ctx.pending_params = {"_line": "ask.branch.choices", "_rung": RUNG_CHOICES}
        ctx.stats(Goal.ASK_BRANCH).misses = 2
        no = Understanding(acts=[Act.ANSWER.value], confirmation="no", raw_text="no")
        plan = self.plan(ctx, no)
        self.assertEqual((plan.goal, plan.rung), (Goal.ASK_BRANCH, RUNG_REPHRASE))
        self.assertEqual(plan.line, "ask.branch.rephrase")
        policy.note_turn(ctx, plan, no)
        self.assertFalse(ctx.book.branch_any)
        self.assertEqual(ctx.stats(Goal.ASK_BRANCH).misses, 1)

    def test_three_failed_read_backs_close_kindly(self):
        ctx = self.booking(name="Priya", name_state=FieldState.HEARD, phone_misses=3)
        plan = self.plan(ctx)
        self.assertEqual((plan.goal, plan.line), (Goal.CLOSE, "phone.failed"))


class Steering(PolicyCase):

    def question(self):
        return Understanding(acts=[Act.QUESTION.value], question="parking?", raw_text="is there parking?")

    def test_cadence_mid_workflow(self):
        ctx = self.identified()
        ctx.book.service, ctx.book.branch = "Teeth Cleaning", "Indiranagar"
        ctx.pending = Goal.ASK_WHEN
        steers = []
        for _ in range(5):
            u = self.question()
            plan = self.plan(ctx, u)
            self.assertEqual(plan.goal, Goal.ASK_WHEN)
            steers.append(plan.steer)
            policy.note_turn(ctx, plan, u)
        self.assertEqual(steers, [True, False, True, False, True])
        self.assertIsNone(ctx.book.date_c)

    def test_offer_help_limits(self):
        ctx = new_context()
        goals = []
        for _ in range(10):
            u = self.question()
            plan = self.plan(ctx, u)
            goals.append(plan.goal)
            policy.note_turn(ctx, plan, u)
        offers = [i for i, g in enumerate(goals) if g == Goal.OFFER_HELP]
        self.assertEqual(goals[0], Goal.ANSWER_ONLY)      # never pushed towards booking on the first answer
        self.assertEqual(len(offers), policy.MAX_OFFER_HELP)
        self.assertTrue(all(b - a >= 2 for a, b in zip(offers, offers[1:])), goals)
        self.assertTrue(all(g in (Goal.OFFER_HELP, Goal.ANSWER_ONLY) for g in goals))

    def test_no_steer_after_an_outcome(self):
        ctx = new_context()
        ctx.outcome = "booked"
        ctx.pending = Goal.ANYTHING_ELSE
        plan = self.plan(ctx, self.question())
        self.assertEqual(plan.goal, Goal.ANSWER_ONLY)
        self.assertFalse(plan.steer)


class Listening(PolicyCase):

    def test_listening_hint(self):
        ctx = self.booking(name="Priya", name_state=FieldState.HEARD)
        self.assertEqual(policy.listening_hint(ctx), {"expect": "open", "digits_so_far": 0})
        ctx.caller.phone_buffer = "9845"
        self.assertEqual(policy.listening_hint(ctx), {"expect": "phone", "digits_so_far": 4})
        ctx.caller.phone_buffer = ""
        for goal, expect in ((Goal.CONFIRM_PHONE, "yes_no"), (Goal.OFFER_SLOTS, "choice"),
                             (Goal.ASK_WHEN, "date"), (Goal.ASK_TIME, "time"), (Goal.ASK_NAME, "name")):
            ctx.pending, ctx.pending_params = goal, {"_line": policy.GOAL_LINES[goal]}
            self.assertEqual(policy.listening_hint(ctx)["expect"], expect, goal)
        ctx.pending, ctx.pending_params = Goal.ASK_NAME, {"_line": "ask.name.spell"}
        self.assertEqual(policy.listening_hint(ctx)["expect"], "spelling")
        ctx.closed_conversation = True
        self.assertEqual(policy.listening_hint(ctx)["expect"], "open")

    def test_expects_information(self):
        ctx = self.booking()
        ctx.pending = Goal.ASK_NAME
        self.assertTrue(policy.expects_information(ctx, "It's Priya Sharma"))
        self.assertFalse(policy.expects_information(ctx, "yes"))
        self.assertFalse(policy.expects_information(ctx, "mm-hmm"))
        self.assertFalse(policy.expects_information(ctx, "why do you need my name?"))
        ctx.pending = Goal.SUMMARY
        self.assertFalse(policy.expects_information(ctx, "yes please"))
        self.assertTrue(policy.expects_information(ctx, "no, the date should be Tuesday"))
        ctx.pending = Goal.GREET
        self.assertTrue(policy.expects_information(ctx, "I'd like to book a cleaning please"))
        self.assertFalse(policy.expects_information(ctx, "hello"))


if __name__ == "__main__":
    unittest.main()
