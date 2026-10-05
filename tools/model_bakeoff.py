"""
R2.7 model bake-off: Gemini Flash-Lite vs Flash on the same messy simulated calls.

Each model runs the conversation harness live, on the R2 engine, with the same
seed, so both face the same callers (harness/sim_caller.py is rule-based and
seeded). The comparison scores what the plan asks for (R11, R2.7):

    correct outcome    the caller's goal met, zero-tolerance hits (Z1-Z7)
    validator drops    sentences the validators refused, per model turn, by rule
    fallbacks          model turns answered by the no-model path (slow or failed)
    reply latency      engine time on model turns, p50 / p95
    naturalness        a blind sheet of model-written replies for you to read
                       aloud and score 1-5; `score` folds the ratings back in

    python tools/model_bakeoff.py run                       # both models, 20 calls each
    python tools/model_bakeoff.py run --n 20 --seed 7 --publish
    python tools/model_bakeoff.py compare harness_runs/A harness_runs/B
    python tools/model_bakeoff.py score docs/test-reports/model-bakeoff-<id>-ratings.md

Needs GEMINI_API_KEY in .env (live calls). GEMINI_FALLBACK_MODEL is blanked
for the runs so a 503 retry stays on the model being tested.
"""

import argparse
import glob
import json
import os
import random
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_MODELS = ("gemini-3.5-flash-lite", "gemini-3.5-flash")
ZERO_TOLERANCE = ("Z1", "Z2", "Z3", "Z4", "Z5", "Z6", "Z7")
SAMPLES_PER_MODEL = 12
# A model within this many points of the best goal-met rate counts as just as correct.
OUTCOME_TIE_POINTS = 5.0


# ---------------------------------------------------------------- running

def run_model(model: str, *, suite: str, n: int, seed: int, intensity: Optional[int],
              concurrency: Optional[int]) -> str:
    """One live harness run on `model`; returns its run directory."""
    slug = re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")
    cmd = [sys.executable, "-m", "harness", "run", suite, "--live", "--engine", "r2",
           "--n", str(n), "--seed", str(seed), "--label", f"bakeoff-{slug}", "--quiet"]
    if intensity is not None:
        cmd += ["--intensity", str(intensity)]
    if concurrency:
        cmd += ["--concurrency", str(concurrency)]
    env = dict(os.environ, GEMINI_MODEL=model, GEMINI_FALLBACK_MODEL="", R2_ENGINE="true")
    print(f"[{model}] {' '.join(cmd[1:])}", flush=True)
    proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
    sys.stdout.write(proc.stdout[-2000:])
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-4000:])
        raise SystemExit(f"[{model}] harness run failed (exit {proc.returncode})")
    found = re.search(r"report:\s+(.+?)[\\/]report\.md", proc.stdout)
    if not found:
        raise SystemExit(f"[{model}] couldn't find the run directory in the harness output")
    return found.group(1).strip()


# ---------------------------------------------------------------- scoring

def load_run(run_dir: str) -> tuple[dict, list]:
    with open(os.path.join(run_dir, "summary.json"), encoding="utf-8") as fh:
        summary = json.load(fh)
    records = []
    for path in sorted(glob.glob(os.path.join(run_dir, "conversations", "*.json"))):
        with open(path, encoding="utf-8") as fh:
            records.append(json.load(fh))
    return summary, records


def model_of(run_dir: str, summary: dict) -> str:
    meta = summary.get("meta") or {}
    if meta.get("model"):
        return meta["model"]
    found = re.search(r"bakeoff-(.+)$", meta.get("run_id") or os.path.basename(os.path.normpath(run_dir)))
    return found.group(1) if found else os.path.basename(os.path.normpath(run_dir))


def percentile(values: list, q: float) -> Optional[float]:
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (k - lo), 1)


def model_turns(record: dict) -> list:
    """Caller turns the model was asked about (Tier-1)."""
    return [t for t in record.get("turns") or () if t.get("caller") and t.get("llm_called")]


