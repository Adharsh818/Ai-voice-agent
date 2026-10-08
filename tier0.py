"""
Tier-0: resolve a caller turn without an LLM round trip.

Most turns in a booking call are short answers to the question Emma just asked:
"yes", a phone number, "root canal", "next Monday", "5 pm". Those are resolved
here in well under a millisecond instead of ~1 s of Gemini latency.

The rules are deliberately conservative. Tier-0 only ever fills the slot the
current step is asking for (plus the yes/no), and returns None — "send this turn
to the LLM" — for anything that looks like a question, a long answer, or a mixed
answer such as "yes, but change the date". Returning None is always safe; a
wrong non-None answer is not, so when in doubt the rule says None.

The returned dict has exactly the shape of ai_engine._basic_entity_fallback, so
the state machine cannot tell which tier produced it.

understand() (bottom of the file) is Tier-0 v2 for the R2 engine: the same
rules, but it returns dialogue.context.Understanding from a Tier0View.
"""

import re
from functools import lru_cache

import ai_engine
import backend_actions
import clock
import dateparse
from dialogue import match
from dialogue.context import Act, Emergency, Expect, Goal, Intent, Tier0View, Understanding

MAX_WORDS = 8
MAX_CONFIRM_WORDS = 5

_QUESTION_RE = re.compile(
    r"\b(what|where|which|who|why|how|when|do you|does|can you|could you|is there|"
    r"are you|price|prices|cost|costs|charge|fee|fees|insurance|address|located|"
    r"location|parking|open|hours)\b"
)
_TIME_WORD_RE = re.compile(
    r"\b(\d{1,2}(:\d{2})?\s*(am|pm|a\.m\.|p\.m\.)|\d{1,2}\s*o'?clock|morning|afternoon|"
    r"evening|noon|night|am|pm)\b"
)
_DATE_WORD_RE = re.compile(
    r"\b(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"week|january|february|march|april|may|june|july|august|september|october|"
    r"november|december|\d{1,2}(st|nd|rd|th))\b"
)
_BOOKING_PHRASE_RE = re.compile(
    r"^(yes,? )?(hi,? |hello,? )?(i('d| would) like to |i want to |i wanna |i need to |"
    r"can i |could i |i'm calling to |i am calling to )?(book|schedule|make|fix|get)"
    r"( an| a| my)? (appointment|booking|visit)( please)?$"
)
_CLOSING_RE = re.compile(r"\b(nothing|that's all|thats all|that is all|no thanks|bye|goodbye)\b")
_NUMBER_IS_RE = re.compile(r"\b(?:number|mobile|phone)(?:\s+number)?(?:\s+is|'s)?\s+([\d(][\d\s().-]{8,}\d)",
                           re.IGNORECASE)
# "It's on Monday, and I'd like to move it to Friday" (the harness's fake model reads it the same way).
_MOVE_TO = re.compile(r"^(?P<old>.*?)[,.]?\s*(?:and\s+)?(?:i'?d like to|i want to|i wanna|can you|could you|please)?\s*"
                      r"(?:move|shift|change|prepone|postpone|reschedule|bring)\s+it\s+(?:forward\s+)?to\s+(?P<new>.+)$",
                      re.IGNORECASE)
_NAME_INTRO_RE = re.compile(
    r"^(?:my name is|my name's|i am|i'm|this is|it's|it is|call me)\s+([a-z][a-z .'-]*)$"
)
_FILLER_RE = re.compile(r"\b(uh+|um+|hmm+|er|please|maybe|on|at|around|about|for|the|let's say|lets say)\b")

# Spoken aliases -> canonical service. A single unambiguous hit is required.
SERVICE_ALIASES = {
    "root canal": "Root Canal Treatment",
    "cleaning": "Teeth Cleaning",
    "scaling": "Teeth Cleaning",
    "check up": "General Check-up",
    "checkup": "General Check-up",
    "check-up": "General Check-up",
    "consultation": "Consultation",
    "consult": "Consultation",
    "filling": "Tooth Filling",
    "cavity": "Tooth Filling",
    "extraction": "Tooth Extraction",
    "pull out": "Tooth Extraction",
    "pulled out": "Tooth Extraction",
    "braces": "Braces",
    "invisalign": "Invisalign",
    "pediatric": "Pediatric Dentistry",
    "paediatric": "Pediatric Dentistry",
}

_DIGIT_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3",
    "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
_MULTIPLIERS = {"double": 2, "triple": 3}


def normalize_spoken_digits(text: str) -> str:
    """
    Turn a spoken number into its digits: "nine eight double seven 6 five" ->
    "987765". "double"/"triple" repeat the next digit, as Indian callers say it.
    Non-number words are dropped; an empty string means no digits were spoken.
    """
    tokens = re.findall(r"[a-z]+|\d", (text or "").lower().replace("-", " "))
    digits, repeat = [], 1
    for tok in tokens:
        if tok in _MULTIPLIERS:
            repeat = _MULTIPLIERS[tok]
            continue
        digit = tok if tok.isdigit() else _DIGIT_WORDS.get(tok)
        if digit is None:
            repeat = 1
            continue
        digits.append(digit * repeat)
        repeat = 1
    return "".join(digits)


def match_service_alias(text: str):
    """Canonical service if exactly one service is named, else None."""
    lower = (text or "").lower()
    hits = {canon for alias, canon in SERVICE_ALIASES.items()
            if re.search(rf"\b{re.escape(alias)}\b", lower)}
    if len(hits) == 1:
        return hits.pop()
    return ai_engine._match_service(text) if not hits else None


def looks_like_question(user_text: str) -> bool:
    """Cheap question detector, used when the LLM is unavailable."""
    lower = (user_text or "").lower().replace("’", "'")
    return "?" in lower or bool(_QUESTION_RE.search(lower))


def _empty() -> dict:
    return {
        "patient_name": None, "phone_number": None, "dental_service": None,
        "appointment_date": None, "appointment_time": None, "confirmation": None,
        "user_query": None,
    }


def _strip_fillers(text: str) -> str:
    return re.sub(r"\s+", " ", _FILLER_RE.sub(" ", text)).strip(" ,.")


def _awaiting_yes_no(s) -> bool:
    """True when Emma's last question was a yes/no confirmation."""
    return (
        (s.step == 2 and bool(s.temp_name))
        or (s.step == 4 and bool(s.temp_phone))
        or (s.step == 5 and bool(s.temp_service))
        or (s.step == 7 and bool(s.temp_date))
        or (s.step == 8 and bool(s.temp_time))
        or s.step in (6, 9, 11)
    )


def fast_entities(user_text: str, s):
    """Entities for this turn, or None when the turn needs the LLM."""
    text = (user_text or "").strip()
    if not text:
        return None
    lower = text.lower().replace("’", "'")
    if "?" in lower or _QUESTION_RE.search(lower):
        return None
    words = re.findall(r"[a-z0-9']+", lower)
    if not words:
        return None

    # The phone step comes before the length cap: a number spoken as words
    # ("nine eight double seven ...") easily runs past MAX_WORDS.
    if s.step == 4 and not s.temp_phone:
        ok, cleaned = backend_actions.validate_phone(normalize_spoken_digits(lower))
        if ok:
            ent = _empty()
            ent["phone_number"] = cleaned
            return ent
        return None

    if len(words) > MAX_WORDS:
        return None

    conf = ai_engine._parse_confirmation(text)
    # "oh" and "one" are everyday words, so spoken digits only count in a run.
    has_digits = any(ch.isdigit() for ch in lower) or len(normalize_spoken_digits(lower)) >= 3
    has_slot_content = (
        has_digits or bool(_TIME_WORD_RE.search(lower)) or bool(_DATE_WORD_RE.search(lower))
        or match_service_alias(lower) is not None
    )
    ent = _empty()

    # Step 11 closing: "nothing, thanks" / "that's all" / "bye".
    if s.step == 11 and _CLOSING_RE.search(lower) and not has_slot_content:
        ent["confirmation"] = "no"
        return ent

    # Step 10 alternatives: the state machine parses the pick from the raw text;
    # Tier-0 only needs to vouch that the turn is a simple pick or yes/no.
    if s.step == 10 and s.offering_alternatives:
        if re.search(r"\b(first|second|earlier|later|former|latter)\b", lower) or (
            conf and len(words) <= MAX_CONFIRM_WORDS
        ) or _TIME_WORD_RE.search(lower):
            ent["confirmation"] = conf
            return ent
        return None

    if _awaiting_yes_no(s):
        # A bare yes/no only. "No, it's Priya" or "yes but Tuesday" carry a
        # correction the LLM must extract.
        if conf and len(words) <= MAX_CONFIRM_WORDS and not has_slot_content:
            if conf == "no" and s.step == 2 and len(words) > 1 and not re.fullmatch(
                r"(no|nope|nah)(,)?( that's| that is| it's| it is)?( not)?( right| correct| wrong)?", lower
            ):
                return None  # probably "no, it's <name>"
            ent["confirmation"] = conf
            return ent
        return None

    if s.step in (1, 3):
        if _BOOKING_PHRASE_RE.match(lower.strip(" .!")):
            return ent  # the state machine reads the booking keyword itself
        if s.step == 1 and conf == "yes" and len(words) <= 4 and not has_slot_content:
            ent["confirmation"] = "yes"
            return ent
        return None

    if s.step == 2:  # no candidate name yet
        m = _NAME_INTRO_RE.match(lower.strip(" .!"))
        if m:
            name = m.group(1).strip(" .")
            if 1 <= len(name.split()) <= 4 and ai_engine._looks_like_name(name, strict=True):
                ent["patient_name"] = name.title()
                return ent
        return None

    if s.step == 5:  # no candidate service yet
        service = match_service_alias(lower)
        if service and not _DATE_WORD_RE.search(lower) and not _TIME_WORD_RE.search(lower):
            ent["dental_service"] = service
            return ent
        return None

    if s.step == 7:  # no candidate date yet
        if _TIME_WORD_RE.search(lower) or match_service_alias(lower):
            return None  # mixed answer: let the LLM split date from time/service
        phrase = _strip_fillers(lower)
        if phrase and len(phrase.split()) <= 4:
            _d, resolved, _err = backend_actions.resolve_date(phrase)
            if resolved:
                ent["appointment_date"] = phrase
                return ent
        return None

    if s.step == 8:  # no candidate time yet
        if _DATE_WORD_RE.search(lower):
            return None
        phrase = _strip_fillers(lower)
        if phrase and len(phrase.split()) <= 3:
            _t, resolved, _err = backend_actions.resolve_time(phrase)
            if resolved:
                ent["appointment_time"] = phrase
                return ent
        return None

    return None


