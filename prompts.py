"""
Everything Emma can say without the model (docs/R2_DESIGN.md, section 8).

Two jobs:
1. The fallback for every goal: when the model is down, slow, or its reply
   fails validation, Emma speaks a human-written line for Python's goal.
2. Commit-critical lines, always pre-written so they are exact: the phone
   read-back, slot offers, the summary, and the booked / cancelled / moved
   confirmations (GOAL_SPECS critical=True).

Lines are keyed by id (LINES below; policy.py, book.py, manage.py and
handlers.py reference ids only, never wording). Each id has several variants
(VARIANTS, written by E3); render() picks one this call hasn't used, never
the same text as Emma's previous sentence, so no line repeats word for word
within a call (NORTH_STAR checklist). Loop-breaker rungs are separate ids
("ask.when" -> "ask.when.rephrase" -> "ask.when.choices"), so a re-ask is a
genuinely different sentence, not a synonym swap.

Wording rules (tests/test_realism.py scans this file): short, warm,
receptionist phrasing; one question; no bot / disclaimer / form / handoff
wording; "virtual receptionist" only in config.HONEST_LINE.

Pre-rendering: ElevenLabs characters are scarce (free tier). Only lines with
cache=True and no placeholders are pre-rendered (phrases.all_phrases picks
them up); everything else goes to live TTS. tests keep the cached total under
CACHE_CHAR_BUDGET.

Owner in Sprint 1b: E3. The registry below is the contract.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional

import clock
import config
import phones

CACHE_CHAR_BUDGET = 2500     # extra characters the new cached lines may cost, in total


@dataclass(frozen=True)
class LineSpec:
    desc: str                    # what it is for, with an example of the intended wording
    params: tuple = ()           # placeholders the variants may use ({name}, {phone} ...)
    critical: bool = False       # states exact facts or an outcome; never replaced by model text
    cache: bool = False          # worth pre-rendering (frequent, no placeholders)


def _l(desc: str, *params: str, critical: bool = False, cache: bool = False) -> LineSpec:
    return LineSpec(desc, tuple(params), critical, cache)


# ---------------------------------------------------------------- the registry
# Ids referenced by the dialogue modules. Rungs: <id> (1st ask), <id>.rephrase
# (2nd), <id>.choices (3rd: offer options / spell / digit groups).
# cache=True only on the lines heard in most calls (asks for name, number,
# service and day, fillers of the conversation, the checking phrase): all
# their variants together must stay under CACHE_CHAR_BUDGET. Rare lines (red
# flag, abuse, language) go to live TTS; a few hundred ms there costs nothing.
LINES: dict[str, LineSpec] = {
    # conversation
    "ask.intent": _l("Open question when nothing is under way. 'What can I do for you?'", cache=True),
    "capability": _l("What Emma can help with, casual, then an open question. 'I can tell you about the "
                     "clinic, our services, doctors, prices and timings, and book, change or cancel "
                     "appointments. What would you like to know?'", cache=True),
    "capability.who": _l("'Who are you?' 'This is Emma at Pearl Dental.' + the capability sentence. Never "
                         "claims to be human and never raises what she is unprompted."),
    "offer.help": _l("Occasional soft steer after answering. 'If you'd like, I can book that for you too.' "
                     "No question mark needed.", cache=True),
    "anything_else": _l("'Anything else I can help with?'", cache=True),
    "close": _l("Warm goodbye. 'Take care, bye!'", cache=True),
    "close.booked": _l("Goodbye after a booking. 'Great, see you then. Take care!'", cache=True),
    "hold_on": _l("'Sure, take your time.'", critical=True, cache=True),
    "repeat.prefix": _l("Before re-speaking the last line. 'Sure.' / 'Of course.'", critical=True, cache=True),
    "repeat.hear": _l("'Can you hear me?' Before re-speaking the last line. 'Yes, I can hear you.'", critical=True,
                      cache=True),
    "go_on": _l("After a cut-off fragment. 'Sorry, go on.' / 'Go ahead.'", critical=True, cache=True),
    "silence.1": _l("First silence nudge. 'Are you still there?'", critical=True, cache=True),
    "silence.2": _l("'I can't hear you. If you're there, just say something.'", critical=True),
    "silence.3": _l("Goodbye after silence. 'I'll let you go for now. Call us any time. Bye!'",
                    critical=True),
    "chitchat": _l("Fallback for small talk. 'I'm good, thanks for asking!'"),
    "thanks": _l("Reply to thanks. 'You're welcome!'", cache=True),
    # knowledge
    "answer.fact": _l("A verified fact, spoken as-is: '{text}'", "text", critical=True),
    "unknown": _l("Honest not-sure, natural. 'Hmm, I'm not sure about that one.'", cache=True),
    "unknown.offer": _l("Offer after not-sure. 'I can ask someone from the clinic to call you about it, "
                        "if you like?'"),
    "clinical": _l("Only for genuinely clinical questions (diagnosis, 'do I need X', medicine). "
                   "'That's really one for the doctor to check when they see you.'"),
    # global handlers
    "help_first": _l("First person request: offer to help. 'I can probably sort that out for you myself. "
                     "What's it about?'"),
    "callback.offer": _l("Offer a callback. 'I can have someone from the team call you back about it. "
                         "Would that help?'"),
    "callback.done": _l("Task created. 'Done, someone from the clinic will call you back on {phone}.'",
                        "phone", critical=True),
    "transfer": _l("Phone calls: task created, then put through. 'Sure, I'm putting you through to the front "
                   "desk now. If they can't pick up, they'll call you back on {phone}.'", "phone", critical=True),
    "callback.already": _l("A callback is already arranged on this call. 'The team will call you back to sort "
                           "out a time that works.'", critical=True),
    "english_only": _l("Gently English only. 'Sorry, I can only help in English on this line. Our clinic team "
                       "speaks Kannada and Hindi, shall I have them call you?'", critical=True),
    "abuse.warn": _l("One calm warning. 'I do want to help, but let's keep it friendly, okay?'",
                     critical=True),
    "abuse.close": _l("Polite close. 'I'm going to end the call here. Take care.'", critical=True),
    "dont_keep.ack": _l("'Sure, we won't keep a record of this call.'", critical=True),
    "red_flag": _l("108 / ER advice, calm and clear. 'That sounds serious. Please go to the nearest emergency "
                   "room or call 108 right away. I've let the clinic know too.'", critical=True),
    "urgent.ack": _l("Notice: empathy + same-day promise. 'Oh no, I'm sorry. Let's get you seen today.'",
                     critical=True),
    "honesty": _l("Notice: exactly config.HONEST_LINE, then the conversation carries on.", critical=True),
    "confirm_change": _l("Ambiguous new value for a filled detail. 'Did you want to change the {field} to "
                         "{value}?'", "field", "value", critical=True),
    "correction.ack": _l("Notice: 'Okay, {value} instead.'", "value", critical=True),
    # identity
    "confirm.phone.caller_id": _l("Phone calls: the caller ID stands in for the read-back. 'Is the number "
                                  "you're calling from the best one to reach you on?'", critical=True),
    "confirm.phone.caller_id.manage": _l("'Is the appointment under the number you're calling from?'",
                                         critical=True),
    "ask.phone.not_caller_id": _l("They said no to the caller ID. 'No problem. What's the best number to "
                                  "reach you on?'", critical=True),
    "ask.name": _l("'Can I get your name?' / 'Sure, the 2nd. Can I get your name first?' (ack via notice)",
                   cache=True),
    "ask.name.rephrase": _l("'Sorry, what name should I put it under?'", cache=True),
    "ask.name.spell": _l("'Sorry, could you spell that for me?'", cache=True),
    "ask.name.manage": _l("'And what name is the appointment under?'", cache=True),
    "ask.name.callback": _l("'And who should they ask for?'"),
    "ack.name": _l("Notice, implicit confirmation. '{name}, got it.' / 'Thanks, {name}.'", "name",
                   critical=True),
    "ack.spelled": _l("Notice: letters read back. '{letters}, got it.'", "letters", critical=True),
    "ask.phone": _l("'And what's the best number to reach you on?'", cache=True),
    "ask.phone.rephrase": _l("'Sorry, could you give me the number once more?'"),
    "ask.phone.choices": _l("'Could you say it a few digits at a time? I'm listening.'"),
    "ask.phone.manage": _l("'Sure. What's the number the appointment is booked under?'"),
    "ask.phone.callback": _l("'What's the best number for them to call you on?'"),
    "ask.phone.why": _l("They don't know the number. 'No problem. I find bookings by the number they were "
                        "made with. Could it be this number, or one you've used with us before?'"),
    "phone.more": _l("Partial number heard. 'Mm-hmm.'", critical=True, cache=True),
    "phone.too_many": _l("'Sorry, I got a few too many digits there. Could you say it once more?'",
                         critical=True),
    "confirm.phone": _l("Grouped read-back. 'So that's {phone}, right?'", "phone", critical=True),
    "confirm.phone.retry": _l("After a rejected read-back with no new digits. 'Sorry about that. "
                              "What's the number again?'", critical=True, cache=True),
    "phone.failed": _l("Exit after 3 failed read-backs. 'I'm sorry, the line isn't great. Please call us back "
                       "when you can, and we'll sort it out.' Closes the call.", critical=True),
    # BOOK
    "ask.patient": _l("Booking for someone else. 'Sure. What's {relation_or_their} name?'", "relation_or_their"),
    "ask.patient.rephrase": _l("Rung 2. 'Sorry, what was {relation_or_their} name?'", "relation_or_their"),
    "ask.age": _l("Pediatric only. 'And how old is {patient}?'", "patient"),
    "ask.service": _l("'What would you like to come in for?'", cache=True),
    "ask.service.rephrase": _l("'Is it a check-up, or is something bothering you?'"),
    "ask.service.choices": _l("'We do check-ups, cleanings, fillings, root canals, extractions, braces and "
                              "children's dentistry. Which one is it?'"),
    "clarify.service": _l("Ambiguous service. 'Is that {options}?'", "options"),
    "service.unknown": _l("Notice: a treatment not bookable directly. 'For {phrase} we'd start with a "
                          "consultation, so the doctor can take a look.'", "phrase", critical=True),
    "ask.branch": _l("Only branches offering the service. 'We do {service} at {branches}. Which suits you?' "
                     "({service} is plural: 'root canals', 'braces')",
                     "service", "branches"),
    "ask.branch.rephrase": _l("'Which of those is closer for you, {branches}?'", "branches"),
    "ask.branch.choices": _l("'I can just book whichever has the earliest slot, if you like?'"),
    "branch.only": _l("Notice: only one branch offers it. 'We do {service} at our {branch} branch.'",
                      "service", "branch", critical=True),
    "branch.no_service": _l("Notice: 'Our {branch} branch doesn't do {service}, but {branches} do.'",
                            "branch", "service", "branches", critical=True),
    "doctor.unknown": _l("Notice: 'We don't have a {name} here, but {doctors} {are_is} at {branch}.'",
                         "name", "doctors", "are_is", "branch", critical=True),
    "doctor.other_branch": _l("Notice: '{doctor} is at our {branch} branch.'", "doctor", "branch", critical=True),
    "doctor.gender_none": _l("Notice: 'There isn't a {gender_word} doctor for that at {branch}, but {doctors} "
                             "can see you.'", "gender_word", "branch", "doctors", critical=True),
    "ask.when": _l("'When would suit you?'", cache=True),
    "ask.when.rephrase": _l("'What day works best for you?'", cache=True),
    "ask.when.choices": _l("'No worries, we'll find something. Are you thinking this week or next?'", cache=True),
    "ask.when.after_reject": _l("After offers were turned down. 'No problem. What would work better?'"),
    "ack.when": _l("Notice: 'Sure, {when}.'", "when", critical=True),
    "ack.request": _l("Notice: the opening request said back. 'Sure, a check-up for your daughter at "
                      "Jayanagar.'", "request", critical=True),
    "ask.time": _l("A day is known. 'Morning or evening?' / 'Any particular time for {day}?'", "day"),
    "ask.time.choices": _l("'Would morning, afternoon or evening be easier?'", cache=True),
    "resolve.ampm": _l("'{hour} in the morning or the evening?'", "hour", critical=True),
    "date.sunday": _l("Notice: 'We're closed on Sundays.'", critical=True, cache=True),
    "date.past": _l("Notice: 'That one's already gone by.'", critical=True),
    "date.horizon": _l("Notice: 'I can only book up to two months ahead.'", critical=True),
    "date.invalid_day": _l("Notice: '{month} doesn't have a {day}.'", "month", "day", critical=True),
    "time.outside_hours": _l("Notice: 'We're open 7 in the morning to 9 at night.'", critical=True),
    "time.lunch": _l("Notice: 'We break for lunch from 2 to 2:30.'", critical=True),
    "time.taken": _l("Notice: the exact time the caller asked for again isn't free. '4:30 isn't free, I'm afraid.'",
                     "time", critical=True),
    "exact.free": _l("Notice: the exact time asked for is free, so the summary follows at once (owner, 7 Oct). "
                     "'Good news, that time's free.'", critical=True),
    "offer.exact": _l("'{slot} is free, with {doctor}. Shall I take that?'", "slot", "doctor", critical=True),
    # Rung 2-3 of the offer ladder (the caller answered something else): the same
    # slots, said shorter and differently, never the offer word for word again.
    "offer.exact.rephrase": _l("'So {slot} with {doctor}, shall I take it?'", "slot", "doctor", critical=True),
    "offer.two.rephrase": _l("'So it's {a} or {b} with {doctor}. Which would you like?'", "a", "b", "doctor",
                             critical=True),
    "offer.one.rephrase": _l("'{a} with {doctor} is the nearest I have. Shall I take it?'", "a", "doctor",
                             critical=True),
    "offer.later_days.rephrase": _l("'{day} is full, so it's {a} or {b}. Which would you like?'", "day", "a", "b",
                                    critical=True),
    "offer.full_everywhere.rephrase": _l("'{day} is full everywhere, so the nearest are {a} or {b}.'", "day", "a",
                                         "b", critical=True),
    "offer.other_branch.rephrase": _l("'{other} has {times}, since {branch} is full. Shall I take it?'", "other",
                                      "times", "branch", critical=True),
    "offer.two": _l("'I can do {a} or {b}, with {doctor}. Which suits you?'", "a", "b", "doctor", critical=True),
    "offer.one": _l("'The closest I have is {a}, with {doctor}. Would that work?'", "a", "doctor", critical=True),
    "window.full": _l("Notice: the part of the day asked for is full; other times follow. "
                      "'Tomorrow evening is all booked, I'm afraid.'", "when", critical=True),
    "offer.later_days": _l("'{day} is full, but I have {a} or {b}. Would either work?'", "day", "a", "b",
                           critical=True),
    "offer.other_branch.two": _l("As offer.other_branch, with two times. 'Nagarbhavi's full that day, but "
                                 "Indiranagar has 12 or 12:30. Which would you like?'", "branch", "other", "times",
                                 critical=True),
    "offer.other_branch.two.rephrase": _l("'{other} has {times}, or I can look at another day at {branch}. "
                                          "What would you prefer?'", "other", "times", "branch",
                                          critical=True),
    "offer.same_time_elsewhere": _l("The caller keeps asking for a time their branch doesn't have; another "
                                    "branch has exactly it that day. 'Nagarbhavi has nothing at 5, but "
                                    "Indiranagar does, with Dr Menon. Would that work?'", "branch", "other", "time",
                                    "doctor", critical=True),
    "offer.which": _l("A plain yes to two offered times: ask which. 'Sure, which one: {a} or {b}?'", "a", "b",
                      critical=True),
    "offer.other_branch": _l("The day the caller insists on is full at their branch, another branch has it. "
                             "'Nagarbhavi's full on the 15th, but Indiranagar has 1:30. Would that work?'",
                             "branch", "other", "times", critical=True),
    "offer.full_everywhere": _l("The caller insisted on a full day and no branch has it. 'I'm sorry, the 15th "
                                "is full at every branch. The nearest I have is {a} or {b}.'", "day", "a", "b",
                                critical=True),
    "patient.clash": _l("Notice: the slot overlaps an appointment the patient already has (no date or time). "
                        "'That one clashes with another appointment Priya already has, so I can't book it.'",
                        "patient", critical=True),
    "slot.gone": _l("Notice: 'Sorry, that one's just been taken.'", critical=True, cache=True),
    "no_slots": _l("'I've nothing at {branch} until after {until}. Shall I look at another branch, or a later "
                   "date?'", "branch", "until", critical=True),
    "max_reached": _l("'There are already three appointments on this number. I can change or cancel one of "
                      "those for you, if you like?'", critical=True),
    "duplicate": _l("No details before verification (Z7). 'It looks like {patient} already has an "
                    "appointment with us. Did you want another one, or to change that one?'", "patient",
                    critical=True),
    "summary": _l("One natural summary, then the question. 'So that's {service} with {doctor} at {branch}, "
                  "{when}, for {patient}. Shall I book it?'",
                  "service", "doctor", "branch", "when", "patient", critical=True),
    "summary.again": _l("Barge-in during the summary. 'Let me just finish the details: {when} at {branch}. "
                        "Shall I book it?'", "when", "branch", critical=True),
    "what_to_change": _l("'Sure, what should I change?'", cache=True),
    "booked": _l("Committed only. 'Done, you're booked for {when} at {branch}. Anything else I can help "
                 "with?'", "when", "branch", critical=True),
    "booked.emergency": _l("Committed only. 'Done, you're booked for {when} at {branch}, and I've told the "
                           "team you're in pain.'", "when", "branch", critical=True),
    "dropped": _l("Cancel at the summary (Z5). 'Okay, I won't book that. Did you want to cancel an appointment "
                  "you already have?'", critical=True),
    "checking": _l("Played before a search or a commit (before_action). 'Let me just check that for you.' / "
                   "'One moment, let me have a look.'", critical=True, cache=True),
    # MANAGE
    "ask.appt_date": _l("'And what date is the appointment?'", cache=True),
    "ask.appt_date.rephrase": _l("'Do you remember roughly which day it's on?'"),
    "verify.failed": _l("Reveal nothing. 'Hmm, I can't find one matching those details. Could you check the "
                        "date for me?'", critical=True),
    "verify.failed.final": _l("Second failure: offer a callback. 'I'm still not finding it. I can have the "
                              "team call you to sort it out, would that help?'", critical=True),
    "pick.appointment": _l("'I can see {options}. Which one is it?'", "options", critical=True),
    "state.appointment": _l("'I have you down for {appt}. Anything else?' (never 'booked': nothing was booked)",
                            "appt", critical=True),
    "confirm.cancel": _l("'That's {appt}. There's no fee to cancel. Shall I cancel it?'", "appt", critical=True),
    "ask.cancel_reason": _l("Optional, once. 'Can I ask what's changed? Just so we know.'"),
    "cancelled": _l("Committed only. 'Done, that's cancelled.' + rebook offer.", critical=True, cache=True),
    "offer.rebook": _l("'Would you like to book another time instead?'"),
    "ask.new_when": _l("'Sure. When would you like to move it to?'"),
    "ask.new_when.rephrase": _l("Rung 2 of the new-time question. 'Which day would suit you better?'"),
    "ask.new_when.choices": _l("Rung 3. 'No worries. Later this week, or sometime next week?'"),
    "confirm.reschedule": _l("'So that's moving it from {old} to {new}. Shall I go ahead?'", "old", "new",
                             critical=True),
    "rescheduled": _l("Committed only. 'Done, you're now booked for {new}.'", "new", critical=True),
    "same_slot": _l("Notice: 'That's already your time.'", critical=True),
    "too_late": _l("'That one's too close to change over the phone now. I can ask the team to call you about "
                   "it, would that help?'", critical=True),
    "manage.already_cancelled": _l("Notice: the appointment changed under us (staff cancelled it). 'Looks like "
                                   "that one's already been cancelled.'", critical=True),
    "manage.not_found": _l("Notice: the verified appointment vanished before the change. 'Hmm, I can't see that "
                           "appointment any more.'", critical=True),
}

# Openers (R6): at most one per reply, never the same as the last one, only on
# replies without a model `say`, never before a summary or a read-back.
OPENERS = ("Okay,", "Sure,", "Right,", "Alright,")
# Light "umm" / "so": at most once every 4 turns, never in summaries or numbers.
SOFTENERS = ("So,", "Umm,")
# The first checking variant is phrases.CHECKING (the cached one the old engine plays).
CHECKING_FIRST = "Let me just check that for you."

# line id -> variants (2-4 per frequent line, 1-2 elsewhere), each using only
# the placeholders its LineSpec lists. Read them aloud: a good receptionist on
# a Bengaluru front desk should sound like this. The first variant of every
# cached line is the most neutral one; render() starts with a random unused
# one, so two calls don't open with the same sentence either.
VARIANTS: dict[str, tuple] = {
    # conversation
    "ask.intent": ("What can I do for you?", "How can I help you today?", "What can I help you with?",
                   "Tell me, what can I do for you?"),
    "capability": ("I can tell you about our clinic, the services we offer, our doctors, prices and timings, "
                   "and book, change or cancel appointments. What would you like to know?",
                   "I can help with anything about the clinic, like our services, doctors, prices and "
                   "timings, and I can book, change or cancel appointments. What would you like to do?"),
    "capability.who": ("This is Emma at Pearl Dental. I can tell you about the clinic, our services, doctors, "
                       "prices and timings, and book, change or cancel appointments.",
                       "I'm Emma from Pearl Dental. I can answer questions about the clinic, and book, change "
                       "or cancel appointments for you. What can I do for you?"),
    "offer.help": ("If you'd like, I can book that for you too.", "I can book you in as well, if you like.",
                   "Happy to set up an appointment too, whenever you're ready."),
    "anything_else": ("Anything else I can help with?", "Is there anything else I can do for you?",
                      "Anything else you need?"),
    "close": ("Take care, bye!", "Thanks for calling, take care!", "Alright, have a good day. Bye!"),
    "close.booked": ("Great, see you then. Take care!", "Lovely, we'll see you then. Bye!",
                     "Perfect, see you soon. Take care!"),
    "hold_on": ("Sure, take your time.", "No problem, I'll wait.", "Of course, no rush."),
    "repeat.prefix": ("Sure.", "Of course.", "No problem."),
    "repeat.hear": ("Yes, I can hear you.", "Yes, I'm here.", "I can hear you, yes."),
    "go_on": ("Sorry, go on.", "Go ahead.", "Sorry, you were saying?"),
    "silence.1": ("Are you still there?", "Hello, can you hear me?"),
    "silence.2": ("I can't hear you. If you're there, just say something.",
                  "I think the line's gone quiet. Are you there?"),
    "silence.3": ("I'll let you go for now. Call us any time. Bye!",
                  "I think we've lost the line, so I'll let you go. Do call back any time. Bye!"),
    "chitchat": ("I'm good, thanks for asking!", "Doing well, thank you!", "All good here, thanks!"),
    "thanks": ("You're welcome!", "No problem at all!", "My pleasure!"),
    # knowledge
    "answer.fact": ("{text}",),
    "unknown": ("Hmm, I'm not sure about that one.", "Honestly, I don't know that one.",
                "I'm not sure about that, sorry."),
    "unknown.offer": ("I can ask someone from the clinic to call you about it, if you like?",
                      "I can have the team find out and call you, if that helps?"),
    "clinical": ("That's really one for the doctor to check when they see you.",
                 "That's something the doctor would need to look at in person.",
                 "The doctor would need to see it to tell you properly."),
    # global handlers
    "help_first": ("I can probably sort that out for you myself. What's it about?",
                   "I should be able to help with that. What do you need?",
                   "Let me see if I can help first. What's it regarding?"),
    "callback.offer": ("I can have someone from the team call you about it. Would that help?",
                       "If you like, I'll ask the team to give you a call about it. Shall I?"),
    "transfer": ("Sure, I'm putting you through to the front desk now. If they can't pick up, "
                 "they'll call you back on {phone}.",
                 "Of course, let me put you through to the front desk. If nobody picks up, "
                 "they'll call you back on {phone}."),
    "confirm.phone.caller_id": ("Is the number you're calling from the best one to reach you on?",
                                "Shall I use the number you're calling from?"),
    "confirm.phone.caller_id.manage": ("Is the appointment under the number you're calling from?",
                                       "Is it booked under this number you're calling from?"),
    "ask.phone.not_caller_id": ("No problem. What's the best number to reach you on?",
                                "Sure. Which number should I use?"),
    "callback.done": ("Done, someone from the clinic will call you back on {phone}.",
                      "Okay, I've asked the team to call you back on {phone}."),
    "callback.already": ("The team will call you back to sort out a time that works for you.",
                         "Someone from the clinic will ring you back to find a time that suits."),
    "english_only": ("Sorry, I can only help in English on this line. Our clinic team speaks Kannada and Hindi, "
                     "shall I have them call you?",
                     "I'm sorry, I can only do English on this line. Shall I ask someone who speaks Kannada or "
                     "Hindi to call you?"),
    "abuse.warn": ("I do want to help, but let's keep it friendly, okay?",
                   "I'm happy to help, but please keep it polite."),
    "abuse.close": ("I'm going to end the call here. Take care.", "I'll end the call now. Take care."),
    "dont_keep.ack": ("Sure, we won't keep a record of this call.",
                      "Of course, I won't keep a record of this call."),
    "red_flag": ("That sounds serious. Please go to the nearest emergency room or call 108 right away. "
                 "I've let the clinic know too.",
                 "Please don't wait on this. Call 108 or go to the nearest emergency room now. "
                 "I've told the clinic as well."),
    "urgent.ack": ("Oh no, I'm sorry. Let's get you seen today.",
                   "Oh, that sounds painful. Let's get you in today."),
    "honesty": (config.HONEST_LINE,),
    "confirm_change": ("Did you want to change the {field} to {value}?",
                       "Should I change the {field} to {value}?"),
    # Not "Sure, {value} it is.": the time asked for may not be free, and the offer follows.
    # Not "let me look at {value}": the value is often "at 5" ("let me look at at 5").
    "correction.ack": ("Okay, {value} instead.", "Got it, {value} then.", "Sure, {value}. Let me look."),
    # identity
    "ask.name": ("Can I get your name?", "May I know your name, please?", "Can I get your name first?",
                 "And your name, please?"),
    "ask.name.rephrase": ("Sorry, what name should I put it under?",
                          "Sorry, I didn't quite get the name. What should I put it under?"),
    "ask.name.spell": ("Sorry, could you spell that for me?", "Could you spell your name for me, please?"),
    "ask.name.manage": ("And what name is the appointment under?", "Which name is it booked under?"),
    "ask.name.callback": ("And who should they ask for?", "And what name should they ask for?"),
    "ack.name": ("{name}, got it.", "Thanks, {name}.", "Nice to meet you, {name}."),
    "ack.spelled": ("{letters}, got it.", "Okay, {letters}. Thank you."),
    "ask.phone": ("And what's the best number to reach you on?", "And your phone number, please?",
                  "What's a good number for you?"),
    "ask.phone.rephrase": ("Sorry, could you give me the number once more?",
                           "Sorry, I missed that. What's the number?"),
    "ask.phone.choices": ("Could you say it a few digits at a time? I'm listening.",
                          "Let's do it slowly, a few digits at a time. Go ahead."),
    "ask.phone.manage": ("Sure. What's the number the appointment is booked under?",
                         "Which phone number is the appointment under?"),
    "ask.phone.callback": ("What's the best number for them to call you on?",
                           "Which number should they call you on?"),
    "ask.phone.why": ("No problem. I find bookings by the number they were made with. Is there another "
                      "number you might have used with us?",
                      "That's okay. Bookings are kept under a phone number, so I'll need that one. "
                      "Might it be a family member's number, or an older one of yours?"),
    "phone.more": ("Mm-hmm.", "Yes, go on.", "Okay, go on."),
    "phone.too_many": ("Sorry, I got a few too many digits there. Could you say it once more?",
                       "Hmm, that's more digits than a phone number. Could you say it again for me?"),
    "confirm.phone": ("So that's {phone}, right?", "Let me read that back: {phone}. Is that right?",
                      "That's {phone}, correct?"),
    "confirm.phone.retry": ("Sorry about that. What's the number again?",
                            "My mistake. Could you give me the number again?"),
    "phone.failed": ("I'm sorry, the line isn't great. Please call us back when you can, and we'll sort it out.",
                     "Sorry, I'm struggling to catch the number on this line. Do call us back when you can, "
                     "and we'll get it sorted."),
    # BOOK
    "ask.patient": ("Sure. What's {relation_or_their} name?", "Of course. And {relation_or_their} name?"),
    "ask.patient.rephrase": ("Sorry, what was {relation_or_their} name?",
                             "Sorry, I didn't catch {relation_or_their} name. Could you say it again?"),
    "ask.age": ("And how old is {patient}?", "How old is {patient}, if you don't mind me asking?"),
    "ask.service": ("What would you like to come in for?", "What's the visit for?",
                    "And what's it for, a check-up or something else?"),
    "ask.service.rephrase": ("Is it a check-up, or is something bothering you?",
                             "Is it a routine check-up, or is there a problem with a tooth?"),
    "ask.service.choices": ("We do check-ups, cleanings, fillings, root canals, extractions, braces and "
                            "children's dentistry. Which one is it?",
                            "Is it a cleaning, a filling, a check-up, or something else?"),
    "clarify.service": ("Is that {options}?", "Just to check, is it {options}?"),
    "service.unknown": ("For {phrase} we'd start with a consultation, so the doctor can take a look.",
                        "For {phrase}, we'd begin with a quick consultation so the doctor can see what's needed."),
    "ask.branch": ("We do {service} at {branches}. Which suits you?",
                   "For {service}, it's {branches}. Which one's easier for you?",
                   "That's available at {branches}. Which branch would you like?"),
    "ask.branch.rephrase": ("Which of those is closer for you, {branches}?",
                            "Which is more convenient for you, {branches}?"),
    "ask.branch.choices": ("I can just book whichever has the earliest slot, if you like?",
                           "Shall I just go with whichever branch has the earliest slot?"),
    "branch.only": ("We do {service} at our {branch} branch.", "{service} are done at our {branch} branch."),
    "branch.no_service": ("Our {branch} branch doesn't do {service}, but {branches} do.",
                          "We don't do {service} at {branch}, but {branches} do."),
    "doctor.unknown": ("We don't have a {name} here, but {doctors} {are_is} at {branch}.",
                       "There's no {name} with us, but {doctors} {are_is} at our {branch} branch."),
    "doctor.other_branch": ("{doctor} is at our {branch} branch.", "{doctor} works at our {branch} branch."),
    "doctor.gender_none": ("There isn't a {gender_word} doctor for that at {branch}, but {doctors} can see you.",
                           "We don't have a {gender_word} doctor for that at {branch}, but {doctors} can."),
    "ask.when": ("When would suit you?", "What day works for you?", "When would you like to come in?",
                 "When's good for you?"),
    "ask.when.rephrase": ("Is there a day that's easiest for you?", "Which day of the week usually works better?"),
    "ask.when.choices": ("No worries, we'll find something. Are you thinking this week or next?",
                         "That's okay, we'll find a time. Would this week or next week be better?"),
    "ask.when.after_reject": ("No problem. What would work better?",
                              "Okay, no worries. What day or time would suit you better?"),
    "ack.when": ("Sure, {when}.", "Okay, {when}.", "{when}, got it."),
    "ack.request": ("Sure, {request}.", "Of course, {request}.", "Okay, {request}."),
    "ask.time": ("Morning or evening?", "Any particular time for {day}?", "What time would suit you for {day}?",
                 "Would morning or evening suit you better?"),
    "ask.time.choices": ("Would morning, afternoon or evening be easier?",
                         "Is morning, afternoon or evening better for you?"),
    "resolve.ampm": ("{hour} in the morning or the evening?", "Is that {hour} in the morning, or in the evening?"),
    "date.sunday": ("We're closed on Sundays.", "We're closed on Sunday, sorry."),
    "date.past": ("That one's already gone by.", "That date's already passed."),
    "date.horizon": ("I can only book up to two months ahead.", "We only take bookings up to two months out."),
    "date.invalid_day": ("{month} doesn't have a {day}.", "There's no {day} in {month}."),
    "time.outside_hours": ("We're open 7 in the morning to 9 at night.",
                           "Our timings are 7 in the morning to 9 at night."),
    "time.lunch": ("We break for lunch from 2 to 2:30.", "We're closed for lunch between 2 and 2:30."),
    "time.taken": ("{time} isn't free, I'm afraid.", "Sorry, {time}'s already taken."),
    "exact.free": ("Good news, that time's free.", "Yes, that one's free.", "Lovely, that's free."),
    "offer.exact.rephrase": ("So {slot} with {doctor}, shall I take it?",
                             "Just to check, would {slot} with {doctor} suit you?"),
    "offer.two.rephrase": ("So it's {a} or {b} with {doctor}. Which would you like?",
                           "Would {a} or {b} be better for you? That's with {doctor}."),
    "offer.one.rephrase": ("{a} with {doctor} is the nearest I have. Shall I take it?",
                           "Would {a} with {doctor} do, or shall I look at another day?"),
    "offer.later_days.rephrase": ("{day} is full, so it's {a} or {b}. Which would you like?",
                                  "Since {day} has nothing left, would {a} or {b} work instead?"),
    "offer.full_everywhere.rephrase": ("{day} is full everywhere, so the nearest are {a} or {b}. Which would you like?",
                                       "With {day} full, would {a} or {b} do, or another day altogether?"),
    "offer.other_branch.rephrase": ("{other} has {times}, since {branch} is full. Shall I take it?",
                                    "Would {times} at {other} work, as {branch} has nothing that day?"),
    "offer.exact": ("{slot} is free, with {doctor}. Shall I take that?",
                    "I have {slot} with {doctor}. Shall I take that?",
                    "{slot} with {doctor} is available. Would that work?"),
    "offer.two": ("I can do {a} or {b}, with {doctor}. Which suits you?",
                  "I have {a} or {b} with {doctor}. Which would you prefer?",
                  # {doctor} may be "Dr Reddy for the first and Dr Ali for the second": keep it last.
                  "{a} or {b} are free, with {doctor}. Which one works?"),
    "offer.one": ("The closest I have is {a}, with {doctor}. Would that work?",
                  "The nearest I can do is {a} with {doctor}. Is that okay?"),
    "window.full": ("{when} is all booked, I'm afraid.", "Nothing's free {when}, sorry."),
    "offer.later_days": ("{day} is full, but I have {a} or {b}. Would either work?",
                         "{day}'s fully booked, sorry. I can do {a} or {b}. Would either suit you?"),
    "offer.other_branch.two": ("{branch} is full that day, but {other} has {times}. Which would you like?",
                               "There's nothing left at {branch} that day, but {other} has {times}. "
                               "Which one suits you?"),
    # The second try gives a way out, not the same question again (sim 6 Oct: "which one suits you?" x3).
    "offer.other_branch.two.rephrase": ("{other} has {times}, or I can look at another day at {branch}. "
                                        "What would you prefer?",
                                        "I could do {times} at {other}, or find you another day at {branch}. "
                                        "Which is better?"),
    "offer.which": ("Sure, which one: {a} or {b}?", "Lovely. {a} or {b}?"),
    "offer.same_time_elsewhere": ("{branch} has nothing at {time}, but {other} does, with {doctor}. "
                                  "Would that work?",
                                  "We can't do {time} at {branch}, but {other} has {time} with {doctor}. "
                                  "Shall I take that?"),
    "offer.other_branch": ("{branch} is full that day, but {other} has {times}. Would that work?",
                           "There's nothing left at {branch} that day, but I've got {times} at {other}. Does that suit you?"),
    "offer.full_everywhere": ("I'm sorry, {day} is full at all our branches. The nearest I have is {a} or {b}. "
                              "Could one of those work?",
                              "{day}'s full everywhere, I'm afraid. I could do {a} or {b} instead. Any good?"),
    "patient.clash": ("That one clashes with another appointment {patient} already has, so I can't book it.",
                      "Ah, {patient} already has an appointment around then, so that one won't work."),
    "slot.gone": ("Sorry, that one's just been taken.", "Oh, that slot's just gone, sorry."),
    "no_slots": ("I've nothing at {branch} until after {until}. Shall I look at another branch, or a later date?",
                 "{branch} is full until after {until}. Should I try another branch or a later date?"),
    "max_reached": ("There are already three appointments on this number. I can change or cancel one of those "
                    "for you, if you like?",
                    "This number already has three upcoming appointments. Would you like to change or cancel "
                    "one of them?"),
    "duplicate": ("It looks like {patient} already has an appointment with us. Did you want another one, or to "
                  "change that one?",
                  "{patient} already has an appointment with us. Is this for another one, or did you want to "
                  "change it?"),
    "summary": ("So that's {service} with {doctor} at {branch}, {when}, for {patient}. Shall I book it?",
                "That's {service} for {patient} with {doctor} at our {branch} branch, {when}. Shall I go ahead "
                "and book it?",
                "Just to check, that's {service} with {doctor}, {when} at {branch}, for {patient}. Shall I book "
                "that?"),
    "summary.again": ("Let me just finish the details: {when} at {branch}. Shall I book it?",
                      "Just to finish, that's {when} at {branch}. Shall I go ahead?"),
    "what_to_change": ("Sure, what should I change?", "No problem, what would you like to change?"),
    "booked": ("Done, you're booked for {when} at {branch}. Anything else I can help with?",
               "All done, you're booked in for {when} at our {branch} branch. Anything else?",
               "That's booked for {when} at {branch}. Is there anything else I can do for you?"),
    "booked.emergency": ("Done, you're booked for {when} at {branch}, and I've told the team you're in pain.",
                         "All done, you're booked for {when} at {branch}. I've let the team know you're in pain."),
    "dropped": ("Okay, I won't book that. Did you want to cancel an appointment you already have?",
                "Sure, I won't go ahead with that one. Was it an appointment you already have that you wanted "
                "to cancel?"),
    "checking": (CHECKING_FIRST, "One moment, let me have a look.", "Let me just pull that up.",
                 "Give me a second, I'll check."),
    # MANAGE
    "ask.appt_date": ("And what date is the appointment?", "And which day is the appointment on?"),
    "ask.appt_date.rephrase": ("Do you remember roughly which day it's on?", "Roughly when is it, do you remember?"),
    "verify.failed": ("Hmm, I can't find one matching those details. Could you check the date for me?",
                      "I'm not finding it with those details. Could you double-check the date?"),
    "verify.failed.final": ("I'm still not finding it. I can have the team call you to sort it out, would that "
                            "help?",
                            "Still nothing, sorry. Shall I ask the team to call you and sort it out?"),
    "pick.appointment": ("I can see {options}. Which one is it?", "There's {options}. Which one did you mean?"),
    "state.appointment": ("I have you down for {appt}. Anything else?",
                          "Your appointment is {appt}. Anything else I can help with?"),
    "confirm.cancel": ("That's {appt}. There's no fee to cancel. Shall I cancel it?",
                       "I have {appt}. There's no charge to cancel. Should I go ahead and cancel it?"),
    "ask.cancel_reason": ("Can I ask what's changed? Just so we know.", "If you don't mind me asking, what's changed?"),
    "cancelled": ("Done, that's cancelled.", "All done, it's cancelled."),
    "offer.rebook": ("Would you like to book another time instead?", "Shall I find you another time?"),
    "ask.new_when": ("Sure. When would you like to move it to?", "No problem. When would suit you better?"),
    "ask.new_when.rephrase": ("Which day would suit you better?", "Is there a day that's easier for you?"),
    "ask.new_when.choices": ("No worries. Later this week, or sometime next week?",
                             "That's okay, we'll find something. Would this week or next week be better?"),
    "confirm.reschedule": ("So that's moving it from {old} to {new}. Shall I go ahead?",
                           "That's {old} moving to {new}. Shall I move it?"),
    "rescheduled": ("Done, you're now booked for {new}.", "All done, it's moved to {new}."),
    "same_slot": ("That's already your time.", "That's the time you already have."),
    "too_late": ("That one's too close to change over the phone now. I can ask the team to call you about it, "
                 "would that help?",
                 "It's a bit too close to change now, I'm afraid. Shall I have the team call you about it?"),
    "manage.already_cancelled": ("Looks like that one's already been cancelled.",
                                 "Ah, that appointment's already been cancelled, actually."),
    "manage.not_found": ("Hmm, I can't see that appointment any more.",
                         "I'm not finding that appointment any more, sorry."),
}

# When a line has only one variant (or one that fits the params) and it would
# repeat Emma's previous sentence word for word, a person would say "like I
# said". Only used then; never on lines whose first word is a name or a number.
_REPEAT_LEADS = ("Like I said, ", "As I said, ")
# First words that may be lower-cased after an opener or a lead ("Sure, we're...").
_LOWERABLE = frozenset("""
    a all an and any anything are can could did do doing every for give go happy have hello hmm honestly how
    if is it it's just let let's my no of oh okay our please should so sorry still that that's the
    there there's this those treatments we we'd we're what what's when when's which would yeah yes you you're your
    may shall where who whose tell
    """.split())
_PLACEHOLDER = re.compile(r"{(\w+)}")
_RECENT_KEEP = 10


def placeholders(text: str) -> set:
    """The {name} placeholders in a variant (tests check them against LineSpec.params)."""
    return set(_PLACEHOLDER.findall(text))


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", (text or "").lower()).strip()


def _lower_first(text: str) -> str:
    """'We're open' -> "we're open" after an opener; names, days and "I" keep their capital."""
    first = text.split(" ", 1)[0].strip(",.!?").lower()
    return text[:1].lower() + text[1:] if first in _LOWERABLE else text