def score_run(records: list) -> dict:
    goal = [r["metrics"].get("goal_met") for r in records if r["metrics"].get("goal_met") is not None]
    zero = Counter()
    for r in records:
        for metric, count in (r["metrics"].get("counts") or {}).items():
            if metric in ZERO_TOLERANCE:
                zero[metric] += count
    turns = [t for r in records for t in model_turns(r)]
    ok = [t for t in turns if t.get("llm_ok") is not False]
    traces = [t.get("trace") or {} for t in turns]
    drops = Counter(rule for tr in traces for rule in tr.get("dropped") or ())
    latency = [t["engine_ms"] for t in ok if isinstance(t.get("engine_ms"), (int, float))]
    errors = Counter(t.get("llm_error") or "failed" for t in turns if t.get("llm_ok") is False)
    return {
        "calls": len(records),
        "goal_met_pct": round(100 * sum(goal) / len(goal), 1) if goal else None,
        "zero_tolerance": dict(zero),
        "zero_total": sum(zero.values()),
        "model_turns": len(turns),
        "failed": sum(errors.values()),
        "errors": dict(errors),
        "fallback_pct": _pct(sum(1 for tr in traces if tr.get("fallback")), len(turns)),
        "turns_with_drop_pct": _pct(sum(1 for tr in traces if tr.get("dropped")), len(turns)),
        "drops_per_turn": round(sum(drops.values()) / len(turns), 2) if turns else None,
        "drop_rules": dict(drops.most_common()),
        "model_say_pct": _pct(sum(1 for tr in traces if tr.get("used_model_say")), len(turns)),
        "model_ask_pct": _pct(sum(1 for tr in traces if tr.get("used_model_ask")), len(turns)),
        "p50_ms": percentile(latency, 0.5),
        "p95_ms": percentile(latency, 0.95),
        "quota_exhausted": any((r.get("llm") or {}).get("quota_exhausted") for r in records),
    }


def _pct(part: int, whole: int) -> Optional[float]:
    return round(100 * part / whole, 1) if whole else None


def samples(records: list, k: int, rng: random.Random) -> list:
    """Model-written replies (the model's `say` was spoken), with the caller line before them."""
    pool = []
    for r in records:
        for t in model_turns(r):
            if (t.get("trace") or {}).get("used_model_say") and t.get("emma"):
                pool.append({"caller": t["caller"], "emma": t["emma"], "call": r["id"], "turn": t.get("n")})
    rng.shuffle(pool)
    return pool[:k]


def recommend(scores: dict) -> tuple[Optional[str], str]:
    """
    Correctness first: no zero-tolerance hits, and a goal-met rate within
    OUTCOME_TIE_POINTS of the best. Among those, the fewer validator drops and
    fallbacks, then the faster p50. Naturalness is your call from the sheet.
    """
    usable = {m: s for m, s in scores.items() if s["model_turns"] and not s["quota_exhausted"]}
    if not usable:
        return None, "No usable run: every model's run had no model turns or ran out of quota."
    safe = {m: s for m, s in usable.items() if s["zero_total"] == 0} or usable
    best_goal = max((s["goal_met_pct"] or 0) for s in safe.values())
    close = {m: s for m, s in safe.items() if (s["goal_met_pct"] or 0) >= best_goal - OUTCOME_TIE_POINTS}

    def key(item):
        s = item[1]
        return ((s["turns_with_drop_pct"] or 0) + (s["fallback_pct"] or 0), s["p50_ms"] or 1e9)

    model, s = min(close.items(), key=key)
    why = (f"{model}: goal met {s['goal_met_pct']}%, {s['zero_total']} zero-tolerance hits, "
           f"{s['turns_with_drop_pct']}% of model turns lost a sentence to the validators, "
           f"{s['fallback_pct']}% fell back, p50 {s['p50_ms']} ms.")
    return model, why


# ---------------------------------------------------------------- reports

