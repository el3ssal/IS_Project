"""Search screens (Phase 6, presentation tier, SRS §13.1; Phase 8:
fully bilingual, SRS §16.3).

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
from .i18n import LabelRegistry, dir_wrap, lang_for_token, t

logger = get_logger(__name__)

_MODULES = ["customers", "bookings", "contracts", "payments", "staff", "files"]
_SECTION_TITLES = {
    "customers": "search.customers", "bookings": "search.bookings",
    "contracts": "search.contracts", "payments": "search.payments",
    "staff": "search.staff", "files": "search.files",
    "tasks": "search.tasks", "events": "search.events",
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
SIG_STATUS_KEYS = {
    "draft": "ops.sig_draft",
    "pending_signature": "ops.sig_pending_signature",
    "partially_signed": "ops.sig_partially_signed",
    "signed": "ops.sig_signed",
    "cancelled": "ops.sig_cancelled",
}
PAY_STATUS_KEYS = {
    "recorded": "fin.pay_recorded",
    "reversed": "booking.status_reversed",
    "void": "fin.pay_void",
}
METHOD_KEYS = {
    "cash": "booking.method_cash", "bank_transfer": "booking.method_bank_transfer",
    "visa_card": "booking.method_visa_card", "instapay": "booking.method_instapay",
    "other": "booking.method_other",
}
TASK_STATUS_KEYS = {
    "pending": "ops.task_status_pending",
    "in_progress": "ops.task_status_in_progress",
    "completed": "ops.task_status_completed",
}
PRIORITY_KEYS = {
    "low": "ops.priority_low", "medium": "ops.priority_medium",
    "high": "ops.priority_high", "urgent": "ops.priority_urgent",
    "normal": "ops.priority_normal",
}
CAT_KEYS = {
    "Identity Documents": "files.cat_identity",
    "Contracts": "files.cat_contracts",
    "Receipts": "files.cat_receipts",
    "Event Photos": "files.cat_photos",
    "Customer Documents": "files.cat_customer_docs",
    "Other": "files.cat_other",
}
ENTITY_KEYS = {
    "customer": "files.entity_customer",
    "booking": "files.entity_booking",
    "contract": "files.entity_contract",
    "payment": "files.entity_payment",
    "expense": "files.entity_expense",
}


def _page_line(result: dict, lang: str) -> str:
    pages = max(1, (result["total"] + result["page_size"] - 1)
                // result["page_size"])
    return (f"{t(lang, 'common.page')} {result['page']}/{pages} — "
            f"{t(lang, 'common.total')}: {result['total']}")


def _rows_md(headers: list[str], rows: list[list[str]], lang: str) -> str:
    if not rows:
        return f"_{t(lang, 'common.no_records')}_"
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def _h(lang: str, *keys: str) -> list[str]:
    return [t(lang, k) for k in keys]


def _render_module(name: str, result: dict, lang: str) -> str:
    lines = [f"### {t(lang, _SECTION_TITLES[name])} — {_page_line(result, lang)}"]
    if name == "customers":
        rows = [[r["id"], r["full_name"], r["phone"], r["email"],
                 r["national_id"]] for r in result["rows"]]
        lines.append(_rows_md(_h(lang, "search.table_id", "search.table_name",
                                  "search.table_phone", "search.table_email",
                                  "search.table_national"), rows, lang))
    elif name == "bookings":
        rows = [[r["number"], r["customer"], r["hall_id"], r["start_at"],
                 t(lang, BOOKING_STATUS_KEYS.get(r["status"], "booking.status_pending")),
                 r["total_amount_display"]] for r in result["rows"]]
        lines.append(_rows_md(_h(lang, "search.table_booking",
                                  "search.table_customer", "search.table_hall",
                                  "search.table_start", "search.table_status",
                                  "search.table_total"), rows, lang))
    elif name == "contracts":
        rows = [[r["number"], r["booking_number"], r["customer"],
                 t(lang, SIG_STATUS_KEYS.get(r["signature_status"], "ops.sig_draft")),
                 r["signature_date"]]
                for r in result["rows"]]
        lines.append(_rows_md(_h(lang, "search.table_contract",
                                  "search.table_booking", "search.table_customer",
                                  "search.table_signature",
                                  "search.table_signed_on"), rows, lang))
    elif name == "payments":
        rows = [[r["number"], r["booking_number"],
                 t(lang, METHOD_KEYS.get(r["method"], "booking.method_other")),
                 r["amount_display"], r["confirmed_at"],
                 t(lang, PAY_STATUS_KEYS.get(r["status"], "fin.pay_recorded"))]
                for r in result["rows"]]
        lines.append(_rows_md(_h(lang, "search.table_payment",
                                  "search.table_booking", "search.table_method",
                                  "search.table_amount", "search.table_confirmed",
                                  "search.table_status"), rows, lang))
    elif name == "staff":
        rows = [[r["full_name"], r["username"], r["phone"], r["email"],
                 r["specialties"],
                 t(lang, "common.yes" if r["is_active"] else "common.no")]
                for r in result["rows"]]
        lines.append(_rows_md(_h(lang, "search.table_name",
                                  "search.table_username", "search.table_phone",
                                  "search.table_email",
                                  "search.table_specialties",
                                  "search.table_active"), rows, lang))
    elif name == "files":
        rows = [[r["id"], r["original_filename"],
                 t(lang, CAT_KEYS.get(r["category"], "files.cat_other")),
                 t(lang, ENTITY_KEYS.get(r["entity_type"], "files.entity_customer")),
                 r["entity_id"], r["size_bytes"], r["uploaded_at"]]
                for r in result["rows"]]
        lines.append(_rows_md(_h(lang, "search.table_id",
                                  "search.table_filename", "search.table_category",
                                  "search.table_entity", "search.table_record",
                                  "search.table_size", "search.table_uploaded"),
                              rows, lang))
    elif name == "tasks":
        rows = [[r["id"], r["name"],
                 t(lang, TASK_STATUS_KEYS.get(r["status"], "ops.task_status_pending")),
                 t(lang, PRIORITY_KEYS.get(r["priority"], "ops.priority_medium")),
                 r["due_at"], r["booking_id"]] for r in result["rows"]]
        lines.append(_rows_md(_h(lang, "search.table_task",
                                  "search.table_name", "search.table_status",
                                  "search.table_priority", "search.table_due",
                                  "search.table_booking"), rows, lang))
    elif name == "events":
        rows = [[r["number"], r["customer"], r["hall_id"], r["start_at"],
                 t(lang, BOOKING_STATUS_KEYS.get(r["status"], "booking.status_pending"))]
                for r in result["rows"]]
        lines.append(_rows_md(_h(lang, "search.table_booking",
                                  "search.table_customer", "search.table_hall",
                                  "search.table_start", "search.table_status"),
                              rows, lang))
    return "\n\n".join(lines)


def build_search_ui(db: DatabaseManager, auth: AuthService,
                    settings: AppSettings, session_token, lang_state) -> dict:
    """Build the Search tab. Returns label targets for the toggle."""
    reg = LabelRegistry()
    search = SearchService(db, settings)

    def _lang(token) -> str:
        return lang_for_token(db, auth, token)

    def employee(token):
        emp = auth.validate_session(token)
        if emp is None:
            raise gr.Error(t(_lang(token), "login.session_expired"))
        return emp

    with reg.tab("tab.search"):
        reg.markdown("search.heading")
        reg.markdown("search.global_hint")
        with gr.Row():
            g_term = reg.textbox("search.term")
            g_btn = reg.button("common.search", variant="primary")
        g_out = gr.Markdown("")

        reg.markdown("search.advanced_heading")
        with gr.Row():
            a_module = reg.dropdown_t(
                "search.module",
                [("search.customers", "customers"),
                 ("search.bookings", "bookings"),
                 ("search.contracts", "contracts"),
                 ("search.payments", "payments"),
                 ("search.staff", "staff"),
                 ("search.files", "files")],
                value="customers")
            a_term = reg.textbox("search.term")
            a_status = reg.textbox("search.status_optional")
        with gr.Row():
            a_hall = reg.number("search.hall_id", precision=0)
            a_from = reg.textbox("search.date_from_optional")
            a_to = reg.textbox("search.date_to_optional")
            a_page = reg.number("common.page", precision=0, value=1)
            a_btn = reg.button("common.search", variant="primary")
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
                    raise gr.Error(t(lang, "search.msg_unknown_module"))
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

    return reg.result()
