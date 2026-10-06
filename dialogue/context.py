"""
The per-call context: everything Emma knows about this call, in one picklable
object (docs/R2_DESIGN.md, section 3).

Replaces the 12 fixed steps of ai_engine.SessionState with a checklist plus a
priority order. Details can arrive in any order and are never asked twice;
Python works out the next goal from what is still missing (policy.next_goal).

Only plain data lives here: builtins, str enums, dataclasses, dates and the
frozen dateparse constraints. No connections, locks, callables or module
references, so a context survives pickle (the harness snapshots calls, and a
crash-safe resume can come later). Per-call state never lives in a module
global.

The enums and the GOAL_SPECS table are the contract every module codes
against: nlu.py builds its JSON schema from them, prompts.py keys lines by
them, and listening_hint() reads the expect column.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from dateparse import DateConstraint, TimeConstraint


# ---------------------------------------------------------------- enums


class Intent(str, Enum):
    """What the caller wants done. Questions alone never start a workflow."""
    NONE = "none"                # not said yet (just greeted, or chatting)
    BOOK = "book"
    RESCHEDULE = "reschedule"
    CANCEL = "cancel"
    CHECK = "check"              # "when is my appointment?"
    INFO = "info"                # only asking questions so far


MANAGE_INTENTS = (Intent.RESCHEDULE, Intent.CANCEL, Intent.CHECK)


class Emergency(str, Enum):
    NONE = "none"
    URGENT = "urgent"            # severe pain, swelling, bleeding, broken tooth: same-day slot + task
    RED_FLAG = "red_flag"        # breathing / swallowing trouble, spreading swelling: 108 / ER, task, end


class Act(str, Enum):
    """What the caller's turn did, besides any details it carried. A turn can have several."""
    ANSWER = "answer"                    # answered what Emma asked
    INFO = "info"                        # volunteered details Emma had not asked for yet
    QUESTION = "question"                # asked something (clinic, prices, dental, anything)
    CAPABILITY = "capability"            # "how can you help", "what do you do", "who are you"
    CHITCHAT = "chitchat"                # "how are you", small talk, thanks
    NON_ANSWER = "non_answer"            # replied without answering ("I've been really busy")
    CORRECTION = "correction"            # fixing something already given
    WANTS_HUMAN = "wants_human"          # asks for a person / receptionist / doctor on the line
    ROBOT_QUESTION = "robot_question"    # sincerely asks if Emma is a bot or a real person
    REPEAT = "repeat"                    # "sorry?", "come again", "say that again"
    WAIT = "wait"                        # "hold on", "one second"
    END = "end"                          # "that's all", "bye"
    ABUSE = "abuse"
    OTHER_LANGUAGE = "other_language"    # asks for, or speaks, a language other than English
    DONT_KEEP = "dont_keep"              # asks not to be recorded / kept
    FRAGMENT = "fragment"                # cut off mid-sentence ("What's the best", "Cancel the com")
    BACKCHANNEL = "backchannel"          # "mm-hmm", "yeah" with nothing pending
    UNCLEAR = "unclear"                  # nothing usable understood


