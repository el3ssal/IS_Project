"""Customers, quotations and bookings screens (Phase 3, presentation tier).

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

logger = get_logger(__name__)


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "_No records yet._"
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def _hold_countdown(booking) -> str:
    """Remaining hold time for a Pending booking, e.g. '31h 12m left'."""
    if booking.status != "pending":
        return "—"
    if booking.hold_expires_at is None:
        return "no hold set"
    seconds = int((booking.hold_expires_at - datetime.now()).total_seconds())
    if seconds <= 0:
        return "hold expired"
    hours, rem = divmod(seconds, 3600)
    return f"{hours}h {rem // 60}m left"


def build_booking_ui(db: DatabaseManager, auth: AuthService,
                     settings: AppSettings, session_token) -> None:
    """Build the Customers / Quotations / Bookings tabs. Must be called
    inside a gr.Blocks context, after the login/main views exist."""
    customers = CustomerService(db, settings)
    quotations = QuotationService(db, settings)
    bookings = BookingService(db, settings)
    halls = HallService(db, settings)
    catalog = CatalogService(db, settings)
    availability = AvailabilityService(db, settings)

    def employee(token):
        emp = auth.validate_session(token)
        if emp is None:
            raise gr.Error("Your session has expired. Please log in again.")
        return emp

    def _system_employee():
        row = db.query_one("SELECT id FROM employees WHERE username = 'owner'")
        return Employee(id=int(row["id"]), username="owner", full_name="Owner")

    def _hall_choices():
        return [(f"{h.name} (cap {h.capacity})", h.id)
                for h in halls.list_halls(_system_employee(), include_inactive=True)]

    def _package_choices():
        return [("No package", None)] + [
            (p.name, p.id) for p in catalog.list_packages(_system_employee())]

    def _type_choices():
        return [("Any", None)] + [
            (t.name, t.id) for t in halls.list_event_types(_system_employee())]

    def _service_choices():
        return [(s.name, s.id) for s in catalog.list_services(_system_employee())]

    def _when(date: str, start: str, end: str):
        start_dt = parse_iso_datetime(f"{date}T{start}:00", "Start")
        end_dt = parse_iso_datetime(f"{date}T{end}:00", "End")
        return start_dt, end_dt

    # ============================================================== CUSTOMERS
    with gr.Tab("Customers"):
        gr.Markdown("### Customers — National IDs are masked unless your role "
                    "may see them. Example customers are marked **(example)**.")
        cust_list = gr.Markdown("")
        refresh_cust_btn = gr.Button("Refresh customers")
        cust_search = gr.Textbox(label="Search (name / phone / email)")
        with gr.Row():
            cust_name = gr.Textbox(label="Full name")
            cust_phone = gr.Textbox(label="Primary phone", placeholder="01012345678")
            cust_email = gr.Textbox(label="Email")
        with gr.Row():
            cust_address = gr.Textbox(label="Address")
            cust_national = gr.Textbox(label="National ID (sensitive)")
            cust_notes = gr.Textbox(label="Internal notes")
        cust_force = gr.Checkbox(
            label="Create anyway — I reviewed the possible duplicate warning",
            value=False)
        cust_create_btn = gr.Button("Create customer", variant="primary")
        cust_dup = gr.Markdown("")
        gr.Markdown("#### Customer profile")
        with gr.Row():
            prof_id = gr.Number(label="Customer ID", precision=0)
            prof_btn = gr.Button("Show profile")
        prof_out = gr.Markdown("")
        with gr.Row():
            inter_type = gr.Dropdown(
                ["call", "inquiry", "quotation", "booking", "cancellation",
                 "payment", "contract", "note"], value="call", label="Interaction type")
            inter_summary = gr.Textbox(label="Interaction summary")
            inter_btn = gr.Button("Add interaction")

        def _customers_md(emp, search=""):
            # The service already masks the National ID unless the caller
            # holds customer.view_sensitive — the UI just displays it.
            rows = []
            for c in customers.list_customers(emp, search=search or ""):
                rows.append([c.id, c.full_name, c.phone, c.email, c.national_id])
            return _md_table(["ID", "Name", "Phone", "Email", "National ID"], rows)

        def do_refresh_cust(token, search):
            return _customers_md(employee(token), search)

        def do_create_cust(token, name, phone, email, address, national, notes, force):
            emp = employee(token)
            try:
                created = customers.create_customer(
                    emp, full_name=name or "", phone=phone or "",
                    email=email or "", address=address or "",
                    national_id=national or "", notes=notes or "", force=bool(force))
            except ApplicationError as exc:
                dupes = getattr(exc, "duplicates", None)
                if dupes:
                    lines = ["**Possible/confirmed duplicate(s):**"]
                    for d in dupes:
                        lines.append(f"- {d['full_name']} (same {d['match']})")
                    lines.append("Tick the checkbox to create anyway "
                                 "(possible duplicates only).")
                    return _customers_md(emp), "\n".join(lines)
                raise gr.Error(str(exc))
            gr.Info(f"Customer '{created.full_name}' created (ID {created.id}).")
            return _customers_md(emp), ""

        def do_profile(token, cid):
            emp = employee(token)
            if not cid:
                raise gr.Error("Enter a customer ID first.")
            try:
                prof = customers.profile(emp, int(cid))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            c = prof["customer"]
            t = prof["totals"]
            lines = [
                f"## {c.full_name}",
                f"**Phone:** {c.phone}  \n**Email:** {c.email or '—'}  \n"
                f"**National ID:** {c.national_id or '—'}  \n**Address:** {c.address or '—'}",
                f"**Bookings:** {t['booking_count']}  \n"
                f"**Contracted:** {format_money(t['contracted'])}  \n"
                f"**Collected:** {format_money(t['collected'])}  \n"
                f"**Outstanding:** {format_money(t['outstanding'])}",
            ]
            lines.append("### Bookings")
            lines.append(_md_table(
                ["Number", "Hall", "Date", "Status", "Total"],
                [[b["number"], b["hall_id"], b["start_at"][:16], b["status"],
                  format_money(b["total_amount"])] for b in prof["bookings"]]))
            lines.append("### Interactions")
            lines.append(_md_table(
                ["Type", "Summary"],
                [[i.type, i.summary] for i in prof["interactions"]]))
            return "\n\n".join(lines)

        def do_add_interaction(token, cid, itype, summary):
            emp = employee(token)
            if not cid:
                raise gr.Error("Enter a customer ID first.")
            try:
                customers.add_interaction(emp, int(cid), itype, summary or "")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info("Interaction recorded.")
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
    with gr.Tab("Quotations"):
        gr.Markdown("### Quotations — check availability but reserve nothing. "
                    "An accepted quotation converts to a Pending booking.")
        quot_list = gr.Markdown("")
        refresh_quot_btn = gr.Button("Refresh quotations")
        with gr.Row():
            q_cust = gr.Number(label="Customer ID", precision=0)
            q_hall = gr.Dropdown(choices=_hall_choices(), label="Hall")
            q_pkg = gr.Dropdown(choices=_package_choices(), label="Package")
            q_type = gr.Dropdown(choices=_type_choices(), label="Event type")
        with gr.Row():
            q_date = gr.Textbox(label="Event date (YYYY-MM-DD)", placeholder="2026-12-04")
            q_start = gr.Textbox(label="Start time (HH:MM)", value="18:00")
            q_end = gr.Textbox(label="End time (HH:MM)", value="23:00")
            q_guests = gr.Number(label="Guests", precision=0, value=100)
        q_services = gr.CheckboxGroup(choices=_service_choices(),
                                      label="Additional services (quantity 1)")
        q_create_btn = gr.Button("Create quotation", variant="primary")
        q_out = gr.Markdown("")
        with gr.Row():
            q_id = gr.Number(label="Quotation ID", precision=0)
            q_send_btn = gr.Button("Send")
            q_accept_btn = gr.Button("Accept")
            q_convert_btn = gr.Button("Accept & convert to booking", variant="primary")

        def _quotations_md(emp):
            rows = []
            for q in quotations.list_quotations(emp):
                rows.append([q.id, q.number, q.customer_id, q.hall_id,
                             q.start_at.strftime("%Y-%m-%d %H:%M") if q.start_at else "",
                             q.guest_count, format_money(q.total_amount), q.status])
            return _md_table(["ID", "Number", "Customer", "Hall", "Event",
                              "Guests", "Total", "Status"], rows)

        def do_refresh_quot(token):
            return _quotations_md(employee(token))

        def do_create_quot(token, cid, hall, pkg, etype, date, start, end,
                           guests, service_ids):
            emp = employee(token)
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
            lines = [f"**Quotation {quote.number} created** (valid until "
                     f"{quote.valid_until.strftime('%Y-%m-%d %H:%M') if quote.valid_until else '—'}).",
                     "| Item | Amount |", "| --- | ---:|"]
            for item in items:
                lines.append(f"| {item.label} | {format_money(item.amount)} |")
            lines.append(f"| **Total** | **{format_money(quote.total_amount)}** |")
            gr.Info(f"Quotation {quote.number} created — time NOT reserved.")
            return _quotations_md(emp), "\n".join(lines)

        def _quot_action(token, qid, action):
            emp = employee(token)
            if not qid:
                raise gr.Error("Enter a quotation ID first.")
            try:
                quote = quotations.get_quotation(emp, int(qid))
                if quote is None:
                    raise gr.Error(f"Quotation #{int(qid)} does not exist.")
                if action == "send":
                    quote = quotations.send_quotation(emp, int(qid))
                    gr.Info(f"Quotation {quote.number} sent.")
                    return _quotations_md(emp), f"Quotation {quote.number} is now Sent."
                if action == "accept":
                    quote = quotations.accept_quotation(emp, int(qid))
                    gr.Info(f"Quotation {quote.number} accepted.")
                    return _quotations_md(emp), f"Quotation {quote.number} is now Accepted."
                # convert: accept first (if not already), then convert.
                if quote.status in ("draft", "sent"):
                    quote = quotations.accept_quotation(emp, int(qid))
                booking = bookings.create_booking(
                    emp, customer_id=quote.customer_id, hall_id=quote.hall_id,
                    event_type_id=quote.event_type_id, package_id=quote.package_id,
                    start_at=quote.start_at, end_at=quote.end_at,
                    guests=quote.guest_count, quotation_id=quote.id)
                gr.Info(f"Booking {booking.number} created (Pending, hold started).")
                return _quotations_md(emp), (
                    f"**Booking {booking.number}** created — Pending, "
                    f"hold until {booking.hold_expires_at.strftime('%Y-%m-%d %H:%M') if booking.hold_expires_at else '—'}, "
                    f"deposit required {format_money(booking.deposit_required)}.")
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
    with gr.Tab("Bookings"):
        gr.Markdown("### Bookings — Pending holds count down; a valid deposit "
                    "confirms automatically. No manual override of conflicts.")
        book_list = gr.Markdown("")
        refresh_book_btn = gr.Button("Refresh bookings")
        with gr.Row():
            b_cust = gr.Number(label="Customer ID", precision=0)
            b_hall = gr.Dropdown(choices=_hall_choices(), label="Hall")
            b_pkg = gr.Dropdown(choices=_package_choices(), label="Package")
            b_type = gr.Dropdown(choices=_type_choices(), label="Event type")
        with gr.Row():
            b_date = gr.Textbox(label="Event date (YYYY-MM-DD)", placeholder="2026-12-04")
            b_start = gr.Textbox(label="Start time (HH:MM)", value="18:00")
            b_end = gr.Textbox(label="End time (HH:MM)", value="23:00")
            b_guests = gr.Number(label="Guests", precision=0, value=100)
        b_services = gr.CheckboxGroup(choices=_service_choices(),
                                      label="Additional services (quantity 1)")
        with gr.Row():
            b_notes = gr.Textbox(label="Internal notes")
            b_contact = gr.Textbox(label="Additional contact phone")
        b_create_btn = gr.Button("Create booking (Pending)", variant="primary")
        b_out = gr.Markdown("")
        gr.Markdown("#### Booking detail / timeline")
        with gr.Row():
            b_id = gr.Number(label="Booking ID", precision=0)
            b_detail_btn = gr.Button("Show booking")
        b_detail = gr.Markdown("")
        gr.Markdown("#### Record deposit / payment")
        with gr.Row():
            pay_amount = gr.Number(label="Amount (EGP)")
            pay_method = gr.Dropdown(["cash", "bank_transfer", "visa_card",
                                      "instapay", "other"], value="cash",
                                     label="Method")
            pay_ref = gr.Textbox(label="Reference (optional)")
            pay_initiated = gr.Textbox(
                label="Initiated at (ISO, optional — for grace-period cases)")
            pay_btn = gr.Button("Record payment", variant="primary")
        gr.Markdown("#### Lifecycle — Confirmed → In progress → Completed, "
                    "or Confirmed → No-show.")
        with gr.Row():
            inprog_btn = gr.Button("Mark in progress")
            complete_btn = gr.Button("Mark completed")
            noshow_btn = gr.Button("Mark no-show")
        gr.Markdown("#### Notes / cancel — cancellation needs the "
                    "`booking.cancel` permission (Manager/Owner, SRS §8.8).")
        with gr.Row():
            note_text = gr.Textbox(label="New internal notes")
            note_btn = gr.Button("Update notes")
            cancel_reason = gr.Textbox(label="Cancellation reason")
            cancel_btn = gr.Button("Cancel booking")
        gr.Markdown("#### Reschedule / transfer (Phase 5, SRS §8.9/§8.12) — "
                    "needs `booking.reschedule` / `booking.transfer` (Manager/Owner).")
        with gr.Row():
            rs_date = gr.Textbox(label="New date (YYYY-MM-DD)", placeholder="2026-12-11")
            rs_start = gr.Textbox(label="New start (HH:MM)", value="18:00")
            rs_end = gr.Textbox(label="New end (HH:MM)", value="23:00")
            rs_hall = gr.Dropdown(choices=_hall_choices(),
                                  label="New hall (blank = same hall)")
            rs_reason = gr.Textbox(label="Reschedule reason")
            rs_btn = gr.Button("Reschedule booking")
        with gr.Row():
            tr_customer = gr.Number(label="New customer ID", precision=0)
            tr_reason = gr.Textbox(label="Transfer reason")
            tr_btn = gr.Button("Transfer booking")

        def _bookings_md(emp):
            rows = []
            for b in bookings.list_bookings(emp):
                rows.append([b.id, b.number, b.customer_id, b.hall_id,
                             b.start_at.strftime("%Y-%m-%d %H:%M") if b.start_at else "",
                             b.status, format_money(b.total_amount),
                             format_money(b.deposit_paid), _hold_countdown(b)])
            return _md_table(["ID", "Number", "Customer", "Hall", "Event", "Status",
                              "Total", "Deposit paid", "Hold"], rows)

        def do_refresh_books(token):
            return _bookings_md(employee(token))

        def do_create_book(token, cid, hall, pkg, etype, date, start, end,
                           guests, service_ids, notes, contact):
            emp = employee(token)
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
            gr.Info(f"Booking {booking.number} created (Pending).")
            return _bookings_md(emp), (
                f"**Booking {booking.number}** — Pending, total "
                f"{format_money(booking.total_amount)}, deposit required "
                f"{format_money(booking.deposit_required)}, hold until "
                f"{booking.hold_expires_at.strftime('%Y-%m-%d %H:%M') if booking.hold_expires_at else '—'}.")

        def do_detail(token, bid):
            emp = employee(token)
            if not bid:
                raise gr.Error("Enter a booking ID first.")
            try:
                booking = bookings.get_booking(emp, int(bid))
                if booking is None:
                    raise gr.Error(f"Booking #{int(bid)} does not exist.")
                items = bookings.list_price_items(emp, int(bid))
                payments = bookings.list_payments(emp, int(bid))
                timeline = bookings.timeline(emp, int(bid))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            lines = [
                f"## Booking {booking.number} — {booking.status}",
                f"**Customer:** {booking.customer_id}  \n**Hall:** {booking.hall_id}  \n"
                f"**Event:** {booking.start_at.strftime('%Y-%m-%d %H:%M') if booking.start_at else '—'}"
                f" – {booking.end_at.strftime('%H:%M') if booking.end_at else '—'}  \n"
                f"**Guests:** {booking.guest_count}  \n**Total:** {format_money(booking.total_amount)}  \n"
                f"**Deposit:** {format_money(booking.deposit_paid)} of "
                f"{format_money(booking.deposit_required)}  \n"
                f"**Hold:** {_hold_countdown(booking)}  \n**Notes:** {booking.notes or '—'}",
                "### Price items",
                _md_table(["Item", "Amount"],
                          [[i["label"], format_money(i["amount"])] for i in items]),
                "### Payments",
                _md_table(["Number", "Amount", "Method", "Status"],
                          [[p.number, format_money(p.amount), p.method, p.status]
                           for p in payments]),
                "### Timeline",
                _md_table(["Time", "Action", "Details"],
                          [[e["created_at"], e["action"], e["reason"]] for e in timeline]),
            ]
            return "\n\n".join(lines)

        def do_pay(token, bid, amount, method, ref, initiated):
            emp = employee(token)
            if not bid:
                raise gr.Error("Enter a booking ID first.")
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
            gr.Info(f"Payment {payment.number} recorded. Booking is now {status}.")
            return _bookings_md(emp), (
                f"Payment **{payment.number}** recorded: "
                f"{format_money(payment.amount)} ({method}). "
                f"Booking status: **{status}**.")

        def do_notes(token, bid, notes):
            emp = employee(token)
            if not bid:
                raise gr.Error("Enter a booking ID first.")
            try:
                bookings.update_notes(emp, int(bid), notes or "")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info("Notes updated.")
            return _bookings_md(emp), "Notes updated (old/new recorded in audit)."

        def do_cancel(token, bid, reason):
            emp = employee(token)
            if not bid:
                raise gr.Error("Enter a booking ID first.")
            try:
                booking = bookings.cancel_booking(emp, int(bid), reason or "")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"Booking {booking.number} cancelled.")
            return _bookings_md(emp), f"Booking **{booking.number}** cancelled."

        def do_reschedule(token, bid, date, start, end, hall, reason):
            emp = employee(token)
            if not bid:
                raise gr.Error("Enter a booking ID first.")
            try:
                start_dt, end_dt = _when(date, start, end)
                booking = bookings.reschedule_booking(
                    emp, int(bid), new_start_at=start_dt, new_end_at=end_dt,
                    new_hall_id=int(hall) if hall else None,
                    reason=reason or "")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"Booking {booking.number} rescheduled.")
            return _bookings_md(emp), (
                f"Booking **{booking.number}** rescheduled to "
                f"{booking.start_at.strftime('%Y-%m-%d %H:%M') if booking.start_at else '—'}"
                f" — new total {format_money(booking.total_amount)}.")

        def do_transfer(token, bid, new_cid, reason):
            emp = employee(token)
            if not bid:
                raise gr.Error("Enter a booking ID first.")
            if not new_cid:
                raise gr.Error("Enter the new customer ID first.")
            try:
                booking = bookings.transfer_booking(
                    emp, int(bid), new_customer_id=int(new_cid),
                    reason=reason or "")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"Booking {booking.number} transferred.")
            return _bookings_md(emp), (
                f"Booking **{booking.number}** transferred to customer "
                f"**{booking.customer_id}**. Payments stay attached; a new "
                f"contract version was generated when one existed.")

        def _lifecycle(token, bid, action):
            emp = employee(token)
            if not bid:
                raise gr.Error("Enter a booking ID first.")
            try:
                if action == "in_progress":
                    booking = bookings.mark_in_progress(emp, int(bid))
                elif action == "completed":
                    booking = bookings.mark_completed(emp, int(bid))
                else:
                    booking = bookings.mark_no_show(emp, int(bid))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"Booking {booking.number} is now {booking.status}.")
            return _bookings_md(emp), f"Booking **{booking.number}** → **{booking.status}**."

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
