"""
Running conversations: one scenario, one simulated caller, or a whole suite.

Every conversation runs in its own world (harness/world.py: a fresh seeded
SQLite file and a frozen clock) and produces one JSON record: the transcript
with per-turn engine data, the database outcome, the automatic metrics
(harness/metrics.py) and, for scripted scenarios, the result of every
expected property. A suite writes those records plus a summary and a
Markdown report (harness/report.py) under harness_runs/<run-id>/, which is
git-ignored.

The engine keeps process-wide state (config.DB_PATH, the database thread, the
frozen clock, llm's client), so conversations in one process run one after
another. Concurrency means worker processes: offline runs default to one
process (a call takes well under a second), live runs to two, which keeps a
single Gemini key under its rate limit. A 429 is retried with a back-off by
the adapter; a call whose model requests still failed is marked llm_degraded
and left out of the dialogue metrics, and once the key looks exhausted the
remaining calls are skipped rather than run against a dead model.

    from harness import runner
    record = runner.run_scenario("reg_location_no_loop")          # offline
    run_dir, summary = runner.run_suite("sim", n=20, seed=1)
"""

import asyncio
import json
import logging
import os
import random
import re
import time
import traceback
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime, time as dtime, timedelta
from typing import Callable, Optional

import config

from harness import engine_adapter, lines, metrics, sim_caller
from harness import scenarios as sc
from harness import world as world_mod

logger = logging.getLogger(__name__)

RUNS_DIR = os.path.join(config.BASE_DIR, "harness_runs")
SUITES = ("regression", "catalogue", "scenarios", "sim")
DEFAULT_CONCURRENCY = {"offline": 1, "nlu_down": 1, "live": 2}
# A whole call, including any 429 back-off waits; a stuck call is cut off and recorded as such.
CONVERSATION_TIMEOUT_S = {"offline": 120.0, "nlu_down": 120.0, "live": 900.0}

# The scenario caller's defaults; a scenario's `profile` overrides any of them.
# The name and number match the ones scripted lines say ("I'm Priya Sharma,
# my number is 98450 12345"), so the simulated caller and the script agree.
# Monday 5 Oct at 10:00 is free at Nagarbhavi in the DEMO seed (11:00 is
# taken), so a plain booking stays plain; cat_taken_slot asks for 11:00 on purpose.
SCENARIO_PROFILE = {
    "name": "Priya Sharma", "phone": "9845012345", "service": "Teeth Cleaning", "service_phrase": "a cleaning",
    "branch": None, "day": date(2026, 10, 5), "time": dtime(10, 0),
}

_PATTERN_NAMES = {
    sc.DEFLECT: "doctor deflection", sc.HUMAN_CLAIM: "claims to be human", sc.BOOK_WORDS: "booking",
    sc.MANAGE_WORDS: "change / cancel", sc.INFO_WORDS: "clinic info", sc.REAL_DOCTORS: "a real doctor's name",
}


def pattern_name(pattern: str) -> str:
    return _PATTERN_NAMES.get(pattern, pattern if len(pattern) <= 70 else pattern[:67] + "...")


# ---------------------------------------------------------------- one call in progress


class Conversation:
    """
    One call: the engine adapter, the engine's per-call session and the
    transcript so far. tools/converse.py pickles the session and keeps the
    turns between process invocations, so everything here is plain data.
    """

    def __init__(self, adapter: engine_adapter.EngineAdapter, session, *, turns: Optional[list] = None,
                 silence_step: int = 0, rng: Optional[random.Random] = None):
        self.adapter = adapter
        self.session = session
        self.turns: list[dict] = turns if turns is not None else []
        self.silence_step = silence_step
        self.rng = rng
        if self.turns:
            adapter.last_emma = self.turns[-1].get("emma") or ""

    @property
    def last_emma(self) -> str:
        return self.turns[-1]["emma"] if self.turns else ""

    @property
    def closed(self) -> bool:
        return bool(self.turns and self.turns[-1].get("closed"))

    async def greet(self) -> dict:
        turn = await self.adapter.turn(self.session, "")
        return self._append(turn, {"by": "greeting"})

    async def say(self, text: str, *, heard_previous: bool = True, **meta) -> dict:
        """The caller's words ("" or whitespace = silence) and Emma's reply."""
        if not (text or "").strip():
            self.silence_step += 1
            said = tuple(t.get("emma") or "" for t in self.turns)
            turn = self.adapter.silence_turn(self.session, self.silence_step, rng=self.rng, said=said)
            return self._append(turn, meta)
        self.silence_step = 0
        turn = await self.adapter.turn(self.session, text, heard_previous=heard_previous)
        return self._append(turn, meta)

    def _append(self, turn: dict, meta: dict) -> dict:
        turn = {"n": len(self.turns), **turn}
        for key, value in meta.items():
            if value not in (None, ""):
                turn[key] = value
        self.turns.append(turn)
        return turn


