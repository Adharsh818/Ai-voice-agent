"""
Dashboard login (plan 5.10): one staff password, a signed session cookie.

The dashboard shows patient names, numbers and transcripts, so it is never
open. The password is stored only as a scrypt hash in DASHBOARD_PASSWORD_HASH
(print one with `python tools/hash_password.py`); with no valid hash every
dashboard page and API answers 503 "locked" and /health says why.

    scrypt:<n>:<r>:<p>:<salt>:<key>      salt and key in unpadded base64url

No "$" separators: .env loaders expand "$..." and would mangle the hash.

Sessions are stateless cookies, signed with HMAC-SHA256 from the standard
library (no itsdangerous): base64url(json payload) + "." + base64url(signature).
The signing key mixes DASHBOARD_SESSION_SECRET (or a per-process random secret)
with the password hash, so changing the password logs everyone out. The cookie
is HttpOnly and SameSite=Strict; state-changing requests must also come from
the dashboard's own origin. Logout revokes the session id for its lifetime.

Failed logins are rate limited per client: DASHBOARD_LOGIN_MAX_FAILURES within
DASHBOARD_LOGIN_WINDOW_S locks that client out for the same window.
"""

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import deque
from typing import Optional
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

import config

COOKIE_NAME = "emma_session"
SCRYPT_N, SCRYPT_R, SCRYPT_P, KEY_LEN = 2 ** 14, 8, 1, 32
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Without DASHBOARD_SESSION_SECRET: sessions last until the server restarts.
_PROCESS_SECRET = secrets.token_bytes(32)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ---------------------------------------------------------------- password hashes
def hash_password(password: str, *, n: int = SCRYPT_N, r: int = SCRYPT_R, p: int = SCRYPT_P,
                  salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=KEY_LEN,
                         maxmem=256 * 1024 * 1024)
    return f"scrypt:{n}:{r}:{p}:{_b64(salt)}:{_b64(key)}"


def _parse(stored: str):
    try:
        scheme, n, r, p, salt, key = (stored or "").strip().split(":")
        if scheme != "scrypt":
            return None
        n, r, p = int(n), int(r), int(p)
        if n < 2 ** 12 or n & (n - 1) or not (1 <= r <= 32) or not (1 <= p <= 16):
            return None
        salt_b, key_b = _unb64(salt), _unb64(key)
        if len(salt_b) < 8 or len(key_b) < 16:
            return None
        return n, r, p, salt_b, key_b
    except (ValueError, TypeError):
        return None


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of `password` against a stored scrypt hash."""
    parsed = _parse(stored)
    if parsed is None or not password:
        return False
    n, r, p, salt, key = parsed
    try:
        candidate = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(key),
                                   maxmem=256 * 1024 * 1024)
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(candidate, key)


def status() -> dict:
    """Is the dashboard usable? For /health and the login page (never reveals the hash)."""
    if not config.DASHBOARD_PASSWORD_HASH:
        return {"configured": False,
                "reason": "DASHBOARD_PASSWORD_HASH is not set; run tools/hash_password.py and add it to .env"}
    if _parse(config.DASHBOARD_PASSWORD_HASH) is None:
        return {"configured": False,
                "reason": "DASHBOARD_PASSWORD_HASH is not a valid scrypt hash; run tools/hash_password.py again"}
    return {"configured": True, "reason": None,
            "session_hours": config.DASHBOARD_SESSION_HOURS,
            "persistent_sessions": bool(config.DASHBOARD_SESSION_SECRET)}


def configured() -> bool:
    return status()["configured"]


# ---------------------------------------------------------------- session cookies
_revoked: dict = {}          # session id -> expiry (epoch seconds)
_revoked_lock = threading.Lock()


def _signing_key() -> bytes:
    base = config.DASHBOARD_SESSION_SECRET.encode("utf-8") or _PROCESS_SECRET
    return hmac.new(base, b"emma-dashboard-session|" + config.DASHBOARD_PASSWORD_HASH.encode("utf-8"),
                    hashlib.sha256).digest()


def _sign(payload: str) -> str:
    return _b64(hmac.new(_signing_key(), payload.encode("ascii"), hashlib.sha256).digest())


def issue_session(now: Optional[float] = None, user: str = "staff") -> str:
    now = time.time() if now is None else now
    payload = _b64(json.dumps({"sub": user, "iat": int(now),
                               "exp": int(now + config.DASHBOARD_SESSION_HOURS * 3600),
                               "sid": secrets.token_hex(8)}, separators=(",", ":")).encode("utf-8"))
    return f"{payload}.{_sign(payload)}"


def read_session(token: Optional[str], now: Optional[float] = None) -> Optional[dict]:
    """The session's payload if `token` is genuine, unexpired and not logged out; else None."""
    if not token or "." not in token or not configured():
        return None
    payload, signature = token.rsplit(".", 1)
    try:
        # Compared as bytes: a mangled cookie with non-ASCII characters must be
        # a plain "not logged in", not a TypeError on every dashboard request.
        genuine = hmac.compare_digest(signature.encode("ascii"), _sign(payload).encode("ascii"))
    except UnicodeError:
        return None
    if not genuine:
        return None
    try:
        data = json.loads(_unb64(payload))
    except ValueError:
        return None
    now = time.time() if now is None else now
    if not isinstance(data, dict) or data.get("exp", 0) <= now:
        return None
    with _revoked_lock:
        if data.get("sid") in _revoked:
            return None
    return data


