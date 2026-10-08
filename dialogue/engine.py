"""
The R2 turn pipeline behind ai_engine.async_process_turn
(docs/R2_DESIGN.md, section 5).

    closed? -> closing line                     greeting? -> rotated greeting
    merge a stashed fragment; red-flag word screen
    Tier-0 (tier0.understand, no model)  --else-->  progress("llm_start");
        brief.build -> nlu.understand_stream -> await head()  (None -> no-model fallback)
    progress("commit")                          (from here the turn always completes)
    handlers.handle (globals)  ->  apply.apply  ->  workflow.advance (actions,
        progress("before_action", phrase=...) before searches / commits)
    policy.next_goal  ->  compose the reply:
        [model `say`, validated, streamed via on_sentence]
        + [Notices, pre-written]
        + [ask: the model's if its next_goal == Python's goal, the goal is not
           critical and it validates; else prompts.render(plan.line)]
    policy.note_turn, history, trace  ->  TurnOutput

One model call per turn at most; with Gemini down, slow or rate-limited the
same pipeline runs on Tier-0 / the fallback understanding and pre-written
lines, so the caller never meets a dead end.

Nothing is mutated before progress("commit"): until then the call session
may cancel the turn and merge it with the caller's next words.

Owner in Sprint 1b: E1.
"""

from __future__ import annotations

import dataclasses
import logging
import random
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, Optional

import config
import phrases
import prompts
import speech
from dialogue import apply as applier
from dialogue import book, handlers, manage, policy
from dialogue.context import (
    Act, ActionResult, CallContext, CLOSING_GOALS, Emergency, Expect, Goal, GoalPlan, Intent,
    MANAGE_INTENTS, Notice, RUNG_ASK, RUNG_EXIT, Tier0View, TurnTrace, Understanding,
)
from dialogue.runtime import Runtime, safe_call

logger = logging.getLogger(__name__)

OnSentence = Callable[[str], Awaitable[None]]

# Goals whose pre-written ask may take a short opener ("Sure, ...") when the
# model said nothing first. Never a summary, read-back, offer or outcome.
OPENER_GOALS = frozenset({
    Goal.ASK_NAME, Goal.ASK_PHONE, Goal.ASK_PATIENT, Goal.ASK_AGE, Goal.ASK_SERVICE, Goal.ASK_BRANCH,
    Goal.ASK_WHEN, Goal.ASK_TIME, Goal.ASK_APPT_DATE, Goal.ASK_NEW_WHEN, Goal.WHAT_TO_CHANGE,
    Goal.ANYTHING_ELSE, Goal.ASK_INTENT,
})
_ENDS_SENTENCE = re.compile(r"[.!?][\"')\]]*$")


@dataclass
class TurnOutput:
    """
    The engine's result; ai_engine wraps it in its TurnResult (shared
    contract: text, tier, entities, nlu_ms, action, goal_before, goal_after,
    spoken_count).
    """
    text: str                                    # the whole reply, sentences joined by spaces
    tier: int                                    # 0 Tier-0, 1 model, 2 no-model fallback, -1 greeting / closed
    entities: dict = field(default_factory=dict)  # the Understanding as a dict (logs, harness)
    nlu_ms: float = 0.0
    action: Optional[str] = None                 # booked | rescheduled | cancelled | None
    goal_before: Optional[str] = None
    goal_after: Optional[str] = None
    spoken_count: int = 0                        # leading sentences already delivered through on_sentence


async def build_runtime(ctx: CallContext, progress: Optional[Callable] = None) -> Runtime:
    """This turn's Runtime: the shared database, a catalog snapshot and the knowledge base."""
    import db
    import facts
    catalog = None
    try:
        catalog = await facts.get_catalog()
    except Exception as exc:                     # noqa: BLE001 - a turn without a catalog still answers
        logger.warning("catalog unavailable: %r", exc)
    kb = safe_call(facts.get_knowledge, default=None)
    rt = Runtime(call_id=ctx.call_id or "call", db=db.get_db(),
                 catalog=catalog if catalog is not None else facts.Catalog(),
                 kb=kb if kb is not None else facts.Knowledge())
    if progress is not None:
        rt.progress = progress
    return rt