def _upper_first(text: str) -> str:
    """A placeholder at the start ("{service} is at...") may arrive lower-case: "braces is at" -> "Braces is at"."""
    return text[:1].upper() + text[1:] if text[:1].isalpha() else text


def _fill(variant: str, params: dict) -> Optional[str]:
    """The variant with params filled in, or None when a placeholder it needs wasn't given."""
    needed = placeholders(variant)
    if any(params.get(k) in (None, "") for k in needed):
        return None
    text = variant.format_map({k: str(params[k]) for k in needed})
    # "What time on {day}" with day "tomorrow" -> "What time tomorrow", never "on tomorrow".
    text = re.sub(r"\bon (today|tomorrow)\b", r"\1", text)
    return _upper_first(text)


def render(line_id: str, memory, params: Optional[dict] = None, rng=None) -> str:
    """
    One variant of `line_id` with params filled in. Picks a variant this call
    has not used yet (memory: context.PromptMemory), otherwise the least
    recently used one, and never text equal to memory.recent[-1]. Records the
    choice in memory. Unknown ids raise KeyError (a test checks every id the
    dialogue modules use exists here).

    Variants that need a param the caller didn't pass are skipped ("Any
    particular time on {day}?" without a day falls back to "Morning or
    evening?"), so a missing detail never leaves a hole in the sentence.
    """
    spec = LINES[line_id]                                    # KeyError for an unknown id, on purpose
    variants = VARIANTS[line_id]
    rng = rng or random
    params = params or {}
    filled = [(i, _fill(v, params)) for i, v in enumerate(variants)]
    filled = [(i, text) for i, text in filled if text is not None]
    if not filled:
        raise KeyError(f"{line_id}: no variant fits params {sorted(params)} (spec {spec.params})")
    used = memory.used.setdefault(line_id, [])
    last = _norm(memory.recent[-1]) if memory.recent else None

    def last_use(index: int) -> int:
        return max((pos for pos, idx in enumerate(used) if idx == index), default=-1)

    fresh = [(i, t) for i, t in filled if i not in used and _norm(t) != last]
    if fresh:
        index, text = rng.choice(fresh)
    else:
        ranked = sorted(filled, key=lambda it: last_use(it[0]))
        others = [(i, t) for i, t in ranked if _norm(t) != last]
        if others:
            index, text = others[0]
        else:                                               # the only wording left is the one just said
            index, text = ranked[0]
            lead = next((l for l in _REPEAT_LEADS if not _norm(memory.recent[-1]).startswith(_norm(l))),
                        _REPEAT_LEADS[0])
            text = lead + _lower_first(text)
    used.append(index)
    memory.recent.append(text)
    del memory.recent[:-_RECENT_KEEP]
    return text


