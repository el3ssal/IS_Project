"""Expenses (Phase 5, business layer — SRS §8.5, BR-24).

Fields: amount, date, category, description, method, recording
employee, scope (venue / hall / event), approval Pending/Approved/
Rejected, payment Pending/Paid — approval and payment status are kept
separate. A venue-scope expense is never also counted as an event
expense (one row, one scope). No supplier/inventory/tax module.
"""
from __future__ import annotations

from datetime import datetime

from ..config.settings import AppSettings
from ..data.database import DatabaseManager
from ..data.repositories import booking_repository as booking_repo
from ..data.repositories import catalog_repository as catalog_repo
from ..data.repositories import finance_repository as repo
from ..utils.exceptions import NotFoundError, ValidationError
from ..utils.logging_config import get_logger
from ..utils.validators import validate_date_yyyy_mm_dd
from . import audit_service
from .models import EXPENSE_CATEGORIES, PAYMENT_METHODS, Expense
from .permissions import require_permission

logger = get_logger(__name__)


def _row_to_expense(row: dict) -> Expense:
    return Expense(
        id=row["id"], amount=row["amount"], date=row["date"],
        category=row["category"], description=row["description"],
        method=row["method"], scope_type=row["scope_type"],
        scope_id=row["scope_id"], recorded_by=row["recorded_by"],
        approval_status=row["approval_status"],
        payment_status=row["payment_status"],
    )


