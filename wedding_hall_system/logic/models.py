"""Domain models (Phase 1, logic tier).

Plain dataclasses with field validation only — no SQL, no Gradio.
Phase 1 models: Employee, Role, Permission, Session. Phase 2 adds
Hall, EventType, Package, Service, PricingRule and the pricing
breakdown value objects.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from ..utils.exceptions import ValidationError
from ..utils.validators import (
    egp_to_piasters,  # noqa: F401  (re-exported for services)
    normalize_email,
    normalize_phone,
    require_text,
    validate_amount_piasters,
    validate_positive_int,
    validate_time_hhmm,
    validate_username,
    validate_weekday,
)


@dataclass
class Role:
    id: int | None = None
    name: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        self.name = require_text(self.name, "Role name", max_length=50)


@dataclass
class Permission:
    id: int | None = None
    code: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        self.code = require_text(self.code, "Permission code", max_length=100)


@dataclass
class Employee:
    """One row = one login account AND one staff profile (GAP G-20)."""

    id: int | None = None
    username: str = ""
    full_name: str = ""
    phone: str = ""
    email: str = ""
    specialties: str = ""  # comma-separated, SRS §7.1 (schema v2)
    is_active: bool = True
    roles: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.username = validate_username(self.username)
        self.full_name = require_text(self.full_name, "Full name", max_length=200)
        self.phone = normalize_phone(self.phone) if self.phone else ""
        self.email = normalize_email(self.email) if self.email else ""

    @property
    def display_name(self) -> str:
        return self.full_name or self.username

    def has_role(self, role: str) -> bool:
        return role in self.roles


@dataclass
class Session:
    id: int | None = None
    employee_id: int | None = None
    token_hash: str = ""
    expires_at: datetime | None = None

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return True
        return (now or datetime.now()) >= self.expires_at


# ------------------------------------------------------------------ Phase 2
# Hall / catalog models. Money is integer piasters everywhere
# (ARCHITECTURE_REVIEW §2 rule 5). Status values match the schema CHECKs.

HALL_STATUSES = ("active", "inactive", "maintenance")
SERVICE_PRICING_METHODS = ("per_event", "per_hour", "per_guest", "by_level")


@dataclass
class Hall:
    """A bookable hall (SRS §4.1). ``is_active`` is the soft-delete flag:
    deactivating a hall never deletes historical bookings."""

    id: int | None = None
    name: str = ""
    description: str = ""
    capacity: int = 1
    base_price: int = 0  # piasters, per event
    status: str = "active"
    facilities: list[str] = field(default_factory=list)
    notes: str = ""
    is_active: bool = True

    def __post_init__(self) -> None:
        self.name = require_text(self.name, "Hall name", max_length=100)
        self.description = str(self.description or "")
        self.capacity = validate_positive_int(self.capacity, "Capacity")
        self.base_price = validate_amount_piasters(self.base_price, "Base price")
        if self.status not in HALL_STATUSES:
            raise ValidationError(f"Hall status must be one of {HALL_STATUSES}.")
        self.facilities = [str(f).strip() for f in (self.facilities or []) if str(f).strip()]
        self.notes = str(self.notes or "")

    @property
    def is_bookable(self) -> bool:
        return self.is_active and self.status == "active"

    def facilities_json(self) -> str:
        return json.dumps(self.facilities, ensure_ascii=False)


@dataclass
class HallWeeklyHours:
    id: int | None = None
    hall_id: int | None = None
    weekday: int = 0  # Monday=0 .. Sunday=6
    open_time: str = "12:00"
    close_time: str = "23:00"
    is_closed: bool = False

    def __post_init__(self) -> None:
        self.weekday = validate_weekday(self.weekday)
        if not self.is_closed:
            self.open_time = validate_time_hhmm(self.open_time, "Opening time")
            self.close_time = validate_time_hhmm(self.close_time, "Closing time")
            if self.close_time <= self.open_time:
                raise ValidationError("Closing time must be after opening time.")


@dataclass
class HallSpecialHours:
    id: int | None = None
    hall_id: int | None = None
    date: str = ""  # YYYY-MM-DD
    open_time: str | None = None
    close_time: str | None = None
    is_closed: bool = False
    reason: str = ""

    def __post_init__(self) -> None:
        from ..utils.validators import validate_date_yyyy_mm_dd

        self.date = validate_date_yyyy_mm_dd(self.date, "Date")
        if self.is_closed:
            self.open_time = None
            self.close_time = None
        else:
            self.open_time = validate_time_hhmm(self.open_time, "Opening time")
            self.close_time = validate_time_hhmm(self.close_time, "Closing time")
            if self.close_time <= self.open_time:
                raise ValidationError("Closing time must be after opening time.")
        self.reason = str(self.reason or "")


@dataclass
class MaintenanceBlock:
    id: int | None = None
    hall_id: int | None = None
    type: str = ""
    description: str = ""
    start_at: datetime | None = None
    end_at: datetime | None = None
    recorded_by: int | None = None

    def __post_init__(self) -> None:
        self.type = require_text(self.type, "Maintenance type", max_length=100)
        self.description = str(self.description or "")
        if self.start_at is None or self.end_at is None:
            raise ValidationError("Maintenance needs a start and an end.")
        if self.end_at <= self.start_at:
            raise ValidationError("Maintenance end must be after its start.")


@dataclass
class EventType:
    id: int | None = None
    name: str = ""
    default_duration_min: int = 120
    is_active: bool = True

    def __post_init__(self) -> None:
        self.name = require_text(self.name, "Event type name", max_length=100)
        self.default_duration_min = validate_positive_int(
            self.default_duration_min, "Default duration (minutes)"
        )


@dataclass
class Package:
    """A configurable package (SRS §3.6). Prices in piasters."""

    id: int | None = None
    name: str = ""
    description: str = ""
    base_price: int = 0
    default_duration_min: int = 120
    min_duration_min: int = 60
    included_guests: int = 0
    extra_guest_price: int = 0
    is_active: bool = True
    hall_ids: list[int] = field(default_factory=list)
    event_type_ids: list[int] = field(default_factory=list)
    service_ids: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.name = require_text(self.name, "Package name", max_length=100)
        self.description = str(self.description or "")
        self.base_price = validate_amount_piasters(self.base_price, "Base price")
        self.default_duration_min = validate_positive_int(
            self.default_duration_min, "Default duration (minutes)"
        )
        self.min_duration_min = validate_positive_int(
            self.min_duration_min, "Minimum duration (minutes)"
        )
        if self.min_duration_min > self.default_duration_min:
            raise ValidationError("Minimum duration cannot exceed the default duration.")
        if self.included_guests < 0:
            raise ValidationError("Included guest count cannot be negative.")
        self.extra_guest_price = validate_amount_piasters(
            self.extra_guest_price, "Extra guest price"
        )


@dataclass
class Service:
    """One catalog service (SRS §3.6). ``unit_price`` is piasters per unit
    of ``pricing_method``: per event, per hour, per guest, or per level."""

    id: int | None = None
    name: str = ""
    description: str = ""
    pricing_method: str = "per_event"
    unit_price: int = 0
    is_active: bool = True
    hall_ids: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.name = require_text(self.name, "Service name", max_length=100)
        self.description = str(self.description or "")
        if self.pricing_method not in SERVICE_PRICING_METHODS:
            raise ValidationError(f"Pricing method must be one of {SERVICE_PRICING_METHODS}.")
        self.unit_price = validate_amount_piasters(self.unit_price, "Unit price")


@dataclass
class PricingRule:
    """One dynamic-pricing rule (SRS §3.7). A rule matches when every
    non-null criterion matches (hall/package/event type/weekday/season).
    ``adjustment_value`` is signed: positive = surcharge, negative =
    reduction. For ``percent`` it is whole percentage points."""

    id: int | None = None
    name: str = ""
    hall_id: int | None = None
    package_id: int | None = None
    event_type_id: int | None = None
    weekday: int | None = None
    season: str = ""
    adjustment_type: str = "percent"
    adjustment_value: int = 0
    is_active: bool = True

    def __post_init__(self) -> None:
        self.name = require_text(self.name, "Rule name", max_length=100)
        if self.weekday is not None:
            self.weekday = validate_weekday(self.weekday)
        if self.adjustment_type not in ("percent", "fixed"):
            raise ValidationError("Adjustment type must be 'percent' or 'fixed'.")
        if not isinstance(self.adjustment_value, int) or isinstance(self.adjustment_value, bool):
            raise ValidationError("Adjustment value must be a whole number.")
        if self.adjustment_type == "percent" and not -100 <= self.adjustment_value <= 100:
            raise ValidationError("A percent adjustment must be between -100 and 100.")
        self.season = str(self.season or "")

    def matches(self, *, hall_id: int | None, package_id: int | None,
                event_type_id: int | None, weekday: int | None,
                season: str = "") -> bool:
        if not self.is_active:
            return False
        if self.hall_id is not None and self.hall_id != hall_id:
            return False
        if self.package_id is not None and self.package_id != package_id:
            return False
        if self.event_type_id is not None and self.event_type_id != event_type_id:
            return False
        if self.weekday is not None and self.weekday != weekday:
            return False
        if self.season and self.season != (season or ""):
            return False
        return True


@dataclass
class ServiceSelection:
    """One service chosen for a price calculation. ``quantity`` is the
    count for per_event/by_level; ``duration_min`` is used for per_hour
    (event duration if omitted); per_guest uses the guest count."""

    service_id: int
    quantity: int = 1
    duration_min: int | None = None

    def __post_init__(self) -> None:
        self.quantity = validate_positive_int(self.quantity, "Service quantity")
        if self.duration_min is not None:
            self.duration_min = validate_positive_int(self.duration_min, "Service duration (minutes)")


@dataclass
class PriceItem:
    """One line of an itemised price breakdown. Amounts in piasters;
    negative amounts are discounts/reductions."""

    kind: str  # base | adjustment | service | extra_guests | discount
    label: str
    amount: int

    def __post_init__(self) -> None:
        self.kind = require_text(self.kind, "Price item kind", max_length=30)
        self.label = require_text(self.label, "Price item label", max_length=200)
        if not isinstance(self.amount, int) or isinstance(self.amount, bool):
            raise ValidationError("Price item amount must be a whole number of piasters.")


@dataclass
class PriceBreakdown:
    """Result of a price calculation (SRS §3.7): itemised lines plus the
    total. The total is the sum of the lines, never negative."""

    items: list[PriceItem] = field(default_factory=list)
    hall_id: int | None = None
    package_id: int | None = None
    event_type_id: int | None = None
    guests: int = 0
    duration_min: int = 0

    @property
    def total(self) -> int:
        return max(0, sum(item.amount for item in self.items))

    def total_by_kind(self, kind: str) -> int:
        return sum(item.amount for item in self.items if item.kind == kind)


# ------------------------------------------------------------------ Phase 3
# Customer / quotation / booking models (SRS §3.3, §3.5, §5). Money is
# integer piasters; datetimes are datetime objects in the venue
# timezone (repositories convert to/from ISO-8601 text).

BOOKING_STATUSES = ("pending", "confirmed", "in_progress", "completed",
                    "cancelled", "no_show", "expired")
QUOTATION_STATUSES = ("draft", "sent", "accepted", "expired")
PAYMENT_METHODS = ("cash", "bank_transfer", "visa_card", "instapay", "other")
INTERACTION_TYPES = ("call", "inquiry", "quotation", "booking", "cancellation",
                     "payment", "contract", "note")


def mask_national_id(national_id: str) -> str:
    """Mask a National ID for display (SRS §5.2, FR-012): never expose
    the full value outside a `customer.view_sensitive` check."""
    value = str(national_id or "").strip()
    if not value:
        return ""
    if len(value) <= 4:
        return "****"
    return "****" + value[-4:]


@dataclass
class Customer:
    """A customer record (SRS §5.1). ``phone``/``email`` are stored as
    entered; ``phone_normalized``/``email_normalized`` are the
    duplicate-detection keys (SRS §5.2)."""

    id: int | None = None
    full_name: str = ""
    phone: str = ""
    phone_normalized: str = ""
    email: str = ""
    email_normalized: str = ""
    address: str = ""
    national_id: str = ""
    notes: str = ""
    additional_phones: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.full_name = require_text(self.full_name, "Full name", max_length=200)
        self.phone = normalize_phone(self.phone) if self.phone else ""
        self.phone_normalized = normalize_phone(self.phone) if self.phone else ""
        self.email = normalize_email(self.email) if self.email else ""
        self.email_normalized = self.email
        self.address = str(self.address or "")
        self.national_id = str(self.national_id or "").strip()
        self.notes = str(self.notes or "")
        self.additional_phones = [
            normalize_phone(p) for p in (self.additional_phones or []) if str(p).strip()
        ]

    def masked_national_id(self) -> str:
        return mask_national_id(self.national_id)


@dataclass
class CustomerInteraction:
    """One internal history entry for a customer (SRS §5.3)."""

    id: int | None = None
    customer_id: int | None = None
    employee_id: int | None = None
    type: str = "note"
    summary: str = ""

    def __post_init__(self) -> None:
        if self.type not in INTERACTION_TYPES:
            raise ValidationError(f"Interaction type must be one of {INTERACTION_TYPES}.")
        self.summary = require_text(self.summary, "Interaction summary", max_length=1000)


@dataclass
class Quotation:
    """A price quotation (SRS §3.5). Checks availability but reserves
    nothing; the frozen price lives in ``quotation_items``."""

    id: int | None = None
    number: str = ""
    customer_id: int | None = None
    hall_id: int | None = None
    event_type_id: int | None = None
    package_id: int | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    guest_count: int = 1
    total_amount: int = 0
    status: str = "draft"
    valid_until: datetime | None = None
    created_by: int | None = None

    def __post_init__(self) -> None:
        if self.status not in QUOTATION_STATUSES:
            raise ValidationError(f"Quotation status must be one of {QUOTATION_STATUSES}.")
        if self.start_at is not None and self.end_at is not None and self.end_at <= self.start_at:
            raise ValidationError("Quotation end time must be after its start time.")
        self.guest_count = validate_positive_int(self.guest_count, "Guest count")
        self.total_amount = validate_amount_piasters(self.total_amount, "Total amount")

    @property
    def is_expired_by_time(self) -> bool:
        return self.valid_until is not None and datetime.now() >= self.valid_until


@dataclass
class QuotationItem:
    """One frozen price line of a quotation (kind/label/amount)."""

    id: int | None = None
    quotation_id: int | None = None
    kind: str = ""
    label: str = ""
    amount: int = 0

    def __post_init__(self) -> None:
        self.kind = require_text(self.kind, "Price item kind", max_length=30)
        self.label = require_text(self.label, "Price item label", max_length=200)
        if not isinstance(self.amount, int) or isinstance(self.amount, bool):
            raise ValidationError("Price item amount must be a whole number of piasters.")


@dataclass
class Booking:
    """A booking (SRS §3.1–§3.4). Single current status; every
    transition is recorded in ``booking_history``. ``Rescheduled`` is a
    timeline event, never a status (SRS §3.3 normalization)."""

    id: int | None = None
    number: str = ""
    customer_id: int | None = None
    hall_id: int | None = None
    quotation_id: int | None = None
    event_type_id: int | None = None
    package_id: int | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    guest_count: int = 1
    status: str = "pending"
    total_amount: int = 0
    deposit_required: int = 0
    deposit_paid: int = 0
    hold_expires_at: datetime | None = None
    grace_expires_at: datetime | None = None
    additional_contact_phone: str = ""
    notes: str = ""
    previous_booking_id: int | None = None
    created_by: int | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.status not in BOOKING_STATUSES:
            raise ValidationError(f"Booking status must be one of {BOOKING_STATUSES}.")
        if self.start_at is not None and self.end_at is not None and self.end_at <= self.start_at:
            raise ValidationError("Booking end time must be after its start time.")
        self.guest_count = validate_positive_int(self.guest_count, "Guest count")
        self.total_amount = validate_amount_piasters(self.total_amount, "Total amount")
        self.deposit_required = validate_amount_piasters(self.deposit_required, "Deposit required")
        self.deposit_paid = validate_amount_piasters(self.deposit_paid, "Deposit paid")
        self.additional_contact_phone = (
            normalize_phone(self.additional_contact_phone)
            if self.additional_contact_phone else ""
        )
        self.notes = str(self.notes or "")

    @property
    def is_active_hold(self) -> bool:
        """A Pending booking whose hold has not yet expired."""
        return self.status == "pending" and (
            self.hold_expires_at is None or datetime.now() < self.hold_expires_at
        )

    @property
    def outstanding(self) -> int:
        return max(0, self.total_amount - self.deposit_paid)


@dataclass
class BookingHistory:
    """One timeline entry for a booking (SRS §12.2)."""

    id: int | None = None
    booking_id: int | None = None
    employee_id: int | None = None
    action: str = ""
    old_value: str = ""
    new_value: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        self.action = require_text(self.action, "History action", max_length=100)
        self.old_value = str(self.old_value or "")
        self.new_value = str(self.new_value or "")
        self.reason = str(self.reason or "")


@dataclass
class Payment:
    """One recorded payment (SRS §8.1–§8.2). Immutable once recorded;
    corrections go through ``payment_corrections`` (Phase 5)."""

    id: int | None = None
    number: str = ""
    booking_id: int | None = None
    amount: int = 0
    method: str = "cash"
    reference: str = ""
    status: str = "recorded"
    idempotency_key: str | None = None
    initiated_at: datetime | None = None
    confirmed_at: datetime | None = None
    recorded_by: int | None = None

    def __post_init__(self) -> None:
        if self.amount <= 0:
            raise ValidationError("Payment amount must be greater than zero.")
        if self.method not in PAYMENT_METHODS:
            raise ValidationError(f"Payment method must be one of {PAYMENT_METHODS}.")
        self.reference = str(self.reference or "")
        if self.status not in ("recorded", "reversed", "void"):
            raise ValidationError("Payment status must be recorded, reversed or void.")


# ------------------------------------------------------------------ Phase 5
# Financial management (SRS §8). Money is integer piasters; datetimes
# are datetime objects in the venue timezone.

EXPENSE_CATEGORIES = (
    "Maintenance", "Utilities", "Supplies", "Staff", "Marketing", "Other",
)
EXPENSE_SCOPES = ("venue", "hall", "event")
INSTALLMENT_STATUSES = ("pending", "paid", "overdue")
REFUND_STATUSES = ("pending_approval", "approved", "paid", "rejected", "cancelled")


@dataclass
class Installment:
    """One installment of a booking's remaining balance (SRS §8.4).
    A plan is created once per Confirmed booking; amounts are frozen
    at creation and never rewritten by later price changes."""

    id: int | None = None
    booking_id: int | None = None
    sequence: int = 1
    amount: int = 0
    due_at: datetime | None = None
    status: str = "pending"
    paid_payment_id: int | None = None
    # Computed display value, never charged automatically (SRS §8.4
    # lists late fees as policy; the system never auto-charges, A-46).
    late_fee_piasters: int = 0

    def __post_init__(self) -> None:
        if self.amount <= 0:
            raise ValidationError("Installment amount must be greater than zero.")
        if self.sequence < 1:
            raise ValidationError("Installment sequence must be positive.")
        if self.status not in INSTALLMENT_STATUSES:
            raise ValidationError(
                f"Installment status must be one of {INSTALLMENT_STATUSES}.")


@dataclass
class Expense:
    """One venue expense (SRS §8.5). Approval status and payment status
    are kept separate: an expense can be approved but unpaid, or paid
    only after approval."""

    id: int | None = None
    amount: int = 0
    date: str = ""  # YYYY-MM-DD
    category: str = "Other"
    description: str = ""
    method: str = "cash"
    scope_type: str = "venue"
    scope_id: int | None = None
    recorded_by: int | None = None
    approval_status: str = "pending"
    payment_status: str = "pending"

    def __post_init__(self) -> None:
        if self.amount <= 0:
            raise ValidationError("Expense amount must be greater than zero.")
        if self.category not in EXPENSE_CATEGORIES:
            raise ValidationError(
                f"Expense category must be one of {EXPENSE_CATEGORIES}.")
        if self.method not in PAYMENT_METHODS:
            raise ValidationError(f"Expense method must be one of {PAYMENT_METHODS}.")
        if self.scope_type not in EXPENSE_SCOPES:
            raise ValidationError(
                f"Expense scope must be one of {EXPENSE_SCOPES}.")
        if self.scope_type == "venue" and self.scope_id is not None:
            raise ValidationError("A venue-scope expense has no scope id.")
        if self.scope_type != "venue" and self.scope_id is None:
            raise ValidationError(
                f"A {self.scope_type}-scope expense needs a hall/booking id.")
        self.date = str(self.date or "")
        self.description = str(self.description or "")
        if self.approval_status not in ("pending", "approved", "rejected"):
            raise ValidationError(
                "Expense approval status must be pending, approved or rejected.")
        if self.payment_status not in ("pending", "paid"):
            raise ValidationError(
                "Expense payment status must be pending or paid.")


@dataclass
class Refund:
    """One refund (SRS §8.7). A separate transaction linked to the
    original payment and booking; the original payment is never edited.
    Workflow: pending_approval -> approved -> paid (or rejected);
    cancellable before Paid only."""

    id: int | None = None
    payment_id: int | None = None
    booking_id: int | None = None
    amount: int = 0
    reason: str = ""
    method: str = "cash"
    status: str = "pending_approval"
    created_by: int | None = None
    approved_by: int | None = None
    approved_at: datetime | None = None
    paid_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.amount <= 0:
            raise ValidationError("Refund amount must be greater than zero.")
        if self.method not in PAYMENT_METHODS:
            raise ValidationError(f"Refund method must be one of {PAYMENT_METHODS}.")
        self.reason = str(self.reason or "")
        if self.status not in REFUND_STATUSES:
            raise ValidationError(
                f"Refund status must be one of {REFUND_STATUSES}.")


# ------------------------------------------------------------------ Phase 4
# Contracts / operations / notifications (SRS §6, §7, §10, §11).
# Contract bodies are rendered HTML (A-38: no PDF library permitted yet,
# so contracts are versioned printable HTML documents). Money is integer
# piasters; datetimes are datetime objects in the venue timezone.

CONTRACT_SIGNATURE_STATUSES = ("draft", "pending_signature", "partially_signed",
                              "signed", "cancelled")
TASK_STATUSES = ("pending", "in_progress", "completed")
TASK_PRIORITIES = ("low", "medium", "high", "urgent")
NOTIFICATION_PRIORITIES = ("low", "normal", "high", "urgent")
NOTIFICATION_TYPES = (
    "new_booking", "hold_expiring", "booking_cancelled", "booking_rescheduled",
    "booking_services_changed",
    "payment_received", "installment_due", "event_approaching",
    "task_due_soon", "task_overdue", "task_assigned", "assignment_conflict",
    "contract_awaiting_signature", "hall_maintenance",
)
ATTACHMENT_CATEGORIES = (
    "Identity Documents", "Contracts", "Receipts", "Event Photos",
    "Customer Documents", "Other",
)
# SRS §11 allowlist: never store executables; preview only safe types.
ALLOWED_UPLOAD_EXTENSIONS = ("pdf", "jpg", "jpeg", "png")

# Placeholders a contract template may use (validated at template save).
CONTRACT_PLACEHOLDERS = (
    "contract_number", "date", "venue_name", "customer_name",
    "customer_phone", "customer_address", "customer_national_id",
    "hall_name", "event_type", "package_name", "event_date",
    "event_start", "event_end", "guest_count", "total_amount",
    "deposit_required", "deposit_paid", "outstanding",
    "hold_hours", "grace_hours", "deposit_percent", "booking_number",
)


@dataclass
class Contract:
    """A contract for one booking (SRS §6). One row per booking contract;
    the rendered document lives in versioned ``contract_versions`` rows.
    ``signature_date`` is the date the signed copy was signed (schema v2)."""

    id: int | None = None
    number: str = ""
    booking_id: int | None = None
    template_name: str = ""
    current_version_id: int | None = None
    signature_status: str = "draft"
    signature_date: str = ""  # YYYY-MM-DD

    def __post_init__(self) -> None:
        self.number = require_text(self.number, "Contract number", max_length=50)
        self.template_name = str(self.template_name or "")
        if self.signature_status not in CONTRACT_SIGNATURE_STATUSES:
            raise ValidationError(
                f"Signature status must be one of {CONTRACT_SIGNATURE_STATUSES}.")
        self.signature_date = str(self.signature_date or "")


@dataclass
class ContractVersion:
    """One frozen version of a contract document (SRS §6). Insert-only:
    editing creates a new version; old versions are never rewritten."""

    id: int | None = None
    contract_id: int | None = None
    version: int = 1
    body: str = ""  # rendered HTML document
    created_by: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.version, int) or self.version < 1:
            raise ValidationError("Contract version must be a positive number.")
        self.body = str(self.body or "")


@dataclass
class Attachment:
    """Metadata for one uploaded file (SRS §11). Bytes live under
    ``storage/uploads/`` under a random storage name; this row is the
    only link between the stored file and the business record."""

    id: int | None = None
    entity_type: str = ""
    entity_id: int | None = None
    original_filename: str = ""
    storage_filename: str = ""
    mime_type: str = ""
    size_bytes: int = 0
    category: str = "Other"
    uploaded_by: int | None = None
    is_deleted: bool = False

    def __post_init__(self) -> None:
        self.entity_type = require_text(self.entity_type, "Entity type", max_length=50)
        self.original_filename = require_text(
            self.original_filename, "Original filename", max_length=255)
        self.storage_filename = require_text(
            self.storage_filename, "Storage filename", max_length=255)
        self.mime_type = str(self.mime_type or "")
        if not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise ValidationError("Attachment size must be zero or more.")
        if self.category not in ATTACHMENT_CATEGORIES:
            raise ValidationError(f"Attachment category must be one of {ATTACHMENT_CATEGORIES}.")


@dataclass
class EmployeeAvailability:
    """One availability / time-off entry for an employee (SRS §7.1).
    ``kind`` 'available' is an explicit availability window; the other
    kinds block assignment (warn-only, never a hard block — SRS §7.4)."""

    id: int | None = None
    employee_id: int | None = None
    date: str = ""  # YYYY-MM-DD
    start_time: str | None = None  # HH:MM or None (whole day)
    end_time: str | None = None
    kind: str = "available"
    note: str = ""

    def __post_init__(self) -> None:
        from ..utils.validators import validate_date_yyyy_mm_dd

        self.date = validate_date_yyyy_mm_dd(self.date, "Date")
        if self.kind not in ("available", "unavailable", "time_off"):
            raise ValidationError("Availability kind must be available, unavailable or time_off.")
        if self.start_time is not None:
            self.start_time = validate_time_hhmm(self.start_time, "Start time")
        if self.end_time is not None:
            self.end_time = validate_time_hhmm(self.end_time, "End time")
        if (self.start_time is not None and self.end_time is not None
                and self.end_time <= self.start_time):
            raise ValidationError("Availability end time must be after its start time.")
        self.note = str(self.note or "")


@dataclass
class EventPlan:
    """One event plan per booking (SRS §7.2). One row per booking; the
    checklist lives in ``tasks`` rows linked to this plan."""

    id: int | None = None
    booking_id: int | None = None


@dataclass
class TaskTemplate:
    """A reusable task checklist template (SRS §7.2). Applies to bookings
    whose event type and/or package match; a null criterion matches all."""

    id: int | None = None
    name: str = ""
    event_type_id: int | None = None
    package_id: int | None = None
    is_active: bool = True

    def __post_init__(self) -> None:
        self.name = require_text(self.name, "Template name", max_length=100)


@dataclass
class TaskTemplateItem:
    """One checklist line of a task template. ``default_due_offset_min``
    is minutes BEFORE the event start at which the task is due."""

    id: int | None = None
    template_id: int | None = None
    name: str = ""
    description: str = ""
    default_due_offset_min: int = 0
    priority: str = "medium"

    def __post_init__(self) -> None:
        self.name = require_text(self.name, "Task name", max_length=100)
        self.description = str(self.description or "")
        if not isinstance(self.default_due_offset_min, int) or self.default_due_offset_min < 0:
            raise ValidationError("Due offset must be zero or more minutes.")
        if self.priority not in TASK_PRIORITIES:
            raise ValidationError(f"Task priority must be one of {TASK_PRIORITIES}.")


@dataclass
class Task:
    """One operational task (SRS §7.2–§7.3). ``due_at`` is a point in
    time; ``service_id`` links the task to a catalog service when the
    work is that service's delivery."""

    id: int | None = None
    event_plan_id: int | None = None
    booking_id: int | None = None
    service_id: int | None = None
    name: str = ""
    description: str = ""
    assigned_to: int | None = None
    priority: str = "medium"
    due_at: datetime | None = None
    status: str = "pending"

    def __post_init__(self) -> None:
        self.name = require_text(self.name, "Task name", max_length=100)
        self.description = str(self.description or "")
        if self.priority not in TASK_PRIORITIES:
            raise ValidationError(f"Task priority must be one of {TASK_PRIORITIES}.")
        if self.status not in TASK_STATUSES:
            raise ValidationError(f"Task status must be one of {TASK_STATUSES}.")


