"""
Python's decision about what Emma's reply should achieve (docs/R2_DESIGN.md,
sections 4 and 9).

next_goal() is a pure function of the context (and this turn's
understanding, for the steer rule). It never asks for something the context
already holds (M2), and it is where the loop breaker and the steer-back
policy live:

Priority (BOOK): clarifications raised this turn -> name -> phone (read back)
-> max-3 / duplicate checks -> patient (someone else) -> age (pediatric) ->
service -> branch (offering it) -> when -> time of day / AM-PM -> offer ->
summary -> anything else. MANAGE: phone -> name -> appointment date ->
verify -> pick -> check / cancel / reschedule steps. Globals (emergency,
person, language, abuse...) override both (handlers.py).

The workflows (book.next_goal, manage.next_goal) own their action states and
the wording params for them (offers, summaries, outcomes). Identity (name,
phone) is shared by every workflow, so it is decided here, and so is the M2
guard: a workflow plan that asks for a detail the context already holds is
replaced by the first item of missing(), which by construction never
contains a filled detail.

Loop breaker, per goal (context.GoalStats):
- never the same sentence twice in a row (prompts.render + validate V7)
- rung 1 ask, rung 2 rephrase, rung 3 choices / spell / digit groups,
  rung 4 exit: take the default for optional details (branch: whichever is
  earliest; when: earliest available; time: any) or, for required ones,
  offer a callback (a task only if they want it) or close kindly
- a non-answer ("I've been really busy") jumps straight to the choices rung
- questions, corrections and intent switches are not misses

Steer-back (criterion 4, M6): the caller's question is always answered
first. After a pure question the pending ask is included on the 1st, 3rd,
5th... consecutive question turn (never twice running, always a different
wording); with no workflow under way an OFFER_HELP steer comes at most every
second answer, never twice in a row, at most three times a call.

Owner in Sprint 1b: E1.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Optional

import clock
import config
import prompts
from dateparse import DateConstraint
from dialogue import book, manage
from dialogue.context import (
    Act, CallContext, CLOSING_GOALS, Expect, FieldState, GOAL_SPECS, Goal, GoalPlan, Intent,
    MANAGE_INTENTS, RUNG_ASK, RUNG_CHOICES, RUNG_EXIT, Understanding,
)
from dialogue.runtime import safe_call

# The pre-written line for each goal (prompts.LINES ids). Workflows may hand
# back their own line (with params) for the goals they own.
GOAL_LINES = {
    Goal.GREET: "ask.intent", Goal.ASK_INTENT: "ask.intent", Goal.ANSWER_ONLY: "unknown",
    Goal.OFFER_HELP: "offer.help", Goal.CAPABILITY: "capability", Goal.ANYTHING_ELSE: "anything_else",
    Goal.CLOSE: "close", Goal.HOLD_ON: "hold_on", Goal.REPEAT: "repeat.prefix", Goal.GO_ON: "go_on",
    Goal.SILENCE: "silence.1", Goal.HELP_FIRST: "help_first", Goal.CALLBACK_OFFER: "callback.offer",
    Goal.CALLBACK_DONE: "callback.done", Goal.ENGLISH_ONLY: "english_only", Goal.ABUSE_WARN: "abuse.warn",
    Goal.ABUSE_CLOSE: "abuse.close", Goal.DONT_KEEP_ACK: "dont_keep.ack", Goal.RED_FLAG: "red_flag",
    Goal.CONFIRM_CHANGE: "confirm_change", Goal.ASK_NAME: "ask.name", Goal.SPELL_NAME: "ask.name.spell",
    Goal.ASK_PHONE: "ask.phone", Goal.PHONE_MORE: "phone.more", Goal.CONFIRM_PHONE: "confirm.phone",
    Goal.ASK_PATIENT: "ask.patient", Goal.ASK_AGE: "ask.age", Goal.ASK_SERVICE: "ask.service",
    Goal.CLARIFY_SERVICE: "clarify.service", Goal.ASK_BRANCH: "ask.branch", Goal.ASK_WHEN: "ask.when",
    Goal.ASK_TIME: "ask.time", Goal.RESOLVE_AMPM: "resolve.ampm", Goal.OFFER_SLOTS: "offer.two",
    Goal.NO_SLOTS: "no_slots", Goal.MAX_REACHED: "max_reached", Goal.DUPLICATE_CHECK: "duplicate",
    Goal.SUMMARY: "summary", Goal.SUMMARY_AGAIN: "summary.again", Goal.WHAT_TO_CHANGE: "what_to_change",
    Goal.BOOKED: "booked", Goal.DROPPED: "dropped", Goal.ASK_APPT_DATE: "ask.appt_date",
    Goal.VERIFY_FAILED: "verify.failed", Goal.PICK_APPOINTMENT: "pick.appointment",
    Goal.STATE_APPOINTMENT: "state.appointment", Goal.CONFIRM_CANCEL: "confirm.cancel",
    Goal.ASK_CANCEL_REASON: "ask.cancel_reason", Goal.CANCELLED: "cancelled", Goal.OFFER_REBOOK: "offer.rebook",
    Goal.ASK_NEW_WHEN: "ask.new_when", Goal.OFFER_NEW_SLOTS: "offer.two",
    Goal.CONFIRM_RESCHEDULE: "confirm.reschedule", Goal.RESCHEDULED: "rescheduled", Goal.TOO_LATE: "too_late",
}

# Loop-breaker ladders where the generic "<id>.rephrase" / "<id>.choices"
# naming doesn't fit. Index 0 is rung 1.
RUNG_LINES = {
    Goal.ASK_NAME: ("ask.name", "ask.name.rephrase", "ask.name.spell"),
    Goal.SPELL_NAME: ("ask.name.spell", "ask.name.spell", "ask.name.spell"),
    Goal.PHONE_MORE: ("phone.more", "ask.phone.choices", "ask.phone.choices"),
    Goal.ASK_TIME: ("ask.time", "ask.time.choices", "ask.time.choices"),
    Goal.ASK_APPT_DATE: ("ask.appt_date", "ask.appt_date.rephrase", "ask.appt_date.rephrase"),
    Goal.ASK_INTENT: ("ask.intent", "ask.intent", "capability"),
    Goal.SUMMARY: ("summary", "summary.again", "summary.again"),
}

# Goals that ask the caller for a detail, and the detail they fill (M2).
ASK_GOALS = frozenset({
    Goal.ASK_NAME, Goal.ASK_PHONE, Goal.PHONE_MORE, Goal.CONFIRM_PHONE, Goal.ASK_PATIENT, Goal.ASK_AGE,
    Goal.ASK_SERVICE, Goal.ASK_BRANCH, Goal.ASK_WHEN, Goal.ASK_TIME, Goal.ASK_APPT_DATE, Goal.ASK_NEW_WHEN,
    Goal.ASK_CANCEL_REASON,
})
# Workflow goals that go ahead of the name and phone questions: they react
# to what the caller just said, or report what just happened.
CLARIFY_FIRST = frozenset({
    Goal.CONFIRM_CHANGE, Goal.CLARIFY_SERVICE, Goal.RESOLVE_AMPM, Goal.SPELL_NAME, Goal.WHAT_TO_CHANGE,
    Goal.DROPPED, Goal.BOOKED, Goal.CANCELLED, Goal.RESCHEDULED, Goal.CALLBACK_OFFER, Goal.CALLBACK_DONE,
    Goal.SUMMARY_AGAIN,
})
# Statements of an outcome: said once, then "anything else?".
OUTCOME_GOALS = frozenset({
    Goal.BOOKED, Goal.CANCELLED, Goal.RESCHEDULED, Goal.STATE_APPOINTMENT, Goal.DROPPED, Goal.CALLBACK_DONE,
})
# The model's acknowledgement never precedes these (they speak for themselves).
NO_MODEL_SAY = frozenset({
    Goal.RED_FLAG, Goal.CLOSE, Goal.ABUSE_WARN, Goal.ABUSE_CLOSE, Goal.REPEAT, Goal.HOLD_ON, Goal.GO_ON,
    Goal.SILENCE, Goal.GREET,
})
# Exits that can't take a default: offer a callback instead (rung 4).
_CLOSE_ON_EXIT = frozenset({
    Goal.ASK_INTENT, Goal.ANYTHING_ELSE, Goal.CALLBACK_OFFER, Goal.OFFER_HELP, Goal.OFFER_REBOOK,
    Goal.CAPABILITY, Goal.ANSWER_ONLY, Goal.HELP_FIRST, Goal.ENGLISH_ONLY,
})

# Acts that mean the caller did something other than ignore the question.
_NOT_A_MISS = (
    Act.QUESTION, Act.CAPABILITY, Act.CHITCHAT, Act.CORRECTION, Act.WANTS_HUMAN, Act.ROBOT_QUESTION,
    Act.REPEAT, Act.WAIT, Act.END, Act.ABUSE, Act.OTHER_LANGUAGE, Act.DONT_KEEP, Act.FRAGMENT,
)
# Understanding attributes a goal's answer arrives in. Details outside this
# set mean the caller volunteered something else: progress, not a miss.
_GOAL_FIELDS = {
    Goal.ASK_NAME: ("name", "name_spelled"), Goal.SPELL_NAME: ("name", "name_spelled"),
    Goal.ASK_PHONE: ("phone_digits",), Goal.PHONE_MORE: ("phone_digits",),
    Goal.ASK_PATIENT: ("patient_name", "for_someone_else", "relation"), Goal.ASK_AGE: ("age",),
    Goal.ASK_SERVICE: ("service", "service_phrase"), Goal.CLARIFY_SERVICE: ("service", "service_phrase"),
    Goal.ASK_BRANCH: ("branch", "branch_any"), Goal.ASK_WHEN: ("date_phrase", "time_phrase"),
    Goal.ASK_TIME: ("time_phrase",), Goal.RESOLVE_AMPM: ("time_phrase",),
    Goal.ASK_APPT_DATE: ("appt_date_phrase", "date_phrase"), Goal.ASK_NEW_WHEN: ("date_phrase", "time_phrase"),
    Goal.OFFER_SLOTS: ("choice_index", "reject_options", "time_phrase", "date_phrase"),
    Goal.OFFER_NEW_SLOTS: ("choice_index", "reject_options", "time_phrase", "date_phrase"),
}
_DETAIL_FIELDS = (
    "name", "name_spelled", "phone_digits", "for_someone_else", "patient_name", "age", "service",
    "service_phrase", "branch", "branch_any", "doctor", "doctor_phrase", "doctor_gender", "date_phrase",
    "time_phrase", "choice_index", "reject_options", "appt_date_phrase", "cancel_reason",
)
_YES_NO_GOALS = frozenset(g for g, spec in GOAL_SPECS.items() if spec.expect == Expect.YES_NO)

MAX_OFFER_HELP = 3

# Rung lines that propose a default as a yes/no question ("Shall I just go
# with whichever branch has the earliest slot?"). A yes takes it (apply.py);
# a no is an answer, not a miss, and the ladder steps back to the rephrase,
# so the exit never takes the very default the caller just turned down.
PROPOSAL_LINES = {"ask.branch.choices": Goal.ASK_BRANCH}


# ---------------------------------------------------------------- the decision


def next_goal(ctx: CallContext, u: Optional[Understanding] = None, *, catalog=None,
              raised: tuple = ()) -> GoalPlan:
    """
    The goal, line id, params, rung and steer flag for this turn's reply.
    `catalog` (facts.Catalog) fills params such as the branches that offer a
    service; `raised` are goals apply.py raised this turn (DROPPED...).
    """
    if Goal.DROPPED in raised:
        return _plan(ctx, Goal.DROPPED, catalog, rung=RUNG_ASK)
    if ctx.change_proposal:
        return _plan(ctx, Goal.CONFIRM_CHANGE, catalog, u=u)
    if u is not None and u.has(Act.CAPABILITY) and u.pure_question:
        return _capability(ctx, u)
    if not ctx.workflow_active():
        return _conversation(ctx, u, catalog)

    plan = _workflow_plan(ctx, u, catalog)
    if u is not None and u.pure_question and plan.goal not in OUTCOME_GOALS:
        plan.steer = should_steer(ctx, u)
    return plan


def _workflow_plan(ctx: CallContext, u: Optional[Understanding], catalog) -> GoalPlan:
    wf = _workflow_next(ctx)
    if wf is not None and wf.goal == Goal.CALLBACK_OFFER and ctx.outcome == "callback" \
            and ctx.pending != Goal.CALLBACK_DONE:
        # A callback is already arranged on this call: offering another would be
        # the same question on a loop. Say the team will sort it out, once.
        wf = GoalPlan(Goal.CALLBACK_DONE, "callback.already")
    if wf is not None and wf.goal in OUTCOME_GOALS and ctx.pending == wf.goal:
        wf = None                                       # the outcome was said: move on
        if not missing(ctx):
            return _plan(ctx, Goal.ANYTHING_ELSE, catalog, u=u)
    # Barge-in during the summary: finish it and ask again (recap-heard rule).
    if wf is not None and wf.goal == Goal.SUMMARY and not ctx.last_reply_heard \
            and ctx.pending in (Goal.SUMMARY, Goal.SUMMARY_AGAIN):
        return _from_workflow(ctx, GoalPlan(Goal.SUMMARY_AGAIN, "summary.again",
                                            dict(wf.params, **_summary_again_params(ctx))), u)
    # Clarifications the workflow raised come first; its other action states
    # (offers, summary, checks) only once name and phone are settled (D2).
    if wf is not None and wf.goal in CLARIFY_FIRST:
        return _from_workflow(ctx, wf, u)
    ident = _identity_goal(ctx)
    if ident is not None:
        return _identity_plan(ctx, ident, u, catalog)
    if wf is not None and not holds(ctx, wf.goal):
        return _from_workflow(ctx, wf, u)
    todo = missing(ctx)
    for goal in todo:
        if goal in ASK_GOALS or goal in (Goal.CLARIFY_SERVICE, Goal.RESOLVE_AMPM, Goal.SPELL_NAME):
            return _plan(ctx, goal, catalog, u=u)
        break                                           # an action state with no workflow plan
    if wf is not None and wf.goal == Goal.ANYTHING_ELSE:
        return _from_workflow(ctx, wf, u)
    return _plan(ctx, Goal.ANYTHING_ELSE, catalog, u=u)


def _workflow_next(ctx: CallContext) -> Optional[GoalPlan]:
    """The workflow's own next goal, or None (done, or the workflow is unavailable)."""
    module = book if ctx.intent == Intent.BOOK else manage
    plan = safe_call(module.next_goal, ctx)
    return plan if isinstance(plan, GoalPlan) else None


