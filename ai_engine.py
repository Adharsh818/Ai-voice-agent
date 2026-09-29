import asyncio
import json
import re
import logging
import time
from datetime import datetime
import config
import backend_actions

logger = logging.getLogger(__name__)

# Gemini SDK (google-genai) — the async client used by the real-time pipeline.
try:
    from google import genai as genai_new
    from google.genai import types as genai_types
    GENAI_NEW_AVAILABLE = True
except ImportError:
    GENAI_NEW_AVAILABLE = False


# ==========================================
# EMMA PERSONA & RESPONSE GENERATION PROMPT
# ==========================================

EMMA_RECEPTIONIST_SYSTEM_PROMPT = """You are Emma, the warm, professional, and friendly receptionist for Pearl Dental Clinic, Nagarbhavi, Bangalore.

PERSONALITY & VOICE:
- Speak naturally and conversationally, exactly like a real human receptionist on a phone call.
- Use contractions freely: "I'll", "we're", "that's", "you've", "it's", "don't".
- Use warm, natural fillers occasionally: "Sure thing!", "Absolutely!", "Of course!", "Perfect!", "Great!", "Wonderful!".
- Keep every response SHORT — maximum 1-2 sentences and under 30 words. Voice conversations must be concise.
- Match the caller's energy: be upbeat if they are, calm if they seem nervous.
- Never sound robotic. Never say "I have noted your information." Instead say "Got it!".
- Vary your phrasing slightly each time — don't repeat the exact same words.

PEARL DENTAL CLINIC — KNOWLEDGE BASE:
- Name: Pearl Dental Clinic
- Location: Nagarbhavi, Bangalore, Karnataka.
- Working Hours: Monday to Saturday, 7:00 AM to 9:00 PM. Closed on Sundays.
- Lunch Break: 2:00 PM to 2:30 PM daily.
- Services: General Check-up, Consultation, Teeth Cleaning, Tooth Filling, Root Canal Treatment, Tooth Extraction, Braces, Invisalign, Pediatric Dentistry.
- Pricing: Varies by procedure — suggest the caller ask the doctor directly or visit in person for an exact quote. Keep it reassuring.
- Phone: They can reach the clinic directly for any urgent queries.

HOW TO RESPOND:
- You are given a "Dialogue Directive" — this is what you MUST communicate or ask next. Rephrase it naturally.
- You may also be given a "User Query" — an off-topic question the caller asked. Answer it briefly using the Knowledge Base, then transition back to the Dialogue Directive smoothly.
- If no User Query, just rephrase the Dialogue Directive warmly.
- NEVER mention "Dialogue Directive" or "User Query" in your response.
- Speak as if you are on a live phone call. Keep it flowing and natural.
"""

# NLU Extraction prompt (for async_extract_entities_with_llm)
EMMA_NLU_SYSTEM_PROMPT = """You are an NLU parser for Pearl Dental Clinic's voice receptionist.
Your job is to extract structured data from a patient's spoken response.
Output ONLY valid JSON with no markdown, no explanation.
"""



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


# Global session state (used by sync/legacy functions only)
state = SessionState()

# Async Gemini client (lazy-initialized)
_genai_client = None
_nlu_unavailable_until = 0.0

def _get_genai_client():
    """Lazy-initialize the google-genai async client."""
    global _genai_client
    if _genai_client is None and GENAI_NEW_AVAILABLE and config.GEMINI_API_KEY:
        _genai_client = genai_new.Client(api_key=config.GEMINI_API_KEY)
    return _genai_client


