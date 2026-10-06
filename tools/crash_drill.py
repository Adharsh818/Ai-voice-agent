"""
Crash drill: kill a process mid-booking, then check the database (plan section 13, phase E2).

    python tools/crash_drill.py                  30 rounds on a fresh sample clinic in a scratch folder
    python tools/crash_drill.py --rounds 100 --seed 3
    python tools/crash_drill.py --from data/emma.db
                                                 drill on an online copy of the live database (the live
                                                 file itself is never written)

Each round starts a writer process that does what calls do, through the same
code (scheduling.py): hold an offered slot then book it, move a booking, cancel
one. After a random moment the writer is killed outright (TerminateProcess on
Windows, SIGKILL on Linux), like a power cut or `kill -9` of the server. Then,
as a restarted Emma would, the drill opens the database and checks:

- SQLite integrity_check and foreign keys; no doctor booked twice; every booking
  holds its slot claims (tools/backup.py --check), and no claim is left on a
  cancelled appointment;
- every change the writer reported as done is there (nothing committed was lost);
- the request that was in flight is all or nothing: its appointment, idempotency
  record, audit row and Calendar outbox row are all there or none are;
- retrying that request books (or moves, or cancels) exactly once, and a second
  retry returns the stored result instead of acting again;
- holds left by the killed call expire and free their slots.

--linger MS makes the writer pause up to MS inside each transaction just before
COMMIT, and again after COMMIT before reporting it, so more kills land mid-write
and between a write and its answer (default 20; 0 for the plain timing).
Exit code 1 if any round finds a problem.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import backup  # noqa: E402
import clock  # noqa: E402
import config  # noqa: E402
import db  # noqa: E402
import scheduling  # noqa: E402
import seed_demo  # noqa: E402


# ---------------------------------------------------------------- the writer (child process)

def _say(tag: str, **fields):
    print(tag, json.dumps(fields, default=str), flush=True)


def _apply(conn, op: dict) -> scheduling.Result:
    """Run one operation; the same call is used for the parent's retries."""
    if op["kind"] == "book":
        return scheduling.book(conn, service=op["service"], doctor_id=op["doctor_id"],
                               start=datetime.fromisoformat(op["start"]), patient_name=op["name"],
                               phone=op["phone"], call_id=op["call_id"], idem_key=op["idem"])
    if op["kind"] == "move":
        return scheduling.reschedule(conn, op["appointment_id"], doctor_id=op["doctor_id"],
                                     start=datetime.fromisoformat(op["start"]), call_id=op["call_id"],
                                     idem_key=op["idem"])
    return scheduling.cancel(conn, op["appointment_id"], call_id=op["call_id"], idem_key=op["idem"],
                             reason="crash drill")


def writer(db_path: str, round_no: int, seed: int, linger_ms: int):
    rng = random.Random(seed * 1000 + round_no)
    if linger_ms:
        outbox = scheduling._outbox

        def lingering_outbox(*args, **kwargs):          # the last write before COMMIT
            outbox(*args, **kwargs)
            time.sleep(rng.uniform(0, linger_ms) / 1000)

        scheduling._outbox = lingering_outbox
    conn = db.connect(db_path)
    services = [r["name"] for r in conn.execute("SELECT name FROM services")]
    _say("READY")
    for i in range(100000):
        call_id = f"drill-{round_no}-{i}"
        mine = [r["id"] for r in conn.execute(
            "SELECT id FROM appointments WHERE status = 'booked' AND created_by_call_id LIKE 'drill-%' "
            "AND start_utc > ?", (db.utc_str(clock.now() + timedelta(days=1)),))]
        roll = rng.random()
        op = {"idem": f"{call_id}:x", "call_id": call_id}
        if roll < 0.6 or not mine:
            day = clock.now().date() + timedelta(days=rng.randint(2, 14))
            slots = scheduling.find_slots(conn, service=rng.choice(services), dates=[day], limit=4,
                                          call_id=call_id)
            if not slots:
                continue
            slot = rng.choice(slots)
            if scheduling.hold(conn, slot, call_id) is None:
                continue
            op.update(kind="book", service=slot.service, doctor_id=slot.doctor_id, start=slot.start.isoformat(),
                      name=f"Drill Patient {round_no}-{i}", phone=f"+9190{rng.randint(10**7, 10**8 - 1)}")
        elif roll < 0.8:
            appt = scheduling.get_appointment(conn, rng.choice(mine))
            day = clock.now().date() + timedelta(days=rng.randint(2, 14))
            slots = scheduling.find_slots(conn, service=appt["service_id"], dates=[day], limit=4,
                                          call_id=call_id, ignore_appointment=appt["id"])
            if not slots:
                continue
            slot = rng.choice(slots)
            op.update(kind="move", appointment_id=appt["id"], doctor_id=slot.doctor_id, start=slot.start.isoformat())
        else:
            op.update(kind="cancel", appointment_id=rng.choice(mine))
        _say("TRY", **op)
        r = _apply(conn, op)
        if linger_ms:                                    # committed, the caller not yet told
            time.sleep(rng.uniform(0, linger_ms) / 1000)
        _say("DONE", idem=op["idem"], ok=r.ok, code=r.code, appointment_id=r.appointment_id)


