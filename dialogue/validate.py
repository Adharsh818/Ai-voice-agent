"""
Reply validators: the guardrails between the model's words and the caller
(docs/R2_DESIGN.md, section 8.2; plan 0.2).

Every sentence of the model's `say` and `ask` is checked as it streams, before
it can be spoken. A failing sentence is dropped (never edited); if the `ask`
is dropped, or the model's goal differs from Python's, Emma speaks the
pre-written line for Python's goal instead. Commit-critical lines never come
from the model at all.

Checks (each has unit tests, positive and negative):
  V1  facts     every number, price, date, weekday, time, doctor, branch and
                person name must be in the turn's allow-list (the brief's
                knowledge, the offered slots, the caller's own words)
  V1b prices    a price stated for a service must match that service's fact
  V2  wording   no bot / AI / automated / assistant wording (unless this turn
                answers a sincere robot question), no "real person",
                "transfer", "connect you", no promises of a callback unless
                a task was created, no doctor deflection unless clinical
  V3  claims    no "booked", "cancelled", "moved", "confirmed", "all set"
                unless Python committed that action this turn
  V4  shape     `say` has no question; the whole reply has at most one
  V5  length    say <= 2 sentences, reply <= 40 words, sentence <= 25 words
  V6  medical   no medicine names, doses, diagnoses or "you need a ..."
  V7  repeat    not >= 0.85 similar to any of Emma's recent sentences, and
                not repeating a notice Python is about to say
  V8  language  English (Latin script) only; no markup, URLs, JSON

The patterns are deliberately phrase-shaped rather than single words: the
model explains dental treatments in plain words ("the X-ray machine", "a
cancelled appointment costs nothing"), and a validator that drops every
answer containing a common word just swaps one robotic reply for another.

Owner in Sprint 1b: E2. Pattern lists are data and may be extended; the
function signatures are the contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from typing import Optional

SIMILARITY_LIMIT = 0.85
MAX_SAY_SENTENCES = 2
MAX_REPLY_WORDS = 40
MAX_SENTENCE_WORDS = 25

# V2: never in model text (the honesty turn is answered by config.HONEST_LINE, a notice).
BOT_WORDS = (
    r"\bbots?\b", r"\bchat ?bot", r"\brobot", r"\ba\.?i\.?\b", r"artificial intelligence", r"\bautomated\b",
    r"\bautomatic (system|voice|receptionist)", r"\b(virtual|digital|ai|automated|voice) (assistant|receptionist|agent)\b",
    r"\bi'?m (an? |the |your )?(\w+ )?assistant\b", r"\bi am (an? |the |your )?(\w+ )?assistant\b",
    r"language model", r"\b(i'?m|i am|this is) (just |only )?(a |an )?(machine|computer|program|software)\b",
    # About Emma herself only: "it's not a real emergency" is ordinary speech.
    r"\b(i'?m|i am) not (a )?(real|human)\b", r"\bnot an? (real )?(person|human)\b",
)
HANDOFF_WORDS = (
    r"real person", r"\bhuman\b", r"transfer you", r"connect you", r"put you through",
    r"speak (to|with) (a|the|our) (staff|receptionist|manager|front desk|team)", r"hold the line",
    r"let me get (someone|somebody|a colleague)", r"pass you (on|over) to",
)
CALLBACK_PROMISES = (
    r"\bcall you back\b", r"\bwill call you\b", r"\b(we'?ll|i'?ll|they'?ll|someone will) (give you a )?call\b",
    r"\bget back to you\b", r"\breach out\b", r"\b(team|staff|someone|somebody) will (call|contact|ring)\b",
    r"\b(team|staff|someone|somebody) (can|could|would|should|shall) (call|contact|ring) you\b",
    # "Let me have someone call you" / "I'll get the doctor's team to ring you". "What should I call
    # you?" is a name question and has no one being sent to call.
    r"\b(have|get|ask) (someone|somebody|a colleague|the clinic|the doctor|(the|our|\w+'s) team)\b[^.?!]*"
    r"\b(call|ring|phone|contact) you\b",
)
DEFLECTION = (
    r"\b(doctor|dentist|dr\.? \w+) (will|can|would|could|should) (go through|discuss|explain|check|tell|advise|"
    r"talk you through|look at|answer|let you know|guide)",
    r"\b(ask|consult|check with|speak to|talk to) (the|your|a|our) (doctor|dentist)\b",
    r"\b(best|better) (to )?(ask|discuss(ed)?|check(ed)?|answered)\b.*\b(doctor|dentist)\b",
    r"\bdiscuss (that|this|it) (with (the|your) (doctor|dentist)|at your (visit|appointment))",
    r"\b(doctor|dentist) (is|would be) (the )?best (person|placed)\b",
)
# V2b: the machinery behind the call. 8 Oct: "Ah, just a bit of a mix-up with the speech-to-text."
MACHINERY = (
    r"\bspeech[- ]to[- ]text\b", r"\btranscri(be|bed|ption|pt)\b", r"\b(my|the|our) (system|software|program)\b",
    r"\b(technical|system) (glitch|issue|problem|error)\b", r"\bglitch\b", r"\b(voice|speech) recognition\b",
    r"\b(my|the) (microphone|audio) (picked|didn'?t pick|is|was)\b",
)
# V9: clinic policies with no fact behind them (8 Oct: "If I don't turn up, what will happen?" ->
# "Nothing serious, we just prefer a quick call..."). Topic -> the fact id that would allow it.
POLICY_TOPICS = {
    "policy.no_show": (r"\bno[- ]shows?\b", r"\b(don'?t|do not|didn'?t|can'?t) (turn|show) up\b",
                       r"\bmiss(ed|ing)? (your|the|an|their) appointment\b",
                       r"\bif you (can'?t|cannot|don'?t|do not) (make it|come|turn up)\b"),
    "policy.late": (r"\b(arrive|arriving|come|coming|running) late\b", r"\blate (arrival|fee|charge)s?\b"),
    "policy.penalty": (r"\bpenalt(y|ies)\b", r"\bfined?\b"),
    "policy.refund": (r"\brefunds?\b", r"\brefunded\b"),
    "policy.deposit": (r"\bdeposits?\b", r"\badvance (payment|fee|amount)\b", r"\bpay (in advance|upfront)\b"),
}
# V3: outcome words, allowed only when that action committed this turn.
CLAIMS = {
    "booked": (
        r"\b(i'?ve|i have|we'?ve|you'?re|you are|it'?s|that'?s|is|has been|been|all|now) (all )?booked\b",
        r"\bbooked (it|that|this|you|your)\b", r"\bbooking is (done|confirmed|complete)\b", r"\ball set\b",
        r"\b(appointment|slot|booking) is (now )?(fixed|set|done|booked)\b",
    ),
    "cancelled": (
        r"\b(i'?ve|i have|we'?ve|it'?s|it is|that'?s|is|has been|been|now) cancell?ed\b",
        r"\bcancell?ed (it|that|this|your)\b",
    ),
    "rescheduled": (
        r"\b(i'?ve|i have|we'?ve|it'?s|that'?s|is|has been|been|now) (moved|rescheduled|shifted|preponed|postponed)\b",
        r"\b(moved|rescheduled|shifted) (it|that|this|your)\b",
        # A change acknowledged as made (8 Oct, after booking 8:00: "Seven" -> "Ah, seven
        # instead of eight, got it." and nothing changed). Python asks "Move it to 7?" itself.
        r"\binstead( of [^,.!?]+)?,? (got it|noted|done|that'?s fine|no problem|perfect)\b",
        r"\b(got it|noted|done|perfect|okay|sure)[,!]? [^.?!]*\binstead\b(?![^.?!]*\?)",
        r"\b(i'?ve |i have )?(changed|updated|switched|swapped|amended) (it|that|this|your|the)\b",
    ),
    "any": (
        r"\b(is|been|it'?s|that'?s|all|now|you'?re) confirmed\b", r"\bconfirmed (it|that|this|your|the)\b",
        r"\bi'?ve (booked|cancell?ed|moved|reserved|scheduled|fixed)\b",
        r"\bi (have|just) (booked|cancell?ed|moved|reserved|scheduled)\b",
    ),
}
# V6
MEDICAL = (
    r"\b\d+\s?(mg|ml|milligrams?)\b", r"\btablets?\b", r"\bcapsules?\b", r"\bparacetamol\b", r"\bibuprofen\b",
    r"\bantibiotics?\b", r"\bpainkillers?\b", r"\bpain killers?\b", r"\bdolo\b", r"\bcombiflam\b",
    r"\bamoxicillin\b", r"\bmetronidazole\b", r"\bdiclofenac\b", r"\bdosage\b", r"\bdoses?\b",
    r"\byou (have|probably have|might have|may have|could have) (an? |some )?(infection|abscess|cavity|decay|gum disease)\b",
    r"\b(sounds|looks) like (you have |it'?s |an? )*(infection|abscess|cavity|decay|gum disease)\b",
    r"\bit'?s (probably|likely|definitely|most likely) (an? )?(infection|abscess|cavity|decay)\b",
    r"\byou(?: need|'ll need| will need| would need| definitely need| may need| might need| probably need)"
    r" (an? )?(root canal|extraction|filling|crown|surgery|implant|antibiotics?)\b",
    r"\b(apply|take|rinse with)\b.*\b(times a day|every \d+ hours|twice a day)\b",
)
# V8: anything that isn't plain spoken English text.
NON_ENGLISH = (r"[ऀ-ॿ]", r"[ಀ-೿]", r"https?://", r"\bwww\.", r"[{}<>\[\]]", r"\*\*", r"`", r"#", r"\|", r"\\")
_LATIN_EXTRA = set("₹‘’“”–—…é")

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september",
           "october", "november", "december")
# "May I..." is a verb, not the month; only count it next to a number.
_DAY_RE = re.compile(r"\b(" + "|".join(_WEEKDAYS + tuple(m for m in _MONTHS if m != "may")) + r")s?\b", re.I)
_MAY_RE = re.compile(r"\b(\d{1,2}(st|nd|rd|th)? may|may \d{1,2})\b", re.I)
_DOCTOR_RE = re.compile(r"\b(?:Dr\.?|Doctor)\s+([A-Z][a-zA-Z]+)")
_BRANCH_RE = re.compile(r"\b([A-Z][a-z]+(?: [A-Z][a-z]+)?) (branch|clinic|centre|center)\b")
_NOT_PLACES = {"our", "the", "this", "that", "main", "nearest", "any", "every", "each", "other", "another",
               "your", "a", "dental", "pearl", "pearl dental", "which", "what", "same", "new", "closest"}
_VOCATIVE_RE = (
    re.compile(r"^(?:thanks|thank you|okay|ok|sure|great|perfect|got it|hi|hello|alright|right|lovely|"
               r"no problem|no worries|of course|wonderful|nice to meet you)[,!]?\s+([A-Z][a-z]+)\b"),
    re.compile(r",\s+([A-Z][a-z]+)[.!?]*$"),
)
_TITLE_RE = re.compile(r"\b(?:Mr|Mrs|Ms|Miss|Mister)\.?\s+[A-Z][a-z]+")
_PRICE_RE = re.compile(r"₹|\brs\.?\s?\d|\brupees?\b|\binr\b|\bcosts?\b|\bprice[sd]?\b|\bfees?\b|\bcharges?\b", re.I)

_OPENERS = re.compile(
    r"^(?:(?:okay|ok|sure|right|alright|all right|so|umm|um|uh|great|perfect|lovely|got it|no worries|"
    r"no problem|of course|mm-hmm|mhm|oh|ah|well|yes|yeah|absolutely|certainly)\b[\s,.!]*)+")


@dataclass
class Allowed:
    """What this turn's reply may mention (built by brief.build from the brief it sent)."""
    numbers: frozenset = frozenset()       # digits as strings: "400", "5", "30"
    doctors: frozenset = frozenset()       # "dr rao" (lower-case spoken names)
    branches: frozenset = frozenset()
    person_names: frozenset = frozenset()  # the caller's / patient's names heard this call
    days: frozenset = frozenset()          # weekday names and "5th"-style ordinals allowed
    prices: dict = field(default_factory=dict)   # facts.price_numbers
    committed: Optional[str] = None        # booked | cancelled | rescheduled committed this turn
    honesty_turn: bool = False             # the caller sincerely asked if Emma is a bot
    clinical: bool = False                 # the question was genuinely clinical
    callback_task: bool = False            # a callback task exists (a promise may be made)
    recent: tuple = ()                     # Emma's recent sentences and this turn's notices (V7)
    policies: frozenset = frozenset()      # "policy.*" fact ids in the knowledge base (V9)

    def for_turn(self, **changes) -> "Allowed":
        """
        A copy with this turn's facts that only exist after the head arrived
        (committed, clinical, honesty_turn, callback_task, notices added to
        recent): the engine calls it between apply/advance and composing, so
        the brief's allow-list itself is never mutated.
        """
        extra = changes.pop("notices", ())
        out = replace(self, **changes)
        if extra:
            out = replace(out, recent=tuple(out.recent) + tuple(extra))
        return out


