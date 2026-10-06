"""
MANAGE workflows: check, cancel, reschedule (docs/R2_DESIGN.md, section 10.2;
plan 5.6).

    phone (read back, yes; carried over if already given) -> patient name ->
    appointment date -> verify -> [several matches: pick by name, then time]
      CHECK       -> STATE_APPOINTMENT -> anything else
      CANCEL      -> CONFIRM_CANCEL (no fee) -> clear yes -> [reason, optional,
                     asked once, skipped if already given] -> scheduling.cancel
                     -> "cancelled" notice + OFFER_REBOOK -> yes: BOOK, service
                     carried
      RESCHEDULE  -> ASK_NEW_WHEN -> OFFER_NEW_SLOTS (same service, same branch
                     by default, ignore_appointment, held) -> CONFIRM_RESCHEDULE
                     ("from X to Y") -> clear yes -> scheduling.reschedule
                     (expected_version) -> "rescheduled" notice (new time) ->
                     policy's "anything else?"

Verification (Z7): scheduling.future_appointments(phone) filtered by
match.name_similarity >= NAME_MATCH_RATIO and the date. Nothing about any
appointment enters the context, the brief or a reply until it passes: the
rows of a failed lookup never leave verify(). On a miss Emma reveals nothing
("verify.failed", the same words whether or not the number has bookings, and
asks to check the date); a second miss offers a callback (a task only if they
want it; handlers.py takes the answer). Starting within the lead time (or
already past) -> TOO_LATE + callback offer.

A yes only counts when Emma's confirmation was the last thing she asked
(ctx.pending) and the caller heard it to the end (ctx.last_reply_heard), the
same recap-heard rule as BOOK (Z1). ActionResult.action is set only after
scheduling returned ok for this very change (Z2).

The ManageDraft fields carry the whole state, so next_goal() stays a pure
function of the context:
    summary_heard   the caller said a clear yes to CONFIRM_CANCEL for target
    reason_asked    ASK_CANCEL_REASON was (or is being) asked
    done            this action is finished (committed, declined, or its
                    callback offer answered); next_goal() then says the
                    outcome once and hands back to policy (ANYTHING_ELSE)

Owner in Sprint 1b: E5.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

import clock
import config
import dateparse
import prompts
import scheduling
from dialogue import match
from dialogue.context import (
    CLOSING_GOALS, GOAL_SPECS, Act, MANAGE_INTENTS, RUNG_ASK, RUNG_CHOICES, ActionResult, BookingDraft, CallContext,
    FieldState, Goal, GoalPlan, Intent, ManageDraft, Notice, OfferedSlot, Understanding, VerifiedAppointment,
)
from dialogue.runtime import Runtime

logger = logging.getLogger(__name__)

# Line ids for the rare races where the appointment changed under us between
# verification and the commit (staff cancelled it, or it vanished). Requested
# from the words track; until prompts.LINES has them the notice is skipped and
# the rebook offer alone is said, which still never claims anything.
ALREADY_CANCELLED_LINE = "manage.already_cancelled"   # "Looks like that one's already been cancelled."
NOT_FOUND_LINE = "manage.not_found"                   # "Hmm, I can't see that appointment any more."

# "Don't cancel it after all", said while Emma asks for the reason.
_KEEP_IT = re.compile(r"\b(?:don'?t|do\s+not)\s+cancel\b|\bkeep\s+it\b|\bnever\s*mind\b|\bleave\s+it\b")

# Days suggest() looks past the requested ones before giving up (its later_days default).
_LATER_DAYS = 7


# ---------------------------------------------------------------- plans


def plan(goal: Goal, line: str, params: Optional[dict] = None, rung: int = RUNG_ASK, **overrides) -> GoalPlan:
    """A GoalPlan with critical / expect / closes_call taken from the contract table."""
    spec = GOAL_SPECS[goal]
    out = GoalPlan(goal=goal, line=line, params=dict(params or {}), rung=rung, critical=spec.critical,
                   expect=spec.expect, closes_call=goal in CLOSING_GOALS)
    for key, value in overrides.items():
        setattr(out, key, value)
    return out


def laddered(ctx: CallContext, goal: Goal, lines: tuple, params: Optional[dict] = None) -> GoalPlan:
    """
    The loop-breaker rung for a re-ask: each miss on `goal` moves one line
    down `lines` (ask -> rephrase -> choices), so a re-ask is never the same
    sentence (docs/R2_DESIGN.md, section 9).
    """
    idx = min(ctx.stats(goal).misses, len(lines) - 1)
    return plan(goal, lines[idx], params, rung=min(idx + 1, RUNG_CHOICES))


def phone_plan(ctx: CallContext, first_line: str) -> Optional[GoalPlan]:
    """
    The phone part of the identity checklist, shared by MANAGE and the
    callback flow (handlers.callback_goal): read-back, keep listening on a
    partial number, ask (in rungs), or close kindly after 3 failed read-backs.
    None once the number is confirmed.
    """
    c = ctx.caller
    if c.phone_state == FieldState.CONFIRMED and c.phone_e164:
        return None
    if c.phone_misses >= 3:
        return plan(Goal.CLOSE, "phone.failed", critical=True, use_model_say=False, steer=False, closes_call=True)
    if c.phone_state == FieldState.PENDING and c.phone_e164:
        return plan(Goal.CONFIRM_PHONE, "confirm.phone", {"phone": prompts.speak_phone(c.phone_e164)})
    if c.phone_buffer:
        return plan(Goal.PHONE_MORE, "phone.more", use_model_say=False)
    if c.phone_misses and ctx.stats(Goal.ASK_PHONE).misses == 0:
        return plan(Goal.ASK_PHONE, "confirm.phone.retry")
    return laddered(ctx, Goal.ASK_PHONE, (first_line, "ask.phone.rephrase", "ask.phone.choices"))


# ---------------------------------------------------------------- speaking appointments


def _slot_words(start: datetime) -> str:
    return prompts.speak_slot(start, today=clock.today())


def appointment_words(a: VerifiedAppointment) -> str:
    """ "Monday the 5th at 5, a cleaning with Dr Rao at Nagarbhavi" (only ever for a verified appointment)."""
    return f"{_slot_words(a.start)}, {prompts.speak_service(a.service)} with {a.doctor} at {a.branch}"


def _new_words(slot: OfferedSlot, target: VerifiedAppointment) -> str:
    """The new time, naming the doctor only when it changes."""
    words = slot.spoken or _slot_words(slot.start)
    return words if slot.doctor_id == target.doctor_id else f"{words} with {slot.doctor}"


def _pick_options(matches: list) -> str:
    names = {m.patient_name.strip().lower() for m in matches}
    parts = []
    for m in matches:
        words = f"{_slot_words(m.start)} with {m.doctor}"
        if len(names) > 1:
            words = f"{m.patient_name.split()[0]}'s, {words}"
        parts.append(words)
    return prompts.speak_list(parts)


# ---------------------------------------------------------------- verification


def _names(ctx: CallContext) -> list:
    """The names the appointment could be under: the one given for it, else the caller's own."""
    out = []
    for name in (ctx.manage.patient_name, ctx.caller.name):
        if name and name.strip() and name.strip() not in out:
            out.append(name.strip())
    return out


