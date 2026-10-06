"""
Apply one Understanding to the context (docs/R2_DESIGN.md, section 5, step 6).

This is where raw values become facts Python trusts:
- names via match.clean_name / join_spelled, fuzzy-matched against names
  already heard this call (Adharsh / Adashar), with the spell-back rule
- phone digits accumulated across turns (match.accumulate_phone), read back,
  explicit yes required
- services / branches / doctors checked against the catalog; a branch that
  doesn't offer the service is refused with the branches that do (Z6)
- dates and times through dateparse.parse_when (date_iso_hint only when
  dateparse can't read the phrase), with typed issues turned into notices
- yes / no resolved against the deterministic parser; disagreement means
  "unclear" and Emma asks again (invariant 3)
- corrections anywhere; intent switches carrying name and phone across

It never talks to the caller directly: it returns Notices (pre-written
sentences for this turn) and leaves the goal to policy.next_goal.
It never commits an appointment change: only workflow commit() does. It
never touches the database either: a parked or dropped draft sets
rt.release_requested and the engine releases the holds right after.

Owner in Sprint 1b: E1.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Optional

import clock
import dateparse
import phones
import prompts
from dateparse import DateConstraint, TimeConstraint
from dialogue import match
from dialogue.context import (
    Act, BookingDraft, CallContext, FieldState, Goal, Intent, MANAGE_INTENTS, ManageDraft, Notice,
    Understanding,
)
from dialogue.runtime import Runtime, safe_call

logger = logging.getLogger(__name__)

# "Actually", "instead", "make it", "no, ...": the caller is changing something on purpose.
_CUE_RE = re.compile(
    r"^\s*no\b|\b(actually|instead|make it|change (it|that|the)|rather|i mean|i meant|not .{1,30} but|"
    r"sorry,? (it'?s|its|i)|correction|wrong)\b", re.I)
# At the summary these mean "don't book this" (the 1 Oct cancel-at-recap call, Z5).
_DONT_BOOK_RE = re.compile(
    r"\b(cancel|don'?t (book|want|need)|do not (book|want)|forget (it|about it)|never ?mind|leave it|"
    r"no need)\b", re.I)

_LEADING_CUE_RE = re.compile(
    r"^\s*(?:(?:no|nope|sorry|actually|instead|rather|so|um+|uh+|okay|ok|then|please|make it|change it to|"
    r"change that to|change the \w+ to|i mean|i meant|let'?s (?:do|say|make it)|can we (?:do|make it)|"
    r"the (?:date|day|time) (?:is wrong|should be|is)|it should be|i wanted(?: it on| on)?|i want(?: it on| on)?|"
    r"(?:the )?(?:date|day|time) is wrong)[\s,.]+)+",
    re.I)


def _strip_cue(spoken: str) -> str:
    """ "make it Tuesday" -> "Tuesday": the correction said back without the caller's own lead-in."""
    text = _LEADING_CUE_RE.sub("", spoken or "").strip(" ,.")
    return text or (spoken or "").strip()


SUMMARY_GOALS = (Goal.SUMMARY, Goal.SUMMARY_AGAIN, Goal.WHAT_TO_CHANGE)
OFFER_GOALS = (Goal.OFFER_SLOTS, Goal.OFFER_NEW_SLOTS)
# Pending goals where a date or time is the answer, so a new one is applied directly.
WHEN_GOALS = (Goal.ASK_WHEN, Goal.ASK_TIME, Goal.RESOLVE_AMPM, Goal.NO_SLOTS, Goal.ASK_NEW_WHEN,
              *OFFER_GOALS, *SUMMARY_GOALS)
WORKFLOW_INTENTS = (Intent.BOOK, *MANAGE_INTENTS)

_ISSUE_LINES = {
    "PAST": "date.past", "SUNDAY": "date.sunday", "BEYOND_HORIZON": "date.horizon",
    "INVALID_DAY": "date.invalid_day", "OUTSIDE_HOURS": "time.outside_hours", "LUNCH": "time.lunch",
}
_MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
                "October", "November", "December")
_WHEN_WORD_RE = re.compile(
    r"\b(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|january|february|march|"
    r"april|may|june|july|august|september|october|november|december|\d{1,2}(st|nd|rd|th)?|morning|"
    r"afternoon|evening|night)\b", re.I)


# ---------------------------------------------------------------- entry points


