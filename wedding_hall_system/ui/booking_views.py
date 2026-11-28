"""Customers, quotations and bookings screens (Phase 3, presentation tier;
Phase 8: fully bilingual, SRS §16.3; FR-026 service changes, FR-029
scheduling advisory).

Thin UI only: parse input, call ONE service method, render the result.
No SQL and no business rules here. Every handler resolves the session
token to an employee and lets the service enforce permissions; a denied
action raises gr.Error with the service's message.

National IDs are masked in lists by the service unless the caller holds
``customer.view_sensitive`` — the UI never sees a full ID it may not show.
"""
from __future__ import annotations

from datetime import datetime

import gradio as gr

from ..config.settings import AppSettings, format_money
from ..data.database import DatabaseManager
from ..logic.auth_service import AuthService
from ..logic.availability_service import AvailabilityService
from ..logic.booking_service import BookingService
from ..logic.catalog_service import CatalogService
from ..logic.customer_service import CustomerService
from ..logic.hall_service import HallService
from ..logic.models import Employee, ServiceSelection
from ..logic.quotation_service import QuotationService
from ..utils.exceptions import ApplicationError
from ..utils.logging_config import get_logger
from ..utils.validators import egp_to_piasters, parse_iso_datetime
from .i18n import LabelRegistry, dir_wrap, format_advisory_reason, lang_for_token, t, translate_price_label
from .live_refresh import login_follow_up

logger = get_logger(__name__)

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
    "reversed": "booking.status_reversed",
}
INTER_TYPE_KEYS = {
    "call": "booking.inter_call", "inquiry": "booking.inter_inquiry",
    "quotation": "booking.inter_quotation", "booking": "booking.inter_booking",
    "cancellation": "booking.inter_cancellation",
    "payment": "booking.inter_payment", "contract": "booking.inter_contract",
    "note": "booking.inter_note",
}
PAY_METHOD_KEYS = {
    "cash": "booking.method_cash", "bank_transfer": "booking.method_bank_transfer",
    "visa_card": "booking.method_visa_card", "instapay": "booking.method_instapay",
    "other": "booking.method_other",
}
DUP_MATCH_KEYS = {
    "phone": "booking.dup_phone", "national_id": "booking.dup_national",
    "email": "booking.dup_email",
}


def _md_table(headers: list[str], rows: list[list[str]], lang: str) -> str:
    if not rows:
        return dir_wrap(lang, f"_{t(lang, 'common.no_records')}_")
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return dir_wrap(lang, "\n".join(out))


