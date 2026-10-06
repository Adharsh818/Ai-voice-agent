import asyncio
import json
import re
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
import config
import backend_actions
import llm
import logredact
import phones
import phrases
import tier0
from dialogue.context import CallContext

logger = logging.getLogger(__name__)


# ==========================================
# CLINIC FACTS & NLU PROMPT
# ==========================================

def _load_clinic_facts():
    """
    Render only verified facts from clinic_facts.json for the model's context.
    Unverified entries are left out entirely, so the model has nothing to repeat
    and falls back to the staff-escalation line instead of inventing an answer.
    Returns (facts_text, escalation_line).
    """
    try:
        with open(config.CLINIC_FACTS_PATH, encoding="utf-8") as fh:
            facts = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.warning("clinic_facts.json unavailable (%s); using hours and services only", exc)
        facts = {}

    lines = [f"- Clinic: {facts.get('clinic_name', config.CLINIC_NAME)}"]
    for key, label in (("hours", "Hours"), ("pricing_policy", "Pricing"),
                       ("insurance_policy", "Insurance")):
        entry = facts.get(key) or {}
        if entry.get("verified") and entry.get("text"):
            lines.append(f"- {label}: {entry['text']}")
    services = facts.get("services") or {}
    lines.append("- Services: " + ", ".join(
        services.get("list", []) if services.get("verified") else config.ALLOWED_SERVICES
    ))
    for entry in facts.get("facts", []):
        if entry.get("verified") and entry.get("text"):
            lines.append(f"- {entry.get('topic', entry.get('id'))}: {entry['text']}")
    for branch in facts.get("branches", []):
        if branch.get("verified"):
            detail = branch.get("address", "")
            if branch.get("parking"):
                detail += f". Parking: {branch['parking']}"
            if branch.get("phone") and branch["phone"] != "PLACEHOLDER":
                detail += f", phone {branch['phone']}"
            lines.append(f"- Branch {branch['name']}: {detail}")
    escalation = ((facts.get("escalation") or {}).get("text")
                  or "The doctor can go through that with you at your visit.")
    return "\n".join(lines), escalation


CLINIC_FACTS, ESCALATION_LINE = _load_clinic_facts()

# One merged request per Tier-1 turn: slot extraction AND, when the caller asked
# something off-topic, a one-sentence grounded answer. Python decides every
# transition; the model's `answer` is only ever spoken text.
EMMA_NLU_SYSTEM_PROMPT = """You are the language-understanding layer for Emma, the receptionist at Pearl Dental Clinic in Bengaluru.
Read the caller's latest words and output ONLY a JSON object, no markdown.

Keys (leave a key out entirely when it has no value; never output nulls, it only slows the reply):
- patient_name: the caller's name if they state it in THIS response.
- phone_number: a 10-digit mobile number stated in THIS response, digits only (convert spoken words such as "double nine" to digits).
- dental_service: the dental service they ask for.
- appointment_date: the date they prefer, as they said it (e.g. "next Monday", "25th August", "tomorrow").
- appointment_time: the time they prefer, as they said it (e.g. "5 PM", "10:30 AM", "evening").
- confirmation: "yes" if they agreed with or confirmed Emma's last question, "no" if they disagreed or refused, else null.
- user_query: if they asked a question that is not itself booking information (hours, location, price, services...), their question in a few words, else null.
- answer: ONLY when user_query is set: one short spoken sentence (under 25 words) answering it strictly from CLINIC FACTS below. If the facts do not cover it, answer exactly: "@ESCALATION@". Never invent prices, addresses, doctors, insurance terms or medical advice. Do not ask a question in `answer`; Emma continues with her own next question. Sound like a friendly receptionist and never bring up being an AI, a bot or automated. The one exception: if the caller sincerely asks whether they are talking to a real person or a bot, set user_query and answer exactly "@HONEST@" (never claim to be human).

CLINIC FACTS:
@FACTS@
""".replace("@ESCALATION@", ESCALATION_LINE).replace("@FACTS@", CLINIC_FACTS).replace("@HONEST@", config.HONEST_LINE)


# ==========================================
# CONVERSATION STATE MACHINE
# ==========================================
class SessionState:
    def __init__(self):
        self.step = 1  # 1: Greeting, 2: Name, 3: Purpose, 4: Phone, 5: Service, 6: Location, 7: Date, 8: Time, 9: Recap, 10: Booking/Alternatives, 11: Confirmation, 12: Closed
        self.greeting_spoken = False
        
        self.name = ""
        self.name_confirmed = False
        
        self.phone = ""
        self.phone_confirmed = False
        
        self.purpose = ""
        
        self.service = ""
        self.service_confirmed = False
        
        self.location_confirmed = False
        
        self.date_str = ""  # YYYY-MM-DD
        self.date_confirmed = False
        
        self.time_str = ""  # II:MM PM
        self.time_confirmed = False
        
        self.recap_confirmed = False
        self.booking_confirmed = False
        self.closed_conversation = False
        
        # Temp variables for current validation step
        self.temp_name = ""
        self.temp_phone = ""
        self.temp_service = ""
        self.temp_date = ""
        self.temp_time = ""
        
        # Retry counters: how many times we've asked to confirm a specific field
        self.name_confirm_attempts = 0
        # How many times a spoken name was rejected as not looking like a name.
        # After two misses the keyword screen is dropped, so a caller whose real
        # name trips it is never stuck being asked forever.
        self.name_capture_attempts = 0
        self.phone_confirm_attempts = 0
        self.service_confirm_attempts = 0
        self.date_confirm_attempts = 0
        self.time_confirm_attempts = 0
        
        # Alternative slots offering
        self.offering_alternatives = False
        self.alternative_slots = []

        # Conversation history for LLM context (last 20 turns)
        self.history: list[dict] = []
        
    def reset(self):
        self.__init__()

    def to_dict(self):
        return {
            "step": self.step,
            "greeting_spoken": self.greeting_spoken,
            "name": self.name,
            "name_confirmed": self.name_confirmed,
            "phone": self.phone,
            "phone_confirmed": self.phone_confirmed,
            "purpose": self.purpose,
            "service": self.service,
            "service_confirmed": self.service_confirmed,
            "location_confirmed": self.location_confirmed,
            "date_str": self.date_str,
            "date_confirmed": self.date_confirmed,
            "time_str": self.time_str,
            "time_confirmed": self.time_confirmed,
            "recap_confirmed": self.recap_confirmed,
            "booking_confirmed": self.booking_confirmed,
            "closed_conversation": self.closed_conversation,
            "temp_name": self.temp_name,
            "temp_phone": self.temp_phone,
            "temp_service": self.temp_service,
            "temp_date": self.temp_date,
            "temp_time": self.temp_time,
            "offering_alternatives": self.offering_alternatives,
            "alternative_slots": self.alternative_slots,
            "name_confirm_attempts": self.name_confirm_attempts,
            "phone_confirm_attempts": self.phone_confirm_attempts,
        }


