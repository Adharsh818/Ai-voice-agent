"""
Talk to Emma one caller turn at a time, from separate commands.

This is how an AI agent (or a person at a terminal) plays a caller: every
command is its own process, and the conversation's state lives on disk in
harness_runs/converse/<id>/ (git-ignored): the pickled engine session, the
conversation's own seeded clinic database (emma.db) and the transcript. The
clinic clock is frozen at the conversation's moment again on every command,
so "tomorrow" means the same day all the way through.

    python tools/converse.py new [--goal book] [--card existing_appointment] [--live | --offline | --nlu-down]
                                 [--r2 | --legacy] [--now ISO] [--seed N]
        prints the conversation id, Emma's greeting and, with --card (or a
        cancel / reschedule / check goal), a private caller card: a real seeded
        appointment (patient, phone, date and time, service, branch) to cancel,
        move or ask about
    python tools/converse.py say <id> "Hi, I'd like to book a cleaning."
        prints only Emma's reply, then "[call ended]" if she ended the call
    python tools/converse.py silence <id>
        the caller says nothing (the call session's silence ladder answers)
    python tools/converse.py end <id> [--gave-up]
        prints the transcript, the database outcome and the automatic metrics
        as JSON, and saves the transcript (harness_runs/converse/<id>/transcript.json)
    python tools/converse.py show <id>      the transcript so far, readable
    python tools/converse.py list           conversations on disk

Live (real Gemini with the .env key) is the default; --offline uses the
harness's fake NLU and --nlu-down no model at all, and neither touches the
network. Model failures (quota, timeouts) go to stderr, so stdout is only
ever Emma's words.

The engine is whatever config.R2_ENGINE picks (off: the 12-step machine);
--r2 or --legacy at `new` pins one for the whole conversation. The pickled
session carries it from command to command, since ai_engine routes each turn
by the session's type.
"""

import argparse
import asyncio
import json
import logging
import os
import pickle
import random
import secrets
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness import engine_adapter, report, runner  # noqa: E402  (needs the repo root on sys.path)
from harness import world as world_mod  # noqa: E402

CONVERSE_DIR = ROOT / "harness_runs" / "converse"
GOALS = ("book", "cancel", "reschedule", "check", "questions", "emergency")
EXPECTED = {"book": "booked", "cancel": "cancelled", "reschedule": "rescheduled", "check": "none",
            "questions": "none", "emergency": "booked_or_task"}


# ---------------------------------------------------------------- state on disk


def _dir(conv_id: str) -> Path:
    return CONVERSE_DIR / conv_id


def _load(conv_id: str) -> tuple:
    d = _dir(conv_id)
    path = d / "state.json"
    if not path.exists():
        raise SystemExit(f"no conversation {conv_id!r} (python tools/converse.py list)")
    with open(path, encoding="utf-8") as fh:
        return d, json.load(fh)