class Goal(str, Enum):
    """
    What Emma's reply is trying to achieve this turn. Python computes it
    (policy.next_goal); the model predicts it too (nlu `next_goal`), and the
    model's own question is only used when the two agree.
    """
    # conversation
    GREET = "greet"
    ASK_INTENT = "ask_intent"                  # "What can I do for you?"
    ANSWER_ONLY = "answer_only"                # answer the question, no question back this turn
    OFFER_HELP = "offer_help"                  # occasional soft steer: "I can book that for you too, if you like."
    CAPABILITY = "capability"                  # what Emma can help with
    ANYTHING_ELSE = "anything_else"
    CLOSE = "close"                            # goodbye; ends the call
    # global handlers
    HOLD_ON = "hold_on"                        # "Sure, take your time."
    REPEAT = "repeat"                          # re-speak the last line (asked for, so not a loop)
    GO_ON = "go_on"                            # after a fragment: "Sorry, go on."
    SILENCE = "silence"                        # nudge after silence (params: level 1-3; 3 closes)
    HELP_FIRST = "help_first"                  # first person request: offer to help
    CALLBACK_OFFER = "callback_offer"          # "Want me to have the team call you back?"
    CALLBACK_DONE = "callback_done"            # task created; tell them who will call
    TRANSFER = "transfer"                      # phone calls: task created, put through to the front desk; ends Emma's part
    ENGLISH_ONLY = "english_only"
    ABUSE_WARN = "abuse_warn"
    ABUSE_CLOSE = "abuse_close"
    DONT_KEEP_ACK = "dont_keep_ack"
    RED_FLAG = "red_flag"                      # 108 / ER advice; ends the call
    CONFIRM_CHANGE = "confirm_change"          # "Did you want to change the date to Tuesday?"
    # identity (shared by every workflow)
    ASK_NAME = "ask_name"
    SPELL_NAME = "spell_name"
    ASK_PHONE = "ask_phone"
    PHONE_MORE = "phone_more"                  # partial number heard: "Mm-hmm." and keep listening
    CONFIRM_PHONE = "confirm_phone"            # grouped read-back, explicit yes required
    # BOOK
    ASK_PATIENT = "ask_patient"                # booking for someone else: their name
    ASK_AGE = "ask_age"                        # pediatric only
    ASK_SERVICE = "ask_service"
    CLARIFY_SERVICE = "clarify_service"        # "a filling, an extraction or a check-up?"
    ASK_BRANCH = "ask_branch"                  # only branches that offer the service
    ASK_WHEN = "ask_when"
    ASK_TIME = "ask_time"                      # a day is known: "Morning or evening?"
    RESOLVE_AMPM = "resolve_ampm"              # "7 in the morning or the evening?"
    OFFER_SLOTS = "offer_slots"                # real, held slots
    NO_SLOTS = "no_slots"                      # nothing within the search: another week, or a callback
    MAX_REACHED = "max_reached"                # 3 future appointments on this number
    DUPLICATE_CHECK = "duplicate_check"        # same patient already has one: another, or change it?
    SUMMARY = "summary"                        # one natural summary, then "Shall I book it?"
    SUMMARY_AGAIN = "summary_again"            # the caller barged into the summary: finish it, ask again
    WHAT_TO_CHANGE = "what_to_change"          # "no" to the summary with nothing else
    BOOKED = "booked"                          # committed; confirmation + "anything else?"
    DROPPED = "dropped"                        # caller said cancel / don't book at the summary
    # MANAGE
    ASK_APPT_DATE = "ask_appt_date"            # verification: the date of the appointment
    VERIFY_FAILED = "verify_failed"            # nothing matched; reveal nothing, ask which detail to check
    PICK_APPOINTMENT = "pick_appointment"      # several verified matches
    STATE_APPOINTMENT = "state_appointment"    # CHECK: the verified appointment
    CONFIRM_CANCEL = "confirm_cancel"
    ASK_CANCEL_REASON = "ask_cancel_reason"    # optional, asked once
    CANCELLED = "cancelled"
    OFFER_REBOOK = "offer_rebook"
    ASK_NEW_WHEN = "ask_new_when"
    OFFER_NEW_SLOTS = "offer_new_slots"
    CONFIRM_RESCHEDULE = "confirm_reschedule"  # "from X to Y, shall I move it?"
    RESCHEDULED = "rescheduled"
    TOO_LATE = "too_late"                      # starts within the lead time or has passed


class Expect(str, Enum):
    """What the caller's next words are likely to be (ai_engine.listening_hint, the turn detector)."""
    PHONE = "phone"
    NAME = "name"
    YES_NO = "yes_no"
    DATE = "date"
    TIME = "time"
    CHOICE = "choice"
    OPEN = "open"
    SPELLING = "spelling"


class FieldState(str, Enum):
    EMPTY = "empty"
    HEARD = "heard"              # taken as said; confirmed implicitly by speaking it back
    PENDING = "pending"          # read back, waiting for an explicit yes (phone)
    CONFIRMED = "confirmed"
    UNVERIFIED = "unverified"    # a name that kept failing: kept, flagged for staff


@dataclass(frozen=True)
class GoalSpec:
    expect: Expect
    critical: bool               # True: only a pre-written line may carry it (never the model's words)
    required_for: tuple = ()     # workflows whose checklist this goal fills


def _spec(expect: Expect, critical: bool = False, *required: Intent) -> GoalSpec:
    return GoalSpec(expect, critical, tuple(required))


