"""Phase 3 tests: quotations — availability without reservation,
conversion to a Pending booking, expiry, and lifecycle rules."""
from __future__ import annotations

from datetime import datetime, timedelta

from tests.conftest import Phase1TestCase
from wedding_hall_system.logic.booking_service import BookingService
from wedding_hall_system.logic.customer_service import CustomerService
from wedding_hall_system.logic.quotation_service import QuotationService
from wedding_hall_system.utils.exceptions import (
    AuthorizationError,
    BookingConflictError,
    NotFoundError,
    ValidationError,
)

FRIDAY = datetime(2026, 12, 4, 18, 0)   # Friday: +10% pricing rule applies
FRIDAY_END = datetime(2026, 12, 4, 23, 0)
MONDAY = datetime(2026, 12, 7, 18, 0)
MONDAY_END = datetime(2026, 12, 7, 23, 0)


class TestQuotations(Phase1TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.login_as("owner", "Owner123!")[0]
        self.receptionist = self.login_as("receptionist", "Reception123!")[0]
        self.staff = self.login_as("staff", "Staff123!")[0]
        self.quotations = QuotationService(self.db, self.settings)
        self.bookings = BookingService(self.db, self.settings)
        self.customer = next(
            c for c in CustomerService(self.db, self.settings).list_customers(self.owner)
            if c.full_name.startswith("Ahmed Hassan"))

    def _create(self, start=FRIDAY, end=FRIDAY_END, guests=200):
        return self.quotations.create_quotation(
            self.receptionist, customer_id=self.customer.id, hall_id=1,
            package_id=1, event_type_id=1, start_at=start, end_at=end,
            guests=guests)

    # -- creation / price snapshot ---------------------------------------------
    def test_create_quotation_freezes_price_and_items(self):
        quote = self._create()
        self.assertEqual(quote.status, "draft")
        self.assertTrue(quote.number.startswith("Q-"))
        # Friday: hall 15,000 + package 25,000 + 10% rule = 44,000 EGP.
        self.assertEqual(quote.total_amount, 4_400_000)
        items = self.quotations.list_items(self.owner, quote.id)
        self.assertEqual(sum(i.amount for i in items), 4_400_000)
        self.assertTrue(any("Friday" in i.label for i in items))

    def test_create_quotation_requires_customer_view_permission(self):
        with self.assertRaises(AuthorizationError):
            self.quotations.create_quotation(
                self.staff, customer_id=self.customer.id, hall_id=1,
                start_at=MONDAY, end_at=MONDAY_END, guests=100)

    def test_create_quotation_rejects_unknown_customer(self):
        with self.assertRaises(NotFoundError):
            self.quotations.create_quotation(
                self.receptionist, customer_id=99999, hall_id=1,
                start_at=MONDAY, end_at=MONDAY_END, guests=100)

    def test_create_quotation_rejects_bad_window(self):
        # Outside hall hours (23:00–23:30 is past closing).
        with self.assertRaises((ValidationError, BookingConflictError)):
            self.quotations.create_quotation(
                self.receptionist, customer_id=self.customer.id, hall_id=1,
                start_at=datetime(2026, 12, 7, 22, 30),
                end_at=datetime(2026, 12, 7, 23, 30), guests=100)
        # Over capacity (Main Hall seats 400).
        with self.assertRaises(ValidationError):
            self._create(start=MONDAY, end=MONDAY_END, guests=500)

    # -- quotations do NOT reserve (SRS §3.5) ----------------------------------
    def test_quotation_does_not_reserve_the_slot(self):
        self._create()
        # The very same hall/slot is still bookable directly afterwards.
        booking = self.bookings.create_booking(
            self.receptionist, customer_id=self.customer.id, hall_id=1,
            package_id=1, event_type_id=1, start_at=FRIDAY, end_at=FRIDAY_END,
            guests=200)
        self.assertEqual(booking.status, "pending")

    def test_quotation_cannot_be_sent_twice(self):
        quote = self._create()
        self.quotations.send_quotation(self.receptionist, quote.id)
        with self.assertRaises(ValidationError):
            self.quotations.send_quotation(self.receptionist, quote.id)

    def test_accepted_quotation_converts_to_pending_booking(self):
        quote = self._create()
        accepted = self.quotations.accept_quotation(self.receptionist, quote.id)
        self.assertEqual(accepted.status, "accepted")
        booking = self.bookings.create_booking(
            self.receptionist, customer_id=accepted.customer_id,
            hall_id=accepted.hall_id, event_type_id=accepted.event_type_id,
            package_id=accepted.package_id, start_at=accepted.start_at,
            end_at=accepted.end_at, guests=accepted.guest_count,
            quotation_id=accepted.id)
        self.assertEqual(booking.status, "pending")
        self.assertEqual(booking.total_amount, 4_400_000)
        self.assertIsNotNone(booking.hold_expires_at)
        self.assertEqual(booking.quotation_id, quote.id)
        self.assertEqual(
            self.quotations.get_quotation(self.owner, quote.id).status, "accepted")
        # The booking's frozen price items match the quotation items.
        items = self.bookings.list_price_items(self.owner, booking.id)
        self.assertEqual(sum(i["amount"] for i in items), 4_400_000)

    def test_conversion_rejects_if_slot_taken_after_acceptance(self):
        quote = self._create()
        self.quotations.accept_quotation(self.receptionist, quote.id)
        # Someone else books the slot directly in the meantime.
        self.bookings.create_booking(
            self.receptionist, customer_id=self.customer.id, hall_id=1,
            package_id=1, event_type_id=1, start_at=FRIDAY, end_at=FRIDAY_END,
            guests=200)
        with self.assertRaises(BookingConflictError):
            self.bookings.create_booking(
                self.receptionist, customer_id=quote.customer_id,
                hall_id=quote.hall_id, event_type_id=quote.event_type_id,
                package_id=quote.package_id, start_at=quote.start_at,
                end_at=quote.end_at, guests=quote.guest_count,
                quotation_id=quote.id)

    def test_accepting_an_accepted_quotation_rejected(self):
        quote = self._create()
        self.quotations.accept_quotation(self.receptionist, quote.id)
        with self.assertRaises(ValidationError):
            self.quotations.accept_quotation(self.receptionist, quote.id)

    def test_cannot_convert_a_draft_quotation(self):
        quote = self._create()
        with self.assertRaises(ValidationError):
            self.bookings.create_booking(
                self.receptionist, customer_id=quote.customer_id,
                hall_id=quote.hall_id, start_at=quote.start_at,
                end_at=quote.end_at, guests=quote.guest_count,
                quotation_id=quote.id)

    # -- expiry ------------------------------------------------------------------
    def test_expired_quotation_cannot_be_accepted(self):
        quote = self._create(start=MONDAY, end=MONDAY_END)
        self.db.execute("UPDATE quotations SET valid_until = ? WHERE id = ?",
                        (datetime(2026, 11, 1).isoformat(), quote.id))
        with self.assertRaises(ValidationError):
            self.quotations.accept_quotation(self.receptionist, quote.id)
        self.assertEqual(
            self.quotations.get_quotation(self.owner, quote.id).status, "expired")

    def test_expired_quotation_cannot_be_sent(self):
        quote = self._create(start=MONDAY, end=MONDAY_END)
        self.db.execute("UPDATE quotations SET valid_until = ? WHERE id = ?",
                        (datetime(2026, 11, 1).isoformat(), quote.id))
        with self.assertRaises(ValidationError):
            self.quotations.send_quotation(self.receptionist, quote.id)

    def test_expire_overdue_marks_old_quotations(self):
        quote = self._create(start=MONDAY, end=MONDAY_END)
        self.db.execute("UPDATE quotations SET valid_until = ? WHERE id = ?",
                        (datetime(2026, 11, 1).isoformat(), quote.id))
        self.assertEqual(len(self.quotations.sweep_expired_quotations(self.owner)), 1)
        self.assertEqual(
            self.quotations.get_quotation(self.owner, quote.id).status, "expired")

    def test_valid_quotation_stays_draft_after_expiry_sweep(self):
        quote = self._create(start=MONDAY, end=MONDAY_END)
        self.assertEqual(self.quotations.sweep_expired_quotations(self.owner), [])
        self.assertEqual(
            self.quotations.get_quotation(self.owner, quote.id).status, "draft")

    # -- seeded example quotation --------------------------------------------------
    def test_seeded_example_quotation_exists(self):
        quotes = self.quotations.list_quotations(self.owner)
        self.assertTrue(any(q.number.startswith("Q-") for q in quotes))
        self.assertTrue(all(q.status == "draft" for q in quotes))