def llm_summary(turns: list[dict], adapter: Optional[engine_adapter.EngineAdapter] = None) -> dict:
    """How the model calls went in one conversation (live runs: quota and timeouts)."""
    called = [t for t in turns if t.get("llm_called")]
    errors = Counter(t.get("llm_error") or "failed" for t in called if t.get("llm_ok") is False)
    return {
        "calls": len(called),
        "failed": sum(errors.values()),
        "errors": dict(errors),
        "quota_wait_ms": sum(t.get("quota_wait_ms") or 0 for t in turns),
        "quota_exhausted": bool(adapter and adapter.quota_exhausted),
    }


def build_record(*, conv_id: str, suite: str, mode: str, now: datetime, turns: list, outcome: dict, catalog: dict,
                 goal: Optional[str], expected_outcome: Optional[str], ended_by: str, card: Optional[dict] = None,
                 simple: bool = False, sim: Optional[dict] = None, profile: Optional[dict] = None,
                 seed=None, extra: Optional[dict] = None, adapter=None, started: Optional[float] = None) -> dict:
    """One conversation as plain JSON, scored by metrics.evaluate()."""
    record = {
        "id": conv_id, "suite": suite, "mode": mode, "seed": seed, "now": now.isoformat(),
        "goal": goal, "expected_outcome": expected_outcome, "simple": bool(simple), "card": card,
        "profile": profile, "sim": sim, "ended_by": ended_by, "outcome": outcome, "catalog": catalog,
        "engine": engine_adapter.EngineAdapter.features(), "llm": llm_summary(turns, adapter),
        "engine_name": getattr(adapter, "engine_used", None),
        "caller_turns": max(0, len(turns) - 1), "turns": turns,
        "duration_s": round(time.perf_counter() - started, 2) if started else None,
    }
    if extra:
        record.update(extra)
    record["metrics"] = metrics.evaluate(record)
    return record


# ---------------------------------------------------------------- scripted scenarios


def scenario_card(w: world_mod.World, scn: sc.Scenario) -> Optional[dict]:
    """The seeded appointment a managing scenario works on (deterministic per scenario)."""
    if not scn.card:
        return None
    today = w.now.date()
    if (scn.profile or {}).get("earlier"):
        # "Prepone" needs room: an appointment at least three days out.
        for i in range(60):
            card = w.pick_card(index=i)
            if card is None:
                break
            if date.fromisoformat(card["date"]) >= today + timedelta(days=3):
                return card
    return w.pick_card(index=0)


def scenario_profile(scn: sc.Scenario, card: Optional[dict], today: date) -> sim_caller.Profile:
    """The simulated caller that plays a scenario's Auto steps."""
    p = dict(SCENARIO_PROFILE)
    over = dict(scn.profile or {})
    start_goal = over.pop("goal", None) or scn.goal
    earlier = over.pop("earlier", False)
    if scn.goal == "emergency" or start_goal == "emergency":
        p.update(service="Consultation", service_phrase="an emergency visit", day=today)
    if card and scn.goal in ("cancel", "reschedule", "check"):
        card_day = date.fromisoformat(card["date"])
        p.update(name=card["patient"], phone=card["phone"], service=card["service"], branch=card["branch"],
                 service_phrase=sim_caller.SERVICE_PHRASES.get(card["service"], [card["service"]])[0])
        if scn.goal in ("cancel", "check"):
            p.update(day=card_day, time=dtime.fromisoformat(card["time"]))
        else:
            p["day"] = sim_caller.moved_day(card_day, -1 if earlier else 2, today)
    p.update(over)
    return sim_caller.Profile(goal=start_goal, card=card, **p)


