"""
Where each reply's wait went, call by call, from logs/turns.jsonl (no caller words are in it).

    python tools/call_report.py                    today's calls
    python tools/call_report.py --day 2026-10-23   one day
    python tools/call_report.py --last 3           the last 3 calls, any day
    bash tools/call_report_remote.sh [args]        the same on the hosted Emma, from the PC

For every caller turn, the wait from "the caller stopped talking" to "Emma is heard"
is split into its parts, and the biggest part is named with whoever is responsible:

    hearing   end of speech noticed by Deepgram (stt_ms: its endpointing silence + delay)
    holding   Emma's own wait for a trailing word or the rest of a number (hold_ms)
    model     understanding the turn (nlu_ms: Gemini, or Gemma via Sarvam)
    typing    the deliberate typing beat before a reply (pause_ms)
    voice     the rest until the first audio was sent: mostly the voice's first byte (ElevenLabs,
              Piper when the call fell back) when the sentence wasn't cached
    line      sent -> heard: network and the browser's playback buffer

`voice` column: cache = pre-rendered Rachel, live = ElevenLabs, backup = Piper (the second
voice), filler = an "Okay." from the cache covered the wait. `end` column: what ended the
caller's turn: final = Deepgram's 300 ms silence (speech_final), utt = its 1 s fallback
(utterance_end, used when the line never goes quiet enough), text = typed.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date, datetime
from pathlib import Path

PARTS = ("hearing", "holding", "model", "typing", "voice", "line")
WHO = {"hearing": "Deepgram (listening)", "holding": "Emma's turn-taking wait",
       "model": "the language model", "typing": "the typing beat (on purpose)",
       "voice": "the voice (ElevenLabs / Piper)", "line": "network + browser playback"}
SLOW_MS = 2000
ENDS = {"speech_final": "final", "utterance_end": "utt", "text": "text"}


def load(log_dir: Path) -> list[dict]:
    rows = []
    for path in sorted(log_dir.glob("turns.jsonl*"), reverse=True):     # .3 .2 .1 then the live file
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    return sorted(rows, key=lambda r: r.get("ts") or 0)


def parts(r: dict) -> dict:
    """The wait split into PARTS (ms); None where the turn didn't measure it."""
    g = lambda k: r.get(k) if isinstance(r.get(k), (int, float)) else None   # noqa: E731
    stt, hold, nlu, pause = g("stt_ms"), g("hold_ms"), g("nlu_ms"), g("pause_ms") or 0.0
    endpoint, first, heard = g("endpoint_ms"), g("first_audio_ms"), g("perceived_ms")
    out = dict.fromkeys(PARTS)
    out["hearing"], out["holding"], out["model"], out["typing"] = stt, hold, nlu, pause or None
    if first is not None and endpoint is not None:
        if r.get("first_audio_source") == "filler":
            out["model"] = None                 # a cached "Okay." went out first: the model wasn't waited on
        else:
            rest = first - endpoint - (nlu or 0.0) - pause
            out["voice"] = max(rest, 0.0)
    if heard is not None and first is not None:
        out["line"] = max(heard - first, 0.0)
    return out


def fmt(ms) -> str:
    return "    -" if ms is None else f"{ms / 1000:5.2f}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--logs", default="logs", help="folder with turns.jsonl (default: logs)")
    ap.add_argument("--day", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--last", type=int, help="the last N calls instead of one day")
    args = ap.parse_args(argv)

    rows = [r for r in load(Path(args.logs)) if r.get("call_id") and r.get("turn")]
    if args.last:
        keep = list(dict.fromkeys(r["call_id"] for r in reversed(rows)))[:args.last]
        rows = [r for r in rows if r["call_id"] in keep]
    else:
        day = date.fromisoformat(args.day) if args.day else date.today()
        rows = [r for r in rows if r.get("ts") and datetime.fromtimestamp(r["ts"]).date() == day]
    if not rows:
        print("No calls in the turn log for that choice.")
        return 1

    calls: dict[str, list] = {}
    for r in rows:
        calls.setdefault(r["call_id"], []).append(r)
    totals = {p: [] for p in PARTS}
    heard_all, slow, voices, ends = [], {p: 0 for p in PARTS}, {}, {}
    for call_id, turns in calls.items():
        start = datetime.fromtimestamp(turns[0]["ts"]).strftime("%d %b %H:%M")
        print(f"\n== call {call_id}  {start}  {turns[0].get('channel') or ''}  ({len(turns)} turns)")
        print(" turn  heard | hearing holding   model  typing   voice    line | voice   end    slowest part")
        for r in turns:
            p = parts(r)
            heard = r.get("perceived_ms") if isinstance(r.get("perceived_ms"), (int, float)) else None
            src = r.get("first_audio_source") or "-"
            end = ENDS.get(r.get("endpoint_source"), r.get("endpoint_source") or "-")
            ends[end] = ends.get(end, 0) + 1
            voices[src] = voices.get(src, 0) + 1
            known = {k: v for k, v in p.items() if v is not None and k != "typing"}
            top = max(known, key=known.get) if known else None
            flag = ""
            if heard is not None:
                heard_all.append(heard)
                if heard >= SLOW_MS and top:
                    slow[top] += 1
                    flag = f"<- slow: {WHO[top]}"
            for k, v in p.items():
                if v is not None:
                    totals[k].append(v)
            print(f" {r['turn']:>4}  {fmt(heard)} | {fmt(p['hearing'])}   {fmt(p['holding'])}   {fmt(p['model'])}"
                  f"   {fmt(p['typing'])}   {fmt(p['voice'])}   {fmt(p['line'])} | {src:<7} {end:<6} {flag}")

    print("\n== summary (seconds; median / 90th percentile)")
    if heard_all:
        q = statistics.quantiles(heard_all, n=10)[-1] if len(heard_all) > 1 else heard_all[0]
        print(f"  heard after   {statistics.median(heard_all) / 1000:.2f} / {q / 1000:.2f}"
              f"   ({len(heard_all)} turns, {sum(h >= SLOW_MS for h in heard_all)} took {SLOW_MS / 1000:.0f} s or more)")
    for k in PARTS:
        vals = totals[k]
        if vals:
            q = statistics.quantiles(vals, n=10)[-1] if len(vals) > 1 else vals[0]
            print(f"  {k:<12}  {statistics.median(vals) / 1000:.2f} / {q / 1000:.2f}   {WHO[k]}")
    if any(slow.values()):
        print("  slow turns (2 s+) by their biggest part: "
              + ", ".join(f"{WHO[k]} {n}" for k, n in sorted(slow.items(), key=lambda kv: -kv[1]) if n))
    print("  first audio from: " + ", ".join(f"{k} {n}" for k, n in sorted(voices.items(), key=lambda kv: -kv[1]))
          + ("   <- 'backup' turns are the second voice (Piper)" if voices.get("backup") else ""))
    print("  turn ended by: " + ", ".join(f"{k} {n}" for k, n in sorted(ends.items(), key=lambda kv: -kv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
