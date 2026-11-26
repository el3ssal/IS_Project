"""Search screens (Phase 6, presentation tier, SRS §13.1).

Thin UI only: parse input, call ONE service method, render the result.
Global search shows only the modules the caller may see (the service
enforces it); snippets never carry amounts the caller may not see or
National IDs. Advanced search exposes the per-module filters with
sorting and pagination.
"""
from __future__ import annotations

import gradio as gr

from ..config.settings import AppSettings
from ..data.database import DatabaseManager
from ..logic.auth_service import AuthService
from ..logic.search_service import SearchService
from ..utils.exceptions import ApplicationError
from ..utils.logging_config import get_logger
from .i18n import dir_wrap, t

logger = get_logger(__name__)

_MODULES = ["customers", "bookings", "contracts", "payments", "staff", "files"]
_SECTION_TITLES = {
    "customers": "search.customers", "bookings": "search.bookings",
    "contracts": "search.contracts", "payments": "search.payments",
    "staff": "search.staff", "files": "search.files",
    "tasks": "search.tasks", "events": "search.events",
}


def _page_line(result: dict, lang: str) -> str:
    pages = max(1, (result["total"] + result["page_size"] - 1)
                // result["page_size"])
    return (f"{t(lang, 'common.page')} {result['page']}/{pages} — "
            f"{t(lang, 'common.total')}: {result['total']}")


def _rows_md(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "_No records yet._"
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def _render_module(name: str, result: dict, lang: str) -> str:
    lines = [f"### {t(lang, _SECTION_TITLES[name])} — {_page_line(result, lang)}"]
    if name == "customers":
        rows = [[r["id"], r["full_name"], r["phone"], r["email"],
                 r["national_id"]] for r in result["rows"]]
        lines.append(_rows_md(["ID", "Name", "Phone", "Email", "National ID"], rows))
    elif name == "bookings":
        rows = [[r["number"], r["customer"], r["hall_id"], r["start_at"],
                 r["status"], r["total_amount_display"]] for r in result["rows"]]
        lines.append(_rows_md(["Booking", "Customer", "Hall", "Start",
                               "Status", "Total"], rows))
    elif name == "contracts":
        rows = [[r["number"], r["booking_number"], r["customer"],
                 r["signature_status"], r["signature_date"]]
                for r in result["rows"]]
        lines.append(_rows_md(["Contract", "Booking", "Customer",
                               "Signature", "Signed on"], rows))
    elif name == "payments":
        rows = [[r["number"], r["booking_number"], r["method"],
                 r["amount_display"], r["confirmed_at"], r["status"]]
                for r in result["rows"]]
        lines.append(_rows_md(["Payment", "Booking", "Method", "Amount",
                               "Confirmed", "Status"], rows))
    elif name == "staff":
        rows = [[r["full_name"], r["username"], r["phone"], r["email"],
                 r["specialties"], "yes" if r["is_active"] else "no"]
                for r in result["rows"]]
        lines.append(_rows_md(["Name", "Username", "Phone", "Email",
                               "Specialties", "Active"], rows))
    elif name == "files":
        rows = [[r["id"], r["original_filename"], r["category"],
                 r["entity_type"], r["entity_id"], r["size_bytes"],
                 r["uploaded_at"]] for r in result["rows"]]
        lines.append(_rows_md(["ID", "Filename", "Category", "Entity",
                               "Record", "Size (bytes)", "Uploaded"], rows))
    elif name == "tasks":
        rows = [[r["id"], r["name"], r["status"], r["priority"],
                 r["due_at"], r["booking_id"]] for r in result["rows"]]
        lines.append(_rows_md(["Task", "Name", "Status", "Priority",
                               "Due", "Booking"], rows))
    elif name == "events":
        rows = [[r["number"], r["customer"], r["hall_id"], r["start_at"],
                 r["status"]] for r in result["rows"]]
        lines.append(_rows_md(["Booking", "Customer", "Hall", "Start",
                               "Status"], rows))
    return "\n\n".join(lines)


def build_search_ui(db: DatabaseManager, auth: AuthService,
                    settings: AppSettings, session_token, lang_state) -> dict:
    """Build the Search tab. Returns label targets for the toggle."""
    search = SearchService(db, settings)

    def employee(token):
        emp = auth.validate_session(token)
        if emp is None:
            raise gr.Error("Your session has expired. Please log in again.")
        return emp

    with gr.Tab("Search • بحث"):
        gr.Markdown("## Search — global + advanced (SRS §13.1)")
        gr.Markdown("One box across customers, bookings, contracts, "
                    "payments, staff, files and your own tasks.")
        with gr.Row():
            g_term = gr.Textbox(label="Search term")
            g_btn = gr.Button("Search", variant="primary")
        g_out = gr.Markdown("")

        gr.Markdown("### Advanced search")
        with gr.Row():
            a_module = gr.Dropdown(_MODULES, value="customers", label="Module")
            a_term = gr.Textbox(label="Search term")
            a_status = gr.Textbox(label="Status (optional)")
        with gr.Row():
            a_hall = gr.Number(label="Hall ID (bookings, optional)", precision=0)
            a_from = gr.Textbox(label="From date (YYYY-MM-DD, optional)")
            a_to = gr.Textbox(label="To date (YYYY-MM-DD, optional)")
            a_page = gr.Number(label="Page", precision=0, value=1)
            a_btn = gr.Button("Search", variant="primary")
        a_out = gr.Markdown("")

        def do_global(token, term, lang):
            emp = employee(token)
            try:
                results = search.global_search(emp, term or "")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            lang = lang or "ar"
            parts = []
            for name, result in results.items():
                parts.append(_render_module(name, result, lang))
            if not parts:
                return dir_wrap(lang, f"_{t(lang, 'common.no_records')}_")
            return dir_wrap(lang, "\n\n".join(parts))

        def do_advanced(token, module, term, status, hall_id, date_from,
                        date_to, page, lang):
            emp = employee(token)
            lang = lang or "ar"
            status = (status or "").strip() or None
            date_from = (date_from or "").strip() or None
            date_to = (date_to or "").strip() or None
            kwargs = {"term": term or "", "page": int(page or 1)}
            try:
                if module == "customers":
                    result = search.search_customers(emp, **kwargs)
                elif module == "bookings":
                    result = search.search_bookings(
                        emp, status=status,
                        hall_id=int(hall_id) if hall_id else None,
                        date_from=date_from, date_to=date_to, **kwargs)
                elif module == "contracts":
                    result = search.search_contracts(
                        emp, signature_status=status, **kwargs)
                elif module == "payments":
                    result = search.search_payments(
                        emp, date_from=date_from, date_to=date_to, **kwargs)
                elif module == "staff":
                    result = search.search_staff(emp, status=status, **kwargs)
                elif module == "files":
                    result = search.search_files(
                        emp, date_from=date_from, date_to=date_to, **kwargs)
                else:  # pragma: no cover - dropdown is fixed
                    raise gr.Error("Unknown module.")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return dir_wrap(lang, _render_module(module, result, lang))

        g_btn.click(do_global, inputs=[session_token, g_term, lang_state],
                    outputs=[g_out])
        g_term.submit(do_global, inputs=[session_token, g_term, lang_state],
                      outputs=[g_out])
        a_btn.click(do_advanced,
                    inputs=[session_token, a_module, a_term, a_status, a_hall,
                            a_from, a_to, a_page, lang_state],
                    outputs=[a_out])

    return {
        "labels": [(g_term, "search.term"), (g_btn, "common.search"),
                   (a_term, "search.term"), (a_status, "search.status"),
                   (a_hall, "search.hall"), (a_from, "search.date_from"),
                   (a_to, "search.date_to"), (a_page, "common.page"),
                   (a_btn, "common.search")],
        "renders": [],
    }