def _starts_with_opener(text: str) -> bool:
    return bool(re.match(r"^\s*(okay|ok|sure|right|alright|all right|so|umm|no worries|no problem|sorry|done|"
                         r"all done|great|lovely|perfect|of course|mm-hmm|thanks|oh|yes|hmm|got it)\b",
                         text or "", re.I))


def with_opener(text: str, memory, rng=None) -> str:
    """
    Prefix an opener if the rules allow (see OPENERS); records it in memory.
    Never the same opener as last time, and never onto a reply that already
    starts with one ("Sure, sure, ..." is the repeated-words feel the owner
    flagged), a summary or a read-back (they start "So that's" / "That's").
    """
    if not text or _starts_with_opener(text) or re.match(r"^\s*(so )?that's\b", text, re.I):
        return text
    choices = [o for o in OPENERS if o != memory.last_opener]
    opener = (rng or random).choice(choices)
    memory.last_opener = opener
    return f"{opener} {_lower_first(text)}"


def with_softener(text: str, memory, rng=None) -> str:
    """
    A light "So," or "Umm," at most once every 4 turns (the engine bumps
    memory.turns_since_umm each turn). Never onto numbers, read-backs or a
    reply that already has an opener.
    """
    if (not text or memory.turns_since_umm < 4 or _starts_with_opener(text) or re.search(r"\d", text)
            or re.match(r"^\s*(so )?that's\b", text, re.I)):
        return text
    memory.turns_since_umm = 0
    return f"{(rng or random).choice(SOFTENERS)} {_lower_first(text)}"