async def process_turn(ctx: CallContext, text: str, progress: Optional[Callable] = None,
                       on_sentence: Optional[OnSentence] = None, runtime=None) -> TurnOutput:
    """
    One caller turn. `text` == "" means the greeting (first call) or silence
    (later: handlers.silence). If on_sentence is given, each reply sentence
    that is final and validated is awaited through it as soon as it exists;
    such a sentence is always exactly one item of
    speech.split_sentences(TurnOutput.text), in order, and spoken_count says
    how many there were, so the call session speaks only the rest.
    `runtime` (dialogue.runtime.Runtime) is built from db / facts when None.
    """
    text = (text or "").strip()
    goal_before = _value(ctx.pending)
    if ctx.closed_conversation:
        line = "close.booked" if ctx.outcome == "booked" else "close"
        return TurnOutput(_render(ctx, line), tier=-1, goal_before=goal_before, goal_after=goal_before)
    if not text and not ctx.greeted:
        return _greet(ctx, goal_before)

    text = _answer_to_greeting(ctx, text)
    if text == _ASK_WHAT_TO_KNOW:
        return _say_line(ctx, goal_before, Goal.ASK_INTENT,
                         random.choice(("Sure, what would you like to know?", "Of course, what would you like to know?")))
    rt = runtime if runtime is not None else await build_runtime(ctx, progress)
    if runtime is not None and progress is not None:
        rt.progress = progress
    rt.events, rt.raised, rt.release_requested = [], [], False
    if not text:
        return await _silence(ctx, rt, goal_before)

    started = time.perf_counter()
    merged = _merge_fragment(ctx.fragment, text)
    stream = brief = None
    tier, fallback = 0, False
    try:
        if safe_call(handlers.red_flag_words, merged, default=False):
            # Never waits on the model: breathing or swallowing trouble gets 108 at once.
            u = Understanding(emergency=Emergency.RED_FLAG, source="tier0")
        else:
            view = _view(ctx, rt)
            u = _tier0(merged, view) if config.TIER0_ENABLED else None
            if u is not None and _dangles(merged, view, u):
                u = Understanding(acts=[Act.FRAGMENT.value], source="tier0")
            if u is None:
                tier = 1
                rt.emit("llm_start")
                # When the fallback reading already understands the turn clearly (a
                # name, a number, a yes, a plain request), the model gets only a
                # short head start: it adds natural wording, but a slow day (first
                # words after 2-6 s on 6 Oct) must not cost the caller the full wait.
                quick = _tier0(merged, view, lenient=True) if config.TIER0_ENABLED else None
                deadline = config.NLU_FAST_DEADLINE_S if _clear_enough(quick) else None
                stream, u, brief = await _model(ctx, merged, rt, head_deadline_s=deadline)
                if u is None:
                    tier, fallback = 2, True
                    u = _tier0(merged, view, lenient=True) or Understanding(acts=[Act.UNCLEAR.value])
                    u.source = "fallback"
                    _mark_question(u, merged)
        nlu_ms = (time.perf_counter() - started) * 1000
        u.raw_text = merged
        rt.emit("commit")
        return await _complete(ctx, u, rt, stream=stream, brief=brief, tier=tier, fallback=fallback,
                               nlu_ms=nlu_ms, goal_before=goal_before, on_sentence=on_sentence)
    finally:
        if stream is not None:
            await _close(stream)


# ---------------------------------------------------------------- after commit