# ==========================================
# LLM NLU EXTRACTION
# ==========================================
# A few idioms are affirmative even though they contain a negative word. They are
# matched first and win outright, so "no problem" is never heard as a refusal.
_AFFIRMATIVE_IDIOMS = (
    "no problem", "no worries", "not a problem", "no issue", "no doubt",
    "why not", "can't wait", "cant wait",
)

_NEGATIVE_RE = re.compile(
    r"\b(no|nope|nah|not|none|neither|nothing|isn'?t|aren'?t|wasn'?t|don'?t|doesn'?t|"
    r"didn'?t|won'?t|can'?t|cannot|wrong|incorrect|mistake|mistaken|error|cancel|"
    r"change|different|never ?mind)\b"
)

_AFFIRMATIVE_RE = re.compile(
    r"\b(yes|yeah|yep|yup|yea|sure|correct|right|affirmative|ok|okay|exactly|"
    r"absolutely|definitely|certainly|of course|confirm(?:s|ed)?|go ahead|proceed|"
    r"sounds good|looks good|perfect|great|fine|works?|do that|please do|cool|"
    r"alright|all right|that's it|thats it)\b"
)


def _parse_confirmation(user_text):
    """
    Classify an utterance as "yes", "no", or None when it is genuinely unclear.

    Negation is tested BEFORE affirmation. The affirmative vocabulary is full of
    words that also appear inside rejections — "that's not *right*", "that is not
    *correct*", "*definitely* not" — so checking yes first turns explicit refusals
    into consent. Matching is word-bounded, which additionally stops "right" from
    firing inside "alright" and "ok" from firing inside "book".
    """
    text = (user_text or "").lower().strip().replace("’", "'")
    if not text:
        return None
    if any(idiom in text for idiom in _AFFIRMATIVE_IDIOMS):
        # The idiom only settles the turn if nothing else in the sentence is
        # negative: "no problem" is consent, but "no problem with the name, the
        # date is wrong" is a mixed signal and must not be read as consent.
        rest = text
        for idiom in _AFFIRMATIVE_IDIOMS:
            rest = rest.replace(idiom, " ")
        return "yes" if not _NEGATIVE_RE.search(rest) else None
    if _NEGATIVE_RE.search(text):
        return "no"
    if _AFFIRMATIVE_RE.search(text):
        return "yes"
    return None


# Words that are never part of a spoken name, used to reject utterances that the
# name step would otherwise adopt wholesale (dates, services, pleasantries).
_NON_NAME_WORDS = {
    "book", "booking", "appointment", "appointments", "schedule", "scheduling",
    "dentist", "dental", "clinic", "doctor", "checkup", "check-up", "cleaning",
    "filling", "canal", "root", "braces", "invisalign", "extraction",
    "consultation", "pediatric", "today", "tomorrow", "yesterday", "monday",
    "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "next",
    "week", "weekend", "morning", "afternoon", "evening", "night", "noon",
    "am", "pm", "oclock", "please", "want", "need", "hello", "thanks", "thank",
    "sorry", "what", "when", "where", "how", "help", "pain", "tooth", "teeth",
    "toothache", "hurts", "hurting",
}


_GREETING_ONLY_RE = re.compile(
    r"^(hi|hello|hey|hii|hallo|hullo|good (morning|afternoon|evening)|namaste)"
    r"( there| emma| ma'?am)?([ ,.!?]+(hi|hello|hey))*$"
)


def _looks_like_name(text, strict=False):
    """
    Reject utterances that clearly are not a spoken name.

    The structural checks always apply. `strict` adds a keyword screen and is used
    when falling back to the caller's raw words; an explicitly NLU-extracted name
    only needs the structural checks. The keyword screen has false positives on
    real names ("Sunday Adebayo"), so callers must be able to get past it — see
    `name_capture_attempts` in the name step.
    """
    text = (text or "").strip()
    if len(text) < 2:
        return False
    if any(ch.isdigit() for ch in text):
        return False
    words = text.split()
    # Long South Indian names run to five or six parts, so the cap is generous.
    if not words or len(words) > 6:
        return False
    if len(re.sub(r"[^a-z]", "", text.lower())) < 2:
        return False
    if not strict:
        return True
    # "Yes" answered to "may I have your name?" is an acknowledgement, not a name.
    if len(words) <= 2 and _parse_confirmation(text):
        return False
    return not any(re.sub(r"[^a-z-]", "", w.lower()) in _NON_NAME_WORDS for w in words)


def _basic_entity_fallback(user_text: str) -> dict:
    """Return only cheap, deterministic hints when the NLU provider is down."""
    confirmation = _parse_confirmation(user_text)

    phone_match = re.search(r"(?:\+?91[\s-]?)?([6-9](?:[\s-]?\d){9})\b", user_text or "")
    return {
        "patient_name": None,
        "phone_number": phone_match.group(0) if phone_match else None,
        "dental_service": None,
        "appointment_date": None,
        "appointment_time": None,
        "confirmation": confirmation,
        "user_query": None,
    }


def _recent_history(s, turns=6):
    """
    Render the last few turns as plain dialogue for LLM context.

    `s.history` is appended to on every turn but was previously never read, so
    the NLU had no way to resolve references like "the second one" or "same time
    as before".  Returns "" when there is nothing yet.
    """
    recent = getattr(s, "history", None) or []
    lines = []
    for turn in recent[-turns:]:
        who = "Patient" if turn.get("role") == "user" else "Emma"
        content = (turn.get("content") or "").strip()
        if content:
            lines.append(f"{who}: {content}")
    return "\n".join(lines)