@dataclass
class Verdict:
    ok: bool
    rule: str = ""                         # which check failed ("V1", "V3"...), for TurnTrace.dropped
    detail: str = ""                       # logged, never spoken


OK = Verdict(True)


def check_sentence(sentence: str, allowed: Allowed, *, part: str) -> Verdict:
    """V1-V3, V6-V8 on one sentence. `part` is "say" or "ask"."""
    allowed = allowed if allowed is not None else Allowed()
    text = re.sub(r"\s+", " ", (sentence or "").strip())
    if not text:
        return Verdict(False, "V5", "empty sentence")
    low = text.lower().replace("’", "'")

    # V8 first: a JSON fragment or another script would make every later check meaningless.
    for pattern in NON_ENGLISH:
        if re.search(pattern, text):
            return Verdict(False, "V8", f"non-speech text {pattern!r}")
    if any(ord(c) > 0x024F and c not in _LATIN_EXTRA for c in text):
        return Verdict(False, "V8", "non-Latin script")

    # V2: honesty and handoff wording.
    if not allowed.honesty_turn:
        hit = _first(BOT_WORDS, low)
        if hit:
            return Verdict(False, "V2", f"bot wording {hit!r}")
    hit = _first(HANDOFF_WORDS, low)
    if hit:
        return Verdict(False, "V2", f"handoff wording {hit!r}")
    if not allowed.callback_task:
        hit = _first(CALLBACK_PROMISES, low)
        if hit:
            return Verdict(False, "V2", f"callback promise {hit!r}")
    if not allowed.clinical:
        hit = _first(DEFLECTION, low)
        if hit:
            return Verdict(False, "V2", f"doctor deflection {hit!r}")
    hit = _first(MACHINERY, low)
    if hit:
        return Verdict(False, "V2", f"machinery wording {hit!r}")
    # "Got it, Mr Shetty" (8 Oct): a title guesses the caller's gender; Emma uses names only.
    m = _TITLE_RE.search(text)
    if m:
        return Verdict(False, "V2", f"title {m.group(0)!r}")

    # V9: a clinic policy with no fact behind it.
    for fact_id, patterns in POLICY_TOPICS.items():
        if fact_id in allowed.policies:
            continue
        hit = _first(patterns, low)
        if hit:
            return Verdict(False, "V9", f"policy without a fact ({fact_id}) {hit!r}")

    # V3: outcome claims Python didn't make.
    for action, patterns in CLAIMS.items():
        if allowed.committed and (action == "any" or action == allowed.committed):
            continue
        hit = _first(patterns, low)
        if hit:
            return Verdict(False, "V3", f"claims {action} {hit!r}")

    # V6: medical advice.
    hit = _first(MEDICAL, low)
    if hit:
        return Verdict(False, "V6", f"medical {hit!r}")

    # V1: facts.
    verdict = _check_facts(text, low, allowed)
    if not verdict.ok:
        return verdict

    # V7: loops and repeated notices.
    for previous in allowed.recent or ():
        score = similarity(text, previous)
        if score >= SIMILARITY_LIMIT:
            return Verdict(False, "V7", f"{score:.2f} similar to {previous[:60]!r}")
    return OK