# ---------------------------------------------------------------- the drill (parent)

def _rows(conn, sql, *args):
    return conn.execute(sql, args).fetchall()


def _footprint(conn, op: dict, appointment_id) -> dict:
    """What the in-flight request left behind, before any retry."""
    action = _rows(conn, "SELECT result_json FROM actions WHERE idempotency_key = ?", op["idem"])
    audit = _rows(conn, "SELECT COUNT(*) FROM audit_events WHERE correlation_id = ?", op["call_id"])[0][0]
    created = _rows(conn, "SELECT id FROM appointments WHERE created_by_call_id = ?", op["call_id"])
    return {"action": bool(action), "audit": audit, "created": [r[0] for r in created],
            "result": json.loads(action[0][0]) if action else None, "appointment_id": appointment_id}


def check_round(conn, done: list, op: dict | None) -> tuple[list, str]:
    """Problems found after a kill, and what happened to the in-flight request."""
    problems = list(backup.check(db_path_of(conn)))
    stale = _rows(conn, "SELECT COUNT(*) FROM slot_claims c JOIN appointments a ON a.id = c.appointment_id "
                        "WHERE a.status NOT IN ('booked', 'needs_reschedule')")[0][0]
    if stale:
        problems.append(f"{stale} slot claims left on cancelled appointments")
    for d in done:                                       # durable: every reported change is there
        if not d["ok"]:
            continue
        if not _rows(conn, "SELECT 1 FROM actions WHERE idempotency_key = ?", d["idem"]):
            problems.append(f"{d['idem']} was reported done but is missing")
        if d["kind"] == "cancel":
            status = _rows(conn, "SELECT status FROM appointments WHERE id = ?", d["appointment_id"])
            if not status or status[0][0] != "cancelled":
                problems.append(f"cancellation {d['idem']} was lost")
    if op is None:
        return problems, "between requests"

    before = _footprint(conn, op, op.get("appointment_id"))
    committed = before["action"]
    parts = [before["action"], before["audit"] > 0]
    if op["kind"] == "book":
        parts.append(bool(before["created"]))
    if committed and op["kind"] == "book":
        appt_id = before["created"][0] if before["created"] else None
        if appt_id is None or not _rows(conn, "SELECT 1 FROM sync_outbox WHERE appointment_id = ?", appt_id):
            problems.append(f"{op['idem']}: committed booking has no Calendar outbox row")
    if any(parts) and not all(parts):
        problems.append(f"{op['idem']} is half written: action={parts[0]} audit={parts[1]}"
                        + (f" appointment={parts[2]}" if len(parts) > 2 else ""))

    first = _apply(conn, op)
    second = _apply(conn, op)
    if committed and not first.replayed:
        problems.append(f"{op['idem']}: committed before the kill, but the retry acted again")
    if first.ok and not second.replayed:
        problems.append(f"{op['idem']}: the second retry acted again")
    if op["kind"] == "book":
        made = _rows(conn, "SELECT COUNT(*) FROM appointments WHERE created_by_call_id = ?", op["call_id"])[0][0]
        if made != (1 if first.ok else 0):
            problems.append(f"{op['idem']}: {made} appointments after retrying (expected {int(first.ok)})")
    problems += backup.check(db_path_of(conn))
    if committed:
        return problems, f"{op['kind']} committed before the kill; retry replayed"
    return problems, f"{op['kind']} rolled back; retry {'done' if first.ok else 'refused: ' + first.code}"


def db_path_of(conn) -> str:
    return conn.execute("PRAGMA database_list").fetchone()["file"]


def expire_holds(conn) -> tuple[int, int]:
    """Holds the killed calls left, and how many remain once their time is up (should be 0)."""
    left = _rows(conn, "SELECT COUNT(*) FROM slot_holds")[0][0]
    with db.transaction(conn):
        scheduling._purge_expired(conn, clock.now() + timedelta(seconds=config.HOLD_TTL_S + 1))
    remaining = _rows(conn, "SELECT COUNT(*) FROM slot_claims WHERE hold_id IS NOT NULL")[0][0]
    return left, remaining


