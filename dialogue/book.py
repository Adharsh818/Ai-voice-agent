"""
BOOK workflow on the scheduling engine (docs/R2_DESIGN.md, section 10.1).

State, as a checklist over context.BookingDraft (any order, nothing re-asked):

    name -> phone (read back, yes) -> max-3 / duplicate checks -> patient
    (only for someone else; age for pediatric) -> service (ambiguity; unknown
    treatment -> Consultation) -> branch (only branches whose doctors do the
    service; one branch -> say it) -> doctor preference (optional: catalog
    doctor, unknown name -> say so and name who is there, lady / male doctor)
    -> when (date; time of day; AM/PM) -> offer (scheduling.suggest, the top
    two held with scheduling.hold) -> choice -> summary (heard in full) ->
    clear yes -> scheduling.book -> BOOKED (+ emergency task when urgent)

Three entry points for the engine:
- take(ctx, u, rt): every booking detail the caller's turn carried, applied
  to the draft (service, branch, doctor, gender, patient, when, a pick among
  the offers, a rejection). Pure context work, no database, safe to call
  twice for the same Understanding (apply.py may call it; advance() always
  does). Returns the Notices this turn must say.
- advance(ctx, u, confirmation, rt): take(), answers to Emma's own BOOK
  questions (duplicate, max-3, a branch change for a doctor, a single offer,
  "no" to the summary), then the actions on the database thread: holds
  released or refreshed, the max-3 / duplicate checks once the phone is
  confirmed, the commit when the summary gate is open, a search when
  service, branch and when are settled and nothing valid is on offer.
- next_goal(ctx): the first unmet checklist item as a GoalPlan. Pure.

Guarantees (docs/SUCCESS_CRITERIA.md):
- Z6: a branch is only ever kept, searched or booked if one of its doctors
  does the service (facts.Catalog, derived from doctor_services); otherwise
  "branch.no_service" names the branches that do. scheduling re-validates
  the doctor's services at commit as the last guard.
- Z1: the commit runs only when Emma's last reply was the summary (pending
  SUMMARY / SUMMARY_AGAIN), the caller said a clear yes, the summary for
  this exact draft version was heard in full (summary_heard and
  ctx.last_reply_heard) and nothing changed since. Idempotency key
  f"{rt.idem_prefix}:book:{draft_id}:{version}", so a retried commit never
  makes a second appointment.
- Z2: action="booked" and the BOOKED goal only after scheduling.book
  returned ok. TAKEN / TOO_SOON at commit -> "slot.gone" + a fresh offer.
- Z7: the duplicate check says only that the patient already has an
  appointment; nothing about it (date, time, doctor) is stored or spoken.
- M1: the commit also checks every required detail is present (service, a
  branch offering it, a held slot, the patient's name, the confirmed phone).

Owner in Sprint 1b: E6.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time
from typing import Optional

import clock
import config
import dateparse
import prompts
import scheduling
from dateparse import DateConstraint, TimeConstraint
from dialogue import match
from dialogue.context import (GOAL_SPECS, Act, ActionResult, CallContext, Emergency, FieldState, Goal, GoalPlan,
                              Intent, Notice, OfferedSlot, Understanding)
from dialogue.runtime import Runtime, optional_module

logger = logging.getLogger(__name__)

# The service booked when the caller wants something the clinic doesn't book
# directly ("whitening", "implants"): the doctor sees them first.
CONSULTATION = "Consultation"
# Offer rounds the caller can turn down before Emma offers a callback (section 9).
MAX_OFFER_ROUNDS = 3
# How many offered slots are held at once (the caller hears at most two).
HOLD_TOP = 2
# Scheduling codes at commit that mean "that slot can't be had any more".
SLOT_LOST = ("TAKEN", "TOO_SOON")
# dateparse issue codes -> the notice that explains them.
ISSUE_LINES = {
    "PAST": "date.past",
    "SUNDAY": "date.sunday",
    "BEYOND_HORIZON": "date.horizon",
    "INVALID_DAY": "date.invalid_day",
    "OUTSIDE_HOURS": "time.outside_hours",
}
# scheduling reasons for a refused exact request that are worth saying.
REASON_LINES = {"OUTSIDE_HOURS": "time.outside_hours", "LUNCH": "time.lunch"}
GENDER_WORDS = {"female": "lady", "male": "male"}
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
               "October", "November", "December")


# ---------------------------------------------------------------- per-draft working notes
#
# A few facts the checklist needs across turns that BookingDraft has no field
# for (the checks' outcome, whether the first search happened, an empty
# search, the turn the booking was made). They live in the draft's instance
# dict, so they pickle with it, travel with a parked draft and vanish with a
# fresh one, and never take part in equality (dataclass fields only).
# Only counts and booleans are kept: nothing about another appointment (Z7).


def _notes(draft) -> dict:
    return draft.__dict__.setdefault("_book_notes", {})


def _plan(goal: Goal, line: str, **params) -> GoalPlan:
    spec = GOAL_SPECS[goal]
    return GoalPlan(goal=goal, line=line, params=params, critical=spec.critical, expect=spec.expect)


# ---------------------------------------------------------------- speakable forms (prompts.py owns the wording)


def speak_slot(start: datetime, with_day: bool = True) -> str:
    return prompts.speak_slot(start, with_day=with_day)


def speak_hour(t: time) -> str:
    """ "7" / "7:30" for "7 in the morning or the evening?" (the line adds the day part)."""
    hour = t.hour % 12 or 12
    return f"{hour}:{t.minute:02d}" if t.minute else str(hour)


def _spoken_service(name: str) -> str:
    """ "Root Canal Treatment" -> "a root canal" (the summary, the service options)."""
    return prompts.speak_service(name)


def _service_plural(name: str) -> str:
    """ "root canals", "braces": reads right in "We do {service} at Indiranagar or Whitefield"."""
    return prompts.service_plural(name) or prompts.speak_service(name)


def checking_phrase(ctx: CallContext) -> str:
    """A varied "let me just check" (D3), never the same one back to back."""
    return prompts.checking_phrase(ctx.prompts)


def _same_name(a: str, b: str) -> bool:
    return bool(a and b) and match.name_similarity(a, b) >= match.NAME_MATCH_RATIO


def _title(name: Optional[str]) -> Optional[str]:
    name = " ".join((name or "").split())
    return name.title() if name and name.islower() else (name or None)


# ---------------------------------------------------------------- catalog helpers


def _consultation(catalog) -> Optional[str]:
    svc = catalog.service(CONSULTATION)
    if svc is None:
        svc = next((s for s in catalog.services if s.is_consultation), None)
    return svc.name if svc else None


def _is_pediatric(service: Optional[str]) -> bool:
    return bool(service) and "pediatric" in service.lower()


def _branch_names(catalog) -> list:
    return [b.name for b in catalog.branches]


def offering_branches(ctx: CallContext, catalog) -> list:
    """
    Branches that can see this patient for the draft's service, in catalog
    order: one of their doctors does it (and is the preferred doctor, or of
    the preferred gender, when there is a preference).
    """
    b = ctx.book
    if not b.service:
        return []
    names = set(catalog.branches_offering(b.service))
    if b.doctor_id is not None:
        doc = _doctor_by_id(catalog, b.doctor_id)
        names &= {doc.branch} if doc else set()
    elif b.doctor_gender:
        names &= {d.branch for d in catalog.doctors_for(b.service, None, b.doctor_gender)}
    return [n for n in _branch_names(catalog) if n in names]


def _doctor_by_id(catalog, doctor_id: Optional[int]):
    return next((d for d in catalog.doctors if d.id == doctor_id), None)


def _doctors_line(catalog, service: Optional[str], branch: Optional[str]) -> tuple:
    """(doctor names, branch words) for "We don't have a Dr Sharma, but Dr Rao and Dr Shetty are at Nagarbhavi"."""
    if branch:
        docs = catalog.doctors_for(service, branch) if service else [
            d for d in catalog.doctors if d.branch.lower() == branch.lower()]
        return [d.spoken for d in docs], branch
    docs = catalog.doctors_for(service) if service else []
    branches = [n for n in _branch_names(catalog) if n in {d.branch for d in docs}]
    return [d.spoken for d in docs], prompts.speak_list(branches, "and")


# ---------------------------------------------------------------- applying the caller's details


def take(ctx: CallContext, u: Understanding, rt: Runtime) -> list:
    """
    Apply every booking detail in `u` to ctx.book; return the Notices.
    Idempotent per Understanding: a second call with the same `u` does
    nothing (apply.py and advance() may both call it). When apply.py applies
    the booking details itself, it marks `u` with u.__dict__["_book_taken"]
    = True so they are not applied (and their notices said) twice; the hold
    and rejection bookkeeping in advance() copes with either order. Every change to a
    detail the summary depends on calls ctx.book.touch(); a change to what
    the offers were searched for drops the offers (their holds are released
    in advance()).
    """
    if u is None or u.__dict__.get("_book_taken"):
        return []
    u.__dict__["_book_taken"] = True
    catalog = rt.catalog
    notices: list = []
    b = ctx.book

    # A pick among the offers comes first, so "5:30" picks the 5:30 slot
    # instead of becoming a new time to search for. A pick apply.py already
    # made counts too, or its time would start a new search and drop it.
    picked = _take_choice(ctx, u) or _picked_already(ctx, u)

    # A change apply.py put to the caller ("Did you want to change the date
    # to Tuesday?") waits for their yes: that detail is not taken here.
    proposal = ctx.change_proposal if ctx.change_proposal.get("source") != "book" else {}
    held_back = {proposal.get("field")} if proposal else set()

    _take_patient(ctx, u)
    if "service" not in held_back:
        notices += _take_service(ctx, u, catalog)
    if "branch" in held_back:
        pass
    elif u.branch_any and not u.branch:
        if not b.branch_any or b.branch:
            b.branch_any, b.branch = True, None
            _changed(ctx, search=True)
    elif u.branch:
        branch = catalog.branch(u.branch)
        if branch is not None and branch.name != b.branch:
            b.branch, b.branch_any = branch.name, False
            _changed(ctx, search=True)
    if "doctor" not in held_back:
        notices += _take_doctor(ctx, u, catalog)
    if not picked and "date" not in held_back:
        notices += _take_when(ctx, u)
        if b.offered and b.chosen is None and u.reject_options:
            _reject_offers(ctx)
    return notices


def _changed(ctx: CallContext, *, search: bool = False):
    """A summary detail changed: touch(); if the search inputs changed, drop the offers and the pick."""
    b = ctx.book
    b.touch()
    notes = _notes(b)
    notes.pop("what_to_change", None)
    if search:
        if b.offered and b.chosen is None:
            b.offer_rounds += 1                            # a new ask while slots were on offer turns them down
        if b.offered or b.chosen is not None:
            notes["release"] = True
        b.offered, b.chosen = [], None
        notes.pop("no_slots", None)


def _take_choice(ctx: CallContext, u: Understanding) -> bool:
    b = ctx.book
    if not b.offered or b.chosen is not None:
        return False
    pick = None
    if u.choice_index is not None and 1 <= u.choice_index <= len(b.offered):
        pick = b.offered[u.choice_index - 1]
    elif u.time_phrase or u.date_phrase:
        when = dateparse.parse_when(" ".join(p for p in (u.date_phrase, u.time_phrase) if p), expecting="time")
        if when.time is not None and when.time.kind == "exact":
            hits = [s for s in b.offered if s.start.time() == when.time.start
                    and (when.date is None or (when.date.exact and s.start.date() == when.date.start))]
            if len(hits) == 1:
                pick = hits[0]
    if pick is None:
        return False
    _choose(ctx, pick)
    return True


def _picked_already(ctx: CallContext, u: Understanding) -> bool:
    """
    The slot on the draft was picked by this very answer (apply.py runs
    first in the engine and may have made the pick): an index, or a time
    that is exactly the chosen slot's.
    """
    b = ctx.book
    if b.chosen is None or b.chosen not in b.offered or ctx.pending not in (Goal.OFFER_SLOTS, Goal.NO_SLOTS):
        return False
    if u.choice_index is not None:
        return True
    if not (u.time_phrase or u.date_phrase):
        return False
    when = dateparse.parse_when(" ".join(p for p in (u.date_phrase, u.time_phrase) if p), expecting="time")
    return when.time is not None and when.time.kind == "exact" and when.time.start == b.chosen.start.time()


def _exact_free(b) -> bool:
    """The first slot found is exactly the day and time the caller asked for, at their branch."""
    if not b.offered or b.date_c is None or not b.date_c.exact or b.time_c is None or b.time_c.kind != "exact":
        return False
    notes = _notes(b)
    first = b.offered[0]
    if notes.get("other_branch") and first.branch != notes["other_branch"]:
        return False
    return first.start.date() == b.date_c.start and first.start.time() == b.time_c.start


def _choose(ctx: CallContext, slot: OfferedSlot):
    b = ctx.book
    b.chosen = slot
    if b.branch and slot.branch != b.branch:
        b.branch = slot.branch                       # picked from another branch's offer (same day elsewhere)
    b.touch()
    _notes(b)["keep_hold"] = slot.hold_id


def _reject_offers(ctx: CallContext):
    """
    None of the offers suit: free them, forget the when, ask what would work
    better. The round is counted against the offer that was turned down, so
    a rejection apply.py already counted is not counted twice.
    """
    b = ctx.book
    offer = _notes(b).pop("offer", None) or {}
    b.offer_rounds = max(b.offer_rounds, offer.get("round", b.offer_rounds) + 1)
    b.offered, b.chosen = [], None
    b.date_c, b.time_c, b.when_phrase, b.any_time = None, None, "", False
    b.touch()
    notes = _notes(b)
    notes["release"] = True
    notes.pop("no_slots", None)


def _take_patient(ctx: CallContext, u: Understanding):
    b = ctx.book
    if u.for_someone_else is False and b.for_someone_else and not u.patient_name:
        b.for_someone_else, b.patient_name, b.relation, b.age = False, None, None, None
        b.touch()
        return
    someone_else = bool(u.for_someone_else or u.patient_name or u.relation)
    if someone_else and not b.for_someone_else:
        b.for_someone_else = True
        b.touch()
    if u.relation and u.relation != b.relation:
        b.relation = u.relation
    name = u.patient_name
    if not name and ctx.pending == Goal.ASK_PATIENT and u.name:
        name = u.name                                      # a bare name answering "what's their name?"
    name = _title(name)
    if name and name != b.patient_name:
        b.for_someone_else = True
        b.patient_name = name
        b.touch()
    if u.age is not None and 0 < u.age < 120 and u.age != b.age:
        b.age = u.age
        b.touch()


def _take_service(ctx: CallContext, u: Understanding, catalog) -> list:
    b = ctx.book
    notices: list = []
    service = None
    if u.service:
        svc = catalog.service(u.service)
        service = svc.name if svc else None
    if service is None and u.service_phrase and u.service_phrase != b.service_phrase:
        phrase = u.service_phrase.strip()
        found = match.match_service(phrase, list(catalog.services), asked=ctx.pending == Goal.ASK_SERVICE)
        if found.value:
            service = found.value
        elif len(found.options) > 1:
            b.service_phrase, b.service_options = phrase, list(found.options)
            return notices
        else:
            # Not something the clinic books directly ("whitening", "implants"):
            # the doctor sees them first, and the caller hears why.
            consult = _consultation(catalog)
            if consult and consult != b.service:
                notices.append(Notice("service.unknown", {"phrase": found.unknown_phrase or phrase}))
                service = consult
        b.service_phrase = phrase
    if service and service != b.service:
        b.service = service
        b.service_options = []
        if u.service_phrase:
            b.service_phrase = u.service_phrase
        _changed(ctx, search=True)
    return notices


def _take_doctor(ctx: CallContext, u: Understanding, catalog) -> list:
    b = ctx.book
    notices: list = []
    doc = catalog.doctor(u.doctor) if u.doctor else None
    phrase = u.doctor_phrase or (u.doctor if u.doctor and doc is None else None)
    if doc is not None and doc.id != b.doctor_id:
        if b.service and b.service not in doc.services:
            return notices                                 # he doesn't do it: the offer names who does
        if b.branch and doc.branch.lower() != b.branch.lower():
            notices.append(Notice("doctor.other_branch", {"doctor": doc.spoken, "branch": doc.branch}))
            ctx.change_proposal = {"field": "branch", "value": doc.branch, "doctor_id": doc.id,
                                   "doctor": doc.spoken, "source": "book"}
            return notices
        b.doctor_id, b.doctor, b.doctor_gender = doc.id, doc.spoken, None
        if not b.branch:
            b.branch, b.branch_any = doc.branch, False
            notices.append(Notice("doctor.other_branch", {"doctor": doc.spoken, "branch": doc.branch},
                                  covered_by=(doc.branch,)))
        _changed(ctx, search=True)
    elif phrase and not doc:
        b.unknown_doctor = phrase.strip()
        if b.doctor_id is not None:
            b.doctor_id, b.doctor = None, None
            _changed(ctx, search=True)
    if u.doctor_gender in GENDER_WORDS and doc is None and u.doctor_gender != b.doctor_gender:
        b.doctor_gender = u.doctor_gender
        b.doctor_id, b.doctor = None, None
        _changed(ctx, search=True)
    return notices


def _take_when(ctx: CallContext, u: Understanding) -> list:
    b = ctx.book
    notices: list = []
    if not (u.date_phrase or u.time_phrase or u.date_iso_hint):
        return notices
    date_c, time_c, issues = None, None, []
    if u.date_phrase:
        expecting = "date" if ctx.pending in (Goal.ASK_WHEN, Goal.NO_SLOTS) else None
        w = dateparse.parse_when(u.date_phrase, expecting=expecting)
        date_c, time_c, issues = w.date, w.time, list(w.issues)
    if u.time_phrase:
        expecting = "time" if ctx.pending in (Goal.ASK_TIME, Goal.RESOLVE_AMPM) or date_c or b.date_c else None
        w = dateparse.parse_when(u.time_phrase, expecting=expecting)
        if w.time is not None:
            time_c = w.time
        if date_c is None and w.date is not None:
            date_c = w.date
        issues += [i for i in w.issues if i.code not in {x.code for x in issues}]
    if date_c is None and u.date_iso_hint and not any(i.code in ISSUE_LINES and i.code != "OUTSIDE_HOURS"
                                                      for i in issues):
        date_c, more = _iso_date(u.date_iso_hint)
        issues += more

    # "Morning" / "evening" answering "7 in the morning or the evening?"
    if b.time_c is not None and b.time_c.kind == "ambiguous" and time_c is not None and date_c is None:
        resolved = _resolve_ampm(b.time_c, time_c)
        if resolved is not None:
            time_c = resolved

    for issue in issues:
        line = ISSUE_LINES.get(issue.code)
        if line == "date.invalid_day":
            month = issue.detail.get("month")
            notices.append(Notice(line, {"month": MONTH_NAMES[month - 1] if isinstance(month, int)
                                         and 1 <= month <= 12 else "That month",
                                         "day": prompts.ordinal(issue.detail.get("day") or 0)}))
        elif line:
            notices.append(Notice(line))

    changed = False
    if date_c is not None and date_c != b.date_c:
        b.date_c = date_c
        b.when_phrase = u.date_phrase or b.when_phrase
        changed = True                                     # a new day keeps the time of day already given
    if time_c is not None and time_c != b.time_c:
        b.time_c = time_c
        changed = True
    if time_c is not None and time_c.kind == "any":
        b.any_time = True
    if changed:
        _changed(ctx, search=True)
    return notices


def _iso_date(hint: str) -> tuple:
    """The model's date hint, only when dateparse couldn't read the phrase; checked like any other date."""
    try:
        day = date.fromisoformat(hint.strip()[:10])
    except (ValueError, AttributeError):
        return None, []
    check = getattr(dateparse, "_check_date", None)
    if check is None:
        return DateConstraint(day, day), []
    dc, issues = check(DateConstraint(day, day), clock.today())
    return dc, issues


def _resolve_ampm(ambiguous: TimeConstraint, answer: TimeConstraint) -> Optional[TimeConstraint]:
    for cand in ambiguous.candidates:
        if answer.kind == "exact" and answer.start == cand:
            return TimeConstraint("exact", cand, label=ambiguous.label)
        if answer.kind == "window" and answer.start <= cand < answer.end:
            return TimeConstraint("exact", cand, label=ambiguous.label)
    return None


# ---------------------------------------------------------------- answers to Emma's own BOOK questions


def _answer(ctx: CallContext, u: Understanding, confirmation: Optional[str]) -> list:
    b = ctx.book
    notes = _notes(b)
    pending = ctx.pending
    notices: list = []

    if pending == Goal.CONFIRM_CHANGE and ctx.change_proposal.get("source") == "book" and confirmation:
        proposal, ctx.change_proposal = ctx.change_proposal, {}
        if confirmation == "yes":
            b.branch, b.branch_any = proposal["value"], False
            if proposal.get("doctor_id") is not None:
                b.doctor_id, b.doctor, b.doctor_gender = proposal["doctor_id"], proposal.get("doctor"), None
            _changed(ctx, search=True)
        return notices

    if pending == Goal.DUPLICATE_CHECK and not b.duplicate_ok:
        if confirmation == "yes" or u.choice_index == 1 or u.intent == Intent.BOOK:
            b.duplicate_ok = True
    elif pending == Goal.MAX_REACHED and confirmation == "no":
        notes["max_declined"] = True
    elif pending == Goal.OFFER_SLOTS and b.offered and b.chosen is None and u.choice_index is None:
        if confirmation == "yes" and len(b.offered) == 1:
            _choose(ctx, b.offered[0])
        elif _restates_time_at_offer(b, u):
            # "No, 5 in the evening" after hearing 4 was the nearest: look at 5
            # on the following days too, instead of saying 4 again (sim 6 Oct).
            notices.append(Notice("time.taken", {"time": speak_hour(b.time_c.start)}))
            notes["same_time_later"] = _search_key(b)
            _changed(ctx, search=True)
        elif confirmation == "no" and not (u.date_phrase or u.time_phrase or u.branch or u.doctor):
            _reject_offers(ctx)
    elif pending in (Goal.SUMMARY, Goal.SUMMARY_AGAIN, Goal.WHAT_TO_CHANGE) and _restates_request(b, u):
        # "No, it should be 11:30" (or "the 15th") when that is what they asked
        # for and it wasn't free: search again, so they hear it's taken and what
        # is free, instead of "what should I change?" on a loop. Each time counts
        # as a turned-down offer, so the offer ladder still ends on a callback.
        if _restates_request(b, u) == "time":
            notices.append(Notice("time.taken", {"time": speak_hour(b.time_c.start)}))
            notes["same_time_later"] = _search_key(b)
        b.offer_rounds += 1
        _changed(ctx, search=True)
        if b.offer_rounds >= MAX_OFFER_ROUNDS:
            _reject_offers(ctx)
    elif pending in (Goal.SUMMARY, Goal.SUMMARY_AGAIN) and confirmation == "no" \
            and b.summary_version == b.version:
        notes["what_to_change"] = b.version
    elif pending == Goal.WHAT_TO_CHANGE and notes.get("what_to_change") == b.version \
            and not (u.has(Act.QUESTION) or u.has(Act.FRAGMENT)):
        # "I just want the cleaning booked" / "nothing": no detail named to
        # change, so the booking stands as summarised. Ask about the summary
        # again instead of "what should I change?" a second time (the 1 Oct
        # recap loop).
        notes.pop("what_to_change", None)

    # The offers were turned down but are already gone (apply.py dropped
    # them first) while the search inputs are unchanged: forget the when as
    # well, or the same search would offer the same slots again (the
    # "braces 8 AM x3" loop).
    offer = notes.get("offer")
    rejected = u.reject_options or (pending == Goal.OFFER_SLOTS and confirmation == "no" and u.choice_index is None
                                    and not (u.date_phrase or u.time_phrase or u.branch or u.doctor))
    if rejected and offer and not b.offered and b.chosen is None and offer.get("key") == _search_key(b):
        _reject_offers(ctx)
    return notices


# ---------------------------------------------------------------- keeping the draft valid


def _restates_time_at_offer(b, u: Understanding) -> bool:
    """The caller says again the exact time they asked for, and none of the offered slots is at it."""
    asked = b.time_c.start if b.time_c is not None and b.time_c.kind == "exact" else None
    if asked is None or not u.time_phrase or any(s.start.time() == asked for s in b.offered):
        return False
    when = dateparse.parse_when(" ".join(p for p in (u.date_phrase, u.time_phrase) if p), expecting="time")
    if when.date is not None and b.date_c is not None and when.date != b.date_c:
        return False                                  # a new day: an ordinary change, not a repeat
    return when.time is not None and when.time.kind == "exact" and when.time.start == asked


def _restates_request(b, u: Understanding) -> Optional[str]:
    """
    "time" / "date" when the caller repeats the exact time (or day) they asked
    for and the slot on the draft isn't it (it wasn't free); else None.
    """
    if b.chosen is None or not (u.time_phrase or u.date_phrase):
        return None
    when = dateparse.parse_when(" ".join(p for p in (u.date_phrase, u.time_phrase) if p), expecting="time")
    asked_time = b.time_c.start if b.time_c is not None and b.time_c.kind == "exact" else None
    if u.time_phrase and asked_time is not None and when.time is not None and when.time.kind == "exact" \
            and when.time.start == asked_time and b.chosen.start.time() != asked_time:
        return "time"
    asked_day = b.date_c.start if b.date_c is not None and b.date_c.exact else None
    if not u.time_phrase and asked_day is not None and when.date is not None and when.date.exact \
            and when.date.start == asked_day and b.chosen.start.date() != asked_day:
        return "date"
    return None


def _validate(ctx: CallContext, catalog) -> list:
    """
    Make the draft consistent with the catalog after this turn's details:
    branch vs service (Z6), doctor and gender preferences, the one-branch
    case, the unknown doctor's notice. Each problem is reported once,
    because fixing it removes it.
    """
    b = ctx.book
    notices: list = []

    if b.doctor_id is not None and b.service:
        doc = _doctor_by_id(catalog, b.doctor_id)
        if doc is None or b.service not in doc.services:
            b.doctor_id, b.doctor = None, None
            _changed(ctx, search=True)
    if b.doctor_id is not None and b.branch and not ctx.change_proposal:
        doc = _doctor_by_id(catalog, b.doctor_id)
        if doc is None or doc.branch.lower() != b.branch.lower():
            # The caller kept the branch rather than follow the doctor: the
            # branch wins, so the search isn't filtered down to nobody.
            b.doctor_id, b.doctor = None, None
            _changed(ctx, search=True)

    if b.doctor_gender and b.service:
        anywhere = catalog.doctors_for(b.service, None, b.doctor_gender)
        here = catalog.doctors_for(b.service, b.branch, b.doctor_gender) if b.branch else anywhere
        if not here:
            names, where = _doctors_line(catalog, b.service, b.branch)
            if not b.branch:
                where = prompts.speak_list(catalog.branches_offering(b.service), "or")
            notices.append(Notice("doctor.gender_none", {"gender_word": GENDER_WORDS[b.doctor_gender],
                                                         "branch": where, "doctors": prompts.speak_list(names, "and")}))
            b.doctor_gender = None
            _changed(ctx, search=True)

    if b.service and b.branch:
        offering = catalog.branches_offering(b.service)
        if b.branch not in offering:
            others = [n for n in _branch_names(catalog) if n in offering]
            notices.append(Notice("branch.no_service", {"branch": b.branch,
                                                        "service": _service_plural(b.service),
                                                        "branches": prompts.speak_list(others, "and")}))
            b.branch = others[0] if len(others) == 1 else None
            _changed(ctx, search=True)

    if b.service and not b.branch and not b.branch_any:
        options = offering_branches(ctx, catalog)
        if len(options) == 1:
            b.branch = options[0]
            notices.append(Notice("branch.only", {"service": _service_plural(b.service),
                                                  "branch": options[0]}, covered_by=(options[0],)))
            _changed(ctx, search=True)

    if b.unknown_doctor and (b.service or b.branch):
        names, where = _doctors_line(catalog, b.service, b.branch)
        if names:
            notices.append(Notice("doctor.unknown", {"name": b.unknown_doctor, "doctors": prompts.speak_list(names, "and"),
                                                     "are_is": "are" if len(names) > 1 else "is",
                                                     "branch": where}))
        b.unknown_doctor = None                            # said once, then cleared

    if b.emergency or ctx.emergency == Emergency.URGENT:
        if not b.emergency:
            b.emergency = True
            b.touch()
        if not b.service:
            consult = _consultation(catalog)
            if consult:
                b.service = consult
                _changed(ctx, search=True)
        if b.date_c is None:
            today = clock.today()
            b.date_c, b.any_time = DateConstraint(today, today), True
            _changed(ctx, search=True)
    return notices


# ---------------------------------------------------------------- the checklist


def _phone_plan(ctx: CallContext) -> Optional[GoalPlan]:
    c = ctx.caller
    if c.phone_state == FieldState.CONFIRMED and c.phone_e164:
        return None
    if c.phone_state == FieldState.PENDING and c.phone_e164:
        return _plan(Goal.CONFIRM_PHONE, "confirm.phone", phone=prompts.speak_phone(c.phone_e164))
    if c.phone_buffer:
        return _plan(Goal.PHONE_MORE, "phone.more")
    return _plan(Goal.ASK_PHONE, "ask.phone")


def _patient_name(ctx: CallContext) -> Optional[str]:
    b = ctx.book
    return b.patient_name if b.for_someone_else else ctx.caller.name


def _settled_when(b) -> bool:
    if b.date_c is None:
        return False
    if b.time_c is not None and b.time_c.kind == "ambiguous":
        return False
    return b.time_c is not None or b.any_time or b.date_c.kind == "earliest" or b.emergency


def _search_ready(ctx: CallContext) -> bool:
    """
    Service, branch and when settled, and the caller known: name and a
    confirmed phone come first (D2), so the max-3 and duplicate checks always
    run before a slot is held.
    """
    b, c = ctx.book, ctx.caller
    return bool(b.service and not b.service_options and (b.branch or b.branch_any) and _settled_when(b)
                and c.name and c.phone_e164 and c.phone_state == FieldState.CONFIRMED
                and not ctx.change_proposal)               # "change the branch?" is answered first


def _search_key(b) -> str:
    return repr((b.service, b.branch, b.branch_any, b.doctor_id, b.doctor_gender, b.date_c, b.time_c,
                 b.any_time, b.emergency))


def next_goal(ctx: CallContext) -> Optional[GoalPlan]:
    """The first unmet BOOK checklist item as a GoalPlan (line ids from prompts.LINES), or None when done."""
    b = ctx.book
    notes = _notes(b)
    c = ctx.caller

    if b.appointment_id:
        if notes.get("booked_turn") == ctx.turn:
            when = speak_slot(b.chosen.start) if b.chosen else ""
            branch = b.chosen.branch if b.chosen else (b.branch or "")
            line = "booked.emergency" if b.emergency else "booked"
            return _plan(Goal.BOOKED, line, when=when, branch=branch)
        return None

    if ctx.change_proposal.get("source") == "book":
        return _plan(Goal.CONFIRM_CHANGE, "confirm_change", field="branch", value=ctx.change_proposal["value"])

    if not c.name:
        return _plan(Goal.ASK_NAME, "ask.name")
    phone = _phone_plan(ctx)
    if phone is not None:
        return phone

    checks = notes.get("checks") or {}
    if checks.get("future", 0) >= config.MAX_FUTURE_APPOINTMENTS_PER_PHONE:
        if notes.get("max_declined"):
            return _plan(Goal.ANYTHING_ELSE, "anything_else")
        return _plan(Goal.MAX_REACHED, "max_reached")
    if b.for_someone_else and not b.patient_name:
        their = f"your {b.relation}'s" if b.relation else "their"
        return _plan(Goal.ASK_PATIENT, "ask.patient", relation_or_their=their)
    if checks.get("dup") and not b.duplicate_ok:
        return _plan(Goal.DUPLICATE_CHECK, "duplicate", patient=_patient_name(ctx))

    if b.service_options:
        return _plan(Goal.CLARIFY_SERVICE, "clarify.service",
                     options=prompts.speak_list([_spoken_service(o) for o in b.service_options], "or"))
    if not b.service:
        return _plan(Goal.ASK_SERVICE, "ask.service")
    if _is_pediatric(b.service) and b.age is None:
        return _plan(Goal.ASK_AGE, "ask.age", patient=_patient_name(ctx))
    if not b.branch and not b.branch_any:
        return _plan(Goal.ASK_BRANCH, "ask.branch", service=_service_plural(b.service),
                     branches=prompts.speak_list(notes.get("branch_options") or [], "or"))

    if b.date_c is None:
        if b.offer_rounds >= MAX_OFFER_ROUNDS:
            return _plan(Goal.CALLBACK_OFFER, "callback.offer")
        if b.offer_rounds:
            return _plan(Goal.ASK_WHEN, "ask.when.after_reject")
        return _plan(Goal.ASK_WHEN, "ask.when")
    if b.time_c is not None and b.time_c.kind == "ambiguous":
        return _plan(Goal.RESOLVE_AMPM, "resolve.ampm", hour=speak_hour(b.time_c.candidates[0]))
    if not _settled_when(b):
        day = prompts.speak_day(b.date_c.start) if b.date_c.exact else (b.when_phrase or prompts.speak_when(b.date_c, None))
        return _plan(Goal.ASK_TIME, "ask.time", day=day)

    if b.chosen is None:
        if b.offered:
            return _offer_plan(b)
        gap = notes.get("no_slots")
        if gap:
            return _plan(Goal.NO_SLOTS, "no_slots", branch=gap["branch"], until=gap["until"])
        return _plan(Goal.OFFER_SLOTS, "checking")         # about to search (a plan hint before advance)

    if notes.get("what_to_change") == b.version:
        return _plan(Goal.WHAT_TO_CHANGE, "what_to_change")
    params = summary_params(ctx)
    if b.summary_version == b.version and (b.summary_heard or (
            ctx.pending in (Goal.SUMMARY, Goal.SUMMARY_AGAIN) and not ctx.last_reply_heard)):
        return _plan(Goal.SUMMARY_AGAIN, "summary.again", **params)
    return _plan(Goal.SUMMARY, "summary", **params)


def _search_patient(ctx: CallContext) -> Optional[tuple]:
    """(phone, name) whose own appointments a search must avoid, once both are known (sim 6 Oct clash loop)."""
    c = ctx.caller
    name = _patient_name(ctx)
    if c.phone_state != FieldState.CONFIRMED or not c.phone_e164 or not name:
        return None
    return c.phone_e164, scheduling.norm_name(name)


def _in_clock_order(slots: list) -> list:
    """Two times on one day are said in clock order ("5 or 5:30", never "5:30 or 5"); "the first one" follows."""
    if len(slots) > 1 and len({s.start.date() for s in slots}) == 1:
        return sorted(slots, key=lambda s: s.start)
    return slots


def _offer_key(b) -> tuple:
    return tuple(s.start.isoformat() + s.branch for s in b.offered)


def _two_spoken(first, second) -> tuple:
    """(a, b) for saying two offered slots: the second without its day when it's the same day."""
    b_spoken = second.spoken if second.start.date() != first.start.date() \
        else speak_slot(second.start, with_day=False)
    return first.spoken, b_spoken


