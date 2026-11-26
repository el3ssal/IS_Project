"""Refunds (Phase 5, business layer — SRS §8.7, BR-15).

A refund is a separate transaction linked to the original payment and
booking; the original payment is never edited. Workflow:
Pending Approval -> Approved -> Paid (or Rejected); cancellable before
Paid only. Multiple refunds against one payment are allowed, but the
total of approved + paid refunds must never exceed the refundable
amount — enforced here, per payment AND per booking.
"""
from __future__ import annotations

from datetime import datetime

from ..config.settings import AppSettings
from ..data.database import DatabaseManager
from ..data.repositories import booking_repository as booking_repo
from ..data.repositories import finance_repository as repo
from ..utils.exceptions import (
    NotFoundError,
    PaymentError,
    RefundLimitExceededError,
    ValidationError,
)
from ..utils.logging_config import get_logger
from . import audit_service
from .models import PAYMENT_METHODS, Refund
from .permissions import require_permission

logger = get_logger(__name__)

# Refund amounts in these statuses consume the refundable pool.
_ACTIVE_REFUND_STATUSES = ("approved", "paid")


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _row_to_refund(row: dict) -> Refund:
    return Refund(
        id=row["id"], payment_id=row["payment_id"], booking_id=row["booking_id"],
        amount=row["amount"], reason=row["reason"], method=row["method"],
        status=row["status"], created_by=row["created_by"],
        approved_by=row["approved_by"],
        approved_at=(datetime.fromisoformat(row["approved_at"])
                     if row["approved_at"] else None),
        paid_at=(datetime.fromisoformat(row["paid_at"])
                 if row["paid_at"] else None),
    )


