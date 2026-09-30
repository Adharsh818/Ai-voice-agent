"""
SQLite access: connection settings, migrations, and one writer thread.

SQLite in WAL mode is the source of truth for appointments. Every database call
from the running server goes through Database.run(), which executes it on a
single dedicated thread. That serialises writes without blocking the event loop,
and a slow disk never stalls a live call.

Serialising writes is a convenience, not the safety net: the slot_claims
primary key (see migrations/001_init.sql) is what makes a double booking
impossible, even for separate processes or connections.

Timestamps: *_utc columns hold "YYYY-MM-DDTHH:MM:SSZ". utc_str()/parse_utc()
convert; clock.localize() turns them back into clinic time.
"""

import asyncio
import functools
import logging
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import clock
import config

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


# ---------------------------------------------------------------- timestamps
def utc_str(dt: datetime) -> str:
    """Aware datetime (or naive clinic-local) -> 'YYYY-MM-DDTHH:MM:SSZ'."""
    return clock.localize(dt).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def local(value: str) -> datetime:
    """A stored *_utc value as a clinic-local aware datetime."""
    return parse_utc(value).astimezone(clock.TZ)


def now_str() -> str:
    return utc_str(clock.now())


# ---------------------------------------------------------------- connections
def connect(path: str) -> sqlite3.Connection:
    """
    A connection in autocommit mode (isolation_level=None), so transactions are
    explicit: `with transaction(conn): ...` issues BEGIN IMMEDIATE, which takes
    the write lock up front instead of failing halfway through.
    """
    if path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE ... COMMIT, or ROLLBACK on any exception."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply every migrations/NNN_*.sql not yet recorded; returns the versions applied."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    applied = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = path.stem
        if version in done:
            continue
        # executescript commits any open transaction first, so wrap the script
        # itself in BEGIN/COMMIT to keep each migration all-or-nothing.
        script = path.read_text(encoding="utf-8")
        try:
            conn.executescript(
                "BEGIN IMMEDIATE;\n" + script
                + f"\nINSERT INTO schema_migrations VALUES ('{version}', '{now_str()}');\nCOMMIT;"
            )
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        applied.append(version)
        logger.info("Applied migration %s", version)
    return applied


# ---------------------------------------------------------------- the writer thread
class Database:
    """One connection, used only from one thread; await run(fn, ...) from async code."""

    def __init__(self, path: str):
        self.path = path
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="emma-db")
        self._conn: sqlite3.Connection | None = None
        self._thread: int | None = None

    def _call(self, fn, *args, **kwargs):
        if self._conn is None:
            self._conn = connect(self.path)
            self._thread = threading.get_ident()
        return fn(self._conn, *args, **kwargs)

    async def run(self, fn, *args, **kwargs):
        """Run fn(conn, *args, **kwargs) on the database thread."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, functools.partial(self._call, fn, *args, **kwargs)
        )

    def run_sync(self, fn, *args, **kwargs):
        """Same as run(), for synchronous callers (worker threads, tools, tests)."""
        if threading.get_ident() == self._thread:
            return self._call(fn, *args, **kwargs)  # already on the db thread
        return self._executor.submit(self._call, fn, *args, **kwargs).result()

    def close(self):
        def _close(conn):
            conn.close()

        if self._conn is not None:
            self._executor.submit(lambda: _close(self._conn)).result()
            self._conn = None
        self._executor.shutdown(wait=True)


_db: Database | None = None


def get_db() -> Database:
    """The process-wide database at config.DB_PATH, migrated (and demo-seeded if empty)."""
    global _db
    if _db is None or _db.path != config.DB_PATH:
        if _db is not None:
            _db.close()
        _db = Database(config.DB_PATH)
        _db.run_sync(migrate)
        if config.DEMO_SEED_ON_EMPTY:
            import seed_demo  # local import: seed_demo uses scheduling, which uses db

            _db.run_sync(seed_demo.seed_if_empty)
    return _db


def reset():
    """Close the process-wide database (tests point config.DB_PATH elsewhere first)."""
    global _db
    if _db is not None:
        _db.close()
        _db = None
