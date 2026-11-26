"""Customer / interaction queries (Phase 3, data tier).

Repositories own SQL. Services call these and never build SQL from
user input — all queries are parameterised. Duplicate detection uses
the normalized phone/email columns (SRS §5.2); the National ID value
is never returned by a duplicate search.

Phase 8 (FR-014): the National ID is encrypted at rest (Fernet, see
utils/crypto.py). Every write encrypts; every read decrypts, so
callers always see plaintext and never touch ciphertext. Duplicate
detection matches on ``national_id_hash`` (SHA-256 blind index) —
Fernet is randomised, so the ciphertext itself cannot be matched.
"""
from __future__ import annotations

from ...utils.crypto import blind_index, decrypt_text, encrypt_text
from ..database import DatabaseManager


def _decrypt_row(data: dict) -> dict:
    """Decrypt the National ID of a customer row in place and drop
    the blind-index hash (callers never need it)."""
    if "national_id" in data:
        data["national_id"] = decrypt_text(data["national_id"])
    data.pop("national_id_hash", None)
    return data


def insert_customer(db: DatabaseManager, *, full_name: str, phone: str,
                    phone_normalized: str, email: str, email_normalized: str,
                    address: str, national_id: str, notes: str,
                    conn=None) -> int:
    sql = """INSERT INTO customers
             (full_name, phone, phone_normalized, email, email_normalized,
              address, national_id, national_id_hash, notes)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"""
    params = (full_name, phone, phone_normalized, email, email_normalized,
              address, encrypt_text(national_id), blind_index(national_id),
              notes)
    if conn is not None:
        return int(conn.execute(sql, params).lastrowid)
    return db.execute(sql, params)


def update_customer(db: DatabaseManager, customer_id: int, *, full_name: str,
                    phone: str, phone_normalized: str, email: str,
                    email_normalized: str, address: str, national_id: str,
                    notes: str, conn=None) -> None:
    sql = """UPDATE customers SET full_name = ?, phone = ?, phone_normalized = ?,
             email = ?, email_normalized = ?, address = ?, national_id = ?,
             national_id_hash = ?,
             notes = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?"""
    params = (full_name, phone, phone_normalized, email, email_normalized,
              address, encrypt_text(national_id), blind_index(national_id),
              notes, customer_id)
    if conn is not None:
        conn.execute(sql, params)
        return
    db.execute(sql, params)


def get_customer(db: DatabaseManager, customer_id: int) -> dict | None:
    row = db.query_one("SELECT * FROM customers WHERE id = ?", (customer_id,))
    if row is None:
        return None
    data = _decrypt_row(dict(row))
    data["additional_phones"] = [
        r["phone"] for r in db.query_all(
            "SELECT phone FROM customer_contacts WHERE customer_id = ? ORDER BY id",
            (customer_id,),
        )
    ]
    return data


def list_customers(db: DatabaseManager, *, search: str = "") -> list[dict]:
    """List customers, optionally filtered by name/phone/email (LIKE,
    parameterised). National ID is included in the row — services mask
    it before display unless the caller may see it (FR-012)."""
    if search:
        like = f"%{search}%"
        rows = db.query_all(
            """SELECT * FROM customers
               WHERE full_name LIKE ? OR phone LIKE ? OR phone_normalized LIKE ?
                 OR email LIKE ? OR email_normalized LIKE ?
               ORDER BY full_name""",
            (like, like, like, like, like),
        )
    else:
        rows = db.query_all("SELECT * FROM customers ORDER BY full_name")
    out = []
    for row in rows:
        data = _decrypt_row(dict(row))
        data["additional_phones"] = []
        out.append(data)
    return out


def find_by_phone(db: DatabaseManager, phone_normalized: str) -> list[dict]:
    """Exact normalized-phone matches (confirmed duplicates, SRS §5.2)."""
    if not phone_normalized:
        return []
    rows = db.query_all(
        "SELECT * FROM customers WHERE phone_normalized = ? ORDER BY id",
        (phone_normalized,),
    )
    return [_decrypt_row(dict(r)) for r in rows]


def find_by_email(db: DatabaseManager, email_normalized: str) -> list[dict]:
    """Exact normalized-email matches (possible duplicates, SRS §5.2)."""
    if not email_normalized:
        return []
    rows = db.query_all(
        "SELECT * FROM customers WHERE email_normalized = ? ORDER BY id",
        (email_normalized,),
    )
    return [_decrypt_row(dict(r)) for r in rows]


def find_by_national_id(db: DatabaseManager, national_id: str) -> list[dict]:
    """Exact National ID matches (confirmed duplicates, SRS §5.2). The
    returned rows are used only to count/identify matches — services
    must never expose the ID value in a duplicate warning. Matching
    uses the blind-index hash (FR-014): the ciphertext is randomised
    and cannot be matched directly."""
    if not national_id:
        return []
    rows = db.query_all(
        "SELECT * FROM customers WHERE national_id_hash = ? AND national_id_hash != '' ORDER BY id",
        (blind_index(national_id),),
    )
    return [_decrypt_row(dict(r)) for r in rows]


def insert_contact(db: DatabaseManager, *, customer_id: int, phone: str,
                   label: str = "", conn=None) -> int:
    sql = """INSERT INTO customer_contacts (customer_id, phone, label)
             VALUES (?, ?, ?)"""
    if conn is not None:
        return int(conn.execute(sql, (customer_id, phone, label)).lastrowid)
    return db.execute(sql, (customer_id, phone, label))


def delete_contacts(db: DatabaseManager, customer_id: int, conn=None) -> None:
    if conn is not None:
        conn.execute("DELETE FROM customer_contacts WHERE customer_id = ?",
                     (customer_id,))
        return
    db.execute("DELETE FROM customer_contacts WHERE customer_id = ?", (customer_id,))


def insert_interaction(db: DatabaseManager, *, customer_id: int,
                       employee_id: int | None, type_: str, summary: str,
                       conn=None) -> int:
    sql = """INSERT INTO customer_interactions
             (customer_id, employee_id, type, summary) VALUES (?, ?, ?, ?)"""
    params = (customer_id, employee_id, type_, summary)
    if conn is not None:
        return int(conn.execute(sql, params).lastrowid)
    return db.execute(sql, params)


def list_interactions(db: DatabaseManager, customer_id: int) -> list[dict]:
    rows = db.query_all(
        """SELECT * FROM customer_interactions WHERE customer_id = ?
           ORDER BY created_at, id""",
        (customer_id,),
    )
    return [dict(r) for r in rows]