class ExpenseService:
    def __init__(self, db: DatabaseManager, settings: AppSettings | None = None) -> None:
        self.db = db
        self.settings = settings or AppSettings.from_env()

    # -- writes ---------------------------------------------------------------
    def record_expense(self, employee, *, amount_piasters: int, date: str,
                       category: str, description: str = "",
                       method: str = "cash", scope_type: str = "venue",
                       scope_id: int | None = None) -> Expense:
        """Record an expense (starts Pending approval, Pending payment).
        Scope is validated: a hall scope needs an existing hall, an
        event scope needs an existing booking."""
        require_permission(self.db, employee, "expense.record")
        if amount_piasters <= 0:
            raise ValidationError("Expense amount must be greater than zero.")
        if category not in EXPENSE_CATEGORIES:
            raise ValidationError(
                f"Expense category must be one of {EXPENSE_CATEGORIES}.")
        if method not in PAYMENT_METHODS:
            raise ValidationError(f"Expense method must be one of {PAYMENT_METHODS}.")
        if scope_type not in ("venue", "hall", "event"):
            raise ValidationError("Expense scope must be venue, hall or event.")
        if scope_type == "venue" and scope_id is not None:
            raise ValidationError("A venue-scope expense has no scope id.")
        if scope_type == "hall":
            if scope_id is None or catalog_repo.get_hall(self.db, scope_id) is None:
                raise NotFoundError(f"Hall #{scope_id} does not exist.")
        if scope_type == "event":
            if scope_id is None or booking_repo.get_booking(self.db, scope_id) is None:
                raise NotFoundError(f"Booking #{scope_id} does not exist.")
        validate_date_yyyy_mm_dd(date, "Expense date")
        expense_id = repo.insert_expense(
            self.db, amount=amount_piasters, date=date, category=category,
            description=description or "", method=method, scope_type=scope_type,
            scope_id=scope_id, recorded_by=employee.id)
        audit_service.record(
            self.db, employee_id=employee.id, action="expense.recorded",
            entity_type="expense", entity_id=expense_id,
            new_value={"amount": amount_piasters, "category": category,
                       "scope_type": scope_type, "scope_id": scope_id},
            description=f"Expense #{expense_id} recorded: {category} "
                        f"({scope_type}).")
        logger.info("Expense #%s recorded (%s)", expense_id, category)
        row = repo.get_expense(self.db, expense_id)
        assert row is not None
        return _row_to_expense(row)

    def _get_or_raise(self, expense_id: int, conn=None) -> dict:
        row = repo.get_expense(self.db, expense_id, conn=conn)
        if row is None:
            raise NotFoundError(f"Expense #{expense_id} does not exist.")
        return row

    def approve_expense(self, employee, expense_id: int) -> Expense:
        """Pending -> Approved. Approval is separate from payment."""
        require_permission(self.db, employee, "expense.approve")
        with self.db.transaction() as conn:
            row = self._get_or_raise(expense_id, conn=conn)
            if row["approval_status"] != "pending":
                raise ValidationError(
                    f"Only a pending expense can be approved "
                    f"(current status: {row['approval_status']}).")
            repo.update_expense_approval(
                self.db, expense_id, "approved", conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="expense.approved",
                entity_type="expense", entity_id=expense_id,
                old_value={"approval_status": "pending"},
                new_value={"approval_status": "approved"},
                description=f"Expense #{expense_id} approved.",
                conn=conn)
        row = repo.get_expense(self.db, expense_id)
        assert row is not None
        return _row_to_expense(row)

    def reject_expense(self, employee, expense_id: int, reason: str = "") -> Expense:
        require_permission(self.db, employee, "expense.approve")
        with self.db.transaction() as conn:
            row = self._get_or_raise(expense_id, conn=conn)
            if row["approval_status"] != "pending":
                raise ValidationError(
                    f"Only a pending expense can be rejected "
                    f"(current status: {row['approval_status']}).")
            repo.update_expense_approval(
                self.db, expense_id, "rejected", conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="expense.rejected",
                entity_type="expense", entity_id=expense_id,
                old_value={"approval_status": "pending"},
                new_value={"approval_status": "rejected"},
                description=f"Expense #{expense_id} rejected: {reason or '—'}.",
                conn=conn)
        row = repo.get_expense(self.db, expense_id)
        assert row is not None
        return _row_to_expense(row)

    def mark_expense_paid(self, employee, expense_id: int) -> Expense:
        """Pending -> Paid, only after approval (SRS §8.5 keeps the two
        statuses separate; an unapproved expense can never be paid)."""
        require_permission(self.db, employee, "expense.record")
        with self.db.transaction() as conn:
            row = self._get_or_raise(expense_id, conn=conn)
            if row["approval_status"] != "approved":
                raise ValidationError(
                    "Only an approved expense can be marked paid "
                    f"(approval status: {row['approval_status']}).")
            if row["payment_status"] == "paid":
                raise ValidationError("Expense is already marked paid.")
            repo.update_expense_payment(
                self.db, expense_id, "paid", conn=conn)
            audit_service.record(
                self.db, employee_id=employee.id, action="expense.paid",
                entity_type="expense", entity_id=expense_id,
                old_value={"payment_status": "pending"},
                new_value={"payment_status": "paid"},
                description=f"Expense #{expense_id} marked paid.",
                conn=conn)
        row = repo.get_expense(self.db, expense_id)
        assert row is not None
        return _row_to_expense(row)

    # -- reads ---------------------------------------------------------------
    def list_expenses(self, employee, *, category: str | None = None,
                      scope_type: str | None = None,
                      scope_id: int | None = None,
                      approval_status: str | None = None,
                      payment_status: str | None = None,
                      date_from: str | None = None,
                      date_to: str | None = None) -> list[Expense]:
        require_permission(self.db, employee, "payment.view")
        return [_row_to_expense(r) for r in repo.list_expenses(
            self.db, category=category, scope_type=scope_type,
            scope_id=scope_id, approval_status=approval_status,
            payment_status=payment_status, date_from=date_from,
            date_to=date_to)]

    def totals(self, employee, *, date_from: str | None = None,
               date_to: str | None = None) -> dict:
        """Expense totals by approval/payment status and by category
        (SRS §8.5 reports). One row is counted exactly once, under its
        single scope."""
        require_permission(self.db, employee, "payment.view")
        by_category = {
            category: sum(
                r["amount"] for r in repo.list_expenses(
                    self.db, category=category, approval_status="approved",
                    date_from=date_from, date_to=date_to))
            for category in EXPENSE_CATEGORIES
        }
        return {
            "approved": repo.sum_expenses(
                self.db, approval_status="approved",
                date_from=date_from, date_to=date_to),
            "paid": repo.sum_expenses(
                self.db, approval_status="approved", payment_status="paid",
                date_from=date_from, date_to=date_to),
            "pending": repo.sum_expenses(
                self.db, approval_status="pending",
                date_from=date_from, date_to=date_to),
            "by_category_approved": by_category,
        }