def _placeholders(card: Optional[dict], profile: sim_caller.Profile) -> dict:
    out = {"name": profile.name, "phone": profile.phone}
    for key, value in (card or {}).items():
        out[f"card_{key}"] = value
    if card:
        out.update(card_phone=card["phone_spoken"], card_date=card["date_spoken"], card_time=card["time_spoken"])
    return out


def ask_matches(ask: lines.Ask, until: tuple) -> bool:
    """Is Emma asking one of `until` ("date", "confirm:summary", "choice:time"...)?"""
    names = {ask.kind}
    if ask.kind == "confirm":
        names.add(f"confirm:{ask.slot}")
    if ask.kind == "choice":
        names.add(f"choice:{ask.subject}")
    return bool(names & set(until))


def _say_checks(step: sc.Say, turn: dict, step_no: int) -> list[dict]:
    reply = turn.get("emma") or ""
    out = []
    for pattern in step.expect:
        out.append({"type": "expect", "step": step_no, "turn": turn["n"], "pattern": pattern_name(pattern),
                    "ok": bool(re.search(pattern, reply, re.IGNORECASE))})
    if step.expect_any:
        ok = any(re.search(p, reply, re.IGNORECASE) for p in step.expect_any)
        out.append({"type": "expect_any", "step": step_no, "turn": turn["n"],
                    "pattern": " | ".join(pattern_name(p) for p in step.expect_any), "ok": ok})
    for pattern in step.forbid:
        m = re.search(pattern, reply, re.IGNORECASE)
        out.append({"type": "forbid", "step": step_no, "turn": turn["n"], "pattern": pattern_name(pattern),
                    "ok": m is None, **({"found": m.group(0)} if m else {})})
    return out


async def _auto(conv: Conversation, caller: sim_caller.SimCaller, step: sc.Auto, today: date) -> str:
    """Let the simulated caller answer until Emma asks one of step.until (or the call ends)."""
    done = 0
    while True:
        line = conv.last_emma
        if step.until and ask_matches(lines.classify_ask(line, today), step.until):
            return "reached"
        if conv.closed:
            return "emma_closed"
        if done >= step.max_turns:
            return "max_turns"
        ct = caller.respond(line)
        if ct is None:
            return caller.ended_by or "caller_done"
        await conv.say(ct.text, heard_previous=ct.heard_previous, by="sim", disruption=ct.disruption, ask_seen=ct.ask)
        done += 1
        if ct.final:
            return caller.ended_by or "caller_bye"


async def _drive_scenario(scn: sc.Scenario, conv: Conversation, caller: sim_caller.SimCaller, today: date,
                          slots: dict, checks: list) -> str:
    for step_no, step in enumerate(scn.steps):
        if conv.closed:
            checks.append({"type": "note", "step": step_no, "ok": True,
                           "detail": f"Emma ended the call before step {step_no}"})
            return "emma_closed"
        if isinstance(step, sc.Say):
            text = step.text.format_map(slots)
            caller.note_said(text, expect=lines.classify_ask(conv.last_emma, today).expect)
            turn = await conv.say(text, heard_previous=step.heard_previous, by="script", label=step.label)
            checks.extend(_say_checks(step, turn, step_no))
            continue
        result = await _auto(conv, caller, step, today)
        if step.until:
            if result != "reached":
                checks.append({"type": "note" if step.optional else "reach", "step": step_no,
                               "turn": len(conv.turns) - 1, "ok": step.optional,
                               "pattern": " / ".join(step.until), "detail": f"never reached ({result})"})
                return result
            continue
        return result
    return "script_end"


