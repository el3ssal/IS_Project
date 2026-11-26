"""Permission tests (Phase 1, SRS §2.2, §21 test 18 in part)."""
from __future__ import annotations

from conftest import Phase1TestCase

from wedding_hall_system.logic.permissions import (
    get_employee_permissions,
    has_permission,
    require_permission,
)
from wedding_hall_system.utils.exceptions import AuthorizationError


class TestPermissionData(Phase1TestCase):
    def test_owner_has_every_permission(self):
        owner, _ = self.login_as("owner", "Owner123!")
        all_codes = {r["code"] for r in self.db.query_all("SELECT code FROM permissions")}
        assert all_codes, "no permissions seeded"
        assert get_employee_permissions(self.db, owner.id) == all_codes

    def test_staff_has_only_task_permissions(self):
        staff, _ = self.login_as("staff", "Staff123!")
        perms = get_employee_permissions(self.db, staff.id)
        assert "task.view_assigned" in perms
        assert "task.update_own" in perms
        assert "finance.view" not in perms
        assert "booking.create" not in perms

    def test_receptionist_cannot_view_profit(self):
        receptionist, _ = self.login_as("receptionist", "Reception123!")
        assert has_permission(self.db, receptionist, "booking.view_price")
        assert not has_permission(self.db, receptionist, "booking.view_profit")

    def test_accountant_can_record_payments_but_not_manage_halls(self):
        accountant, _ = self.login_as("accountant", "Account123!")
        assert has_permission(self.db, accountant, "payment.record")
        assert not has_permission(self.db, accountant, "hall.manage")

    def test_require_permission_raises_for_unauthorised_user(self):
        staff, _ = self.login_as("staff", "Staff123!")
        with self.assertRaises(AuthorizationError):
            require_permission(self.db, staff, "finance.view")

    def test_require_permission_raises_when_logged_out(self):
        with self.assertRaises(AuthorizationError):
            require_permission(self.db, None, "booking.view")

    def test_owner_can_change_role_permissions_without_code_change(self):
        # Grant Staff a permission through data, verify it takes effect.
        staff, _ = self.login_as("staff", "Staff123!")
        assert not has_permission(self.db, staff, "booking.view")
        perm = self.db.query_one("SELECT id FROM permissions WHERE code = 'booking.view'")
        role = self.db.query_one("SELECT id FROM roles WHERE name = 'Staff'")
        self.db.execute(
            "INSERT INTO role_permissions (role_id, permission_id) VALUES (?, ?)",
            (int(role["id"]), int(perm["id"])),
        )
        assert has_permission(self.db, staff, "booking.view")


if __name__ == "__main__":
    import unittest

    unittest.main()
