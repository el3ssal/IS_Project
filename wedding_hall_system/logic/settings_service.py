"""Owner settings screen (Phase 7, SRS §14, FR-067).

The Owner (``settings.manage``) views and edits the system settings
stored in ``system_settings``; anyone with ``settings.view`` (Owner,
Manager) can list them. Every value is validated against a spec
(range, type, JSON shape, IANA timezone) and the change is written
in the same transaction as its audit row (old/new values).

A setting change affects FUTURE transactions only: confirmed bookings
keep their frozen price/policy snapshots (SRS §14, BR-11) — changing
a value here never rewrites history, signed contracts, confirmed
prices or policy snapshots.
"""
from __future__ import annotations

import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..config.settings import AppSettings
from ..data.database import DatabaseManager
from ..data.repositories import system_repository
from ..utils.exceptions import NotFoundError, ValidationError
from ..utils.logging_config import get_logger
from . import audit_service
from .models import Employee
from .permissions import require_permission

logger = get_logger(__name__)

# key -> spec. ``kind``: "int" | "text" | "choice" | "json".
# ``group`` drives the UI grouping; ``label`` is the human name.
SETTING_SPECS: dict[str, dict] = {
    "venue.name": {"label": "Venue name", "group": "Venue",
                   "kind": "text"},
    "venue.currency": {"label": "Currency (3-letter code)", "group": "Venue",
                       "kind": "text"},
    "venue.timezone": {"label": "Venue timezone (IANA)", "group": "Venue",
                       "kind": "text"},
    "venue.default_language": {"label": "Default language (ar/en)",
                               "group": "Venue", "kind": "choice",
                               "choices": ["ar", "en"]},
    "booking.hold_hours": {"label": "Hold duration (hours)",
                           "group": "Booking", "kind": "int",
                           "min": 1, "max": 720},
    "booking.grace_hours": {"label": "Grace period after hold (hours)",
                            "group": "Booking", "kind": "int",
                            "min": 0, "max": 168},
    "booking.buffer_minutes": {"label": "Buffer before/after event (minutes)",
                               "group": "Booking", "kind": "int",
                               "min": 0, "max": 240},
    "booking.deposit_percent": {"label": "Deposit (% of total)",
                               "group": "Booking", "kind": "int",
                               "min": 0, "max": 100},
    "booking.min_duration_minutes": {"label": "Minimum event duration (minutes)",
                                     "group": "Booking", "kind": "int",
                                     "min": 30, "max": 480},
    "auth.max_failed_logins": {"label": "Failed logins before lockout",
                               "group": "Security", "kind": "int",
                               "min": 1, "max": 20},
    "auth.lockout_minutes": {"label": "Lockout duration (minutes)",
                             "group": "Security", "kind": "int",
                             "min": 1, "max": 120},
    "auth.session_expiry_hours": {"label": "Session expiry (hours)",
                                  "group": "Security", "kind": "int",
                                  "min": 1, "max": 24},
    "attachments.max_size_mb": {"label": "Max attachment size (MB)",
                                "group": "Files", "kind": "int",
                                "min": 1, "max": 100},
    "pricing.max_discount_percent": {"label": "Max manual discount (%)",
                                     "group": "Pricing", "kind": "int",
                                     "min": 0, "max": 100},
    "finance.installments_count": {"label": "Installments per plan",
                                   "group": "Finance", "kind": "int",
                                   "min": 1, "max": 12},
    "finance.installment_interval_days": {"label": "Days between installments",
                                          "group": "Finance", "kind": "int",
                                          "min": 1, "max": 365},
    "finance.final_deadline_days_before_event": {
        "label": "Final installment deadline (days before event)",
        "group": "Finance", "kind": "int", "min": 0, "max": 90},
    "finance.late_fee_percent": {"label": "Late fee (% — display only, never auto-charged)",
                                 "group": "Finance", "kind": "int",
                                 "min": 0, "max": 100},
    "finance.cancellation_policy": {"label": "Cancellation fee tiers (JSON)",
                                    "group": "Finance", "kind": "json",
                                    "json_kind": "cancellation_policy"},
    "finance.no_show_policy": {"label": "No-show policy (JSON)",
                               "group": "Finance", "kind": "json",
                               "json_kind": "no_show_policy"},
}