async def _complete(ctx: CallContext, u: Understanding, rt: Runtime, *, stream, brief, tier: int,
                    fallback: bool, nlu_ms: float, goal_before: Optional[str],
                    on_sentence: Optional[OnSentence]) -> TurnOutput:
    """Steps 6-10: everything that mutates the context. Always runs to the end."""
    ctx.turn += 1
    if ctx.fragment and u.has(Act.FRAGMENT):
        # A second cut-off in a row (already merged with the first): read it as
        # it stands instead of stashing again, so "go on" is never a loop.
        u.acts = [a for a in u.acts if _value(a) != Act.FRAGMENT.value] or [Act.UNCLEAR.value]
    ctx.fragment = ""
    ctx.silence_level = 0
    conf = applier.resolve_confirmation(u, u.raw_text, ctx.pending)
    mark = safe_call(policy.progress_mark, ctx, default=None)
    if mark is not None:
        u.__dict__["_progress_mark"] = mark      # what the context held before this turn (policy._is_miss)

    opening = ctx.intent != Intent.BOOK and not ctx.book.service
    outcome = await _handle(ctx, u, conf, rt)
    notices: list = list(outcome.notices) if outcome is not None else []
    result = ActionResult()
    if outcome is None or outcome.carry_on:
        try:
            notices += applier.apply(ctx, u, rt)
        except Exception:                        # noqa: BLE001 - a bug here must not drop the call
            logger.exception("apply failed on turn %d", ctx.turn)
        await _release_if_requested(ctx, rt)
        result = await _advance(ctx, u, conf, rt)
        notices += list(result.notices or ())
        if opening and ctx.intent == Intent.BOOK and ctx.book.service and ctx.emergency == Emergency.NONE:
            notices.append(applier.request_notice(ctx.book))

    if outcome is not None and outcome.plan is not None:
        plan = outcome.plan
    else:
        plan = policy.next_goal(ctx, u, catalog=rt.catalog, raised=tuple(rt.raised))
        if plan.rung >= RUNG_EXIT:
            exit_plan = policy.take_exit(ctx, plan)
            if exit_plan is None:
                # A default was taken (branch: earliest, when: earliest...): let the
                # workflow act on it now, so the caller hears the result this turn.
                await _release_if_requested(ctx, rt)
                again = await _advance(ctx, Understanding(source=u.source), None, rt)
                notices += list(again.notices or ())
                if again.action:
                    result = again
                plan = policy.next_goal(ctx, None, catalog=rt.catalog)
            else:
                plan = exit_plan
    _caller_id_line(ctx, plan)

    allowed = _allowed(brief, ctx, result.action, u)
    say, dropped, streamed = [], [], []
    use_say = plan.use_model_say and stream is not None and u.source == "llm"
    if use_say:
        say, dropped, streamed = await _stream_say(stream, allowed, on_sentence)
    if (u.has(Act.QUESTION) or u.clinical) and not say and plan.goal not in CLOSING_GOALS \
            and plan.goal not in (Goal.REPEAT, Goal.HOLD_ON, Goal.GO_ON):
        answer = _fallback_answer(u, rt)
        # "Is 1 pm not possible at all?" is answered by the offer that follows;
        # an honest "not sure" in front of it would contradict it.
        asks_about_booking = bool(u.date_phrase or u.time_phrase) and (plan.goal in (
            Goal.OFFER_SLOTS, Goal.OFFER_NEW_SLOTS, Goal.SUMMARY, Goal.CONFIRM_RESCHEDULE, Goal.NO_SLOTS)
            or ctx.intent in (Intent.BOOK, Intent.RESCHEDULE))
        if not (answer.line == "unknown" and asks_about_booking):
            notices.insert(0, answer)

    ask = None
    # A re-ask (rung 2+) is always the pre-written rephrase or choices line:
    # the model can't see the ladder and tends to ask the same thing again.
    if plan.steer and stream is not None and not plan.critical and u.source == "llm" \
            and u.next_goal == plan.goal and plan.rung <= RUNG_ASK:
        ask, rule = await _model_ask(stream, allowed, say)
        if rule:
            dropped.append(rule)

    sentences = compose(ctx, plan, notices, say, ask)
    if not sentences:
        sentences = _split(_render(ctx, plan.line, plan.params) or phrases.ERROR_REPLY)
    reply = " ".join(sentences)
    if streamed and speech.split_sentences(reply)[:len(streamed)] != streamed:
        logger.warning("streamed sentences don't prefix the reply (turn %d)", ctx.turn)

    if result.action:
        ctx.outcome = result.action
    policy.note_turn(ctx, plan, u)
    if plan.closes_call or plan.goal in CLOSING_GOALS or (outcome is not None and outcome.closes_call):
        ctx.closed_conversation = True
    ctx.remember(u.raw_text, reply)
    if plan.goal != Goal.REPEAT:
        ctx.last_emma = reply
    ctx.note_said(say + ([ask] if ask else []))
    goal_after = _value(ctx.pending)
    ctx.add_trace(TurnTrace(
        turn=ctx.turn, tier=tier, acts=[_value(a) for a in u.acts], goal_before=goal_before,
        goal_after=goal_after, action=result.action, model_goal=_value(u.next_goal),
        used_model_say=bool(say), used_model_ask=bool(ask), dropped=dropped, fallback=fallback,
        nlu_ms=round(nlu_ms, 1)))
    logger.info("turn %d tier=%d %s -> %s%s nlu=%.0fms", ctx.turn, tier, goal_before, goal_after,
                f" action={result.action}" if result.action else "", nlu_ms)
    return TurnOutput(reply, tier=tier, entities=_entities(u), nlu_ms=nlu_ms, action=result.action,
                      goal_before=goal_before, goal_after=goal_after, spoken_count=len(streamed))