def apply(ctx: CallContext, u: Understanding, rt: Runtime) -> list:
    """
    Mutate ctx with everything u carries; return the Notices to speak. Order:
    intent switch -> identity (name, phone) -> patient -> service -> branch ->
    doctor -> when -> choice / rejection -> confirmation (routed to whatever
    ctx.pending was asking). Every changed booking field calls
    ctx.book.touch() and drops holds / offers that depended on it.
    """
    text = u.raw_text or ""
    conf = resolve_confirmation(u, text, ctx.pending)
    cued = _cued(u, text)
    notices: list = []

    # At the summary "cancel" / "don't book" drops this draft, never books it (Z5).
    if ctx.intent == Intent.BOOK and ctx.pending in (Goal.SUMMARY, Goal.SUMMARY_AGAIN) \
            and (u.intent == Intent.CANCEL or (conf != "yes" and _DONT_BOOK_RE.search(text))):
        return _drop(ctx, rt)

    if u.intent == Intent.BOOK and ctx.intent != Intent.BOOK and _DONT_BOOK_RE.search(text) \
            and not u.carries_details:
        u.intent = None                  # "No, I don't want to book anything" never starts a booking
    if u.intent in WORKFLOW_INTENTS and u.intent != ctx.intent and _asks_for(ctx, u):
        notices += switch_intent(ctx, u.intent, rt)
    elif u.intent == Intent.BOOK and ctx.book.appointment_id and u.carries_details:
        notices += _fresh_booking(ctx, rt)              # "and one for my son too"
    elif u.intent == Intent.INFO and ctx.intent == Intent.NONE:
        ctx.intent = Intent.INFO
    elif u.intent in MANAGE_INTENTS and u.intent == ctx.intent and _verification_given_up(ctx):
        _retry_verification(ctx)
    elif u.intent in MANAGE_INTENTS and u.intent == ctx.intent and ctx.manage.done and ctx.manage.verified \
            and u.intent != Intent.CHECK and ctx.outcome not in ("cancelled", "rescheduled", "callback"):
        # "I want to cancel my appointment" after the change was set aside
        # (a "no" at the confirmation, or a decline Emma misread): pick it up
        # again from the verified appointment, never "anything else?" on a loop.
        ctx.manage.done = False
        ctx.manage.summary_heard = False

    proposal_before = dict(ctx.change_proposal)
    _apply_name(ctx, u, cued, notices, rt)
    _apply_phone(ctx, u, conf, cued, notices)
    _apply_patient(ctx, u)
    _apply_service(ctx, u, cued, notices, rt)
    _apply_branch(ctx, u, cued, notices, rt)
    _apply_doctor(ctx, u, notices, rt)
    chose = _apply_choice(ctx, u, conf, rt)
    if not chose:
        _apply_when(ctx, u, cued, notices, rt)
    if u.cancel_reason:
        ctx.manage.cancel_reason = u.cancel_reason.strip()[:200]
    notices += _route_confirmation(ctx, u, conf, proposal_before, rt)
    return notices


def request_notice(b: BookingDraft) -> Notice:
    """
    The opening request said back the way a receptionist would ("Sure, a
    check-up for your daughter at Jayanagar."), so the caller knows it was
    heard before Emma asks for anything. engine.compose() folds in the day
    ("ack.when") and drops it when another notice already corrects the request.
    """
    service = _spoken_service(b.service)
    who = f"your {b.relation}" if b.relation else (b.patient_name if b.for_someone_else else "")
    return Notice("ack.request", {"service": service, "who": who, "branch": b.branch or ""},
                  covered_by=(service.split(" ", 1)[-1].lower(),))    # "check-up", not "check"


# Words that actually ask for another workflow. Once a workflow is under way,
# a reading's intent alone doesn't move the call: "It's tomorrow" while a
# cancellation is being verified must never turn it into a booking (Z5).
_ASKS_FOR = {
    Intent.BOOK: re.compile(r"\b(book|booking|new appointment|another (appointment|one)|make an appointment|"
                            r"schedule (an|a|one)|fix (an|a) appointment|want to come in|"
                            r"appointment for|one for my)\b", re.I),
    Intent.CANCEL: re.compile(r"\b(cancel|cancell?ing|call off|drop the appointment|don'?t need the appointment)", re.I),
    Intent.RESCHEDULE: re.compile(r"\b(reschedul|re-schedul|move|shift|change|prepone|postpone|different (day|time|date)|"
                                  r"push it|bring it forward)", re.I),
    Intent.CHECK: re.compile(r"\b(check|confirm|when is|what time is|when'?s my|what day is|"
                             r"my appointment (is|on)|do i have an appointment|"
                             r"(when|what time|what day|which day) (is )?my (appointment|booking))", re.I),
}


def _asks_for(ctx: CallContext, u: Understanding) -> bool:
    """Switch to u.intent? Always from no workflow; from another one only when the words ask for it."""
    if ctx.intent not in WORKFLOW_INTENTS or u.source == "tier0":
        return True                     # Tier-0 only reads an intent from an explicit request
    pattern = _ASKS_FOR.get(u.intent)
    return pattern is None or bool(pattern.search(u.raw_text or ""))


_CANCEL_IT_RE = re.compile(r"\b(cancel|call (it )?off)\b", re.I)
_NOT_RE = re.compile(r"\b(no|not|don'?t|do not|never ?mind|wait|hold on|keep)\b", re.I)
_LEADING_YES_NO = re.compile(r"\s*(yes|yeah|yep|yup|sure|okay|ok|no|nope|nah|not really)\b", re.I)


def resolve_confirmation(u: Understanding, text: str, pending=None) -> Optional[str]:
    """
    "yes" / "no" / None. The NLU reading wins when it has one, except when
    the deterministic parser (match.parse_yes_no) disagrees: then None, and
    Emma re-asks. Same rule as ai_engine._resolve_confirmation.

    The parser reads "cancel" as a no (at a booking summary it is one), but
    when Emma has just asked "Shall I cancel it?" (`pending` CONFIRM_CANCEL),
    "yes, please cancel it" is the clearest yes there is.
    """
    nlu = u.confirmation if u.confirmation in ("yes", "no") else None
    parsed = safe_call(match.parse_yes_no, text or "", default=None)
    if pending == Goal.CONFIRM_CANCEL and _CANCEL_IT_RE.search(text or "") and not _NOT_RE.search(text or ""):
        parsed = "yes"
        nlu = nlu if nlu == "yes" else None
    elif parsed == "no" and pending not in SUMMARY_GOALS and u.intent == Intent.CANCEL \
            and _CANCEL_IT_RE.search(text or ""):
        # "Can you cancel that one instead?" while Emma read back the number
        # asks for another workflow; it says nothing against the number.
        parsed = safe_call(match.parse_yes_no, _CANCEL_IT_RE.sub(" ", text or ""), default=None)
    if nlu is None and parsed and u.has(Act.CHITCHAT) and not u.has(Act.ANSWER) \
            and not _LEADING_YES_NO.match(text or ""):
        return None          # "Hope you're not too busy today" is small talk, not a "no"
    if nlu and parsed and nlu != parsed:
        logger.info("confirmation conflict (nlu=%s parser=%s); re-asking", nlu, parsed)
        return None
    return nlu or parsed