def check_shape(say_sentences: list, ask: str) -> Verdict:
    """V4 and V5 across the whole reply (applied incrementally as sentences arrive)."""
    say_sentences = [s for s in (say_sentences or []) if s and s.strip()]
    ask = (ask or "").strip()
    for sentence in say_sentences:
        if "?" in sentence:
            return Verdict(False, "V4", "a question in say")
    if ask.count("?") > 1:
        return Verdict(False, "V4", "more than one question in ask")
    if len(say_sentences) > MAX_SAY_SENTENCES:
        return Verdict(False, "V5", f"say has {len(say_sentences)} sentences")
    ask_parts = [p for p in re.split(r"(?<=[.!?])\s+", ask) if p] if ask else []
    for sentence in say_sentences + ask_parts:
        if _word_count(sentence) > MAX_SENTENCE_WORDS:
            return Verdict(False, "V5", f"sentence of {_word_count(sentence)} words")
    total = sum(_word_count(s) for s in say_sentences) + _word_count(ask)
    if total > MAX_REPLY_WORDS:
        return Verdict(False, "V5", f"reply of {total} words")
    return OK


def similarity(a: str, b: str) -> float:
    """0..1 similarity of two sentences, normalised for case, punctuation and openers."""
    plain_a, plain_b = _plain(a), _plain(b)
    if not plain_a or not plain_b:
        return 0.0
    words_a, words_b = _strip_opener(plain_a).split(), _strip_opener(plain_b).split()
    if not words_a or not words_b:
        # Both were nothing but openers ("Okay." / "Okay."): compare them as said.
        return 1.0 if plain_a == plain_b else 0.0
    return SequenceMatcher(None, words_a, words_b, autojunk=False).ratio()