def _validate_cancellation_policy(value) -> None:
    if not isinstance(value, list) or not value:
        raise ValidationError(
            "Cancellation policy must be a non-empty list of tiers.")
    seen = set()
    for tier in value:
        if not isinstance(tier, dict):
            raise ValidationError("Each cancellation tier must be an object.")
        days = tier.get("min_days_before_event")
        fee = tier.get("fee_percent_of_total")
        if not isinstance(days, int) or days < 0:
            raise ValidationError(
                "Each tier needs min_days_before_event (integer >= 0).")
        if not isinstance(fee, (int, float)) or not 0 <= fee <= 100:
            raise ValidationError(
                "Each tier needs fee_percent_of_total between 0 and 100.")
        if days in seen:
            raise ValidationError(
                f"Duplicate cancellation tier for {days} days.")
        seen.add(days)


def _validate_no_show_policy(value) -> None:
    if not isinstance(value, dict):
        raise ValidationError("No-show policy must be an object.")
    if value.get("fee_basis") not in ("deposit", "total"):
        raise ValidationError(
            "No-show fee_basis must be 'deposit' or 'total'.")
    refundable = value.get("refundable_percent")
    if not isinstance(refundable, (int, float)) or not 0 <= refundable <= 100:
        raise ValidationError(
            "No-show refundable_percent must be between 0 and 100.")


def validate_setting_value(key: str, raw: str) -> str:
    """Validate a raw (string) value for ``key``; return the canonical
    stored string. Raises ValidationError on any bad value."""
    spec = SETTING_SPECS.get(key)
    if spec is None:
        raise ValidationError(f"Unknown setting: {key!r}.")
    kind = spec["kind"]
    if kind == "int":
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            raise ValidationError(
                f"{spec['label']} must be a whole number.") from None
        if not spec["min"] <= value <= spec["max"]:
            raise ValidationError(
                f"{spec['label']} must be between {spec['min']} and "
                f"{spec['max']}.")
        return str(value)
    if kind == "choice":
        value = str(raw).strip()
        if value not in spec["choices"]:
            raise ValidationError(
                f"{spec['label']} must be one of {spec['choices']}.")
        return value
    if kind == "json":
        try:
            parsed = json.loads(str(raw))
        except (TypeError, json.JSONDecodeError):
            raise ValidationError(
                f"{spec['label']} must be valid JSON.") from None
        if spec["json_kind"] == "cancellation_policy":
            _validate_cancellation_policy(parsed)
        elif spec["json_kind"] == "no_show_policy":
            _validate_no_show_policy(parsed)
        return json.dumps(parsed, ensure_ascii=False)
    # text
    value = str(raw or "").strip()
    if not value:
        raise ValidationError(f"{spec['label']} must not be empty.")
    if key == "venue.currency":
        if len(value) != 3 or not value.isalpha():
            raise ValidationError(
                "Currency must be a 3-letter code (e.g. EGP).")
        return value.upper()
    if key == "venue.timezone":
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValidationError(f"Unknown timezone: {value!r}.")
        return value
    if key == "venue.name" and len(value) > 200:
        raise ValidationError("Venue name is too long (max 200 characters).")
    return value


class SettingsService:
    """Owner settings (SRS §14)."""

    def __init__(self, db: DatabaseManager,
                 settings: AppSettings | None = None) -> None:
        self.db = db
        self.settings = settings or AppSettings.from_env()

    def list_settings(self, employee: Employee) -> list[dict]:
        require_permission(self.db, employee, "settings.view")
        rows = []
        for key, spec in SETTING_SPECS.items():
            value = system_repository.get_setting(self.db, key, "")
            rows.append({"key": key, "label": spec["label"],
                         "group": spec["group"], "kind": spec["kind"],
                         "value": value})
        return rows

    def update_setting(self, employee: Employee, key: str,
                       raw_value: str) -> dict:
        """Validate and store one setting; audit old/new in the same
        transaction. Returns the updated spec row."""
        require_permission(self.db, employee, "settings.manage")
        if key not in SETTING_SPECS:
            raise NotFoundError(f"Unknown setting: {key!r}.")
        canonical = validate_setting_value(key, raw_value)
        old = system_repository.get_setting(self.db, key, "")
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO system_settings (key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (key, canonical))
            audit_service.record(
                self.db, employee_id=employee.id, action="settings.changed",
                entity_type="setting", entity_id=None,
                old_value={"key": key, "value": old},
                new_value={"key": key, "value": canonical},
                description=f"Setting {key} changed from {old!r} to "
                            f"{canonical!r}.", conn=conn)
        logger.info("Setting %s changed by %s: %r -> %r",
                    key, employee.username, old, canonical)
        spec = SETTING_SPECS[key]
        return {"key": key, "label": spec["label"], "group": spec["group"],
                "kind": spec["kind"], "value": canonical}
