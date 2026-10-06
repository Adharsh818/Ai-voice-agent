"""
Deterministic matchers shared by Tier-0 (tier0.understand) and apply.py.

Everything here is pure text work, fast and side-effect free: yes/no,
digits (Indian phrasing and Deepgram's US formatting), spelled names, fuzzy
name matching, services / branches / doctors against the DB catalog, and the
cut-off fragment detector. Returning None ("don't know") is always safe; a
wrong answer is not, so every rule stays conservative (plan 5.5).

Owner in Sprint 1b: E4 (with tier0.py). The yes/no parser moves here from
ai_engine._parse_confirmation, unchanged in behaviour, so the new engine does
not import the old one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Optional

import phones

# Spoken digit vocabulary. "oh" and "o" count only inside a run of digits.
DIGIT_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3",
    "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
MULTIPLIERS = {"double": 2, "triple": 3}
TENS_WORDS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
              "seventy": 70, "eighty": 80, "ninety": 90}
TEEN_WORDS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
              "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}

# A spoken name and a heard one count as the same person at or above this ratio.
NAME_MATCH_RATIO = 0.8
# Looser threshold for "is this the name we already heard, misrecognised?"
NAME_ECHO_RATIO = 0.72

# More digits than any Indian number (+91 and ten digits) means the buffer went wrong.
MAX_PHONE_DIGITS = 12


@dataclass(frozen=True)
class DigitRun:
    """Digits found in one utterance."""
    digits: str                  # "7899377462"
    complete: bool               # a whole number on its own (10-digit mobile / 11-digit landline after prefixes)
    too_many: bool = False       # more than 12 digits: ask again


@dataclass(frozen=True)
class CatalogMatch:
    """A service / branch / doctor match. `options` > 1 means ambiguous: ask which."""
    value: Optional[str]
    options: tuple = ()
    unknown_phrase: Optional[str] = None   # named something not in the catalog ("Dr Sharma", "whitening")


def _norm(text: str) -> str:
    """Lowercase, curly apostrophes straightened, whitespace collapsed."""
    return re.sub(r"\s+", " ", (text or "").lower().replace("’", "'")).strip()


def _words(text: str) -> list:
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", _norm(text))


# ---------------------------------------------------------------- yes / no
# Copied from ai_engine (not imported: the new engine never imports the old
# one). tests/test_match.py pins parity with ai_engine._parse_confirmation.

# A few idioms are affirmative even though they contain a negative word. They are
# matched first and win outright, so "no problem" is never heard as a refusal.
_AFFIRMATIVE_IDIOMS = (
    "no problem", "no worries", "not a problem", "no issue", "no doubt",
    "why not", "can't wait", "cant wait",
)

_TAG_NO = re.compile(r"[,\s]+(?:no|na|naa)\s*\?\s*$")
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


def parse_yes_no(text: str) -> Optional[str]:
    """
    "yes", "no" or None. Negation is tested before affirmation ("that's not
    right" is a no), affirmative idioms containing "no" are handled first
    ("no problem"), and matching is word-bounded. Same behaviour as
    ai_engine._parse_confirmation, which tests/test_booking_flow.py pins.
    """
    text = (text or "").lower().strip().replace("’", "'")
    if not text:
        return None
    tag = _TAG_NO.search(text)
    if tag and len(text[:tag.start()].split()) >= 3:
        # Indian-English tag question: "It's been raining a lot, no?" asks
        # for agreement; it is not a "no" to what Emma asked.
        text = text[:tag.start()]
    if any(idiom in text for idiom in _AFFIRMATIVE_IDIOMS):
        # The idiom only settles the turn if nothing else in the sentence is
        # negative: "no problem with the name, the date is wrong" is mixed.
        rest = text
        for idiom in _AFFIRMATIVE_IDIOMS:
            rest = rest.replace(idiom, " ")
        return "yes" if not _NEGATIVE_RE.search(rest) else None
    if _NEGATIVE_RE.search(text):
        return "no"
    if _AFFIRMATIVE_RE.search(text):
        return "yes"
    return None


# ---------------------------------------------------------------- digits


def _digit_tokens(text: str) -> list:
    """
    Tokens for the digit reader: single numerals (so "(789) 937-7462" and
    "98765-43210" read as plain digit runs, the punctuation ignored) and words.
    """
    t = _norm(text)
    t = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", t)        # "twenty-one", "mm-hmm"
    t = re.sub(r"\b(?:oh|o)\s*[,.!?]", " ", t)           # "Oh, nine eight": an interjection
    return re.findall(r"\d|[a-z]+", t)


def _is_digitish(tok: Optional[str]) -> bool:
    return bool(tok) and (tok.isdigit() or tok in MULTIPLIERS or tok in TENS_WORDS
                          or tok in TEEN_WORDS or (tok in DIGIT_WORDS and tok not in ("oh", "o")))


def _read_digits(text: str) -> str:
    """Every digit said, in order: numerals, digit words, double / triple, tens and teens."""
    toks = _digit_tokens(text)
    out, repeat, i = [], 1, 0
    while i < len(toks):
        tok = toks[i]
        if tok in MULTIPLIERS:
            repeat = MULTIPLIERS[tok]
            i += 1
            continue
        if tok in TENS_WORDS:
            value = TENS_WORDS[tok]
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            unit = DIGIT_WORDS.get(nxt) if nxt not in ("oh", "o", "zero") else None
            if unit and unit != "0":
                value += int(unit)          # "ninety eight" -> 98
                i += 1
            out.append(str(value) * repeat)
            repeat = 1
            i += 1
            continue
        if tok in TEEN_WORDS:
            out.append(str(TEEN_WORDS[tok]) * repeat)
            repeat = 1
            i += 1
            continue
        digit = tok if tok.isdigit() else DIGIT_WORDS.get(tok)
        if digit is not None and tok in ("oh", "o"):
            # "oh" is a digit only inside a run: "nine oh two", not "oh okay".
            prev = toks[i - 1] if i > 0 else None
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            if not (_is_digitish(prev) or _is_digitish(nxt) or nxt in ("oh", "o")):
                digit = None
        if digit is None:
            repeat = 1
            i += 1
            continue
        out.append(digit * repeat)
        repeat = 1
        i += 1
    return "".join(out)


def _strip_prefix(digits: str) -> str:
    """Drop a +91 / 91 / 0 prefix when what remains is a 10-digit mobile number."""
    if len(digits) == 12 and digits.startswith("91") and digits[2] in "6789":
        return digits[2:]
    if len(digits) == 13 and digits.startswith("091") and digits[3] in "6789":
        return digits[3:]
    if len(digits) == 11 and digits.startswith("0") and digits[1] in "6789":
        return digits[1:]
    return digits


def _whole(digits: str) -> bool:
    return 10 <= len(digits) <= MAX_PHONE_DIGITS and phones.to_e164(digits) is not None


def extract_digits(text: str) -> DigitRun:
    """
    Digits in `text`: numerals, spoken digits, "double nine", "triple zero",
    tens words ("ninety-eight" -> 98), and formatted numbers such as Deepgram's
    "(789) 937-7462" or "+91 98765-43210". A +91 / 91 / 0 prefix is stripped
    when what remains is a whole number.
    """
    digits = _read_digits(text)
    if len(digits) > MAX_PHONE_DIGITS:
        return DigitRun(digits, complete=False, too_many=True)
    digits = _strip_prefix(digits)
    return DigitRun(digits, complete=_whole(digits))


def accumulate_phone(buffer: str, run: DigitRun) -> tuple[str, Optional[str]]:
    """
    Add this turn's digits to the per-call buffer (Caller.phone_buffer).
    Returns (new buffer, e164 or None). A complete number on its own replaces
    the buffer rather than appending (the caller started again). More than 12
    buffered digits resets the buffer and returns ("", None); the caller hears
    the too-many-digits line.
    """
    if run.too_many:
        return "", None
    if not run.digits:
        return buffer or "", None
    if run.complete:
        return run.digits, phones.to_e164(run.digits)
    combined = (buffer or "") + run.digits
    if len(combined) > MAX_PHONE_DIGITS:
        return "", None
    combined = _strip_prefix(combined)
    if _whole(combined):
        return combined, phones.to_e164(combined)
    return combined, None


# ---------------------------------------------------------------- spelling and names

# How letters are said when spelling: "bee", "aitch", "double you"...
LETTER_NAMES = {
    "ay": "a", "bee": "b", "be": "b", "see": "c", "cee": "c", "dee": "d", "ee": "e",
    "eff": "f", "ef": "f", "gee": "g", "aitch": "h", "edge": "h", "eich": "h", "jay": "j",
    "kay": "k", "el": "l", "ell": "l", "em": "m", "en": "n", "pee": "p", "cue": "q",
    "queue": "q", "ar": "r", "are": "r", "ess": "s", "es": "s", "tee": "t", "tea": "t",
    "vee": "v", "ex": "x", "why": "y", "wy": "y", "zed": "z", "zee": "z",
}
_SPELL_LEADS = re.compile(
    r"^(?:(?:yes|yeah|okay|ok|sure|so|um+|uh+)[ ,]+)*"
    r"(?:(?:it'?s|it is|that'?s|that is|the spelling is|spelling is|spelled|spelt|"
    r"i spell it|you spell it|my name is|it'?s spelled|it is spelled)[ ,:]+)?"
)
_SPELL_FILLERS = {"and", "then", "um", "umm", "uh", "comma", "space", "dot", "next", "is"}


def join_spelled(text: str) -> Optional[str]:
    """
    Letters spelled out ("A D H A R S H", "a for apple, d, h...", "double s")
    joined into a name ("Adharsh"), or None if `text` is not a spelling.
    """
    t = _norm(text)
    t = _SPELL_LEADS.sub("", t)
    toks = re.findall(r"[a-z]+", t.replace("-", " ").replace(".", " "))
    letters, i = [], 0
    while i < len(toks):
        tok = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if tok in ("double", "triple"):
            if nxt in ("u", "you"):           # "double u" is the letter W
                letters.append("w")
                i += 2
                continue
            letter = nxt if nxt and len(nxt) == 1 else LETTER_NAMES.get(nxt or "")
            if not letter:
                return None
            letters.append(letter * MULTIPLIERS[tok])
            i += 2
            continue
        letter = tok if len(tok) == 1 else LETTER_NAMES.get(tok)
        if letter:
            letters.append(letter)
            # "a for apple" / "d as in delhi": skip the example word.
            if nxt == "for" and i + 2 < len(toks) and toks[i + 2].startswith(letter):
                i += 3
                continue
            if nxt == "as" and i + 3 < len(toks) and toks[i + 2] == "in" and toks[i + 3].startswith(letter):
                i += 4
                continue
            i += 1
            continue
        if tok in _SPELL_FILLERS:
            i += 1
            continue
        return None
    word = "".join(letters)
    singles = sum(1 for tok in toks if len(tok) == 1)
    if len(word) < 2 or singles < 1:
        return None                         # "why are" is words, not the letters Y R
    return word.capitalize()


# Words that are never part of a spoken name (dates, services, pleasantries,
# function words). The old engine's _NON_NAME_WORDS plus what R2 needs.
NON_NAME_WORDS = {
    "book", "booking", "appointment", "appointments", "schedule", "scheduling",
    "dentist", "dental", "clinic", "doctor", "checkup", "check-up", "cleaning",
    "filling", "canal", "root", "braces", "invisalign", "extraction",
    "consultation", "pediatric", "today", "tomorrow", "yesterday", "monday",
    "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "next",
    "week", "weekend", "morning", "afternoon", "evening", "night", "noon",
    "am", "pm", "oclock", "please", "want", "need", "hello", "thanks", "thank",
    "sorry", "what", "when", "where", "how", "help", "pain", "tooth", "teeth",
    "toothache", "hurts", "hurting",
    # R2 additions
    "yes", "no", "yeah", "okay", "ok", "sure", "the", "a", "an", "is", "are", "was",
    "my", "your", "our", "to", "for", "of", "and", "or", "but", "so", "because", "with",
    "who", "why", "which", "can", "could", "would", "will", "do", "does", "don't",
    "not", "it", "that", "this", "there", "here", "you", "me", "we", "they", "he", "she",
    "son", "daughter", "mother", "father", "wife", "husband", "child", "kid", "mom", "dad",
    "number", "phone", "name", "cancel", "reschedule", "change", "hi", "hey", "bye",
    "wait", "hold", "repeat", "again", "pardon", "nothing", "just", "actually", "also",
    "spell", "spelling", "price", "cost", "fee", "much", "time", "date", "branch",
    "address", "about", "like", "know", "think", "um", "uh", "hmm", "mm", "ah", "oh",
    # "I'm in a lot of pain", "I'm still here", "I'm calling about...": states, not names
    "in", "on", "at", "still", "fine", "good", "well", "calling", "looking", "available", "free",
    "busy", "afraid", "glad", "happy", "ready", "done", "back", "really", "very", "too", "lot",
    "having", "trying", "going", "feeling", "worried", "late", "early", "out", "away", "home",
    "travelling", "traveling", "new", "confused", "interested",
}
_NAME_LEADS = re.compile(
    r"^(?:(?:yes|yeah|yep|okay|ok|sure|hi|hello|so|um+|uh+|well|right|oh|ah|of course)[ ,]+)*"
    r"(?:(?:my name is|my name's|the name is|name is|it'?s|it is|this is|i am|i'm|"
    r"call me|you can call me|myself|put it under|under|"
    # The patient's name when booking for someone else ("Her name is Diya", demo rehearsal 7 Oct).
    r"(?:his|her|their)(?: name is| name's)|(?:she|he)(?:'s| is) called|"
    r"(?:my )?(?:daughter|son|child|kid|wife|husband|mother|mom|mum|father|dad|brother|sister)'?s name(?: is|'s))[ ,]+)?"
)
_NAME_TAILS = re.compile(r"(?:[ ,]+(?:here|speaking|please|thanks|thank you|only))+$")


def clean_name(text: str) -> Optional[str]:
    """
    A spoken name from an answer to "what's your name?": strips "my name is",
    "this is", fillers and punctuation, title-cases, rejects anything with
    digits, dates, services or more than six words. None if it isn't a name.
    """
    t = _norm(text)
    if not t or re.search(r"\d", t):
        return None
    t = re.sub(r"[^a-z' .,-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .,-")
    t = _NAME_LEADS.sub("", t)
    t = _NAME_TAILS.sub("", t).strip(" .,-")
    t = re.sub(r"\s*,\s*", " ", t)
    words = [w.strip(".'") for w in t.split() if w.strip(".'")]
    if not words or len(words) > 6:
        return None
    if len(re.sub(r"[^a-z]", "", "".join(words))) < 2:
        return None
    if any(w in NON_NAME_WORDS or w.replace("-", "") in NON_NAME_WORDS for w in words):
        return None
    if all(len(w) == 1 for w in words):
        return None                         # letters alone are a spelling, not a name
    return " ".join(w.title() if len(w) > 1 else w.upper() for w in words)


def _name_key(name: str) -> str:
    """A rough sound-alike key: aspirated consonants folded, doubles collapsed."""
    n = re.sub(r"[^a-z]", "", (name or "").lower())
    for a, b in (("ph", "f"), ("th", "t"), ("sh", "s"), ("dh", "d"), ("bh", "b"), ("kh", "k"),
                 ("gh", "g"), ("ch", "c"), ("ks", "x"), ("ee", "i"), ("oo", "u"), ("w", "v"),
                 ("z", "j"), ("q", "k")):
        n = n.replace(a, b)
    if n:
        n = n[0] + n[1:].replace("h", "")
    return re.sub(r"(.)\1+", r"\1", n)


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return max(SequenceMatcher(None, a, b).ratio(),
               SequenceMatcher(None, _name_key(a), _name_key(b)).ratio())


def name_similarity(a: str, b: str) -> float:
    """0..1 similarity of two names, tolerant of STT spellings (Adharsh / Adashar / Adrish)."""
    a_words = re.findall(r"[a-z]+", (a or "").lower())
    b_words = re.findall(r"[a-z]+", (b or "").lower())
    if not a_words or not b_words:
        return 0.0
    full = _ratio(" ".join(a_words), " ".join(b_words))
    if len(a_words) == len(b_words):
        # Word by word too, so one misheard surname doesn't sink a long name.
        per = sum(_ratio(x, y) for x, y in zip(a_words, b_words)) / len(a_words)
        return max(full, per)
    # "Priya" against "Priya Sharma": the shorter name is the start of the longer.
    short, long_ = (a_words, b_words) if len(a_words) < len(b_words) else (b_words, a_words)
    prefix = sum(_ratio(x, y) for x, y in zip(short, long_)) / len(short)
    return max(full, min(prefix, 0.9))


def closest_name(candidate: str, heard: list) -> Optional[str]:
    """The name in `heard` that `candidate` most likely is (ratio >= NAME_ECHO_RATIO), else None."""
    best, best_score = None, 0.0
    for name in heard or ():
        score = name_similarity(candidate, name)
        if score > best_score:
            best, best_score = name, score
    return best if best_score >= NAME_ECHO_RATIO else None


# ---------------------------------------------------------------- catalog

# Spoken phrases and STT slips -> canonical service name. Used only when that
# service exists in the catalog; the catalog's own aliases are added per call.
SERVICE_PHRASES = {
    "root canal": "Root Canal Treatment", "route canal": "Root Canal Treatment",
    "roots canal": "Root Canal Treatment", "root canal treatment": "Root Canal Treatment",
    "rct": "Root Canal Treatment", "nerve treatment": "Root Canal Treatment",
    "cleaning": "Teeth Cleaning", "teeth cleaning": "Teeth Cleaning", "scaling": "Teeth Cleaning",
    "polishing": "Teeth Cleaning", "teeth cleaned": "Teeth Cleaning", "clean my teeth": "Teeth Cleaning",
    "teeth clean": "Teeth Cleaning", "braces consultation": "Braces", "consultation for braces": "Braces",
    "invisalign consultation": "Invisalign", "see the dentist": "Consultation",
    "check up": "General Check-up", "checkup": "General Check-up", "general check": "General Check-up",
    "routine check": "General Check-up", "regular check": "General Check-up",
    "consultation": "Consultation", "consult": "Consultation", "see the doctor": "Consultation",
    "toothache": "Consultation", "tooth ache": "Consultation", "tooth pain": "Consultation",
    "filling": "Tooth Filling", "cavity": "Tooth Filling", "cavities": "Tooth Filling",
    "extraction": "Tooth Extraction", "pull out": "Tooth Extraction", "pulled out": "Tooth Extraction",
    "remove a tooth": "Tooth Extraction", "wisdom tooth": "Tooth Extraction",
    "braces": "Braces", "orthodontic": "Braces", "brace": "Braces",
    "invisalign": "Invisalign", "invisible braces": "Invisalign", "clear aligners": "Invisalign",
    "aligners": "Invisalign",
    "pediatric": "Pediatric Dentistry", "paediatric": "Pediatric Dentistry",
    "kids dentist": "Pediatric Dentistry", "child dentist": "Pediatric Dentistry",
    "children's dentist": "Pediatric Dentistry",
}
# STT slips that are ordinary words: only read as a service when Emma just asked for one.
ASKED_ONLY_PHRASES = {
    "chicken": "General Check-up", "check in": "General Check-up", "chick up": "General Check-up",
    "clean up": "Teeth Cleaning", "cleanup": "Teeth Cleaning",
}
# "tooth" alone: which one? (CLARIFY_SERVICE)
TOOTH_OPTIONS = ("Tooth Filling", "Tooth Extraction", "General Check-up")
_TOOTH_RE = re.compile(r"\b(tooth|teeth|molar|dental problem|tooth problem)\b")
# Treatments the demo clinic doesn't book directly: BOOK offers a Consultation.
UNKNOWN_TREATMENTS = (
    "teeth whitening", "tooth whitening", "whitening", "bleaching", "dental implants", "dental implant",
    "implants", "implant", "crowns", "crown", "veneers", "veneer", "dentures", "denture",
    "bridge", "gum treatment", "gum surgery", "smile design", "smile makeover", "bonding",
    "laser treatment", "tooth jewellery", "tooth jewelry", "x-ray", "xray", "jaw surgery",
)


@lru_cache(maxsize=1024)
def _spaced(text: str) -> str:
    """Hyphens and punctuation to spaces, for phrase matching ("check-up" == "check up")."""
    return " " + re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]", " ", _norm(text))) + " "


def _phrase_hits(text: str, table: dict) -> list:
    """
    Canonical values whose phrases appear in `text`, longest phrase first so
    "invisible braces" wins over "braces" and "root canal treatment" counts once.
    """
    spaced = _spaced(text)
    taken, hits = [], []
    for phrase in sorted(table, key=len, reverse=True):
        p = _spaced(phrase).strip()
        if not p or p not in spaced:
            continue
        # Plain string search (no per-phrase regex): word bounded, optional plural.
        pos = spaced.find(" " + p, 0)
        while pos != -1:
            start, end = pos + 1, pos + 1 + len(p)
            for suffix in ("", "s", "es"):
                if spaced.startswith(suffix + " ", end):
                    end += len(suffix)
                    break
            else:
                end = -1
            if end != -1 and not any(a < end and start < b for a, b in taken):
                taken.append((start, end))
                if table[phrase] not in hits:
                    hits.append(table[phrase])
            pos = spaced.find(" " + p, pos + 1)
    return hits


def match_service(text: str, services: list, *, asked: bool = False) -> CatalogMatch:
    """
    Match a phrase against facts.Service entries (name + aliases, word
    bounded; tolerant of STT slips like "route canal" and "chicken" for
    "check-up" only when the pending question was the service, `asked`).
    "tooth" alone is ambiguous: options = filling, extraction, check-up. A
    treatment the clinic doesn't list ("whitening", "implant") -> value None,
    unknown_phrase set (BOOK offers a Consultation).
    """
    names = {s.name for s in services or ()}
    if not names or not _norm(text):
        return CatalogMatch(None)
    table = {}
    for phrase, canon in SERVICE_PHRASES.items():
        if canon in names:
            table[phrase] = canon
    if asked:
        for phrase, canon in ASKED_ONLY_PHRASES.items():
            if canon in names:
                table[phrase] = canon
    for svc in services:
        table[svc.name.lower()] = svc.name
        for alias in svc.aliases or ():
            if alias and len(alias) >= 3:
                table[alias.lower()] = svc.name
    hits = _phrase_hits(text, table)
    if len(hits) == 1:
        return CatalogMatch(hits[0])
    if len(hits) > 1:
        return CatalogMatch(None, tuple(hits))
    # A treatment the catalog doesn't have.
    spaced = _spaced(text)
    for phrase in UNKNOWN_TREATMENTS:
        if " " + _spaced(phrase).strip() + " " in spaced:
            return CatalogMatch(None, unknown_phrase=phrase)
    if _TOOTH_RE.search(_norm(text)):
        options = tuple(o for o in TOOTH_OPTIONS if o in names)
        if len(options) > 1:
            return CatalogMatch(None, options)
    return CatalogMatch(None)


_CITY_WORDS = {"bengaluru", "bangalore", "blr", "city"}
_BRANCH_WORD_RE = re.compile(r"\b([a-z]+)\s+(?:branch|clinic|centre|center|side|location)\b")
_NOT_A_PLACE = {
    "your", "the", "which", "nearest", "closest", "main", "any", "other", "that", "this",
    "new", "a", "an", "dental", "pearl", "same", "one", "my", "our", "another", "different",
    "each", "every", "which", "what", "big", "small", "best", "good", "nearby", "local",
}


def _compact(text: str) -> str:
    return re.sub(r"[^a-z]", "", (text or "").lower())


def _close(a: str, b: str, threshold: float) -> bool:
    """SequenceMatcher ratio >= threshold, with the cheap upper bounds tried first (Tier-0 runs every turn)."""
    if 2 * min(len(a), len(b)) < threshold * (len(a) + len(b)):
        return False
    if a[0] != b[0] and a[-1] != b[-1]:
        return False                        # an STT slip keeps one end of the word
    sm = SequenceMatcher(None, a, b)
    return sm.real_quick_ratio() >= threshold and sm.quick_ratio() >= threshold and sm.ratio() >= threshold


def match_branch(text: str, branches: list) -> CatalogMatch:
    """
    Match a phrase against facts.Branch names and areas ("near the metro" is
    not a match). Spacing slips ("indira nagar", "white field") and small STT
    misspellings ("nagarabhavi") still match; "<word> branch" naming an
    unknown place comes back as unknown_phrase.
    """
    words = re.findall(r"[a-z]+", _norm(text))
    if not words or not branches:
        return CatalogMatch(None)
    hits = []
    for br in branches:
        keys = {_compact(br.name)}
        area = (br.area or "").split(",")[0]
        if _compact(area) and _compact(area) not in _CITY_WORDS:
            keys.add(_compact(area))
        found = False
        for n in (1, 2, 3):
            for i in range(len(words) - n + 1):
                window = "".join(words[i:i + n])
                if len(window) < 4:
                    continue
                for key in keys:
                    if window == key or (len(key) >= 6 and _close(window, key, 0.86)):
                        found = True
                        break
                if found:
                    break
            if found:
                break
        if found and br.name not in hits:
            hits.append(br.name)
    if len(hits) == 1:
        return CatalogMatch(hits[0])
    if len(hits) > 1:
        return CatalogMatch(None, tuple(hits))
    m = _BRANCH_WORD_RE.search(_norm(text))
    if m and m.group(1) not in _NOT_A_PLACE and len(m.group(1)) >= 4:
        return CatalogMatch(None, unknown_phrase=m.group(1).title())
    return CatalogMatch(None)


_TITLE_RE = re.compile(r"\b(?:dr|doctor|doc)\b\.?\s+([a-z]+)(?:\s+([a-z]+))?")
# Words that can follow "doctor" without being a name ("doctor appointment", "doctor is").
_NOT_A_DOCTOR_NAME = {
    "appointment", "appointments", "visit", "please", "is", "are", "was", "were", "will",
    "would", "can", "could", "should", "may", "might", "available", "for", "to", "at", "on",
    "in", "who", "which", "what", "when", "where", "how", "and", "or", "but", "the", "a",
    "an", "about", "said", "told", "says", "say", "there", "here", "today", "tomorrow",
    "sahab", "saab", "sahib", "sir", "madam", "maam", "mam", "ji", "if", "that", "this",
    "with", "has", "have", "had", "does", "do", "did", "only", "also", "too", "it", "me",
    "you", "him", "her", "them", "i", "we", "they", "he", "she", "first", "again", "now",
    "then", "so", "because", "consultation", "check", "checkup", "fee", "fees", "charges",
    "time", "timings", "slot", "of", "from", "by", "near", "any", "anyone", "someone",
    "good", "best", "lady", "female", "male", "gents", "office", "visiting", "not", "no",
    "yes", "okay", "ok", "like", "prefer", "want", "need", "monday", "tuesday", "wednesday",
    "thursday", "friday", "saturday", "sunday", "morning", "evening", "afternoon", "am", "pm",
}


def match_doctor(text: str, doctors: list, *, allow_bare: bool = False) -> CatalogMatch:
    """
    Match "Dr Rao", "Doctor Meera", "Rao" against facts.Doctor entries.
    A doctor title with an unknown surname ("Dr Sharma") -> unknown_phrase.
    A bare surname ("Rao") only counts with allow_bare (an answer to a doctor
    question): otherwise "I'm Priya Nair" would pick Dr Nair.
    `value` is the doctor's spoken name ("Dr Rao").
    """
    t = _norm(text)
    if not t or not doctors:
        return CatalogMatch(None)
    names = []
    for d in doctors or ():
        parts = [p for p in re.findall(r"[a-z]+", d.name.lower()) if p not in ("dr", "doctor")]
        names.append((d, parts))

    def lookup(word: str, exact_only: bool = False) -> list:
        found = []
        for d, parts in names:
            if word in parts:
                found.append(d.spoken)
        if found or exact_only:
            return found
        for d, parts in names:
            if any(len(p) >= 4 and _close(word, p, 0.8) for p in parts):
                found.append(d.spoken)
        return found

    titled = False
    for m in _TITLE_RE.finditer(t):
        w1, w2 = m.group(1), m.group(2)
        if w1 in _NOT_A_DOCTOR_NAME:
            continue
        titled = True
        hits = lookup(w1)
        if w2 and w2 not in _NOT_A_DOCTOR_NAME:
            second = lookup(w2)
            if second and (not hits or set(second) & set(hits)):
                hits = [h for h in second if not hits or h in hits]
        hits = list(dict.fromkeys(hits))
        if len(hits) == 1:
            return CatalogMatch(hits[0])
        if len(hits) > 1:
            return CatalogMatch(None, tuple(hits))
        return CatalogMatch(None, unknown_phrase="Dr " + w1.title())
    if allow_bare and not titled:
        hits = []
        for w in re.findall(r"[a-z]+", t):
            if w in _NOT_A_DOCTOR_NAME or len(w) < 3:
                continue
            hits.extend(lookup(w, exact_only=True))
        hits = list(dict.fromkeys(hits))
        if len(hits) == 1:
            return CatalogMatch(hits[0])
        if len(hits) > 1:
            return CatalogMatch(None, tuple(hits))
    return CatalogMatch(None)


_FEMALE_RE = re.compile(
    r"\b(lady|ladies|female|woman|women|girl)\s+(doctor|dentist|doc|dr)\b|\b(doctor|dentist)\s+who\s+is\s+a\s+(lady|woman)\b"
)
_MALE_RE = re.compile(r"\b(male|gents|gent|man|gentleman|men)\s+(doctor|dentist|doc|dr)\b")


def doctor_gender(text: str) -> Optional[str]:
    """ "lady doctor", "female dentist" -> "female"; "male doctor", "gents doctor" -> "male". """
    t = _norm(text)
    female, male = bool(_FEMALE_RE.search(t)), bool(_MALE_RE.search(t))
    if female == male:
        return None
    if re.search(r"\b(not|no|don't need|doesn't have to be)\s+(a\s+)?(lady|female|woman|male|gents|man)\b", t):
        return None
    return "female" if female else "male"


# ---------------------------------------------------------------- fragments, backchannels, questions

# A turn ending on one of these was cut off: "Cancel the", "I want to", "November 22, at".
_DANGLING = {
    "the", "a", "an", "to", "for", "with", "at", "on", "in", "of", "from", "by", "about",
    "and", "but", "or", "so", "because", "if", "than", "my", "your", "our", "his", "her",
    "their", "what's", "whats", "which", "where's", "how's", "um", "uh", "umm", "uhh", "er",
    "like", "just", "also", "maybe", "best",
}
# Words in _DANGLING that also end whole sentences after these ("I think so",
# "can I come in", "hold on", "whichever is best"). "that", "this", "some",
# "any" and "her" are not in _DANGLING at all: "I'd like that", "book her".
_COMPLETE_AFTER = {
    "so": {"think", "guess", "hope", "suppose", "believe", "say", "said", "not"},
    "in": {"come", "drop", "walk", "pop", "fit", "check", "squeeze", "log", "sign", "get"},
    "on": {"hold", "go", "carry", "come", "hang", "later", "and"},
    "best": {"is", "was", "be", "works", "work", "seems", "sounds", "suits", "whichever", "what's", "think"},
}
_WH_WORDS = {"what", "when", "where", "which", "who", "how", "whose"}
_NUMBER_WORDS = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
                 "eleven", "twelve", "thirty", "fifteen", "forty-five", "o'clock"}
# Auxiliaries dangle unless a pronoun precedes them ("Yes, I can." is complete).
_AUX = {"is", "are", "was", "can", "could", "would", "should", "will", "do", "does", "did",
        "don't", "doesn't", "didn't", "can't", "won't", "have", "has", "am", "i'm", "it's",
        "let", "let's", "want", "need", "tell", "what", "how", "when", "why", "who"}
_PRONOUNS = {"i", "you", "we", "it", "he", "she", "they", "that", "this", "there"}
# Single words that are never a whole turn on their own.
_LONE_FRAGMENTS = {
    "but", "and", "so", "because", "actually", "i", "can", "could", "would", "what", "how",
    "is", "will", "don't", "do", "does", "if", "or", "then", "also", "well", "um", "uh", "umm",
    "uhh", "er", "like", "the", "my", "i'm", "it's", "tell", "let", "when", "where", "why",
    "which", "who", "maybe",
}
# Short real words that can end a sentence after "the" / "my" ("cancel the one").
_SHORT_WORDS = {
    "one", "day", "all", "kid", "son", "job", "gum", "gums", "jaw", "fee", "way", "end", "bit",
    "lot", "two", "six", "ten", "rct", "pm", "am", "mom", "dad", "app", "doc", "dr", "car",
    "bus", "tab", "eye", "ear", "lip", "box", "pin", "sir",
}
_VERB_STARTS = {"cancel", "book", "change", "reschedule", "move", "shift", "tell", "what's",
                "whats", "check", "give", "make", "fix", "get", "is", "can", "could", "what"}


def is_fragment(text: str, expect: str) -> bool:
    """
    True when `text` was cut off mid-sentence and has no usable answer:
    "What's the best", "Tell me what can you", "Cancel the com", "But",
    "Don't". Never True for a complete short answer to what Emma asked
    ("yes", "Monday", "Priya", digits while expect == "phone").
    """
    expect = getattr(expect, "value", expect) or "open"
    words = _words(text)
    if not words:
        return False
    last = words[-1]
    if expect == "phone" and _read_digits(text) and all(
            _is_digitish(w) or w.isdigit() or w in ("oh", "o", "and", "my", "number", "is", "it's")
            for w in words) and last not in _DANGLING:
        return False
    if expect == "spelling" and join_spelled(text):
        return False
    if len(words) == 1:
        if last in _LONE_FRAGMENTS:
            return True
        return False
    if is_backchannel(text):
        return False
    if (text or "").rstrip().endswith("?"):
        return False                        # the recogniser heard a finished question
    prev = words[-2]
    if last in ("am", "pm") and (any(c.isdigit() for c in prev) or prev in _NUMBER_WORDS):
        return False                        # "next Monday at 10 am" is a time, not "I am ..."
    if last in _DANGLING:
        return prev not in _COMPLETE_AFTER.get(last, ())
    if last in _AUX:
        if last in ("is", "are", "was", "were") and any(w in _WH_WORDS for w in words[:-2]):
            return False                    # "what time my appointment is", "where the clinic is"
        return prev not in _PRONOUNS
    if last == "you":
        # "Tell me what can you" / "how good you" dangle; "thank you", "how are you" don't.
        return prev in {"can", "could", "would", "will", "do", "did", "does", "good", "much", "well", "help"}
    if prev in {"the", "my", "your", "a", "an"} and len(last) <= 3 and last not in _SHORT_WORDS \
            and words[0] in _VERB_STARTS:
        return True                         # "Cancel the com"
    return False


_BACKCHANNEL_WORDS = {
    "mm", "mmm", "mhm", "mhmm", "hmm", "hm", "uh", "huh", "yeah", "yes", "yep", "yup", "ya",
    "okay", "ok", "right", "sure", "alright", "fine", "cool", "oh", "ah", "achha", "acha",
    "accha", "got", "it", "i", "see", "great", "good", "nice", "hmmm",
}
_BACKCHANNEL_RE = re.compile(r"^(?:m+-?h+m+|u+h+-?h+u+h+|m+-?m+|uh-huh|mm-hmm)$")


def is_backchannel(text: str) -> bool:
    """ "mm-hmm", "yeah", "okay", "right" (at most 2 words, nothing else)."""
    t = re.sub(r"[^a-z' -]", " ", _norm(text)).strip()
    toks = t.split()
    if not toks or len(toks) > 2:
        return False
    for tok in toks:
        if _BACKCHANNEL_RE.match(tok):
            continue
        if tok.replace("-", "") in _BACKCHANNEL_WORDS:
            continue
        return False
    joined = " ".join(toks)
    return joined not in ("it", "i", "got", "see") and not (len(toks) == 2 and toks[0] == "i" and toks[1] != "see")


_QUESTION_RE = re.compile(
    r"\b(what|where|which|who|why|how|when|do you|does|can you|could you|is there|"
    r"are you|price|prices|cost|costs|charge|charges|fee|fees|insurance|address|located|"
    r"location|parking|open|hours|timings|how much|tell me about)\b"
)


# "Can I book a cleaning around 6?" / "Is it possible to get an appointment?" ask for a booking, not a fact.
_REQUEST_ASK_RE = re.compile(
    r"\b((can|could|may) (i|you|we)( please)? (book|get|have|make|schedule|fix|come)|"
    r"(is|would) it (be )?possible to (book|get|have|make|schedule|fix|come))\b")
# "Hello? Yes, I'm still here." answers "are you still there?"; the "?" is the line, not a question.
_HELLO_RE = re.compile(r"^\s*(hello|hi|hey|hallo)\s*\?+\s*")


def looks_like_question(text: str) -> bool:
    """Cheap question detector (question words, "?", price / hours / address words)."""
    lower = _HELLO_RE.sub("", _norm(text))
    if _REQUEST_ASK_RE.search(lower):
        # The request itself isn't a question; one asked beside it still is
        # ("Can I book a check-up, and where is your Jayanagar branch?").
        lower = _REQUEST_ASK_RE.sub(" ", lower).replace("?", " ")
    return "?" in lower or bool(_QUESTION_RE.search(lower))