def _sentence_key(sentence: str) -> str:
    """Words only, lower case, "sundays" == "sunday": what makes two sentences the same one."""
    words = re.findall(r"[a-z0-9']+", sentence.lower())
    return " ".join(w[:-1] if w.endswith("s") and len(w) > 3 else w for w in words)


def compose(ctx: CallContext, plan, notices: list, say: list, ask: Optional[str]) -> list:
    """
    The reply as a list of sentences: validated `say` sentences, then the
    notices not already covered by `say`, then the ask (model or pre-written),
    with the opener rule and prompts memory updated. Pure; the streaming
    variant in process_turn emits the same sentences in the same order.
    """
    sentences = [_finish(s) for s in say if s and s.strip()]
    said = " ".join(sentences).lower()
    seen = set()
    # apply.py and the workflow can both raise the same notice; the workflow's
    # copy comes later and names the real branches and doctors from the
    # catalog, so the last copy's params win, said where the first one was.
    latest = {n.line: n for n in notices if isinstance(n, Notice)}
    notices = [latest[n.line] if isinstance(n, Notice) else n for n in notices]
    notices = _fold_request(notices, latest, plan)
    for notice in notices:
        if not isinstance(notice, Notice):
            continue
        if notice.covered_by and said and any(str(w).lower() in said for w in notice.covered_by):
            continue                                 # the model already said it
        if notice.line == "ack.when" and plan.goal in (Goal.OFFER_SLOTS, Goal.OFFER_NEW_SLOTS):
            continue                                 # the offer says the day and time itself
        if notice.line in seen:
            # apply.py and the workflow may both raise the same fact ("Nagarbhavi
            # doesn't do braces") in slightly different words: say it once.
            continue
        seen.add(notice.line)
        line = _render(ctx, notice.line, notice.params)
        if line:
            # A fact answer and a notice can carry the same sentence ("We're
            # closed on Sundays." twice in one reply): say each sentence once.
            have = {_sentence_key(s) for s in sentences}
            sentences += [s for s in _split(line) if _sentence_key(s) not in have]
    if plan.goal == Goal.REPEAT:
        prefix = _render(ctx, plan.line or "repeat.prefix", plan.params)
        return sentences + _split(prefix) + _split(ctx.last_emma)
    if not plan.steer:
        return sentences
    if ask:
        return sentences + _split(ask)
    line = _render(ctx, plan.line, plan.params)
    if not line:
        return sentences
    if not sentences and plan.goal in OPENER_GOALS and not plan.critical and ctx.turn > 0:
        line = safe_call(prompts.with_opener, line, ctx.prompts, default=line) or line
    return sentences + _split(line)


# ---------------------------------------------------------------- the model


async def _model(ctx: CallContext, text: str, rt: Runtime, head_deadline_s: Optional[float] = None):
    """(stream, understanding, brief); understanding None means the no-model fallback."""
    import nlu
    from dialogue import brief as briefing
    try:
        hint = policy.plan_hint(ctx, catalog=rt.catalog)
        brief = briefing.build(ctx, text, rt.catalog, rt.kb, hint)
    except Exception as exc:                     # noqa: BLE001
        logger.warning("brief failed (%r); answering without the model", exc)
        return None, None, None
    stream = None
    try:
        stream = await nlu.understand_stream(brief, head_deadline_s=head_deadline_s)
        u = await stream.head()
    except Exception as exc:                     # noqa: BLE001 - asyncio.CancelledError is not an Exception
        logger.warning("model unavailable (%r); answering without it", exc)
        u = None
    if u is None:
        if stream is not None:
            await _close(stream)
        return None, None, brief
    return stream, u, brief