# The contract table. critical=True lines state facts that must be exact
# (read-backs, slots, summaries, outcomes) or end the call.
GOAL_SPECS: dict[Goal, GoalSpec] = {
    Goal.GREET: _spec(Expect.OPEN, True),
    Goal.ASK_INTENT: _spec(Expect.OPEN),
    Goal.ANSWER_ONLY: _spec(Expect.OPEN),
    Goal.OFFER_HELP: _spec(Expect.YES_NO),
    Goal.CAPABILITY: _spec(Expect.OPEN),
    Goal.ANYTHING_ELSE: _spec(Expect.YES_NO),
    Goal.CLOSE: _spec(Expect.OPEN, True),
    Goal.HOLD_ON: _spec(Expect.OPEN, True),
    Goal.REPEAT: _spec(Expect.OPEN, True),
    Goal.GO_ON: _spec(Expect.OPEN, True),
    Goal.SILENCE: _spec(Expect.OPEN, True),
    Goal.HELP_FIRST: _spec(Expect.OPEN),
    Goal.CALLBACK_OFFER: _spec(Expect.YES_NO),
    Goal.CALLBACK_DONE: _spec(Expect.YES_NO, True),
    Goal.TRANSFER: _spec(Expect.OPEN, True),
    Goal.ENGLISH_ONLY: _spec(Expect.YES_NO, True),
    Goal.ABUSE_WARN: _spec(Expect.OPEN, True),
    Goal.ABUSE_CLOSE: _spec(Expect.OPEN, True),
    Goal.DONT_KEEP_ACK: _spec(Expect.OPEN, True),
    Goal.RED_FLAG: _spec(Expect.OPEN, True),
    Goal.CONFIRM_CHANGE: _spec(Expect.YES_NO, True),
    Goal.ASK_NAME: _spec(Expect.NAME, False, Intent.BOOK, *MANAGE_INTENTS),
    Goal.SPELL_NAME: _spec(Expect.SPELLING, False, Intent.BOOK, *MANAGE_INTENTS),
    Goal.ASK_PHONE: _spec(Expect.PHONE, False, Intent.BOOK, *MANAGE_INTENTS),
    Goal.PHONE_MORE: _spec(Expect.PHONE, True, Intent.BOOK, *MANAGE_INTENTS),
    Goal.CONFIRM_PHONE: _spec(Expect.YES_NO, True, Intent.BOOK, *MANAGE_INTENTS),
    Goal.ASK_PATIENT: _spec(Expect.NAME, False, Intent.BOOK),
    Goal.ASK_AGE: _spec(Expect.OPEN, False, Intent.BOOK),
    Goal.ASK_SERVICE: _spec(Expect.OPEN, False, Intent.BOOK),
    Goal.CLARIFY_SERVICE: _spec(Expect.CHOICE, False, Intent.BOOK),
    Goal.ASK_BRANCH: _spec(Expect.CHOICE, False, Intent.BOOK),
    Goal.ASK_WHEN: _spec(Expect.DATE, False, Intent.BOOK),
    Goal.ASK_TIME: _spec(Expect.TIME, False, Intent.BOOK, Intent.RESCHEDULE),
    Goal.RESOLVE_AMPM: _spec(Expect.TIME, False, Intent.BOOK, Intent.RESCHEDULE),
    Goal.OFFER_SLOTS: _spec(Expect.CHOICE, True, Intent.BOOK),
    Goal.NO_SLOTS: _spec(Expect.DATE, True, Intent.BOOK, Intent.RESCHEDULE),
    Goal.MAX_REACHED: _spec(Expect.OPEN, True, Intent.BOOK),
    Goal.DUPLICATE_CHECK: _spec(Expect.CHOICE, True, Intent.BOOK),
    Goal.SUMMARY: _spec(Expect.YES_NO, True, Intent.BOOK),
    Goal.SUMMARY_AGAIN: _spec(Expect.YES_NO, True, Intent.BOOK),
    Goal.WHAT_TO_CHANGE: _spec(Expect.OPEN, False, Intent.BOOK),
    Goal.BOOKED: _spec(Expect.YES_NO, True, Intent.BOOK),
    Goal.DROPPED: _spec(Expect.YES_NO, True, Intent.BOOK),
    Goal.ASK_APPT_DATE: _spec(Expect.DATE, False, *MANAGE_INTENTS),
    Goal.VERIFY_FAILED: _spec(Expect.OPEN, True, *MANAGE_INTENTS),
    Goal.PICK_APPOINTMENT: _spec(Expect.CHOICE, True, *MANAGE_INTENTS),
    Goal.STATE_APPOINTMENT: _spec(Expect.YES_NO, True, Intent.CHECK),
    Goal.CONFIRM_CANCEL: _spec(Expect.YES_NO, True, Intent.CANCEL),
    Goal.ASK_CANCEL_REASON: _spec(Expect.OPEN, False, Intent.CANCEL),
    Goal.CANCELLED: _spec(Expect.YES_NO, True, Intent.CANCEL),
    Goal.OFFER_REBOOK: _spec(Expect.YES_NO, False, Intent.CANCEL),
    Goal.ASK_NEW_WHEN: _spec(Expect.DATE, False, Intent.RESCHEDULE),
    Goal.OFFER_NEW_SLOTS: _spec(Expect.CHOICE, True, Intent.RESCHEDULE),
    Goal.CONFIRM_RESCHEDULE: _spec(Expect.YES_NO, True, Intent.RESCHEDULE),
    Goal.RESCHEDULED: _spec(Expect.YES_NO, True, Intent.RESCHEDULE),
    Goal.TOO_LATE: _spec(Expect.YES_NO, True, *MANAGE_INTENTS),
}

