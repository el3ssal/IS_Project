"""In-app notification queries (Phase 4, data tier). Repositories own
SQL — all queries are parameterised. A notification targets one employee,
one role, or both; role-targeted rows are visible to every holder of
that role (SRS §10: no per-user configuration).
"""
from __future__ import annotations

import sqlite3

from ..database import DatabaseManager


def insert_notification(db: DatabaseManager, *, recipient_role_id: int | None,
                        recipient_employee_id: int | None, type: str, title: str,
                        body: str, priority: str, entity_type: str,
                        entity_id: int | None, data_json: str | None = None,
                        conn=None) -> int:
    sql = """INSERT INTO notifications
             (recipient_role_id, recipient_employee_id, type, title, body,
              priority, entity_type, entity_id, data_json)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"""
    # data_json is NOT NULL (migration 005): rows without i18n keys
    # store '{}' ("no i18n data"), never NULL — an explicit NULL
    # would override the column DEFAULT and violate the constraint.
    params = (recipient_role_id, recipient_employee_id, type, title, body,
              priority, entity_type, entity_id,
              data_json if data_json is not None else "{}")
    if conn is not None:
        return int(conn.execute(sql, params).lastrowid)
    return db.execute(sql, params)


def list_inbox(db: DatabaseManager, *, employee_id: int, role_ids: list[int],
               unread_only: bool = False) -> list[dict]:
    """Newest first. A row is visible when it targets the employee
    directly or one of their roles."""
    # Parentheses matter: without them the unread filter would bind
    # only to the role branch (AND binds tighter than OR).
    sql = """SELECT * FROM notifications
             WHERE (recipient_employee_id = ?"""
    params: list = [employee_id]
    if role_ids:
        placeholders = ", ".join("?" for _ in role_ids)
        sql += f" OR recipient_role_id IN ({placeholders})"
        params.extend(role_ids)
    sql += ")"
    if unread_only:
        sql += " AND is_read = 0"
    sql += " ORDER BY id DESC"
    return [dict(r) for r in db.query_all(sql, tuple(params))]


def get_notification(db: DatabaseManager, notification_id: int) -> dict | None:
    row = db.query_one("SELECT * FROM notifications WHERE id = ?",
                       (notification_id,))
    return dict(row) if row else None


def exists_notification(db: DatabaseManager, *, type: str, entity_type: str,
                        entity_id: int, since_iso: str,
                        conn=None) -> bool:
    """Has a notification of this type for this record been created
    since ``since_iso``? Used to de-duplicate lazy time-based warnings
    (hold-expiring, event-approaching) that fire from read sweeps
    (SRS §10, GAP G-11). ``created_at`` is SQLite CURRENT_TIMESTAMP
    (UTC, ``YYYY-MM-DD HH:MM:SS`` with a space separator), so callers
    must pass ``since_iso`` in UTC in the same format for an exact
    24h window (A-81). The comparison additionally normalizes both
    sides with SQLite ``datetime()``: a ``T``-separated ISO value
    (``datetime.now().isoformat()``) used to compare as a *smaller*
    string than any space-separated timestamp on the same date
    (``' '`` < ``'T'``), so the dedup silently failed whenever the
    UTC date of ``created_at`` equalled the date of ``since_iso`` —
    every night 00:00–02:00 Africa/Cairo — and a duplicate warning
    was created. That format mismatch, not the 2h UTC/local skew,
    was the real cause of the duplicate notifications."""
    sql = """SELECT 1 FROM notifications
             WHERE type = ? AND entity_type = ? AND entity_id = ?
               AND datetime(created_at) >= datetime(?) LIMIT 1"""
    params = (type, entity_type, entity_id, since_iso)
    if conn is not None:
        row = conn.execute(sql, params).fetchone()
    else:
        row = db.query_one(sql, params)
    return row is not None


def mark_read(db: DatabaseManager, notification_id: int) -> None:
    db.execute("UPDATE notifications SET is_read = 1 WHERE id = ?",
               (notification_id,))


def unread_count(db: DatabaseManager, *, employee_id: int, role_ids: list[int]) -> int:
    sql = """SELECT COUNT(*) AS n FROM notifications
             WHERE is_read = 0 AND (recipient_employee_id = ?"""
    params: list = [employee_id]
    if role_ids:
        placeholders = ", ".join("?" for _ in role_ids)
        sql += f" OR recipient_role_id IN ({placeholders})"
        params.extend(role_ids)
    sql += ")"
    row = db.query_one(sql, tuple(params))
    return int(row["n"]) if row else 0