# ==========================================
# LLM NLU EXTRACTION
# ==========================================
def extract_entities_with_llm(user_text):
    """
    Calls the LLM to extract booking slots (patient_name, phone_number,
    dental_service, appointment_date, appointment_time, confirmation).
    """
    # Build prompt
    state_desc = (
        f"Step: {state.step}\n"
        f"Name: {state.name or state.temp_name or 'None'}\n"
        f"Phone: {state.phone or state.temp_phone or 'None'}\n"
        f"Service: {state.service or state.temp_service or 'None'}\n"
        f"Date: {state.date_str or state.temp_date or 'None'}\n"
        f"Time: {state.time_str or state.temp_time or 'None'}"
    )

    prompt = f"""
You are an NLU parser extracting values from a patient's voice response.
Current Session State:
{state_desc}

User Voice Response: "{user_text}"

Extract the following values based on what the user said (only extract if explicitly stated or clearly implied):
1. patient_name: If the user states their name (e.g. "My name is Adharsh", "I am Bob"). Keep it capitalized.
2. phone_number: If the user states their mobile phone number (e.g. "9876543210").
3. dental_service: If the user states the service they want (e.g., "Consultation", "Root Canal").
4. appointment_date: If the user states their preferred date (e.g., "tomorrow", "Monday", "next Friday", "25 August").
5. appointment_time: If the user states their preferred time (e.g., "5:30 PM", "morning", "around 5 PM").
6. confirmation: If the user says "yes", "no", "correct", "wrong", "is correct", "that's wrong", "sure", "no problem", or answers a yes/no question. Map to "yes" or "no".

Format output strictly as a JSON object:
{{
  "patient_name": "extracted_name_or_null",
  "phone_number": "extracted_phone_or_null",
  "dental_service": "extracted_service_or_null",
  "appointment_date": "extracted_date_or_null",
  "appointment_time": "extracted_time_or_null",
  "confirmation": "yes_or_no_or_null"
}}
Output ONLY the JSON and nothing else.
"""

    response_text = ""
    provider = config.LLM_PROVIDER

    # 1. Use Gemini if configured
    if provider == "gemini" and GEMINI_AVAILABLE and config.GEMINI_API_KEY:
        try:
            genai.configure(api_key=config.GEMINI_API_KEY)
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content(
                prompt,
                generation_config={"response_mime_type": "application/json"}
            )
            response_text = response.text
        except Exception as e:
            # Fall back to Ollama for THIS call only — never mutate global config.
            logger.warning("Gemini generation failed: %s. Falling back to Ollama.", e)
            provider = "ollama"

    # 2. Use Ollama
    if not response_text or provider == "ollama":
        try:
            response = ollama.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.0}
            )
            response_text = response['message']['content']
        except Exception as e:
            logger.warning("Ollama generation failed: %s", e)
            # Stub fallback if both fail
            response_text = "{}"

    # Parse JSON
    try:
        # Extract JSON if surrounded by markdown code blocks
        clean_json = re.sub(r"^```json\s*|```$", "", response_text.strip(), flags=re.MULTILINE)
        data = json.loads(clean_json)
    except Exception:
        # Fallback empty structure
        data = {}
        
    # Standardize values (turn "null" strings into actual None)
    for k in ["patient_name", "phone_number", "dental_service", "appointment_date", "appointment_time", "confirmation"]:
        val = data.get(k)
        if val == "null" or val == "None" or val == "":
            data[k] = None
            
    return data


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


async def async_extract_entities_with_llm(user_text, s=None):
    """
    Async NLU extraction using Gemini 3.6 Flash.
    Extracts booking entities + detects off-topic user queries.
    """
    if s is None:
        s = state

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
        f"Recent conversation (context only — do NOT extract from these lines):\n{history_block}\n\n"
        if history_block else ""
    )

    prompt = f"""Current appointment booking session state:
{state_desc}

{context_section}Patient's spoken response: "{user_text}"

Extract the following from what the patient said. Only extract if clearly stated.
Use the recent conversation above only to resolve references such as "the second
one", "that time", or "same as before" — never to fill a slot the patient did not
actually mention in this response:
1. patient_name - their full name if stated (e.g. "My name is Adharsh" → "Adharsh")
2. phone_number - their 10-digit mobile number if stated (digits only)
3. dental_service - the dental service they want if stated (e.g. "root canal", "cleaning")
4. appointment_date - the date they prefer if stated (e.g. "tomorrow", "next Monday", "25th August")
5. appointment_time - the time they prefer if stated (e.g. "5 PM", "morning", "10:30 AM")
6. confirmation - "yes" if they confirmed/agreed, "no" if they denied/disagreed, null otherwise
7. user_query - if the patient asked a general question unrelated to giving booking info (e.g. "where are you located?", "what are your hours?", "how much does it cost?", "do you do braces?") — extract their exact question/intent here. Otherwise null.

Output ONLY this JSON, no markdown, no explanation:
{{
  "patient_name": null,
  "phone_number": null,
  "dental_service": null,
  "appointment_date": null,
  "appointment_time": null,
  "confirmation": null,
  "user_query": null
}}"""

    global _nlu_unavailable_until
    response_text = ""
    client = _get_genai_client()

    # Avoid adding a network timeout to every caller turn when the provider is
    # temporarily unavailable.  The deterministic fallback still handles the
    # booking flow during this short cooldown.
    if client and time.monotonic() >= _nlu_unavailable_until:
        try:
            response = await client.aio.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=prompt,
                config={
                    "system_instruction": EMMA_NLU_SYSTEM_PROMPT,
                    "response_mime_type": "application/json",
                    "temperature": 0.0,
                },
            )
            response_text = response.text
        except Exception as e:
            logger.error("Async Gemini NLU extraction failed: %s", e)
            _nlu_unavailable_until = time.monotonic() + 30
            return _basic_entity_fallback(user_text)
    else:
        return _basic_entity_fallback(user_text)

    # Parse JSON
    try:
        clean_json = re.sub(r"^```json\s*|```$", "", response_text.strip(), flags=re.MULTILINE)
        data = json.loads(clean_json)
    except Exception:
        data = {}

    # Normalize null-like strings to actual None
    for k in ["patient_name", "phone_number", "dental_service",
              "appointment_date", "appointment_time", "confirmation", "user_query"]:
        val = data.get(k)
        if val in ("null", "None", "", "undefined"):
            data[k] = None

    return data