# Goals after which the call is over (closed_conversation is set).
CLOSING_GOALS = frozenset({Goal.CLOSE, Goal.ABUSE_CLOSE, Goal.RED_FLAG, Goal.TRANSFER})

# Loop breaker (docs/R2_DESIGN.md, section 9): which rung of the ladder an ask
# is on, from how many times the caller's reply missed it.
RUNG_ASK, RUNG_REPHRASE, RUNG_CHOICES, RUNG_EXIT = 1, 2, 3, 4


# ---------------------------------------------------------------- one turn's understanding


@dataclass
class Understanding:
    """
    What one caller turn meant. Tier-0 (tier0.understand), the model
    (nlu.understand_stream) and the no-model fallback all produce this same
    type, so nothing downstream can tell which tier made it. Every value is
    raw: apply.py validates it (catalog, dateparse, phones) before use.
    """
    acts: list = field(default_factory=list)       # Act values, e.g. ["answer", "question"]
    intent: Optional[Intent] = None                # None = no change of intent expressed
    emergency: Emergency = Emergency.NONE
    confirmation: Optional[str] = None             # "yes" | "no" | None
    correction: bool = False
    # identity
    name: Optional[str] = None                     # the caller's own name, as said
    name_spelled: Optional[str] = None             # letters joined: "ADHARSH"
    phone_digits: Optional[str] = None             # digits only, as many as were said this turn
    for_someone_else: Optional[bool] = None
    patient_name: Optional[str] = None
    relation: Optional[str] = None                 # "son", "mother"...
    age: Optional[int] = None
    # booking details
    service: Optional[str] = None                  # canonical service name (schema enum)
    service_phrase: Optional[str] = None           # the words used ("route canal", "whitening")
    branch: Optional[str] = None                   # canonical branch name (schema enum)
    branch_any: bool = False                       # "whichever is earliest / closest"
    doctor: Optional[str] = None                   # a catalog doctor's spoken name ("Dr Rao")
    doctor_phrase: Optional[str] = None            # a doctor named but not in the catalog ("Dr Sharma")
    doctor_gender: Optional[str] = None            # "female" | "male"
    date_phrase: Optional[str] = None
    time_phrase: Optional[str] = None
    date_iso_hint: Optional[str] = None            # used only if dateparse cannot read date_phrase
    choice_index: Optional[int] = None             # 1-based, among the options Emma just offered
    reject_options: bool = False                   # "none of those work"
    # manage
    appt_date_phrase: Optional[str] = None         # verification: the date of the existing appointment
    cancel_reason: Optional[str] = None
    # knowledge
    question: Optional[str] = None                 # the caller's question in a few words
    faq_ids: list = field(default_factory=list)    # knowledge-base fact ids that answer it
    clinical: bool = False                         # needs a doctor's judgement (diagnosis, "do I need X", medicine)
    wants_callback: Optional[bool] = None          # answer to a callback offer
    # the model's proposal (empty for Tier-0 and fallback)
    next_goal: Optional[Goal] = None
    say: str = ""                                  # acknowledgement / answer, 0-2 sentences, no question
    ask: str = ""                                  # at most one question, toward next_goal
    # bookkeeping
    source: str = "tier0"                          # tier0 | llm | fallback
    raw_text: str = ""                             # the caller's words this turn (after fragment merge)

    def has(self, act: Act) -> bool:
        return act.value in self.acts or act in self.acts

    @property
    def carries_details(self) -> bool:
        """True when the turn gave any booking or identity detail, a yes/no or a pick."""
        return any(v not in (None, "", False) for v in (
            self.name, self.name_spelled, self.phone_digits, self.patient_name, self.age,
            self.service, self.service_phrase, self.branch, self.branch_any, self.doctor,
            self.doctor_phrase, self.doctor_gender, self.date_phrase, self.time_phrase,
            self.choice_index, self.reject_options, self.appt_date_phrase, self.confirmation,
            self.cancel_reason, self.for_someone_else))

    @property
    def pure_question(self) -> bool:
        """A question, capability ask or small talk that carries nothing else (the steer-back rules)."""
        asked = self.has(Act.QUESTION) or self.has(Act.CAPABILITY) or self.has(Act.CHITCHAT)
        return asked and not self.carries_details and self.intent in (None, Intent.INFO, Intent.NONE)