async def async_extract_entities_with_llm(user_text, s):
    """
    Tier-1 NLU: one bounded Gemini request extracting booking slots, an
    off-topic question, and a grounded one-sentence answer to it.
    Falls back to deterministic hints when the model is slow, down or disabled.
    """
    if s is None:
        raise ValueError("async_extract_entities_with_llm requires a per-call SessionState")
    if not user_text or not user_text.strip():
        return {}

    state_desc = (
        f"Step: {s.step}\n"
        f"Name: {s.name or s.temp_name or 'None'}\n"
        f"Phone: {s.phone or s.temp_phone or 'None'}\n"
        f"Service: {s.service or s.temp_service or 'None'}\n"
        f"Date: {s.date_str or s.temp_date or 'None'}\n"
        f"Time: {s.time_str or s.temp_time or 'None'}"
    )
    history_block = _recent_history(s)
    context_section = (
        "Recent conversation (context only; do NOT extract from these lines, use them "
        "only to resolve references like \"the second one\" or \"same time\"):\n"
        f"{history_block}\n\n"
        if history_block else ""
    )
    prompt = (
        f"Booking session state:\n{state_desc}\n\n"
        f"{context_section}"
        f"Caller's latest words: \"{user_text}\""
    )

    data = await llm.get_nlu().generate_json(prompt, EMMA_NLU_SYSTEM_PROMPT)
    if data is None:
        return _basic_entity_fallback(user_text)
    for k in ("patient_name", "phone_number", "dental_service", "appointment_date",
              "appointment_time", "confirmation", "user_query", "answer"):
        data.setdefault(k, None)
    if data["confirmation"] not in ("yes", "no"):
        data["confirmation"] = None
    if data["phone_number"] is not None:
        data["phone_number"] = str(data["phone_number"])
    return data


def _remember(s, user_text, reply):
    if user_text:
        s.history.append({"role": "user", "content": user_text})
    s.history.append({"role": "assistant", "content": reply})
    if len(s.history) > 20:
        s.history = s.history[-20:]


async def generate_emma_response(directive: str, s, user_text: str = "",
                                 user_query: str = None, answer: str = None) -> str:
    """
    Emma's spoken reply for this turn: the state machine's directive, preceded
    by a one-sentence grounded answer when the caller asked something off-topic.
    The answer comes from the same NLU request, so this makes no second model
    call. Without an answer (model down or silent) Emma simply carries on.
    """
    if not directive:
        directive = "Could you please repeat that?"
    reply = directive
    if user_query and answer and answer.strip():
        reply = f"{answer.strip()} {directive}"
    _remember(s, user_text, reply)
    # Mask before truncating: a cut read-back ("9 8 7 6") is too short to be caught later.
    logger.info("Emma: %s", logredact.mask_phones(reply)[:120])
    return reply


# ==========================================
# DIALOGUE MANAGEMENT - SHARED STATE MACHINE
# ==========================================

def _confirmation_message(service: str, formatted_date: str, time_str: str) -> str:
    """
    Booking confirmation text. Confirmations are spoken only (decision Q11): no
    email is collected and no invitation is sent, so none is promised.
    """
    return (
        f"Done, you're booked for a {service} on {formatted_date} at {time_str}, "
        f"at our {config.DEFAULT_BRANCH} branch. Anything else I can help with?"
    )


def _match_service(spoken):
    """
    Map a spoken service phrase onto one of the clinic's allowed services.

    Matching is bidirectional so both "root canal" and "I need a root canal
    treatment" resolve to "Root Canal Treatment".  Very short fragments are
    rejected: a stray "a" would otherwise substring-match the first service.
    """
    spoken = (spoken or "").strip().lower()
    if len(spoken) < 4:
        return None
    for allowed in config.ALLOWED_SERVICES:
        low = allowed.lower()
        if low in spoken or spoken in low:
            return allowed
    return None


# Which conversation step owns each bookable slot.  The absorber below only
# pre-fills slots owned by LATER steps: the active step must collect and confirm
# its own slot, and earlier steps have already been confirmed.
_SLOT_STEP = {"name": 2, "phone": 4, "service": 5, "date": 7, "time": 8}


def _absorb_volunteered_slots(entities, s):
    """
    Pre-fill slots the caller volunteered before being asked for them.

    A caller who opens with "I'd like a root canal next Monday at 5" has already
    supplied three slots.  Emma still confirms every field individually, but she
    must never re-ask for something she was just told.  Confirmed slots are left
    untouched, and nothing here consumes this turn's yes/no.
    """
    def pending(slot, confirmed):
        return _SLOT_STEP[slot] > s.step and not confirmed

    if pending("name", s.name_confirmed) and not s.temp_name and entities.get("patient_name"):
        s.temp_name = entities["patient_name"].title()

    if pending("phone", s.phone_confirmed) and not s.temp_phone and entities.get("phone_number"):
        is_valid, cleaned_phone = backend_actions.validate_phone(entities["phone_number"])
        if is_valid:
            s.temp_phone = cleaned_phone

    if pending("service", s.service_confirmed) and not s.temp_service and entities.get("dental_service"):
        matched = _match_service(entities["dental_service"])
        if matched:
            s.temp_service = matched

    if pending("date", s.date_confirmed) and not s.temp_date and entities.get("appointment_date"):
        _, resolved_date, _err = backend_actions.resolve_date(entities["appointment_date"])
        if resolved_date:
            s.temp_date = resolved_date

    if pending("time", s.time_confirmed) and not s.temp_time and entities.get("appointment_time"):
        _, resolved_time, _err = backend_actions.resolve_time(entities["appointment_time"])
        if resolved_time:
            s.temp_time = resolved_time


# --- Slot prompts -----------------------------------------------------------
# Each returns the confirmation question when the slot was already volunteered
# (see _absorb_volunteered_slots), otherwise the open question.

def _name_prompt(s, spell=False):
    """Confirm the candidate name.  Spelled out letter-by-letter only on retry."""
    if spell:
        spelled = " ".join(list(s.temp_name.upper()))
        return f"Sorry, let me just check the spelling. {spelled}. Is that right?"
    return f"{s.temp_name}, did I get that right?"


def _purpose_prompt():
    return "Thanks! And what can I do for you today?"


def _spoken_phone(number):
    """Read back in two groups, the way people check numbers: '9 8 7 6 5, 4 3 2 1 0'."""
    return phones.spoken(phones.to_e164(number) or number)


