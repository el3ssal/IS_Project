"""Per-user preferences (Phase 6, SRS §14/§16.3).

The only per-user preference in scope is the UI language ('ar' or
'en'). It is stored on the employee row (``employees.language``,
migration 004); an empty value falls back to the venue default
(``venue.default_language`` setting, seeded 'ar'). Changing it is
audited like any other profile change.
"""
from __future__ import annotations

from ..data.database import DatabaseManager
from ..data.repositories import employee_repository, system_repository
from ..utils.exceptions import ValidationError
from ..utils.logging_config import get_logger
from . import audit_service

logger = get_logger(__name__)

LANGUAGES = ("ar", "en")


def get_language(db: DatabaseManager, employee) -> str:
    """Effective UI language for an employee: their own preference,
    else the venue default."""
    row = employee_repository.get_by_id(db, int(employee.id))
    # sqlite3.Row has no .get(); index it, and tolerate a database
    # that predates migration 004 (no language column yet).
    try:
        stored = row["language"] if row is not None else ""
    except (IndexError, KeyError):
        stored = ""
    if stored in LANGUAGES:
        return stored
    default = system_repository.get_setting(db, "venue.default_language", "ar")
    return default if default in LANGUAGES else "ar"


def set_language(db: DatabaseManager, employee, language: str) -> str:
    """Persist a per-user language preference (SRS §14)."""
    if language not in LANGUAGES:
        raise ValidationError(
            f"Language must be one of {LANGUAGES} (got {language!r}).")
    old = get_language(db, employee)
    employee_repository.update_employee(db, int(employee.id), language=language)
    audit_service.record(
        db, employee_id=employee.id, action="user.language_change",
        entity_type="employee", entity_id=int(employee.id),
        old_value={"language": old}, new_value={"language": language},
        description=f"UI language changed {old} -> {language}.")
    return language