def _expect_checks(scn: sc.Scenario, record: dict) -> list[dict]:
    exp = scn.expect
    out = []
    outcome = record["outcome"]
    if exp.outcome:
        met = metrics.goal_met(exp.outcome, outcome, record.get("card"))
        out.append({"type": "outcome", "ok": bool(met), "pattern": exp.outcome, "detail": outcome_text(outcome)})
    booked = outcome.get("booked") or []
    if exp.booked and booked:
        appt = booked[0]
        start = datetime.fromisoformat(appt["start"])
        want = exp.booked
        checks = []
        if "service" in want:
            checks.append(("service", appt["service"] == want["service"], appt["service"]))
        if "branch_in" in want:
            checks.append(("branch", appt["branch"] in want["branch_in"], appt["branch"]))
        if "date" in want:
            checks.append(("date", start.date().isoformat() == want["date"], start.date().isoformat()))
        if "time" in want:
            checks.append(("time", start.strftime("%H:%M") == want["time"], start.strftime("%H:%M")))
        if "patient" in want:
            checks.append(("patient", lines.norm(want["patient"]) in lines.norm(appt["patient"]), appt["patient"]))
        if "phone" in want:
            checks.append(("phone", appt["phone"] == want["phone"], appt["phone"]))
        for name, ok, got in checks:
            out.append({"type": "booked", "ok": ok, "pattern": f"{name} = {want.get(name, want.get('branch_in'))}",
                        "detail": f"got {got}"})
    elif exp.booked and exp.outcome == "booked":
        out.append({"type": "booked", "ok": False, "pattern": "booking details", "detail": "nothing was booked"})
    emma_lines = [(t["n"], t.get("emma") or "") for t in record["turns"]]
    card = record.get("card")
    if exp.states_card and card:
        when = dtime.fromisoformat(card["time"])
        told = [n for n, line in emma_lines if n > 0 and lines.mentions_time(line, when)]
        out.append({"type": "states_card", "ok": bool(told), "pattern": f"tells the appointment time ({card['time']})",
                    **({"detail": f"turn {told[0]}"} if told else {})})
    for pattern in exp.must_not_say:
        hits = [n for n, line in emma_lines if re.search(pattern, line, re.IGNORECASE)]
        out.append({"type": "must_not_say", "ok": not hits, "pattern": pattern_name(pattern),
                    **({"detail": f"said at turn {hits[0]}"} if hits else {})})
    for pattern in exp.should_cover:
        hit = any(re.search(pattern, line, re.IGNORECASE) for _, line in emma_lines)
        out.append({"type": "should_cover", "ok": hit, "pattern": pattern_name(pattern)})
    counted = [f for f in record["metrics"]["findings"]
               if not f["llm_degraded"] and f["severity"] != "info"]
    for metric in exp.zero:
        hits = [f for f in counted if f["metric"] == metric]
        out.append({"type": "zero", "ok": not hits, "pattern": metric,
                    **({"detail": f"turn {hits[0]['turn']}: {hits[0]['detail'][:110]}"} if hits else {})})
    return out


def outcome_text(outcome: dict) -> str:
    parts = []
    for a in outcome.get("booked", []):
        parts.append(f"booked {a['service']} at {a['branch']} {a['start'][:16]}")
    for a in outcome.get("cancelled", []):
        parts.append(f"cancelled {a['service']} {a['start'][:16]}")
    for a in outcome.get("rescheduled", []):
        parts.append(f"moved {a['from'][:16]} -> {a['to'][:16]}")
    for t in outcome.get("tasks", []):
        parts.append(f"task {t['kind']}")
    return "; ".join(parts) or "no change"


def scenario_status(scn: sc.Scenario, passed: bool, error: bool = False, engine: Optional[str] = None) -> str:
    """
    pass | known-bug (fails, bug named) | fixed? (bug named but passes) | REGRESSION (an invariant fails).
    A scenario's `bug` names a defect of the 12-step machine; on the R2 engine
    every scenario is an invariant.
    """
    bug = None if engine == "r2" else scn.bug
    if error:
        return "error"
    if passed:
        return "fixed?" if bug else "pass"
    return "known-bug" if bug else "REGRESSION"


