"""
Global handlers: every turn, before the workflows (docs/R2_DESIGN.md,
section 10.3; plan 5.6).

In order:
  1. red flag (breathing / swallowing trouble, swelling spreading to the eye
     or neck, uncontrolled bleeding, jaw injury): 108 / ER advice, red_flag
     task, end the call. Checked on the raw text too (red_flag_words) before
     any model call, so it never waits on Gemini.
  2. urgent (severe pain, swelling, bleeding, broken or knocked-out tooth):
     "urgent.ack", ctx.emergency = URGENT, BOOK with the emergency lead time,
     earliest same-day slot, emergency task at commit (no same-day slot ->
     earliest tomorrow + task flagged).
  3. terminal / meta acts: abuse (one warning, then close), end ("that's all,
     bye" -> close), repeat (re-speak ctx.last_emma), wait ("take your time"),
     fragment (stash in ctx.fragment, "go on"), silence (ladder of 3).
  4. notices that let the turn carry on: sincere robot question ->
     config.HONEST_LINE then straight back to the task (never unprompted);
     don't-keep request -> ctx.keep_transcript = False + ack.
  5. person request: first time "help_first" (offer to help); if they insist
     -> callback: confirm a number (known confirmed phone, else ask), create
     the callback task, then tell them (invariant 7: the task exists before
     the promise).
  6. other language: gently English only, offer a call back from the Kannada /
     Hindi-speaking team (language task only if they want it). Hinglish
     (Hindi words inside English) is out of scope and is treated as English.
  7. answers to global offers: callback yes / no, anything-else no -> close,
     offer-help yes -> BOOK.

A callback the caller accepted is "in progress" while ctx.callback_reason
starts with ACCEPTED (policy.py also writes a reason when it only offers
one). The number is collected like any phone: apply.py reads the digits and
the yes / no to the read-back (run from here, so the workflow doesn't act on
that turn), callback_goal() asks or reads back, and the confirmed number
creates the task here, before CALLBACK_DONE is planned. If the task can't
be written nothing is promised (Emma offers to help herself instead).

Handler lines are statements said as they are (use_model_say=False). REPEAT,
HOLD_ON and GO_ON leave Emma's question open: the engine re-speaks
ctx.last_emma for REPEAT, and the caller's next words answer whatever was
pending before.

Tasks go through tasks.py on the database thread, imported with
runtime.optional_module, so a missing module degrades to "no task, no
promise" instead of crashing a call.

Owner in Sprint 1b: E5.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import prompts
import scheduling
from dateparse import DateConstraint
from dialogue.context import (
    MANAGE_INTENTS, Act, CallContext, Emergency, FieldState, Goal, GoalPlan, Intent, Notice, Understanding,
)
from dialogue import match
from dialogue.manage import phone_plan, plan
from dialogue.runtime import Runtime, optional_module

logger = logging.getLogger(__name__)


@dataclass
class GlobalOutcome:
    """What a global handler decided. plan overrides the workflow's goal; carry_on lets apply/workflows run too."""
    plan: Optional[GoalPlan] = None
    notices: list = field(default_factory=list)
    carry_on: bool = True            # False: skip apply/workflows this turn (red flag, abuse close, repeat...)
    closes_call: bool = False


# An urgent caller is booked for this unless they asked for something else.
URGENT_SERVICE = "Consultation"
# Task notes (staff read them on the dashboard).
LANGUAGE_NOTE = "Wants a call back in Kannada or Hindi"
PERSON_NOTE = "Asked to speak to someone at the clinic"
# A run of fragments longer than this stops being stashed and goes through as
# a turn, so a caller who trails off repeatedly is never stuck on "go on".
FRAGMENT_MAX_WORDS = 14

# Goals whose yes / no is an answer to a callback offer.
_OFFER_GOALS = (Goal.CALLBACK_OFFER, Goal.TOO_LATE, Goal.ENGLISH_ONLY)
# Goals that ended with "anything else?": a plain no closes the call.
_ANYTHING_ELSE_GOALS = (Goal.ANYTHING_ELSE, Goal.BOOKED, Goal.STATE_APPOINTMENT, Goal.RESCHEDULED,
                        Goal.CALLBACK_DONE)
# A yes to these commits a change, so "yes, thanks, bye" must not close first.
_COMMIT_GOALS = (Goal.SUMMARY, Goal.SUMMARY_AGAIN, Goal.CONFIRM_CANCEL, Goal.CONFIRM_RESCHEDULE)


