import re
import os
import json
import threading
from datetime import datetime, date, time, timedelta
from dateutil.parser import parse as parse_date
from dateutil.relativedelta import relativedelta
import clock
import config

_booking_lock = threading.RLock()
_clinic_tz = clock.TZ

# Try importing Google API client libraries, but don't fail if they aren't installed yet.
try:
    from google.oauth2 import service_account
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    GOOGLE_LIBS_AVAILABLE = True
except ImportError:
    GOOGLE_LIBS_AVAILABLE = False


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
# 3b. ALTERNATIVE SLOT GENERATION
# ==========================================
def _is_bookable_time(t: time) -> bool:
    """
    True if `t` is a valid 30-minute appointment start: inside working hours
    (last start is 30 min before close) and outside the lunch break.
    """
    start = time(config.CLINIC_START_HOUR, 0)
    last_start = (
        datetime.combine(clock.today(), time(config.CLINIC_END_HOUR, 0))
        - timedelta(minutes=30)
    ).time()
    lunch_start = time(config.LUNCH_START_HOUR, config.LUNCH_START_MIN)
    lunch_end = time(config.LUNCH_END_HOUR, config.LUNCH_END_MIN)

    if t < start or t > last_start:
        return False
    if lunch_start <= t < lunch_end:
        return False
    return True


def _candidate_alt_slots(dt_start, max_candidates=6):
    """
    Candidate alternative appointment datetimes near `dt_start`, ordered by
    closeness (nearest first; earlier preferred on ties). Only same-day slots
    that fall within bookable hours are returned — no fixed 5 PM / 6 PM pair.
    """
    candidates = []
    # +/- 30-minute steps, ordered by absolute distance from the requested time.
    offsets = [-30, 30, -60, 60, -90, 90, -120, 120, -150, 150, -180, 180]
    for off in offsets:
        cand = dt_start + timedelta(minutes=off)
        if cand.date() != dt_start.date():
            continue
        if _is_bookable_time(cand.time()):
            candidates.append(cand)
        if len(candidates) >= max_candidates:
            break
    return candidates