def _phone_prompt(s, lead="Great."):
    if s.temp_phone:
        return f"{lead} So that's {_spoken_phone(s.temp_phone)}, right?"
    return f"{lead} And what's the best mobile number to reach you on?"


def _after_name(s):
    """
    Leave the name step.  When the caller already volunteered a service, date or
    time — or said at the greeting that they wanted to book — we know why they
    called, so skip the purpose question instead of asking something they just
    answered.
    """
    if s.purpose == "booking" or s.temp_service or s.temp_date or s.temp_time:
        s.purpose = "booking"
        s.step = 4
        return _phone_prompt(s)
    s.step = 3
    return _purpose_prompt()


def _service_prompt(s, lead="Thanks."):
    if s.temp_service:
        return f"{lead} And that's for a {s.temp_service}, is that right?"
    return f"{lead} And what's the visit for?"


def _date_prompt(s):
    if s.temp_date:
        formatted = datetime.strptime(s.temp_date, "%Y-%m-%d").strftime("%A, %d %B")
        return f"So {formatted}, is that right?"
    return "What day would suit you?"


def _time_prompt(s):
    if s.temp_time:
        return f"At {s.temp_time}, is that right?"
    return "What time works best for you?"


def _current_question(s):
    """
    Re-ask whatever Emma is waiting for, without changing any state. Used after
    answering an off-topic question so the booking picks up where it left off.
    """
    if s.step == 1:
        return "Would you like to book a visit?"
    if s.step == 2:
        return _name_prompt(s) if s.temp_name else "May I have your full name, please?"
    if s.step == 3:
        return "What can I do for you today?"
    if s.step == 4:
        return _phone_prompt(s, lead="").strip()
    if s.step == 5:
        return _service_prompt(s, lead="").strip()
    if s.step == 6:
        return f"Is our {config.DEFAULT_BRANCH} branch okay for you?"
    if s.step == 7:
        return _date_prompt(s)
    if s.step == 8:
        return _time_prompt(s)
    if s.step == 9:
        return "Shall I go ahead and book it?"
    if s.step == 10 and s.alternative_slots:
        return "Would " + " or ".join(s.alternative_slots[:2]) + " work for you?"
    if s.step == 11:
        return "Is there anything else I can help you with?"
    return "How can I help you?"


def expects_information(s, text: str) -> bool:
    """
    True when the caller has just given Emma something a receptionist would
    write down: an answer to her question for a name, number, service, date or
    time, a first description of what they need, or a correction at the recap.
    Not a bare yes/no, and not a question. The call session then plays a short
    burst of typing before Emma answers (docs/NORTH_STAR.md, decision R6).
    An R2 CallContext is answered by dialogue.policy.expects_information.
    """
    if getattr(s, "is_recovery", False):
        return False                      # Emma searches with "let me have a look" instead
    if isinstance(s, CallContext):
        from dialogue import policy
        return policy.expects_information(s, text)
    words = re.findall(r"[a-z0-9']+", (text or "").lower())
    if not words or tier0.looks_like_question(text):
        return False
    confirmation = _parse_confirmation(text)
    if len(words) <= 3 and confirmation:
        return False
    awaiting = ((s.step == 2 and not s.temp_name) or s.step == 3 or (s.step == 4 and not s.temp_phone)
                or (s.step == 5 and not s.temp_service) or (s.step == 7 and not s.temp_date)
                or (s.step == 8 and not s.temp_time))
    if awaiting:
        return True
    if s.step == 1:
        return len(words) >= 4                          # explaining what they need
    return s.step == 9 and confirmation == "no" and len(words) >= 4   # "no, the number is ..."


def _is_pure_question(entities):
    """An off-topic question that carries no booking information or yes/no."""
    if not entities.get("user_query"):
        return False
    slots = ("patient_name", "phone_number", "dental_service", "appointment_date",
             "appointment_time", "confirmation")
    return not any(entities.get(k) for k in slots)


def _recap_message(s, updated=False):
    """
    Read the booking back to the caller.  The name is spoken normally — spelling
    it out letter-by-letter on every recap is slow and robotic on a voice call —
    but the phone number stays digit-by-digit, which is how people verify numbers.
    """
    formatted_date = datetime.strptime(s.date_str, "%Y-%m-%d").strftime("%A the %d %B")
    lead = "Okay, so now that's" if updated else "So that's"
    return (
        f"{lead} a {s.service} for {s.name} on {formatted_date} at {s.time_str}, "
        f"at our {config.DEFAULT_BRANCH} branch, and your number is {_spoken_phone(s.phone)}. "
        "Shall I book it?"
    )


def _resolve_confirmation(user_text, entities):
    """
    The NLU's reading wins when it has one, since it sees the whole utterance in
    context. The one exception is an outright disagreement: if the NLU heard
    "yes" while the deterministic parser heard an explicit refusal, Emma treats
    the turn as unclear and re-asks rather than picking a side. Guessing "yes"
    here is what let a rejected recap commit a booking.
    """
    raw_conf = entities.get("confirmation")
    text_conf = _parse_confirmation(user_text)
    if raw_conf and text_conf and raw_conf != text_conf:
        logger.info("Confirmation conflict (nlu=%s text=%s); re-asking", raw_conf, text_conf)
        return None
    return raw_conf or text_conf


