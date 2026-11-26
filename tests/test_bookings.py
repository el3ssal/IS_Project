"""Phase 3 tests: bookings — lifecycle, holds, deposits, buffer time,
double-booking prevention (incl. a concurrency test), timeline + audit.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta

from tests.conftest import Phase1TestCase
from wedding_hall_system.data.database import DatabaseManager
from wedding_hall_system.logic.auth_service import AuthService
from wedding_hall_system.logic.booking_service import BookingService
from wedding_hall_system.logic.customer_service import CustomerService
from wedding_hall_system.logic.models import ServiceSelection
from wedding_hall_system.utils.exceptions import (
    AuthorizationError,
    BookingConflictError,
    NotFoundError,
    PaymentError,
    ValidationError,
)

FRIDAY = datetime(2026, 12, 4, 18, 0)       # Friday: +10% pricing rule
FRIDAY_END = datetime(2026, 12, 4, 23, 0)
MONDAY = datetime(2026, 12, 7, 18, 0)
MONDAY_END = datetime(2026, 12, 7, 23, 0)
MONDAY_MID = datetime(2026, 12, 7, 12, 0)
MONDAY_MID_END = datetime(2026, 12, 7, 17, 0)


class TestBookings(Phase1TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.login_as("owner", "Owner123!")[0]
        self.manager = self.login_as("manager", "Manager123!")[0]
        self.receptionist = self.login_as("receptionist", "Reception123!")[0]
        self.accountant = self.login_as("accountant", "Account123!")[0]
        self.staff = self.login_as("staff", "Staff123!")[0]
        self.bookings = BookingService(self.db, self.settings)
        self.customer = next(
            c for c in CustomerService(self.db, self.settings).list_customers(self.owner)
            if c.full_name.startswith("Ahmed Hassan"))

    def _book(self, start=FRIDAY, end=FRIDAY_END, hall_id=1, guests=200,
              **kwargs):
        return self.bookings.create_booking(
            self.receptionist, customer_id=self.customer.id, hall_id=hall_id,
            package_id=1, event_type_id=1, start_at=start, end_at=end,
            guests=guests, **kwargs)

    def _confirm(self, booking, amount=1_320_000):
        return self.bookings.record_payment(
            self.accountant, booking.id, amount, "cash")

    # -- creation ---------------------------------------------------------------
    def test_create_booking_pending_with_hold_and_deposit(self):
        booking = self._book()
        self.assertEqual(booking.status, "pending")
        self.assertTrue(booking.number.startswith("BK-"))
        self.assertEqual(booking.total_amount, 4_400_000)   # Friday 44,000 EGP
        self.assertEqual(booking.deposit_required, 1_320_000)  # 30% of total
        self.assertEqual(booking.deposit_paid, 0)
        hold_hours = (booking.hold_expires_at - booking.created_at).total_seconds() / 3600
        self.assertAlmostEqual(hold_hours, 48, delta=0.1)
        grace_hours = (booking.grace_expires_at - booking.hold_expires_at).total_seconds() / 3600
        self.assertAlmostEqual(grace_hours, 24, delta=0.1)
        # Frozen price items match the total.
        items = self.bookings.list_price_items(self.owner, booking.id)
        self.assertEqual(sum(i["amount"] for i in items), 4_400_000)

    def test_create_booking_rejects_unknown_customer_or_hall(self):
        with self.assertRaises(NotFoundError):
            self.bookings.create_booking(
                self.receptionist, customer_id=99999, hall_id=1,
                start_at=MONDAY, end_at=MONDAY_END, guests=100)
        with self.assertRaises(NotFoundError):
            self.bookings.create_booking(
                self.receptionist, customer_id=self.customer.id, hall_id=99999,
                start_at=MONDAY, end_at=MONDAY_END, guests=100)

    def test_create_booking_rejects_bad_windows(self):
        # Outside hall hours (210 min, so the duration check passes
        # and the hours check is what rejects it).
        with self.assertRaises(BookingConflictError):
            self._book(start=datetime(2026, 12, 7, 20, 0),
                       end=datetime(2026, 12, 7, 23, 30))
        # Too short (min 120 minutes).
        with self.assertRaises(ValidationError):
            self._book(start=MONDAY, end=datetime(2026, 12, 7, 19, 30))
        # Over capacity (Main Hall seats 400).
        with self.assertRaises(ValidationError):
            self._book(start=MONDAY, end=MONDAY_END, guests=401)
        # End before start.
        with self.assertRaises(ValidationError):
            self._book(start=MONDAY_END, end=MONDAY)

    def test_staff_cannot_create_bookings(self):
        with self.assertRaises(AuthorizationError):
            self.bookings.create_booking(
                self.staff, customer_id=self.customer.id, hall_id=1,
                start_at=MONDAY, end_at=MONDAY_END, guests=100)

    def test_create_booking_writes_timeline_and_audit(self):
        booking = self._book()
        timeline = self.bookings.timeline(self.owner, booking.id)
        self.assertEqual([t["action"] for t in timeline], ["created"])
        audits = self.db.query_all(
            "SELECT * FROM audit_logs WHERE entity_type = 'booking' "
            "AND entity_id = ? AND action = 'booking.created'", (booking.id,))
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0]["employee_id"], self.receptionist.id)

    # -- double-booking prevention (critical) -----------------------------------
    def test_same_hall_same_time_rejected(self):
        self._book()
        with self.assertRaises(BookingConflictError):
            self._book()

    def test_same_hall_overlapping_time_rejected(self):
        self._book()
        with self.assertRaises(BookingConflictError):
            self._book(start=datetime(2026, 12, 4, 17, 0),
                       end=datetime(2026, 12, 4, 22, 0))

    def test_different_hall_same_time_allowed(self):
        self._book()
        other = self._book(hall_id=2, guests=100)  # Garden Hall, 150 seats
        self.assertEqual(other.status, "pending")

    def test_cancelled_booking_frees_the_slot(self):
        booking = self._book()
        # Cancellation needs booking.cancel (Manager/Owner per the role
        # map and SRS §8.8 approval); the Receptionist does not hold it.
        self.bookings.cancel_booking(self.manager, booking.id, "Customer changed mind")
        replacement = self._book()
        self.assertEqual(replacement.status, "pending")

    def test_receptionist_cannot_cancel_bookings(self):
        booking = self._book()
        with self.assertRaises(AuthorizationError):
            self.bookings.cancel_booking(self.receptionist, booking.id, "nope")

    def test_concurrent_double_booking_only_one_succeeds(self):
        errors: list[Exception] = []
        created: list[object] = []
        barrier = threading.Barrier(2)

        def attempt():
            # Each thread opens its own manager/connections on the same
            # file (DatabaseManager holds no persistent connection, so
            # there is nothing to close afterwards).
            manager = DatabaseManager(self.db_path)
            try:
                auth = AuthService(manager, self.settings)
                employee, _token = auth.login("receptionist", "Reception123!")
                service = BookingService(manager, self.settings)
                barrier.wait(timeout=10)
                created.append(service.create_booking(
                    employee, customer_id=self.customer.id, hall_id=1,
                    package_id=1, event_type_id=1, start_at=FRIDAY,
                    end_at=FRIDAY_END, guests=200))
            except Exception as exc:  # noqa: BLE001 - record any failure
                errors.append(exc)

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        self.assertEqual(len(created), 1, f"expected exactly one booking, got {created}")
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], BookingConflictError)
        # The repository stores datetimes as ISO strings with a "T"
        # separator ("2026-12-04T18:00:00"), so compare against that.
        rows = self.db.query_all(
            "SELECT * FROM bookings WHERE hall_id = 1 AND start_at = ?",
            (FRIDAY.isoformat(),))
        self.assertEqual(len(rows), 1)

    # -- buffer time (A-18) -------------------------------------------------------
    def test_buffer_conflict_rejected_and_exact_buffer_allowed(self):
        self._book(start=MONDAY_MID, end=MONDAY_MID_END)
        # 17:30 start needs a 16:30 buffered start -> overlaps the 12:00-17:00 event.
        with self.assertRaises(BookingConflictError):
            self._book(start=datetime(2026, 12, 7, 17, 30),
                       end=datetime(2026, 12, 7, 22, 30))
        # 18:00 start: buffered start is exactly 17:00, half-open -> OK.
        next_booking = self._book(start=MONDAY, end=MONDAY_END)
        self.assertEqual(next_booking.status, "pending")

    # -- holds and expiry (G-11, lazy) ----------------------------------------------
    def test_expired_hold_frees_slot_on_access(self):
        booking = self._book()
        self.db.execute(
            "UPDATE bookings SET hold_expires_at = ?, grace_expires_at = ? "
            "WHERE id = ?",
            ((datetime.now() - timedelta(hours=1)).isoformat(),
             (datetime.now() - timedelta(minutes=1)).isoformat(), booking.id))
        # Reading the booking lazily expires it...
        self.assertEqual(self.bookings.get_booking(self.owner, booking.id).status,
                         "expired")
        # ...and the slot is bookable again.
        replacement = self._book()
        self.assertEqual(replacement.status, "pending")

    def test_confirmed_booking_never_expires(self):
        booking = self._book()
        self._confirm(booking)
        self.db.execute(
            "UPDATE bookings SET hold_expires_at = ?, grace_expires_at = ? "
            "WHERE id = ?",
            ((datetime.now() - timedelta(hours=1)).isoformat(),
             (datetime.now() - timedelta(minutes=1)).isoformat(), booking.id))
        self.assertEqual(self.bookings.get_booking(self.owner, booking.id).status,
                         "confirmed")
        with self.assertRaises(BookingConflictError):
            self._book()

    # -- deposit-based confirmation (§3.4) -------------------------------------------
    def test_deposit_below_30_percent_does_not_confirm(self):
        booking = self._book()
        self.bookings.record_payment(self.accountant, booking.id, 1_000_000, "cash")
        self.assertEqual(self.bookings.get_booking(self.owner, booking.id).status,
                         "pending")
        self.assertEqual(self.bookings.get_booking(self.owner, booking.id).deposit_paid,
                         1_000_000)

    def test_valid_deposit_confirms(self):
        booking = self._book()
        payment = self._confirm(booking)
        self.assertEqual(payment.status, "recorded")
        confirmed = self.bookings.get_booking(self.owner, booking.id)
        self.assertEqual(confirmed.status, "confirmed")
        self.assertEqual(confirmed.deposit_paid, 1_320_000)

    def test_partial_deposits_accumulate_to_confirmation(self):
        booking = self._book()
        self.bookings.record_payment(self.accountant, booking.id, 500_000, "cash")
        self.assertEqual(self.bookings.get_booking(self.owner, booking.id).status,
                         "pending")
        self.bookings.record_payment(self.accountant, booking.id, 820_000, "cash")
        self.assertEqual(self.bookings.get_booking(self.owner, booking.id).status,
                         "confirmed")

    def test_deposit_recorded_as_real_payment_row(self):
        booking = self._book()
        self._confirm(booking)
        payments = self.bookings.list_payments(self.owner, booking.id)
        self.assertEqual(len(payments), 1)
        self.assertEqual(payments[0].amount, 1_320_000)
        self.assertTrue(payments[0].number.startswith("PAY-"))
        self.assertIsNotNone(payments[0].idempotency_key)

    def test_payment_on_cancelled_booking_rejected(self):
        booking = self._book()
        self.bookings.cancel_booking(self.manager, booking.id, "cancelled")
        with self.assertRaises(PaymentError):
            self.bookings.record_payment(self.accountant, booking.id, 1_320_000, "cash")

    def test_payment_idempotency_key_replays_without_double_counting(self):
        booking = self._book()
        first = self.bookings.record_payment(
            self.accountant, booking.id, 1_320_000, "cash",
            idempotency_key="test-key-1")
        second = self.bookings.record_payment(
            self.accountant, booking.id, 1_320_000, "cash",
            idempotency_key="test-key-1")
        self.assertEqual(first.id, second.id)
        self.assertEqual(self.bookings.get_booking(self.owner, booking.id).deposit_paid,
                         1_320_000)
        self.assertEqual(len(self.bookings.list_payments(self.owner, booking.id)), 1)

    def test_price_frozen_after_confirmation(self):
        booking = self._book()
        self._confirm(booking)
        # Package price changes AFTER confirmation must not move the total.
        self.db.execute("UPDATE packages SET base_price = ? WHERE id = 1",
                        (99_000_00,))
        confirmed = self.bookings.get_booking(self.owner, booking.id)
        self.assertEqual(confirmed.total_amount, 4_400_000)
        snapshots = self.db.query_all(
            "SELECT * FROM booking_policy_snapshots WHERE booking_id = ?",
            (booking.id,))
        self.assertEqual(len(snapshots), 1)

    # -- grace period (§3.4) --------------------------------------------------------
    def test_payment_initiated_before_hold_expiry_confirms_in_grace(self):
        booking = self._book()
        now = datetime.now()
        self.db.execute(
            "UPDATE bookings SET hold_expires_at = ?, grace_expires_at = ? "
            "WHERE id = ?",
            ((now - timedelta(hours=1)).isoformat(), (now + timedelta(hours=23)).isoformat(), booking.id))
        self.bookings.record_payment(
            self.accountant, booking.id, 1_320_000, "cash",
            initiated_at=now - timedelta(hours=2))
        self.assertEqual(self.bookings.get_booking(self.owner, booking.id).status,
                         "confirmed")

    def test_backdated_payment_after_hold_expiry_rejected(self):
        booking = self._book()
        now = datetime.now()
        self.db.execute(
            "UPDATE bookings SET hold_expires_at = ?, grace_expires_at = ? "
            "WHERE id = ?",
            ((now - timedelta(hours=1)).isoformat(), (now + timedelta(hours=23)).isoformat(), booking.id))
        with self.assertRaises(PaymentError):
            self.bookings.record_payment(
                self.accountant, booking.id, 1_320_000, "cash",
                initiated_at=now - timedelta(minutes=30))

    def test_payment_after_grace_expiry_rejected(self):
        booking = self._book()
        self.db.execute(
            "UPDATE bookings SET hold_expires_at = ?, grace_expires_at = ? "
            "WHERE id = ?",
            ((datetime.now() - timedelta(hours=2)).isoformat(),
             (datetime.now() - timedelta(minutes=1)).isoformat(), booking.id))
        with self.assertRaises(PaymentError):
            self.bookings.record_payment(self.accountant, booking.id, 1_320_000, "cash")

    # -- lifecycle -------------------------------------------------------------------
    def test_lifecycle_transitions_and_invalid_ones(self):
        booking = self._book()
        self._confirm(booking)
        # Confirmed -> In progress.
        booking = self.bookings.mark_in_progress(self.receptionist, booking.id)
        self.assertEqual(booking.status, "in_progress")
        # In progress -> Completed.
        booking = self.bookings.mark_completed(self.receptionist, booking.id)
        self.assertEqual(booking.status, "completed")
        # Completed -> anything is rejected.
        with self.assertRaises(ValidationError):
            self.bookings.cancel_booking(self.manager, booking.id, "too late")

    def test_invalid_transitions_rejected(self):
        booking = self._book()  # pending, no deposit
        with self.assertRaises(ValidationError):
            self.bookings.mark_in_progress(self.receptionist, booking.id)
        with self.assertRaises(ValidationError):
            self.bookings.mark_completed(self.receptionist, booking.id)
        with self.assertRaises(ValidationError):
            self.bookings.mark_no_show(self.receptionist, booking.id)

    def test_no_show_only_from_confirmed(self):
        booking = self._book()
        self._confirm(booking)
        booking = self.bookings.mark_no_show(self.receptionist, booking.id)
        self.assertEqual(booking.status, "no_show")
        rows = self.db.query_all(
            "SELECT * FROM no_show_records WHERE booking_id = ?", (booking.id,))
        self.assertEqual(len(rows), 1)

    def test_cancel_writes_cancellation_record_and_timeline(self):
        booking = self._book()
        self.bookings.cancel_booking(self.manager, booking.id, "Customer changed mind")
        rows = self.db.query_all(
            "SELECT * FROM cancellation_records WHERE booking_id = ?", (booking.id,))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["reason"], "Customer changed mind")
        timeline = self.bookings.timeline(self.owner, booking.id)
        actions = [t["action"] for t in timeline]
        self.assertEqual(actions, ["created", "cancelled"])
        # Timeline survives cancellation (SRS §12.2).
        self.assertEqual(self.bookings.get_booking(self.owner, booking.id).status,
                         "cancelled")

    def test_cancel_twice_rejected(self):
        booking = self._book()
        self.bookings.cancel_booking(self.manager, booking.id, "first")
        with self.assertRaises(ValidationError):
            self.bookings.cancel_booking(self.manager, booking.id, "second")

    def test_notes_edit_audits_old_and_new(self):
        booking = self._book(notes="First note")
        self.bookings.update_notes(self.receptionist, booking.id, "Updated note")
        rows = self.db.query_all(
            "SELECT * FROM audit_logs WHERE entity_type = 'booking' "
            "AND entity_id = ? AND action = 'booking.notes_updated'", (booking.id,))
        self.assertEqual(len(rows), 1)
        self.assertIn("First note", rows[0]["old_value_json"])
        self.assertIn("Updated note", rows[0]["new_value_json"])
        self.assertEqual(self.bookings.get_booking(self.owner, booking.id).notes,
                         "Updated note")

    def test_receptionist_cannot_record_payments(self):
        booking = self._book()
        with self.assertRaises(AuthorizationError):
            self.bookings.record_payment(self.receptionist, booking.id, 1_320_000, "cash")

    def test_discount_at_booking_writes_discount_record(self):
        # Friday 44,000 EGP with a 10% manual discount -> 39,600 EGP;
        # the discount record is written at booking time (FR-027).
        booking = self._book(discount={
            "type": "percent", "value": 10, "reason": "Loyal customer"})
        self.assertEqual(booking.total_amount, 3_960_000)
        rows = self.db.query_all(
            "SELECT * FROM discount_records WHERE booking_id = ?", (booking.id,))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["amount"], 440_000)
        self.assertEqual(rows[0]["reason"], "Loyal customer")

    def test_discount_over_limit_rejected_at_booking(self):
        with self.assertRaises(ValidationError):
            self._book(discount={
                "type": "percent", "value": 50, "reason": "Too generous"})

    def test_receptionist_cannot_see_profit(self):
        booking = self._book()
        self._confirm(booking)
        with self.assertRaises(AuthorizationError):
            self.bookings.profit(self.receptionist, booking.id)
        profit = self.bookings.profit(self.owner, booking.id)
        self.assertEqual(profit["total_amount"], 4_400_000)
