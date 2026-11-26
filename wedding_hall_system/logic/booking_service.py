"""Booking lifecycle (Phase 3, business layer — SRS §3.1–§3.4, §8.1–§8.2,
UC-03/UC-07).

The critical rule (SRS §3.2, GAP G-12): booking creation validates
and inserts inside ONE ``BEGIN IMMEDIATE`` transaction and re-checks
availability on the SAME connection immediately before insert, so two
concurrent attempts cannot both succeed. There is no override path.

Confirmation is automatic once a valid deposit is recorded and the
hold/grace conditions and hall availability pass (SRS §3.4) — no
separate managerial approval. Holds expire lazily on access (GAP G-11):
every creation/read sweeps overdue holds first.
"""
from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from ..config.settings import AppSettings, format_money
from ..data.database import DatabaseManager
from ..data.repositories import booking_repository as repo
from ..data.repositories import catalog_repository as catalog_repo
from ..data.repositories import contract_repository as contract_repo
from ..data.repositories import customer_repository as customer_repo
from ..data.repositories import finance_repository as finance_repo
from ..data.repositories import notification_repository as notification_repo
from ..data.repositories import operations_repository as ops_repo
from ..data.repositories import system_repository
from ..utils.exceptions import (
    BookingConflictError,
    NotFoundError,
    PaymentError,
    ValidationError,
)
from ..utils.logging_config import get_logger
from . import audit_service
from .availability_service import AvailabilityService, expire_overdue_holds
from .models import (
    Booking,
    BookingHistory,
    Hall,
    Payment,
    ServiceSelection,
)
from .notification_service import notify
from .permissions import require_permission
from .pricing_service import PricingService

logger = get_logger(__name__)

# Lifecycle transitions allowed by this phase (SRS §3.3). `Rescheduled`
# is a timeline event, never a status. Reopening a Cancelled/Expired
# booking is forbidden (SRS §8.11, BR-17) — create a new booking
# linked via previous_booking_id instead (Phase 5 owns the full flow).
CANCELLABLE_STATUSES = ("pending", "confirmed")


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _setting_int(db: DatabaseManager, key: str, default: int) -> int:
    try:
        return int(system_repository.get_setting(db, key, str(default)))
    except ValueError:
        return default


def _row_to_booking(row: dict) -> Booking:
    return Booking(
        id=row["id"], number=row["number"], customer_id=row["customer_id"],
        hall_id=row["hall_id"], quotation_id=row["quotation_id"],
        event_type_id=row["event_type_id"], package_id=row["package_id"],
        start_at=_parse(row["start_at"]), end_at=_parse(row["end_at"]),
        guest_count=row["guest_count"], status=row["status"],
        total_amount=row["total_amount"],
        deposit_required=row["deposit_required"],
        deposit_paid=row["deposit_paid"],
        hold_expires_at=_parse(row["hold_expires_at"]),
        grace_expires_at=_parse(row["grace_expires_at"]),
        additional_contact_phone=row["additional_contact_phone"],
        notes=row["notes"], previous_booking_id=row["previous_booking_id"],
        created_by=row["created_by"], created_at=_parse(row["created_at"]),
    )


def _row_to_payment(row: dict) -> Payment:
    return Payment(
        id=row["id"], number=row["number"], booking_id=row["booking_id"],
        amount=row["amount"], method=row["method"], reference=row["reference"],
        status=row["status"], idempotency_key=row["idempotency_key"],
        initiated_at=_parse(row["initiated_at"]),
        confirmed_at=_parse(row["confirmed_at"]),
        recorded_by=row["recorded_by"],
    )