def _verification_given_up(ctx: CallContext) -> bool:
    """Verification failed, the callback was declined, and nothing was found or changed."""
    m = ctx.manage
    return (m.done and not m.verified and not m.matches and m.verify_attempts > 0
            and ctx.stats(Goal.CALLBACK_OFFER).asked < 2)


def _retry_verification(ctx: CallContext):
    """
    "No, I just want to move my appointment" after Emma couldn't find it and
    the caller turned down a callback: they still want it done, so look again
    from the date (name and phone stand) instead of closing on "anything else?".
    Once only: a second round that fails ends on the callback offer again.
    """
    m = ctx.manage
    m.done, m.verify_attempts, m.appt_date = False, 0, None
    for goal in (Goal.VERIFY_FAILED, Goal.ASK_APPT_DATE):
        ctx.goal_stats.pop(goal.value, None)


def switch_intent(ctx: CallContext, new: Intent, rt: Runtime) -> list:
    """
    Change workflow mid-call. Name and phone carry over. BOOK -> MANAGE parks
    the booking draft (ctx.parked_book) and releases its holds; MANAGE ->
    BOOK starts a fresh draft (the service can be carried from the appointment
    just cancelled). At the summary, "cancel" / "don't book" means drop this
    draft (Goal.DROPPED asks whether an existing appointment was meant), never
    a booking (the 1 Oct cancel-at-recap call, Z5).
    """
    old = ctx.intent
    if old == new:
        return []
    if old == Intent.BOOK and ctx.pending in (Goal.SUMMARY, Goal.SUMMARY_AGAIN) and new == Intent.CANCEL:
        return _drop(ctx, rt)
    if new in MANAGE_INTENTS:
        if old == Intent.BOOK and _draft_started(ctx.book) and not ctx.book.appointment_id:
            parked = ctx.book
            parked.offered = []                         # its holds are released below
            ctx.parked_book = parked
            rt.release_requested = True
        if old == Intent.BOOK:
            ctx.book = BookingDraft(draft_id=ctx.book.draft_id + 1)
        if old in MANAGE_INTENTS:
            ctx.manage.action = new                     # keep the verification ("check, then cancel it")
            ctx.manage.done = False
        else:
            ctx.manage = ManageDraft(action=new)
    elif new == Intent.BOOK:
        carried = None
        m = ctx.manage
        if old in MANAGE_INTENTS and m.target is not None and m.action == Intent.CANCEL and m.done:
            carried = m.target.service                 # "cancel it and book another cleaning"
        if m.offered:
            rt.release_requested = True
        if ctx.parked_book is not None and carried is None:
            ctx.book, ctx.parked_book = ctx.parked_book, None
            ctx.book.draft_id += 1
            ctx.book.touch()
        elif ctx.book.appointment_id or _draft_started(ctx.book) and old in MANAGE_INTENTS:
            ctx.book = BookingDraft(draft_id=ctx.book.draft_id + 1, service=carried)
        elif carried and not ctx.book.service:
            ctx.book.service = carried
        if old in MANAGE_INTENTS:
            ctx.manage = ManageDraft()
    ctx.intent = new
    ctx.change_proposal = {}
    return []


def apply_correction(ctx: CallContext, field_name: str, value, rt: Runtime, *, cued: bool = False,
                     spoken: str = "") -> list:
    """
    Replace a filled detail. With a correction cue ("actually", "instead",
    "make it", "no, ...") or u.correction, apply it and acknowledge
    ("correction.ack"); at the summary any new detail is a correction;
    otherwise set ctx.change_proposal and let policy ask CONFIRM_CHANGE.
    Dependent details are re-checked (a new service re-validates the branch;
    a new date drops the offers).
    """
    if field_name == "date":
        # Said back in Emma's words ("around 6"), never the caller's raw phrase
        # ("Around 6 in the evening, please"; model-down drill, 6 Oct).
        spoken = _spoken_value(field_name, value) if value and (value[0] or value[1]) else spoken
    spoken = spoken or _spoken_value(field_name, value)
    if not (cued or ctx.pending in SUMMARY_GOALS):
        ctx.change_proposal = {"field": field_name, "value": value, "spoken": spoken}
        return []
    notices = _set_field(ctx, field_name, value, rt)
    notices.append(Notice("correction.ack", {"value": _strip_cue(spoken)}, covered_by=_cover_words(_strip_cue(spoken))))
    return notices


# ---------------------------------------------------------------- identity