ROWS = (
    ("Calls", "calls", ""),
    ("Goal met", "goal_met_pct", "%"),
    ("Zero-tolerance hits (Z1-Z7)", "zero_total", ""),
    ("Model turns", "model_turns", ""),
    ("Failed model calls", "failed", ""),
    ("Fell back to the no-model path", "fallback_pct", "%"),
    ("Turns with a validator drop", "turns_with_drop_pct", "%"),
    ("Validator drops per model turn", "drops_per_turn", ""),
    ("Model's own `say` spoken", "model_say_pct", "%"),
    ("Model's own `ask` spoken", "model_ask_pct", "%"),
    ("Reply latency p50 (engine)", "p50_ms", " ms"),
    ("Reply latency p95 (engine)", "p95_ms", " ms"),
)


def _cell(value, unit: str) -> str:
    return "n/a" if value is None else f"{value}{unit}"


def comparison_md(bake_id: str, runs: dict, scores: dict, choice: Optional[str], why: str,
                  sheet_name: Optional[str]) -> str:
    models = list(scores)
    out = [f"# Model bake-off {bake_id} (R2.7)", "",
           "Same seeded messy callers for each model, live Gemini, R2 engine "
           "(`tools/model_bakeoff.py`). Latency is the engine's time on model turns, "
           "without speech recognition or synthesis.", "",
           "| | " + " | ".join(f"`{m}`" for m in models) + " |",
           "|---|" + "---|" * len(models)]
    for label, key, unit in ROWS:
        out.append(f"| {label} | " + " | ".join(_cell(scores[m][key], unit) for m in models) + " |")
    out += ["", "## Validator drops by rule", ""]
    for m in models:
        rules = scores[m]["drop_rules"]
        out.append(f"- `{m}`: " + (", ".join(f"{k} x{v}" for k, v in rules.items()) or "none"))
    out += ["", "## Zero-tolerance hits", ""]
    for m in models:
        z = scores[m]["zero_tolerance"]
        out.append(f"- `{m}`: " + (", ".join(f"{k} x{v}" for k, v in sorted(z.items())) or "none"))
    if any(s["errors"] for s in scores.values()):
        out += ["", "## Model errors", ""]
        for m in models:
            out.append(f"- `{m}`: " + (", ".join(f"{k} x{v}" for k, v in scores[m]["errors"].items()) or "none"))
    out += ["", "## Recommendation", "",
            f"On correctness, validator drops and speed: **{choice or 'none'}**. {why}", "",
            "Naturalness decides between models that are close: read the blind sheet aloud, "
            "score each reply 1-5, then run "
            f"`python tools/model_bakeoff.py score {sheet_name or '<ratings sheet>'}`.", "",
            "## Runs", ""]
    for m in models:
        out.append(f"- `{m}`: `{_shown(runs[m])}`")
    return "\n".join(out) + "\n"


def ratings_md(bake_id: str, items: list) -> str:
    out = [f"# Naturalness ratings, bake-off {bake_id}", "",
           "Read each of Emma's replies aloud as if you were the caller. Score 1-5 in the last "
           "column (5 = a real receptionist would say exactly that, 1 = robotic or wrong). "
           "The model behind each reply is hidden until you score.", "",
           "| # | Caller | Emma | Score |", "|---|---|---|---|"]
    for i, item in enumerate(items, 1):
        caller = item["caller"].replace("|", "/")
        emma = item["emma"].replace("|", "/")
        out.append(f"| {i} | {caller} | {emma} |  |")
    return "\n".join(out) + "\n"


def build_sheet(per_model: dict, k: int, seed: int) -> tuple[list, dict]:
    rng = random.Random(seed)
    items = []
    for model, records in per_model.items():
        for s in samples(records, k, rng):
            items.append({**s, "model": model})
    rng.shuffle(items)
    key = {str(i): item["model"] for i, item in enumerate(items, 1)}
    return items, key


