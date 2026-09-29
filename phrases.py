"""
Fixed sentences Emma speaks, pre-rendered to PCM at startup.

A reply is split into sentences (speech.split_sentences); every sentence found
in the prompt cache plays instantly, and only the dynamic ones (names, numbers,
dates) go to live TTS — in parallel, while the cached audio is already playing.
Most replies open with one of these, so first audio is usually a cache hit.

Keep entries as complete sentences exactly as the templates in ai_engine.py
and backend_actions.py produce them. A sentence missing from this list still
works; it is just synthesised live.
"""

import config

# Short backchannels played while an LLM turn is still thinking.
FILLERS = ["Okay.", "Mm-hmm.", "Sure."]
# Played before the (slow) availability check and booking.
CHECKING = "Let me check that for you."
# Spoken when a turn fails unexpectedly.
ERROR_REPLY = "Sorry, I had a little trouble there. Could you say that again?"

FIXED_SENTENCES = [
    # greeting / step 1
    "Wonderful!", "Let's get your appointment scheduled.", "May I have your full name, please?",
    "I can help you book an appointment.", "Would you like to schedule one?",
    "No problem.", "When would be a better time for me to call you back?",
    "Would you like to book an appointment?",
    # name / purpose
    "Thank you!", "How may I help you today?", "Sorry about that.",
    "Could you please tell me your full name again?",
    "At the moment, I can assist only with scheduling appointments.",
    # phone
    "Great!", "I can help with that.", "What is the best mobile number to reach you?",
    "That doesn't appear to be a valid 10-digit Indian mobile number.", "Could you please repeat it?",
    "Is that correct?",
    # service
    "Thank you.", "Which dental service would you like to book?",
    "I'm sorry, but that service is currently unavailable through this booking assistant.",
    "We offer: General Check-up, Consultation, Teeth Cleaning, Tooth Filling, Root Canal Treatment, "
    "Tooth Extraction, Braces, Invisalign, and Pediatric Dentistry.",
    "Which of these would you like to book?",
    # location
    "Pearl Dental Clinic currently has one location.",
    "Your appointment will be at our Nagarbhavi clinic.", "Is that okay?",
    "I understand, but Pearl Dental Clinic only operates at our Nagarbhavi clinic.",
    "Is it okay to schedule your appointment there?",
    "Is it okay to book your appointment at our Nagarbhavi clinic?",
    # date / time
    "Which date would you prefer for your appointment?", "What time works best for you?",
    "Could you please specify the date again?", "Could you choose another time?",
    "We're closed on Sundays.", "Could you please choose a date between Monday and Saturday?",
    "That date has already passed.", "Could you choose a future date?",
    "Our clinic operates from 7 AM to 9 PM.",
    "The clinic is closed for lunch break between 2 PM and 2:30 PM.",
    "Sorry, I didn't catch the date.", "Which day would you like to come in?",
    "Sorry, I didn't catch the time.",
    # recap / booking
    "Before I book your appointment, let me confirm everything.",
    "Let me recap the updated details.", "Is everything correct?", "Is everything correct now?",
    "Which information is incorrect?", "Please tell me what to update.",
    "Which details are incorrect?", "Please let me know what to update.",
    "Please say yes to confirm or let me know what to change.",
    "Shall I go ahead and book the appointment with those details?",
    "I couldn't secure that slot just now.", "Would you like me to check another time?",
    "Your appointment has been successfully confirmed.",
    "Unfortunately that slot isn't available.", "I'm sorry, I didn't catch that.",
    "Could you please choose another time or date?",
    "I couldn't secure that alternative just now.", "Could you choose another time or date?",
    "Is there anything else I can help you with today?",
    "Is there anything else I can help you with?",
    # closing
    "Thank you for choosing Pearl Dental Clinic.", "We look forward to seeing you.",
    "Have a wonderful day.", "Have a wonderful day!",
    "I can assist only with scheduling appointments today.",
    "Since we've confirmed your booking, is there another appointment you'd like to schedule?",
    "Sorry, I didn't catch that.", "Could you please repeat?", "How can I help you?",
]


def all_phrases(extra=()):
    """Everything worth pre-rendering, de-duplicated, in a stable order."""
    seen, out = set(), []
    for text in [config.GREETING, *FIXED_SENTENCES, *FILLERS, CHECKING, ERROR_REPLY, *extra]:
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out