def checking_phrase(memory, rng=None) -> str:
    """
    A "let me just check" variant not yet used this call (the before_action
    phrase), else the least recently used; never the one said last time
    (owner decision D3).
    """
    variants = VARIANTS["checking"]
    used = memory.checking_used
    fresh = [v for v in variants if v not in used]
    if fresh:
        phrase = (rng or random).choice(fresh)
    else:
        last = used[-1] if used else None
        phrase = min((v for v in variants if v != last),
                     key=lambda v: max(pos for pos, u in enumerate(used) if u == v))
    used.append(phrase)
    return phrase


# ---------------------------------------------------------------- speakable values

def speak_list(items, conj: str = "or") -> str:
    """ ["Indiranagar", "Whitefield"] -> "Indiranagar or Whitefield"; three -> "A, B or C"."""
    items = [str(i) for i in items if i]
    if len(items) <= 1:
        return items[0] if items else ""
    return f"{', '.join(items[:-1])} {conj} {items[-1]}"


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _as_date(value) -> date:
    return value.date() if isinstance(value, datetime) else value


def speak_day(day, today=None) -> str:
    """ "today", "tomorrow", "Monday the 5th" (weekday + ordinal within 2 weeks; "5 October" beyond)."""
    day = _as_date(day)
    today = _as_date(today) if today is not None else clock.today()
    delta = (day - today).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    if 1 < delta < 14:
        return f"{day.strftime('%A')} the {ordinal(day.day)}"
    return f"{day.day} {day.strftime('%B')}"