def _offer_plan(b) -> GoalPlan:
    slots = b.offered
    first = slots[0]
    elsewhere = _notes(b).get("same_time_branch")
    if elsewhere and elsewhere[1] == _offer_key(b):
        return _plan(Goal.OFFER_SLOTS, "offer.same_time_elsewhere", branch=elsewhere[0], other=first.branch,
                     time=speak_hour(first.start.time()), doctor=first.doctor)
    if len(slots) > 1 and _notes(b).get("which") == _offer_key(b):
        # "Yes" to two times: ask which, the way a receptionist would.
        a, b_spoken = _two_spoken(first, slots[1])
        return _plan(Goal.OFFER_SLOTS, "offer.which", a=a, b=b_spoken)
    requested = None
    if b.date_c is not None and b.date_c.exact and b.time_c is not None and b.time_c.kind == "exact":
        requested = datetime.combine(b.date_c.start, b.time_c.start, tzinfo=first.start.tzinfo)
    notes = _notes(b)
    if notes.get("other_branch") and first.branch != notes["other_branch"]:
        times = speak_slot(first.start, with_day=False)
        line = "offer.other_branch"
        if len(slots) > 1 and slots[1].branch == first.branch:
            times = prompts.speak_list([times, speak_slot(slots[1].start, with_day=False)], "or")
            line = "offer.other_branch.two"          # two times: "which would you like?", never "shall I take it?"
        return _plan(Goal.OFFER_SLOTS, line, branch=notes["other_branch"], other=first.branch, times=times)
    if requested is not None and first.start == requested:
        return _plan(Goal.OFFER_SLOTS, "offer.exact", slot=first.spoken, doctor=first.doctor)
    if len(slots) == 1:
        return _plan(Goal.OFFER_SLOTS, "offer.one", a=first.spoken, doctor=first.doctor)
    second = slots[1]
    wanted = set(b.date_c.dates()) if b.date_c is not None and b.date_c.kind != "earliest" else set()
    if b.date_c is not None and b.date_c.exact and notes.get("full_day") == b.date_c.start.isoformat() \
            and not any(s.start.date() in wanted for s in slots):
        return _plan(Goal.OFFER_SLOTS, "offer.full_everywhere", day=prompts.speak_day(b.date_c.start),
                     a=first.spoken, b=second.spoken)
    if b.date_c is not None and b.date_c.exact and not any(s.start.date() in wanted for s in slots):
        return _plan(Goal.OFFER_SLOTS, "offer.later_days", day=prompts.speak_day(b.date_c.start),
                     a=first.spoken, b=second.spoken)
    _, b_spoken = _two_spoken(first, second)
    doctor = first.doctor if first.doctor == second.doctor \
        else f"{first.doctor} for the first and {second.doctor} for the second"
    return _plan(Goal.OFFER_SLOTS, "offer.two", a=first.spoken, b=b_spoken, doctor=doctor)