def _apply_name(ctx: CallContext, u: Understanding, cued: bool, notices: list, rt: Runtime) -> None:
    c = ctx.caller
    spelled = None
    if u.name_spelled:
        spelled = safe_call(match.join_spelled, u.name_spelled, default=None) or _letters(u.name_spelled)
    elif u.name and ctx.pending == Goal.SPELL_NAME and _SPELLED_RE.search(u.raw_text or ""):
        spelled = _clean_name(u.name)                   # "P R I Y A, Priya": the letters, said back as a word
    name = spelled or (_clean_name(u.name) if u.name else None)
    if not name:
        return
    if spelled and c.name and len(spelled.split()) == 1 and len(c.name.split()) > 1:
        name = _with_spelled_word(c.name, spelled)      # one word spelled: the rest of the name stays
    if name not in c.names_heard:
        c.names_heard.append(name)
    if ctx.intent in MANAGE_INTENTS:
        # The name the appointment is under (it may be a family member's).
        ctx.manage.patient_name = name
        if not c.name:
            c.name, c.name_state = name, FieldState.HEARD
        notices.append(_name_notice(name, spelled))
        return
    if not c.name:
        c.name = name
        c.name_state = FieldState.CONFIRMED if spelled else FieldState.HEARD
        notices.append(_name_notice(name, spelled))
        return
    if name.lower() == c.name.lower():
        if spelled and c.name_state != FieldState.CONFIRMED:
            c.name_state = FieldState.CONFIRMED
            notices.append(_name_notice(name, spelled))
        return
    if spelled:                                         # the answer to "could you spell that?"
        c.name, c.name_state = name, FieldState.CONFIRMED
        ctx.book.touch()
        notices.append(_name_notice(name, spelled))
        return
    asked = ctx.pending in (Goal.ASK_NAME, Goal.SPELL_NAME)
    if not (cued or asked or ctx.pending in SUMMARY_GOALS):
        if safe_call(match.closest_name, name, [c.name], default=None):
            return                                      # "Adashar" after "Adharsh": the same person, misheard
        notices += apply_correction(ctx, "name", name, rt, cued=False, spoken=name)
        return
    # A corrected name: one miss -> spell it; a third -> keep it flagged (invariant 8).
    c.name = name
    c.name_misses += 1
    ctx.book.touch()
    if c.name_misses >= 3:
        c.name_state = FieldState.UNVERIFIED
        notices.append(_name_notice(name, None))
    else:
        c.name_state = FieldState.HEARD
        rt.raised.append(Goal.SPELL_NAME)


_SPELLED_RE = re.compile(r"\b[A-Za-z](?:[ .,-]+[A-Za-z]){2,}\b")


def _with_spelled_word(full: str, spelled: str) -> str:
    """ "Priya Sharma" + spelled "PRIYA" -> "Priya Sharma"; + "PREEYA" -> "Preeya Sharma" (the closest word is replaced)."""
    import difflib
    words = full.split()
    word = spelled.strip().title()
    best = max(range(len(words)), key=lambda i: difflib.SequenceMatcher(None, words[i].lower(), word.lower()).ratio())
    words[best] = word
    return " ".join(words)


def _name_notice(name: str, spelled: Optional[str]) -> Notice:
    if spelled:
        return Notice("ack.spelled", {"letters": " ".join(spelled.replace(" ", "").upper())},
                      covered_by=(name,))
    return Notice("ack.name", {"name": name}, covered_by=(name,))


def _apply_phone(ctx: CallContext, u: Understanding, conf: Optional[str], cued: bool, notices: list) -> None:
    c = ctx.caller
    if u.phone_digits:
        digits, too_many = _digit_run(u.phone_digits)
        if too_many:
            c.phone_buffer = ""
            notices.append(Notice("phone.too_many"))
            return
        buffer, e164 = _accumulate(c.phone_buffer, u.phone_digits, digits)
        if e164 is None:
            if not buffer and len(c.phone_buffer) + len(digits) > 12:
                notices.append(Notice("phone.too_many"))
            c.phone_buffer = buffer
            return
        c.phone_buffer = ""
        if c.phone_state == FieldState.CONFIRMED and c.phone_e164:
            if e164 == c.phone_e164:
                return
            direct = cued or ctx.pending in (Goal.ASK_PHONE, Goal.PHONE_MORE, Goal.CONFIRM_PHONE, *SUMMARY_GOALS)
            if not direct:
                ctx.change_proposal = {"field": "phone", "value": e164,
                                       "spoken": safe_call(prompts.speak_phone, e164, default=e164)}
                return
        if c.phone_state == FieldState.PENDING and ctx.pending == Goal.CONFIRM_PHONE and e164 != c.phone_e164 \
                and c.phone_source != "caller_id":
            c.phone_misses += 1                         # "937, not 837": the read-back was wrong
        c.phone_source = None                           # a number they said, read back in groups
        c.phone_e164 = e164
        c.phone_state = FieldState.PENDING              # read back in groups, explicit yes required
        return
    if ctx.pending == Goal.CONFIRM_PHONE and c.phone_state == FieldState.PENDING:
        if conf == "yes":
            c.phone_state = FieldState.CONFIRMED
        elif conf == "no":
            if c.phone_source == "caller_id":
                c.phone_source = "declined"             # another number, not a misheard one
            else:
                c.phone_misses += 1
            c.phone_e164 = None
            c.phone_state = FieldState.EMPTY
            c.phone_buffer = ""


def _digit_run(text: str) -> tuple:
    """(digits, too_many) via match.extract_digits, else the turn detector's digit reader."""
    run = safe_call(match.extract_digits, text, default=None)
    if run is not None:
        return run.digits, bool(run.too_many)
    import turn_detector
    digits = safe_call(turn_detector.spoken_digits, text, default="") or re.sub(r"\D", "", text)
    return digits, len(digits) > 12 and phones.to_e164(digits) is None