def _clinic_hour(hour: int) -> bool:
    return config.CLINIC_START_HOUR <= hour < config.CLINIC_END_HOUR


def speak_time(t) -> str:
    """
    17:00 -> "5", 17:30 -> "5:30", 07:00 -> "7 in the morning" when AM/PM
    could confuse: only when the other reading (7 PM) is also a time the
    clinic is open, so "1:30" and "6" stay short.
    """
    t = t.time() if isinstance(t, datetime) else t
    h12 = t.hour % 12 or 12
    text = f"{h12}" if t.minute == 0 else f"{h12}:{t.minute:02d}"
    other = t.hour + 12 if t.hour < 12 else t.hour - 12
    if _clinic_hour(other):
        text += " in the morning" if t.hour < 12 else " in the evening"
    return text


def speak_span(start, end) -> str:
    """09:00-17:00 -> "9 to 5"; 07:00-15:00 -> "7 in the morning to 3"; 09:00-21:00 -> "9 in the morning to 9 at night"."""
    a, b = speak_time(start), speak_time(end)
    if b.endswith(" in the evening") and (end.hour if isinstance(end, time) else end.time().hour) >= 21:
        b = b.replace(" in the evening", " at night")
    return f"{a} to {b}"


def speak_slot(start, today=None, with_day: bool = True) -> str:
    """ "Monday the 5th at 5" (a datetime from scheduling / OfferedSlot.start)."""
    if not with_day:
        return speak_time(start)
    return f"{speak_day(start, today)} at {speak_time(start)}"