def summary_params(ctx: CallContext) -> dict:
    """Params for the "summary" line: service, doctor, branch, when, patient (all from the chosen held slot)."""
    b = ctx.book
    slot = b.chosen
    if slot is None:
        return {}
    return {
        "service": _spoken_service(slot.service),
        "doctor": slot.doctor,
        "branch": slot.branch,
        "when": slot.spoken or speak_slot(slot.start),
        "patient": _patient_name(ctx) or "",
    }


def missing_required(ctx: CallContext, catalog) -> list:
    """
    The required details (SUCCESS_CRITERIA M1) a commit would still lack, by
    name. Empty means the booking may be committed once the summary gate is
    open.
    """
    b = ctx.book
    out = []
    if not b.service:
        out.append("service")
    slot = b.chosen
    if slot is None:
        out.append("slot")
    else:
        if slot.service != b.service:
            out.append("service")
        if slot.branch not in catalog.branches_offering(slot.service):
            out.append("branch")
        if b.branch and slot.branch != b.branch:
            out.append("branch")
    if not _patient_name(ctx):
        out.append("patient")
    if ctx.caller.phone_state != FieldState.CONFIRMED or not ctx.caller.phone_e164:
        out.append("phone")
    return out


# ---------------------------------------------------------------- actions (database thread)