# ---------------------------------------------------------------- slots and drafts


@dataclass
class OfferedSlot:
    """A real slot Emma offered, with the hold that reserves it for this call."""
    doctor_id: int
    doctor: str                  # spoken name, "Dr Rao"
    branch_id: int
    branch: str
    service_id: int
    service: str
    start: datetime              # clinic-local, timezone-aware
    end: datetime
    hold_id: Optional[str] = None
    spoken: str = ""             # how it was said: "Monday the 5th at 5"


@dataclass
class Caller:
    """Who is calling. Shared by every workflow, so an intent switch never re-asks it."""
    name: Optional[str] = None
    name_state: FieldState = FieldState.EMPTY
    names_heard: list = field(default_factory=list)   # every name candidate this call (fuzzy matching)
    name_misses: int = 0                               # corrections of the name; 1 -> spell it
    phone_e164: Optional[str] = None
    phone_state: FieldState = FieldState.EMPTY
    phone_buffer: str = ""                             # digits accumulated across turns
    phone_misses: int = 0                              # read-backs rejected
    # "caller_id": phone_e164 came from the phone network, so it's confirmed by asking
    # "Is the number you're calling from the best one?" rather than read back;
    # "declined": the caller said no to that question (not a mishearing).
    phone_source: Optional[str] = None


@dataclass
class BookingDraft:
    """The BOOK checklist. `version` goes up on every change, so a summary heard earlier can't be reused."""
    draft_id: int = 1
    version: int = 0
    service: Optional[str] = None
    service_phrase: Optional[str] = None               # kept for the brief ("route canal")
    service_options: list = field(default_factory=list)  # ambiguity: candidates to choose from
    branch: Optional[str] = None
    branch_any: bool = False
    doctor_id: Optional[int] = None                    # preference, from the catalog
    doctor: Optional[str] = None
    doctor_gender: Optional[str] = None
    unknown_doctor: Optional[str] = None               # "Dr Sharma": said once, then cleared
    date_c: Optional[DateConstraint] = None
    time_c: Optional[TimeConstraint] = None
    when_phrase: str = ""
    any_time: bool = False                             # "whenever", "any time": no time question
    for_someone_else: bool = False
    patient_name: Optional[str] = None
    relation: Optional[str] = None
    age: Optional[int] = None
    offered: list = field(default_factory=list)        # OfferedSlot, currently held
    offer_rounds: int = 0                              # searches the caller turned down
    chosen: Optional[OfferedSlot] = None
    duplicate_ok: bool = False                         # caller wants another despite an existing one
    summary_version: Optional[int] = None              # draft version the caller heard summarised
    summary_heard: bool = False                        # every detail sentence played (recap-heard rule)
    emergency: bool = False
    appointment_id: Optional[str] = None               # set once booked

    def touch(self):
        """Record a change: any summary heard before no longer counts."""
        self.version += 1
        self.summary_heard = False


@dataclass
class VerifiedAppointment:
    """An existing appointment, only ever loaded after verification (Z7)."""
    appointment_id: str
    version: int
    patient_name: str
    service: str
    service_id: int
    doctor: str
    doctor_id: int
    branch: str
    branch_id: int
    start: datetime
    spoken: str = ""


