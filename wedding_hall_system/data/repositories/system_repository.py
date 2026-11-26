"""System settings / audit queries (Phase 1, data tier)."""
from __future__ import annotations

import sqlite3

from ..database import DatabaseManager


def get_setting(db: DatabaseManager, key: str, default: str = "") -> str:
    row = db.query_one("SELECT value FROM system_settings WHERE key = ?", (key,))
    return row["value"] if row else default


def set_setting(db: DatabaseManager, key: str, value: str) -> None:
    db.execute(
        """INSERT INTO system_settings (key, value) VALUES (?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        (key, value),
    )


def delete_setting(db: DatabaseManager, key: str) -> None:
    db.execute("DELETE FROM system_settings WHERE key = ?", (key,))


def list_settings_by_prefix(db: DatabaseManager, prefix: str) -> list[dict]:
    rows = db.query_all(
        "SELECT key, value FROM system_settings WHERE key LIKE ? ORDER BY key",
        (prefix + "%",),
    )
    return [dict(r) for r in rows]


def list_audit(db: DatabaseManager, *, limit: int = 100) -> list[sqlite3.Row]:
    return db.query_all(
        "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)
    )