# ======================================================================
# Tier-0 v2 (docs/R2_DESIGN.md, section 6): understand() for the R2 engine.
#
# Same philosophy as fast_entities above, but it returns the engine's
# Understanding type and reads only a Tier0View, never the context. Strict
# mode (the default) answers short, unambiguous replies to what Emma just
# asked, plus a few global patterns (repeat, wait, bye, capability, booking
# openers, emergencies). Everything else returns None and goes to the model.
# lenient=True is the no-model fallback: broader extraction, always an
# Understanding, used when Gemini is down, slow or rate-limited.
#
# fast_entities and the helpers above stay untouched until integration
# removes the old engine.
# ======================================================================

STRICT_MAX_WORDS = 8

# Goals after which "thanks" / "that's all" means the caller is wrapping up.

@lru_cache(maxsize=512)
def _parse_when_on(text: str, expecting, today) -> "dateparse.When":
    return dateparse.parse_when(text, today=today, expecting=expecting)


def _parse_when(text: str, *, expecting=None) -> "dateparse.When":
    """
    dateparse.parse_when, memoised per day: one turn asks about the same
    words several times (strict checks, the split into date and time), and
    Tier-0 has to stay well under 5 ms. When is frozen, so sharing is safe.
    """
    return _parse_when_on(text or "", expecting, clock.today())

_CLOSING_QUESTION_GOALS = frozenset({
    Goal.ANYTHING_ELSE, Goal.BOOKED, Goal.CANCELLED, Goal.RESCHEDULED, Goal.STATE_APPOINTMENT,
    Goal.OFFER_REBOOK, Goal.OFFER_HELP, Goal.CALLBACK_DONE, Goal.DROPPED, Goal.CLOSE,
})
# Goals where a booking / cancel opener answers Emma's question rather than volunteering.
_INTENT_GOALS = frozenset({
    None, Goal.GREET, Goal.ASK_INTENT, Goal.ANYTHING_ELSE, Goal.OFFER_HELP, Goal.CAPABILITY,
    Goal.ANSWER_ONLY, Goal.OFFER_REBOOK, Goal.DROPPED, Goal.HELP_FIRST, Goal.MAX_REACHED,
    Goal.DUPLICATE_CHECK,
})
# Goals where a bare "cancel it" can only mean the booking or an appointment.
_BARE_CANCEL_GOALS = _INTENT_GOALS | {Goal.SUMMARY, Goal.SUMMARY_AGAIN, Goal.WHAT_TO_CHANGE}
_OFFER_GOALS = frozenset({Goal.OFFER_SLOTS, Goal.OFFER_NEW_SLOTS})
# Only part of a number ("it ends with 003", "the last four are..."): not digits to collect.
_NOT_A_NUMBER_RE = re.compile(r"\b(?:ends? (?:at|with|in)|last (?:two|three|four|few|digits?)|starts? with)\b")
# A date or time said while Emma collects a number ("On Saturday, October 10 at 03:30PM").
# Words, not dateparse: "nine two six one" must stay digits, not 9 o'clock.
_DATE_TIME_WORDS_RE = re.compile(
    r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|today|tomorrow|tonight|"
    r"january|february|march|april|may|june|july|august|september|october|november|december|"
    r"a\.?m\.?|p\.?m\.?|o'?clock|morning|afternoon|evening|night)\b|\b\d{1,2}:\d{2}\b")
# "I don't know the number", "I only know the name".
_NO_NUMBER_RE = re.compile(r"\b(?:(?:don'?t|do not|didn'?t) (?:know|have|remember)(?: (?:the|my|that|which))?"
                           r"(?: phone)? number|only (?:know|have|remember) the name|forgot (?:the|my) number|"
                           r"not sure (?:of|about|which) (?:the )?number)\b")
# Answers to "another one, or change that one?" (Goal.DUPLICATE_CHECK).
_DUP_CHANGE_RE = re.compile(r"\b(?:change|move|shift|reschedule|replace|swap|instead|update|that one|"
                            r"the old one|the existing one|existing)\b")
_DUP_ANOTHER_RE = re.compile(r"\b(?:another|one more|a new one|new one|second|additional|extra|separate|"
                             r"different one|both)\b")
# A bare "book it" right after the booking was made.
_BOOK_IT_AGAIN_RE = re.compile(r"^(?:(?:yes|yeah|yep|okay|ok|sure|alright|fine|great|good|thanks|thank you)"
                               r"[ ,.!]+)*(?:please )?(?:book it|book that|go ahead|do it|confirm it|"
                               r"book it please|yes book it)$")
_DATE_ISSUES = frozenset({"PAST", "SUNDAY", "BEYOND_HORIZON", "INVALID_DAY"})

_LEAD_RE = re.compile(
    r"^(?:(?:hi|hello|hey|yes|yeah|yep|ok|okay|sure|so|um+|uh+|good morning|good afternoon|"
    r"good evening|ma'?am|madam|sir|emma|actually|please|and|then|also|well|right|alright|"
    r"see|listen)\s+)*"
)
_WANT = (r"(?:i'?d like to|i would like to|i want to|i wanna|i need to|i have to|i wish to|"
         r"i was hoping to|i'?m calling to|i am calling to|i called to|i'?m looking to|"
         r"i am looking to|can i|could i|may i|can you|could you|please|let me|want to|"
         r"need to|looking to|i'?ll|i will|i'?d|just want to|just wanted to|i wanted to)\s+")
_BOOK_HEAD_RE = re.compile(
    rf"^(?:{_WANT})?(?:book|schedule|make|fix|get|take|set up|arrange)\b(?P<rest>.*)$|"
    r"^(?:i'?d like|i want|i need|i would like|can i (?:get|have)|could i (?:get|have)|"
    r"i'?m looking for|i am looking for|looking for)\b(?P<rest2>.*)$"
)
_CANCEL_HEAD_RE = re.compile(rf"^(?:{_WANT})?(?:cancel|call off)\b(?P<rest>.*)$")
_RESCHEDULE_HEAD_RE = re.compile(
    rf"^(?:{_WANT})?(?P<verb>reschedule|re schedule|change|move|shift|postpone|prepone|rebook|re book)"
    r"\b(?P<rest>.*)$"
)
_CHECK_RE = re.compile(
    rf"^(?:(?:{_WANT})?(?:check|confirm|know|find out|verify)\s+(?:my|the)\s+(?:appointment|booking)"
    r"(?:\s+(?:time|date|details|timing))?|when is my (?:appointment|booking)|"
    r"what time is my (?:appointment|booking))(?:\s+please)?$"
)
_APPT_NOUN_RE = re.compile(r"\b(appointment|appointments|booking|bookings|visit)\b")
# Words an opener may carry besides its verb and object.
_OPENER_FILLER = {
    "an", "a", "my", "one", "another", "new", "the", "appointment", "appointments", "booking",
    "visit", "slot", "please", "for", "me", "with", "you", "to", "dentist", "dental", "doctor",
    "clinic", "at", "your", "pearl", "this", "that", "it", "existing", "upcoming", "booked",
    "i", "have", "had", "already", "just", "some", "kind", "of", "get", "done", "is", "there",
    "um", "uh", "today", "actually", "quick", "small", "only",
}

_REPEAT_RE = re.compile(
    r"^(?:(?:sorry|hello|excuse me|um+|uh+|what)\s+)*(?:sorry|pardon|pardon me|come again|what|huh|"
    r"what was that|say that again|say again|say it again|repeat|repeat that|repeat it|"
    r"(?:can|could|would) you (?:please )?(?:repeat|say) (?:that|it)(?: again)?|"
    r"(?:can|could|would) you (?:please )?repeat|please repeat(?: that| it)?|"
    r"i didn'?t (?:get|catch|hear|understand) (?:that|you|it)|i couldn'?t hear (?:you|that|it)|"
    r"i can'?t hear (?:you|that)|one more time|again please|what did you say|excuse me)"
    r"(?:\s+(?:please|again|sorry|ma'?am|madam))*$"
)
_UNIT = r"(?:sec|second|seconds|minute|minutes|moment|min)"
_WAIT_RE = re.compile(
    r"^(?:(?:ok|okay|yes|yeah|sure|sorry|just|um+|uh+|wait)\s+)*"
    rf"(?:hold on|hang on|wait|one {_UNIT}|a {_UNIT}|just a {_UNIT}|give me (?:a|one|a few) {_UNIT}|"
    rf"let me (?:check|see|think|look)|wait a {_UNIT}|two {_UNIT}|1 {_UNIT})"
    rf"(?:\s+(?:please|a {_UNIT}|one {_UNIT}|let me (?:check|see|think|look)|my (?:calendar|diary|schedule)|"
    r"on|for me|a bit|i'?m checking|i am checking|i'?ll check|i will check|i'?ll tell you|ma'?am|madam))*$"
)
_BYE_RE = re.compile(r"\b(bye|goodbye|good bye|bye bye|take care|have a (?:nice|good|great) day|see you)\b")
_END_RE = re.compile(
    r"\b(bye|goodbye|that'?s all|that is all|that'?ll be all|that will be all|nothing else|"
    r"no thanks|no thank you|nothing|that'?s it for now|i'?m done|i am done|all good)\b"
)
_END_VOCAB = {
    "no", "nope", "ok", "okay", "thanks", "thank", "you", "so", "much", "that's", "thats", "all",
    "nothing", "else", "bye", "goodbye", "good", "it", "is", "that", "will", "be", "that'll",
    "great", "fine", "alright", "then", "have", "a", "nice", "day", "take", "care", "very",
    "sir", "ma'am", "maam", "madam", "for", "now", "i'm", "im", "i", "am", "done", "see",
    "lot", "yeah", "yes", "right", "cool", "perfect", "emma", "and", "just",
}
_THANKS_RE = re.compile(r"^(?:ok(?:ay)?\s+)?(?:thanks|thank you|thank you so much|thanks a lot|thank you very much|"
                        r"many thanks|thanks so much)(?:\s+(?:emma|ma'?am|madam|sir))?$")