def _handle_conversation_step(user_text, entities, s):
    """
    Core conversation state machine. Takes user text and pre-extracted entities.
    Uses the per-call session state object `s`.
    Returns Emma's next verbal response string.
    """
    user_lower = user_text.lower().strip() if user_text else ""

    conf = _resolve_confirmation(user_text, entities)

    # Handle corrections at Step 9 Recap
    if s.step == 9 and conf == "no":
        has_new_val = False
        if entities.get("patient_name"):
            s.name = entities["patient_name"]
            s.temp_name = s.name
            has_new_val = True
        if entities.get("phone_number"):
            ok, clean_p = backend_actions.validate_phone(entities["phone_number"])
            if ok:
                s.phone = clean_p
                s.temp_phone = clean_p
                has_new_val = True
        if entities.get("dental_service"):
            matched = _match_service(entities["dental_service"])
            if matched:
                s.service = matched
                s.temp_service = matched
                has_new_val = True
        if entities.get("appointment_date"):
            res_d, res_d_str, err = backend_actions.resolve_date(entities["appointment_date"])
            if res_d_str:
                s.date_str = res_d_str
                s.temp_date = res_d_str
                has_new_val = True
        if entities.get("appointment_time"):
            res_t, res_t_str, err = backend_actions.resolve_time(entities["appointment_time"])
            if res_t_str:
                s.time_str = res_t_str
                s.temp_time = res_t_str
                has_new_val = True

        if has_new_val:
            return _recap_message(s, updated=True)
        else:
            return "Sure, what should I change?"

    # Absorb anything the caller volunteered ahead of schedule, so no step below
    # re-asks for a slot Emma has already been given.
    _absorb_volunteered_slots(entities, s)

    # --- STEP 1: GREETING CONFIRMATION ---
    if s.step == 1:
        wants_booking = any(k in user_lower for k in ["book", "appointment", "schedule", "dentist"])
        # Slots the absorber just accepted are proof of a booking intent; raw
        # entities that failed validation still count as "the caller is engaged".
        volunteered = bool(s.temp_name or s.temp_service or s.temp_date or s.temp_time)
        raw_slots = bool(entities.get("patient_name") or entities.get("dental_service")
                         or entities.get("appointment_date") or entities.get("appointment_time"))
        ready = any(w in user_lower for w in ["yes", "sure", "yeah", "ok", "okay", "yup"])

        # An explicit refusal is checked first: "no, I don't want to book anything"
        # contains a booking keyword, and must not be read as consent because of it.
        if conf == "no" or "later" in user_lower or "busy" in user_lower:
            s.closed_conversation = True
            return "No problem at all. Just give us a call whenever you're ready. Take care!"

        if conf == "yes" or wants_booking or volunteered or raw_slots or ready:
            if wants_booking or volunteered:
                s.purpose = "booking"
            s.step = 2
            # temp_name may already be filled by _absorb_volunteered_slots when
            # the caller introduced themselves in the same breath.
            if s.temp_name:
                return f"Sure, I can help with that. {_name_prompt(s)}"
            return "Sure, I can help with that. May I have your full name, please?"

        if _GREETING_ONLY_RE.match(user_lower.strip(" .!?,")):
            # "Hi" / "Hello?" back to her greeting: greet them and ask again, as
            # a receptionist would, instead of pushing a booking at a "hello".
            return "Hi there! How can I help you today?"
        return "Sure. Would you like to book a visit?"

    # --- STEP 2: COLLECT NAME ---
    elif s.step == 2:
        if s.temp_name:
            # We already have a candidate name — waiting for confirmation
            if conf == "yes":
                s.name = s.temp_name
                s.name_confirmed = True
                s.name_confirm_attempts = 0
                return _after_name(s)
            elif conf == "no":
                rejected = s.temp_name
                s.temp_name = ""
                s.name_confirm_attempts = 0
                # The correction usually arrives in the same breath ("no, it's
                # Alex"), so use it instead of asking the caller to repeat.
                corrected = entities.get("patient_name")
                if corrected and _looks_like_name(corrected):
                    corrected = corrected.strip().title()
                    if corrected.lower() != rejected.lower():
                        s.temp_name = corrected
                        return f"Sorry about that. {_name_prompt(s)}"
                return "Sorry about that. Could you tell me your full name again?"
            else:
                # NLU couldn't determine yes or no.
                # Check if the user re-stated the same name (implicit confirmation)
                norm_response = re.sub(r"[^a-z\s]", "", user_lower).strip()
                norm_name = s.temp_name.lower().strip()
                # If the user's response IS the name (or contains it), treat as
                # confirmation. The length guard matters: norm_response is empty
                # for a letter-free answer such as a spoken phone number, and
                # "" is a substring of every name.
                if len(norm_response) >= 2 and (norm_name in norm_response or norm_response in norm_name):
                    s.name = s.temp_name
                    s.name_confirmed = True
                    s.name_confirm_attempts = 0
                    return _after_name(s)

                # If we've already asked 2+ times and still can't get clear signal,
                # accept the name to avoid infinite loops
                s.name_confirm_attempts += 1
                if s.name_confirm_attempts >= 2:
                    s.name = s.temp_name
                    s.name_confirmed = True
                    s.name_confirm_attempts = 0
                    logger.info("Name accepted after %d attempts: %s", s.name_confirm_attempts + 1, s.name)
                    return _after_name(s)

                # Ask once more — this time spelled out, since plain repetition
                # clearly did not land.
                return _name_prompt(s, spell=True)
        else:
            # No candidate yet — try to get name from NLU or raw text
            nlu_name = entities.get("patient_name")
            name_input = nlu_name or user_text or ""
            name_input = re.sub(
                r"^(my name is|my name's|i am|i'm|this is|it is|it's|its|call me|the name is|name is)\s+",
                "", name_input, flags=re.IGNORECASE,
            )
            name_input = re.sub(r"[.!?,]", "", name_input).strip()
            # Raw words get the keyword screen too, so "next Monday at 5 pm" is
            # never adopted — and then spelled back — as the caller's name. The
            # screen is dropped after two misses, because it has false positives
            # on real names and no caller may be trapped at this step. The
            # structural checks (no digits, word cap) still apply either way.
            strict = not nlu_name and s.name_capture_attempts < 2
            if not _looks_like_name(name_input, strict=strict):
                s.name_capture_attempts += 1
                return "May I have your full name, please?"
            s.name_capture_attempts = 0
            s.temp_name = name_input.title()
            s.name_confirm_attempts = 0
            return _name_prompt(s)


    # --- STEP 3: PURPOSE OF CALL ---
    elif s.step == 3:
        booking_keywords = ["book", "appointment", "schedule", "dentist", "checkup", "cleaning",
                           "filling", "root canal", "braces", "invisalign", "extraction"]
        user_lower = user_text.lower()
        is_booking = any(k in user_lower for k in booking_keywords)
        # Naming a service, date or time is itself a booking request, even without
        # one of the keywords above.
        if not is_booking and (s.temp_service or s.temp_date or s.temp_time):
            is_booking = True
        if is_booking:
            s.purpose = "booking"
            s.step = 4
            return _phone_prompt(s, lead="Sure.")
        else:
            return "I can help you set up a visit. Would you like to book one?"

    # --- STEP 4: COLLECT PHONE ---
    elif s.step == 4:
        if not s.temp_phone:
            phone_input = entities.get("phone_number") or user_text
            is_valid, cleaned_phone = backend_actions.validate_phone(phone_input)
            if not is_valid:
                return "Sorry, I think I missed a digit there. Could you say the number again?"
            s.temp_phone = cleaned_phone
            s.phone_confirm_attempts = 0
            return f"So that's {_spoken_phone(s.temp_phone)}, right?"
        else:
            if conf == "yes":
                s.phone = s.temp_phone
                s.phone_confirmed = True
                s.phone_confirm_attempts = 0
                s.step = 5
                return _service_prompt(s)
            elif conf == "no":
                rejected = s.temp_phone
                s.temp_phone = ""
                s.phone_confirm_attempts = 0
                corrected = entities.get("phone_number")
                if corrected:
                    ok, cleaned = backend_actions.validate_phone(corrected)
                    if ok and cleaned != rejected:
                        s.temp_phone = cleaned
                        return _phone_prompt(s, lead="Sorry about that.")
                return "No problem. What's the best mobile number to reach you on?"
            else:
                s.phone_confirm_attempts += 1
                if s.phone_confirm_attempts >= 2:
                    s.phone = s.temp_phone
                    s.phone_confirmed = True
                    s.phone_confirm_attempts = 0
                    s.step = 5
                    return _service_prompt(s)
                return f"Sorry, is that {_spoken_phone(s.temp_phone)}?"

    # --- STEP 5: SERVICE SELECTION ---
    elif s.step == 5:
        if not s.temp_service:
            matched_service = _match_service(entities.get("dental_service") or user_text)
            if not matched_service:
                return (
                    "Sorry, which treatment is it for? A check-up, a cleaning, a filling, a root canal, "
                    "an extraction, or braces?"
                )
            s.temp_service = matched_service
            return f"A {s.temp_service}, is that right?"
        else:
            if conf == "yes":
                s.service = s.temp_service
                s.service_confirmed = True
                s.step = 6
                return f"Great. And that'd be at our {config.DEFAULT_BRANCH} branch, is that okay?"
            elif conf == "no":
                rejected = s.temp_service
                s.temp_service = ""
                corrected = _match_service(entities.get("dental_service") or user_text)
                if corrected and corrected != rejected:
                    s.temp_service = corrected
                    return _service_prompt(s, lead="Sorry about that.")
                return "No problem. What's the visit for?"
            else:
                return f"A {s.temp_service}, is that right?"

    # --- STEP 6: LOCATION CONFIRMATION ---
    elif s.step == 6:
        if conf == "yes":
            s.location_confirmed = True
            s.step = 7
            return _date_prompt(s)
        elif conf == "no":
            return f"Sorry, on this call I can only book our {config.DEFAULT_BRANCH} branch. Would that still work?"
        else:
            return f"That'd be at our {config.DEFAULT_BRANCH} branch, is that okay?"

    # --- STEP 7: PREFERRED DATE ---
    elif s.step == 7:
        if not s.temp_date:
            date_input = entities.get("appointment_date") or user_text
            res_date, res_date_str, err_msg = backend_actions.resolve_date(date_input)
            if not res_date_str:
                if err_msg:
                    return f"{err_msg}"
                return "Could you please specify the date again?"
            s.temp_date = res_date_str
            return _date_prompt(s)
        else:
            if conf == "yes":
                s.date_str = s.temp_date
                s.date_confirmed = True
                s.step = 8
                return _time_prompt(s)
            elif conf == "no":
                rejected = s.temp_date
                s.temp_date = ""
                # "no, make it Tuesday" resolves to Tuesday, but "not Monday"
                # resolves right back to the date being rejected — so a corrected
                # value only counts when it actually differs.
                _d, resolved, _err = backend_actions.resolve_date(
                    entities.get("appointment_date") or user_text
                )
                if resolved and resolved != rejected:
                    s.temp_date = resolved
                    return f"Sorry about that. {_date_prompt(s)}"
                return "No problem. What day would suit you?"
            else:
                return _date_prompt(s)

    # --- STEP 8: PREFERRED TIME ---
    elif s.step == 8:
        if not s.temp_time:
            time_input = entities.get("appointment_time") or user_text
            res_time, res_time_str, err_msg = backend_actions.resolve_time(time_input)
            if not res_time_str:
                if err_msg:
                    return f"{err_msg}"
                return "Could you choose another time?"
            s.temp_time = res_time_str
            return _time_prompt(s)
        else:
            if conf == "yes":
                s.time_str = s.temp_time
                s.time_confirmed = True
                s.step = 9
                return _recap_message(s)
            elif conf == "no":
                rejected = s.temp_time
                s.temp_time = ""
                _t, resolved, _err = backend_actions.resolve_time(
                    entities.get("appointment_time") or user_text
                )
                if resolved and resolved != rejected:
                    s.temp_time = resolved
                    return f"Sorry about that. {_time_prompt(s)}"
                return "No problem. What time works best for you?"
            else:
                return _time_prompt(s)

    # --- STEP 9: FINAL RECAP ---
    elif s.step == 9:
        if conf == "yes":
            s.recap_confirmed = True
            s.step = 10
            is_available, alts = backend_actions.check_availability(s.date_str, s.time_str, s.service)
            if is_available:
                success, msg = backend_actions.book_appointment(
                    s.name, s.phone, s.service, s.date_str, s.time_str
                )
                if not success:
                    s.step = 9
                    return "Sorry, that slot just went. Shall I look at another time?"
                s.booking_confirmed = True
                s.step = 11
                dt_obj = datetime.strptime(s.date_str, "%Y-%m-%d")
                formatted_date = dt_obj.strftime("%A the %d %B")
                return _confirmation_message(s.service, formatted_date, s.time_str)
            else:
                s.offering_alternatives = True
                s.alternative_slots = alts
                if len(alts) >= 2:
                    return f"Ah, that one's taken. I could do {alts[0]} or {alts[1]}, would either work?"
                elif len(alts) == 1:
                    return f"Ah, that one's taken. I could do {alts[0]}, would that work?"
                else:
                    return "Hmm, that time's full and there's nothing close to it that day. Would another time work?"
        elif conf == "no":
            return "Sure, what should I change?"
        else:
            return "Sorry, shall I go ahead and book that?"

    # --- STEP 10: ALTERNATIVE BOOKING SLOTS ---
    elif s.step == 10:
        if s.offering_alternatives:
            user_lower = user_text.lower()
            chosen_slot = None
            if len(s.alternative_slots) >= 1:
                for slot in s.alternative_slots:
                    slot_clean = slot.lower()
                    short_slot = re.sub(r"^0", "", slot_clean)
                    hour = short_slot.split(":")[0]
                    ampm = "pm" if "pm" in slot_clean else "am"
                    if short_slot in user_lower or (hour in user_lower and ampm in user_lower) or slot_clean in user_lower:
                        chosen_slot = slot
                        break
                if not chosen_slot:
                    if "first" in user_lower or "earlier" in user_lower or "former" in user_lower:
                        chosen_slot = s.alternative_slots[0]
                    elif "second" in user_lower or "later" in user_lower or "latter" in user_lower:
                        if len(s.alternative_slots) >= 2:
                            chosen_slot = s.alternative_slots[1]
                if not chosen_slot and len(s.alternative_slots) == 1:
                    # Reuse the confirmation already parsed for this turn. Calling the
                    # sync, global-`state` NLU here corrupted concurrent sessions and
                    # bypassed the per-session state object `s`.
                    # This deliberately does NOT also scan the raw text for "work" /
                    # "sure" / "ok": those substrings fire inside "that doesn't work",
                    # "None of those work" and "book", which would accept the very
                    # slot the caller just refused. _parse_confirmation covers the
                    # same words with word boundaries and negation handled.
                    if conf == "yes":
                        chosen_slot = s.alternative_slots[0]

            if chosen_slot:
                s.time_str = chosen_slot
                success, msg = backend_actions.book_appointment(
                    s.name, s.phone, s.service, s.date_str, s.time_str
                )
                if not success:
                    return "Sorry, that one just went too. What other time would work?"
                s.booking_confirmed = True
                s.step = 11
                dt_obj = datetime.strptime(s.date_str, "%Y-%m-%d")
                formatted_date = dt_obj.strftime("%A the %d %B")
                return _confirmation_message(s.service, formatted_date, s.time_str)
            else:
                if len(s.alternative_slots) >= 2:
                    return f"Sorry, was that {s.alternative_slots[0]} or {s.alternative_slots[1]}?"
                elif len(s.alternative_slots) == 1:
                    return f"Sorry, would {s.alternative_slots[0]} work?"
                else:
                    return "What other time would work for you?"
        else:
            s.step = 9
            return "Let me just check that again. Shall I go ahead and book it?"

    # --- STEP 11: CLOSING CHECK ---
    elif s.step == 11:
        if conf == "no" or "nothing" in user_text.lower() or "no thanks" in user_text.lower() or "that's all" in user_text.lower() or "bye" in user_text.lower():
            s.step = 12
            s.closed_conversation = True
            return "Great, we'll see you then. Take care, bye!"
        else:
            return "Sure, anything else I can help with?"

    # --- STEP 12: CLOSED ---
    elif s.step == 12:
        return "Take care, bye!"

    return "Sorry, I didn't catch that. Could you please repeat?"


