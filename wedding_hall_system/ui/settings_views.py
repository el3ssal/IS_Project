"""Owner settings screens (Phase 7, presentation tier, SRS §14;
Phase 8: fully bilingual, SRS §16.3).

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
from .i18n import LabelRegistry, dir_wrap, lang_for_token, t
from .live_refresh import login_follow_up

logger = get_logger(__name__)


def _settings_md(rows: list[dict], lang: str) -> str:
    if not rows:
        return dir_wrap(lang, f"_{t(lang, 'set.msg_no_settings')}_")
    out = [f"| {t(lang, 'set.table_setting')} | {t(lang, 'set.table_group')} | "
           f"{t(lang, 'set.table_value')} |", "| --- | --- | --- |"]
    current_group = None
    for r in rows:
        # t() returns the key itself when missing — fall back to the
        # service's label/group for settings without a translation.
        group_key = f"set.group.{r['group']}"
        group = t(lang, group_key)
        if group == group_key:
            group = r["group"]
        if r["group"] != current_group:
            current_group = r["group"]
            out.append(f"| **{group}** | | |")
        label_key = f"set.label.{r['key']}"
        label = t(lang, label_key)
        if label == label_key:
            label = r["label"]
        display = r["value"]
        if len(display) > 80:
            display = display[:77] + "…"
        out.append(f"| {label} (`{r['key']}`) | {group} | `{display}` |")
    return dir_wrap(lang, "\n".join(out))


def build_settings_ui(db: DatabaseManager, auth: AuthService,
                      settings: AppSettings, session_token) -> dict:
    """Build the Settings tab (Owner). Returns the LabelRegistry
    result for the language toggle."""
    reg = LabelRegistry()
    svc = SettingsService(db, settings)
    notifications = NotificationService(db)
    reports = ReportService(db, settings)

    def _lang(token) -> str:
        return lang_for_token(db, auth, token)

    def employee(token):
        emp = auth.validate_session(token)
        if emp is None:
            raise gr.Error(t(_lang(token), "login.session_expired"))
        return emp

    with reg.tab("tab.settings"):
        reg.markdown("set.heading")
        reg.markdown("set.help")
        with gr.Row():
            list_btn = reg.button("set.refresh")
        settings_md = gr.Markdown("")

        reg.markdown("set.edit_heading")
        with gr.Row():
            set_key = reg.dropdown_data(
                "set.key",
                lambda lang: [(f"{t(lang, 'set.label.' + key)} — {key}", key)
                              for key in SETTING_SPECS])
            set_value = reg.textbox("set.value")
            set_btn = reg.button("set.save", variant="primary")
        set_msg = gr.Markdown("")

        reg.markdown("set.notif_heading")
        with gr.Row():
            notif_json = reg.textbox("set.notif_json", lines=6)
            notif_load_btn = reg.button("set.load")
            notif_save_btn = reg.button("set.save_notif")
        notif_msg = gr.Markdown("")

        reg.markdown("set.widgets_heading")
        with gr.Row():
            widgets_json = reg.textbox("set.widgets_json", lines=6)
            widgets_load_btn = reg.button("set.load")
            widgets_save_btn = reg.button("set.save_widgets")
        widgets_msg = gr.Markdown("")

        def do_list(token):
            emp = employee(token)
            lang = _lang(token)
            try:
                return _settings_md(svc.list_settings(emp), lang)
            except ApplicationError as exc:
                raise gr.Error(str(exc))

        def do_save(token, key, value):
            emp = employee(token)
            lang = _lang(token)
            if not key:
                raise gr.Error(t(lang, "set.msg_choose"))
            try:
                updated = svc.update_setting(emp, key, value or "")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "set.msg_saved").format(key=key))
            return (dir_wrap(lang, t(lang, "set.msg_saved_out").format(
                        key=key, value=updated["value"])),
                    _settings_md(svc.list_settings(emp), lang))

        def do_notif_load(token):
            emp = employee(token)
            try:
                return json.dumps(notifications.get_config(emp),
                                  ensure_ascii=False, indent=2)
            except ApplicationError as exc:
                raise gr.Error(str(exc))

        def do_notif_save(token, raw):
            emp = employee(token)
            lang = _lang(token)
            try:
                config = json.loads(raw or "{}")
            except ValueError:
                raise gr.Error(t(lang, "set.msg_bad_json_notif"))
            try:
                notifications.update_config(emp, config)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "set.msg_notif_saved"))
            return dir_wrap(lang, t(lang, "set.msg_notif_saved"))

        def do_widgets_load(token):
            emp = employee(token)
            try:
                return json.dumps(reports.get_dashboard_config(emp),
                                  ensure_ascii=False, indent=2)
            except ApplicationError as exc:
                raise gr.Error(str(exc))

        def do_widgets_save(token, raw):
            emp = employee(token)
            lang = _lang(token)
            try:
                config = json.loads(raw or "{}")
            except ValueError:
                raise gr.Error(t(lang, "set.msg_bad_json_widgets"))
            try:
                reports.update_dashboard_widgets(emp, config)
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "set.msg_widgets_saved"))
            return dir_wrap(lang, t(lang, "set.msg_widgets_saved"))

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

    result = reg.result()
    # A-94: populate the settings list right after sign-in (session/
    # permission-tolerant wrapper, see ui.live_refresh). A non-Owner
    # role has no settings.view permission, so the list simply stays
    # empty for them instead of popping an error at login.
    result["on_login"] = [
        (login_follow_up(auth, do_list,
                         "do_list_settings_on_login"), [settings_md]),
    ]
    return result