def _hold_countdown(booking, lang: str) -> str:
    """Remaining hold time for a Pending booking."""
    if booking.status != "pending":
        return "—"
    if booking.hold_expires_at is None:
        return t(lang, "booking.msg_hold_no")
    seconds = int((booking.hold_expires_at - datetime.now()).total_seconds())
    if seconds <= 0:
        return t(lang, "booking.msg_hold_expired")
    hours, rem = divmod(seconds, 3600)
    return t(lang, "booking.msg_hold_left").format(h=hours, m=rem // 60)


def _status(lang: str, status: str) -> str:
    return t(lang, BOOKING_STATUS_KEYS.get(status, "booking.status_pending"))


def build_booking_ui(db: DatabaseManager, auth: AuthService,
                     settings: AppSettings, session_token) -> dict:
    """Build the Customers / Quotations / Bookings tabs. Must be called
    inside a gr.Blocks context, after the login/main views exist.
    Returns the LabelRegistry result for the language toggle."""
    reg = LabelRegistry()
    customers = CustomerService(db, settings)
    quotations = QuotationService(db, settings)
    bookings = BookingService(db, settings)
    halls = HallService(db, settings)
    catalog = CatalogService(db, settings)
    availability = AvailabilityService(db, settings)

    def _lang(token) -> str:
        return lang_for_token(db, auth, token)

    def employee(token):
        emp = auth.validate_session(token)
        if emp is None:
            raise gr.Error(t(_lang(token), "login.session_expired"))
        return emp

    def _system_employee():
        row = db.query_one("SELECT id FROM employees WHERE username = 'owner'")
        return Employee(id=int(row["id"]), username="owner", full_name="Owner")

    def _hall_choices():
        # A-95: booking flows only offer halls that can actually be
        # booked — a deactivated hall is excluded here (availability
        # would refuse it anyway). Configuration tabs keep listing
        # inactive halls (catalog_views) so they can be reactivated.
        return [(f"{h.name} — {h.capacity}", h.id)
                for h in halls.list_halls(_system_employee(), include_inactive=False)]

    def _service_choices():
        return [(s.name, s.id) for s in catalog.list_services(_system_employee())]

    # Named choice builders (A-95): shared by the dropdown_data
    # lambdas below and the ChoiceRegistry, one helper per list.
    def _package_choices(lang: str):
        return [(t(lang, "booking.no_package"), None)] + [
            (p.name, p.id) for p in catalog.list_packages(_system_employee())]

    def _event_type_choices(lang: str):
        return [(t(lang, "booking.any"), None)] + [
            (et.name, et.id) for et in halls.list_event_types(_system_employee())]

    def _when(date: str, start: str, end: str):
        start_dt = parse_iso_datetime(f"{date}T{start}:00", "Start")
        end_dt = parse_iso_datetime(f"{date}T{end}:00", "End")
        return start_dt, end_dt

    # ============================================================== CUSTOMERS
    with reg.tab("tab.customers"):
        reg.markdown("booking.customers_heading")
        cust_list = gr.Markdown("")
        refresh_cust_btn = reg.button("booking.refresh_customers")
        cust_search = reg.textbox("booking.search")
        with gr.Row():
            cust_name = reg.textbox("booking.cust_name")
            cust_phone = reg.textbox("booking.cust_phone", placeholder="01012345678")
            cust_email = reg.textbox("booking.cust_email")
        with gr.Row():
            cust_address = reg.textbox("booking.cust_address")
            cust_national = reg.textbox("booking.cust_national")
            cust_notes = reg.textbox("booking.cust_notes")
        cust_force = reg.checkbox("booking.cust_force", value=False)
        cust_create_btn = reg.button("booking.create_customer", variant="primary")
        cust_dup = gr.Markdown("")
        reg.markdown("booking.profile_heading")
        with gr.Row():
            prof_id = reg.number("booking.profile_id", precision=0)
            prof_btn = reg.button("booking.show_profile")
        prof_out = gr.Markdown("")
        with gr.Row():
            inter_type = reg.dropdown_t(
                "booking.inter_type",
                [("booking.inter_call", "call"),
                 ("booking.inter_inquiry", "inquiry"),
                 ("booking.inter_quotation", "quotation"),
                 ("booking.inter_booking", "booking"),
                 ("booking.inter_cancellation", "cancellation"),
                 ("booking.inter_payment", "payment"),
                 ("booking.inter_contract", "contract"),
                 ("booking.inter_note", "note")],
                value="call")
            inter_summary = reg.textbox("booking.inter_summary")
            inter_btn = reg.button("booking.add_interaction")

        def _customers_md(emp, lang, search=""):
            # The service already masks the National ID unless the caller
            # holds customer.view_sensitive — the UI just displays it.
            rows = []
            for c in customers.list_customers(emp, search=search or ""):
                rows.append([c.id, c.full_name, c.phone, c.email, c.national_id])
            return dir_wrap(lang, _md_table(
                [t(lang, "booking.table_id"), t(lang, "booking.table_name"),
                 t(lang, "booking.table_phone"), t(lang, "booking.table_email"),
                 t(lang, "booking.table_national")], rows, lang))

        def do_refresh_cust(token, search):
            emp = employee(token)
            return _customers_md(emp, _lang(token), search)

        def do_create_cust(token, name, phone, email, address, national, notes, force):
            emp = employee(token)
            lang = _lang(token)
            try:
                created = customers.create_customer(
                    emp, full_name=name or "", phone=phone or "",
                    email=email or "", address=address or "",
                    national_id=national or "", notes=notes or "", force=bool(force))
            except ApplicationError as exc:
                dupes = getattr(exc, "duplicates", None)
                if dupes:
                    lines = [t(lang, "booking.msg_dup_heading")]
                    for d in dupes:
                        match = t(lang, DUP_MATCH_KEYS.get(d["match"], "booking.dup_phone"))
                        lines.append(t(lang, "booking.msg_dup_line").format(
                            name=d["full_name"], match=match))
                    lines.append(t(lang, "booking.msg_dup_hint"))
                    return _customers_md(emp, lang), dir_wrap(lang, "\n".join(lines))
                raise gr.Error(str(exc))
            gr.Info(t(lang, "booking.msg_customer_created").format(
                name=created.full_name, id=created.id))
            return _customers_md(emp, lang), ""

        def do_profile(token, cid):
            emp = employee(token)
            lang = _lang(token)
            if not cid:
                raise gr.Error(t(lang, "booking.msg_need_customer_id"))
            try:
                prof = customers.profile(emp, int(cid))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            c = prof["customer"]
            totals = prof["totals"]
            lines = [
                f"## {c.full_name}",
                f"**{t(lang, 'booking.profile_phone')}:** {c.phone}  \n"
                f"**{t(lang, 'booking.profile_email')}:** {c.email or '—'}  \n"
                f"**{t(lang, 'booking.profile_national')}:** {c.national_id or '—'}  \n"
                f"**{t(lang, 'booking.profile_address')}:** {c.address or '—'}",
                f"**{t(lang, 'booking.profile_bookings')}:** {totals['booking_count']}  \n"
                f"**{t(lang, 'booking.profile_contracted')}:** {format_money(totals['contracted'])}  \n"
                f"**{t(lang, 'booking.profile_collected')}:** {format_money(totals['collected'])}  \n"
                f"**{t(lang, 'booking.profile_outstanding')}:** {format_money(totals['outstanding'])}",
                f"### {t(lang, 'booking.profile_bookings')}",
                _md_table(
                    [t(lang, "booking.table_number"), t(lang, "booking.table_hall"),
                     t(lang, "booking.table_event"), t(lang, "booking.table_status"),
                     t(lang, "booking.table_total")],
                    [[b["number"], b["hall_id"], b["start_at"][:16],
                      _status(lang, b["status"]), format_money(b["total_amount"])]
                     for b in prof["bookings"]], lang),
                f"### {t(lang, 'booking.profile_interactions')}",
                _md_table(
                    [t(lang, "booking.table_type"), t(lang, "booking.table_summary")],
                    [[t(lang, INTER_TYPE_KEYS.get(i.type, "booking.inter_note")), i.summary]
                     for i in prof["interactions"]], lang),
            ]
            return dir_wrap(lang, "\n\n".join(lines))

        def do_add_interaction(token, cid, itype, summary):
            emp = employee(token)
            lang = _lang(token)
            if not cid:
                raise gr.Error(t(lang, "booking.msg_need_customer_id"))
            try:
                customers.add_interaction(emp, int(cid), itype, summary or "")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "booking.msg_interaction"))
            return do_profile(token, cid)

        refresh_cust_btn.click(do_refresh_cust, inputs=[session_token, cust_search],
                               outputs=[cust_list])
        cust_create_btn.click(do_create_cust, inputs=[session_token, cust_name,
                              cust_phone, cust_email, cust_address, cust_national,
                              cust_notes, cust_force], outputs=[cust_list, cust_dup])
        prof_btn.click(do_profile, inputs=[session_token, prof_id], outputs=[prof_out])
        inter_btn.click(do_add_interaction, inputs=[session_token, prof_id,
                        inter_type, inter_summary], outputs=[prof_out])

    # ============================================================== QUOTATIONS
    with reg.tab("tab.quotations"):
        reg.markdown("booking.quotations_heading")
        quot_list = gr.Markdown("")
        refresh_quot_btn = reg.button("booking.refresh_quotations")
        with gr.Row():
            q_cust = reg.number("booking.q_cust", precision=0)
            q_hall = reg.dropdown("booking.q_hall", choices=_hall_choices())
            q_pkg = reg.dropdown_data(
                "booking.q_pkg", _package_choices)
            q_type = reg.dropdown_data(
                "booking.q_type", _event_type_choices)
        with gr.Row():
            q_date = reg.textbox("booking.q_date", placeholder="2026-12-04")
            q_start = reg.textbox("booking.q_start", value="18:00")
            q_end = reg.textbox("booking.q_end", value="23:00")
            q_guests = reg.number("booking.q_guests", precision=0, value=100)
        q_services = reg.checkbox_group("booking.q_services", choices=_service_choices())
        q_create_btn = reg.button("booking.create_quotation", variant="primary")
        q_out = gr.Markdown("")
        with gr.Row():
            q_id = reg.number("booking.q_id", precision=0)
            q_send_btn = reg.button("booking.q_send")
            q_accept_btn = reg.button("booking.q_accept")
            q_convert_btn = reg.button("booking.q_convert", variant="primary")

        def _quotations_md(emp, lang):
            rows = []
            for q in quotations.list_quotations(emp):
                rows.append([q.id, q.number, q.customer_id, q.hall_id,
                             q.start_at.strftime("%Y-%m-%d %H:%M") if q.start_at else "",
                             q.guest_count, format_money(q.total_amount),
                             _status(lang, q.status)])
            return dir_wrap(lang, _md_table(
                [t(lang, "booking.table_id"), t(lang, "booking.table_number"),
                 t(lang, "booking.table_customer"), t(lang, "booking.table_hall"),
                 t(lang, "booking.table_event"), t(lang, "booking.table_guests"),
                 t(lang, "booking.table_total"), t(lang, "booking.table_status")],
                rows, lang))

        def do_refresh_quot(token):
            emp = employee(token)
            return _quotations_md(emp, _lang(token))

        def do_create_quot(token, cid, hall, pkg, etype, date, start, end,
                           guests, service_ids):
            emp = employee(token)
            lang = _lang(token)
            try:
                start_dt, end_dt = _when(date, start, end)
                quote = quotations.create_quotation(
                    emp, customer_id=int(cid), hall_id=int(hall),
                    package_id=int(pkg) if pkg else None,
                    event_type_id=int(etype) if etype else None,
                    start_at=start_dt, end_at=end_dt, guests=int(guests),
                    service_selections=[ServiceSelection(service_id=int(sid))
                                        for sid in service_ids])
                items = quotations.list_items(emp, quote.id)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            valid = (quote.valid_until.strftime("%Y-%m-%d %H:%M")
                     if quote.valid_until else "—")
            lines = [t(lang, "booking.msg_quot_created").format(
                        number=quote.number, valid=valid),
                     f"| {t(lang, 'booking.table_item')} | {t(lang, 'booking.table_amount')} |",
                     "| --- | ---:|"]
            for item in items:
                lines.append(f"| {translate_price_label(lang, item.label)} | {format_money(item.amount)} |")
            lines.append(f"| **{t(lang, 'booking.table_total')}** | **{format_money(quote.total_amount)}** |")
            gr.Info(t(lang, "booking.msg_quot_created_info").format(number=quote.number))
            return _quotations_md(emp, lang), dir_wrap(lang, "\n".join(lines))

        def _quot_action(token, qid, action):
            emp = employee(token)
            lang = _lang(token)
            if not qid:
                raise gr.Error(t(lang, "booking.msg_need_quotation_id"))
            try:
                quote = quotations.get_quotation(emp, int(qid))
                if quote is None:
                    raise gr.Error(t(lang, "booking.msg_quot_not_exist").format(id=int(qid)))
                if action == "send":
                    quote = quotations.send_quotation(emp, int(qid))
                    gr.Info(t(lang, "booking.msg_quot_sent").format(number=quote.number))
                    return _quotations_md(emp, lang), dir_wrap(lang, t(lang, "booking.msg_quot_sent_out").format(number=quote.number))
                if action == "accept":
                    quote = quotations.accept_quotation(emp, int(qid))
                    gr.Info(t(lang, "booking.msg_quot_accepted").format(number=quote.number))
                    return _quotations_md(emp, lang), dir_wrap(lang, t(lang, "booking.msg_quot_accepted_out").format(number=quote.number))
                # convert: accept first (if not already), then convert.
                if quote.status in ("draft", "sent"):
                    quote = quotations.accept_quotation(emp, int(qid))
                booking = bookings.create_booking(
                    emp, customer_id=quote.customer_id, hall_id=quote.hall_id,
                    event_type_id=quote.event_type_id, package_id=quote.package_id,
                    start_at=quote.start_at, end_at=quote.end_at,
                    guests=quote.guest_count, quotation_id=quote.id)
                gr.Info(t(lang, "booking.msg_booking_created").format(number=booking.number))
                return _quotations_md(emp, lang), dir_wrap(lang, t(lang, "booking.msg_convert_out").format(
                    number=booking.number,
                    hold=booking.hold_expires_at.strftime("%Y-%m-%d %H:%M") if booking.hold_expires_at else "—",
                    deposit=format_money(booking.deposit_required)))
            except ApplicationError as exc:
                raise gr.Error(str(exc))

        refresh_quot_btn.click(do_refresh_quot, inputs=[session_token], outputs=[quot_list])
        q_create_btn.click(do_create_quot, inputs=[session_token, q_cust, q_hall,
                           q_pkg, q_type, q_date, q_start, q_end, q_guests,
                           q_services], outputs=[quot_list, q_out])
        q_send_btn.click(lambda t, i: _quot_action(t, i, "send"),
                         inputs=[session_token, q_id], outputs=[quot_list, q_out])
        q_accept_btn.click(lambda t, i: _quot_action(t, i, "accept"),
                           inputs=[session_token, q_id], outputs=[quot_list, q_out])
        q_convert_btn.click(lambda t, i: _quot_action(t, i, "convert"),
                            inputs=[session_token, q_id], outputs=[quot_list, q_out])

    # ============================================================== BOOKINGS
    with reg.tab("tab.bookings"):
        reg.markdown("booking.bookings_heading")
        book_list = gr.Markdown("")
        refresh_book_btn = reg.button("booking.refresh_bookings")
        with gr.Row():
            b_cust = reg.number("booking.b_cust", precision=0)
            b_hall = reg.dropdown("booking.b_hall", choices=_hall_choices())
            b_pkg = reg.dropdown_data(
                "booking.b_pkg", _package_choices)
            b_type = reg.dropdown_data(
                "booking.b_type", _event_type_choices)
        with gr.Row():
            b_date = reg.textbox("booking.b_date", placeholder="2026-12-04")
            b_start = reg.textbox("booking.b_start", value="18:00")
            b_end = reg.textbox("booking.b_end", value="23:00")
            b_guests = reg.number("booking.b_guests", precision=0, value=100)
        b_services = reg.checkbox_group("booking.b_services", choices=_service_choices())
        with gr.Row():
            b_notes = reg.textbox("booking.b_notes")
            b_contact = reg.textbox("booking.b_contact")
        b_create_btn = reg.button("booking.create_booking", variant="primary")
        b_out = gr.Markdown("")
        reg.markdown("booking.detail_heading")
        with gr.Row():
            b_id = reg.number("booking.b_id", precision=0)
            b_detail_btn = reg.button("booking.show_booking")
        b_detail = gr.Markdown("")
        reg.markdown("booking.pay_heading")
        with gr.Row():
            pay_amount = reg.number("booking.pay_amount")
            pay_method = reg.dropdown_t(
                "booking.pay_method",
                [("booking.method_cash", "cash"),
                 ("booking.method_bank_transfer", "bank_transfer"),
                 ("booking.method_visa_card", "visa_card"),
                 ("booking.method_instapay", "instapay"),
                 ("booking.method_other", "other")],
                value="cash")
            pay_ref = reg.textbox("booking.pay_ref")
            pay_initiated = reg.textbox("booking.pay_initiated")
            pay_btn = reg.button("booking.record_payment", variant="primary")
        reg.markdown("booking.lifecycle_heading")
        with gr.Row():
            inprog_btn = reg.button("booking.in_progress")
            complete_btn = reg.button("booking.completed")
            noshow_btn = reg.button("booking.no_show")
        reg.markdown("booking.notes_heading")
        with gr.Row():
            note_text = reg.textbox("booking.note_text")
            note_btn = reg.button("booking.update_notes")
            cancel_reason = reg.textbox("booking.cancel_reason")
            cancel_btn = reg.button("booking.cancel_booking")
        reg.markdown("booking.reschedule_heading")
        with gr.Row():
            rs_date = reg.textbox("booking.rs_date", placeholder="2026-12-11")
            rs_start = reg.textbox("booking.rs_start", value="18:00")
            rs_end = reg.textbox("booking.rs_end", value="23:00")
            rs_hall = reg.dropdown("booking.rs_hall", choices=_hall_choices())
            rs_reason = reg.textbox("booking.rs_reason")
            rs_btn = reg.button("booking.reschedule_booking")
        with gr.Row():
            tr_customer = reg.number("booking.tr_customer", precision=0)
            tr_reason = reg.textbox("booking.tr_reason")
            tr_btn = reg.button("booking.transfer_booking")
        reg.markdown("booking.change_heading")
        with gr.Row():
            ch_services = reg.checkbox_group("booking.change_services",
                                             choices=_service_choices())
            ch_reason = reg.textbox("booking.change_reason")
        with gr.Row():
            ch_load_btn = reg.button("booking.load_current")
            ch_btn = reg.button("booking.change_btn", variant="primary")
        reg.markdown("booking.advisory_heading")
        reg.markdown("booking.advisory_help")
        with gr.Row():
            adv_hall = reg.dropdown("booking.adv_hall", choices=_hall_choices())
            adv_pkg = reg.dropdown_data(
                "booking.adv_package", _package_choices)
            adv_date = reg.textbox("booking.adv_date", placeholder="2026-12-04")
            adv_start = reg.textbox("booking.adv_start", value="18:00")
            adv_end = reg.textbox("booking.adv_end", value="23:00")
            adv_guests = reg.number("booking.adv_guests", precision=0, value=100)
        adv_btn = reg.button("booking.find_alternatives", variant="primary")
        adv_out = gr.Markdown("")

        def _bookings_md(emp, lang):
            rows = []
            for b in bookings.list_bookings(emp):
                rows.append([b.id, b.number, b.customer_id, b.hall_id,
                             b.start_at.strftime("%Y-%m-%d %H:%M") if b.start_at else "",
                             _status(lang, b.status), format_money(b.total_amount),
                             format_money(b.deposit_paid), _hold_countdown(b, lang)])
            return dir_wrap(lang, _md_table(
                [t(lang, "booking.table_id"), t(lang, "booking.table_number"),
                 t(lang, "booking.table_customer"), t(lang, "booking.table_hall"),
                 t(lang, "booking.table_event"), t(lang, "booking.table_status"),
                 t(lang, "booking.table_total"), t(lang, "booking.table_deposit"),
                 t(lang, "booking.table_hold")], rows, lang))

        def do_refresh_books(token):
            emp = employee(token)
            return _bookings_md(emp, _lang(token))

        def do_create_book(token, cid, hall, pkg, etype, date, start, end,
                           guests, service_ids, notes, contact):
            emp = employee(token)
            lang = _lang(token)
            try:
                start_dt, end_dt = _when(date, start, end)
                booking = bookings.create_booking(
                    emp, customer_id=int(cid), hall_id=int(hall),
                    package_id=int(pkg) if pkg else None,
                    event_type_id=int(etype) if etype else None,
                    start_at=start_dt, end_at=end_dt, guests=int(guests),
                    service_selections=[ServiceSelection(service_id=int(sid))
                                        for sid in service_ids],
                    notes=notes or "", additional_contact_phone=contact or "")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "booking.msg_booking_created").format(number=booking.number))
            return _bookings_md(emp, lang), dir_wrap(lang, t(lang, "booking.msg_book_created_out").format(
                number=booking.number, total=format_money(booking.total_amount),
                deposit=format_money(booking.deposit_required),
                hold=booking.hold_expires_at.strftime("%Y-%m-%d %H:%M") if booking.hold_expires_at else "—"))

        def do_detail(token, bid):
            emp = employee(token)
            lang = _lang(token)
            if not bid:
                raise gr.Error(t(lang, "booking.msg_need_booking_id"))
            try:
                booking = bookings.get_booking(emp, int(bid))
                if booking is None:
                    raise gr.Error(t(lang, "booking.msg_booking_not_exist").format(id=int(bid)))
                items = bookings.list_price_items(emp, int(bid))
                payments = bookings.list_payments(emp, int(bid))
                timeline = bookings.timeline(emp, int(bid))
                changes = bookings.list_service_changes(emp, int(bid))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            lines = [
                f"## {t(lang, 'booking.table_number')} {booking.number} — {_status(lang, booking.status)}",
                f"**{t(lang, 'booking.detail_customer')}:** {booking.customer_id}  \n"
                f"**{t(lang, 'booking.detail_hall')}:** {booking.hall_id}  \n"
                f"**{t(lang, 'booking.detail_event')}:** "
                f"{booking.start_at.strftime('%Y-%m-%d %H:%M') if booking.start_at else '—'}"
                f" – {booking.end_at.strftime('%H:%M') if booking.end_at else '—'}  \n"
                f"**{t(lang, 'booking.detail_guests')}:** {booking.guest_count}  \n"
                f"**{t(lang, 'booking.detail_total')}:** {format_money(booking.total_amount)}  \n"
                f"**{t(lang, 'booking.detail_deposit')}:** {format_money(booking.deposit_paid)} "
                f"of {format_money(booking.deposit_required)}  \n"
                f"**{t(lang, 'booking.detail_hold')}:** {_hold_countdown(booking, lang)}  \n"
                f"**{t(lang, 'booking.detail_notes')}:** {booking.notes or '—'}",
                f"### {t(lang, 'booking.detail_price_items')}",
                _md_table([t(lang, "booking.table_item"), t(lang, "booking.table_amount")],
                          [[translate_price_label(lang, i["label"]), format_money(i["amount"])]
                           for i in items], lang),
                f"### {t(lang, 'booking.detail_payments')}",
                _md_table([t(lang, "booking.table_number"), t(lang, "booking.table_amount"),
                           t(lang, "booking.table_method"), t(lang, "booking.table_status")],
                          [[p.number, format_money(p.amount),
                            t(lang, PAY_METHOD_KEYS.get(p.method, "booking.method_other")),
                            _status(lang, p.status)] for p in payments], lang),
                f"### {t(lang, 'booking.detail_timeline')}",
                _md_table([t(lang, "booking.table_time"), t(lang, "booking.table_action"),
                           t(lang, "booking.table_details")],
                          [[e["created_at"], e["action"], e["reason"]] for e in timeline], lang),
            ]
            if changes:
                lines.append(f"### {t(lang, 'booking.detail_changes')}")
                lines.append(_md_table(
                    [t(lang, "booking.table_time"), t(lang, "booking.table_by"),
                     t(lang, "booking.table_reason"), t(lang, "booking.table_old"),
                     t(lang, "booking.table_new"), t(lang, "booking.table_delta")],
                    [[c["created_at"], c["employee_id"] or "—", c["reason"] or "—",
                      format_money(c["old_total"]), format_money(c["new_total"]),
                      format_money(c["delta"])] for c in changes], lang))
            return dir_wrap(lang, "\n\n".join(lines))

        def do_pay(token, bid, amount, method, ref, initiated):
            emp = employee(token)
            lang = _lang(token)
            if not bid:
                raise gr.Error(t(lang, "booking.msg_need_booking_id"))
            try:
                amount_piasters = egp_to_piasters(amount, "Amount (EGP)")
                initiated_dt = (parse_iso_datetime(initiated, "Initiated at")
                                if str(initiated or "").strip() else None)
                payment = bookings.record_payment(
                    emp, int(bid), amount_piasters, method,
                    reference=ref or "", initiated_at=initiated_dt)
                booking = bookings.get_booking(emp, int(bid))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            status = booking.status if booking else "?"
            gr.Info(t(lang, "booking.msg_payment_recorded").format(
                number=payment.number, status=_status(lang, status)))
            return _bookings_md(emp, lang), dir_wrap(lang, t(lang, "booking.msg_pay_out").format(
                number=payment.number, amount=format_money(payment.amount),
                method=t(lang, PAY_METHOD_KEYS.get(method, "booking.method_other")),
                status=_status(lang, status)))

        def do_notes(token, bid, notes):
            emp = employee(token)
            lang = _lang(token)
            if not bid:
                raise gr.Error(t(lang, "booking.msg_need_booking_id"))
            try:
                bookings.update_notes(emp, int(bid), notes or "")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "booking.msg_notes_updated"))
            return _bookings_md(emp, lang), dir_wrap(lang, t(lang, "booking.msg_notes_out"))

        def do_cancel(token, bid, reason):
            emp = employee(token)
            lang = _lang(token)
            if not bid:
                raise gr.Error(t(lang, "booking.msg_need_booking_id"))
            try:
                booking = bookings.cancel_booking(emp, int(bid), reason or "")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "booking.msg_booking_cancelled").format(number=booking.number))
            return _bookings_md(emp, lang), dir_wrap(lang, t(lang, "booking.msg_cancel_out").format(number=booking.number))

        def do_reschedule(token, bid, date, start, end, hall, reason):
            emp = employee(token)
            lang = _lang(token)
            if not bid:
                raise gr.Error(t(lang, "booking.msg_need_booking_id"))
            try:
                start_dt, end_dt = _when(date, start, end)
                booking = bookings.reschedule_booking(
                    emp, int(bid), new_start_at=start_dt, new_end_at=end_dt,
                    new_hall_id=int(hall) if hall else None,
                    reason=reason or "")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "booking.msg_rescheduled").format(number=booking.number))
            return _bookings_md(emp, lang), dir_wrap(lang, t(lang, "booking.msg_reschedule_out").format(
                number=booking.number,
                when=booking.start_at.strftime("%Y-%m-%d %H:%M") if booking.start_at else "—",
                total=format_money(booking.total_amount)))

        def do_transfer(token, bid, new_cid, reason):
            emp = employee(token)
            lang = _lang(token)
            if not bid:
                raise gr.Error(t(lang, "booking.msg_need_booking_id"))
            if not new_cid:
                raise gr.Error(t(lang, "booking.msg_need_new_customer"))
            try:
                booking = bookings.transfer_booking(
                    emp, int(bid), new_customer_id=int(new_cid),
                    reason=reason or "")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "booking.msg_transferred").format(number=booking.number))
            return _bookings_md(emp, lang), dir_wrap(lang, t(lang, "booking.msg_transfer_out").format(
                number=booking.number, customer=booking.customer_id))

        def _lifecycle(token, bid, action):
            emp = employee(token)
            lang = _lang(token)
            if not bid:
                raise gr.Error(t(lang, "booking.msg_need_booking_id"))
            try:
                if action == "in_progress":
                    booking = bookings.mark_in_progress(emp, int(bid))
                elif action == "completed":
                    booking = bookings.mark_completed(emp, int(bid))
                else:
                    booking = bookings.mark_no_show(emp, int(bid))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "booking.msg_lifecycle").format(
                number=booking.number, status=_status(lang, booking.status)))
            return _bookings_md(emp, lang), dir_wrap(lang, t(lang, "booking.msg_lifecycle_out").format(
                number=booking.number, status=_status(lang, booking.status)))

        def do_load_current(token, bid):
            emp = employee(token)
            lang = _lang(token)
            if not bid:
                raise gr.Error(t(lang, "booking.msg_need_booking_id"))
            try:
                current = bookings.list_booking_services(emp, int(bid))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return gr.update(value=[int(s["service_id"]) for s in current])

        def do_change_services(token, bid, service_ids, reason):
            emp = employee(token)
            lang = _lang(token)
            if not bid:
                raise gr.Error(t(lang, "booking.msg_need_booking_id"))
            try:
                result = bookings.change_services(
                    emp, int(bid),
                    [ServiceSelection(service_id=int(sid)) for sid in service_ids],
                    reason=reason or "")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            booking = result["booking"]
            gr.Info(t(lang, "booking.msg_services_changed").format(
                number=booking.number,
                old=format_money(result["old_total"]),
                new=format_money(result["new_total"])))
            lines = [t(lang, "booking.msg_change_out").format(
                number=booking.number,
                old=format_money(result["old_total"]),
                new=format_money(result["new_total"]),
                delta=format_money(result["delta"]))]
            if result["delta"] > 0 and result["due_at"]:
                lines.append(t(lang, "booking.msg_change_due").format(
                    due=result["due_at"][:16], amount=format_money(result["delta"])))
            if result["refund_id"]:
                lines.append(t(lang, "booking.msg_change_refund").format(
                    id=result["refund_id"],
                    amount=format_money(-result["delta"])))
            if result["contract_action"] == "new_version":
                lines.append(t(lang, "booking.msg_change_contract"))
            return _bookings_md(emp, lang), dir_wrap(lang, "\n\n".join(lines))

        def do_advisory(token, hall, pkg, date, start, end, guests):
            emp = employee(token)
            lang = _lang(token)
            try:
                start_dt, end_dt = _when(date, start, end)
                suggestions = availability.suggest_alternatives(
                    emp, hall_id=int(hall), start_at=start_dt, end_at=end_dt,
                    guests=int(guests), package_id=int(pkg) if pkg else None,
                    lang=lang)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            if not suggestions:
                return dir_wrap(lang, t(lang, "booking.advisory_none"))
            lines = [f"### {t(lang, 'advisory.title')}"]
            for s in suggestions:
                lines.append(
                    f"- **{s.hall_name}** — "
                    f"{s.start_at.strftime('%Y-%m-%d %H:%M')} – "
                    f"{s.end_at.strftime('%H:%M')} — "
                    f"{format_advisory_reason(lang, s)}")
            return dir_wrap(lang, "\n".join(lines))

        refresh_book_btn.click(do_refresh_books, inputs=[session_token], outputs=[book_list])
        b_create_btn.click(do_create_book, inputs=[session_token, b_cust, b_hall,
                           b_pkg, b_type, b_date, b_start, b_end, b_guests,
                           b_services, b_notes, b_contact],
                           outputs=[book_list, b_out])
        b_detail_btn.click(do_detail, inputs=[session_token, b_id], outputs=[b_detail])
        pay_btn.click(do_pay, inputs=[session_token, b_id, pay_amount, pay_method,
                      pay_ref, pay_initiated], outputs=[book_list, b_out])
        note_btn.click(do_notes, inputs=[session_token, b_id, note_text],
                       outputs=[book_list, b_out])
        cancel_btn.click(do_cancel, inputs=[session_token, b_id, cancel_reason],
                         outputs=[book_list, b_out])
        rs_btn.click(do_reschedule, inputs=[session_token, b_id, rs_date,
                                            rs_start, rs_end, rs_hall, rs_reason],
                     outputs=[book_list, b_out])
        tr_btn.click(do_transfer, inputs=[session_token, b_id, tr_customer,
                                          tr_reason],
                     outputs=[book_list, b_out])
        inprog_btn.click(lambda t, i: _lifecycle(t, i, "in_progress"),
                         inputs=[session_token, b_id], outputs=[book_list, b_out])
        complete_btn.click(lambda t, i: _lifecycle(t, i, "completed"),
                           inputs=[session_token, b_id], outputs=[book_list, b_out])
        noshow_btn.click(lambda t, i: _lifecycle(t, i, "no_show"),
                         inputs=[session_token, b_id], outputs=[book_list, b_out])
        ch_load_btn.click(do_load_current, inputs=[session_token, b_id],
                          outputs=[ch_services])
        ch_btn.click(do_change_services, inputs=[session_token, b_id, ch_services,
                                                 ch_reason],
                     outputs=[book_list, b_out])
        adv_btn.click(do_advisory, inputs=[session_token, adv_hall, adv_pkg,
                                            adv_date, adv_start, adv_end, adv_guests],
                      outputs=[adv_out])

    result = reg.result()
    # A-94: populate the customers/quotations/bookings lists right
    # after sign-in (session/permission-tolerant wrappers, see
    # ui.live_refresh). The customers refresh searches with an empty
    # term — the full list, as the refresh button does.
    result["on_login"] = [
        (login_follow_up(auth, lambda token: do_refresh_cust(token, ""),
                         "do_refresh_cust_on_login"), [cust_list]),
        (login_follow_up(auth, do_refresh_quot,
                         "do_refresh_quot_on_login"), [quot_list]),
        (login_follow_up(auth, do_refresh_books,
                         "do_refresh_books_on_login"), [book_list]),
    ]
    # A-95: every dropdown/checkbox whose options come from the
    # database, with the one helper that rebuilds its choices.
    result["choice_entries"] = [
        (q_hall, lambda lang: _hall_choices()),
        (q_pkg, _package_choices),
        (q_type, _event_type_choices),
        (q_services, lambda lang: _service_choices()),
        (b_hall, lambda lang: _hall_choices()),
        (b_pkg, _package_choices),
        (b_type, _event_type_choices),
        (b_services, lambda lang: _service_choices()),
        (rs_hall, lambda lang: _hall_choices()),
        (adv_hall, lambda lang: _hall_choices()),
        (adv_pkg, _package_choices),
        (ch_services, lambda lang: _service_choices()),
    ]
    return result
