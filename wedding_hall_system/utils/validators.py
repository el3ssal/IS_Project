"""Input validation and normalisation (Phase 1).

Pure functions — no DB, no UI. Used by models and services so the same
rule is enforced everywhere, including direct service calls (tests).
"""
from __future__ import annotations

import re
from datetime import datetime

from .exceptions import ValidationError

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,50}$")


def require_text(value: str | None, field: str, *, max_length: int = 200) -> str:
    """Return a stripped, non-empty string or raise ValidationError."""
    if value is None:
        raise ValidationError(f"{field} is required.")
    text = str(value).strip()
    if not text:
        raise ValidationError(f"{field} is required.")
    if len(text) > max_length:
        raise ValidationError(f"{field} must be at most {max_length} characters.")
    return text


def validate_username(username: str | None) -> str:
    """Usernames: 3-50 chars, letters/digits/._- only."""
    name = require_text(username, "Username", max_length=50)
    if not _USERNAME_RE.match(name):
        raise ValidationError(
            "Username must be 3-50 characters and contain only letters, digits, '.', '_' or '-'."
        )
    return name


def validate_password_strength(password: str | None) -> str:
    """Minimum password rule for this project: 8+ chars, a letter and a digit."""
    if password is None or len(password) < 8:
        raise ValidationError("Password must be at least 8 characters long.")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise ValidationError("Password must contain at least one letter and one digit.")
    return password


def normalize_phone(phone: str | None) -> str:
    """Normalise an Egyptian phone number for duplicate detection (SRS §5.2).

    Removes spaces/dashes; converts a leading ``0`` to the ``+20`` country
    code so ``01012345678`` and ``+201012345678`` compare equal.
    Returns "" for empty input (phone is optional on some records).
    """
    if phone is None:
        return ""
    digits = re.sub(r"[\s\-()]", "", str(phone).strip())
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    elif digits.startswith("0"):
        digits = "+20" + digits[1:]
    elif digits.startswith("20") and not digits.startswith("+"):
        digits = "+" + digits
    if not re.fullmatch(r"\+?\d{7,15}", digits):
        raise ValidationError("Phone number looks invalid. Use digits, optionally starting with +20.")
    return digits


def normalize_email(email: str | None) -> str:
    """Lower-case and strip an email address; "" for empty input."""
    if email is None:
        return ""
    cleaned = str(email).strip().lower()
    if not cleaned:
        return ""
    if not _EMAIL_RE.match(cleaned):
        raise ValidationError("Email address looks invalid.")
    return cleaned


def validate_amount_piasters(amount: int | None, field: str = "Amount") -> int:
    """Money is integer piasters; must be a non-negative integer."""
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise ValidationError(f"{field} must be a whole number of piasters.")
    if amount < 0:
        raise ValidationError(f"{field} cannot be negative.")
    return amount


def parse_iso_datetime(value: str | None, field: str = "Date/time") -> datetime:
    """Parse an ISO-8601 datetime string or raise ValidationError."""
    text = require_text(value, field)
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        raise ValidationError(f"{field} must be an ISO-8601 date/time, e.g. 2026-12-01T18:00:00.")


def validate_time_hhmm(value: str | None, field: str = "Time") -> str:
    """Validate a 24-hour ``HH:MM`` time string; return it normalised."""
    text = require_text(value, field)
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", text):
        raise ValidationError(f"{field} must be a 24-hour time like 18:00.")
    return text


def validate_date_yyyy_mm_dd(value: str | None, field: str = "Date") -> str:
    """Validate a ``YYYY-MM-DD`` date string; return it unchanged."""
    from datetime import date as _date

    text = require_text(value, field)
    try:
        _date.fromisoformat(text)
    except ValueError:
        raise ValidationError(f"{field} must be a date like 2026-12-01.")
    if len(text) != 10:
        raise ValidationError(f"{field} must be a date like 2026-12-01.")
    return text


def validate_weekday(value: int | None, field: str = "Weekday") -> int:
    """Weekday convention: Monday=0 .. Sunday=6 (Python datetime.weekday)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{field} must be a whole number 0 (Monday) to 6 (Sunday).")
    if not 0 <= value <= 6:
        raise ValidationError(f"{field} must be between 0 (Monday) and 6 (Sunday).")
    return value


def validate_positive_int(value: int | None, field: str, *, minimum: int = 1) -> int:
    """A whole number >= ``minimum`` (counts, minutes, capacities)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{field} must be a whole number.")
    if value < minimum:
        raise ValidationError(f"{field} must be at least {minimum}.")
    return value


def egp_to_piasters(value: float | int | None, field: str = "Amount (EGP)") -> int:
    """Convert a UI amount in EGP (up to 2 decimals) to integer piasters."""
    if value is None or isinstance(value, bool):
        raise ValidationError(f"{field} is required.")
    try:
        amount = float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field} must be a number.")
    if amount < 0:
        raise ValidationError(f"{field} cannot be negative.")
    if round(amount * 100) != amount * 100:
        raise ValidationError(f"{field} may have at most 2 decimal places.")
    return int(round(amount * 100))
