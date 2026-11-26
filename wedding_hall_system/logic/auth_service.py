"""Authentication and sessions (Phase 1, SRS §2.3; Remember Me Phase 8).

- Passwords: PBKDF2-HMAC-SHA256, unique 16-byte salt, 600,000 iterations.
  Never stored in plaintext; never logged.
- Sessions: secure random token; only its SHA-256 hash is stored
  server-side; 8-hour expiry; logout deletes the row.
- Remember Me (Phase 8, FR-011): a separate persistent token, also
  random and stored only as a SHA-256 hash, 30-day expiry, rotated on
  every use, at most 3 live tokens per employee, revoked on logout,
  password change/reset and account disable. The raw token lives only
  in the browser's localStorage (gr.BrowserState) — never in a URL,
  never in a log, and never the password. A stolen token is therefore
  single-purpose and short-lived, and rotation invalidates it after
  one use by its owner.
- Lockout: 5 failed attempts -> 15-minute lock (both configurable
  via system_settings / AppSettings).
"""

from __future__ import annotations

REMEMBER_ME_MAX_TOKENS = 3

import hashlib
import secrets
from datetime import datetime, timedelta

from ..config.settings import AppSettings
from ..data.database import DatabaseManager
from ..data.repositories import employee_repository
from ..data.seed import HASH_ALGORITHM, hash_password
from ..utils.exceptions import AuthenticationError, ValidationError
from ..utils.logging_config import get_logger
from ..utils.validators import validate_password_strength, validate_username
from . import audit_service
from .models import Employee
from .permissions import get_employee_roles