def _from_workflow(ctx: CallContext, wf: GoalPlan, u: Optional[Understanding]) -> GoalPlan:
    """A workflow's plan with the loop breaker applied (rung, rephrased line) and the spec flags."""
    rung = rung_for(ctx, wf.goal, u)
    line = wf.line or GOAL_LINES.get(wf.goal, "ask.intent")
    if rung > RUNG_ASK:
        line = _rung_line(wf.goal, rung, base=line)
    spec = GOAL_SPECS[wf.goal]
    return GoalPlan(goal=wf.goal, line=line, params=dict(wf.params), rung=rung,
                    critical=wf.critical or spec.critical,
                    use_model_say=wf.use_model_say and wf.goal not in NO_MODEL_SAY,
                    steer=wf.steer, expect=spec.expect,
                    closes_call=wf.closes_call or wf.goal in CLOSING_GOALS)


def _identity_goal(ctx: CallContext) -> Optional[Goal]:
    """Name and phone: shared by every workflow, asked once (BOOK: name first; MANAGE: phone first)."""
    c = ctx.caller
    name_goal = None
    if ctx.intent == Intent.BOOK:
        if not c.name:
            name_goal = Goal.ASK_NAME
        elif c.name_state == FieldState.HEARD and c.name_misses in (1, 2):
            name_goal = Goal.SPELL_NAME
    if name_goal is not None:
        return name_goal
    if c.phone_state == FieldState.CONFIRMED:
        return None
    if c.phone_state == FieldState.PENDING and c.phone_e164:
        return Goal.CONFIRM_PHONE
    return Goal.PHONE_MORE if c.phone_buffer else Goal.ASK_PHONE


