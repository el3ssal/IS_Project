"""Phase 7 tests: calendar (SRS §9, FR-057/FR-058).

Every test uses a TEMPORARY database — tests never touch real data.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from tests.conftest import Phase1TestCase
from wedding_hall_system.logic.booking_service import BookingService
from wedding_hall_system.logic.calendar_service import CalendarService
from wedding_hall_system.logic.customer_service import CustomerService
from wedding_hall_system.logic.hall_service import HallService
from wedding_hall_system.utils.exceptions import (
    AuthorizationError,
    BookingConflictError,
    ValidationError,
)

FRIDAY = datetime(2026, 12, 4, 18, 0)       # Friday: +10% pricing rule
FRIDAY_END = datetime(2026, 12, 4, 23, 0)
SATURDAY = datetime(2026, 12, 5, 18, 0)
SATURDAY_END = datetime(2026, 12, 5, 23, 0)


class TestCalendar(Phase1TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.login_as("owner", "Owner123!")[0]
        self.manager = self.login_as("manager", "Manager123!")[0]
        self.receptionist = self.login_as("receptionist", "Reception123!")[0]
        self.accountant = self.login_as("accountant", "Account123!")[0]
        self.staff = self.login_as("staff", "Staff123!")[0]
        self.bookings = BookingService(self.db, self.settings)
        self.calendar = CalendarService(self.db, self.settings)
        self.halls = HallService(self.db, self.settings)
        self.customer = next(
            c for c in CustomerService(self.db, self.settings).list_customers(self.owner)
            if c.full_name.startswith("Ahmed Hassan"))

    def _book(self, start=FRIDAY, end=FRIDAY_END, hall_id=1, guests=200):
        return self.bookings.create_booking(
            self.receptionist, customer_id=self.customer.id, hall_id=hall_id,
            package_id=1, event_type_id=1, start_at=start, end_at=end,
            guests=guests)

    # -- list_events ----------------------------------------------------------
    def test_booking_appears_in_calendar_range(self):
        booking = self._book()
        events = self.calendar.list_events(
            self.owner, date_from="2026-12-04", date_to="2026-12-04")
        kinds = [(e["kind"], e.get("booking_id")) for e in events]
        self.assertIn(("booking", booking.id), kinds)
        event = next(e for e in events if e.get("booking_id") == booking.id)
        self.assertEqual(event["number"], booking.number)
        self.assertEqual(event["status"], "pending")
        self.assertEqual(event["total"], 4_400_000)  # Owner sees the price

    def test_booking_outside_range_not_listed(self):
        self._book()
        events = self.calendar.list_events(
            self.owner, date_from="2026-12-10", date_to="2026-12-11")
        self.assertEqual(
            [e for e in events if e["kind"] == "booking"], [])

    def test_per_hall_filter(self):
        self._book(hall_id=1)
        # Garden Hall seats 150, not 200.
        self._book(start=SATURDAY, end=SATURDAY_END, hall_id=2, guests=100)
        hall2 = self.calendar.list_events(
            self.owner, date_from="2026-12-04", date_to="2026-12-05",
            hall_id=2)
        self.assertEqual(len([e for e in hall2 if e["kind"] == "booking"]), 1)
        self.assertEqual(hall2[0]["hall"], "Garden Hall (example)")
        all_halls = self.calendar.list_events(
            self.owner, date_from="2026-12-04", date_to="2026-12-05")
        self.assertEqual(
            len([e for e in all_halls if e["kind"] == "booking"]), 2)

    def test_maintenance_and_closure_appear(self):
        self.halls.add_maintenance(
            self.manager, 1, type_="Cleaning", description="Deep clean",
            start_at=datetime(2026, 12, 6, 9, 0),
            end_at=datetime(2026, 12, 6, 12, 0))
        self.halls.add_special_hours(
            self.manager, 1, date="2026-12-07", open_time=None,
            close_time=None, is_closed=True, reason="Private holiday")
        events = self.calendar.list_events(
            self.owner, date_from="2026-12-06", date_to="2026-12-07")
        kinds = [e["kind"] for e in events]
        self.assertIn("maintenance", kinds)
        self.assertIn("closure", kinds)

    def test_price_hidden_without_permission(self):
        self._book()
        # Every seeded role with booking.view also holds
        # booking.view_price, so revoke it from the Receptionist in
        # data (the FR-002 pattern: permissions are data) and check
        # the calendar hides the price while still listing the event.
        self.db.execute(
            """DELETE FROM role_permissions
               WHERE role_id = (SELECT id FROM roles WHERE name = 'Receptionist')
                 AND permission_id = (SELECT id FROM permissions
                                      WHERE code = 'booking.view_price')""")
        events = self.calendar.list_events(
            self.receptionist, date_from="2026-12-04", date_to="2026-12-04")
        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0]["total"])
        self.assertEqual(events[0]["number"],
                         events[0]["title"].split(" — ")[0])

    def test_staff_cannot_open_calendar(self):
        with self.assertRaises(AuthorizationError):
            self.calendar.list_events(
                self.staff, date_from="2026-12-04", date_to="2026-12-04")

    def test_invalid_range_rejected(self):
        with self.assertRaises(ValidationError):
            self.calendar.list_events(
                self.owner, date_from="2026-12-05", date_to="2026-12-04")
        with self.assertRaises(ValidationError):
            self.calendar.list_events(
                self.owner, date_from="04/12/2026", date_to="2026-12-05")

    # -- detail ----------------------------------------------------------------
    def test_booking_detail_panel(self):
        booking = self._book()
        detail = self.calendar.booking_detail(self.owner, booking.id)
        self.assertEqual(detail["number"], booking.number)
        self.assertIn("Ahmed Hassan", detail["customer"])
        self.assertEqual(detail["hall"], "Main Hall (example)")
        self.assertEqual(detail["status"], "pending")
        self.assertEqual(detail["guests"], 200)
        self.assertEqual(detail["total"], 4_400_000)
        self.assertEqual(detail["paid"], 0)
        self.assertEqual(detail["outstanding"], 4_400_000)

    def test_detail_unknown_booking(self):
        with self.assertRaises(ValidationError):
            self.calendar.booking_detail(self.owner, 99999)

    # -- reschedule from calendar (FR-058) --------------------------------------
    def test_reschedule_from_calendar_uses_same_validation(self):
        booking = self._book()
        other = self._book(start=SATURDAY, end=SATURDAY_END, hall_id=1)
        # Moving the first booking onto the second one's slot must fail
        # exactly like the manual reschedule (same workflow).
        with self.assertRaises(BookingConflictError):
            self.calendar.reschedule_from_calendar(
                self.manager, booking.id,
                new_start_at=SATURDAY, new_end_at=SATURDAY_END,
                reason="Moved from calendar")
        # And a free slot works.
        moved = self.calendar.reschedule_from_calendar(
            self.manager, booking.id,
            new_start_at=datetime(2026, 12, 7, 18, 0),
            new_end_at=datetime(2026, 12, 7, 23, 0),
            reason="Moved from calendar")
        self.assertEqual(moved.start_at, datetime(2026, 12, 7, 18, 0))
        self.assertEqual(other.status, "pending")  # untouched

    def test_reschedule_from_calendar_needs_permission(self):
        booking = self._book()
        with self.assertRaises(AuthorizationError):
            self.calendar.reschedule_from_calendar(
                self.receptionist, booking.id,
                new_start_at=datetime(2026, 12, 7, 18, 0),
                new_end_at=datetime(2026, 12, 7, 23, 0),
                reason="No permission")