def _name_matches(said: str, booked: str) -> bool:
    """
    Callers often give only a first name ("Aarav" for "Aarav Sharma"), so the
    said name is compared with the full booked name and with its first and
    last words; STT spellings are tolerated by match.name_similarity.
    """
    said, booked = said.strip(), booked.strip()
    if not said or not booked:
        return False
    said_words, booked_words = said.split(), booked.split()
    pairs = [(said, booked), (said, booked_words[0]), (said, booked_words[-1]),
             (said_words[0], booked_words[0])]
    return max(match.name_similarity(a, b) for a, b in pairs) >= match.NAME_MATCH_RATIO


def _start(row: dict) -> datetime:
    return clock.localize(datetime.fromisoformat(row["start"]))


def _verified(row: dict) -> VerifiedAppointment:
    appt = VerifiedAppointment(
        appointment_id=row["id"], version=row["version"], patient_name=row["patient_name"],
        service=row["service"], service_id=row["service_id"], doctor=row["doctor"], doctor_id=row["doctor_id"],
        branch=row["branch"], branch_id=row["branch_id"], start=_start(row))
    appt.spoken = appointment_words(appt)
    return appt


async def verify(ctx: CallContext, rt: Runtime) -> bool:
    """
    Look up and filter; fills ctx.manage.matches only on success. Counts
    attempts. Needs a confirmed phone, a name and the appointment's date.
    """
    m = ctx.manage
    phone = ctx.caller.phone_e164 if ctx.caller.phone_state == FieldState.CONFIRMED else None
    names = _names(ctx)
    if not phone or not names or m.appt_date is None:
        return False
    m.verify_attempts += 1
    rows = await rt.run(scheduling.future_appointments, phone)
    days = set(m.appt_date.dates())
    hits = [r for r in rows
            if _start(r).date() in days and any(_name_matches(n, r["patient_name"]) for n in names)]
    if not hits:
        return False                 # the rows die here: nothing about them is kept (Z7)
    m.verified = True
    m.matches = [_verified(r) for r in hits]
    if len(m.matches) == 1:
        m.target = m.matches[0]
    return True