async def advance(ctx: CallContext, u: Understanding, confirmation: Optional[str], rt: Runtime) -> ActionResult:
    """
    Run whatever actions the checklist now allows (validate branch/service,
    duplicate and max-3 checks, search + hold, choice, commit). Returns the
    ActionResult (action="booked" only after scheduling.book returned ok)
    with any Notices for this turn.
    """
    b = ctx.book
    result = ActionResult()
    catalog = rt.catalog
    if b.appointment_id:
        return result
    notes = _notes(b)

    # The recap-heard rule: the summary Emma gave last turn counts only if it
    # played to the end and was for this exact version of the draft.
    if ctx.pending in (Goal.SUMMARY, Goal.SUMMARY_AGAIN) and b.summary_version == b.version \
            and ctx.last_reply_heard:
        b.summary_heard = True

    if u is None:
        u = Understanding()
    result.notices += take(ctx, u, rt)
    result.notices += _answer(ctx, u, confirmation)
    _yes_to_two(ctx, u, confirmation)
    result.notices += _validate(ctx, catalog)
    notes["branch_options"] = offering_branches(ctx, catalog)

    await _sync_holds(ctx, rt)
    await _run_checks(ctx, rt)

    if _commit_ready(ctx, confirmation, catalog):
        await _commit(ctx, rt, result)
    if result.action is None and b.chosen is None and not b.offered and _search_ready(ctx) \
            and _checks_clear(ctx) and (notes.get("no_slots") or {}).get("key") != _search_key(b):
        await _search(ctx, rt, result.notices)
        if b.chosen is None and _exact_free(b):
            # The exact time asked for is free: take it and go straight to the
            # summary, one yes instead of two (owner's decision, 7 Oct; T5).
            _choose(ctx, b.offered[0])
            await _sync_holds(ctx, rt)
            result.notices.append(Notice("exact.free", covered_by=("free",)))
    elif result.action is None and _insists_on_full_day(ctx, u):
        await _search_same_day_elsewhere(ctx, rt)

    plan = next_goal(ctx)
    if plan is not None and plan.goal == Goal.SUMMARY and b.summary_version != b.version:
        b.summary_version, b.summary_heard = b.version, False
    if plan is not None and plan.goal == Goal.CALLBACK_OFFER and not ctx.callback_reason:
        ctx.callback_reason = f"Couldn't find a time that suits for {b.service or 'an appointment'}"
    return result


