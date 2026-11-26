"""Console tests (Phase 1) — the instructor-visible test file (Deck S3 step 6).

Run:  python tests/test_console.py
Requires: 3 console test cases including bad input (Deck S2).
Each case prints PASS/FAIL; the script exits 0 only if all pass.
Uses a temporary database; never touches real data.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wedding_hall_system.config.settings import AppSettings
from wedding_hall_system.data.database import DatabaseManager
from wedding_hall_system.data.seed import seed
from wedding_hall_system.logic.auth_service import AuthService
from wedding_hall_system.logic.permissions import has_permission
from wedding_hall_system.utils.exceptions import ApplicationError


def run_case(name: str, fn) -> bool:
    try:
        fn()
    except AssertionError as exc:
        print(f"[FAIL] {name}: {exc}")
        return False
    except Exception as exc:  # noqa: BLE001 - console runner reports it
        print(f"[FAIL] {name}: unexpected error: {exc!r}")
        return False
    print(f"[PASS] {name}")
    return True


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db = DatabaseManager(Path(tmp) / "console_test.db")
        db.initialize()
        seed(db)
        auth = AuthService(db, AppSettings(db_path=Path(tmp) / "console_test.db"))

        def case_wrong_password_rejected() -> None:
            """Bad input: wrong password must be rejected."""
            try:
                auth.login("owner", "WrongPassword1")
            except ApplicationError:
                return
            raise AssertionError("login with a wrong password succeeded")

        def case_unknown_user_rejected() -> None:
            """Bad input: unknown username must be rejected."""
            try:
                auth.login("no_such_user", "Whatever123")
            except ApplicationError:
                return
            raise AssertionError("login with an unknown username succeeded")

        def case_owner_login_and_permission() -> None:
            """Happy path: Owner logs in and holds a permission Staff lacks."""
            employee, token = auth.login("owner", "Owner123!")
            assert employee.username == "owner", "wrong employee returned"
            assert token, "no session token returned"
            assert auth.validate_session(token) is not None, "session not valid"
            assert has_permission(db, employee, "user.manage"), "Owner lacks user.manage"
            staff, _ = auth.login("staff", "Staff123!")
            assert not has_permission(db, staff, "user.manage"), "Staff wrongly has user.manage"
            auth.logout(token)
            assert auth.validate_session(token) is None, "logout did not invalidate session"

        results = [
            run_case("1. Wrong password is rejected (bad input)", case_wrong_password_rejected),
            run_case("2. Unknown username is rejected (bad input)", case_unknown_user_rejected),
            run_case("3. Owner logs in; Owner has user.manage, Staff does not", case_owner_login_and_permission),
        ]
    passed = sum(results)
    print(f"\n{passed}/{len(results)} console test cases passed.")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