def _identity_plan(ctx: CallContext, goal: Goal, u, catalog) -> GoalPlan:
    c = ctx.caller
    if goal in (Goal.ASK_PHONE, Goal.CONFIRM_PHONE, Goal.PHONE_MORE) and c.phone_misses >= 3:
        return GoalPlan(Goal.CLOSE, "phone.failed", critical=True, use_model_say=False,
                        closes_call=True, expect=Expect.OPEN)
    plan = _plan(ctx, goal, catalog, u=u)
    manage_flow = ctx.intent in MANAGE_INTENTS
    if goal == Goal.ASK_PHONE and manage_flow and u is not None and u.has(Act.NON_ANSWER) \
            and ctx.pending in (Goal.ASK_PHONE, Goal.PHONE_MORE, Goal.CONFIRM_PHONE):
        # "I don't know the number, I only know the name" (8 Oct): say why it's needed
        # rather than "a few digits at a time" on a loop.
        plan.line = "ask.phone.why"
        return plan
    if goal == Goal.ASK_PHONE and plan.rung == RUNG_ASK:
        if ctx.pending == Goal.CONFIRM_PHONE:
            plan.line = "confirm.phone.retry"           # the read-back was wrong, no new digits
        elif manage_flow:
            plan.line = "ask.phone.manage"
    return plan


