"""Phase 4 tests: in-app notifications — creation, role routing, inbox
and read flow, config handling, and bad-type rejection."""
from __future__ import annotations

from tests.conftest import Phase1TestCase
from wedding_hall_system.logic.notification_service import NotificationService, notify
from wedding_hall_system.utils.exceptions import (
    AuthorizationError,
    NotFoundError,
    ValidationError,
)


class TestNotifications(Phase1TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.login_as("owner", "Owner123!")[0]
        self.manager = self.login_as("manager", "Manager123!")[0]
        self.receptionist = self.login_as("receptionist", "Reception123!")[0]
        self.staff = self.login_as("staff", "Staff123!")[0]
        self.notifications = NotificationService(self.db)

    def test_notify_employee_direct_and_inbox_read_flow(self):
        notify(self.db, type="task_assigned", recipient_employee_id=self.staff.id,
               title="Task assigned: Setup check", body="You were assigned a task.")
        self.assertEqual(self.notifications.unread_count(self.staff), 1)
        self.assertEqual(self.notifications.unread_count(self.manager), 0)
        inbox = self.notifications.inbox(self.staff)
        self.assertEqual(len(inbox), 1)
        self.assertFalse(inbox[0]["is_read"])
        self.assertEqual(inbox[0]["type"], "task_assigned")
        unread = self.notifications.inbox(self.staff, unread_only=True)
        self.assertEqual(len(unread), 1)
        self.notifications.mark_read(self.staff, inbox[0]["id"])
        self.assertEqual(self.notifications.unread_count(self.staff), 0)
        self.assertEqual(
            len(self.notifications.inbox(self.staff, unread_only=True)), 0)
        # Read items still show when asked for.
        self.assertEqual(len(self.notifications.inbox(self.staff)), 1)
        with self.assertRaises(NotFoundError):
            self.notifications.mark_read(self.staff, 99999)

    def test_notify_role_routing_and_owner_fallback(self):
        # payment_received routes to Accountant + Manager per config.
        notify(self.db, type="payment_received",
               title="Payment received", body="13,200.00 EGP received.")
        self.assertEqual(self.notifications.unread_count(self.manager), 1)
        accountant = self.login_as("accountant", "Account123!")[0]
        self.assertEqual(self.notifications.unread_count(accountant), 1)
        self.assertEqual(self.notifications.unread_count(self.receptionist), 0)
        # A type missing from the config falls back to the Owner.
        self.notifications.update_config(
            self.owner, {"task_assigned": {"roles": [], "priority": "normal"}})
        notify(self.db, type="event_approaching",
               title="Event approaching", body="An event starts tomorrow.")
        self.assertEqual(self.notifications.unread_count(self.owner), 1)
        # A type not in NOTIFICATION_TYPES is rejected.
        with self.assertRaises(ValidationError):
            notify(self.db, type="not_a_real_type", title="x", body="y")

    def test_mark_all_read_and_other_users_cannot_mark(self):
        for _ in range(3):
            notify(self.db, type="task_assigned", recipient_employee_id=self.staff.id,
                   title="T", body="B")
        self.assertEqual(self.notifications.unread_count(self.staff), 3)
        self.notifications.mark_all_read(self.staff)
        self.assertEqual(self.notifications.unread_count(self.staff), 0)
        # Marking someone else's notification as read is rejected.
        notify(self.db, type="task_assigned", recipient_employee_id=self.staff.id,
               title="T", body="B")
        note = self.notifications.inbox(self.staff)[0]
        with self.assertRaises(AuthorizationError):
            self.notifications.mark_read(self.manager, note["id"])

    def test_notification_config_round_trip(self):
        config = self.notifications.get_config(self.owner)
        self.assertIn("task_assigned", config)
        self.assertIn("contract_awaiting_signature", config)
        updated = dict(config)
        updated["task_assigned"] = {"roles": [], "priority": "high"}
        self.notifications.update_config(self.owner, updated)
        self.assertEqual(
            self.notifications.get_config(self.owner)["task_assigned"]["priority"],
            "high")
        with self.assertRaises(ValidationError):
            self.notifications.update_config(self.owner, {"bogus_type": {}})
        with self.assertRaises(AuthorizationError):  # staff cannot change config
            self.notifications.update_config(self.staff, updated)
