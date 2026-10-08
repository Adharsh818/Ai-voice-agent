"""
Reset the DEMO clinic for a rehearsal or the demo itself (plan 7.4).

    .\\.venv\\Scripts\\python.exe tools\\demo_reset.py              # demo on 2026-10-08
    .\\.venv\\Scripts\\python.exe tools\\demo_reset.py --dry-run    # say what would happen
    .\\.venv\\Scripts\\python.exe tools\\demo_reset.py --demo-date 2026-10-07

Stop Emma first: the database is rebuilt from scratch. In order, it:

1. removes from Google Calendar every event Emma created for the current
   appointments (and nothing else), so no stale bookings stay visible;
2. deletes data/emma.db (appointments, calls, transcripts, tasks, blocks,
   campaigns, audit) and builds a fresh DEMO clinic;
3. books the fixed demo appointments docs/DEMO_SCRIPT.md relies on (FIXTURES,
   placed relative to the demo date), then about 40 random DEMO appointments
   over the next two weeks around them;
4. reconnects the four branch calendars (no new sharing emails) and queues
   every appointment for the sync worker, which fills the calendars as soon
   as Emma starts.

Everything is fictitious DEMO data; the phone numbers are never dialled.
"""

import argparse
import json
import os
import sys
import urllib.request
from datetime import date, datetime, time, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import calendar_sync  # noqa: E402
import clock  # noqa: E402
import config  # noqa: E402
import db  # noqa: E402
import scheduling  # noqa: E402
import seed_demo  # noqa: E402

DEMO_DATE = date(2026, 10, 8)

# (key, patient, caller, phone, service, doctor, day offset from the demo date, HH:MM)
# Day +1 is the recovery scene: Dr Rao is blocked that day, Priya answers and
# moves, Rahul declines (a staff task). Anita is moved and Kiran cancels in
# scenes 3 and 4; Meera is the family booking's existing appointment.
FIXTURES = [
    ("recovery_priya", "Priya Sharma", "Priya Sharma", "9876543210", "Consultation", "Dr Rao", 1, "11:00"),
    ("recovery_rahul", "Rahul Verma", "Rahul Verma", "9123456789", "General Check-up", "Dr Rao", 1, "12:00"),
    ("reschedule_anita", "Anita Desai", "Anita Desai", "9845012345", "Teeth Cleaning", "Dr Menon", 4, "10:00"),
    ("cancel_kiran", "Kiran Rao", "Kiran Rao", "9845067890", "Tooth Filling", "Dr Nair", 2, "13:00"),
]


def server_running() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{config.SERVER_PORT}/health", timeout=1.5):
            return True
    except Exception:
        return False


def created_events(conn) -> list:
    """(calendar id, event id) for every appointment Emma mirrored to Google Calendar."""
    rows = conn.execute("SELECT a.id, b.calendar_id FROM appointments a JOIN branches b ON b.id = a.branch_id "
                        "WHERE b.calendar_id IS NOT NULL AND a.calendar_event_id IS NOT NULL").fetchall()
    return [(r["calendar_id"], calendar_sync.event_id_for(r["id"])) for r in rows]


def clear_calendar(client, events: list) -> tuple:
    removed = gone = failed = 0
    for calendar_id, event_id in events:
        try:
            client.delete(calendar_id, event_id)
            removed += 1
        except calendar_sync.CalendarError as exc:
            if exc.status in (404, 410):
                gone += 1                      # cancelled earlier: already deleted
            else:
                failed += 1
    return removed, gone, failed


def _on(day: date, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.combine(day, time(h, m), tzinfo=clock.TZ)


def _bookable(day: date) -> date:
    while day.weekday() == 6:                   # closed on Sundays
        day += timedelta(days=1)
    return day


def book_fixtures(conn, demo_date: date) -> list:
    made = []
    for key, patient, caller, phone, service, doctor, offset, hhmm in FIXTURES:
        doctor_id = conn.execute("SELECT id FROM doctors WHERE spoken_name = ?", (doctor,)).fetchone()["id"]
        start = _on(_bookable(demo_date + timedelta(days=offset)), hhmm)
        result = scheduling.book(conn, service=service, doctor_id=doctor_id, start=start, patient_name=patient,
                                 caller_name=caller, phone=phone, source="seed", actor="seed",
                                 idem_key=f"fixture:{key}")
        made.append((key, patient, phone, service, doctor, start, result.ok, result.code))
    return made


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--demo-date", default=DEMO_DATE.isoformat(), help="YYYY-MM-DD (default 2026-10-08)")
    parser.add_argument("--dry-run", action="store_true", help="report only; change nothing")
    parser.add_argument("--keep-calendar", action="store_true", help="don't touch Google Calendar")
    args = parser.parse_args()
    demo_date = date.fromisoformat(args.demo_date)
    if demo_date < clock.today():
        print(f"The demo date {demo_date} is in the past; using tomorrow instead.")
        demo_date = clock.today() + timedelta(days=1)

    running = server_running()
    if running and not args.dry_run:
        print("Emma is running. Stop her first (the database is rebuilt), then run this again.", file=sys.stderr)
        return 1

    client, why = (None, "skipped (--keep-calendar)") if args.keep_calendar else calendar_sync.build_client()
    events = []
    if os.path.exists(config.DB_PATH):
        conn = db.connect(config.DB_PATH)
        db.migrate(conn)
        events = created_events(conn)
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("appointments", "calls", "tasks", "blocked_times")}
        conn.close()
        print(f"Current database: {counts}; {len(events)} events in Google Calendar.")
    if args.dry_run:
        print(f"Dry run: would clear those events ({'calendar ' + why if client is None else 'calendar ready'}), "
              f"rebuild {config.DB_PATH} and book {len(FIXTURES)} fixtures around {demo_date}."
              + (" Emma is running: stop her before the real reset." if running else ""))
        return 0

    if client is not None and events:
        removed, gone, failed = clear_calendar(client, events)
        print(f"Google Calendar: removed {removed} events ({gone} were already gone, {failed} failed).")
        if failed:
            print("Some events could not be removed; they will stay in the calendars until deleted by hand.")
    elif client is None:
        print(f"Google Calendar not touched: {why}.")

    for suffix in ("", "-wal", "-shm"):
        path = config.DB_PATH + suffix
        if os.path.exists(path):
            os.remove(path)
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    conn = db.connect(config.DB_PATH)
    db.migrate(conn)
    seed_demo.seed_catalog(conn)
    fixtures = book_fixtures(conn, demo_date)
    random_made = seed_demo.seed_appointments(conn)
    print(f"\nFresh DEMO clinic: {random_made} random appointments, plus the demo fixtures:")
    for key, patient, phone, service, doctor, start, ok, code in fixtures:
        status = "booked" if ok else f"NOT booked ({code})"
        print(f"  {key:<17} {patient:<13} {phone}  {service:<16} {doctor:<10} {start:%a %d %b %H:%M}  {status}")

    if client is not None:
        import setup_calendars                       # tools/, next to this file
        report = setup_calendars.setup(conn, client, config.CALENDAR_SHARE_WITH)
        queued = calendar_sync.enqueue_all(conn)
        print(f"\nCalendars reconnected: {', '.join(r['branch'] for r in report)}; "
              f"{queued} appointments queued (they appear once Emma is running).")
    conn.close()
    print("\nDone. Start Emma, then run tools/preflight.py.")
    return 0 if all(f[6] for f in fixtures) else 2


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.exit(main())
