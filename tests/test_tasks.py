"""Phase 4 tests: task templates, event-plan generation, task assignment
(warn-only conflicts), staff status updates, and task notifications."""
from __future__ import annotations

from datetime import datetime

from tests.conftest import Phase1TestCase
from wedding_hall_system.logic.booking_service import BookingService
from wedding_hall_system.logic.customer_service import CustomerService
from wedding_hall_system.logic.notification_service import NotificationService
from wedding_hall_system.logic.staff_service import StaffService
from wedding_hall_system.logic.task_service import TaskService
from wedding_hall_system.utils.exceptions import (
    AuthenticationError,
    AuthorizationError,
    NotFoundError,
    ValidationError,
)

FRIDAY = datetime(2026, 12, 4, 18, 0)
FRIDAY_END = datetime(2026, 12, 4, 23, 0)
MONDAY = datetime(2026, 12, 7, 18, 0)
MONDAY_END = datetime(2026, 12, 7, 23, 0)

TEMPLATE_ITEMS = [
    {"name": "Setup check", "description": "Check tables and stage.",
     "due_offset_min": 180, "priority": "high"},
    {"name": "Sound check", "description": "Test microphones.",
     "due_offset_min": 120, "priority": "medium"},
]


class TestTasks(Phase1TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.login_as("owner", "Owner123!")[0]
        self.manager = self.login_as("manager", "Manager123!")[0]
        self.receptionist = self.login_as("receptionist", "Reception123!")[0]
        self.accountant = self.login_as("accountant", "Account123!")[0]
        self.staff = self.login_as("staff", "Staff123!")[0]
        self.tasks = TaskService(self.db)
        self.staff_service = StaffService(self.db)
        self.notifications = NotificationService(self.db)
        self.bookings = BookingService(self.db, self.settings)
        self.customer = next(
            c for c in CustomerService(self.db, self.settings).list_customers(self.owner)
            if c.full_name.startswith("Ahmed Hassan"))

    def _book(self, start=FRIDAY, end=FRIDAY_END, **kwargs):
        kwargs.setdefault("event_type_id", 1)
        kwargs.setdefault("package_id", 1)
        return self.bookings.create_booking(
            self.receptionist, customer_id=self.customer.id, hall_id=1,
            start_at=start, end_at=end, guests=200, **kwargs)

    def _confirmed_booking(self, **kwargs):
        booking = self._book(**kwargs)
        self.bookings.record_payment(self.accountant, booking.id, 1_320_000, "cash")
        return self.bookings.get_booking(self.owner, booking.id)

    def _plan(self, **kwargs):
        booking = self._confirmed_booking(**kwargs)
        return booking, self.tasks.generate_event_plan(self.manager, booking.id)

    # -- task templates --------------------------------------------------------
    def test_seeded_example_task_template_exists(self):
        templates = self.tasks.list_templates(self.manager)
        self.assertEqual(len(templates), 1)
        self.assertEqual(templates[0]["name"], "Wedding Day Checklist (example)")
        self.assertEqual(len(templates[0]["items"]), 4)

    def test_task_template_crud_and_permissions(self):
        created = self.tasks.create_template(
            self.manager, name="Engagement Checklist", event_type_id=None,
            package_id=None, items=TEMPLATE_ITEMS)
        self.assertEqual(created["name"], "Engagement Checklist")
        self.assertEqual(len(created["items"]), 2)
        with self.assertRaises(AuthorizationError):  # staff cannot create
            self.tasks.create_template(
                self.staff, name="Nope", event_type_id=None, package_id=None,
                items=TEMPLATE_ITEMS)
        with self.assertRaises(ValidationError):  # no items
            self.tasks.create_template(
                self.manager, name="Empty", event_type_id=None,
                package_id=None, items=[])
        with self.assertRaises(ValidationError):  # bad priority
            self.tasks.create_template(
                self.manager, name="Bad", event_type_id=None, package_id=None,
                items=[{"name": "X", "description": "", "due_offset_min": 60,
                        "priority": "critical"}])
        updated = self.tasks.update_template(
            self.manager, created["id"], name="Engagement Checklist v2",
            items=[{"name": "Only item", "description": "", "due_offset_min": 30,
                    "priority": "low"}])
        self.assertEqual(updated["name"], "Engagement Checklist v2")
        self.assertEqual(len(updated["items"]), 1)
        self.tasks.delete_template(self.manager, created["id"])
        with self.assertRaises(NotFoundError):
            self.tasks.get_template(self.manager, created["id"])

    def test_deleting_template_never_affects_generated_plans(self):
        # Generation copies items into tasks (SRS §7.2), so deleting the
        # template afterwards leaves the existing plan untouched.
        booking, plan = self._plan()
        self.tasks.delete_template(self.manager, plan["template_id"])
        with self.assertRaises(NotFoundError):
            self.tasks.get_template(self.manager, plan["template_id"])
        kept = self.tasks.get_event_plan(self.manager, booking.id)
        self.assertEqual(len(kept["tasks"]), 4)

    # -- event-plan generation ---------------------------------------------------
    def test_generate_event_plan_auto_matches_example_template(self):
        booking, plan = self._plan()
        self.assertEqual(plan["template_name"], "Wedding Day Checklist (example)")
        self.assertEqual(len(plan["tasks"]), 4)
        by_name = {t["name"]: t for t in plan["tasks"]}
        # Due = event start (18:00) minus the item offset.
        self.assertEqual(by_name["Confirm hall setup and seating"]["due_at"],
                         "2026-12-04T15:00:00")
        self.assertEqual(by_name["Sound and lighting check"]["due_at"],
                         "2026-12-04T16:00:00")
        self.assertEqual(by_name["Photographer arrival"]["due_at"],
                         "2026-12-04T17:00:00")
        self.assertEqual(by_name["Welcome guests"]["due_at"], "2026-12-04T18:00:00")
        self.assertTrue(all(t["status"] == "pending" for t in plan["tasks"]))
        # Second generation for the same booking is rejected (one plan).
        with self.assertRaises(ValidationError):
            self.tasks.generate_event_plan(self.manager, booking.id)

    def test_generate_event_plan_rejected_for_pending_booking(self):
        booking = self._book()  # still Pending
        with self.assertRaises(ValidationError):
            self.tasks.generate_event_plan(self.manager, booking.id)

    def test_generate_event_plan_from_explicit_template(self):
        template = self.tasks.create_template(
            self.manager, name="Custom", event_type_id=None, package_id=None,
            items=TEMPLATE_ITEMS)
        booking = self._confirmed_booking()
        plan = self.tasks.generate_event_plan(
            self.manager, booking.id, template_id=template["id"])
        self.assertEqual(plan["template_id"], template["id"])
        self.assertEqual(len(plan["tasks"]), 2)

    def test_generate_event_plan_no_matching_template(self):
        # A booking whose event type/package match no template: use a
        # different event type with no template at all.
        booking = self._confirmed_booking(event_type_id=2, package_id=2)
        # Remove the only template that could match by deleting it? No —
        # the example template is event-type specific (Wedding). A
        # Conference booking (event type 2) with package 2 has no match.
        with self.assertRaises(ValidationError):
            self.tasks.generate_event_plan(self.manager, booking.id)

    # -- assignment + warn-only conflicts -----------------------------------------
    def test_assign_task_happy_path_and_notification(self):
        _booking, plan = self._plan()
        task_id = plan["tasks"][0]["id"]
        assigned = self.tasks.assign_task(self.manager, task_id, self.staff.id)
        self.assertEqual(assigned["assigned_to"], self.staff.id)
        self.assertEqual(assigned["warnings"], [])
        inbox = self.notifications.inbox(self.staff)
        self.assertTrue(any(n["type"] == "task_assigned"
                            and plan["tasks"][0]["name"] in n["title"]
                            for n in inbox))
        history = self.tasks.task_history(self.manager, task_id)
        self.assertEqual(history[0]["action"], "assigned")
        self.assertEqual(history[0]["new_value"], str(self.staff.id))

    def test_assign_task_warns_on_unavailability_but_still_succeeds(self):
        _booking, plan = self._plan()
        self.staff_service.set_availability(
            self.manager, self.staff.id, date="2026-12-04",
            kind="unavailable", note="Day off")
        task_id = plan["tasks"][0]["id"]  # due 2026-12-04 15:00
        assigned = self.tasks.assign_task(self.manager, task_id, self.staff.id)
        self.assertEqual(assigned["assigned_to"], self.staff.id)  # NOT blocked
        self.assertEqual(len(assigned["warnings"]), 1)
        self.assertIn("unavailable on 2026-12-04", assigned["warnings"][0])
        # The warning is recorded in the audit log and the history.
        history = self.tasks.task_history(self.manager, task_id)
        self.assertEqual(history[0]["action"], "assigned")
        self.assertEqual(history[0]["new_value"], str(self.staff.id))
        audit = self.db.query_one(
            "SELECT description FROM audit_logs WHERE action = 'task.assign' "
            "AND entity_id = ?", (task_id,))
        self.assertIn("unavailable", audit["description"])
        # The Manager is notified of the conflict.
        inbox = self.notifications.inbox(self.manager)
        self.assertTrue(any(n["type"] == "assignment_conflict"
                            for n in inbox))

    def test_assign_task_warns_on_overlapping_assignment(self):
        template = self.tasks.create_template(
            self.manager, name="Tight", event_type_id=None, package_id=None,
            items=[{"name": "First", "description": "", "due_offset_min": 180,
                    "priority": "high"},
                   {"name": "Second", "description": "", "due_offset_min": 170,
                    "priority": "high"}])
        booking = self._confirmed_booking()
        plan = self.tasks.generate_event_plan(
            self.manager, booking.id, template_id=template["id"])
        first, second = plan["tasks"][0]["id"], plan["tasks"][1]["id"]
        self.tasks.assign_task(self.manager, first, self.staff.id)
        assigned = self.tasks.assign_task(self.manager, second, self.staff.id)
        self.assertEqual(len(assigned["warnings"]), 1)
        self.assertIn("overlapping assignment", assigned["warnings"][0])

    def test_assign_task_rejects_unknown_task_or_employee(self):
        with self.assertRaises(NotFoundError):
            self.tasks.assign_task(self.manager, 99999, self.staff.id)
        _booking, plan = self._plan()
        with self.assertRaises(NotFoundError):
            self.tasks.assign_task(self.manager, plan["tasks"][0]["id"], 99999)
        with self.assertRaises(AuthorizationError):  # receptionist cannot assign
            self.tasks.assign_task(self.receptionist, plan["tasks"][0]["id"],
                                   self.staff.id)

    # -- status updates ---------------------------------------------------------------
    def test_staff_status_updates_and_permissions(self):
        _booking, plan = self._plan()
        task_id = plan["tasks"][0]["id"]
        self.tasks.assign_task(self.manager, task_id, self.staff.id)
        task = self.tasks.update_task_status(self.staff, task_id, "in_progress")
        self.assertEqual(task.status, "in_progress")
        task = self.tasks.update_task_status(self.staff, task_id, "completed")
        self.assertEqual(task.status, "completed")
        with self.assertRaises(ValidationError):  # completed is final
            self.tasks.update_task_status(self.staff, task_id, "in_progress")
        # Someone who is neither the assignee nor a Manager cannot touch it.
        with self.assertRaises(AuthorizationError):
            self.tasks.update_task_status(self.accountant, task_id, "in_progress")

    def test_completed_task_is_final_and_cannot_be_reassigned(self):
        # The SRS has no task-cancellation workflow and the schema
        # allows only pending / in_progress / completed.
        _booking, plan = self._plan()
        task_id = plan["tasks"][0]["id"]
        self.tasks.assign_task(self.manager, task_id, self.staff.id)
        with self.assertRaises(ValidationError):  # no 'cancelled' status
            self.tasks.update_task_status(self.staff, task_id, "cancelled")
        self.tasks.update_task_status(self.staff, task_id, "completed")
        with self.assertRaises(ValidationError):  # completed is final
            self.tasks.update_task_status(self.manager, task_id, "in_progress")
        with self.assertRaises(ValidationError):  # completed cannot reassign
            self.tasks.assign_task(self.manager, task_id, self.staff.id)

    def test_update_task_details(self):
        # Manager edits the generated checklist (FR-042): rename, change
        # priority and link the task to a catalog service.
        _booking, plan = self._plan()
        task_id = plan["tasks"][0]["id"]
        edited = self.tasks.update_task_details(
            self.manager, task_id, name="Hall setup (final check)",
            priority="urgent", service_id=1)
        self.assertEqual(edited.name, "Hall setup (final check)")
        self.assertEqual(edited.priority, "urgent")
        self.assertEqual(edited.service_id, 1)
        history = self.tasks.task_history(self.manager, task_id)
        self.assertEqual(history[0]["action"], "edited")
        self.assertIn("Hall setup (final check)", history[0]["new_value"])
        with self.assertRaises(AuthorizationError):  # staff cannot edit
            self.tasks.update_task_details(
                self.staff, task_id, name="Nope")
        with self.assertRaises(ValidationError):  # bad priority
            self.tasks.update_task_details(
                self.manager, task_id, priority="critical")
        with self.assertRaises(NotFoundError):  # unknown service
            self.tasks.update_task_details(
                self.manager, task_id, service_id=99999)
        # Completed tasks are final and cannot be edited.
        self.tasks.assign_task(self.manager, task_id, self.staff.id)
        self.tasks.update_task_status(self.staff, task_id, "completed")
        with self.assertRaises(ValidationError):
            self.tasks.update_task_details(
                self.manager, task_id, name="Too late")

    def test_task_list_scoping(self):
        _booking, plan = self._plan()
        task_id = plan["tasks"][0]["id"]
        self.tasks.assign_task(self.manager, task_id, self.staff.id)
        own = self.tasks.list_tasks(self.staff)
        self.assertEqual(len(own), 1)
        self.assertEqual(own[0]["id"], task_id)
        all_tasks = self.tasks.list_tasks(self.manager)
        self.assertEqual(len(all_tasks), 4)

    # -- staff profiles / availability ---------------------------------------------------
    def test_staff_profile_and_availability(self):
        profile = self.staff_service.get_profile(self.manager, self.staff.id)
        self.assertEqual(profile["username"], "staff")
        self.assertEqual(profile["task_counts"], {"pending": 0, "in_progress": 0,
                                                  "completed": 0})
        updated = self.staff_service.update_profile(
            self.manager, self.staff.id, full_name="Staff Member",
            specialties="Photography, Setup")
        self.assertEqual(updated["full_name"], "Staff Member")
        self.assertEqual(updated["specialties"], "Photography, Setup")
        with self.assertRaises(AuthorizationError):  # receptionist cannot edit
            self.staff_service.update_profile(
                self.receptionist, self.staff.id, full_name="X")
        entry = self.staff_service.set_availability(
            self.manager, self.staff.id, date="2026-12-05",
            start_time="09:00", end_time="17:00", kind="available")
        self.assertEqual(entry["kind"], "available")
        entries = self.staff_service.list_availability(
            self.manager, target_id=self.staff.id, date="2026-12-05")
        self.assertEqual(len(entries), 1)
        # A staff member sees only their own availability.
        mine = self.staff_service.list_availability(self.staff)
        self.assertTrue(all(e["employee_id"] == self.staff.id for e in mine))

    def test_staff_roles_and_active_status(self):
        # Owner changes roles; Manager cannot; nobody changes their own.
        updated = self.staff_service.set_roles(
            self.owner, self.staff.id, ["Staff", "Receptionist"])
        self.assertEqual(sorted(updated["roles"]),
                         ["Receptionist", "Staff"])
        with self.assertRaises(AuthorizationError):
            self.staff_service.set_roles(
                self.manager, self.staff.id, ["Staff"])
        with self.assertRaises(ValidationError):
            self.staff_service.set_roles(self.owner, self.owner.id, ["Owner"])
        with self.assertRaises(ValidationError):
            self.staff_service.set_roles(self.owner, self.staff.id, [])
        with self.assertRaises(ValidationError):
            self.staff_service.set_roles(
                self.owner, self.staff.id, ["NotARole"])
        # Active status: Owner-only; a deactivated account cannot log in.
        deactivated = self.staff_service.update_profile(
            self.owner, self.staff.id, is_active=False)
        self.assertFalse(deactivated["is_active"])
        with self.assertRaises(AuthenticationError):
            self.auth.login("staff", "Staff123!")
        with self.assertRaises(AuthorizationError):  # manager cannot deactivate
            self.staff_service.update_profile(
                self.manager, self.receptionist.id, is_active=False)
        with self.assertRaises(ValidationError):  # owner cannot deactivate self
            self.staff_service.update_profile(
                self.owner, self.owner.id, is_active=False)
        reactivated = self.staff_service.update_profile(
            self.owner, self.staff.id, is_active=True)
        self.assertTrue(reactivated["is_active"])
        self.auth.login("staff", "Staff123!")  # logs in again
