"""Owner settings screens (Phase 7, presentation tier, SRS §14).

Thin UI only: parse input, call ONE service method, render the
result. Editing settings needs ``settings.manage`` (Owner only in
the seed); listing needs ``settings.view``. A change affects future
transactions only — confirmed bookings keep their frozen snapshots.
"""
from __future__ import annotations

import json

import gradio as gr

from ..config.settings import AppSettings
from ..data.database import DatabaseManager
from ..logic.auth_service import AuthService
from ..logic.notification_service import NotificationService
from ..logic.report_service import ReportService
from ..logic.settings_service import SETTING_SPECS, SettingsService
from ..utils.exceptions import ApplicationError
from ..utils.logging_config import get_logger

logger = get_logger(__name__)


def _settings_md(rows: list[dict]) -> str:
    if not rows:
        return "_No settings._"
    out = ["| Setting | Group | Value |", "| --- | --- | --- |"]
    current_group = None
    for r in rows:
        if r["group"] != current_group:
            current_group = r["group"]
            out.append(f"| **{current_group}** | | |")
        display = r["value"]
        if len(display) > 80:
            display = display[:77] + "…"
        out.append(f"| {r['label']} (`{r['key']}`) | {r['group']} | `{display}` |")
    return "\n".join(out)


def build_settings_ui(db: DatabaseManager, auth: AuthService,
                      settings: AppSettings, session_token) -> dict:
    """Build the Settings tab (Owner)."""
    svc = SettingsService(db, settings)
    notifications = NotificationService(db)
    reports = ReportService(db, settings)

    def employee(token):
        emp = auth.validate_session(token)
        if emp is None:
            raise gr.Error("Your session has expired. Please log in again.")
        return emp

    key_choices = [f"{spec['label']} — {key}"
                   for key, spec in SETTING_SPECS.items()]

    with gr.Tab("Settings"):
        gr.Markdown("## Settings — Owner only (SRS §14)")
        gr.Markdown("Changes apply to future transactions only. Confirmed "
                    "bookings keep their frozen price/policy snapshots, "
                    "signed contracts and history are never rewritten.")
        with gr.Row():
            list_btn = gr.Button("Refresh settings list")
        settings_md = gr.Markdown("")

        gr.Markdown("### Edit one setting")
        with gr.Row():
            set_key = gr.Dropdown(key_choices, label="Setting")
            set_value = gr.Textbox(label="New value")
            set_btn = gr.Button("Save setting", variant="primary")
        set_msg = gr.Markdown("")

        gr.Markdown("### Notification config (JSON: type → roles + priority)")
        with gr.Row():
            notif_json = gr.Textbox(label="notification.config JSON", lines=6)
            notif_load_btn = gr.Button("Load current")
            notif_save_btn = gr.Button("Save notification config")
        notif_msg = gr.Markdown("")

        gr.Markdown("### Dashboard widgets (JSON: role → widget keys)")
        with gr.Row():
            widgets_json = gr.Textbox(label="dashboard.widgets JSON", lines=6)
            widgets_load_btn = gr.Button("Load current")
            widgets_save_btn = gr.Button("Save dashboard widgets")
        widgets_msg = gr.Markdown("")

        def do_list(token):
            emp = employee(token)
            try:
                return _settings_md(svc.list_settings(emp))
            except ApplicationError as exc:
                raise gr.Error(str(exc))

        def do_save(token, choice, value):
            emp = employee(token)
            if not choice:
                raise gr.Error("Choose a setting first.")
            key = choice.rsplit(" — ", 1)[-1]
            try:
                updated = svc.update_setting(emp, key, value or "")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(f"Setting {key} saved.")
            return (f"Saved `{key}` = `{updated['value']}`.",
                    _settings_md(svc.list_settings(emp)))

        def do_notif_load(token):
            emp = employee(token)
            try:
                return json.dumps(notifications.get_config(emp),
                                  ensure_ascii=False, indent=2)
            except ApplicationError as exc:
                raise gr.Error(str(exc))

        def do_notif_save(token, raw):
            emp = employee(token)
            try:
                config = json.loads(raw or "{}")
            except ValueError:
                raise gr.Error("Notification config must be valid JSON.")
            try:
                notifications.update_config(emp, config)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info("Notification config saved.")
            return "Notification config saved."

        def do_widgets_load(token):
            emp = employee(token)
            try:
                return json.dumps(reports.get_dashboard_config(emp),
                                  ensure_ascii=False, indent=2)
            except ApplicationError as exc:
                raise gr.Error(str(exc))

        def do_widgets_save(token, raw):
            emp = employee(token)
            try:
                config = json.loads(raw or "{}")
            except ValueError:
                raise gr.Error("Dashboard widgets must be valid JSON.")
            try:
                reports.update_dashboard_widgets(emp, config)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info("Dashboard widgets saved.")
            return "Dashboard widgets saved."

        list_btn.click(do_list, inputs=[session_token], outputs=[settings_md])
        set_btn.click(do_save, inputs=[session_token, set_key, set_value],
                      outputs=[set_msg, settings_md])
        notif_load_btn.click(do_notif_load, inputs=[session_token],
                             outputs=[notif_json])
        notif_save_btn.click(do_notif_save, inputs=[session_token, notif_json],
                             outputs=[notif_msg])
        widgets_load_btn.click(do_widgets_load, inputs=[session_token],
                               outputs=[widgets_json])
        widgets_save_btn.click(do_widgets_save,
                               inputs=[session_token, widgets_json],
                               outputs=[widgets_msg])

    return {}
