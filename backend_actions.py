"""
Helpers for the current 12-step dialogue in ai_engine.py.

validate_phone / resolve_date / resolve_time are what that dialogue parses
with; Day 2 replaces them with phones.py and dateparse.py. Availability and
booking go through the SQLite scheduling engine (scheduling.py): Google
Calendar is never read during a call, and it becomes a one-way mirror on Day 4.
Until the branch-aware workflows land, bookings are made at DEFAULT_BRANCH
with whichever doctor is free.
"""

import re
from datetime import datetime, date, time, timedelta
from dateutil.parser import parse as parse_date
from dateutil.relativedelta import relativedelta
import clock
import config
import db
import phones
import scheduling
from dateparse import DateConstraint, TimeConstraint


# ==========================================
# 1. PHONE NUMBER VALIDATION
# ==========================================
def validate_phone(phone_str):
    """
    Validates Indian mobile numbers (10 digits, starting with 6, 7, 8, or 9).
    Cleans up spaces, dashes, and leading +91 / 91 / 0.
    Returns (is_valid, cleaned_phone)
    """
    if not phone_str:
        return False, ""
    
    # Remove any non-digit characters
    digits = re.sub(r"\D", "", phone_str)
    
    # Strip leading country code (+91, 91) or leading 0 if the remaining part is 10 digits
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
        
    # Check if exactly 10 digits and starts with 6, 7, 8, or 9
    if len(digits) == 10 and digits[0] in "6789":
        return True, digits
    
    return False, phone_str


# ==========================================
# 2. DATE RESOLVER (NATURAL LANGUAGE)
# ==========================================
def resolve_date(date_str, base_date=None):
    """
    Parses expressions like 'today', 'tomorrow', 'next Monday', '25 August', etc.
    Returns (resolved_date_obj, resolved_date_formatted_str, error_message)
    """
    if base_date is None:
        # The clinic's date, not the server's: they differ on a UTC host.
        base_date = clock.today()
        
    if not date_str:
        return None, "", "Date string is empty."
        
    cleaned = date_str.lower().strip()
    
    # Handle simple relative days
    if cleaned in ["today", "now"]:
        resolved = base_date
    elif cleaned == "tomorrow":
        resolved = base_date + timedelta(days=1)
    elif cleaned in ["day after tomorrow", "day after"]:
        resolved = base_date + timedelta(days=2)
    elif cleaned == "earliest available" or cleaned == "earliest":
        # Start checking from today (or tomorrow if clinic is closed now)
        resolved = base_date
        # If Sunday, advance to Monday
        if resolved.weekday() == 6:
            resolved += timedelta(days=1)
    else:
        # Handle day of week (e.g. 'monday', 'next friday')
        days_of_week = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6
        }
        
        found_day = None
        for day_name, day_num in days_of_week.items():
            if day_name in cleaned:
                found_day = (day_name, day_num)
                break
                
        if found_day:
            day_name, target_weekday = found_day
            current_weekday = base_date.weekday()
            
            days_ahead = target_weekday - current_weekday
            if days_ahead <= 0:  # Target day is earlier in the week or is today
                days_ahead += 7
                
            # "Next Monday" on Friday means the upcoming Monday.  The old
            # branch added an extra week despite documenting the opposite.
                
            resolved = base_date + timedelta(days=days_ahead)
        else:
            # Fall back to dateutil.parser for absolute dates like "25 August", "14 July 2026"
            try:
                # Parse the string. It might return datetime.
                dt = parse_date(date_str, default=datetime(base_date.year, base_date.month, base_date.day))
                resolved = dt.date()
                
                # Check if the year was explicitly specified in the date string
                year_explicit = False
                year_str = str(dt.year)
                # Check if full year (e.g. "2026") or short year (e.g. "26") is in the input string
                if year_str in date_str or (len(year_str) == 4 and year_str[2:] in date_str):
                    year_explicit = True
                
                # If parsed date is in the past, and year was not explicitly specified, assume next year
                if resolved < base_date and not year_explicit:
                    resolved = resolved + relativedelta(years=1)
            except Exception:
                return None, "", "Sorry, I didn't catch the date. Which day would you like to come in?"

    # Validate Sunday constraint
    if resolved < base_date:
        return None, "", "That date has already passed. Could you choose a future date?"

    if resolved.weekday() == 6:  # Sunday
        return None, "", "We're closed on Sundays. Could you please choose a date between Monday and Saturday?"
        
    return resolved, resolved.strftime("%Y-%m-%d"), ""


