"""
Create the four branch calendars and share them read-only (plan 5.9).

    .\\.venv\\Scripts\\python.exe tools\\setup_calendars.py --share-with demo.pearl@gmail.com

For each active branch this makes sure a calendar called
"Pearl Dental — <branch>" exists, owned by the service account in
secrets/google-service-account.json, shares it as **reader** with the given
Gmail (default: CALENDAR_SHARE_WITH in .env), and stores its id in
branches.calendar_id. Safe to run again: an existing calendar (by stored id,
or by name) is reused and an existing share is left alone; a calendar still
carrying its old name ("... (DEMO)", until 6 Oct) is renamed.

Then it queues every current and future appointment for the sync worker, so
the calendars fill in as soon as the server runs. --no-resync skips that.

Staff calendars are view-only by design: the dashboard is the only editor, and
the worker overwrites any change made in Google Calendar.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import calendar_sync  # noqa: E402
import config  # noqa: E402
import db  # noqa: E402

DESCRIPTION = calendar_sync.CALENDAR_DESCRIPTION


def setup(conn, client, share_with: str = "", dry_run: bool = False) -> list[dict]:
    """Ensure one shared calendar per active branch; returns what was done per branch."""
    share_with = (share_with or "").strip().lower()
    report = []
    branches = conn.execute("SELECT id, name, calendar_id FROM branches WHERE active = 1 ORDER BY id").fetchall()
    for branch in branches:
        summary = calendar_sync.CALENDAR_NAME.format(branch=branch["name"])
        item = {"branch": branch["name"], "calendar_id": None, "created": False, "shared": False,
                "renamed": False}
        calendar_id = branch["calendar_id"] if branch["calendar_id"] and client.calendar_exists(
            branch["calendar_id"]) else (client.find_calendar(summary) or client.find_calendar(
                calendar_sync.LEGACY_CALENDAR_NAME.format(branch=branch["name"])))
        if calendar_id is None:
            if dry_run:
                report.append(dict(item, calendar_id="(would create)"))
                continue
            calendar_id = client.create_calendar(summary, DESCRIPTION, config.CLINIC_TIMEZONE)
            item["created"] = True
        elif client.calendar_info(calendar_id) != {"summary": summary, "description": DESCRIPTION}:
            # An older name or description (until 6 Oct: "... (DEMO)"): bring it up to date.
            if not dry_run:
                client.rename_calendar(calendar_id, summary, DESCRIPTION)
            item["renamed"] = True
        item["calendar_id"] = calendar_id
        if calendar_id != branch["calendar_id"] and not dry_run:
            with db.transaction(conn):
                conn.execute("UPDATE branches SET calendar_id = ? WHERE id = ?", (calendar_id, branch["id"]))
        if share_with and share_with not in client.readers(calendar_id):
            if not dry_run:
                client.add_reader(calendar_id, share_with)
            item["shared"] = True
        report.append(item)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--share-with", default=config.CALENDAR_SHARE_WITH,
                        help="Gmail to share the calendars with as reader (default: CALENDAR_SHARE_WITH)")
    parser.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
    parser.add_argument("--no-resync", action="store_true", help="don't queue existing appointments for sync")
    args = parser.parse_args()

    client, reason = calendar_sync.build_client()
    if client is None:
        print(f"Google Calendar isn't available: {reason}", file=sys.stderr)
        return 1
    if not args.share_with:
        print("Note: no --share-with (or CALENDAR_SHARE_WITH), so nobody else can see the calendars yet.")

    database = db.get_db()
    try:
        report = database.run_sync(setup, client, args.share_with, args.dry_run)
        for item in report:
            flags = ", ".join(f for f, on in (("created", item["created"]), ("renamed", item.get("renamed")),
                                                 ("shared", item["shared"])) if on)
            print(f"{item['branch']:<12} {item['calendar_id']}" + (f"  ({flags})" if flags else ""))
        if not args.dry_run and not args.no_resync:
            queued = database.run_sync(calendar_sync.enqueue_all)
            print(f"Queued {queued} appointments for the sync worker.")
        if args.share_with and not args.dry_run:
            print(f"\nIn {args.share_with}, accept the sharing emails (or open each link below) "
                  f"to add the calendars:")
            for item in report:
                print(f"  https://calendar.google.com/calendar/r?cid={item['calendar_id']}")
    finally:
        db.reset()
    return 0


if __name__ == "__main__":
    sys.exit(main())
