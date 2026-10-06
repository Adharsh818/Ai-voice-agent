"""
The single source of what Emma may state as fact (plan 3.1; docs/R2_DESIGN.md,
section 11).

Two sources, merged into the per-turn brief and the validators' allow-list:

- The DB catalog (read from SQLite, never hard-coded): branches, doctors
  (gender, branch, services, weekly hours), services (duration, aliases) and
  which branch offers which service (derived from doctor_services). This is
  what makes booking branch-aware: braces are only at the branches whose
  doctors do braces.
- The verified knowledge base in clinic_facts.json: prices, payment,
  insurance, parking, what to bring, policies, branch addresses and
  landmarks. Only entries marked "verified": true are ever loaded.

Anything in neither source is unknown: Emma says so honestly ("I'm not sure
about that one") and offers a callback, instead of inventing it or
deflecting to the doctor. General, non-clinical dental knowledge (what a
root canal is) is the model's own, allowed by the brief without numbers.

Owner in Sprint 1b: E3. The dataclasses are the contract (fields are only ever
added, with defaults).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time as time_mod
from dataclasses import dataclass, field
from datetime import time
from typing import Optional

import clock
import config
import prompts

logger = logging.getLogger(__name__)

# Said when nothing verified answers the question (never the doctor deflection).
DEFAULT_UNKNOWN = "Hmm, I'm not sure about that one."


@dataclass(frozen=True)
class Service:
    id: int
    name: str                          # canonical, "Root Canal Treatment"
    spoken: str                        # how Emma says it, "a root canal"
    duration_min: int
    is_consultation: bool
    aliases: tuple = ()                # from services.aliases_json plus STT slips
    branches: tuple = ()               # branch names that offer it (any active doctor does it)


@dataclass(frozen=True)
class Doctor:
    id: int
    name: str                          # "Dr. Meera Rao"
    spoken: str                        # "Dr Rao"
    gender: Optional[str]              # female | male | other
    branch: str
    branch_id: int
    services: tuple = ()               # service names
    hours: str = ""                    # spoken summary: "Monday to Saturday, 9 to 5"


@dataclass(frozen=True)
class Branch:
    id: int
    name: str                          # "Indiranagar"
    area: str
    address: str = ""                  # from the knowledge base (verified)
    landmark: str = ""
    parking: str = ""
    hours: str = ""                    # spoken, from the doctors' rota within clinic hours
    services: tuple = ()               # service names offered here


@dataclass(frozen=True)
class Catalog:
    """A snapshot of the clinic's structure. Loaded once per call (cheap) so a mid-call edit can't split a turn."""
    branches: tuple = ()
    doctors: tuple = ()
    services: tuple = ()
    loaded_at: str = ""

    def service(self, name: str) -> Optional[Service]:
        return next((s for s in self.services if s.name.lower() == (name or "").lower()), None)

    def branch(self, name: str) -> Optional[Branch]:
        return next((b for b in self.branches if b.name.lower() == (name or "").lower()), None)

    def doctor(self, spoken: str) -> Optional[Doctor]:
        key = (spoken or "").lower()
        return next((d for d in self.doctors if key in (d.spoken.lower(), d.name.lower())), None)

    def branches_offering(self, service: str) -> tuple:
        svc = self.service(service)
        return svc.branches if svc else ()

    def doctors_for(self, service: str, branch: Optional[str] = None, gender: Optional[str] = None) -> tuple:
        return tuple(d for d in self.doctors
                     if service in d.services
                     and (branch is None or d.branch.lower() == branch.lower())
                     and (gender is None or d.gender == gender))


@dataclass(frozen=True)
class Fact:
    id: str                            # "price.root_canal"
    topic: str                         # "root canal price"
    text: str                          # the verified sentence Emma may say
    keywords: tuple = ()               # for the no-model fallback lookup
    service: str = ""                  # catalog service a price fact is for ("Braces"), else ""


@dataclass(frozen=True)
class Knowledge:
    """The verified knowledge base (clinic_facts.json)."""
    version: str = ""
    clinic_name: str = ""
    hours: str = ""
    overview: str = ""                 # "tell me about the clinic"
    facts: tuple = ()                  # Fact
    unknown_line: str = ""             # honest "not sure" wording (never the doctor deflection)
    branches: tuple = ()               # (name, ((key, value), ...)): verified address / landmark / parking

    def get(self, fact_id: str) -> Optional[Fact]:
        return next((f for f in self.facts if f.id == fact_id), None)

    def branch_info(self, name: str) -> dict:
        """Verified address / landmark / parking for a branch name ({} when the KB has none)."""
        key = (name or "").lower()
        return next((dict(info) for n, info in self.branches if n.lower() == key), {})


# ---------------------------------------------------------------- loading

# STT slips and everyday words for services, on top of services.aliases_json
# (the 1 Oct calls: "route canal" for root canal). Kept short: every alias is
# matched anywhere in a sentence, so nothing that means something else here.
STT_ALIASES = {
    "Root Canal Treatment": ("route canal", "root canal treatment", "root canals"),
    "General Check-up": ("general checkup", "routine check", "routine checkup", "dental check"),
    "Teeth Cleaning": ("teeth cleaning", "clean my teeth", "cleaning of teeth"),
    "Tooth Filling": ("fillings", "tooth filling"),
    "Tooth Extraction": ("tooth removal", "remove my tooth", "pull my tooth", "take out a tooth"),
    "Braces": ("brace", "teeth straightening"),
    "Pediatric Dentistry": ("children's dentist", "kids dentistry", "child's teeth", "children's dentistry"),
}

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _hm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def _speak_days(days: list) -> str:
    """[0..5] -> "Monday to Saturday"; [0, 2, 4] -> "Monday, Wednesday and Friday"."""
    days = sorted(days)
    if len(days) >= 3 and days == list(range(days[0], days[-1] + 1)):
        return f"{_WEEKDAYS[days[0]]} to {_WEEKDAYS[days[-1]]}"
    return prompts.speak_list([_WEEKDAYS[d] for d in days], "and")


def _speak_rota(spans_by_day: dict) -> str:
    """
    {weekday: [(start, end), ...]} -> "Monday to Friday, 10 to 6, and Saturday,
    10 to 2". Days with the same hours are grouped, the way staff say it.
    """
    groups: dict = {}
    for day in sorted(spans_by_day):
        key = tuple(sorted(spans_by_day[day]))
        groups.setdefault(key, []).append(day)
    parts = []
    for spans, days in sorted(groups.items(), key=lambda kv: kv[1][0]):
        hours = " and ".join(prompts.speak_span(_hm(a), _hm(b)) for a, b in spans)
        parts.append(f"{_speak_days(days)}, {hours}")
    return ", and ".join(parts)


def load_catalog(conn, kb: Optional[Knowledge] = None) -> Catalog:
    """
    Build the Catalog from the DB (branches, doctors, services, doctor_services,
    availability_rules). A branch offers a service when one of its active
    doctors does it (doctor_services); that is never written down anywhere
    else, so a rota change in the DB changes what Emma offers. Addresses and
    parking come from the verified knowledge base (`kb`, default get_knowledge()).
    """
    kb = kb if kb is not None else get_knowledge()
    branch_rows = conn.execute("SELECT id, name, area FROM branches WHERE active = 1 ORDER BY id").fetchall()
    service_rows = conn.execute(
        "SELECT id, name, duration_min, is_consultation, aliases_json FROM services WHERE active = 1 ORDER BY id"
    ).fetchall()
    doctor_rows = conn.execute(
        "SELECT d.id, d.name, d.spoken_name, d.gender, d.branch_id, b.name AS branch "
        "FROM doctors d JOIN branches b ON b.id = d.branch_id "
        "WHERE d.active = 1 AND b.active = 1 ORDER BY d.id"
    ).fetchall()
    service_name = {r["id"]: r["name"] for r in service_rows}
    does: dict = {}                                          # doctor id -> [service names], in service order
    for r in conn.execute("SELECT doctor_id, service_id FROM doctor_services ORDER BY service_id"):
        if r["service_id"] in service_name:
            does.setdefault(r["doctor_id"], []).append(service_name[r["service_id"]])
    rota: dict = {}                                          # doctor id -> {weekday: [(start, end)]}
    for r in conn.execute("SELECT doctor_id, weekday, start_time, end_time FROM availability_rules "
                          "ORDER BY doctor_id, weekday, start_time"):
        rota.setdefault(r["doctor_id"], {}).setdefault(r["weekday"], []).append((r["start_time"], r["end_time"]))

    doctors = tuple(
        Doctor(id=r["id"], name=r["name"], spoken=r["spoken_name"], gender=r["gender"], branch=r["branch"],
               branch_id=r["branch_id"], services=tuple(does.get(r["id"], ())),
               hours=_speak_rota(rota.get(r["id"], {})))
        for r in doctor_rows
    )

    branches = []
    for r in branch_rows:
        here = [d for d in doctors if d.branch_id == r["id"]]
        offered = {s for d in here for s in d.services}
        # Branch hours: earliest start to latest finish of its doctors, per weekday.
        days: dict = {}
        for d in here:
            for wd, spans in rota.get(d.id, {}).items():
                lo, hi = min(a for a, _ in spans), max(b for _, b in spans)
                cur = days.get(wd)
                days[wd] = [(min(lo, cur[0][0]), max(hi, cur[0][1]))] if cur else [(lo, hi)]
        info = kb.branch_info(r["name"])
        branches.append(Branch(
            id=r["id"], name=r["name"], area=r["area"], address=info.get("address", ""),
            landmark=info.get("landmark", ""), parking=info.get("parking", ""), hours=_speak_rota(days),
            services=tuple(s["name"] for s in service_rows if s["name"] in offered),
        ))

    services = []
    for r in service_rows:
        try:
            aliases = [a.lower() for a in json.loads(r["aliases_json"] or "[]") if a]
        except ValueError:
            aliases = []
        for extra in STT_ALIASES.get(r["name"], ()):
            if extra not in aliases:
                aliases.append(extra)
        services.append(Service(
            id=r["id"], name=r["name"], spoken=prompts.speak_service(r["name"]), duration_min=r["duration_min"],
            is_consultation=bool(r["is_consultation"]), aliases=tuple(aliases),
            branches=tuple(b.name for b in branches if r["name"] in b.services),
        ))
    return Catalog(branches=tuple(branches), doctors=doctors, services=tuple(services),
                   loaded_at=clock.now().isoformat(timespec="seconds"))


def _verified_text(entry) -> str:
    return entry.get("text", "") if isinstance(entry, dict) and entry.get("verified") else ""


def load_knowledge(path: Optional[str] = None) -> Knowledge:
    """
    Verified entries of clinic_facts.json (config.CLINIC_FACTS_PATH); unverified
    ones are left out entirely. The old engine's "escalation" deflection line
    ("the doctor can go through that...") is never loaded: Emma answers, or
    says honestly she isn't sure (feedback 3).
    """
    path = path or config.CLINIC_FACTS_PATH
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.warning("clinic_facts.json unavailable (%s); knowledge base is empty", exc)
        return Knowledge(clinic_name=config.CLINIC_NAME, unknown_line=DEFAULT_UNKNOWN)

    facts = []
    for key in ("hours", "pricing_policy", "insurance_policy"):
        entry = raw.get(key) or {}
        if _verified_text(entry):
            facts.append(Fact(id=key, topic=key.replace("_", " "), text=entry["text"],
                              keywords=tuple(k.lower() for k in entry.get("keywords", ()))))
    for entry in raw.get("facts", []):
        if _verified_text(entry) and entry.get("id"):
            facts.append(Fact(id=entry["id"], topic=entry.get("topic", entry["id"]), text=entry["text"],
                              keywords=tuple(k.lower() for k in entry.get("keywords", ())),
                              service=entry.get("service", "")))
    branches = []
    for entry in raw.get("branches", []):
        if not (isinstance(entry, dict) and entry.get("verified") and entry.get("name")):
            continue
        name = entry["name"]
        info = {k: entry[k] for k in ("address", "landmark", "parking")
                if entry.get(k) and entry[k] != "PLACEHOLDER"}
        branches.append((name, tuple(sorted(info.items()))))
        low = name.lower()
        if info.get("address"):
            facts.append(Fact(id=f"branch.{name}.address", topic=f"{name} address",
                              text=f"Our {name} branch is at {info['address']}.",
                              keywords=(low, "address", "located", "location", "where", "directions", "landmark")))
        if info.get("parking"):
            facts.append(Fact(id=f"branch.{name}.parking", topic=f"{name} parking", text=info["parking"],
                              keywords=(low, "parking", "park", "car", "bike", "two-wheeler")))
    overview = next((f.text for f in facts if f.id == "clinic.overview"), "")
    return Knowledge(
        version=str(raw.get("version", "")),
        clinic_name=raw.get("clinic_name", config.CLINIC_NAME),
        hours=_verified_text(raw.get("hours")),
        overview=overview,
        facts=tuple(facts),
        unknown_line=raw.get("unknown_line") or DEFAULT_UNKNOWN,
        branches=tuple(branches),
    )


def knowledge_block(catalog: Catalog, kb: Knowledge) -> str:
    """
    The KNOWLEDGE section of the model's system prompt: every verified fact
    with its id, and the catalog (branches with address / parking / hours and
    services offered; doctors with gender, branch and services; services with
    duration and branches). Stable text for a given catalog + KB version, so
    the provider's prompt cache can reuse it across turns.
    """
    lines = [f"CLINIC: {kb.clinic_name or config.CLINIC_NAME}."]
    if kb.hours:
        lines.append(f"HOURS: {kb.hours}")
    lines.append("FACTS (verified; say them in your own words, keep every number exact):")
    for f in kb.facts:
        if f.id.startswith("branch."):
            continue                                         # listed under BRANCHES below
        lines.append(f"- [{f.id}] {f.text}")
    lines.append("BRANCHES (every branch is bookable, but only for the services listed for it):")
    for b in catalog.branches:
        detail = [f"- {b.name}"]
        if b.address:
            detail.append(f"address: {b.address}")
        if b.parking:
            detail.append(f"parking: {b.parking}")
        if b.hours:
            detail.append(f"hours: {b.hours}")
        detail.append("services: " + (", ".join(b.services) or "none"))
        lines.append("; ".join(detail))
    lines.append("DOCTORS:")
    for d in catalog.doctors:
        lines.append(f"- {d.spoken} ({d.name}), {d.gender or 'unspecified'}, {d.branch}: "
                     f"{', '.join(d.services)}; hours: {d.hours or 'not on the rota'}")
    lines.append("SERVICES:")
    for s in catalog.services:
        where = ", ".join(s.branches) or "not bookable right now"
        lines.append(f"- {s.name} (say \"{s.spoken}\"), {s.duration_min} min, at {where}")
    return "\n".join(lines)


# ---------------------------------------------------------------- the no-model fallback

_PRICE_CUE = re.compile(r"\b(price|prices|pricing|cost|costs|costing|charge|charges|fee|fees|rate|rates|"
                        r"how much|expensive|cheap|budget|rupees)\b")
_WHERE_CUE = re.compile(r"\b(which|what|where|any)\s+(branch|branches|clinic|clinics|location|locations|centre|"
                        r"center)\b|\bwhere (can|do|is|are)\b|\bwhich (one|place)\b")
_DOCTOR_CUE = re.compile(r"\b(doctors?|dentists?|dr|lady doctor|female doctor|male doctor|orthodontist)\b")
_WHO_CUE = re.compile(r"\b(who|which|what|any|names?|list|tell me)\b")
_HOURS_CUE = re.compile(r"\b(hours|timings?|open|opening|close|closing|what time|available on|in on|working)\b")
_SERVICES_CUE = re.compile(r"\b(what|which)\s+(services|treatments)\b|\bwhat (all )?do you (do|offer)\b|"
                           r"\bservices (do you|you) (have|offer|provide)\b")
_DO_YOU_DO = re.compile(r"\b(do you|can you|you guys|is there)\s+(do|offer|provide|have|treat)\b|\bdo you do\b")
_BRANCHES_CUE = re.compile(r"\b(where are you|where is the clinic|your (branches|locations)|how many branches|"
                           r"which areas|branches do you have|located)\b")
_BRANCH_WHERE = re.compile(r"\b(where'?s|where (is|are)|address|located|directions|landmark|"
                           r"how (do|can) i (get|reach))\b")
_FEMALE = re.compile(r"\b(lady|female|woman|women)\b")
_MALE = re.compile(r"\b(male|gents?|man|gentleman)\b")
_STOP = frozenset("a an the is are do does you your i me my we our to of for in on at and or it this that what "
                  "which how can could would please tell about there any".split())


def _norm(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9' -]+", " ", (text or "").lower().replace("’", "'")).strip() + " "


def _has(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase.lower())}(?![a-z0-9])", text) is not None


def _services_in(text: str, catalog: Catalog) -> list:
    """Catalog services named in the text, by name, spoken form or alias (longest match wins ties)."""
    found = []
    for s in catalog.services:
        names = {s.name.lower(), s.spoken.lower().removeprefix("a ").removeprefix("an "), *s.aliases}
        if any(_has(text, n) for n in names if n):
            found.append(s)
    # "check-up" also matches Consultation's "see the doctor"? Keep the most specific: drop
    # Consultation when something more specific is named too.
    if len(found) > 1:
        found = [s for s in found if not s.is_consultation or s.name.lower() in text] or found
    return found


def _branches_in(text: str, catalog: Catalog) -> list:
    return [b for b in catalog.branches if _has(text, b.name)]


def _doctor_names(doctors) -> str:
    return prompts.speak_list([d.spoken for d in doctors], "and")


def _gender_in(text: str) -> Optional[str]:
    if _FEMALE.search(text):
        return "female"
    if _MALE.search(text):
        return "male"
    return None


def _catalog_answer(t: str, catalog: Catalog, kb: Knowledge) -> Optional[str]:
    """Questions the DB answers: which branch does X, who are the doctors, hours, what services."""
    services, branches = _services_in(t, catalog), _branches_in(t, catalog)
    doctor = next((d for d in catalog.doctors
                   if _has(t, d.spoken) or _has(t, d.name.replace(".", "")) or _has(t, d.name)
                   or _has(t, "doctor " + d.spoken.split()[-1])), None)

    if doctor is not None:
        if _HOURS_CUE.search(t) or re.search(r"\b(when|which days?|days)\b", t):
            return f"{doctor.spoken} works {doctor.hours}, at our {doctor.branch} branch."
        return f"{doctor.spoken} is at our {doctor.branch} branch, for {prompts.speak_list([prompts.service_plural(s) for s in doctor.services], 'and')}."

    # "Can I get a check-up, and where is your Jayanagar branch?" asks for the
    # branch's address, not which branches do check-ups.
    if branches and _BRANCH_WHERE.search(t) and not _DO_YOU_DO.search(t):
        fact = kb.get(f"branch.{branches[0].name}.address")
        if fact is not None:
            return fact.text

    if services and (_WHERE_CUE.search(t) or _DO_YOU_DO.search(t) or _BRANCHES_CUE.search(t)) \
            and not _DOCTOR_CUE.search(t):
        s = services[0]
        plural = prompts.service_plural(s.name)
        if not s.branches:
            return None
        where = prompts.speak_list(s.branches, "and")
        noun = "branch" if len(s.branches) == 1 else "branches"
        prefix = "Yes, we" if _DO_YOU_DO.search(t) and not _WHERE_CUE.search(t) else "We"
        if branches and branches[0].name not in s.branches:
            return f"Our {branches[0].name} branch doesn't do {plural}, but our {where} {noun} do."
        return f"{prefix} do {plural} at our {where} {noun}."

    if (_DOCTOR_CUE.search(t) and (_WHO_CUE.search(t) or services or branches or _gender_in(t))) \
            or (services and re.search(r"\bwho (does|do|can do|is doing|handles)\b", t)):
        gender = _gender_in(t)
        pool = [d for d in catalog.doctors
                if (not services or services[0].name in d.services)
                and (not branches or d.branch == branches[0].name)
                and (gender is None or d.gender == gender)]
        if not pool:
            return None
        by_branch: dict = {}
        for d in pool:
            by_branch.setdefault(d.branch, []).append(d)
        if services:
            parts = [f"{_doctor_names(ds)} at {b}" for b, ds in by_branch.items()]
            return f"For {prompts.service_plural(services[0].name)}, it's {prompts.speak_list(parts, 'and')}."
        if len(by_branch) == 1:
            b, ds = next(iter(by_branch.items()))
            verb = "is" if len(ds) == 1 else "are"
            return f"At {b}, it's {_doctor_names(ds)}." if gender is None else \
                f"{_doctor_names(ds)} {verb} at our {b} branch."
        first, *rest = [f"{_doctor_names(ds)} {'is' if len(ds) == 1 else 'are'} at {b}" if i == 0
                        else f"{_doctor_names(ds)} at {b}" for i, (b, ds) in enumerate(by_branch.items())]
        return first + "".join(f", {p}" for p in rest[:-1]) + (f", and {rest[-1]}." if rest else ".")

    if _HOURS_CUE.search(t) and branches and branches[0].hours:
        return f"Our {branches[0].name} branch is open {branches[0].hours}."

    if _SERVICES_CUE.search(t):
        return "We do " + prompts.speak_list([prompts.service_plural(s.name) for s in catalog.services], "and") + "."

    if _BRANCHES_CUE.search(t) and not branches and catalog.branches:
        return (f"We have {_count_word(len(catalog.branches))} branches, at "
                f"{prompts.speak_list([b.name for b in catalog.branches], 'and')}.")
    return None


def _count_word(n: int) -> str:
    return ("zero one two three four five six seven eight nine ten".split() + [str(n)] * 99)[n] if n < 11 else str(n)


def _price_answer(t: str, kb: Knowledge, catalog: Catalog) -> Optional[str]:
    """A price question: the price fact for the treatment named, else the general pricing line."""
    prices = [f for f in kb.facts if f.id.startswith("price.")]
    named = {s.name for s in _services_in(t, catalog)}
    for f in prices:                                         # treatments the KB names but the DB doesn't book
        if f.service and f.service in named:
            return f.text
    generic = {"price", "cost", "charges", "fee"}
    for f in prices:
        if any(_has(t, k) for k in f.keywords if k not in generic):
            return f.text
    if re.search(r"\b(cancel|reschedul)", t):
        fact = kb.get("policy.cancellation")
        return fact.text if fact else None
    fact = kb.get("pricing_policy")
    return fact.text if fact else None


def _keyword_answer(t: str, kb: Knowledge, catalog: Catalog) -> Optional[str]:
    """The fact whose keywords overlap the question most. Ties between branches mean the branch wasn't said."""
    scored = []
    for f in kb.facts:
        if f.service:                                        # catalog price facts need a price cue (see lookup)
            continue
        score = sum(len(k.split()) for k in f.keywords if _has(t, k))
        if score:
            scored.append((score, f))
    if not scored:
        return None
    best = max(s for s, _ in scored)
    top = [f for s, f in scored if s == best]
    if len(top) > 1 and all(f.id.startswith("branch.") for f in top):
        names = prompts.speak_list([b.name for b in catalog.branches] or [f.id.split(".")[1] for f in top], "and")
        if all(f.id.endswith(".parking") for f in top):
            return f"Parking depends on the branch. We're at {names}, so just tell me which one and I'll check."
        return f"We have branches at {names}."
    return top[0].text


