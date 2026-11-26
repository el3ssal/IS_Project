"""Phase 2 tests: halls, hours, maintenance, event types, availability.

Uses the seeded EXAMPLE catalog (Main Hall / Garden Hall, 12:00–23:00
daily). Dates: 2026-12-04 is a Friday, 2026-12-05 a Saturday,
2026-12-07 a Monday.
"""
from __future__ import annotations

from datetime import datetime

from tests.conftest import Phase1TestCase
from wedding_hall_system.logic.availability_service import AvailabilityService
from wedding_hall_system.logic.hall_service import HallService
from wedding_hall_system.utils.exceptions import AuthorizationError, ValidationError

FRIDAY = datetime(2026, 12, 4, 18, 0)
FRIDAY_END = datetime(2026, 12, 4, 23, 0)


class TestHalls(Phase1TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.login_as("owner", "Owner123!")[0]
        self.manager = self.login_as("manager", "Manager123!")[0]
        self.receptionist = self.login_as("receptionist", "Reception123!")[0]
        self.halls = HallService(self.db, self.settings)
        self.availability = AvailabilityService(self.db, self.settings)
        self.main_hall = next(
            h for h in self.halls.list_halls(self.owner) if h.name.startswith("Main Hall"))

    # -- hall CRUD ----------------------------------------------------------
    def test_hall_crud_round_trip(self):
        hall = self.halls.create_hall(
            self.owner, name="Test Hall", description="d", capacity=80,
            base_price=500_000, facilities=["AC"])
        self.assertEqual(hall.capacity, 80)
        fetched = self.halls.get_hall(self.owner, hall.id)
        self.assertEqual(fetched.name, "Test Hall")
        updated = self.halls.update_hall(self.owner, hall.id, capacity=90)
        self.assertEqual(updated.capacity, 90)
        deactivated = self.halls.set_hall_active(self.owner, hall.id, False)
        self.assertFalse(deactivated.is_active)
        reactivated = self.halls.set_hall_active(self.owner, hall.id, True)
        self.assertTrue(reactivated.is_active)
        names = [h.name for h in self.halls.list_halls(self.owner)]
        self.assertIn("Test Hall", names)

    def test_duplicate_hall_name_rejected(self):
        with self.assertRaises(ValidationError):
            self.halls.create_hall(self.owner, name="Main Hall (example)",
                                   capacity=10, base_price=0)

    def test_invalid_hall_rejected(self):
        with self.assertRaises(ValidationError):
            self.halls.create_hall(self.owner, name="Bad", capacity=0, base_price=0)
        with self.assertRaises(ValidationError):
            self.halls.create_hall(self.owner, name="Bad", capacity=10,
                                   base_price=-1)
        with self.assertRaises(ValidationError):
            self.halls.create_hall(self.owner, name="Bad", capacity=10,
                                   base_price=0, status="bogus")

    def test_receptionist_cannot_manage_halls(self):
        with self.assertRaises(AuthorizationError):
            self.halls.create_hall(self.receptionist, name="Nope",
                                   capacity=10, base_price=0)
        with self.assertRaises(AuthorizationError):
            self.halls.set_hall_active(self.receptionist, self.main_hall.id, False)

    def test_manager_can_manage_halls(self):
        hall = self.halls.create_hall(self.manager, name="Manager Hall",
                                      capacity=50, base_price=100_000)
        self.assertIsNotNone(hall.id)

    def test_seeded_example_halls_exist(self):
        names = [h.name for h in self.halls.list_halls(self.owner)]
        self.assertIn("Main Hall (example)", names)
        self.assertIn("Garden Hall (example)", names)
        self.assertEqual(self.main_hall.capacity, 400)

    # -- hours / availability ----------------------------------------------
    def test_available_inside_opening_hours(self):
        result = self.availability.check_availability(
            self.owner, hall_id=self.main_hall.id, start_at=FRIDAY, end_at=FRIDAY_END)
        self.assertTrue(result.available, result.reason)

    def test_outside_opening_hours_rejected(self):
        result = self.availability.check_availability(
            self.owner, hall_id=self.main_hall.id,
            start_at=datetime(2026, 12, 4, 8, 0), end_at=datetime(2026, 12, 4, 10, 0))
        self.assertFalse(result.available)
        self.assertIn("opening hours", result.reason)

    def test_event_spanning_midnight_rejected(self):
        result = self.availability.check_availability(
            self.owner, hall_id=self.main_hall.id,
            start_at=datetime(2026, 12, 4, 22, 0), end_at=datetime(2026, 12, 5, 1, 0))
        self.assertFalse(result.available)

    def test_multi_interval_day_event_must_fit_one_interval(self):
        hall = self.halls.create_hall(self.owner, name="Split Hall",
                                      capacity=50, base_price=100_000)
        self.halls.set_weekly_hours(self.owner, hall.id, weekday=0,
                                    open_time="12:00", close_time="15:00")
        self.halls.set_weekly_hours(self.owner, hall.id, weekday=0,
                                    open_time="18:00", close_time="23:00")
        spanning = self.availability.check_availability(
            self.owner, hall_id=hall.id,
            start_at=datetime(2026, 12, 7, 14, 0), end_at=datetime(2026, 12, 7, 19, 0))
        self.assertFalse(spanning.available)
        inside = self.availability.check_availability(
            self.owner, hall_id=hall.id,
            start_at=datetime(2026, 12, 7, 18, 30), end_at=datetime(2026, 12, 7, 20, 0))
        self.assertTrue(inside.available, inside.reason)

    def test_special_closure_blocks_booking(self):
        self.halls.add_special_hours(self.owner, self.main_hall.id,
                                     date="2026-12-04", open_time=None,
                                     close_time=None, is_closed=True,
                                     reason="Holiday")
        result = self.availability.check_availability(
            self.owner, hall_id=self.main_hall.id, start_at=FRIDAY, end_at=FRIDAY_END)
        self.assertFalse(result.available)
        self.assertIn("closed", result.reason)

    def test_special_opening_narrows_hours(self):
        self.halls.add_special_hours(self.owner, self.main_hall.id,
                                     date="2026-12-07", open_time="14:00",
                                     close_time="18:00", is_closed=False,
                                     reason="Short day")
        early = self.availability.check_availability(
            self.owner, hall_id=self.main_hall.id,
            start_at=datetime(2026, 12, 7, 12, 0), end_at=datetime(2026, 12, 7, 13, 0))
        self.assertFalse(early.available)
        inside = self.availability.check_availability(
            self.owner, hall_id=self.main_hall.id,
            start_at=datetime(2026, 12, 7, 15, 0), end_at=datetime(2026, 12, 7, 17, 0))
        self.assertTrue(inside.available, inside.reason)

    def test_maintenance_blocks_and_buffer(self):
        self.halls.add_maintenance(
            self.owner, self.main_hall.id, type_="AC repair",
            start_at=datetime(2026, 12, 4, 17, 0), end_at=datetime(2026, 12, 4, 20, 0))
        overlapping = self.availability.check_availability(
            self.owner, hall_id=self.main_hall.id,
            start_at=datetime(2026, 12, 4, 19, 0), end_at=datetime(2026, 12, 4, 20, 0))
        self.assertFalse(overlapping.available)
        self.assertIn("Maintenance", overlapping.reason)
        # 21:00 start: 60-min buffer starts exactly at 20:00, when the
        # maintenance ends (half-open) — available.
        after = self.availability.check_availability(
            self.owner, hall_id=self.main_hall.id,
            start_at=datetime(2026, 12, 4, 21, 0), end_at=datetime(2026, 12, 4, 22, 30))
        self.assertTrue(after.available, after.reason)

    def test_inactive_hall_unavailable(self):
        self.halls.set_hall_active(self.owner, self.main_hall.id, False)
        result = self.availability.check_availability(
            self.owner, hall_id=self.main_hall.id, start_at=FRIDAY, end_at=FRIDAY_END)
        self.assertFalse(result.available)
        self.assertIn("inactive", result.reason)

    def test_maintenance_status_hall_unavailable(self):
        self.halls.update_hall(self.owner, self.main_hall.id, status="maintenance")
        result = self.availability.check_availability(
            self.owner, hall_id=self.main_hall.id, start_at=FRIDAY, end_at=FRIDAY_END)
        self.assertFalse(result.available)

    def test_validate_event_window(self):
        minutes = self.availability.validate_event_window(
            self.owner, hall=self.main_hall, start_at=FRIDAY, end_at=FRIDAY_END,
            guests=100, package_min_duration_min=180)
        self.assertEqual(minutes, 300)
        with self.assertRaises(ValidationError):
            self.availability.validate_event_window(
                self.owner, hall=self.main_hall, start_at=FRIDAY,
                end_at=datetime(2026, 12, 4, 19, 0), guests=100,
                package_min_duration_min=180)
        with self.assertRaises(ValidationError):
            self.availability.validate_event_window(
                self.owner, hall=self.main_hall, start_at=FRIDAY, end_at=FRIDAY_END,
                guests=401, package_min_duration_min=180)

    # -- event types ----------------------------------------------------------
    def test_event_type_crud_and_deactivation(self):
        created = self.halls.create_event_type(self.owner, name="Test Type",
                                               default_duration_min=90)
        self.assertEqual(created.default_duration_min, 90)
        updated = self.halls.update_event_type(self.owner, created.id, is_active=False)
        self.assertFalse(updated.is_active)
        names = [t.name for t in self.halls.list_event_types(self.owner)]
        self.assertIn("Test Type", names)
        active_names = [t.name for t in self.halls.list_event_types(
            self.owner, include_inactive=False)]
        self.assertNotIn("Test Type", active_names)

    def test_seeded_event_types_exist(self):
        names = [t.name for t in self.halls.list_event_types(self.owner)]
        for expected in ("Wedding", "Engagement", "Birthday", "Graduation", "Private Events"):
            self.assertIn(expected, names)

    def test_receptionist_cannot_manage_event_types(self):
        with self.assertRaises(AuthorizationError):
            self.halls.create_event_type(self.receptionist, name="Nope")
