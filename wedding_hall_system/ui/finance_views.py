"""Finance screens (Phase 5, presentation tier).

Thin UI only: parse input, call ONE service method, render the result.
No SQL and no business rules here. Every handler resolves the session
token to an employee and lets the service enforce permissions; a denied
action raises gr.Error with the service's message.

Money is entered in EGP and converted to integer piasters by the
service boundary (egp_to_piasters). Amounts are shown with
format_money. The accounting basis is shown on the reconciliation
screen (decision A-45, SRS §8.6).
"""
from __future__ import annotations

import json

import gradio as gr

from ..config.settings import AppSettings, format_money
from ..data.database import DatabaseManager
from ..logic.auth_service import AuthService
from ..logic.booking_service import BookingService
from ..logic.expense_service import ExpenseService
from ..logic.models import EXPENSE_CATEGORIES
from ..logic.payment_service import PaymentService
from ..logic.pricing_service import PricingService
from ..logic.refund_service import RefundService
from ..utils.exceptions import ApplicationError
from ..utils.logging_config import get_logger
from ..utils.validators import egp_to_piasters

logger = get_logger(__name__)


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "_No records yet._"
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def build_finance_ui(db: DatabaseManager, auth: AuthService,
                     settings: AppSettings, session_token) -> None:
    """Build the Finance tab. Must be called inside a gr.Blocks
    context, after the login/main views exist."""
    bookings = BookingService(db, settings)
    payments = PaymentService(db, settings)
    expenses = ExpenseService(db, settings)
    refunds = RefundService(db, settings)
    pricing = PricingService(db, settings)

    def employee(token):
        emp = auth.validate_session(token)
        if emp is None:
            raise gr.Error("Your session has expired. Please log in again.")
        return emp

    with gr.Tab("Finance"):
        gr.Markdown("## Finance — payments, installments, expenses, refunds, reconciliation (SRS §8)")
        with gr.Row():
            f_bid = gr.Number(label="Booking ID", precision=0)
            f_show_btn = gr.Button("Show booking finance")

        # -- payments / corrections --------------------------------------
        gr.Markdown("### Payments & corrections")
        with gr.Row():
            corr_pid = gr.Number(label="Payment ID", precision=0)
            corr_type = gr.Dropdown(["reversal", "void"], value="reversal",
                                    label="Correction type")
            corr_reason = gr.Textbox(label="Correction reason")
            corr_btn = gr.Button("Reverse / void payment")
        corr_list_btn = gr.Button("List corrections for payment")
        f_pay_list = gr.Markdown("")

        # -- installments -------------------------------------------------
        gr.Markdown("### Installments")
        with gr.Row():
            inst_count = gr.Number(label="Installments (blank = policy default)",
                                   precision=0)
            inst_plan_btn = gr.Button("Create installment plan", variant="primary")
            inst_list_btn = gr.Button("List installments")
        with gr.Row():
            inst_id = gr.Number(label="Installment ID", precision=0)
            inst_method = gr.Dropdown(["cash", "bank_transfer", "visa_card",
                                       "instapay", "other"], value="cash",
                                      label="Method")
            inst_ref = gr.Textbox(label="Reference (optional)")
            inst_pay_btn = gr.Button("Pay installment", variant="primary")
        sweep_btn = gr.Button("Run due sweep (mark overdue + notify)")
        f_inst_list = gr.Markdown("")

        # -- expenses ------------------------------------------------------
        gr.Markdown("### Expenses")
        with gr.Row():
            exp_amount = gr.Number(label="Amount (EGP)")
            exp_date = gr.Textbox(label="Date (YYYY-MM-DD)", placeholder="2026-11-26")
            exp_cat = gr.Dropdown(list(EXPENSE_CATEGORIES), value="Other",
                                  label="Category")
        with gr.Row():
            exp_desc = gr.Textbox(label="Description")
            exp_method = gr.Dropdown(["cash", "bank_transfer", "visa_card",
                                      "instapay", "other"], value="cash",
                                     label="Method")
            exp_scope = gr.Dropdown(["venue", "hall", "event"], value="venue",
                                    label="Scope")
            exp_scope_id = gr.Number(label="Hall/booking ID (for hall/event scope)",
                                     precision=0)
        exp_btn = gr.Button("Record expense", variant="primary")
        with gr.Row():
            exp_id = gr.Number(label="Expense ID", precision=0)
            exp_approve_btn = gr.Button("Approve")
            exp_reject_btn = gr.Button("Reject")
            exp_paid_btn = gr.Button("Mark paid")
            exp_list_btn = gr.Button("List expenses")
        f_exp_list = gr.Markdown("")

        # -- refunds --------------------------------------------------------
        gr.Markdown("### Refunds")
        with gr.Row():
            ref_pid = gr.Number(label="Payment ID", precision=0)
            ref_amount = gr.Number(label="Amount (EGP)")
            ref_method = gr.Dropdown(["cash", "bank_transfer", "visa_card",
                                      "instapay", "other"], value="cash",
                                     label="Method")
            ref_reason = gr.Textbox(label="Reason")
            ref_btn = gr.Button("Request refund", variant="primary")
        with gr.Row():
            ref_id = gr.Number(label="Refund ID", precision=0)
            ref_approve_btn = gr.Button("Approve")
            ref_reject_btn = gr.Button("Reject")
            ref_pay_btn = gr.Button("Mark paid")
            ref_cancel_btn = gr.Button("Cancel")
            ref_list_btn = gr.Button("List refunds for booking")
        f_ref_list = gr.Markdown("")

        # -- policy snapshot + reconciliation -------------------------------
        gr.Markdown("### Financial policy snapshot & reconciliation")
        with gr.Row():
            snap_btn = gr.Button("Show frozen financial policy for booking")
            recon_btn = gr.Button("Reconciliation", variant="primary")
        f_out = gr.Markdown("")

        # -- handlers --------------------------------------------------------
        def do_show(token, bid):
            emp = employee(token)
            if not bid:
                raise gr.Error("Enter a booking ID first.")
            try:
                booking = bookings.get_booking(emp, int(bid))
                if booking is None:
                    raise gr.Error(f"Booking #{int(bid)} does not exist.")
                over = payments.overpayment_for_booking(emp, int(bid))
                pay_rows = [[p.number, format_money(p.amount), p.method,
                             p.status] for p in bookings.list_payments(emp, int(bid))]
                refund_rows = [[r.id, r.payment_id, format_money(r.amount),
                                r.status, r.reason]
                               for r in refunds.list_refunds(emp, booking_id=int(bid))]
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            lines = [
                f"## Booking {booking.number} — {booking.status}",
                f"**Total:** {format_money(booking.total_amount)}  \n"
                f"**Collected:** {format_money(over['collected'])}  \n"
                f"**Refunds paid:** {format_money(over['refunds_paid'])}  \n"
                f"**Net collected:** {format_money(over['net_collected'])}  \n"
                f"**Outstanding:** {format_money(over['outstanding'])}  \n"
                f"**Overpayment:** {format_money(over['overpayment'])}  \n"
                f"**Refundable:** {format_money(over['refundable'])} "
                f"({over['refundable_basis']})",
                "### Payments",
                _md_table(["Number", "Amount", "Method", "Status"], pay_rows),
                "### Refunds",
                _md_table(["ID", "Payment", "Amount", "Status", "Reason"],
                          refund_rows),
            ]
            return "\n\n".join(lines)

        def do_correct(token, pid, ctype, reason):
            emp = employee(token)
            if not pid:
                raise gr.Error("Enter a payment ID first.")
            try:
                payment = payments.reverse_payment(
                    emp, int(pid), correction_type=ctype, reason=reason or "")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"Payment {payment.number} {ctype}.")
            return (f"Payment **{payment.number}** {ctype}: "
                    f"{format_money(payment.amount)}. The original record is "
                    f"preserved with status **{payment.status}**.")

        def do_list_corr(token, pid):
            emp = employee(token)
            if not pid:
                raise gr.Error("Enter a payment ID first.")
            try:
                rows = payments.list_corrections(emp, int(pid))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return _md_table(
                ["ID", "Type", "Amount", "Reason"],
                [[r["id"], r["correction_type"], format_money(r["amount"]),
                  r["reason"]] for r in rows])

        def do_plan(token, bid, count):
            emp = employee(token)
            if not bid:
                raise gr.Error("Enter a booking ID first.")
            try:
                plan = payments.create_installment_plan(
                    emp, int(bid),
                    count=int(count) if count else None)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"Installment plan created ({len(plan)} parts).")
            return _md_table(
                ["#", "Amount", "Due", "Status"],
                [[i.sequence, format_money(i.amount),
                  i.due_at.strftime("%Y-%m-%d") if i.due_at else "—",
                  i.status] for i in plan])

        def do_list_inst(token, bid):
            emp = employee(token)
            if not bid:
                raise gr.Error("Enter a booking ID first.")
            try:
                rows = payments.list_installments(emp, int(bid))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return _md_table(
                ["ID", "#", "Amount", "Due", "Status", "Late fee", "Payment"],
                [[i.id, i.sequence, format_money(i.amount),
                  i.due_at.strftime("%Y-%m-%d") if i.due_at else "—", i.status,
                  format_money(i.late_fee_piasters) if i.late_fee_piasters else "—",
                  i.paid_payment_id or "—"] for i in rows])

        def do_pay_inst(token, iid, method, ref):
            emp = employee(token)
            if not iid:
                raise gr.Error("Enter an installment ID first.")
            try:
                payment = payments.pay_installment(
                    emp, int(iid), method, reference=ref or "")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"Installment paid via {payment.number}.")
            return (f"Installment **{iid}** paid: "
                    f"{format_money(payment.amount)} via payment "
                    f"**{payment.number}**.")

        def do_sweep(token):
            emp = employee(token)
            try:
                result = payments.run_due_sweep(emp)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return f"Due sweep done: **{result['overdue_marked']}** installment(s) marked overdue (Accountant notified)."

        def do_record_exp(token, amount, date, cat, desc, method, scope, scope_id):
            emp = employee(token)
            try:
                amount_piasters = egp_to_piasters(amount, "Amount (EGP)")
                expense = expenses.record_expense(
                    emp, amount_piasters=amount_piasters, date=date or "",
                    category=cat, description=desc or "", method=method,
                    scope_type=scope,
                    scope_id=int(scope_id) if scope_id else None)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"Expense #{expense.id} recorded (Pending approval).")
            return (f"Expense **#{expense.id}** recorded: "
                    f"{format_money(expense.amount)} ({cat}, {scope}). "
                    f"Approval: **pending**, payment: **pending**.")

        def _expense_action(token, eid, action):
            emp = employee(token)
            if not eid:
                raise gr.Error("Enter an expense ID first.")
            try:
                if action == "approve":
                    expense = expenses.approve_expense(emp, int(eid))
                elif action == "reject":
                    expense = expenses.reject_expense(emp, int(eid))
                else:
                    expense = expenses.mark_expense_paid(emp, int(eid))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"Expense #{expense.id}: {action}.")
            return (f"Expense **#{expense.id}**: approval "
                    f"**{expense.approval_status}**, payment "
                    f"**{expense.payment_status}**.")

        def do_list_exp(token):
            emp = employee(token)
            try:
                rows = expenses.list_expenses(emp)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return _md_table(
                ["ID", "Date", "Category", "Amount", "Scope", "Approval", "Payment"],
                [[e.id, e.date, e.category, format_money(e.amount),
                  f"{e.scope_type}#{e.scope_id}" if e.scope_id else e.scope_type,
                  e.approval_status, e.payment_status] for e in rows])

        def do_request_ref(token, pid, amount, method, reason):
            emp = employee(token)
            if not pid:
                raise gr.Error("Enter a payment ID first.")
            try:
                amount_piasters = egp_to_piasters(amount, "Amount (EGP)")
                refund = refunds.request_refund(
                    emp, int(pid), amount_piasters, reason or "", method=method)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"Refund #{refund.id} requested (Pending approval).")
            return (f"Refund **#{refund.id}** requested: "
                    f"{format_money(refund.amount)} — status "
                    f"**pending_approval**.")

        def _refund_action(token, rid, action):
            emp = employee(token)
            if not rid:
                raise gr.Error("Enter a refund ID first.")
            try:
                if action == "approve":
                    refund = refunds.approve_refund(emp, int(rid))
                elif action == "reject":
                    refund = refunds.reject_refund(emp, int(rid))
                elif action == "pay":
                    refund = refunds.mark_refund_paid(emp, int(rid))
                else:
                    refund = refunds.cancel_refund(emp, int(rid),
                                                   reason="Cancelled from UI.")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"Refund #{refund.id}: {refund.status}.")
            return f"Refund **#{refund.id}** — status **{refund.status}**."

        def do_list_ref(token, bid):
            emp = employee(token)
            if not bid:
                raise gr.Error("Enter a booking ID first.")
            try:
                rows = refunds.list_refunds(emp, booking_id=int(bid))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return _md_table(
                ["ID", "Payment", "Amount", "Status", "Reason"],
                [[r.id, r.payment_id, format_money(r.amount), r.status,
                  r.reason] for r in rows])

        def do_snapshot(token, bid):
            emp = employee(token)
            if not bid:
                raise gr.Error("Enter a booking ID first.")
            try:
                policy = pricing.financial_policy_for_booking(int(bid), emp)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return ("### Frozen financial policy for booking "
                    f"**{int(bid)}**\n\n```json\n"
                    f"{json.dumps(policy, indent=2, ensure_ascii=False)}\n```\n\n"
                    "_This is the policy frozen at confirmation (or the "
                    "current settings when the booking has no snapshot yet); "
                    "later setting changes never rewrite it._")

        def do_recon(token):
            emp = employee(token)
            try:
                result = payments.reconcile(emp)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            s = result["summary"]
            lines = [
                "## Reconciliation",
                f"**Contract value:** {format_money(s['contract_value'])}  \n"
                f"**Collected:** {format_money(s['collected'])}  \n"
                f"**Refunds paid:** {format_money(s['refunds_paid'])}  \n"
                f"**Recognised revenue:** {format_money(s['recognised_revenue'])}  \n"
                f"**Overpayment (not revenue):** {format_money(s['overpayment'])}  \n"
                f"**Outstanding:** {format_money(s['outstanding'])}  \n"
                f"**Expenses (approved):** {format_money(s['expenses_approved'])}  \n"
                f"**Cash flow:** {format_money(s['cash_flow'])}  \n"
                f"**Profit:** {format_money(s['profit'])}  \n"
                f"_{s['basis']}_",
                "### Discrepancies",
                _md_table(["Booking", "Issue", "Detail"],
                          [[d["number"], d["issue"], d["detail"]]
                           for d in result["discrepancies"]]),
                "### Per-booking",
                _md_table(
                    ["Booking", "Status", "Total", "Collected", "Net",
                     "Outstanding", "Overpay", "Refundable", "Flags"],
                    [[r["number"], r["status"], format_money(r["total_amount"]),
                      format_money(r["collected"]),
                      format_money(r["net_collected"]),
                      format_money(r["outstanding"]),
                      format_money(r["overpayment"]),
                      format_money(r["refundable"]),
                      ", ".join(r["flags"]) or "—"] for r in result["bookings"]]),
            ]
            return "\n\n".join(lines)

        f_show_btn.click(do_show, inputs=[session_token, f_bid], outputs=[f_out])
        corr_btn.click(do_correct, inputs=[session_token, corr_pid, corr_type,
                                           corr_reason], outputs=[f_out])
        corr_list_btn.click(do_list_corr, inputs=[session_token, corr_pid],
                            outputs=[f_pay_list])
        inst_plan_btn.click(do_plan, inputs=[session_token, f_bid, inst_count],
                            outputs=[f_inst_list])
        inst_list_btn.click(do_list_inst, inputs=[session_token, f_bid],
                            outputs=[f_inst_list])
        inst_pay_btn.click(do_pay_inst, inputs=[session_token, inst_id,
                                                inst_method, inst_ref],
                           outputs=[f_inst_list])
        sweep_btn.click(do_sweep, inputs=[session_token], outputs=[f_inst_list])
        exp_btn.click(do_record_exp, inputs=[session_token, exp_amount, exp_date,
                                             exp_cat, exp_desc, exp_method,
                                             exp_scope, exp_scope_id],
                      outputs=[f_exp_list])
        exp_approve_btn.click(lambda t, i: _expense_action(t, i, "approve"),
                              inputs=[session_token, exp_id], outputs=[f_exp_list])
        exp_reject_btn.click(lambda t, i: _expense_action(t, i, "reject"),
                             inputs=[session_token, exp_id], outputs=[f_exp_list])
        exp_paid_btn.click(lambda t, i: _expense_action(t, i, "paid"),
                           inputs=[session_token, exp_id], outputs=[f_exp_list])
        exp_list_btn.click(do_list_exp, inputs=[session_token], outputs=[f_exp_list])
        ref_btn.click(do_request_ref, inputs=[session_token, ref_pid, ref_amount,
                                              ref_method, ref_reason],
                      outputs=[f_ref_list])
        ref_approve_btn.click(lambda t, i: _refund_action(t, i, "approve"),
                              inputs=[session_token, ref_id], outputs=[f_ref_list])
        ref_reject_btn.click(lambda t, i: _refund_action(t, i, "reject"),
                             inputs=[session_token, ref_id], outputs=[f_ref_list])
        ref_pay_btn.click(lambda t, i: _refund_action(t, i, "pay"),
                          inputs=[session_token, ref_id], outputs=[f_ref_list])
        ref_cancel_btn.click(lambda t, i: _refund_action(t, i, "cancel"),
                             inputs=[session_token, ref_id], outputs=[f_ref_list])
        ref_list_btn.click(do_list_ref, inputs=[session_token, f_bid],
                           outputs=[f_ref_list])
        snap_btn.click(do_snapshot, inputs=[session_token, f_bid], outputs=[f_out])
        recon_btn.click(do_recon, inputs=[session_token], outputs=[f_out])