def lookup(text: str, kb: Knowledge, catalog: Catalog, faq_ids: tuple = ()) -> Optional[str]:
    """
    A verified answer without the model (Gemini down, or the model's answer
    failed validation): faq_ids first, then keyword overlap, then catalog
    questions (which branch does braces, who are the doctors, hours). None
    when nothing fits: the caller then hears the honest unknown line.

    In practice two structural checks run before the plain keyword overlap,
    because a lone keyword picks the wrong fact: "which branch does braces"
    contains "braces", which is the braces price fact's keyword. So: faq_ids,
    then a price question (a price cue + the treatment), then the catalog
    questions, then keyword overlap.
    """
    texts = [f.text for f in (kb.get(i) for i in faq_ids or ()) if f is not None]
    if texts:
        return " ".join(dict.fromkeys(texts[:2]))
    t = _norm(text)
    if not t.strip():
        return None
    if _PRICE_CUE.search(t):
        return _price_answer(t, kb, catalog)
    return _catalog_answer(t, catalog, kb) or _keyword_answer(t, kb, catalog)


# ---------------------------------------------------------------- allow-lists for the validators

_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
    "forty-five": 45, "fifty": 50, "sixty": 60, "hundred": 100,
}


def _numbers(text: str) -> frozenset:
    """
    Numbers as digit strings, the same way validate.numbers_in reads the
    model's words (used when it is available, so the two can't disagree).
    """
    try:
        from dialogue import validate
        return frozenset(validate.numbers_in(text))
    except (ImportError, NotImplementedError, AttributeError):
        pass
    low = (text or "").lower()
    found = {d.replace(",", "") for d in re.findall(r"\d[\d,]*(?:\.\d+)?", low)}
    found |= {n.split(".")[0] for n in list(found)}
    for word, value in _WORD_NUMBERS.items():
        if re.search(rf"\b{word}\b", low):
            found.add(str(value))
    if re.search(r"\bone and a half\b", low):
        found.add("1.5")
    for m in re.finditer(r"(\d+(?:\.\d+)?|one and a half|one|two|three|four|five)\s+lakh", low):
        word = m.group(1)
        value = 1.5 if word == "one and a half" else float(_WORD_NUMBERS.get(word, word))
        found.add(str(int(value * 100000)))
    return frozenset(x for x in found if x)