# ---------------------------------------------------------------- deterministic screens

# A phrase counts only when no negation sits just before it ("no trouble breathing").
_NEGATION = re.compile(r"\b(?:no|not|never|without|isn'?t|aren'?t|don'?t|doesn'?t|didn'?t|wasn'?t)\b"
                       r"(?:\W+\w+){0,2}\W*$")

_RED_FLAG = [re.compile(p) for p in (
    # breathing
    r"\b(?:can'?t|cannot|can not|unable to|couldn'?t|struggling to|hard to|difficult to)\s+breathe?\b",
    r"\b(?:trouble|difficulty|problem|problems|hard time)\s+(?:in\s+)?breathing\b",
    r"\bshort(?:ness)?\s+of\s+breath\b",
    r"\b(?:breathless|not able to breathe|choking)\b",
    # swallowing
    r"\b(?:can'?t|cannot|can not|unable to|couldn'?t|hard to|difficult to)\s+swallow\b",
    r"\b(?:trouble|difficulty|problem|problems|pain)\s+(?:in\s+)?swallowing\b",
    # swelling spreading to the eye or neck, or spreading at all
    r"\b(?:swell(?:ing|ed|s)?|swollen)\b.{0,40}\b(?:eye|eyes|neck|throat|spreading)\b",
    r"\b(?:eye|eyes|neck|throat)\b.{0,30}\b(?:swell(?:ing|ed|s)?|swollen)\b",
    r"\bswelling\s+(?:is\s+)?spreading\b",
    # uncontrolled bleeding
    r"\b(?:won'?t|will not|doesn'?t|does not|not)\s+stop(?:ping)?\s+bleeding\b",
    r"\bbleeding\b.{0,25}\b(?:won'?t|will not|doesn'?t|does not|not)\s+stop\b",
    r"\b(?:can'?t|cannot)\s+stop\s+the\s+bleeding\b",
    r"\b(?:bleeding\s+(?:heavily|a lot|badly|non-?stop)|heavy\s+bleeding|lot of blood|so much blood)\b",
    # jaw injury
    r"\b(?:broke|broken|fractured?|dislocated?|injured)\s+(?:my\s+|his\s+|her\s+)?jaw\b",
    r"\bjaw\b.{0,20}\b(?:broken|fractured|dislocated|injured)\b",
    r"\b(?:can'?t|cannot)\s+(?:open|close)\s+(?:my|his|her)\s+mouth\b",
    # infection spreading
    r"\b(?:high\s+)?fever\b.{0,40}\bswell(?:ing|en)\b|\bswell(?:ing|en)\b.{0,40}\b(?:high\s+)?fever\b",
)]

_URGENT = [re.compile(p) for p in (
    r"\b(?:severe|terrible|unbearable|excruciating|extreme|intense|horrible|really bad|very bad|so much|"
    r"a lot of|lots of|bad)\s+(?:tooth\s*|teeth\s*|dental\s*|jaw\s*)?(?:pain|ache|toothache)\b",
    r"\b(?:pain|toothache)\b.{0,20}\b(?:unbearable|killing me|so bad|very bad|really bad|severe)\b",
    r"\bkilling me\b",
    r"\b(?:swelling|swollen|swelled)\b",
    r"\bbleeding\b",
    r"\b(?:broken|broke|cracked|chipped|knocked[- ]out|knocked)\s+(?:my\s+|a\s+|his\s+|her\s+)?(?:front\s+)?"
    r"(?:tooth|teeth)\b",
    r"\b(?:tooth|teeth)\b.{0,15}\b(?:broke|broken|cracked|knocked out|fell out|came out)\b",
    r"\b(?:abscess|pus)\b",
)]

# "What if I can't breathe after it?", "Is it normal to have trouble swallowing?": asking, not reporting.
_HYPOTHETICAL = re.compile(r"\b(?:what if|if|in case|suppose|supposing|is it normal|is that normal|would|could)\b"
                           r"[^.!?]*$")

# A turn that ends on a goodbye ends the call, whatever came before it ("This
# isn't working, I'll just come in person. Bye."): a reading that missed the
# END act must never answer a goodbye with another question.
_SIGN_OFF = re.compile(r"\b(?:bye|good\s*bye|bye[- ]bye)\b[\s.!]*$", re.IGNORECASE)
_LANGUAGE_NAMES = re.compile(r"\b(?:kannada|hindi|tamil|telugu|malayalam|marathi|bengali|urdu|gujarati)\b")