async def _stream_say(stream, allowed, on_sentence: Optional[OnSentence]) -> tuple:
    """Validated say sentences, each sent through on_sentence as soon as it is final."""
    import dialogue.validate as validate
    say, dropped, streamed = [], [], []
    try:
        async for chunk in stream.sentences("say"):
            for sentence in _split(chunk):
                verdict = safe_call(validate.check_sentence, sentence, allowed, part="say", default=None)
                if verdict is None or not verdict.ok:
                    dropped.append(getattr(verdict, "rule", "") or "unvalidated")
                    continue
                shape = safe_call(validate.check_shape, say + [sentence], "", default=None)
                if shape is None or not shape.ok:
                    dropped.append(getattr(shape, "rule", "") or "unvalidated")
                    continue
                say.append(sentence)
                if on_sentence is not None:
                    await on_sentence(sentence)
                    streamed.append(sentence)
    except Exception as exc:                     # noqa: BLE001 - a stalled stream falls back to pre-written lines
        logger.info("say stream ended early: %r", exc)
    return say, dropped, streamed


async def _model_ask(stream, allowed, say: list) -> tuple:
    """(the model's ask if every sentence validates, else None; the failing rule)."""
    import dialogue.validate as validate
    try:
        text = (await stream.text("ask") or "").strip()
    except Exception as exc:                     # noqa: BLE001
        logger.info("ask stream failed: %r", exc)
        return None, ""
    if not text:
        return None, ""
    parts = _split(text)
    for sentence in parts:
        verdict = safe_call(validate.check_sentence, sentence, allowed, part="ask", default=None)
        if verdict is None or not verdict.ok:
            return None, getattr(verdict, "rule", "") or "unvalidated"
    shape = safe_call(validate.check_shape, say, " ".join(parts), default=None)
    if shape is None or not shape.ok:
        return None, getattr(shape, "rule", "") or "unvalidated"
    return " ".join(parts), ""


def _allowed(brief, ctx: CallContext, action: Optional[str], u: Understanding):
    """The brief's allow-list, updated with what happened after it was built (the commit, recent lines)."""
    import dialogue.validate as validate
    base = getattr(brief, "allowed", None)
    changes = {"committed": action}
    if u.has(Act.ROBOT_QUESTION):
        changes["honesty_turn"] = True
    if u.clinical:
        changes["clinical"] = True
    if ctx.tasks_created:
        changes["callback_task"] = True
    # Emma's latest sentences are added to the brief's own recent list (which
    # also holds her last line and this turn's notices), never swapped for it,
    # so V7 still catches a near-repeat of either.
    recent = tuple(ctx.prompts.recent[-6:])
    if base is None:
        return validate.Allowed(recent=recent, **changes)
    if callable(getattr(base, "for_turn", None)):
        extra = tuple(r for r in recent if r not in tuple(base.recent))
        return base.for_turn(notices=extra, **changes)
    if dataclasses.is_dataclass(base):
        return dataclasses.replace(base, **{k: v for k, v in changes.items() if hasattr(base, k)})
    return base


async def _close(stream) -> None:
    try:
        await stream.aclose()
    except Exception:                            # noqa: BLE001
        pass


# ---------------------------------------------------------------- pieces of the turn


_ASK_WHAT_TO_KNOW = "<<ask what to know>>"       # marker: a yes to "anything you'd like to know?"
_GREETING_YES = {"book": "Yes, I'd like to book an appointment.", "info": _ASK_WHAT_TO_KNOW}