# ---------------------------------------------------------------- reading the caller's details


@dataclass
class _Heard:
    verify_detail: bool = False      # a name or appointment date for verification arrived this turn
    new_when: bool = False           # a new date / time for the reschedule arrived this turn
    reset_holds: bool = False        # the action changed: holds from the old one go


def _parse_when(text: str, expecting: Optional[str] = None, iso_hint: Optional[str] = None):
    when = dateparse.parse_when(text or "", expecting=expecting)
    if when.date is None and iso_hint:
        try:
            day = date.fromisoformat(iso_hint[:10])
            if day >= clock.today():
                when = dateparse.When(dateparse.DateConstraint(day, day), when.time, when.issues)
        except ValueError:
            pass
    return when


def _resolve_ampm(current, new):
    """'7' was ambiguous; "evening" or "7 pm" picks one of its candidates."""
    if current is None or current.kind != "ambiguous" or new is None:
        return new
    if new.kind == "exact" and new.start in current.candidates:
        return new
    if new.kind == "window":
        inside = [t for t in current.candidates if new.start <= t < new.end]
        if len(inside) == 1:
            return dateparse.TimeConstraint("exact", inside[0], label=current.label)
    return new


def _reset_action(m: ManageDraft) -> None:
    """A switch between check / cancel / reschedule keeps the verification, drops the rest."""
    m.cancel_reason = None
    m.reason_asked = False
    m.new_date_c = None
    m.new_time_c = None
    m.offered = []
    m.offer_rounds = 0
    m.chosen = None
    m.summary_heard = False
    m.done = False


def _absorb(ctx: CallContext, u: Understanding) -> _Heard:
    """
    Take the MANAGE details this turn carried. Before verification any date
    is the appointment's date; afterwards dates and times are the new time
    for a reschedule. Both can come in one sentence ("move my Monday one to
    Wednesday": appt_date_phrase + date_phrase).
    """
    m = ctx.manage
    heard = _Heard()
    if m.action != ctx.intent:
        if m.action is not None:
            _reset_action(m)
            heard.reset_holds = True
        m.action = ctx.intent

    was_verified = m.verified
    asked_date = ctx.pending in (Goal.ASK_APPT_DATE, Goal.VERIFY_FAILED)
    date_was_appt = False
    if not was_verified:
        name = u.patient_name or u.name_spelled or u.name
        if name and name.strip():
            m.patient_name = name.strip().title() if name.isupper() else name.strip()
            heard.verify_detail = True
        # A plain date is the appointment's own date when Emma asked for it, or
        # for a check / cancel; "move it to Friday" is the new time instead.
        phrase = u.appt_date_phrase
        asking = u.has(Act.QUESTION) and not u.has(Act.ANSWER)     # "What are your timings on Saturday?"
        if not phrase and u.date_phrase and not asking and (asked_date or m.action != Intent.RESCHEDULE):
            phrase, date_was_appt = u.date_phrase, True
        iso = u.date_iso_hint if asked_date and not u.appt_date_phrase else None
        if phrase or iso:
            when = _parse_when(phrase or "", "date" if asked_date else None, iso)
            if when.date is not None:
                m.appt_date = when.date
                heard.verify_detail = True
            date_was_appt = date_was_appt or iso is not None
        if heard.verify_detail and m.done:
            m.done = False           # they want another go after the callback offer was declined
        if not m.patient_name and ctx.caller.name:
            m.patient_name = ctx.caller.name     # most callers book under their own name

    if u.cancel_reason and u.cancel_reason.strip() and not m.done:
        m.cancel_reason = u.cancel_reason.strip()

    new_when_phrase = None
    if m.action == Intent.RESCHEDULE and (u.date_phrase or u.time_phrase) and not date_was_appt:
        if was_verified or not asked_date or u.appt_date_phrase:
            new_when_phrase = " ".join(p for p in (u.date_phrase, u.time_phrase) if p)
    if new_when_phrase and m.chosen is not None and ctx.pending == Goal.OFFER_NEW_SLOTS \
            and _fits(m.chosen.start, _parse_when(new_when_phrase, "time")):
        # "Let's do 9 in the morning" picked one of the offers (apply.py made
        # the pick): it is not a new time to search for, which would drop it.
        new_when_phrase = None
    if new_when_phrase:
        expecting = "time" if ctx.pending == Goal.RESOLVE_AMPM else (
            "date" if ctx.pending in (Goal.ASK_NEW_WHEN, Goal.NO_SLOTS) else None)
        # Sunday / past / outside-hours notices come from apply.py, which reads the same words.
        when = _parse_when(new_when_phrase, expecting, u.date_iso_hint if was_verified else None)
        if when.date is not None:
            m.new_date_c = when.date
            if when.time is not None:
                m.new_time_c = _resolve_ampm(m.new_time_c, when.time)
            heard.new_when = True
        elif when.time is not None:
            m.new_time_c = _resolve_ampm(m.new_time_c, when.time)
            heard.new_when = m.new_date_c is not None
    return heard