def _screen(patterns: list, text: str, negated_inside: bool = False) -> bool:
    t = " ".join((text or "").lower().replace("’", "'").split())
    for pattern in patterns:
        for found in pattern.finditer(t):
            before = t[:found.start()]
            if _NEGATION.search(before) or _HYPOTHETICAL.search(before):
                continue
            if negated_inside and re.search(r"\bnot\b", found.group()):
                continue             # "the pain's not so bad"
            return True
    return False


def red_flag_words(text: str) -> bool:
    """Deterministic red-flag screen on the raw words (no model needed)."""
    return _screen(_RED_FLAG, text)


def urgent_words(text: str) -> bool:
    """Deterministic urgent screen (severe pain, swelling, bleeding, broken tooth)."""
    return _screen(_URGENT, text, negated_inside=True)


def _non_latin(text: str) -> bool:
    """Devanagari, Kannada, Tamil... (anything alphabetic past Latin Extended)."""
    return any(ch.isalpha() and ord(ch) > 0x024F for ch in text or "")


def wants_other_language(u: Understanding) -> bool:
    """
    Another language, asked for or spoken. Hinglish is out of scope and is
    treated as English, so a Latin-script turn counts only when the reading
    says other_language AND a language is named ("can you speak Kannada?").
    """
    text = u.raw_text or ""
    return _non_latin(text) or (u.has(Act.OTHER_LANGUAGE) and bool(_LANGUAGE_NAMES.search(text.lower())))


# ---------------------------------------------------------------- tasks


async def create_task(ctx: CallContext, rt: Runtime, *, kind: str, priority: str = "normal", note: str = "",
                      appointment_id: Optional[str] = None) -> Optional[int]:
    """
    A staff task for this call (tasks.create_task on the database thread).
    Only a confirmed number is attached (plan invariant 8). Returns the id,
    or None if the task couldn't be written; callers then promise nothing.
    """
    tasks = optional_module("tasks")
    if tasks is None:
        logger.warning("tasks module unavailable: no %s task for call %s", kind, rt.call_id)
        return None
    phone = ctx.caller.phone_e164 if ctx.caller.phone_state == FieldState.CONFIRMED else None
    try:
        task_id = await rt.run(tasks.create_task, kind=kind, priority=priority, phone_e164=phone, note=note,
                               call_id=rt.call_id, appointment_id=appointment_id)
    except Exception:
        logger.exception("creating a %s task failed for call %s", kind, rt.call_id)
        return None
    ctx.tasks_created.append(task_id)
    return task_id


async def _release_holds(rt: Runtime) -> None:
    """The call is ending: give any held slots back straight away."""
    try:
        await rt.run(scheduling.release_holds, rt.call_id)
    except Exception:
        logger.exception("releasing holds failed for call %s", rt.call_id)


# ---------------------------------------------------------------- the callback flow

# An accepted callback carries this prefix in ctx.callback_reason, which tells
# it apart from a reason policy.py records when it only *offers* one.
ACCEPTED = "Callback: "
# Goals that collect the callback number.
_PHONE_GOALS = (Goal.ASK_PHONE, Goal.PHONE_MORE, Goal.CONFIRM_PHONE)


def callback_in_progress(ctx: CallContext) -> bool:
    return bool(ctx.callback_reason) and ctx.callback_reason.startswith(ACCEPTED)


def callback_goal(ctx: CallContext) -> Optional[GoalPlan]:
    """
    While an accepted callback still needs a number: ask for it, keep
    listening on a partial one, or read it back. None when no callback is in
    progress or the number is confirmed (the task is then written by handle()).
    """
    if not callback_in_progress(ctx):
        return None
    c = ctx.caller
    if ctx.line_e164 and c.phone_state == FieldState.EMPTY and not c.phone_buffer and c.phone_misses < 3:
        # A phone call: "Is the number you're calling from the best one?" rather than digits.
        c.phone_e164, c.phone_state, c.phone_source = ctx.line_e164, FieldState.PENDING, "caller_id"
    return phone_plan(ctx, "ask.phone.callback")