# ==========================================
# DIALOGUE MANAGEMENT - PUBLIC API
# ==========================================

CLOSED_REPLY = "Great, we'll see you then. Take care, bye!"


@dataclass
class TurnResult:
    text: str
    tier: int                  # 0 = deterministic fast path, 1 = LLM NLU, 2 = R2 no-model fallback,
                               # -1 = no NLU (greeting/closed)
    entities: dict = field(default_factory=dict)
    nlu_ms: float = 0.0
    step_before: int | str = 0     # R2: the goal values (same as goal_before / goal_after)
    step_after: int | str = 0
    # Shared facade contract (docs/R2_DESIGN.md, section 15). Defaults keep the
    # 12-step engine and its tests unchanged.
    action: str | None = None      # "booked" | "rescheduled" | "cancelled", only after Python committed it
    goal_before: str | None = None
    goal_after: str | None = None
    spoken_count: int = 0          # leading sentences of text already delivered through on_sentence


def _noop_progress(event, **data):
    return None


async def async_process_turn(user_text, s, progress=None, **kwargs) -> TurnResult:
    """
    Process one caller turn and return Emma's reply with timing metadata.

    Order: greeting -> Tier-0 -> (Tier-1 LLM NLU) -> Python state machine ->
    reply. The model is limited to NLU; every progression, validation and
    booking decision is made by `_handle_conversation_step`, so a model reply
    can never book an appointment the caller did not explicitly confirm.

    `progress(event)` lets the real-time layer react without polling:
      "llm_start"     Tier-0 declined; a model round trip is starting
      "before_action" the caller just confirmed the recap; a slow availability
                      check and booking are about to run
      "commit"        the state machine is about to mutate `s`; from here the
                      turn can no longer be cancelled and restarted

    With config.R2_ENGINE on, or when `s` is an R2 CallContext (from
    new_session), the turn runs on dialogue.engine instead; an `on_sentence`
    keyword then receives each validated reply sentence as soon as it is
    final. The 12-step machine doesn't stream, so on_sentence is only in the
    signature when R2 is the engine (call_session checks the signature).
    """
    if s is None:
        raise ValueError("async_process_turn requires a per-call SessionState")
    if getattr(s, "is_recovery", False):
        # An outbound recovery call (dialogue.recovery), whichever engine inbound calls use.
        return await _recovery_turn(user_text, s, progress)
    if isinstance(s, CallContext):
        # new_session() hands out a CallContext exactly when config.R2_ENGINE is
        # on, so routing on the type follows the flag and keeps a SessionState
        # made directly (the old tests) on the machine it was built for.
        return await _r2_turn(user_text, s, progress, kwargs.get("on_sentence"))
    emit = progress or _noop_progress
    step_before = s.step

    if s.closed_conversation:
        return TurnResult(CLOSED_REPLY, tier=-1, step_before=step_before, step_after=s.step)

    if s.step == 1 and not s.greeting_spoken and not user_text:
        s.greeting_spoken = True
        greeting = phrases.next_greeting()
        s.history.append({"role": "assistant", "content": greeting})
        return TurnResult(greeting, tier=-1, step_before=step_before, step_after=s.step)

    started = time.perf_counter()
    entities = tier0.fast_entities(user_text, s) if config.TIER0_ENABLED else None
    tier = 0
    if entities is None:
        tier = 1
        emit("llm_start")
        try:
            # Looked up on the module at call time so tests can patch it.
            entities = await async_extract_entities_with_llm(user_text, s=s)
        except Exception as e:
            # The deterministic flow still handles simple confirmations even when
            # the NLU provider is unavailable.
            logger.error("NLU extraction failed: %s", e)
            entities = _basic_entity_fallback(user_text)
    entities = entities or {}
    if tier == 1 and not entities.get("user_query") and "answer" not in entities \
            and tier0.looks_like_question(user_text):
        # The model was unavailable (deterministic fallback). A question must not
        # be consumed as a slot value, e.g. adopted as the caller's name.
        entities["user_query"] = user_text
    nlu_ms = (time.perf_counter() - started) * 1000

    if _is_pure_question(entities):
        # Answer (or escalate) and re-ask the pending question. The state machine
        # is not run, so the question text can never be taken as a slot value.
        answer = (entities.get("answer") or "").strip() or ESCALATION_LINE
        reply = f"{answer} {_current_question(s)}"
        _remember(s, user_text, reply)
        logger.info("turn tier=%d off-topic question at step %d", tier, s.step)
        return TurnResult(reply, tier=tier, entities=entities, nlu_ms=nlu_ms,
                          step_before=step_before, step_after=s.step)

    if s.step == 9 and _resolve_confirmation(user_text, entities) == "yes":
        emit("before_action")
    emit("commit")
    # Availability and calendar operations are synchronous today.  Keep them
    # off the event loop so one calendar request cannot stall every call.
    booked_before = s.booking_confirmed
    directive = await asyncio.to_thread(_handle_conversation_step, user_text, entities, s)
    action = "booked" if s.booking_confirmed and not booked_before else None
    reply = await generate_emma_response(
        directive, s, user_text=user_text,
        user_query=entities.get("user_query"), answer=entities.get("answer"),
    )
    logger.info("turn tier=%d step %d->%d nlu=%.0fms", tier, step_before, s.step, nlu_ms)
    return TurnResult(reply, tier=tier, entities=entities, nlu_ms=nlu_ms,
                      step_before=step_before, step_after=s.step, action=action)


