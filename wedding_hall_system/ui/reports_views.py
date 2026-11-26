"""Report + audit screens (Phase 6, presentation tier, SRS §13.4,
§12.1; Phase 8: fully bilingual, SRS §16.3).

Thin UI only: parse input, call ONE service method, render the result.
Reports enforce role permissions in the service (operational reports
need ``report.view``, the financial report needs ``finance.view``).
CSV export is the mandatory academic export; charts use Gradio's
built-in BarPlot over a pandas DataFrame (pandas ships with Gradio —
no new dependency, requirements.txt stays gradio + cryptography).

The service returns canonical English column names and basis text
(SRS §13.4); the rendered table, totals, comparison and basis are
translated at display time, while the CSV export keeps the canonical
English column names so it stays stable for spreadsheet tools.

Audit log: Owner/Manager only (A-59); read-only and non-reversible —
the audit table has no delete endpoint at all.
"""
from __future__ import annotations

import json
import tempfile

import gradio as gr
import pandas as pd

from ..config.settings import AppSettings
from ..data.database import DatabaseManager
from ..logic import audit_service
from ..logic.auth_service import AuthService
from ..logic.report_service import ReportService
from ..utils.exceptions import ApplicationError
from ..utils.logging_config import get_logger
from .finance_views import _flag
from .i18n import REPORT_TITLES, LabelRegistry, dir_wrap, lang_for_token, t

logger = get_logger(__name__)

REPORTS = ["bookings", "hall_utilization", "customers", "packages",
           "staff", "events", "cancellations", "financial"]

# Service column name (canonical English, SRS §13.4) -> rendered key.
COLUMN_KEYS = {
    "Booking": "reports.col_booking", "Customer": "reports.col_customer",
    "Hall": "reports.col_hall", "Start": "reports.col_start",
    "Status": "reports.col_status", "Total (EGP)": "reports.col_total",
    "Collected (EGP)": "reports.col_collected",
    "Refunds paid (EGP)": "reports.col_refunds_paid",
    "Outstanding (EGP)": "reports.col_outstanding",
    "Events": "reports.col_events", "Booked hours": "reports.col_booked_hours",
    "Available hours": "reports.col_available_hours",
    "Utilization %": "reports.col_utilization",
    "Phone": "reports.col_phone", "Bookings": "reports.col_bookings",
    "Package": "reports.col_package", "Employee": "reports.col_employee",
    "Assigned": "reports.col_assigned", "Completed": "reports.col_completed",
    "Overdue": "reports.col_overdue", "Event type": "reports.col_event_type",
    "Guests": "reports.col_guests", "Event date": "reports.col_event_date",
    "Fee (EGP)": "reports.col_fee",
    "Refundable (EGP)": "reports.col_refundable",
    "Net collected (EGP)": "reports.col_net",
    "Flags": "reports.col_flags",
}
TOTALS_KEYS = {
    "bookings": "reports.tot_bookings",
    "contract_value": "reports.tot_contract_value",
    "events": "reports.tot_events",
    "customers": "reports.tot_customers",
    "total": "reports.tot_total",
    "collected": "reports.tot_collected",
    "assigned": "reports.tot_assigned",
    "completed": "reports.tot_completed",
    "overdue": "reports.tot_overdue",
    "cancelled": "reports.tot_cancelled",
    "fees": "reports.tot_fees",
    "refundable": "reports.tot_refundable",
    "refunds_paid": "reports.tot_refunds_paid",
    "net_collected": "reports.col_net",
    "recognised_revenue": "reports.tot_revenue",
    "overpayment": "reports.tot_overpayment",
    "outstanding": "reports.tot_outstanding",
    "expenses_approved": "reports.tot_expenses_approved",
    "expenses_paid": "reports.tot_expenses_paid",
    "cash_in": "reports.tot_cash_in",
    "cash_out": "reports.tot_cash_out",
    "cash_flow": "reports.tot_cash_flow",
    "profit": "reports.tot_profit",
    "period": "reports.tot_period",
}
BASIS_KEYS = {
    "bookings": "reports.basis_bookings",
    "hall_utilization": "reports.basis_hall_utilization",
    "customers": "reports.basis_customers",
    "packages": "reports.basis_packages",
    "staff": "reports.basis_staff",
    "events": "reports.basis_events",
    "cancellations": "reports.basis_cancellations",
    "financial": "fin.basis_cash",
}
CHART_KEYS = {
    "bookings": "reports.chart_bookings",
    "hall_utilization": "reports.chart_hall_utilization",
    "customers": "reports.chart_customers",
    "packages": "reports.chart_packages",
    "staff": "reports.chart_staff",
    "events": "reports.chart_events",
    "cancellations": "reports.chart_cancellations",
    "financial": "reports.chart_financial",
}
BOOKING_STATUS_KEYS = {
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
}