_ROBOT_RE = re.compile(
    r"\b(?:are you|is this|is that|am i (?:talking|speaking) (?:to|with)|i'?m (?:talking|speaking) (?:to|with))"
    r"\s+(?:a |an )?(?:real |actual |live )?(?:bot|robot|machine|computer|ai|a i|artificial intelligence|"
    r"human|human being|person|recording|recorded message|automated|program|chatbot|real person)\b|"
    r"\bare you (?:real|human|automated)\b"
)
_ROBOT_FILLER = {
    "hello", "hi", "sorry", "wait", "excuse", "me", "just", "so", "ok", "okay", "tell", "or", "a",
    "an", "i", "is", "this", "am", "talking", "to", "real", "honestly", "actually", "are", "you",
    "speaking", "with", "machine", "bot", "robot", "ai", "person", "human", "what", "something",
    "else", "like", "being", "one", "question", "quick", "can", "ask", "first",
}
_HUMAN_RE = re.compile(
    r"\b(?:talk|speak|connect|transfer|put me through)\b.*\b(?:to|with)\b.*"
    r"\b(?:person|human|someone|somebody|receptionist|staff|manager|operator|doctor|dentist|team|real person)\b|"
    r"\b(?:i want|i need|get me|give me)\s+(?:a |the )?(?:real |live )?(?:person|human|receptionist|manager|operator)\b"
)
_CAP_LEAD_RE = re.compile(
    r"^(?:(?:hi|hello|hey|ok|okay|so|um+|uh+|yes|yeah|emma|ma'?am|madam|please|actually|and|first|"
    r"sorry|wait|one second|tell me|can you tell me|could you tell me|just tell me|first tell me|but)\s+)*"
)
_CAPABILITY_RE = re.compile(
    r"^(?:how (?:can|could|will|would|do) you help(?: me| us)?(?: today)?(?: with)?|"
    r"what (?:all )?(?:can|could|do) you (?:do|help(?: me)? with|offer)(?: for me)?(?: here)?|"
    r"what (?:all )?(?:things )?can you help(?: me)? with|what can i ask(?: you)?(?: about)?|"
    r"what are you able to do|what (?:all )?do you (?:help|handle) with|what do you do(?: here)?|"
    r"what is your (?:job|role|work|name)|what'?s your (?:job|role|name)|who are you|who is this|"
    r"who am i (?:talking|speaking) (?:to|with)|what is this(?: service)?|what are you|"
    r"in what ways can you help(?: me)?|how are you going to help(?: me)?|"
    r"what (?:all )?(?:things )?you can (?:do|help(?: me| us)? with|offer)(?: for me| for us)?|"
    r"what (?:all )?you (?:do|offer)(?: here)?)"
    r"(?:\s+(?:please|emma|exactly|actually|then))*$"
)
_LANGUAGES = r"(?:hindi|kannada|tamil|telugu|malayalam|marathi|bengali|urdu|gujarati|punjabi)"
_LANGUAGE_RE = re.compile(
    rf"^(?:please\s+)?(?:speak|talk|reply|answer|explain)\s+(?:to me\s+)?in\s+{_LANGUAGES}(?:\s+please)?$|"
    rf"^{_LANGUAGES}\s+(?:please|mein|me|bolo|bolie|boliye)$"
)
_NON_LATIN_RE = re.compile(r"[Ͱ-῿　-￿]")

_RED_FLAG_RE = re.compile(
    r"\b(?:can'?t|cannot|can not|unable to|hard to|trouble|difficulty|struggling to|not able to)\s+"
    r"(?:breath(?:e|ing)?|swallow(?:ing)?)\b|"
    r"\b(?:breathing|swallowing)\s+(?:problem|trouble|difficulty|is hard|is difficult)\b|"
    r"\b(?:swelling|swollen)\b.{0,30}\b(?:eye|eyes|neck|throat|spreading|spread)\b|"
    r"\b(?:bleeding|blood)\b.{0,30}\b(?:won'?t|will not|doesn'?t|does not|not)\s+stop|"
    r"\b(?:non ?stop|nonstop|uncontrolled|uncontrollable)\s+bleeding\b|"
    r"\b(?:broken|broke|fractured|dislocated)\s+(?:my\s+)?jaw\b|"
    r"\bjaw\s+(?:is\s+)?(?:broken|fractured|dislocated|injured|injury)\b"
)
_URGENT_RE = re.compile(
    r"\b(?:severe|unbearable|terrible|extreme|killing|very bad|really bad|a lot of|lot of|too much|so much|"
    r"bad|intense|horrible)\s+(?:tooth\s*)?(?:pain|toothache|ache)\b|"
    r"\b(?:pain|toothache)\b.{0,25}\b(?:unbearable|severe|killing me|can'?t sleep|can'?t bear)\b|"
    r"\b(?:swelling|swollen)\b|\bbleeding\b|"
    r"\b(?:broken|broke|cracked|chipped|knocked out|fell out)\b.{0,20}\b(?:tooth|teeth)\b|"
    r"\b(?:tooth|teeth)\b.{0,20}\b(?:broke|broken|cracked|chipped|knocked out|fell out)\b|"
    r"\bemergency\b|\babscess\b|\burgent(?:ly)?\b"
)
_NEGATION_RE = re.compile(r"\b(?:no|not|isn'?t|without|never|any|nothing)\b")
# "no swelling, just a check-up": a symptom ruled out before the real answer. Cut
# out before the yes / no test, so its "no" isn't heard as declining the question.
_NEGATED_SYMPTOM_RE = re.compile(
    r"\b(?:no|not|without|never had|there'?s no|there is no|i don'?t have|i do not have)\s+(?:any\s+)?"
    r"(?:swelling|pain|bleeding|fever|problem|problems|issue|issues|emergency|ache|toothache|"
    r"sensitivity|discomfort)\b(?:\s+(?:as such|or anything|really))?[ ,.]*"
)

