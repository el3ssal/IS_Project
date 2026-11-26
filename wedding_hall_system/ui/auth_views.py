"""Auth view helpers (Phase 1; Phase 8: bilingual, SRS §16.3).

Kept small in Phase 1: the login/main views are assembled in
``ui/app.py``. This module will grow in later phases with the
change-password and account-management screens. It exists now so the
Phase 0 file map (ui/auth_views.py) is honoured from the start.
"""
from __future__ import annotations

from ..logic.models import Employee
from .i18n import t


def welcome_markdown(employee: Employee, permission_count: int,
                     lang: str = "ar") -> str:
    return (
        f"## {t(lang, 'login.welcome')}, {employee.display_name}\n"
        f"**{t(lang, 'login.username')}:** {employee.username}  \n"
        f"**{t(lang, 'login.roles')}:** {', '.join(employee.roles) or '—'}  \n"
        f"**{t(lang, 'login.permissions')}:** {permission_count}"
    )
