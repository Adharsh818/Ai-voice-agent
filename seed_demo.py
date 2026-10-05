"""
The DEMO clinic: 4 Bengaluru branches, 8 fictional doctors, 9 services and a
few weeks of sample appointments. Everything is marked is_demo, and the
dashboard labels it DEMO; Emma never says "demo" on a call.

Doctors, patients and phone numbers are invented. Sample appointments are made
through scheduling.book(), so every one of them obeys the same rules as a
real booking.

    python seed_demo.py            seed an empty database (no-op otherwise)
    python seed_demo.py --reset    delete the database at EMMA_DB_PATH and reseed
"""

import argparse
import json
import logging
import os
import random
from datetime import date, datetime, time, timedelta

import clock
import config
import db
import scheduling

logger = logging.getLogger(__name__)

# name, area
BRANCHES = [
    ("Nagarbhavi", "Nagarbhavi, Bengaluru"),
    ("Indiranagar", "Indiranagar, Bengaluru"),
    ("Jayanagar", "Jayanagar, Bengaluru"),
    ("Whitefield", "Whitefield, Bengaluru"),
]

# name, minutes, is_consultation, spoken aliases (Tier-0 uses them from Day 2)
SERVICES = [
    ("General Check-up", 30, 0, ["check up", "checkup", "check-up", "general check"]),
    ("Consultation", 30, 1, ["consultation", "consult", "see the doctor", "toothache", "tooth pain"]),
    ("Teeth Cleaning", 30, 0, ["cleaning", "scaling", "polishing"]),
    ("Tooth Filling", 45, 0, ["filling", "cavity"]),
    ("Tooth Extraction", 45, 0, ["extraction", "pull out", "pulled out", "remove a tooth", "wisdom tooth"]),
    ("Root Canal Treatment", 60, 0, ["root canal", "rct"]),
    ("Braces", 30, 1, ["braces", "orthodontic"]),
    ("Invisalign", 30, 1, ["invisalign", "clear aligners", "aligners"]),
    ("Pediatric Dentistry", 30, 0, ["pediatric", "paediatric", "kids dentist", "child dentist", "for my child"]),
]

GENERAL = ["General Check-up", "Consultation", "Teeth Cleaning", "Tooth Filling", "Tooth Extraction"]
MON_SAT = range(0, 6)

# name, spoken name, gender, branch, services, {weekdays: [(start, end), ...]}
DOCTORS = [
    ("Dr. Meera Rao", "Dr Rao", "female", "Nagarbhavi", GENERAL,
     {MON_SAT: [("09:00", "17:00")]}),
    ("Dr. Arjun Shetty", "Dr Shetty", "male", "Nagarbhavi",
     ["Root Canal Treatment", "Tooth Filling", "Consultation", "General Check-up"],
     {MON_SAT: [("13:00", "21:00")]}),
    ("Dr. Kavya Iyer", "Dr Iyer", "female", "Indiranagar", ["Braces", "Invisalign", "Consultation"],
     {range(0, 5): [("10:00", "18:00")], (5,): [("10:00", "14:00")]}),
    ("Dr. Rahul Menon", "Dr Menon", "male", "Indiranagar", GENERAL + ["Root Canal Treatment"],
     {MON_SAT: [("07:00", "15:00")]}),
    ("Dr. Sneha Kulkarni", "Dr Kulkarni", "female", "Jayanagar",
     ["Pediatric Dentistry", "General Check-up", "Teeth Cleaning", "Consultation"],
     {MON_SAT: [("09:00", "17:00")]}),
    ("Dr. Vikram Nair", "Dr Nair", "male", "Jayanagar", GENERAL + ["Root Canal Treatment"],
     {MON_SAT: [("12:00", "21:00")]}),
    ("Dr. Ananya Reddy", "Dr Reddy", "female", "Whitefield",
     ["General Check-up", "Consultation", "Teeth Cleaning", "Tooth Filling", "Pediatric Dentistry"],
     {MON_SAT: [("08:00", "16:00")]}),
    ("Dr. Farhan Ali", "Dr Ali", "male", "Whitefield",
     ["Tooth Extraction", "Braces", "Invisalign", "Consultation"],
     {(0, 2, 4, 5): [("14:30", "21:00")]}),
]

# A closure after the demo date, to show closures are respected.
CLOSURES = [(date(2026, 10, 15), "Nagarbhavi", "Staff training (DEMO)")]

PATIENTS = [
    "Aarav Sharma", "Diya Patel", "Rohan Gupta", "Ishita Nair", "Kabir Singh", "Ananya Krishnan",
    "Vihaan Joshi", "Saanvi Reddy", "Arjun Pillai", "Meera Iyer", "Aditya Rao", "Kavya Menon",
    "Siddharth Bhat", "Nisha Hegde", "Rahul Kamath", "Pooja Shenoy", "Varun Acharya", "Sneha Desai",
    "Karthik Subramanian", "Lakshmi Narayan",
]