async def generate_emma_response(directive: str, s, user_text: str = "", user_query: str = None) -> str:
    """
    Delivers Emma's verbal response. If an off-topic user_query was asked,
    uses Gemini to answer it briefly first before continuing with the directive.
    Otherwise, delivers the directive cleanly with full accuracy and zero truncation.
    """
    if not directive:
        directive = "Could you please repeat that?"

    # If no off-topic user query, use directive directly for 100% accuracy on spelling/numbers/questions
    if not user_query:
        if user_text:
            s.history.append({"role": "user", "content": user_text})
        s.history.append({"role": "assistant", "content": directive})
        if len(s.history) > 20:
            s.history = s.history[-20:]
        logger.info("Emma (Directive): %s", directive[:100])
        return directive

    # Handle off-topic user query with Gemini
    client = _get_genai_client()
    if not client:
        return directive

    history_block = _recent_history(s)
    context_section = f"Recent conversation so far:\n{history_block}\n\n" if history_block else ""

    prompt = (
        f"{context_section}"
        f"Answer this user question briefly (1 sentence) using the clinic knowledge base, "
        f"then seamlessly transition into asking/saying: \"{directive}\"\n"
        f"User Question: {user_query}"
    )

    try:
        response = await client.aio.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=prompt,
            config={
                "system_instruction": EMMA_RECEPTIONIST_SYSTEM_PROMPT,
                "temperature": 0.5,
                "max_output_tokens": 250,  # Prevent any truncation
            },
        )
        generated = response.text.strip() if response.text else f"I can help with that. {directive}"
        if user_text:
            s.history.append({"role": "user", "content": user_text})
        s.history.append({"role": "assistant", "content": generated})
        if len(s.history) > 20:
            s.history = s.history[-20:]
        logger.info("Emma (Gemini+Query): %s", generated[:100])
        return generated
    except Exception as e:
        logger.error("Gemini response error: %s", e)
        if user_text:
            s.history.append({"role": "user", "content": user_text})
        s.history.append({"role": "assistant", "content": directive})
        return directive




# ==========================================
# DIALOGUE MANAGEMENT â€” SHARED STATE MACHINE
# ==========================================

def _confirmation_message(service: str, formatted_date: str, time_str: str) -> str:
    """
    Booking confirmation text. Only promises a Google Calendar invitation when
    real Google APIs are in use — in mock mode no invitation is actually sent,
    so Emma must not claim one is coming.
    """
    msg = (
        "Your appointment has been successfully confirmed. "
        f"Your appointment for {service} has been confirmed for {formatted_date} "
        f"at {time_str} at Pearl Dental Clinic, Nagarbhavi. "
    )
    if not config.USE_MOCK_APIS:
        msg += "You will also receive a Google Calendar invitation shortly. "
    msg += "Is there anything else I can help you with today?"
    return msg


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
        return f"Just to confirm — your name is {s.temp_name}, spelled {spelled}. Is that correct?"
    return f"You said {s.temp_name} — did I get that right?"