def _offer_reason(ctx: CallContext) -> str:
    """The task note for a callback the caller just accepted, from what was on offer."""
    m = ctx.manage
    action = (m.action or ctx.intent).value
    if ctx.pending == Goal.ENGLISH_ONLY or (ctx.language_warnings >= 2 and ctx.pending == Goal.CALLBACK_OFFER
                                            and ctx.intent not in MANAGE_INTENTS):
        return LANGUAGE_NOTE
    if ctx.pending == Goal.TOO_LATE:
        return f"Wants to {action} an appointment that is too close to change by phone"
    if ctx.intent in MANAGE_INTENTS and not m.verified:
        return f"Wants to {action} an appointment but it couldn't be found on the call"
    if ctx.intent in MANAGE_INTENTS:
        return f"Wants to {action} an appointment; no time on the call suited"
    if ctx.intent == Intent.BOOK:
        return "Wants to book; couldn't settle the details on the call"
    return "Asked for a call back"


async def _callback_commit(ctx: CallContext, rt: Runtime, out: GlobalOutcome) -> GlobalOutcome:
    """Create the task, then (only then) tell them who will call (invariant 7)."""
    note = (ctx.callback_reason or "")[len(ACCEPTED):] or PERSON_NOTE
    kind = "language" if note == LANGUAGE_NOTE else "callback"
    priority = "high" if "too close" in note else "normal"
    target = ctx.manage.target if ctx.intent in MANAGE_INTENTS else None
    task_id = await create_task(ctx, rt, kind=kind, priority=priority, note=note,
                                appointment_id=target.appointment_id if target else None)
    ctx.callback_reason = None
    if task_id is None:
        return _stop(out, plan(Goal.HELP_FIRST, "help_first"))     # no task, no promise
    ctx.outcome = ctx.outcome or "callback"
    if ctx.intent in MANAGE_INTENTS:
        ctx.manage.done = True
    elif ctx.intent == Intent.BOOK and not ctx.book.appointment_id:
        # The team will sort it out on the callback (or they wanted a person,
        # not this booking): the booking stops here instead of offering the
        # same slots again. The draft stays, so "actually, let's book it" resumes.
        ctx.intent = Intent.NONE
    phone = prompts.speak_phone(ctx.caller.phone_e164)
    if ctx.can_transfer:
        # On the phone the front desk can take the call now; the task stays,
        # so if nobody picks up the promise to call back still holds.
        ctx.transfer_requested = True
        ctx.outcome = "transferred"
        if ctx.caller.phone_source == "caller_id":
            phone = "this number"
        return _stop(out, plan(Goal.TRANSFER, "transfer", {"phone": phone}, use_model_say=False))
    return _stop(out, plan(Goal.CALLBACK_DONE, "callback.done", {"phone": phone}))


async def _collect_number(ctx: CallContext, u: Understanding, rt: Runtime, out: GlobalOutcome) -> GlobalOutcome:
    """
    A callback turn: apply.py reads the digits or the yes / no to the
    read-back exactly as it does for a booking (run here, so the engine
    doesn't run it twice), then the task is written once the number is
    confirmed, or the number question goes on.
    """
    from dialogue import apply as applier
    try:
        out.notices += applier.apply(ctx, u, rt)
    except Exception:
        logger.exception("apply failed in the callback flow (call %s)", rt.call_id)
    c = ctx.caller
    if c.phone_state == FieldState.CONFIRMED and c.phone_e164:
        return await _callback_commit(ctx, rt, out)
    next_plan = callback_goal(ctx)
    if next_plan is None:
        ctx.callback_reason = None
        return out
    if next_plan.closes_call:
        ctx.callback_reason = None   # three failed read-backs: phone.failed closes kindly
    return _stop(out, next_plan)


async def _callback_accepted(ctx: CallContext, u: Understanding, rt: Runtime, note: str,
                             out: GlobalOutcome) -> GlobalOutcome:
    ctx.callback_reason = ACCEPTED + note
    c = ctx.caller
    if ctx.can_transfer and c.phone_source == "caller_id" and c.phone_e164:
        # Being put through: the number they're calling from is the one to call
        # back if nobody picks up; no need to ask about it first.
        c.phone_state = FieldState.CONFIRMED
    if ctx.caller.phone_state == FieldState.CONFIRMED and ctx.caller.phone_e164:
        return await _callback_commit(ctx, rt, out)
    if u.phone_digits:
        return await _collect_number(ctx, u, rt, out)
    return _stop(out, callback_goal(ctx))


# ---------------------------------------------------------------- handle