class BookingService:
    def __init__(self, db: DatabaseManager, settings: AppSettings | None = None) -> None:
        self.db = db
        self.settings = settings or AppSettings.from_env()
        self.availability = AvailabilityService(db, settings)
        self.pricing = PricingService(db, settings)

    # -- helpers ------------------------------------------------------------
    def _next_number(self, prefix: str, conn) -> str:
        today = datetime.now().strftime("%Y%m%d")
        day_prefix = f"{prefix}-{today}-"
        seq = repo.count_bookings_on_day(self.db, day_prefix, conn=conn) + 1
        return f"{day_prefix}{seq:04d}"

    def _policy(self) -> dict:
        return {
            "hold_hours": _setting_int(self.db, "booking.hold_hours",
                                        self.settings.hold_hours),
            "grace_hours": _setting_int(self.db, "booking.grace_hours",
                                         self.settings.grace_hours),
            "deposit_percent": _setting_int(self.db, "booking.deposit_percent",
                                            self.settings.deposit_percent),
        }

    def _get_or_raise(self, booking_id: int, conn=None) -> dict:
        row = repo.get_booking(self.db, booking_id, conn=conn)
        if row is None:
            raise NotFoundError(f"Booking #{booking_id} does not exist.")
        return row

    def sweep_expired_holds(self, now: datetime | None = None) -> list[int]:
        """Expire overdue Pending holds (lazy expiry, GAP G-11) and fire
        the time-based notifications that have no background process to
        emit them (decision A-50): hold-expiring (hold ends within 6h)
        and event-approaching (Confirmed event starts within 48h).
        Each fires at most once per booking per 24h (de-duplicated on
        the notifications table)."""
        expired = expire_overdue_holds(self.db, now=now)
        try:
            self._notify_time_based(now or datetime.now())
        except Exception:  # pragma: no cover - notifications are best-effort
            logger.warning("Time-based notification sweep failed.")
        return expired

    def _notify_time_based(self, now: datetime) -> None:
        """Fire hold-expiring / event-approaching warnings once per
        booking per 24h (FR-060, lazy — GAP G-11)."""
        # A-81: the dedup window is measured against
        # notifications.created_at, which is SQLite CURRENT_TIMESTAMP
        # (UTC). It must therefore be computed from UTC now, in the
        # same 'YYYY-MM-DD HH:MM:SS' format — a local-time window both
        # shrank the effective window to ~22h (A-50) and, combined
        # with the 'T' separator of .isoformat(), broke the dedup
        # entirely 00:00–02:00 Africa/Cairo. ``now`` (local) is still
        # used for the hold/event windows below, which compare
        # against naive-local stored values.
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
            "%Y-%m-%d %H:%M:%S")
        hold_soon = repo.list_pending_holds_expiring_soon(
            self.db, _iso(now), _iso(now + timedelta(hours=6)))
        for booking in hold_soon:
            if notification_repo.exists_notification(
                    self.db, type="hold_expiring", entity_type="booking",
                    entity_id=int(booking["id"]), since_iso=since):
                continue
            notify(self.db, type="hold_expiring",
                   title=f"Hold expiring — booking {booking['number']}",
                   body=f"Hold expires at {booking['hold_expires_at']}; "
                        f"record the deposit or the booking expires.",
                   title_key="notif.hold_expiring.title",
                   body_key="notif.hold_expiring.body",
                   data={"number": booking["number"],
                         "when": booking["hold_expires_at"]},
                   entity_type="booking", entity_id=int(booking["id"]))
        starting = repo.list_confirmed_starting_soon(
            self.db, _iso(now), _iso(now + timedelta(hours=48)))
        for booking in starting:
            if notification_repo.exists_notification(
                    self.db, type="event_approaching", entity_type="booking",
                    entity_id=int(booking["id"]), since_iso=since):
                continue
            notify(self.db, type="event_approaching",
                   title=f"Event approaching — booking {booking['number']}",
                   body=f"Event starts {booking['start_at']} in hall "
                        f"#{booking['hall_id']}.",
                   title_key="notif.event_approaching.title",
                   body_key="notif.event_approaching.body",
                   data={"number": booking["number"],
                         "when": booking["start_at"],
                         "hall": booking["hall_id"]},
                   entity_type="booking", entity_id=int(booking["id"]))

    # -- creation -----------------------------------------------------------
    def create_booking(self, employee, *, customer_id: int, hall_id: int,
                       event_type_id: int | None = None,
                       package_id: int | None = None,
                       start_at: datetime, end_at: datetime, guests: int,
                       quotation_id: int | None = None,
                       service_selections: list[ServiceSelection] | None = None,
                       discount: dict | None = None,
                       notes: str = "", additional_contact_phone: str = "",
                       previous_booking_id: int | None = None) -> Booking:
        """Create a Pending booking and start its hold (UC-03 step 1).

        Validation + insert run inside ONE BEGIN IMMEDIATE transaction
        with an availability re-check on the same connection immediately
        before insert (SRS §3.2). If ``quotation_id`` is given, the
        booking is a conversion of an Accepted quotation: its frozen
        price is used (never re-priced) and its parameters must match.
        ``previous_booking_id`` links a re-booking to a Cancelled or
        Expired booking (SRS §8.11, FR-055): reopening is forbidden,
        a new booking is created instead, and old contracts/payments do
        not transfer.
        """
        require_permission(self.db, employee, "booking.create")
        if customer_repo.get_customer(self.db, customer_id) is None:
            raise NotFoundError(f"Customer #{customer_id} does not exist.")
        if previous_booking_id is not None:
            previous = repo.get_booking(self.db, previous_booking_id)
            if previous is None:
                raise NotFoundError(
                    f"Previous booking #{previous_booking_id} does not exist.")
            if previous["status"] not in ("cancelled", "expired"):
                raise ValidationError(
                    "A new booking can only be linked to a Cancelled or "
                    f"Expired booking (previous booking is "
                    f"{previous['status']}).")
        if end_at <= start_at:
            raise ValidationError("End time must be after start time.")
        hall_row = catalog_repo.get_hall(self.db, hall_id)
        if hall_row is None:
            raise NotFoundError(f"Hall #{hall_id} does not exist.")
        hall = Hall(**{k: v for k, v in hall_row.items() if k in Hall.__dataclass_fields__})

        quotation_row: dict | None = None
        frozen_items: list[dict] | None = None
        total: int
        if quotation_id is not None:
            quotation_row = repo.get_quotation(self.db, quotation_id)
            if quotation_row is None:
                raise NotFoundError(f"Quotation #{quotation_id} does not exist.")
            if quotation_row["status"] != "accepted":
                raise ValidationError(
                    "Only an Accepted quotation can be converted to a booking "
                    f"(current status: {quotation_row['status']}).")
            if int(quotation_row["customer_id"]) != customer_id:
                raise ValidationError("Quotation belongs to a different customer.")
            if int(quotation_row["hall_id"]) != hall_id:
                raise ValidationError("Quotation is for a different hall.")
            if (quotation_row["start_at"] != _iso(start_at)
                    or quotation_row["end_at"] != _iso(end_at)
                    or int(quotation_row["guest_count"]) != guests):
                raise ValidationError(
                    "Booking time/guests must match the accepted quotation.")
            frozen_items = repo.list_quotation_items(self.db, quotation_id)
            total = int(quotation_row["total_amount"])
            package_id = quotation_row["package_id"]
            event_type_id = quotation_row["event_type_id"]
            service_selections = self._selections_from_frozen_items(frozen_items)

        duration_min = int((end_at - start_at).total_seconds() // 60)
        package_min = None
        if package_id is not None:
            package_row = catalog_repo.get_package(self.db, package_id)
            if package_row is None:
                raise NotFoundError(f"Package #{package_id} does not exist.")
            package_min = int(package_row["min_duration_min"])
        self.availability.validate_event_window(
            employee, hall=hall, start_at=start_at, end_at=end_at, guests=guests,
            package_min_duration_min=package_min, duration_min=duration_min)
        # Pre-check (read-only, fast feedback). The binding check runs
        # again inside the transaction — never rely on this one alone.
        pre = self.availability.check_availability(
            employee, hall_id=hall_id, start_at=start_at, end_at=end_at)
        if not pre.available:
            raise BookingConflictError(
                f"Hall is not available for the requested time: {pre.reason}")

        if quotation_row is None:
            breakdown = self.pricing.calculate(
                employee, hall_id=hall_id, package_id=package_id,
                event_type_id=event_type_id, start_at=start_at,
                duration_min=duration_min, guests=guests,
                service_selections=service_selections, discount=discount)
            total = breakdown.total
            price_items = [(i.kind, i.label, i.amount) for i in breakdown.items]
        else:
            price_items = [(i["kind"], i["label"], int(i["amount"]))
                           for i in frozen_items]

        policy = self._policy()
        deposit_required = round(total * policy["deposit_percent"] / 100)
        now = datetime.now()
        hold_expires = now + timedelta(hours=policy["hold_hours"])
        grace_expires = hold_expires + timedelta(hours=policy["grace_hours"])

        with self.db.transaction(immediate=True) as conn:
            expire_overdue_holds(self.db, now=now, conn=conn)
            # Binding re-check on the SAME connection, immediately before
            # insert (SRS §3.2, GAP G-12). Any failure rolls everything back.
            recheck = self.availability.check_availability(
                employee, hall_id=hall_id, start_at=start_at, end_at=end_at,
                conn=conn)
            if not recheck.available:
                raise BookingConflictError(
                    f"Hall is not available for the requested time: {recheck.reason}")
            number = self._next_number("BK", conn)
            booking_id = repo.insert_booking(
                self.db, number=number, customer_id=customer_id, hall_id=hall_id,
                quotation_id=quotation_id, event_type_id=event_type_id,
                package_id=package_id, start_at=_iso(start_at), end_at=_iso(end_at),
                guest_count=guests, total_amount=total,
                deposit_required=deposit_required,
                hold_expires_at=_iso(hold_expires), grace_expires_at=_iso(grace_expires),
                additional_contact_phone=additional_contact_phone or "",
                notes=notes or "", created_by=employee.id, conn=conn,
                # Same clock as the hold/grace timestamps (naive local):
                # the DB default CURRENT_TIMESTAMP is UTC, which made
                # hold - created_at read 50h instead of 48h in Cairo.
                created_at=_iso(now), previous_booking_id=previous_booking_id)
            for kind, label, amount in price_items:
                repo.insert_booking_price_item(
                    self.db, booking_id=booking_id, kind=kind, label=label,
                    amount=amount, conn=conn)
            self._insert_booking_services(
                conn, booking_id, service_selections or [], price_items)
            if discount is not None:
                discount_item = next(
                    (i for i in price_items if i[0] == "discount"), None)
                if discount_item is not None:
                    self._insert_discount_record(
                        conn, booking_id, employee.id, discount, -discount_item[2])
            elif quotation_row is not None:
                discount_item = next(
                    (i for i in price_items if i[0] == "discount"), None)
                if discount_item is not None:
                    reason = discount_item[1].split("—", 1)[-1].strip()
                    self._insert_discount_record(
                        conn, booking_id, employee.id,
                        {"type": "fixed", "value": -discount_item[2],
                         "reason": reason or "quotation discount"}, -discount_item[2])
            repo.insert_booking_history(
                self.db, booking_id=booking_id, employee_id=employee.id,
                action="created", old_value="", new_value="pending",
                reason=f"Booking {number} created; hold until {_iso(hold_expires)}.",
                conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="booking.created",
                entity_type="booking", entity_id=booking_id,
                new_value={"number": number, "hall_id": hall_id,
                           "start_at": _iso(start_at), "end_at": _iso(end_at),
                           "total_amount": total, "status": "pending",
                           "hold_expires_at": _iso(hold_expires)},
                description=f"Booking {number} created (Pending, hold 48h).",
                conn=conn)
            customer_repo.insert_interaction(
                self.db, customer_id=customer_id, employee_id=employee.id,
                type_="booking",
                summary=f"Booking {number} created for {start_at.date()} "
                        f"({format_money(total)}).", conn=conn)
            notify(self.db, type="new_booking",
                   title=f"New booking {number}",
                   body=f"Booking {number} created for {start_at.date()} "
                        f"({format_money(total)}).",
                   title_key="notif.new_booking.title",
                   body_key="notif.new_booking.body",
                   data={"number": number, "date": str(start_at.date()),
                         "amount": format_money(total)},
                   entity_type="booking", entity_id=booking_id, conn=conn)
        logger.info("Booking %s created (Pending) for customer #%s",
                    number, customer_id)
        created = self.get_booking(employee, booking_id)
        assert created is not None
        return created

    def _insert_booking_services(self, conn, booking_id: int,
                                 selections: list[ServiceSelection],
                                 price_items: list[tuple]) -> None:
        """Persist the chosen services. Service-kind price items appear
        in selection order, so they zip 1:1 with the selections."""
        service_items = [i for i in price_items if i[0] == "service"]
        for selection, item in zip(selections, service_items):
            service_row = catalog_repo.get_service(self.db, selection.service_id)
            if service_row is None:
                raise NotFoundError(f"Service #{selection.service_id} does not exist.")
            included = "included in package" in item[1]
            repo.insert_booking_service(
                self.db, booking_id=booking_id, service_id=selection.service_id,
                quantity=selection.quantity,
                unit_price=int(service_row["unit_price"]),
                amount=int(item[2]), is_included_in_package=included, conn=conn)

    @staticmethod
    def _insert_discount_record(conn, booking_id: int, employee_id: int | None,
                                discount: dict, amount: int) -> None:
        conn.execute(
            """INSERT INTO discount_records
               (booking_id, employee_id, amount, percent, reason)
               VALUES (?, ?, ?, ?, ?)""",
            (booking_id, employee_id, amount,
             discount.get("value") if discount.get("type") == "percent" else None,
             str(discount.get("reason") or "")),
        )

    def _selections_from_frozen_items(
            self, frozen_items: list[dict]) -> list[ServiceSelection]:
        """Recover service selections from a quotation's frozen price
        items (decision A-29). Labels are written by
        PricingService._service_label / the included-service marker, so
        the name and quantity parse deterministically: "× N" (per event
        / by level), "N min" (per hour), "per guest" (quantity 1 — the
        guest count drives the amount). A service renamed or deactivated
        after the quotation fails loudly at conversion instead of
        silently changing the booking."""
        by_name = {s["name"]: s for s in catalog_repo.list_services(self.db)}
        selections: list[ServiceSelection] = []
        for item in frozen_items:
            if item["kind"] != "service":
                continue
            label = str(item["label"])
            if not label.startswith("Service — "):
                continue
            rest = label[len("Service — "):]
            name, _, detail = rest.partition(" (")
            detail = detail.rstrip(")")
            service_row = by_name.get(name)
            if service_row is None:
                raise ValidationError(
                    f"Service '{name}' from the quotation no longer exists; "
                    "the quotation cannot be converted.")
            if not service_row["is_active"]:
                raise ValidationError(
                    f"Service '{name}' from the quotation is inactive; "
                    "the quotation cannot be converted.")
            quantity, duration_min = 1, None
            match = re.match(r"× (\d+)$", detail)
            if match:
                quantity = int(match.group(1))
            elif detail.endswith(" min"):
                duration_min = int(detail.split(" ")[0])
            elif detail.startswith("level × "):
                quantity = int(detail.split("× ")[1])
            selections.append(ServiceSelection(
                service_id=int(service_row["id"]), quantity=quantity,
                duration_min=duration_min))
        return selections

    # -- reads ----------------------------------------------------------------
    def get_booking(self, employee, booking_id: int) -> Booking | None:
        require_permission(self.db, employee, "booking.view")
        try:
            self.sweep_expired_holds()
        except Exception:  # pragma: no cover - sweep is best-effort on reads
            logger.warning("Hold-expiry sweep failed on booking read.")
        row = repo.get_booking(self.db, booking_id)
        return _row_to_booking(row) if row else None

    def list_bookings(self, employee, *, customer_id: int | None = None,
                      hall_id: int | None = None, status: str | None = None,
                      start_from: datetime | None = None,
                      start_to: datetime | None = None) -> list[Booking]:
        require_permission(self.db, employee, "booking.view")
        try:
            self.sweep_expired_holds()
        except Exception:  # pragma: no cover - sweep is best-effort on reads
            logger.warning("Hold-expiry sweep failed on booking list.")
        rows = repo.list_bookings(
            self.db, customer_id=customer_id, hall_id=hall_id, status=status,
            start_from=_iso(start_from) if start_from else None,
            start_to=_iso(start_to) if start_to else None)
        return [_row_to_booking(r) for r in rows]

    def list_price_items(self, employee, booking_id: int) -> list[dict]:
        require_permission(self.db, employee, "booking.view_price")
        self._get_or_raise(booking_id)
        return repo.list_booking_price_items(self.db, booking_id)

    def list_payments(self, employee, booking_id: int) -> list[Payment]:
        require_permission(self.db, employee, "payment.view")
        self._get_or_raise(booking_id)
        return [_row_to_payment(r) for r in repo.list_payments(self.db, booking_id)]

    def timeline(self, employee, booking_id: int) -> list[dict]:
        """Booking Timeline (SRS §12.2): the booking_history rows for the
        booking, oldest first. Survives cancellation and expiry —
        history is never deleted. The audit log keeps its own copy of
        every event (same transaction), but the timeline shows each
        event ONCE — merging the audit rows in would duplicate every
        entry (``booking.created`` alongside ``created``)."""
        require_permission(self.db, employee, "booking.view")
        self._get_or_raise(booking_id)
        return [{
            "source": "history", "action": r["action"],
            "employee_id": r["employee_id"], "old_value": r["old_value"],
            "new_value": r["new_value"], "reason": r["reason"],
            "created_at": r["created_at"],
        } for r in repo.list_booking_history(self.db, booking_id)]

    # -- payments / confirmation ----------------------------------------------
    def record_payment(self, employee, booking_id: int, amount_piasters: int,
                       method: str, reference: str = "",
                       initiated_at: datetime | None = None,
                       idempotency_key: str | None = None) -> Payment:
        """Record a payment (UC-07, SRS §8.1). Deposit or balance may be
        paid in several transactions with different methods. If the
        payment brings the recorded total to the required deposit AND
        the hold/grace conditions and hall availability pass, the
        booking confirms automatically (SRS §3.4) — no separate
        approval. A payment below the deposit is recorded and the
        booking stays Pending.

        Idempotency (SRS §8.2): passing the same ``idempotency_key``
        twice returns the original payment and creates no duplicate.
        """
        require_permission(self.db, employee, "payment.record")
        if amount_piasters <= 0:
            raise PaymentError("Payment amount must be greater than zero.")
        if method not in ("cash", "bank_transfer", "visa_card", "instapay", "other"):
            raise PaymentError(f"Payment method must be one of "
                               f"('cash', 'bank_transfer', 'visa_card', 'instapay', 'other').")
        key = idempotency_key or secrets.token_hex(16)
        now = datetime.now()
        with self.db.transaction(immediate=True) as conn:
            expire_overdue_holds(self.db, now=now, conn=conn)
            # Idempotency first: a retry returns the original payment
            # even if the booking has since changed state (SRS §8.2).
            existing = repo.get_payment_by_idempotency_key(self.db, key, conn=conn)
            if existing is not None:
                return _row_to_payment(existing)
            row = self._get_or_raise(booking_id, conn=conn)
            booking = _row_to_booking(row)
            if booking.status not in ("pending", "confirmed"):
                raise PaymentError(
                    f"Cannot record a payment for a {booking.status} booking.")
            if initiated_at is not None and booking.status == "pending":
                hold_end = booking.hold_expires_at
                if hold_end is not None and initiated_at > hold_end and now > hold_end:
                    raise PaymentError(
                        "Payment initiation cannot be backdated after the hold expired.")
            number = self._next_payment_number(conn)
            payment_id = repo.insert_payment(
                self.db, number=number, booking_id=booking_id,
                amount=amount_piasters, method=method, reference=reference,
                idempotency_key=key,
                initiated_at=_iso(initiated_at) if initiated_at else _iso(now),
                confirmed_at=_iso(now), recorded_by=employee.id, conn=conn)
            total_paid = repo.sum_recorded_payments(self.db, booking_id, conn=conn)
            deposit_paid = min(total_paid, booking.deposit_required)
            repo.update_booking_deposit_paid(
                self.db, booking_id, deposit_paid, conn=conn)
            repo.insert_booking_history(
                self.db, booking_id=booking_id, employee_id=employee.id,
                action="payment_recorded", old_value="", new_value=str(total_paid),
                reason=f"Payment {number} recorded: {format_money(amount_piasters)} "
                       f"({method}).", conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="payment.recorded",
                entity_type="payment", entity_id=payment_id,
                new_value={"booking_id": booking_id, "amount": amount_piasters,
                           "method": method, "number": number},
                description=f"Payment {number} recorded for booking "
                            f"{booking.number}.", conn=conn)
            notify(self.db, type="payment_received",
                   title=f"Payment received — booking {booking.number}",
                   body=f"Payment {number}: {format_money(amount_piasters)} "
                        f"({method}).",
                   title_key="notif.payment_received.title",
                   body_key="notif.payment_received.body",
                   data={"number": booking.number, "payment": number,
                         "amount": format_money(amount_piasters),
                         "method": method},
                   entity_type="booking", entity_id=booking_id, conn=conn)
            customer_repo.insert_interaction(
                self.db, customer_id=booking.customer_id, employee_id=employee.id,
                type_="payment",
                summary=f"Payment {number} recorded for booking {booking.number}: "
                        f"{format_money(amount_piasters)} ({method}).", conn=conn)
            confirmed = False
            if booking.status == "pending" and total_paid >= booking.deposit_required:
                confirmed = self._try_confirm_locked(
                    conn, employee, booking, total_paid, initiated_at, now)
            if confirmed:
                repo.insert_booking_history(
                    self.db, booking_id=booking_id, employee_id=employee.id,
                    action="confirmed", old_value="pending", new_value="confirmed",
                    reason="Required deposit verified; hold/grace and availability passed.",
                    conn=conn)
                audit_service.record(
                    self.db, employee_id=employee.id, action="booking.confirmed",
                    entity_type="booking", entity_id=booking_id,
                    old_value={"status": "pending"},
                    new_value={"status": "confirmed"},
                    description=f"Booking {booking.number} confirmed (deposit verified).",
                    conn=conn)
        logger.info("Payment %s recorded for booking #%s (confirmed=%s)",
                    number, booking_id, confirmed)
        payment_row = self.db.query_one(
            "SELECT * FROM payments WHERE id = ?", (payment_id,))
        return _row_to_payment(dict(payment_row))

    def _next_payment_number(self, conn) -> str:
        today = datetime.now().strftime("%Y%m%d")
        day_prefix = f"PAY-{today}-"
        seq = repo.count_payments_on_day(self.db, day_prefix, conn=conn) + 1
        return f"{day_prefix}{seq:04d}"

    def _try_confirm_locked(self, conn, employee, booking: Booking,
                            total_paid: int, initiated_at: datetime | None,
                            now: datetime) -> bool:
        """Confirm a Pending booking inside the payment transaction.
        Returns True when confirmed. The deposit is already verified by
        the caller; this checks the remaining conditions (SRS §3.4):
        hold/grace timing and hall still available. Never confirms when
        the hall is no longer valid — the payment stays recorded and the
        booking stays Pending."""
        if booking.hold_expires_at is not None and now <= booking.hold_expires_at:
            within_time = True
        elif (initiated_at is not None and booking.hold_expires_at is not None
              and initiated_at <= booking.hold_expires_at
              and booking.grace_expires_at is not None
              and now <= booking.grace_expires_at):
            within_time = True
        else:
            within_time = False
        if not within_time:
            return False
        result = self.availability.check_availability(
            employee, hall_id=booking.hall_id, start_at=booking.start_at,
            end_at=booking.end_at, exclude_booking_id=booking.id, conn=conn)
        if not result.available:
            logger.warning("Deposit recorded but booking #%s not confirmed: %s",
                           booking.id, result.reason)
            return False
        repo.update_booking_status(self.db, booking.id, "confirmed", conn=conn)
        self.pricing.create_snapshots(employee, booking.id, conn=conn)
        return True

    # -- notes / cancel ---------------------------------------------------------
    def update_notes(self, employee, booking_id: int, notes: str) -> Booking:
        """Edit the single internal notes field (SRS §5.4). Every edit
        records old/new values in the audit log (AR-09)."""
        require_permission(self.db, employee, "booking.update")
        with self.db.transaction() as conn:
            row = self._get_or_raise(booking_id, conn=conn)
            old_notes = row["notes"]
            repo.update_booking_notes(self.db, booking_id, notes or "", conn=conn)
            repo.insert_booking_history(
                self.db, booking_id=booking_id, employee_id=employee.id,
                action="notes_updated", old_value=old_notes, new_value=notes or "",
                reason="Internal notes updated.", conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="booking.notes_updated",
                entity_type="booking", entity_id=booking_id,
                old_value={"notes": old_notes}, new_value={"notes": notes or ""},
                description="Booking notes updated.", conn=conn)
        updated = self.get_booking(employee, booking_id)
        assert updated is not None
        return updated

    def cancel_booking(self, employee, booking_id: int, reason: str = "") -> Booking:
        """Cancel a Pending/Confirmed booking (SRS §8.8, BR-16).

        The hall time is released immediately (status -> Cancelled) —
        never after the refund is paid. The fee and refundable amount
        come from the policy snapshot frozen at confirmation
        (decision A-43): a tiered % of the total by days before the
        event, capped at the amount paid. A Pending booking has no
        snapshot and no fee — refundable is the deposit paid so far
        (Phase 3 behaviour, decision A-31, preserved). When money is
        due, a refund request is created automatically (pending
        approval, linked to the latest recorded payment); refund
        approval stays separate from cancellation approval. Contracts,
        payments and history are preserved.
        """
        require_permission(self.db, employee, "booking.cancel")
        if not str(reason or "").strip():
            raise ValidationError("A cancellation reason is required.")
        now = datetime.now()
        with self.db.transaction(immediate=True) as conn:
            expire_overdue_holds(self.db, conn=conn)
            row = self._get_or_raise(booking_id, conn=conn)
            booking = _row_to_booking(row)
            if booking.status not in CANCELLABLE_STATUSES:
                raise ValidationError(
                    f"A {booking.status} booking cannot be cancelled.")
            paid = repo.sum_recorded_payments(self.db, booking_id, conn=conn)
            if booking.status == "pending":
                fee, refundable = 0, booking.deposit_paid
            else:
                policy = self.pricing.financial_policy_for_booking(booking_id)
                days_before = (booking.start_at.date() - now.date()).days
                fee, refundable = self.pricing.cancellation_fee_for(
                    policy, total_amount=booking.total_amount,
                    paid_amount=paid, days_before_event=days_before)
            repo.update_booking_status(self.db, booking_id, "cancelled", conn=conn)
            repo.insert_cancellation_record(
                self.db, booking_id=booking_id, fee_amount=fee,
                refundable_amount=refundable, reason=reason,
                requested_by=employee.id, approved_by=employee.id, conn=conn)
            refund_id = None
            if refundable > 0:
                payments = [p for p in repo.list_payments(self.db, booking_id)
                            if p["status"] == "recorded"]
                if payments:
                    latest = payments[-1]
                    refund_id = finance_repo.insert_refund(
                        self.db, payment_id=int(latest["id"]),
                        booking_id=booking_id, amount=refundable,
                        reason=f"Cancellation refund: {reason}",
                        method=latest["method"], created_by=employee.id,
                        conn=conn)
            repo.insert_booking_history(
                self.db, booking_id=booking_id, employee_id=employee.id,
                action="cancelled", old_value=booking.status, new_value="cancelled",
                reason=f"{reason} (fee {format_money(fee)}, refundable "
                       f"{format_money(refundable)}).",
                conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="booking.cancelled",
                entity_type="booking", entity_id=booking_id,
                old_value={"status": booking.status},
                new_value={"status": "cancelled", "fee": fee,
                           "refundable": refundable, "refund_id": refund_id},
                description=f"Booking {booking.number} cancelled: {reason}. "
                            f"Fee {format_money(fee)}, refundable "
                            f"{format_money(refundable)}.",
                conn=conn)
            if refund_id is not None:
                audit_service.record(
                    self.db, employee_id=employee.id, action="refund.requested",
                    entity_type="refund", entity_id=refund_id,
                    new_value={"booking_id": booking_id, "amount": refundable},
                    description="Refund requested automatically by "
                                f"cancellation of booking {booking.number}.",
                    conn=conn)
            notify(self.db, type="booking_cancelled",
                   title=f"Booking cancelled — {booking.number}",
                   body=f"Booking {booking.number} cancelled: {reason}. "
                        f"Fee {format_money(fee)}, refundable "
                        f"{format_money(refundable)}.",
                   title_key="notif.booking_cancelled.title",
                   body_key="notif.booking_cancelled.body",
                   data={"number": booking.number, "reason": reason,
                         "fee": format_money(fee),
                         "refundable": format_money(refundable)},
                   entity_type="booking", entity_id=booking_id, conn=conn)
            customer_repo.insert_interaction(
                self.db, customer_id=booking.customer_id, employee_id=employee.id,
                type_="cancellation",
                summary=f"Booking {booking.number} cancelled: {reason}.", conn=conn)
        logger.info("Booking #%s cancelled: %s (fee=%s, refundable=%s)",
                    booking_id, reason, fee, refundable)
        cancelled = self.get_booking(employee, booking_id)
        assert cancelled is not None
        return cancelled

    # -- rescheduling (SRS §8.9) ----------------------------------------------
    def reschedule_booking(self, employee, booking_id: int, *,
                           new_start_at: datetime, new_end_at: datetime,
                           new_hall_id: int | None = None,
                           reason: str = "") -> Booking:
        """Reschedule a Pending/Confirmed booking (SRS §8.9).

        One BEGIN IMMEDIATE transaction, never leaving old and new
        reservations both active: the new window is validated (hours,
        buffer, maintenance, closures, hall status, all active
        bookings) and re-checked on the same connection, the price is
        recalculated with the CURRENT rules, and the old/new amounts
        are preserved in reschedule_history. A price increase needs
        approval — the reschedule itself, done by a
        ``booking.reschedule`` holder (Manager/Owner), IS that
        approval; the difference becomes outstanding with a due date
        (the installment final deadline) and no payment is collected
        here. A price decrease reduces the contract value and the
        excess paid becomes refund-eligible. Non-completed task due
        dates shift by the same delta as the event. The contract is
        not regenerated automatically (decision A-48): the Manager
        creates a new contract version when required.
        """
        require_permission(self.db, employee, "booking.reschedule")
        if new_end_at <= new_start_at:
            raise ValidationError("End time must be after start time.")
        if not str(reason or "").strip():
            raise ValidationError("A reschedule needs a reason.")
        now = datetime.now()
        with self.db.transaction(immediate=True) as conn:
            expire_overdue_holds(self.db, now=now, conn=conn)
            row = self._get_or_raise(booking_id, conn=conn)
            booking = _row_to_booking(row)
            if booking.status not in ("pending", "confirmed"):
                raise ValidationError(
                    f"A {booking.status} booking cannot be rescheduled.")
            hall_id = new_hall_id if new_hall_id is not None else booking.hall_id
            hall_row = catalog_repo.get_hall(self.db, hall_id)
            if hall_row is None:
                raise NotFoundError(f"Hall #{hall_id} does not exist.")
            hall = Hall(**{k: v for k, v in hall_row.items()
                           if k in Hall.__dataclass_fields__})
            duration_min = int((new_end_at - new_start_at).total_seconds() // 60)
            package_min = None
            if booking.package_id is not None:
                package_row = catalog_repo.get_package(self.db, booking.package_id)
                if package_row is None:
                    raise NotFoundError(
                        f"Package #{booking.package_id} does not exist.")
                package_min = int(package_row["min_duration_min"])
            self.availability.validate_event_window(
                employee, hall=hall, start_at=new_start_at, end_at=new_end_at,
                guests=booking.guest_count,
                package_min_duration_min=package_min, duration_min=duration_min)
            recheck = self.availability.check_availability(
                employee, hall_id=hall_id, start_at=new_start_at,
                end_at=new_end_at, exclude_booking_id=booking_id, conn=conn)
            if not recheck.available:
                raise BookingConflictError(
                    f"Hall is not available for the requested time: {recheck.reason}")
            # Re-price with the current rules, carrying the original
            # manual discount (if any) forward.
            selections = [
                ServiceSelection(service_id=int(s["service_id"]),
                                 quantity=int(s["quantity"]))
                for s in repo.list_booking_services(self.db, booking_id)]
            discount = self._discount_for_reprice(conn, booking_id)
            breakdown = self.pricing.calculate(
                employee, hall_id=hall_id, package_id=booking.package_id,
                event_type_id=booking.event_type_id, start_at=new_start_at,
                duration_min=duration_min, guests=booking.guest_count,
                service_selections=selections, discount=discount)
            new_total = breakdown.total
            policy = self.pricing.financial_policy_for_booking(booking_id)
            deposit_percent = int(policy.get("deposit_percent", 30))
            new_deposit_required = round(new_total * deposit_percent / 100)
            due_at = None
            if new_total > booking.total_amount:
                deadline_days = int(
                    policy.get("final_deadline_days_before_event", 7))
                due = new_start_at - timedelta(days=deadline_days)
                due_at = _iso(due if due > now else now)
            repo.update_booking_schedule(
                self.db, booking_id, hall_id=hall_id,
                start_at=_iso(new_start_at), end_at=_iso(new_end_at),
                total_amount=new_total, deposit_required=new_deposit_required,
                conn=conn)
            repo.delete_booking_price_items(self.db, booking_id, conn=conn)
            for item in breakdown.items:
                repo.insert_booking_price_item(
                    self.db, booking_id=booking_id, kind=item.kind,
                    label=item.label, amount=item.amount, conn=conn)
            repo.delete_booking_services(self.db, booking_id, conn=conn)
            self._insert_booking_services(
                conn, booking_id, selections,
                [(i.kind, i.label, i.amount) for i in breakdown.items])
            finance_repo.insert_reschedule_history(
                self.db, booking_id=booking_id, old_hall_id=booking.hall_id,
                new_hall_id=hall_id, old_start_at=_iso(booking.start_at),
                new_start_at=_iso(new_start_at), old_end_at=_iso(booking.end_at),
                new_end_at=_iso(new_end_at), old_amount=booking.total_amount,
                new_amount=new_total, approved_by=employee.id, due_at=due_at,
                conn=conn)
            # Shift non-completed task due dates by the event delta.
            delta = new_start_at - booking.start_at
            for task in ops_repo.list_tasks(self.db, booking_id=booking_id):
                if task["status"] == "completed" or not task["due_at"]:
                    continue
                old_due = _parse(task["due_at"])
                new_due = old_due + delta if old_due else None
                ops_repo.update_task(
                    self.db, int(task["id"]), due_at=_iso(new_due), conn=conn)
                ops_repo.insert_task_history(
                    self.db, task_id=int(task["id"]), employee_id=employee.id,
                    action="rescheduled", old_value=task["due_at"],
                    new_value=_iso(new_due), conn=conn)
            repo.insert_booking_history(
                self.db, booking_id=booking_id, employee_id=employee.id,
                action="rescheduled", old_value=_iso(booking.start_at),
                new_value=_iso(new_start_at),
                reason=f"{reason} Price {format_money(booking.total_amount)} "
                       f"-> {format_money(new_total)}"
                       + (f"; increase due {due_at[:10]}." if due_at else "."),
                conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="booking.rescheduled",
                entity_type="booking", entity_id=booking_id,
                old_value={"start_at": _iso(booking.start_at),
                           "end_at": _iso(booking.end_at),
                           "total_amount": booking.total_amount,
                           "hall_id": booking.hall_id},
                new_value={"start_at": _iso(new_start_at),
                           "end_at": _iso(new_end_at),
                           "total_amount": new_total, "hall_id": hall_id,
                           "due_at": due_at},
                description=f"Booking {booking.number} rescheduled: {reason}.",
                conn=conn)
            notify(self.db, type="booking_rescheduled",
                   title=f"Booking rescheduled — {booking.number}",
                   body=f"Booking {booking.number} moved to "
                        f"{new_start_at.strftime('%Y-%m-%d %H:%M')}; price "
                        f"{format_money(booking.total_amount)} -> "
                        f"{format_money(new_total)}.",
                   title_key="notif.booking_rescheduled.title",
                   body_key="notif.booking_rescheduled.body",
                   data={"number": booking.number,
                         "when": new_start_at.strftime("%Y-%m-%d %H:%M"),
                         "old": format_money(booking.total_amount),
                         "new": format_money(new_total)},
                   entity_type="booking", entity_id=booking_id, conn=conn)
        logger.info("Booking #%s rescheduled to %s (total %s -> %s)",
                    booking_id, new_start_at, booking.total_amount, new_total)
        updated = self.get_booking(employee, booking_id)
        assert updated is not None
        return updated

    @staticmethod
    def _discount_for_reprice(conn, booking_id: int) -> dict | None:
        """Recover the booking's manual discount (if any) so a
        reschedule re-prices WITH it instead of silently dropping it
        (decision A-48). ``discount_records.amount`` is the positive
        discount amount; ``percent`` holds the % when it was a
        percent discount."""
        row = conn.execute(
            """SELECT amount, percent, reason FROM discount_records
               WHERE booking_id = ? ORDER BY id DESC LIMIT 1""",
            (booking_id,)).fetchone()
        if row is None:
            return None
        if row["percent"] is not None:
            return {"type": "percent", "value": int(row["percent"]),
                    "reason": row["reason"] or "manual discount"}
        return {"type": "fixed", "value": int(row["amount"]),
                "reason": row["reason"] or "manual discount"}

    # -- post-confirmation service changes (SRS §3.6, FR-026, BR-13) ---------
    def change_services(self, employee, booking_id: int, *,
                        service_selections: list[ServiceSelection],
                        reason: str = "") -> dict:
        """Change the services of a Confirmed booking (SRS §3.6, FR-026).

        Re-prices with the CURRENT rules (carrying the original manual
        discount forward, like a reschedule), records the delta, and:

        - increase: the difference becomes outstanding with a due date
          (the installment final deadline); no payment is collected here.
        - decrease: the excess paid becomes refund-eligible — a refund
          request is created in ``pending_approval`` (never auto-paid)
          for the refundable part; approval/payment stay separate.
        - contract: a new contract version is required when the total
          changes (decision A-70); a zero-delta swap is a note only.
          The version is generated AFTER the change commits (same
          pattern as ownership transfer) and the change record is
          then marked ``new_version``.

        Only Confirmed bookings (decision A-69: an event already in
        progress is being delivered, its services are not re-priced).
        Returns the change record: booking, old/new totals, delta,
        due_at, refund_id, contract_action, change_id.
        """
        require_permission(self.db, employee, "booking.change_services")
        now = datetime.now()
        with self.db.transaction(immediate=True) as conn:
            expire_overdue_holds(self.db, now=now, conn=conn)
            row = self._get_or_raise(booking_id, conn=conn)
            booking = _row_to_booking(row)
            if booking.status != "confirmed":
                raise ValidationError(
                    "Services can only be changed on a Confirmed booking "
                    f"(current status: {booking.status}).")
            old_rows = repo.list_booking_services(self.db, booking_id)
            old_services = []
            for s in old_rows:
                service_row = catalog_repo.get_service(self.db, int(s["service_id"]))
                old_services.append({
                    "service_id": int(s["service_id"]),
                    "name": service_row["name"] if service_row is not None else "",
                    "quantity": int(s["quantity"]), "amount": int(s["amount"])})
            duration_min = int((booking.end_at - booking.start_at).total_seconds() // 60)
            discount = self._discount_for_reprice(conn, booking_id)
            breakdown = self.pricing.calculate(
                employee, hall_id=booking.hall_id, package_id=booking.package_id,
                event_type_id=booking.event_type_id, start_at=booking.start_at,
                duration_min=duration_min, guests=booking.guest_count,
                service_selections=service_selections, discount=discount)
            new_total = breakdown.total
            delta = new_total - booking.total_amount
            policy = self.pricing.financial_policy_for_booking(booking_id)
            deposit_percent = int(policy.get("deposit_percent", 30))
            new_deposit_required = round(new_total * deposit_percent / 100)
            due_at = None
            if delta > 0:
                deadline_days = int(policy.get("final_deadline_days_before_event", 7))
                due = booking.start_at - timedelta(days=deadline_days)
                due_at = _iso(due if due > now else now)
            repo.update_booking_amounts(
                self.db, booking_id, total_amount=new_total,
                deposit_required=new_deposit_required, conn=conn)
            repo.delete_booking_price_items(self.db, booking_id, conn=conn)
            for item in breakdown.items:
                repo.insert_booking_price_item(
                    self.db, booking_id=booking_id, kind=item.kind,
                    label=item.label, amount=item.amount, conn=conn)
            repo.delete_booking_services(self.db, booking_id, conn=conn)
            self._insert_booking_services(
                conn, booking_id, service_selections,
                [(i.kind, i.label, i.amount) for i in breakdown.items])
            new_services = []
            for sel, item in zip(
                    service_selections,
                    [i for i in breakdown.items if i.kind == "service"]):
                service_row = catalog_repo.get_service(self.db, int(sel.service_id))
                new_services.append({
                    "service_id": int(sel.service_id),
                    "name": service_row["name"] if service_row is not None else "",
                    "quantity": int(sel.quantity),
                    "amount": int(item.amount)})
            refund_id = None
            if delta < 0:
                from .refund_service import RefundService

                refundable = RefundService(
                    self.db, self.settings).refundable_amount_for_booking(
                        booking_id, conn=conn)
                eligible = int(refundable["refundable"])
                if eligible > 0:
                    payments = [p for p in repo.list_payments(self.db, booking_id)
                                if p["status"] == "recorded"]
                    if payments:
                        largest = max(payments, key=lambda p: int(p["amount"]))
                        refund_id = finance_repo.insert_refund(
                            self.db, payment_id=int(largest["id"]),
                            booking_id=booking_id, amount=eligible,
                            reason="Service change reduced the contract value "
                                   f"({format_money(booking.total_amount)} -> "
                                   f"{format_money(new_total)}).",
                            method="cash", created_by=employee.id, conn=conn)
            change_id = repo.insert_booking_service_change(
                self.db, booking_id=booking_id, employee_id=employee.id,
                old_services_json=json.dumps(old_services, ensure_ascii=False),
                new_services_json=json.dumps(new_services, ensure_ascii=False),
                old_total=booking.total_amount, new_total=new_total,
                delta=delta, due_at=due_at, refund_id=refund_id,
                contract_action="note", reason=str(reason or ""), conn=conn)
            repo.insert_booking_history(
                self.db, booking_id=booking_id, employee_id=employee.id,
                action="services_changed",
                old_value=json.dumps(old_services, ensure_ascii=False),
                new_value=json.dumps(new_services, ensure_ascii=False),
                reason=f"{reason} Price {format_money(booking.total_amount)} "
                       f"-> {format_money(new_total)}"
                       + (f"; increase due {due_at[:10]}." if due_at else ".")
                       + (f" Refund #{refund_id} pending approval."
                          if refund_id else ""),
                conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="booking.services_changed",
                entity_type="booking", entity_id=booking_id,
                old_value={"services": old_services,
                           "total_amount": booking.total_amount},
                new_value={"services": new_services, "total_amount": new_total,
                           "delta": delta, "due_at": due_at,
                           "refund_id": refund_id},
                description=f"Booking {booking.number} services changed: {reason}.",
                conn=conn)
            notify(self.db, type="booking_services_changed",
                   title=f"Services changed — {booking.number}",
                   body=f"Booking {booking.number} services changed; price "
                        f"{format_money(booking.total_amount)} -> "
                        f"{format_money(new_total)}.",
                   title_key="notif.booking_services_changed.title",
                   body_key="notif.booking_services_changed.body",
                   data={"number": booking.number,
                         "old": format_money(booking.total_amount),
                         "new": format_money(new_total)},
                   entity_type="booking", entity_id=booking_id, conn=conn)
        contract_action = "note"
        if delta != 0:
            contract = contract_repo.get_contract_by_booking(self.db, booking_id)
            if contract is not None:
                try:
                    from .contract_service import ContractService

                    ContractService(self.db, self.settings).create_new_version(
                        employee, int(contract["id"]))
                    contract_action = "new_version"
                    self.db.execute(
                        "UPDATE booking_service_changes SET contract_action = ? WHERE id = ?",
                        (contract_action, change_id))
                except Exception:  # pragma: no cover - version failure path
                    logger.warning(
                        "Service change #%s committed but contract version "
                        "creation failed; a new version is still required.",
                        change_id)
                    repo.insert_booking_history(
                        self.db, booking_id=booking_id, employee_id=employee.id,
                        action="services_changed",
                        old_value="", new_value="",
                        reason="Contract version creation failed — a new "
                               "contract version is still required.")
        logger.info("Booking #%s services changed (total %s -> %s)",
                    booking_id, booking.total_amount, new_total)
        updated = self.get_booking(employee, booking_id)
        assert updated is not None
        return {
            "booking": updated, "old_total": booking.total_amount,
            "new_total": new_total, "delta": delta, "due_at": due_at,
            "refund_id": refund_id, "contract_action": contract_action,
            "change_id": change_id,
        }

    def list_service_changes(self, employee, booking_id: int) -> list[dict]:
        """The recorded service-change history of one booking (FR-026)."""
        require_permission(self.db, employee, "booking.view")
        self._get_or_raise(booking_id)
        return repo.list_booking_service_changes(self.db, booking_id)

    def list_booking_services(self, employee, booking_id: int) -> list[dict]:
        """Current service selections of one booking (FR-026 UI helper)."""
        require_permission(self.db, employee, "booking.view")
        self._get_or_raise(booking_id)
        return repo.list_booking_services(self.db, booking_id)

    # -- ownership transfer (SRS §8.12) ----------------------------------------
    def transfer_booking(self, employee, booking_id: int, *,
                         new_customer_id: int, reason: str = "") -> Booking:
        """Transfer a Pending/Confirmed booking to another customer
        (SRS §8.12). Administrative approval is the transfer action
        itself (``booking.transfer`` holders: Manager/Owner). Old/new
        owner, reason, request/approval employee and timestamp are
        preserved in booking_transfers; payments stay attached to the
        booking and payer identity is never rewritten; the new customer
        benefits from the amounts already paid. When the booking has a
        contract, a new contract version is generated afterwards with
        the new owner's data (the old versions are preserved)."""
        require_permission(self.db, employee, "booking.transfer")
        if not str(reason or "").strip():
            raise ValidationError("A transfer needs a reason.")
        with self.db.transaction(immediate=True) as conn:
            expire_overdue_holds(self.db, conn=conn)
            row = self._get_or_raise(booking_id, conn=conn)
            booking = _row_to_booking(row)
            if booking.status not in ("pending", "confirmed"):
                raise ValidationError(
                    f"A {booking.status} booking cannot be transferred.")
            if new_customer_id == booking.customer_id:
                raise ValidationError(
                    "The booking already belongs to that customer.")
            if customer_repo.get_customer(self.db, new_customer_id) is None:
                raise NotFoundError(f"Customer #{new_customer_id} does not exist.")
            finance_repo.insert_booking_transfer(
                self.db, booking_id=booking_id,
                old_customer_id=booking.customer_id,
                new_customer_id=new_customer_id, reason=reason,
                requested_by=employee.id, approved_by=employee.id, conn=conn)
            repo.update_booking_customer(
                self.db, booking_id, new_customer_id, conn=conn)
            repo.insert_booking_history(
                self.db, booking_id=booking_id, employee_id=employee.id,
                action="ownership_transferred",
                old_value=str(booking.customer_id),
                new_value=str(new_customer_id),
                reason=reason, conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id,
                action="booking.ownership_transferred",
                entity_type="booking", entity_id=booking_id,
                old_value={"customer_id": booking.customer_id},
                new_value={"customer_id": new_customer_id},
                description=f"Booking {booking.number} transferred to "
                            f"customer #{new_customer_id}: {reason}.",
                conn=conn)
            customer_repo.insert_interaction(
                self.db, customer_id=new_customer_id, employee_id=employee.id,
                type_="transfer",
                summary=f"Booking {booking.number} transferred to this "
                        f"customer: {reason}.", conn=conn)
        # New contract version with the new owner's data, when a
        # contract exists (SRS §8.12 "when required"). This runs after
        # the transfer commits: it is a separate, best-effort step —
        # a contract failure must not roll back the transfer itself.
        contract = contract_repo.get_contract_by_booking(self.db, booking_id)
        if contract is not None:
            try:
                from .contract_service import ContractService

                ContractService(self.db, self.settings).create_new_version(
                    employee, int(contract["id"]))
            except Exception:  # pragma: no cover - best-effort regeneration
                logger.warning("Contract re-version after transfer of "
                               "booking #%s failed.", booking_id)
        logger.info("Booking #%s transferred to customer #%s",
                    booking_id, new_customer_id)
        updated = self.get_booking(employee, booking_id)
        assert updated is not None
        return updated

    # -- lifecycle transitions (SRS §3.3) --------------------------------------
    def _transition(self, employee, booking_id: int, from_statuses: tuple[str, ...],
                    to_status: str, action: str, reason: str = "") -> Booking:
        """One status step, with history + audit in the same transaction.
        Invalid steps are rejected — the lifecycle is a strict state
        machine, never a free-form status edit."""
        require_permission(self.db, employee, "booking.update")
        with self.db.transaction(immediate=True) as conn:
            expire_overdue_holds(self.db, conn=conn)
            row = self._get_or_raise(booking_id, conn=conn)
            booking = _row_to_booking(row)
            if booking.status not in from_statuses:
                raise ValidationError(
                    f"A {booking.status} booking cannot move to {to_status}.")
            repo.update_booking_status(self.db, booking_id, to_status, conn=conn)
            repo.insert_booking_history(
                self.db, booking_id=booking_id, employee_id=employee.id,
                action=action, old_value=booking.status, new_value=to_status,
                reason=reason, conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action=f"booking.{action}",
                entity_type="booking", entity_id=booking_id,
                old_value={"status": booking.status},
                new_value={"status": to_status},
                description=f"Booking {booking.number}: {booking.status} -> {to_status}.",
                conn=conn)
        updated = self.get_booking(employee, booking_id)
        assert updated is not None
        return updated

    def mark_in_progress(self, employee, booking_id: int) -> Booking:
        """Confirmed → In progress (the event has started)."""
        return self._transition(employee, booking_id, ("confirmed",),
                                "in_progress", "started")

    def mark_completed(self, employee, booking_id: int) -> Booking:
        """In progress → Completed (the event has finished)."""
        return self._transition(employee, booking_id, ("in_progress",),
                                "completed", "completed")

    def mark_no_show(self, employee, booking_id: int, reason: str = "") -> Booking:
        """Confirmed → No-show (SRS §8.10). Records the no-show and
        frees the hall time. The financial outcome follows the
        no-show policy frozen at confirmation (decision A-44): the
        deposit is forfeited, nothing is refundable, and the unpaid
        balance stays outstanding. Contract and payments are
        preserved."""
        require_permission(self.db, employee, "booking.update")
        with self.db.transaction(immediate=True) as conn:
            expire_overdue_holds(self.db, conn=conn)
            row = self._get_or_raise(booking_id, conn=conn)
            booking = _row_to_booking(row)
            if booking.status != "confirmed":
                raise ValidationError(
                    f"Only a Confirmed booking can be marked no-show "
                    f"(current status: {booking.status}).")
            paid = repo.sum_recorded_payments(self.db, booking_id, conn=conn)
            policy = self.pricing.financial_policy_for_booking(booking_id)
            fee, refundable = self.pricing.no_show_fee_for(
                policy, total_amount=booking.total_amount,
                paid_amount=paid, deposit_paid=booking.deposit_paid)
            repo.update_booking_status(self.db, booking_id, "no_show", conn=conn)
            repo.insert_no_show_record(
                self.db, booking_id=booking_id, reason=reason,
                recorded_by=employee.id, fee_amount=fee,
                refundable_amount=refundable, conn=conn)
            repo.insert_booking_history(
                self.db, booking_id=booking_id, employee_id=employee.id,
                action="no_show", old_value="confirmed", new_value="no_show",
                reason=f"{reason} (fee {format_money(fee)}, refundable "
                       f"{format_money(refundable)}).",
                conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="booking.no_show",
                entity_type="booking", entity_id=booking_id,
                old_value={"status": "confirmed"},
                new_value={"status": "no_show", "fee": fee,
                           "refundable": refundable},
                description=f"Booking {booking.number} marked no-show. "
                            f"Fee {format_money(fee)}, refundable "
                            f"{format_money(refundable)}.",
                conn=conn)
        updated = self.get_booking(employee, booking_id)
        assert updated is not None
        return updated

    def profit(self, employee, booking_id: int) -> dict:
        """Financial summary of one booking (SRS §8.6). Requires
        ``booking.view_profit`` — receptionists must not see it.
        Collected is gross recorded payments; refunds paid are
        reported separately and netted for outstanding (decision
        A-45). Outstanding is 0 for cancelled/expired/no-show
        bookings — their money is governed by the fee records."""
        require_permission(self.db, employee, "booking.view_profit")
        booking = self._get_or_raise(booking_id)
        payments = repo.list_payments(self.db, booking_id)
        collected = sum(p["amount"] for p in payments if p["status"] == "recorded")
        refunds_paid = finance_repo.sum_refunds_for_booking(
            self.db, booking_id, statuses=("paid",))
        net = collected - refunds_paid
        total = int(booking["total_amount"])
        active = booking["status"] in ("pending", "confirmed", "in_progress",
                                       "completed")
        return {
            "booking_id": booking_id,
            "number": booking["number"],
            "status": booking["status"],
            "total_amount": total,
            "deposit_required": int(booking["deposit_required"]),
            "deposit_paid": int(booking["deposit_paid"]),
            "collected": collected,
            "refunds_paid": refunds_paid,
            "net_collected": net,
            "outstanding": max(0, total - net) if active else 0,
            "overpayment": max(0, net - total) if active else 0,
        }