# ==========================================
# 4. MOCK DATABASE OPERATIONS
# ==========================================
def _load_mock_db():
    if not os.path.exists(config.MOCK_DB_PATH):
        # Create empty mock database structure
        db = {"appointments": []}
        with open(config.MOCK_DB_PATH, "w") as f:
            json.dump(db, f, indent=4)
        return db
        
    try:
        with open(config.MOCK_DB_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {"appointments": []}

def _save_mock_db(db):
    with open(config.MOCK_DB_PATH, "w") as f:
        json.dump(db, f, indent=4)


# ==========================================
# 5. GOOGLE CALENDAR & GOOGLE SHEETS API
# ==========================================
def get_google_services():
    """
    Authenticates and builds Google Calendar and Sheets services.
    Returns (calendar_service, sheets_service) or (None, None).
    """
    if not GOOGLE_LIBS_AVAILABLE:
        return None, None
        
    try:
        # Check service account credentials first
        if os.path.exists(config.CREDENTIALS_FILE):
            # Load credentials
            with open(config.CREDENTIALS_FILE, 'r') as f:
                creds_data = json.load(f)
            
            # Check if it's service account or web client credentials
            if creds_data.get('type') == 'service_account':
                creds = service_account.Credentials.from_service_account_file(
                    config.CREDENTIALS_FILE,
                    scopes=[
                        'https://www.googleapis.com/auth/calendar',
                        'https://www.googleapis.com/auth/spreadsheets'
                    ]
                )
            else:
                # OAuth2 user flow (web/desktop client)
                creds = None
                if os.path.exists(config.TOKEN_FILE):
                    creds = Credentials.from_authorized_user_file(config.TOKEN_FILE)
                if not creds or not creds.valid:
                    if creds and creds.expired and creds.refresh_token:
                        creds.refresh(Request())
                    else:
                        flow = InstalledAppFlow.from_client_secrets_file(
                            config.CREDENTIALS_FILE,
                            scopes=[
                                'https://www.googleapis.com/auth/calendar',
                                'https://www.googleapis.com/auth/spreadsheets'
                            ]
                        )
                        creds = flow.run_local_server(port=0)
                    with open(config.TOKEN_FILE, 'w') as token:
                        token.write(creds.to_json())
            
            # Build services
            cal_service = build('calendar', 'v3', credentials=creds)
            sheets_service = build('sheets', 'v4', credentials=creds)
            return cal_service, sheets_service
    except Exception as e:
        print(f"Error authenticating with Google APIs: {e}. Falling back to Mock APIs.")
        
    return None, None


# ==========================================
# 6. BUSINESS LOGIC - AVAILABILITY CHECK
# ==========================================
def check_availability(date_str, time_str):
    """
    Checks if a slot is available on Google Calendar (or Mock database).
    A slot is busy if there is an overlapping appointment in the 30-minute interval.
    Returns (is_available, alternative_slots)
    """
    # Parse date and time to verify
    try:
        dt_start = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %I:%M %p")
        dt_end = dt_start + timedelta(minutes=30)
    except Exception:
        return False, []

    # Candidate alternatives are computed dynamically around the requested time
    # (nearest bookable 30-minute slots on the same day) instead of a fixed pair.
    alt_slots = _candidate_alt_slots(dt_start)

    # Use Google Calendar if enabled & configured
    if not config.USE_MOCK_APIS:
        cal_service, _ = get_google_services()
        if cal_service:
            try:
                # Format RFC3339 timestamps
                time_min = dt_start.replace(tzinfo=_clinic_tz).isoformat()
                time_max = dt_end.replace(tzinfo=_clinic_tz).isoformat()
                
                events_result = cal_service.events().list(
                    calendarId=config.CALENDAR_ID,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True
                ).execute()
                
                events = events_result.get('items', [])
                
                if len(events) == 0:
                    # Slot is free!
                    return True, []
                else:
                    # Slot is busy. Check availability of the nearest alternatives.
                    confirmed_alts = []
                    for alt_dt in alt_slots:
                        alt_min = alt_dt.replace(tzinfo=_clinic_tz).isoformat()
                        alt_max = (alt_dt + timedelta(minutes=30)).replace(tzinfo=_clinic_tz).isoformat()

                        alt_res = cal_service.events().list(
                            calendarId=config.CALENDAR_ID,
                            timeMin=alt_min,
                            timeMax=alt_max,
                            singleEvents=True
                        ).execute()

                        if len(alt_res.get('items', [])) == 0:
                            confirmed_alts.append(alt_dt.strftime("%I:%M %p"))
                        if len(confirmed_alts) >= 2:
                            break

                    # Return only genuinely free alternatives (possibly none).
                    return False, confirmed_alts
            except Exception as e:
                print(f"Google Calendar API check failed: {e}. Falling back to Mock.")

    # FALLBACK/DEFAULT TO MOCK DATABASE
    db = _load_mock_db()
    is_free = True
    
    for appt in db.get("appointments", []):
        appt_start = datetime.strptime(f"{appt['date']} {appt['time']}", "%Y-%m-%d %I:%M %p")
        appt_end = appt_start + timedelta(minutes=30)
        
        # Check overlap
        if max(dt_start, appt_start) < min(dt_end, appt_end):
            is_free = False
            break
            
    if is_free:
        return True, []
        
    # Check mock alternatives (nearest free slots around the requested time)
    confirmed_alts = []
    for alt_dt in alt_slots:
        alt_end = alt_dt + timedelta(minutes=30)
        alt_free = True
        for appt in db.get("appointments", []):
            appt_start = datetime.strptime(f"{appt['date']} {appt['time']}", "%Y-%m-%d %I:%M %p")
            appt_end = appt_start + timedelta(minutes=30)
            if max(alt_dt, appt_start) < min(alt_end, appt_end):
                alt_free = False
                break
        if alt_free:
            confirmed_alts.append(alt_dt.strftime("%I:%M %p"))
        if len(confirmed_alts) >= 2:
            break

    # Return only genuinely free alternatives (possibly none).
    return False, confirmed_alts


# ==========================================
# 7. BUSINESS LOGIC - BOOK APPOINTMENT
# ==========================================
def book_appointment(name, phone, service, date_str, time_str, age=None):
    """
    Books the appointment by:
    1. Creating a Google Calendar event.
    2. Logging the details in Google Sheets.
    If credentials aren't set, stores the record in mock_db.json.
    Returns (success_boolean, status_message)
    """
    # Double check inputs
    phone_ok, clean_phone = validate_phone(phone)
    if not phone_ok:
        return False, "Invalid phone number."
        
    # Build event title and body
    event_title = f"Dental Appointment — {config.CLINIC_NAME}"
    description = f"Patient Name: {name}\nPhone Number: {clean_phone}\nAge: {age if age else 'Not collected'}\nService: {service}"
    
    try:
        dt_start = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %I:%M %p")
        dt_end = dt_start + timedelta(minutes=30)
    except Exception as e:
        return False, f"Failed to parse booking date/time: {e}"

    # Keep the check-and-book sequence atomic within this application process.
    # Calendar is the production source of truth; a Sheets logging error must
    # never cause a second, local booking to be created.
    with _booking_lock:
        if not config.USE_MOCK_APIS:
            is_available, _ = check_availability(date_str, time_str)
            if not is_available:
                return False, "That appointment time is no longer available."

            cal_service, sheets_service = get_google_services()
            if not cal_service:
                return False, "Calendar service is unavailable; the appointment was not booked."
            try:
                event = {
                    'summary': event_title,
                    'description': description,
                    'start': {'dateTime': dt_start.replace(tzinfo=_clinic_tz).isoformat(), 'timeZone': config.CLINIC_TIMEZONE},
                    'end': {'dateTime': dt_end.replace(tzinfo=_clinic_tz).isoformat(), 'timeZone': config.CLINIC_TIMEZONE},
                }
                cal_service.events().insert(calendarId=config.CALENDAR_ID, body=event).execute()
            except Exception as e:
                print(f"Google Calendar event insertion failed: {e}")
                return False, "Calendar booking failed. Please try another time."

            if sheets_service and config.SPREADSHEET_ID:
                try:
                    values = [[name, clean_phone, str(age) if age else "N/A", service,
                               date_str, time_str, clock.now().strftime("%Y-%m-%d %H:%M:%S")]]
                    sheets_service.spreadsheets().values().append(
                        spreadsheetId=config.SPREADSHEET_ID,
                        range="Sheet1!A:G",
                        valueInputOption="USER_ENTERED",
                        insertDataOption="INSERT_ROWS",
                        body={'values': values},
                    ).execute()
                except Exception as e:
                    print(f"Google Sheets logging failed: {e}")
            return True, "Successfully booked on Google Calendar."

        db = _load_mock_db()
        for appt in db.get("appointments", []):
            if appt["phone"] == clean_phone and appt["date"] == date_str and appt["time"] == time_str:
                return True, "Appointment already exists for this caller and slot."
            appt_start = datetime.strptime(f"{appt['date']} {appt['time']}", "%Y-%m-%d %I:%M %p")
            appt_end = appt_start + timedelta(minutes=30)
            if max(dt_start, appt_start) < min(dt_end, appt_end):
                return False, "That appointment time is no longer available."

        db["appointments"].append({
            "name": name,
            "phone": clean_phone,
            "age": age,
            "service": service,
            "date": date_str,
            "time": time_str,
            "booked_at": clock.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        _save_mock_db(db)
        return True, "Booked successfully (logged in local mock database)."