def _yes_to_two(ctx: CallContext, u: Understanding, confirmation: Optional[str]) -> None:
    """
    A plain "yes" to two offered times picks neither: Emma asks which
    ("Sure, which one: 12 or 12:30?"). A second plain yes takes the first;
    the summary that follows still names it, so the caller can change it.
    """
    b = ctx.book
    if ctx.pending != Goal.OFFER_SLOTS or confirmation != "yes" or b.chosen is not None or len(b.offered) < 2 \
            or u.choice_index is not None or u.time_phrase or u.date_phrase:
        return
    notes = _notes(b)
    if notes.get("which") == _offer_key(b):
        notes.pop("which", None)
        _choose(ctx, b.offered[0])
    else:
        notes["which"] = _offer_key(b)


def _checks_clear(ctx: CallContext) -> bool:
    """No search while the max-3 limit or an unanswered duplicate stands in the way (nothing could be booked)."""
    checks = _notes(ctx.book).get("checks") or {}
    if checks.get("future", 0) >= config.MAX_FUTURE_APPOINTMENTS_PER_PHONE:
        return False
    return not (checks.get("dup") and not ctx.book.duplicate_ok)


async def _sync_holds(ctx: CallContext, rt: Runtime):
    """Free the holds this turn made stale; keep the rest alive while the caller talks."""
    b = ctx.book
    notes = _notes(b)
    try:
        if notes.pop("release", False):
            await rt.run(scheduling.release_holds, rt.call_id)
            notes.pop("keep_hold", None)
        elif b.chosen is not None and (notes.get("keep_hold") or b.offered != [b.chosen]):
            # A pick frees the other offers (apply.py may have made the pick,
            # leaving no note): only the chosen slot stays held.
            notes.pop("keep_hold", None)
            keep = [b.chosen.hold_id] if b.chosen.hold_id else []
            await rt.run(scheduling.release_holds, rt.call_id, keep)
            b.offered = [b.chosen]
        if b.offered or b.chosen is not None:
            await rt.run(scheduling.refresh_holds, rt.call_id)
    except Exception:
        logger.exception("hold bookkeeping failed for call %s", rt.call_id)


