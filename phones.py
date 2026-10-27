"""
Indian phone numbers: validation, canonical storage form, and speech.

Accepted (after removing spaces, dashes and brackets):
    mobile    10 digits starting 6-9          9876543210, +91 98765 43210, 09876543210
    landline  0 + STD code + number = 11 digits (080 2345 6789), or the same
              without the 0 after +91

Stored as E.164 ("+919876543210"). Read back in two groups, the way people
check numbers: "9 8 7 6 5, 4 3 2 1 0".
"""

import re
from typing import Optional


def to_e164(text: str) -> Optional[str]:
    """Canonical +91 form, or None if `text` is not a valid Indian number."""
    if not text:
        return None
    raw = str(text).strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+") or (len(digits) == 12 and digits.startswith("91")):
        if not digits.startswith("91"):
            return None                       # another country code
        digits = digits[2:]
        # After +91: a mobile, or a landline written without its leading 0.
        return "+91" + digits if len(digits) == 10 and digits[0] != "0" else None
    if len(digits) == 11 and digits.startswith("0"):
        rest = digits[1:]
        return "+91" + rest if rest[0] != "0" else None
    if len(digits) == 10 and digits[0] != "0":
        # Exactly 10 digits is a number (owner, 25 Oct): a mobile, or a landline said without its 0.
        # A misheard first digit ("2" for "9") is caught by the read-back, not by refusing it.
        return "+91" + digits
    return None


def is_mobile(e164: str) -> bool:
    return bool(e164) and e164.startswith("+91") and len(e164) == 13 and e164[3] in "6789"


def national(e164: str) -> str:
    """How the caller would say it: 10-digit mobile, or 0 + STD + number for a landline."""
    if not e164:
        return ""
    rest = e164[3:] if e164.startswith("+91") else e164
    return rest if is_mobile(e164) else "0" + rest


# Two-digit STD codes (the metros: 8-digit local numbers). Elsewhere the code
# is 3-4 digits after the 0 and the local number 6-7 digits.
_METRO_STD = {"11", "20", "22", "33", "40", "44", "79", "80"}


def said_as_landline(digits: str) -> bool:
    """
    "080 2345 6789": a leading 0 then a metro STD code (or one starting 1-5).
    +91 80... could be a Bengaluru landline or a mobile starting 80, so how
    the caller said it decides the read-back. "098765 43210" is a mobile.
    """
    d = re.sub(r"\D", "", digits or "")
    return len(d) == 11 and d[0] == "0" and (d[1] in "12345" or d[1:3] in _METRO_STD)


def spoken(e164: str, landline: bool = False) -> str:
    """
    Digit-by-digit read-back, grouped the way people check numbers. A mobile in
    two groups: '9 8 7 6 5, 4 3 2 1 0'. A landline as STD code, then the
    number in two: '0 8 0, 2 3 4 5, 6 7 8 9' (not eight digits in one breath).
    landline: the caller said it as one (said_as_landline), for the +91 8...
    numbers that could be either.
    """
    digits = national(e164)
    if landline and len(digits) == 10 and (e164 or "").startswith("+91"):
        digits = "0" + digits
    elif not landline and len(digits) == 11 and digits.startswith("0") and (e164 or "").startswith("+91"):
        digits = digits[1:]                   # said as 10 digits: read back as said, no 0 added
    if len(digits) == 10:
        groups = [digits[:5], digits[5:]]
    elif len(digits) == 11 and digits.startswith("0"):
        std = 3 if digits[1:3] in _METRO_STD else 4
        local = digits[std:]
        half = len(local) // 2
        groups = [digits[:std], local[:half], local[half:]]
    else:
        groups = [digits[:len(digits) // 2], digits[len(digits) // 2:]]
    return ", ".join(" ".join(g) for g in groups if g)


def masked(e164: str) -> str:
    """For logs and the calendar: last four digits only."""
    return "******" + e164[-4:] if e164 else ""