def _accumulate(buffer: str, text: str, digits: str) -> tuple:
    """(new buffer, e164 or None): match.accumulate_phone, else a plain append-and-check."""
    run = safe_call(match.extract_digits, text, default=None)
    if run is not None:
        result = safe_call(match.accumulate_phone, buffer, run, default=None)
        if result is not None:
            return result
    whole = phones.to_e164(digits) if len(digits) >= 10 else None
    if whole:
        return "", whole
    joined = buffer + digits
    if len(joined) > 12:
        return "", None
    e164 = phones.to_e164(joined) if len(joined) >= 10 else None
    return ("", e164) if e164 else (joined, None)


def _apply_patient(ctx: CallContext, u: Understanding) -> None:
    b = ctx.book
    changed = False
    if u.for_someone_else is not None and u.for_someone_else != b.for_someone_else:
        b.for_someone_else = bool(u.for_someone_else)
        changed = True
    if u.patient_name:
        name = _clean_name(u.patient_name) or u.patient_name.strip().title()
        if name != b.patient_name:
            b.patient_name, b.for_someone_else, changed = name, True, True
    if u.relation and u.relation != b.relation:
        b.relation, b.for_someone_else, changed = u.relation.strip().lower(), True, True
    if u.age is not None and 0 <= u.age <= 120 and u.age != b.age:
        b.age, changed = u.age, True
    if changed:
        b.touch()


# ---------------------------------------------------------------- catalog details


def _apply_service(ctx: CallContext, u: Understanding, cued: bool, notices: list, rt: Runtime) -> None:
    if not (u.service or u.service_phrase) or ctx.intent in MANAGE_INTENTS:
        return
    catalog = rt.catalog
    services = tuple(getattr(catalog, "services", ()) or ())
    name, options, unknown = None, (), None
    if services:
        svc = catalog.service(u.service) if u.service else None
        if svc is not None:
            name = svc.name
        else:
            m = safe_call(match.match_service, u.service_phrase or u.service, list(services), default=None)
            if m is not None:
                name, options, unknown = m.value, tuple(m.options), m.unknown_phrase
    else:
        name = u.service                                # no catalog snapshot: keep what was said
    b = ctx.book
    if u.service_phrase:
        b.service_phrase = u.service_phrase
    if options and len(options) > 1 and not name:
        b.service_options = list(options)               # "tooth": filling, extraction or check-up?
        return
    if unknown and not name:
        # The plain Consultation, not another consult-type service (Braces, Invisalign).
        consults = [s.name for s in services if s.is_consultation]
        consult = next((n for n in consults if n.lower() == "consultation"), consults[0] if consults else None)
        if consult is None:
            return
        notices.append(Notice("service.unknown", {"phrase": unknown}))
        name = consult
    if not name or name == b.service:
        return
    if b.service and not (cued or ctx.pending in (Goal.ASK_SERVICE, Goal.CLARIFY_SERVICE, *SUMMARY_GOALS)):
        notices += apply_correction(ctx, "service", name, rt, cued=False)
        return
    correcting = bool(b.service)
    notices += _set_field(ctx, "service", name, rt)
    if correcting:
        spoken = safe_call(prompts.speak_service, name, default=name)
        notices.append(Notice("correction.ack", {"value": _strip_cue(spoken)}, covered_by=_cover_words(_strip_cue(spoken))))


def _apply_branch(ctx: CallContext, u: Understanding, cued: bool, notices: list, rt: Runtime) -> None:
    b = ctx.book
    if ctx.intent in MANAGE_INTENTS:
        return
    if u.branch_any and not u.branch:
        if not b.branch_any:
            b.branch_any = True
            b.touch()
        return
    if not u.branch:
        return
    catalog = rt.catalog
    name = u.branch
    if getattr(catalog, "branches", ()):
        br = catalog.branch(u.branch)
        if br is None:
            m = safe_call(match.match_branch, u.branch, list(catalog.branches), default=None)
            br = catalog.branch(m.value) if m is not None and m.value else None
        if br is None:
            return
        name = br.name
    if name == b.branch:
        return
    if b.branch and not (cued or ctx.pending in (Goal.ASK_BRANCH, Goal.NO_SLOTS, *SUMMARY_GOALS)):
        notices += apply_correction(ctx, "branch", name, rt, cued=False)
        return
    notices += _set_field(ctx, "branch", name, rt)


def _apply_doctor(ctx: CallContext, u: Understanding, notices: list, rt: Runtime) -> None:
    b = ctx.book
    if ctx.intent in MANAGE_INTENTS:
        return
    catalog = rt.catalog
    doctors = tuple(getattr(catalog, "doctors", ()) or ())
    if u.doctor:
        doc = catalog.doctor(u.doctor) if doctors else None
        if doc is None and doctors:
            m = safe_call(match.match_doctor, u.doctor, list(doctors), default=None)
            doc = catalog.doctor(m.value) if m is not None and m.value else None
            if doc is None and m is not None and m.unknown_phrase:
                notices.append(_unknown_doctor(ctx, m.unknown_phrase, catalog))
                return
        if doc is not None and doc.id != b.doctor_id:
            b.doctor_id, b.doctor, b.unknown_doctor = doc.id, doc.spoken, None
            b.touch()
            if b.branch and b.branch.lower() != doc.branch.lower():
                # Dr Rao works at another branch: say so, then ask before moving the booking.
                notices.append(Notice("doctor.other_branch", {"doctor": doc.spoken, "branch": doc.branch}))
                ctx.change_proposal = {"field": "branch", "value": doc.branch, "spoken": doc.branch}
            elif not b.branch:
                b.branch = doc.branch
    elif u.doctor_phrase:
        notices.append(_unknown_doctor(ctx, u.doctor_phrase, catalog))
    if u.doctor_gender in ("female", "male") and u.doctor_gender != b.doctor_gender:
        b.doctor_gender = u.doctor_gender
        b.touch()
        if doctors and b.service and not catalog.doctors_for(b.service, b.branch, b.doctor_gender):
            others = catalog.doctors_for(b.service, b.branch)
            notices.append(Notice("doctor.gender_none", {
                "gender_word": "lady" if b.doctor_gender == "female" else "male",
                "branch": b.branch or "our clinics",
                "doctors": _list([d.spoken for d in others[:3]])}))
            b.doctor_gender = None