async def _run_checks(ctx: CallContext, rt: Runtime):
    """
    Once the phone is confirmed: how many future appointments it has (max 3)
    and whether this patient already has one (D1). Re-run when the phone or
    the patient changes. Only the count and a yes/no are kept (Z7).
    """
    b = ctx.book
    c = ctx.caller
    notes = _notes(b)
    if c.phone_state != FieldState.CONFIRMED or not c.phone_e164:
        return
    patient = _patient_name(ctx)
    key = repr((c.phone_e164, scheduling.norm_name(patient or "")))
    if (notes.get("checks") or {}).get("key") == key:
        return
    try:
        rows = await rt.run(scheduling.future_appointments, c.phone_e164)
    except Exception:
        logger.exception("future_appointments failed for call %s", rt.call_id)
        return
    dup = bool(patient) and any(_same_name(patient, r.get("patient_name") or "") for r in rows)
    notes["checks"] = {"key": key, "future": len(rows), "dup": dup}


def _commit_ready(ctx: CallContext, confirmation: Optional[str], catalog) -> bool:
    """The summary gate (Z1) plus every required detail (M1)."""
    b = ctx.book
    return (ctx.pending in (Goal.SUMMARY, Goal.SUMMARY_AGAIN)
            and confirmation == "yes"
            and b.chosen is not None
            and b.summary_heard
            and ctx.last_reply_heard
            and b.summary_version == b.version
            and not b.appointment_id
            and _checks_clear(ctx)
            and not missing_required(ctx, catalog))


