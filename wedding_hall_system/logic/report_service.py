"""Reports, dashboards and CSV export (Phase 6, SRS §13.2–§13.4).

Reports are computed from the same measures as Phase 5
(``PaymentService.financial_summary`` / ``reconcile``, cash basis,
decision A-45) so a report total and the reconciliation screen always
agree on the same fixture. Every report enforces role permissions:
operational reports need ``report.view``, the financial report needs
``finance.view``. Amounts in report rows are integer piasters; CSV
export renders them as decimal EGP numbers (``44000.00``) so the file
opens correctly in a spreadsheet.

Dashboards are role-specific (SRS §13.2): each widget is computed only
when the caller holds the permission its data needs, and only widgets
the Owner enabled for the caller's role (``dashboard.widgets`` setting)
are returned. There is no per-individual customization.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timedelta

from ..config.defaults import DEFAULT_DASHBOARD_WIDGETS, SEED_ROLES
from ..config.settings import AppSettings, format_money
from ..data.database import DatabaseManager
from ..data.repositories import booking_repository, catalog_repository
from ..data.repositories import customer_repository, employee_repository
from ..data.repositories import finance_repository as fin_repo
from ..data.repositories import operations_repository as ops_repo
from ..data.repositories import system_repository
from ..utils.exceptions import ValidationError
from ..utils.logging_config import get_logger
from ..utils.validators import validate_date_yyyy_mm_dd
from . import audit_service
from .booking_service import BookingService
from .payment_service import PaymentService
from .permissions import has_permission, require_permission

logger = get_logger(__name__)

CONFIG_KEY = "dashboard.widgets"

KNOWN_WIDGETS = {
    "revenue", "profit", "bookings", "expenses", "pending_bookings",
    "upcoming_events", "refunds", "hall_status", "staff_overview",
    "tasks_overview", "conflicts", "maintenance", "new_bookings",
    "customers", "quotations", "holds_expiring", "payments",
    "installments_due", "financial_summary", "my_tasks",
    "upcoming_tasks", "overdue_tasks",
}

_ACTIVE_BOOKING_STATUSES = ("confirmed", "in_progress", "completed")
_WIDGET_PERMISSION: dict[str, str | tuple[str, ...]] = {
    "revenue": "finance.view",
    "profit": "finance.view",
    "expenses": ("expense.record", "expense.approve", "finance.view"),
    "financial_summary": "finance.view",
    "bookings": "booking.view",
    "pending_bookings": "booking.view",
    "upcoming_events": "booking.view",
    "new_bookings": "booking.view",
    "holds_expiring": "booking.view",
    "hall_status": "hall.view",
    "maintenance": "hall.view",
    "staff_overview": "task.view",
    "tasks_overview": "task.view",
    "conflicts": "task.view",
    "customers": "customer.view",
    "quotations": "quotation.view",
    "payments": "payment.view",
    "installments_due": "payment.view",
    "refunds": ("refund.request", "refund.approve"),
    "my_tasks": "task.view_assigned",
    "upcoming_tasks": "task.view_assigned",
    "overdue_tasks": "task.view_assigned",
}


def to_csv(headers: list[str], rows: list[list]) -> str:
    """Render a report as CSV text (stdlib csv module; the mandatory
    academic export, SRS §13.4). Amounts arrive already formatted by
    the caller (decimal EGP numbers for money columns)."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue()


def _egp(piasters: int) -> str:
    """CSV-friendly money: 4400000 piasters -> '44000.00'."""
    return f"{piasters / 100:.2f}"


def _parse_day(value: str) -> date:
    return date.fromisoformat(validate_date_yyyy_mm_dd(value, "Date"))


def _range_bounds(date_from: str | None, date_to: str | None) -> tuple[str, str]:
    """Validate a report window and return (from_iso, to_exclusive_iso)
    for the booking repository's half-open start_at filter."""
    if date_from:
        _parse_day(date_from)
    if date_to:
        _parse_day(date_to)
    if date_from and date_to and date_to < date_from:
        raise ValidationError("Report end date must not be before its start date.")
    start = date_from or "0001-01-01"
    end = (date.fromisoformat(date_to) + timedelta(days=1)).isoformat() \
        if date_to else "9999-12-31"
    return start, end


def _previous_bounds(date_from: str, date_to: str) -> tuple[str, str]:
    """The equal-length period immediately before [date_from, date_to]
    (period comparison, SRS §13.4)."""
    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    length = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length - 1)
    return prev_start.isoformat(), prev_end.isoformat()