async def play_scenario(scn: sc.Scenario, w: world_mod.World, mode: str = "offline", seed: int = 0,
                        suite: str = "scenarios", engine: Optional[str] = None) -> dict:
    started = time.perf_counter()
    mode = "nlu_down" if scn.nlu_down else mode
    today = w.now.date()
    random.seed(f"scenario:{scn.id}:{seed}")
    rng = random.Random(f"{scn.id}:{seed}")
    catalog = w.catalog()
    card = scenario_card(w, scn)
    profile = scenario_profile(scn, card, today)
    caller = sim_caller.SimCaller(profile, rng, disruptions=(), today=today)
    adapter = engine_adapter.EngineAdapter(mode, call_id=scn.id, engine=engine)
    baseline = w.snapshot()
    checks: list[dict] = []
    ended_by = "script_end"
    with adapter.installed():
        session = adapter.new_session()
        adapter.engine_used = engine_adapter.engine_label(session)
        conv = Conversation(adapter, session, rng=random.Random(f"silence:{scn.id}"))
        try:
            ended_by = await asyncio.wait_for(_scenario_call(scn, conv, caller, today, card, profile, checks),
                                              timeout=CONVERSATION_TIMEOUT_S[mode])
        except asyncio.TimeoutError:
            ended_by = "harness_timeout"
            checks.append({"type": "timeout", "ok": False, "detail": "the call didn't finish in time"})
    outcome = adapter.outcome(baseline)
    record = build_record(
        conv_id=scn.id, suite=suite, mode=mode, now=w.now, turns=conv.turns, outcome=outcome, catalog=catalog,
        goal=scn.goal, expected_outcome=scn.expect.outcome, ended_by=ended_by, card=card, simple=scn.simple,
        sim=caller.flags(), profile=profile.as_dict(), seed=seed, adapter=adapter, started=started,
        extra={"title": scn.title, "group": scn.group, "source": scn.source, "bug": scn.bug,
               "steps": [_step_dict(s) for s in scn.steps]},
    )
    checks.extend(_expect_checks(scn, record))
    passed = all(c["ok"] for c in checks)
    record["checks"] = checks
    record["passed"] = passed
    record["status"] = scenario_status(scn, passed, engine=adapter.engine_used)
    return record


async def _scenario_call(scn, conv, caller, today, card, profile, checks) -> str:
    await conv.greet()
    return await _drive_scenario(scn, conv, caller, today, _placeholders(card, profile), checks)


def _step_dict(step) -> dict:
    if isinstance(step, sc.Say):
        out = {"say": step.text}
        if not step.heard_previous:
            out["heard_previous"] = False
        return out
    return {"auto": list(step.until) or "to the end", "max_turns": step.max_turns}


def run_scenario(scenario, mode: str = "offline", now=None, seed: int = 0, suite: str = "scenarios",
                 engine: Optional[str] = None) -> dict:
    """One scenario (a Scenario or its id) in a fresh world. Synchronous. `engine`: None, "legacy" or "r2"."""
    scn = sc.get(scenario) if isinstance(scenario, str) else scenario
    with world_mod.World(scn.now or now) as w:
        return asyncio.run(play_scenario(scn, w, mode=mode, seed=seed, suite=suite, engine=engine))


# ---------------------------------------------------------------- simulated callers


async def play_sim(index: int, seed: int, w: world_mod.World, mode: str = "offline", goal: Optional[str] = None,
                   intensity: Optional[int] = None, max_turns: int = sim_caller.MAX_TURNS,
                   engine: Optional[str] = None) -> dict:
    """One simulated call. (seed, index) reproduces it exactly in offline mode."""
    started = time.perf_counter()
    conv_id = f"sim-{seed}-{index:04d}"
    today = w.now.date()
    random.seed(f"sim:{seed}:{index}")
    rng = random.Random(f"sim:{seed}:{index}")
    catalog = w.catalog()
    card = w.pick_card(rng=rng)
    profile = sim_caller.random_profile(rng, goal=goal, card=card, catalog=catalog, today=today)
    disruptions = sim_caller.plan_disruptions(rng, intensity=intensity, card=card, goal=profile.goal)
    caller = sim_caller.SimCaller(profile, rng, disruptions=disruptions, max_turns=max_turns, today=today)
    adapter = engine_adapter.EngineAdapter(mode, call_id=conv_id, engine=engine)
    baseline = w.snapshot()
    ended = {"by": None}
    with adapter.installed():
        session = adapter.new_session()
        adapter.engine_used = engine_adapter.engine_label(session)
        conv = Conversation(adapter, session, rng=random.Random(f"silence:{conv_id}"))
        try:
            await asyncio.wait_for(_sim_call(conv, caller, ended), timeout=CONVERSATION_TIMEOUT_S[mode])
        except asyncio.TimeoutError:
            ended["by"] = "harness_timeout"
    outcome = adapter.outcome(baseline)
    simple = profile.goal == "book" and not disruptions and profile.service is not None
    managing = profile.goal in ("cancel", "reschedule") or caller.goal in ("cancel", "reschedule")
    return build_record(
        conv_id=conv_id, suite="sim", mode=mode, now=w.now, turns=conv.turns, outcome=outcome, catalog=catalog,
        goal=caller.goal, expected_outcome=caller.expected_outcome, ended_by=ended["by"] or "unknown",
        card=card if managing else None, simple=simple, sim=caller.flags(), profile=profile.as_dict(), seed=seed,
        adapter=adapter, started=started,
        extra={"index": index, "disruptions": disruptions,
               "disruptions_used": sorted({name for _turn, name in caller.used})},
    )


