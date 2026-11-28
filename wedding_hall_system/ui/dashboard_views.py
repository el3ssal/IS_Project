"""Dashboard screens (Phase 6, presentation tier, SRS §13.2–§13.3;
Phase 8: fully bilingual, SRS §16.3).

Thin UI only: parse input, call ONE service method, render the result.
No SQL and no business rules here. Every handler resolves the session
token to an employee and lets the service enforce permissions.

All rendered content is language-aware: handlers take the session
language state and render through ``ui/i18n.py`` (Arabic RTL by
default). The builder returns its label targets so the main view's
language toggle can rename the inputs, and a ``render`` function the
toggle calls to re-render the dashboard in the new language.

Service-rendered English fragments (hold countdowns, warning tags,
status names) are translated at render time by pattern — the service
text is never edited.
"""
from __future__ import annotations

import re

import gradio as gr

from ..config.settings import AppSettings, format_money
from ..data.database import DatabaseManager
from ..logic.auth_service import AuthService
from ..logic.report_service import ReportService
from ..utils.exceptions import ApplicationError
from ..utils.logging_config import get_logger
from .i18n import WIDGET_TITLES, LabelRegistry, dir_wrap, lang_for_token, t
from .live_refresh import login_follow_up

logger = get_logger(__name__)

STATUS_KEYS = {
    "pending": "booking.status_pending",
    "confirmed": "booking.status_confirmed",
    "in_progress": "booking.status_in_progress",
    "completed": "booking.status_completed",
    "cancelled": "booking.status_cancelled",
    "no_show": "booking.status_no_show",
    "expired": "booking.status_expired",
    "draft": "booking.status_draft",
    "sent": "booking.status_sent",
    "accepted": "booking.status_accepted",
    "reversed": "booking.status_reversed",
    "active": "catalog.status_active",
    "inactive": "catalog.status_inactive",
    "maintenance": "catalog.status_maintenance",
}
METHOD_KEYS = {
    "cash": "booking.method_cash", "bank_transfer": "booking.method_bank_transfer",
    "visa_card": "booking.method_visa_card", "instapay": "booking.method_instapay",
    "other": "booking.method_other",
}
WARN_KEYS = {
    "hold expiring soon": "dashboard.warn_hold_soon",
    "deposit outstanding": "dashboard.warn_deposit",
}


def _md_table(headers: list[str], rows: list[list[str]], lang: str) -> str:
    if not rows:
        return f"_{t(lang, 'common.no_records')}_"
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def _hold(lang: str, value: str) -> str:
    """Translate a service hold countdown (``"31h 12m left"`` /
    ``"expired"``) into the reader's language."""
    if value == "expired":
        return t(lang, "dashboard.hold_expired")
    m = re.match(r"^(\d+)h (\d+)m left$", str(value))
    if m:
        return t(lang, "dashboard.hold_left").format(h=m.group(1), m=m.group(2))
    return value