def _fmt_remaining(delta: timedelta) -> str:
    if delta.total_seconds() <= 0:
        return "expired"
    hours = int(delta.total_seconds() // 3600)
    minutes = int((delta.total_seconds() % 3600) // 60)
    return f"{hours}h {minutes}m left"


class ReportService:
    """Operational + financial reports, role dashboards, CSV export."""

    def __init__(self, db: DatabaseManager, settings: AppSettings | None = None) -> None:
        self.db = db
        self.settings = settings or AppSettings.from_env()
        self._payments = PaymentService(db, settings)
        self._bookings = BookingService(db, settings)

    # -- dashboard widget configuration (Owner) ------------------------------
    def get_dashboard_config(self, employee) -> dict:
        require_permission(self.db, employee, "settings.view")
        return self._dashboard_config()

    def _dashboard_config(self) -> dict:
        raw = system_repository.get_setting(self.db, CONFIG_KEY, "")
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except ValueError:
                logger.warning("dashboard.widgets is not valid JSON; using defaults.")
        return json.loads(json.dumps(DEFAULT_DASHBOARD_WIDGETS))

    def update_dashboard_widgets(self, employee, config: dict) -> dict:
        """Owner controls widget availability by role (SRS §13.2)."""
        require_permission(self.db, employee, "settings.manage")
        if not isinstance(config, dict):
            raise ValidationError("Dashboard widget config must be an object.")
        known_roles = {r["name"] for r in SEED_ROLES}
        for role, widgets in config.items():
            if role not in known_roles:
                raise ValidationError(f"Unknown role in dashboard config: {role!r}.")
            if not isinstance(widgets, list):
                raise ValidationError(f"Widgets for {role!r} must be a list.")
            unknown = sorted(set(widgets) - KNOWN_WIDGETS)
            if unknown:
                raise ValidationError(
                    f"Unknown dashboard widgets for {role!r}: {', '.join(unknown)}.")
        system_repository.set_setting(
            self.db, CONFIG_KEY, json.dumps(config, ensure_ascii=False))
        audit_service.record(
            self.db, employee_id=employee.id, action="settings.dashboard_widgets",
            entity_type="system_setting", entity_id=None,
            new_value={"roles": sorted(config)},
            description="Dashboard widget availability updated.")
        return config

    # -- dashboards ------------------------------------------------------------
    def dashboard(self, employee, *, now: datetime | None = None) -> dict:
        """Role dashboard (SRS §13.2): only the widgets the Owner
        enabled for the caller's roles, and only the ones the caller
        has permission to see. Staff get their own tasks only."""
        now = now or datetime.now()
        config = self._dashboard_config()
        roles = list(getattr(employee, "roles", []) or [])
        wanted: list[str] = []
        for role in roles:
            for widget in config.get(role, []):
                if widget not in wanted:
                    wanted.append(widget)
        if not wanted:  # employee with no listed role sees nothing
            return {"roles": roles, "widgets": {}}
        widgets: dict[str, dict] = {}
        for key in wanted:
            perm = _WIDGET_PERMISSION.get(key)
            if perm is None:
                continue
            codes = (perm,) if isinstance(perm, str) else perm
            if not any(has_permission(self.db, employee, c) for c in codes):
                continue
            builder = getattr(self, f"_widget_{key}", None)
            if builder is None:
                continue
            widgets[key] = builder(employee, now)
        return {"roles": roles, "widgets": widgets}

    def _widget_revenue(self, employee, now: datetime) -> dict:
        summary = self._payments.financial_summary(employee)
        return {"recognised_revenue": summary["recognised_revenue"],
                "collected": summary["collected"],
                "display": format_money(summary["recognised_revenue"],
                                        self.settings.currency)}

    def _widget_profit(self, employee, now: datetime) -> dict:
        summary = self._payments.financial_summary(employee)
        return {"profit": summary["profit"],
                "expenses_approved": summary["expenses_approved"],
                "display": format_money(summary["profit"],
                                        self.settings.currency)}

    def _widget_expenses(self, employee, now: datetime) -> dict:
        approved = fin_repo.sum_expenses(self.db, approval_status="approved")
        pending = fin_repo.sum_expenses(self.db, approval_status="pending")
        return {"approved": approved, "pending_approval": pending,
                "display": format_money(approved, self.settings.currency)}

    def _widget_financial_summary(self, employee, now: datetime) -> dict:
        summary = self._payments.financial_summary(employee)
        return {"contract_value": summary["contract_value"],
                "collected": summary["collected"],
                "refunds_paid": summary["refunds_paid"],
                "outstanding": summary["outstanding"],
                "profit": summary["profit"],
                "basis": summary["basis"]}

    def _widget_bookings(self, employee, now: datetime) -> dict:
        rows = booking_repository.list_bookings(self.db)
        by_status: dict[str, int] = {}
        for row in rows:
            by_status[row["status"]] = by_status.get(row["status"], 0) + 1
        return {"total": len(rows), "by_status": by_status}

    def _widget_pending_bookings(self, employee, now: datetime) -> dict:
        pending = self.pending_bookings(employee, now=now)["rows"]
        return {"count": len(pending),
                "expiring_soon": sum(1 for r in pending if r["expiring_soon"]),
                "missing_deposit_total": sum(r["missing_deposit"] for r in pending)}

    def _widget_upcoming_events(self, employee, now: datetime) -> dict:
        end = now + timedelta(days=7)
        rows = booking_repository.list_bookings(
            self.db, status="confirmed",
            start_from=now.isoformat(timespec="seconds"),
            start_to=end.isoformat(timespec="seconds"))
        rows += booking_repository.list_bookings(
            self.db, status="in_progress",
            start_from=now.isoformat(timespec="seconds"),
            start_to=end.isoformat(timespec="seconds"))
        return {"count": len(rows),
                "events": [{"number": r["number"], "start_at": r["start_at"],
                            "hall_id": r["hall_id"]} for r in rows]}

    def _widget_new_bookings(self, employee, now: datetime) -> dict:
        week_ago = (now - timedelta(days=7)).isoformat(timespec="seconds")
        rows = [r for r in booking_repository.list_bookings(self.db)
                if str(r.get("created_at", "")) >= week_ago]
        return {"count": len(rows),
                "numbers": [r["number"] for r in rows]}

    def _widget_customers(self, employee, now: datetime) -> dict:
        rows = customer_repository.list_customers(self.db)
        return {"count": len(rows)}

    def _widget_quotations(self, employee, now: datetime) -> dict:
        rows = self.db.query_all("SELECT status, COUNT(*) AS n FROM quotations "
                                 "GROUP BY status")
        return {"by_status": {r["status"]: int(r["n"]) for r in rows}}

    def _widget_holds_expiring(self, employee, now: datetime) -> dict:
        pending = self.pending_bookings(employee, now=now)["rows"]
        soon = [r for r in pending if r["expiring_soon"]]
        return {"count": len(soon),
                "bookings": [{"number": r["number"],
                              "hold_remaining": r["hold_remaining"]}
                             for r in soon]}

    def _widget_hall_status(self, employee, now: datetime) -> dict:
        halls = catalog_repository.list_halls(self.db)
        today = now.date().isoformat()
        out = []
        for hall in halls:
            active = booking_repository.list_bookings(
                self.db, hall_id=int(hall["id"]),
                start_from=today, start_to=(now.date() + timedelta(days=1)).isoformat())
            active = [b for b in active
                      if b["status"] in _ACTIVE_BOOKING_STATUSES]
            maintenance = [m for m in catalog_repository.list_maintenance(
                self.db, hall_id=int(hall["id"]))
                if str(m["end_at"])[:10] >= today]
            out.append({"hall_id": int(hall["id"]), "name": hall["name"],
                        "status": hall["status"],
                        "events_today": len(active),
                        "upcoming_maintenance": len(maintenance)})
        return {"halls": out}

    def _widget_maintenance(self, employee, now: datetime) -> dict:
        rows = [m for m in catalog_repository.list_maintenance(self.db)
                if str(m["end_at"])[:10] >= now.date().isoformat()]
        return {"count": len(rows),
                "blocks": [{"hall_id": m["hall_id"], "type": m["type"],
                            "start_at": m["start_at"], "end_at": m["end_at"]}
                           for m in rows]}

    def _widget_staff_overview(self, employee, now: datetime) -> dict:
        staff = employee_repository.list_employees(self.db)
        active = sum(1 for s in staff if s["is_active"])
        tasks = ops_repo.list_tasks(self.db)
        per_employee: dict[int, int] = {}
        for task in tasks:
            if task["assigned_to"] is not None and task["status"] != "completed":
                per_employee[int(task["assigned_to"])] = \
                    per_employee.get(int(task["assigned_to"]), 0) + 1
        return {"employees": len(staff), "active": active,
                "open_tasks_by_employee": per_employee}

    def _widget_tasks_overview(self, employee, now: datetime) -> dict:
        rows = ops_repo.list_tasks(self.db)
        by_status: dict[str, int] = {}
        for row in rows:
            by_status[row["status"]] = by_status.get(row["status"], 0) + 1
        overdue = sum(1 for r in rows
                      if r["status"] != "completed" and r["due_at"]
                      and datetime.fromisoformat(r["due_at"]) < now)
        return {"total": len(rows), "by_status": by_status,
                "overdue": overdue}

    def _widget_conflicts(self, employee, now: datetime) -> dict:
        """Staff with two or more incomplete tasks due within 30 minutes
        of each other (the Phase 4 warn-only conflict window, SRS §7.4)."""
        rows = [r for r in ops_repo.list_tasks(self.db)
                if r["status"] != "completed" and r["due_at"]
                and r["assigned_to"] is not None]
        by_employee: dict[int, list[dict]] = {}
        for row in rows:
            by_employee.setdefault(int(row["assigned_to"]), []).append(row)
        conflicts = []
        for emp_id, tasks in by_employee.items():
            due = sorted(datetime.fromisoformat(t["due_at"]) for t in tasks)
            clash = any((due[i + 1] - due[i]) <= timedelta(minutes=30)
                        for i in range(len(due) - 1))
            if clash:
                emp = employee_repository.get_by_id(self.db, emp_id)
                conflicts.append({
                    "employee_id": emp_id,
                    "employee_name": emp["full_name"] if emp else "",
                    "open_tasks": len(tasks)})
        return {"count": len(conflicts), "conflicts": conflicts}

    def _widget_payments(self, employee, now: datetime) -> dict:
        rows = self.db.query_all(
            "SELECT * FROM payments WHERE status = 'recorded' "
            "ORDER BY confirmed_at DESC LIMIT 10")
        collected = fin_repo.sum_recorded_payments_in_range(self.db)
        return {"recent_count": len(rows),
                "recent": [{"number": r["number"], "method": r["method"],
                            "amount": int(r["amount"])} for r in rows],
                "collected_total": collected}

    def _widget_installments_due(self, employee, now: datetime) -> dict:
        overdue = fin_repo.list_overdue_installments(
            self.db, now.isoformat(timespec="seconds"))
        return {"overdue_count": len(overdue),
                "overdue": [{"id": int(r["id"]), "booking_id": int(r["booking_id"]),
                             "amount": int(r["amount"]),
                             "due_date": r["due_date"]} for r in overdue]}

    def _widget_refunds(self, employee, now: datetime) -> dict:
        rows = fin_repo.list_refunds(self.db, status="pending_approval")
        return {"pending_approval": len(rows),
                "pending_amount": sum(int(r["amount"]) for r in rows)}

    def _widget_my_tasks(self, employee, now: datetime) -> dict:
        rows = ops_repo.list_tasks(self.db, assigned_to=int(employee.id))
        return {"count": len(rows),
                "by_status": _count_by(rows, "status")}

    def _widget_upcoming_tasks(self, employee, now: datetime) -> dict:
        rows = [r for r in ops_repo.list_tasks(self.db, assigned_to=int(employee.id))
                if r["status"] != "completed" and r["due_at"]
                and now <= datetime.fromisoformat(r["due_at"])
                <= now + timedelta(days=7)]
        return {"count": len(rows),
                "tasks": [{"id": int(r["id"]), "name": r["name"],
                           "due_at": r["due_at"]} for r in rows]}

    def _widget_overdue_tasks(self, employee, now: datetime) -> dict:
        rows = [r for r in ops_repo.list_tasks(self.db, assigned_to=int(employee.id))
                if r["status"] != "completed" and r["due_at"]
                and datetime.fromisoformat(r["due_at"]) < now]
        return {"count": len(rows),
                "tasks": [{"id": int(r["id"]), "name": r["name"],
                           "due_at": r["due_at"]} for r in rows]}

    # -- pending bookings dashboard (SRS §13.3) -------------------------------
    def pending_bookings(self, employee, *, hall_id: int | None = None,
                         expiring_within_hours: int | None = None,
                         now: datetime | None = None) -> dict:
        """Pending bookings with time remaining on the hold and the
        missing deposit (SRS §13.3). Overdue holds are expired lazily
        first (A-22), so the list is never stale."""
        require_permission(self.db, employee, "booking.view")
        now = now or datetime.now()
        self._bookings.sweep_expired_holds(now=now)
        rows = booking_repository.list_bookings(self.db, status="pending",
                                                hall_id=hall_id)
        out = []
        for row in rows:
            hold_expires = (datetime.fromisoformat(row["hold_expires_at"])
                            if row.get("hold_expires_at") else None)
            remaining = (hold_expires - now) if hold_expires else None
            missing = max(0, int(row["deposit_required"]) - int(row["deposit_paid"]))
            expiring_soon = remaining is not None and remaining <= timedelta(hours=6)
            if (expiring_within_hours is not None and remaining is not None
                    and remaining > timedelta(hours=expiring_within_hours)):
                continue
            warnings = []
            if expiring_soon:
                warnings.append("hold expiring soon")
            if missing > 0:
                warnings.append("deposit outstanding")
            customer = customer_repository.get_customer(
                self.db, int(row["customer_id"]))
            out.append({
                "booking_id": int(row["id"]), "number": row["number"],
                "customer": customer["full_name"] if customer else "",
                "hall_id": int(row["hall_id"]), "start_at": row["start_at"],
                "hold_expires_at": row["hold_expires_at"] or "",
                "hold_remaining": _fmt_remaining(remaining) if remaining else "—",
                "deposit_required": int(row["deposit_required"]),
                "deposit_paid": int(row["deposit_paid"]),
                "missing_deposit": missing,
                "expiring_soon": expiring_soon,
                "warnings": warnings,
            })
        return {"rows": out, "count": len(out)}

    # -- reports (SRS §13.4) -----------------------------------------------------
    def booking_report(self, employee, *, date_from: str | None = None,
                       date_to: str | None = None) -> dict:
        """Bookings in the window with money columns (FR-066). The
        basis: event date is the booking's start date; amounts are the
        frozen booking totals (Phase 3), collected is recorded payments
        net of paid refunds (Phase 5)."""
        require_permission(self.db, employee, "report.view")
        start, end = _range_bounds(date_from, date_to)
        rows = booking_repository.list_bookings(
            self.db, start_from=start, start_to=end)
        data = []
        for row in rows:
            collected = booking_repository.sum_recorded_payments(
                self.db, int(row["id"]))
            refunds = fin_repo.sum_refunds_for_booking(
                self.db, int(row["id"]), statuses=("paid",))
            customer = customer_repository.get_customer(
                self.db, int(row["customer_id"]))
            hall = catalog_repository.get_hall(self.db, int(row["hall_id"]))
            data.append({
                "number": row["number"],
                "customer": customer["full_name"] if customer else "",
                "hall": hall["name"] if hall else "",
                "start_at": row["start_at"], "status": row["status"],
                "total_amount": int(row["total_amount"]),
                "collected": collected,
                "refunds_paid": refunds,
                "outstanding": max(0, int(row["total_amount"]) - collected + refunds),
            })
        totals = {
            "bookings": len(data),
            "contract_value": sum(r["total_amount"] for r in data),
            "collected": sum(r["collected"] for r in data),
            "refunds_paid": sum(r["refunds_paid"] for r in data),
            "outstanding": sum(r["outstanding"] for r in data),
        }
        comparison = None
        if date_from and date_to:
            prev_from, prev_to = _previous_bounds(date_from, date_to)
            prev = booking_repository.list_bookings(
                self.db, start_from=prev_from,
                start_to=(date.fromisoformat(prev_to) + timedelta(days=1)).isoformat())
            comparison = {
                "period": f"{prev_from} to {prev_to}",
                "bookings": len(prev),
                "contract_value": sum(int(r["total_amount"]) for r in prev),
            }
        return {"columns": ["Booking", "Customer", "Hall", "Start",
                            "Status", "Total (EGP)", "Collected (EGP)",
                            "Refunds paid (EGP)", "Outstanding (EGP)"],
                "rows": [[r["number"], r["customer"], r["hall"], r["start_at"],
                          r["status"], _egp(r["total_amount"]),
                          _egp(r["collected"]), _egp(r["refunds_paid"]),
                          _egp(r["outstanding"])] for r in data],
                "totals": totals, "comparison": comparison,
                "basis": ("Event date = booking start date. Total = frozen "
                          "booking total; collected = recorded payments; "
                          "outstanding = total − collected + refunds paid.")}

    def hall_utilization(self, employee, *, date_from: str | None = None,
                         date_to: str | None = None) -> dict:
        """Per-hall utilization (FR-066). Available hours come from the
        hall's weekly opening schedule over the window; booked hours are
        the overlap of active bookings with the window. Exceptional
        closures are not subtracted — the basis states this, so the
        figure is an upper bound on true availability."""
        require_permission(self.db, employee, "report.view")
        start, end = _range_bounds(date_from, date_to)
        first = date.fromisoformat(start) if date_from else date(2026, 1, 1)
        last = (date.fromisoformat(end) - timedelta(days=1)) if date_to \
            else date.today()
        if last < first:
            first, last = last, first
        range_start = datetime.combine(first, datetime.min.time())
        range_end = datetime.combine(last + timedelta(days=1), datetime.min.time())
        rows = []
        for hall in catalog_repository.list_halls(self.db):
            hours = catalog_repository.list_weekly_hours(self.db, int(hall["id"]))
            by_weekday = {int(h["weekday"]): (h["open_time"], h["close_time"])
                          for h in hours}
            available_min = 0
            day = first
            while day <= last:
                window = by_weekday.get(day.weekday())
                if window:
                    open_t = datetime.combine(day, datetime.strptime(
                        window[0], "%H:%M").time())
                    close_t = datetime.combine(day, datetime.strptime(
                        window[1], "%H:%M").time())
                    available_min += int((close_t - open_t).total_seconds() // 60)
                day += timedelta(days=1)
            bookings = [b for b in booking_repository.list_bookings(
                self.db, hall_id=int(hall["id"]),
                start_from=start, start_to=end)
                if b["status"] in _ACTIVE_BOOKING_STATUSES]
            booked_min = 0
            for booking in bookings:
                b_start = datetime.fromisoformat(booking["start_at"])
                b_end = datetime.fromisoformat(booking["end_at"])
                overlap = (min(b_end, range_end) - max(b_start, range_start))
                booked_min += max(0, int(overlap.total_seconds() // 60))
            utilization = (round(100 * booked_min / available_min, 1)
                           if available_min else 0.0)
            rows.append({
                "hall": hall["name"], "events": len(bookings),
                "booked_hours": round(booked_min / 60, 1),
                "available_hours": round(available_min / 60, 1),
                "utilization_percent": utilization,
            })
        return {"columns": ["Hall", "Events", "Booked hours",
                            "Available hours", "Utilization %"],
                "rows": [[r["hall"], r["events"], r["booked_hours"],
                          r["available_hours"], r["utilization_percent"]]
                         for r in rows],
                "totals": {"events": sum(r["events"] for r in rows)},
                "comparison": None,
                "basis": ("Available hours from the weekly opening schedule "
                          "over the window; exceptional closures are not "
                          "subtracted. Booked hours are active bookings "
                          "clipped to the window.")}

    def customer_report(self, employee, *, date_from: str | None = None,
                        date_to: str | None = None) -> dict:
        require_permission(self.db, employee, "report.view")
        start, end = _range_bounds(date_from, date_to)
        bookings = booking_repository.list_bookings(
            self.db, start_from=start, start_to=end)
        per_customer: dict[int, dict] = {}
        for row in bookings:
            entry = per_customer.setdefault(int(row["customer_id"]), {
                "bookings": 0, "total": 0, "collected": 0})
            entry["bookings"] += 1
            entry["total"] += int(row["total_amount"])
            entry["collected"] += booking_repository.sum_recorded_payments(
                self.db, int(row["id"]))
        rows = []
        for customer_id, agg in per_customer.items():
            customer = customer_repository.get_customer(self.db, customer_id)
            rows.append({
                "customer": customer["full_name"] if customer else "",
                "phone": customer["phone"] if customer else "",
                "bookings": agg["bookings"], "total": agg["total"],
                "collected": agg["collected"],
                "outstanding": max(0, agg["total"] - agg["collected"]),
            })
        rows.sort(key=lambda r: r["total"], reverse=True)
        return {"columns": ["Customer", "Phone", "Bookings", "Total (EGP)",
                            "Collected (EGP)", "Outstanding (EGP)"],
                "rows": [[r["customer"], r["phone"], r["bookings"],
                          _egp(r["total"]), _egp(r["collected"]),
                          _egp(r["outstanding"])] for r in rows],
                "totals": {"customers": len(rows),
                           "total": sum(r["total"] for r in rows),
                           "collected": sum(r["collected"] for r in rows)},
                "comparison": None,
                "basis": "Event date = booking start date; one row per customer."}

    def package_report(self, employee, *, date_from: str | None = None,
                       date_to: str | None = None) -> dict:
        require_permission(self.db, employee, "report.view")
        start, end = _range_bounds(date_from, date_to)
        bookings = booking_repository.list_bookings(
            self.db, start_from=start, start_to=end)
        per_package: dict[str, dict] = {}
        for row in bookings:
            name = "No package"
            if row.get("package_id"):
                package = catalog_repository.get_package(
                    self.db, int(row["package_id"]))
                name = package["name"] if package else "No package"
            entry = per_package.setdefault(name, {"bookings": 0, "total": 0})
            entry["bookings"] += 1
            entry["total"] += int(row["total_amount"])
        rows = [{"package": name, **agg}
                for name, agg in sorted(per_package.items(),
                                        key=lambda kv: kv[1]["total"],
                                        reverse=True)]
        return {"columns": ["Package", "Bookings", "Total (EGP)"],
                "rows": [[r["package"], r["bookings"], _egp(r["total"])]
                         for r in rows],
                "totals": {"bookings": sum(r["bookings"] for r in rows),
                           "total": sum(r["total"] for r in rows)},
                "comparison": None,
                "basis": "Event date = booking start date; bookings without a package are grouped as 'No package'."}

    def staff_report(self, employee, *, date_from: str | None = None,
                     date_to: str | None = None) -> dict:
        """Staff performance (FR-066): tasks assigned/completed/overdue
        per employee. The window filters by task due date."""
        require_permission(self.db, employee, "report.view")
        start, end = _range_bounds(date_from, date_to)
        tasks = ops_repo.list_tasks(self.db)
        if date_from or date_to:
            tasks = [t for t in tasks
                     if t["due_at"] and start <= t["due_at"][:10] < end]
        per_employee: dict[int, dict] = {}
        for task in tasks:
            if task["assigned_to"] is None:
                continue
            entry = per_employee.setdefault(int(task["assigned_to"]), {
                "assigned": 0, "completed": 0, "overdue": 0})
            entry["assigned"] += 1
            if task["status"] == "completed":
                entry["completed"] += 1
            elif task["due_at"] and datetime.fromisoformat(task["due_at"]) < datetime.now():
                entry["overdue"] += 1
        rows = []
        for emp_id, agg in per_employee.items():
            emp = employee_repository.get_by_id(self.db, emp_id)
            rows.append({"employee": emp["full_name"] if emp else "",
                         **agg})
        rows.sort(key=lambda r: r["completed"], reverse=True)
        return {"columns": ["Employee", "Assigned", "Completed", "Overdue"],
                "rows": [[r["employee"], r["assigned"], r["completed"],
                          r["overdue"]] for r in rows],
                "totals": {"assigned": sum(r["assigned"] for r in rows),
                           "completed": sum(r["completed"] for r in rows),
                           "overdue": sum(r["overdue"] for r in rows)},
                "comparison": None,
                "basis": ("One row per employee with tasks in the window "
                          "(window filters by task due date); overdue = "
                          "not completed and past its due date.")}

    def event_report(self, employee, *, date_from: str | None = None,
                     date_to: str | None = None) -> dict:
        require_permission(self.db, employee, "report.view")
        start, end = _range_bounds(date_from, date_to)
        bookings = booking_repository.list_bookings(
            self.db, start_from=start, start_to=end)
        rows = []
        for row in bookings:
            hall = catalog_repository.get_hall(self.db, int(row["hall_id"]))
            event_type = ""
            if row.get("event_type_id"):
                type_row = self.db.query_one(
                    "SELECT name FROM event_types WHERE id = ?",
                    (int(row["event_type_id"]),))
                event_type = type_row["name"] if type_row else ""
            customer = customer_repository.get_customer(
                self.db, int(row["customer_id"]))
            rows.append({
                "number": row["number"],
                "customer": customer["full_name"] if customer else "",
                "hall": hall["name"] if hall else "",
                "event_type": event_type, "start_at": row["start_at"],
                "guests": int(row["guest_count"]), "status": row["status"],
            })
        return {"columns": ["Booking", "Customer", "Hall", "Event type",
                            "Start", "Guests", "Status"],
                "rows": [[r["number"], r["customer"], r["hall"],
                          r["event_type"], r["start_at"], r["guests"],
                          r["status"]] for r in rows],
                "totals": {"events": len(rows)},
                "comparison": None,
                "basis": "Event date = booking start date."}

    def cancellation_report(self, employee, *, date_from: str | None = None,
                            date_to: str | None = None) -> dict:
        require_permission(self.db, employee, "report.view")
        start, end = _range_bounds(date_from, date_to)
        bookings = booking_repository.list_bookings(
            self.db, status="cancelled", start_from=start, start_to=end)
        bookings += booking_repository.list_bookings(
            self.db, status="no_show", start_from=start, start_to=end)
        rows = []
        for row in bookings:
            record = fin_repo.get_cancellation_record(self.db, int(row["id"]))
            no_show = fin_repo.get_no_show_record(self.db, int(row["id"]))
            fee = (int(record["fee_amount"]) if record
                   else int(no_show["fee_amount"]) if no_show else 0)
            refundable = (int(record["refundable_amount"]) if record
                          else int(no_show["refundable_amount"]) if no_show else 0)
            customer = customer_repository.get_customer(
                self.db, int(row["customer_id"]))
            rows.append({
                "number": row["number"],
                "customer": customer["full_name"] if customer else "",
                "status": row["status"], "start_at": row["start_at"],
                "total": int(row["total_amount"]), "fee": fee,
                "refundable": refundable,
            })
        return {"columns": ["Booking", "Customer", "Status", "Event date",
                            "Total (EGP)", "Fee (EGP)", "Refundable (EGP)"],
                "rows": [[r["number"], r["customer"], r["status"],
                          r["start_at"][:10], _egp(r["total"]), _egp(r["fee"]),
                          _egp(r["refundable"])] for r in rows],
                "totals": {"cancelled": len(rows),
                           "fees": sum(r["fee"] for r in rows),
                           "refundable": sum(r["refundable"] for r in rows)},
                "comparison": None,
                "basis": ("Cancelled + no-show bookings by event date; fee "
                          "and refundable come from the confirmation-time "
                          "policy snapshot (A-43/A-44).")}

    def financial_report(self, employee, *, date_from: str | None = None,
                         date_to: str | None = None) -> dict:
        """Financial report (FR-066): the Phase 5 reconciliation figures
        (FR-050) as a report, so the report and the Finance screen
        always agree. Needs ``finance.view`` (Accountant/Owner)."""
        require_permission(self.db, employee, "finance.view")
        summary = self._payments.financial_summary(
            employee, date_from=date_from, date_to=date_to)
        recon = self._payments.reconcile(employee)
        rows = [[r["number"], r["status"], _egp(r["total_amount"]),
                 _egp(r["collected"]), _egp(r["refunds_paid"]),
                 _egp(r["net_collected"]), _egp(r["outstanding"]),
                 "; ".join(r["flags"]) or "—"]
                for r in recon["bookings"]]
        return {"columns": ["Booking", "Status", "Total (EGP)",
                            "Collected (EGP)", "Refunds paid (EGP)",
                            "Net collected (EGP)", "Outstanding (EGP)",
                            "Flags"],
                "rows": rows,
                "totals": summary,
                "comparison": None,
                "basis": summary["basis"]}

    # -- CSV export -------------------------------------------------------------
    _REPORTS = {
        "bookings": booking_report,
        "hall_utilization": hall_utilization,
        "customers": customer_report,
        "packages": package_report,
        "staff": staff_report,
        "events": event_report,
        "cancellations": cancellation_report,
        "financial": financial_report,
    }

    def export_csv(self, employee, report_name: str, *,
                   date_from: str | None = None,
                   date_to: str | None = None) -> str:
        """CSV export for any report (mandatory export, SRS §13.4).
        Permission is enforced by the report method itself."""
        method = self._REPORTS.get(report_name)
        if method is None:
            raise ValidationError(
                f"Unknown report {report_name!r}. Available: "
                f"{', '.join(sorted(self._REPORTS))}.")
        report = method(self, employee, date_from=date_from, date_to=date_to)
        return to_csv(report["columns"], report["rows"])


def _count_by(rows: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        out[row[key]] = out.get(row[key], 0) + 1
    return out