@dataclass
class ManageDraft:
    """The MANAGE checklist: verify (phone + name + date), then check, cancel or reschedule."""
    action: Optional[Intent] = None                    # RESCHEDULE | CANCEL | CHECK
    patient_name: Optional[str] = None                 # the name the appointment is under
    appt_date: Optional[DateConstraint] = None
    verify_attempts: int = 0
    verified: bool = False
    matches: list = field(default_factory=list)        # VerifiedAppointment, after verification only
    target: Optional[VerifiedAppointment] = None
    cancel_reason: Optional[str] = None
    reason_asked: bool = False
    new_date_c: Optional[DateConstraint] = None
    new_time_c: Optional[TimeConstraint] = None
    offered: list = field(default_factory=list)        # OfferedSlot
    offer_rounds: int = 0
    chosen: Optional[OfferedSlot] = None
    summary_heard: bool = False
    done: bool = False                                 # the change (or the check) is complete


@dataclass
class GoalStats:
    asked: int = 0               # times Emma asked for it
    misses: int = 0              # replies that did not fill it (not counting questions or switches)
    last_turn: int = 0           # turn number it was last asked


@dataclass
class PromptMemory:
    """What has been said, so no line repeats word for word in a call (prompts.render)."""
    used: dict = field(default_factory=dict)           # line id -> list of variant indexes used, in order
    recent: list = field(default_factory=list)         # Emma's last few sentences (similarity check)
    last_opener: Optional[str] = None
    turns_since_umm: int = 99
    checking_used: list = field(default_factory=list)  # "let me just check" variants used


@dataclass
class TurnTrace:
    """One turn, for logs and the harness (the off-topic logging gap in docs/HANDOFF.md, section 5)."""
    turn: int
    tier: int
    acts: list
    goal_before: Optional[str]
    goal_after: Optional[str]
    action: Optional[str] = None
    model_goal: Optional[str] = None
    used_model_say: bool = False
    used_model_ask: bool = False
    dropped: list = field(default_factory=list)        # validator names that dropped a sentence
    fallback: bool = False                             # the model was unavailable this turn
    nlu_ms: float = 0.0


@dataclass
class CallContext:
    """
    Everything about one call. Created by ai_engine.new_session(). The fields
    closed_conversation, history and last_reply_heard are part of the shared
    facade contract (call_session and the harness read or set them).
    """
    call_id: Optional[str] = None
    turn: int = 0
    greeted: bool = False
    greeting_offer: Optional[str] = None         # the greeting asked "book an appointment?" / "know about the clinic?"
    closed_conversation: bool = False
    outcome: Optional[str] = None                      # booked | rescheduled | cancelled | checked | callback | red_flag | ...
    history: list = field(default_factory=list)        # [{"role": "user"|"assistant", "content": str}], last 20
    last_reply_heard: bool = True                      # set by call_session: did Emma's previous turn play to the end?

    intent: Intent = Intent.NONE
    emergency: Emergency = Emergency.NONE
    caller: Caller = field(default_factory=Caller)
    book: BookingDraft = field(default_factory=BookingDraft)
    manage: ManageDraft = field(default_factory=ManageDraft)
    parked_book: Optional[BookingDraft] = None         # a booking set aside by an intent switch

    pending: Optional[Goal] = None                     # the goal of Emma's last reply
    pending_params: dict = field(default_factory=dict)
    goal_stats: dict = field(default_factory=dict)     # Goal value -> GoalStats
    question_streak: int = 0                           # consecutive pure-question turns
    answers_since_offer: int = 0
    offers_made: int = 0
    human_requests: int = 0
    abuse_warnings: int = 0
    language_warnings: int = 0
    silence_level: int = 0
    fragment: str = ""                                 # a cut-off utterance, prefixed to the next turn
    change_proposal: dict = field(default_factory=dict)  # CONFIRM_CHANGE: {"field": ..., "value": ...}
    callback_reason: Optional[str] = None              # why a callback is on offer (task note)
    tasks_created: list = field(default_factory=list)  # task ids from tasks.create_task
    keep_transcript: bool = True
    # Phone calls (Asterisk, audiosocket.py): the front desk can take a live
    # transfer, and set when Emma has promised one (the transport routes the call).
    can_transfer: bool = False
    transfer_requested: bool = False
    last_emma: str = ""                             # Emma's last full reply (REPEAT re-speaks it)
    prompts: PromptMemory = field(default_factory=PromptMemory)
    trace: list = field(default_factory=list)          # TurnTrace, last 50

    # -- helpers used by every module (docs/R2_DESIGN.md, section 3.3) --------

    def stats(self, goal: Goal) -> GoalStats:
        """The loop-breaker counters for a goal, created on first use."""
        return self.goal_stats.setdefault(goal.value, GoalStats())

    def remember(self, user_text: str, reply: str):
        """Append the turn to history (last 20 entries), as the old engine did."""
        if user_text:
            self.history.append({"role": "user", "content": user_text})
        if reply:
            self.history.append({"role": "assistant", "content": reply})
        if len(self.history) > 20:
            self.history = self.history[-20:]

    def workflow_active(self) -> bool:
        return self.intent in (Intent.BOOK, *MANAGE_INTENTS)

    def offers(self) -> list:
        """The slots on offer in the workflow under way (a pick refers to these)."""
        return self.manage.offered if self.intent == Intent.RESCHEDULE else self.book.offered

    def add_trace(self, trace: "TurnTrace") -> None:
        """Keep the last 50 turn records (logs and the harness read them)."""
        self.trace.append(trace)
        if len(self.trace) > 50:
            self.trace = self.trace[-50:]

    def note_said(self, sentences: list) -> None:
        """
        Remember Emma's sentences for the repetition check (validate V7 and
        prompts.render read PromptMemory.recent). Pre-written lines are
        recorded by render itself, so only add what render did not produce.
        """
        for sentence in sentences:
            if sentence and (not self.prompts.recent or self.prompts.recent[-1] != sentence):
                self.prompts.recent.append(sentence)
        if len(self.prompts.recent) > 8:
            self.prompts.recent = self.prompts.recent[-8:]


