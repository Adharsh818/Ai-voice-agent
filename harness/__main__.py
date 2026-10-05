"""
python -m harness: run conversation suites and rebuild reports.

    python -m harness run regression                     regression scenarios, offline (fake NLU, no network)
    python -m harness run catalogue --live               the edge-case catalogue against real Gemini
    python -m harness run scenarios                      both scenario groups
    python -m harness run sim --n 200 --seed 1           200 simulated callers, offline
    python -m harness run sim --n 3 --live               3 simulated callers on real Gemini (2 workers)
    python -m harness run scenarios --nlu-down           every scenario with the model down (fallback path)
    python -m harness run sim --n 200 --engine r2        200 simulated callers on the R2 engine
    python -m harness run regression --only reg_location_no_loop,reg_cancel_at_recap
    python -m harness list                               the scenario catalogue
    python -m harness show <run-dir> <conversation-id>   one transcript
    python -m harness report <run-dir>                   rebuild report.md from the saved records

Results land in harness_runs/<run-id>/ (git-ignored): report.md, summary.json,
transcripts.md and conversations/<id>.json. --publish also copies report.md
to docs/test-reports/. Exit status is 1 when a zero-tolerance guarantee
failed or a scenario regressed, so the command can gate a sprint.
"""

import argparse
import json
import logging
import os
import sys


def _mode(args) -> str:
    if args.live:
        return "live"
    return "nlu_down" if args.nlu_down else "offline"


def cmd_run(args) -> int:
    from harness import runner

    only = [x.strip() for x in args.only.split(",") if x.strip()] if args.only else None
    run_dir, summary = runner.run_suite(
        args.suite, mode=_mode(args), n=args.n, seed=args.seed, concurrency=args.concurrency, now=args.now,
        only=only, goal=args.goal, intensity=args.intensity, label=args.label, publish=args.publish,
        progress=None if args.quiet else print, log_level=logging.INFO if args.verbose else logging.ERROR,
        engine=None if args.engine == "default" else args.engine,
    )
    print()
    _print_summary(summary)
    print(f"\nreport:      {os.path.join(run_dir, 'report.md')}")
    print(f"transcripts: {os.path.join(run_dir, 'transcripts.md')}")
    if summary.get("published"):
        print(f"published:   {summary['published']}")
    regressions = (summary.get("scenario_counts") or {}).get("REGRESSION", 0)
    return 1 if (not summary["zero_tolerance_ok"] or regressions or summary["harness_errors"]) else 0


def _print_summary(summary: dict):
    from harness import report

    small = summary["indicative_only"]
    for row in summary["metrics"]:
        print(f"  {row['metric']:<4} {row['name']:<52} {report._value_text(row, small):<44} {row['status']}")
    if summary.get("scenario_counts"):
        print("  scenarios: " + ", ".join(f"{k} {v}" for k, v in sorted(summary["scenario_counts"].items())))
    llm = summary["llm"]
    if llm["mode"] == "live":
        print(f"  model: {llm['calls']} requests, {llm['failed']} failed {llm['errors'] or ''}")
    if summary["harness_errors"]:
        print(f"  HARNESS ERRORS: {len(summary['harness_errors'])}")


def cmd_list(args) -> int:
    from harness import scenarios as sc

    for s in sc.SCENARIOS:
        flag = "bug" if s.bug else "   "
        print(f"{s.group:<10} {flag} {s.id:<34} {s.title}")
    print(f"\n{len(sc.SCENARIOS)} scenarios ({sum(1 for s in sc.SCENARIOS if s.bug)} name a known bug)")
    return 0


def cmd_show(args) -> int:
    from harness import report

    path = os.path.join(args.run_dir, "conversations", f"{args.conversation}.json")
    with open(path, encoding="utf-8") as fh:
        record = json.load(fh)
    print(report.transcript_text(record))
    return 0


def cmd_report(args) -> int:
    from harness import report

    records, meta = report.load_run(args.run_dir)
    summary = report.write(args.run_dir, records, meta)
    _print_summary(summary)
    print(f"\nreport: {os.path.join(args.run_dir, 'report.md')}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m harness", description="Emma's conversation test harness.")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run a suite")
    run.add_argument("suite", choices=["regression", "catalogue", "scenarios", "sim"])
    where = run.add_mutually_exclusive_group()
    where.add_argument("--live", action="store_true", help="real Gemini with the .env key")
    where.add_argument("--offline", action="store_true", help="fake NLU, no network (the default)")
    where.add_argument("--nlu-down", action="store_true",
                       help="no NLU at all: the engine's own model-is-down path (no network)")
    run.add_argument("--engine", choices=["default", "legacy", "r2"], default="default",
                     help="default: whatever config.R2_ENGINE picks (off: the 12-step machine); "
                          "legacy or r2 force one")
    run.add_argument("--n", type=int, default=20, help="sim: number of calls (default 20)")
    run.add_argument("--seed", type=int, default=1, help="sim: seed (same seed, same calls)")
    run.add_argument("--goal", choices=["book", "cancel", "reschedule", "questions", "emergency"],
                     help="sim: give every caller this goal")
    run.add_argument("--intensity", type=int, help="sim: disruptions per call (default: random 0-3)")
    run.add_argument("--concurrency", type=int, help="worker processes (default: 1 offline, 2 live)")
    run.add_argument("--now", help="clinic clock, ISO local time (default 2026-10-01T10:00)")
    run.add_argument("--only", help="comma-separated scenario ids")
    run.add_argument("--label", help="appended to the run id")
    run.add_argument("--publish", action="store_true", help="copy report.md to docs/test-reports/")
    run.add_argument("--quiet", action="store_true", help="no per-call progress lines")
    run.add_argument("--verbose", action="store_true", help="engine logs on the console")
    run.set_defaults(fn=cmd_run)

    lst = sub.add_parser("list", help="list the scenarios")
    lst.set_defaults(fn=cmd_list)

    show = sub.add_parser("show", help="print one conversation's transcript")
    show.add_argument("run_dir")
    show.add_argument("conversation")
    show.set_defaults(fn=cmd_show)

    rep = sub.add_parser("report", help="rebuild a run's report from its records")
    rep.add_argument("run_dir")
    rep.set_defaults(fn=cmd_report)

    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")    # Windows consoles default to cp1252
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