def _translate_cell(lang: str, col: str, value):
    if col == "Status":
        key = BOOKING_STATUS_KEYS.get(str(value))
        return t(lang, key) if key else value
    if col == "Flags" and isinstance(value, str) and value != "—":
        return "; ".join(_flag(lang, f) for f in value.split("; "))
    if col == "Package" and value == "No package":
        return t(lang, "reports.no_package")
    return value


def _md_table(headers: list[str], rows: list[list[str]], lang: str) -> str:
    if not rows:
        return f"_{t(lang, 'common.no_records')}_"
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def _chart(report_name: str, report: dict, lang: str):
    """One BarPlot per report where a chart is meaningful; None when
    there is nothing to plot. DataFrames are built in the UI only —
    the service stays dependency-free. Axis labels are the translated
    column names."""
    rows = report["rows"]
    if not rows:
        return None
    try:
        df = pd.DataFrame(rows, columns=report["columns"])
        col = lambda name: t(lang, COLUMN_KEYS.get(name, "reports.col_booking"))
        title = t(lang, CHART_KEYS[report_name])
        if report_name == "bookings":
            grouped = df.groupby("Hall")["Total (EGP)"].apply(
                lambda s: s.astype(float).sum()).reset_index()
            grouped.columns = [col("Hall"), col("Total (EGP)")]
            return gr.BarPlot(grouped, x=col("Hall"), y=col("Total (EGP)"),
                              title=title)
        if report_name == "hall_utilization":
            df = df.rename(columns={c: col(c) for c in df.columns})
            return gr.BarPlot(df, x=col("Hall"), y=col("Utilization %"),
                              title=title)
        if report_name == "customers":
            df = df.head(10).rename(columns={c: col(c) for c in df.columns})
            return gr.BarPlot(df, x=col("Customer"), y=col("Total (EGP)"),
                              title=title)
        if report_name == "packages":
            df = df.rename(columns={c: col(c) for c in df.columns})
            return gr.BarPlot(df, x=col("Package"), y=col("Total (EGP)"),
                              title=title)
        if report_name == "staff":
            df = df.rename(columns={c: col(c) for c in df.columns})
            return gr.BarPlot(df, x=col("Employee"), y=col("Completed"),
                              title=title)
        if report_name == "events":
            grouped = df.groupby("Hall").size().reset_index(name="Events")
            grouped.columns = [col("Hall"), col("Events")]
            return gr.BarPlot(grouped, x=col("Hall"), y=col("Events"),
                              title=title)
        if report_name == "cancellations":
            df = df.rename(columns={c: col(c) for c in df.columns})
            return gr.BarPlot(df, x=col("Booking"), y=col("Fee (EGP)"),
                              title=title)
        if report_name == "financial":
            df = df.head(10).rename(columns={c: col(c) for c in df.columns})
            return gr.BarPlot(df, x=col("Booking"),
                              y=col("Collected (EGP)"), title=title)
    except Exception as exc:  # pragma: no cover - chart is best-effort
        logger.warning("Chart failed for %s: %s", report_name, exc)
    return None


def _render_report(report_name: str, report: dict, lang: str) -> str:
    title = t(lang, REPORT_TITLES[report_name])
    headers = [t(lang, COLUMN_KEYS.get(c, "reports.col_booking"))
               for c in report["columns"]]
    rows = [[_translate_cell(lang, col, value)
             for col, value in zip(report["columns"], row)]
            for row in report["rows"]]
    lines = [f"## {title}", _md_table(headers, rows, lang)]
    totals = {k: v for k, v in (report.get("totals") or {}).items()
              if k != "basis"}
    if totals:
        rendered = {t(lang, TOTALS_KEYS.get(k, "reports.tot_total")): v
                    for k, v in totals.items()}
        lines.append(f"**{t(lang, 'reports.totals')}:** "
                     + json.dumps(rendered, ensure_ascii=False))
    if report.get("comparison"):
        rendered = {t(lang, TOTALS_KEYS.get(k, "reports.tot_total")): v
                    for k, v in report["comparison"].items()}
        lines.append(f"**{t(lang, 'reports.comparison')}:** "
                     + json.dumps(rendered, ensure_ascii=False))
    lines.append(f"**{t(lang, 'reports.basis')}:** "
                 + t(lang, BASIS_KEYS[report_name]))
    return dir_wrap(lang, "\n\n".join(lines))


