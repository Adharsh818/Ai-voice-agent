"""
Demo-morning check (plan 7.4): is everything Emma needs ready?

    .\\.venv\\Scripts\\python.exe tools\\preflight.py

Run it with Emma started. It reads /health and the local settings, asks
ElevenLabs and Deepgram how much credit is left (read-only account calls with
the keys in .env) and checks the demo appointments are in place. Each line is
OK, WARN (works, but look at it) or FAIL (fix before the demo). Exit code 1 if
anything failed.
"""

import json
import os
import sys
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config  # noqa: E402

ELEVENLABS_MIN_CHARS = 3000      # a full rehearsal uses roughly 1,500-2,500 characters
results = []


def report(level: str, what: str, detail: str = ""):
    results.append(level)
    print(f"  {level:<4}  {what}" + (f": {detail}" if detail else ""))


def get_json(url: str, headers: dict = None, timeout: float = 8.0):
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _why(exc, where: str) -> str:
    code = getattr(exc, "code", None)
    if code in (401, 403):
        return f"this API key isn't allowed to read the balance (fine for calls); check it at {where}"
    return f"couldn't check ({type(exc).__name__}{' ' + str(code) if code else ''}); check it at {where}"


def check_server():
    print("Emma")
    try:
        health = get_json(f"http://127.0.0.1:{config.SERVER_PORT}/health", timeout=3)
    except Exception as exc:
        report("FAIL", "server", f"not reachable on port {config.SERVER_PORT} ({type(exc).__name__}); start Emma")
        return None
    report("OK", "server", f"http://localhost:{config.SERVER_PORT}")
    llm = health.get("llm") or {}
    report("OK" if llm.get("verified") else "FAIL", "Gemini", f"{llm.get('model')} verified={llm.get('verified')}")
    report("OK" if health.get("deepgram") else "FAIL", "Deepgram key")
    report("OK" if health.get("elevenlabs") else "FAIL", "ElevenLabs key")
    report("OK" if health.get("prompt_cache_ready") else "WARN", "pre-rendered prompts",
           "ready" if health.get("prompt_cache_ready") else "still warming; wait a minute")
    call = health.get("call") or {}
    report("WARN" if call.get("busy") else "OK", "call line", "busy" if call.get("busy") else "free")
    cal = health.get("calendar") or {}
    outbox = cal.get("outbox") or {}
    if not cal.get("enabled"):
        report("WARN", "Google Calendar", cal.get("reason") or "off")
    elif outbox.get("failed"):
        report("WARN", "Google Calendar", f"{outbox['failed']} failed syncs (System page > Retry all failed)")
    else:
        report("OK", "Google Calendar", f"{outbox.get('pending', 0)} waiting to sync")
    dash = health.get("dashboard") or {}
    report("OK" if dash.get("auth_configured") else "FAIL", "dashboard login", dash.get("reason") or "set")
    rec = health.get("recovery") or {}
    report("OK" if rec else "WARN", "recovery calls", f"window {rec.get('window')}" if rec else "runner not running")
    return health


def check_settings():
    print("Settings")
    report("OK" if config.R2_ENGINE else "WARN", "R2 conversation engine", "on" if config.R2_ENGINE else "off")
    report("OK" if not config.DEV_CAPTURE_AUDIO else "WARN", "audio capture",
           "off" if not config.DEV_CAPTURE_AUDIO else "ON: callers are being recorded to captures/")
    local = config.SERVER_HOST in ("127.0.0.1", "localhost", "::1")
    report("OK" if local else "WARN", "network exposure", "this computer only" if local else config.SERVER_HOST)
    report("OK", "model deadlines", f"head {config.NLU_HEAD_DEADLINE_S}s, reply {config.GEMINI_TIMEOUT}s")
    now = datetime.now().time()
    import outbound
    start, end = outbound._window()                       # noqa: SLF001
    inside = start <= now < end
    report("OK" if inside else "WARN", "recovery calling window",
           f"{start:%H:%M}-{end:%H:%M}" + ("" if inside else ", outside it right now: calls will wait"))


def check_credit():
    print("Credit")
    if config.ELEVENLABS_API_KEY:
        try:
            sub = get_json("https://api.elevenlabs.io/v1/user/subscription",
                           {"xi-api-key": config.ELEVENLABS_API_KEY})
            left = int(sub.get("character_limit", 0)) - int(sub.get("character_count", 0))
            level = "OK" if left >= ELEVENLABS_MIN_CHARS else "WARN"
            report(level, "ElevenLabs characters left", f"{left:,}" + ("" if level == "OK" else
                   " (low: Piper takes over when it runs out)"))
        except Exception as exc:
            report("WARN", "ElevenLabs characters left", _why(exc, "elevenlabs.io > Subscription"))
    if config.DEEPGRAM_API_KEY:
        try:
            headers = {"Authorization": f"Token {config.DEEPGRAM_API_KEY}"}
            projects = get_json("https://api.deepgram.com/v1/projects", headers).get("projects") or []
            total = 0.0
            for p in projects:
                for b in get_json(f"https://api.deepgram.com/v1/projects/{p['project_id']}/balances",
                                  headers).get("balances") or []:
                    total += float(b.get("amount") or 0)
            report("OK" if total >= 1 else "WARN", "Deepgram balance", f"${total:,.2f}")
        except Exception as exc:
            report("WARN", "Deepgram balance", _why(exc, "console.deepgram.com > Billing"))


def check_demo_data():
    print("Demo data")
    import db
    import demo_reset
    if not os.path.exists(config.DB_PATH):
        report("FAIL", "database", "missing; run tools/demo_reset.py")
        return
    conn = db.connect(config.DB_PATH)
    try:
        for key, patient, _caller, phone, service, doctor, _offset, _hhmm in demo_reset.FIXTURES:
            row = conn.execute(
                "SELECT a.status, a.start_utc FROM appointments a JOIN patients p ON p.id = a.patient_id "
                "JOIN services s ON s.id = a.service_id WHERE p.name = ? AND s.name = ? ORDER BY a.start_utc DESC",
                (patient, service)).fetchone()
            if row is None:
                report("WARN", f"fixture {key}", "missing; run tools/demo_reset.py")
            elif row["status"] != "booked":
                report("WARN", f"fixture {key}", f"is {row['status']} (used in a rehearsal?); reset before the demo")
            else:
                report("OK", f"fixture {key}", f"{patient}, {service} with {doctor}, {db.local(row['start_utc']):%a %d %b %H:%M}")
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("calls", "tasks")}
        if counts["calls"] or counts["tasks"]:
            report("WARN", "rehearsal leftovers", f"{counts['calls']} calls, {counts['tasks']} tasks in the history")
    finally:
        conn.close()


def main() -> int:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    print("Pearl Dental demo preflight\n")
    check_server()
    check_settings()
    check_credit()
    check_demo_data()
    failed, warned = results.count("FAIL"), results.count("WARN")
    print(f"\n{'READY' if not failed else 'NOT READY'}: {failed} to fix, {warned} to look at.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
