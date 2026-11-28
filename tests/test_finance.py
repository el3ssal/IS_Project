"""Phase 5 tests: financial management — payments/corrections,
installments, expenses, refunds, cancellation fees, rescheduling,
no-show, ownership transfer, policy snapshots, reconciliation, and
the remaining FR-060 notification triggers (SRS §8, §10).

Money is integer piasters throughout (1 EGP = 100). The seeded
Friday Classic Wedding booking totals 4,400,000 (44,000 EGP) with a
30% deposit of 1,320,000; the Monday equivalent is 4,000,000 with a
1,200,000 deposit. Tests run on 2026-11-26, so the Friday booking
(2026-12-04) is 8 days out (50% cancellation tier), 2026-12-18 is
22 days out (25% tier) and 2027-01-08 is 43 days out (10% tier).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from tests.conftest import Phase1TestCase
from wedding_hall_system.data.repositories import system_repository
from wedding_hall_system.logic.booking_service import BookingService
from wedding_hall_system.logic.contract_service import ContractService
from wedding_hall_system.logic.customer_service import CustomerService
from wedding_hall_system.logic.expense_service import ExpenseService
from wedding_hall_system.logic.notification_service import NotificationService
from wedding_hall_system.logic.payment_service import PaymentService
from wedding_hall_system.logic.pricing_service import PricingService
from wedding_hall_system.logic.refund_service import RefundService
from wedding_hall_system.logic.task_service import TaskService
from wedding_hall_system.utils.exceptions import (
    AuthorizationError,
    BookingConflictError,
    NotFoundError,
    PaymentError,
    RefundLimitExceededError,
    ValidationError,
)

FRIDAY = datetime(2026, 12, 4, 18, 0)
FRIDAY_END = datetime(2026, 12, 4, 23, 0)
MID = datetime(2026, 12, 18, 18, 0)          # 22 days out: 25% tier
MID_END = datetime(2026, 12, 18, 23, 0)
FAR = datetime(2027, 1, 8, 18, 0)            # 43 days out: 10% tier
FAR_END = datetime(2027, 1, 8, 23, 0)
MONDAY = datetime(2026, 12, 7, 18, 0)
MONDAY_END = datetime(2026, 12, 7, 23, 0)
MONDAY_MID = datetime(2026, 12, 7, 12, 0)
MONDAY_MID_END = datetime(2026, 12, 7, 17, 0)


class TestFinance(Phase1TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.login_as("owner", "Owner123!")[0]
        self.manager = self.login_as("manager", "Manager123!")[0]
        self.receptionist = self.login_as("receptionist", "Reception123!")[0]
        self.accountant = self.login_as("accountant", "Account123!")[0]
        self.staff = self.login_as("staff", "Staff123!")[0]
        self.bookings = BookingService(self.db, self.settings)
        self.payments = PaymentService(self.db, self.settings)
        self.expenses = ExpenseService(self.db, self.settings)
        self.refunds = RefundService(self.db, self.settings)
        self.pricing = PricingService(self.db, self.settings)
        self.contracts = ContractService(self.db, self.settings)
        self.tasks = TaskService(self.db)
        self.notifications = NotificationService(self.db)
        customers = CustomerService(self.db, self.settings).list_customers(self.owner)
        self.customer = next(
            c for c in customers if c.full_name.startswith("Ahmed Hassan"))
        self.customer2 = next(
            c for c in customers if c.full_name.startswith("Mona Ali"))

    def _book(self, start=FRIDAY, end=FRIDAY_END, hall_id=1, guests=200,
              **kwargs):
        kwargs.setdefault("package_id", 1)
        kwargs.setdefault("event_type_id", 1)
        return self.bookings.create_booking(
            self.receptionist, customer_id=self.customer.id, hall_id=hall_id,
            start_at=start, end_at=end, guests=guests, **kwargs)

    def _confirm(self, booking, amount=1_320_000):
        return self.bookings.record_payment(
            self.accountant, booking.id, amount, "cash")

    def _overpay(self, booking, extra=600_000):
        """Confirm, then pay the full balance plus ``extra``."""
        self._confirm(booking)
        total = booking.total_amount
        self.bookings.record_payment(
            self.accountant, booking.id,
            total - booking.deposit_required + extra, "bank_transfer")
        return self.bookings.get_booking(self.owner, booking.id)

    # -- payments / corrections (FR-045, FR-046, T-13, T-14) --------------
    def test_payment_idempotency_same_key_twice_is_one_payment(self):
        booking = self._book()
        first = self.bookings.record_payment(
            self.accountant, booking.id, 500_000, "cash",
            idempotency_key="retry-key-1")
        second = self.bookings.record_payment(
            self.accountant, booking.id, 500_000, "cash",
            idempotency_key="retry-key-1")
        self.assertEqual(first.id, second.id)
        self.assertEqual(first.number, second.number)
        rows = self.db.query_all(
            "SELECT * FROM payments WHERE booking_id = ?", (booking.id,))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["amount"], 500_000)

    def test_partial_payments_accumulate_to_confirm(self):
        booking = self._book()
        self.bookings.record_payment(self.accountant, booking.id, 500_000, "cash")
        still_pending = self.bookings.get_booking(self.owner, booking.id)
        self.assertEqual(still_pending.status, "pending")
        self.bookings.record_payment(self.accountant, booking.id, 820_000, "visa_card")
        confirmed = self.bookings.get_booking(self.owner, booking.id)
        self.assertEqual(confirmed.status, "confirmed")
        self.assertEqual(confirmed.deposit_paid, 1_320_000)

    def test_reversal_preserves_original_and_recomputes_deposit(self):
        booking = self._book()
        payment = self._confirm(booking)
        reversed_payment = self.payments.reverse_payment(
            self.accountant, payment.id, reason="Wrong method recorded")
        self.assertEqual(reversed_payment.status, "reversed")
        self.assertEqual(reversed_payment.amount, 1_320_000)
        corrections = self.payments.list_corrections(self.accountant, payment.id)
        self.assertEqual(len(corrections), 1)
        self.assertEqual(corrections[0]["correction_type"], "reversal")
        self.assertEqual(corrections[0]["reason"], "Wrong method recorded")
        # Original row is preserved; deposit recomputed; the booking
        # stays Confirmed (decision A-49) with a shortfall to reconcile.
        after = self.bookings.get_booking(self.owner, booking.id)
        self.assertEqual(after.status, "confirmed")
        self.assertEqual(after.deposit_paid, 0)
        # A second reversal of the same payment is rejected.
        with self.assertRaises(ValidationError):
            self.payments.reverse_payment(
                self.accountant, payment.id, reason="again")

    def test_void_needs_reason_and_recorded_payment_only(self):
        booking = self._book()
        payment = self._confirm(booking)
        with self.assertRaises(ValidationError):
            self.payments.reverse_payment(
                self.accountant, payment.id, correction_type="void", reason=" ")
        voided = self.payments.reverse_payment(
            self.accountant, payment.id, correction_type="void",
            reason="Duplicate entry")
        self.assertEqual(voided.status, "void")
        with self.assertRaises(ValidationError):
            self.payments.reverse_payment(
                self.accountant, payment.id, reason="already void")

    def test_receptionist_cannot_reverse_payments(self):
        booking = self._book()
        payment = self._confirm(booking)
        with self.assertRaises(AuthorizationError):
            self.payments.reverse_payment(
                self.receptionist, payment.id, reason="nope")

    def test_overpayment_shown_separately_not_revenue(self):
        booking = self._overpay(self._book())
        over = self.payments.overpayment_for_booking(self.owner, booking.id)
        self.assertEqual(over["collected"], 5_000_000)
        self.assertEqual(over["overpayment"], 600_000)
        self.assertEqual(over["outstanding"], 0)
        self.assertEqual(over["refundable"], 600_000)
        self.assertIn("overpayment", over["refundable_basis"])

    # -- installments (FR-048) ----------------------------------------------
    def test_installment_plan_creation_amounts_and_dates(self):
        booking = self._book(start=FAR, end=FAR_END)
        self._confirm(booking)
        plan = self.payments.create_installment_plan(self.accountant, booking.id)
        self.assertEqual(len(plan), 3)
        self.assertEqual(sum(i.amount for i in plan), 3_080_000)
        self.assertEqual([i.sequence for i in plan], [1, 2, 3])
        self.assertEqual(plan[0].amount, 1_026_666)
        self.assertEqual(plan[2].amount, 1_026_668)
        dues = [i.due_at for i in plan]
        self.assertTrue(dues[0] < dues[1] < dues[2])
        # Final deadline is event start minus 7 days (2027-01-01).
        self.assertLessEqual(dues[2], datetime(2027, 1, 1, 23, 59))
        self.assertTrue(all(i.status == "pending" for i in plan))

    def test_installment_plan_rejected_for_pending_and_duplicate(self):
        booking = self._book()
        with self.assertRaises(ValidationError):
            self.payments.create_installment_plan(self.accountant, booking.id)
        self._confirm(booking)
        self.payments.create_installment_plan(self.accountant, booking.id)
        with self.assertRaises(ValidationError):
            self.payments.create_installment_plan(self.accountant, booking.id)

    def test_pay_installment_links_payment_and_marks_paid(self):
        booking = self._book(start=FAR, end=FAR_END)
        self._confirm(booking)
        plan = self.payments.create_installment_plan(self.accountant, booking.id)
        payment = self.payments.pay_installment(
            self.accountant, plan[0].id, "bank_transfer", reference="INST-1")
        self.assertEqual(payment.amount, plan[0].amount)
        after = self.payments.list_installments(self.owner, booking.id)
        self.assertEqual(after[0].status, "paid")
        self.assertEqual(after[0].paid_payment_id, payment.id)
        with self.assertRaises(ValidationError):
            self.payments.pay_installment(
                self.accountant, plan[0].id, "cash")

    def test_installment_overdue_detection_and_notification(self):
        booking = self._book(start=FAR, end=FAR_END)
        self._confirm(booking)
        self.payments.create_installment_plan(self.accountant, booking.id)
        before = self.notifications.unread_count(self.accountant)
        result = self.payments.run_due_sweep(
            self.accountant, now=datetime(2027, 1, 5, 12, 0))
        self.assertEqual(result["overdue_marked"], 3)
        rows = self.payments.list_installments(self.owner, booking.id)
        self.assertTrue(all(i.status == "overdue" for i in rows))
        # One installment_due notification per overdue installment.
        self.assertEqual(
            self.notifications.unread_count(self.accountant), before + 3)
        # A second sweep finds nothing new to mark.
        again = self.payments.run_due_sweep(
            self.accountant, now=datetime(2027, 1, 5, 12, 0))
        self.assertEqual(again["overdue_marked"], 0)

    def test_installment_plan_rejected_when_fully_paid(self):
        booking = self._overpay(self._book())
        with self.assertRaises(ValidationError):
            self.payments.create_installment_plan(self.accountant, booking.id)

    # -- expenses (FR-049) ---------------------------------------------------
    def test_expense_record_approve_pay_flow(self):
        expense = self.expenses.record_expense(
            self.accountant, amount_piasters=5_000_000, date="2026-11-26",
            category="Supplies", description="Tables", method="cash")
        self.assertEqual(expense.approval_status, "pending")
        self.assertEqual(expense.payment_status, "pending")
        expense = self.expenses.approve_expense(self.accountant, expense.id)
        self.assertEqual(expense.approval_status, "approved")
        expense = self.expenses.mark_expense_paid(self.accountant, expense.id)
        self.assertEqual(expense.payment_status, "paid")

    def test_expense_payment_requires_approval(self):
        expense = self.expenses.record_expense(
            self.accountant, amount_piasters=100_000, date="2026-11-26",
            category="Utilities")
        with self.assertRaises(ValidationError):
            self.expenses.mark_expense_paid(self.accountant, expense.id)

    def test_expense_scope_validation(self):
        with self.assertRaises(NotFoundError):
            self.expenses.record_expense(
                self.accountant, amount_piasters=100_000, date="2026-11-26",
                category="Maintenance", scope_type="hall", scope_id=99999)
        with self.assertRaises(NotFoundError):
            self.expenses.record_expense(
                self.accountant, amount_piasters=100_000, date="2026-11-26",
                category="Maintenance", scope_type="event", scope_id=99999)
        with self.assertRaises(ValidationError):
            self.expenses.record_expense(
                self.accountant, amount_piasters=100_000, date="2026-11-26",
                category="Maintenance", scope_type="venue", scope_id=1)
        hall_expense = self.expenses.record_expense(
            self.accountant, amount_piasters=100_000, date="2026-11-26",
            category="Maintenance", scope_type="hall", scope_id=1)
        self.assertEqual(hall_expense.scope_type, "hall")

    def test_expense_approval_permission(self):
        expense = self.expenses.record_expense(
            self.accountant, amount_piasters=100_000, date="2026-11-26",
            category="Marketing")
        with self.assertRaises(AuthorizationError):
            self.expenses.approve_expense(self.receptionist, expense.id)
        # The Manager holds expense.approve.
        approved = self.expenses.approve_expense(self.manager, expense.id)
        self.assertEqual(approved.approval_status, "approved")

    def test_expense_totals_by_category(self):
        first = self.expenses.record_expense(
            self.accountant, amount_piasters=200_000, date="2026-11-26",
            category="Supplies")
        second = self.expenses.record_expense(
            self.accountant, amount_piasters=300_000, date="2026-11-26",
            category="Utilities")
        self.expenses.record_expense(
            self.accountant, amount_piasters=999_000, date="2026-11-26",
            category="Supplies")
        self.expenses.approve_expense(self.accountant, first.id)
        self.expenses.approve_expense(self.accountant, second.id)
        totals = self.expenses.totals(self.owner)
        self.assertEqual(totals["approved"], 500_000)
        self.assertEqual(totals["pending"], 999_000)
        self.assertEqual(totals["by_category_approved"]["Supplies"], 200_000)
        self.assertEqual(totals["by_category_approved"]["Utilities"], 300_000)

    # -- refunds (FR-051, T-15) ----------------------------------------------
    def test_refund_over_cap_rejected(self):
        booking = self._overpay(self._book())
        payment = self.bookings.list_payments(self.owner, booking.id)[0]
        with self.assertRaises(RefundLimitExceededError):
            self.refunds.request_refund(
                self.accountant, payment.id, 700_000, "Too much")
        refund = self.refunds.request_refund(
            self.accountant, payment.id, 600_000, "Overpayment refund")
        self.assertEqual(refund.status, "pending_approval")

    def test_refund_rejected_when_nothing_refundable(self):
        booking = self._book()
        self._confirm(booking)
        payment = self.bookings.list_payments(self.owner, booking.id)[0]
        with self.assertRaises(RefundLimitExceededError):
            self.refunds.request_refund(
                self.accountant, payment.id, 100_000, "No overpayment")

    def test_refund_workflow_approve_pay(self):
        booking = self._overpay(self._book())
        payment = self.bookings.list_payments(self.owner, booking.id)[0]
        refund = self.refunds.request_refund(
            self.accountant, payment.id, 200_000, "Customer overpaid")
        self.assertEqual(refund.status, "pending_approval")
        refund = self.refunds.approve_refund(self.accountant, refund.id)
        self.assertEqual(refund.status, "approved")
        refund = self.refunds.mark_refund_paid(self.accountant, refund.id)
        self.assertEqual(refund.status, "paid")
        over = self.payments.overpayment_for_booking(self.owner, booking.id)
        self.assertEqual(over["refunds_paid"], 200_000)
        self.assertEqual(over["refundable"], 400_000)
        # The original payment is untouched.
        kept = self.bookings.list_payments(self.owner, booking.id)[0]
        self.assertEqual(kept.amount, 1_320_000)
        self.assertEqual(kept.status, "recorded")

    def test_refund_cap_rechecked_at_approval(self):
        booking = self._overpay(self._book())
        payment = self.bookings.list_payments(self.owner, booking.id)[0]
        first = self.refunds.request_refund(
            self.accountant, payment.id, 400_000, "First")
        second = self.refunds.request_refund(
            self.accountant, payment.id, 400_000, "Second")
        self.refunds.approve_refund(self.accountant, first.id)
        # Only 200,000 of the 600,000 overpayment is left now.
        with self.assertRaises(RefundLimitExceededError):
            self.refunds.approve_refund(self.accountant, second.id)

    def test_refund_cancel_before_paid_and_paid_cannot_cancel(self):
        booking = self._overpay(self._book())
        payment = self.bookings.list_payments(self.owner, booking.id)[0]
        refund = self.refunds.request_refund(
            self.accountant, payment.id, 100_000, "Changed mind")
        cancelled = self.refunds.cancel_refund(
            self.accountant, refund.id, "Customer withdrew the request")
        self.assertEqual(cancelled.status, "cancelled")
        other = self.refunds.request_refund(
            self.accountant, payment.id, 100_000, "Second")
        self.refunds.approve_refund(self.accountant, other.id)
        self.refunds.mark_refund_paid(self.accountant, other.id)
        with self.assertRaises(ValidationError):
            self.refunds.cancel_refund(
                self.accountant, other.id, "Too late")

    def test_refund_reject_flow(self):
        booking = self._overpay(self._book())
        payment = self.bookings.list_payments(self.owner, booking.id)[0]
        refund = self.refunds.request_refund(
            self.accountant, payment.id, 100_000, "Please refund")
        rejected = self.refunds.reject_refund(self.accountant, refund.id, "No")
        self.assertEqual(rejected.status, "rejected")

    # -- cancellation (FR-052, T-16) -----------------------------------------
    def test_cancel_confirmed_applies_tier_fee_and_auto_refund(self):
        booking = self._book(start=FAR, end=FAR_END)
        self._confirm(booking)
        cancelled = self.bookings.cancel_booking(
            self.manager, booking.id, "Customer changed mind")
        self.assertEqual(cancelled.status, "cancelled")
        rows = self.db.query_all(
            "SELECT * FROM cancellation_records WHERE booking_id = ?",
            (booking.id,))
        self.assertEqual(len(rows), 1)
        # 43 days out: 10% of 4,400,000 = 440,000 fee.
        self.assertEqual(rows[0]["fee_amount"], 440_000)
        self.assertEqual(rows[0]["refundable_amount"], 880_000)
        refunds = self.refunds.list_refunds(self.owner, booking_id=booking.id)
        self.assertEqual(len(refunds), 1)
        self.assertEqual(refunds[0].amount, 880_000)
        self.assertEqual(refunds[0].status, "pending_approval")

    def test_cancel_close_to_event_forfeits_deposit(self):
        booking = self._book()  # Friday, 8 days out: 50% tier
        self._confirm(booking)
        self.bookings.cancel_booking(self.manager, booking.id, "Too late")
        rows = self.db.query_all(
            "SELECT * FROM cancellation_records WHERE booking_id = ?",
            (booking.id,))
        # Nominal fee is 50% of 4,400,000 = 2,200,000, but the fee is
        # capped at the amount paid (decision A-43): the whole deposit
        # is forfeited and nothing is refundable.
        self.assertEqual(rows[0]["fee_amount"], 1_320_000)
        self.assertEqual(rows[0]["refundable_amount"], 0)
        self.assertEqual(
            self.refunds.list_refunds(self.owner, booking_id=booking.id), [])

    def test_cancel_mid_tier_fee(self):
        booking = self._book(start=MID, end=MID_END)  # 22 days: 25% tier
        self._confirm(booking)
        self.bookings.cancel_booking(self.manager, booking.id, "Changed plans")
        rows = self.db.query_all(
            "SELECT * FROM cancellation_records WHERE booking_id = ?",
            (booking.id,))
        self.assertEqual(rows[0]["fee_amount"], 1_100_000)
        self.assertEqual(rows[0]["refundable_amount"], 220_000)

    def test_cancel_pending_keeps_fee_zero(self):
        booking = self._book()
        self.bookings.record_payment(self.accountant, booking.id, 500_000, "cash")
        self.bookings.cancel_booking(self.manager, booking.id, "Never confirmed")
        rows = self.db.query_all(
            "SELECT * FROM cancellation_records WHERE booking_id = ?",
            (booking.id,))
        self.assertEqual(rows[0]["fee_amount"], 0)
        self.assertEqual(rows[0]["refundable_amount"], 500_000)
        refunds = self.refunds.list_refunds(self.owner, booking_id=booking.id)
        self.assertEqual(len(refunds), 1)
        self.assertEqual(refunds[0].amount, 500_000)

    def test_cancel_releases_hall_while_refund_unpaid(self):
        booking = self._book(start=FAR, end=FAR_END)
        self._confirm(booking)
        self.bookings.cancel_booking(self.manager, booking.id, "Cancel")
        # The auto-created refund is still pending_approval (unpaid),
        # yet the same hall/slot can be booked again immediately (T-16).
        replacement = self._book(start=FAR, end=FAR_END)
        self.assertEqual(replacement.status, "pending")
        self.assertNotEqual(replacement.id, booking.id)

    def test_cancel_fee_uses_confirmation_time_snapshot(self):
        booking = self._book(start=FAR, end=FAR_END)
        self._confirm(booking)
        # The Owner changes the policy AFTER confirmation: 100% fee.
        system_repository.set_setting(
            self.db, "finance.cancellation_policy",
            json.dumps([{"min_days_before_event": 0,
                         "fee_percent_of_total": 100}]))
        self.bookings.cancel_booking(self.manager, booking.id, "After change")
        rows = self.db.query_all(
            "SELECT * FROM cancellation_records WHERE booking_id = ?",
            (booking.id,))
        # Frozen snapshot still says 10%: fee 440,000, refundable 880,000.
        self.assertEqual(rows[0]["fee_amount"], 440_000)
        self.assertEqual(rows[0]["refundable_amount"], 880_000)

    def test_financial_snapshot_frozen_after_policy_change(self):
        booking = self._book()
        self._confirm(booking)
        system_repository.set_setting(
            self.db, "finance.installments_count", "5")
        snapshots = self.pricing.get_snapshots(self.owner, booking.id)
        self.assertEqual(
            snapshots["financial_policy"]["installments_count"], 3)
        self.assertEqual(
            snapshots["financial_policy"]["cancellation_policy"][0]
            ["fee_percent_of_total"], 10)

    # -- no-show (FR-054) ------------------------------------------------------
    def test_no_show_forfeits_deposit(self):
        booking = self._book()
        self._confirm(booking)
        no_show = self.bookings.mark_no_show(
            self.receptionist, booking.id, "Nobody came")
        self.assertEqual(no_show.status, "no_show")
        rows = self.db.query_all(
            "SELECT * FROM no_show_records WHERE booking_id = ?",
            (booking.id,))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["fee_amount"], 1_320_000)
        self.assertEqual(rows[0]["refundable_amount"], 0)
        refundable = self.refunds.refundable_amount_for_booking(booking.id)
        self.assertEqual(refundable["refundable"], 0)

    # -- rescheduling (FR-053) -------------------------------------------------
    def test_reschedule_reprices_and_preserves_history(self):
        booking = self._book()
        self._confirm(booking)
        moved = self.bookings.reschedule_booking(
            self.manager, booking.id, new_start_at=MONDAY,
            new_end_at=MONDAY_END, reason="Customer asked for Monday")
        self.assertEqual(moved.start_at, MONDAY)
        # Monday has no weekend surcharge: 40,000 EGP.
        self.assertEqual(moved.total_amount, 4_000_000)
        self.assertEqual(moved.deposit_required, 1_200_000)
        history = self.db.query_all(
            "SELECT * FROM reschedule_history WHERE booking_id = ?",
            (booking.id,))
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["old_amount"], 4_400_000)
        self.assertEqual(history[0]["new_amount"], 4_000_000)
        items = self.bookings.list_price_items(self.owner, booking.id)
        self.assertEqual(sum(i["amount"] for i in items), 4_000_000)
        timeline = self.bookings.timeline(self.owner, booking.id)
        self.assertIn("rescheduled", [t["action"] for t in timeline])

    def test_reschedule_conflict_rejected_and_atomic(self):
        booking = self._book(start=MONDAY, end=MONDAY_END)
        self._confirm(booking, amount=1_200_000)
        other = self._book(start=MONDAY_MID, end=MONDAY_MID_END,
                           package_id=None, event_type_id=None, guests=100)
        with self.assertRaises(BookingConflictError):
            self.bookings.reschedule_booking(
                self.manager, booking.id, new_start_at=MONDAY_MID,
                new_end_at=MONDAY_MID_END, reason="Into the other booking")
        unchanged = self.bookings.get_booking(self.owner, booking.id)
        self.assertEqual(unchanged.start_at, MONDAY)
        self.assertEqual(unchanged.total_amount, 4_000_000)
        self.assertEqual(
            self.db.query_one(
                "SELECT COUNT(*) AS n FROM reschedule_history "
                "WHERE booking_id = ?", (booking.id,))["n"], 0)
        self.assertEqual(other.status, "pending")

    def test_reschedule_price_increase_becomes_outstanding_with_due_date(self):
        booking = self._book(start=MONDAY, end=MONDAY_END)
        self._confirm(booking, amount=1_200_000)
        moved = self.bookings.reschedule_booking(
            self.manager, booking.id, new_start_at=FRIDAY,
            new_end_at=FRIDAY_END, reason="Customer wants Friday")
        self.assertEqual(moved.total_amount, 4_400_000)
        # Status stays Confirmed; the 400,000 EGP increase is
        # outstanding with a due date. The due date is the event
        # minus 7 days (2026-11-27), clamped to "now" when that
        # deadline is already past — so the expectation is computed
        # from today, not hard-coded (it broke on 2026-11-28).
        self.assertEqual(moved.status, "confirmed")
        profit = self.bookings.profit(self.owner, booking.id)
        self.assertEqual(profit["outstanding"], 3_200_000)
        history = self.db.query_all(
            "SELECT * FROM reschedule_history WHERE booking_id = ?",
            (booking.id,))
        due = FRIDAY - timedelta(days=7)
        expected_due = (due if due > datetime.now() else datetime.now())
        self.assertEqual(history[0]["due_at"][:10],
                         expected_due.strftime("%Y-%m-%d"))

    def test_reschedule_shifts_task_due_dates(self):
        booking = self._book()
        self._confirm(booking)
        plan = self.tasks.generate_event_plan(self.manager, booking.id)
        before = self.tasks.list_tasks(
            self.manager, event_plan_id=int(plan["id"]))
        self.assertEqual(len(before), 4)
        self.bookings.reschedule_booking(
            self.manager, booking.id, new_start_at=datetime(2026, 12, 11, 18, 0),
            new_end_at=datetime(2026, 12, 11, 23, 0), reason="One week later")
        after = self.tasks.list_tasks(
            self.manager, event_plan_id=int(plan["id"]))
        for old, new in zip(before, after):
            # list_tasks returns raw rows (due_at is an ISO string).
            self.assertEqual(
                datetime.fromisoformat(new["due_at"])
                - datetime.fromisoformat(old["due_at"]),
                timedelta(days=7))

    def test_reschedule_permission(self):
        booking = self._book()
        with self.assertRaises(AuthorizationError):
            self.bookings.reschedule_booking(
                self.receptionist, booking.id, new_start_at=MONDAY,
                new_end_at=MONDAY_END, reason="nope")

    def test_reschedule_keeps_manual_discount(self):
        booking = self._book(discount={
            "type": "percent", "value": 10, "reason": "Loyal customer"})
        self._confirm(booking, amount=1_188_000)
        moved = self.bookings.reschedule_booking(
            self.manager, booking.id, new_start_at=MONDAY,
            new_end_at=MONDAY_END, reason="Monday instead")
        # Monday 40,000 EGP with the same 10% discount = 36,000 EGP.
        self.assertEqual(moved.total_amount, 3_600_000)

    # -- ownership transfer (FR-056) -------------------------------------------
    def test_transfer_preserves_payments_and_history(self):
        booking = self._book()
        payment = self._confirm(booking)
        moved = self.bookings.transfer_booking(
            self.manager, booking.id, new_customer_id=self.customer2.id,
            reason="Bride's family takes over")
        self.assertEqual(moved.customer_id, self.customer2.id)
        transfers = self.db.query_all(
            "SELECT * FROM booking_transfers WHERE booking_id = ?",
            (booking.id,))
        self.assertEqual(len(transfers), 1)
        self.assertEqual(transfers[0]["old_customer_id"], self.customer.id)
        self.assertEqual(transfers[0]["new_customer_id"], self.customer2.id)
        # Payments stay attached, payer identity never rewritten.
        kept = self.bookings.list_payments(self.owner, booking.id)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].id, payment.id)
        self.assertEqual(kept[0].recorded_by, self.accountant.id)
        timeline = self.bookings.timeline(self.owner, booking.id)
        self.assertIn("ownership_transferred",
                      [t["action"] for t in timeline])

    def test_transfer_rejected_for_wrong_states_and_same_customer(self):
        booking = self._book()
        self.bookings.cancel_booking(self.manager, booking.id, "cancelled")
        with self.assertRaises(ValidationError):
            self.bookings.transfer_booking(
                self.manager, booking.id, new_customer_id=self.customer2.id,
                reason="too late")
        other = self._book(start=MONDAY, end=MONDAY_END)
        with self.assertRaises(ValidationError):
            self.bookings.transfer_booking(
                self.manager, other.id, new_customer_id=self.customer.id,
                reason="same customer")
        with self.assertRaises(AuthorizationError):
            self.bookings.transfer_booking(
                self.receptionist, other.id,
                new_customer_id=self.customer2.id, reason="nope")

    def test_transfer_regenerates_contract_version(self):
        booking = self._book()
        self._confirm(booking)
        contract = self.contracts.generate_contract(
            self.manager, booking.id, "Standard Wedding Contract")
        self.bookings.transfer_booking(
            self.manager, booking.id, new_customer_id=self.customer2.id,
            reason="New owner")
        versions = self.contracts.list_versions(self.manager, contract.id)
        self.assertEqual(len(versions), 2)
        self.assertIn("Mona Ali", versions[1].body)

    # -- reopening (FR-055) ------------------------------------------------------
    def test_reopen_rejected_and_linked_rebooking(self):
        booking = self._book()
        self.bookings.cancel_booking(self.manager, booking.id, "cancelled")
        with self.assertRaises(ValidationError):
            self.bookings.cancel_booking(self.manager, booking.id, "again")
        linked = self.bookings.create_booking(
            self.receptionist, customer_id=self.customer.id, hall_id=1,
            package_id=1, event_type_id=1, start_at=FRIDAY, end_at=FRIDAY_END,
            guests=200, previous_booking_id=booking.id)
        self.assertEqual(linked.previous_booking_id, booking.id)
        self.assertEqual(linked.status, "pending")
        pending_booking = self._book(start=MONDAY, end=MONDAY_END)
        with self.assertRaises(ValidationError):
            self.bookings.create_booking(
                self.receptionist, customer_id=self.customer.id, hall_id=1,
                package_id=1, event_type_id=1, start_at=FAR, end_at=FAR_END,
                guests=200, previous_booking_id=pending_booking.id)

    # -- financial summary + reconciliation (FR-050) ------------------------------
    def test_financial_summary_measures(self):
        booking = self._book()
        self._confirm(booking)
        expense = self.expenses.record_expense(
            self.accountant, amount_piasters=500_000, date="2026-11-26",
            category="Supplies")
        self.expenses.approve_expense(self.accountant, expense.id)
        summary = self.payments.financial_summary(self.owner)
        self.assertEqual(summary["contract_value"], 4_400_000)
        self.assertEqual(summary["collected"], 1_320_000)
        self.assertEqual(summary["recognised_revenue"], 1_320_000)
        self.assertEqual(summary["outstanding"], 3_080_000)
        self.assertEqual(summary["expenses_approved"], 500_000)
        self.assertEqual(summary["profit"], 820_000)

    def test_financial_summary_overpayment_is_not_revenue(self):
        self._overpay(self._book())
        summary = self.payments.financial_summary(self.owner)
        self.assertEqual(summary["contract_value"], 4_400_000)
        self.assertEqual(summary["collected"], 5_000_000)
        self.assertEqual(summary["recognised_revenue"], 4_400_000)
        self.assertEqual(summary["overpayment"], 600_000)
        self.assertEqual(summary["outstanding"], 0)

    def test_reconciliation_flags_deposit_shortfall(self):
        booking = self._book()
        payment = self._confirm(booking)
        self.payments.reverse_payment(
            self.accountant, payment.id, reason="Wrong method")
        result = self.payments.reconcile(self.owner)
        issues = [d for d in result["discrepancies"]
                  if d["booking_id"] == booking.id]
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["issue"], "deposit shortfall")

    def test_reconciliation_flags_unrefunded_cancellation(self):
        booking = self._book(start=FAR, end=FAR_END)
        self._confirm(booking)
        self.bookings.cancel_booking(self.manager, booking.id, "Cancel")
        # Auto-refund exists (pending) — no discrepancy yet.
        result = self.payments.reconcile(self.owner)
        self.assertEqual(
            [d for d in result["discrepancies"]
             if d["booking_id"] == booking.id], [])
        refund = self.refunds.list_refunds(self.owner, booking_id=booking.id)[0]
        self.refunds.cancel_refund(
            self.accountant, refund.id, "Customer declined the refund")
        result = self.payments.reconcile(self.owner)
        issues = [d for d in result["discrepancies"]
                  if d["booking_id"] == booking.id]
        self.assertEqual(len(issues), 1)
        self.assertEqual(
            issues[0]["issue"], "refundable balance with no refund request")

    def test_profit_includes_refunds(self):
        booking = self._overpay(self._book())
        payment = self.bookings.list_payments(self.owner, booking.id)[0]
        refund = self.refunds.request_refund(
            self.accountant, payment.id, 200_000, "Overpayment")
        self.refunds.approve_refund(self.accountant, refund.id)
        self.refunds.mark_refund_paid(self.accountant, refund.id)
        profit = self.bookings.profit(self.owner, booking.id)
        self.assertEqual(profit["collected"], 5_000_000)
        self.assertEqual(profit["refunds_paid"], 200_000)
        self.assertEqual(profit["net_collected"], 4_800_000)
        self.assertEqual(profit["overpayment"], 400_000)

    # -- audit trail (FR-062, T-19) ----------------------------------------------
    def test_finance_actions_write_audit_with_old_and_new(self):
        booking = self._book()
        payment = self._confirm(booking)
        self.payments.reverse_payment(
            self.accountant, payment.id, reason="Wrong method")
        refundable = self.refunds.refundable_amount_for_booking(booking.id)
        self.assertEqual(refundable["refundable"], 0)  # nothing to refund now
        expense = self.expenses.record_expense(
            self.accountant, amount_piasters=100_000, date="2026-11-26",
            category="Supplies")
        self.expenses.approve_expense(self.accountant, expense.id)
        self.bookings.transfer_booking(
            self.manager, booking.id, new_customer_id=self.customer2.id,
            reason="Family takes over")
        actions = {r["action"] for r in self.db.query_all(
            "SELECT action FROM audit_logs")}
        self.assertIn("payment.reversal", actions)
        self.assertIn("expense.recorded", actions)
        self.assertIn("expense.approved", actions)
        self.assertIn("booking.ownership_transferred", actions)
        reversal = self.db.query_one(
            "SELECT * FROM audit_logs WHERE action = 'payment.reversal'")
        self.assertIn("recorded", reversal["old_value_json"])
        self.assertIn("reversed", reversal["new_value_json"])
        transfer = self.db.query_one(
            "SELECT * FROM audit_logs "
            "WHERE action = 'booking.ownership_transferred'")
        self.assertIn(str(self.customer.id), transfer["old_value_json"])
        self.assertIn(str(self.customer2.id), transfer["new_value_json"])

    # -- notifications (FR-060 remaining triggers) -------------------------------
    def test_new_booking_and_payment_notifications(self):
        before_manager = self.notifications.unread_count(self.manager)
        before_accountant = self.notifications.unread_count(self.accountant)
        booking = self._book()
        self.assertEqual(
            self.notifications.unread_count(self.manager), before_manager + 1)
        self._confirm(booking)
        self.assertEqual(
            self.notifications.unread_count(self.manager), before_manager + 2)
        self.assertEqual(
            self.notifications.unread_count(self.accountant),
            before_accountant + 1)

    def test_booking_cancelled_notification(self):
        booking = self._book()
        before = self.notifications.unread_count(self.manager)
        self.bookings.cancel_booking(self.manager, booking.id, "Cancel")
        self.assertEqual(
            self.notifications.unread_count(self.manager), before + 1)

    def test_hold_expiring_notification_once(self):
        booking = self._book()
        soon = datetime.now() + timedelta(hours=1)
        self.db.execute(
            "UPDATE bookings SET hold_expires_at = ? WHERE id = ?",
            (soon.isoformat(timespec="seconds"), booking.id))
        before_manager = self.notifications.unread_count(self.manager)
        before_receptionist = self.notifications.unread_count(self.receptionist)
        self.bookings.sweep_expired_holds()
        self.assertEqual(
            self.notifications.unread_count(self.manager), before_manager + 1)
        self.assertEqual(
            self.notifications.unread_count(self.receptionist),
            before_receptionist + 1)
        # A second sweep does not duplicate the warning.
        self.bookings.sweep_expired_holds()
        self.assertEqual(
            self.notifications.unread_count(self.manager), before_manager + 1)

    def test_event_approaching_notification(self):
        booking = self._book()
        self._confirm(booking)
        soon = datetime.now() + timedelta(hours=24)
        self.db.execute(
            "UPDATE bookings SET start_at = ? WHERE id = ?",
            (soon.isoformat(timespec="seconds"), booking.id))
        before = self.notifications.unread_count(self.manager)
        self.bookings.sweep_expired_holds()
        self.assertEqual(
            self.notifications.unread_count(self.manager), before + 1)
        self.bookings.sweep_expired_holds()
        self.assertEqual(
            self.notifications.unread_count(self.manager), before + 1)
