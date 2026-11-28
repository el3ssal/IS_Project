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
from wedding_hall_system.utils.exceptions import (
    AuthorizationError,
    NotFoundError,
    ValidationError,
)

FRIDAY = datetime(2026, 12, 4, 18, 0)
FRIDAY_END = datetime(2026, 12, 4, 23, 0)
MONDAY = datetime(2026, 12, 7, 18, 0)
MONDAY_END = datetime(2026, 12, 7, 23, 0)


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


# ------------------------------------------------------------------ A-90/A-92
class TestHallDelete(Phase1TestCase):
    """A-90: permanent hall deletion, blocked by preserved records."""

    def setUp(self) -> None:
        super().setUp()
        self.owner = self.login_as("owner", "Owner123!")[0]
        self.manager = self.login_as("manager", "Manager123!")[0]
        self.receptionist = self.login_as("receptionist", "Reception123!")[0]
        self.accountant = self.login_as("accountant", "Account123!")[0]
        self.halls = HallService(self.db, self.settings)
        from wedding_hall_system.logic.booking_service import BookingService
        from wedding_hall_system.logic.catalog_service import CatalogService
        from wedding_hall_system.logic.customer_service import CustomerService
        from wedding_hall_system.logic.expense_service import ExpenseService
        from wedding_hall_system.logic.quotation_service import QuotationService
        self.bookings = BookingService(self.db, self.settings)
        self.catalog = CatalogService(self.db, self.settings)
        self.expenses = ExpenseService(self.db, self.settings)
        self.quotations = QuotationService(self.db, self.settings)
        self.customer = next(
            c for c in CustomerService(self.db, self.settings).list_customers(self.owner)
            if c.full_name.startswith("Ahmed Hassan"))
        self.main_hall = next(
            h for h in self.halls.list_halls(self.owner)
            if h.name.startswith("Main Hall"))

    def _make_hall(self, name="Delete Me Hall"):
        hall = self.halls.create_hall(
            self.owner, name=name, description="d", capacity=200,
            base_price=500_000)
        # Friday hours, so the hall can take the Friday test bookings.
        self.halls.set_weekly_hours(self.owner, hall.id, weekday=4,
                                    open_time="12:00", close_time="23:00")
        return hall

    def _book_in(self, hall_id):
        # No package: the fresh hall is not linked to the seeded
        # Classic Wedding package, so pricing uses the hall alone.
        return self.bookings.create_booking(
            self.receptionist, customer_id=self.customer.id, hall_id=hall_id,
            package_id=None, event_type_id=1, start_at=FRIDAY, end_at=FRIDAY_END,
            guests=100)

    def test_delete_hall_with_no_dependents_succeeds_and_removes_config(self):
        hall = self._make_hall()
        self.halls.add_special_hours(self.owner, hall.id, date="2026-12-25",
                                     open_time="12:00", close_time="23:00")
        self.halls.add_maintenance(
            self.owner, hall.id, type_="AC repair",
            start_at=datetime(2026, 12, 1, 9, 0),
            end_at=datetime(2026, 12, 3, 18, 0))
        self.catalog.create_pricing_rule(
            self.owner, name="Hall rule", hall_id=hall.id,
            adjustment_type="percent", adjustment_value=5)
        name = self.halls.delete_hall(self.owner, hall.id)
        self.assertEqual(name, "Delete Me Hall")
        self.assertIsNone(
            self.db.query_one("SELECT id FROM halls WHERE id = ?", (hall.id,)))
        for table in ("hall_weekly_hours", "hall_special_hours",
                      "maintenance_blocks", "pricing_rules"):
            self.assertEqual(
                self.db.query_one(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE hall_id = ?",
                    (hall.id,))["n"], 0, table)
        row = self.db.query_one(
            "SELECT * FROM audit_logs WHERE action = 'hall.deleted' "
            "AND entity_id = ?", (hall.id,))
        self.assertIsNotNone(row)

    def test_delete_blocked_by_booking(self):
        from wedding_hall_system.utils.exceptions import HallDeleteBlockedError
        hall = self._make_hall()
        self._book_in(hall.id)
        with self.assertRaises(HallDeleteBlockedError) as ctx:
            self.halls.delete_hall(self.owner, hall.id)
        self.assertEqual(ctx.exception.bookings, 1)
        self.assertEqual(ctx.exception.quotations, 0)
        # The hall is still there.
        self.assertIsNotNone(self.halls.get_hall(self.owner, hall.id))

    def test_delete_blocked_by_quotation(self):
        from wedding_hall_system.utils.exceptions import HallDeleteBlockedError
        hall = self._make_hall()
        self.quotations.create_quotation(
            self.receptionist, customer_id=self.customer.id, hall_id=hall.id,
            start_at=FRIDAY, end_at=FRIDAY_END, guests=100)
        with self.assertRaises(HallDeleteBlockedError) as ctx:
            self.halls.delete_hall(self.owner, hall.id)
        self.assertEqual(ctx.exception.quotations, 1)
        self.assertEqual(ctx.exception.bookings, 0)

    def test_delete_blocked_by_reschedule_history(self):
        # Book in hall A, confirm, reschedule to the Main Hall: the
        # booking now points at the Main Hall, but the history row
        # still names hall A — deleting A must stay blocked.
        from wedding_hall_system.utils.exceptions import HallDeleteBlockedError
        hall = self._make_hall()
        booking = self._book_in(hall.id)
        self.bookings.record_payment(self.accountant, booking.id, 1_200_000, "cash")
        self.bookings.reschedule_booking(
            self.manager, booking.id, new_start_at=MONDAY,
            new_end_at=MONDAY_END, new_hall_id=self.main_hall.id,
            reason="Moved to Main Hall")
        self.assertEqual(
            self.bookings.get_booking(self.owner, booking.id).hall_id,
            self.main_hall.id)
        with self.assertRaises(HallDeleteBlockedError) as ctx:
            self.halls.delete_hall(self.owner, hall.id)
        self.assertEqual(ctx.exception.history, 1)
        self.assertEqual(ctx.exception.bookings, 0)

    def test_delete_blocked_by_hall_expense(self):
        from wedding_hall_system.utils.exceptions import HallDeleteBlockedError
        hall = self._make_hall()
        self.expenses.record_expense(
            self.accountant, amount_piasters=100_000, date="2026-11-28",
            category="Maintenance", description="AC fix",
            scope_type="hall", scope_id=hall.id)
        with self.assertRaises(HallDeleteBlockedError) as ctx:
            self.halls.delete_hall(self.owner, hall.id)
        self.assertEqual(ctx.exception.expenses, 1)

    def test_delete_permission_denied(self):
        hall = self._make_hall()
        for emp in (self.receptionist, self.accountant):
            with self.assertRaises(AuthorizationError):
                self.halls.delete_hall(emp, hall.id)
        self.assertIsNotNone(self.halls.get_hall(self.owner, hall.id))

    def test_manager_can_delete_hall(self):
        hall = self._make_hall()
        self.assertEqual(self.halls.delete_hall(self.manager, hall.id),
                         "Delete Me Hall")

    def test_delete_unknown_hall(self):
        with self.assertRaises(NotFoundError):
            self.halls.delete_hall(self.owner, 99999)


