"""Contracts, event plans, tasks, staff and notifications (Phase 4).

Thin UI only: parse input, call ONE service method, render the result.
No SQL and no business rules here. Every handler resolves the session
token to an employee and lets the service enforce permissions; a denied
action raises gr.Error with the service's message.

Contracts are versioned HTML documents (no PDF library is permitted
yet — A-38): viewing a version renders the frozen HTML in the page,
printable to PDF from the browser. Signed copies are uploaded as files
and downloaded only through the authorized service handler.
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

logger = get_logger(__name__)


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "_No records yet._"
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def build_operations_ui(db: DatabaseManager, auth: AuthService,
                        settings: AppSettings, session_token) -> None:
    """Build the Phase 4 tabs. Must be called inside a gr.Blocks
    context, after the login/main views exist."""
    contracts = ContractService(db, settings)
    tasks = TaskService(db)
    staff = StaffService(db)
    notifications = NotificationService(db)

    def employee(token):
        emp = auth.validate_session(token)
        if emp is None:
            raise gr.Error("Your session has expired. Please log in again.")
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
    with gr.Tab("Contracts"):
        gr.Markdown("### Contracts — versioned HTML documents (printable to PDF "
                    "from the browser). Generate only from a Confirmed booking; "
                    "a new version preserves the old ones.")
        contracts_list = gr.Markdown("")
        refresh_contracts_btn = gr.Button("Refresh contracts")
        with gr.Row():
            gen_booking_id = gr.Number(label="Booking ID", precision=0)
            gen_template = gr.Dropdown(_template_choices(), label="Template")
            gen_contract_btn = gr.Button("Generate contract", variant="primary")
        with gr.Row():
            view_contract_id = gr.Number(label="Contract ID", precision=0)
            view_version = gr.Number(label="Version", precision=0, value=1)
            view_contract_btn = gr.Button("View version")
        contract_html = gr.HTML("")
        with gr.Row():
            newver_contract_id = gr.Number(label="Contract ID", precision=0)
            newver_btn = gr.Button("Create new version (re-render from template)")
        with gr.Row():
            sig_contract_id = gr.Number(label="Contract ID", precision=0)
            sig_status = gr.Dropdown(
                ["draft", "pending_signature", "partially_signed", "signed",
                 "cancelled"], value="pending_signature", label="Signature status")
            sig_date = gr.Textbox(label="Signature date (YYYY-MM-DD, required to sign)",
                                  placeholder="2026-11-27")
            sig_btn = gr.Button("Set signature status")
        gr.Markdown("#### Signed copy upload")
        with gr.Row():
            up_contract_id = gr.Number(label="Contract ID", precision=0)
            up_file = gr.File(label="Signed contract file (PDF, JPG or PNG, max 10 MB)",
                              type="filepath")
            up_date = gr.Textbox(label="Signature date (YYYY-MM-DD)",
                                 placeholder="2026-11-27")
            up_btn = gr.Button("Upload signed copy", variant="primary")
        gr.Markdown("#### Attachments")
        with gr.Row():
            att_contract_id = gr.Number(label="Contract ID", precision=0)
            att_refresh_btn = gr.Button("List attachments")
            att_download_id = gr.Number(label="Attachment ID to download", precision=0)
            att_download_btn = gr.Button("Download attachment")
        attachments_list = gr.Markdown("")
        att_file = gr.File(label="Downloaded attachment")

        def _contracts_md():
            rows = [[c.id, c.number, c.booking_id, c.template_name,
                     c.current_version_id or "—", c.signature_status,
                     c.signature_date or "—"]
                    for c in contracts.list_contracts(_system_employee())]
            return _md_table(["ID", "Number", "Booking", "Template",
                              "Current version", "Signature", "Signed on"], rows)

        def do_refresh_contracts(token):
            employee(token)
            return _contracts_md()

        def do_generate_contract(token, booking_id, template_name):
            emp = employee(token)
            if not booking_id:
                raise gr.Error("Enter a booking ID first.")
            if not template_name:
                raise gr.Error("Pick a contract template first.")
            try:
                contract = contracts.generate_contract(
                    emp, int(booking_id), template_name)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"Contract {contract.number} generated (version 1).")
            return _contracts_md()

        def do_view_contract(token, contract_id, version):
            emp = employee(token)
            if not contract_id:
                raise gr.Error("Enter a contract ID first.")
            try:
                body = contracts.get_version_body(
                    emp, int(contract_id), int(version or 1))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return body

        def do_new_version(token, contract_id):
            emp = employee(token)
            if not contract_id:
                raise gr.Error("Enter a contract ID first.")
            try:
                version = contracts.create_new_version(emp, int(contract_id))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"Version {version.version} created; older versions preserved.")
            return _contracts_md()

        def do_set_signature(token, contract_id, status, date):
            emp = employee(token)
            if not contract_id:
                raise gr.Error("Enter a contract ID first.")
            try:
                contract = contracts.set_signature_status(
                    emp, int(contract_id), status,
                    signature_date=(date or "").strip() or None)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"Contract {contract.number} is now {contract.signature_status}.")
            return _contracts_md()

        def do_upload_signed(token, contract_id, file_path, date):
            emp = employee(token)
            if not contract_id:
                raise gr.Error("Enter a contract ID first.")
            if not file_path:
                raise gr.Error("Choose a file to upload.")
            try:
                result = contracts.upload_signed_copy(
                    emp, int(contract_id), str(file_path),
                    original_filename=str(file_path).rsplit("/", 1)[-1],
                    signature_date=(date or "").strip())
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"Signed copy stored (sha256 {result['sha256'][:12]}…); "
                    "contract marked signed.")
            return _contracts_md()

        def do_list_attachments(token, contract_id):
            emp = employee(token)
            if not contract_id:
                raise gr.Error("Enter a contract ID first.")
            try:
                rows = contracts.list_attachments(emp, int(contract_id))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return _md_table(
                ["ID", "Original name", "Category", "Size (bytes)",
                 "Uploaded by", "Uploaded at"],
                [[r["id"], r["original_filename"], r["category"], r["size_bytes"],
                  r["uploaded_by"], r["uploaded_at"]] for r in rows])

        def do_download_attachment(token, attachment_id):
            emp = employee(token)
            if not attachment_id:
                raise gr.Error("Enter an attachment ID first.")
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
    with gr.Tab("Contract Templates"):
        gr.Markdown("### Contract templates — stored as settings rows. "
                    "Placeholders use `{{name}}` (see the template list for the "
                    "allowed names). Changing a template never changes contracts "
                    "already generated from it.")
        templates_list = gr.Markdown("")
        refresh_templates_btn = gr.Button("Refresh templates")
        tpl_name = gr.Textbox(label="Template name")
        tpl_body = gr.Textbox(label="Template body (HTML with {{placeholders}})",
                              lines=12)
        with gr.Row():
            tpl_create_btn = gr.Button("Create template", variant="primary")
            tpl_update_btn = gr.Button("Update template")
            tpl_delete_btn = gr.Button("Delete template")

        def _templates_md():
            rows = [[t["name"], t["key"], f"{len(t['body'])} chars"]
                    for t in contracts.list_templates(_system_employee())]
            return _md_table(["Name", "Settings key", "Body length"], rows)

        def do_refresh_templates(token):
            employee(token)
            return _templates_md()

        def do_tpl_save(token, name, body, update: bool):
            emp = employee(token)
            try:
                if update:
                    contracts.update_template(emp, name=name, body=body or "")
                    gr.Info(f"Template {name!r} updated.")
                else:
                    contracts.create_template(emp, name=name, body=body or "")
                    gr.Info(f"Template {name!r} created.")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return _templates_md()

        def do_tpl_delete(token, name):
            emp = employee(token)
            try:
                contracts.delete_template(emp, name=name)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"Template {name!r} deleted.")
            return _templates_md()

        refresh_templates_btn.click(do_refresh_templates, inputs=[session_token],
                                    outputs=[templates_list])
        tpl_create_btn.click(lambda t, n, b: do_tpl_save(t, n, b, False),
                             inputs=[session_token, tpl_name, tpl_body],
                             outputs=[templates_list])
        tpl_update_btn.click(lambda t, n, b: do_tpl_save(t, n, b, True),
                             inputs=[session_token, tpl_name, tpl_body],
                             outputs=[templates_list])
        tpl_delete_btn.click(do_tpl_delete, inputs=[session_token, tpl_name],
                             outputs=[templates_list])

    # ================================================== EVENT PLANS & TASKS
    with gr.Tab("Event Plans & Tasks"):
        gr.Markdown("### Event plans — auto-generated from a task template for a "
                    "Confirmed booking (one plan per booking). Assignments warn "
                    "on staff conflicts but never block (SRS §7.4).")
        plan_md = gr.Markdown("")
        with gr.Row():
            plan_booking_id = gr.Number(label="Booking ID", precision=0)
            plan_template_id = gr.Number(label="Template ID (empty = auto-match)",
                                         precision=0)
            plan_gen_btn = gr.Button("Generate event plan", variant="primary")
            plan_view_btn = gr.Button("View event plan")
        gr.Markdown("#### Task templates")
        task_templates_list = gr.Markdown("")
        refresh_task_templates_btn = gr.Button("Refresh task templates")
        with gr.Row():
            tt_name = gr.Textbox(label="Template name")
            tt_event_type_id = gr.Number(label="Event type ID (empty = any)",
                                         precision=0)
            tt_package_id = gr.Number(label="Package ID (empty = any)", precision=0)
        tt_items = gr.Textbox(
            label="Items — one per line: name | description | due offset (min before event) | priority",
            lines=4,
            placeholder="Confirm hall setup and seating | Check tables and stage | 180 | high")
        tt_create_btn = gr.Button("Create task template", variant="primary")
        gr.Markdown("#### Assign / update a task")
        with gr.Row():
            asg_task_id = gr.Number(label="Task ID", precision=0)
            asg_employee_id = gr.Number(label="Assign to employee ID", precision=0)
            asg_btn = gr.Button("Assign task", variant="primary")
        with gr.Row():
            st_task_id = gr.Number(label="Task ID", precision=0)
            st_status = gr.Dropdown(["pending", "in_progress", "completed"],
                                    label="New status")
            st_note = gr.Textbox(label="Note (optional)")
            st_btn = gr.Button("Update task status")
        gr.Markdown("#### Edit a generated task (Manager)")
        with gr.Row():
            edit_task_id = gr.Number(label="Task ID", precision=0)
            edit_name = gr.Textbox(label="New name (empty = unchanged)")
            edit_desc = gr.Textbox(label="New description (empty = unchanged)")
            edit_priority = gr.Dropdown(["", "low", "medium", "high", "urgent"],
                                        value="", label="New priority")
            edit_due = gr.Textbox(label="New due date/time (YYYY-MM-DD HH:MM, empty = unchanged)")
            edit_service_id = gr.Number(label="Link to service ID (empty = unchanged)",
                                        precision=0)
            edit_btn = gr.Button("Update task details")
        with gr.Row():
            hist_task_id = gr.Number(label="Task ID", precision=0)
            hist_btn = gr.Button("Show task history")
        task_hist_md = gr.Markdown("")

        def _task_templates_md():
            rows = []
            for t in tasks.list_templates(_system_employee()):
                rows.append([t["id"], t["name"], t["event_type_id"] or "any",
                             t["package_id"] or "any", len(t["items"])])
            return _md_table(["ID", "Name", "Event type", "Package", "Items"], rows)

        def _plan_tasks_md(plan: dict) -> str:
            rows = [[t["id"], t["name"], t["assigned_to"] or "—",
                     t["due_at"] or "—", t["priority"], t["status"]]
                    for t in plan["tasks"]]
            template = plan.get("template_name") or "—"
            return (f"**Event plan #{plan['id']}** — template {template}  \n"
                    + _md_table(["ID", "Task", "Assigned to", "Due at",
                                 "Priority", "Status"], rows))

        def do_generate_plan(token, booking_id, template_id):
            emp = employee(token)
            if not booking_id:
                raise gr.Error("Enter a booking ID first.")
            try:
                plan = tasks.generate_event_plan(
                    emp, int(booking_id),
                    template_id=int(template_id) if template_id else None)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"Event plan #{plan['id']} generated "
                    f"({len(plan['tasks'])} task(s)).")
            return _plan_tasks_md(plan)

        def do_view_plan(token, booking_id):
            emp = employee(token)
            if not booking_id:
                raise gr.Error("Enter a booking ID first.")
            try:
                plan = tasks.get_event_plan(emp, int(booking_id))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return _plan_tasks_md(plan)

        def do_refresh_task_templates(token):
            employee(token)
            return _task_templates_md()

        def do_create_task_template(token, name, event_type_id, package_id, items_text):
            emp = employee(token)
            items = []
            for line in (items_text or "").splitlines():
                parts = [p.strip() for p in line.split("|")]
                if len(parts) < 3 or not parts[0]:
                    continue
                try:
                    offset = int(parts[2])
                except ValueError:
                    raise gr.Error(f"Due offset must be a number: {line!r}")
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
            gr.Info(f"Task template {template['name']!r} created.")
            return _task_templates_md()

        def do_assign_task(token, task_id, employee_id):
            emp = employee(token)
            if not task_id or not employee_id:
                raise gr.Error("Enter a task ID and an employee ID first.")
            try:
                result = tasks.assign_task(emp, int(task_id), int(employee_id))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            if result.get("warnings"):
                gr.Info("Assigned WITH warnings: " + "; ".join(result["warnings"]))
            else:
                gr.Info(f"Task #{task_id} assigned to employee #{employee_id}.")
            return _md_table(["ID", "Task", "Assigned to", "Due at", "Status"],
                             [[result["id"], result["name"],
                               result["assigned_to"] or "—",
                               result["due_at"] or "—", result["status"]]])

        def do_update_status(token, task_id, status, note):
            emp = employee(token)
            if not task_id:
                raise gr.Error("Enter a task ID first.")
            try:
                task = tasks.update_task_status(emp, int(task_id), status,
                                                note=note or "")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"Task #{task_id} is now {task.status}.")
            return _md_table(["ID", "Task", "Assigned to", "Due at", "Status"],
                             [[task.id, task.name, task.assigned_to or "—",
                               task.due_at or "—", task.status]])

        def do_update_task_details(token, task_id, name, desc, priority,
                                   due, service_id):
            emp = employee(token)
            if not task_id:
                raise gr.Error("Enter a task ID first.")
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
            gr.Info(f"Task #{task_id} updated.")
            return _md_table(["ID", "Task", "Assigned to", "Due at", "Status"],
                             [[task.id, task.name, task.assigned_to or "—",
                               task.due_at or "—", task.status]])

        def do_task_history(token, task_id):
            emp = employee(token)
            if not task_id:
                raise gr.Error("Enter a task ID first.")
            try:
                rows = tasks.task_history(emp, int(task_id))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return _md_table(["When", "Changed by", "Action", "Old", "New"],
                             [[r["created_at"], r["employee_id"] or "—", r["action"],
                               r["old_value"] or "—", r["new_value"] or "—"]
                              for r in rows])

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
    with gr.Tab("Staff"):
        gr.Markdown("### Staff — the login accounts from Phase 1. Manage profiles "
                    "and availability here; roles are managed with the accounts.")
        staff_list = gr.Markdown("")
        refresh_staff_btn = gr.Button("Refresh staff")
        with gr.Row():
            prof_employee_id = gr.Number(label="Employee ID", precision=0)
            prof_view_btn = gr.Button("View profile")
        profile_md = gr.Markdown("")
        with gr.Row():
            upd_employee_id = gr.Number(label="Employee ID", precision=0)
            upd_full_name = gr.Textbox(label="Full name")
            upd_phone = gr.Textbox(label="Phone")
            upd_email = gr.Textbox(label="Email")
            upd_specialties = gr.Textbox(label="Specialties (comma separated)")
            upd_btn = gr.Button("Update profile", variant="primary")
        gr.Markdown("#### Roles and active status (Owner only)")
        with gr.Row():
            roles_employee_id = gr.Number(label="Employee ID", precision=0)
            roles_text = gr.Textbox(label="Roles (comma separated)",
                                    placeholder="Staff, Receptionist")
            roles_btn = gr.Button("Set roles")
        with gr.Row():
            active_employee_id = gr.Number(label="Employee ID", precision=0)
            active_check = gr.Checkbox(label="Active account", value=True)
            active_btn = gr.Button("Set active status")
        gr.Markdown("#### Availability")
        with gr.Row():
            av_employee_id = gr.Number(label="Employee ID", precision=0)
            av_date = gr.Textbox(label="Date (YYYY-MM-DD)", placeholder="2026-12-05")
            av_kind = gr.Dropdown(["available", "unavailable", "time_off"],
                                  value="available", label="Kind")
            av_start = gr.Textbox(label="Start time (HH:MM, optional)")
            av_end = gr.Textbox(label="End time (HH:MM, optional)")
            av_note = gr.Textbox(label="Note")
            av_set_btn = gr.Button("Set availability")
        with gr.Row():
            av_list_employee_id = gr.Number(label="Employee ID (empty = all)",
                                            precision=0)
            av_list_date = gr.Textbox(label="Date (YYYY-MM-DD, optional)")
            av_list_btn = gr.Button("List availability")
            av_del_id = gr.Number(label="Availability entry ID to delete", precision=0)
            av_del_btn = gr.Button("Delete entry")
        availability_md = gr.Markdown("")

        def _staff_md():
            rows = [[e["id"], e["username"], e["full_name"], e["phone"],
                     e["email"], e["specialties"], ", ".join(e["roles"]),
                     "yes" if e["is_active"] else "no"]
                    for e in staff.list_employees(_system_employee())]
            return _md_table(["ID", "Username", "Full name", "Phone", "Email",
                              "Specialties", "Roles", "Active"], rows)

        def do_refresh_staff(token):
            employee(token)
            return _staff_md()

        def do_view_profile(token, employee_id):
            emp = employee(token)
            if not employee_id:
                raise gr.Error("Enter an employee ID first.")
            try:
                profile = staff.get_profile(emp, int(employee_id))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            counts = profile["task_counts"]
            lines = [
                f"**{profile['full_name']}** (@{profile['username']})",
                f"Roles: {', '.join(profile['roles']) or '—'}  |  "
                f"Phone: {profile['phone'] or '—'}  |  Email: {profile['email'] or '—'}",
                f"Specialties: {profile['specialties'] or '—'}  |  "
                f"Active: {'yes' if profile['is_active'] else 'no'}",
                f"Tasks — pending {counts['pending']}, in progress "
                f"{counts['in_progress']}, completed {counts['completed']}",
            ]
            return "\n\n".join(lines)

        def do_update_profile(token, employee_id, full_name, phone, email, specialties):
            emp = employee(token)
            if not employee_id:
                raise gr.Error("Enter an employee ID first.")
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
            gr.Info("Profile updated.")
            return _staff_md()

        def do_set_roles(token, employee_id, roles_text):
            emp = employee(token)
            if not employee_id:
                raise gr.Error("Enter an employee ID first.")
            roles = [r.strip() for r in (roles_text or "").split(",") if r.strip()]
            try:
                staff.set_roles(emp, int(employee_id), roles)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"Roles updated for employee #{int(employee_id)}.")
            return _staff_md()

        def do_set_active(token, employee_id, active):
            emp = employee(token)
            if not employee_id:
                raise gr.Error("Enter an employee ID first.")
            try:
                staff.update_profile(emp, int(employee_id), is_active=bool(active))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"Employee #{int(employee_id)} is now "
                    f"{'active' if active else 'inactive'}.")
            return _staff_md()

        def do_set_availability(token, employee_id, date, kind, start, end, note):
            emp = employee(token)
            if not employee_id:
                raise gr.Error("Enter an employee ID first.")
            try:
                staff.set_availability(
                    emp, int(employee_id), date=(date or "").strip(),
                    start_time=(start or "").strip() or None,
                    end_time=(end or "").strip() or None,
                    kind=kind, note=note or "")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"Availability set for employee #{int(employee_id)} on {date}.")
            return _staff_md()

        def do_list_availability(token, employee_id, date):
            emp = employee(token)
            try:
                rows = staff.list_availability(
                    emp, target_id=int(employee_id) if employee_id else None,
                    date=(date or "").strip() or None)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            return _md_table(["ID", "Employee", "Date", "Start", "End", "Kind", "Note"],
                             [[r["id"], r["employee_id"], r["date"],
                               r["start_time"] or "—", r["end_time"] or "—",
                               r["kind"], r["note"] or "—"] for r in rows])

        def do_delete_availability(token, entry_id):
            emp = employee(token)
            if not entry_id:
                raise gr.Error("Enter an availability entry ID first.")
            try:
                staff.delete_availability(emp, int(entry_id))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"Availability entry #{int(entry_id)} deleted.")
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
    with gr.Tab("Notifications"):
        gr.Markdown("### Notifications — in-app inbox. Created on assignments, "
                    "status changes and contract events.")
        notif_md = gr.Markdown("")
        refresh_notif_btn = gr.Button("Refresh inbox")
        with gr.Row():
            notif_show_read = gr.Checkbox(label="Show read notifications", value=False)
            notif_id = gr.Number(label="Notification ID to mark read", precision=0)
            notif_read_btn = gr.Button("Mark as read")
            notif_read_all_btn = gr.Button("Mark all as read")
        gr.Markdown("#### Notification configuration (Owner-editable setting "
                    "`notification.config` — type → recipient roles and priority)")
        notif_config_md = gr.Markdown("")

        def _notif_md(emp, show_read: bool):
            items = notifications.inbox(emp, unread_only=not show_read)
            rows = [[n["id"], n["type"], n["title"], n["body"][:80],
                     n["priority"], "yes" if n["is_read"] else "no",
                     n["created_at"]] for n in items]
            return _md_table(["ID", "Type", "Title", "Message", "Priority",
                              "Read", "Created"], rows)

        def do_refresh_notif(token, show_read):
            emp = employee(token)
            unread = notifications.unread_count(emp)
            # The config is Owner-managed (settings.view); other roles
            # just see the note.
            if has_permission(db, emp, "settings.view"):
                config = notifications.get_config(emp)
                config_text = "```json\n" + json.dumps(config, indent=2) + "\n```"
            else:
                config_text = "_Notification configuration is managed by the Owner._"
            gr.Info(f"{unread} unread notification(s).")
            return _notif_md(emp, show_read), config_text

        def do_mark_read(token, notification_id):
            emp = employee(token)
            if not notification_id:
                raise gr.Error("Enter a notification ID first.")
            try:
                notifications.mark_read(emp, int(notification_id))
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"Notification #{int(notification_id)} marked as read.")
            return _notif_md(emp, False)

        def do_mark_all_read(token):
            emp = employee(token)
            count = notifications.mark_all_read(emp)
            gr.Info(f"{count} notification(s) marked as read.")
            return _notif_md(emp, False)

        refresh_notif_btn.click(do_refresh_notif,
                                inputs=[session_token, notif_show_read],
                                outputs=[notif_md, notif_config_md])
        notif_read_btn.click(do_mark_read, inputs=[session_token, notif_id],
                             outputs=[notif_md])
        notif_read_all_btn.click(do_mark_all_read, inputs=[session_token],
                                 outputs=[notif_md])