def allowed_numbers(catalog: Catalog, kb: Knowledge) -> frozenset:
    """Every number (as digits) that appears in verified knowledge or the catalog: the validators' base allow-list."""
    parts = [kb.hours, kb.overview, *(f.text for f in kb.facts)]
    for b in catalog.branches:
        parts += [b.address, b.parking, b.hours]
    for d in catalog.doctors:
        parts.append(d.hours)
    nums = set(_numbers(" ".join(p for p in parts if p)))
    nums |= {str(s.duration_min) for s in catalog.services}
    nums |= {str(len(catalog.branches)), str(len(catalog.doctors)), str(len(catalog.services))}
    # The clinic's own hours and lunch break (config), spoken in pre-written lines.
    for hour in (config.CLINIC_START_HOUR, config.CLINIC_END_HOUR, config.LUNCH_START_HOUR, config.LUNCH_END_HOUR):
        nums |= {str(hour), str(hour % 12 or 12)}
    nums |= {str(config.LUNCH_END_MIN)} if config.LUNCH_END_MIN else set()
    return frozenset(n for n in nums if n and n != "0")


def price_numbers(kb: Knowledge) -> dict:
    """service or topic keyword -> frozenset of numbers in its price fact (the price-consistency validator)."""
    out: dict = {}
    for f in kb.facts:
        if not f.id.startswith("price."):
            continue
        nums = _numbers(f.text)
        if not nums:
            continue
        keys = {f.id.split(".", 1)[1].replace("_", " "), f.topic.lower()}
        if f.service:
            keys.add(f.service.lower())
        keys |= {k for k in f.keywords if k not in ("price", "cost", "charges", "fee")}
        for key in keys:
            out[key] = out.get(key, frozenset()) | nums
    return out


