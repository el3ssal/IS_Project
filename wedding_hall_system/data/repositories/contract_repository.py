"""Contract / contract-version / attachment queries (Phase 4, data tier).

Repositories own SQL — all queries are parameterised. Contract versions
are insert-only: the service creates a new version row on every edit;
this module never updates a version body.
"""
from __future__ import annotations

import sqlite3

from ..database import DatabaseManager


# ----------------------------------------------------------------- contracts


def insert_contract(db: DatabaseManager, *, number: str, booking_id: int,
                    template_name: str, conn=None) -> int:
    # signature_status is intentionally omitted: the schema DEFAULT is
    # 'draft' (SRS §6: Draft, Pending Signature, Partially Signed,
    # Signed, Cancelled). A generated contract starts as a draft.
    sql = """INSERT INTO contracts
             (number, booking_id, template_name)
             VALUES (?, ?, ?)"""
    params = (number, booking_id, template_name)
    if conn is not None:
        return int(conn.execute(sql, params).lastrowid)
    return db.execute(sql, params)


def get_contract(db: DatabaseManager, contract_id: int, conn=None) -> dict | None:
    if conn is not None:
        row = conn.execute("SELECT * FROM contracts WHERE id = ?",
                           (contract_id,)).fetchone()
    else:
        row = db.query_one("SELECT * FROM contracts WHERE id = ?", (contract_id,))
    return dict(row) if row else None


def count_contracts_by_template(db: DatabaseManager, template_name: str) -> int:
    row = db.query_one(
        "SELECT COUNT(*) AS n FROM contracts WHERE template_name = ?",
        (template_name,))
    return int(row["n"]) if row else 0


def get_contract_by_booking(db: DatabaseManager, booking_id: int) -> dict | None:
    row = db.query_one(
        "SELECT * FROM contracts WHERE booking_id = ? ORDER BY id DESC LIMIT 1",
        (booking_id,),
    )
    return dict(row) if row else None


def list_contracts(db: DatabaseManager, *, booking_id: int | None = None,
                   signature_status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM contracts"
    clauses: list[str] = []
    params: list = []
    if booking_id is not None:
        clauses.append("booking_id = ?")
        params.append(booking_id)
    if signature_status:
        clauses.append("signature_status = ?")
        params.append(signature_status)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id DESC"
    return [dict(r) for r in db.query_all(sql, tuple(params))]


def set_current_version(db: DatabaseManager, contract_id: int, version_id: int,
                        conn=None) -> None:
    sql = "UPDATE contracts SET current_version_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
    params = (version_id, contract_id)
    if conn is not None:
        conn.execute(sql, params)
    else:
        db.execute(sql, params)


def update_signature_status(db: DatabaseManager, contract_id: int, status: str,
                            signature_date: str | None = None, conn=None) -> None:
    sql = """UPDATE contracts
             SET signature_status = ?, signature_date = ?,
                 updated_at = CURRENT_TIMESTAMP
             WHERE id = ?"""
    params = (status, signature_date or "", contract_id)
    if conn is not None:
        conn.execute(sql, params)
    else:
        db.execute(sql, params)


def count_contracts_on_day(db: DatabaseManager, day_prefix: str, conn=None) -> int:
    if conn is not None:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM contracts WHERE number LIKE ?",
            (day_prefix + "%",)).fetchone()
    else:
        row = db.query_one(
            "SELECT COUNT(*) AS n FROM contracts WHERE number LIKE ?",
            (day_prefix + "%",))
    return int(row["n"]) if row else 0


# ---------------------------------------------------------- contract versions


def insert_version(db: DatabaseManager, *, contract_id: int, version: int,
                   body: str, created_by: int | None, conn=None) -> int:
    sql = """INSERT INTO contract_versions
             (contract_id, version, body, created_by) VALUES (?, ?, ?, ?)"""
    params = (contract_id, version, body, created_by)
    if conn is not None:
        return int(conn.execute(sql, params).lastrowid)
    return db.execute(sql, params)


def get_version(db: DatabaseManager, contract_id: int, version: int) -> dict | None:
    row = db.query_one(
        "SELECT * FROM contract_versions WHERE contract_id = ? AND version = ?",
        (contract_id, version),
    )
    return dict(row) if row else None


def list_versions(db: DatabaseManager, contract_id: int) -> list[dict]:
    return [dict(r) for r in db.query_all(
        "SELECT * FROM contract_versions WHERE contract_id = ? ORDER BY version",
        (contract_id,),
    )]


def max_version(db: DatabaseManager, contract_id: int, conn=None) -> int:
    if conn is not None:
        row = conn.execute(
            "SELECT MAX(version) AS v FROM contract_versions WHERE contract_id = ?",
            (contract_id,)).fetchone()
    else:
        row = db.query_one(
            "SELECT MAX(version) AS v FROM contract_versions WHERE contract_id = ?",
            (contract_id,),
        )
    return int(row["v"]) if row and row["v"] is not None else 0


# ---------------------------------------------------------------- attachments


def insert_attachment(db: DatabaseManager, *, entity_type: str, entity_id: int,
                      original_filename: str, storage_filename: str,
                      mime_type: str, size_bytes: int, category: str,
                      uploaded_by: int | None, conn=None) -> int:
    sql = """INSERT INTO attachments
             (entity_type, entity_id, original_filename, storage_filename,
              mime_type, size_bytes, category, uploaded_by)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?)"""
    params = (entity_type, entity_id, original_filename, storage_filename,
              mime_type, size_bytes, category, uploaded_by)
    if conn is not None:
        return int(conn.execute(sql, params).lastrowid)
    return db.execute(sql, params)


def get_attachment(db: DatabaseManager, attachment_id: int) -> dict | None:
    row = db.query_one("SELECT * FROM attachments WHERE id = ? AND is_deleted = 0",
                       (attachment_id,))
    return dict(row) if row else None


def list_attachments(db: DatabaseManager, *, entity_type: str | None = None,
                     entity_id: int | None = None,
                     category: str | None = None) -> list[dict]:
    sql = "SELECT * FROM attachments WHERE is_deleted = 0"
    params: list = []
    if entity_type:
        sql += " AND entity_type = ?"
        params.append(entity_type)
    if entity_id is not None:
        sql += " AND entity_id = ?"
        params.append(entity_id)
    if category:
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY id DESC"
    return [dict(r) for r in db.query_all(sql, tuple(params))]


def soft_delete_attachment(db: DatabaseManager, attachment_id: int, conn=None) -> None:
    sql = "UPDATE attachments SET is_deleted = 1 WHERE id = ?"
    if conn is not None:
        conn.execute(sql, (attachment_id,))
    else:
        db.execute(sql, (attachment_id,))
