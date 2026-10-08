"""
Turning a run's conversation records into the numbers that decide the fix order.

    summary.json    metric table vs the targets in docs/SUCCESS_CRITERIA.md
                    (each with its stated base), scenario results, the failure
                    catalogue, outcomes, disruptions, latency, model health
    report.md       the same for people: what failed, how often, an example, the likely cause
    transcripts.md  every conversation as a readable transcript with its findings

The failure catalogue groups findings by signature (metric plus a masked
Emma line or reason), so one root cause is one row however many calls hit
it. Likely causes come from KNOWN_CAUSES, the bugs already traced in
docs/HANDOFF.md section 5; anything unmatched is left for triage.

Calls whose model requests failed (llm_degraded, live runs only) are left out
of every metric and listed separately, so a quota problem never reads as a
dialogue problem. Below 200 calls a rate is shown as a count over its base and
marked indicative, as SUCCESS_CRITERIA section 2 asks.
"""

import json
import os
import re
from collections import Counter, defaultdict
from statistics import mean, median
from typing import Optional

from harness import metrics

MIN_CALLS_FOR_RATES = 200
EXAMPLES_PER_ROW = 3

# (pattern over "signature + example line", likely cause, fix owner). First match wins.
KNOWN_CAUSES = [
    # Masked signatures have no punctuation, so these two match the signature, not an example line.
    (r"no problem at all just give us a call",
     "A negative word at the greeting ('cancel', 'can't', 'no') is taken as 'not booking' and the call closes",
     "R2 engine: intent at the greeting"),
    (r"sure anything else i can help with",
     "After a booking, step 11 ignores any new request (reschedule, cancel, a question)",
     "R2 engine: intent switching"),
    (r"only book our nagarbhavi|that'?d be at our nagarbhavi|nagarbhavi branch(, is that okay| okay)",
     "Every booking goes to DEFAULT_BRANCH and the step 6 refusal has no exit (HANDOFF 5, audit problem 2)",
     "R2 engine: branch-aware booking"),
    (r"what should i change",
     "The recap 'no' only takes field corrections: no intent switch to cancel (HANDOFF 5)",
     "R2 engine: intent switching"),
    (r"what other time would work|that time'?s full|nothing close to it|that one'?s taken",
     "No slot for the service at DEFAULT_BRANCH and step 10 has no exit (braces at Nagarbhavi, HANDOFF 5)",
     "R2 engine: branch-aware slot search"),
    (r"deflection|doctor can go through|at your visit",
     "ESCALATION_LINE is spoken whenever the model's answer is missing or the facts don't cover it (HANDOFF 5)",
     "R2 engine + knowledge base (capability, general dental answers)"),
    (r"would you like to book (a visit|one)|i can help you set up a visit",
     "Every answer re-asks the pending question, so the same steer repeats (criterion 3)",
     "R2 engine: steer-back policy and loop breaker"),
    (r"missed a digit",
     "The phone step takes any non-number (a refusal, a question) as a misheard number",
     "R2 engine: phone goal"),
    (r"which treatment is it for",
     "An unknown or unmatched service gets the fixed treatment list again",
     "R2 engine + knowledge base (unknown services)"),
    (r"specify the date again|didn'?t catch the date|choose a future date|between monday and saturday",
     "The date step can't use the answer (non-answer, fragment, time-only, Sunday)",
     "R2 engine: date goal / dateparse"),
    (r"choose another time|didn'?t catch the time|operates from",
     "The time step can't use the answer",
     "R2 engine: time goal"),
    (r"full name|did i get that right|spelling",
     "The name step loops (a non-name answer, or a correction it can't take)",
     "R2 engine: name goal / R3 name capture"),
    (r"z5 asked to cancel|z5 asked to reschedule|z5 cancelled|m7 .*cancel",
     "No cancel / reschedule workflow in the 12-step engine",
     "R2 engine: MANAGE workflow"),
    (r"^z1 |^m1 booking: no summary", "An action was committed without a clear yes to a summary the caller heard "
     "(an offered slot booked as soon as it's picked; a yes to 'shall I go ahead' after a cancel)",
     "R2 engine: commit guard"),
    (r"sorry, was that .* or ", "Picking an offered slot isn't understood unless it's said as '9 am' (step 10 ignores the NLU)",
     "R2 engine: offer choice"),
    (r"doubled sentence", "The same sentence twice in one reply (a fact answer plus the step's own line)",
     "R2 engine: reply composer"),
    (r"^z2 ", "Emma claimed an action Python didn't commit", "R2 engine: validators"),
    (r"greeting repeated|fell back to the start", "The dialogue restarted", "R2 engine"),
    (r"^crash", "The engine raised an exception", "engine"),
]