# Words a yes / no reply may carry beside the answer itself.
_YES_NO_VOCAB = {
    "yes", "yeah", "yep", "yup", "ya", "yea", "sure", "correct", "right", "ok", "okay", "exactly",
    "absolutely", "definitely", "certainly", "of", "course", "go", "ahead", "proceed", "sounds",
    "looks", "good", "perfect", "great", "fine", "works", "work", "that's", "thats", "that", "is",
    "it", "it's", "its", "please", "do", "book", "confirm", "alright", "all", "cool", "the",
    "number", "my", "thanks", "thank", "you", "sir", "ma'am", "maam", "madam", "that'll", "be",
    "done", "mhm", "mm", "hmm", "uh", "huh", "no", "nope", "nah", "not", "isn't", "wrong",
    "incorrect", "sorry", "i", "don't", "think", "so", "actually", "mistake", "this", "one",
    "very", "much", "will", "would", "oh", "haan", "ji", "hundred", "percent",
}
_PHONE_FILLER = {
    "my", "number", "is", "it's", "its", "it", "the", "phone", "mobile", "cell", "yeah", "yes",
    "okay", "ok", "sure", "uh", "um", "umm", "and", "then", "plus", "code", "sorry", "rest",
    "remaining", "last", "digits", "digit", "are", "sir", "ma'am", "maam", "hmm", "mm", "so",
    "that's", "thats", "next", "few", "first", "after", "that", "comma", "dash", "hyphen",
    "contact", "whatsapp", "this", "on", "you", "can", "reach", "me", "at", "call",
}
# Words a date / time reply may carry besides the date itself.
_WHEN_VOCAB = {
    "today", "tomorrow", "day", "after", "next", "this", "coming", "week", "weekend", "month",
    "morning", "afternoon", "evening", "night", "noon", "early", "late", "lunch", "before",
    "around", "about", "at", "on", "in", "by", "the", "of", "sometime", "some", "time", "any",
    "anytime", "earliest", "soon", "as", "possible", "asap", "first", "available", "slot",
    "please", "maybe", "i", "think", "preferably", "prefer", "would", "be", "good", "fine",
    "okay", "ok", "yes", "yeah", "sure", "works", "then", "or", "between", "and", "to", "from",
    "half", "past", "quarter", "oclock", "pm", "am", "is", "it", "let's", "lets", "say", "go",
    "with", "for", "we", "make", "shall", "um", "uh", "hmm", "so", "possibly", "ideally",
    "approximately", "approx", "like", "just", "only", "that", "date", "free", "i'm", "im",
    "whenever", "anyday", "flexible", "doesn't", "matter", "don't", "mind", "earlier", "later",
    "itself", "nearest", "a", "an", "end", "start", "beginning", "middle", "mid", "same", "following",
    "tonight", "evenings", "mornings", "afternoons", "weekdays", "weekday", "weekends", "will",
    "can", "come", "visit", "suit", "suits", "convenient", "better", "best", "my", "side",
    "o'clock", "clock", "hours", "hour", "sharp", "exactly", "nd", "st", "rd", "th", "o",
}
_WHEN_VOCAB |= set(dateparse.WEEKDAYS) | set(dateparse.MONTHS)
_ORDINAL_WORD_RE = re.compile(
    r"\d+(?:st|nd|rd|th)|(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|"
    r"twelfth|thirteenth|fourteenth|fifteenth|sixteenth|seventeenth|eighteenth|nineteenth|twentieth|"
    r"thirtieth)"
)
_ORDINAL_PICKS = {
    "first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
    "former": 1, "earlier": 1, "earliest": 1, "sooner": 1, "latter": -1, "later": -1, "last": -1,
}
_PICK_RE = re.compile(
    r"\b(first|1st|second|2nd|third|3rd|fourth|4th|former|latter|last|earlier|earliest|sooner|later)"
    r"(?:\s+(?:one|option|slot|time|choice|appointment))?\b"
)
_NUMBER_PICK_RE = re.compile(r"^(?:the\s+|option\s+|number\s+)*(one|two|three|1|2|3)(?:\s+please)?$")
# A bare clock time picking an offer ("5 is fine", "the 5:30 one", "at 5 please"),
# matched on dateparse.normalize output (number words are digits there). parse_when
# only reads a bare number when it is the whole reply, so the number is cut out first.
_BARE_TIME_PICK_RE = re.compile(
    r"^(?:the |at |around |about )?(\d{1,2}(?:[:.]\d{2}|\s+[0-5]\d)?)"
    r"(?:\s+(?:1|slot|oclock|please|then|works|is fine|is good|is ok|is okay|is perfect|sounds good|"
    r"would be (?:good|great|fine|perfect)))*$"
)
_EITHER_RE = re.compile(r"\b(either|any of them|any one|anyone|whichever|both are fine|both work|anything is fine)\b")
_REJECT_RE = re.compile(
    r"\b(none|neither|nothing)\b|\b(?:something|anything|some) else\b|"
    r"\b(?:other|another|different) (?:time|times|day|days|slot|slots|option|options|date)\b|"
    r"\b(?:doesn'?t|don'?t|won'?t|does not|do not|will not) (?:work|suit)\b|"
    r"\bnot (?:suitable|possible|convenient|good)\b"
)
_ANY_BRANCH_RE = re.compile(
    r"^(?:(?:um+|uh+|ok|okay|yeah|i think|any|just)\s+)*(?:any|anywhere|any branch|any one|anyone|whichever|"
    r"whichever is (?:earliest|soonest|first|closest|nearest|free|available|fine)|doesn'?t matter|"
    r"does not matter|no preference|any is fine|anything is fine|wherever|earliest|any location|"
    r"whatever is (?:earliest|available|free|fine))(?:\s+(?:is fine|works|please|branch|one|is ok|is okay))*$"
)
# "Any branch is fine, whichever has a slot": the same answer with more words.
_ANY_BRANCH_LOOSE_RE = re.compile(
    r"\b(?:any branch|any (?:one|location|clinic|of them|of those)|whichever (?:branch|one|is|has)|"
    r"doesn'?t matter|does not matter|no preference|anywhere is fine|wherever|don'?t mind which)\b")


def _any_branch(t: str, view: Tier0View) -> bool:
    """At the branch question: "whichever", "any branch is fine, whichever has a slot", and no branch named."""
    if _ANY_BRANCH_RE.match(t):
        return True
    if len(_tokens(t)) > 12 or not _ANY_BRANCH_LOOSE_RE.search(t):
        return False
    return match.match_branch(t, _catalog_part(view, "branches")).value is None


_DOCTOR_FILLER = {
    "with", "dr", "doctor", "doc", "please", "i", "want", "prefer", "would", "like", "a", "the",
    "can", "have", "lady", "female", "male", "gents", "gent", "man", "woman", "preferably", "if",
    "possible", "i'd", "id", "need", "only", "dentist", "should", "be", "ladies", "is", "fine",
    "okay", "ok", "better", "um", "uh",
}
_SERVICE_FILLER = {
    "a", "an", "the", "i", "need", "want", "for", "please", "my", "it's", "its", "just", "some",
    "get", "done", "think", "maybe", "like", "would", "i'd", "id", "to", "have", "do", "um", "uh",
    "ok", "okay", "yeah", "so", "it", "is", "regular", "general", "routine", "basic", "normal",
    "treatment", "appointment", "booking", "visit", "dental", "full", "simple", "looking", "of",
    "kind", "sort", "probably", "me", "with", "am", "i'm", "only", "one", "small", "quick",
    "proper", "complete", "wanted", "thinking", "actually", "this", "time", "and", "also",
}
_AGE_RE = re.compile(
    r"^(?:(?:he|she|they|my\s+\w+)\s+(?:is|'s)\s+|(?:he's|she's|it's|i'm|i\s+am|about|around|just|only)\s+)*"
    r"(\d{1,2}|[a-z]+(?:\s+[a-z]+)?)(?:\s+(?:years?|yrs?)(?:\s+old)?)?(?:\s+now)?$"
)
_AGE_WORDS = {**{k: int(v) for k, v in match.DIGIT_WORDS.items() if k not in ("oh", "o")},
              **match.TEEN_WORDS, **match.TENS_WORDS}
_CORRECTION_RE = re.compile(r"\b(actually|instead|make it|change it to|i meant|i mean|not .{1,20} but|sorry it'?s)\b")
_FOR_SOMEONE_RE = re.compile(
    r"\bfor my (son|daughter|mother|mom|mum|father|dad|wife|husband|child|kid|kids|baby|brother|"
    r"sister|grandmother|grandfather|grandma|grandpa|parents|friend|uncle|aunt|aunty|niece|nephew|boy|girl)\b"
)