def _unknown_doctor(ctx: CallContext, phrase: str, catalog) -> Notice:
    """ "We don't have a Dr Sharma here, but Dr Rao and Dr Shetty are at Nagarbhavi." Preference cleared."""
    b = ctx.book
    b.unknown_doctor = None                             # said once, then cleared
    b.doctor_id = b.doctor = None
    doctors = tuple(getattr(catalog, "doctors", ()) or ())
    branch = b.branch
    pool = [d for d in doctors if (not b.service or b.service in d.services)
            and (not branch or d.branch.lower() == branch.lower())]
    if not branch and pool:
        branch = pool[0].branch
        pool = [d for d in pool if d.branch == branch]
    names = [d.spoken for d in pool[:3]]
    name = phrase.strip()
    if not re.match(r"(?i)^(dr|doctor)\b", name):
        name = f"Dr {name.title()}"
    return Notice("doctor.unknown", {"name": name, "doctors": _list(names) or "our doctors",
                                     "are_is": "is" if len(names) == 1 else "are",
                                     "branch": branch or "our clinics"})


# ---------------------------------------------------------------- when


def _apply_when(ctx: CallContext, u: Understanding, cued: bool, notices: list, rt: Runtime) -> None:
    if ctx.intent in MANAGE_INTENTS:
        _apply_manage_when(ctx, u, notices, rt)
        return
    phrase = " ".join(p for p in (u.date_phrase, u.time_phrase) if p).strip()
    if not phrase:
        return
    expecting = "time" if ctx.pending in (Goal.ASK_TIME, Goal.RESOLVE_AMPM) else \
        "date" if ctx.pending == Goal.ASK_WHEN else None
    when = _parse(phrase, expecting)
    date_c, time_c = when.date, when.time
    issues = list(when.issues)
    if date_c is None and u.date_phrase and u.date_iso_hint and not issues:
        date_c, more = _from_iso(u.date_iso_hint)
        issues += more
    notices += _issue_notices(issues)
    b = ctx.book
    if time_c is not None and b.time_c is not None and b.time_c.kind == "ambiguous" and time_c.kind == "window":
        pick = next((c for c in b.time_c.candidates if time_c.start <= c < time_c.end), None)
        if pick is not None:                            # "7" then "evening" -> 19:00
            time_c = TimeConstraint("exact", pick, label=f"{b.time_c.label} {time_c.label}".strip())
    any_time = (time_c is not None and time_c.kind == "any") or (date_c is not None and date_c.kind == "earliest")
    if time_c is not None and time_c.kind == "any":
        time_c = None
    new_date = date_c is not None and date_c != b.date_c
    new_time = time_c is not None and time_c != b.time_c
    if not (new_date or new_time or (any_time and not b.any_time)):
        return
    changing = (new_date and b.date_c is not None) or \
        (new_time and b.time_c is not None and b.time_c.kind != "ambiguous" and ctx.pending != Goal.RESOLVE_AMPM)
    value = (date_c if new_date else b.date_c, time_c if new_time else b.time_c, any_time or b.any_time)
    if changing and not (cued or ctx.pending in WHEN_GOALS):
        notices += apply_correction(ctx, "date", value, rt, cued=False, spoken=phrase)
        return
    notices += _set_field(ctx, "date", value, rt)
    b.when_phrase = phrase
    if changing:
        said = safe_call(prompts.speak_when, date_c if new_date else None, time_c if new_time else None,
                         default="") or _strip_cue(phrase)
        notices.append(Notice("correction.ack", {"value": said}, covered_by=_cover_words(said)))
    elif new_date:
        spoken = safe_call(prompts.speak_when, date_c, time_c, default=phrase) or phrase
        notices.append(Notice("ack.when", {"when": spoken}, covered_by=_cover_words(phrase)))


def _apply_manage_when(ctx: CallContext, u: Understanding, notices: list, rt: Runtime) -> None:
    m = ctx.manage
    if not m.verified:
        # "What are your timings on Saturday?" is a question, not the appointment's date.
        asking = u.has(Act.QUESTION) and not u.has(Act.ANSWER)
        phrase = u.appt_date_phrase or (None if asking else u.date_phrase)
        if phrase:
            when = _parse(phrase, "date")
            if when.date is not None:
                m.appt_date = when.date                 # verification only; issues don't apply
        return
    if ctx.intent != Intent.RESCHEDULE:
        return
    phrase = " ".join(p for p in (u.date_phrase, u.time_phrase) if p).strip()
    if not phrase:
        return
    when = _parse(phrase, "time" if ctx.pending in (Goal.ASK_TIME, Goal.RESOLVE_AMPM) else None)
    notices += _issue_notices(list(when.issues))
    changed = False
    if when.date is not None and when.date != m.new_date_c:
        m.new_date_c, changed = when.date, True
    if when.time is not None and when.time.kind != "any" and when.time != m.new_time_c:
        m.new_time_c, changed = when.time, True
    if changed:
        if m.offered:
            rt.release_requested = True
        m.offered, m.chosen, m.summary_heard = [], None, False