def numbers_in(text: str) -> frozenset:
    """Every number in `text` as digits: numerals, number words ("four hundred"), "1,500", "one and a half"."""
    return frozenset(_number_values(text))


# ---------------------------------------------------------------- helpers


def _first(patterns, low: str) -> Optional[str]:
    for pattern in patterns:
        match = re.search(pattern, low)
        if match:
            return match.group(0)
    return None


def _plain(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", (text or "").lower().replace("’", "'")))


def _strip_opener(plain: str) -> str:
    return _OPENERS.sub("", plain).strip()


def _word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9₹][A-Za-z0-9'’,.:₹-]*", text or ""))


def _check_facts(text: str, low: str, allowed: Allowed) -> Verdict:
    """V1 and V1b."""
    stated = _number_values(text, skip_lone_one=True)
    unknown = sorted(n for n in stated if n not in allowed.numbers)
    if unknown:
        return Verdict(False, "V1", f"number {unknown[0]} not in the brief")

    days = {d.lower() for d in allowed.days}
    for match in _DAY_RE.finditer(text):
        if match.group(1).lower() not in days:
            return Verdict(False, "V1", f"day {match.group(1)!r} not in the brief")
    if _MAY_RE.search(low) and "may" not in days:
        return Verdict(False, "V1", "month 'May' not in the brief")

    doctor_words = _name_words(allowed.doctors) | _name_words(allowed.person_names)
    for match in _DOCTOR_RE.finditer(text):
        if match.group(1).lower() not in doctor_words:
            return Verdict(False, "V1", f"doctor {match.group(1)!r} not in the brief")

    places = {b.lower() for b in allowed.branches} | _name_words(allowed.branches)
    for match in _BRANCH_RE.finditer(text):
        words = match.group(1).lower().split()
        while words and words[0] in _NOT_PLACES:
            words = words[1:]                     # "Our Indiranagar branch" -> "indiranagar"
        name = " ".join(words)
        if not words or name in _NOT_PLACES:
            continue
        if name not in places and not all(w in places for w in words):
            return Verdict(False, "V1", f"branch {match.group(1)!r} not in the brief")

    people = _name_words(allowed.person_names) | _name_words(allowed.doctors) | places | set(_WEEKDAYS) | set(_MONTHS)
    for pattern in _VOCATIVE_RE:
        match = pattern.search(text)
        if match and match.group(1).lower() not in people:
            return Verdict(False, "V1", f"name {match.group(1)!r} not heard this call")

    # V1b: a real number given as the price of the wrong service.
    if stated and allowed.prices and _PRICE_RE.search(low):
        expected = set()
        for key, numbers in allowed.prices.items():
            if _mentions(low, str(key).lower()):
                expected |= {str(n) for n in numbers}
        if expected:
            wrong = sorted(n for n in stated if n not in expected)
            if wrong:
                return Verdict(False, "V1b", f"price {wrong[0]} doesn't match the fact")
    return OK


def _name_words(names) -> set:
    out = set()
    for name in names or ():
        for word in re.findall(r"[a-z]+", str(name).lower()):
            if word not in ("dr", "doctor", "mr", "mrs", "ms"):
                out.add(word)
    return out


def _mentions(low: str, key: str) -> bool:
    """Does the sentence mention a price key ("root canal", "cleaning")? Plurals count."""
    words = set(re.findall(r"[a-z]+", low))
    words |= {w[:-1] for w in words if w.endswith("s")}
    key_words = [w[:-1] if w.endswith("s") and len(w) > 3 else w for w in re.findall(r"[a-z]+", key)]
    key_words = [w for w in key_words if w not in ("price", "fee", "cost", "or", "and", "the", "a")]
    return bool(key_words) and all(w in words for w in key_words)


# Number words -> values. Ordinals are included because dates are spoken that way ("the fifth").
_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14, "fifteenth": 15, "sixteenth": 16,
    "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "twentieth": 20, "thirtieth": 30,
}
_SCALES = {"hundred": 100, "thousand": 1000, "lakh": 100000, "lakhs": 100000, "crore": 10000000,
           "crores": 10000000, "million": 1000000}
