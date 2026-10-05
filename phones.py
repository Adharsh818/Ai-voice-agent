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
    if len(digits) == 10 and digits[0] in "6789":
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


def spoken(e164: str) -> str:
    """Digit-by-digit read-back in two groups: '9 8 7 6 5, 4 3 2 1 0'."""
    digits = national(e164)
    cut = 5 if len(digits) == 10 else 3 if digits.startswith("080") else len(digits) // 2
    return " ".join(digits[:cut]) + ", " + " ".join(digits[cut:])


def masked(e164: str) -> str:
    """For logs and the calendar: last four digits only."""
    return "******" + e164[-4:] if e164 else ""