def _answer_to_greeting(ctx: CallContext, text: str) -> str:
    """A bare "yes" to "Would you like to book an appointment?" is that request, not an empty yes."""
    offer = getattr(ctx, "greeting_offer", None)
    if not offer or not text or ctx.pending != Goal.GREET:
        return text
    ctx.greeting_offer = None
    from dialogue import match
    if len(text.split()) <= 4 and safe_call(match.parse_yes_no, text, default=None) == "yes":
        return _GREETING_YES[offer]
    return text


def _say_line(ctx: CallContext, goal_before: Optional[str], goal: Goal, line: str) -> TurnOutput:
    """A short fixed reply that needs no understanding (it is recorded like any other)."""
    ctx.pending = goal
    ctx.pending_params = {}
    ctx.remember("yes", line)
    ctx.last_emma = line
    ctx.note_said(_split(line))
    return TurnOutput(line, tier=0, goal_before=goal_before, goal_after=goal.value)


def _greet(ctx: CallContext, goal_before: Optional[str]) -> TurnOutput:
    greeting = phrases.next_greeting()
    ctx.greeted = True
    ctx.greeting_offer = phrases.greeting_offer(greeting)
    ctx.pending = Goal.GREET
    ctx.pending_params = {}
    ctx.remember("", greeting)
    ctx.last_emma = greeting
    ctx.note_said(_split(greeting))
    return TurnOutput(greeting, tier=-1, goal_before=goal_before, goal_after=Goal.GREET.value)


async def _silence(ctx: CallContext, rt: Runtime, goal_before: Optional[str]) -> TurnOutput:
    """"" mid-call: the silence ladder (handlers.silence); the pending question stays pending."""
    plan = safe_call(handlers.silence, ctx, default=None)
    if not isinstance(plan, GoalPlan):
        ctx.silence_level += 1
        level = min(ctx.silence_level, 3)
        plan = GoalPlan(Goal.SILENCE, f"silence.{level}", params={"level": level}, critical=True,
                        use_model_say=False, closes_call=level >= 3)
    reply = _render(ctx, plan.line, plan.params) or phrases.ERROR_REPLY
    if plan.closes_call or plan.goal in CLOSING_GOALS:
        ctx.closed_conversation = True
    ctx.remember("", reply)
    return TurnOutput(reply, tier=-1, goal_before=goal_before, goal_after=_value(ctx.pending))


async def _handle(ctx: CallContext, u: Understanding, conf, rt: Runtime):
    try:
        return await handlers.handle(ctx, u, conf, rt)
    except Exception as exc:                     # noqa: BLE001
        logger.warning("handlers failed: %r", exc)
        return None


async def _advance(ctx: CallContext, u: Understanding, conf, rt: Runtime) -> ActionResult:
    """The workflow under way runs its actions (search, hold, verify, commit) on the database thread."""
    if ctx.intent == Intent.BOOK:
        module = book
    elif ctx.intent in MANAGE_INTENTS:
        module = manage
    else:
        return ActionResult()
    try:
        result = await module.advance(ctx, u, conf, rt)
    except Exception as exc:                     # noqa: BLE001
        logger.warning("%s.advance failed: %r", module.__name__, exc)
        return ActionResult()
    return result if isinstance(result, ActionResult) else ActionResult()


async def _release_if_requested(ctx: CallContext, rt: Runtime) -> None:
    """Holds of a parked, dropped or re-searched draft go back to the pool (apply asked for it)."""
    if not rt.release_requested:
        return
    rt.release_requested = False
    import scheduling
    keep = [s.hold_id for s in (*ctx.book.offered, *ctx.manage.offered) if getattr(s, "hold_id", None)]
    try:
        await rt.run(scheduling.release_holds, rt.call_id, keep)
    except Exception as exc:                     # noqa: BLE001 - holds also expire on their own
        logger.info("releasing holds failed: %r", exc)


def _fallback_answer(u: Understanding, rt: Runtime) -> Notice:
    """No usable model answer: a verified fact, the clinical line for clinical questions, or an honest "not sure"."""
    import facts
    if u.clinical:
        return Notice("clinical")
    answer = safe_call(facts.lookup, u.raw_text, rt.kb, rt.catalog, tuple(u.faq_ids or ()), default=None)
    return Notice("answer.fact", {"text": answer}) if answer else Notice("unknown")


