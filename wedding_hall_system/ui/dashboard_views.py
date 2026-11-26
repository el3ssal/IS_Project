"""Dashboard screens (Phase 6, presentation tier, SRS §13.2–§13.3).

Thin UI only: parse input, call ONE service method, render the result.
No SQL and no business rules here. Every handler resolves the session
token to an employee and lets the service enforce permissions.

All rendered content is language-aware: handlers take the session
language state and render through ``ui/i18n.py`` (Arabic RTL by
default). The builder returns its label targets so the main view's
language toggle can rename the inputs, and a ``render`` function the
toggle calls to re-render the dashboard in the new language.
"""
from __future__ import annotations

import gradio as gr

from ..config.settings import AppSettings, format_money
from ..data.database import DatabaseManager
from ..logic.auth_service import AuthService
from ..logic.report_service import ReportService
from ..utils.exceptions import ApplicationError
from ..utils.logging_config import get_logger
from .i18n import WIDGET_TITLES, dir_wrap, t

logger = get_logger(__name__)


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "_No records yet._"
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def _render_dashboard(reports: ReportService, employee, lang: str) -> str:
    data = reports.dashboard(employee)
    lines = [f"## {t(lang, 'dashboard.title')}"]
    lines.append(f"**{t(lang, 'login.roles')}:** "
                 f"{', '.join(data['roles']) or '—'}")
    if not data["widgets"]:
        lines.append(f"_{t(lang, 'common.no_records')}_")
        return dir_wrap(lang, "\n\n".join(lines))
    for key, widget in data["widgets"].items():
        title = t(lang, WIDGET_TITLES.get(key, key))
        lines.append(f"### {title}")
        lines.append(_render_widget(key, widget, lang, reports))
    return dir_wrap(lang, "\n\n".join(lines))


def _render_widget(key: str, widget: dict, lang: str,
                   reports: ReportService) -> str:
    if key in ("revenue", "profit"):
        return f"**{widget['display']}**"
    if key == "expenses":
        return (f"{t(lang, 'dashboard.expenses')}: "
                f"**{format_money(widget['approved'], reports.settings.currency)}**  \n"
                f"Pending approval: "
                f"{format_money(widget['pending_approval'], reports.settings.currency)}")
    if key == "financial_summary":
        return _md_table(
            ["", "EGP"],
            [[t(lang, "dashboard.revenue"), format_money(widget["collected"], reports.settings.currency)],
             [t(lang, "dashboard.profit"), format_money(widget["profit"], reports.settings.currency)],
             ["Outstanding", format_money(widget["outstanding"], reports.settings.currency)],
             [t(lang, "dashboard.refunds"), format_money(widget["refunds_paid"], reports.settings.currency)]])
    if key == "bookings":
        rows = [[s, n] for s, n in sorted(widget["by_status"].items())]
        return _md_table(["Status", "Count"], rows) + f"\n\nTotal: **{widget['total']}**"
    if key == "pending_bookings":
        return (f"**{widget['count']}** pending  •  "
                f"{widget['expiring_soon']} expiring soon  •  "
                f"missing deposit total: "
                f"{format_money(widget['missing_deposit_total'], reports.settings.currency)}")
    if key == "upcoming_events":
        rows = [[e["number"], e["start_at"], e["hall_id"]]
                for e in widget["events"]]
        return _md_table(["Booking", "Start", "Hall"], rows)
    if key == "new_bookings":
        return f"**{widget['count']}** — {', '.join(widget['numbers']) or '—'}"
    if key == "customers":
        return f"**{widget['count']}**"
    if key == "quotations":
        rows = [[s, n] for s, n in sorted(widget["by_status"].items())]
        return _md_table(["Status", "Count"], rows)
    if key == "holds_expiring":
        rows = [[b["number"], b["hold_remaining"]] for b in widget["bookings"]]
        return _md_table(["Booking", "Hold remaining"], rows)
    if key == "hall_status":
        rows = [[h["name"], h["status"], h["events_today"],
                 h["upcoming_maintenance"]] for h in widget["halls"]]
        return _md_table(["Hall", "Status", "Events today", "Maintenance"], rows)
    if key == "maintenance":
        rows = [[b["hall_id"], b["type"], b["start_at"], b["end_at"]]
                for b in widget["blocks"]]
        return _md_table(["Hall", "Type", "From", "To"], rows)
    if key == "staff_overview":
        rows = [[e, n] for e, n in
                sorted(widget["open_tasks_by_employee"].items())]
        return (f"Employees: **{widget['employees']}** "
                f"({widget['active']} active)\n\n"
                + _md_table(["Employee ID", "Open tasks"], rows))
    if key == "tasks_overview":
        rows = [[s, n] for s, n in sorted(widget["by_status"].items())]
        return (_md_table(["Status", "Count"], rows)
                + f"\n\nOverdue: **{widget['overdue']}**")
    if key == "conflicts":
        rows = [[c["employee_name"], c["open_tasks"]]
                for c in widget["conflicts"]]
        return _md_table(["Employee", "Open tasks"], rows)
    if key == "payments":
        rows = [[p["number"], p["method"],
                 format_money(p["amount"], reports.settings.currency)]
                for p in widget["recent"]]
        return (_md_table(["Payment", "Method", "Amount"], rows)
                + f"\n\nCollected total: "
                  f"**{format_money(widget['collected_total'], reports.settings.currency)}**")
    if key == "installments_due":
        rows = [[i["id"], i["booking_id"],
                 format_money(i["amount"], reports.settings.currency),
                 i["due_date"]] for i in widget["overdue"]]
        return _md_table(["Installment", "Booking", "Amount", "Due"], rows)
    if key == "refunds":
        return (f"Pending approval: **{widget['pending_approval']}**  \n"
                f"Amount: {format_money(widget['pending_amount'], reports.settings.currency)}")
    if key in ("my_tasks", "upcoming_tasks", "overdue_tasks"):
        if key == "my_tasks":
            rows = [[s, n] for s, n in sorted(widget["by_status"].items())]
            return _md_table(["Status", "Count"], rows)
        rows = [[task["id"], task["name"], task["due_at"]]
                for task in widget["tasks"]]
        return _md_table(["Task", "Name", "Due"], rows)
    return str(widget)