# ---------------------------------------------------------------- process-wide snapshots

CATALOG_TTL_S = 60.0
_catalog_cache: dict = {}                                    # db path -> (monotonic time, Catalog)
_knowledge_cache: dict = {}                                  # path -> (mtime_ns, Knowledge)


async def get_catalog() -> Catalog:
    """
    Process-wide cached catalog, read on the database thread
    (await db.get_db().run(load_catalog)); refreshed when older than a minute.
    The engine takes one snapshot per turn (Runtime.catalog).
    """
    import db                                                # local: facts stays importable without the DB layer

    key = config.DB_PATH
    hit = _catalog_cache.get(key)
    if hit and time_mod.monotonic() - hit[0] < CATALOG_TTL_S:
        return hit[1]
    kb = get_knowledge()
    catalog = await db.get_db().run(load_catalog, kb)
    _catalog_cache.clear()                                   # one database per process; tests switch paths
    _catalog_cache[key] = (time_mod.monotonic(), catalog)
    return catalog


def clear_cache() -> None:
    """Forget the cached catalog and knowledge base (tests, or after editing the clinic in the dashboard)."""
    _catalog_cache.clear()
    _knowledge_cache.clear()


def get_knowledge() -> Knowledge:
    """Process-wide cached knowledge base; reloaded when clinic_facts.json changes."""
    path = config.CLINIC_FACTS_PATH
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        mtime = None
    hit = _knowledge_cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    kb = load_knowledge(path)
    _knowledge_cache.clear()
    _knowledge_cache[path] = (mtime, kb)
    return kb