@dataclass
class TaskHistory:
    """One status/assignment change of a task (SRS §7.3)."""

    id: int | None = None
    task_id: int | None = None
    employee_id: int | None = None
    action: str = ""
    old_value: str = ""
    new_value: str = ""

    def __post_init__(self) -> None:
        self.action = require_text(self.action, "History action", max_length=100)
        self.old_value = str(self.old_value or "")
        self.new_value = str(self.new_value or "")


@dataclass
class Notification:
    """One in-app notification (SRS §10). Targeted at one employee, one
    role, or both; role-targeted rows are visible to every holder of
    that role. No per-user configuration exists (SRS §10)."""

    id: int | None = None
    recipient_role_id: int | None = None
    recipient_employee_id: int | None = None
    type: str = ""
    title: str = ""
    body: str = ""
    priority: str = "normal"
    entity_type: str = ""
    entity_id: int | None = None
    is_read: bool = False

    def __post_init__(self) -> None:
        if self.type not in NOTIFICATION_TYPES:
            raise ValidationError(f"Notification type must be one of {NOTIFICATION_TYPES}.")
        self.title = require_text(self.title, "Notification title", max_length=200)
        self.body = str(self.body or "")
        if self.priority not in NOTIFICATION_PRIORITIES:
            raise ValidationError(f"Notification priority must be one of {NOTIFICATION_PRIORITIES}.")
        self.entity_type = str(self.entity_type or "")
