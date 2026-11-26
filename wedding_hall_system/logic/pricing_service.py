"""Pricing engine (Phase 2, business layer — SRS §3.6–§3.7, §3.5).

Itemised breakdown, all money in integer piasters:
  hall base price (per event)
+ package base price (if a package is chosen)
+ matching dynamic-pricing rules (in creation order; percent rules apply
  to the running subtotal, fixed rules add/subtract directly)
+ extra guests above the package included count (package rate)
+ additional services (per event / per hour / per guest / by level),
  skipping services already included in the package (never double-charged)
− manual discount (fixed or %, capped by the configured maximum)

Pricing snapshots (FR-028, BR-11): ``create_snapshots`` freezes the
current booking policy and financial policy for a booking into the two
separate snapshot tables, so later config changes never rewrite history.
Snapshots are written at booking confirmation (Phase 3 calls this);
Phase 2 exposes and tests the mechanism.
"""
from __future__ import annotations

import json
import math
from datetime import datetime

from ..config.settings import AppSettings, format_money
from ..data.database import DatabaseManager
from ..data.repositories import catalog_repository as repo
from ..data.repositories import system_repository
from ..utils.exceptions import (
    AuthorizationError,
    NotFoundError,
    ValidationError,
)
from ..utils.logging_config import get_logger
from .models import (
    Hall,
    Package,
    PriceBreakdown,
    PriceItem,
    PricingRule,
    Service,
    ServiceSelection,
)
from .permissions import has_permission, require_permission

logger = get_logger(__name__)


def _setting_int(db: DatabaseManager, key: str, default: int) -> int:
    try:
        return int(system_repository.get_setting(db, key, str(default)))
    except ValueError:
        return default


def _setting_json(db: DatabaseManager, key: str, default):
    """Read a JSON setting; fall back to ``default`` when missing or
    unparsable (a bad Owner edit must never break confirmations)."""
    raw = system_repository.get_setting(db, key, "")
    if not raw:
        return json.loads(json.dumps(default))
    try:
        parsed = json.loads(raw)
    except ValueError:
        logger.warning("Setting %s is not valid JSON; using the default.", key)
        return json.loads(json.dumps(default))
    return parsed


# Cancellation fee tiers (decision A-43): fee is a % of the total
# contract value, chosen by days between cancellation and the event.
# The SRS defines the policy shape (§8.4 "refund policy by cancellation
# timing") but no numbers — these are documented seed defaults.
DEFAULT_CANCELLATION_POLICY: list[dict] = [
    {"min_days_before_event": 30, "fee_percent_of_total": 10},
    {"min_days_before_event": 14, "fee_percent_of_total": 25},
    {"min_days_before_event": 0, "fee_percent_of_total": 50},
]

# No-show terms (decision A-44): the deposit is forfeited, nothing is
# refundable, and the unpaid balance stays outstanding.
DEFAULT_NO_SHOW_POLICY: dict = {
    "fee_basis": "deposit",
    "refundable_percent": 0,
}


