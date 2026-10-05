"""
An adaptive, rule-based simulated caller.

Each caller has a goal (book, cancel, reschedule, questions only, emergency)
and a profile (name, phone, service, branch, day and time, an existing
appointment when managing one). Every turn it reads Emma's last line
(harness/lines.py), works out what she is asking, and answers the way a real
caller might, in varied spoken phrasing: several ways to say a number, a date
or a time, Indian-English turns of phrase ("kindly", "prepone", "tomorrow
itself"), plain and chatty styles. It checks every read-back against what it
meant and corrects Emma when she gets something wrong.

Seeded disruptions make the call messy the way real calls are: meta and
off-topic questions, non-answers, several details at once, self-corrections,
wrong-then-fixed numbers, changes of mind, intent switches (book, then cancel
instead), fragments cut off mid-sentence, silence, "are you a bot?", "can I
talk to a person?", and a barge-in over the final summary. Everything comes
from one random.Random, so a seed reproduces a call exactly; stops after
MAX_TURNS caller turns.

It also flags what the metrics cannot see from the transcript alone: when it
had to repeat something it had already given (M5), and whether it gave up.
"""

import random
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Optional

import clock

from harness import fake_nlu, lines

MAX_TURNS = 30

GOALS = ("book", "cancel", "reschedule", "questions", "emergency")
GOAL_WEIGHTS = (0.6, 0.12, 0.12, 0.08, 0.08)

FIRST_NAMES = [
    "Priya", "Rahul", "Ananya", "Arjun", "Kavitha", "Suresh", "Deepa", "Vikram", "Lakshmi", "Karthik", "Sneha",
    "Aditya", "Meenakshi", "Rohit", "Divya", "Sanjay", "Pooja", "Harish", "Nandini", "Manoj", "Shreya", "Ganesh",
    "Asha", "Naveen", "Bhavana", "Imran", "Farah", "Joseph", "Gurpreet", "Adharsh", "Srinivas", "Revathi",
]
LAST_NAMES = [
    "Sharma", "Kumar", "Patel", "Hegde", "Gowda", "Pillai", "Krishnan", "Joshi", "Singh", "Khan", "Bhat",
    "Naidu", "Verma", "Mishra", "Agarwal", "Das", "Banerjee", "Srinivasan", "Raghavan", "Prasad", "Murthy",
]
CHILD_NAMES = ["Aarav", "Diya", "Vihaan", "Saanvi", "Ishaan", "Anika", "Kabir", "Myra"]

SERVICE_PHRASES = {
    "General Check-up": ["a check-up", "a general check-up", "a routine check-up", "a dental check-up"],
    "Consultation": ["a consultation", "a consultation for a toothache", "to see the dentist about some tooth pain"],
    "Teeth Cleaning": ["a cleaning", "a teeth cleaning", "scaling and polishing", "to get my teeth cleaned"],
    "Tooth Filling": ["a filling", "a tooth filling", "a cavity filled"],
    "Tooth Extraction": ["an extraction", "a tooth extraction", "a wisdom tooth pulled out"],
    "Root Canal Treatment": ["a root canal", "root canal treatment", "an RCT"],
    "Braces": ["braces", "a braces consultation"],
    "Invisalign": ["Invisalign", "clear aligners"],
    "Pediatric Dentistry": ["pediatric dentistry", "a kids dentist appointment", "a children's dentist visit"],
}
UNKNOWN_SERVICE_PHRASES = ["teeth whitening", "a dental implant"]

QUESTIONS = [
    "How much does a cleaning cost?", "What are your timings on Saturday?", "Is there parking at the Nagarbhavi branch?",
    "Do you take insurance?", "Can I pay by UPI?", "How long does a first visit take?",
    "Where is the Indiranagar branch?", "How much is a root canal?", "Do you see children?", "Does a root canal hurt?",
    "What should I bring for my first visit?", "Do you do teeth whitening?", "Are you open on Sundays?",
]
META = ["How can you help me?", "What all can you help me with?", "Who am I speaking to?",
        "Tell me about the clinic first.", "What exactly do you do?"]
CHITCHAT = ["How's your day going?", "It's been raining a lot today, no?", "Sorry, one second, I'm just parking the car.",
            "Hope you're not too busy today."]
NONANSWERS = ["Hmm, I'm not sure.", "I've been really busy lately.", "Let me think.", "I don't know, what do you suggest?"]
BOT_QUESTIONS = ["Wait, am I talking to a real person?", "Are you a bot?", "Is this a real person or a recording?"]
HUMAN_REQUESTS = ["Can I talk to a real person?", "Can you put me through to someone at the front desk?",
                  "I'd rather speak to a human, please."]
REFUSALS = ["I'd rather not give my number.", "Why do you need my number?"]
EMERGENCY_OPENERS = [
    "Hi, I have really bad tooth pain and my face is swollen. Can someone see me today?",
    "My tooth broke and it's bleeding quite a lot, can I come in today?",
    "I've got severe tooth pain and a fever, it's unbearable. I need to see someone urgently.",
]
GIVE_UP = ["Forget it, I'll call back later. Bye.", "This isn't working, I'll just come in person. Bye.",
           "Never mind, I'll try another time. Bye."]
BYES = ["No, that's all, thanks. Bye!", "Nothing else, thank you. Bye.", "That's it, thanks a lot. Bye."]

DISRUPTIONS = (
    "meta", "offtopic", "chitchat", "nonanswer", "refuse", "multi", "self_correct", "wrong_then_fix",
    "change_mind", "intent_switch", "fragment", "silence", "bot", "human", "barge_in", "out_of_order", "sunday",
)
# Disruptions tied to a moment in the call rather than a turn number.
_ON_ASK = {"nonanswer": ("date", "time"), "refuse": ("phone",), "self_correct": ("date", "time"),
           "wrong_then_fix": ("phone",), "sunday": ("date",), "barge_in": ("summary",), "multi": ("name", "open")}