def is_empty(conn) -> bool:
    return conn.execute("SELECT COUNT(*) FROM branches").fetchone()[0] == 0


def seed_catalog(conn):
    with db.transaction(conn):
        for name, area in BRANCHES:
            conn.execute("INSERT OR IGNORE INTO branches (name, area, is_demo) VALUES (?, ?, 1)", (name, area))
        for name, minutes, consult, aliases in SERVICES:
            conn.execute("INSERT OR IGNORE INTO services (name, duration_min, is_consultation, aliases_json) "
                         "VALUES (?, ?, ?, ?)", (name, minutes, consult, json.dumps(aliases)))
        branch_id = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM branches")}
        service_id = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM services")}
        for name, spoken, gender, branch, services, rules in DOCTORS:
            conn.execute("INSERT OR IGNORE INTO doctors (name, spoken_name, gender, branch_id, is_demo) "
                         "VALUES (?, ?, ?, ?, 1)", (name, spoken, gender, branch_id[branch]))
            doctor_id = conn.execute("SELECT id FROM doctors WHERE name = ? AND branch_id = ?",
                                     (name, branch_id[branch])).fetchone()[0]
            conn.executemany("INSERT OR IGNORE INTO doctor_services VALUES (?, ?)",
                             [(doctor_id, service_id[s]) for s in services])
            if not conn.execute("SELECT 1 FROM availability_rules WHERE doctor_id = ?", (doctor_id,)).fetchone():
                for days, spans in rules.items():
                    for wd in days:
                        for start, end in spans:
                            conn.execute("INSERT INTO availability_rules (doctor_id, weekday, start_time, end_time) "
                                         "VALUES (?, ?, ?, ?)", (doctor_id, wd, start, end))
        for day, branch, reason in CLOSURES:
            if not conn.execute("SELECT 1 FROM closures WHERE date = ? AND branch_id = ?",
                                (day.isoformat(), branch_id[branch])).fetchone():
                conn.execute("INSERT INTO closures (date, branch_id, reason, is_demo) VALUES (?, ?, ?, 1)",
                             (day.isoformat(), branch_id[branch], reason))


def seed_appointments(conn, *, count: int = 40, days: int = 14, rng_seed: int = 7, now=None) -> int:
    """Book up to `count` sample appointments over the next `days` days. Returns how many were made."""
    now = clock.localize(now) if now is not None else clock.now()
    rng = random.Random(rng_seed)
    doctors = conn.execute("SELECT id FROM doctors WHERE is_demo = 1 ORDER BY id").fetchall()
    made, attempts = 0, 0
    while made < count and attempts < count * 20:
        attempts += 1
        doctor_id = rng.choice(doctors)["id"]
        services = [r["name"] for r in conn.execute(
            "SELECT s.name FROM services s JOIN doctor_services ds ON ds.service_id = s.id WHERE ds.doctor_id = ?",
            (doctor_id,))]
        service = rng.choice(services)
        day = now.date() + timedelta(days=rng.randint(1, days))
        start = datetime.combine(day, time(rng.randint(7, 20), rng.choice([0, 30])), tzinfo=clock.TZ)
        patient = PATIENTS[made % len(PATIENTS)]
        phone = f"+91900000{made:04d}"                      # fictitious numbers, never dialled
        result = scheduling.book(conn, service=service, doctor_id=doctor_id, start=start, patient_name=patient,
                                 caller_name=patient, phone=phone, source="seed", actor="seed",
                                 idem_key=f"seed:{rng_seed}:{made}", now=now)
        if result.ok:
            made += 1
    return made


def seed(conn, *, appointments: bool = True, now=None) -> dict:
    seed_catalog(conn)
    made = seed_appointments(conn, now=now) if appointments else 0
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("branches", "doctors", "services", "appointments")}
    logger.info("Seeded DEMO clinic: %s (%d new appointments)", counts, made)
    return counts


def seed_if_empty(conn) -> bool:
    if not is_empty(conn):
        return False
    seed(conn)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help=f"delete {config.DB_PATH} first")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.reset:
        for suffix in ("", "-wal", "-shm"):
            path = config.DB_PATH + suffix
            if os.path.exists(path):
                os.remove(path)
                print(f"Deleted {path}")
    conn = db.connect(config.DB_PATH)
    db.migrate(conn)
    print("Seeded." if seed_if_empty(conn) else "Database already has data; nothing to do (use --reset).")
    conn.close()


if __name__ == "__main__":
    main()
