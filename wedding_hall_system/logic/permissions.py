"""Permission checks (Phase 1, business layer — SRS §2.2).

Permissions are data (roles/permissions/role_permissions tables), not
hard-coded role-name checks. Every service calls ``require_permission``
before doing its work; the UI hiding a button is never the enforcement.
"""
from __future__ import annotations

from ..data.database import DatabaseManager
from ..data.repositories import employee_repository
from ..utils.exceptions import AuthorizationError
from .models import Employee


def get_employee_permissions(db: DatabaseManager, employee_id: int) -> set[str]:
    return employee_repository.get_permission_codes(db, employee_id)


def has_permission(db: DatabaseManager, employee: Employee, code: str) -> bool:
    if employee.id is None:
        return False
    return code in get_employee_permissions(db, employee.id)


def require_permission(db: DatabaseManager, employee: Employee | None, code: str) -> None:
    """Raise AuthorizationError unless the employee holds ``code``."""
    if employee is None or employee.id is None:
        raise AuthorizationError("You must be logged in to do that.")
    if not has_permission(db, employee, code):
        raise AuthorizationError(f"Permission denied: '{code}' is required.")


def get_employee_roles(db: DatabaseManager, employee_id: int) -> list[str]:
    return employee_repository.get_roles(db, employee_id)