async def _commit(ctx: CallContext, rt: Runtime, result: ActionResult):
    b = ctx.book
    c = ctx.caller
    slot = b.chosen
    rt.emit("before_action", phrase=checking_phrase(ctx))
    patient = _patient_name(ctx)
    try:
        res = await rt.run(
            scheduling.book, service=slot.service_id, doctor_id=slot.doctor_id, start=slot.start,
            patient_name=patient, phone=c.phone_e164, idem_key=f"{rt.idem_prefix}:book:{b.draft_id}:{b.version}",
            caller_name=c.name, call_id=rt.call_id, patient_age=b.age,
            name_unverified=(not b.for_someone_else and c.name_state == FieldState.UNVERIFIED),
            emergency=b.emergency)
    except Exception:
        logger.exception("scheduling.book raised for call %s", rt.call_id)
        res = scheduling.Result(False, "ERROR")
    result.code = res.code
    if res.ok:
        b.appointment_id = res.appointment_id
        _notes(b)["booked_turn"] = ctx.turn
        ctx.outcome = "booked"
        result.ok, result.action, result.appointment_id = True, "booked", res.appointment_id
        if b.emergency:
            await _emergency_task(ctx, rt, slot, res.appointment_id)
        return

    logger.info("booking refused for call %s: %s", rt.call_id, res.code)
    notes = _notes(b)
    if res.code == "MAX_FUTURE":
        notes["checks"] = {"key": None, "future": config.MAX_FUTURE_APPOINTMENTS_PER_PHONE, "dup": False}
        notes["release"] = True
        b.chosen, b.offered = None, []
        b.touch()
    elif res.code == "PATIENT_CONFLICT" and b.duplicate_ok:
        # They already said they want another appointment; this one overlaps
        # the one the patient has. Asking the duplicate question again would
        # loop on the same slot: say it clashes (no date or time, Z7) and ask
        # for another time.
        result.notices.append(Notice("patient.clash", {"patient": patient or ""}))
        b.chosen = None
        _reject_offers(ctx)
    elif res.code == "PATIENT_CONFLICT":
        notes["checks"] = {"key": (notes.get("checks") or {}).get("key"), "future": 0, "dup": True}
        b.duplicate_ok = False
        notes["release"] = True
        b.chosen, b.offered = None, []
        b.touch()
    else:
        # TAKEN, TOO_SOON (it slipped inside the lead time while they talked)
        # or any rule scheduling re-checked: say so plainly and offer afresh.
        result.notices.append(Notice("slot.gone"))
        notes["release"] = True
        notes.pop("no_slots", None)
        b.chosen, b.offered = None, []
        b.touch()
    await _sync_holds(ctx, rt)


async def _emergency_task(ctx: CallContext, rt: Runtime, slot: OfferedSlot, appointment_id: str):
    """Urgent bookings are flagged to staff (invariant 7: the promise has a task behind it)."""
    tasks = optional_module("tasks")
    if tasks is None:
        return
    note = f"Urgent: in pain, booked {slot.service} with {slot.doctor} at {slot.branch}, {speak_slot(slot.start)}."
    try:
        task_id = await rt.run(tasks.create_task, kind="emergency", priority="urgent",
                               phone_e164=ctx.caller.phone_e164, note=note, call_id=rt.call_id,
                               appointment_id=appointment_id)
        ctx.tasks_created.append(task_id)
    except Exception:
        logger.exception("emergency task failed for call %s", rt.call_id)


