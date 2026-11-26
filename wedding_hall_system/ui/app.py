"""Gradio presentation tier (Phase 1).

Thin UI only: parse input, call ONE service method, render the result.
No SQL and no business rules here — those live in logic/ and data/.
Phase 1 screens: login, a main view (who is logged in, their roles and
permissions, database status), change password, logout. Nothing else
is shown yet: later phases add their own *_views.py modules.
"""
from __future__ import annotations

import gradio as gr

from ..config.settings import AppSettings
from ..data.database import DatabaseManager
from ..logic.auth_service import AuthService
from ..logic.permissions import get_employee_permissions
from ..logic.preferences import get_language, set_language
from ..ui.backup_views import build_backup_ui
from ..ui.booking_views import build_booking_ui
from ..ui.calendar_views import build_calendar_ui
from ..ui.catalog_views import build_catalog_ui
from ..ui.dashboard_views import build_dashboard_ui
from ..ui.files_views import build_files_ui
from ..ui.finance_views import build_finance_ui
from ..ui.i18n import t
from ..ui.operations_views import build_operations_ui
from ..ui.reports_views import build_reports_ui
from ..ui.search_views import build_search_ui
from ..ui.settings_views import build_settings_ui
from ..utils.exceptions import ApplicationError
from ..utils.logging_config import get_logger

logger = get_logger(__name__)


