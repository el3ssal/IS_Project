"""Phase 3 tests: customers, duplicate detection, interactions, profile.

Uses the seeded EXAMPLE customers (Ahmed Hassan / Mona Ali / Karim
Mostafa — all marked "(example)").
"""
from __future__ import annotations

from tests.conftest import Phase1TestCase
from wedding_hall_system.logic.booking_service import BookingService
from wedding_hall_system.logic.customer_service import CustomerService
from wedding_hall_system.utils.exceptions import (
    AuthorizationError,
    NotFoundError,
    ValidationError,
)


class TestCustomers(Phase1TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.login_as("owner", "Owner123!")[0]
        self.receptionist = self.login_as("receptionist", "Reception123!")[0]
        self.staff = self.login_as("staff", "Staff123!")[0]
        self.customers = CustomerService(self.db, self.settings)

    # -- creation / normalisation -------------------------------------------
    def test_create_customer_normalises_phone_and_email(self):
        customer = self.customers.create_customer(
            self.receptionist, full_name="Test Person", phone="010 9999 8888",
            email="  Test@Example.COM ", address="Benha")
        self.assertEqual(customer.phone_normalized, "+201099998888")
        self.assertEqual(customer.email_normalized, "test@example.com")

    def test_create_customer_requires_name_and_phone(self):
        with self.assertRaises(ValidationError):
            self.customers.create_customer(self.receptionist, full_name="", phone="01011111111")
        with self.assertRaises(ValidationError):
            self.customers.create_customer(self.receptionist, full_name="No Phone", phone="")

    def test_invalid_phone_or_email_rejected(self):
        with self.assertRaises(ValidationError):
            self.customers.create_customer(
                self.receptionist, full_name="Bad", phone="abc")
        with self.assertRaises(ValidationError):
            self.customers.create_customer(
                self.receptionist, full_name="Bad", phone="01011111111",
                email="not-an-email")

    def test_seeded_example_customers_exist(self):
        names = [c.full_name for c in self.customers.list_customers(self.owner)]
        self.assertIn("Ahmed Hassan (example)", names)
        self.assertIn("Mona Ali (example)", names)
        self.assertIn("Karim Mostafa (example)", names)

    # -- duplicate detection (SRS §5.2, T-21) --------------------------------
    def test_duplicate_phone_blocks_creation(self):
        with self.assertRaises(ValidationError) as ctx:
            self.customers.create_customer(
                self.receptionist, full_name="Copy Cat", phone="+201012345678")
        self.assertTrue(getattr(ctx.exception, "duplicates", []))
        self.assertEqual(ctx.exception.duplicates[0]["match"], "phone")
        # No second row was created.
        self.assertEqual(
            len(self.customers.list_customers(self.owner, search="Copy Cat")), 0)

    def test_duplicate_phone_matches_different_format(self):
        # 01012345678 and +201012345678 are the same normalized phone.
        with self.assertRaises(ValidationError):
            self.customers.create_customer(
                self.receptionist, full_name="Copy Cat", phone="01012345678")

    def test_duplicate_national_id_blocks_creation(self):
        with self.assertRaises(ValidationError) as ctx:
            self.customers.create_customer(
                self.receptionist, full_name="Copy Cat", phone="01055555555",
                national_id="29001010123456")
        self.assertEqual(ctx.exception.duplicates[0]["match"], "national_id")
        # The full National ID is never exposed in the warning.
        self.assertNotIn("29001010123456", str(ctx.exception))

    def test_email_only_match_is_possible_duplicate_reviewable(self):
        with self.assertRaises(ValidationError) as ctx:
            self.customers.create_customer(
                self.receptionist, full_name="Same Email", phone="01055555555",
                email="ahmed.hassan@example.com")
        self.assertEqual(ctx.exception.duplicates[0]["match"], "email")
        # Staff reviewed the match: force creates, without merging.
        created = self.customers.create_customer(
            self.receptionist, full_name="Same Email", phone="01055555555",
            email="ahmed.hassan@example.com", force=True)
        self.assertIsNotNone(created.id)
        matches = self.customers.list_customers(self.owner, search="ahmed.hassan@example.com")
        self.assertEqual(len(matches), 2)  # original + forced copy, no merge

    def test_find_duplicates_reports_matches(self):
        matches = self.customers.find_duplicates(
            self.owner, phone="01123456789", email="nobody@example.com")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["full_name"], "Mona Ali (example)")
        self.assertEqual(matches[0]["match"], "phone")
        self.assertNotIn("national_id", str(matches[0]))

    # -- update / audit -------------------------------------------------------
    def test_update_customer_audits_identity_edit_without_id_value(self):
        mona = next(c for c in self.customers.list_customers(self.owner)
                    if c.full_name.startswith("Mona Ali"))
        updated = self.customers.update_customer(
            self.owner, mona.id, address="New address, Benha",
            national_id="29999999999999")
        self.assertEqual(updated.address, "New address, Benha")
        rows = self.db.query_all(
            "SELECT * FROM audit_logs WHERE entity_type = 'customer' "
            "AND entity_id = ? AND action = 'customer.updated'",
            (mona.id,))
        self.assertEqual(len(rows), 1)
        # The National ID value itself is never in the audit JSON.
        self.assertNotIn("29999999999999", rows[0]["old_value_json"])
        self.assertNotIn("29999999999999", rows[0]["new_value_json"])
        self.assertIn("changed", rows[0]["new_value_json"])

    def test_update_to_another_customers_phone_blocked(self):
        mona = next(c for c in self.customers.list_customers(self.owner)
                    if c.full_name.startswith("Mona Ali"))
        with self.assertRaises(ValidationError):
            self.customers.update_customer(self.owner, mona.id, phone="01012345678")

    def test_update_unknown_customer_raises_not_found(self):
        with self.assertRaises(NotFoundError):
            self.customers.update_customer(self.owner, 99999, full_name="Ghost")

    # -- masking (FR-012) -----------------------------------------------------
    def test_national_id_masked_for_receptionist_full_for_owner(self):
        for employee, expect_full in ((self.receptionist, False), (self.owner, True)):
            row = next(c for c in self.customers.list_customers(employee)
                       if c.full_name.startswith("Ahmed Hassan"))
            if expect_full:
                self.assertEqual(row.national_id, "29001010123456")
            else:
                self.assertTrue(row.national_id.startswith("****"))
                self.assertNotIn("29001010123456", row.national_id)

    # -- interactions / profile ------------------------------------------------
    def test_interactions_listed_in_order(self):
        ahmed = next(c for c in self.customers.list_customers(self.owner)
                     if c.full_name.startswith("Ahmed Hassan"))
        self.customers.add_interaction(
            self.receptionist, ahmed.id, "call", "Called to confirm the date.")
        self.customers.add_interaction(
            self.receptionist, ahmed.id, "inquiry", "Asked about the Garden Hall.")
        history = self.customers.list_interactions(self.owner, ahmed.id)
        summaries = [i.summary for i in history]
        self.assertIn("Called to confirm the date.", summaries)
        self.assertIn("Asked about the Garden Hall.", summaries)
        self.assertLess(summaries.index("Called to confirm the date."),
                        summaries.index("Asked about the Garden Hall."))

    def test_profile_totals_stay_distinct(self):
        # Ahmed Hassan (example) has the seeded example quotation but no
        # booking yet: all totals are zero and distinct.
        ahmed = next(c for c in self.customers.list_customers(self.owner)
                     if c.full_name.startswith("Ahmed Hassan"))
        profile = self.customers.profile(self.owner, ahmed.id)
        self.assertEqual(profile["totals"]["contracted"], 0)
        self.assertEqual(profile["totals"]["collected"], 0)
        self.assertEqual(profile["totals"]["outstanding"], 0)
        self.assertEqual(profile["totals"]["booking_count"], 0)
        self.assertEqual(len(profile["quotations"]), 1)

    def test_profile_totals_after_booking_and_payment(self):
        from datetime import datetime

        ahmed = next(c for c in self.customers.list_customers(self.owner)
                     if c.full_name.startswith("Ahmed Hassan"))
        bookings = BookingService(self.db, self.settings)
        # Friday 2026-12-04: hall 15,000 + package 25,000 = 40,000 EGP,
        # Friday rule +10% -> total 44,000 EGP (4,400,000 piasters).
        booking = bookings.create_booking(
            self.receptionist, customer_id=ahmed.id, hall_id=1,
            package_id=1, event_type_id=1,
            start_at=datetime(2026, 12, 4, 18, 0),
            end_at=datetime(2026, 12, 4, 23, 0), guests=200)
        accountant = self.login_as("accountant", "Account123!")[0]
        bookings.record_payment(accountant, booking.id, 1_320_000, "cash")
        profile = self.customers.profile(self.owner, ahmed.id)
        totals = profile["totals"]
        self.assertEqual(totals["contracted"], 4_400_000)
        self.assertEqual(totals["collected"], 1_320_000)
        self.assertEqual(totals["outstanding"], 4_400_000 - 1_320_000)
        self.assertEqual(totals["booking_count"], 1)

    # -- permissions ------------------------------------------------------------
    def test_staff_cannot_create_customers(self):
        with self.assertRaises(AuthorizationError):
            self.customers.create_customer(
                self.staff, full_name="Nope", phone="01011111111")