async def _search(ctx: CallContext, rt: Runtime, notices: list):
    """Search, hold the top two, release older holds; the first search of a draft says "let me just check"."""
    b = ctx.book
    notes = _notes(b)
    catalog = rt.catalog
    branches = [b.branch] if b.branch else offering_branches(ctx, catalog)
    branch_ids = [catalog.branch(n).id for n in branches if catalog.branch(n) is not None]
    if not branch_ids:
        return
    notes.pop("other_branch", None)                  # a fresh search: offers are at the caller's branch again
    if not notes.get("searched"):
        notes["searched"] = True
        rt.emit("before_action", phrase=checking_phrase(ctx))
    time_c = b.time_c if b.time_c is not None and b.time_c.kind in ("exact", "window") else None
    try:
        await rt.run(scheduling.release_holds, rt.call_id)
        found = await rt.run(scheduling.suggest, service=b.service, date_c=b.date_c, time_c=time_c,
                             branch_ids=branch_ids, doctor_id=b.doctor_id, gender=b.doctor_gender,
                             emergency=b.emergency, call_id=rt.call_id, patient=_search_patient(ctx),
                             same_time_later=notes.get("same_time_later") == _search_key(b))
        held = []
        for slot in found.slots[:HOLD_TOP]:
            hold_id = await rt.run(scheduling.hold, slot, rt.call_id, emergency=b.emergency)
            if hold_id:
                held.append(OfferedSlot(slot.doctor_id, slot.doctor, slot.branch_id, slot.branch, slot.service_id,
                                        slot.service, slot.start, slot.end, hold_id, speak_slot(slot.start)))
    except Exception:
        logger.exception("slot search failed for call %s", rt.call_id)
        return
    if found.requested is not None and found.kind != "exact":
        for reason in found.reasons:
            if reason in REASON_LINES:
                notices.append(Notice(REASON_LINES[reason]))
                break
    b.offered = held = _in_clock_order(held)
    if notes.get("same_time_later") == _search_key(b) and time_c is not None and time_c.kind == "exact" \
            and not any(s.start.time() == time_c.start for s in held):
        # Not at their branch on the following days either: that time at another branch that day.
        await _search_same_time_elsewhere(ctx, rt)
        held = b.offered
    if held and time_c is not None and time_c.kind == "window" \
            and not any(time_c.start <= s.start.time() < time_c.end for s in held):
        # "Tomorrow evening" with the evening full: say so before offering other times.
        when = prompts.speak_when(b.date_c, time_c) if b.date_c is not None else ""
        if when:
            notices.append(Notice("window.full", {"when": when}))
    if held:
        notes.pop("no_slots", None)
        notes["offer"] = {"key": _search_key(b), "round": b.offer_rounds}
        return
    until = found.searched_until or (b.date_c.end if b.date_c else clock.today())
    notes["no_slots"] = {"key": _search_key(b), "branch": prompts.speak_list(branches, "or"), "until": prompts.speak_day(until)}


def _insists_on_full_day(ctx: CallContext, u: Understanding) -> bool:
    """
    "The 15th is fine" again, after Emma said the 15th is full and offered
    other days: the caller wants that day. Saying the same two slots a third
    time is the loop the owner flagged, so look at the day once more, wider.
    """
    b = ctx.book
    if ctx.pending != Goal.OFFER_SLOTS or not b.offered or b.chosen is not None or u.choice_index is not None:
        return False
    if b.date_c is None or not b.date_c.exact or any(s.start.date() == b.date_c.start for s in b.offered):
        return False
    if not u.date_phrase or _notes(b).get("full_day") == b.date_c.start.isoformat():
        return False
    when = dateparse.parse_when(u.date_phrase)
    return when.date is not None and when.date.exact and when.date.start == b.date_c.start


async def _search_same_day_elsewhere(ctx: CallContext, rt: Runtime):
    """
    The day the caller insists on is full at their branch: try the other
    branches that do the service on that same day, the way a receptionist
    would ("Nagarbhavi's full on the 15th, but Indiranagar has 1:30"). If
    none has it either, the offer is said once more as "full everywhere",
    in different words. Once per day asked for.
    """
    b = ctx.book
    notes = _notes(b)
    notes["full_day"] = b.date_c.start.isoformat()
    others = [n for n in offering_branches(ctx, rt.catalog) if n != b.branch] if b.branch else []
    branch_ids = [rt.catalog.branch(n).id for n in others if rt.catalog.branch(n) is not None]
    if not branch_ids or b.doctor_id is not None:
        return                                       # a chosen doctor works at one branch only
    time_c = b.time_c if b.time_c is not None and b.time_c.kind in ("exact", "window") else None
    try:
        found = await rt.run(scheduling.suggest, service=b.service, date_c=b.date_c, time_c=time_c,
                             branch_ids=branch_ids, doctor_id=None, gender=b.doctor_gender,
                             emergency=b.emergency, call_id=rt.call_id, patient=_search_patient(ctx), later_days=1)
        same_day = [s for s in found.slots if s.start.date() == b.date_c.start]
        # One branch only, so "that works" has one meaning and the line names every slot it offers.
        same_day = [s for s in same_day if same_day and s.branch == same_day[0].branch][:HOLD_TOP]
        if not same_day:
            return
        await rt.run(scheduling.release_holds, rt.call_id)
        held = []
        for slot in same_day:
            hold_id = await rt.run(scheduling.hold, slot, rt.call_id, emergency=b.emergency)
            if hold_id:
                held.append(OfferedSlot(slot.doctor_id, slot.doctor, slot.branch_id, slot.branch, slot.service_id,
                                        slot.service, slot.start, slot.end, hold_id, speak_slot(slot.start)))
    except Exception:
        logger.exception("same-day search at other branches failed for call %s", rt.call_id)
        return
    if held:
        notes["other_branch"] = b.branch
        b.offered = _in_clock_order(held)
        notes["offer"] = {"key": _search_key(b), "round": b.offer_rounds}


async def _search_same_time_elsewhere(ctx: CallContext, rt: Runtime):
    """
    The caller keeps asking for one time their branch doesn't have, that day
    or the next ones: offer exactly that time at another branch that does the
    service ("Nagarbhavi has nothing at 5, but Indiranagar has 5 with Dr Menon").
    """
    b = ctx.book
    others = [n for n in offering_branches(ctx, rt.catalog) if n != b.branch] if b.branch else []
    branch_ids = [rt.catalog.branch(n).id for n in others if rt.catalog.branch(n) is not None]
    if not branch_ids or b.doctor_id is not None or b.date_c is None or not b.date_c.exact:
        return
    try:
        found = await rt.run(scheduling.suggest, service=b.service, date_c=b.date_c, time_c=b.time_c,
                             branch_ids=branch_ids, doctor_id=None, gender=b.doctor_gender,
                             emergency=b.emergency, call_id=rt.call_id, patient=_search_patient(ctx), limit=1)
        if found.kind != "exact" or not found.slots:
            return
        slot = found.slots[0]
        hold_id = await rt.run(scheduling.hold, slot, rt.call_id, emergency=b.emergency)
    except Exception:
        logger.exception("same-time search at other branches failed for call %s", rt.call_id)
        return
    if hold_id:
        await rt.run(scheduling.release_holds, rt.call_id, [hold_id])
        b.offered = [OfferedSlot(slot.doctor_id, slot.doctor, slot.branch_id, slot.branch, slot.service_id,
                                 slot.service, slot.start, slot.end, hold_id, speak_slot(slot.start))]
        _notes(b)["same_time_branch"] = (b.branch, _offer_key(b))


async def release(ctx: CallContext, rt: Runtime) -> None:
    """Release this call's holds (intent switch, new search, hang-up). Never raises."""
    try:
        for draft in (ctx.book, ctx.parked_book):
            if draft is None:
                continue
            draft.offered = []
            if draft.chosen is not None:
                draft.chosen.hold_id = None
            draft.summary_heard = False
            notes = _notes(draft)
            notes.pop("release", None)
            notes.pop("keep_hold", None)
            notes.pop("offer", None)
    except Exception:
        logger.exception("clearing offers failed")
    try:
        await rt.run(scheduling.release_holds, rt.call_id)
    except Exception:
        logger.exception("releasing holds failed for call %s", getattr(rt, "call_id", None))