def _purpose_prompt():
    return "Thank you! How may I help you today?"


def _phone_prompt(s, lead="Great!"):
    if s.temp_phone:
        spaced = " ".join(list(s.temp_phone))
        return f"{lead} Just to confirm, your phone number is {spaced}. Is that correct?"
    return f"{lead} What is the best mobile number to reach you?"


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


def _service_prompt(s, lead="Thank you."):
    if s.temp_service:
        return f"{lead} Just to confirm, you'd like to book a {s.temp_service}. Is that correct?"
    return f"{lead} Which dental service would you like to book?"


def _date_prompt(s):
    if s.temp_date:
        formatted = datetime.strptime(s.temp_date, "%Y-%m-%d").strftime("%A, %d %B")
        return f"You'd like to visit on {formatted}. Is that correct?"
    return "Which date would you prefer for your appointment?"


def _time_prompt(s):
    if s.temp_time:
        return f"You'd like to visit at {s.temp_time}. Is that correct?"
    return "What time works best for you?"


def _recap_message(s, updated=False):
    """
    Read the booking back to the caller.  The name is spoken normally — spelling
    it out letter-by-letter on every recap is slow and robotic on a voice call —
    but the phone number stays digit-by-digit, which is how people verify numbers.
    """
    spaced_phone = " ".join(list(s.phone))
    formatted_date = datetime.strptime(s.date_str, "%Y-%m-%d").strftime("%d %B %Y")
    lead = (
        "Thank you. Let me recap the updated details. " if updated
        else "Before I book your appointment, let me confirm everything. "
    )
    tail = "Is everything correct now?" if updated else "Is everything correct?"
    return (
        f"{lead}"
        f"Name: {s.name}. "
        f"Phone Number: {spaced_phone}. "
        f"Service: {s.service}. "
        f"Clinic: Pearl Dental Clinic, Nagarbhavi. "
        f"Appointment Date: {formatted_date}. "
        f"Appointment Time: {s.time_str}. "
        f"{tail}"
    )


