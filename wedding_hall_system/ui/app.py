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

A-89 adds the same treatment for the *session* token itself
(storage key ``whms-session``): the footer Settings gear changes the
theme by reloading the whole page, and a reload wipes ``gr.State``
— so without this the user was logged out on every light/dark
switch. The session token is already a random opaque bearer value
hashed server-side with the same 8-hour expiry, so persisting it in
localStorage is the same trust model as Remember Me, only
short-lived; it is validated on every load, cleared on logout, and
never the password. Remember Me (30-day token) is unchanged and
stays the fallback when the session has expired.

Post-Phase 8 polish adds live RTL (``ui/rtl.py``): a hidden ``gr.HTML``
direction signal whose value is ``<span data-lang="ar|en">`` — every
handler that can change the language (login, restore, toggle, logout)
updates it — and the launch CSS flips the container direction with
``:has()`` on the signal (``RTL_JS`` is a secondary belt-and-braces
that flips ``<html dir>`` live). CSS/js are Gradio 6 launch arguments
(``__main__`` passes ``rtl.launch_kwargs()``); they must not be passed
to ``gr.Blocks(...)``. No custom theme: the app uses Gradio's default
theme, so the "Built with Gradio" footer and its light/dark settings
gear stay visible (A-85).
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
from ..ui.i18n import dir_wrap, t
from .live_refresh import ChoiceRegistry, make_refresh_choices_handler
from .rtl import dir_signal_html
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

    with gr.Blocks(title=t("en", "app.title")) as demo:
        session_token = gr.State(None)
        current_user = gr.State(None)
        # Session language (Phase 6, SRS §16.3): Arabic is the venue
        # default; the toggle below switches it and persists the
        # per-user preference (logic/preferences.py). A-93: the login
        # screen itself is English-only and LTR — the state starts
        # English and only becomes the user's language on sign-in.
        lang_state = gr.State("en")
        # Remember Me (Phase 8, FR-011): see the module docstring.
        remember_state = gr.BrowserState(
            None, storage_key="whms-remember-me",
            secret=os.environ.get("WEDDING_HALL_BROWSER_SECRET") or None)
        # Session persistence (A-89): the session token itself is
        # kept in the browser's localStorage (storage key
        # ``whms-session``) so a full page reload — e.g. the theme
        # switch in the footer Settings gear — does not log the user
        # out. Same trust model as Remember Me (random opaque token,
        # hashed server-side, 8-hour expiry), only short-lived; it is
        # validated on every load and cleared on logout. The
        # BrowserState value is the session token: login writes it,
        # restore reads it, logout clears it.
        session_state = gr.BrowserState(
            None, storage_key="whms-session",
            secret=os.environ.get("WEDDING_HALL_BROWSER_SECRET") or None)
        # RTL direction signal (ui/rtl.py): a hidden HTML component
        # whose value is `<span data-lang="ar|en">`. RTL_JS reads it
        # through the <gradio-app> shadow DOM and flips the whole page
        # direction live; every handler that can change the language
        # updates this component. Hidden via CSS, not visible=False,
        # so the element is always in the DOM for the observer.
        dir_signal = gr.HTML(dir_signal_html("en"),
                             elem_id="whms-dir-signal")

        # -- login view -------------------------------------------------
        # A-93: English-only and LTR while logged out — every label,
        # button and message on this screen is built in English, and
        # the direction signal starts "en", whatever the user's saved
        # language is. The main view switches to the user's language
        # after a successful login (or restore); logout resets the
        # login chrome back to English (do_logout below).
        with gr.Column(visible=True) as login_view:
            title_md = gr.Markdown(dir_wrap("en", t("en", "app.title_heading")))
            hint_md = gr.Markdown(dir_wrap("en", t("en", "app.login_hint")))
            username = gr.Textbox(label=t("en", "login.username"),
                                  placeholder="owner")
            password = gr.Textbox(label=t("en", "login.password"),
                                  type="password")
            remember_cb = gr.Checkbox(label=t("en", "login.remember_me"),
                                      value=False)
            login_btn = gr.Button(t("en", "login.button"), variant="primary")
            login_msg = gr.Markdown("")

        # -- main view --------------------------------------------------
        with gr.Column(visible=False) as main_view:
            welcome = gr.Markdown("")
            with gr.Row():
                refresh_btn = gr.Button(t("ar", "login.refresh"))
                logout_btn = gr.Button(t("ar", "login.logout"))
                lang_radio = gr.Radio(
                    choices=[("العربية", "ar"), ("English", "en")],
                    value="en", label=t("ar", "login.language"))
            status_box = gr.Markdown("")
            cp_heading = gr.Markdown(dir_wrap("ar", t("ar", "login.change_password_heading")))
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
            # Markdown components whose toggle "value" is rendered
            # text: the toggle wraps those in dir_wrap (A-87).
            markdown_value_comps: set = set()

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
            markdown_value_comps.update([title_md, hint_md, cp_heading])
            for b in built:
                for comp, key in b.get("labels", []):
                    _upd(comp)["label"] = key
                for comp, key in b.get("texts", []):
                    _upd(comp)["value"] = key
                    markdown_value_comps.add(comp)
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
            # A-91: handlers to run right after a successful login /
            # session restore (chained with .then() below), so lists
            # like the halls table are already populated when the user
            # opens the site. (fn, outputs) pairs, mirroring "renders".
            on_login: list = []
            for b in built:
                on_login.extend(b.get("on_login", []))
            # A-95: refresh every registered dropdown/checkbox choice
            # on login too. The registry is assembled here, after all
            # builders ran, so it covers every tab; the builders only
            # contribute "choice_entries" (component, choices_fn)
            # pairs and "choice_mutation_events" (click events whose
            # handlers can change an option list).
            choice_registry = ChoiceRegistry()
            for b in built:
                for comp, fn in b.get("choice_entries", []):
                    choice_registry.register(comp, fn)
            refresh_choices_fn, refresh_choices_outputs = (
                make_refresh_choices_handler(db, auth, choice_registry))
            on_login.append((refresh_choices_fn, refresh_choices_outputs))
            toggle_components = list(updates)

        def _status_text(lang: str = "ar") -> str:
            tables = db.table_names()
            n_users = db.query_one("SELECT COUNT(*) AS n FROM employees")["n"]
            n_roles = db.query_one("SELECT COUNT(*) AS n FROM roles")["n"]
            version = db.current_version()
            return dir_wrap(lang, (
                f"**{t(lang, 'app.status_db')}:** `{settings.db_path}`  \n"
                f"**{t(lang, 'app.status_schema')}:** {version}  \n"
                f"**{t(lang, 'app.status_tables')}:** {len(tables)}  \n"
                f"**{t(lang, 'app.status_employees')}:** {n_users}  \n"
                f"**{t(lang, 'app.status_roles')}:** {n_roles}  \n"
                f"**{t(lang, 'app.status_venue')}:** {settings.venue_name} "
                f"({settings.currency}, {settings.timezone})"
            ))

        def _welcome_md(lang: str, employee) -> str:
            perms = sorted(get_employee_permissions(db, employee.id))
            return dir_wrap(lang, (
                f"## {t(lang, 'login.welcome')}, {employee.display_name}\n"
                f"**{t(lang, 'login.username')}:** {employee.username}  \n"
                f"**{t(lang, 'login.roles')}:** {', '.join(employee.roles) or '—'}  \n"
                f"**{t(lang, 'login.permissions')} ({len(perms)}):** {', '.join(perms)}"
            ))

        def _enter_main(employee, token, lang, remember_token):
            """Outputs shared by login and remember-me restore."""
            return (
                token,
                {"username": employee.username, "roles": employee.roles},
                gr.update(visible=False), gr.update(visible=True),
                _welcome_md(lang, employee), _status_text(lang), "", lang,
                gr.update(value=lang), remember_token, token,
                dir_signal_html(lang),
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

        def do_restore(stored_session, stored_token):
            """Page load (FR-011 + A-89): first try the persisted
            session token — a still-valid session signs the employee
            straight back in, so a full page reload (e.g. the theme
            switch in the footer Settings gear) does not log them
            out. If the session is gone/expired, fall back to the
            Remember Me token (validate, rotate, open a fresh
            session). If neither works, clear both stored values and
            show the login view."""
            if stored_session:
                employee = auth.validate_session(stored_session)
                if employee is not None:
                    lang = get_language(db, employee)
                    gr.Info(t(lang, "login.session_restored"))
                    # The Remember Me token was not used: pass it
                    # through untouched (no rotation).
                    return _enter_main(employee, stored_session, lang,
                                       stored_token)
                # A dead session token must not linger in the browser.
                stored_session = None
            if not stored_token:
                # A-93: logged out means the English LTR login screen.
                return (None, None, gr.update(visible=True),
                        gr.update(visible=False), "", "", "", "en",
                        gr.update(value="en"), None, None,
                        dir_signal_html("en"))
            employee, new_token = auth.validate_remember_token(stored_token)
            if employee is None:
                return (None, None, gr.update(visible=True),
                        gr.update(visible=False), "",
                        dir_wrap("en", t("en", "login.session_expired")), "", "en",
                        gr.update(value="en"), None, None,
                        dir_signal_html("en"))
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
                    value = t(lang, spec["value"])
                    kwargs["value"] = (dir_wrap(lang, value)
                                       if comp in markdown_value_comps
                                       else value)
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
            # Live RTL: flip the page direction with the language.
            outputs.append(gr.update(value=dir_signal_html(lang)))
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
            # A-89: the persisted session token is cleared too, so a
            # later reload cannot sign back in after an explicit logout.
            # A-93: the login chrome is reset to English LTR as well —
            # the language toggle renamed it while the user was logged
            # in, and the logged-out screen must always be English.
            return (
                None, None,
                gr.update(visible=True), gr.update(visible=False),
                "", "", "", None, None, dir_signal_html("en"),
                dir_wrap("en", t("en", "app.title_heading")),
                dir_wrap("en", t("en", "app.login_hint")),
                gr.update(label=t("en", "login.username")),
                gr.update(label=t("en", "login.password")),
                gr.update(label=t("en", "login.remember_me")),
                gr.update(value=t("en", "login.button")),
                gr.update(value="en"),
                "en",
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
        # login_outputs order: session gr.State, current user, views,
        # welcome/status/message, language, Remember Me BrowserState,
        # session BrowserState (A-89), direction signal (last).
        login_outputs = [session_token, current_user, login_view, main_view,
                         welcome, status_box, login_msg, lang_state,
                         lang_radio, remember_state, session_state, dir_signal]
        login_click = login_btn.click(
            do_login, inputs=[username, password, remember_cb],
            outputs=login_outputs,
        )
        password_submit = password.submit(
            do_login, inputs=[username, password, remember_cb],
            outputs=login_outputs,
        )
        load_event = demo.load(
            do_restore, inputs=[session_state, remember_state],
            outputs=login_outputs,
        )
        # A-91: populate lists right after sign-in — login button,
        # password submit, and page-load restore alike. The handlers
        # are session-tolerant (a failed login or an anonymous load
        # leaves the lists untouched), so chaining is safe even when
        # the preceding event raised.
        for on_login_fn, on_login_outputs in on_login:
            login_click.then(on_login_fn, inputs=[session_token],
                             outputs=on_login_outputs)
            password_submit.then(on_login_fn, inputs=[session_token],
                                 outputs=on_login_outputs)
            load_event.then(on_login_fn, inputs=[session_token],
                            outputs=on_login_outputs)
        # A-95: the same choices refresh runs after every mutation
        # that can change an option list (a hall/package/service/
        # event-type/template save or delete).
        for b in built:
            for ev in b.get("choice_mutation_events", []):
                ev.then(refresh_choices_fn, inputs=[session_token],
                        outputs=refresh_choices_outputs)
        logout_btn.click(
            do_logout, inputs=[session_token, remember_state],
            outputs=[session_token, current_user, login_view, main_view,
                     welcome, status_box, login_msg, remember_state,
                     session_state, dir_signal,
                     # A-93: login chrome reset to English on logout.
                     title_md, hint_md, username, password, remember_cb,
                     login_btn, lang_radio, lang_state],
        )
        # Language toggle: outputs are the language state, the welcome
        # and status lines, one merged update per toggle component,
        # and the dashboard re-render outputs.
        lang_outputs: list = [lang_state, welcome, status_box]
        lang_outputs.extend(toggle_components)
        for _, fn_outputs in render_fns:
            lang_outputs.extend(fn_outputs)
        lang_outputs.append(dir_signal)
        lang_radio.change(do_language, inputs=[lang_radio, session_token],
                          outputs=lang_outputs)
        refresh_btn.click(do_refresh, inputs=[session_token], outputs=[status_box])
        change_pw_btn.click(
            do_change_password, inputs=[session_token, old_pw, new_pw],
            outputs=[old_pw, new_pw],
        )

    return demo
