"""Python data migrations (Phase 8).

SQL migrations cannot encrypt: Fernet needs Python. The Python steps
run from DatabaseManager.initialize() right after their matching SQL
migration, on the same connection, before the version is recorded —
so a failed Python step aborts the whole initialization loudly
instead of leaving a half-migrated database.
"""
from __future__ import annotations

import sqlite3

from ..utils.logging_config import get_logger

logger = get_logger(__name__)


def migrate_005_encrypt_national_ids(conn: sqlite3.Connection) -> int:
    """Encrypt every stored National ID in place (FR-014).

    Reads each plaintext value, encrypts it, writes the ciphertext
    back together with its blind index (SHA-256, for duplicate
    detection), and validates decrypt-after-encrypt on every row
    before moving on. Rows that are already encrypted are only
    (re)indexed. Raises ConfigurationError when no encryption key
    is configured and a row needs encrypting — the migration refuses
    to leave plaintext behind silently. Returns the number of rows
    newly encrypted.
    """
    from ..utils.crypto import blind_index, decrypt_text, encrypt_text, is_encrypted

    rows = conn.execute("SELECT id, national_id FROM customers").fetchall()
    newly_encrypted = 0
    for row in rows:
        value = row["national_id"] or ""
        if not value:
            continue
        if is_encrypted(value):
            cipher = value
        else:
            cipher = encrypt_text(value)
            if decrypt_text(cipher) != value:
                raise ValueError(
                    "Migration 005: decrypt-after-encrypt check failed for "
                    f"customer #{row['id']} — refusing to continue."
                )
            newly_encrypted += 1
        conn.execute(
            "UPDATE customers SET national_id = ?, national_id_hash = ? WHERE id = ?",
            (cipher, blind_index(decrypt_text(cipher)), int(row["id"])),
        )
    logger.info("Migration 005: encrypted %d National ID(s) in place.",
                newly_encrypted)
    return newly_encrypted
