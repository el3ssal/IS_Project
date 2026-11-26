"""Employee / role / permission / session queries (Phase 1, data tier).

Repositories own SQL. Services call these (or DatabaseManager helpers)
and never build SQL from user input — all queries are parameterised.
"""
from __future__ import annotations

import sqlite3

from ..database import DatabaseManager


def get_by_username(db: DatabaseManager, username: str) -> sqlite3.Row | None:
    return db.query_one("SELECT * FROM employees WHERE username = ?", (username,))


def get_by_id(db: DatabaseManager, employee_id: int) -> sqlite3.Row | None:
    return db.query_one("SELECT * FROM employees WHERE id = ?", (employee_id,))


def list_employees(db: DatabaseManager) -> list[sqlite3.Row]:
    return db.query_all(
        "SELECT * FROM employees ORDER BY username"
    )


def update_employee(db: DatabaseManager, employee_id: int, *,
                    full_name: str | None = None, phone: str | None = None,
                    email: str | None = None, specialties: str | None = None,
                    is_active: bool | None = None,
                    language: str | None = None) -> None:
    """Update profile fields only — username and password are never
    changed here (username is the login identity; passwords go through
    AuthService). ``language`` is the per-user UI preference (Phase 6,
    SRS §14); validated by the caller (logic/preferences.py)."""
    sets: list[str] = ["updated_at = CURRENT_TIMESTAMP"]
    params: list = []
    if full_name is not None:
        sets.append("full_name = ?")
        params.append(full_name)
    if phone is not None:
        sets.append("phone = ?")
        params.append(phone)
    if email is not None:
        sets.append("email = ?")
        params.append(email)
    if specialties is not None:
        sets.append("specialties = ?")
        params.append(specialties)
    if is_active is not None:
        sets.append("is_active = ?")
        params.append(1 if is_active else 0)
    if language is not None:
        sets.append("language = ?")
        params.append(language)
    if len(sets) == 1:
        return
    params.append(employee_id)
    db.execute(
        f"UPDATE employees SET {', '.join(sets)} WHERE id = ?", tuple(params))


def get_roles(db: DatabaseManager, employee_id: int) -> list[str]:
    rows = db.query_all(
        """SELECT r.name FROM roles r
           JOIN employee_roles er ON er.role_id = r.id
           WHERE er.employee_id = ? ORDER BY r.name""",
        (employee_id,),
    )
    return [r["name"] for r in rows]


def get_role_id(db: DatabaseManager, role_name: str) -> int | None:
    row = db.query_one("SELECT id FROM roles WHERE name = ?", (role_name,))
    return int(row["id"]) if row else None


def get_role_ids(db: DatabaseManager, employee_id: int) -> list[int]:
    rows = db.query_all(
        """SELECT r.id FROM roles r
           JOIN employee_roles er ON er.role_id = r.id
           WHERE er.employee_id = ? ORDER BY r.name""",
        (employee_id,),
    )
    return [int(r["id"]) for r in rows]


def list_roles(db: DatabaseManager) -> list[str]:
    rows = db.query_all("SELECT name FROM roles ORDER BY name")
    return [r["name"] for r in rows]


def set_roles(db: DatabaseManager, employee_id: int, role_ids: list[int],
              conn=None) -> None:
    """Replace an employee's roles (delete + insert). Pass the open
    transaction connection when the caller needs atomicity with an
    audit row."""
    if conn is not None:
        conn.execute("DELETE FROM employee_roles WHERE employee_id = ?",
                     (employee_id,))
        for role_id in role_ids:
            conn.execute(
                "INSERT INTO employee_roles (employee_id, role_id) VALUES (?, ?)",
                (employee_id, role_id),
            )
        return
    db.execute("DELETE FROM employee_roles WHERE employee_id = ?",
               (employee_id,))
    for role_id in role_ids:
        db.execute(
            "INSERT INTO employee_roles (employee_id, role_id) VALUES (?, ?)",
            (employee_id, role_id),
        )


def get_permission_codes(db: DatabaseManager, employee_id: int) -> set[str]:
    rows = db.query_all(
        """SELECT p.code FROM permissions p
           JOIN role_permissions rp ON rp.permission_id = p.id
           JOIN employee_roles er ON er.role_id = rp.role_id
           WHERE er.employee_id = ?""",
        (employee_id,),
    )
    return {r["code"] for r in rows}


def get_session_by_token_hash(db: DatabaseManager, token_hash: str) -> sqlite3.Row | None:
    return db.query_one("SELECT * FROM sessions WHERE token_hash = ?", (token_hash,))
