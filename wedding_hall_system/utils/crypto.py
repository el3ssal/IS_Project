"""Field-level encryption (Phase 8, SRS §2.4, FR-014).

Sensitive customer values (National ID) are encrypted at rest with
Fernet (AES-128-CBC + HMAC-SHA256, from the vetted ``cryptography``
library — never an invented algorithm). The key lives OUTSIDE the
database: it comes from the ``WEDDING_HALL_ENCRYPTION_KEY``
environment variable and is never committed, never stored in the DB,
and never logged.

- ``encrypt_text`` / ``decrypt_text``: values are stored as
  ``enc:v1:<fernet-token>``. Empty values stay empty (nothing to
  protect). Reads tolerate legacy plaintext (no prefix) so a database
  that predates migration 005 can still be read — but new writes
  ALWAYS encrypt and raise loudly when no key is configured; there
  is no silent fallback to plaintext.
- ``blind_index``: a SHA-256 hash used ONLY for exact-match duplicate
  detection (SRS §5.2). Fernet is randomised, so the ciphertext
  itself cannot be matched; the hash lets the service find a
  duplicate National ID without ever decrypting other rows. A hash
  is not reversible, and it is never displayed.

Key management (see scripts/generate_encryption_key.py and README):
generate a key once, set the environment variable on the host that
runs the app, and back the key up somewhere safe OFF the server
(e.g. the venue's password manager). If the key is lost, encrypted
values cannot be recovered — that is the point of encryption, and
the README says so. Rotating the key requires a re-encryption
migration; there is none yet, so treat the key as long-lived.
"""
from __future__ import annotations

import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

from .exceptions import ConfigurationError

ENV_VAR = "WEDDING_HALL_ENCRYPTION_KEY"
PREFIX = "enc:v1:"

_fernet: Fernet | None = None


def generate_key() -> str:
    """Generate a fresh Fernet key (URL-safe base64, 32 bytes)."""
    return Fernet.generate_key().decode("ascii")


def get_fernet() -> Fernet:
    """Return the configured Fernet instance.

    Raises ConfigurationError (loudly, with the fix) when no key is
    configured or the configured value is not a valid Fernet key.
    Never falls back to a default key — a default key would be a
    backdoor.
    """
    global _fernet
    if _fernet is not None:
        return _fernet
    raw = os.environ.get(ENV_VAR, "").strip()
    if not raw:
        raise ConfigurationError(
            f"Field-level encryption is not configured: environment variable "
            f"{ENV_VAR} is not set. Generate a key with "
            f"`python scripts/generate_encryption_key.py` and set {ENV_VAR} "
            f"before starting the app. Encrypted customer data cannot be "
            f"written or read without it."
        )
    try:
        _fernet = Fernet(raw.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise ConfigurationError(
            f"Environment variable {ENV_VAR} is not a valid Fernet key. "
            f"Generate a fresh key with "
            f"`python scripts/generate_encryption_key.py`."
        ) from exc
    return _fernet


def reset_fernet_cache() -> None:
    """Test helper: forget the cached cipher so a new env value applies."""
    global _fernet
    _fernet = None


def is_encrypted(value: str | None) -> bool:
    return bool(value) and str(value).startswith(PREFIX)


def encrypt_text(plaintext: str | None) -> str:
    """Encrypt a value for storage. Empty values stay empty."""
    value = str(plaintext or "")
    if not value:
        return ""
    token = get_fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return PREFIX + token


def decrypt_text(stored: str | None) -> str:
    """Decrypt a stored value. Legacy plaintext (no prefix) is
    returned as-is so pre-migration rows stay readable; anything
    that looks encrypted but fails to decrypt raises
    ConfigurationError (wrong key) — never silently return garbage."""
    value = str(stored or "")
    if not value:
        return ""
    if not value.startswith(PREFIX):
        return value
    try:
        return get_fernet().decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ConfigurationError(
            f"An encrypted value could not be decrypted with the "
            f"configured {ENV_VAR}. The key is wrong or the data is "
            f"corrupt — check the key before retrying."
        ) from exc


def blind_index(value: str | None) -> str:
    """Deterministic SHA-256 hash for exact-match duplicate detection.

    The hash is one-way: it can prove two National IDs are equal, it
    can never reveal the ID, and it is never shown to any user.
    """
    value = str(value or "").strip()
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