def _parse(phrase: str, expecting: Optional[str]):
    when = safe_call(dateparse.parse_when, phrase, today=clock.today(), expecting=expecting, default=None)
    return when if when is not None else dateparse.When()


def _from_iso(hint: str) -> tuple:
    """The model's YYYY-MM-DD, used only when dateparse couldn't read the phrase; same rules applied."""
    try:
        day = date.fromisoformat(hint.strip()[:10])
    except ValueError:
        return None, []
    checked, issues = dateparse._check_date(DateConstraint(day, day), clock.today())
    return checked, list(issues)


def _issue_notices(issues: list) -> list:
    out = []
    for issue in issues:
        line = _ISSUE_LINES.get(getattr(issue, "code", ""))
        if not line:
            continue
        params = {}
        if line == "date.invalid_day":
            month = issue.detail.get("month")
            day = issue.detail.get("day")
            params = {"month": _MONTH_NAMES[month - 1] if isinstance(month, int) and 1 <= month <= 12
                      else "That month", "day": _ordinal(day) if isinstance(day, int) else "that day"}
        if not any(n.line == line for n in out):
            out.append(Notice(line, params))
    return out


# ---------------------------------------------------------------- offers and confirmations


def _apply_choice(ctx: CallContext, u: Understanding, conf: Optional[str], rt: Runtime) -> bool:
    """A pick among the offered slots (by index, by a time that matches one, or yes to a single offer)."""
    offers = ctx.offers()
    if not offers:
        return False
    chosen = None
    if u.choice_index is not None and 1 <= u.choice_index <= len(offers):
        chosen = offers[u.choice_index - 1]
    elif ctx.pending in OFFER_GOALS and (u.time_phrase or u.date_phrase):
        when = _parse(" ".join(p for p in (u.date_phrase, u.time_phrase) if p), "time")
        if when.time is not None and when.time.kind == "exact":
            hits = [s for s in offers if s.start.time() == when.time.start
                    and (when.date is None or s.start.date() in set(when.date.dates()))]
            if len(hits) == 1:
                chosen = hits[0]
    elif ctx.pending in OFFER_GOALS and conf == "yes":
        line = ctx.pending_params.get("_line", "")
        if len(offers) == 1 or line in ("offer.exact", "offer.one"):
            chosen = offers[0]
    if chosen is not None:
        if ctx.intent == Intent.RESCHEDULE:
            ctx.manage.chosen = chosen
            ctx.manage.summary_heard = False
        else:
            ctx.book.chosen = chosen
            if ctx.book.branch and chosen.branch != ctx.book.branch:
                ctx.book.branch = chosen.branch   # offered from another branch (the day was full at theirs)
            ctx.book.touch()
        return True
    no_new_when = not (u.date_phrase or u.time_phrase)
    if u.reject_options or (ctx.pending in OFFER_GOALS and conf == "no" and no_new_when and not u.choice_index):
        draft = ctx.manage if ctx.intent == Intent.RESCHEDULE else ctx.book
        draft.offer_rounds += 1
        draft.offered = []
        draft.chosen = None
        rt.release_requested = True
    return False


def _route_confirmation(ctx: CallContext, u: Understanding, conf: Optional[str], proposal: dict,
                        rt: Runtime) -> list:
    """A yes / no answers whatever Emma's last reply asked (only the goals this module owns)."""
    pending = ctx.pending
    if pending == Goal.CONFIRM_CHANGE and proposal:
        if ctx.change_proposal != proposal:
            return []                                   # a newer change replaced it
        if conf == "yes":
            ctx.change_proposal = {}
            notices = _set_field(ctx, proposal["field"], proposal["value"], rt)
            spoken = proposal.get("spoken") or ""
            notices.append(Notice("correction.ack", {"value": _strip_cue(spoken)}, covered_by=_cover_words(_strip_cue(spoken))))
            return notices
        if conf == "no" or u.carries_details:
            ctx.change_proposal = {}                    # keep what we had
        return []
    if pending == Goal.ASK_BRANCH and ctx.pending_params.get("_line") == "ask.branch.choices" \
            and conf == "yes" and not ctx.book.branch and not ctx.book.branch_any:
        ctx.book.branch_any = True                      # "Shall I just go with whichever is earliest?" "Yes."
        ctx.book.touch()
        return []
    if pending == Goal.DROPPED and ctx.intent in (Intent.NONE, Intent.INFO):
        if conf == "yes":
            return switch_intent(ctx, Intent.CANCEL, rt)
        return []
    if pending == Goal.OFFER_REBOOK and conf == "yes" and ctx.intent != Intent.BOOK:
        return switch_intent(ctx, Intent.BOOK, rt)
    return []


# ---------------------------------------------------------------- field setters