# ---------------------------------------------------------------- database helpers


def _checking(ctx: CallContext, rt: Runtime) -> None:
    """The varied "let me just check" the call session plays with typing (D3)."""
    try:
        phrase = prompts.checking_phrase(ctx.prompts)
    except Exception:                # a missing phrase must never stop a change
        phrase = None
    if phrase:
        rt.emit("before_action", phrase=phrase)
    else:
        rt.emit("before_action")


async def _release(rt: Runtime, keep: tuple = ()) -> None:
    try:
        await rt.run(scheduling.release_holds, rt.call_id, list(keep))
    except Exception:
        logger.exception("releasing holds failed for call %s", rt.call_id)


def _gone(ctx: CallContext, notices: list, line: str) -> None:
    """The appointment changed under us (cancelled elsewhere, or vanished): say so, offer to book."""
    m = ctx.manage
    if line in prompts.LINES:
        notices.append(Notice(line))
    m.target = None
    m.matches = []
    m.chosen = None
    m.offered = []
    m.done = True


async def _reload(ctx: CallContext, rt: Runtime, notices: list) -> bool:
    """STALE: the appointment changed since verification. Load it again and re-confirm."""
    m = ctx.manage
    row = await rt.run(scheduling.get_appointment, m.target.appointment_id)
    if row is None:
        _gone(ctx, notices, NOT_FOUND_LINE)
        return False
    if row["status"] not in ("booked", "needs_reschedule") or row.get("caller_phone_e164") != ctx.caller.phone_e164:
        _gone(ctx, notices, ALREADY_CANCELLED_LINE if row["status"] == "cancelled" else NOT_FOUND_LINE)
        return False
    fresh = _verified(row)
    m.matches = [fresh if a.appointment_id == fresh.appointment_id else a for a in m.matches]
    m.target = fresh
    m.summary_heard = False
    return True


def too_late(ctx: CallContext, appt: VerifiedAppointment) -> bool:
    """Already started, or (to move it) inside the booking lead time: scheduling would refuse."""
    now = clock.now()
    if appt.start <= now:
        return True
    return ctx.manage.action == Intent.RESCHEDULE and appt.start < now + timedelta(minutes=config.BOOKING_LEAD_MIN)


def _heard_yes(ctx: CallContext, goal: Goal, confirmation: Optional[str]) -> bool:
    return confirmation == "yes" and ctx.pending == goal and ctx.last_reply_heard


# ---------------------------------------------------------------- advance


async def advance(ctx: CallContext, u: Understanding, confirmation: Optional[str], rt: Runtime) -> ActionResult:
    """Verify, pick, search + hold, commit cancel / reschedule. action is set only after scheduling returned ok."""
    result = ActionResult()
    if ctx.intent not in MANAGE_INTENTS:
        return result
    m = ctx.manage
    had_target = m.target is not None
    heard = _absorb(ctx, u)
    if heard.reset_holds:
        await _release(rt)

    # The rebook offer after a cancel (or after an appointment turned out gone).
    if m.done and ctx.pending in (Goal.CANCELLED, Goal.OFFER_REBOOK):
        if confirmation == "yes":
            start_rebook(ctx)
        return result

    if not (ctx.caller.phone_state == FieldState.CONFIRMED and ctx.caller.phone_e164):
        return result
    if not m.verified:
        if _names(ctx) and m.appt_date is not None and (m.verify_attempts == 0 or heard.verify_detail):
            await verify(ctx, rt)
        if not m.verified:
            return result
    if m.target is None and not m.done:
        _pick(ctx, u)
    if m.target is None or m.done:
        return result
    # A new time said before the appointment was found ("move it to Friday")
    # is searched as soon as it is.
    if not had_target and _ready_to_search(m) and not m.offered and m.chosen is None:
        heard.new_when = True

    if m.action != Intent.RESCHEDULE and (m.offered or m.chosen is not None):
        m.offered, m.chosen = [], None       # switched away from a reschedule: give its slots back
        await _release(rt)

    if m.action == Intent.CHECK:
        if ctx.pending == Goal.STATE_APPOINTMENT:
            m.done = True
            ctx.outcome = ctx.outcome or "checked"
        return result
    if too_late(ctx, m.target):
        return result
    if m.action == Intent.CANCEL:
        return await _advance_cancel(ctx, u, confirmation, rt, result)
    if m.action == Intent.RESCHEDULE:
        return await _advance_reschedule(ctx, u, confirmation, rt, result, heard)
    return result