def _cause(text: str) -> tuple:
    low = text.lower()
    for pattern, cause, owner in KNOWN_CAUSES:
        if re.search(pattern, low):
            return cause, owner
    return "", "triage"


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _percentile(values: list, q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round(q * (len(ordered) - 1)))))
    return round(ordered[k], 1)


def _counted(record: dict, metric: Optional[str] = None, severity: str = "fail") -> list:
    return [f for f in record["metrics"]["findings"]
            if not f["llm_degraded"] and f["severity"] == severity and (metric is None or f["metric"] == metric)]


# ---------------------------------------------------------------- the summary


def summarize(records: list, meta: dict) -> dict:
    ok = [r for r in records if not r.get("harness_error")]
    errors = [{"id": r["id"], "error": r["harness_error"]} for r in records if r.get("harness_error")]
    degraded = [r for r in ok if r["metrics"]["llm_degraded_call"]]
    clean = [r for r in ok if not r["metrics"]["llm_degraded_call"]]
    n = len(clean)
    small = n < MIN_CALLS_FOR_RATES

    rows = []
    for target in metrics.TARGETS:
        rows.append(_metric_row(target, clean, small))

    summary = {
        "meta": meta,
        "calls": len(ok),
        "scored_calls": n,
        "indicative_only": small,
        "harness_errors": errors,
        "degraded_calls": [r["id"] for r in degraded],
        "metrics": rows,
        "zero_tolerance_ok": all(r["status"] == "PASS" for r in rows if r["metric"] in metrics.ZERO_TOLERANCE),
        "catalogue": _catalogue(clean),
        "outcomes": _outcomes(clean),
        "latency": _latency(ok),
        "llm": _llm(ok, meta),
        "turns": _turns(clean),
    }
    scen = [r for r in ok if "status" in r]
    if scen:
        summary["scenarios"] = [{
            "id": r["id"], "title": r.get("title"), "group": r.get("group"), "status": r["status"],
            "passed": r["passed"], "bug": r.get("bug"), "llm_degraded": r["metrics"]["llm_degraded_call"],
            "failed_checks": [_check_text(c) for c in r["checks"] if not c["ok"]],
        } for r in scen]
        summary["scenario_counts"] = dict(Counter(r["status"] for r in scen))
    if any(r.get("suite") == "sim" for r in ok):
        summary["disruptions"] = _disruptions(clean)
    return summary


def _metric_row(target: dict, clean: list, small: bool) -> dict:
    m = target["metric"]
    row = {"metric": m, "name": target["name"], "base_name": target["base"], "target": target["target"],
           "kind": target["kind"]}
    if m in ("M1", "M2", "M4", "M5"):
        base_key = {"M1": "committed_actions", "M2": "emma_questions", "M4": "emma_replies", "M5": "caller_turns"}[m]
        count = sum(len(_counted(r, m)) for r in clean)
        base = sum(r["metrics"]["bases"][base_key] for r in clean)
    elif m == "M7":
        applicable = [r for r in clean if r["metrics"].get("m7_applicable")]
        base = len(applicable)
        count = sum(1 for r in applicable if r["metrics"].get("m7_success"))
    elif m == "T5":
        values = [r["metrics"]["t5_caller_turns"] for r in clean if r["metrics"].get("t5_caller_turns") is not None]
        base = len(values)
        count = median(values) if values else None
    else:
        count = sum(1 for r in clean if _counted(r, m))
        base = len(clean)
    row.update(count=count, base=base)
    candidates = sum(1 for r in clean if _counted(r, m, severity="candidate"))
    if candidates:
        row["candidates"] = candidates
    kind = target["kind"]
    if kind == "count":
        row["value"] = count
        row["status"] = "PASS" if count == 0 else "FAIL"
    elif kind == "median_max":
        row["value"] = count
        row["status"] = "n/a" if count is None else ("PASS" if count <= target["target"] else "FAIL")
    elif not base:
        row["value"] = None
        row["status"] = "n/a"
    else:
        value = count / base
        row["value"] = round(value, 4)
        good = value < target["target"] if kind == "rate_below" else value > target["target"]
        row["status"] = ("PASS" if good else "FAIL") + ("*" if small else "")
    return row