# ==========================================
# 3. TIME RESOLVER
# ==========================================
def resolve_time(time_str):
    """
    Parses time strings (e.g. 'morning', 'afternoon', 'evening', '5 PM', '5:30 PM')
    Returns (resolved_time_obj, resolved_time_formatted_str, error_message)
    """
    if not time_str:
        return None, "", "Time string is empty."
        
    cleaned = time_str.lower().strip()
    
    # Handle natural time slots
    # Standard clinic hours: Morning starts at 7 AM, Afternoon starts at 12 PM, Evening starts at 4 PM or 5 PM
    if cleaned in ["morning", "earliest available", "earliest"]:
        # Earliest slot is 7:00 AM
        resolved_time = time(7, 0)
    elif cleaned == "afternoon":
        # 12:00 PM
        resolved_time = time(12, 0)
    elif cleaned == "evening":
        # 5:00 PM
        resolved_time = time(17, 0)
    else:
        # Standardize relative descriptions
        # "around 5 PM" -> "5 PM"
        cleaned = re.sub(r"(around|about|approx|at)\s+", "", cleaned)
        try:
            # Try parsing with dateutil
            dt = parse_date(cleaned)
            resolved_time = dt.time()
        except Exception:
            return None, "", "Sorry, I didn't catch the time. What time works best for you?"
            
    # Check working hours (7:00 AM to 9:00 PM)
    start_time = time(config.CLINIC_START_HOUR, 0)
    end_time = time(config.CLINIC_END_HOUR, 0)
    
    # Check lunch break (2:00 PM to 2:30 PM)
    lunch_start = time(config.LUNCH_START_HOUR, config.LUNCH_START_MIN)
    lunch_end = time(config.LUNCH_END_HOUR, config.LUNCH_END_MIN)
    
    latest_start = (datetime.combine(clock.today(), end_time) - timedelta(minutes=30)).time()
    if resolved_time < start_time or resolved_time > latest_start:
        return None, "", "Our clinic operates from 7:00 AM to 9:00 PM. Could you choose another time?"
        
    if lunch_start <= resolved_time < lunch_end:
        return None, "", "The clinic is closed for lunch break between 2:00 PM and 2:30 PM. Could you choose another time?"
        
    return resolved_time, resolved_time.strftime("%I:%M %p"), ""


# ==========================================
# 4. AVAILABILITY AND BOOKING (scheduling engine)
# ==========================================
def _parse_slot(date_str, time_str):
    try:
        naive = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %I:%M %p")
    except (TypeError, ValueError):
        return None
    return naive.replace(tzinfo=clock.TZ)


def _catalog(conn, service):
    """(branch row, service row) for the single-branch dialogue."""
    branch = scheduling.get_branch(conn, config.DEFAULT_BRANCH)
    svc = scheduling.get_service(conn, service or "Consultation") or scheduling.get_service(conn, "Consultation")
    return branch, svc


def _check(conn, date_str, time_str, service):
    start = _parse_slot(date_str, time_str)
    branch, svc = _catalog(conn, service)
    if start is None or branch is None or svc is None:
        return False, []
    offer = scheduling.suggest(
        conn, service=svc["id"], branch_ids=[branch["id"]],
        date_c=DateConstraint(start.date(), start.date()),
        time_c=TimeConstraint("exact", start.time()),
    )
    if offer.kind == "exact":
        return True, []
    # This dialogue can only change the time, so only same-day alternatives are useful.
    return False, [s.start.strftime("%I:%M %p") for s in offer.slots if s.start.date() == start.date()]


def check_availability(date_str, time_str, service=None):
    """
    (is_available, alternatives) for `service` at DEFAULT_BRANCH, with every
    scheduling rule applied: lead time, hours, lunch, doctor rota, closures,
    blocks and existing bookings. Alternatives are "HH:MM AM" strings, nearest first.
    """
    return db.get_db().run_sync(_check, date_str, time_str, service)


def _book(conn, name, phone, service, date_str, time_str, age):
    start = _parse_slot(date_str, time_str)
    branch, svc = _catalog(conn, service)
    e164 = phones.to_e164(phone)
    if start is None or branch is None or svc is None:
        return False, "Failed to parse booking date/time."
    if e164 is None:
        return False, "Invalid phone number."
    offer = scheduling.suggest(
        conn, service=svc["id"], branch_ids=[branch["id"]],
        date_c=DateConstraint(start.date(), start.date()),
        time_c=TimeConstraint("exact", start.time()),
    )
    if offer.kind == "exact":
        doctor_id = offer.slots[0].doctor_id
    else:
        # Still call book(): a retry of a booking that already succeeded is
        # answered from its idempotency record rather than refused as taken.
        doctor = conn.execute(
            "SELECT d.id FROM doctors d JOIN doctor_services ds ON ds.doctor_id = d.id "
            "WHERE d.branch_id = ? AND ds.service_id = ? AND d.active = 1 ORDER BY d.id",
            (branch["id"], svc["id"])).fetchone()
        if doctor is None:
            return False, "That appointment time is no longer available."
        doctor_id = doctor["id"]
    result = scheduling.book(
        conn, service=svc["id"], doctor_id=doctor_id, start=start, patient_name=name, caller_name=name,
        phone=e164, patient_age=age, idem_key=f"legacy:{e164}:{date_str}:{time_str}:{svc['name']}",
    )
    if result.ok:
        return True, "Appointment already booked." if result.replayed else "Booked."
    return False, "That appointment time is no longer available."


def book_appointment(name, phone, service, date_str, time_str, age=None):
    """Book at DEFAULT_BRANCH. Returns (success, message); retrying the same booking is harmless."""
    return db.get_db().run_sync(_book, name, phone, service, date_str, time_str, age)