async def _sim_call(conv: Conversation, caller: sim_caller.SimCaller, ended: dict):
    await conv.greet()
    while True:
        ct = caller.respond(conv.last_emma)
        if ct is None:
            ended["by"] = caller.ended_by or "caller_done"
            return
        await conv.say(ct.text, heard_previous=ct.heard_previous, by="sim", disruption=ct.disruption,
                       ask_seen=ct.ask)
        if ct.final:
            ended["by"] = caller.ended_by or "caller_bye"
            return
        if conv.closed:
            ended["by"] = "silence" if conv.turns[-1].get("silence_step") else "emma_closed"
            return


def run_sim_call(index: int, seed: int = 1, mode: str = "offline", now=None, goal: Optional[str] = None,
                 intensity: Optional[int] = None, engine: Optional[str] = None) -> dict:
    with world_mod.World(now) as w:
        return asyncio.run(play_sim(index, seed, w, mode=mode, goal=goal, intensity=intensity, engine=engine))


# ---------------------------------------------------------------- jobs and suites


def jobs_for(suite: str, *, mode: str = "offline", n: int = 20, seed: int = 1, now=None, only=None,
             goal: Optional[str] = None, intensity: Optional[int] = None, engine: Optional[str] = None) -> list[dict]:
    """The conversations a suite runs, as picklable job dicts (one per worker call)."""
    now_iso = world_mod.parse_now(now).isoformat()
    if suite == "sim":
        return [{"kind": "sim", "suite": suite, "index": i, "seed": seed, "mode": mode, "now": now_iso,
                 "goal": goal, "intensity": intensity, "engine": engine, "id": f"sim-{seed}-{i:04d}"}
                for i in range(n)]
    chosen = sc.suite(suite)
    if only:
        wanted = set(only)
        chosen = [s for s in sc.SCENARIOS if s.id in wanted]
        missing = wanted - {s.id for s in chosen}
        if missing:
            raise KeyError(f"unknown scenario ids: {', '.join(sorted(missing))}")
    return [{"kind": "scenario", "suite": suite, "id": s.id, "seed": seed, "mode": mode, "now": now_iso,
             "engine": engine} for s in chosen]


def run_job(job: dict) -> dict:
    """Run one job in this process and return its record (never raises: errors become records)."""
    started = time.perf_counter()
    try:
        if job["kind"] == "sim":
            return run_sim_call(job["index"], seed=job["seed"], mode=job["mode"], now=job["now"],
                                goal=job.get("goal"), intensity=job.get("intensity"), engine=job.get("engine"))
        return run_scenario(job["id"], mode=job["mode"], now=job["now"], seed=job["seed"], suite=job["suite"],
                            engine=job.get("engine"))
    except Exception as exc:
        logger.error("harness error in %s: %s", job.get("id"), exc, exc_info=True)
        return {"id": job.get("id"), "suite": job.get("suite"), "mode": job.get("mode"), "harness_error":
                f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc(limit=12),
                "duration_s": round(time.perf_counter() - started, 2)}


def quiet_logging(level: int = logging.ERROR):
    """Keep engine and llm chatter off the console (the adapter still records llm warnings)."""
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    for handler in root.handlers:
        handler.setLevel(level)


def _worker_init(level: int):
    quiet_logging(level)


def new_run_id(suite: str, mode: str, label: Optional[str] = None) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    tail = f"-{re.sub(r'[^A-Za-z0-9_-]+', '-', label)}" if label else ""
    return f"{stamp}-{suite}-{mode}{tail}"


def _brief(record: dict) -> str:
    if record.get("harness_error"):
        return f"HARNESS ERROR {record['harness_error'][:80]}"
    m = record["metrics"]
    bad = sorted(m["counts"])
    llm = record["llm"]
    parts = [f"{record['caller_turns']:>2} turns", f"{record['outcome']['kind']:<11}"]
    if "status" in record:
        parts.insert(0, f"{record['status']:<10}")
    else:
        parts.insert(0, f"{record['goal']:<10}")
        parts.append("goal met" if m.get("goal_met") else ("goal unmet" if m.get("goal_met") is False else ""))
    if bad:
        parts.append(" ".join(bad))
    if llm["failed"]:
        parts.append(f"llm failed x{llm['failed']} ({', '.join(llm['errors'])})")
    return "  ".join(p for p in parts if p)