def _conversation(ctx: CallContext, u: Optional[Understanding], catalog) -> GoalPlan:
    """No workflow under way: answer, steer occasionally, or ask what they need."""
    done = ctx.outcome is not None or ctx.pending in OUTCOME_GOALS or ctx.pending == Goal.ANYTHING_ELSE
    if u is not None and u.pure_question and u.has(Act.QUESTION):
        if not done and should_steer(ctx, u):
            return _plan(ctx, Goal.OFFER_HELP, catalog, rung=RUNG_ASK)
        plan = _plan(ctx, Goal.ANSWER_ONLY, catalog, rung=RUNG_ASK)
        plan.steer = False
        return plan
    if done:
        return _plan(ctx, Goal.ANYTHING_ELSE, catalog, u=u)
    return _plan(ctx, Goal.ASK_INTENT, catalog, u=u)


def _capability(ctx: CallContext, u: Understanding) -> GoalPlan:
    """ "How can you help?" is answered for what it is; it never pulls the caller into booking (feedback 7)."""
    who = bool(re.search(r"\bwho (are|is) (you|this)\b", (u.raw_text or "").lower()))
    return GoalPlan(Goal.CAPABILITY, "capability.who" if who else "capability", rung=RUNG_ASK,
                    critical=False, use_model_say=True, steer=True, expect=Expect.OPEN)


# ---------------------------------------------------------------- plans and params


def _plan(ctx: CallContext, goal: Goal, catalog=None, *, u: Optional[Understanding] = None,
          rung: Optional[int] = None) -> GoalPlan:
    """A plan built here (not by a workflow): line by rung, params from the context."""
    if rung is None:
        rung = rung_for(ctx, goal, u)
    spec = GOAL_SPECS[goal]
    return GoalPlan(goal=goal, line=_rung_line(goal, rung), params=_params(ctx, goal, catalog),
                    rung=rung, critical=spec.critical, use_model_say=goal not in NO_MODEL_SAY,
                    steer=True, expect=spec.expect, closes_call=goal in CLOSING_GOALS)


def _rung_line(goal: Goal, rung: int, base: Optional[str] = None) -> str:
    """The line for a rung: separate ids, so a re-ask is a genuinely different sentence."""
    index = max(0, min(rung, RUNG_CHOICES) - 1)
    if goal in RUNG_LINES and (base is None or base == RUNG_LINES[goal][0]):
        return RUNG_LINES[goal][index]
    base = base or GOAL_LINES.get(goal, "ask.intent")
    if index == 0:
        return base
    for suffix in ((".rephrase",) if index == 1 else (".choices", ".rephrase")):
        if base + suffix in prompts.LINES:
            return base + suffix
    return base