def revoke(token: Optional[str]):
    """Logout: this session stops working now, even though the cookie was signed."""
    data = read_session(token)
    if not data:
        return
    now = time.time()
    with _revoked_lock:
        for sid in [s for s, exp in _revoked.items() if exp <= now]:
            del _revoked[sid]
        _revoked[data["sid"]] = data["exp"]


# ---------------------------------------------------------------- login rate limit
class LoginLimiter:
    """Per-client failed-login counter with a lockout."""

    def __init__(self, max_failures: Optional[int] = None, window_s: Optional[int] = None):
        self.max_failures = max_failures
        self.window_s = window_s
        self._failures: dict = {}
        self._locked_until: dict = {}
        self._lock = threading.Lock()

    def _limits(self):
        return (self.max_failures or config.DASHBOARD_LOGIN_MAX_FAILURES,
                self.window_s or config.DASHBOARD_LOGIN_WINDOW_S)

    def retry_after(self, key: str, now: Optional[float] = None) -> int:
        """Seconds this client must wait before trying again (0 = go ahead)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            until = self._locked_until.get(key, 0)
            if until > now:
                return int(until - now) + 1
            self._locked_until.pop(key, None)
            return 0

    def failure(self, key: str, now: Optional[float] = None):
        now = time.monotonic() if now is None else now
        max_failures, window = self._limits()
        with self._lock:
            recent = self._failures.setdefault(key, deque())
            recent.append(now)
            while recent and recent[0] <= now - window:
                recent.popleft()
            if len(recent) >= max_failures:
                self._locked_until[key] = now + window
                recent.clear()

    def success(self, key: str):
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)

    def reset(self):
        with self._lock:
            self._failures.clear()
            self._locked_until.clear()


limiter = LoginLimiter()


# ---------------------------------------------------------------- FastAPI glue
def same_origin(headers) -> bool:
    """A state-changing request must come from the dashboard's own page (or a configured origin)."""
    origin = (headers.get("origin") or "").rstrip("/")
    if not origin:
        return True                 # not a browser; the cookie is still required
    if origin in config.ALLOWED_ORIGINS:
        return True
    host = (headers.get("host") or "").lower()
    return bool(host) and urlsplit(origin).netloc.lower() == host


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def current_user(request: Request) -> Optional[str]:
    data = read_session(request.cookies.get(COOKIE_NAME))
    return data.get("sub") if data else None


def require_staff(request: Request) -> str:
    """Dependency for every dashboard API route: 503 if locked, 401 if not logged in, 403 cross-site."""
    state = status()
    if not state["configured"]:
        raise HTTPException(status_code=503, detail="Dashboard locked: " + state["reason"])
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Please log in.")
    if request.method not in _SAFE_METHODS and not same_origin(request.headers):
        raise HTTPException(status_code=403, detail="Cross-site request refused.")
    return user


def set_session_cookie(response, token: str):
    response.set_cookie(COOKIE_NAME, token, max_age=int(config.DASHBOARD_SESSION_HOURS * 3600),
                        httponly=True, samesite="strict", secure=config.DASHBOARD_COOKIE_SECURE, path="/")


def clear_session_cookie(response):
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict",
                           secure=config.DASHBOARD_COOKIE_SECURE)