def _handle_conversation_step(user_text, entities, s):
    """
    Core conversation state machine. Takes user text and pre-extracted entities.
    Uses session state object `s` (supports both global and per-session state).
    Returns Emma's next verbal response string.
    """
    user_lower = user_text.lower().strip() if user_text else ""

    # The NLU's reading wins when it has one, since it sees the whole utterance in
    # context. The one exception is an outright disagreement: if the NLU heard
    # "yes" while the deterministic parser heard an explicit refusal, Emma treats
    # the turn as unclear and re-asks rather than picking a side. Guessing "yes"
    # here is what let a rejected recap commit a booking.
    raw_conf = entities.get("confirmation")
    text_conf = _parse_confirmation(user_text)
    if raw_conf and text_conf and raw_conf != text_conf:
        conf = None
        logger.info("Confirmation conflict (nlu=%s text=%s); re-asking", raw_conf, text_conf)
    else:
        conf = raw_conf or text_conf

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
            return "Which information is incorrect? Please tell me what to update."

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
            return "No problem. When would be a better time for me to call you back?"

        if conf == "yes" or wants_booking or volunteered or raw_slots or ready:
            if wants_booking or volunteered:
                s.purpose = "booking"
            s.step = 2
            # temp_name may already be filled by _absorb_volunteered_slots when
            # the caller introduced themselves in the same breath.
            if s.temp_name:
                return f"Wonderful! Let's get your appointment scheduled. {_name_prompt(s)}"
            return "Wonderful! Let's get your appointment scheduled. May I have your full name, please?"

        return "Is this a good time to talk?"

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
                return "Sorry about that. Could you please tell me your full name again?"
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
            return _phone_prompt(s, lead="I can help with that.")
        else:
            return "At the moment, I can assist only with scheduling appointments."

    # --- STEP 4: COLLECT PHONE ---
    elif s.step == 4:
        if not s.temp_phone:
            phone_input = entities.get("phone_number") or user_text
            is_valid, cleaned_phone = backend_actions.validate_phone(phone_input)
            if not is_valid:
                return (
                    "That doesn't appear to be a valid 10-digit Indian mobile number. "
                    "Could you please repeat it?"
                )
            s.temp_phone = cleaned_phone
            s.phone_confirm_attempts = 0
            spaced = " ".join(list(s.temp_phone))
            return f"Just to confirm, your phone number is {spaced}. Is that correct?"
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
                return "No problem. What is the best mobile number to reach you?"
            else:
                s.phone_confirm_attempts += 1
                if s.phone_confirm_attempts >= 2:
                    s.phone = s.temp_phone
                    s.phone_confirmed = True
                    s.phone_confirm_attempts = 0
                    s.step = 5
                    return _service_prompt(s)
                spaced = " ".join(list(s.temp_phone))
                return f"Is your mobile number {spaced}? Is that correct?"

    # --- STEP 5: SERVICE SELECTION ---
    elif s.step == 5:
        if not s.temp_service:
            matched_service = _match_service(entities.get("dental_service") or user_text)
            if not matched_service:
                return (
                    "I'm sorry, but that service is currently unavailable through this booking assistant. "
                    "We offer: General Check-up, Consultation, Teeth Cleaning, Tooth Filling, Root Canal Treatment, "
                    "Tooth Extraction, Braces, Invisalign, and Pediatric Dentistry. Which of these would you like to book?"
                )
            s.temp_service = matched_service
            return f"Just to confirm, you'd like to book a {s.temp_service}. Is that correct?"
        else:
            if conf == "yes":
                s.service = s.temp_service
                s.service_confirmed = True
                s.step = 6
                return "Pearl Dental Clinic currently has one location. Your appointment will be at our Nagarbhavi clinic. Is that okay?"
            elif conf == "no":
                rejected = s.temp_service
                s.temp_service = ""
                corrected = _match_service(entities.get("dental_service") or user_text)
                if corrected and corrected != rejected:
                    s.temp_service = corrected
                    return _service_prompt(s, lead="Sorry about that.")
                return "No problem. Which dental service would you like to book?"
            else:
                return f"Just to confirm, you'd like to book a {s.temp_service}. Is that correct?"

    # --- STEP 6: LOCATION CONFIRMATION ---
    elif s.step == 6:
        if conf == "yes":
            s.location_confirmed = True
            s.step = 7
            return _date_prompt(s)
        elif conf == "no":
            return "I understand, but Pearl Dental Clinic only operates at our Nagarbhavi clinic. Is it okay to schedule your appointment there?"
        else:
            return "Your appointment will be at our Nagarbhavi clinic. Is that okay?"

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
                return "No problem. Which date would you prefer for your appointment?"
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
            is_available, alts = backend_actions.check_availability(s.date_str, s.time_str)
            if is_available:
                success, msg = backend_actions.book_appointment(
                    s.name, s.phone, s.service, s.date_str, s.time_str
                )
                if not success:
                    s.step = 9
                    return "I couldn't secure that slot just now. Would you like me to check another time?"
                s.booking_confirmed = True
                s.step = 11
                dt_obj = datetime.strptime(s.date_str, "%Y-%m-%d")
                formatted_date = dt_obj.strftime("%A, %d %B %Y")
                return _confirmation_message(s.service, formatted_date, s.time_str)
            else:
                s.offering_alternatives = True
                s.alternative_slots = alts
                if len(alts) >= 2:
                    return (
                        f"Unfortunately that slot isn't available. "
                        f"Would either {alts[0]} or {alts[1]} work instead?"
                    )
                elif len(alts) == 1:
                    return (
                        f"Unfortunately that slot isn't available. "
                        f"Would {alts[0]} work instead?"
                    )
                else:
                    return (
                        "Unfortunately that slot isn't available and we don't have other open slots near that time. "
                        "Could you please choose another time or date?"
                    )
        elif conf == "no":
            return "Which details are incorrect? Please let me know what to update."
        else:
            return "Is everything correct? Please say yes to confirm or let me know what to change."

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
                    return "I couldn't secure that alternative just now. Could you choose another time or date?"
                s.booking_confirmed = True
                s.step = 11
                dt_obj = datetime.strptime(s.date_str, "%Y-%m-%d")
                formatted_date = dt_obj.strftime("%A, %d %B %Y")
                return _confirmation_message(s.service, formatted_date, s.time_str)
            else:
                if len(s.alternative_slots) >= 2:
                    return (
                        f"I'm sorry, I didn't catch that. "
                        f"Would either {s.alternative_slots[0]} or {s.alternative_slots[1]} work instead?"
                    )
                elif len(s.alternative_slots) == 1:
                    return (
                        f"I'm sorry, I didn't catch that. "
                        f"Would {s.alternative_slots[0]} work instead?"
                    )
                else:
                    return "Could you please choose another time or date?"
        else:
            s.step = 9
            return "Let me check availability again. Is everything correct?"

    # --- STEP 11: CLOSING CHECK ---
    elif s.step == 11:
        if conf == "no" or "nothing" in user_text.lower() or "no thanks" in user_text.lower() or "that's all" in user_text.lower() or "bye" in user_text.lower():
            s.step = 12
            s.closed_conversation = True
            return (
                "Thank you for choosing Pearl Dental Clinic. "
                "We look forward to seeing you. "
                "Have a wonderful day."
            )
        else:
            return (
                "I can assist only with scheduling appointments today. "
                "Since we've confirmed your booking, is there another appointment you'd like to schedule?"
            )

    # --- STEP 12: CLOSED ---
    elif s.step == 12:
        return "Thank you for choosing Pearl Dental Clinic. Have a wonderful day."

    return "Sorry, I didn't catch that. Could you please repeat?"