@dataclass(frozen=True)
class Tier0View:
    """
    The read-only slice of the call Tier-0 may look at (tier0.understand).
    Built by the engine each turn; Tier-0 never sees or mutates the context.
    """
    expect: Expect
    pending: Optional[Goal]
    intent: Intent
    digits_so_far: str = ""              # Caller.phone_buffer
    offered: tuple = ()                  # OfferedSlot currently on offer (choice picks)
    options: tuple = ()                  # other spoken options on offer (services, branches, appointments)
    names_heard: tuple = ()
    catalog: object = None               # facts.Catalog snapshot (services, branches, doctors + aliases)


@dataclass
class Notice:
    """
    A pre-written sentence Python adds this turn, between the model's `say`
    and the ask: implicit confirmations ("Priya, got it."), corrections
    ("Okay, Tuesday instead."), facts the caller must hear ("We don't have a
    Dr Sharma, but Dr Rao and Dr Shetty are at Nagarbhavi.").
    `covered_by` lists words that, if the model's `say` already contains them,
    make the notice redundant (it is then dropped, so nothing is said twice).
    """
    line: str                    # prompts line id
    params: dict = field(default_factory=dict)
    covered_by: tuple = ()


@dataclass
class GoalPlan:
    """Python's decision for this turn's reply (policy.next_goal)."""
    goal: Goal
    line: str                    # prompts line id for the pre-written version of the ask/statement
    params: dict = field(default_factory=dict)
    rung: int = RUNG_ASK         # loop-breaker rung the line was chosen for
    critical: bool = False       # only the pre-written line; the model's ask is discarded
    use_model_say: bool = True   # the model's acknowledgement/answer may precede
    steer: bool = True           # False = answer only, no question this turn
    expect: Expect = Expect.OPEN
    closes_call: bool = False


@dataclass
class ActionResult:
    """What a workflow commit did. `action` feeds TurnResult.action; nothing else may claim it."""
    action: Optional[str] = None     # booked | rescheduled | cancelled | None
    ok: bool = False
    code: str = ""                   # scheduling.Result.code (OK, TAKEN, MAX_FUTURE, ...)
    appointment_id: Optional[str] = None
    notices: list = field(default_factory=list)


def new_context(call_id: Optional[str] = None) -> CallContext:
    """A fresh context for one call (ai_engine.new_session delegates here)."""
    return CallContext(call_id=call_id)