logger = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class AuthService:
    def __init__(self, db: DatabaseManager, settings: AppSettings | None = None) -> None:
        self.db = db
        self.settings = settings or AppSettings.from_env()

    # -- password helpers ------------------------------------------------
    def _verify(self, password: str, salt_hex: str, expected_hash_hex: str) -> bool:
        digest = hashlib.pbkdf2_hmac(
            HASH_ALGORITHM, password.encode("utf-8"),
            bytes.fromhex(salt_hex), self.settings.password_hash_iterations,
        )
        return secrets.compare_digest(digest.hex(), expected_hash_hex)

    @staticmethod
    def _row_to_employee(row, roles: list[str]) -> Employee:
        return Employee(
            id=int(row["id"]), username=row["username"],
            full_name=row["full_name"], phone=row["phone"] or "",
            email=row["email"] or "", is_active=bool(row["is_active"]),
            roles=roles,
        )

    # -- login / logout / sessions ----------------------------------------
    def login(self, username: str, password: str) -> tuple[Employee, str]:
        """Validate credentials; return (employee, session_token).

        Raises AuthenticationError with a generic message on any failure
        (do not reveal whether the username exists).
        """
        username = validate_username(username) if username and username.strip() else ""
        if not username or not password:
            raise AuthenticationError("Invalid username or password.")
        row = employee_repository.get_by_username(self.db, username)
        now = datetime.now()

        def _fail(reason: str) -> None:
            with self.db.transaction() as conn:
                conn.execute(
                    "INSERT INTO login_attempts (employee_id, username_attempted, success)"
                    " VALUES (?, ?, 0)",
                    (int(row["id"]) if row else None, username),
                )
                if row:
                    attempts = int(row["failed_attempts"]) + 1
                    locked_until = None
                    if attempts >= self.settings.max_failed_logins:
                        locked_until = (now + timedelta(minutes=self.settings.lockout_minutes)).isoformat(timespec="seconds")
                        audit_service.record(
                            self.db, employee_id=int(row["id"]), action="account.locked",
                            entity_type="employee", entity_id=int(row["id"]),
                            description=f"Account locked after {attempts} failed logins.",
                            conn=conn,
                        )
                    conn.execute(
                        "UPDATE employees SET failed_attempts = ?, locked_until = ? WHERE id = ?",
                        (attempts, locked_until, int(row["id"])),
                    )
            logger.warning("Failed login for username=%s (%s)", username, reason)
            raise AuthenticationError("Invalid username or password.")

        if row is None:
            _fail("unknown user")
        if not row["is_active"]:
            _fail("disabled account")
        locked_until = _parse_iso(row["locked_until"])
        if locked_until and now < locked_until:
            raise AuthenticationError(
                f"Account is locked until {locked_until:%Y-%m-%d %H:%M}. Please try again later."
            )
        if not self._verify(password, row["password_salt"], row["password_hash"]):
            _fail("wrong password")

        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        expires_at = (now + timedelta(hours=self.settings.session_expiry_hours)).isoformat(timespec="seconds")
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE employees SET failed_attempts = 0, locked_until = NULL WHERE id = ?",
                (int(row["id"]),),
            )
            conn.execute(
                "INSERT INTO login_attempts (employee_id, username_attempted, success) VALUES (?, ?, 1)",
                (int(row["id"]), username),
            )
            conn.execute(
                "INSERT INTO sessions (employee_id, token_hash, expires_at) VALUES (?, ?, ?)",
                (int(row["id"]), token_hash, expires_at),
            )
        roles = get_employee_roles(self.db, int(row["id"]))
        employee = self._row_to_employee(row, roles)
        logger.info("Login successful for username=%s roles=%s", username, roles)
        return employee, token

    def validate_session(self, token: str | None) -> Employee | None:
        """Return the employee for a valid, unexpired session token, else None."""
        if not token:
            return None
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        row = employee_repository.get_session_by_token_hash(self.db, token_hash)
        if row is None:
            return None
        expires_at = _parse_iso(row["expires_at"])
        if expires_at is None or datetime.now() >= expires_at:
            self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            return None
        emp = employee_repository.get_by_id(self.db, int(row["employee_id"]))
        if emp is None or not emp["is_active"]:
            return None
        return self._row_to_employee(emp, get_employee_roles(self.db, int(emp["id"])))

    def create_session(self, employee: Employee) -> str:
        """Create a fresh session token for an already-authenticated
        employee (Remember Me restore, FR-011). The caller must
        already have verified the employee's identity — this method
        performs no credential check."""
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        expires_at = (datetime.now() + timedelta(
            hours=self.settings.session_expiry_hours)).isoformat(timespec="seconds")
        self.db.execute(
            "INSERT INTO sessions (employee_id, token_hash, expires_at)"
            " VALUES (?, ?, ?)",
            (int(employee.id), token_hash, expires_at),
        )
        return token

    def logout(self, token: str | None) -> None:
        if not token:
            return
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    # -- Remember Me (Phase 8, FR-011) --------------------------------------
    def issue_remember_token(self, employee: Employee) -> str:
        """Issue a persistent login token for ``employee`` and return
        the raw token (the caller stores it in the browser only).
        At most REMEMBER_ME_MAX_TOKENS live tokens per employee; the
        oldest extras are revoked. Issuance is audited (no token value
        is ever logged or stored in the audit row)."""
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        expires_at = (datetime.now() + timedelta(days=self.settings.remember_me_days)).isoformat(timespec="seconds")
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO remember_me_tokens (employee_id, token_hash, expires_at)"
                " VALUES (?, ?, ?)",
                (int(employee.id), token_hash, expires_at),
            )
            conn.execute(
                """DELETE FROM remember_me_tokens WHERE employee_id = ?
                   AND id NOT IN (
                       SELECT id FROM remember_me_tokens
                       WHERE employee_id = ? ORDER BY id DESC LIMIT ?)""",
                (int(employee.id), int(employee.id), REMEMBER_ME_MAX_TOKENS),
            )
        audit_service.record(
            self.db, employee_id=int(employee.id), action="auth.remember_issued",
            entity_type="employee", entity_id=int(employee.id),
            description="Remember Me token issued (30-day expiry).",
        )
        return token

    def validate_remember_token(self, token: str | None) -> tuple[Employee | None, str | None]:
        """Validate a persistent token. On success the token is ROTATED
        (old row deleted, fresh token issued) and (employee, new_token)
        is returned. On any failure (unknown/expired/disabled) the row
        is removed and (None, None) is returned. The raw token is
        never logged."""
        if not token:
            return None, None
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        row = self.db.query_one(
            "SELECT * FROM remember_me_tokens WHERE token_hash = ?", (token_hash,))
        if row is None:
            return None, None
        expires_at = _parse_iso(row["expires_at"])
        if expires_at is None or datetime.now() >= expires_at:
            self.db.execute(
                "DELETE FROM remember_me_tokens WHERE token_hash = ?", (token_hash,))
            return None, None
        emp = employee_repository.get_by_id(self.db, int(row["employee_id"]))
        if emp is None or not emp["is_active"]:
            self.db.execute(
                "DELETE FROM remember_me_tokens WHERE token_hash = ?", (token_hash,))
            return None, None
        employee = self._row_to_employee(emp, get_employee_roles(self.db, int(emp["id"])))
        # Rotate: the presented token dies, a fresh one is issued.
        self.db.execute(
            "DELETE FROM remember_me_tokens WHERE token_hash = ?", (token_hash,))
        new_token = self.issue_remember_token(employee)
        return employee, new_token

    def revoke_remember_token(self, token: str | None) -> None:
        """Revoke one persistent token (logout). No-op for empty/unknown."""
        if not token:
            return
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        row = self.db.query_one(
            "SELECT employee_id FROM remember_me_tokens WHERE token_hash = ?",
            (token_hash,))
        self.db.execute(
            "DELETE FROM remember_me_tokens WHERE token_hash = ?", (token_hash,))
        if row is not None:
            audit_service.record(
                self.db, employee_id=int(row["employee_id"]),
                action="auth.remember_revoked", entity_type="employee",
                entity_id=int(row["employee_id"]),
                description="Remember Me token revoked (logout).",
            )

    def revoke_all_remember_tokens(self, employee_id: int) -> None:
        """Revoke every persistent token of one employee (password
        change/reset, account disable)."""
        self.db.execute(
            "DELETE FROM remember_me_tokens WHERE employee_id = ?", (int(employee_id),))
        audit_service.record(
            self.db, employee_id=int(employee_id), action="auth.remember_revoked",
            entity_type="employee", entity_id=int(employee_id),
            description="All Remember Me tokens revoked (credential change).",
        )

    # -- password management ------------------------------------------------
    def change_password(self, employee: Employee, old_password: str, new_password: str) -> None:
        if employee.id is None:
            raise AuthenticationError("You must be logged in to change your password.")
        validate_password_strength(new_password)
        row = employee_repository.get_by_id(self.db, employee.id)
        if row is None or not self._verify(old_password, row["password_salt"], row["password_hash"]):
            raise AuthenticationError("Your current password is incorrect.")
        pw_hash, salt = hash_password(new_password, iterations=self.settings.password_hash_iterations)
        self.db.execute(
            "UPDATE employees SET password_hash = ?, password_salt = ?, updated_at = ? WHERE id = ?",
            (pw_hash, salt, _now_iso(), employee.id),
        )
        # A password change invalidates every persistent login (FR-011).
        self.revoke_all_remember_tokens(int(employee.id))
        audit_service.record(
            self.db, employee_id=employee.id, action="password.changed",
            entity_type="employee", entity_id=employee.id,
            description="Password changed by the account holder.",
        )

    def admin_reset_password(self, admin: Employee, username: str, new_password: str) -> None:
        """Authorised reset (Owner/Manager with user.manage). The admin must
        already have passed require_permission in the caller/UI."""
        from .permissions import require_permission

        require_permission(self.db, admin, "user.manage")
        validate_password_strength(new_password)
        target = validate_username(username)
        row = employee_repository.get_by_username(self.db, target)
        if row is None:
            raise ValidationError(f"No account found for username '{target}'.")
        pw_hash, salt = hash_password(new_password, iterations=self.settings.password_hash_iterations)
        self.db.execute(
            "UPDATE employees SET password_hash = ?, password_salt = ?, failed_attempts = 0,"
            " locked_until = NULL, updated_at = ? WHERE id = ?",
            (pw_hash, salt, _now_iso(), int(row["id"])),
        )
        self.revoke_all_remember_tokens(int(row["id"]))
        audit_service.record(
            self.db, employee_id=admin.id, action="password.reset",
            entity_type="employee", entity_id=int(row["id"]),
            description=f"Password reset by {admin.username}.",
        )

    def set_account_active(self, admin: Employee, username: str, active: bool) -> None:
        from .permissions import require_permission

        require_permission(self.db, admin, "user.manage")
        target = validate_username(username)
        row = employee_repository.get_by_username(self.db, target)
        if row is None:
            raise ValidationError(f"No account found for username '{target}'.")
        self.db.execute(
            "UPDATE employees SET is_active = ?, updated_at = ? WHERE id = ?",
            (1 if active else 0, _now_iso(), int(row["id"])),
        )
        if not active:
            self.revoke_all_remember_tokens(int(row["id"]))
        audit_service.record(
            self.db, employee_id=admin.id,
            action="account.enabled" if active else "account.disabled",
            entity_type="employee", entity_id=int(row["id"]),
            description=f"Account {'enabled' if active else 'disabled'} by {admin.username}.",
        )
