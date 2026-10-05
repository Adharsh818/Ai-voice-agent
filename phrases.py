"""
Fixed sentences Emma speaks, pre-rendered to PCM at startup.

A reply is split into sentences (speech.split_sentences); every sentence found
in the prompt cache plays instantly, and only the dynamic ones (names, numbers,
dates) go to live TTS, in parallel with the cached audio already playing.

Wording follows docs/NORTH_STAR.md: short, warm, receptionist phrasing; no
disclaimers or form-style recaps. tests/test_realism.py rejects banned phrases
in everything listed here, in ai_engine.py and in every prompts.VARIANTS line.
"""

import random

import config

# Short backchannels played while an LLM turn is still thinking.
FILLERS = ["Okay.", "Mm-hmm.", "Sure.", "Right."]
# Openers a reply may start with (R6); never the same one twice in a row.
OPENERS = ["Okay,", "Sure,", "Right,", "Alright,"]
# Played before the (slow) availability check and booking, with typing sounds.
CHECKING = "Let me just check that for you."
# Spoken when a turn fails unexpectedly.
ERROR_REPLY = "Sorry, could you say that again?"

FIXED_SENTENCES = [
    # greeting / step 1
    "Sure, I can help with that.", "May I have your full name, please?",
    "Sure.", "Would you like to book a visit?", "Hi there!", "How can I help you today?",
    "No problem at all.", "Just give us a call whenever you're ready.", "Take care!",
    # name / purpose
    "Thanks!", "And what can I do for you today?", "What can I do for you today?", "Sorry about that.",
    "Could you tell me your full name again?",
    "I can help you set up a visit.", "Would you like to book one?",
    # phone
    "Great.", "And what's the best mobile number to reach you on?",
    "Sorry, I think I missed a digit there.", "Could you say the number again?",
    "No problem.", "What's the best mobile number to reach you on?",
    # service
    "Thanks.", "And what's the visit for?", "What's the visit for?",
    "Sorry, which treatment is it for?",
    "A check-up, a cleaning, a filling, a root canal, an extraction, or braces?",
    # location
    f"And that'd be at our {config.DEFAULT_BRANCH} branch, is that okay?",
    f"That'd be at our {config.DEFAULT_BRANCH} branch, is that okay?",
    f"Is our {config.DEFAULT_BRANCH} branch okay for you?",
    # date / time
    "What day would suit you?", "What time works best for you?",
    "Could you please specify the date again?", "Could you choose another time?",
    "We're closed on Sundays.", "Could you please choose a date between Monday and Saturday?",
    "That date has already passed.", "Could you choose a future date?",
    "Our clinic operates from 7 AM to 9 PM.",
    "The clinic is closed for lunch break between 2 PM and 2:30 PM.",
    "Sorry, I didn't catch the date.", "Which day would you like to come in?",
    "Sorry, I didn't catch the time.",
    # recap / booking
    "Shall I book it?", "Shall I go ahead and book it?", "Sure, what should I change?",
    "Sorry, shall I go ahead and book that?",
    "Sorry, that slot just went.", "Shall I look at another time?",
    "Hmm, that time's full and there's nothing close to it that day.", "Would another time work?",
    "Sorry, that one just went too.", "What other time would work?", "What other time would work for you?",
    "Anything else I can help with?", "Sure, anything else I can help with?",
    "Let me just check that again.",
    # closing
    "Great, we'll see you then.", "Take care, bye!",
    "Sorry, I didn't catch that.", "Could you please repeat?", "How can I help you?",
]

_last_greeting = None


def next_greeting() -> str:
    """A greeting from config.GREETINGS, never the same one twice in a row (R1)."""
    global _last_greeting
    choices = [g for g in config.GREETINGS if g != _last_greeting] or config.GREETINGS
    _last_greeting = random.choice(choices)
    return _last_greeting


def all_phrases(extra=()):
    """
    Everything worth pre-rendering, de-duplicated, in a stable order: the old
    engine's fixed sentences, then the R2 engine's cached line variants
    (prompts.static_lines, one sentence each, so the call session's sentence
    lookup finds them). The R2 lines may add at most prompts.CACHE_CHAR_BUDGET
    characters in total: ElevenLabs characters are scarce, and a line that
    doesn't fit simply plays through live TTS.
    """
    import prompts                     # local: prompts imports phones/clock, keep this module light to import

    seen, out = set(), []
    for text in [*config.GREETINGS, config.HONEST_LINE, *FIXED_SENTENCES, *FILLERS, *OPENERS,
                 CHECKING, ERROR_REPLY, *extra]:
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    budget = prompts.CACHE_CHAR_BUDGET
    for text in prompts.static_lines():
        if text and text not in seen and len(text) <= budget:
            budget -= len(text)
            seen.add(text)
            out.append(text)
    return out