def _pick(ctx: CallContext, u: Understanding) -> None:
    """Several verified matches: by name first, then by the time (or "the first one")."""
    m = ctx.manage
    options = list(m.matches)
    if len(options) == 1:
        m.target = options[0]
        return
    if ctx.pending != Goal.PICK_APPOINTMENT:
        return
    if u.choice_index and 1 <= u.choice_index <= len(options):
        m.target = options[u.choice_index - 1]
        return
    name = u.patient_name or u.name
    if name:
        named = [a for a in options if _name_matches(name, a.patient_name)]
        if named and len(named) < len(options):
            options = named
    if u.time_phrase or u.date_phrase:
        when = _parse_when(" ".join(p for p in (u.date_phrase, u.time_phrase) if p), "time")
        timed = [a for a in options if _fits(a.start, when)]
        if timed:
            options = timed
    if len(options) == 1:
        m.target = options[0]


def _fits(start: datetime, when) -> bool:
    if when.date is not None and start.date() not in set(when.date.dates()):
        return False
    t = when.time
    if t is None:
        return when.date is not None
    if t.kind == "exact":
        return start.time() == t.start
    if t.kind == "ambiguous":
        return start.time() in t.candidates
    if t.kind == "window":
        return t.start <= start.time() < t.end
    return True


# ---------------------------------------------------------------- cancel


def _reason_text(u: Understanding, confirmation: Optional[str]) -> Optional[str]:
    """What the caller said when asked why, if it is a reason (not a bare "no" or a question)."""
    if u.cancel_reason:
        return u.cancel_reason.strip()
    text = (u.raw_text or "").strip()
    if not text or u.pure_question:
        return None
    if confirmation is not None and len(text.split()) <= 3:
        return None                  # "no", "not really", "yes sure"
    return text[:200]


async def _advance_cancel(ctx, u, confirmation, rt, result) -> ActionResult:
    m = ctx.manage
    if ctx.pending == Goal.CONFIRM_CANCEL and not m.summary_heard:
        if _heard_yes(ctx, Goal.CONFIRM_CANCEL, confirmation):
            # The clear yes to the summary is what cancels (Z1), so it goes
            # through on this turn. The reason is kept when the caller gives
            # one, but never asked between the yes and the cancel: a question
            # there would make the cancel hang on an unrelated reply.
            m.summary_heard = True
            m.reason_asked = True
            return await _commit_cancel(ctx, rt, result)
        elif confirmation == "no":
            m.done = True                    # keep it; policy moves on to anything else
        return result
    if ctx.pending == Goal.ASK_CANCEL_REASON and m.summary_heard:
        m.reason_asked = True
        if _KEEP_IT.search((u.raw_text or "").lower()):
            m.summary_heard = False
            m.done = True
            return result
        m.cancel_reason = m.cancel_reason or _reason_text(u, confirmation)
        return await _commit_cancel(ctx, rt, result)
    return result


async def _commit_cancel(ctx, rt, result) -> ActionResult:
    m = ctx.manage
    t = m.target
    _checking(ctx, rt)
    res = await rt.run(scheduling.cancel, t.appointment_id,
                       idem_key=f"{rt.idem_prefix}:cancel:{t.appointment_id}:{t.version}",
                       reason=m.cancel_reason, expected_version=t.version, call_id=rt.call_id)
    result.code = res.code
    if res.ok and res.code == "OK":
        result.ok, result.action, result.appointment_id = True, "cancelled", t.appointment_id
        # The outcome is a notice, said only now that scheduling returned ok
        # (Z2); the reply's question is the rebook offer (next_goal).
        result.notices.append(Notice("cancelled"))
        m.done = True
        ctx.outcome = "cancelled"
    elif res.code == "ALREADY_CANCELLED":
        _gone(ctx, result.notices, ALREADY_CANCELLED_LINE)
    elif res.code in ("NOT_FOUND", "NOT_ACTIVE"):
        _gone(ctx, result.notices, NOT_FOUND_LINE)
    elif res.code == "STALE":
        await _reload(ctx, rt, result.notices)
    elif res.code != "TOO_LATE":             # TOO_LATE: next_goal sees it from the clock
        logger.warning("cancel %s refused: %s", t.appointment_id, res.code)
        m.summary_heard = False              # never a claim: state it again and re-ask
    return result


