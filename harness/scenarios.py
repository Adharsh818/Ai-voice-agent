"""
The scripted scenario catalogue.

First, every failing call from the 30 Sep - 1 Oct voice tests (docs/HANDOFF.md
section 5) as a regression scenario; then one scenario per edge case from
docs/SUCCESS_CRITERIA.md section 3 and HANDOFF step 1.

A scenario is a list of steps plus expected properties, never Emma's exact
wording, so it survives the engine rewrite:

    Say("How can you help me?", expect=[...], forbid=[DEFLECT])
        a fixed caller line; the reply must match every `expect` regex, at
        least one `expect_any` regex, and no `forbid` regex (case-insensitive)
    Auto(until=("confirm:summary",))
        the deterministic simulated caller (no disruptions) answers whatever
        Emma asks, from the scenario's profile, until she asks the named thing
        ("date", "branch", "confirm:phone", "choice:time"...). Without `until`
        it runs to the end of the call. This is what lets "say No four times
        at the branch question" reach the branch question whatever order the
        engine asks in. optional=True: if Emma never asks it (a better engine
        may not need to), the script ends there without failing.

Expect(...) holds the call-level properties: the database outcome, details of
the booking, phrases that must never be said, topics that must be covered, and
the metrics that must have no findings. `bug` names the known defect that
makes a scenario fail on today's engine; tests/test_conversations.py marks
those as expected failures until the fix sprint flips them.

Placeholders in caller lines: {card_patient} {card_phone} {card_date}
{card_time} {card_service} {card_branch} (a real seeded appointment) and
{name} {phone} (the scenario caller).
"""

from dataclasses import dataclass, field
from datetime import date, time
from typing import Optional

from harness import lines

DEFLECT = "|".join(lines.DEFLECTION_PATTERNS)
HUMAN_CLAIM = r"\b(i'?m|i am) (a )?(real )?(human|person)\b|\bi'?m not (a |an )?(bot|robot|ai)\b"
BOOK_WORDS = r"\b(book|booking|appointments?)\b"
MANAGE_WORDS = r"\b(change|changing|move|moving|reschedul\w*|cancel\w*)\b"
INFO_WORDS = r"\b(price|prices|pricing|costs?|timings?|hours|services|treatments|doctors?|dentists?|clinic|branch(es)?)\b"
REAL_DOCTORS = r"\bdr\.? (rao|shetty|iyer|menon|kulkarni|nair|reddy|ali|meera|arjun|kavya|rahul|sneha|vikram|ananya|farhan)\b"
BRACES_BRANCHES = ["Indiranagar", "Whitefield"]
PEDIATRIC_BRANCHES = ["Jayanagar", "Whitefield"]

TODAY = date(2026, 10, 1)            # the world's default frozen day (Thursday)
TOMORROW = date(2026, 10, 2)
SATURDAY = date(2026, 10, 3)
MONDAY = date(2026, 10, 5)
TUESDAY = date(2026, 10, 6)

DEFAULT_ZERO = ("M3", "Z1", "Z2", "Z5", "Z6", "Z7", "CRASH")


@dataclass
class Say:
    text: str
    expect: list = field(default_factory=list)
    expect_any: list = field(default_factory=list)
    forbid: list = field(default_factory=list)
    heard_previous: bool = True
    label: str = ""


@dataclass
class Auto:
    until: tuple = ()
    max_turns: int = 16
    label: str = ""
    optional: bool = False                   # not reaching `until` ends the script without failing it


@dataclass
class Expect:
    outcome: Optional[str] = None            # booked cancelled rescheduled none not_booked task booked_or_task
    booked: dict = field(default_factory=dict)  # service, branch_in, date, time, patient, phone
    must_not_say: list = field(default_factory=list)
    should_cover: list = field(default_factory=list)
    zero: tuple = DEFAULT_ZERO
    states_card: bool = False                # Emma tells the caller the card appointment's time (after verifying)


@dataclass
class Scenario:
    id: str
    title: str
    group: str                               # regression | catalogue
    source: str
    steps: list
    goal: str = "book"                       # the caller's goal as scored (Z5, M7, M10)
    profile: dict = field(default_factory=dict)
    card: bool = False
    expect: Expect = field(default_factory=Expect)
    bug: Optional[str] = None
    nlu_down: bool = False
    now: Optional[str] = None
    simple: bool = False


