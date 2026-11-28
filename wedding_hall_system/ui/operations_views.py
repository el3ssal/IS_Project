"""Contracts, event plans, tasks, staff and notifications (Phase 4;
Phase 8: fully bilingual, SRS §16.3; bilingual contract templates;
notifications rendered in the reader's language).

Thin UI only: parse input, call ONE service method, render the result.
No SQL and no business rules here. Every handler resolves the session
token to an employee and lets the service enforce permissions; a denied
action raises gr.Error with the service's message.

Contracts are versioned HTML documents (no PDF library is permitted
yet — A-38): viewing a version renders the frozen HTML in the page,
printable to PDF from the browser. Signed copies are uploaded as files
and downloaded only through the authorized service handler. A contract
renders from the template's Arabic body when the generating employee's
language is Arabic and the template has one; otherwise English.
"""
from __future__ import annotations

import json

import gradio as gr

from ..config.settings import AppSettings
from ..data.database import DatabaseManager
from ..logic.auth_service import AuthService
from ..logic.contract_service import ContractService
from ..logic.models import Employee
from ..logic.notification_service import NotificationService
from ..logic.permissions import has_permission
from ..logic.staff_service import StaffService
from ..logic.task_service import TaskService
from ..utils.exceptions import ApplicationError
from ..utils.logging_config import get_logger
from .i18n import (LabelRegistry, dir_wrap, format_notification, lang_for_token, t)
from .live_refresh import login_follow_up

logger = get_logger(__name__)