def _check_text(c: dict) -> str:
    bits = [c["type"]]
    if c.get("pattern"):
        bits.append(str(c["pattern"]))
    if c.get("turn") is not None:
        bits.append(f"(turn {c['turn']})")
    if c.get("found"):
        bits.append(f"said {c['found']!r}")
    if c.get("detail"):
        bits.append(f"- {c['detail']}")
    return " ".join(bits)


def _catalogue(clean: list) -> list:
    groups: dict[str, dict] = {}
    for r in clean:
        for f in r["metrics"]["findings"]:
            if f["llm_degraded"] or f["severity"] == "info":
                continue
            g = groups.setdefault(f["signature"], {"signature": f["signature"], "metric": f["metric"],
                                                   "severity": f["severity"], "findings": 0, "calls": set(),
                                                   "examples": []})
            g["findings"] += 1
            g["calls"].add(r["id"])
            if len(g["examples"]) < EXAMPLES_PER_ROW and r["id"] not in [e["id"] for e in g["examples"]]:
                turn = f.get("turn")
                emma = r["turns"][turn]["emma"] if isinstance(turn, int) and 0 <= turn < len(r["turns"]) else ""
                caller = r["turns"][turn].get("caller", "") if isinstance(turn, int) and 0 <= turn < len(r["turns"]) else ""
                g["examples"].append({"id": r["id"], "turn": turn, "detail": f["detail"], "caller": caller,
                                      "emma": emma})
    rows = []
    for g in groups.values():
        first = g["examples"][0] if g["examples"] else {}
        cause, owner = _cause(f"{g['signature']} {first.get('emma', '')}")
        rows.append({**g, "calls": len(g["calls"]), "cause": cause, "owner": owner})
    severity_rank = {"fail": 0, "candidate": 1}
    rows.sort(key=lambda g: (severity_rank.get(g["severity"], 2), -g["calls"], -g["findings"], g["signature"]))
    return rows


def _outcomes(clean: list) -> dict:
    by_goal: dict[str, dict] = defaultdict(lambda: {"calls": 0, "met": 0, "unmet": 0, "kinds": Counter()})
    for r in clean:
        g = by_goal[r.get("goal") or "none"]
        g["calls"] += 1
        met = r["metrics"].get("goal_met")
        if met is True:
            g["met"] += 1
        elif met is False:
            g["unmet"] += 1
        g["kinds"][r["outcome"]["kind"]] += 1
    return {
        "by_goal": {k: {**v, "kinds": dict(v["kinds"])} for k, v in sorted(by_goal.items())},
        "ended_by": dict(Counter(r.get("ended_by") for r in clean)),
    }


def _latency(ok: list) -> dict:
    out = {}
    for tier, label in ((0, "tier0"), (1, "tier1")):
        values = [t["engine_ms"] for r in ok for t in r["turns"]
                  if t.get("tier") == tier and t.get("caller") and t.get("llm_ok") is not False]
        out[label] = {"turns": len(values), "p50_ms": _percentile(values, 0.5), "p95_ms": _percentile(values, 0.95)}
    return out


def _llm(ok: list, meta: dict) -> dict:
    errors = Counter()
    for r in ok:
        errors.update(r["llm"]["errors"])
    return {
        "mode": meta.get("mode"),
        "calls": sum(r["llm"]["calls"] for r in ok),
        "failed": sum(r["llm"]["failed"] for r in ok),
        "errors": dict(errors),
        "quota_wait_s": round(sum(r["llm"]["quota_wait_ms"] for r in ok) / 1000, 1),
        "quota_exhausted": any(r["llm"]["quota_exhausted"] for r in ok),
        "skipped": meta.get("skipped") or [],
    }