class TestHallStatusSync(Phase1TestCase):
    """A-92: the descriptive status and the is_active flag agree."""

    def setUp(self) -> None:
        super().setUp()
        self.owner = self.login_as("owner", "Owner123!")[0]
        self.halls = HallService(self.db, self.settings)

    def _make_hall(self, name="Sync Hall", status="active"):
        return self.halls.create_hall(
            self.owner, name=name, description="d", capacity=80,
            base_price=500_000, status=status)

    def test_deactivate_active_hall_syncs_status(self):
        hall = self._make_hall()
        out = self.halls.set_hall_active(self.owner, hall.id, False)
        self.assertFalse(out.is_active)
        self.assertEqual(out.status, "inactive")

    def test_deactivate_maintenance_hall_preserves_status(self):
        hall = self._make_hall(status="maintenance")
        out = self.halls.set_hall_active(self.owner, hall.id, False)
        self.assertFalse(out.is_active)
        self.assertEqual(out.status, "maintenance")

    def test_reactivate_inactive_hall_syncs_status(self):
        hall = self._make_hall()
        self.halls.set_hall_active(self.owner, hall.id, False)
        out = self.halls.set_hall_active(self.owner, hall.id, True)
        self.assertTrue(out.is_active)
        self.assertEqual(out.status, "active")

    def test_reactivate_maintenance_hall_preserves_status(self):
        hall = self._make_hall(status="maintenance")
        self.halls.set_hall_active(self.owner, hall.id, False)
        out = self.halls.set_hall_active(self.owner, hall.id, True)
        self.assertTrue(out.is_active)
        self.assertEqual(out.status, "maintenance")

    def test_update_hall_status_syncs_flag(self):
        hall = self._make_hall()
        out = self.halls.update_hall(self.owner, hall.id, status="inactive")
        self.assertFalse(out.is_active)
        out = self.halls.update_hall(self.owner, hall.id, status="active")
        self.assertTrue(out.is_active)
        out = self.halls.update_hall(self.owner, hall.id, status="maintenance")
        self.assertTrue(out.is_active)
        self.assertEqual(out.status, "maintenance")