SIG_STATUS_KEYS = {
    "draft": "ops.sig_draft",
    "pending_signature": "ops.sig_pending_signature",
    "partially_signed": "ops.sig_partially_signed",
    "signed": "ops.sig_signed",
    "cancelled": "ops.sig_cancelled",
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
AV_KIND_KEYS = {
    "available": "ops.av_available",
    "unavailable": "ops.av_unavailable",
    "time_off": "ops.av_time_off",
}
NOTIF_TYPE_KEYS = {
    "new_booking": "ops.notif_type_new_booking",
    "hold_expiring": "ops.notif_type_hold_expiring",
    "booking_cancelled": "ops.notif_type_booking_cancelled",
    "booking_rescheduled": "ops.notif_type_booking_rescheduled",
    "booking_services_changed": "ops.notif_type_booking_services_changed",
    "payment_received": "ops.notif_type_payment_received",
    "installment_due": "ops.notif_type_installment_due",
    "event_approaching": "ops.notif_type_event_approaching",
    "task_due_soon": "ops.notif_type_task_due_soon",
    "task_overdue": "ops.notif_type_task_overdue",
    "task_assigned": "ops.notif_type_task_assigned",
    "assignment_conflict": "ops.notif_type_assignment_conflict",
    "contract_awaiting_signature": "ops.notif_type_contract_awaiting_signature",
    "hall_maintenance": "ops.notif_type_hall_maintenance",
}


def _md_table(headers: list[str], rows: list[list[str]], lang: str) -> str:
    if not rows:
        return dir_wrap(lang, f"_{t(lang, 'common.no_records')}_")
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return dir_wrap(lang, "\n".join(out))


def _yes_no(lang: str, value: bool) -> str:
    return t(lang, "common.yes" if value else "common.no")


def build_operations_ui(db: DatabaseManager, auth: AuthService,
                        settings: AppSettings, session_token) -> dict:
    """Build the Phase 4 tabs. Must be called inside a gr.Blocks
    context, after the login/main views exist. Returns the
    LabelRegistry result for the language toggle."""
    reg = LabelRegistry()
    contracts = ContractService(db, settings)
    tasks = TaskService(db)
    staff = StaffService(db)
    notifications = NotificationService(db)

    def _lang(token) -> str:
        return lang_for_token(db, auth, token)

    def employee(token):
        emp = auth.validate_session(token)
        if emp is None:
            raise gr.Error(t(_lang(token), "login.session_expired"))
        return emp

    def _system_employee() -> Employee:
        # Read-only list helpers need an employee with view rights; the
        # Owner demo account always exists (seeded). Used only to build
        # dropdown choices — every write still checks the real user.
        row = db.query_one("SELECT id FROM employees WHERE username = 'owner'")
        return Employee(id=int(row["id"]), username="owner", full_name="Owner")

    def _template_choices() -> list[str]:
        return [t["name"] for t in contracts.list_templates(_system_employee())]

    # ============================================================== CONTRACTS
    with reg.tab("tab.contracts"):
        reg.markdown("ops.contracts_heading")
        contracts_list = gr.Markdown("")
        refresh_contracts_btn = reg.button("ops.refresh_contracts")
        with gr.Row():
            gen_booking_id = reg.number("ops.gen_booking", precision=0)
            gen_template = reg.dropdown("ops.gen_template", choices=_template_choices())
            gen_contract_btn = reg.button("ops.generate_contract", variant="primary")
        with gr.Row():
            view_contract_id = reg.number("ops.view_contract_id", precision=0)
            view_version = reg.number("ops.view_version", precision=0, value=1)
            view_contract_btn = reg.button("ops.view_version_btn")
        contract_html = gr.HTML("")
        with gr.Row():
            newver_contract_id = reg.number("ops.newver_contract_id", precision=0)
            newver_btn = reg.button("ops.new_version")
        with gr.Row():
            sig_contract_id = reg.number("ops.sig_contract_id", precision=0)
            sig_status = reg.dropdown_t(
                "ops.sig_status",
                [("ops.sig_draft", "draft"),
                 ("ops.sig_pending_signature", "pending_signature"),
                 ("ops.sig_partially_signed", "partially_signed"),
                 ("ops.sig_signed", "signed"),
                 ("ops.sig_cancelled", "cancelled")],
                value="pending_signature")
            sig_date = reg.textbox("ops.sig_date", placeholder="2026-11-27")
            sig_btn = reg.button("ops.set_signature")
        reg.markdown("ops.signed_heading")
        with gr.Row():
            up_contract_id = reg.number("ops.up_contract_id", precision=0)
            up_file = reg.file("ops.up_file")
            up_date = reg.textbox("ops.up_date", placeholder="2026-11-27")
            up_btn = reg.button("ops.upload_signed", variant="primary")
        reg.markdown("ops.att_heading")
        with gr.Row():
            att_contract_id = reg.number("ops.att_contract_id", precision=0)
            att_refresh_btn = reg.button("ops.list_attachments")
            att_download_id = reg.number("ops.att_download_id", precision=0)
            att_download_btn = reg.button("ops.download_attachment")
        attachments_list = gr.Markdown("")
        att_file = reg.file("ops.downloaded_file")

        def _contracts_md(lang: str):
            rows = [[c.id, c.number, c.booking_id, c.template_name,
                     c.current_version_id or "—",
                     t(lang, SIG_STATUS_KEYS.get(c.signature_status, "ops.sig_draft")),
                     c.signature_date or "—"]
                    for c in contracts.list_contracts(_system_employee())]
            return dir_wrap(lang, _md_table(
                [t(lang, "ops.table_id"), t(lang, "ops.table_number"),
                 t(lang, "ops.table_booking"), t(lang, "ops.table_template"),
                 t(lang, "ops.table_current_version"),
                 t(lang, "ops.table_signature"), t(lang, "ops.table_signed_on")],
                rows, lang))

        def do_refresh_contracts(token):
            employee(token)
            return _contracts_md(_lang(token))

        def do_generate_contract(token, booking_id, template_name):
            emp = employee(token)
            lang = _lang(token)
            if not booking_id:
                raise gr.Error(t(lang, "ops.msg_need_booking_id"))
            if not template_name:
                raise gr.Error(t(lang, "ops.msg_pick_template"))
            try:
                contract = contracts.generate_contract(
                    emp, int(booking_id), template_name, lang=lang)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "ops.msg_contract_generated").format(number=contract.number))
            return _contracts_md(lang)

        def do_view_contract(token, contract_id, version):
            emp = employee(token)
            if not contract_id:
                raise gr.Error(t(_lang(token), "ops.msg_need_contract_id"))
            try:
                body = contracts.get_version_body(
                    emp, int(contract_id), int(version or 1))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return body

        def do_new_version(token, contract_id):
            emp = employee(token)
            lang = _lang(token)
            if not contract_id:
                raise gr.Error(t(lang, "ops.msg_need_contract_id"))
            try:
                version = contracts.create_new_version(emp, int(contract_id), lang=lang)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "ops.msg_version_created").format(version=version.version))
            return _contracts_md(lang)

        def do_set_signature(token, contract_id, status, date):
            emp = employee(token)
            lang = _lang(token)
            if not contract_id:
                raise gr.Error(t(lang, "ops.msg_need_contract_id"))
            try:
                contract = contracts.set_signature_status(
                    emp, int(contract_id), status,
                    signature_date=(date or "").strip() or None)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "ops.msg_signature_set").format(
                number=contract.number,
                status=t(lang, SIG_STATUS_KEYS.get(contract.signature_status, "ops.sig_draft"))))
            return _contracts_md(lang)

        def do_upload_signed(token, contract_id, file_path, date):
            emp = employee(token)
            lang = _lang(token)
            if not contract_id:
                raise gr.Error(t(lang, "ops.msg_need_contract_id"))
            if not file_path:
                raise gr.Error(t(lang, "ops.msg_need_file"))
            try:
                result = contracts.upload_signed_copy(
                    emp, int(contract_id), str(file_path),
                    original_filename=str(file_path).rsplit("/", 1)[-1],
                    signature_date=(date or "").strip())
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "ops.msg_signed_stored").format(hash=result["sha256"][:12]))
            return _contracts_md(lang)

        def do_list_attachments(token, contract_id):
            emp = employee(token)
            lang = _lang(token)
            if not contract_id:
                raise gr.Error(t(lang, "ops.msg_need_contract_id"))
            try:
                rows = contracts.list_attachments(emp, int(contract_id))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return dir_wrap(lang, _md_table(
                [t(lang, "ops.table_id"), t(lang, "ops.table_original_name"),
                 t(lang, "ops.table_category"), t(lang, "ops.table_size"),
                 t(lang, "ops.table_uploaded_by"), t(lang, "ops.table_uploaded_at")],
                [[r["id"], r["original_filename"], r["category"], r["size_bytes"],
                  r["uploaded_by"], r["uploaded_at"]] for r in rows], lang))

        def do_download_attachment(token, attachment_id):
            emp = employee(token)
            lang = _lang(token)
            if not attachment_id:
                raise gr.Error(t(lang, "ops.msg_need_attachment_id"))
            try:
                path = contracts.download_attachment(emp, int(attachment_id))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return str(path)

        refresh_contracts_btn.click(do_refresh_contracts, inputs=[session_token],
                                    outputs=[contracts_list])
        gen_contract_btn.click(do_generate_contract,
                               inputs=[session_token, gen_booking_id, gen_template],
                               outputs=[contracts_list])
        view_contract_btn.click(do_view_contract,
                                inputs=[session_token, view_contract_id, view_version],
                                outputs=[contract_html])
        newver_btn.click(do_new_version, inputs=[session_token, newver_contract_id],
                         outputs=[contracts_list])
        sig_btn.click(do_set_signature,
                      inputs=[session_token, sig_contract_id, sig_status, sig_date],
                      outputs=[contracts_list])
        up_btn.click(do_upload_signed,
                     inputs=[session_token, up_contract_id, up_file, up_date],
                     outputs=[contracts_list])
        att_refresh_btn.click(do_list_attachments,
                              inputs=[session_token, att_contract_id],
                              outputs=[attachments_list])
        att_download_btn.click(do_download_attachment,
                               inputs=[session_token, att_download_id],
                               outputs=[att_file])

    # ==================================================== CONTRACT TEMPLATES
    with reg.tab("tab.contract_templates"):
        reg.markdown("ops.templates_heading")
        templates_list = gr.Markdown("")
        refresh_templates_btn = reg.button("ops.refresh_templates")
        tpl_name = reg.textbox("ops.tpl_name")
        tpl_body = reg.textbox("ops.tpl_body", lines=12)
        tpl_body_ar = reg.textbox("ops.tpl_body_ar", lines=12)
        with gr.Row():
            tpl_create_btn = reg.button("ops.create_template", variant="primary")
            tpl_update_btn = reg.button("ops.update_template")
            tpl_delete_btn = reg.button("ops.delete_template")

        def _templates_md(lang: str):
            rows = [[tpl["name"], tpl["key"],
                     f"{len(tpl['body'])} {t(lang, 'ops.chars')}"]
                    for tpl in contracts.list_templates(_system_employee())]
            return dir_wrap(lang, _md_table(
                [t(lang, "ops.table_name"), t(lang, "ops.table_key"),
                 t(lang, "ops.table_body_length")], rows, lang))

        def do_refresh_templates(token):
            employee(token)
            return _templates_md(_lang(token))

        def do_tpl_save(token, name, body, body_ar, update: bool):
            emp = employee(token)
            lang = _lang(token)
            try:
                if update:
                    contracts.update_template(emp, name=name, body=body or "",
                                              body_ar=body_ar or None)
                    gr.Info(t(lang, "ops.msg_template_updated").format(name=name))
                else:
                    contracts.create_template(emp, name=name, body=body or "",
                                              body_ar=body_ar or None)
                    gr.Info(t(lang, "ops.msg_template_created").format(name=name))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return _templates_md(lang)

        def do_tpl_delete(token, name):
            emp = employee(token)
            lang = _lang(token)
            try:
                contracts.delete_template(emp, name=name)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "ops.msg_template_deleted").format(name=name))
            return _templates_md(lang)

        refresh_templates_btn.click(do_refresh_templates, inputs=[session_token],
                                    outputs=[templates_list])
        tpl_create_evt = tpl_create_btn.click(lambda t, n, b, ba: do_tpl_save(t, n, b, ba, False),
                             inputs=[session_token, tpl_name, tpl_body, tpl_body_ar],
                             outputs=[templates_list])
        tpl_update_evt = tpl_update_btn.click(lambda t, n, b, ba: do_tpl_save(t, n, b, ba, True),
                             inputs=[session_token, tpl_name, tpl_body, tpl_body_ar],
                             outputs=[templates_list])
        tpl_delete_evt = tpl_delete_btn.click(do_tpl_delete, inputs=[session_token, tpl_name],
                             outputs=[templates_list])

    # ================================================== EVENT PLANS & TASKS
    with reg.tab("tab.event_plans"):
        reg.markdown("ops.plans_heading")
        plan_md = gr.Markdown("")
        with gr.Row():
            plan_booking_id = reg.number("ops.plan_booking", precision=0)
            plan_template_id = reg.number("ops.plan_template", precision=0)
            plan_gen_btn = reg.button("ops.generate_plan", variant="primary")
            plan_view_btn = reg.button("ops.view_plan")
        reg.markdown("ops.task_templates_heading")
        task_templates_list = gr.Markdown("")
        refresh_task_templates_btn = reg.button("ops.refresh_task_templates")
        with gr.Row():
            tt_name = reg.textbox("ops.tt_name")
            tt_event_type_id = reg.number("ops.tt_event_type", precision=0)
            tt_package_id = reg.number("ops.tt_package", precision=0)
        tt_items = reg.textbox("ops.tt_items", lines=4,
                               placeholder_key="ops.tt_items_hint")
        tt_create_btn = reg.button("ops.create_task_template", variant="primary")
        reg.markdown("ops.assign_heading")
        with gr.Row():
            asg_task_id = reg.number("ops.asg_task", precision=0)
            asg_employee_id = reg.number("ops.asg_employee", precision=0)
            asg_btn = reg.button("ops.assign_task", variant="primary")
        with gr.Row():
            st_task_id = reg.number("ops.st_task", precision=0)
            st_status = reg.dropdown_t(
                "ops.st_status",
                [("ops.task_status_pending", "pending"),
                 ("ops.task_status_in_progress", "in_progress"),
                 ("ops.task_status_completed", "completed")])
            st_note = reg.textbox("ops.st_note")
            st_btn = reg.button("ops.update_status")
        reg.markdown("ops.edit_heading")
        with gr.Row():
            edit_task_id = reg.number("ops.edit_task", precision=0)
            edit_name = reg.textbox("ops.edit_name")
            edit_desc = reg.textbox("ops.edit_desc")
            edit_priority = reg.dropdown_t(
                "ops.edit_priority",
                [("ops.priority_none", ""),
                 ("ops.priority_low", "low"),
                 ("ops.priority_medium", "medium"),
                 ("ops.priority_high", "high"),
                 ("ops.priority_urgent", "urgent")],
                value="")
            edit_due = reg.textbox("ops.edit_due")
            edit_service_id = reg.number("ops.edit_service", precision=0)
            edit_btn = reg.button("ops.update_task")
        with gr.Row():
            hist_task_id = reg.number("ops.hist_task", precision=0)
            hist_btn = reg.button("ops.show_history")
        task_hist_md = gr.Markdown("")

        def _task_templates_md(lang: str):
            rows = []
            for tmpl in tasks.list_templates(_system_employee()):
                rows.append([tmpl["id"], tmpl["name"],
                             tmpl["event_type_id"] or t(lang, "common.any"),
                             tmpl["package_id"] or t(lang, "common.any"),
                             len(tmpl["items"])])
            return dir_wrap(lang, _md_table(
                [t(lang, "ops.table_id"), t(lang, "ops.table_name"),
                 t(lang, "ops.table_event_type"), t(lang, "ops.table_package"),
                 t(lang, "ops.table_items")], rows, lang))

        def _plan_tasks_md(plan: dict, lang: str) -> str:
            rows = [[t["id"], t["name"], t["assigned_to"] or "—",
                     t["due_at"] or "—",
                     t(lang, PRIORITY_KEYS.get(t["priority"], "ops.priority_medium")),
                     t(lang, TASK_STATUS_KEYS.get(t["status"], "ops.task_status_pending"))]
                    for t in plan["tasks"]]
            template = plan.get("template_name") or "—"
            return dir_wrap(lang, (t(lang, "ops.plan_line").format(id=plan["id"], template=template)
                    + "  \n"
                    + _md_table(
                        [t(lang, "ops.table_id"), t(lang, "ops.table_task"),
                         t(lang, "ops.table_assigned"), t(lang, "ops.table_due"),
                         t(lang, "ops.table_priority"), t(lang, "ops.table_status")],
                        rows, lang)))

        def do_generate_plan(token, booking_id, template_id):
            emp = employee(token)
            lang = _lang(token)
            if not booking_id:
                raise gr.Error(t(lang, "ops.msg_need_booking_id"))
            try:
                plan = tasks.generate_event_plan(
                    emp, int(booking_id),
                    template_id=int(template_id) if template_id else None)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "ops.msg_plan_generated").format(
                id=plan["id"], n=len(plan["tasks"])))
            return _plan_tasks_md(plan, lang)

        def do_view_plan(token, booking_id):
            emp = employee(token)
            lang = _lang(token)
            if not booking_id:
                raise gr.Error(t(lang, "ops.msg_need_booking_id"))
            try:
                plan = tasks.get_event_plan(emp, int(booking_id))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return _plan_tasks_md(plan, lang)

        def do_refresh_task_templates(token):
            employee(token)
            return _task_templates_md(_lang(token))

        def do_create_task_template(token, name, event_type_id, package_id, items_text):
            emp = employee(token)
            lang = _lang(token)
            items = []
            for line in (items_text or "").splitlines():
                parts = [p.strip() for p in line.split("|")]
                if len(parts) < 3 or not parts[0]:
                    continue
                try:
                    offset = int(parts[2])
                except ValueError:
                    raise gr.Error(t(lang, "ops.msg_due_offset_number").format(line=line))
                items.append({"name": parts[0],
                              "description": parts[1] if len(parts) > 1 else "",
                              "due_offset_min": offset,
                              "priority": parts[3] if len(parts) > 3 else "medium"})
            try:
                template = tasks.create_template(
                    emp, name=name or "",
                    event_type_id=int(event_type_id) if event_type_id else None,
                    package_id=int(package_id) if package_id else None,
                    items=items)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "ops.msg_task_template_created").format(name=template["name"]))
            return _task_templates_md(lang)

        def _task_rows_md(task_rows: list[dict], lang: str) -> str:
            return dir_wrap(lang, _md_table(
                [t(lang, "ops.table_id"), t(lang, "ops.table_task"),
                 t(lang, "ops.table_assigned"), t(lang, "ops.table_due"),
                 t(lang, "ops.table_status")],
                [[r["id"], r["name"], r["assigned_to"] or "—",
                  r["due_at"] or "—",
                  t(lang, TASK_STATUS_KEYS.get(r["status"], "ops.task_status_pending"))]
                 for r in task_rows], lang))

        def do_assign_task(token, task_id, employee_id):
            emp = employee(token)
            lang = _lang(token)
            if not task_id or not employee_id:
                raise gr.Error(t(lang, "ops.msg_need_task_employee"))
            try:
                result = tasks.assign_task(emp, int(task_id), int(employee_id))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            if result.get("warnings"):
                gr.Info(t(lang, "ops.msg_assigned_warnings").format(
                    warnings="; ".join(result["warnings"])))
            else:
                gr.Info(t(lang, "ops.msg_assigned").format(
                    task=task_id, employee=employee_id))
            return _task_rows_md([result], lang)

        def do_update_status(token, task_id, status, note):
            emp = employee(token)
            lang = _lang(token)
            if not task_id:
                raise gr.Error(t(lang, "ops.msg_need_task_id"))
            try:
                task = tasks.update_task_status(emp, int(task_id), status,
                                                note=note or "")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "ops.msg_task_status").format(
                task=task_id,
                status=t(lang, TASK_STATUS_KEYS.get(task.status, "ops.task_status_pending"))))
            return _task_rows_md([{"id": task.id, "name": task.name,
                                  "assigned_to": task.assigned_to,
                                  "due_at": task.due_at,
                                  "status": task.status}], lang)

        def do_update_task_details(token, task_id, name, desc, priority,
                                   due, service_id):
            emp = employee(token)
            lang = _lang(token)
            if not task_id:
                raise gr.Error(t(lang, "ops.msg_need_task_id"))
            try:
                task = tasks.update_task_details(
                    emp, int(task_id),
                    name=(name or "").strip() or None,
                    description=(desc or "").strip() or None,
                    priority=priority or None,
                    due_at=(due or "").strip() or None,
                    service_id=int(service_id) if service_id else None)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "ops.msg_task_updated").format(task=task_id))
            return _task_rows_md([{"id": task.id, "name": task.name,
                                  "assigned_to": task.assigned_to,
                                  "due_at": task.due_at,
                                  "status": task.status}], lang)

        def do_task_history(token, task_id):
            emp = employee(token)
            lang = _lang(token)
            if not task_id:
                raise gr.Error(t(lang, "ops.msg_need_task_id"))
            try:
                rows = tasks.task_history(emp, int(task_id))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return dir_wrap(lang, _md_table(
                [t(lang, "ops.table_when"), t(lang, "ops.table_changed_by"),
                 t(lang, "ops.table_action"), t(lang, "ops.table_old"),
                 t(lang, "ops.table_new")],
                [[r["created_at"], r["employee_id"] or "—", r["action"],
                  r["old_value"] or "—", r["new_value"] or "—"]
                 for r in rows], lang))

        plan_gen_btn.click(do_generate_plan,
                           inputs=[session_token, plan_booking_id, plan_template_id],
                           outputs=[plan_md])
        plan_view_btn.click(do_view_plan, inputs=[session_token, plan_booking_id],
                            outputs=[plan_md])
        refresh_task_templates_btn.click(do_refresh_task_templates,
                                         inputs=[session_token],
                                         outputs=[task_templates_list])
        tt_create_btn.click(do_create_task_template,
                            inputs=[session_token, tt_name, tt_event_type_id,
                                    tt_package_id, tt_items],
                            outputs=[task_templates_list])
        asg_btn.click(do_assign_task,
                      inputs=[session_token, asg_task_id, asg_employee_id],
                      outputs=[plan_md])
        st_btn.click(do_update_status,
                     inputs=[session_token, st_task_id, st_status, st_note],
                     outputs=[plan_md])
        edit_btn.click(do_update_task_details,
                       inputs=[session_token, edit_task_id, edit_name, edit_desc,
                               edit_priority, edit_due, edit_service_id],
                       outputs=[plan_md])
        hist_btn.click(do_task_history, inputs=[session_token, hist_task_id],
                       outputs=[task_hist_md])

    # ================================================================= STAFF
    with reg.tab("tab.staff"):
        reg.markdown("ops.staff_heading")
        staff_list = gr.Markdown("")
        refresh_staff_btn = reg.button("ops.refresh_staff")
        with gr.Row():
            prof_employee_id = reg.number("ops.prof_employee", precision=0)
            prof_view_btn = reg.button("ops.view_profile")
        profile_md = gr.Markdown("")
        with gr.Row():
            upd_employee_id = reg.number("ops.upd_employee", precision=0)
            upd_full_name = reg.textbox("ops.upd_full_name")
            upd_phone = reg.textbox("ops.upd_phone")
            upd_email = reg.textbox("ops.upd_email")
            upd_specialties = reg.textbox("ops.upd_specialties")
            upd_btn = reg.button("ops.update_profile", variant="primary")
        reg.markdown("ops.roles_heading")
        with gr.Row():
            roles_employee_id = reg.number("ops.roles_employee", precision=0)
            roles_text = reg.textbox("ops.roles_text", placeholder="Staff, Receptionist")
            roles_btn = reg.button("ops.set_roles")
        with gr.Row():
            active_employee_id = reg.number("ops.active_employee", precision=0)
            active_check = reg.checkbox("ops.active_check", value=True)
            active_btn = reg.button("ops.set_active")
        reg.markdown("ops.avail_heading")
        with gr.Row():
            av_employee_id = reg.number("ops.av_employee", precision=0)
            av_date = reg.textbox("ops.av_date", placeholder="2026-12-05")
            av_kind = reg.dropdown_t(
                "ops.av_kind",
                [("ops.av_available", "available"),
                 ("ops.av_unavailable", "unavailable"),
                 ("ops.av_time_off", "time_off")],
                value="available")
            av_start = reg.textbox("ops.av_start")
            av_end = reg.textbox("ops.av_end")
            av_note = reg.textbox("ops.av_note")
            av_set_btn = reg.button("ops.set_availability")
        with gr.Row():
            av_list_employee_id = reg.number("ops.av_list_employee", precision=0)
            av_list_date = reg.textbox("ops.av_list_date")
            av_list_btn = reg.button("ops.list_availability")
            av_del_id = reg.number("ops.av_del_id", precision=0)
            av_del_btn = reg.button("ops.delete_entry")
        availability_md = gr.Markdown("")

        def _staff_md(lang: str):
            rows = [[e["id"], e["username"], e["full_name"], e["phone"],
                     e["email"], e["specialties"], ", ".join(e["roles"]),
                     _yes_no(lang, e["is_active"])]
                    for e in staff.list_employees(_system_employee())]
            return dir_wrap(lang, _md_table(
                [t(lang, "ops.table_id"), t(lang, "ops.table_username"),
                 t(lang, "ops.table_full_name"), t(lang, "ops.table_phone"),
                 t(lang, "ops.table_email"), t(lang, "ops.table_specialties"),
                 t(lang, "ops.table_roles"), t(lang, "ops.table_active")],
                rows, lang))

        def do_refresh_staff(token):
            employee(token)
            return _staff_md(_lang(token))

        def do_view_profile(token, employee_id):
            emp = employee(token)
            lang = _lang(token)
            if not employee_id:
                raise gr.Error(t(lang, "ops.msg_need_employee_id"))
            try:
                profile = staff.get_profile(emp, int(employee_id))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            counts = profile["task_counts"]
            lines = [
                f"**{profile['full_name']}** (@{profile['username']})",
                f"{t(lang, 'ops.profile_roles')}: {', '.join(profile['roles']) or '—'}  |  "
                f"{t(lang, 'ops.profile_phone')}: {profile['phone'] or '—'}  |  "
                f"{t(lang, 'ops.profile_email')}: {profile['email'] or '—'}",
                f"{t(lang, 'ops.profile_specialties')}: {profile['specialties'] or '—'}  |  "
                f"{t(lang, 'ops.profile_active')}: {_yes_no(lang, profile['is_active'])}",
                t(lang, "ops.profile_tasks").format(
                    pending=counts["pending"], in_progress=counts["in_progress"],
                    completed=counts["completed"]),
            ]
            return dir_wrap(lang, "\n\n".join(lines))

        def do_update_profile(token, employee_id, full_name, phone, email, specialties):
            emp = employee(token)
            lang = _lang(token)
            if not employee_id:
                raise gr.Error(t(lang, "ops.msg_need_employee_id"))
            try:
                # Empty fields are left unchanged (clearing a field is not
                # supported from this screen).
                staff.update_profile(
                    emp, int(employee_id),
                    full_name=(full_name or "").strip() or None,
                    phone=(phone or "").strip() or None,
                    email=(email or "").strip() or None,
                    specialties=(specialties or "").strip() or None)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "ops.msg_profile_updated"))
            return _staff_md(lang)

        def do_set_roles(token, employee_id, roles_text):
            emp = employee(token)
            lang = _lang(token)
            if not employee_id:
                raise gr.Error(t(lang, "ops.msg_need_employee_id"))
            roles = [r.strip() for r in (roles_text or "").split(",") if r.strip()]
            try:
                staff.set_roles(emp, int(employee_id), roles)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "ops.msg_roles_updated").format(id=int(employee_id)))
            return _staff_md(lang)

        def do_set_active(token, employee_id, active):
            emp = employee(token)
            lang = _lang(token)
            if not employee_id:
                raise gr.Error(t(lang, "ops.msg_need_employee_id"))
            try:
                staff.update_profile(emp, int(employee_id), is_active=bool(active))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "ops.msg_employee_active").format(
                id=int(employee_id),
                state=t(lang, "common.active" if active else "common.inactive")))
            return _staff_md(lang)

        def do_set_availability(token, employee_id, date, kind, start, end, note):
            emp = employee(token)
            lang = _lang(token)
            if not employee_id:
                raise gr.Error(t(lang, "ops.msg_need_employee_id"))
            try:
                staff.set_availability(
                    emp, int(employee_id), date=(date or "").strip(),
                    start_time=(start or "").strip() or None,
                    end_time=(end or "").strip() or None,
                    kind=kind, note=note or "")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "ops.msg_avail_set").format(id=int(employee_id), date=date))
            return _staff_md(lang)

        def do_list_availability(token, employee_id, date):
            emp = employee(token)
            lang = _lang(token)
            try:
                rows = staff.list_availability(
                    emp, target_id=int(employee_id) if employee_id else None,
                    date=(date or "").strip() or None)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return dir_wrap(lang, _md_table(
                [t(lang, "ops.table_id"), t(lang, "ops.table_employee"),
                 t(lang, "ops.table_date"), t(lang, "ops.table_start"),
                 t(lang, "ops.table_end"), t(lang, "ops.table_kind"),
                 t(lang, "ops.table_note")],
                [[r["id"], r["employee_id"], r["date"],
                  r["start_time"] or "—", r["end_time"] or "—",
                  t(lang, AV_KIND_KEYS.get(r["kind"], "ops.av_available")),
                  r["note"] or "—"] for r in rows], lang)
)
        def do_delete_availability(token, entry_id):
            emp = employee(token)
            lang = _lang(token)
            if not entry_id:
                raise gr.Error(t(lang, "ops.msg_need_avail_id"))
            try:
                staff.delete_availability(emp, int(entry_id))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "ops.msg_avail_deleted").format(id=int(entry_id)))
            return ""

        refresh_staff_btn.click(do_refresh_staff, inputs=[session_token],
                                outputs=[staff_list])
        prof_view_btn.click(do_view_profile, inputs=[session_token, prof_employee_id],
                            outputs=[profile_md])
        upd_btn.click(do_update_profile,
                      inputs=[session_token, upd_employee_id, upd_full_name,
                              upd_phone, upd_email, upd_specialties],
                      outputs=[staff_list])
        roles_btn.click(do_set_roles,
                        inputs=[session_token, roles_employee_id, roles_text],
                        outputs=[staff_list])
        active_btn.click(do_set_active,
                         inputs=[session_token, active_employee_id, active_check],
                         outputs=[staff_list])
        av_set_btn.click(do_set_availability,
                         inputs=[session_token, av_employee_id, av_date, av_kind,
                                 av_start, av_end, av_note],
                         outputs=[staff_list])
        av_list_btn.click(do_list_availability,
                          inputs=[session_token, av_list_employee_id, av_list_date],
                          outputs=[availability_md])
        av_del_btn.click(do_delete_availability, inputs=[session_token, av_del_id],
                         outputs=[availability_md])

    # ========================================================== NOTIFICATIONS
    with reg.tab("tab.notifications"):
        reg.markdown("ops.notif_heading")
        notif_md = gr.Markdown("")
        refresh_notif_btn = reg.button("ops.refresh_inbox")
        with gr.Row():
            notif_show_read = reg.checkbox("ops.notif_show_read", value=False)
            notif_id = reg.number("ops.notif_id", precision=0)
            notif_read_btn = reg.button("ops.mark_read")
            notif_read_all_btn = reg.button("ops.mark_all_read")
        reg.markdown("ops.notif_config_heading")
        notif_config_md = gr.Markdown("")

        def _notif_md(emp, lang, show_read: bool):
            items = notifications.inbox(emp, unread_only=not show_read)
            rows = []
            for n in items:
                title, body = format_notification(lang, n)
                rows.append([n["id"],
                             t(lang, NOTIF_TYPE_KEYS.get(n["type"], "ops.notif_type_new_booking")),
                             title, body[:80],
                             t(lang, PRIORITY_KEYS.get(n["priority"], "ops.priority_normal")),
                             _yes_no(lang, n["is_read"]), n["created_at"]])
            return dir_wrap(lang, _md_table(
                [t(lang, "ops.table_id"), t(lang, "ops.table_type"),
                 t(lang, "ops.table_title"), t(lang, "ops.table_message"),
                 t(lang, "ops.table_priority"), t(lang, "ops.table_read"),
                 t(lang, "ops.table_created")], rows, lang))

        def do_refresh_notif(token, show_read):
            emp = employee(token)
            lang = _lang(token)
            unread = notifications.unread_count(emp)
            # The config is Owner-managed (settings.view); other roles
            # just see the note.
            if has_permission(db, emp, "settings.view"):
                config = notifications.get_config(emp)
                config_text = "```json\n" + json.dumps(config, indent=2) + "\n```"
            else:
                config_text = f"_{t(lang, 'ops.msg_config_owner_only')}_"
            gr.Info(t(lang, "ops.msg_unread").format(count=unread))
            return _notif_md(emp, lang, show_read), dir_wrap(lang, config_text)

        def do_mark_read(token, notification_id):
            emp = employee(token)
            lang = _lang(token)
            if not notification_id:
                raise gr.Error(t(lang, "ops.msg_need_notif_id"))
            try:
                notifications.mark_read(emp, int(notification_id))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "ops.msg_notif_read").format(id=int(notification_id)))
            return _notif_md(emp, lang, False)

        def do_mark_all_read(token):
            emp = employee(token)
            lang = _lang(token)
            count = notifications.mark_all_read(emp)
            gr.Info(t(lang, "ops.msg_notif_all_read").format(count=count))
            return _notif_md(emp, lang, False)

        refresh_notif_btn.click(do_refresh_notif,
                                inputs=[session_token, notif_show_read],
                                outputs=[notif_md, notif_config_md])
        notif_read_btn.click(do_mark_read, inputs=[session_token, notif_id],
                             outputs=[notif_md])
        notif_read_all_btn.click(do_mark_all_read, inputs=[session_token],
                                 outputs=[notif_md])

    result = reg.result()
    # A-94: populate the contracts/templates/task-templates/staff/
    # notifications lists right after sign-in (session/permission-
    # tolerant wrappers, see ui.live_refresh). The notifications
    # refresh loads unread only, as its button does; it also shows a
    # brief unread-count toast on login, by design.
    result["on_login"] = [
        (login_follow_up(auth, do_refresh_contracts,
                         "do_refresh_contracts_on_login"), [contracts_list]),
        (login_follow_up(auth, do_refresh_templates,
                         "do_refresh_templates_on_login"), [templates_list]),
        (login_follow_up(auth, do_refresh_task_templates,
                         "do_refresh_task_templates_on_login"),
         [task_templates_list]),
        (login_follow_up(auth, do_refresh_staff,
                         "do_refresh_staff_on_login"), [staff_list]),
        (login_follow_up(auth,
                         lambda token: do_refresh_notif(token, False),
                         "do_refresh_notif_on_login", n_outputs=2),
         [notif_md, notif_config_md]),
    ]
    # A-95: the contract-generation template dropdown goes stale when
    # a template is created or deleted; its choices are rebuilt by
    # the same helper as at build time.
    result["choice_entries"] = [
        (gen_template, lambda lang: _template_choices()),
    ]
    result["choice_mutation_events"] = [
        tpl_create_evt, tpl_update_evt, tpl_delete_evt,
    ]
    return result