def _day_part(t: time) -> str:
    return "morning" if t.hour < 12 else "afternoon" if t.hour < 16 else "evening"


def _speak_time_c(time_c) -> str:
    """The time half of a preference: "evening", "after 5", "at 6", "around 6", "any time"."""
    if time_c is None:
        return ""
    label = (time_c.label or "").lower().strip()
    if time_c.kind == "any":
        return "any time"
    if time_c.kind == "exact" and time_c.start is not None:
        if label == "noon":
            return "at noon"
        word = "around" if re.search(r"\b(around|about|approx|near|like|ish)\b", label) else "at"
        return f"{word} {speak_time(time_c.start)}"
    if time_c.kind == "ambiguous" and time_c.candidates:
        c = time_c.candidates[0]
        return f"at {c.hour % 12 or 12}" + ("" if c.minute == 0 else f":{c.minute:02d}")
    if time_c.kind == "window" and time_c.start is not None:
        if label and not re.search(r"\d", label):
            return label                                  # "evening", "after work", "early morning"
        if re.match(r"(after|not before)\b", label):
            return f"after {speak_time(time_c.start)}"
        if re.match(r"(before|by|till|until|not after)\b", label) and time_c.end is not None:
            return f"before {speak_time(time_c.end)}"
        if time_c.end is not None:
            return f"between {speak_time(time_c.start)} and {speak_time(time_c.end)}"
        return f"in the {_day_part(time_c.start)}"
    return ""