def _params(ctx: CallContext, goal: Goal, catalog) -> dict:
    """Params for the pre-written lines this module builds itself."""
    b, c = ctx.book, ctx.caller
    if goal == Goal.CONFIRM_PHONE:
        return {"phone": speak_phone(c.phone_e164)}
    if goal == Goal.CONFIRM_CHANGE:
        p = ctx.change_proposal
        return {"field": _FIELD_WORDS.get(p.get("field"), p.get("field", "details")),
                "value": p.get("spoken") or str(p.get("value", ""))}
    if goal == Goal.ASK_PATIENT:
        return {"relation_or_their": f"your {b.relation}'s" if b.relation else "their"}
    if goal == Goal.ASK_AGE:
        return {"patient": b.patient_name or (f"your {b.relation}" if b.relation else "the patient")}
    if goal == Goal.CLARIFY_SERVICE:
        spoken = [safe_call(prompts.speak_service, o, default=o) for o in b.service_options]
        return {"options": safe_call(prompts.speak_list, spoken, default=" or ".join(spoken))}
    if goal == Goal.ASK_BRANCH:
        branches = list(catalog.branches_offering(b.service)) if catalog is not None and b.service else []
        if not branches and catalog is not None:
            branches = [br.name for br in catalog.branches]
        service = safe_call(prompts.speak_service, b.service or "", default=b.service or "That")
        listed = safe_call(prompts.speak_list, branches, default=" or ".join(branches))
        return {"service": (service[:1].upper() + service[1:]) if service else "That", "branches": listed}
    if goal == Goal.ASK_TIME:
        day = "that day"
        if b.date_c is not None and b.date_c.exact:
            day = safe_call(prompts.speak_day, b.date_c.start, default=b.date_c.start.strftime("%A"))
        return {"day": day}
    if goal == Goal.RESOLVE_AMPM:
        hour = "7"
        if b.time_c is not None and b.time_c.candidates:
            h = b.time_c.candidates[0].hour % 12 or 12
            hour = str(h)
        return {"hour": hour}
    if goal == Goal.SUMMARY_AGAIN:
        return _summary_again_params(ctx)
    if goal == Goal.CALLBACK_DONE:
        return {"phone": speak_phone(c.phone_e164)}
    return {}


_FIELD_WORDS = {"date": "date", "time": "time", "service": "visit", "branch": "branch",
                "name": "name", "phone": "number", "doctor": "doctor"}


def _summary_again_params(ctx: CallContext) -> dict:
    slot = ctx.book.chosen
    if slot is None:
        return {"when": "", "branch": ""}
    when = slot.spoken or safe_call(prompts.speak_slot, slot.start, default=slot.start.strftime("%A at %H:%M"))
    return {"when": when, "branch": slot.branch}


def speak_phone(e164: Optional[str]) -> str:
    """Grouped digits for a read-back; plain digits if the speakable helper is unavailable."""
    if not e164:
        return ""
    return safe_call(prompts.speak_phone, e164, default=" ".join(e164[-10:]))


# ---------------------------------------------------------------- the checklist (M1, M2)


def holds(ctx: CallContext, goal: Goal) -> bool:
    """True when the context already has the detail `goal` would ask for (the M2 rule)."""
    b, c, m = ctx.book, ctx.caller, ctx.manage
    if goal == Goal.ASK_NAME:
        return bool(m.patient_name if ctx.intent in MANAGE_INTENTS else c.name)
    if goal in (Goal.ASK_PHONE, Goal.PHONE_MORE):
        return c.phone_state in (FieldState.CONFIRMED, FieldState.PENDING) and bool(c.phone_e164)
    if goal == Goal.CONFIRM_PHONE:
        return c.phone_state == FieldState.CONFIRMED
    if goal == Goal.ASK_PATIENT:
        return not b.for_someone_else or bool(b.patient_name)
    if goal == Goal.ASK_AGE:
        return b.age is not None
    if goal == Goal.ASK_SERVICE:
        return bool(b.service)
    if goal == Goal.ASK_BRANCH:
        return bool(b.branch) or b.branch_any
    if goal == Goal.ASK_WHEN:
        return b.date_c is not None
    if goal == Goal.ASK_TIME:
        return b.any_time or (b.time_c is not None and b.time_c.kind != "ambiguous")
    if goal == Goal.ASK_APPT_DATE:
        return m.appt_date is not None
    if goal == Goal.ASK_NEW_WHEN:
        return m.new_date_c is not None
    if goal == Goal.ASK_CANCEL_REASON:
        return bool(m.cancel_reason) or m.reason_asked
    return False