class PricingService:
    def __init__(self, db: DatabaseManager, settings: AppSettings | None = None) -> None:
        self.db = db
        self.settings = settings or AppSettings.from_env()

    # -- calculation --------------------------------------------------------
    def calculate(self, employee, *, hall_id: int, package_id: int | None = None,
                  event_type_id: int | None = None,
                  start_at: datetime | None = None,
                  duration_min: int | None = None,
                  guests: int = 1,
                  service_selections: list[ServiceSelection] | None = None,
                  discount: dict | None = None,
                  season: str = "") -> PriceBreakdown:
        """Return an itemised breakdown. Raises ValidationError for bad
        inputs (unknown hall/package, over-capacity, below-minimum
        duration, discount over the configured limit).

        ``start_at`` is used only to derive the weekday for rule matching;
        when omitted, no weekday-specific rule matches. ``duration_min``
        defaults to the package default duration (or 120 min with no
        package). ``discount`` is {"type": "fixed"|"percent", "value": int,
        "reason": str} — value is piasters for fixed, whole percent for
        percent.
        """
        require_permission(self.db, employee, "catalog.view")
        hall_row = repo.get_hall(self.db, hall_id)
        if hall_row is None:
            raise NotFoundError(f"Hall #{hall_id} does not exist.")
        hall = Hall(**{k: v for k, v in hall_row.items() if k in Hall.__dataclass_fields__})
        if not hall.is_bookable:
            raise ValidationError(f"Hall '{hall.name}' cannot be booked.")

        package: Package | None = None
        if package_id is not None:
            package_row = repo.get_package(self.db, package_id)
            if package_row is None:
                raise NotFoundError(f"Package #{package_id} does not exist.")
            package = Package(**{k: v for k, v in package_row.items()
                                 if k in Package.__dataclass_fields__})
            if not package.is_active:
                raise ValidationError(f"Package '{package.name}' is inactive.")
            if package.hall_ids and hall_id not in package.hall_ids:
                raise ValidationError(
                    f"Package '{package.name}' is not offered for hall '{hall.name}'.")
            if event_type_id is not None and package.event_type_ids \
                    and event_type_id not in package.event_type_ids:
                raise ValidationError(
                    f"Package '{package.name}' is not offered for this event type.")

        if guests < 1:
            raise ValidationError("Guest count must be at least 1.")
        if guests > hall.capacity:
            raise ValidationError(
                f"Guest count ({guests}) exceeds hall capacity ({hall.capacity}).")

        duration = duration_min if duration_min is not None else (
            package.default_duration_min if package else 120)
        if duration < 1:
            raise ValidationError("Duration must be at least 1 minute.")
        minimum = package.min_duration_min if package else _setting_int(
            self.db, "booking.min_duration_minutes", 120)
        if duration < minimum:
            raise ValidationError(
                f"Duration ({duration} min) is below the minimum of {minimum} min.")

        items: list[PriceItem] = []
        items.append(PriceItem("base", f"Hall — {hall.name}", hall.base_price))
        if package is not None:
            items.append(PriceItem("base", f"Package — {package.name}", package.base_price))

        # Dynamic pricing rules, in creation order.
        weekday = start_at.weekday() if start_at is not None else None
        subtotal = sum(i.amount for i in items)
        for rule_row in repo.list_pricing_rules(self.db, include_inactive=False):
            rule = PricingRule(**{k: v for k, v in rule_row.items()
                                  if k in PricingRule.__dataclass_fields__})
            if not rule.matches(hall_id=hall_id, package_id=package_id,
                                event_type_id=event_type_id, weekday=weekday,
                                season=season):
                continue
            if rule.adjustment_type == "percent":
                amount = round(subtotal * rule.adjustment_value / 100)
            else:
                amount = rule.adjustment_value
            items.append(PriceItem(
                "adjustment",
                f"Rule — {rule.name} ({rule.adjustment_value:+d}"
                f"{'%' if rule.adjustment_type == 'percent' else ' EGP'})",
                amount))
            subtotal += amount

        # Extra guests above the package included count (SRS §3.7).
        if package is not None and guests > package.included_guests:
            extra = guests - package.included_guests
            amount = extra * package.extra_guest_price
            items.append(PriceItem(
                "extra_guests",
                f"Extra guests — {extra} × {format_money(package.extra_guest_price)}",
                amount))
            subtotal += amount

        # Additional services (SRS §3.6, BR-12: never double-charge).
        included_service_ids = set(package.service_ids) if package else set()
        for selection in service_selections or []:
            service_row = repo.get_service(self.db, selection.service_id)
            if service_row is None:
                raise NotFoundError(f"Service #{selection.service_id} does not exist.")
            service = Service(**{k: v for k, v in service_row.items()
                                 if k in Service.__dataclass_fields__})
            if not service.is_active:
                raise ValidationError(f"Service '{service.name}' is inactive.")
            if service.hall_ids and hall_id not in service.hall_ids:
                raise ValidationError(
                    f"Service '{service.name}' is not offered for hall '{hall.name}'.")
            if service.id in included_service_ids:
                items.append(PriceItem(
                    "service", f"Service — {service.name} (included in package)", 0))
                continue
            amount = self._service_amount(service, selection, guests, duration)
            items.append(PriceItem("service", self._service_label(service, selection, duration), amount))
            subtotal += amount

        # Manual discount (SRS §3.7): fixed or percent, capped.
        if discount:
            discount_amount = self._discount_amount(discount, subtotal)
            items.append(PriceItem(
                "discount",
                f"Discount — {discount.get('reason') or 'manual discount'}",
                -discount_amount))

        return PriceBreakdown(
            items=items, hall_id=hall_id, package_id=package_id,
            event_type_id=event_type_id, guests=guests, duration_min=duration,
        )

    @staticmethod
    def _service_amount(service: Service, selection: ServiceSelection,
                        guests: int, duration_min: int) -> int:
        if service.pricing_method == "per_event":
            return service.unit_price * selection.quantity
        if service.pricing_method == "per_hour":
            minutes = selection.duration_min or duration_min
            return service.unit_price * math.ceil(minutes / 60)
        if service.pricing_method == "per_guest":
            return service.unit_price * guests
        # by_level: quantity is the level multiplier.
        return service.unit_price * selection.quantity

    @staticmethod
    def _service_label(service: Service, selection: ServiceSelection,
                       duration_min: int) -> str:
        method = service.pricing_method
        if method == "per_event":
            detail = f"× {selection.quantity}"
        elif method == "per_hour":
            detail = f"{selection.duration_min or duration_min} min"
        elif method == "per_guest":
            detail = "per guest"
        else:
            detail = f"level × {selection.quantity}"
        return f"Service — {service.name} ({detail})"

    def _discount_amount(self, discount: dict, subtotal: int) -> int:
        dtype = discount.get("type")
        value = discount.get("value")
        if dtype not in ("fixed", "percent"):
            raise ValidationError("Discount type must be 'fixed' or 'percent'.")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValidationError("Discount value must be a non-negative whole number.")
        if not str(discount.get("reason") or "").strip():
            raise ValidationError("A discount needs a reason.")
        max_percent = _setting_int(self.db, "pricing.max_discount_percent", 20)
        if dtype == "percent":
            if value > max_percent:
                raise ValidationError(
                    f"Discount of {value}% exceeds the maximum of {max_percent}%.")
            amount = round(subtotal * value / 100)
        else:
            amount = value
        if amount > subtotal:
            raise ValidationError("Discount cannot exceed the price before discount.")
        return amount

    # -- snapshots ------------------------------------------------------------
    def current_policy(self) -> dict:
        """The policy values a snapshot freezes (settings + active rules)."""
        return {
            "hold_hours": _setting_int(self.db, "booking.hold_hours", 48),
            "grace_hours": _setting_int(self.db, "booking.grace_hours", 24),
            "buffer_minutes": _setting_int(self.db, "booking.buffer_minutes", 60),
            "deposit_percent": _setting_int(self.db, "booking.deposit_percent", 30),
            "min_duration_minutes": _setting_int(self.db, "booking.min_duration_minutes", 120),
            "max_discount_percent": _setting_int(self.db, "pricing.max_discount_percent", 20),
            "currency": system_repository.get_setting(self.db, "venue.currency", "EGP"),
            "active_pricing_rules": [
                {k: v for k, v in r.items()
                 if k in ("id", "name", "hall_id", "package_id", "event_type_id",
                          "weekday", "season", "adjustment_type", "adjustment_value")}
                for r in repo.list_pricing_rules(self.db, include_inactive=False)
            ],
            # Phase 5 financial policy (SRS §8.4): installment plan,
            # cancellation tiers and no-show terms are frozen with the
            # rest of the policy at confirmation (FR-048, BR-11).
            "installments_count": _setting_int(
                self.db, "finance.installments_count", 3),
            "installment_interval_days": _setting_int(
                self.db, "finance.installment_interval_days", 30),
            "final_deadline_days_before_event": _setting_int(
                self.db, "finance.final_deadline_days_before_event", 7),
            "late_fee_percent": _setting_int(
                self.db, "finance.late_fee_percent", 0),
            "cancellation_policy": _setting_json(
                self.db, "finance.cancellation_policy",
                DEFAULT_CANCELLATION_POLICY),
            "no_show_policy": _setting_json(
                self.db, "finance.no_show_policy", DEFAULT_NO_SHOW_POLICY),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }

    def create_snapshots(self, employee, booking_id: int, conn=None) -> dict[str, int]:
        """Freeze booking + financial policy for a booking (FR-028, BR-11).

        Both snapshot tables are written in ONE transaction so a booking
        never ends up with only half its policy frozen. Re-running for
        the same booking replaces the snapshot (INSERT OR REPLACE).
        Requires ``booking.create`` or ``payment.record``: snapshots are
        created as part of booking confirmation, which is triggered by
        the Accountant recording the deposit (``payment.record``) as
        well as by booking creation. Pass ``conn`` to write inside the
        caller's transaction (confirmation must never commit a Confirmed
        booking without its snapshots).
        """
        if not (has_permission(self.db, employee, "booking.create")
                or has_permission(self.db, employee, "payment.record")):
            raise AuthorizationError(
                "Permission denied: 'booking.create' or 'payment.record' is required.")
        if conn is not None:
            row = conn.execute(
                "SELECT id FROM bookings WHERE id = ?", (booking_id,)).fetchone()
        else:
            row = self.db.query_one("SELECT id FROM bookings WHERE id = ?", (booking_id,))
        if row is None:
            raise NotFoundError(f"Booking #{booking_id} does not exist.")
        policy = self.current_policy()
        booking_policy = {
            "hold_hours": policy["hold_hours"],
            "grace_hours": policy["grace_hours"],
            "buffer_minutes": policy["buffer_minutes"],
            "deposit_percent": policy["deposit_percent"],
            "min_duration_minutes": policy["min_duration_minutes"],
            "active_pricing_rules": policy["active_pricing_rules"],
            "generated_at": policy["generated_at"],
        }
        financial_policy = {
            "deposit_percent": policy["deposit_percent"],
            "max_discount_percent": policy["max_discount_percent"],
            "currency": policy["currency"],
            "installments_count": policy["installments_count"],
            "installment_interval_days": policy["installment_interval_days"],
            "final_deadline_days_before_event":
                policy["final_deadline_days_before_event"],
            "late_fee_percent": policy["late_fee_percent"],
            "cancellation_policy": policy["cancellation_policy"],
            "no_show_policy": policy["no_show_policy"],
            "generated_at": policy["generated_at"],
        }
        if conn is not None:
            self._write_snapshots(conn, booking_id, booking_policy, financial_policy)
        else:
            with self.db.transaction() as own_conn:
                self._write_snapshots(own_conn, booking_id, booking_policy, financial_policy)
        logger.info("Policy snapshots created for booking #%s", booking_id)
        return {"booking_policy": booking_id, "financial_policy": booking_id}

    @staticmethod
    def _write_snapshots(conn, booking_id: int,
                         booking_policy: dict, financial_policy: dict) -> None:
        conn.execute(
            """INSERT INTO booking_policy_snapshots (booking_id, snapshot_json)
               VALUES (?, ?)
               ON CONFLICT(booking_id) DO UPDATE
               SET snapshot_json = excluded.snapshot_json""",
            (booking_id, json.dumps(booking_policy, ensure_ascii=False)),
        )
        conn.execute(
            """INSERT INTO financial_policy_snapshots (booking_id, snapshot_json)
               VALUES (?, ?)
               ON CONFLICT(booking_id) DO UPDATE
               SET snapshot_json = excluded.snapshot_json""",
            (booking_id, json.dumps(financial_policy, ensure_ascii=False)),
        )

    def get_snapshots(self, employee, booking_id: int) -> dict[str, dict | None]:
        """Read the frozen snapshots for a booking (None if not created)."""
        require_permission(self.db, employee, "booking.view")
        out: dict[str, dict | None] = {}
        for table, key in (("booking_policy_snapshots", "booking_policy"),
                           ("financial_policy_snapshots", "financial_policy")):
            row = self.db.query_one(
                f"SELECT snapshot_json FROM {table} WHERE booking_id = ?", (booking_id,))
            out[key] = json.loads(row["snapshot_json"]) if row else None
        return out

    # -- Phase 5: financial policy in force --------------------------------
    def financial_policy_for_booking(self, booking_id: int,
                                     employee=None) -> dict:
        """The financial policy that governs a booking: the snapshot
        frozen at confirmation when one exists, otherwise the current
        settings (Pending bookings have no snapshot yet, SRS §8.4).
        When an employee is given, ``finance.view`` is required —
        internal callers (cancellation, no-show, reschedule) pass none
        because their own permission already gates the operation."""
        if employee is not None:
            require_permission(self.db, employee, "finance.view")
        row = self.db.query_one(
            "SELECT snapshot_json FROM financial_policy_snapshots "
            "WHERE booking_id = ?",
            (booking_id,))
        if row is not None:
            try:
                snapshot = json.loads(row["snapshot_json"])
                if isinstance(snapshot, dict) and snapshot:
                    return snapshot
            except ValueError:
                logger.warning("Financial snapshot for booking #%s is not "
                               "valid JSON; using current policy.", booking_id)
        policy = self.current_policy()
        return {
            "deposit_percent": policy["deposit_percent"],
            "max_discount_percent": policy["max_discount_percent"],
            "currency": policy["currency"],
            "installments_count": policy["installments_count"],
            "installment_interval_days": policy["installment_interval_days"],
            "final_deadline_days_before_event":
                policy["final_deadline_days_before_event"],
            "late_fee_percent": policy["late_fee_percent"],
            "cancellation_policy": policy["cancellation_policy"],
            "no_show_policy": policy["no_show_policy"],
            "generated_at": policy["generated_at"],
        }

    @staticmethod
    def cancellation_fee_for(policy: dict, *, total_amount: int,
                             paid_amount: int,
                             days_before_event: int) -> tuple[int, int]:
        """(fee, refundable) for a cancellation under ``policy``
        (decision A-43). The fee is a % of the total contract value,
        chosen by the tier whose ``min_days_before_event`` the
        cancellation still satisfies; the fee is capped at the amount
        paid and the refundable amount is what is left of the paid
        amount after the fee."""
        tiers = policy.get("cancellation_policy") or DEFAULT_CANCELLATION_POLICY
        if not isinstance(tiers, list) or not tiers:
            tiers = DEFAULT_CANCELLATION_POLICY
        fee_percent = 0
        for tier in sorted(tiers, key=lambda t: t.get("min_days_before_event", 0),
                           reverse=True):
            try:
                if days_before_event >= int(tier.get("min_days_before_event", 0)):
                    fee_percent = int(tier.get("fee_percent_of_total", 0))
                    break
            except (TypeError, ValueError):
                continue
        fee = min(paid_amount, round(total_amount * fee_percent / 100))
        return max(0, fee), max(0, paid_amount - fee)

    @staticmethod
    def no_show_fee_for(policy: dict, *, total_amount: int,
                        paid_amount: int, deposit_paid: int) -> tuple[int, int]:
        """(fee, refundable) for a no-show under ``policy``
        (decision A-44). Default: the deposit is forfeited, nothing is
        refundable, and the unpaid balance stays outstanding."""
        terms = policy.get("no_show_policy") or DEFAULT_NO_SHOW_POLICY
        if not isinstance(terms, dict):
            terms = DEFAULT_NO_SHOW_POLICY
        if terms.get("fee_basis") == "deposit":
            fee = min(paid_amount, deposit_paid)
        else:
            try:
                fee = min(paid_amount, round(
                    total_amount * int(terms.get("fee_percent_of_total", 0)) / 100))
            except (TypeError, ValueError):
                fee = min(paid_amount, deposit_paid)
        try:
            refundable = max(0, round(
                (paid_amount - fee) * int(terms.get("refundable_percent", 0)) / 100))
        except (TypeError, ValueError):
            refundable = 0
        return max(0, fee), refundable
