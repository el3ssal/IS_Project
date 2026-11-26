"""Auth view helpers (Phase 1).

Kept small in Phase 1: the login/main views are assembled in
``ui/app.py``. This module will grow in later phases with the
change-password and account-management screens. It exists now so the
Phase 0 file map (ui/auth_views.py) is honoured from the start.
"""
from __future__ import annotations

from ..logic.models import Employee


def welcome_markdown(employee: Employee, permission_count: int) -> str:
    return (
        f"## Welcome, {employee.display_name}\n"
        f"**Username:** {employee.username}  \n"
        f"**Roles:** {', '.join(employee.roles) or '—'}  \n"
        f"**Permissions:** {permission_count}"
    )