def speak_when(date_c, time_c, today=None) -> str:
    """The caller's own preference said back: "the 2nd", "next Monday evening", "tomorrow around 6"."""
    today = _as_date(today) if today is not None else clock.today()
    day = ""
    if date_c is not None:
        if date_c.kind == "earliest":
            day = "the first free day"
        elif date_c.kind == "set" and date_c.only:
            day = speak_list([speak_day(d, today) for d in date_c.only], "or")
        elif date_c.start == date_c.end:
            day = speak_day(date_c.start, today)
        else:
            monday = today - timedelta(days=today.weekday())
            if date_c.start <= max(today, monday) and date_c.end <= monday + timedelta(days=6):
                day = "this week"
            elif date_c.start >= monday + timedelta(days=7) and date_c.end <= monday + timedelta(days=13):
                day = "next week"
            else:
                day = f"between {speak_day(date_c.start, today)} and {speak_day(date_c.end, today)}"
    part = _speak_time_c(time_c)
    if not day:
        if part in ("morning", "afternoon", "evening", "early morning", "late morning", "early afternoon",
                    "late afternoon", "early evening", "late evening"):
            return f"in the {part}"
        return part
    if not part:
        return day
    if part == "tonight":
        return "tonight" if day == "today" else f"{day} night"
    if day == "today" and part in ("morning", "afternoon", "evening"):
        return f"this {part}"
    if day in ("today", "tomorrow") and re.fullmatch(r"(early |late )?(morning|afternoon|evening)", part):
        return f"{day} {part}"
    if re.fullmatch(r"(early |late )?(morning|afternoon|evening)", part):
        return f"{day}, in the {part}"
    return f"{day}, {part}" if part == "any time" else f"{day} {part}"


