"""
Named staff logins (docs/REAL_CLINIC_GAPS.md S2, 11 Oct).

The shared password (DASHBOARD_PASSWORD_HASH) stays: it is the owner's
login, with the admin role and the user name "owner". An admin adds named
accounts on the dashboard (System tab); each logs in with their own user
name and password, and everything they do is audited under that name. A
deactivated account's sessions stop working at once (`inactive` is checked
on every request, refreshed whenever the table changes and at start-up).
"""

from __future__ import annotations

import re
import threading
from typing import Optional

import auth
import db

OWNER = "owner"
# "demo" (19 Oct): look and talk to Emma, never change anything (auth.require_staff refuses it).
ROLES = ("admin", "staff", "demo")
_USERNAME = re.compile(r"^[a-z][a-z0-9._-]{1,31}$")
MIN_PASSWORD = 10

_inactive: set = set()
_lock = threading.Lock()


def refresh(conn) -> None:
    """Re-read which accounts are switched off (or gone)."""
    rows = conn.execute("SELECT username FROM staff_users WHERE active = 0").fetchall()
    with _lock:
        _inactive.clear()
        _inactive.update(r[0] for r in rows)


def is_active(username: str) -> bool:
    with _lock:
        return username not in _inactive


def find(conn, username: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM staff_users WHERE username = ?", ((username or "").strip().lower(),)).fetchone()
    return dict(row) if row else None


def verify(conn, username: str, password: str) -> Optional[dict]:
    """The account if the user name and password match an active one; else None (same time either way)."""
    user = find(conn, username)
    stored = user["password_hash"] if user else auth.hash_password("not a real account")
    ok = auth.verify_password(password, stored)
    if not (ok and user and user["active"]):
        return None
    with db.transaction(conn):
        conn.execute("UPDATE staff_users SET last_login_at = ? WHERE id = ?", (db.now_str(), user["id"]))
    return user


def list_users(conn) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT id, username, display_name, role, active, created_at, last_login_at FROM staff_users ORDER BY username")]


def add_user(conn, username: str, display_name: str, password: str, role: str, actor: str) -> dict:
    username = (username or "").strip().lower()
    if not _USERNAME.match(username) or username == OWNER:
        return {"ok": False, "reason": "User name: 2-32 lower-case letters, digits, dot, dash or underscore."}
    if len(password or "") < MIN_PASSWORD:
        return {"ok": False, "reason": f"Password: at least {MIN_PASSWORD} characters."}
    if role not in ROLES:
        return {"ok": False, "reason": "Role must be admin or staff."}
    if find(conn, username):
        return {"ok": False, "reason": "That user name is taken."}
    with db.transaction(conn):
        conn.execute("INSERT INTO staff_users (username, display_name, role, password_hash, created_at) "
                     "VALUES (?, ?, ?, ?, ?)", (username, (display_name or username).strip(), role,
                                                auth.hash_password(password), db.now_str()))
        conn.execute("INSERT INTO audit_events (ts, actor, action, entity, entity_id) "
                     "VALUES (?, ?, 'staff_add', 'staff', ?)", (db.now_str(), actor, username))
    refresh(conn)
    return {"ok": True, "username": username}


def set_active(conn, username: str, active: bool, actor: str) -> bool:
    with db.transaction(conn):
        n = conn.execute("UPDATE staff_users SET active = ? WHERE username = ?", (int(active), username)).rowcount
        if n:
            conn.execute("INSERT INTO audit_events (ts, actor, action, entity, entity_id) VALUES (?, ?, ?, 'staff', ?)",
                         (db.now_str(), actor, "staff_enable" if active else "staff_disable", username))
    refresh(conn)
    return bool(n)


def reset_password(conn, username: str, password: str, actor: str) -> dict:
    if len(password or "") < MIN_PASSWORD:
        return {"ok": False, "reason": f"Password: at least {MIN_PASSWORD} characters."}
    with db.transaction(conn):
        n = conn.execute("UPDATE staff_users SET password_hash = ? WHERE username = ?",
                         (auth.hash_password(password), username)).rowcount
        if n:
            conn.execute("INSERT INTO audit_events (ts, actor, action, entity, entity_id) "
                         "VALUES (?, ?, 'staff_password', 'staff', ?)", (db.now_str(), actor, username))
    return {"ok": bool(n), "reason": None if n else "No such user."}