# ---------------------------------------------------------------- reschedule


def _choose(offered: list, u: Understanding, confirmation: Optional[str]) -> Optional[OfferedSlot]:
    if u.choice_index and 1 <= u.choice_index <= len(offered):
        return offered[u.choice_index - 1]
    if u.time_phrase:
        when = _parse_when(" ".join(p for p in (u.date_phrase, u.time_phrase) if p), "time")
        if when.time is not None and when.time.kind in ("exact", "ambiguous"):
            fits = [s for s in offered if _fits(s.start, when)]
            if len(fits) == 1:
                return fits[0]
    if confirmation == "yes" and len(offered) == 1:
        return offered[0]
    return None


async def _advance_reschedule(ctx, u, confirmation, rt, result, heard) -> ActionResult:
    m = ctx.manage
    search = heard.new_when
    if m.chosen is not None and ctx.pending == Goal.CONFIRM_RESCHEDULE and not heard.new_when:
        if _heard_yes(ctx, Goal.CONFIRM_RESCHEDULE, confirmation):
            return await _commit_reschedule(ctx, rt, result)
        if confirmation == "no" or u.reject_options:
            m.chosen = None
            m.new_date_c = m.new_time_c = None
            m.offer_rounds += 1
            await _release(rt)
        return result
    if ctx.pending == Goal.OFFER_NEW_SLOTS and not heard.new_when:
        # apply.py may already have taken the pick (m.chosen) or the "none of
        # those" (offers cleared, round counted); finish either here.
        pick = m.chosen if m.chosen is not None else (_choose(m.offered, u, confirmation) if m.offered else None)
        if pick is not None:
            m.chosen, m.offered = pick, []
            await _release(rt, keep=(pick.hold_id,) if pick.hold_id else ())
            return result
        if u.reject_options or (confirmation == "no" and not u.choice_index):
            if m.offered:
                m.offer_rounds += 1
            m.offered = []
            m.new_date_c = m.new_time_c = None
            await _release(rt)
        return result
    if search:
        m.chosen = None
        await _search(ctx, rt, result)
        first = m.offered[0] if m.offered else None
        dc, tc = m.new_date_c, m.new_time_c
        if first is not None and dc is not None and dc.exact and tc is not None and tc.kind == "exact" \
                and first.start.date() == dc.start and first.start.time() == tc.start:
            # The exact new time asked for is free: straight to the move's
            # read-back, one yes instead of two (owner's decision, 7 Oct).
            m.chosen, m.offered = first, []
            await _release(rt, keep=(first.hold_id,) if first.hold_id else ())
            result.notices.append(Notice("exact.free", covered_by=("free",)))
    return result


def _ready_to_search(m: ManageDraft) -> bool:
    return m.new_date_c is not None and not (m.new_time_c is not None and m.new_time_c.kind == "ambiguous")


async def _search(ctx: CallContext, rt: Runtime, result: ActionResult) -> None:
    """
    scheduling.suggest for the same service at the same branch, with the
    appointment's own doctor first, ignoring the appointment itself so its
    current cells count as free; the top two (one doctor) are held.
    """
    m = ctx.manage
    t = m.target
    m.offered = []
    if not _ready_to_search(m) or m.offer_rounds >= 3:
        return
    if m.offer_rounds == 0:
        _checking(ctx, rt)
    await _release(rt)
    time_c = m.new_time_c if m.new_time_c is not None and m.new_time_c.kind != "any" else None
    common = dict(service=t.service_id, date_c=m.new_date_c, time_c=time_c, branch_ids=[t.branch_id],
                  call_id=rt.call_id, ignore_appointment=t.appointment_id)

    def same(slot) -> bool:
        return slot.doctor_id == t.doctor_id and slot.start == t.start

    sug = await rt.run(scheduling.suggest, doctor_id=t.doctor_id, **common)
    if sug.requested is not None and sug.requested == t.start:
        result.notices.append(Notice("same_slot"))
    slots = [s for s in sug.slots if not same(s)]
    if not slots:
        sug = await rt.run(scheduling.suggest, **common)
        slots = [s for s in sug.slots if not same(s)]
    if len(slots) > 1 and slots[1].doctor_id != slots[0].doctor_id:
        slots = slots[:1]            # the offer lines name one doctor
    for slot in slots[:2]:
        hold_id = await rt.run(scheduling.hold, slot, rt.call_id)
        if hold_id is None:
            continue
        m.offered.append(OfferedSlot(
            doctor_id=slot.doctor_id, doctor=slot.doctor, branch_id=slot.branch_id, branch=slot.branch,
            service_id=slot.service_id, service=slot.service, start=slot.start, end=slot.end,
            hold_id=hold_id, spoken=_slot_words(slot.start)))