# ==========================================
# R2 FACADE (docs/R2_DESIGN.md, section 15)
# ==========================================
# The call session, the harness and the tools use only these, so the 12-step
# machine above can be removed at integration without touching them.

def new_session(call_id: str | None = None):
    """A per-call state: an R2 CallContext when config.R2_ENGINE is on, else today's SessionState."""
    if config.R2_ENGINE:
        from dialogue.context import new_context
        return new_context(call_id)
    return SessionState()


def phone_line(s, caller_id: str | None = None, can_transfer: bool = False) -> bool:
    """
    A call that came in over the phone (audiosocket.py). A caller ID that is a
    real phone number stands in for asking: Emma confirms it with "Is the
    number you're calling from the best one to reach you on?" instead of
    asking for digits. can_transfer lets a callback become a live transfer to
    the front desk. Returns whether the caller ID was taken; the 12-step
    machine (no CallContext) ignores all of it.
    """
    if not isinstance(s, CallContext):
        return False
    from dialogue.context import FieldState
    s.can_transfer = bool(can_transfer)
    e164 = phones.to_e164(caller_id) if caller_id else None
    if not e164 or s.caller.phone_state != FieldState.EMPTY:
        return False
    s.caller.phone_e164, s.caller.phone_state, s.caller.phone_source = e164, FieldState.PENDING, "caller_id"
    return True