def _view(ctx: CallContext, rt: Runtime) -> Tier0View:
    hint = policy.listening_hint(ctx)
    return Tier0View(
        expect=Expect(hint["expect"]), pending=ctx.pending, intent=ctx.intent,
        digits_so_far=ctx.caller.phone_buffer, offered=tuple(ctx.offers()),
        options=tuple(ctx.book.service_options), names_heard=tuple(ctx.caller.names_heard),
        catalog=rt.catalog)


_WORKFLOW_INTENTS = (Intent.BOOK, Intent.CANCEL, Intent.RESCHEDULE, Intent.CHECK)


def _clear_enough(u: Optional[Understanding]) -> bool:
    """
    The no-model reading answers what Emma asked, or is a plain request with no
    question in it: safe to go ahead without waiting long for the model.
    Questions, chit-chat and anything unclear still wait, the model does those better.
    """
    if u is None or u.has(Act.QUESTION) or u.has(Act.UNCLEAR) or u.emergency != Emergency.NONE:
        return False
    if u.has(Act.ANSWER):
        return True
    return u.intent in _WORKFLOW_INTENTS


def _tier0(text: str, view: Tier0View, lenient: bool = False) -> Optional[Understanding]:
    import tier0
    understand = getattr(tier0, "understand", None)
    if not callable(understand):
        return None
    u = safe_call(understand, text, view, lenient=lenient, default=None)
    return u if isinstance(u, Understanding) else None


def _merge_fragment(fragment: str, text: str) -> str:
    """
    The stashed cut-off words joined to the caller's next words. A caller who
    was cut off usually starts the sentence again ("Tell me what can you" ...
    "Tell me what you can do for me"): when the new words open the same way,
    they replace the fragment instead of being glued to it, so the restarted
    sentence reads as the complete thing it is.
    """
    if not fragment:
        return text
    head = lambda s, n: [w.strip(".,!?").lower() for w in s.split()[:n]]
    n = min(2, len(fragment.split()))
    if len(text.split()) > n and head(fragment, n) == head(text, n):
        return text
    return f"{fragment} {text}".strip()


def _dangles(text: str, view: Tier0View, u: Understanding) -> bool:
    """
    A cut-off opener Tier-0 still read as a request ("I need to book a
    cleaning for my" taken as a plain booking opener). Stashing it and
    merging it with the next words loses nothing, while acting on it loses
    the rest of the sentence ("...for my son"), so a dangling end wins
    (R2_DESIGN 10.3: a fragment is never taken as an answer).
    """
    if u.has(Act.FRAGMENT) or u.emergency != Emergency.NONE or u.intent is None:
        return False
    if any(v not in (None, "", False) for v in (u.name, u.name_spelled, u.phone_digits, u.date_phrase,
                                                u.time_phrase, u.choice_index, u.confirmation)):
        return False                             # a real answer ("next Monday at 8 am") is never re-read
    if len(text.split()) > getattr(handlers, "FRAGMENT_MAX_WORDS", 14):
        return False                             # too long to stash: handlers lets it through as is
    from dialogue import match
    return bool(safe_call(match.is_fragment, text, view.expect.value, default=False))


_QUESTION_FALLBACK = re.compile(
    r"\?|\b(what|where|which|who|why|how|do you|does|can you|could you|is there|are you|price|cost|"
    r"charge|fee|insurance|address|parking|open|hours)\b", re.I)


def _mark_question(u: Understanding, text: str) -> None:
    """Without the model, a question must never be consumed as a detail (e.g. taken as a name)."""
    if u.has(Act.QUESTION):
        return
    from dialogue import match
    asked = safe_call(match.looks_like_question, text, default=None)
    if asked is None:
        asked = bool(_QUESTION_FALLBACK.search(text))
    if asked:
        u.acts = list(u.acts) + [Act.QUESTION.value]
        u.question = u.question or text
        if u.name and not re.search(r"\b(my name|this is|i am|i'm)\b", text, re.I):
            u.name = None