async def _commit_reschedule(ctx, rt, result) -> ActionResult:
    m = ctx.manage
    t, c = m.target, m.chosen
    _checking(ctx, rt)
    res = await rt.run(scheduling.reschedule, t.appointment_id, doctor_id=c.doctor_id, start=c.start,
                       idem_key=f"{rt.idem_prefix}:reschedule:{t.appointment_id}:{t.version}",
                       expected_version=t.version, call_id=rt.call_id)
    result.code = res.code
    if res.ok and res.code == "OK":
        result.ok, result.action, result.appointment_id = True, "rescheduled", t.appointment_id
        if res.appointment:
            moved = _verified(res.appointment)
            m.matches = [moved if a.appointment_id == moved.appointment_id else a for a in m.matches]
            m.target = moved
        # The new time is heard in the same reply that commits it (Z2); policy
        # follows it with "anything else?".
        result.notices.append(Notice("rescheduled", {"new": _slot_words(m.target.start)}))
        m.done = True
        ctx.outcome = "rescheduled"
    elif res.code == "SAME_SLOT":
        result.notices.append(Notice("same_slot"))
        m.chosen = None
        m.new_date_c = m.new_time_c = None
        await _release(rt)
    elif res.code == "TOO_LATE":
        m.chosen = None
        await _release(rt)
    elif res.code == "STALE":
        await _reload(ctx, rt, result.notices)
    elif res.code in ("NOT_FOUND", "NOT_ACTIVE"):
        await _release(rt)
        status = (res.appointment or {}).get("status")
        _gone(ctx, result.notices, ALREADY_CANCELLED_LINE if status == "cancelled" else NOT_FOUND_LINE)
    else:                                    # TAKEN, or a rule re-validated at commit: offer again
        logger.info("reschedule %s refused: %s", t.appointment_id, res.code)
        result.notices.append(Notice("slot.gone"))
        m.chosen = None
        await _search(ctx, rt, result)
    return result


# ---------------------------------------------------------------- rebook


def start_rebook(ctx: CallContext) -> None:
    """
    "Would you like to book another time?" -> yes: a fresh BOOK draft that
    keeps the service and branch of the appointment just cancelled; name and
    phone carry over in ctx.caller (docs/R2_DESIGN.md, section 10.4).
    """
    old = ctx.manage.target
    draft = BookingDraft(draft_id=ctx.book.draft_id + 1)
    if old is not None:
        draft.service, draft.branch = old.service, old.branch
        draft.patient_name = None
    ctx.book = draft
    ctx.manage = ManageDraft()
    ctx.intent = Intent.BOOK


# ---------------------------------------------------------------- next goal


def next_goal(ctx: CallContext) -> Optional[GoalPlan]:
    """The first unmet MANAGE checklist item, or None when the change or check is complete."""
    if ctx.intent not in MANAGE_INTENTS:
        return None
    m = ctx.manage
    p = phone_plan(ctx, "ask.phone.manage")
    if p is not None:
        return p
    if not m.verified:
        return _verify_plan(ctx)
    if m.target is None:
        if m.done:
            return _rebook_plan(ctx)
        if len(m.matches) > 1:
            return plan(Goal.PICK_APPOINTMENT, "pick.appointment", {"options": _pick_options(m.matches)})
        return None
    t = m.target
    if m.action == Intent.CHECK:
        if m.done or ctx.pending == Goal.STATE_APPOINTMENT:
            return None
        return plan(Goal.STATE_APPOINTMENT, "state.appointment", {"appt": t.spoken or appointment_words(t)})
    if m.action == Intent.CANCEL:
        if m.done:
            return _rebook_plan(ctx) if ctx.outcome == "cancelled" else None
        if too_late(ctx, t):
            return plan(Goal.TOO_LATE, "too_late") if ctx.pending != Goal.TOO_LATE else None
        if not m.summary_heard:
            return plan(Goal.CONFIRM_CANCEL, "confirm.cancel", {"appt": t.spoken or appointment_words(t)})
        return plan(Goal.ASK_CANCEL_REASON, "ask.cancel_reason")
    if m.action == Intent.RESCHEDULE:
        return _reschedule_plan(ctx, t)
    return None