def _save(d: Path, state: dict, session=None):
    with open(d / "state.json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1, default=str)
    if session is not None:
        with open(d / "session.pkl", "wb") as fh:
            pickle.dump(session, fh)


def _session(d: Path):
    with open(d / "session.pkl", "rb") as fh:
        return pickle.load(fh)


def _new_id() -> str:
    while True:
        conv_id = f"{datetime.now():%m%d-%H%M%S}-{secrets.token_hex(2)}"
        if not _dir(conv_id).exists():
            return conv_id


def _warn_llm(turn: dict):
    if turn.get("llm_ok") is False:
        print(f"[model call failed: {turn.get('llm_error')}; Emma answered without it]", file=sys.stderr)
    if turn.get("engine_error"):
        print(f"[engine error: {turn['engine_error']}]", file=sys.stderr)


# ---------------------------------------------------------------- commands


def cmd_new(args) -> int:
    mode = "offline" if args.offline else ("nlu_down" if args.nlu_down else "live")
    engine = "r2" if args.r2 else ("legacy" if args.legacy else None)
    conv_id = _new_id()
    d = _dir(conv_id)
    d.mkdir(parents=True)
    now = world_mod.parse_now(args.now)
    wants_card = bool(args.card) or args.goal in ("cancel", "reschedule", "check")
    with world_mod.World(now, path=str(d / "emma.db")) as w:
        catalog = w.catalog()
        card = w.pick_card(rng=random.Random(args.seed if args.seed is not None else conv_id)) if wants_card else None
        baseline = w.snapshot()
        adapter = engine_adapter.EngineAdapter(mode, call_id=conv_id, engine=engine)
        random.seed(conv_id)
        with adapter.installed():
            session = adapter.new_session()
            conv = runner.Conversation(adapter, session)
            turn = asyncio.run(conv.greet())
    state = {"id": conv_id, "mode": mode, "engine": engine_adapter.engine_label(session),
             "now": now.isoformat(), "goal": args.goal, "card": card,
             "created": datetime.now().isoformat(timespec="seconds"), "baseline": baseline, "catalog": catalog,
             "turns": conv.turns, "silence_step": 0, "ended": False}
    _save(d, state, session)
    print(f"conversation: {conv_id}  ({state['engine']} engine, {mode})")
    print(f"Emma: {turn['emma']}")
    if card:
        print()
        print("Caller card (private: these are your details as the caller; Emma can't see this):")
        print(f"  patient:      {card['patient']}")
        print(f"  phone:        {card['phone_spoken']}")
        print(f"  appointment:  {card['date_spoken']} at {card['time_spoken']}  ({card['date']} {card['time']})")
        print(f"  service:      {card['service']}")
        print(f"  branch:       {card['branch']} (with {card['doctor']})")
    if mode == "live":
        import config
        if not config.GEMINI_API_KEY:
            print("[no GEMINI_API_KEY in .env: live turns will run without the model]", file=sys.stderr)
    return 0


def _turn(args, text: str) -> int:
    d, state = _load(args.id)
    if state.get("ended"):
        print(f"[conversation {args.id} has already ended; start a new one]", file=sys.stderr)
        return 1
    if state["turns"] and state["turns"][-1].get("closed"):
        print("[call ended]")
        return 0
    with world_mod.World(state["now"], path=str(d / "emma.db"), seed=False):
        session = _session(d)
        adapter = engine_adapter.EngineAdapter(state["mode"], call_id=state["id"])
        adapter.engine_used = engine_adapter.engine_label(session)
        with adapter.installed():
            conv = runner.Conversation(adapter, session, turns=state["turns"], silence_step=state["silence_step"],
                                       rng=random.Random(f"{state['id']}:{len(state['turns'])}"))
            turn = asyncio.run(conv.say(text, by="agent"))
    state["turns"] = conv.turns
    state["silence_step"] = conv.silence_step
    _save(d, state, session)
    _warn_llm(turn)
    print(turn["emma"])
    if turn.get("closed"):
        print("[call ended]")
    return 0


def cmd_say(args) -> int:
    text = " ".join(args.words).strip()
    if not text:
        print("say needs the caller's words (use `silence` for none)", file=sys.stderr)
        return 2
    return _turn(args, text)


def cmd_silence(args) -> int:
    return _turn(args, "")


def _record(d: Path, state: dict, ended_by: str) -> dict:
    with world_mod.World(state["now"], path=str(d / "emma.db"), seed=False):
        outcome = engine_adapter.EngineAdapter.outcome(state["baseline"])
    goal = state.get("goal")
    return runner.build_record(
        conv_id=state["id"], suite="converse", mode=state["mode"], now=world_mod.parse_now(state["now"]),
        turns=state["turns"], outcome=outcome, catalog=state["catalog"], goal=goal,
        expected_outcome=EXPECTED.get(goal), ended_by=ended_by, card=state.get("card"),
        extra={"engine_name": state.get("engine")},
    )


def _ended_by(state: dict, gave_up: bool) -> str:
    if state["turns"] and state["turns"][-1].get("closed"):
        return "silence" if state["turns"][-1].get("silence_step") else "emma_closed"
    return "caller_gave_up" if gave_up else "caller_bye"


def cmd_end(args) -> int:
    d, state = _load(args.id)
    record = _record(d, state, _ended_by(state, args.gave_up))
    path = d / "transcript.json"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=1, default=str)
    state["ended"] = True
    _save(d, state)
    m = record["metrics"]
    out = {
        "id": state["id"],
        "mode": state["mode"],
        "engine": state.get("engine"),
        "goal": state.get("goal"),
        "card": state.get("card"),
        "transcript": [_compact(t) for t in state["turns"]],
        "outcome": {"summary": runner.outcome_text(record["outcome"]), **record["outcome"]},
        "metrics": {
            "goal_met": m.get("goal_met"),
            "counts": m["counts"],
            "findings": [{k: f[k] for k in ("metric", "turn", "detail", "severity", "llm_degraded")}
                         for f in m["findings"]],
            "llm": record["llm"],
        },
        "transcript_path": str(path),
    }
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return 0


