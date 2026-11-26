"""Authentication and sessions (Phase 1, SRS §2.3).

- Passwords: PBKDF2-HMAC-SHA256, unique 16-byte salt, 600,000 iterations.
  Never stored in plaintext; never logged.
- Sessions: secure random token; only its SHA-256 hash is stored
  server-side; 8-hour expiry; logout deletes the row.
- Lockout: 5 failed attempts -> 15-minute lock (both configurable
  via system_settings / AppSettings).
- No persistent "Remember Me": omitted deliberately (GAP G-10) — a
  session lasts for the browser session / expiry, whichever is first.
"""
from __future__ import annotations

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

    def logout(self, token: str | None) -> None:
        if not token:
            return
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

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
        audit_service.record(
            self.db, employee_id=admin.id,
            action="account.enabled" if active else "account.disabled",
            entity_type="employee", entity_id=int(row["id"]),
            description=f"Account {'enabled' if active else 'disabled'} by {admin.username}.",
        )
