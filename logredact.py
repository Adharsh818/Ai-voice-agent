"""
Keep phone numbers out of logs.

Caller text and Emma's read-backs ("your number is 9 8 7 6 5 4 3 2 1 0") are
logged for debugging. Any run of 7+ digits, optionally with single spaces
between them (how numbers are read back) and a leading "+", is masked to its
last four digits. Dashes are deliberately not separators, so ISO dates such as
2026-10-05 in log lines stay readable.
"""

import logging
import re

# Not part of a word, a time (10:30) or a decimal (1191.4); a sentence-ending
# full stop after the number is fine.
_PHONE_RE = re.compile(r"(?<![\w:])(?<!\d\.)\+?\d(?: ?\d){6,}(?!\w)(?!\.\d)(?!:)")


def mask_phones(text: str) -> str:
    def mask(match: re.Match) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        return "******" + digits[-4:]

    return _PHONE_RE.sub(mask, text or "")


class RedactPhones(logging.Filter):
    """Handler filter: rewrites each record's final message with phones masked."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = mask_phones(message)
        if redacted != message:
            record.msg, record.args = redacted, None
        return True


def install(logger: logging.Logger = None):
    """Attach the filter to every handler of `logger` (default: root)."""
    logger = logger or logging.getLogger()
    for handler in logger.handlers:
        if not any(isinstance(f, RedactPhones) for f in handler.filters):
            handler.addFilter(RedactPhones())