def run_suite(suite: str, *, mode: str = "offline", n: int = 20, seed: int = 1, concurrency: Optional[int] = None,
              now=None, only=None, goal: Optional[str] = None, intensity: Optional[int] = None,
              label: Optional[str] = None, out_root: Optional[str] = None, publish: bool = False,
              progress: Optional[Callable[[str], None]] = print, log_level: int = logging.ERROR,
              engine: Optional[str] = None) -> tuple:
    """
    Run a suite and write harness_runs/<run-id>/: conversations/<id>.json,
    summary.json, report.md and transcripts.md. Returns (run_dir, summary).
    `publish` also copies the report to docs/test-reports/<run-id>.md.
    """
    from harness import report        # local: report imports runner for pattern names

    if suite not in SUITES:
        raise KeyError(f"suite must be one of {SUITES}")
    jobs = jobs_for(suite, mode=mode, n=n, seed=seed, now=now, only=only, goal=goal, intensity=intensity,
                    engine=engine)
    concurrency = max(1, concurrency or DEFAULT_CONCURRENCY.get(mode, 1))
    run_id = new_run_id(suite, mode, "-".join(x for x in (engine, label) if x) or None)
    run_dir = os.path.join(out_root or RUNS_DIR, run_id)
    conv_dir = os.path.join(run_dir, "conversations")
    os.makedirs(conv_dir, exist_ok=True)
    say = progress or (lambda _msg: None)
    say(f"run {run_id}: {len(jobs)} conversation(s), {mode}, engine {engine or 'default'}, "
        f"{concurrency} worker(s)")
    started = time.perf_counter()
    records: list[dict] = []
    skipped: list[str] = []

    def keep(record: dict):
        records.append(record)
        path = os.path.join(conv_dir, f"{record['id']}.json")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(record, fh, ensure_ascii=False, indent=1, default=str)
        say(f"[{len(records):>4}/{len(jobs)}] {record['id']:<34} {_brief(record)}")

    def exhausted(record: dict) -> bool:
        return bool((record.get("llm") or {}).get("quota_exhausted"))

    if concurrency == 1:
        quiet_logging(log_level)
        for i, job in enumerate(jobs):
            record = run_job(job)
            keep(record)
            if exhausted(record):
                skipped = [j["id"] for j in jobs[i + 1:]]
                say(f"Gemini quota looks exhausted: skipping the remaining {len(skipped)} conversation(s)")
                break
    else:
        with ProcessPoolExecutor(max_workers=concurrency, initializer=_worker_init, initargs=(log_level,)) as pool:
            futures = {pool.submit(run_job, job): job for job in jobs}
            stop = False
            for future in as_completed(futures):
                if future.cancelled():
                    continue
                record = future.result()
                keep(record)
                if exhausted(record) and not stop:
                    stop = True
                    for other, job in futures.items():
                        if other.cancel():
                            skipped.append(job["id"])
                    if skipped:
                        say(f"Gemini quota looks exhausted: skipped {len(skipped)} conversation(s)")
    order = {job["id"]: i for i, job in enumerate(jobs)}
    records.sort(key=lambda r: order.get(r["id"], 0))
    meta = {"run_id": run_id, "suite": suite, "mode": mode, "seed": seed, "n": len(jobs),
            "now": world_mod.parse_now(now).isoformat(), "concurrency": concurrency, "goal": goal,
            "intensity": intensity, "only": list(only) if only else None, "skipped": skipped,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "wall_s": round(time.perf_counter() - started, 1),
            "engine": engine_adapter.EngineAdapter.features(), "engine_name": engine or "default"}
    summary = report.write(run_dir, records, meta)
    if publish:
        target = os.path.join(config.BASE_DIR, "docs", "test-reports", f"{run_id}.md")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(os.path.join(run_dir, "report.md"), encoding="utf-8") as src, \
                open(target, "w", encoding="utf-8", newline="\n") as dst:
            dst.write(src.read())
        summary["published"] = target
    say(f"report: {os.path.join(run_dir, 'report.md')}")
    return run_dir, summary