_NUMBER_WORDS = set(_UNITS) | set(_TENS) | set(_SCALES)
_TOKEN_RE = re.compile(r"\d[\d,]*(?:\.\d+)?|[a-z]+(?:-[a-z]+)?")


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _number_values(text: str, skip_lone_one: bool = False) -> set:
    """
    Numbers in a sentence. skip_lone_one drops "one" used as a pronoun
    ("which one", "no one"): V1 would otherwise drop every natural reply
    that says "one", while "one hundred" or "one and a half" still count.
    """
    low = (text or "").lower().replace("’", "'")
    out = set()
    # "one and a half" / "two and a half hours".
    def half(match):
        word = match.group(1)
        base = _UNITS.get(word, _TENS.get(word))
        if base is None and word.isdigit():
            base = int(word)
        if base is None:
            return match.group(0)
        out.add(_fmt(base + 0.5))
        return " "
    low = re.sub(r"\b([a-z]+|\d+) and a half\b", half, low)
    # Clock times and ordinals: "5:30" -> 5, 30; "5th" -> 5.
    low = re.sub(r"(\d+):(\d+)", r"\1 \2", low)
    low = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", low)
    low = low.replace("o'clock", " ")

    tokens = _TOKEN_RE.findall(low)
    total, current, in_number, last_kind = 0, 0, False, None

    def flush():
        nonlocal total, current, in_number, last_kind
        if in_number:
            out.add(_fmt(total + current))
        total, current, in_number, last_kind = 0, 0, False, None

    for i, tok in enumerate(tokens):
        if tok[0].isdigit():
            flush()
            try:
                current = float(tok.replace(",", ""))
            except ValueError:
                continue
            in_number, last_kind = True, "unit"   # "5 thousand" scales; "5 30" is two numbers
            continue
        tok = tok.replace("-", " ")
        parts = tok.split() if " " in tok else [tok]
        for part in parts:
            if part == "one" and skip_lone_one and not in_number:
                nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
                prev = tokens[i - 1] if i > 0 else ""
                if nxt not in _NUMBER_WORDS and prev not in _NUMBER_WORDS:
                    continue
            if part in _UNITS:
                if in_number and last_kind in ("unit",):
                    flush()                       # "nine eight": two numbers, not ninety-eight
                current += _UNITS[part]
                in_number, last_kind = True, "unit"
            elif part in _TENS:
                if in_number and last_kind in ("unit", "tens"):
                    flush()
                current += _TENS[part]
                in_number, last_kind = True, "tens"
            elif part in _SCALES and not in_number and i > 0 and tokens[i - 1] == "a":
                current = _SCALES[part] if _SCALES[part] == 100 else 0
                total = 0 if _SCALES[part] == 100 else _SCALES[part]
                in_number, last_kind = True, "scale"   # "a hundred", "a thousand"
            elif part in _SCALES and in_number:
                scale = _SCALES[part]
                if scale == 100:
                    current = (current or 1) * 100
                else:
                    total += (current or 1) * scale
                    current = 0
                last_kind = "scale"
            elif part == "and" and in_number and i + 1 < len(tokens) and tokens[i + 1] in _NUMBER_WORDS:
                continue                          # "one thousand and fifty"
            else:
                flush()
    flush()
    return out