class TestHallDeleteUI(Phase1TestCase):
    """A-90/A-91/A-92 at handler level: two-step delete, auto-loaded
    halls list, single status column."""

    def _demo_and_token(self):
        from wedding_hall_system.ui.app import build_app
        demo = build_app(self.settings, self.db)
        _, token = self.auth.login("owner", "Owner123!")
        return demo, token

    def test_halls_table_has_single_status_column(self):
        # A-92 (correction): the نشط column is gone; the table has 6
        # columns: ID, name, capacity, base price, status, facilities.
        from tests.test_ui_rtl_theme import _find_fn
        demo, token = self._demo_and_token()
        md = _find_fn(demo, "do_refresh_halls")(token)
        lines = [ln for ln in md.splitlines() if ln.startswith("| ")]
        headers = [c.strip() for c in lines[0].strip("|").split("|")]
        self.assertEqual(len(headers), 6)
        self.assertEqual(headers[-1], "المرافق")
        self.assertIn("الحالة", headers)

    def test_delete_flow_arm_then_confirm(self):
        from tests.test_ui_rtl_theme import _find_fn
        demo, token = self._demo_and_token()
        halls = HallService(self.db, self.settings)
        owner = self.login_as("owner", "Owner123!")[0]
        hall = halls.create_hall(owner, name="UI Delete Hall", capacity=50,
                                 base_price=100_000)
        arm = _find_fn(demo, "do_arm_delete_hall")
        confirm = _find_fn(demo, "do_delete_hall")
        refresh = _find_fn(demo, "do_refresh_halls")
        confirm_upd, cancel_upd, warn = arm(token, hall.id)
        self.assertTrue(confirm_upd.get("visible"))
        self.assertTrue(cancel_upd.get("visible"))
        self.assertIn("UI Delete Hall", warn)
        md, confirm_upd, cancel_upd, warn = confirm(token, hall.id)
        self.assertFalse(confirm_upd.get("visible"))
        self.assertFalse(cancel_upd.get("visible"))
        self.assertEqual(warn, "")
        self.assertNotIn("UI Delete Hall", refresh(token))

    def test_delete_flow_cancel_leaves_hall(self):
        from tests.test_ui_rtl_theme import _find_fn
        demo, token = self._demo_and_token()
        halls = HallService(self.db, self.settings)
        owner = self.login_as("owner", "Owner123!")[0]
        hall = halls.create_hall(owner, name="UI Keep Hall", capacity=50,
                                 base_price=100_000)
        arm = _find_fn(demo, "do_arm_delete_hall")
        cancel = _find_fn(demo, "do_cancel_delete_hall")
        refresh = _find_fn(demo, "do_refresh_halls")
        arm(token, hall.id)
        confirm_upd, cancel_upd, warn = cancel()
        self.assertFalse(confirm_upd.get("visible"))
        self.assertFalse(cancel_upd.get("visible"))
        self.assertEqual(warn, "")
        self.assertIn("UI Keep Hall", refresh(token))

    def test_delete_blocked_shows_bilingual_error(self):
        from gradio.exceptions import Error as GradioError

        from tests.test_ui_rtl_theme import _find_fn
        from wedding_hall_system.logic.booking_service import BookingService
        from wedding_hall_system.logic.customer_service import CustomerService
        demo, token = self._demo_and_token()
        owner = self.login_as("owner", "Owner123!")[0]
        receptionist = self.login_as("receptionist", "Reception123!")[0]
        halls = HallService(self.db, self.settings)
        hall = halls.create_hall(owner, name="UI Blocked Hall", capacity=200,
                                 base_price=100_000)
        halls.set_weekly_hours(owner, hall.id, weekday=4,
                               open_time="12:00", close_time="23:00")
        customer = next(
            c for c in CustomerService(self.db, self.settings).list_customers(owner)
            if c.full_name.startswith("Ahmed Hassan"))
        BookingService(self.db, self.settings).create_booking(
            receptionist, customer_id=customer.id, hall_id=hall.id,
            package_id=None, event_type_id=1, start_at=FRIDAY, end_at=FRIDAY_END,
            guests=100)
        confirm = _find_fn(demo, "do_delete_hall")
        with self.assertRaises(GradioError) as ctx:
            confirm(token, hall.id)
        self.assertIn("لا يمكن حذف", str(ctx.exception))

    def test_halls_autoload_on_login_wiring(self):
        # A-91: the login/restore events chain a .then() that fills
        # the halls list; the handler itself is session-tolerant
        # (anonymous load leaves the list untouched, no error).
        from tests.test_ui_rtl_theme import _find_fn
        demo, token = self._demo_and_token()
        fns = [fn for fn in demo.fns.values()
               if (fn.get_config().get("api_name") or "").startswith(
                   "do_refresh_halls_on_login")]
        self.assertEqual(len(fns), 3)
        handler = fns[0].fn
        self.assertIn("Main Hall", handler(token))
        # Anonymous load: a bare gr.update() — the list is left
        # untouched, no error is raised.
        untouched = handler(None)
        self.assertEqual(untouched.get("__type__"), "update")
        self.assertNotIn("value", untouched)