def _turns(clean: list) -> dict:
    values = [r["caller_turns"] for r in clean]
    return {"median": median(values) if values else None, "mean": round(mean(values), 1) if values else None,
            "max": max(values) if values else None}


def _disruptions(clean: list) -> dict:
    out: dict[str, dict] = {}
    for r in clean:
        # The ones that actually happened: a planned "nonanswer" never fires if Emma never asks an open date question.
        names = r.get("disruptions_used", r.get("disruptions")) or ["(none)"]
        for name in names:
            d = out.setdefault(name, {"calls": 0, "goal_met": 0, "goal_scored": 0, "loops": 0, "dead_ends": 0})
            d["calls"] += 1
            met = r["metrics"].get("goal_met")
            if met is not None:
                d["goal_scored"] += 1
                d["goal_met"] += int(bool(met))
            d["loops"] += int(bool(_counted(r, "M3")))
            d["dead_ends"] += int(bool(_counted(r, "M10")))
    return dict(sorted(out.items()))


# ---------------------------------------------------------------- Markdown


def _value_text(row: dict, small: bool) -> str:
    if row["kind"] == "count":
        return f"{row['count']} (of {row['base']} {row['base_name']})"
    if row["kind"] == "median_max":
        return "n/a" if row["count"] is None else f"median {row['count']} (over {row['base']} {row['base_name']})"
    if row["value"] is None:
        return f"n/a (no {row['base_name']})"
    if small:
        return f"{row['count']} / {row['base']} {row['base_name']}"
    return f"{_pct(row['value'])} ({row['count']} / {row['base']} {row['base_name']})"


def _target_text(row: dict) -> str:
    t = row["target"]
    return {"count": "0", "rate_below": f"< {_pct(t)}", "rate_above": f"> {_pct(t)}",
            "median_max": f"<= {t}"}[row["kind"]]


def _cell(text: str, limit: int = 140) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).replace("|", "\\|")
    return text if len(text) <= limit else text[:limit - 3] + "..."


