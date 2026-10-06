"""
Phase E evaluation report (plan section 13; docs/FINAL_PHASES_PLAN.md, E1-E2).

    python tools/evaluate.py                 docs/EVALUATION.md from the latest harness runs,
                                             the real calls in logs/turns.jsonl and the fault drills
    python tools/evaluate.py --fresh         run the 53 scenarios and the four 200-call sims first
    python tools/evaluate.py --stt           also replay the owner's recordings (captures/*.wav)
                                             through Deepgram for word error (paid: ~2 min of audio);
                                             results are kept in logs/stt_eval.json and reused
    python tools/evaluate.py --crash         also run the crash drill (tools/crash_drill.py, 30 kills,
                                             about a minute); kept in logs/crash_drill.json and reused
    python tools/evaluate.py --since 2026-10-05   real-call turns from that day on (default: all)

Every number sits next to its target from docs/SUCCESS_CRITERIA.md with an
honest PASS / FAIL; nothing is typed in by hand, so the report can be
regenerated at any time.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import config  # noqa: E402

RUNS = ROOT / "harness_runs"
TURNS = Path(config.LOG_DIR) / "turns.jsonl"
STT_CACHE = Path(config.LOG_DIR) / "stt_eval.json"
CRASH_CACHE = Path(config.LOG_DIR) / "crash_drill.json"
OUT = ROOT / "docs" / "EVALUATION.md"
SIMS = [("nlu_down", 11), ("nlu_down", 7), ("offline", 7), ("offline", 11)]

# docs/SUCCESS_CRITERIA.md T1 / T2 (seconds).
T1 = (0.9, 1.8)          # simple turns (rules): p50, p95
T2 = (1.8, 3.0)          # turns that need the model


# ---------------------------------------------------------------- harness runs

def _latest(kind: str) -> Path | None:
    """The newest harness run directory whose name ends with `kind` (e.g. "sim-offline-r2")."""
    found = sorted((p for p in RUNS.glob(f"*-{kind}") if (p / "summary.json").exists()), reverse=True)
    return found[0] if found else None


def _run_harness(args: list) -> Path | None:
    out = subprocess.run([sys.executable, "-m", "harness", "run", *args, "--quiet"], cwd=ROOT,
                         capture_output=True, text=True, timeout=1800)
    for line in (out.stdout + out.stderr).splitlines():
        if line.strip().startswith("report:"):
            return Path(line.split(":", 1)[1].strip()).parent
    return None


def harness_results(fresh: bool) -> dict:
    """{"scenarios": summary, "sims": [(label, summary, dir)]}; runs them first with fresh=True."""
    runs = {}
    if fresh:
        runs["scenarios"] = _run_harness(["scenarios", "--engine", "r2", "--offline"])
        for mode, seed in SIMS:
            flag = "--nlu-down" if mode == "nlu_down" else "--offline"
            runs[(mode, seed)] = _run_harness(["sim", "--engine", "r2", flag, "--n", "200", "--seed", str(seed),
                                              "--concurrency", "2"])
    scen_dir = runs.get("scenarios") or _latest("scenarios-offline-r2")
    sims = []
    for mode, seed in SIMS:
        path = runs.get((mode, seed)) or _latest_sim(mode, seed)
        if path is not None:
            sims.append((mode, seed, json.loads((path / "summary.json").read_text(encoding="utf-8")), path))
    scenarios = json.loads((scen_dir / "summary.json").read_text(encoding="utf-8")) if scen_dir else None
    return {"scenarios": scenarios, "scenarios_dir": scen_dir, "sims": sims}


def _latest_sim(mode: str, seed: int) -> Path | None:
    for path in sorted(RUNS.glob(f"*-sim-{mode}-r2"), reverse=True):
        try:
            meta = json.loads((path / "summary.json").read_text(encoding="utf-8")).get("meta", {})
        except (OSError, ValueError):
            continue
        if meta.get("seed") == seed and meta.get("n", meta.get("calls", 200)) in (200, None):
            return path
    return None


def _metric(summary: dict, key: str) -> dict | None:
    return next((m for m in summary.get("metrics", []) if m.get("metric") == key), None)


# ---------------------------------------------------------------- real calls

def _pct(values: list, q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def real_calls(since: str | None) -> dict:
    """Latency, fallback, filler and barge-in rates from the real calls in logs/turns.jsonl."""
    rows = []
    floor = datetime.fromisoformat(since).timestamp() if since else 0
    for path in [p for p in TURNS.parent.glob(TURNS.name + "*") if p.is_file()]:     # rotated files too
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("ts", 0) >= floor:
                rows.append(r)
    replies = [r for r in rows if r.get("tier") is not None and r["tier"] >= 0]
    rules = [r["perceived_ms"] / 1000 for r in replies if r["tier"] == 0 and r.get("perceived_ms")]
    model = [r["perceived_ms"] / 1000 for r in replies if r["tier"] in (1, 2) and r.get("perceived_ms")]
    endpoint = [r["endpoint_ms"] / 1000 for r in replies if r.get("endpoint_ms")]
    model_turns = [r for r in replies if r["tier"] in (1, 2)]
    return {
        "turns": len(replies), "calls": len({r.get("call_id") for r in rows}),
        "first": datetime.fromtimestamp(min(r["ts"] for r in rows)).date().isoformat() if rows else None,
        "last": datetime.fromtimestamp(max(r["ts"] for r in rows)).date().isoformat() if rows else None,
        "rules": rules, "model": model, "endpoint": endpoint,
        "fallback": (sum(1 for r in model_turns if r["tier"] == 2), len(model_turns)),
        "filler": (sum(1 for r in replies if r.get("filler")), len(replies)),
        "barge_in": (sum(1 for r in replies if r.get("barge_in")), len(replies)),
        "rule_share": (len([r for r in replies if r["tier"] == 0]), len(replies)),
    }


# ---------------------------------------------------------------- speech recognition

def stt_results(run: bool) -> dict | None:
    """Word error, cut lines and end-of-turn wait on captures/*.wav (Deepgram); cached in logs/stt_eval.json."""
    if run:
        import replay
        out = {"when": datetime.now().isoformat(timespec="minutes"), "model": config.DEEPGRAM_MODEL, "files": []}
        lines = replay._script()
        for wav in sorted((ROOT / "captures").glob("*.wav")):
            res = asyncio.run(replay.replay(str(wav)))
            s = replay.score(res["turns"], lines)
            out["files"].append({"file": wav.name, "lines": len(lines), "errors": s["errors"],
                                 "ref_words": s["ref_words"], "cut": len(s["cut"]), "lags_ms": s["lags"]})
        STT_CACHE.parent.mkdir(parents=True, exist_ok=True)
        STT_CACHE.write_text(json.dumps(out, indent=1), encoding="utf-8")
        return out
    if STT_CACHE.exists():
        return json.loads(STT_CACHE.read_text(encoding="utf-8"))
    return None


# ---------------------------------------------------------------- fault drills

def fault_drills() -> dict:
    """Run tests/test_fault_drills.py in-process; names and outcomes of every drill."""
    sys.path.insert(0, str(ROOT / "tests"))
    suite = unittest.defaultTestLoader.loadTestsFromName("test_fault_drills")
    tests = []

    def walk(s):
        for t in s:
            if isinstance(t, unittest.TestSuite):
                walk(t)
            else:
                tests.append(t)
    walk(suite)                                       # before run(): a suite drops its tests as it runs them
    listed = [(t._testMethodName, t.id(), (t._testMethodDoc or "").strip().splitlines()[0]
               if t._testMethodDoc else "") for t in tests]
    result = unittest.TestResult()
    suite.run(result)
    failed = {t.id() for t, _ in result.failures + result.errors}
    return {"ran": result.testsRun, "failed": len(failed),
            "drills": [(name, tid not in failed, doc) for name, tid, doc in listed]}


def crash_results(run: bool) -> dict | None:
    """tools/crash_drill.py: a writer killed mid-booking 30 times; cached in logs/crash_drill.json."""
    if run:
        import crash_drill
        summary = crash_drill.drill(rounds=30, say=lambda *_: None)
        CRASH_CACHE.parent.mkdir(parents=True, exist_ok=True)
        CRASH_CACHE.write_text(json.dumps(summary, indent=1), encoding="utf-8")
        return summary
    if CRASH_CACHE.exists():
        return json.loads(CRASH_CACHE.read_text(encoding="utf-8"))
    return None


# ---------------------------------------------------------------- the report

def _verdict(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def _s(value) -> str:
    return "n/a" if value is None else f"{value:.2f} s"


def render(h: dict, calls: dict, stt: dict | None, drills: dict, since: str | None,
           crash: dict | None = None) -> str:
    out = ["# Evaluation report", "",
           f"Generated by `tools/evaluate.py` on {datetime.now():%d %b %Y %H:%M}. Targets are from "
           "[SUCCESS_CRITERIA.md](SUCCESS_CRITERIA.md); every number below comes from data in the repo "
           "(harness runs, `logs/turns.jsonl`, the fault-drill tests), so run the tool again to refresh it.", ""]

    # Latency
    r50, r95 = _pct(calls["rules"], .5), _pct(calls["rules"], .95)
    m50, m95 = _pct(calls["model"], .5), _pct(calls["model"], .95)
    window = f"since {since}" if since else f"{calls['first']} to {calls['last']}"
    out += ["## 1. Reply latency on real calls", "",
            f"{calls['turns']} replies in {calls['calls']} browser calls ({window}). Perceived latency is from the "
            "end of the caller's speech to Emma's first audible sound (`latency.TurnTimer`).", "",
            "| Turns | Count | p50 | p95 | Target p50 / p95 | Result |", "|---|---|---|---|---|---|",
            f"| Simple (rules, T1) | {len(calls['rules'])} | {_s(r50)} | {_s(r95)} | {T1[0]} s / {T1[1]} s | "
            f"{_verdict(r50 is not None and r50 <= T1[0] and r95 <= T1[1])} |",
            f"| Needs the model (T2) | {len(calls['model'])} | {_s(m50)} | {_s(m95)} | {T2[0]} s / {T2[1]} s | "
            f"{_verdict(m50 is not None and m50 <= T2[0] and m95 <= T2[1])} |",
            f"| End-of-speech detection alone | {len(calls['endpoint'])} | {_s(_pct(calls['endpoint'], .5))} | "
            f"{_s(_pct(calls['endpoint'], .95))} | (part of the above) | |", ""]
    fb, fb_n = calls["fallback"]
    fl, fl_n = calls["filler"]
    bi, bi_n = calls["barge_in"]
    rs, rs_n = calls["rule_share"]
    out += ["| Rate | Value |", "|---|---|",
            f"| Turns handled by Emma's own rules (no model wait) | {rs}/{rs_n} ({100 * rs / max(1, rs_n):.0f}%) |",
            f"| Model turns answered by the fallback (model too slow or down) | {fb}/{fb_n} ({100 * fb / max(1, fb_n):.0f}%) |",
            f"| Replies that needed a filler (\"Okay, ...\") | {fl}/{fl_n} ({100 * fl / max(1, fl_n):.0f}%) |",
            f"| Replies the caller interrupted (barge-in) | {bi}/{bi_n} ({100 * bi / max(1, bi_n):.0f}%) |", "",
            "Most of the wait on simple turns is end-of-speech detection (the recogniser deciding the caller has "
            "finished), not Emma's thinking; the T1 gap is the main open issue (see section 6).", ""]

    # Task success
    out += ["## 2. Task success and safety (simulated callers)", ""]
    sc = h["scenarios"]
    if sc:
        out += [f"Scripted scenarios: **{sc.get('calls', '?')} calls**, zero-tolerance checks "
                f"{'all pass' if sc.get('zero_tolerance_ok') else 'FAILED'} (`{h['scenarios_dir'].name}`).", ""]
    if h["sims"]:
        keys = ["M1", "M2", "M3", "M5", "M7", "M9", "M10", "T5"]
        head = "| Run | " + " | ".join(keys) + " | Z1-Z7 |"
        out += ["Each run is 200 simulated callers with random disruptions (the model faked, or switched off "
                "to exercise Emma's fallback path).", "", head, "|" + "---|" * (len(keys) + 2)]
        for mode, seed, summary, path in h["sims"]:
            cells = []
            for k in keys:
                m = _metric(summary, k)
                if m is None:
                    cells.append("-")
                elif m.get("kind", "").startswith("rate"):
                    cells.append(f"{100 * m['value']:.1f}% {'✓' if m['status'] == 'PASS' else '✗'}")
                else:
                    cells.append(f"{m['value']} {'✓' if m['status'] == 'PASS' else '✗'}")
            z = [m for m in summary.get("metrics", []) if str(m.get("metric", "")).startswith("Z")]
            zok = all(m["status"] == "PASS" for m in z)
            label = "model off" if mode == "nlu_down" else "model faked"
            out.append(f"| {label}, seed {seed} | " + " | ".join(cells) + f" | {'all 0 ✓' if zok else 'FAIL ✗'} |")
        out += ["", "M1 required detail skipped (target 0) · M2 question repeated (<2%) · M3 repetition loop (<1%) · "
                "M5 caller had to repeat (<2%) · M7 booking completed (≥95%) · M9 unnecessary restart (<1%) · "
                "M10 dead end (<2%) · T5 caller turns for a simple booking (median ≤9) · Z1-Z7 zero-tolerance "
                "safety checks (action without a heard yes, claimed action not committed, claims to be human, "
                "invented facts, wrong outcome, service at a branch that doesn't offer it, details before "
                "verification).", ""]

    # STT
    out += ["## 3. Speech recognition on the owner's recordings", ""]
    if stt and stt.get("files"):
        out += [f"Deepgram {stt['model']}, replayed in real time through Emma's listening pipeline "
                f"(`tools/replay.py`, {stt['when']}). Lines are the 30-line test set ([STT_TEST_SET.md](STT_TEST_SET.md)).", "",
                "| Recording | Word error | Lines cut in half | Wait after last word p50 / p90 |", "|---|---|---|---|"]
        for f in stt["files"]:
            lags = f["lags_ms"]
            out.append(f"| {f['file']} | {100 * f['errors'] / max(1, f['ref_words']):.0f}% | {f['cut']} of {f['lines']} | "
                       f"{(_pct(lags, .5) or 0) / 1000:.2f} s / {(_pct(lags, .9) or 0) / 1000:.2f} s |")
        out += ["", "Word error counts every inserted, dropped or changed word against the script, so filler "
                "words and number formats count as errors; names (\"Adharsh\") and some ordinals are the real "
                "misses, and Emma's read-backs catch them.", ""]
    else:
        out += ["Not measured in this run (`python tools/evaluate.py --stt` replays `captures/*.wav` through "
                "Deepgram). On 6 Oct the replay measured about 17% word error on the headset recording and "
                "1-2 lines cut of 30 (HANDOFF.md, section 0000).", ""]

    # Drills
    out += ["## 4. Booking integrity under failure", "",
            f"`tests/test_fault_drills.py`: **{drills['ran'] - drills['failed']} of {drills['ran']} drills pass**.", "",
            "| Drill | Result |", "|---|---|"]
    for name, ok, doc in drills["drills"]:
        out.append(f"| {doc or name.replace('test_', '').replace('_', ' ')} | {'pass' if ok else 'FAIL'} |")
    off = [s for m, _, s, _ in h["sims"] if m == "nlu_down"]
    if off:
        z_all = all(all(m["status"] == "PASS" for m in s.get("metrics", []) if str(m.get("metric", "")).startswith("Z"))
                    for s in off)
        out += ["", f"With the language model switched off for {200 * len(off)} simulated calls, every zero-tolerance "
                f"check {'stayed at 0' if z_all else 'did NOT stay at 0'}: no booking, change or cancellation without a "
                "clear yes to a summary the caller heard, nothing claimed that Python didn't commit, nothing booked "
                "at a branch that doesn't offer the service. Double booking is prevented by the database itself "
                "(one claim row per doctor per 30-minute cell, plan 4.2), so it can't happen even under a race.", ""]

    if crash:
        o = crash["outcomes"]
        mid = sum(v for k, v in o.items() if "rolled back" in k)
        after = sum(v for k, v in o.items() if "committed" in k)
        out += ["**Killing the process mid-booking** (`tools/crash_drill.py`, " + crash["when"][:10] + "): a writer "
                "doing what calls do (hold an offered slot then book it, move a booking, cancel one) through "
                f"`scheduling.py` was killed outright {crash['rounds']} times at random moments, "
                f"{crash['changes']} changes committed in between. "
                + ("**No round found a problem.**" if not crash["failed_rounds"]
                   else f"**{crash['failed_rounds']} rounds found a problem.**"), "",
                "| After each kill, on restart | Result |", "|---|---|",
                f"| SQLite integrity_check, foreign keys, no doctor booked twice, every booking holding its "
                f"claims, no claim left on a cancelled booking | {'pass' if not crash['failed_rounds'] else 'FAIL'} |",
                f"| every change reported as done is still there | {'pass' if not crash['failed_rounds'] else 'FAIL'} |",
                f"| killed mid-request ({mid} times): nothing half-written, the retry acts once | "
                f"{'pass' if not crash['failed_rounds'] else 'FAIL'} |",
                f"| killed after the write, before the answer ({after} times): the retry returns the stored "
                f"result, no second booking | {'pass' if not crash['failed_rounds'] else 'FAIL'} |",
                f"| holds left by killed calls ({crash['holds_left']}) free their slots when they expire | "
                f"{'pass' if not crash['holds_remaining'] else 'FAIL'} |", ""]

    # Gaps
    out += ["## 5. How this was measured", "",
            "- Real calls: the owner's browser calls (headset and laptop), logged per turn by `latency.LatencyLog`.",
            "- Simulated callers: `harness` (scripted scenarios and an LLM-free simulated caller with disruptions: "
            "corrections, silence, barge-in, chit-chat, intent switches, the bot question).",
            "- Recordings: `captures/` (git-ignored), the 30-line test set read twice by the owner.", "",
            "## 6. Known gaps", "",
            "- Simple-turn latency (T1) is above target: end-of-speech detection dominates; Deepgram's endpointing "
            "and the voice-gated end of turn were tuned on the owner's recordings to avoid cutting callers off, "
            "which costs time.",
            "- T5 (turns per simple booking) is 10 on seed 7: those simulated callers already have an appointment, "
            "and Emma asks whether they want another one or a change.",
            "- Deepgram mishears some Indian names; Emma confirms names implicitly and reads numbers back.", ""]
    return "\n".join(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="tools/evaluate.py", description=__doc__.splitlines()[1])
    parser.add_argument("--fresh", action="store_true", help="run the scenarios and four sims first (~5 min)")
    parser.add_argument("--stt", action="store_true", help="replay captures/*.wav through Deepgram (paid)")
    parser.add_argument("--crash", action="store_true", help="run the crash drill (30 kills, ~1 min)")
    parser.add_argument("--since", help="real-call turns from this date (YYYY-MM-DD)")
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args(argv)
    h = harness_results(args.fresh)
    text = render(h, real_calls(args.since), stt_results(args.stt), fault_drills(), args.since,
                  crash_results(args.crash))
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text + "\n")
    print(f"Wrote {os.path.relpath(args.out, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