def missing(ctx: CallContext) -> list:
    """The current workflow's unfilled checklist items as Goals, in priority order (brief + harness M1)."""
    b, c, m = ctx.book, ctx.caller, ctx.manage
    todo: list = []
    phone = None
    if c.phone_state != FieldState.CONFIRMED:
        if c.phone_state == FieldState.PENDING and c.phone_e164:
            phone = Goal.CONFIRM_PHONE
        else:
            phone = Goal.PHONE_MORE if c.phone_buffer else Goal.ASK_PHONE
    if ctx.intent == Intent.BOOK:
        if not c.name:
            todo.append(Goal.ASK_NAME)
        elif c.name_state == FieldState.HEARD and c.name_misses in (1, 2):
            todo.append(Goal.SPELL_NAME)
        if phone:
            todo.append(phone)
        if b.for_someone_else and not b.patient_name:
            todo.append(Goal.ASK_PATIENT)
        if b.service and "pediatric" in b.service.lower() and b.age is None:
            todo.append(Goal.ASK_AGE)
        if not b.service:
            todo.append(Goal.CLARIFY_SERVICE if b.service_options else Goal.ASK_SERVICE)
        if not b.branch and not b.branch_any:
            todo.append(Goal.ASK_BRANCH)
        if b.date_c is None:
            todo.append(Goal.ASK_WHEN)
        if b.time_c is not None and b.time_c.kind == "ambiguous":
            todo.append(Goal.RESOLVE_AMPM)
        elif b.date_c is not None and b.time_c is None and not b.any_time:
            todo.append(Goal.ASK_TIME)
        if b.chosen is None:
            todo.append(Goal.OFFER_SLOTS)
        if not b.appointment_id:
            todo.append(Goal.SUMMARY)
    elif ctx.intent in MANAGE_INTENTS:
        if phone:
            todo.append(phone)
        if not m.patient_name:
            todo.append(Goal.ASK_NAME)
        if m.appt_date is None:
            todo.append(Goal.ASK_APPT_DATE)
        if not m.done and not m.verified:
            if m.verify_attempts:
                todo.append(Goal.VERIFY_FAILED)         # verification itself is an action, not a question
        elif not m.done:
            if len(m.matches) > 1 and m.target is None:
                todo.append(Goal.PICK_APPOINTMENT)
            if m.action == Intent.CANCEL or ctx.intent == Intent.CANCEL:
                todo.append(Goal.CONFIRM_CANCEL)
            elif m.action == Intent.RESCHEDULE or ctx.intent == Intent.RESCHEDULE:
                if m.new_date_c is None:
                    todo.append(Goal.ASK_NEW_WHEN)
                if m.chosen is None:
                    todo.append(Goal.OFFER_NEW_SLOTS)
                todo.append(Goal.CONFIRM_RESCHEDULE)
            else:
                todo.append(Goal.STATE_APPOINTMENT)
    return todo


def plan_hint(ctx: CallContext, catalog=None) -> GoalPlan:
    """The goal before hearing the caller (for the brief): ctx.pending if still open, else the next missing item."""
    plan = safe_call(next_goal, ctx, None, catalog=catalog)
    if isinstance(plan, GoalPlan):
        return plan
    goal = ctx.pending or Goal.ASK_INTENT
    return GoalPlan(goal, GOAL_LINES.get(goal, "ask.intent"), expect=GOAL_SPECS[goal].expect)


# ---------------------------------------------------------------- loop breaker


def _is_miss(ctx: CallContext, goal: Goal, u: Optional[Understanding]) -> bool:
    """Emma asked `goal`, and this reply neither filled it nor did anything else that counts."""
    if u is None or ctx.pending != goal:
        return False
    if any(u.has(a) for a in _NOT_A_MISS) or u.correction:
        return False
    if u.intent in (Intent.BOOK, *MANAGE_INTENTS) and u.intent != ctx.intent:
        return False                                    # an intent switch
    given = {f for f in _DETAIL_FIELDS if getattr(u, f, None) not in (None, "", False)}
    own = set(_GOAL_FIELDS.get(goal, ()))
    if goal in (Goal.ASK_PHONE, Goal.PHONE_MORE) and "phone_digits" in given:
        return False                                    # digits are progress, even a partial number
    if given - own and _moved(ctx, u):
        return False                                    # volunteered something else: progress
    if goal in _YES_NO_GOALS and u.confirmation in ("yes", "no"):
        return False
    if _proposed(ctx, goal) and u.confirmation in ("yes", "no"):
        return False
    return True


def progress_mark(ctx: CallContext) -> tuple:
    """
    Everything a turn can fill or change, as one comparable value. The engine
    records it before the turn is applied, so a reply that only repeats what
    Emma already knows ("I just want to book a cleaning" for the third time)
    is not mistaken for progress and the loop breaker still moves on.
    """
    b, c, m = ctx.book, ctx.caller, ctx.manage
    return (ctx.intent, b.version, len(b.offered), c.name, c.name_state, c.phone_e164, c.phone_state,
            c.phone_buffer, m.verified, m.appt_date, m.patient_name, m.new_date_c, m.new_time_c,
            len(m.offered), m.chosen is not None, m.cancel_reason)


def _moved(ctx: CallContext, u: Understanding) -> bool:
    """Did this turn change anything? True when the engine recorded no mark (tests calling note_turn directly)."""
    mark = u.__dict__.get("_progress_mark")
    if mark is None:
        return True
    return safe_call(progress_mark, ctx, default=None) != mark


