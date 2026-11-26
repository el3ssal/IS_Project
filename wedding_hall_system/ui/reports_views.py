"""Report + audit screens (Phase 6, presentation tier, SRS §13.4, §12.1).

Thin UI only: parse input, call ONE service method, render the result.
Reports enforce role permissions in the service (operational reports
need ``report.view``, the financial report needs ``finance.view``).
CSV export is the mandatory academic export; charts use Gradio's
built-in BarPlot over a pandas DataFrame (pandas ships with Gradio —
no new dependency, requirements.txt stays gradio-only).
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import gradio as gr
import pandas as pd

from ..config.settings import AppSettings
from ..data.database import DatabaseManager
from ..logic import audit_service
from ..logic.auth_service import AuthService
from ..logic.report_service import ReportService
from ..utils.exceptions import ApplicationError
from ..utils.logging_config import get_logger
from .i18n import REPORT_TITLES, dir_wrap, t

logger = get_logger(__name__)

REPORTS = ["bookings", "hall_utilization", "customers", "packages",
           "staff", "events", "cancellations", "financial"]


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "_No records yet._"
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def _chart(report_name: str, report: dict):
    """One BarPlot per report where a chart is meaningful; None when
    there is nothing to plot. DataFrames are built in the UI only —
    the service stays dependency-free."""
    rows = report["rows"]
    if not rows:
        return None
    try:
        if report_name == "bookings":
            df = pd.DataFrame(rows, columns=report["columns"])
            grouped = df.groupby("Hall")["Total (EGP)"].apply(
                lambda s: s.astype(float).sum()).reset_index()
            return gr.BarPlot(grouped, x="Hall", y="Total (EGP)",
                              title="Contract value by hall")
        if report_name == "hall_utilization":
            df = pd.DataFrame(rows, columns=report["columns"])
            return gr.BarPlot(df, x="Hall", y="Utilization %",
                              title="Utilization % by hall")
        if report_name == "customers":
            df = pd.DataFrame(rows, columns=report["columns"]).head(10)
            return gr.BarPlot(df, x="Customer", y="Total (EGP)",
                              title="Top customers by contract value")
        if report_name == "packages":
            df = pd.DataFrame(rows, columns=report["columns"])
            return gr.BarPlot(df, x="Package", y="Total (EGP)",
                              title="Contract value by package")
        if report_name == "staff":
            df = pd.DataFrame(rows, columns=report["columns"])
            return gr.BarPlot(df, x="Employee", y="Completed",
                              title="Completed tasks by employee")
        if report_name == "events":
            df = pd.DataFrame(rows, columns=report["columns"])
            grouped = df.groupby("Hall").size().reset_index(name="Events")
            return gr.BarPlot(grouped, x="Hall", y="Events",
                              title="Events by hall")
        if report_name == "cancellations":
            df = pd.DataFrame(rows, columns=report["columns"])
            return gr.BarPlot(df, x="Booking", y="Fee (EGP)",
                              title="Cancellation fees")
        if report_name == "financial":
            df = pd.DataFrame(rows, columns=report["columns"]).head(10)
            return gr.BarPlot(df, x="Booking", y="Collected (EGP)",
                              title="Collected by booking (top 10)")
    except Exception as exc:  # pragma: no cover - chart is best-effort
        logger.warning("Chart failed for %s: %s", report_name, exc)
    return None


def _render_report(report_name: str, report: dict, lang: str) -> str:
    title = t(lang, REPORT_TITLES[report_name])
    lines = [f"## {title}", _md_table(report["columns"], report["rows"])]
    totals = report.get("totals") or {}
    if totals:
        lines.append("**Totals:** " + json.dumps(totals, ensure_ascii=False))
    if report.get("comparison"):
        lines.append(f"**{t(lang, 'reports.comparison')}:** "
                     + json.dumps(report["comparison"], ensure_ascii=False))
    lines.append(f"**{t(lang, 'reports.basis')}:** {report.get('basis', '')}")
    return dir_wrap(lang, "\n\n".join(lines))


def build_reports_ui(db: DatabaseManager, auth: AuthService,
                     settings: AppSettings, session_token, lang_state) -> dict:
    """Build the Reports tab (incl. the audit log screen, FR-062).
    Returns label targets for the toggle."""
    reports = ReportService(db, settings)

    def employee(token):
        emp = auth.validate_session(token)
        if emp is None:
            raise gr.Error("Your session has expired. Please log in again.")
        return emp

    with gr.Tab("Reports • تقارير"):
        gr.Markdown("## Reports — operational + financial, CSV export (SRS §13.4)")
        with gr.Row():
            rep_name = gr.Dropdown(REPORTS, value="bookings", label="Report")
            rep_from = gr.Textbox(label="From date (YYYY-MM-DD, optional)")
            rep_to = gr.Textbox(label="To date (YYYY-MM-DD, optional)")
            rep_btn = gr.Button("Run report", variant="primary")
            csv_btn = gr.Button("Export CSV", variant="secondary")
        rep_out = gr.Markdown("")
        rep_chart = gr.BarPlot(label="Chart")
        rep_csv = gr.File(label="CSV export")

        gr.Markdown("### Audit log")
        with gr.Row():
            au_employee = gr.Number(label="Employee ID (optional)", precision=0)
            au_action = gr.Textbox(label="Action contains (optional)")
            au_entity = gr.Textbox(label="Entity type (optional)")
            au_from = gr.Textbox(label="From date (YYYY-MM-DD, optional)")
            au_to = gr.Textbox(label="To date (YYYY-MM-DD, optional)")
            au_btn = gr.Button("Search audit log")
        au_out = gr.Markdown("")

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
            return emp, method(emp, date_from=(date_from or "").strip() or None,
                               date_to=(date_to or "").strip() or None)

        def do_run(token, name, date_from, date_to, lang):
            try:
                _, report = _run(token, name, date_from, date_to)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return _render_report(name, report, lang or "ar"), _chart(name, report)

        def do_csv(token, name, date_from, date_to):
            emp = employee(token)
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
            gr.Info(f"CSV export ready ({len(csv_text)} characters).")
            return tmp.name

        def do_audit(token, emp_id, action, entity, date_from, date_to, lang):
            emp = employee(token)
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
            lang = lang or "ar"
            rows = [[r["id"], r["employee_id"], r["action"], r["entity_type"],
                     r["entity_id"], r["created_at"], r["description"]]
                    for r in result["rows"]]
            pages = max(1, (result["total"] + result["page_size"] - 1)
                        // result["page_size"])
            table = _md_table(
                ["ID", "Employee", "Action", "Entity", "Record", "At",
                 "Description"], rows)
            return dir_wrap(
                lang, f"## {t(lang, 'audit.title')}\n\n"
                      f"{t(lang, 'common.page')} {result['page']}/{pages} — "
                      f"{t(lang, 'common.total')}: {result['total']}\n\n{table}")

        rep_btn.click(do_run,
                      inputs=[session_token, rep_name, rep_from, rep_to,
                              lang_state],
                      outputs=[rep_out, rep_chart])
        csv_btn.click(do_csv,
                      inputs=[session_token, rep_name, rep_from, rep_to],
                      outputs=[rep_csv])
        au_btn.click(do_audit,
                     inputs=[session_token, au_employee, au_action, au_entity,
                             au_from, au_to, lang_state],
                     outputs=[au_out])

    return {
        "labels": [(rep_name, "reports.title"),
                   (rep_from, "search.date_from"),
                   (rep_to, "search.date_to"),
                   (rep_btn, "reports.run"),
                   (csv_btn, "reports.export_csv"),
                   (au_employee, "audit.employee"),
                   (au_action, "audit.action"),
                   (au_entity, "audit.entity"),
                   (au_from, "search.date_from"),
                   (au_to, "search.date_to"),
                   (au_btn, "audit.search")],
        "renders": [],
    }
