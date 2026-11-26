"""Phase 2 tests: pricing engine, snapshots, catalog permissions.

Seeded EXAMPLE prices (piasters): Main Hall 1,500,000; Classic Wedding
package 2,500,000 (min 180 min, 200 guests included, extra guest 10,000);
Friday/Saturday rules +10%. Dates: 2026-12-04 Friday, 2026-12-07 Monday.
"""
from __future__ import annotations

from datetime import datetime

from tests.conftest import Phase1TestCase
from wedding_hall_system.logic.catalog_service import CatalogService
from wedding_hall_system.logic.hall_service import HallService
from wedding_hall_system.logic.models import ServiceSelection
from wedding_hall_system.logic.pricing_service import PricingService
from wedding_hall_system.utils.exceptions import (
    AuthorizationError,
    NotFoundError,
    ValidationError,
)

FRIDAY = datetime(2026, 12, 4, 18, 0)   # Friday -> +10% weekend rule
MONDAY = datetime(2026, 12, 7, 18, 0)   # Monday -> no weekday rule


class TestPricing(Phase1TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.login_as("owner", "Owner123!")[0]
        self.manager = self.login_as("manager", "Manager123!")[0]
        self.receptionist = self.login_as("receptionist", "Reception123!")[0]
        self.pricing = PricingService(self.db, self.settings)
        self.catalog = CatalogService(self.db, self.settings)
        self.halls = HallService(self.db, self.settings)
        self.main_hall = next(
            h for h in self.halls.list_halls(self.owner) if h.name.startswith("Main Hall"))
        self.garden_hall = next(
            h for h in self.halls.list_halls(self.owner) if h.name.startswith("Garden Hall"))
        self.classic = next(
            p for p in self.catalog.list_packages(self.owner)
            if p.name.startswith("Classic Wedding"))
        self.services = {s.name: s for s in self.catalog.list_services(self.owner)}

    def _calc(self, **kwargs):
        defaults = dict(hall_id=self.main_hall.id, package_id=self.classic.id,
                        start_at=MONDAY, duration_min=300, guests=200)
        defaults.update(kwargs)
        return self.pricing.calculate(self.owner, **defaults)

    # -- breakdown / totals ---------------------------------------------------
    def test_breakdown_sums_to_total(self):
        breakdown = self._calc()
        self.assertEqual(breakdown.total, sum(i.amount for i in breakdown.items))
        self.assertEqual(breakdown.total, 4_000_000)  # hall 1.5M + package 2.5M
        kinds = {i.kind for i in breakdown.items}
        self.assertIn("base", kinds)

    def test_weekend_rule_applies_on_friday_only(self):
        friday = self._calc(start_at=FRIDAY)
        self.assertEqual(friday.total, 4_400_000)  # +10% of 4,000,000
        adjustments = [i for i in friday.items if i.kind == "adjustment"]
        self.assertEqual(len(adjustments), 1)
        self.assertEqual(adjustments[0].amount, 400_000)
        monday = self._calc(start_at=MONDAY)
        self.assertEqual(monday.total, 4_000_000)
        self.assertEqual([i for i in monday.items if i.kind == "adjustment"], [])

    def test_extra_guests_charged_at_package_rate(self):
        breakdown = self._calc(guests=250)
        extra = [i for i in breakdown.items if i.kind == "extra_guests"]
        self.assertEqual(len(extra), 1)
        self.assertEqual(extra[0].amount, 50 * 10_000)
        self.assertEqual(breakdown.total, 4_500_000)

    def test_no_extra_guest_charge_within_included_count(self):
        breakdown = self._calc(guests=150)
        self.assertEqual([i for i in breakdown.items if i.kind == "extra_guests"], [])
        self.assertEqual(breakdown.total, 4_000_000)

    # -- services ---------------------------------------------------------------
    def test_service_pricing_methods(self):
        breakdown = self.pricing.calculate(
            self.owner, hall_id=self.main_hall.id, package_id=None,
            start_at=MONDAY, duration_min=300, guests=100,
            service_selections=[
                ServiceSelection(service_id=self.services["DJ"].id),
                ServiceSelection(service_id=self.services["Catering"].id),
                ServiceSelection(service_id=self.services["Lighting & Sound"].id),
            ])
        amounts = {i.label: i.amount for i in breakdown.items if i.kind == "service"}
        self.assertEqual(amounts["Service — DJ (× 1)"], 250_000)
        self.assertEqual(amounts["Service — Catering (per guest)"], 100 * 15_000)
        # 300 min = 5 hours x 100,000 piasters.
        self.assertEqual(amounts["Service — Lighting & Sound (300 min)"], 500_000)
        self.assertEqual(breakdown.total, 1_500_000 + 250_000 + 1_500_000 + 500_000)

    def test_included_service_not_double_charged(self):
        # Photographer is included in the Classic package.
        breakdown = self._calc(service_selections=[
            ServiceSelection(service_id=self.services["Photographer"].id)])
        included = [i for i in breakdown.items
                    if i.kind == "service" and "Photographer" in i.label]
        self.assertEqual(len(included), 1)
        self.assertEqual(included[0].amount, 0)
        self.assertIn("included in package", included[0].label)
        self.assertEqual(breakdown.total, 4_000_000)

    def test_service_limited_to_another_hall_rejected(self):
        limited = self.catalog.create_service(
            self.owner, name="Garden Only", pricing_method="per_event",
            unit_price=100_000, hall_ids=[self.garden_hall.id])
        with self.assertRaises(ValidationError):
            self._calc(service_selections=[ServiceSelection(service_id=limited.id)])

    def test_unknown_service_rejected(self):
        with self.assertRaises(NotFoundError):
            self._calc(service_selections=[ServiceSelection(service_id=99999)])

    # -- validation ---------------------------------------------------------------
    def test_min_duration_rejected(self):
        with self.assertRaises(ValidationError):
            self._calc(duration_min=120)  # Classic minimum is 180

    def test_over_capacity_rejected(self):
        with self.assertRaises(ValidationError):
            self._calc(guests=401)

    def test_package_not_offered_for_hall_rejected(self):
        garden_only = self.catalog.create_package(
            self.owner, name="Garden Only Package", base_price=100_000,
            hall_ids=[self.garden_hall.id])
        with self.assertRaises(ValidationError):
            self.pricing.calculate(self.owner, hall_id=self.main_hall.id,
                                   package_id=garden_only.id, start_at=MONDAY,
                                   duration_min=180, guests=50)

    def test_inactive_package_rejected(self):
        self.catalog.update_package(self.owner, self.classic.id, is_active=False)
        with self.assertRaises(ValidationError):
            self._calc()

    # -- discounts ------------------------------------------------------------------
    def test_percent_discount_within_limit(self):
        breakdown = self._calc(discount={"type": "percent", "value": 10,
                                         "reason": "Goodwill"})
        self.assertEqual(breakdown.total, 3_600_000)
        discounts = [i for i in breakdown.items if i.kind == "discount"]
        self.assertEqual(discounts[0].amount, -400_000)

    def test_fixed_discount(self):
        breakdown = self._calc(discount={"type": "fixed", "value": 500_000,
                                         "reason": "Manager approval"})
        self.assertEqual(breakdown.total, 3_500_000)

    def test_discount_over_limit_rejected(self):
        with self.assertRaises(ValidationError):
            self._calc(discount={"type": "percent", "value": 25, "reason": "Too much"})
        with self.assertRaises(ValidationError):
            self._calc(discount={"type": "percent", "value": 10, "reason": ""})
        with self.assertRaises(ValidationError):
            self._calc(discount={"type": "fixed", "value": 9_999_999, "reason": "x"})

    # -- pricing rules ----------------------------------------------------------------
    def test_fixed_rule_adjustment(self):
        self.catalog.create_pricing_rule(
            self.owner, name="Winter fixed cut", package_id=self.classic.id,
            adjustment_type="fixed", adjustment_value=-500_000)
        breakdown = self._calc()
        self.assertEqual(breakdown.total, 3_500_000)

    def test_inactive_rule_ignored(self):
        rules = self.catalog.list_pricing_rules(self.owner)
        friday_rule = next(r for r in rules if r.name.startswith("Friday"))
        self.catalog.update_pricing_rule(self.owner, friday_rule.id, is_active=False)
        breakdown = self._calc(start_at=FRIDAY)
        self.assertEqual(breakdown.total, 4_000_000)

    def test_rule_crud_and_permissions(self):
        rule = self.catalog.create_pricing_rule(
            self.owner, name="Test rule", adjustment_type="percent",
            adjustment_value=5)
        self.assertIsNotNone(rule.id)
        with self.assertRaises(AuthorizationError):
            self.catalog.create_pricing_rule(
                self.receptionist, name="Nope", adjustment_type="percent",
                adjustment_value=5)
        with self.assertRaises(AuthorizationError):
            self.catalog.create_package(self.receptionist, name="Nope",
                                        base_price=100_000)
        created = self.catalog.create_package(self.manager, name="Manager Pkg",
                                              base_price=100_000)
        self.assertIsNotNone(created.id)

    # -- snapshots ----------------------------------------------------------------------
    def _make_booking(self) -> int:
        customer_id = self.db.execute(
            "INSERT INTO customers (full_name, phone) VALUES (?, ?)",
            ("Test Customer", "+201000000000"))
        return self.db.execute(
            """INSERT INTO bookings
               (number, customer_id, hall_id, package_id, start_at, end_at,
                guest_count, status, total_amount)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            ("B-0001", customer_id, self.main_hall.id, self.classic.id,
             "2026-12-04T18:00:00", "2026-12-04T23:00:00", 200, 4_000_000))

    def test_snapshots_frozen_after_price_change(self):
        booking_id = self._make_booking()
        self.pricing.create_snapshots(self.owner, booking_id)
        before = self.pricing.get_snapshots(self.owner, booking_id)
        self.assertIsNotNone(before["booking_policy"])
        self.assertIsNotNone(before["financial_policy"])
        self.assertEqual(before["booking_policy"]["deposit_percent"], 30)
        self.assertEqual(before["financial_policy"]["max_discount_percent"], 20)
        # Change the package price and the rules afterwards: snapshots stay.
        self.catalog.update_package(self.owner, self.classic.id, base_price=9_999_999)
        rules = self.catalog.list_pricing_rules(self.owner)
        self.catalog.update_pricing_rule(self.owner, rules[0].id, is_active=False)
        after = self.pricing.get_snapshots(self.owner, booking_id)
        self.assertEqual(before, after)
        self.assertEqual(len(after["booking_policy"]["active_pricing_rules"]), 2)

    def test_snapshot_missing_booking_rejected(self):
        with self.assertRaises(NotFoundError):
            self.pricing.create_snapshots(self.owner, 99999)

    def test_snapshot_requires_booking_permission(self):
        # Staff has no booking permissions at all.
        booking_id = self._make_booking()
        staff = self.login_as("staff", "Staff123!")[0]
        with self.assertRaises(AuthorizationError):
            self.pricing.create_snapshots(staff, booking_id)

    def test_no_snapshots_before_creation(self):
        booking_id = self._make_booking()
        snapshots = self.pricing.get_snapshots(self.owner, booking_id)
        self.assertEqual(snapshots, {"booking_policy": None, "financial_policy": None})

    # -- catalog CRUD ---------------------------------------------------------------------
    def test_package_crud_with_links(self):
        package = self.catalog.create_package(
            self.owner, name="Linked Pkg", base_price=200_000,
            hall_ids=[self.main_hall.id],
            event_type_ids=[1],
            service_ids=[self.services["DJ"].id])
        fetched = self.catalog.get_package(self.owner, package.id)
        self.assertEqual(fetched.hall_ids, [self.main_hall.id])
        self.assertEqual(fetched.service_ids, [self.services["DJ"].id])
        updated = self.catalog.update_package(
            self.owner, package.id, hall_ids=[self.garden_hall.id])
        self.assertEqual(updated.hall_ids, [self.garden_hall.id])

    def test_service_crud(self):
        service = self.catalog.create_service(
            self.owner, name="Test Service", pricing_method="per_guest",
            unit_price=50_000)
        self.assertEqual(service.pricing_method, "per_guest")
        updated = self.catalog.update_service(self.owner, service.id, is_active=False)
        self.assertFalse(updated.is_active)
        with self.assertRaises(ValidationError):
            self.catalog.create_service(self.owner, name="Bad Method",
                                        pricing_method="per_day", unit_price=1)

    def test_compatible_services_respect_hall_links(self):
        limited = self.catalog.create_service(
            self.owner, name="Garden Only 2", pricing_method="per_event",
            unit_price=100_000, hall_ids=[self.garden_hall.id])
        for_main = self.catalog.compatible_services(self.owner, hall_id=self.main_hall.id)
        self.assertNotIn(limited.id, [s.id for s in for_main])
        for_garden = self.catalog.compatible_services(self.owner, hall_id=self.garden_hall.id)
        self.assertIn(limited.id, [s.id for s in for_garden])