def _set_field(ctx: CallContext, field_name: str, value, rt: Runtime) -> list:
    """Write one detail and re-check what depends on it. Returns notices (e.g. the branch no longer fits)."""
    b, c = ctx.book, ctx.caller
    notices: list = []
    if field_name == "name":
        c.name, c.name_state = value, FieldState.HEARD
        if value not in c.names_heard:
            c.names_heard.append(value)
        if ctx.intent in MANAGE_INTENTS:
            ctx.manage.patient_name = value
        b.touch()
    elif field_name == "phone":
        c.phone_e164, c.phone_state, c.phone_buffer = value, FieldState.PENDING, ""
    elif field_name == "service":
        b.service, b.service_options = value, []
        _drop_offers(ctx, rt)
        catalog = rt.catalog
        offering = tuple(catalog.branches_offering(value)) if getattr(catalog, "services", ()) else ()
        if b.branch and offering and b.branch not in offering:
            # Z6: the branch they chose doesn't do this service.
            notices.append(Notice("branch.no_service", {"branch": b.branch, "service": _spoken_service(value),
                                                        "branches": _list(list(offering))}))
            b.branch = None
        if not b.branch and len(offering) == 1:
            b.branch = offering[0]
            notices.append(Notice("branch.only", {"service": _capital(_spoken_service(value)),
                                                  "branch": offering[0]}, covered_by=(offering[0],)))
        b.touch()
    elif field_name == "branch":
        catalog = rt.catalog
        offering = tuple(catalog.branches_offering(b.service)) if b.service and getattr(catalog, "services", ()) \
            else ()
        if offering and value not in offering:
            notices.append(Notice("branch.no_service", {"branch": value, "service": _spoken_service(b.service),
                                                        "branches": _list(list(offering))}))
            return notices
        b.branch, b.branch_any = value, False
        if b.doctor_id is not None:
            doc = next((d for d in getattr(catalog, "doctors", ()) if d.id == b.doctor_id), None)
            if doc is not None and doc.branch.lower() != value.lower():
                b.doctor_id = b.doctor = None           # the preferred doctor isn't at the new branch
        _drop_offers(ctx, rt)
        b.touch()
    elif field_name == "date":
        date_c, time_c, any_time = value
        b.date_c, b.time_c, b.any_time = date_c, time_c, bool(any_time)
        _drop_offers(ctx, rt)
        b.touch()
    elif field_name == "doctor":
        doc = getattr(rt.catalog, "doctor", lambda _x: None)(value)
        if doc is not None:
            b.doctor_id, b.doctor = doc.id, doc.spoken
            b.touch()
    return notices


def _drop_offers(ctx: CallContext, rt: Runtime) -> None:
    b = ctx.book
    if b.offered or b.chosen is not None:
        rt.release_requested = True
    b.offered, b.chosen = [], None


def _drop(ctx: CallContext, rt: Runtime) -> list:
    """Cancel at the summary: this draft is dropped, nothing is booked, and DROPPED asks what they meant."""
    rt.release_requested = True
    ctx.book = BookingDraft(draft_id=ctx.book.draft_id + 1)
    ctx.intent = Intent.NONE
    ctx.change_proposal = {}
    rt.raised.append(Goal.DROPPED)
    return []


def _fresh_booking(ctx: CallContext, rt: Runtime) -> list:
    """Another booking after one was made: a new draft, the caller's name and number carried."""
    ctx.book = BookingDraft(draft_id=ctx.book.draft_id + 1)
    return []


# ---------------------------------------------------------------- small helpers


def _cued(u: Understanding, text: str) -> bool:
    return bool(u.correction or u.has(Act.CORRECTION) or _CUE_RE.search(text or ""))


def _draft_started(b: BookingDraft) -> bool:
    return bool(b.service or b.branch or b.date_c or b.offered or b.chosen or b.patient_name)


_NAME_INTRO_RE = re.compile(
    r"^\s*(?:my name is|my name's|name is|i am|i'm|this is|it's|it is|call me|myself)\s+", re.I)


def _clean_name(text: Optional[str]) -> Optional[str]:
    """match.clean_name, else a conservative fallback: 1-4 alphabetic words after "my name is"."""
    if not text:
        return None
    name = safe_call(match.clean_name, text, default=None)
    if name:
        return name
    rest = _NAME_INTRO_RE.sub("", text.strip()).strip(" .,!")
    words = rest.split()
    if 1 <= len(words) <= 4 and all(re.fullmatch(r"[A-Za-z][A-Za-z'.-]*", w) for w in words):
        return " ".join(w[:1].upper() + w[1:].lower() for w in words)
    return None


def _letters(text: str) -> Optional[str]:
    """ "A D H A R S H" -> "Adharsh" when match.join_spelled is unavailable."""
    letters = re.findall(r"\b([A-Za-z])\b", text or "")
    if len(letters) < 2:
        return None
    word = "".join(letters)
    return word[:1].upper() + word[1:].lower()


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _cover_words(text: str) -> tuple:
    """The words that, if the model's say already contains them, make a notice redundant."""
    return tuple(m.group(0).lower() for m in _WHEN_WORD_RE.finditer(text or "")) or \
        tuple(w for w in re.findall(r"[A-Za-z]{3,}", text or "")[:2])


def _spoken_value(field_name: str, value) -> str:
    if field_name == "date" and isinstance(value, tuple):
        return safe_call(prompts.speak_when, value[0], value[1], default="") or "that"
    if field_name == "phone":
        return safe_call(prompts.speak_phone, value, default=str(value))
    if field_name == "service":
        return _spoken_service(value)
    return str(value)


def _spoken_service(name: Optional[str]) -> str:
    return safe_call(prompts.speak_service, name or "", default=name or "that") if name else "that"


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _list(items: list) -> str:
    spoken = safe_call(prompts.speak_list, items, default=None)
    if spoken:
        return spoken
    if len(items) <= 1:
        return items[0] if items else ""
    return ", ".join(items[:-1]) + " or " + items[-1]