async def _r2_turn(user_text, s, progress, on_sentence) -> TurnResult:
    from dialogue import engine
    out = await engine.process_turn(s, user_text or "", progress=progress, on_sentence=on_sentence)
    return TurnResult(out.text, tier=out.tier, entities=out.entities, nlu_ms=out.nlu_ms,
                      step_before=out.goal_before or 0, step_after=out.goal_after or 0,
                      action=out.action, goal_before=out.goal_before, goal_after=out.goal_after,
                      spoken_count=out.spoken_count)


async def _recovery_turn(user_text, s, progress) -> TurnResult:
    """One turn of a recovery call. Its database work runs in a thread; progress events hop back to the loop."""
    import db
    from dialogue import recovery
    loop = asyncio.get_running_loop()

    def emit(event, **data):
        if progress is not None:
            loop.call_soon_threadsafe(lambda: progress(event, **data))

    state_before = s.state
    started = time.perf_counter()
    reply = await asyncio.to_thread(recovery.process_turn, s, user_text or "", db.get_db().run_sync, emit)
    return TurnResult(reply.text, tier=0 if user_text else -1, nlu_ms=(time.perf_counter() - started) * 1000,
                      step_before=state_before, step_after=s.state, action=reply.action,
                      goal_before=state_before, goal_after=s.state)


def listening_hint(s):
    """
    What Emma is listening for: {"expect": "phone|name|yes_no|date|time|choice|open|spelling",
    "digits_so_far": int}. A partial phone number gives expect="phone" with the
    digits buffered so far, so the turn detector waits for the rest.

    For today's SessionState it returns None: call_session then uses
    turn_detector.hint_from_state and the harness its own reading of Emma's
    line, exactly as before this function existed.
    """
    if getattr(s, "is_recovery", False):
        from dialogue import recovery
        return recovery.listening_hint(s)
    if isinstance(s, CallContext):
        from dialogue import policy
        return policy.listening_hint(s)
    return None


if config.R2_ENGINE:
    _process_turn_12_step = async_process_turn

    async def async_process_turn(user_text, s, progress=None, on_sentence=None) -> TurnResult:
        """The R2 entry point: as above, with on_sentence in the signature so call_session streams."""
        return await _process_turn_12_step(user_text, s, progress, on_sentence=on_sentence)


def install_test_nlu(reader):
    """
    Tests and harness only: run the R2 model call on reader(text, expect) ->
    reading dict instead of Gemini (nlu.ReaderBackend). Returns a context
    manager; a reader returning None simulates the model being down.
    """
    import nlu
    return nlu.use_backend(nlu.ReaderBackend(reader))


async def async_get_ai_response(user_text, session_state):
    """Text-only entry point (tests, tools): Emma's reply."""
    result = await async_process_turn(user_text, session_state)
    return result.text