def _caller_id_line(ctx: CallContext, plan) -> None:
    """
    Phone calls with a caller ID: the number's read-back becomes "Is the number
    you're calling from the best one to reach you on?", and after a no, Emma
    asks for the other number rather than apologising for a wrong read-back.
    """
    c = ctx.caller
    if plan.goal == Goal.CONFIRM_PHONE and c.phone_source == "caller_id":
        manage = ctx.intent in MANAGE_INTENTS and not ctx.manage.verified
        plan.line = "confirm.phone.caller_id.manage" if manage else "confirm.phone.caller_id"
        plan.params = {}
        plan.critical = True
    elif plan.goal == Goal.ASK_PHONE and c.phone_source == "declined" and ctx.pending == Goal.CONFIRM_PHONE \
            and plan.line != "ask.phone.why":
        plan.line, plan.params, plan.critical = "ask.phone.not_caller_id", {}, True


# Notices that already say something about the request itself ("Our Nagarbhavi
# branch doesn't do braces"): saying it back first would contradict them.
_REQUEST_CONFLICTS = {"service.unknown", "branch.no_service", "branch.only", "doctor.unknown",
                      "doctor.other_branch", "doctor.gender_none", "correction.ack", "confirm_change",
                      "urgent.ack", "answer.fact", "unknown", "clinical"}


def _fold_request(notices: list, latest: dict, plan) -> list:
    """
    "ack.request" and "ack.when" become one sentence ("Sure, a cleaning for
    Monday the 12th, in the afternoon."); the request is dropped, and the day
    said on its own, when another notice already speaks to the request.
    """
    request = latest.get("ack.request")
    if request is None:
        return notices
    if _REQUEST_CONFLICTS & set(latest):
        return [n for n in notices if not (isinstance(n, Notice) and n.line == "ack.request")]
    p = request.params
    text = p.get("service", "")
    if p.get("who"):
        text += f" for {p['who']}"
    if p.get("branch"):
        text += f" at {p['branch']}"
    when_notice = latest.get("ack.when")
    when = (when_notice.params.get("when") or "") if when_notice else ""
    folds = bool(when) and plan.goal not in (Goal.OFFER_SLOTS, Goal.OFFER_NEW_SLOTS)
    if folds and when.startswith("the earliest"):
        text += " as soon as we can"
    elif folds:
        joiner = " " if re.match(r"(in the|between|around|this|tonight)\b", when) else \
            ", " if p.get("who") or p.get("branch") else " for "
        text += joiner + when
    folded = Notice("ack.request", {"request": text}, covered_by=request.covered_by)
    return [folded if isinstance(n, Notice) and n.line == "ack.request" else n
            for n in notices if not (folds and isinstance(n, Notice) and n.line == "ack.when")]


def _render(ctx: CallContext, line: str, params: Optional[dict] = None) -> str:
    """A pre-written line (prompts.render records it in the call's memory); "" if it can't be rendered."""
    if not line:
        return ""
    clean = {k: v for k, v in (params or {}).items() if not str(k).startswith("_")}
    text = safe_call(prompts.render, line, ctx.prompts, clean, default=None)
    if text is None:
        logger.warning("line %r could not be rendered", line)
        return ""
    return text


def _split(text: str) -> list:
    """Sentences as speech.split_sentences will cut them, each ending in punctuation."""
    out = []
    for part in speech.split_sentences(text or ""):
        part = _finish(part)
        if part:
            out.append(part)
    return out


def _finish(sentence: str) -> str:
    """Capital first letter and closing punctuation, so joined sentences split back the same way."""
    s = re.sub(r"\s+", " ", (sentence or "").strip())
    if not s:
        return ""
    s = s[:1].upper() + s[1:]
    if not _ENDS_SENTENCE.search(s):
        s += "."
    return s


def _value(x) -> Optional[str]:
    if x is None:
        return None
    return x.value if isinstance(x, Enum) else str(x)


def _entities(u: Understanding) -> dict:
    """The Understanding as plain data, empty values left out (logs and the harness)."""
    out = {}
    for f in dataclasses.fields(u):
        v = getattr(u, f.name)
        if v in (None, "", False, []) and f.name not in ("acts",):
            continue
        if isinstance(v, Enum):
            v = v.value
        elif isinstance(v, list):
            v = [_value(x) if isinstance(x, Enum) else x for x in v]
        out[f.name] = v
    return out