def prepare(workdir: Path, source: str | None) -> str:
    path = workdir / "drill.db"
    if source:
        src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        dst = sqlite3.connect(path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
    conn = db.connect(str(path))
    try:
        db.migrate(conn)
        if seed_demo.is_empty(conn):
            seed_demo.seed(conn)
    finally:
        conn.close()
    return str(path)


def run_round(db_path: str, round_no: int, seed: int, linger_ms: int, rng: random.Random) -> tuple[list, str, int]:
    proc = subprocess.Popen([sys.executable, __file__, "--writer", db_path, "--round", str(round_no),
                             "--seed", str(seed), "--linger", str(linger_ms)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(ROOT))
    if proc.stdout.readline().split(" ", 1)[0] != "READY":
        proc.kill()
        return [f"writer did not start: {proc.communicate()[1].strip()[-300:]}"], "no start", 0
    time.sleep(rng.uniform(0.05, 1.0))
    proc.kill()                                          # no cleanup, no rollback handler, no flush
    out, err = proc.communicate()
    tries, done = {}, []
    for line in out.splitlines():
        kind, _, payload = line.partition(" ")
        if kind == "TRY":
            op = json.loads(payload)
            tries[op["idem"]] = op
        elif kind == "DONE":
            d = json.loads(payload)
            op = tries.pop(d["idem"])
            done.append({**op, **d, "appointment_id": d["appointment_id"] or op.get("appointment_id")})
    if err.strip() and "Traceback" in err:
        return [f"writer failed: {err.strip()[-300:]}"], "writer error", len(done)
    in_flight = next(iter(tries.values()), None)
    conn = db.connect(db_path)                           # "restart"
    try:
        problems, outcome = check_round(conn, done, in_flight)
    finally:
        conn.close()
    return problems, outcome, len(done)


def drill(rounds: int = 30, seed: int = 1, linger_ms: int = 20, source: str | None = None,
          keep: bool = False, say=print) -> dict:
    """Run the rounds; a summary (also what tools/evaluate.py reports)."""
    workdir = Path(tempfile.mkdtemp(prefix="emma-crash-drill-"))
    rng = random.Random(seed)
    failed, outcomes, total_done, notes = 0, {}, 0, []
    try:
        db_path = prepare(workdir, source)
        say(f"Crash drill: {rounds} rounds on {db_path} (linger {linger_ms} ms)")
        for n in range(1, rounds + 1):
            problems, outcome, count = run_round(db_path, n, seed, linger_ms, rng)
            total_done += count
            key = outcome.split(";")[0]
            outcomes[key] = outcomes.get(key, 0) + 1
            say(f"  round {n:3d}: {count:3d} changes done, killed with {outcome}"
                + ("" if not problems else "  PROBLEMS: " + "; ".join(problems)))
            failed += bool(problems)
            notes += problems
        conn = db.connect(db_path)
        try:
            left, remaining = expire_holds(conn)
            appointments = _rows(conn, "SELECT COUNT(*) FROM appointments")[0][0]
        finally:
            conn.close()
        if remaining:
            failed += 1
            notes.append(f"{remaining} hold claims remain after the holds expired")
    finally:
        if keep:
            say(f"Kept {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)
    return {"when": datetime.now().isoformat(timespec="minutes"), "rounds": rounds, "seed": seed,
            "linger_ms": linger_ms, "from": source, "changes": total_done, "appointments": appointments,
            "outcomes": outcomes, "holds_left": left, "holds_remaining": remaining, "failed_rounds": failed,
            "problems": notes[:20]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="tools/crash_drill.py", description=__doc__.splitlines()[1])
    parser.add_argument("--rounds", type=int, default=30)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--linger", type=int, default=20, metavar="MS")
    parser.add_argument("--from", dest="source", metavar="DB", help="drill on a copy of this database")
    parser.add_argument("--keep", action="store_true", help="keep the scratch database afterwards")
    parser.add_argument("--save", metavar="JSON", help="also write the summary here (tools/evaluate.py uses it)")
    parser.add_argument("--writer", metavar="DB", help=argparse.SUPPRESS)
    parser.add_argument("--round", type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.writer:
        writer(args.writer, args.round, args.seed, args.linger)
        return 0

    s = drill(args.rounds, args.seed, args.linger, args.source, args.keep)
    print(f"\n{s['changes']} changes committed across {s['rounds']} kills; {s['appointments']} appointments in the end.")
    for key, count in sorted(s["outcomes"].items(), key=lambda kv: -kv[1]):
        print(f"  killed {key}: {count}")
    print(f"  holds left by killed calls: {s['holds_left']}, "
          + ("all freed once expired" if not s["holds_remaining"] else f"{s['holds_remaining']} NOT freed"))
    print("RESULT: " + ("PASS, no round found a problem" if not s["failed_rounds"]
                        else f"FAIL in {s['failed_rounds']} round(s)"))
    if args.save:
        Path(args.save).parent.mkdir(parents=True, exist_ok=True)
        Path(args.save).write_text(json.dumps(s, indent=1), encoding="utf-8")
    return 1 if s["failed_rounds"] else 0


if __name__ == "__main__":
    sys.exit(main())