def _rebook_plan(ctx: CallContext) -> Optional[GoalPlan]:
    """
    After a cancel (or an appointment that turned out to be gone): offer
    another time, once. The "cancelled" notice went out with the commit, so
    this question follows it in the same reply.
    """
    if ctx.pending == Goal.OFFER_REBOOK or ctx.stats(Goal.OFFER_REBOOK).asked:
        return None
    return plan(Goal.OFFER_REBOOK, "offer.rebook")


def _verify_plan(ctx: CallContext) -> Optional[GoalPlan]:
    m = ctx.manage
    if m.done:
        return None
    fails = m.verify_attempts
    if fails >= 2 or (fails == 1 and ctx.stats(Goal.VERIFY_FAILED).misses >= 2):
        # Same words whatever the number holds; a task only if they say yes (handlers.py).
        if ctx.pending == Goal.CALLBACK_OFFER:
            return None
        return plan(Goal.CALLBACK_OFFER, "verify.failed.final", critical=True)
    if fails == 1:
        return laddered(ctx, Goal.VERIFY_FAILED, ("verify.failed", "ask.appt_date.rephrase"))
    if not _names(ctx):
        return laddered(ctx, Goal.ASK_NAME, ("ask.name.manage", "ask.name.rephrase"))
    return laddered(ctx, Goal.ASK_APPT_DATE, ("ask.appt_date", "ask.appt_date.rephrase"))


def _offer_plan(ctx: CallContext, t: VerifiedAppointment) -> GoalPlan:
    m = ctx.manage
    offered = m.offered
    first = offered[0]
    doctor = first.doctor
    dc, tc = m.new_date_c, m.new_time_c
    if len(offered) == 1:
        exact = (dc is not None and dc.exact and tc is not None and tc.kind == "exact"
                 and first.start.date() == dc.start and first.start.time() == tc.start)
        line = "offer.exact" if exact else "offer.one"
        params = {"slot": first.spoken, "doctor": doctor} if exact else {"a": first.spoken, "doctor": doctor}
        return plan(Goal.OFFER_NEW_SLOTS, line, params)
    a, b = offered[0].spoken, offered[1].spoken
    if dc is not None and dc.exact and all(s.start.date() != dc.start for s in offered):
        day = prompts.speak_day(dc.start, today=clock.today())
        return plan(Goal.OFFER_NEW_SLOTS, "offer.later_days", {"day": day, "a": a, "b": b})
    if offered[1].start.date() == first.start.date():
        b = prompts.speak_slot(offered[1].start, with_day=False)   # "Monday the 5th at 2:30 or 3", as book.py says it
    if offered[1].doctor != doctor:
        doctor = f"{doctor} for the first and {offered[1].doctor} for the second"
    return plan(Goal.OFFER_NEW_SLOTS, "offer.two", {"a": a, "b": b, "doctor": doctor})


def _reschedule_plan(ctx: CallContext, t: VerifiedAppointment) -> Optional[GoalPlan]:
    m = ctx.manage
    if m.done:
        return None                  # the "rescheduled" notice was said with the commit
    if too_late(ctx, t):
        return plan(Goal.TOO_LATE, "too_late") if ctx.pending != Goal.TOO_LATE else None
    if m.offer_rounds >= 3:
        return plan(Goal.CALLBACK_OFFER, "callback.offer") if ctx.pending != Goal.CALLBACK_OFFER else None
    if m.chosen is not None:
        return plan(Goal.CONFIRM_RESCHEDULE, "confirm.reschedule",
                    {"old": _slot_words(t.start), "new": _new_words(m.chosen, t)})
    if m.offered:
        return _offer_plan(ctx, t)
    if m.new_time_c is not None and m.new_time_c.kind == "ambiguous":
        hour = str(m.new_time_c.candidates[0].hour) if m.new_time_c.candidates else ""
        return plan(Goal.RESOLVE_AMPM, "resolve.ampm", {"hour": hour})
    if m.new_date_c is not None:
        if ctx.stats(Goal.NO_SLOTS).misses >= 2:
            return plan(Goal.CALLBACK_OFFER, "callback.offer") if ctx.pending != Goal.CALLBACK_OFFER else None
        until = prompts.speak_day(m.new_date_c.end + timedelta(days=_LATER_DAYS), today=clock.today())
        return plan(Goal.NO_SLOTS, "no_slots", {"branch": t.branch, "until": until})
    if m.offer_rounds and ctx.stats(Goal.ASK_NEW_WHEN).misses == 0:
        return plan(Goal.ASK_NEW_WHEN, "ask.when.after_reject")
    return laddered(ctx, Goal.ASK_NEW_WHEN, ("ask.new_when", "ask.when.rephrase", "ask.when.choices"))