def speak_phone(e164: str) -> str:
    """Grouped read-back (phones.spoken): "9 8 7 6 5, 4 3 2 1 0"."""
    canonical = e164 if (e164 or "").startswith("+") else (phones.to_e164(e164) or e164)
    return phones.spoken(canonical)


# How Emma says each catalog service in a sentence ("a root canal"), and the
# plural used in lists ("root canals").
_SERVICE_SPOKEN = {
    "general check-up": ("a check-up", "check-ups"),
    "consultation": ("a consultation", "consultations"),
    "teeth cleaning": ("a cleaning", "cleanings"),
    "tooth filling": ("a filling", "fillings"),
    "root canal treatment": ("a root canal", "root canals"),
    "tooth extraction": ("an extraction", "extractions"),
    "braces": ("braces", "braces"),
    "invisalign": ("Invisalign", "Invisalign"),
    "pediatric dentistry": ("a children's dental visit", "children's dentistry"),
}


def speak_service(name: str) -> str:
    """ "Root Canal Treatment" -> "a root canal", "Teeth Cleaning" -> "a cleaning"."""
    key = (name or "").strip().lower()
    if key in _SERVICE_SPOKEN:
        return _SERVICE_SPOKEN[key][0]
    if not key:
        return ""
    return ("an " if key[0] in "aeiou" else "a ") + key


def service_plural(name: str) -> str:
    """ "Root Canal Treatment" -> "root canals", for lists ("We do cleanings, fillings and braces")."""
    key = (name or "").strip().lower()
    return _SERVICE_SPOKEN.get(key, (None, key))[1]


def static_lines(cached_only: bool = True) -> list:
    """
    Every variant with no placeholders (cache=True lines only by default), for
    pre-rendering. Split into sentences, because the call session looks up
    the audio cache one sentence at a time (speech.split_sentences).
    """
    import speech                                           # local: keeps prompts importable on its own

    seen, out = set(), []
    for line_id, spec in LINES.items():
        if cached_only and not spec.cache:
            continue
        for variant in VARIANTS.get(line_id, ()):
            if placeholders(variant):
                continue
            for sentence in speech.split_sentences(variant):
                if sentence not in seen:
                    seen.add(sentence)
                    out.append(sentence)
    return out
