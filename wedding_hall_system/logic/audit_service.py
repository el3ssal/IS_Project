"""Audit log (Phase 1, minimal).

Phase 1 records security-relevant events (failed logins, lockouts,
password changes, role/permission changes). Later phases reuse
``record`` for every critical business change, writing the audit row
in the SAME transaction as the change (SRS §12.1).
"""
from __future__ import annotations

import json

from ..data.database import DatabaseManager
from ..utils.logging_config import get_logger

logger = get_logger(__name__)


def record(db: DatabaseManager, *, employee_id: int | None, action: str,
           entity_type: str, entity_id: int | None = None,
           old_value: dict | None = None, new_value: dict | None = None,
           description: str = "", conn=None) -> int:
    """Append one audit row. Never logs secrets — callers must not pass
    passwords, tokens or full National ID values in old/new values.
    Pass ``conn`` to write the row inside the caller's transaction
    (required when the caller already holds a write lock)."""
    sql = """INSERT INTO audit_logs
           (employee_id, action, entity_type, entity_id,
            old_value_json, new_value_json, description)
           VALUES (?, ?, ?, ?, ?, ?, ?)"""
    params = (
        employee_id, action, entity_type, entity_id,
        json.dumps(old_value or {}, ensure_ascii=False),
        json.dumps(new_value or {}, ensure_ascii=False),
        description,
    )
    if conn is not None:
        cur = conn.execute(sql, params)
        return int(cur.lastrowid)
    return db.execute(sql, params)


def search(db: DatabaseManager, employee, *, employee_id: int | None = None,
           action: str | None = None, entity_type: str | None = None,
           entity_id: int | None = None, date_from: str | None = None,
           date_to: str | None = None, page: int = 1,
           page_size: int = 20) -> dict:
    """Audit search/filter (Phase 6, SRS §12.1, FR-062): by user, date,
    entity and action. Read-only — ordinary users cannot edit or delete
    entries (there is no such operation anywhere). Old/new JSON is
    returned as stored; callers never put secrets in it (see record)."""
    from ..data.repositories import search_repository as repo
    from .permissions import require_permission

    require_permission(db, employee, "audit.view")
    result = repo.search_audit(
        db, employee_id=employee_id, action=action, entity_type=entity_type,
        entity_id=entity_id, date_from=date_from, date_to=date_to,
        page=page, page_size=page_size)
    result["rows"] = [{
        "id": r["id"], "employee_id": r["employee_id"],
        "action": r["action"], "entity_type": r["entity_type"],
        "entity_id": r["entity_id"], "old_value": r["old_value_json"],
        "new_value": r["new_value_json"], "description": r["description"],
        "created_at": r["created_at"],
    } for r in result["rows"]]
    return result