def _book(**kw) -> Expect:
    kw.setdefault("outcome", "booked")
    return Expect(**kw)


SCENARIOS: list[Scenario] = [
    # ======================================================== regressions (HANDOFF section 5)
    Scenario(
        "reg_location_no_loop", "Saying no at the branch question loops forever", "regression",
        "HANDOFF 5: 'No.' x4 -> 'Sorry, on this call I can only book our Nagarbhavi branch' x4",
        [Say("Hi, I'd like to book a check-up."), Auto(until=("branch", "confirm:branch", "choice:branch")),
         Say("No."), Say("No."), Say("No."), Say("No.")],
        profile={"service": "General Check-up", "service_phrase": "a check-up"},
        expect=Expect(zero=DEFAULT_ZERO),
        bug="step 6 location refusal has no exit (audit problem 2): the same branch line repeats",
    ),
    Scenario(
        "reg_cancel_at_recap", "'I'd like to cancel' at the recap, then 'Bye', ends in a booking", "regression",
        "HANDOFF 5: 'So I would like to cancel.' ... 'Bye.' -> 'shall I go ahead and book that?' -> 'Yeah.' -> booked",
        [Say("Hi, I'd like to book a cleaning."), Auto(until=("confirm:summary",)),
         Say("So I would like to cancel."), Say("Cancel the complete"), Say("Cancel it."), Say("Nothing."),
         Say("Nothing."), Say("Bye."), Say("Yeah.")],
        expect=Expect(outcome="none"),
        bug="no intent switch at the recap: 'cancel' is taken as a field correction and a later 'Yeah' books it",
    ),
    Scenario(
        "reg_braces_nagarbhavi", "Braces can never be booked (Nagarbhavi has no braces doctor)", "regression",
        "HANDOFF 5: '8AM.' x3 while booking Braces",
        [Say("Hello, I want to get braces."), Auto(max_turns=22)],
        profile={"service": "Braces", "service_phrase": "braces", "time": time(8, 0)},
        expect=_book(booked={"service": "Braces", "branch_in": BRACES_BRANCHES}),
        bug="bookings go to DEFAULT_BRANCH Nagarbhavi, which has no braces doctor, so step 10 loops on 'What other time'",
    ),
    Scenario(
        "reg_time_fragment_at_date", "A time-only fragment at the date step is read back as today", "regression",
        "HANDOFF 5: 'November 22, at' [cut off] then '5PM.' -> 'So Thursday, 01 October, is that right?'",
        [Say("Hi, I'd like to book a check-up."), Auto(until=("date",)), Say("November 22, at"),
         Say("5PM.", forbid=[r"\b0?1(st)? october\b", r"\btoday\b", r"\bthursday\b"])],
        profile={"service": "General Check-up", "service_phrase": "a check-up"},
        bug="backend_actions.resolve_date('5PM') falls through to today's date",
    ),
    Scenario(
        "reg_meta_how_can_you_help", "'How can you help me?' gets a booking push, not an answer", "regression",
        "HANDOFF 5 + feedback item 7: meta questions dead-end",
        [Say("How can you help me?", expect=[BOOK_WORDS, MANAGE_WORDS, INFO_WORDS],
             forbid=[DEFLECT, r"^would you like to book a visit\?$"]),
         Say("Okay, I'd like to book a cleaning then."), Auto()],
        expect=_book(),
        bug="no capability answer: meta questions get the escalation line plus 'Would you like to book a visit?'",
    ),
    Scenario(
        "reg_meta_who_are_you", "'Who are you?' gets the doctor line", "regression",
        "HANDOFF 5: 'Who are you?' (short call ending right after)",
        [Say("Who are you?", expect=[r"\bemma\b"], forbid=[DEFLECT, HUMAN_CLAIM]),
         Say("Okay. I want to book a check-up."), Auto()],
        expect=_book(zero=DEFAULT_ZERO + ("Z3",)),
        bug="meta questions fall through to the escalation line (no 'who I am' answer)",
    ),
    Scenario(
        "reg_meta_about_clinic", "'Tell me about the clinic' gets the doctor line", "regression",
        "HANDOFF 5: 'Tell me about the clinic / your company'",
        [Say("Tell me about the clinic.", forbid=[DEFLECT],
             expect_any=[r"nagarbhavi|indiranagar|jayanagar|whitefield|branches", r"check-?ups?|cleanings?|services|treatments",
                         r"\bopen\b|monday|hours"])],
        expect=Expect(outcome="none"),
        bug="no clinic overview answer: the escalation line is spoken instead",
    ),
    Scenario(
        "reg_meta_what_can_you_help_with", "'Tell me what you can help me with' dead-ends", "regression",
        "HANDOFF 5: 'Tell me what can you help me with'",
        [Say("Tell me what you can help me with.", expect=[BOOK_WORDS, INFO_WORDS], forbid=[DEFLECT])],
        expect=Expect(outcome="none"),
        bug="no capability answer for meta questions",
    ),
    Scenario(
        "reg_price_model_down", "A price question while the model is down gets the doctor line", "regression",
        "HANDOFF 5: ESCALATION_LINE is the fallback whenever the model is down",
        [Say("How much does a cleaning cost?", expect_any=[r"\d", r"rupees", r"range", r"depends"], forbid=[DEFLECT])],
        nlu_down=True, expect=Expect(outcome="none"),
        bug="with the NLU down every question gets ESCALATION_LINE instead of a knowledge-base answer",
    ),
    Scenario(
        "reg_price_root_canal", "A price question gets the price (invariant)", "regression",
        "HANDOFF 5: price questions answered with the doctor line",
        [Say("How much is a root canal?", expect_any=[r"5,?000", r"8,?000", r"rupees"], forbid=[DEFLECT])],
        expect=Expect(outcome="none"),
    ),
    Scenario(
        "reg_visit_question", "A general visit question gets the doctor line", "regression",
        "HANDOFF 5: price/visit questions answered with 'The doctor can go through that with you at your visit'",
        [Say("What happens during a cleaning?", forbid=[DEFLECT])],
        expect=Expect(outcome="none"),
        bug="no general (non-medical) dental knowledge: anything outside clinic_facts gets the doctor line",
    ),
    Scenario(
        "reg_fragment_whats_the_best", "A cut-off fragment is answered with the doctor line", "regression",
        "HANDOFF 5: fragments such as 'What's the best' (200 ms endpointing)",
        [Say("What's the best", forbid=[DEFLECT]),
         Say("What's the best time to come in?", forbid=[DEFLECT])],
        expect=Expect(outcome="none"),
        bug="a fragment is treated as a complete question and gets the escalation line",
    ),
    Scenario(
        "reg_fragment_tell_me", "'Tell me what can you' fragment, then the full question", "regression",
        "HANDOFF 5: 'Tell me what can you', 'Tell me how good you'",
        [Say("Tell me what can you", forbid=[DEFLECT]),
         Say("Tell me what you can do for me.", expect=[BOOK_WORDS], forbid=[DEFLECT])],
        expect=Expect(outcome="none"),
        bug="fragments and meta questions get the escalation line",
    ),
    Scenario(
        "reg_fragment_cancel_at_recap", "'Cancel the com' fragment at the recap", "regression",
        "HANDOFF 5: 'Cancel the com' at the recap",
        [Say("Hi, I'd like to book a filling."), Auto(until=("confirm:summary",)),
         Say("Cancel the com"), Say("Cancel the complete booking."), Say("Yes."), Say("Bye.")],
        profile={"service": "Tooth Filling", "service_phrase": "a filling"},
        expect=Expect(outcome="none"),
        bug="no intent switch at the recap: cancelling the booking in progress is impossible",
    ),
    Scenario(
        "reg_repeated_steer", "Three questions in a row: 'Would you like to book a visit?' three times", "regression",
        "HANDOFF feedback item 4 / SUCCESS_CRITERIA 3: the same question repeated",
        [Say("What are your timings?"), Say("Do you take insurance?"), Say("Is there parking at Nagarbhavi?")],
        expect=Expect(outcome="none"),
        bug="every off-topic answer re-asks the pending question, so the same question repeats (criterion 3)",
    ),
    Scenario(
        "reg_deepgram_phone_format", "Deepgram's US-style number '(789) 937-7462' is understood (invariant)",
        "regression", "HANDOFF 5: Deepgram formats numbers US-style",
        [Say("Hi, I want to book a check-up."), Auto(until=("phone",)), Say("(789) 937-7462"), Auto()],
        profile={"service": "General Check-up", "service_phrase": "a check-up", "phone": "7899377462"},
        expect=_book(booked={"phone": "+917899377462"}),
    ),
    Scenario(
        "reg_double_digits_phone", "'double one' in a phone number is understood (invariant)", "regression",
        "NORTH_STAR 2: Indian phrasing of numbers",
        [Say("Hi, I want to book a check-up."), Auto(until=("phone",)),
         Say("nine eight four five zero, double one two three four"), Auto()],
        profile={"service": "General Check-up", "service_phrase": "a check-up", "phone": "9845011234"},
        expect=_book(booked={"phone": "+919845011234"}),
    ),
    Scenario(
        "reg_simple_booking", "A plain booking, details one at a time (invariant, T5)", "regression",
        "SUCCESS_CRITERIA T5",
        [Say("Hi, I'd like to book a cleaning."), Auto()],
        expect=_book(booked={"service": "Teeth Cleaning"}, zero=DEFAULT_ZERO + ("M2", "M4")), simple=True,
    ),
    Scenario(
        "reg_bot_question", "'Am I talking to a real person?' gets the honest line (invariant)", "regression",
        "NORTH_STAR 5: the honesty boundary",
        [Say("Am I talking to a real person?", expect=[r"virtual receptionist|\bnot a (real )?person\b|\bi'?m an? (ai|virtual)"],
             forbid=[HUMAN_CLAIM]),
         Say("Okay, fine. I want to book a check-up."), Auto()],
        expect=_book(zero=DEFAULT_ZERO + ("Z3",)),
    ),

    # ======================================================== catalogue (SUCCESS_CRITERIA 3, HANDOFF step 1)
    Scenario(
        "cat_meta_mid_booking", "A capability question in the middle of a booking", "catalogue",
        "SUCCESS_CRITERIA 4: step away, answer, come back",
        [Say("Hi, I want to book a check-up."), Auto(until=("date",)),
         Say("Wait, what all can you help me with?", expect=[BOOK_WORDS, INFO_WORDS], forbid=[DEFLECT]), Auto()],
        profile={"service": "General Check-up", "service_phrase": "a check-up"},
        expect=_book(), bug="no capability answer for meta questions",
    ),
    Scenario(
        "cat_chitchat_then_book", "Chit-chat before booking", "catalogue", "SUCCESS_CRITERIA 1: off-topic comments",
        [Say("Hi, how's your day going?", forbid=[DEFLECT]), Say("I'd like to book a cleaning."), Auto()],
        expect=_book(), bug="chit-chat gets the escalation (doctor) line",
    ),
    Scenario(
        "cat_offtopic_mid_booking", "An off-topic comment at the date question", "catalogue",
        "SUCCESS_CRITERIA 1 and 4",
        [Say("Hi, I want to book a filling."), Auto(until=("date",)),
         Say("It's been raining so much here, no?", forbid=[DEFLECT]), Auto()],
        profile={"service": "Tooth Filling", "service_phrase": "a filling"},
        expect=_book(), bug="an off-topic comment gets the doctor line and is consumed as a failed date answer",
    ),
    Scenario(
        "cat_refuse_phone", "The caller won't give a number at first", "catalogue", "SUCCESS_CRITERIA 6: refusing to answer",
        [Say("Hi, I'd like to book a check-up."), Auto(until=("phone",)),
         Say("I'd rather not give my number.", forbid=[r"missed a digit"]), Auto()],
        profile={"service": "General Check-up", "service_phrase": "a check-up"},
        expect=_book(), bug="a refusal is treated as a misheard number ('I think I missed a digit')",
    ),
    Scenario(
        "cat_nonanswer_date", "'I've been really busy lately' at the date question", "catalogue",
        "SUCCESS_CRITERIA 6: the non-answer example",
        [Say("Hi, I'd like to book a cleaning."), Auto(until=("date",)),
         Say("I've been really busy lately.",
             expect_any=[r"this week|next week|weekend|morning|evening|suggest|how about|earliest|fits|find something"]),
         Auto()],
        expect=_book(), bug="non-answers get a canned re-ask instead of help choosing",
    ),
    Scenario(
        "cat_change_mind_date", "Changing the day after it was confirmed", "catalogue", "SUCCESS_CRITERIA 11: changes of mind",
        [Say("I want to book a cleaning for Monday."), Auto(until=("time",)),
         Say("Actually, can we make it Saturday instead?",
             expect_any=[r"saturday,? (the )?0?3(rd)?\b", r"\b0?3(rd)? (of )?october\b"], forbid=[r"didn'?t catch"]),
         Auto()],
        profile={"day": MONDAY, "time": time(11, 30)},
        expect=_book(booked={"date": SATURDAY.isoformat()}),
        bug="a change to an already-confirmed date is ignored (only the current step's slot is read)",
    ),
    Scenario(
        "cat_intent_switch_book_to_cancel", "Starts booking, then asks to cancel an existing appointment", "catalogue",
        "HANDOFF step 1: intent switches", [
            Say("Hi, I'd like to book a check-up."), Auto(until=("date",)),
            Say("Actually, I already have an appointment. Can you cancel that one instead?"), Auto(max_turns=18)],
        goal="cancel", card=True, profile={"goal": "book"},
        expect=Expect(outcome="cancelled"),
        bug="no intent switching and no cancel flow in the 12-step engine",
    ),
    Scenario(
        "cat_out_of_order", "'On 2nd October I want an appointment for a cleaning'", "catalogue",
        "NORTH_STAR 4: people who jump ahead",
        [Say("On 2nd October I want an appointment for a cleaning."), Auto()],
        profile={"day": TOMORROW},
        expect=_book(booked={"date": TOMORROW.isoformat(), "service": "Teeth Cleaning"}, zero=DEFAULT_ZERO + ("M2",)),
    ),
    Scenario(
        "cat_multi_detail", "Every detail in one sentence", "catalogue", "SUCCESS_CRITERIA 5: several details at once",
        [Say("Hi, I'm Priya Sharma, my number is 98450 12345, and I'd like a filling next Monday at 10 am."), Auto()],
        profile={"service": "Tooth Filling", "service_phrase": "a filling", "day": MONDAY, "time": time(10, 0)},
        expect=_book(booked={"service": "Tooth Filling", "date": MONDAY.isoformat(), "phone": "+919845012345"},
                     zero=DEFAULT_ZERO + ("M2",)),
    ),
    Scenario(
        "cat_correction_phone", "Correcting a misheard phone number", "catalogue", "SUCCESS_CRITERIA 11: corrections",
        [Say("I want to book a check-up."), Auto(until=("phone",)), Say("98450 12346"),
         Say("No, sorry, it's 98450 12345."), Auto()],
        profile={"service": "General Check-up", "service_phrase": "a check-up"},
        expect=_book(booked={"phone": "+919845012345"}),
    ),
    Scenario(
        "cat_correction_name", "Correcting a misheard name", "catalogue", "NORTH_STAR 2: names",
        [Say("I want to book a check-up."), Auto(until=("name",)), Say("My name is Priya Sarma."),
         Say("No, it's Priya Sharma."), Auto()],
        profile={"service": "General Check-up", "service_phrase": "a check-up"},
        expect=_book(booked={"patient": "Priya Sharma"}),
    ),
    Scenario(
        "cat_unknown_service_whitening", "Teeth whitening (talked about, not directly bookable)", "catalogue",
        "HANDOFF step 1: unknown services",
        [Say("Hi, do you do teeth whitening? I'd like to book that.", forbid=[DEFLECT]),
         Auto(until=("service",), optional=True),
         Say("Teeth whitening, like I said.", forbid=[r"what'?s the visit for|which treatment|what is it for"]), Auto()],
        profile={"service": None, "service_phrase": "teeth whitening"},
        expect=Expect(outcome="booked", must_not_say=[r"we don'?t (do|offer) (teeth )?whitening"],
                      zero=DEFAULT_ZERO + ("M2", "M4")),
        bug="unknown services get a fixed treatment list instead of the whitening fact and a consultation offer",
    ),
    Scenario(
        "cat_unknown_service_implant", "A dental implant", "catalogue", "HANDOFF step 1: unknown services",
        [Say("I need a dental implant, can I book an appointment?", forbid=[DEFLECT]),
         Auto(until=("service",), optional=True),
         Say("A dental implant, like I said.", forbid=[r"what'?s the visit for|which treatment|what is it for"]), Auto()],
        profile={"service": None, "service_phrase": "a dental implant"},
        expect=Expect(outcome="booked", zero=DEFAULT_ZERO + ("M2", "M4")),
        bug="unknown services get a fixed treatment list instead of the implant fact and a consultation offer",
    ),
    Scenario(
        "cat_branch_mismatch_braces", "Braces asked for at Nagarbhavi, which doesn't do braces", "catalogue",
        "HANDOFF step 2: branch-aware booking",
        [Say("I want braces at the Nagarbhavi branch."), Auto()],
        profile={"service": "Braces", "service_phrase": "braces", "branch": "Nagarbhavi"},
        expect=Expect(outcome="booked", booked={"branch_in": BRACES_BRANCHES},
                      should_cover=[r"indiranagar|whitefield"]),
        bug="no branch awareness: braces can't be booked, and Emma never mentions the branches that do them",
    ),
    Scenario(
        "cat_family_pediatric", "Booking for a child", "catalogue", "HANDOFF step 1: family bookings",
        [Say("I want to book an appointment for my son, he's 8."), Auto()],
        profile={"service": "Pediatric Dentistry", "service_phrase": "pediatric dentistry", "patient": "Aarav Sharma",
                 "relation": "son"},
        expect=_book(booked={"service": "Pediatric Dentistry", "branch_in": PEDIATRIC_BRANCHES, "patient": "Aarav"}),
        bug="Nagarbhavi has no pediatric doctor and the patient-vs-caller name isn't handled",
    ),
    Scenario(
        "cat_silence", "Silence in the middle of the call", "catalogue", "SUCCESS_CRITERIA 2: pauses",
        [Say("Hi, I'd like to book a check-up."), Auto(until=("name",)), Say(""), Say(""), Auto()],
        profile={"service": "General Check-up", "service_phrase": "a check-up"},
        expect=_book(), bug="after the silence ladder the same name question comes a third time, word for word",
    ),
    Scenario(
        "cat_bot_question_mid_flow", "'Is this a bot?' in the middle of a booking", "catalogue", "NORTH_STAR 5",
        [Say("Hi, I want to book a check-up."), Auto(until=("phone",)),
         Say("Wait, is this a bot?", expect=[r"virtual receptionist|\bi'?m an? (ai|virtual)"], forbid=[HUMAN_CLAIM]), Auto()],
        profile={"service": "General Check-up", "service_phrase": "a check-up"},
        expect=_book(zero=DEFAULT_ZERO + ("Z3",)),
    ),
    Scenario(
        "cat_person_request", "'Can I talk to a real person?'", "catalogue", "NORTH_STAR 3: help first",
        [Say("Can I talk to a real person?", forbid=[DEFLECT, r"connect you|transfer you|put you through"],
             expect_any=[r"\bhelp\b", BOOK_WORDS]),
         Say("Okay, I want to book a check-up."), Auto()],
        expect=_book(), bug="a person request gets the doctor line instead of an offer to help",
    ),
    Scenario(
        "cat_person_insist", "Insists on a person: a callback is taken", "catalogue", "NORTH_STAR 3: callback only if they insist",
        [Say("Can I talk to a real person?"), Say("No, I really want to speak to a human."),
         Say("Please just have someone call me back."), Auto(max_turns=8)],
        goal="questions", expect=Expect(outcome="task", should_cover=[r"call (you )?back|callback"]),
        bug="a person request gets the doctor line, the second 'no, a human' closes the call, and no callback task is made",
    ),
    Scenario(
        "cat_emergency_red_flag", "Red-flag symptoms", "catalogue", "NORTH_STAR 3: genuine emergencies",
        [Say("My face is swollen, I have a fever and I can't open my mouth properly.",
             expect_any=[r"\btoday\b|right away|as soon as|straight away|urgent|same day|\bnow\b"], forbid=[DEFLECT]),
         Auto(max_turns=16)],
        goal="emergency", profile={"goal": "emergency"},
        expect=Expect(outcome="booked_or_task", booked={"date": TODAY.isoformat()}),
        bug="no emergency path, and the 'can't' in 'I can't open my mouth' reads as a no at the greeting, which closes the call",
    ),
    Scenario(
        "cat_emergency_pain", "Severe pain, wants to come today", "catalogue", "clinic fact policy.emergency",
        [Say("I have severe tooth pain, can I come in today?"), Auto(max_turns=16)],
        goal="emergency", profile={"goal": "emergency"},
        expect=Expect(outcome="booked_or_task"),
        bug="no same-day urgent booking: 'as soon as possible' can't be turned into a slot",
    ),
    Scenario(
        "cat_cancel_verified", "Cancel an existing appointment, with verification", "catalogue",
        "SUCCESS_CRITERIA 2: verification by phone + name + date",
        [Say("Hi, I need to cancel my appointment."), Auto(max_turns=18)],
        goal="cancel", card=True, profile={"goal": "cancel"}, expect=Expect(outcome="cancelled"),
        bug="no cancel flow, and 'cancel' at the greeting reads as a no, which closes the call",
    ),
    Scenario(
        "cat_reschedule_verified", "Reschedule an existing appointment, with verification", "catalogue",
        "SUCCESS_CRITERIA 2", [Say("Hi, I need to reschedule my appointment."), Auto(max_turns=20)],
        goal="reschedule", card=True, profile={"goal": "reschedule"}, expect=Expect(outcome="rescheduled"),
        bug="no reschedule flow in the 12-step engine",
    ),
    Scenario(
        "cat_prepone", "'I want to prepone my appointment'", "catalogue", "Indian English: prepone = bring forward",
        [Say("I want to prepone my appointment."), Auto(max_turns=20)],
        goal="reschedule", card=True, profile={"goal": "reschedule", "earlier": True}, expect=Expect(outcome="rescheduled"),
        bug="no reschedule flow; 'prepone' isn't understood",
    ),
    Scenario(
        "cat_check_appointment", "'When is my appointment?' (verify before revealing)", "catalogue",
        "SUCCESS_CRITERIA Z7", [Say("Hi, can you tell me when my appointment is?"), Auto(max_turns=12)],
        goal="check", card=True, profile={"goal": "check"}, expect=Expect(outcome="none", states_card=True),
        bug="no check flow: the question gets the doctor line and the call closes on 'No'",
    ),
    Scenario(
        "cat_dr_sharma", "'Tomorrow evening around 6 with Dr Sharma' (no such doctor)", "catalogue",
        "SUCCESS_CRITERIA 5: several details at once",
        [Say("Tomorrow evening around 6 with Dr Sharma, for a check-up.", expect_any=[REAL_DOCTORS]), Auto()],
        profile={"service": "General Check-up", "service_phrase": "a check-up", "day": TOMORROW, "time": time(18, 0),
                 "name": "Rahul Verma"},
        expect=_book(booked={"date": TOMORROW.isoformat(), "time": "18:00"},
                     must_not_say=[r"\b(with|see|booked with) dr\.? sharma\b"], zero=DEFAULT_ZERO + ("M2",)),
        bug="doctor names are ignored: no 'there's no Dr Sharma' and no offer of the doctors who are there",
    ),
    Scenario(
        "cat_indian_phrasing", "'Kindly book one check-up for tomorrow morning itself, do the needful'", "catalogue",
        "SUCCESS_CRITERIA 3: Indian-English phrasing",
        [Say("Hello, kindly book one check-up for tomorrow morning itself, do the needful."), Auto()],
        profile={"service": "General Check-up", "service_phrase": "a check-up", "day": TOMORROW, "time": time(10, 0)},
        expect=_book(booked={"date": TOMORROW.isoformat()}),
        bug="'morning' is read as 7 AM, which no Nagarbhavi doctor works, and the offered slot is booked without a "
            "summary (Z1)",
    ),
    Scenario(
        "cat_sunday_request", "Asks for a Sunday", "catalogue", "clinic hours: closed Sundays",
        [Say("Hi, I'd like to book a cleaning."), Auto(until=("date",)),
         Say("Sunday at 10?", expect_any=[r"closed|sunday|monday|saturday"]), Auto()],
        expect=_book(),
    ),
    Scenario(
        "cat_lunch_time", "Asks for 2 pm (lunch break)", "catalogue", "clinic hours: lunch 2 to 2:30",
        [Say("Hi, I'd like to book a cleaning for Tuesday."), Auto(until=("time",)),
         Say("2 pm.", expect_any=[r"lunch|2:30|another time|other time|instead|how about|could do"]), Auto()],
        profile={"day": TUESDAY, "time": time(15, 0)},
        expect=_book(),
    ),
    Scenario(
        "cat_wrong_number", "'Sorry, wrong number'", "catalogue", "an early goodbye",
        [Say("Sorry, wrong number. Bye.", forbid=[DEFLECT])],
        goal="questions", expect=Expect(outcome="none"),
    ),
    Scenario(
        "cat_price_shopper", "Several price questions, then a booking", "catalogue", "SUCCESS_CRITERIA 3 personas: price shopper",
        [Say("How much is a root canal?"), Say("And how much is a filling?"), Say("What about a cleaning, how much is that?"),
         Say("Okay, I'll book a cleaning."), Auto()],
        expect=_book(zero=DEFAULT_ZERO + ("M4",)),
        bug="every answer re-asks 'Would you like to book a visit?', so it repeats three times",
    ),
    Scenario(
        "cat_hours_then_book", "Asks the Saturday hours, then books Saturday", "catalogue", "SUCCESS_CRITERIA 4",
        [Say("What time do you open on Saturday?"), Say("Okay, can I book a check-up for Saturday morning?"), Auto()],
        profile={"service": "General Check-up", "service_phrase": "a check-up", "day": SATURDAY, "time": time(10, 0)},
        expect=_book(booked={"date": SATURDAY.isoformat()}),
        bug="'Saturday morning' becomes 7 AM (no doctor then), and '9 in the morning' isn't understood when picking an "
            "offered slot (step 10 only matches '9 am')",
    ),
    Scenario(
        "cat_taken_slot", "The slot asked for is taken; the caller picks an offered one", "catalogue",
        "NORTH_STAR 4: nothing booked without a clear yes to a summary the caller heard",
        [Say("Hi, I'd like to book a cleaning for Monday at 11 am."), Auto()],
        profile={"service": "Teeth Cleaning", "service_phrase": "a cleaning", "day": MONDAY, "time": time(11, 0)},
        expect=_book(booked={"date": MONDAY.isoformat()}),
        bug="an offered alternative is booked the moment it's picked, with no summary and no yes (Z1)",
    ),
    Scenario(
        "cat_barge_in_summary", "A 'yes' over an interrupted summary must not book", "catalogue",
        "NORTH_STAR 4: a clear yes to a summary the caller actually heard",
        [Say("I want to book a check-up."), Auto(until=("confirm:summary",)),
         Say("Yes, yes, go ahead.", heard_previous=False), Auto()],
        profile={"service": "General Check-up", "service_phrase": "a check-up"},
        expect=Expect(outcome=None, zero=("Z1", "Z2", "CRASH")),
        bug="s.last_reply_heard is ignored: a yes over an interrupted summary books",
    ),
]

BY_ID = {s.id: s for s in SCENARIOS}


def get(scenario_id: str) -> Scenario:
    return BY_ID[scenario_id]


def suite(name: str) -> list[Scenario]:
    if name == "regression":
        return [s for s in SCENARIOS if s.group == "regression"]
    if name == "catalogue":
        return [s for s in SCENARIOS if s.group == "catalogue"]
    if name in ("all", "scenarios"):
        return list(SCENARIOS)
    raise KeyError(name)