def compare(run_dirs: list, *, out_dir: Optional[str], publish: bool, seed: int,
            k: int = SAMPLES_PER_MODEL) -> str:
    runs, scores, per_model = {}, {}, {}
    for run_dir in run_dirs:
        summary, records = load_run(run_dir)
        model = model_of(run_dir, summary)
        runs[model], per_model[model], scores[model] = run_dir, records, score_run(records)
    choice, why = recommend(scores)
    bake_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    if publish:
        out_dir = os.path.join(ROOT, "docs", "test-reports")
    out_dir = out_dir or os.path.join(ROOT, "harness_runs", f"bakeoff-{bake_id}")
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, f"model-bakeoff-{bake_id}")
    items, key = build_sheet(per_model, k, seed)
    sheet = base + "-ratings.md"
    _write(sheet, ratings_md(bake_id, items))
    _write(base + "-key.json", json.dumps({"bake_id": bake_id, "key": key}, indent=1) + "\n")
    _write(base + ".json", json.dumps({"bake_id": bake_id, "runs": runs, "scores": scores,
                                       "recommendation": choice}, indent=1) + "\n")
    report = comparison_md(bake_id, runs, scores, choice, why, _shown(sheet))
    _write(base + ".md", report)
    print(report)
    print(f"report:  {base}.md\nratings: {sheet}")
    return base


def score(sheet: str) -> dict:
    """Mean naturalness per model from a filled ratings sheet and its key file."""
    key_path = re.sub(r"-ratings\.md$", "-key.json", sheet)
    with open(key_path, encoding="utf-8") as fh:
        key = json.load(fh)["key"]
    totals = {}
    with open(sheet, encoding="utf-8") as fh:
        for line in fh:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) == 4 and cells[0].isdigit() and re.fullmatch(r"[1-5](\.\d)?", cells[3]):
                totals.setdefault(key[cells[0]], []).append(float(cells[3]))
    result = {m: {"rated": len(v), "mean": round(sum(v) / len(v), 2)} for m, v in totals.items()}
    for m, r in sorted(result.items(), key=lambda kv: -kv[1]["mean"]):
        print(f"{m}: {r['mean']} / 5 over {r['rated']} replies")
    if not result:
        print("No scores found: fill the Score column with 1-5.")
    return result


def _shown(path: str) -> str:
    """A path relative to the project when it is inside it, else as given."""
    rel = os.path.relpath(os.path.abspath(path), ROOT)
    return path if rel.startswith("..") else rel.replace(os.sep, "/")


def _write(path: str, text: str):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run every model live, then compare")
    run.add_argument("--models", default=",".join(DEFAULT_MODELS))
    run.add_argument("--suite", default="sim", choices=("sim", "regression", "catalogue", "scenarios"))
    run.add_argument("--n", type=int, default=20)
    run.add_argument("--seed", type=int, default=7)
    run.add_argument("--intensity", type=int, default=2, help="sim disruptions per call (messy callers)")
    run.add_argument("--concurrency", type=int)
    run.add_argument("--publish", action="store_true", help="write the report to docs/test-reports/")
    cmp_ = sub.add_parser("compare", help="compare finished harness runs")
    cmp_.add_argument("runs", nargs="+")
    cmp_.add_argument("--seed", type=int, default=7)
    cmp_.add_argument("--out")
    cmp_.add_argument("--publish", action="store_true")
    sc = sub.add_parser("score", help="mean naturalness per model from a filled ratings sheet")
    sc.add_argument("sheet")
    args = p.parse_args(argv)

    if args.cmd == "run":
        models = [m.strip() for m in args.models.split(",") if m.strip()]
        dirs = [run_model(m, suite=args.suite, n=args.n, seed=args.seed, intensity=args.intensity,
                          concurrency=args.concurrency) for m in models]
        compare(dirs, out_dir=None, publish=args.publish, seed=args.seed)
    elif args.cmd == "compare":
        compare(args.runs, out_dir=args.out, publish=args.publish, seed=args.seed)
    else:
        score(args.sheet)


if __name__ == "__main__":
    main()
