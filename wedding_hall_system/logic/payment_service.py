"""Payments, installments, financial summary and reconciliation
(Phase 5, business layer — SRS §8.1–§8.4, §8.6).

Recording a payment itself stays in ``BookingService.record_payment``
(Phase 3: deposit logic + auto-confirmation); this service owns what
Phase 5 adds: corrections/reversals, installment plans, overpayments,
the financial measures of SRS §8.6, and reconciliation.

Accounting basis (decision A-45, GAP G-18 — simplified, cash basis):
revenue is recognised when a payment is recorded; recognised revenue
is capped at the contract value (an overpayment is a refundable
liability, never revenue, FR-047); refunds reduce revenue when paid;
profit = recognised revenue − approved expenses. Every measure is
computed independently and labelled with its basis.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..config.settings import AppSettings, format_money
from ..data.database import DatabaseManager
from ..data.repositories import booking_repository as booking_repo
from ..data.repositories import finance_repository as repo
from ..data.repositories import system_repository
from ..utils.exceptions import (
    NotFoundError,
    PaymentError,
    ValidationError,
)
from ..utils.logging_config import get_logger
from . import audit_service
from .booking_service import BookingService
from .models import PAYMENT_METHODS, Installment, Payment
from .notification_service import notify
from .permissions import require_permission
from .pricing_service import PricingService
from .refund_service import RefundService

logger = get_logger(__name__)

# Bookings whose money still counts as contract value (SRS §8.6).
_ACTIVE_BOOKING_STATUSES = ("pending", "confirmed", "in_progress", "completed")


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _setting_int(db: DatabaseManager, key: str, default: int) -> int:
    try:
        return int(system_repository.get_setting(db, key, str(default)))
    except ValueError:
        return default


def _row_to_installment(row: dict, late_fee_percent: int = 0) -> Installment:
    late_fee = 0
    if row["status"] == "overdue" and late_fee_percent > 0:
        late_fee = round(int(row["amount"]) * late_fee_percent / 100)
    return Installment(
        id=row["id"], booking_id=row["booking_id"], sequence=row["sequence"],
        amount=row["amount"],
        due_at=_parse(row["due_at"]), status=row["status"],
        paid_payment_id=row["paid_payment_id"],
        late_fee_piasters=late_fee,
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


class PaymentService:
    def __init__(self, db: DatabaseManager, settings: AppSettings | None = None) -> None:
        self.db = db
        self.settings = settings or AppSettings.from_env()
        self.bookings = BookingService(db, settings)
        self.pricing = PricingService(db, settings)
        self.refunds = RefundService(db, settings)

    # -- corrections / reversals (SRS §8.2, BR-14) -------------------------
    def reverse_payment(self, employee, payment_id: int, *,
                        correction_type: str = "reversal",
                        reason: str = "") -> Payment:
        """Correct a recorded payment (SRS §8.2). A payment is never
        edited in place: a Reversal/Void record is created (reason,
        employee, timestamp, original reference) and the original is
        preserved with its new status. The correction always reverses
        the WHOLE payment (decision A-47): a partial correction is a
        full reversal followed by a new correct payment. Deposit and
        outstanding are recomputed; a confirmed booking is NOT
        de-confirmed by a reversal (decision A-49) — the shortfall is
        flagged by reconciliation instead."""
        require_permission(self.db, employee, "payment.record")
        if correction_type not in ("reversal", "void"):
            raise ValidationError(
                "Correction type must be 'reversal' or 'void'.")
        if not str(reason or "").strip():
            raise ValidationError("A correction needs a reason.")
        with self.db.transaction(immediate=True) as conn:
            payment = repo.get_payment(self.db, payment_id, conn=conn)
            if payment is None:
                raise NotFoundError(f"Payment #{payment_id} does not exist.")
            if payment["status"] != "recorded":
                raise ValidationError(
                    f"Only a recorded payment can be corrected "
                    f"(payment is {payment['status']}).")
            booking_id = int(payment["booking_id"])
            booking = booking_repo.get_booking(self.db, booking_id, conn=conn)
            assert booking is not None
            repo.insert_correction(
                self.db, original_payment_id=payment_id,
                correction_type=correction_type, amount=int(payment["amount"]),
                reason=reason, created_by=employee.id, conn=conn)
            # The correction type is 'reversal'/'void'; the payment
            # status it maps to is 'reversed'/'void' (schema CHECK).
            status = "reversed" if correction_type == "reversal" else "void"
            repo.update_payment_status(self.db, payment_id, status, conn=conn)
            total_paid = booking_repo.sum_recorded_payments(
                self.db, booking_id, conn=conn)
            deposit_paid = min(total_paid, int(booking["deposit_required"]))
            booking_repo.update_booking_deposit_paid(
                self.db, booking_id, deposit_paid, conn=conn)
            shortfall = ""
            if (booking["status"] == "confirmed"
                    and total_paid < int(booking["deposit_required"])):
                shortfall = (f" Booking stays Confirmed but the deposit is "
                             f"now short by "
                             f"{format_money(int(booking['deposit_required']) - total_paid)}"
                             f" — see reconciliation.")
            booking_repo.insert_booking_history(
                self.db, booking_id=booking_id, employee_id=employee.id,
                action="payment_corrected", old_value="recorded",
                new_value=status,
                reason=f"Payment {payment['number']} {correction_type}: "
                       f"{reason}.{shortfall}",
                conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id,
                action=f"payment.{correction_type}",
                entity_type="payment", entity_id=payment_id,
                old_value={"status": "recorded", "amount": int(payment["amount"])},
                new_value={"status": status},
                description=f"Payment {payment['number']} {correction_type}: "
                            f"{reason}.{shortfall}",
                conn=conn)
        logger.info("Payment #%s %s (%s)", payment_id, correction_type, reason)
        row = repo.get_payment(self.db, payment_id)
        assert row is not None
        return _row_to_payment(row)

    def list_corrections(self, employee, payment_id: int) -> list[dict]:
        require_permission(self.db, employee, "payment.view")
        if repo.get_payment(self.db, payment_id) is None:
            raise NotFoundError(f"Payment #{payment_id} does not exist.")
        return repo.list_corrections(self.db, payment_id)

    # -- installments (SRS §8.4) ---------------------------------------------
    def _finance_policy(self, booking_id: int) -> dict:
        return self.pricing.financial_policy_for_booking(booking_id)

    def create_installment_plan(self, employee, booking_id: int, *,
                                count: int | None = None) -> list[Installment]:
        """Create the installment plan for a Confirmed booking's
        remaining balance (decision A-46). One plan per booking: a
        second plan is rejected so paid installments are never
        rewritten. Due dates are spaced evenly between creation and the
        final deadline (event start minus the configured days); when
        the deadline has already passed, every installment is due now.
        Amounts split the balance evenly; the last installment takes
        the remainder."""
        require_permission(self.db, employee, "payment.record")
        booking = booking_repo.get_booking(self.db, booking_id)
        if booking is None:
            raise NotFoundError(f"Booking #{booking_id} does not exist.")
        if booking["status"] != "confirmed":
            raise ValidationError(
                "An installment plan needs a Confirmed booking "
                f"(current status: {booking['status']}).")
        if repo.count_installments(self.db, booking_id) > 0:
            raise ValidationError(
                "Booking already has an installment plan; create a new "
                "booking instead of rewriting paid installments.")
        policy = self._finance_policy(booking_id)
        count = count if count is not None else int(policy.get("installments_count", 3))
        if count < 1:
            raise ValidationError("Installment count must be at least 1.")
        collected = booking_repo.sum_recorded_payments(self.db, booking_id)
        paid_refunds = repo.sum_refunds_for_booking(
            self.db, booking_id, statuses=("paid",))
        remaining = int(booking["total_amount"]) - (collected - paid_refunds)
        if remaining <= 0:
            raise ValidationError(
                "Nothing is left to install: the booking is fully paid.")
        now = datetime.now()
        event_start = datetime.fromisoformat(booking["start_at"])
        deadline_days = int(policy.get("final_deadline_days_before_event", 7))
        final_deadline = event_start - timedelta(days=deadline_days)
        base = remaining // count
        remainder = remaining - base * count
        with self.db.transaction() as conn:
            created: list[Installment] = []
            for seq in range(1, count + 1):
                if final_deadline <= now:
                    due = now
                else:
                    due = now + (final_deadline - now) * seq / count
                amount = base + (remainder if seq == count else 0)
                installment_id = repo.insert_installment(
                    self.db, booking_id=booking_id, sequence=seq,
                    amount=amount, due_at=_iso(due), conn=conn)
                created.append(Installment(
                    id=installment_id, booking_id=booking_id, sequence=seq,
                    amount=amount, due_at=due, status="pending"))
            booking_repo.insert_booking_history(
                self.db, booking_id=booking_id, employee_id=employee.id,
                action="installment_plan_created", old_value="",
                new_value=str(count),
                reason=f"Installment plan created: {count} installments, "
                       f"balance {format_money(remaining)}, final deadline "
                       f"{final_deadline.strftime('%Y-%m-%d')}.",
                conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id,
                action="installment.plan_created",
                entity_type="booking", entity_id=booking_id,
                new_value={"installments": count, "balance": remaining},
                description=f"Installment plan created for booking "
                            f"{booking['number']}: {count} installments.",
                conn=conn)
        logger.info("Installment plan created for booking #%s (%s parts)",
                    booking_id, count)
        return created

    def _sweep_overdue(self, now: datetime | None = None,
                       conn=None) -> list[dict]:
        """Mark pending installments past their due date as overdue
        (lazy, GAP G-11) and notify the Accountant once per
        installment (the transition itself is the de-duplication)."""
        now = now or datetime.now()
        overdue = repo.list_overdue_installments(self.db, _iso(now), conn=conn)
        for row in overdue:
            repo.update_installment(self.db, int(row["id"]), status="overdue",
                                    conn=conn)
        return overdue

    def list_installments(self, employee, booking_id: int) -> list[Installment]:
        require_permission(self.db, employee, "payment.view")
        if booking_repo.get_booking(self.db, booking_id) is None:
            raise NotFoundError(f"Booking #{booking_id} does not exist.")
        policy = self._finance_policy(booking_id)
        late_fee_percent = int(policy.get("late_fee_percent", 0))
        with self.db.transaction() as conn:
            overdue = self._sweep_overdue(conn=conn)
            for row in overdue:
                if int(row["booking_id"]) == booking_id:
                    notify(self.db, type="installment_due",
                           title=f"Installment overdue — booking #{booking_id}",
                           body=f"Installment {row['sequence']} "
                                f"({format_money(int(row['amount']))}) is overdue.",
                           entity_type="booking", entity_id=booking_id, conn=conn)
        rows = repo.list_installments(self.db, booking_id)
        return [_row_to_installment(r, late_fee_percent) for r in rows]

    def pay_installment(self, employee, installment_id: int, method: str,
                        reference: str = "") -> Payment:
        """Pay one installment: records the payment (idempotent on the
        installment id, so a retry never double-charges) and links it.
        The payment flows through the normal recording path, so a
        deposit/top-up still behaves the same way."""
        require_permission(self.db, employee, "payment.record")
        if method not in PAYMENT_METHODS:
            raise PaymentError(f"Payment method must be one of {PAYMENT_METHODS}.")
        row = repo.get_installment(self.db, installment_id)
        if row is None:
            raise NotFoundError(f"Installment #{installment_id} does not exist.")
        if row["status"] == "paid":
            raise ValidationError("Installment is already paid.")
        if row["paid_payment_id"] is not None:
            raise ValidationError(
                "Installment already has a linked payment; it cannot be "
                "paid twice.")
        key = f"installment-{installment_id}"
        payment = self.bookings.record_payment(
            employee, int(row["booking_id"]), int(row["amount"]), method,
            reference=reference, idempotency_key=key)
        with self.db.transaction() as conn:
            current = repo.get_installment(self.db, installment_id, conn=conn)
            assert current is not None
            if current["status"] == "paid":
                raise ValidationError("Installment is already paid.")
            repo.update_installment(
                self.db, installment_id, status="paid",
                paid_payment_id=payment.id, conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="installment.paid",
                entity_type="installment", entity_id=installment_id,
                old_value={"status": current["status"]},
                new_value={"status": "paid", "payment_id": payment.id},
                description=f"Installment #{installment_id} paid via "
                            f"payment {payment.number}.",
                conn=conn)
        logger.info("Installment #%s paid via payment %s",
                    installment_id, payment.number)
        return payment

    def run_due_sweep(self, employee, now: datetime | None = None) -> dict:
        """Mark every overdue installment and notify once per
        installment. Called from the Finance screen (lazy, GAP G-11 —
        there is no background process)."""
        require_permission(self.db, employee, "finance.view")
        now = now or datetime.now()
        with self.db.transaction() as conn:
            overdue = self._sweep_overdue(now=now, conn=conn)
            for row in overdue:
                notify(self.db, type="installment_due",
                       title=f"Installment overdue — booking #{row['booking_id']}",
                       body=f"Installment {row['sequence']} "
                            f"({format_money(int(row['amount']))}) is overdue.",
                       entity_type="booking", entity_id=int(row["booking_id"]),
                       conn=conn)
        return {"overdue_marked": len(overdue)}

    # -- overpayment (SRS §8.3) ----------------------------------------------
    def overpayment_for_booking(self, employee, booking_id: int) -> dict:
        """Collected / net / overpayment for one booking (FR-047).
        Overpayment stays linked to this booking only — no general
        customer credit — and is shown separately, never counted as
        revenue beyond the contract value."""
        require_permission(self.db, employee, "payment.view")
        booking = booking_repo.get_booking(self.db, booking_id)
        if booking is None:
            raise NotFoundError(f"Booking #{booking_id} does not exist.")
        collected = booking_repo.sum_recorded_payments(self.db, booking_id)
        refunds_paid = repo.sum_refunds_for_booking(
            self.db, booking_id, statuses=("paid",))
        net = collected - refunds_paid
        total = int(booking["total_amount"])
        refundable = self.refunds.refundable_amount_for_booking(booking_id)
        return {
            "booking_id": booking_id,
            "number": booking["number"],
            "status": booking["status"],
            "total_amount": total,
            "collected": collected,
            "refunds_paid": refunds_paid,
            "net_collected": net,
            "overpayment": max(0, net - total),
            "outstanding": max(0, total - net),
            "refundable": refundable["refundable"],
            "refundable_basis": refundable["basis"],
        }

    # -- financial summary + reconciliation (SRS §8.6) ------------------------
    def financial_summary(self, employee, *, date_from: str | None = None,
                          date_to: str | None = None) -> dict:
        """Venue-wide financial measures (FR-050), each computed
        independently. Basis (decision A-45, cash basis): revenue is
        recognised when a payment is recorded, capped at the contract
        value; refunds reduce revenue when paid; profit = recognised
        revenue − approved expenses. Date windows filter by event date
        (contract value), payment confirmation date (collected), refund
        payment date (refunds) and expense date (expenses)."""
        require_permission(self.db, employee, "finance.view")
        bookings = booking_repo.list_bookings(
            self.db,
            start_from=date_from if date_from else None,
            start_to=date_to if date_to else None)
        active = [b for b in bookings if b["status"] in _ACTIVE_BOOKING_STATUSES]
        contract_value = sum(int(b["total_amount"]) for b in active)
        collected = repo.sum_recorded_payments_in_range(
            self.db, date_from=date_from, date_to=date_to)
        refunds_paid = repo.sum_paid_refunds(
            self.db, date_from=date_from, date_to=date_to)
        net_collected = collected - refunds_paid
        recognised = min(net_collected, contract_value)
        expenses_approved = repo.sum_expenses(
            self.db, approval_status="approved",
            date_from=date_from, date_to=date_to)
        expenses_paid = repo.sum_expenses(
            self.db, approval_status="approved", payment_status="paid",
            date_from=date_from, date_to=date_to)
        return {
            "contract_value": contract_value,
            "collected": collected,
            "refunds_paid": refunds_paid,
            "net_collected": net_collected,
            "recognised_revenue": recognised,
            "overpayment": max(0, net_collected - contract_value),
            "outstanding": max(0, contract_value - net_collected),
            "expenses_approved": expenses_approved,
            "expenses_paid": expenses_paid,
            "cash_in": collected,
            "cash_out": refunds_paid + expenses_paid,
            "cash_flow": collected - refunds_paid - expenses_paid,
            "profit": recognised - expenses_approved,
            "basis": ("Cash basis: revenue recognised when recorded, capped "
                      "at contract value; profit = recognised revenue − "
                      "approved expenses."),
        }

    def reconcile(self, employee) -> dict:
        """Reconciliation (FR-050/FR-052 support): per-booking money
        plus every discrepancy worth acting on. A discrepancy is a
        concrete problem (deposit shortfall, money due with no refund
        request, overdue installment, approved-but-unpaid refund), not
        a normal state — an overpayment is flagged on its row, not as
        a discrepancy."""
        require_permission(self.db, employee, "finance.view")
        summary = self.financial_summary(employee)
        rows: list[dict] = []
        discrepancies: list[dict] = []
        for booking in booking_repo.list_bookings(self.db):
            booking_id = int(booking["id"])
            collected = booking_repo.sum_recorded_payments(self.db, booking_id)
            refunds_paid = repo.sum_refunds_for_booking(
                self.db, booking_id, statuses=("paid",))
            net = collected - refunds_paid
            total = int(booking["total_amount"])
            installments = repo.list_installments(self.db, booking_id)
            overdue = [i for i in installments if i["status"] == "overdue"]
            pending = [i for i in installments if i["status"] == "pending"]
            refundable = self.refunds.refundable_amount_for_booking(booking_id)
            flags: list[str] = []
            if booking["status"] == "confirmed" and net < int(booking["deposit_required"]):
                flags.append("deposit shortfall")
                discrepancies.append({
                    "booking_id": booking_id, "number": booking["number"],
                    "issue": "deposit shortfall",
                    "detail": f"Confirmed but net collected "
                              f"{format_money(net)} is below the required "
                              f"deposit "
                              f"{format_money(int(booking['deposit_required']))}.",
                })
            if booking["status"] == "cancelled" and refundable["refundable"] > 0:
                open_refunds = repo.list_refunds(
                    self.db, booking_id=booking_id,
                    status="pending_approval") + repo.list_refunds(
                        self.db, booking_id=booking_id, status="approved")
                if not open_refunds:
                    flags.append("refundable balance with no refund request")
                    discrepancies.append({
                        "booking_id": booking_id, "number": booking["number"],
                        "issue": "refundable balance with no refund request",
                        "detail": f"{format_money(refundable['refundable'])} "
                                  f"refundable but no refund is requested.",
                    })
            if overdue:
                flags.append(f"{len(overdue)} overdue installment(s)")
                discrepancies.append({
                    "booking_id": booking_id, "number": booking["number"],
                    "issue": "overdue installment",
                    "detail": f"{len(overdue)} installment(s) past their due date.",
                })
            approved_unpaid = repo.list_refunds(
                self.db, booking_id=booking_id, status="approved")
            if approved_unpaid:
                flags.append(f"{len(approved_unpaid)} approved refund(s) unpaid")
                discrepancies.append({
                    "booking_id": booking_id, "number": booking["number"],
                    "issue": "approved refund unpaid",
                    "detail": f"{len(approved_unpaid)} approved refund(s) "
                              f"not yet paid.",
                })
            rows.append({
                "booking_id": booking_id, "number": booking["number"],
                "status": booking["status"], "total_amount": total,
                "collected": collected, "refunds_paid": refunds_paid,
                "net_collected": net,
                "outstanding": (max(0, total - net)
                                if booking["status"] in _ACTIVE_BOOKING_STATUSES
                                else 0),
                "overpayment": max(0, net - total),
                "deposit_required": int(booking["deposit_required"]),
                "deposit_paid": int(booking["deposit_paid"]),
                "installments_pending": len(pending),
                "installments_overdue": len(overdue),
                "refundable": refundable["refundable"],
                "flags": flags,
            })
        return {"summary": summary, "bookings": rows,
                "discrepancies": discrepancies}