def _closing_line(ctx: CallContext) -> str:
    return "close.booked" if ctx.outcome == "booked" or ctx.book.appointment_id else "close"


def _stop(out: GlobalOutcome, goal_plan: GoalPlan) -> GlobalOutcome:
    out.plan = goal_plan
    out.carry_on = False
    out.closes_call = goal_plan.closes_call
    return out


def _statement(goal: Goal, line: str, **overrides) -> GoalPlan:
    """A handler's own line, said as it is (no model acknowledgement before it)."""
    return plan(goal, line, use_model_say=False, **overrides)


async def handle(ctx: CallContext, u: Understanding, confirmation: Optional[str], rt: Runtime) -> Optional[GlobalOutcome]:
    """Run the handlers above in order; None when nothing global applies."""
    text = u.raw_text or ""
    if text.strip():
        ctx.silence_level = 0
    out = GlobalOutcome()

    # 1. red flag: advice, task, end. No questions.
    if u.emergency == Emergency.RED_FLAG or red_flag_words(text):
        first = ctx.emergency != Emergency.RED_FLAG
        ctx.emergency = Emergency.RED_FLAG
        ctx.outcome = "red_flag"
        if first:
            await create_task(ctx, rt, kind="red_flag", priority="urgent",
                              note=f"Red flag on the call: {text.strip()[:200]}")
        await _release_holds(rt)
        return _stop(out, _statement(Goal.RED_FLAG, "red_flag", closes_call=True))

    # 2. urgent: same-day booking with the emergency lead time.
    said_urgent = urgent_words(text) and not u.pure_question     # "is swelling normal?" is a question
    if (u.emergency == Emergency.URGENT or said_urgent) and ctx.emergency == Emergency.NONE:
        if _route_urgent(ctx, u, rt):
            out.notices.append(Notice("urgent.ack", covered_by=("seen today",)))

    # 3. terminal and meta acts.
    if u.has(Act.ABUSE):
        if ctx.abuse_warnings == 0:
            ctx.abuse_warnings = 1
            return _stop(out, _statement(Goal.ABUSE_WARN, "abuse.warn"))
        ctx.abuse_warnings += 1
        ctx.outcome = ctx.outcome or "abuse"
        await _release_holds(rt)
        return _stop(out, _statement(Goal.ABUSE_CLOSE, "abuse.close", closes_call=True))
    if (u.has(Act.END) or _SIGN_OFF.search(text)) and not (confirmation == "yes" and ctx.pending in _COMMIT_GOALS):
        # "Bye" never books (the 1 Oct "Bye -> booked" call); "yes, thanks, bye"
        # at a summary still commits and is told the outcome.
        await _release_holds(rt)
        return _stop(out, _statement(Goal.CLOSE, _closing_line(ctx), closes_call=True))
    if u.has(Act.REPEAT) and ctx.last_emma and not u.carries_details:
        # The engine speaks repeat.prefix + ctx.last_emma (asked for, so not a loop).
        hearing = match.HEAR_CHECK_RE.match(" ".join(re.sub(r"[^a-z' ]+", " ", text.lower()).split()))
        return _stop(out, _statement(Goal.REPEAT, "repeat.hear" if hearing else "repeat.prefix"))
    if u.has(Act.WAIT) and not u.carries_details:
        return _stop(out, _statement(Goal.HOLD_ON, "hold_on"))
    if u.has(Act.FRAGMENT) and len(text.split()) <= FRAGMENT_MAX_WORDS:
        ctx.fragment = text.strip()  # the engine puts it in front of the next turn; nothing is applied
        return _stop(out, _statement(Goal.GO_ON, "go_on"))

    # 4. notices, then carry on with whatever was under way.
    if u.has(Act.ROBOT_QUESTION):
        out.notices.append(Notice("honesty", covered_by=("virtual receptionist",)))
    if u.has(Act.DONT_KEEP):
        ctx.keep_transcript = False
        out.notices.append(Notice("dont_keep.ack", covered_by=("record",)))

    # 5. a person: help first; if they insist, a real callback.
    if u.has(Act.WANTS_HUMAN):
        ctx.human_requests += 1
        if ctx.human_requests == 1:
            out.plan = plan(Goal.HELP_FIRST, "help_first")
            out.carry_on = confirmation is None
            return out
        return await _callback_accepted(ctx, u, rt, PERSON_NOTE, out)

    # 6. another language (Hinglish counts as English).
    if wants_other_language(u):
        ctx.language_warnings += 1
        if ctx.language_warnings == 1:
            return _stop(out, _statement(Goal.ENGLISH_ONLY, "english_only"))
        return _stop(out, _statement(Goal.CALLBACK_OFFER, "callback.offer"))

    # 7. answers to global offers, and the callback number.
    answered = await _offer_answer(ctx, u, confirmation, rt, out)
    if answered is not None:
        return answered
    return out if (out.notices or out.plan) else None


