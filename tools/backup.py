"""
Daily backup of Emma's database (plan section 13, phase E; docs/DEPLOY.md).

    python tools/backup.py                     copy data/emma.db to backups/emma-YYYYMMDD-HHMM.db with
                                               SQLite's online backup (safe while Emma is running),
                                               verify the copy, keep the newest BACKUP_KEEP (14)
    python tools/backup.py --check [FILE]      integrity and booking consistency of the live database
                                               (or FILE): SQLite integrity_check, foreign keys, no
                                               doctor booked twice at once, every booking holding its
                                               slot claims
    python tools/backup.py --restore FILE      put FILE back as the live database (Emma must be stopped;
                                               the current one is kept as data/emma.db.before-restore)
    python tools/backup.py --list              backups on disk

Schedule it daily: deploy/emma-backup.timer on Linux; on Windows, Task Scheduler
running `.venv\\Scripts\\python.exe tools\\backup.py` in the project folder.
Backups hold patient data: keep the folder private (it is git-ignored).
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

BACKUP_DIR = Path(os.getenv("BACKUP_DIR", str(ROOT / "backups")))
KEEP = int(os.getenv("BACKUP_KEEP", "14"))


def backup(src_path: str, dest_dir: Path, keep: int = KEEP, now: datetime | None = None) -> Path:
    """An online copy of the database, verified, then old copies beyond `keep` removed."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M")
    final = dest_dir / f"emma-{stamp}.db"
    partial = final.with_suffix(".db.partial")
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    dst = sqlite3.connect(partial)
    try:
        src.backup(dst)                       # consistent snapshot even while Emma writes
    finally:
        dst.close()
        src.close()
    problems = check(str(partial))
    if problems:
        partial.unlink(missing_ok=True)
        raise RuntimeError("backup failed verification: " + "; ".join(problems))
    os.replace(partial, final)
    for old in sorted(dest_dir.glob("emma-*.db"), reverse=True)[keep:]:
        old.unlink()
    return final


def check(path: str) -> list:
    """Problems found (empty when healthy): SQLite integrity, foreign keys, booking consistency."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    problems = []
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            problems.append(f"integrity_check: {result}")
        broken = conn.execute("PRAGMA foreign_key_check").fetchall()
        if broken:
            problems.append(f"{len(broken)} rows break a foreign key")
        doubled = conn.execute(
            "SELECT a.id, b.id AS other FROM appointments a JOIN appointments b "
            "ON a.doctor_id = b.doctor_id AND a.id < b.id AND a.status = 'booked' AND b.status = 'booked' "
            "AND a.start_utc < b.end_utc AND b.start_utc < a.end_utc").fetchall()
        if doubled:
            problems.append(f"{len(doubled)} pairs of bookings overlap for the same doctor")
        unclaimed = conn.execute(
            "SELECT COUNT(*) FROM appointments a WHERE a.status = 'booked' "
            "AND NOT EXISTS (SELECT 1 FROM slot_claims c WHERE c.appointment_id = a.id)").fetchone()[0]
        if unclaimed:
            problems.append(f"{unclaimed} bookings hold no slot claims")
    except sqlite3.DatabaseError as exc:
        problems.append(f"unreadable: {exc}")
    finally:
        conn.close()
    return problems


def emma_running() -> bool:
    """Whether something answers on Emma's port (restoring under a running server would be lost or corrupt)."""
    try:
        with socket.create_connection((config.SERVER_HOST, config.SERVER_PORT), timeout=0.5):
            return True
    except OSError:
        return False


def restore(backup_path: Path, live_path: str) -> Path:
    problems = check(str(backup_path))
    if problems:
        raise RuntimeError(f"{backup_path.name} is not a healthy backup: " + "; ".join(problems))
    kept = Path(live_path + ".before-restore")
    if os.path.exists(live_path):
        shutil.copy2(live_path, kept)
    for suffix in ("-wal", "-shm"):                 # stale WAL from the old database must not replay
        Path(live_path + suffix).unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
    dst = sqlite3.connect(live_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return kept


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="tools/backup.py", description=__doc__.splitlines()[1])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", nargs="?", const=config.DB_PATH, metavar="FILE")
    group.add_argument("--restore", metavar="FILE")
    group.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)

    if args.list:
        for path in sorted(BACKUP_DIR.glob("emma-*.db"), reverse=True):
            print(f"{path.name}  {path.stat().st_size / 1024:.0f} KB")
        return 0
    if args.check:
        problems = check(args.check)
        print("healthy" if not problems else "\n".join(f"PROBLEM: {p}" for p in problems))
        return 1 if problems else 0
    if args.restore:
        if emma_running():
            print(f"Emma is running on port {config.SERVER_PORT}: stop her first, then restore.", file=sys.stderr)
            return 1
        kept = restore(Path(args.restore), config.DB_PATH)
        print(f"Restored {args.restore} (the previous database is kept as {kept.name}).")
        return 0
    if not os.path.exists(config.DB_PATH):
        print(f"No database at {config.DB_PATH}", file=sys.stderr)
        return 1
    path = backup(config.DB_PATH, BACKUP_DIR)
    print(f"Backed up to {path} ({path.stat().st_size / 1024:.0f} KB), verified healthy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
