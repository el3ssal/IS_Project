"""Gradio presentation tier (Phase 1).

Thin UI only: parse input, call ONE service method, render the result.
No SQL and no business rules here — those live in logic/ and data/.
Phase 1 screens: login, a main view (who is logged in, their roles and
permissions, database status), change password, logout. Later phases
add their own *_views.py modules.

Phase 6 (SRS §16.3) added the language toggle; Phase 8 completes it:
every tab builder returns its label/text/tab/choice/placeholder
targets and the toggle renames ALL of them — including tab titles —
in one merged update per component, and re-renders the welcome and
status lines in the new language.

Phase 8 (FR-011) adds Remember Me: the opaque persistent token is
kept in the browser's localStorage through ``gr.BrowserState``
(storage key ``whms-remember-me``) and validated/rotated by
``AuthService`` on page load. The password is never stored on the
client; the token is a random bearer value hashed at rest, 30-day
expiry, at most 3 live tokens per employee, revoked on logout and on
password change. ``WEDDING_HALL_BROWSER_SECRET`` (optional) makes
the stored value survive server restarts; without it Gradio generates
a fresh secret per process and a restart simply asks the user to log
in again. The token's security does not depend on that secret — it is
validated server-side.
"""
from __future__ import annotations

import os

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

    with gr.Blocks(title=t("ar", "app.title")) as demo:
        session_token = gr.State(None)
        current_user = gr.State(None)
        # Session language (Phase 6, SRS §16.3): Arabic is the venue
        # default; the toggle below switches it and persists the
        # per-user preference (logic/preferences.py).
        lang_state = gr.State("ar")
        # Remember Me (Phase 8, FR-011): see the module docstring.
        remember_state = gr.BrowserState(
            None, storage_key="whms-remember-me",
            secret=os.environ.get("WEDDING_HALL_BROWSER_SECRET") or None)

        # -- login view -------------------------------------------------
        # Built in the venue default language (Arabic); the toggle in
        # the main view switches the language after login.
        with gr.Column(visible=True) as login_view:
            title_md = gr.Markdown(t("ar", "app.title_heading"))
            hint_md = gr.Markdown(t("ar", "app.login_hint"))
            username = gr.Textbox(label=t("ar", "login.username"),
                                  placeholder="owner")
            password = gr.Textbox(label=t("ar", "login.password"),
                                  type="password")
            remember_cb = gr.Checkbox(label=t("ar", "login.remember_me"),
                                      value=False)
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
            cp_heading = gr.Markdown(t("ar", "login.change_password_heading"))
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
            # Phase 7 screens: calendar, backup/restore, settings.
            with gr.Tabs():
                built = [
                    build_catalog_ui(db, auth, settings, session_token),
                    build_booking_ui(db, auth, settings, session_token),
                    build_operations_ui(db, auth, settings, session_token),
                    build_finance_ui(db, auth, settings, session_token),
                    build_dashboard_ui(db, auth, settings, session_token,
                                       lang_state),
                    build_search_ui(db, auth, settings, session_token,
                                    lang_state),
                    build_files_ui(db, auth, settings, session_token,
                                   lang_state),
                    build_reports_ui(db, auth, settings, session_token,
                                     lang_state),
                    build_calendar_ui(db, auth, settings, session_token),
                    build_backup_ui(db, auth, settings, session_token),
                    build_settings_ui(db, auth, settings, session_token),
                ]

            # -- language-toggle targets (Phase 8) --------------------
            # Every builder returns its label/text/tab/choice/
            # placeholder targets; the login/main chrome above adds
            # its own. A component may carry a label AND translated
            # choices (a dropdown) or a label AND a placeholder (a
            # textbox), so merge everything into ONE gr.update per
            # component — Gradio rejects the same component twice in
            # one event's outputs.
            updates: dict = {}

            def _upd(comp):
                return updates.setdefault(comp, {})

            for comp, key in [
                    (username, "login.username"),
                    (password, "login.password"),
                    (remember_cb, "login.remember_me"),
                    (lang_radio, "login.language"),
                    (old_pw, "login.current_password"),
                    (new_pw, "login.new_password")]:
                _upd(comp)["label"] = key
            for comp, key in [
                    (title_md, "app.title_heading"),
                    (hint_md, "app.login_hint"),
                    (login_btn, "login.button"),
                    (cp_heading, "login.change_password_heading"),
                    (change_pw_btn, "login.change_password"),
                    # Buttons show their text via `value`, not `label`
                    # (gr.update(label=...) on a Button raises).
                    (refresh_btn, "login.refresh"),
                    (logout_btn, "login.logout")]:
                _upd(comp)["value"] = key
            for b in built:
                for comp, key in b.get("labels", []):
                    _upd(comp)["label"] = key
                for comp, key in b.get("texts", []):
                    _upd(comp)["value"] = key
                for comp, key in b.get("tabs", []):
                    _upd(comp)["label"] = key
                for comp, key in b.get("placeholders", []):
                    _upd(comp)["placeholder"] = key
                for comp, choices in b.get("choices", {}).items():
                    _upd(comp)["choices"] = choices
                for comp, fn in b.get("choices_fns", {}).items():
                    _upd(comp)["choices_fn"] = fn
            render_fns: list = []
            for b in built:
                render_fns.extend(b.get("renders", []))
            toggle_components = list(updates)

        def _status_text(lang: str = "ar") -> str:
            tables = db.table_names()
            n_users = db.query_one("SELECT COUNT(*) AS n FROM employees")["n"]
            n_roles = db.query_one("SELECT COUNT(*) AS n FROM roles")["n"]
            version = db.current_version()
            return (
                f"**{t(lang, 'app.status_db')}:** `{settings.db_path}`  \n"
                f"**{t(lang, 'app.status_schema')}:** {version}  \n"
                f"**{t(lang, 'app.status_tables')}:** {len(tables)}  \n"
                f"**{t(lang, 'app.status_employees')}:** {n_users}  \n"
                f"**{t(lang, 'app.status_roles')}:** {n_roles}  \n"
                f"**{t(lang, 'app.status_venue')}:** {settings.venue_name} "
                f"({settings.currency}, {settings.timezone})"
            )

        def _welcome_md(lang: str, employee) -> str:
            perms = sorted(get_employee_permissions(db, employee.id))
            return (
                f"## {t(lang, 'login.welcome')}, {employee.display_name}\n"
                f"**{t(lang, 'login.username')}:** {employee.username}  \n"
                f"**{t(lang, 'login.roles')}:** {', '.join(employee.roles) or '—'}  \n"
                f"**{t(lang, 'login.permissions')} ({len(perms)}):** {', '.join(perms)}"
            )

        def _enter_main(employee, token, lang, remember_token):
            """Outputs shared by login and remember-me restore."""
            return (
                token,
                {"username": employee.username, "roles": employee.roles},
                gr.update(visible=False), gr.update(visible=True),
                _welcome_md(lang, employee), _status_text(lang), "", lang,
                gr.update(value=lang), remember_token,
            )

        def do_login(name: str, pw: str, remember: bool):
            try:
                employee, token = auth.login(name or "", pw or "")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            lang = get_language(db, employee)
            remember_token = None
            if remember:
                remember_token = auth.issue_remember_token(employee)
            else:
                # Explicit opt-out clears any token a previous login
                # stored in this browser.
                remember_token = None
            gr.Info(t(lang, "login.msg_logged_in").format(
                username=employee.username))
            return _enter_main(employee, token, lang, remember_token)

        def do_restore(stored_token):
            """Page load (FR-011): validate the stored remember token,
            rotate it, open a fresh session, and sign the employee in
            — or clear the stored token and show the login view."""
            if not stored_token:
                return (None, None, gr.update(visible=True),
                        gr.update(visible=False), "", "", "", "ar",
                        gr.update(value="ar"), None)
            employee, new_token = auth.validate_remember_token(stored_token)
            if employee is None:
                return (None, None, gr.update(visible=True),
                        gr.update(visible=False), "",
                        t("ar", "login.session_expired"), "", "ar",
                        gr.update(value="ar"), None)
            lang = get_language(db, employee)
            session = auth.create_session(employee)
            gr.Info(t(lang, "login.session_restored"))
            return _enter_main(employee, session, lang, new_token)

        def do_language(lang: str, token):
            """Language toggle (Phase 6/8, SRS §16.3): persist the
            per-user preference, rename every label/text/tab/choice/
            placeholder in the whole app, and re-render the welcome
            and status lines in the new language."""
            emp = auth.validate_session(token)
            if emp is None:
                raise gr.Error(t("ar", "login.session_expired"))
            lang = lang or "ar"
            set_language(db, emp, lang)
            outputs: list = [lang, _welcome_md(lang, emp), _status_text(lang)]
            for comp in toggle_components:
                spec = updates[comp]
                kwargs: dict = {}
                if "label" in spec:
                    kwargs["label"] = t(lang, spec["label"])
                if "value" in spec:
                    kwargs["value"] = t(lang, spec["value"])
                if "placeholder" in spec:
                    kwargs["placeholder"] = t(lang, spec["placeholder"])
                if "choices" in spec:
                    kwargs["choices"] = [(t(lang, k), v)
                                         for k, v in spec["choices"]]
                if "choices_fn" in spec:
                    kwargs["choices"] = spec["choices_fn"](lang)
                outputs.append(gr.update(**kwargs))
            for fn, fn_outputs in render_fns:
                values = fn(token, lang)
                if isinstance(values, (list, tuple)):
                    outputs.extend(values)
                else:
                    outputs.append(values)
            gr.Info(t(lang, "login.msg_language").format(
                name="العربية" if lang == "ar" else "English"))
            return outputs

        def do_logout(token, stored_token):
            auth.logout(token)
            if stored_token:
                # Revoking the stored token is best-effort: logout
                # must never fail because the token is already gone.
                try:
                    auth.revoke_remember_token(stored_token)
                except Exception:  # noqa: BLE001 - logout must succeed
                    logger.warning("remember-token revoke on logout failed",
                                   exc_info=True)
            return (
                None, None,
                gr.update(visible=True), gr.update(visible=False),
                "", "", "", None,
            )

        def do_refresh(token):
            employee = auth.validate_session(token)
            if employee is None:
                raise gr.Error(t("ar", "login.session_expired"))
            lang = get_language(db, employee)
            return _status_text(lang)

        def do_change_password(token, old: str, new: str):
            employee = auth.validate_session(token)
            if employee is None:
                raise gr.Error(t("ar", "login.session_expired"))
            lang = get_language(db, employee)
            try:
                auth.change_password(employee, old or "", new or "")
            except ApplicationError as exc:
                raise gr.Error(str(exc))
            gr.Info(t(lang, "login.msg_password_changed"))
            return "", ""

        # -- events -----------------------------------------------------
        login_outputs = [session_token, current_user, login_view, main_view,
                         welcome, status_box, login_msg, lang_state,
                         lang_radio, remember_state]
        login_btn.click(
            do_login, inputs=[username, password, remember_cb],
            outputs=login_outputs,
        )
        password.submit(
            do_login, inputs=[username, password, remember_cb],
            outputs=login_outputs,
        )
        demo.load(
            do_restore, inputs=[remember_state],
            outputs=login_outputs,
        )
        logout_btn.click(
            do_logout, inputs=[session_token, remember_state],
            outputs=[session_token, current_user, login_view, main_view,
                     welcome, status_box, login_msg, remember_state],
        )
        # Language toggle: outputs are the language state, the welcome
        # and status lines, one merged update per toggle component,
        # and the dashboard re-render outputs.
        lang_outputs: list = [lang_state, welcome, status_box]
        lang_outputs.extend(toggle_components)
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