def build_reports_ui(db: DatabaseManager, auth: AuthService,
                     settings: AppSettings, session_token, lang_state) -> dict:
    """Build the Reports tab (incl. the audit log screen, FR-062).
    Returns label targets for the toggle."""
    reg = LabelRegistry()
    reports = ReportService(db, settings)

    def _lang(token) -> str:
        return lang_for_token(db, auth, token)

    def employee(token):
        emp = auth.validate_session(token)
        if emp is None:
            raise gr.Error(t(_lang(token), "login.session_expired"))
        return emp

    with reg.tab("tab.reports"):
        reg.markdown("reports.heading")
        with gr.Row():
            rep_name = reg.dropdown_data(
                "reports.report",
                lambda lang: [(t(lang, REPORT_TITLES[m]), m) for m in REPORTS],
                value="bookings")
            rep_from = reg.textbox("search.date_from_optional")
            rep_to = reg.textbox("search.date_to_optional")
            rep_btn = reg.button("reports.run", variant="primary")
            csv_btn = reg.button("reports.export_csv")
        rep_md = gr.Markdown("")
        rep_chart = gr.BarPlot(label=t("ar", "reports.chart"))
        reg.labels.append((rep_chart, "reports.chart"))
        rep_csv = reg.file("reports.export_csv")

        def _run(token, name, date_from, date_to):
            emp = employee(token)
            method = getattr(reports, {
                "bookings": "booking_report",
                "hall_utilization": "hall_utilization",
                "customers": "customer_report",
                "packages": "package_report",
                "staff": "staff_report",
                "events": "event_report",
                "cancellations": "cancellation_report",
                "financial": "financial_report",
            }[name])
            return method(emp, date_from=(date_from or "").strip() or None,
                          date_to=(date_to or "").strip() or None)

        def do_run(token, name, date_from, date_to, lang):
            try:
                report = _run(token, name, date_from, date_to)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return _render_report(name, report, lang or "ar"), \
                _chart(name, report, lang or "ar")

        def do_csv(token, name, date_from, date_to, lang):
            emp = employee(token)
            lang = lang or "ar"
            try:
                csv_text = reports.export_csv(
                    emp, name, date_from=(date_from or "").strip() or None,
                    date_to=(date_to or "").strip() or None)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".csv", prefix=f"{name}_report_",
                delete=False, encoding="utf-8", newline="")
            with tmp:
                tmp.write(csv_text)
            gr.Info(t(lang, "reports.msg_csv_ready").format(n=len(csv_text)))
            return tmp.name

        rep_btn.click(do_run,
                      inputs=[session_token, rep_name, rep_from, rep_to,
                              lang_state],
                      outputs=[rep_md, rep_chart])
        csv_btn.click(do_csv,
                      inputs=[session_token, rep_name, rep_from, rep_to,
                              lang_state],
                      outputs=[rep_csv])

        reg.markdown("reports.audit_heading")
        with gr.Row():
            au_employee = reg.number("audit.employee_optional", precision=0)
            au_action = reg.textbox("audit.action_optional")
            au_entity = reg.textbox("audit.entity_optional")
        with gr.Row():
            au_from = reg.textbox("search.date_from_optional")
            au_to = reg.textbox("search.date_to_optional")
            au_btn = reg.button("audit.search", variant="primary")
        au_out = gr.Markdown("")

        def do_audit(token, emp_id, action, entity, date_from, date_to, lang):
            emp = employee(token)
            lang = lang or "ar"
            try:
                result = audit_service.search(
                    db, emp,
                    employee_id=int(emp_id) if emp_id else None,
                    action=(action or "").strip() or None,
                    entity_type=(entity or "").strip() or None,
                    date_from=(date_from or "").strip() or None,
                    date_to=(date_to or "").strip() or None)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            rows = [[r["id"], r["employee_id"], r["action"], r["entity_type"],
                     r["entity_id"], r["created_at"], r["description"]]
                    for r in result["rows"]]
            pages = max(1, (result["total"] + result["page_size"] - 1)
                        // result["page_size"])
            table = _md_table(
                [t(lang, "audit.table_id"), t(lang, "audit.table_employee"),
                 t(lang, "audit.table_action"), t(lang, "audit.table_entity"),
                 t(lang, "audit.table_record"), t(lang, "audit.table_at"),
                 t(lang, "audit.table_description")], rows, lang)
            return dir_wrap(
                lang, f"## {t(lang, 'audit.title')}\n\n"
                      f"{t(lang, 'common.page')} {result['page']}/{pages} — "
                      f"{t(lang, 'common.total')}: {result['total']}\n\n{table}")

        au_btn.click(do_audit,
                     inputs=[session_token, au_employee, au_action, au_entity,
                             au_from, au_to, lang_state],
                     outputs=[au_out])

    return reg.result()