def _route_urgent(ctx: CallContext, u: Understanding, rt: Runtime) -> bool:
    """
    Pain, swelling, bleeding, a broken tooth: book them in today. A
    Consultation unless they named a service, today unless they named a day,
    any time (no "morning or evening?"); book.py searches with the emergency
    lead time and creates the emergency task when it commits. A caller in the
    middle of a change or cancel keeps that workflow (no "seen today" promise).
    """
    ctx.emergency = Emergency.URGENT
    if ctx.intent in MANAGE_INTENTS:
        return False
    if ctx.intent != Intent.BOOK:
        ctx.intent = Intent.BOOK
    b = ctx.book
    changed = not b.emergency
    b.emergency = True
    if b.service is None and not u.service and not u.service_phrase:
        b.service = URGENT_SERVICE
        changed = True
    if b.date_c is None and not u.date_phrase:
        today = rt.now().date()
        b.date_c = DateConstraint(today, today)
        b.when_phrase = b.when_phrase or "today"
        if b.time_c is None and not u.time_phrase:
            b.any_time = True
        changed = True
    if changed:
        b.touch()                    # a summary heard before this no longer covers the booking
    return True


async def _offer_answer(ctx: CallContext, u: Understanding, confirmation: Optional[str], rt: Runtime,
                        out: GlobalOutcome) -> Optional[GlobalOutcome]:
    pending = ctx.pending
    # Collecting the number for an accepted callback.
    if callback_in_progress(ctx):
        if pending in _PHONE_GOALS:
            if pending != Goal.CONFIRM_PHONE and confirmation == "no" and not u.phone_digits:
                ctx.callback_reason = None           # "no, forget it"
                return None
            return await _collect_number(ctx, u, rt, out)
        ctx.callback_reason = None                   # they moved on before giving a number
    # A callback offer (verification failed twice, too late to change, no time suited, other language).
    offered = pending in _OFFER_GOALS or (u.wants_callback is not None and pending == Goal.ANSWER_ONLY)
    if offered:
        if u.wants_callback is not None:
            yes, no = u.wants_callback is True, u.wants_callback is False
        else:
            yes, no = confirmation == "yes", confirmation == "no"
        if yes:
            return await _callback_accepted(ctx, u, rt, _offer_reason(ctx), out)
        ctx.callback_reason = None                   # declined, or moved on: nothing on offer any more
        if no and pending != Goal.ENGLISH_ONLY and ctx.intent in MANAGE_INTENTS:
            ctx.manage.done = True                   # no callback: hand back to "anything else?"
        return None
    # "Anything else?" -> "no": goodbye.
    if (pending in _ANYTHING_ELSE_GOALS and confirmation == "no" and u.intent in (None, Intent.NONE)
            and not u.has(Act.QUESTION) and not u.has(Act.ROBOT_QUESTION) and not _details_besides_yes_no(u)):
        await _release_holds(rt)
        return _stop(out, _statement(Goal.CLOSE, _closing_line(ctx), closes_call=True))
    # "If you'd like, I can book that for you too." -> "yes please".
    if pending == Goal.OFFER_HELP and confirmation == "yes" and ctx.intent in (Intent.NONE, Intent.INFO):
        ctx.intent = Intent.BOOK
        return out
    return None


def _details_besides_yes_no(u: Understanding) -> bool:
    """Did the turn carry anything other than its yes / no ("no, but can I book for my son")?"""
    saved = u.confirmation
    try:
        u.confirmation = None
        return u.carries_details
    finally:
        u.confirmation = saved


# ---------------------------------------------------------------- silence


def silence(ctx: CallContext) -> GoalPlan:
    """The next rung of the silence ladder (async_process_turn("") mid-call); level 3 closes the call."""
    ctx.silence_level = min(ctx.silence_level + 1, 3)
    level = ctx.silence_level
    return _statement(Goal.SILENCE, f"silence.{level}", params={"level": level}, closes_call=level >= 3)