# ==========================================
# DIALOGUE MANAGEMENT â€” PUBLIC API
# ==========================================

def get_ai_response(user_text):
    """
    Sync version: Updates the global session state based on user_text NLU,
    runs backend validations, and returns Emma's next verbal response.
    Used by legacy main.py and run_demo.py.
    """
    if state.closed_conversation:
        return "Thank you for choosing Pearl Dental Clinic. Have a wonderful day."

    # Step 1 Check - Initial greeting
    if state.step == 1 and not state.greeting_spoken:
        if user_text:
            extracted = extract_entities_with_llm(user_text)
            conf = extracted.get("confirmation")
            if conf == "yes":
                state.greeting_spoken = True
                state.step = 2
                return "Wonderful! Let's get your appointment scheduled. May I have your full name, please?"
            elif conf == "no":
                state.greeting_spoken = True
                state.closed_conversation = True
                return "No problem. When would be a better time for me to call you back?"

        state.greeting_spoken = True
        return (
            "Hello! You've reached Pearl Dental Clinic. "
            "I'm Emma, your virtual dental assistant. "
            "I'll help you schedule your appointment today. "
            "Is this a good time to talk?"
        )

    # Extract slots and delegate to shared state machine
    entities = extract_entities_with_llm(user_text)
    return _handle_conversation_step(user_text, entities, state)



async def async_get_ai_response(user_text, session_state=None):
    """
    Process one appointment turn.

    Gemini is deliberately limited to NLU extraction.  All progression,
    validation, confirmation and booking decisions are made by the Python state
    machine below.  This prevents a model response from booking an appointment
    before the caller has explicitly confirmed the final recap.
    """
    s = session_state or state

    if s.closed_conversation:
        return "Thank you for choosing Pearl Dental Clinic. We look forward to seeing you. Have a wonderful day!"

    # Handle initial greeting when no user text has been spoken yet
    if s.step == 1 and not s.greeting_spoken and not user_text:
        s.greeting_spoken = True
        greeting = (
            "Hello! You've reached Pearl Dental Clinic. I'm Emma, your virtual dental assistant. "
            "I'll help you schedule your appointment today. Is this a good time to talk?"
        )
        s.history.append({"role": "assistant", "content": greeting})
        return greeting

    try:
        entities = await async_extract_entities_with_llm(user_text, s=s)
    except Exception as e:
        # The deterministic flow still handles simple confirmations even when
        # the NLU provider is unavailable.
        logger.error("NLU extraction failed: %s", e)
        entities = {}

    # Availability and calendar operations are synchronous today.  Keep them
    # off FastAPI's event loop so one calendar request cannot stall every call.
    directive = await asyncio.to_thread(_handle_conversation_step, user_text, entities, s)
    response = await generate_emma_response(
        directive,
        s,
        user_text=user_text,
        user_query=entities.get("user_query"),
    )
    logger.info("State-machine Emma response (step=%d): %s", s.step, response[:100])
    return response