def markdown(summary: dict) -> str:
    meta = summary["meta"]
    small = summary["indicative_only"]
    engine = meta.get("engine") or {}
    engine_text = "R2 facade" if engine.get("new_session") else "12-step engine (no new facade yet)"
    out = [f"# Harness run {meta['run_id']}", ""]
    out.append(f"Suite **{meta['suite']}**, mode **{meta['mode']}**, {summary['calls']} call(s) "
               f"({summary['scored_calls']} scored), seed {meta['seed']}, clinic clock {meta['now'][:16]}, "
               f"wall time {meta.get('wall_s')} s. Engine: {engine_text}.")
    out.append("")
    llm = summary["llm"]
    if meta["mode"] == "live":
        out.append(f"Model: {llm['calls']} request(s), {llm['failed']} failed "
                   f"({', '.join(f'{k} x{v}' for k, v in llm['errors'].items()) or 'none'}), "
                   f"{llm['quota_wait_s']} s spent waiting out 429s."
                   + (" **Quota exhausted: later calls were skipped.**" if llm["quota_exhausted"] else ""))
        if summary["degraded_calls"]:
            out.append(f"{len(summary['degraded_calls'])} call(s) had a failed model request and are left out of "
                       f"the metrics: {', '.join(summary['degraded_calls'][:20])}.")
        out.append("")
    if summary["harness_errors"]:
        out.append(f"**{len(summary['harness_errors'])} harness error(s)** (not scored): "
                   + "; ".join(f"{e['id']}: {e['error'][:80]}" for e in summary["harness_errors"][:10]))
        out.append("")

    out += ["## Metrics vs targets", "", "| # | Metric | Result | Target | Status |", "|---|---|---|---|---|"]
    for row in summary["metrics"]:
        if row["metric"] in metrics.ZERO_TOLERANCE:
            continue
        out.append(f"| {row['metric']} | {row['name']} | {_value_text(row, small)} | {_target_text(row)} | "
                   f"{row['status']} |")
    out.append("")
    out += ["### Zero-tolerance guarantees (calls with at least one occurrence)", "",
            "| # | Failure | Calls | Status |", "|---|---|---|---|"]
    for row in summary["metrics"]:
        if row["metric"] not in metrics.ZERO_TOLERANCE:
            continue
        cand = f" (+{row['candidates']} candidate call(s) to review)" if row.get("candidates") else ""
        out.append(f"| {row['metric']} | {row['name']} | {row['count']} of {row['base']}{cand} | {row['status']} |")
    out.append("")
    if small:
        out.append(f"\\* Fewer than {MIN_CALLS_FOR_RATES} scored calls: rates are indicative only "
                   "(docs/SUCCESS_CRITERIA.md section 2). M4 counts phrase-detected candidates; M5 counts only "
                   "what the simulated caller flagged; M6 and the AI-reviewer parts of M4, M5 and M10 are not "
                   "measured here.")
    else:
        out.append("M4 counts phrase-detected candidates; M5 counts only what the simulated caller flagged; M6 and "
                   "the AI-reviewer parts of M4, M5 and M10 are not measured here.")
    out.append("")

    if summary.get("scenarios"):
        counts = summary["scenario_counts"]
        out += ["## Scenarios", "",
                "Statuses: **pass**; **known-bug** fails for the bug named (expected on today's engine); "
                "**fixed?** names a bug but now passes (flip its expectedFailure); **REGRESSION** is an invariant "
                "that fails.", "",
                "  ".join(f"{k}: {v}" for k, v in sorted(counts.items())), "",
                "| Status | Scenario | Failed checks | Known bug |", "|---|---|---|---|"]
        rank = {"REGRESSION": 0, "error": 1, "fixed?": 2, "known-bug": 3, "pass": 4}
        for s in sorted(summary["scenarios"], key=lambda s: (rank.get(s["status"], 5), s["id"])):
            failed = "; ".join(s["failed_checks"][:4]) + (" ..." if len(s["failed_checks"]) > 4 else "")
            out.append(f"| {s['status']} | `{s['id']}` {_cell(s['title'], 70)} | {_cell(failed, 220)} | "
                       f"{_cell(s.get('bug') or '', 120)} |")
        out.append("")

    out += ["## Failure catalogue", "",
            "One row per signature (metric + masked line or reason), most widespread first. Candidates need a "
            "reviewer's judgement.", ""]
    if summary["catalogue"]:
        out += ["| # | Signature | Calls | Findings | Likely cause | Fix owner | Example |", "|---|---|---|---|---|---|---|"]
        for i, g in enumerate(summary["catalogue"], start=1):
            ex = g["examples"][0] if g["examples"] else {}
            example = f"`{ex.get('id')}` t{ex.get('turn')}: caller {ex.get('caller', '')!r} -> Emma {ex.get('emma', '')!r}"
            sev = " (candidate)" if g["severity"] == "candidate" else ""
            out.append(f"| {i} | {_cell(g['signature'], 90)}{sev} | {g['calls']} | {g['findings']} | "
                       f"{_cell(g['cause'] or '(triage)', 110)} | {_cell(g['owner'], 50)} | {_cell(example, 200)} |")
    else:
        out.append("No findings.")
    out.append("")

    oc = summary["outcomes"]
    out += ["## Outcomes", "", "| Goal | Calls | Goal met | Unmet | Database outcome |", "|---|---|---|---|---|"]
    for goal, g in oc["by_goal"].items():
        kinds = ", ".join(f"{k} {v}" for k, v in sorted(g["kinds"].items()))
        out.append(f"| {goal} | {g['calls']} | {g['met']} | {g['unmet']} | {kinds} |")
    out.append("")
    out.append("Ended by: " + ", ".join(f"{k} {v}" for k, v in sorted(oc["ended_by"].items(), key=lambda x: -x[1])))
    t = summary["turns"]
    out.append(f"Caller turns per call: median {t['median']}, mean {t['mean']}, max {t['max']}.")
    out.append("")

    if summary.get("disruptions"):
        out += ["## Disruptions (simulated callers)", "", "Counted where the disruption actually happened in the call.", "",
                "| Disruption | Calls | Goal met | Calls with a loop (M3) | Dead ends (M10) |", "|---|---|---|---|---|"]
        for name, d in summary["disruptions"].items():
            met = f"{d['goal_met']} / {d['goal_scored']}" if d["goal_scored"] else "n/a"
            out.append(f"| {name} | {d['calls']} | {met} | {d['loops']} | {d['dead_ends']} |")
        out.append("")

    lat = summary["latency"]
    out += ["## Engine time per turn", "",
            "Text-harness engine time (no speech): a proxy for T1 / T2, which are measured live from /metrics.", "",
            "| Turns | Count | p50 | p95 |", "|---|---|---|---|"]
    for label, name in (("tier0", "Tier-0 (no model)"), ("tier1", "Tier-1 (model)")):
        v = lat[label]
        out.append(f"| {name} | {v['turns']} | {v['p50_ms']} ms | {v['p95_ms']} ms |")
    out.append("")
    out.append("Transcripts: `transcripts.md` and `conversations/<id>.json` in this run's folder.")
    out.append("")
    return "\n".join(out)