class RefundService:
    def __init__(self, db: DatabaseManager, settings: AppSettings | None = None) -> None:
        self.db = db
        self.settings = settings or AppSettings.from_env()

    # -- refundable amount ------------------------------------------------
    def refundable_amount_for_booking(self, booking_id: int,
                                      conn=None) -> dict:
        """How much of a booking's money is still refundable, and why.

        Cancelled/no-show bookings use the fee record frozen at
        cancellation/no-show time (SRS §8.8/§8.10); active bookings can
        only refund an overpayment (collected above the contract value,
        e.g. after a price-decreasing reschedule, SRS §8.3/§8.9); an
        expired or pending booking refunds what was paid. Approved +
        paid refunds already consume the pool.
        """
        row = booking_repo.get_booking(self.db, booking_id, conn=conn)
        if row is None:
            raise NotFoundError(f"Booking #{booking_id} does not exist.")
        collected = booking_repo.sum_recorded_payments(self.db, booking_id, conn=conn)
        already = repo.sum_refunds_for_booking(
            self.db, booking_id, statuses=_ACTIVE_REFUND_STATUSES, conn=conn)
        total = int(row["total_amount"])
        status = row["status"]
        if status == "cancelled":
            record = repo.get_cancellation_record(self.db, booking_id, conn=conn)
            pool = int(record["refundable_amount"]) if record else 0
            basis = "cancellation refundable amount"
        elif status == "no_show":
            record = repo.get_no_show_record(self.db, booking_id, conn=conn)
            pool = int(record["refundable_amount"]) if record else 0
            basis = "no-show refundable amount"
        elif status in ("pending", "expired"):
            pool = collected
            basis = "amount paid (booking never confirmed)"
        else:  # confirmed / in_progress / completed
            pool = max(0, collected - total)
            basis = "overpayment above the contract value"
        return {
            "booking_id": booking_id, "status": status,
            "collected": collected, "total_amount": total,
            "already_refunded": already, "basis": basis,
            "refundable": max(0, pool - already),
        }

    # -- workflow -----------------------------------------------------------
    def request_refund(self, employee, payment_id: int, amount_piasters: int,
                       reason: str, method: str = "cash") -> Refund:
        """Request a refund against one recorded payment (SRS §8.7).
        Both caps are enforced: the payment's own cap and the booking's
        refundable pool (BR-15)."""
        require_permission(self.db, employee, "refund.request")
        if amount_piasters <= 0:
            raise PaymentError("Refund amount must be greater than zero.")
        if method not in PAYMENT_METHODS:
            raise PaymentError(f"Refund method must be one of {PAYMENT_METHODS}.")
        if not str(reason or "").strip():
            raise ValidationError("A refund needs a reason.")
        payment = repo.get_payment(self.db, payment_id)
        if payment is None:
            raise NotFoundError(f"Payment #{payment_id} does not exist.")
        if payment["status"] != "recorded":
            raise ValidationError(
                f"Only a recorded payment can be refunded "
                f"(payment is {payment['status']}).")
        booking_id = int(payment["booking_id"])
        with self.db.transaction() as conn:
            refundable = self.refundable_amount_for_booking(booking_id, conn=conn)
            if amount_piasters > refundable["refundable"]:
                raise RefundLimitExceededError(
                    f"Refund of {amount_piasters} exceeds the refundable "
                    f"amount of {refundable['refundable']} "
                    f"({refundable['basis']}).")
            payment_refunded = repo.sum_refunds_for_payment(
                self.db, payment_id, statuses=_ACTIVE_REFUND_STATUSES, conn=conn)
            if payment_refunded + amount_piasters > int(payment["amount"]):
                raise RefundLimitExceededError(
                    f"Refund of {amount_piasters} exceeds what is left of "
                    f"payment {payment['number']} "
                    f"({int(payment['amount']) - payment_refunded} remaining).")
            refund_id = repo.insert_refund(
                self.db, payment_id=payment_id, booking_id=booking_id,
                amount=amount_piasters, reason=reason, method=method,
                created_by=employee.id, conn=conn)
            booking_repo.insert_booking_history(
                self.db, booking_id=booking_id, employee_id=employee.id,
                action="refund_requested", old_value="", new_value=str(amount_piasters),
                reason=f"Refund requested: {reason}.", conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="refund.requested",
                entity_type="refund", entity_id=refund_id,
                new_value={"booking_id": booking_id, "payment_id": payment_id,
                           "amount": amount_piasters, "method": method},
                description=f"Refund of {amount_piasters} requested for "
                            f"booking #{booking_id} ({reason}).",
                conn=conn)
        logger.info("Refund #%s requested for payment #%s", refund_id, payment_id)
        row = repo.get_refund(self.db, refund_id)
        assert row is not None
        return _row_to_refund(row)

    def _get_or_raise(self, refund_id: int, conn=None) -> dict:
        row = repo.get_refund(self.db, refund_id, conn=conn)
        if row is None:
            raise NotFoundError(f"Refund #{refund_id} does not exist.")
        return row

    def approve_refund(self, employee, refund_id: int) -> Refund:
        """Pending Approval -> Approved (SRS §8.7). Approval is separate
        from cancellation approval and from the eventual payment."""
        require_permission(self.db, employee, "refund.approve")
        with self.db.transaction() as conn:
            row = self._get_or_raise(refund_id, conn=conn)
            if row["status"] != "pending_approval":
                raise ValidationError(
                    f"Only a pending refund can be approved "
                    f"(current status: {row['status']}).")
            # Re-check the cap at approval time: another approved refund
            # may have consumed the pool since the request.
            refundable = self.refundable_amount_for_booking(
                int(row["booking_id"]), conn=conn)
            if int(row["amount"]) > refundable["refundable"]:
                raise RefundLimitExceededError(
                    f"Refund of {row['amount']} no longer fits the refundable "
                    f"amount of {refundable['refundable']} "
                    f"({refundable['basis']}).")
            repo.update_refund(
                self.db, refund_id, status="approved",
                approved_by=employee.id, approved_at=_iso(datetime.now()),
                conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="refund.approved",
                entity_type="refund", entity_id=refund_id,
                old_value={"status": "pending_approval"},
                new_value={"status": "approved"},
                description=f"Refund #{refund_id} approved.",
                conn=conn)
        row = repo.get_refund(self.db, refund_id)
        assert row is not None
        return _row_to_refund(row)

    def reject_refund(self, employee, refund_id: int, reason: str = "") -> Refund:
        require_permission(self.db, employee, "refund.approve")
        with self.db.transaction() as conn:
            row = self._get_or_raise(refund_id, conn=conn)
            if row["status"] != "pending_approval":
                raise ValidationError(
                    f"Only a pending refund can be rejected "
                    f"(current status: {row['status']}).")
            repo.update_refund(self.db, refund_id, status="rejected", conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="refund.rejected",
                entity_type="refund", entity_id=refund_id,
                old_value={"status": "pending_approval"},
                new_value={"status": "rejected"},
                description=f"Refund #{refund_id} rejected: {reason or '—'}.",
                conn=conn)
        row = repo.get_refund(self.db, refund_id)
        assert row is not None
        return _row_to_refund(row)

    def mark_refund_paid(self, employee, refund_id: int) -> Refund:
        """Approved -> Paid. Only actually paid refunds count as cash
        outflow (SRS §8.6); a Paid refund can never be cancelled —
        correcting it needs a new transaction."""
        require_permission(self.db, employee, "refund.approve")
        with self.db.transaction() as conn:
            row = self._get_or_raise(refund_id, conn=conn)
            if row["status"] != "approved":
                raise ValidationError(
                    f"Only an approved refund can be marked paid "
                    f"(current status: {row['status']}).")
            repo.update_refund(
                self.db, refund_id, status="paid",
                paid_at=_iso(datetime.now()), conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="refund.paid",
                entity_type="refund", entity_id=refund_id,
                old_value={"status": "approved"},
                new_value={"status": "paid"},
                description=f"Refund #{refund_id} paid "
                            f"({row['amount']} via {row['method']}).",
                conn=conn)
        row = repo.get_refund(self.db, refund_id)
        assert row is not None
        return _row_to_refund(row)

    def cancel_refund(self, employee, refund_id: int, reason: str = "") -> Refund:
        """Cancel a refund before it is Paid (SRS §8.7). A Paid refund
        cannot be cancelled."""
        require_permission(self.db, employee, "refund.request")
        if not str(reason or "").strip():
            raise ValidationError("Cancelling a refund needs a reason.")
        with self.db.transaction() as conn:
            row = self._get_or_raise(refund_id, conn=conn)
            if row["status"] not in ("pending_approval", "approved"):
                raise ValidationError(
                    f"A {row['status']} refund cannot be cancelled.")
            repo.update_refund(self.db, refund_id, status="cancelled", conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="refund.cancelled",
                entity_type="refund", entity_id=refund_id,
                old_value={"status": row["status"]},
                new_value={"status": "cancelled"},
                description=f"Refund #{refund_id} cancelled: {reason}.",
                conn=conn)
        row = repo.get_refund(self.db, refund_id)
        assert row is not None
        return _row_to_refund(row)

    # -- reads ---------------------------------------------------------------
    def list_refunds(self, employee, *, booking_id: int | None = None,
                     payment_id: int | None = None,
                     status: str | None = None) -> list[Refund]:
        require_permission(self.db, employee, "payment.view")
        return [_row_to_refund(r) for r in repo.list_refunds(
            self.db, booking_id=booking_id, payment_id=payment_id, status=status)]

    def get_refund(self, employee, refund_id: int) -> Refund | None:
        require_permission(self.db, employee, "payment.view")
        row = repo.get_refund(self.db, refund_id)
        return _row_to_refund(row) if row else None