# Emma turned a value down (taken, lunch, closed...): the caller offers another
# one rather than repeating itself. "Full" only as in "we're full", never "your full name".
_FULL = r"fully booked|(?:we'?re|we are|it'?s|that'?s|is|are) (?:all |completely )?full"
_REJECTION = re.compile(
    rf"\b(closed|passed|taken|{_FULL}|not available|isn'?t available|no slots?|lunch|only (book|do)|can'?t|cannot|"
    r"doesn'?t|don'?t (do|have)|didn'?t catch|missed|sorry|again|spell|another|other|instead|isn'?t)\b"
)
# For M5 a re-ask only has a fair reason when the value broke a rule or needs
# clarifying. "Sorry, what was your name again?" after the caller clearly gave
# it is exactly the repeat M5 counts, so mishearing apologies don't excuse it.
_REASK_REASON = re.compile(
    rf"\b(closed|passed|taken|{_FULL}|booked up|not available|isn'?t available|no (free )?slots?|lunch|"
    r"opening hours|outside|too soon|too late|only (book|do|see|have)|can'?t|cannot|doesn'?t (do|offer)|"
    r"don'?t (do|offer|have)|isn'?t offered|spell|another|other|instead|different|earliest)\b"
)


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


@dataclass
class Profile:
    goal: str
    name: str
    phone: str                              # national 10 digits
    service: Optional[str]                  # canonical; None for a service the clinic doesn't book
    service_phrase: str
    branch: Optional[str] = None            # preferred branch; None = no preference
    branch_firm: bool = False
    day: Optional[date] = None
    time: Optional[time] = None
    time_firm: bool = False
    card: Optional[dict] = None             # an existing appointment (cancel / reschedule / intent switch)
    patient: Optional[str] = None           # booking for a family member
    relation: Optional[str] = None
    questions: list = field(default_factory=list)
    style: str = "plain"                    # plain | chatty | indian | terse
    fallback_service: str = "Consultation"

    def as_dict(self) -> dict:
        out = asdict(self)
        out["day"] = self.day.isoformat() if self.day else None
        out["time"] = self.time.strftime("%H:%M") if self.time else None
        return out

    @property
    def patient_name(self) -> str:
        return self.patient or self.name


def random_phone(rng: random.Random) -> str:
    while True:
        digits = rng.choice("6789") + "".join(rng.choice("0123456789") for _ in range(9))
        if not digits.startswith("900000"):
            return digits


def next_open_day(d: date) -> date:
    return d + timedelta(days=1) if d.weekday() == 6 else d