def _compact(turn: dict) -> dict:
    out = {"n": turn["n"], "caller": turn.get("caller") or "", "emma": turn.get("emma") or ""}
    if turn.get("silence_step"):
        out["silence"] = True
    if turn.get("action"):
        out["action"] = turn["action"]
    if turn.get("llm_error"):
        out["llm_error"] = turn["llm_error"]
    return out


def cmd_show(args) -> int:
    d, state = _load(args.id)
    record = _record(d, state, _ended_by(state, False))
    print(report.transcript_text(record))
    return 0


def cmd_list(args) -> int:
    if not CONVERSE_DIR.exists():
        return 0
    for d in sorted(CONVERSE_DIR.iterdir()):
        path = d / "state.json"
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
        status = "ended" if state.get("ended") else ("closed" if state["turns"] and state["turns"][-1].get("closed")
                                                    else "open")
        print(f"{state['id']}  {state['mode']:<7} {state.get('engine') or '-':<6} {state.get('goal') or '-':<10} {status:<6} "
              f"{max(0, len(state['turns']) - 1)} caller turns")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="tools/converse.py", description="Play a caller against Emma, turn by turn.")
    sub = parser.add_subparsers(dest="command", required=True)

    new = sub.add_parser("new", help="start a conversation")
    new.add_argument("--goal", choices=GOALS, help="what the caller wants (scores the outcome at `end`)")
    new.add_argument("--card", choices=["existing_appointment"],
                     help="print a private card with a real seeded appointment to cancel, move or check")
    where = new.add_mutually_exclusive_group()
    where.add_argument("--live", action="store_true", help="real Gemini with the .env key (the default)")
    where.add_argument("--offline", action="store_true", help="the harness's fake NLU, no network")
    where.add_argument("--nlu-down", action="store_true", help="no model at all: Emma's fallback path, no network")
    which = new.add_mutually_exclusive_group()
    which.add_argument("--r2", action="store_true", help="the R2 engine, whatever config.R2_ENGINE says")
    which.add_argument("--legacy", action="store_true", help="the 12-step engine, whatever config.R2_ENGINE says")
    new.add_argument("--now", help="clinic clock, ISO local time (default 2026-10-01T10:00)")
    new.add_argument("--seed", type=int, help="choose the card deterministically")
    new.set_defaults(fn=cmd_new)

    say = sub.add_parser("say", help="the caller speaks; prints Emma's reply")
    say.add_argument("id")
    say.add_argument("words", nargs="+")
    say.set_defaults(fn=cmd_say)

    sil = sub.add_parser("silence", help="the caller says nothing")
    sil.add_argument("id")
    sil.set_defaults(fn=cmd_silence)

    end = sub.add_parser("end", help="finish: transcript, outcome and metrics as JSON")
    end.add_argument("id")
    end.add_argument("--gave-up", action="store_true", help="the caller hung up frustrated (scores a dead end)")
    end.set_defaults(fn=cmd_end)

    show = sub.add_parser("show", help="the transcript so far")
    show.add_argument("id")
    show.set_defaults(fn=cmd_show)

    lst = sub.add_parser("list", help="conversations on disk")
    lst.set_defaults(fn=cmd_list)

    args = parser.parse_args(argv)
    runner.quiet_logging(logging.ERROR)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")    # Windows consoles default to cp1252
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