def transcript_text(record: dict) -> str:
    """One conversation as a readable transcript, with its findings and outcome."""
    if record.get("harness_error"):
        return f"### {record['id']}\n\nHARNESS ERROR: {record['harness_error']}\n\n```\n{record.get('traceback', '')}\n```\n"
    m = record["metrics"]
    head = [record["id"]]
    if record.get("title"):
        head.append(record["title"])
    head.append(f"goal {record.get('goal')}")
    if record.get("status"):
        head.append(record["status"])
    elif m.get("goal_met") is not None:
        head.append("goal met" if m["goal_met"] else "goal unmet")
    out = [f"### {' · '.join(head)}", ""]
    from harness import runner        # local: runner imports report lazily too
    out.append(f"Outcome: {runner.outcome_text(record['outcome'])}. Ended by {record.get('ended_by')}. "
               f"{record['caller_turns']} caller turns.")
    if record.get("disruptions"):
        out.append(f"Disruptions: {', '.join(record['disruptions'])}.")
    if record.get("card"):
        c = record["card"]
        out.append(f"Card: {c['patient']}, {c['phone']}, {c['date']} {c['time']}, {c['service']} at {c['branch']}.")
    findings = [f for f in m["findings"] if f["severity"] != "info"]
    if findings:
        out.append("Findings: " + "; ".join(f"{f['metric']} t{f['turn']} {f['detail'][:90]}"
                                            + (" [llm degraded]" if f["llm_degraded"] else "") for f in findings))
    failed = [c for c in record.get("checks", []) if not c["ok"]]
    if failed:
        out.append("Failed checks: " + "; ".join(_check_text(c) for c in failed))
    out += ["", "```"]
    for t in record["turns"]:
        n = t["n"]
        if n == 0 and not t.get("caller"):
            out.append(f"{n:>2} EMMA:   {t['emma']}")
            continue
        tags = []
        if t.get("disruption"):
            tags.append(t["disruption"])
        if not t.get("heard_previous", True):
            tags.append("talked over Emma")
        if t.get("llm_ok") is False:
            tags.append(f"llm {t.get('llm_error')}")
        if t.get("action"):
            tags.append(f"DB {t['action']}")
        caller = t.get("caller") or "(silence)"
        out.append(f"{n:>2} CALLER: {caller}" + (f"   [{', '.join(tags)}]" if tags else ""))
        out.append(f"   EMMA:   {t['emma']}")
    out += ["```", ""]
    return "\n".join(out)


def write(run_dir: str, records: list, meta: dict) -> dict:
    """summary.json, report.md and transcripts.md for a finished run; returns the summary."""
    summary = summarize(records, meta)
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1, default=str)
    with open(os.path.join(run_dir, "report.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(markdown(summary))
    with open(os.path.join(run_dir, "transcripts.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"# Transcripts: {meta['run_id']}\n\n")
        for record in records:
            fh.write(transcript_text(record))
            fh.write("\n")
    return summary


def load_run(run_dir: str) -> tuple:
    """(records, meta) from a run folder, to rebuild its report."""
    conv_dir = os.path.join(run_dir, "conversations")
    records = []
    for name in sorted(os.listdir(conv_dir)):
        if name.endswith(".json"):
            with open(os.path.join(conv_dir, name), encoding="utf-8") as fh:
                records.append(json.load(fh))
    with open(os.path.join(run_dir, "summary.json"), encoding="utf-8") as fh:
        meta = json.load(fh)["meta"]
    return records, meta