def _status(lang: str, status: str) -> str:
    return t(lang, STATUS_KEYS.get(status, "booking.status_pending"))


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
    cur = reports.settings.currency
    if key in ("revenue", "profit"):
        return f"**{widget['display']}**"
    if key == "expenses":
        return (f"{t(lang, 'dashboard.expenses')}: "
                f"**{format_money(widget['approved'], cur)}**  \n"
                f"{t(lang, 'dashboard.pending_approval')}: "
                f"{format_money(widget['pending_approval'], cur)}")
    if key == "financial_summary":
        return _md_table(
            ["", "EGP"],
            [[t(lang, "dashboard.revenue"), format_money(widget["collected"], cur)],
             [t(lang, "dashboard.profit"), format_money(widget["profit"], cur)],
             [t(lang, "dashboard.outstanding"), format_money(widget["outstanding"], cur)],
             [t(lang, "dashboard.refunds"), format_money(widget["refunds_paid"], cur)]],
            lang)
    if key == "bookings":
        rows = [[_status(lang, s), n] for s, n in sorted(widget["by_status"].items())]
        return (_md_table([t(lang, "dashboard.table_status"),
                           t(lang, "dashboard.table_count")], rows, lang)
                + f"\n\n{t(lang, 'dashboard.total')}: **{widget['total']}**")
    if key == "pending_bookings":
        return t(lang, "dashboard.pending_line").format(
            count=widget["count"], expiring=widget["expiring_soon"],
            amount=format_money(widget["missing_deposit_total"], cur))
    if key == "upcoming_events":
        rows = [[e["number"], e["start_at"], e["hall_id"]]
                for e in widget["events"]]
        return _md_table([t(lang, "dashboard.table_booking"),
                          t(lang, "dashboard.table_start"),
                          t(lang, "dashboard.table_hall")], rows, lang)
    if key == "new_bookings":
        return f"**{widget['count']}** — {', '.join(widget['numbers']) or '—'}"
    if key == "customers":
        return f"**{widget['count']}**"
    if key == "quotations":
        rows = [[_status(lang, s), n] for s, n in sorted(widget["by_status"].items())]
        return _md_table([t(lang, "dashboard.table_status"),
                          t(lang, "dashboard.table_count")], rows, lang)
    if key == "holds_expiring":
        rows = [[b["number"], _hold(lang, b["hold_remaining"])]
                for b in widget["bookings"]]
        return _md_table([t(lang, "dashboard.table_booking"),
                          t(lang, "dashboard.table_hold")], rows, lang)
    if key == "hall_status":
        rows = [[h["name"], _status(lang, h["status"]), h["events_today"],
                 h["upcoming_maintenance"]] for h in widget["halls"]]
        return _md_table([t(lang, "dashboard.table_hall"),
                          t(lang, "dashboard.table_status"),
                          t(lang, "dashboard.table_events_today"),
                          t(lang, "dashboard.table_maintenance")], rows, lang)
    if key == "maintenance":
        rows = [[b["hall_id"], b["type"], b["start_at"], b["end_at"]]
                for b in widget["blocks"]]
        return _md_table([t(lang, "dashboard.table_hall"),
                          t(lang, "dashboard.table_type"),
                          t(lang, "dashboard.table_from"),
                          t(lang, "dashboard.table_to")], rows, lang)
    if key == "staff_overview":
        rows = [[e, n] for e, n in
                sorted(widget["open_tasks_by_employee"].items())]
        return (t(lang, "dashboard.employees_line").format(
                    n=widget["employees"], m=widget["active"]) + "\n\n"
                + _md_table([t(lang, "dashboard.table_employee"),
                             t(lang, "dashboard.table_open_tasks")], rows, lang))
    if key == "tasks_overview":
        rows = [[_status(lang, s), n] for s, n in sorted(widget["by_status"].items())]
        return (_md_table([t(lang, "dashboard.table_status"),
                           t(lang, "dashboard.table_count")], rows, lang)
                + f"\n\n{t(lang, 'dashboard.overdue')}: **{widget['overdue']}**")
    if key == "conflicts":
        rows = [[c["employee_name"], c["open_tasks"]]
                for c in widget["conflicts"]]
        return _md_table([t(lang, "dashboard.table_employee"),
                          t(lang, "dashboard.table_open_tasks")], rows, lang)
    if key == "payments":
        rows = [[p["number"], t(lang, METHOD_KEYS.get(p["method"], "booking.method_other")),
                 format_money(p["amount"], cur)]
                for p in widget["recent"]]
        return (_md_table([t(lang, "dashboard.table_payment"),
                           t(lang, "dashboard.table_method"),
                           t(lang, "dashboard.table_amount")], rows, lang)
                + f"\n\n{t(lang, 'dashboard.collected_total')}: "
                  f"**{format_money(widget['collected_total'], cur)}**")
    if key == "installments_due":
        rows = [[i["id"], i["booking_id"],
                 format_money(i["amount"], cur),
                 i["due_date"]] for i in widget["overdue"]]
        return _md_table([t(lang, "dashboard.table_installment"),
                          t(lang, "dashboard.table_booking"),
                          t(lang, "dashboard.table_amount"),
                          t(lang, "dashboard.table_due")], rows, lang)
    if key == "refunds":
        return (f"{t(lang, 'dashboard.pending_approval')}: **{widget['pending_approval']}**  \n"
                f"{t(lang, 'dashboard.amount')}: {format_money(widget['pending_amount'], cur)}")
    if key in ("my_tasks", "upcoming_tasks", "overdue_tasks"):
        if key == "my_tasks":
            rows = [[_status(lang, s), n] for s, n in sorted(widget["by_status"].items())]
            return _md_table([t(lang, "dashboard.table_status"),
                              t(lang, "dashboard.table_count")], rows, lang)
        rows = [[task["id"], task["name"], task["due_at"]]
                for task in widget["tasks"]]
        return _md_table([t(lang, "dashboard.table_task"),
                          t(lang, "dashboard.table_name"),
                          t(lang, "dashboard.table_due")], rows, lang)
    return str(widget)


def _render_pending(reports: ReportService, employee, lang: str) -> str:
    data = reports.pending_bookings(employee)
    rows = [[r["number"], r["customer"], r["hall_id"], r["start_at"],
             _hold(lang, r["hold_remaining"]),
             format_money(r["missing_deposit"], reports.settings.currency),
             ", ".join(t(lang, WARN_KEYS.get(w, w)) if w in WARN_KEYS else w
                       for w in r["warnings"]) or "—"]
            for r in data["rows"]]
    table = _md_table(
        [t(lang, "dashboard.table_booking"), t(lang, "dashboard.table_customer"),
         t(lang, "dashboard.table_hall"), t(lang, "dashboard.table_event_start"),
         t(lang, "dashboard.hold_remaining"),
         t(lang, "dashboard.missing_deposit"),
         t(lang, "dashboard.warnings")], rows, lang)
    return dir_wrap(lang, f"## {t(lang, 'dashboard.pending')}\n\n{table}")


def build_dashboard_ui(db: DatabaseManager, auth: AuthService,
                       settings: AppSettings, session_token, lang_state) -> dict:
    """Build the Dashboard tab. Returns label targets + a render
    function for the language toggle (see module docstring)."""
    reg = LabelRegistry()
    reports = ReportService(db, settings)

    def _lang(token) -> str:
        return lang_for_token(db, auth, token)

    def employee(token):
        emp = auth.validate_session(token)
        if emp is None:
            raise gr.Error(t(_lang(token), "login.session_expired"))
        return emp

    with reg.tab("tab.dashboard"):
        refresh_btn = reg.button("common.refresh", variant="primary")
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

    result = reg.result()
    result["renders"] = [(do_refresh, [dash_md, pending_md])]
    # A-94: populate the dashboard right after sign-in (session/
    # permission-tolerant wrapper, see ui.live_refresh), in the
    # user's own language.
    result["on_login"] = [
        (login_follow_up(
            auth,
            lambda token: do_refresh(token, lang_for_token(db, auth, token)),
            "do_refresh_dashboard_on_login", n_outputs=2),
         [dash_md, pending_md]),
    ]
    return result