def _render_pending(reports: ReportService, employee, lang: str) -> str:
    data = reports.pending_bookings(employee)
    rows = [[r["number"], r["customer"], r["hall_id"], r["start_at"],
             r["hold_remaining"],
             format_money(r["missing_deposit"], reports.settings.currency),
             ", ".join(r["warnings"]) or "—"]
            for r in data["rows"]]
    table = _md_table(
        ["Booking", "Customer", "Hall", "Event start",
         t(lang, "dashboard.hold_remaining"),
         t(lang, "dashboard.missing_deposit"),
         t(lang, "dashboard.warnings")], rows)
    return dir_wrap(lang, f"## {t(lang, 'dashboard.pending')}\n\n{table}")


def build_dashboard_ui(db: DatabaseManager, auth: AuthService,
                       settings: AppSettings, session_token, lang_state) -> dict:
    """Build the Dashboard tab. Returns label targets + a render
    function for the language toggle (see module docstring)."""
    reports = ReportService(db, settings)

    def employee(token):
        emp = auth.validate_session(token)
        if emp is None:
            raise gr.Error("Your session has expired. Please log in again.")
        return emp

    with gr.Tab("Dashboard • لوحة المتابعة"):
        refresh_btn = gr.Button("↻ Refresh", variant="primary")
        dash_md = gr.Markdown("")
        pending_md = gr.Markdown("")

        def do_refresh(token, lang):
            emp = employee(token)
            try:
                return (_render_dashboard(reports, emp, lang or "ar"),
                        _render_pending(reports, emp, lang or "ar"))
            except ApplicationError as exc:
                raise gr.Error(str(exc))

        refresh_btn.click(do_refresh, inputs=[session_token, lang_state],
                          outputs=[dash_md, pending_md])

    return {
        "labels": [(refresh_btn, "common.refresh")],
        "renders": [(do_refresh, [dash_md, pending_md])],
    }