def _clean(text: str) -> str:
    """Lowercase, apostrophes straightened, punctuation to spaces (apostrophes and digit colons kept)."""
    t = (text or "").lower().replace("’", "'")
    t = re.sub(r"(?<!\d):|:(?!\d)", " ", t)
    t = re.sub(r"[^a-z0-9': ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _tokens(t: str) -> list:
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", t)


def _u(raw: str, source: str, acts, **fields) -> Understanding:
    return Understanding(acts=[a.value if isinstance(a, Act) else a for a in acts],
                         source=source, raw_text=raw, **fields)


def _negated(t: str, start: int) -> bool:
    """A negation word just before position `start` ("no swelling", "not bleeding")."""
    return bool(_NEGATION_RE.search(t[max(0, start - 14):start]))


def _emergency(t: str) -> Emergency:
    for m in _RED_FLAG_RE.finditer(t):
        if not _negated(t, m.start()):
            return Emergency.RED_FLAG
    for m in _URGENT_RE.finditer(t):
        if not _negated(t, m.start()):
            return Emergency.URGENT
    return Emergency.NONE


def _expect(view: Tier0View) -> str:
    exp = getattr(view, "expect", None) or Expect.OPEN
    return exp.value if isinstance(exp, Expect) else str(exp)


def _catalog_part(view: Tier0View, attr: str) -> tuple:
    return tuple(getattr(view.catalog, attr, ()) or ()) if view.catalog is not None else ()


def _split_when(raw: str, when) -> tuple:
    """
    (date_phrase, time_phrase) for a parsed reply. "tomorrow evening" splits
    into "tomorrow" and "evening" when each part re-parses to the same thing
    on its own; otherwise both carry the whole phrase. A time alone never
    produces a date phrase (the 5 PM -> Thursday call).
    """
    phrase = raw.strip(" ,.!?")
    has_date = when.date is not None or any(i.code in _DATE_ISSUES for i in when.issues)
    has_time = when.time is not None or any(i.code not in _DATE_ISSUES for i in when.issues)
    if when.date is not None and when.time is not None:
        norm = dateparse.normalize(raw)
        label = when.time.label
        if label and label in norm:
            rest = re.sub(r"\s+", " ", norm.replace(label, " ", 1)).strip()
            rest = re.sub(r"^(?:at|around|by|in the|on|about)\s+|\s+(?:at|around|by|in the|on|about)$", "",
                          rest).strip()
            if rest and _when_residue_ok(rest):     # "with Dr Rao" left over: find the windows instead
                alone = _parse_when(rest, expecting="date")
                timed = _parse_when(label, expecting="time")
                if alone.date == when.date and alone.time is None and timed.time == when.time:
                    return rest, label
        found = _when_windows(norm, when)
        return found if found else (phrase, phrase)
    return (phrase if has_date else None), (phrase if has_time else None)


def _when_windows(norm: str, when) -> tuple | None:
    """
    The shortest runs of words (up to 4) that give the date alone and the
    time alone: "tomorrow" and "evening around 6" out of "tomorrow evening
    around 6 with Dr Sharma". None if either can't be isolated.
    """
    toks = norm.split()
    date_part = time_part = None
    for size in range(1, min(4, len(toks)) + 1):
        for i in range(len(toks) - size + 1):
            last = toks[i + size - 1]
            if not (_when_word(toks[i]) and _when_core(last)):
                continue                    # never starts on "with" or ends on "around" / "is"
            chunk = " ".join(toks[i:i + size])
            if date_part is None and last not in _TIME_ONLY_CORE:
                alone = _parse_when(chunk, expecting="date")
                if alone.date == when.date and alone.time is None:
                    date_part = chunk
            if time_part is None and last not in _DATE_ONLY_CORE:
                alone = _parse_when(chunk, expecting="time")
                if alone.time == when.time and alone.date is None:
                    time_part = chunk
        if date_part and time_part:
            return date_part, time_part
    return None


def _when_word(tok: str) -> bool:
    return any(ch.isdigit() for ch in tok) or tok in _WHEN_VOCAB or tok in match.DIGIT_WORDS \
        or tok in match.TEEN_WORDS or tok in match.TENS_WORDS or bool(_ORDINAL_WORD_RE.fullmatch(tok))


_WHEN_CORE = frozenset({
    "today", "tomorrow", "tonight", "morning", "afternoon", "evening", "night", "noon", "midnight",
    "lunch", "week", "weekend", "month", "pm", "am", "oclock", "o'clock", "clock", "evenings",
    "mornings", "afternoons", "weekdays", "weekday", "weekends", "earliest", "asap", "possible",
}) | frozenset(dateparse.WEEKDAYS) | frozenset(dateparse.MONTHS)


# Words that end only a time phrase, or only a date phrase.
_TIME_ONLY_CORE = frozenset({
    "morning", "afternoon", "evening", "night", "noon", "midnight", "lunch", "pm", "am", "oclock",
    "o'clock", "clock", "evenings", "mornings", "afternoons",
})
_DATE_ONLY_CORE = _WHEN_CORE - _TIME_ONLY_CORE - {"tonight", "earliest", "asap", "possible"}


def _when_core(tok: str) -> bool:
    """A word a date or time phrase can end on ("6", "Monday", "evening"), unlike "around" or "is"."""
    return tok in _WHEN_CORE or any(ch.isdigit() for ch in tok) or tok in match.DIGIT_WORDS \
        or tok in match.TEEN_WORDS or tok in match.TENS_WORDS or bool(_ORDINAL_WORD_RE.fullmatch(tok))


def _when_residue_ok(t: str, extra: frozenset = frozenset()) -> bool:
    """Every word of a date / time reply is date-ish or filler: nothing else hides in it."""
    return all(tok in extra or _when_word(tok) for tok in _tokens(t))


def _other_slots(raw: str, view: Tier0View, *, service=True, branch=True, doctor=True) -> bool:
    """True when the reply also names a service, branch or doctor (a mixed answer)."""
    if service:
        svc = match.match_service(raw, _catalog_part(view, "services"))
        if svc.value or svc.unknown_phrase:
            return True
    if branch and match.match_branch(raw, _catalog_part(view, "branches")).value:
        return True
    if doctor:
        d = match.match_doctor(raw, _catalog_part(view, "doctors"))
        if d.value or d.unknown_phrase or d.options or match.doctor_gender(raw):
            return True
    return False


def _service_phrase(text: str) -> str:
    """The caller's words for the service, minus fillers ("a root canal please" -> "root canal")."""
    words = [w for w in _tokens(_clean(text)) if w not in _SERVICE_FILLER]
    return " ".join(words) or text.strip(" ,.!?")


# Who the visit is for. Tier-0's opener reads only the intent and the service,
# so a turn naming someone else goes to the model instead of losing them.
_OPENER_FOR_SOMEONE_RE = re.compile(
    r"\b(?:for (?:my|his|her|our|their)|for (?:him|her|them)|someone else|somebody else|"
    r"son|daughter|kid|kids|child|children|baby|mother|father|mom|mum|dad|wife|husband|brother|sister|"
    r"grandmother|grandfather|grandma|grandpa|family|friend)\b")


def _opener(t: str, view: Tier0View, strict: bool):
    """
    A plain booking / cancel / reschedule / check opener: (intent, service or
    None, service phrase or None), or None. Strict mode allows only the opener
    plus at most a single service ("I want to book a root canal").
    """
    body = _LEAD_RE.sub("", t).strip()
    if not body:
        return None
    if strict and _OPENER_FOR_SOMEONE_RE.search(body):
        return None              # "...a cleaning for my son": the model reads who it's for
    if _CHECK_RE.match(body):
        return Intent.CHECK, None, None
    m = _CANCEL_HEAD_RE.match(body)
    if m:
        rest = m.group("rest")
        if all(w in _OPENER_FILLER for w in _tokens(rest)) and (
                _APPT_NOUN_RE.search(rest) or view.pending in _BARE_CANCEL_GOALS):
            return Intent.CANCEL, None, None
        return None
    m = _RESCHEDULE_HEAD_RE.match(body)
    if m:
        rest = m.group("rest")
        if all(w in _OPENER_FILLER for w in _tokens(rest)) and (
                _APPT_NOUN_RE.search(rest) or m.group("verb") in ("reschedule", "re schedule", "postpone", "prepone")):
            return Intent.RESCHEDULE, None, None
        return None
    m = _BOOK_HEAD_RE.match(body)
    if not m:
        if _APPT_NOUN_RE.search(body) and all(w in _OPENER_FILLER for w in _tokens(body)) \
                and view.pending in _INTENT_GOALS:
            return Intent.BOOK, None, None          # "an appointment, please"
        return None
    verb_form = m.group("rest") is not None         # "book ...", not "I need ..."
    rest = m.group("rest") if verb_form else m.group("rest2")
    rest_words = _tokens(rest or "")
    if all(w in _OPENER_FILLER for w in rest_words):
        if verb_form or _APPT_NOUN_RE.search(rest or ""):
            return Intent.BOOK, None, None
        return None
    svc = match.match_service(rest, _catalog_part(view, "services"),
                              asked=view.pending in (Goal.ASK_SERVICE, Goal.CLARIFY_SERVICE))
    if svc.value is None and not svc.unknown_phrase:
        return None
    if strict:
        when = _parse_when(rest)
        if len(rest_words) > 6 or match.looks_like_question(rest) or not when.empty \
                or _other_slots(rest, view, service=False):
            return None
        phrase = svc.unknown_phrase or _service_phrase(rest)
        if not all(w in _OPENER_FILLER or w in _SERVICE_FILLER or w in phrase.split() for w in rest_words):
            return None
    phrase = svc.unknown_phrase or _service_phrase(rest)
    return Intent.BOOK, svc.value, phrase


def _globals(t: str, raw: str, words: list, view: Tier0View, source: str):
    """Patterns that mean the same whatever Emma asked."""
    if _NON_LATIN_RE.search(raw or "") or _LANGUAGE_RE.match(t):
        return _u(raw, source, [Act.OTHER_LANGUAGE])
    if _REPEAT_RE.match(t) or match.HEAR_CHECK_RE.match(t):
        return _u(raw, source, [Act.REPEAT])
    if _WAIT_RE.match(t):
        return _u(raw, source, [Act.WAIT])
    if len(words) <= 12 and _ROBOT_RE.search(t) and not _HUMAN_RE.search(t):
        if all(w in _ROBOT_FILLER for w in _tokens(_ROBOT_RE.sub(" ", t))):
            return _u(raw, source, [Act.ROBOT_QUESTION])
    # One sentence of a longer turn (8 Oct: "Yeah. I'm talking. I'm not... Are you an AI?").
    for sentence in re.split(r"[.?!]+", (raw or "").lower()):
        s = _clean(sentence)
        if s and _ROBOT_RE.search(s) and not _HUMAN_RE.search(s) \
                and all(w in _ROBOT_FILLER for w in _tokens(_ROBOT_RE.sub(" ", s))):
            return _u(raw, source, [Act.ROBOT_QUESTION])
    if len(words) <= 10 and _HUMAN_RE.search(t):
        return _u(raw, source, [Act.WANTS_HUMAN])
    cap = _CAP_LEAD_RE.sub("", t).strip()
    if cap and _CAPABILITY_RE.match(cap):
        return _u(raw, source, [Act.CAPABILITY])
    # Closing: "bye", "that's all, thanks", "no thank you". Never a yes (Z1:
    # "okay bye" at the summary must not book), at most a "no".
    if words in (["nothing"], ["no"], ["none"]) and view.pending not in _CLOSING_QUESTION_GOALS \
            and view.pending not in (None, Goal.GREET, Goal.ASK_INTENT) and _expect(view) not in ("yes_no", "choice"):
        # "What's the visit for?" -> "Nothing." (8 Oct: "Thanks for calling, take care!" and the
        # call ended). Not an answer; Emma asks again. "No thanks, that's all" still ends it.
        return _u(raw, source, [Act.NON_ANSWER])
    if words and all(w in _END_VOCAB for w in words) and _END_RE.search(t):
        has_bye = bool(_BYE_RE.search(t))
        if has_bye or view.pending in _CLOSING_QUESTION_GOALS or _expect(view) not in ("yes_no", "choice"):
            conf = "no" if match.parse_yes_no(raw) == "no" else None
            return _u(raw, source, [Act.END], confirmation=conf)
    if _THANKS_RE.match(t):
        if view.pending in _CLOSING_QUESTION_GOALS:
            return _u(raw, source, [Act.END])
        return _u(raw, source, [Act.CHITCHAT])
    return None


def _pick_index(t: str, count: int) -> int | None:
    """ "the first one", "second", "the later one", "two" -> a 1-based index within `count`."""
    m = _PICK_RE.search(t)
    num = _NUMBER_PICK_RE.match(t)
    if m:
        idx = _ORDINAL_PICKS[m.group(1)]
    elif num:
        idx = {"one": 1, "two": 2, "three": 3}.get(num.group(1)) or int(num.group(1))
    else:
        return None
    idx = count if idx == -1 else idx
    return idx if 1 <= idx <= count else None


def _offer_pick(t: str, raw: str, view: Tier0View, source: str):
    """A pick among the slots Emma just offered: by position, day or exact time."""
    offered = list(view.offered or ())
    n = len(offered)
    words = _tokens(t)
    idx = _pick_index(t, n)
    if idx:
        return _u(raw, source, [Act.ANSWER], choice_index=idx, confirmation="yes")
    if _REJECT_RE.search(t):
        return _u(raw, source, [Act.ANSWER], reject_options=True, confirmation="no")
    if _EITHER_RE.search(t) and match.parse_yes_no(raw) != "no" and n:
        return _u(raw, source, [Act.ANSWER], choice_index=1, confirmation="yes")
    when = _parse_when(raw, expecting="time")
    bare = _BARE_TIME_PICK_RE.match(dateparse.normalize(raw)) if when.empty else None
    if bare:
        when = _parse_when(bare.group(1), expecting="time")
    if not when.empty and _when_residue_ok(t, frozenset({"one", "slot", "works", "please", "the"})):
        hits = []
        for i, slot in enumerate(offered, 1):
            start = getattr(slot, "start", None)
            if start is None:
                continue
            ok = True
            if when.date is not None:
                ok = when.date.start <= start.date() <= when.date.end
            tc = when.time
            if ok and tc is not None:
                if tc.kind == "exact":
                    ok = start.time() == tc.start
                elif tc.kind == "ambiguous":
                    ok = start.time() in tc.candidates
                elif tc.kind == "window":
                    ok = tc.start <= start.time() < tc.end
            if ok:
                hits.append(i)
        if len(hits) == 1:
            return _u(raw, source, [Act.ANSWER], choice_index=hits[0], confirmation="yes")
        if hits:
            return None                     # "Monday" when both offers are Monday: ask which
        if match.parse_yes_no(raw) is None:
            # A different day or time than offered: a new constraint for the search.
            date_phrase, time_phrase = _split_when(raw, when)
            return _u(raw, source, [Act.ANSWER], date_phrase=date_phrase, time_phrase=time_phrase)
        return None
    conf = match.parse_yes_no(raw)
    if conf and len(words) <= 5 and all(w in _YES_NO_VOCAB for w in words):
        if conf == "yes":
            return _u(raw, source, [Act.ANSWER], confirmation="yes", choice_index=1 if n == 1 else None)
        return _u(raw, source, [Act.ANSWER], confirmation="no", reject_options=True)
    return None


def _option_pick(t: str, raw: str, view: Tier0View, source: str):
    """A pick among other spoken options (services, branches, appointments)."""
    options = [str(o) for o in (view.options or ())]
    pending = view.pending
    services = _catalog_part(view, "services")
    branches = _catalog_part(view, "branches")
    if pending == Goal.ASK_BRANCH and _any_branch(t, view):
        return _u(raw, source, [Act.ANSWER], branch_any=True)
    if pending == Goal.CLARIFY_SERVICE or set(options) & {s.name for s in services}:
        svc = match.match_service(raw, services, asked=True)
        if svc.value and (not options or svc.value in options or pending == Goal.CLARIFY_SERVICE):
            return _u(raw, source, [Act.ANSWER], service=svc.value, service_phrase=_service_phrase(raw))
    if pending == Goal.ASK_BRANCH or set(options) & {b.name for b in branches}:
        br = match.match_branch(raw, branches)
        if br.value:
            return _u(raw, source, [Act.ANSWER], branch=br.value)
    idx = _pick_index(t, len(options)) if options else None
    if idx:
        fields = {"choice_index": idx}
        chosen = options[idx - 1]
        if any(s.name == chosen for s in services):
            fields["service"] = chosen
        if any(b.name == chosen for b in branches):
            fields["branch"] = chosen
        return _u(raw, source, [Act.ANSWER], **fields)
    if options:
        hits = [i for i, o in enumerate(options, 1) if o and re.search(rf"\b{re.escape(o.lower())}\b", t)]
        if len(hits) == 1:
            return _u(raw, source, [Act.ANSWER], choice_index=hits[0])
    return None


def _age(t: str) -> int | None:
    m = _AGE_RE.match(t)
    if not m:
        return None
    said = m.group(1)
    if said.isdigit():
        age = int(said)
    else:
        parts = said.split()
        if not all(p in _AGE_WORDS for p in parts):
            return None
        if len(parts) == 2 and not (parts[0] in match.TENS_WORDS and _AGE_WORDS[parts[1]] < 10):
            return None
        age = sum(_AGE_WORDS[p] for p in parts)
    return age if 0 < age < 100 else None


def _name_fields(name: str, view: Tier0View) -> dict:
    return {"patient_name": name} if view.pending == Goal.ASK_PATIENT else {"name": name}


def _strict_answer(t: str, raw: str, words: list, view: Tier0View):
    """A short, unambiguous answer to what Emma asked, or None."""
    exp = _expect(view)
    pending = view.pending
    src = "tier0"

    if exp == "yes_no":
        if re.search(r"\d", t) or len(match.extract_digits(raw).digits) >= 3:
            return None                     # "no, it's 937": a correction for the model
        conf = match.parse_yes_no(raw)
        if conf == "no" and re.search(r"\b(book|cancel|change|reschedule|move)\b", t):
            return None                     # "don't book it" / "no, change the time"
        if conf and len(words) <= 5 and all(w in _YES_NO_VOCAB for w in words):
            return _u(raw, src, [Act.ANSWER], confirmation=conf)
        return None

    if exp == "phone":
        run = match.extract_digits(raw)
        if not run.digits or (len(run.digits) < 2 and not view.digits_so_far):
            return None
        if not all(w.isdigit() or w in match.DIGIT_WORDS or w in match.MULTIPLIERS or w in match.TENS_WORDS
                   or w in match.TEEN_WORDS or w in _PHONE_FILLER for w in words):
            return None
        return _u(raw, src, [Act.ANSWER], phone_digits=run.digits)

    if exp in ("name", "spelling"):
        spelled = match.join_spelled(raw)
        if spelled:
            return _u(raw, src, [Act.ANSWER], name_spelled=spelled)
        name = match.clean_name(raw)
        if not name:
            return None
        intro = bool(re.match(r"^(?:(?:yes|yeah|okay|ok|sure|hi|hello)\s+)*(?:my name is|my name's|the name is|"
                              r"name is|it's|it is|this is|i am|i'm|call me|myself)\b", t))
        if len(name.split()) > (4 if intro else 3) or not _parse_when(raw).empty:
            return None
        return _u(raw, src, [Act.ANSWER], **_name_fields(name, view))

    if exp in ("date", "time"):
        if match.parse_yes_no(raw) == "no":
            return None                     # "not Monday", "Monday doesn't work"
        when = _parse_when(raw, expecting=exp)
        if when.empty or not _when_residue_ok(t) or _other_slots(raw, view):
            return None
        date_phrase, time_phrase = _split_when(raw, when)
        if pending == Goal.ASK_APPT_DATE:
            if not date_phrase:
                return None
            return _u(raw, src, [Act.ANSWER], appt_date_phrase=date_phrase)
        return _u(raw, src, [Act.ANSWER], date_phrase=date_phrase, time_phrase=time_phrase)

    if exp == "choice":
        if pending in _OFFER_GOALS or (view.offered and not view.options):
            return _offer_pick(t, raw, view, src)
        return _option_pick(t, raw, view, src)

    # expect == "open": only the questions whose answer is a catalog value or a plain fact.
    if pending == Goal.ASK_SERVICE:
        said = _NEGATED_SYMPTOM_RE.sub(" ", t).strip()
        if not said:
            return None
        if said != t:
            t, words = said, _tokens(said)
        else:
            said = raw
        if match.parse_yes_no(said) or len(words) > 6:
            return None
        svc = match.match_service(said, _catalog_part(view, "services"), asked=True)
        if not (svc.value or svc.options or svc.unknown_phrase) or len(svc.options) > 1 and not \
                re.search(r"\b(tooth|teeth|molar)\b", t):
            return None                     # two services named at once: the model sorts it out
        if not _parse_when(said).empty or _other_slots(said, view, service=False):
            return None
        phrase = svc.unknown_phrase or _service_phrase(said)
        if not all(w in _SERVICE_FILLER or w in phrase.split() for w in words):
            return None
        return _u(raw, src, [Act.ANSWER], service=svc.value, service_phrase=phrase)
    if pending == Goal.ASK_AGE:
        age = _age(t)
        return _u(raw, src, [Act.ANSWER], age=age) if age is not None else None
    if pending == Goal.ASK_CANCEL_REASON:
        conf = match.parse_yes_no(raw)
        if conf == "no" and len(words) <= 3:
            return _u(raw, src, [Act.ANSWER], confirmation="no")
        if conf or len(words) > 10 or re.search(r"\b(book|cancel|reschedule|change|move|don'?t)\b", t):
            return None
        return _u(raw, src, [Act.ANSWER], cancel_reason=raw.strip(" ,.!"))
    return None


def _doctor_only(raw: str, words: list, view: Tier0View, source: str):
    """ "Dr Rao, please" / "a lady doctor" on its own: a doctor preference."""
    doctors = _catalog_part(view, "doctors")
    d = match.match_doctor(raw, doctors)
    gender = match.doctor_gender(raw)
    if not (d.value or d.unknown_phrase or gender):
        return None
    name_words = set()
    for doc in doctors:
        name_words.update(re.findall(r"[a-z]+", doc.name.lower()))
    if d.unknown_phrase:
        name_words.update(re.findall(r"[a-z]+", d.unknown_phrase.lower()))
    if not all(w in _DOCTOR_FILLER or w in name_words for w in words):
        return None
    act = Act.ANSWER if _expect(view) == "choice" else Act.INFO
    return _u(raw, source, [act], doctor=d.value, doctor_phrase=d.unknown_phrase, doctor_gender=gender)


def understand(text: str, view: Tier0View, *, lenient: bool = False) -> Understanding | None:
    """
    What the caller's turn meant, without the model, or None ("ask the model").

    Strict (default): short, unambiguous replies to view.expect (yes / no,
    digits, spelling, a name, one catalog match, a date or time, an offer
    pick) and the global patterns. Anything mixed, long or questioning is
    None. lenient=True is the no-model fallback: it always returns an
    Understanding (source "fallback"), extracting whatever it can find.
    Pure and synchronous; well under 5 ms.
    """
    raw = (text or "").strip()
    if lenient:
        return _lenient(raw, view)
    if not raw:
        return None
    t = _clean(raw)
    words = _tokens(t)
    if not words and not _NON_LATIN_RE.search(raw):
        return None

    emergency = _emergency(t)
    if emergency == Emergency.RED_FLAG:
        return _u(raw, "tier0", [Act.INFO], emergency=Emergency.RED_FLAG)

    g = _globals(t, raw, words, view, "tier0")
    if g is not None:
        return g

    if emergency == Emergency.URGENT:
        # Short and plain only ("I have severe tooth pain"); details go to the model.
        if len(words) <= 12 and not match.looks_like_question(raw) and _parse_when(raw).empty \
                and not _other_slots(raw, view, service=False):
            return _u(raw, "tier0", [Act.INFO], emergency=Emergency.URGENT)
        return None

    if view.pending in (Goal.BOOKED, Goal.ANYTHING_ELSE) and _BOOK_IT_AGAIN_RE.match(t.strip(" .!")):
        return _u(raw, "tier0", [Act.ANSWER], confirmation="yes")     # the booking just made
    if view.pending == Goal.DUPLICATE_CHECK and (_DUP_CHANGE_RE.search(t) or _DUP_ANOTHER_RE.search(t)):
        return None                         # the lenient reading has the another / change rules
    if view.pending == Goal.BOOKED and _changes_just_booked(t, raw, words):
        return None                         # a change to the booking just made: the model, else lenient

    opener = None if _MOVE_APPT_RE.search(t) else _opener(t, view, strict=True)
    if opener:
        intent, service, phrase = opener
        act = Act.ANSWER if view.pending in _INTENT_GOALS else Act.INFO
        conf = "yes" if re.match(r"^(yes|yeah|yep|sure|ok|okay)\b", t) else None
        return _u(raw, "tier0", [act], intent=intent, service=service, service_phrase=phrase, confirmation=conf)

    exp = _expect(view)
    if match.is_fragment(raw, exp):
        return _u(raw, "tier0", [Act.FRAGMENT])
    if match.looks_like_question(raw):
        return None
    if len(words) > STRICT_MAX_WORDS and exp not in ("phone", "spelling"):
        return None

    answer = _strict_answer(t, raw, words, view)
    if answer is not None:
        return answer

    if exp not in ("phone", "spelling", "name", "yes_no"):
        doc = _doctor_only(raw, words, view, "tier0")
        if doc is not None:
            return doc

    if match.is_backchannel(raw) and exp not in ("yes_no", "choice"):
        return _u(raw, "tier0", [Act.BACKCHANNEL])
    return None


# A reason given with the cancel request ("..., I'm travelling that week",
# "...because something came up"); the reason is optional and never asked twice.
_CANCEL_WHY_RE = re.compile(
    r"\b(?:appointment|booking)\b[ ,.;-]*(?:because|as|since)?\s*"
    r"((?:i'?m|i am|i have|i've|i'll|i will|i won't|i can't|i cannot|we're|we are|my|there's|something)\b.+)$",
    re.I)


# A question about a branch (where it is, how to get there, its hours or
# parking) names it without choosing it for the booking.
_ABOUT_A_PLACE = re.compile(
    r"\b(where|address|located|location|directions?|how (?:do|can|would) i (?:get|reach|find)|far|near|"
    r"landmark|parking|park|timings?|hours|open|close|closing|opening)\b")
_SUGGEST_RE = re.compile(r"\b(what do you suggest|what would you suggest|you suggest|you tell me|"
                         r"whatever (?:you have|is free|is available|suits you)|anything is fine|"
                         r"what(?:'s| is) (?:free|available)|i don'?t know)\b")
_MOVE_APPT_RE = re.compile(
    r"\b(change|move|shift)\s+(?:my|the|our|her|his)\s+(?:(?:existing|current|old|booked|upcoming|next|dental|"
    r"cleaning|check-?up)\s+)*(appointment|booking)\b")
# Pleasantries that mention a day without asking for it.
_SMALL_TALK_RE = re.compile(
    r"\b(hope you'?re|hope you are|you must be|how are you|how'?s your|how is your|have a (good|nice|great)|"
    r"nice weather|lovely weather|hot|rainy|busy) [^.?!]{0,25}\b(today|tonight|this morning|this evening)\b|"
    r"\b(today|this morning)\b [^.?!]{0,15}\b(so busy|very busy|busy day|hot|rainy)\b")
# A booking asked for before that question ("Can I get a check-up on Saturday, and where...").
_ASKS_TO_BOOK = re.compile(r"\b(book|appointment|come in|(can|could|may) (i|we) (get|have|come)|"
                           r"i'?d like|i want|i need)\b")


_MOVE_IT_RE = re.compile(r"\b(?:move|shift|change|make|push|bring) (?:it|that|this)\b|\binstead\b")
# A change asked for without a new time yet ("change that one", "I want to move it").
_CHANGE_IT_RE = re.compile(r"\b(?:change|move|shift|reschedule|postpone|prepone) (?:it|that|this|that one|"
                           r"this one|the time|the day|the date|the appointment|my appointment)\b")


def _changes_just_booked(t: str, raw: str, words: list) -> bool:
    """Right after "you're booked": a change to that booking, or a bare day / time on its own."""
    when = _parse_when(raw, expecting="time")
    if when.empty:
        return False
    return bool(_MOVE_IT_RE.search(t)) or (len(words) <= 4 and _when_residue_ok(t))


def _lenient(raw: str, view: Tier0View) -> Understanding:
    """
    The no-model fallback: everything findable, catalog matches anywhere,
    dates and digits, yes / no, question detection. Never None, so the turn
    always continues on pre-written lines.
    """
    src = "fallback"
    t = _clean(raw)
    words = _tokens(t)
    if not raw or (not words and not _NON_LATIN_RE.search(raw)):
        return _u(raw, src, [Act.UNCLEAR])
    exp = _expect(view)
    pending = view.pending

    emergency = _emergency(t)
    if emergency == Emergency.RED_FLAG:
        return _u(raw, src, [Act.INFO], emergency=Emergency.RED_FLAG)
    g = _globals(t, raw, words, view, src)
    if g is not None:
        g.emergency = emergency
        return g

    u = _u(raw, src, [], emergency=emergency)
    filled_expected = False

    # "move my existing appointment" is a reschedule even though it says "appointment" (sim 6 Oct).
    moving = bool(_MOVE_APPT_RE.search(t))
    opener = None if moving else _opener(t, view, strict=False)
    if opener:
        u.intent, u.service, u.service_phrase = opener
    elif re.search(r"\b(cancel|call off)\b", t) and (_APPT_NOUN_RE.search(t) or pending in _BARE_CANCEL_GOALS):
        u.intent = Intent.CANCEL
        why = _CANCEL_WHY_RE.search(raw)
        if why:                      # "I want to cancel my appointment, I'm travelling that week."
            u.cancel_reason = why.group(1).strip(" ,.!")
    elif re.search(r"\b(reschedule|postpone|prepone)\b", t) or moving or \
            (_APPT_NOUN_RE.search(t) and re.search(r"\b(change|move|shift)\s+(?:the|it to another)\s+(day|date|time)\b", t)):
        u.intent = Intent.RESCHEDULE         # "I have an appointment but I need to change the day"
    elif re.search(r"\b(when is|what time is|check|confirm)\s+(?:my|the)\s+(appointment|booking)\b", t):
        u.intent = Intent.CHECK
    elif re.search(r"\b(book|appointment)\b", t) and pending in _INTENT_GOALS:
        u.intent = Intent.BOOK

    question = match.looks_like_question(raw)
    if question and match.asks_nothing(raw):
        question = False                      # "I mean, what what is... Okay.": nothing asked (8 Oct)
    if question and exp == "spelling" and match.join_spelled(raw):
        question = False                      # "s r I r a n j a n I?": the "?" is the voice going up
    if question:
        u.acts.append(Act.QUESTION.value)
        u.question = raw.strip()

    # yes / no only where Emma asked one, or in a very short reply.
    if exp in ("yes_no", "choice") or len(words) <= 3:
        u.confirmation = match.parse_yes_no(raw)
        if exp == "yes_no" and u.confirmation:
            filled_expected = True

    if pending == Goal.DUPLICATE_CHECK:
        # "Did you want another one, or to change that one?" (8 Oct: neither answer was
        # understood, six times round, then a transfer). "change" is not a "no".
        if _DUP_CHANGE_RE.search(t):
            u.intent, u.confirmation, filled_expected = Intent.RESCHEDULE, None, True
        elif _DUP_ANOTHER_RE.search(t):
            u.intent, u.confirmation, filled_expected = Intent.BOOK, "yes", True
    elif pending in (Goal.BOOKED, Goal.ANYTHING_ELSE) and _BOOK_IT_AGAIN_RE.match(t.strip(" .!")):
        # "Okay. Book it." straight after "you're booked": the booking just made, not a new one.
        u.intent, u.confirmation = None, "yes"
    elif pending in (Goal.BOOKED, Goal.ANYTHING_ELSE) and u.intent in (None, Intent.NONE) \
            and _CHANGE_IT_RE.search(t):
        u.intent, u.confirmation = Intent.RESCHEDULE, None   # "Yeah. Change that one." after booking
    elif pending in (Goal.BOOKED, Goal.ANYTHING_ELSE) and u.intent in (None, Intent.NONE, Intent.BOOK) \
            and _DUP_ANOTHER_RE.search(t):
        u.intent, u.confirmation = Intent.BOOK, None         # "I want another one" after booking
    elif pending == Goal.BOOKED and u.intent in (None, Intent.NONE) and _changes_just_booked(t, raw, words):
        u.intent = Intent.RESCHEDULE                  # "Can you move it to 7?", or just "Seven"
        if not (u.date_phrase or u.time_phrase):
            u.time_phrase = raw.strip(" .!?")

    # Digits: any amount while a number is expected; elsewhere only a whole number.
    run = match.extract_digits(raw)
    said = _NUMBER_IS_RE.search(raw)
    if said and not run.complete:
        # "I'm Vihaan, my number is 90000 00006, and I'd like Tuesday at 1:30":
        # read only the words after "my number is", so the time isn't taken as digits.
        run = match.extract_digits(said.group(1))
    if exp == "phone" and not run.complete and (_NOT_A_NUMBER_RE.search(t) or _DATE_TIME_WORDS_RE.search(t)):
        # 8 Oct: "On Saturday, October 10 at 03:30PM" and "my phone number ends at zero zero
        # three" were added to the number being collected -> "more digits than a phone number".
        run = match.DigitRun("", complete=False)
    if (exp == "phone" or pending in (Goal.CONFIRM_PHONE, Goal.ASK_PHONE, Goal.PHONE_MORE)) \
            and _NO_NUMBER_RE.search(t) and not run.digits:
        u.acts.append(Act.NON_ANSWER.value)           # "I don't know the number, I only know the name"
        u.confirmation = None                         # "don't know" is not a "no"
    if run.digits and ((exp == "phone" and (len(run.digits) >= 2 or view.digits_so_far)) or run.complete):
        u.phone_digits = run.digits
        if exp == "phone":
            filled_expected = True
        if exp == "yes_no" and run.complete:
            u.confirmation = None           # a corrected number, not a yes

    # Names.
    if exp in ("name", "spelling"):
        spelled = match.join_spelled(raw)
        if spelled:
            u.name_spelled = spelled
            filled_expected = True
        else:
            name = match.clean_name(raw)
            if name and len(name.split()) <= 4:
                for k, v in _name_fields(name, view).items():
                    setattr(u, k, v)
                filled_expected = True
    if not (u.name or u.patient_name or u.name_spelled):
        # Unasked, only a clear introduction is a name: "my name is" anywhere, but "this is" /
        # "I'm" only opening the first line ("Hi, this is Priya"). 8 Oct: "Yeah. I'm talking..."
        # became "change the name to Talking I'M?", "This is another one" -> "Another One".
        m = re.search(r"\b(?:my name is|my name's|myself|call me)\s+([a-z][a-z' -]*)", t)
        if not m and exp in ("name", "spelling"):
            # Asked for a name: "It's for my son, Saanvi Patel. I'm Pooja Shenoy." (sim 7)
            m = re.search(r"\b(?:this is|i am|i'm)\s+([a-z][a-z' -]*)", t)
        if not m and pending in (Goal.GREET, Goal.ASK_INTENT):
            m = re.match(r"^(?:(?:hi|hello|hey|yes|yeah|okay|ok|good morning|good afternoon|good evening)"
                         r"[ ,.!]+)*(?:this is|i am|i'm)\s+([a-z][a-z' -]*)", t)
        if m:
            cand = " ".join(m.group(1).split()[:3])
            name = match.clean_name(cand)
            while name is None and len(cand.split()) > 1:
                cand = " ".join(cand.split()[:-1])
                name = match.clean_name(cand)
            if name:
                for k, v in _name_fields(name, view).items():
                    setattr(u, k, v)

    # Catalog matches anywhere.
    asked = pending in (Goal.ASK_SERVICE, Goal.CLARIFY_SERVICE)
    if u.service is None and not u.service_phrase:
        svc = match.match_service(raw, _catalog_part(view, "services"), asked=asked)
        if svc.value:
            u.service = svc.value
            u.service_phrase = _service_phrase(raw) if len(words) <= 4 else None
        elif svc.unknown_phrase:
            u.service_phrase = svc.unknown_phrase
        elif svc.options and asked:
            u.service_phrase = _service_phrase(raw)
    if asked and (u.service or u.service_phrase):
        filled_expected = True
    br = match.match_branch(raw, _catalog_part(view, "branches"))
    if br.value and question and _ABOUT_A_PLACE.search(t):
        pass             # "Where is your Jayanagar branch?" asks about it; it doesn't choose it (7 Oct rehearsal)
    elif br.value:
        u.branch = br.value
        filled_expected = filled_expected or pending == Goal.ASK_BRANCH
    elif pending == Goal.ASK_BRANCH and _any_branch(t, view):
        u.branch_any = True
        filled_expected = True
    d = match.match_doctor(raw, _catalog_part(view, "doctors"))
    u.doctor, u.doctor_phrase = d.value, d.unknown_phrase
    u.doctor_gender = match.doctor_gender(raw)
    if u.intent is None and pending in _INTENT_GOALS and (u.service or u.service_phrase) \
            and re.search(r"\b(like|want|need|book|get|wanted)\b", t):
        u.intent = Intent.BOOK               # "I'd like a dental implant on Tuesday"

    # Offer and option picks.
    if exp == "choice" and not filled_expected:
        pick = (_offer_pick(t, raw, view, src) if pending in _OFFER_GOALS or (view.offered and not view.options)
                else _option_pick(t, raw, view, src))
        if pick is not None:
            for k in ("choice_index", "reject_options", "service", "service_phrase", "branch", "branch_any",
                      "date_phrase", "time_phrase"):
                v = getattr(pick, k)
                if v not in (None, False, ""):
                    setattr(u, k, v)
            if pick.confirmation:
                u.confirmation = pick.confirmation
            filled_expected = True

    # Dates and times.
    # After "Sorry, go on?" the question that was open is still the appointment
    # date when the caller is moving one (drill, 6 Oct: "It's on October 13th,
    # and" ... "I'd like to move it to the 20th" verified against the 20th).
    resumed = pending == Goal.GO_ON and view.intent == Intent.RESCHEDULE
    moved = _MOVE_TO.match(raw) if pending in (Goal.ASK_APPT_DATE, Goal.VERIFY_FAILED) or resumed else None
    if moved and not (u.date_phrase or u.time_phrase):
        # "It's on Monday, and I'd like to move it to Friday": the first date is
        # the appointment's (verification), the second the new one.
        old = _parse_when(moved.group("old"), expecting="date")
        new = _parse_when(moved.group("new"))
        if old.date is not None and not new.empty:
            u.appt_date_phrase = _split_when(moved.group("old"), old)[0]
            u.date_phrase, u.time_phrase = _split_when(moved.group("new"), new)
            filled_expected = True
    # "What are your timings on Saturday?" asks about the clinic; Saturday isn't
    # a booking day (6 Oct typed-backup test, model cold: "Okay, Saturday the 10th").
    hours_question = bool(question) and bool(_ABOUT_A_PLACE.search(t)) and exp not in ("date", "time")
    # "Hope you're not too busy today": small talk, not a day to book (sim 6 Oct: "Okay, today instead").
    hours_question = hours_question or (bool(_SMALL_TALK_RE.search(t)) and exp not in ("date", "time"))
    when_text = raw
    if hours_question:
        # "Can I get a check-up on Saturday, and where is your Jayanagar branch?":
        # the day belongs to the request said before the question.
        place = _ABOUT_A_PLACE.search(t)
        if place is not None and _ASKS_TO_BOOK.search(t[:place.start()]):
            when_text, hours_question = t[:place.start()], False
    if not (u.date_phrase or u.time_phrase or u.appt_date_phrase) and not (exp == "phone" and u.phone_digits) \
            and not hours_question:
        when = _parse_when(when_text, expecting=exp if exp in ("date", "time") else None)
        if not when.empty:
            date_phrase, time_phrase = _split_when(when_text, when)
            if pending == Goal.ASK_APPT_DATE:
                u.appt_date_phrase = date_phrase
            else:
                u.date_phrase, u.time_phrase = date_phrase, time_phrase
            filled_expected = filled_expected or exp in ("date", "time")
        elif exp == "date" and pending != Goal.ASK_APPT_DATE and _SUGGEST_RE.search(t):
            # "I don't know, what do you suggest?" to "When would suit you?": the earliest times.
            u.date_phrase = "earliest"
            filled_expected = True

    if pending == Goal.ASK_AGE:
        age = _age(t)
        if age is not None:
            u.age = age
            filled_expected = True
    if pending == Goal.ASK_CANCEL_REASON and not question and not u.confirmation and len(words) <= 15:
        u.cancel_reason = raw.strip(" ,.!")
        filled_expected = True

    m = _FOR_SOMEONE_RE.search(t)
    if m:
        u.for_someone_else, u.relation = True, m.group(1)
    if _CORRECTION_RE.search(t):
        u.correction = True

    if filled_expected:
        u.acts.insert(0, Act.ANSWER.value)
    elif u.carries_details or u.intent is not None:
        u.acts.insert(0, Act.INFO.value)
    if not u.acts:
        if match.is_fragment(raw, exp):
            u.acts.append(Act.FRAGMENT.value)
        elif match.is_backchannel(raw):
            u.acts.append(Act.BACKCHANNEL.value)
        else:
            u.acts.append(Act.UNCLEAR.value)
    return u