def random_profile(rng: random.Random, *, goal: Optional[str] = None, card: Optional[dict] = None,
                   catalog: Optional[dict] = None, today: Optional[date] = None) -> Profile:
    """A plausible caller. `card` (world.pick_card) is used when the goal is to manage an appointment."""
    today = today or clock.today()
    goal = goal or rng.choices(GOALS, weights=GOAL_WEIGHTS)[0]
    if goal in ("cancel", "reschedule") and card is None:
        goal = "book"
    style = rng.choices(["plain", "chatty", "indian", "terse"], weights=[0.5, 0.15, 0.25, 0.1])[0]
    name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
    phone = random_phone(rng)
    if rng.random() < 0.08:
        service, phrase = None, rng.choice(UNKNOWN_SERVICE_PHRASES)
    else:
        service = rng.choice(lines.SERVICES)
        phrase = rng.choice(SERVICE_PHRASES[service])
    branch_services = (catalog or {}).get("branch_services") or {}
    offering = [b for b, svcs in branch_services.items() if service in svcs] if service else []
    roll = rng.random()
    if roll < 0.6 and offering:
        branch = rng.choice(offering)
    elif roll < 0.8:
        branch = None
    else:
        branch = rng.choice(lines.BRANCHES)
    day = next_open_day(today + timedelta(days=rng.randint(1, 14)))
    slot = rng.choice([h * 60 + m for h in range(9, 20) for m in (0, 30) if h != 14])
    profile = Profile(
        goal=goal, name=name, phone=phone, service=service, service_phrase=phrase, branch=branch,
        branch_firm=rng.random() < 0.3, day=day, time=time(slot // 60, slot % 60), time_firm=rng.random() < 0.25,
        card=card, style=style, questions=rng.sample(QUESTIONS, k=rng.randint(2, 3)),
    )
    if service == "Pediatric Dentistry" and rng.random() < 0.7:
        profile.relation = rng.choice(["son", "daughter"])
        profile.patient = f"{rng.choice(CHILD_NAMES)} {name.split()[-1]}"
    if goal == "book" and card:
        # A booking caller who may switch to cancelling "my existing
        # appointment" is the person on the card: same name and number.
        profile.name, profile.phone = card["patient"], card["phone"]
    if goal in ("cancel", "reschedule") and card:
        profile.name, profile.phone = card["patient"], card["phone"]
        profile.service = card["service"]
        profile.service_phrase = rng.choice(SERVICE_PHRASES.get(card["service"], [card["service"]]))
        profile.branch = card["branch"]
        card_day = date.fromisoformat(card["date"])
        if goal == "cancel":
            profile.day = card_day
            profile.time = time.fromisoformat(card["time"])
        if goal == "reschedule":
            profile.day = moved_day(card_day, rng.choice([-3, -2, -1, 1, 2, 3, 5, 7]), today)
    if goal == "emergency":
        profile.service, profile.service_phrase = "Consultation", "an emergency visit"
        profile.day, profile.time_firm, profile.branch_firm = today, False, False
    return profile


@dataclass
class CallerTurn:
    text: str
    disruption: Optional[str] = None
    heard_previous: bool = True
    final: bool = False              # the caller hangs up after Emma's reply
    ask: Optional[str] = None        # what the caller thought Emma asked
    note: str = ""


def plan_disruptions(rng: random.Random, *, intensity: Optional[int] = None, allowed=DISRUPTIONS,
                     card: Optional[dict] = None, goal: str = "book") -> list[str]:
    """Which disruptions this call will have. About one call in five is clean (no disruptions)."""
    if intensity is None:
        intensity = 0 if rng.random() < 0.2 else rng.randint(1, 3)
    pool = [d for d in allowed if not (d == "intent_switch" and (card is None or goal != "book"))]
    if goal not in ("book", "emergency"):
        pool = [d for d in pool if d not in ("change_mind", "out_of_order", "sunday", "self_correct", "multi")]
    if goal == "questions":
        pool = [d for d in pool if d not in ("refuse", "wrong_then_fix", "barge_in", "nonanswer")]
    return rng.sample(pool, k=min(intensity, len(pool)))


class SimCaller:
    """
    One simulated caller. Call respond(emma_line) each turn; it returns the
    caller's next words, or None when the caller hangs up (Emma said goodbye,
    or the turn limit was reached).
    """

    def __init__(self, profile: Profile, rng: random.Random, *, disruptions=(), max_turns: int = MAX_TURNS,
                 today: Optional[date] = None):
        self.p = profile
        self.rng = rng
        self.today = today or clock.today()
        self.max_turns = max_turns
        self.turns = 0
        self.stated_goal = False
        self.believes_done = False
        self.ended_by: Optional[str] = None
        self.gave_up = False
        self.repeats: list[int] = []
        self.log: list[dict] = []
        self.given: dict[str, str] = {}
        self.agreed_day = profile.day
        self.agreed_time = profile.time
        self.agreed_branch = profile.branch
        self.goal = profile.goal
        self.initial_goal = profile.goal
        self.pending: Optional[str] = None
        self.pending_ask: Optional[tuple] = None
        self.branch_refusals = 0
        self.time_refusals = 0
        self.asked_questions = 0
        self.unknown_service_tries = 0
        self.statements = 0
        self.last_was_correction = False
        # How often Emma asked the same thing right after a real answer. A re-ask
        # after the caller's own silence, fragment or detour is fair and doesn't count.
        self._ask_counts: Counter = Counter()
        self._line_counts: Counter = Counter()
        self._answered_last = True
        self.disruptions = list(disruptions)
        self._turn_triggers: dict[str, int] = {}
        for d in self.disruptions:
            if d not in _ON_ASK and d not in ("change_mind", "out_of_order"):
                self._turn_triggers[d] = rng.randint(2, 9)
        self.used: list[tuple[int, str]] = []
        self._phone_wrong_given = False
        self._date_confirmed = False
        self.family_said = False
        self.emergency_time_asks = 0

    # -- public ---------------------------------------------------------------
    @property
    def expected_outcome(self) -> str:
        return {"book": "booked", "cancel": "cancelled", "reschedule": "rescheduled", "check": "none",
                "questions": "none", "emergency": "booked_or_task"}[self.goal]

    def note_said(self, text: str, expect: Optional[str] = None):
        """
        A scripted line was said on this caller's behalf: remember what it gave
        away, and treat a date or time in it as what the caller now wants (a
        scripted "make it Saturday instead" changes the caller's mind too).
        """
        reading = fake_nlu.read(text, expect=expect, today=self.today)
        if text.strip() and (reading.intent or reading.service_phrase or reading.question):
            self.stated_goal = True
        for slot in reading.slots:
            self.given[slot] = reading.slots[slot]
        if reading.service and reading.intent != "question" and self.goal in ("book", "emergency"):
            self.p.service = reading.service
            self.p.service_phrase = _phrase_for(reading.service, text)
        if reading.branch and reading.intent != "question" and self.goal == "book":
            self.agreed_branch = reading.branch
        if reading.date and self.goal not in ("cancel", "check"):
            self.agreed_day = reading.date
        if reading.time and self.goal not in ("cancel", "check"):
            self.agreed_time = reading.time
        if reading.intent in ("cancel", "reschedule") and self.p.card and self.goal == "book":
            self.goal = reading.intent
        self.turns += 1

    def respond(self, emma_line: str) -> Optional[CallerTurn]:
        if self.turns >= self.max_turns:
            self.ended_by = "max_turns"
            return None
        ask = lines.classify_ask(emma_line, self.today)
        if ask.kind == "closing":
            self.ended_by = "emma_closed"
            return None
        self.turns += 1
        n = self.turns
        self._observe(emma_line, ask)
        turn = self._decide(emma_line, ask, n)
        turn.ask = ask.kind if ask.kind != "confirm" else f"confirm:{ask.slot}"
        self.last_was_correction = bool(re.match(r"^no\b", lines.norm(turn.text))) and ask.kind == "confirm"
        self._answered_last = bool(turn.text.strip()) and not turn.disruption and turn.note != "back on the line"
        if turn.disruption:
            self.used.append((n, turn.disruption))
        self.log.append({"n": n, "text": turn.text, "disruption": turn.disruption, "ask": turn.ask,
                         "heard_previous": turn.heard_previous, "final": turn.final})
        if turn.final:
            self.ended_by = "caller_gave_up" if self.gave_up else "caller_bye"
        return turn

    def flags(self) -> dict:
        return {"repeats": self.repeats, "gave_up": self.gave_up, "disruptions": self.disruptions,
                "used": self.used, "initial_goal": self.initial_goal, "final_goal": self.goal,
                "ended_by": self.ended_by, "believes_done": self.believes_done}

    # -- observing Emma ---------------------------------------------------------
    def _observe(self, line: str, ask: lines.Ask):
        claimed = lines.claims(line)
        wanted = {"book": "booked", "emergency": "booked", "cancel": "cancelled", "reschedule": "moved"}.get(self.goal)
        if wanted and wanted in claimed:
            self.believes_done = True
        if self.goal == "check" and self.p.card and time.fromisoformat(self.p.card["time"]) in ask.times:
            self.believes_done = True
        if self._answered_last:
            key = (ask.kind, ask.slot, lines.similarity_key(ask.question))
            self._ask_counts[key] += 1
            self._line_counts[lines.similarity_key(line)] += 1
        slot = ask.kind if ask.is_slot_question else None
        if slot and slot in self.given and not _REASK_REASON.search(lines.norm(line)) and not self.last_was_correction:
            self.repeats.append(self.turns)

    def _frustrated(self, line: str, ask: lines.Ask) -> bool:
        """
        Emma asked the same thing a fourth time although every earlier time got
        a real answer. Three is already a loop (M3 flags it); a patient caller
        still answers it, and hangs up on the fourth.
        """
        key = (ask.kind, ask.slot, lines.similarity_key(ask.question))
        return self._ask_counts[key] >= 4 or self._line_counts[lines.similarity_key(line)] >= 4

    # -- deciding what to say -----------------------------------------------------
    def _decide(self, line: str, ask: lines.Ask, n: int) -> CallerTurn:
        if self.pending is not None:
            text, self.pending = self.pending, None
            pending_ask, self.pending_ask = self.pending_ask, None
            # Finish the cut-off sentence only if Emma didn't move on without it.
            same = (ask.kind, ask.slot) == pending_ask or _GO_ON.search(lines.norm(line))
            if same:
                return CallerTurn(text, note="completes the fragment")
        if _LINE_CHECK.search(lines.norm(line)) and ask.kind in ("open", "statement"):
            # "Are you still there?" / "I can't hear you": the caller is back.
            return CallerTurn(self.rng.choice(["Sorry, yes, I'm here.", "Hello? Yes, I'm still here.",
                                               "Yes, sorry, go ahead."]), note="back on the line")
        # A planned disruption still happens when Emma is going round in circles
        # (a fed-up caller is exactly who asks "am I talking to a real person?");
        # the caller gives up on the next turn if the loop carries on.
        disrupted = self._disruption(line, ask, n)
        if disrupted is not None:
            return disrupted
        if self._frustrated(line, ask):
            self.gave_up = True
            return CallerTurn(self.rng.choice(GIVE_UP), final=True, note="gave up")
        return self._answer(line, ask)

    def _disruption(self, line: str, ask: lines.Ask, n: int) -> Optional[CallerTurn]:
        slot_kind = ask.slot if ask.kind == "confirm" else ask.kind
        for d in list(self.disruptions):
            if d in [u[1] for u in self.used]:
                continue
            if d in _ON_ASK:
                # Only the summary disruption fires on a read-back; the rest need
                # Emma's open question ("What day?"), not "So Monday, is that right?".
                moment = slot_kind if d == "barge_in" else (ask.kind if ask.kind != "confirm" else None)
                if moment not in _ON_ASK[d]:
                    continue
                if d == "multi" and ask.kind == "open" and self.stated_goal:
                    continue
                return self._make_on_ask(d, line, ask)
            if d == "change_mind":
                if self._date_confirmed and ask.kind not in ("closing", "anything_else") and not self.believes_done \
                        and slot_kind != "summary":
                    return self._change_mind()
                continue
            if d == "out_of_order":
                continue                              # used in the opening line
            if self._turn_triggers.get(d) is not None and n >= self._turn_triggers[d] and not self.believes_done:
                made = self._make_on_turn(d, line, ask)
                if made is not None:
                    return made
        return None

    def _make_on_turn(self, d: str, line: str, ask: lines.Ask) -> Optional[CallerTurn]:
        r = self.rng
        if d == "meta":
            return CallerTurn(r.choice(META), disruption=d)
        if d == "offtopic":
            return CallerTurn(r.choice(QUESTIONS), disruption=d)
        if d == "chitchat":
            return CallerTurn(r.choice(CHITCHAT), disruption=d)
        if d == "silence":
            return CallerTurn("", disruption=d)
        if d == "bot":
            return CallerTurn(r.choice(BOT_QUESTIONS), disruption=d)
        if d == "human":
            return CallerTurn(r.choice(HUMAN_REQUESTS), disruption=d)
        if d == "intent_switch" and self.goal == "book" and self.p.card:
            self.goal = "cancel"
            self.believes_done = False
            return CallerTurn("Actually, sorry, I just remembered I already have an appointment booked. "
                              "Can you cancel that one instead?", disruption=d)
        if d == "fragment":
            full = self._answer(line, ask)
            words = full.text.split()
            if len(words) < 4 or full.final:
                return None
            self.pending = full.text
            self.pending_ask = (ask.kind, ask.slot)
            cut = " ".join(words[: max(2, len(words) // 3)]).rstrip(".,?!")
            return CallerTurn(cut, disruption=d, note="cut off")
        return None

    def _make_on_ask(self, d: str, line: str, ask: lines.Ask) -> CallerTurn:
        r = self.rng
        if d == "nonanswer":
            return CallerTurn(r.choice(NONANSWERS), disruption=d)
        if d == "refuse":
            return CallerTurn(r.choice(REFUSALS), disruption=d)
        if d == "sunday":
            return CallerTurn(r.choice(["Can I come on Sunday?", "Sunday morning would be best.", "This Sunday?"]),
                              disruption=d)
        if d == "self_correct":
            if ask.kind == "time" or ask.slot == "time":
                wrong = (datetime.combine(self.today, self.agreed_time) - timedelta(hours=1)).time()
                text = f"{self._time_phrase(wrong)}, sorry, no, I mean {self._time_phrase(self.agreed_time)}"
                self.given["time"] = self.agreed_time.strftime("%H:%M")
            else:
                wrong = next_open_day(self.agreed_day + timedelta(days=1))
                text = f"{self._date_phrase(wrong)}, sorry, no, I mean {self._date_phrase(self.agreed_day)}"
                self.given["date"] = self.agreed_day.isoformat()
            return CallerTurn(text, disruption=d)
        if d == "wrong_then_fix":
            digits = list(self.p.phone)
            i = r.randint(3, 9)
            digits[i] = str((int(digits[i]) + r.randint(1, 8)) % 10)
            self._phone_wrong_given = True
            return CallerTurn(self._phone_phrase("".join(digits)), disruption=d, note="deliberately wrong digit")
        if d == "barge_in":
            answer = self._answer(line, ask)
            answer.heard_previous = False
            answer.disruption = d
            answer.note = "talked over the summary"
            return answer
        if d == "multi":
            return CallerTurn(self._multi_line(ask), disruption=d)
        return self._answer(line, ask)

    def _change_mind(self) -> CallerTurn:
        new_day = next_open_day(self.agreed_day + timedelta(days=self.rng.randint(1, 3)))
        self.agreed_day = new_day
        self.given.pop("date", None)
        return CallerTurn(f"Actually, can we make it {self._date_phrase(new_day)} instead?", disruption="change_mind")

    # -- answering ------------------------------------------------------------------
    def _answer(self, line: str, ask: lines.Ask) -> CallerTurn:
        k = ask.kind
        if "?" in line and re.search(r"\bhow old\b|\bwhat age\b|\b(his|her|their|the child'?s) age\b", lines.norm(line)):
            return CallerTurn(self._age_line())
        if k == "anything_else":
            return self._anything_else()
        if k in ("open", "statement") and not self.stated_goal:
            return CallerTurn(self._opening())
        if k == "open":
            if self.believes_done or (self.goal == "questions" and self.asked_questions >= len(self.p.questions)):
                return CallerTurn(self.rng.choice(BYES), final=True)
            return CallerTurn(self._restate())
        if k == "statement":
            self.statements += 1
            if self.believes_done:
                return CallerTurn(self.rng.choice(BYES), final=True)
            if self.goal == "questions":
                return self._next_question()
            return CallerTurn("Okay." if self.statements == 1 else self._restate())
        if k == "name":
            return CallerTurn(self._name_line())
        if k == "spelling":
            first = self.p.patient_name.split()[0].upper()
            return CallerTurn(" ".join(first) + f", {self.p.patient_name.split()[0]}.")
        if k == "phone":
            # A number for a callback is fine to give; a booking's number isn't wanted.
            if self.goal == "questions" and not re.search(r"\bcall\b", lines.norm(line)):
                return CallerTurn("I don't need an appointment, I just had a question.")
            self.given["phone"] = self.p.phone
            return CallerTurn(self._phone_phrase(self.p.phone))
        if k == "service":
            return self._service_answer(ask)
        if k == "branch":
            return self._branch_answer(ask)
        if k == "date":
            return self._date_answer()
        if k == "time":
            return self._time_answer(line)
        if k == "choice":
            return self._choose(ask)
        if k == "confirm":
            return self._confirm(line, ask)
        if k == "yes_no":
            return self._yes_no(line, ask)
        return CallerTurn("Sorry, what was that?")

    def _age_line(self) -> str:
        """The patient's age when Emma asks (a child for a son or daughter), the same all call."""
        if "age" not in self.given:
            self.given["age"] = self.rng.randint(4, 12) if self.p.relation else self.rng.randint(22, 70)
        age = self.given["age"]
        who = {"son": "He's", "daughter": "She's"}.get(self.p.relation or "", "I'm" if not self.p.relation else "They're")
        return f"{who} {age}."

    def _opening(self) -> str:
        self.stated_goal = True
        r, p, g = self.rng, self.p, self.goal
        sp = p.service_phrase
        if "meta" in self.disruptions and not any(u[1] == "meta" for u in self.used) and r.random() < 0.5:
            self.used.append((self.turns, "meta"))
            self.stated_goal = False
            return r.choice(META)
        if g == "book":
            for_whom = f" for my {p.relation}" if p.relation else ""
            if "out_of_order" in self.disruptions:
                self.used.append((self.turns, "out_of_order"))
                self.given["date"] = self.agreed_day.isoformat()
                if p.service:
                    self.given["service"] = p.service
                return f"{_cap(_on(self._date_phrase(self.agreed_day)))} I want an appointment for {sp}{for_whom}."
            options = {
                "plain": [f"Hi, I'd like to book {sp}{for_whom}.", f"Hello, can I get an appointment for {sp}{for_whom}?",
                          "Hi, I need to book an appointment, please."],
                "chatty": [f"Hi there! I've been meaning to get {sp}{for_whom} for ages, can I book one?",
                           f"Hello, good morning! I was hoping to book {sp}{for_whom} sometime soon."],
                "indian": [f"Hello, I want to fix one appointment for {sp}{for_whom}.",
                           f"Kindly book an appointment for {sp}{for_whom}.",
                           f"Good morning, I need {sp}{for_whom}, is it possible to get an appointment?"],
                "terse": [f"Appointment for {sp}{for_whom}.", "Need an appointment."],
            }[p.style]
            text = r.choice(options)
            if p.service and p.service_phrase in text:
                self.given["service"] = p.service
            return text
        if g == "cancel":
            return r.choice({
                "indian": ["I want to cancel my appointment, kindly do the needful.", "Hello, I need to cancel my booking."],
            }.get(p.style, ["Hi, I need to cancel my appointment.", "I won't be able to make my appointment, can you cancel it?",
                            "Hello, I'd like to cancel my booking please."]))
        if g == "reschedule":
            earlier = p.card and self.agreed_day < date.fromisoformat(p.card["date"])
            if p.style == "indian":
                return "I want to prepone my appointment." if earlier else "I want to postpone my appointment, is it possible?"
            return r.choice(["Hi, can I move my appointment to another day?", "I need to reschedule my appointment.",
                             "Hello, I have an appointment but I need to change the day."])
        if g == "questions":
            return self._next_question().text
        if g == "check":
            return r.choice(["Hi, can you tell me when my appointment is?", "I forgot the time of my appointment, can you check?"])
        return r.choice(EMERGENCY_OPENERS)

    def _restate(self) -> str:
        r, g, sp = self.rng, self.goal, self.p.service_phrase
        if g == "book":
            return r.choice([f"I just want to book {sp}.", f"I'd like to book {sp}, please.", "I want to book an appointment."])
        if g == "cancel":
            return r.choice(["I want to cancel my appointment.", "I just need to cancel my existing booking."])
        if g == "reschedule":
            return r.choice(["I want to move my appointment to another day.", "I need to change my appointment."])
        if g == "questions":
            return self._next_question().text
        if g == "check":
            return "I just want to know what time my appointment is."
        return "I'm in a lot of pain, I need to see someone today."

    def _next_question(self) -> CallerTurn:
        if self.asked_questions < len(self.p.questions):
            q = self.p.questions[self.asked_questions]
            self.asked_questions += 1
            return CallerTurn(q)
        return CallerTurn("Okay, thanks, that's all I needed. Bye.", final=True)

    def _anything_else(self) -> CallerTurn:
        if self.believes_done or self.goal == "questions" and self.asked_questions >= len(self.p.questions):
            return CallerTurn(self.rng.choice(BYES), final=True)
        if self.goal == "questions":
            return self._next_question()
        return CallerTurn(self._restate())

    def _name_line(self) -> str:
        r, p = self.rng, self.p
        self.given["name"] = p.patient_name
        if p.patient and not self.family_said:
            self.family_said = True
            return f"It's for my {p.relation}, {p.patient}. I'm {p.name}."
        if p.patient:
            return f"{p.patient}."
        return r.choice({
            "plain": [f"My name is {p.name}.", f"It's {p.name}.", f"{p.name}."],
            "chatty": [f"Oh sure, it's {p.name}.", f"Yes, my name is {p.name}."],
            "indian": [f"{p.name} here.", f"My name is {p.name}.", f"This is {p.name}."],
            "terse": [f"{p.name}."],
        }[p.style])

    def _phone_phrase(self, digits: str) -> str:
        r = self.rng
        style = r.choices(["grouped", "words", "indian", "spaced", "deepgram"], weights=[0.35, 0.2, 0.25, 0.1, 0.1])[0]
        if style == "grouped":
            body = f"{digits[:5]} {digits[5:]}"
        elif style == "spaced":
            body = " ".join(digits)
        elif style == "deepgram":
            body = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
        else:
            body = _digits_in_words(digits, doubles=(style == "indian"))
        lead = r.choice(["", "It's ", "My number is ", "Sure, it's "])
        return f"{lead}{body}."

    def _service_answer(self, ask: lines.Ask) -> CallerTurn:
        p = self.p
        if self.goal in ("cancel", "reschedule"):
            verb = "cancel" if self.goal == "cancel" else "move"
            return CallerTurn(f"It's my {_bare(p.service_phrase)} appointment, I want to {verb} it.")
        if self.goal == "questions":
            return CallerTurn("I don't want to book anything yet, I just had a question.")
        if p.service is None:
            self.unknown_service_tries += 1
            if self.unknown_service_tries >= 2 or ask.options:
                p.service, p.service_phrase = p.fallback_service, "a consultation"
                self.given["service"] = p.service
                return CallerTurn("Okay, let's do a consultation then.")
            return CallerTurn(f"I wanted {p.service_phrase}.")
        self.given["service"] = p.service
        return CallerTurn(self.rng.choice([f"{p.service_phrase[0].upper()}{p.service_phrase[1:]}.",
                                           f"It's for {p.service_phrase}.", f"I need {p.service_phrase}."]))

    def _branch_answer(self, ask: lines.Ask) -> CallerTurn:
        if self.agreed_branch:
            self.given["branch"] = self.agreed_branch
            return CallerTurn(self.rng.choice([f"{self.agreed_branch}, please.", f"The {self.agreed_branch} one.",
                                               f"{self.agreed_branch} would be best for me."]))
        return CallerTurn("Any branch is fine, whichever has a slot.")

    def _date_phrase(self, d: date) -> str:
        r, delta = self.rng, (d - self.today).days
        weekday, month = d.strftime("%A"), d.strftime("%B")
        if delta == 0:
            return "today itself" if self.p.style == "indian" else "today"
        if delta == 1:
            return r.choice(["tomorrow itself", "tomorrow"]) if self.p.style == "indian" else "tomorrow"
        if delta == 2 and r.random() < 0.5:
            return "day after tomorrow"
        if delta <= 6:
            return r.choice([weekday, f"this {weekday}", f"coming {weekday}", f"on {weekday}"])
        return r.choice([f"{ordinal(d.day)} {month}", f"{month} {ordinal(d.day)}", f"the {ordinal(d.day)} of {month}",
                         f"{weekday} the {ordinal(d.day)}"])

    def _time_phrase(self, t: time) -> str:
        r = self.rng
        h12 = t.hour % 12 or 12
        ampm = "am" if t.hour < 12 else "pm"
        part = "morning" if t.hour < 12 else ("afternoon" if t.hour < 16 else "evening")
        if t.minute == 30:
            return r.choice([f"{h12}:30 {ampm}", f"half past {h12} in the {part}", f"{h12} thirty {ampm}"])
        if t.minute:
            return f"{h12}:{t.minute:02d} {ampm}"
        return r.choice([f"{h12} {ampm}", f"{h12} {ampm.upper()}", f"around {h12} in the {part}", f"{h12} in the {part}"])

    def _date_answer(self) -> CallerTurn:
        if self.goal == "check" and self.p.card:
            d = date.fromisoformat(self.p.card["date"])
            return CallerTurn(f"It's {_on(self._date_phrase(d))}, I just don't remember the time.")
        if self.goal in ("cancel", "reschedule") and self.p.card and "card_date" not in self.given:
            self.given["card_date"] = self.p.card["date"]
            d = date.fromisoformat(self.p.card["date"])
            if self.goal == "cancel":
                return CallerTurn(f"It's {_on(self._date_phrase(d))}.")
            return CallerTurn(f"It's {_on(self._date_phrase(d))}, and I'd like to move it to "
                              f"{self._date_phrase(self.agreed_day)}.")
        if self.goal == "questions":
            return CallerTurn("I'm not booking right now, thanks.")
        self.given["date"] = self.agreed_day.isoformat()
        text = self._date_phrase(self.agreed_day)
        return CallerTurn(text[0].upper() + text[1:] + ("." if not text.endswith("?") else ""))

    def _time_answer(self, line: str) -> CallerTurn:
        if self.goal == "emergency":
            self.emergency_time_asks += 1
            return CallerTurn("As soon as possible, whatever is earliest." if self.emergency_time_asks == 1
                              else "Any time today is fine, the earlier the better.")
        if self.goal == "cancel" and self.p.card:
            return CallerTurn(f"It's at {self._time_phrase(time.fromisoformat(self.p.card['time']))}.")
        if self.goal == "check":
            return CallerTurn("That's what I'm asking, I don't remember the time.")
        offered = _offered_times(line)
        if offered and self.agreed_time not in offered and not self.p.time_firm:
            self.agreed_time = offered[0]
        elif _REJECTION.search(lines.norm(line)) and "time" in self.given:
            # The time was refused (taken, lunch, closed): move an hour later, as a caller would.
            later = (datetime.combine(self.today, self.agreed_time) + timedelta(hours=1)).time()
            if later.hour == 14:
                later = time(15, later.minute)
            if later.hour >= 20:
                later = time(10, 0)
            self.agreed_time = later
        self.given["time"] = self.agreed_time.strftime("%H:%M")
        text = self._time_phrase(self.agreed_time)
        return CallerTurn(text[0].upper() + text[1:] + ".")

    def _multi_line(self, ask: lines.Ask) -> str:
        p = self.p
        self.stated_goal = True
        self.given.update(name=p.patient_name, phone=p.phone, date=self.agreed_day.isoformat(),
                          time=self.agreed_time.strftime("%H:%M"))
        if p.service:
            self.given["service"] = p.service
        return (f"I'm {p.name}, my number is {p.phone[:5]} {p.phone[5:]}, and I'd like {p.service_phrase} "
                f"{_on(self._date_phrase(self.agreed_day))} at {self._time_phrase(self.agreed_time)}.")

    def _choose(self, ask: lines.Ask) -> CallerTurn:
        r = self.rng
        if ask.subject == "time":
            offered = [datetime.strptime(o, "%I:%M %p").time() for o in ask.options]
            if self.agreed_time in offered:
                pick = self.agreed_time
            elif self.p.time_firm and self.time_refusals == 0:
                self.time_refusals += 1
                return CallerTurn(f"Is {self._time_phrase(self.agreed_time)} not possible at all?")
            else:
                pick = offered[0]
            self.agreed_time = pick
            self.given["time"] = pick.strftime("%H:%M")
            if ask.dates and self.agreed_day not in ask.dates and self.goal in ("book", "emergency", "reschedule"):
                # Taking an offered slot takes its day too ("10 on Tuesday? Yes, fine").
                self.agreed_day = ask.dates[0]
                self.given["date"] = self.agreed_day.isoformat()
            said = self._time_phrase(pick)
            return CallerTurn(r.choice([f"{_cap(said)} works.", f"Let's do {said}.", f"{_cap(said)}, please."]))
        if ask.subject == "branch":
            pick = self.agreed_branch if self.agreed_branch in ask.options else ask.options[0]
            self.agreed_branch = pick
            self.given["branch"] = pick
            return CallerTurn(r.choice([f"{pick}, please.", f"{pick} works for me."]))
        if ask.subject == "doctor":
            return CallerTurn(f"Dr {ask.options[0]} is fine.")
        if ask.subject == "date":
            pick = date.fromisoformat(ask.options[0])
            self.agreed_day = pick
            self.given["date"] = pick.isoformat()
            return CallerTurn(f"{_cap(self._date_phrase(pick))} is fine.")
        return CallerTurn("The first one, please.")

    def _confirm(self, line: str, ask: lines.Ask) -> CallerTurn:
        r, p = self.rng, self.p
        yes = r.choice(["Yes.", "Yeah, that's right.", "Correct.", "Yes, that's correct.", "Yep."])
        slot = ask.slot
        if slot == "name":
            expected = p.patient_name
            spelled = re.findall(r"\b([A-Z])\b", ask.question)
            if len(spelled) >= 3:
                ok = "".join(spelled).lower() == expected.split()[0].lower()
            else:
                ok = lines.mentions_name(line, expected)
            return CallerTurn(yes if ok else f"No, it's {expected}.")
        if slot == "phone":
            if self._phone_wrong_given:
                self._phone_wrong_given = False
                self.given["phone"] = p.phone
                return CallerTurn(f"No, sorry, it's {self._phone_phrase(p.phone)}", note="fixing the wrong digit")
            if ask.phone == p.phone:
                return CallerTurn(yes)
            return CallerTurn(f"No, it's {self._phone_phrase(p.phone)}")
        if slot == "service":
            if self.goal in ("cancel", "reschedule"):
                return CallerTurn(yes if p.service in ask.services else f"No, it's my {_bare(p.service_phrase)} appointment.")
            if p.service is None:
                if ask.services:
                    p.service = ask.services[0]
                    return CallerTurn("Yes, that's fine.")
                return CallerTurn(f"No, I wanted {p.service_phrase}.")
            return CallerTurn(yes if p.service in ask.services else f"No, it's for {p.service_phrase}.")
        if slot == "branch":
            offered = ask.branches[0] if ask.branches else None
            if offered is None or offered == self.agreed_branch or not self.agreed_branch:
                if offered:
                    self.agreed_branch = offered
                return CallerTurn(r.choice(["Yes, that's fine.", "Yeah, that works.", yes]))
            if p.branch_firm and self.branch_refusals < 2:
                self.branch_refusals += 1
                return CallerTurn(r.choice([f"No, I'd prefer {self.agreed_branch}.", "No.",
                                            f"No, {self.agreed_branch} is closer for me."]))
            self.agreed_branch = offered
            return CallerTurn(f"Okay, {offered} is fine.")
        if slot == "date":
            targets = {self.agreed_day}
            if self.goal in ("cancel", "reschedule") and p.card:
                targets.add(date.fromisoformat(p.card["date"]))
            if targets & set(ask.dates):
                if self.agreed_day in ask.dates:
                    self._date_confirmed = True
                return CallerTurn(yes)
            if self.goal == "cancel" and p.card:
                return CallerTurn(f"No, my appointment is {_on(self._date_phrase(date.fromisoformat(p.card['date'])))}.")
            return CallerTurn(f"No, I said {self._date_phrase(self.agreed_day)}.")
        if slot == "time":
            if self.agreed_time in ask.times:
                return CallerTurn(yes)
            if ask.times and not self.p.time_firm:
                self.agreed_time = ask.times[0]
                return CallerTurn("Yes, that works.")
            return CallerTurn(f"No, {self._time_phrase(self.agreed_time)}.")
        if slot == "summary":
            return self._confirm_summary(line, ask)
        if self.goal == "questions":
            return self._next_question()
        return CallerTurn(yes)

    def _confirm_summary(self, line: str, ask: lines.Ask) -> CallerTurn:
        r, p = self.rng, self.p
        low = lines.norm(line)
        if self.goal == "cancel":
            if "cancel" not in low:
                return CallerTurn("No, I don't want a new booking, I want to cancel my appointment.")
            return CallerTurn(r.choice(["Yes, please cancel it.", "Yes, go ahead and cancel it."]))
        if self.goal == "reschedule" and not re.search(r"\b(move|moving|reschedul|chang|shift)", low):
            return CallerTurn("No, I want to move my existing appointment, not make a new one.")
        if self.goal == "questions":
            return CallerTurn("No, I don't want to book anything, thanks.")
        if ask.dates and self.agreed_day not in ask.dates and self.goal != "emergency":
            return CallerTurn(f"No, the date is wrong. I wanted {self._date_phrase(self.agreed_day)}.")
        if ask.times and self.agreed_time not in ask.times and self.goal != "emergency":
            if not self.p.time_firm:
                self.agreed_time = ask.times[0]
            else:
                return CallerTurn(f"No, the time should be {self._time_phrase(self.agreed_time)}.")
        if p.service and ask.services and p.service not in ask.services and self.goal == "book":
            return CallerTurn(f"No, it's for {p.service_phrase}, not that.")
        if ask.phone and ask.phone != p.phone:
            return CallerTurn(f"No, the number is wrong. It's {self._phone_phrase(p.phone)}")
        for_name = re.search(r"\bfor ([A-Z][a-z]+)\b", line)
        if for_name and for_name.group(1).lower() not in lines.norm(p.patient_name) \
                and for_name.group(1).lower() not in lines.norm(p.name) \
                and for_name.group(1) not in lines.MONTHS and for_name.group(1).lower() not in lines.WEEKDAYS:
            if not any(for_name.group(1) in s for s in lines.SERVICES):
                return CallerTurn(f"No, the name is {p.patient_name}.")
        return CallerTurn(r.choice(["Yes, please go ahead.", "Yes, that's right, book it.", "Yeah, go ahead.",
                                    "Correct, please book it."]))

    def _yes_no(self, line: str, ask: lines.Ask) -> CallerTurn:
        r, g = self.rng, self.goal
        low = lines.norm(ask.question)
        if self.believes_done:
            return CallerTurn(r.choice(BYES), final=True)
        if re.search(r"\b(another|other|different) (time|day)\b", low):
            if "time" in low:
                return CallerTurn(f"Yes, how about {self._time_phrase(self.agreed_time)}?")
            return CallerTurn(f"Yes, {self._date_phrase(self.agreed_day)} maybe?")
        if g == "book":
            self.stated_goal = True
            if "service" not in self.given and self.p.service:
                self.given["service"] = self.p.service
                return CallerTurn(f"Yes, please. I need {self.p.service_phrase}.")
            return CallerTurn(r.choice(["Yes, please.", "Yes.", "Yeah, I'd like that."]))
        if g in ("cancel", "reschedule"):
            self.stated_goal = True
            verb = "cancel" if g == "cancel" else "move"
            return CallerTurn(f"No, I want to {verb} my existing appointment.")
        if g == "check":
            self.stated_goal = True
            return CallerTurn("No, I just want to know when my appointment is.")
        if g == "questions":
            if self.asked_questions < len(self.p.questions):
                return CallerTurn("Not right now. " + self._next_question().text)
            return CallerTurn("No, not right now. That's all, thanks. Bye.", final=True)
        self.stated_goal = True
        return CallerTurn("Yes, as soon as possible please.")


_LINE_CHECK = re.compile(r"\b(still there|still with me|not hearing anything|can'?t hear you)\b")
_OFFER = re.compile(r"\b(could do|can do|i have|how about|free at|available at|closest|nearest|earliest|"
                    r"next (free|available)|would .{0,30}work|either)\b")
_NO_ON = ("today", "tomorrow", "day after", "this ", "coming ", "next ", "on ")
_GO_ON = re.compile(r"\b(go on|go ahead|sorry|didn'?t catch|say that again|repeat|you were saying)\b")


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _on(day_phrase: str) -> str:
    """ "Monday" -> "on Monday", "tomorrow" -> "tomorrow" (no "on tomorrow")."""
    return day_phrase if day_phrase.lower().startswith(_NO_ON) else f"on {day_phrase}"


def _bare(phrase: str) -> str:
    """ "a check-up" -> "check-up", for "my check-up appointment"."""
    return re.sub(r"^(a|an|to)\s+", "", phrase)


def _offered_times(line: str) -> list:
    """
    Times Emma actually offered: those in her question, or in a sentence
    that offers ("I could do 4:30"). A fact such as "we're open 7 in the
    morning to 9 at night" is not an offer.
    """
    sentences = lines.split_sentences(line)
    out = []
    for i, sentence in enumerate(sentences):
        last_question = i == len(sentences) - 1 and sentence.endswith("?")
        if last_question or _OFFER.search(lines.norm(sentence)):
            out += [t for t in lines.times_in(sentence) if t not in out]
    return out


def moved_day(card_day: date, shift: int, today: date) -> date:
    """A new open day `shift` days from the appointment, never the same day and never before tomorrow."""
    step = -1 if shift < 0 else 1
    day = card_day + timedelta(days=shift)
    while day.weekday() == 6 or day == card_day:
        day += timedelta(days=step)
    if day <= today:
        day = card_day + timedelta(days=1)
        while day.weekday() == 6:
            day += timedelta(days=1)
    return day


def _phrase_for(service: str, said: str) -> str:
    """How this caller says `service` from now on: the stock phrase that matches what they just said."""
    low = lines.norm(said)
    for phrase in SERVICE_PHRASES.get(service, []):
        core = re.sub(r"^(a|an|to)\s+", "", phrase.lower())
        if core in low:
            return phrase
    return SERVICE_PHRASES.get(service, [service])[0]


_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]


def _digits_in_words(digits: str, doubles: bool = False) -> str:
    """ "9844501234" -> "nine eight double four five zero one two three four" (Indian style with doubles)."""
    out, i = [], 0
    while i < len(digits):
        run = 1
        while doubles and i + run < len(digits) and digits[i + run] == digits[i] and run < 3:
            run += 1
        if run == 3:
            out.append(f"triple {_WORDS[int(digits[i])]}")
        elif run == 2:
            out.append(f"double {_WORDS[int(digits[i])]}")
        else:
            out.append(_WORDS[int(digits[i])])
        i += run
    return " ".join(out)
