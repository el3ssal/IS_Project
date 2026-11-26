"""Auth tests (Phase 1, SRS §21 test 18 in part; AC-003/AC-006)."""
from __future__ import annotations

from datetime import datetime, timedelta

from conftest import Phase1TestCase

from wedding_hall_system.utils.exceptions import AuthenticationError, ValidationError


class TestPasswordHashing(Phase1TestCase):
    def test_password_is_not_stored_in_plaintext(self):
        row = self.db.query_one("SELECT * FROM employees WHERE username = 'owner'")
        assert row["password_hash"] != "Owner123!"
        assert row["password_salt"]
        assert len(row["password_hash"]) == 64  # sha256 hex

    def test_same_password_gives_different_hashes(self):
        from wedding_hall_system.data.seed import hash_password

        h1, s1 = hash_password("SamePassword1")
        h2, s2 = hash_password("SamePassword1")
        assert s1 != s2 and h1 != h2


class TestLogin(Phase1TestCase):
    def test_owner_can_log_in(self):
        employee, token = self.login_as("owner", "Owner123!")
        assert employee.username == "owner"
        assert "Owner" in employee.roles
        assert token

    def test_wrong_password_rejected(self):
        with self.assertRaises(AuthenticationError):
            self.login_as("owner", "WrongPassword1")

    def test_unknown_user_rejected(self):
        with self.assertRaises(AuthenticationError):
            self.login_as("ghost", "Whatever123")

    def test_empty_credentials_rejected(self):
        with self.assertRaises(AuthenticationError):
            self.login_as("", "")

    def test_lockout_after_five_failures(self):
        for _ in range(5):
            with self.assertRaises(AuthenticationError):
                self.login_as("receptionist", "WrongPassword1")
        # Correct password now fails too: the account is locked.
        with self.assertRaises(AuthenticationError) as ctx:
            self.login_as("receptionist", "Reception123!")
        assert "locked" in str(ctx.exception).lower()

    def test_failed_attempts_are_recorded(self):
        with self.assertRaises(AuthenticationError):
            self.login_as("owner", "WrongPassword1")
        rows = self.db.query_all(
            "SELECT * FROM login_attempts WHERE username_attempted = 'owner' AND success = 0"
        )
        assert len(rows) == 1


class TestSessions(Phase1TestCase):
    def test_session_valid_then_invalid_after_logout(self):
        _, token = self.login_as("manager", "Manager123!")
        assert self.auth.validate_session(token) is not None
        self.auth.logout(token)
        assert self.auth.validate_session(token) is None

    def test_expired_session_rejected(self):
        _, token = self.login_as("manager", "Manager123!")
        import hashlib

        token_hash = hashlib.sha256(token.encode()).hexdigest()
        past = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
        self.db.execute("UPDATE sessions SET expires_at = ? WHERE token_hash = ?", (past, token_hash))
        assert self.auth.validate_session(token) is None

    def test_unknown_token_rejected(self):
        assert self.auth.validate_session("not-a-real-token") is None


class TestPasswords(Phase1TestCase):
    def test_change_password(self):
        employee, _ = self.login_as("staff", "Staff123!")
        self.auth.change_password(employee, "Staff123!", "NewStaff123")
        # Old password no longer works; new one does.
        with self.assertRaises(AuthenticationError):
            self.login_as("staff", "Staff123!")
        self.login_as("staff", "NewStaff123")

    def test_change_password_wrong_current_rejected(self):
        employee, _ = self.login_as("staff", "Staff123!")
        with self.assertRaises(AuthenticationError):
            self.auth.change_password(employee, "WrongCurrent1", "NewStaff123")

    def test_weak_new_password_rejected(self):
        employee, _ = self.login_as("staff", "Staff123!")
        with self.assertRaises(ValidationError):
            self.auth.change_password(employee, "Staff123!", "short")

    def test_admin_reset_and_disable(self):
        owner, _ = self.login_as("owner", "Owner123!")
        self.auth.admin_reset_password(owner, "staff", "ResetStaff1")
        self.login_as("staff", "ResetStaff1")
        self.auth.set_account_active(owner, "staff", False)
        with self.assertRaises(AuthenticationError):
            self.login_as("staff", "ResetStaff1")
        self.auth.set_account_active(owner, "staff", True)
        self.login_as("staff", "ResetStaff1")

    def test_non_admin_cannot_reset_password(self):
        staff, _ = self.login_as("staff", "Staff123!")
        from wedding_hall_system.utils.exceptions import AuthorizationError

        with self.assertRaises(AuthorizationError):
            self.auth.admin_reset_password(staff, "owner", "Hacked1234")


if __name__ == "__main__":
    import unittest

    unittest.main()