def _proposed(ctx: CallContext, goal: Goal) -> bool:
    """Emma's last line for `goal` proposed a default as a yes/no question."""
    return PROPOSAL_LINES.get(str(ctx.pending_params.get("_line", ""))) == goal and ctx.pending == goal


def _declined(ctx: CallContext, goal: Goal, u: Optional[Understanding]) -> bool:
    return u is not None and u.confirmation == "no" and _proposed(ctx, goal)


def _bump(misses: int, u: Optional[Understanding]) -> int:
    """One more miss; a non-answer lands on the choices rung at once (criterion 6)."""
    if u is not None and u.has(Act.NON_ANSWER):
        return max(misses + 1, RUNG_CHOICES - 1)
    return misses + 1


def rung_for(ctx: CallContext, goal: Goal, u: Optional[Understanding] = None) -> int:
    """1-4 from the goal's misses (counting this turn's); a non-answer means at least RUNG_CHOICES."""
    stats = ctx.goal_stats.get(goal.value)
    misses = stats.misses if stats else 0
    if _declined(ctx, goal, u):
        misses = RUNG_CHOICES - 2                       # back to the rephrase, which lists the options
    elif _is_miss(ctx, goal, u):
        misses = _bump(misses, u)
    rung = min(RUNG_EXIT, RUNG_ASK + misses)
    if u is not None and u.has(Act.NON_ANSWER) and ctx.pending == goal:
        rung = max(rung, RUNG_CHOICES)               # a non-answer to this question, not to an earlier one
    return rung


def take_exit(ctx: CallContext, plan: GoalPlan) -> Optional[GoalPlan]:
    """
    Rung 4 for `plan.goal`. Optional details take their default and None is
    returned (the engine re-runs the workflow and asks for the next goal);
    required ones get a callback offer, or a kind close when there is no
    number to call back. Resets the goal's misses, so the ladder restarts if
    it ever comes back.
    """
    goal = plan.goal
    ctx.stats(goal).misses = 0
    b = ctx.book
    if goal == Goal.ASK_BRANCH:
        b.branch_any = True
        b.touch()
        return None
    if goal in (Goal.ASK_WHEN, Goal.NO_SLOTS) and ctx.intent == Intent.BOOK:
        today = clock.today()
        b.date_c = DateConstraint(today, today + timedelta(days=config.BOOKING_HORIZON_DAYS), "earliest")
        b.any_time = True
        b.touch()
        return None
    if goal in (Goal.ASK_TIME, Goal.RESOLVE_AMPM):
        if ctx.intent == Intent.RESCHEDULE:
            ctx.manage.new_time_c = None
        else:
            b.time_c = None
            b.any_time = True
            b.touch()
        return None
    if goal == Goal.SPELL_NAME:
        ctx.caller.name_state = FieldState.UNVERIFIED  # kept, flagged for staff (invariant 8)
        return None
    if goal == Goal.ASK_CANCEL_REASON:
        ctx.manage.reason_asked = True
        return None
    if goal in (Goal.ASK_PHONE, Goal.PHONE_MORE, Goal.CONFIRM_PHONE) and not (
            ctx.intent in MANAGE_INTENTS and ctx.line_e164 and ctx.caller.phone_misses < 3):
        return GoalPlan(Goal.CLOSE, "phone.failed", rung=RUNG_EXIT, critical=True, use_model_say=False,
                        closes_call=True, expect=Expect.OPEN)
    # A phone call where they can't give the number the booking is under (8 Oct: "I only
    # know the name"): the team can find it by name and call back, rather than hanging up.
    if goal in _CLOSE_ON_EXIT or goal in CLOSING_GOALS:
        return GoalPlan(Goal.CLOSE, "close", rung=RUNG_EXIT, critical=True, use_model_say=False,
                        closes_call=True, expect=Expect.OPEN)
    ctx.callback_reason = goal.value
    return GoalPlan(Goal.CALLBACK_OFFER, "callback.offer", rung=RUNG_ASK, critical=False,
                    use_model_say=True, steer=True, expect=Expect.YES_NO)