def build_app(settings: AppSettings | None = None,
              db: DatabaseManager | None = None) -> gr.Blocks:
    settings = settings or AppSettings.from_env()
    db = db or DatabaseManager(settings.db_path)
    db.initialize()
    from ..data.seed import seed

    seed(db)
    auth = AuthService(db, settings)

    with gr.Blocks(title="Wedding Hall Management System") as demo:
        session_token = gr.State(None)
        current_user = gr.State(None)
        # Session language (Phase 6, SRS §16.3): Arabic is the venue
        # default; the toggle below switches it and persists the
        # per-user preference (logic/preferences.py).
        lang_state = gr.State("ar")

        # -- login view -------------------------------------------------
        # Built in the venue default language (Arabic); the toggle in
        # the main view switches the language after login.
        with gr.Column(visible=True) as login_view:
            gr.Markdown(f"# {t('ar', 'app.title')}")
            gr.Markdown(t("ar", "app.login_hint"))
            username = gr.Textbox(label=t("ar", "login.username"),
                                  placeholder="owner")
            password = gr.Textbox(label=t("ar", "login.password"),
                                  type="password")
            login_btn = gr.Button(t("ar", "login.button"), variant="primary")
            login_msg = gr.Markdown("")

        # -- main view --------------------------------------------------
        with gr.Column(visible=False) as main_view:
            welcome = gr.Markdown("")
            with gr.Row():
                refresh_btn = gr.Button(t("ar", "login.refresh"))
                logout_btn = gr.Button(t("ar", "login.logout"))
                lang_radio = gr.Radio(
                    choices=[("العربية", "ar"), ("English", "en")],
                    value="ar", label=t("ar", "login.language"))
            status_box = gr.Markdown("")
            gr.Markdown(f"## {t('ar', 'login.change_password')}")
            old_pw = gr.Textbox(label=t("ar", "login.current_password"),
                                type="password")
            new_pw = gr.Textbox(label=t("ar", "login.new_password"),
                                type="password")
            change_pw_btn = gr.Button(t("ar", "login.change_password"))

            # Phase 2 screens: halls, hours, maintenance, event types,
            # packages, services, pricing rules, price calculator.
            # Phase 3 screens: customers, quotations, bookings.
            # Phase 4 screens: contracts, contract templates, event
            # plans & tasks, staff, notifications.
            # Phase 5 screens: payments/corrections, installments,
            # expenses, refunds, policy snapshots, reconciliation.
            # Phase 6 screens: dashboard, search, files, reports.
            with gr.Tabs():
                build_catalog_ui(db, auth, settings, session_token)
                build_booking_ui(db, auth, settings, session_token)
                build_operations_ui(db, auth, settings, session_token)
                build_finance_ui(db, auth, settings, session_token)
                phase6 = [
                    build_dashboard_ui(db, auth, settings, session_token,
                                       lang_state),
                    build_search_ui(db, auth, settings, session_token,
                                    lang_state),
                    build_files_ui(db, auth, settings, session_token,
                                   lang_state),
                    build_reports_ui(db, auth, settings, session_token,
                                     lang_state),
                ]
                # Phase 7 screens: calendar, backup/restore, settings.
                build_calendar_ui(db, auth, settings, session_token)
                build_backup_ui(db, auth, settings, session_token)
                build_settings_ui(db, auth, settings, session_token)
            # Label targets the language toggle renames, plus the
            # render functions it re-runs (dashboard re-renders in the
            # new language; other tabs re-render on their next click).
            label_targets = [
                (refresh_btn, "login.refresh"),
                (logout_btn, "login.logout"),
                (lang_radio, "login.language"),
                (old_pw, "login.current_password"),
                (new_pw, "login.new_password"),
                (change_pw_btn, "login.change_password"),
            ]
            render_fns: list = []
            for built in phase6:
                label_targets.extend(built.get("labels", []))
                render_fns.extend(built.get("renders", []))

        def _status_text() -> str:
            tables = db.table_names()
            n_users = db.query_one("SELECT COUNT(*) AS n FROM employees")["n"]
            n_roles = db.query_one("SELECT COUNT(*) AS n FROM roles")["n"]
            version = db.current_version()
            return (
                f"**Database:** `{settings.db_path}`  \n"
                f"**Schema version:** {version}  \n"
                f"**Tables:** {len(tables)}  \n"
                f"**Employee accounts:** {n_users}  \n"
                f"**Roles:** {n_roles}  \n"
                f"**Venue:** {settings.venue_name} ({settings.currency}, {settings.timezone})"
            )

        def do_login(name: str, pw: str):
            try:
                employee, token = auth.login(name or "", pw or "")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            perms = sorted(get_employee_permissions(db, employee.id))
            lang = get_language(db, employee)
            welcome_md = (
                f"## {t(lang, 'login.welcome')}, {employee.display_name}\n"
                f"**{t(lang, 'login.username')}:** {employee.username}  \n"
                f"**{t(lang, 'login.roles')}:** {', '.join(employee.roles) or '—'}  \n"
                f"**{t(lang, 'login.permissions')} ({len(perms)}):** {', '.join(perms)}"
            )
            gr.Info(f"Logged in as {employee.username}.")
            return (
                token,
                {"username": employee.username, "roles": employee.roles},
                gr.update(visible=False), gr.update(visible=True),
                welcome_md, _status_text(), "", lang,
                gr.update(value=lang),
            )

        def do_language(lang: str, token):
            """Language toggle (Phase 6, SRS §16.3): persist the
            per-user preference, rename every Phase 6 input label, and
            re-render the dashboard in the new language."""
            emp = auth.validate_session(token)
            if emp is None:
                raise gr.Error("Your session has expired. Please log in again.")
            lang = lang or "ar"
            set_language(db, emp, lang)
            outputs: list = [lang]
            for component, key in label_targets:
                outputs.append(gr.update(label=t(lang, key)))
            for fn, fn_outputs in render_fns:
                values = fn(token, lang)
                if isinstance(values, (list, tuple)):
                    outputs.extend(values)
                else:
                    outputs.append(values)
            gr.Info("Language: العربية" if lang == "ar" else "Language: English")
            return outputs

        def do_logout(token):
            auth.logout(token)
            return (
                None, None,
                gr.update(visible=True), gr.update(visible=False),
                "", "", "",
            )

        def do_refresh(token):
            employee = auth.validate_session(token)
            if employee is None:
                raise gr.Error("Your session has expired. Please log in again.")
            return _status_text()

        def do_change_password(token, old: str, new: str):
            employee = auth.validate_session(token)
            if employee is None:
                raise gr.Error("Your session has expired. Please log in again.")
            try:
                auth.change_password(employee, old or "", new or "")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info("Password changed.")
            return "", ""

        login_btn.click(
            do_login, inputs=[username, password],
            outputs=[session_token, current_user, login_view, main_view,
                     welcome, status_box, login_msg, lang_state, lang_radio],
        )
        password.submit(
            do_login, inputs=[username, password],
            outputs=[session_token, current_user, login_view, main_view,
                     welcome, status_box, login_msg, lang_state, lang_radio],
        )
        logout_btn.click(
            do_logout, inputs=[session_token],
            outputs=[session_token, current_user, login_view, main_view,
                     welcome, status_box, login_msg],
        )
        # Language toggle: outputs are the language state, one label
        # update per label target, and the dashboard re-render outputs.
        lang_outputs: list = [lang_state]
        lang_outputs.extend(comp for comp, _ in label_targets)
        for _, fn_outputs in render_fns:
            lang_outputs.extend(fn_outputs)
        lang_radio.change(do_language, inputs=[lang_radio, session_token],
                          outputs=lang_outputs)
        refresh_btn.click(do_refresh, inputs=[session_token], outputs=[status_box])
        change_pw_btn.click(
            do_change_password, inputs=[session_token, old_pw, new_pw],
            outputs=[old_pw, new_pw],
        )

    return demo