def note_turn(ctx: CallContext, plan: GoalPlan, u: Optional[Understanding]) -> None:
    """
    Update the loop-breaker counters after a turn: a miss when Emma had
    asked ctx.pending, the caller's reply did not fill it, and it was not a
    question, correction, switch or global; then record plan as the new
    pending goal (asked += 1, last_turn).
    """
    previous = ctx.pending
    if previous is not None and _declined(ctx, previous, u):
        ctx.stats(previous).misses = RUNG_CHOICES - 2
    elif previous is not None and plan.goal == previous and _is_miss(ctx, previous, u):
        stats = ctx.stats(previous)
        stats.misses = _bump(stats.misses, u)

    pure = u is not None and u.pure_question
    ctx.question_streak = ctx.question_streak + 1 if pure else 0
    if plan.goal == Goal.OFFER_HELP:
        ctx.offers_made += 1
        ctx.answers_since_offer = 0
    elif plan.goal == Goal.ANSWER_ONLY and not ctx.workflow_active():
        ctx.answers_since_offer += 1                    # a plain answer (the capability menu isn't one)

    if plan.steer:
        stats = ctx.stats(plan.goal)
        stats.asked += 1
        stats.last_turn = ctx.turn
        ctx.pending = plan.goal
        ctx.pending_params = {**plan.params, "_line": plan.line, "_rung": plan.rung}
    else:
        # She answered and asked nothing: a "yes" next turn must not be taken
        # as agreeing to a question she didn't repeat (Z1).
        ctx.pending = Goal.ANSWER_ONLY
        ctx.pending_params = {"_line": plan.line, "_rung": plan.rung}
    if plan.goal in (Goal.SUMMARY, Goal.SUMMARY_AGAIN) and plan.steer:
        ctx.book.summary_version = ctx.book.version
        ctx.book.summary_heard = True               # call_session's last_reply_heard says if it played


def should_steer(ctx: CallContext, u: Understanding) -> bool:
    """The steer-back rule above, for a pure-question turn."""
    if ctx.workflow_active():
        return ctx.question_streak % 2 == 0         # 1st, 3rd, 5th... consecutive question
    if ctx.offers_made >= MAX_OFFER_HELP or ctx.pending == Goal.OFFER_HELP:
        return False
    # Every second answer at most: never on the first one, so a caller who
    # only rang to ask something isn't pushed towards booking straight away.
    return ctx.answers_since_offer >= 1


# ---------------------------------------------------------------- listening side


def listening_hint(ctx: CallContext) -> dict:
    """
    {"expect": one of Expect values, "digits_so_far": int} from the pending
    goal (GOAL_SPECS expect) and the phone buffer. Backs
    ai_engine.listening_hint for the turn detector.
    """
    digits = len(ctx.caller.phone_buffer or "")
    if ctx.closed_conversation:
        return {"expect": Expect.OPEN.value, "digits_so_far": 0}
    if digits:
        return {"expect": Expect.PHONE.value, "digits_so_far": digits}
    goal = ctx.pending
    if goal is None:
        return {"expect": Expect.OPEN.value, "digits_so_far": 0}
    line = str(ctx.pending_params.get("_line", ""))
    if line.endswith(".spell"):
        expect = Expect.SPELLING
    elif line == "ask.phone.choices":
        expect = Expect.PHONE
    else:
        expect = GOAL_SPECS[goal].expect
    return {"expect": expect.value, "digits_so_far": 0}


_WORD_RE = re.compile(r"[a-z0-9']+")
_YES_NO_WORDS = frozenset({
    "yes", "yeah", "yep", "yup", "ok", "okay", "sure", "correct", "right", "no", "nope", "nah", "fine",
    "please", "haan", "ji", "absolutely", "definitely", "go", "ahead", "that's", "thats", "it", "is",
})
_BACKCHANNEL_WORDS = frozenset({"mm", "mhm", "hmm", "mm-hmm", "uh", "huh", "um", "ok", "okay", "yeah", "right"})
_QUESTION_RE = re.compile(
    r"\?|\b(what|where|which|who|why|how|do you|does|can you|could you|is there|are you|price|cost|fee)\b")


def expects_information(ctx: CallContext, text: str) -> bool:
    """
    The typing beat (R6): the caller just gave something to write down: an
    answer to a name / number / service / date / patient / reason question,
    a first description of what they need, or a correction at the summary.
    Not a bare yes/no, a backchannel, or a question.
    """
    lowered = (text or "").lower()
    words = _WORD_RE.findall(lowered)
    if not words or ctx.closed_conversation:
        return False
    from dialogue import match
    if safe_call(match.looks_like_question, text, default=bool(_QUESTION_RE.search(lowered))):
        return False
    confirmation = safe_call(match.parse_yes_no, text, default=None)
    if confirmation is None and set(words) <= _YES_NO_WORDS:
        confirmation = "yes"                            # matcher unavailable: a bare yes/okay
    if len(words) <= 3 and confirmation:
        return False
    if set(words) <= _BACKCHANNEL_WORDS:
        return False
    goal = ctx.pending
    if goal is None or goal in (Goal.GREET, Goal.ASK_INTENT, Goal.ANYTHING_ELSE, Goal.CAPABILITY):
        return len(words) >= 4                          # explaining what they need
    expect = GOAL_SPECS[goal].expect
    if expect in (Expect.NAME, Expect.PHONE, Expect.DATE, Expect.TIME, Expect.SPELLING):
        return True
    if goal in (Goal.ASK_SERVICE, Goal.CLARIFY_SERVICE, Goal.ASK_BRANCH, Goal.ASK_AGE, Goal.ASK_PATIENT,
                Goal.ASK_CANCEL_REASON, Goal.WHAT_TO_CHANGE, Goal.HELP_FIRST):
        return True
    if goal in (Goal.SUMMARY, Goal.SUMMARY_AGAIN, Goal.CONFIRM_PHONE):
        return confirmation == "no" and len(words) >= 4  # "no, the number is ..."
    return False
