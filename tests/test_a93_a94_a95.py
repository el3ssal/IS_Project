"""Tests for A-93 (English-only login), A-94 (auto-load all lists on
login) and A-95 (dropdown choices stay fresh).

A-93: while logged out the whole login screen — labels, inputs,
button, remember-me checkbox, error messages — is English and LTR,
whatever the user's saved language is. After login (or a session /
remember-me restore) the main view uses the user's own language;
logout resets the login screen back to English.

A-94: every list that used to start empty behind a grey refresh
button is populated by the login chain (builders return ``"on_login"``
``(fn, outputs)`` pairs; ``app.py`` chains them after login click,
password submit and page-load restore). The wrappers are
session/permission-tolerant (``ui.live_refresh.login_follow_up``):
no valid session, or a role that may not see a list, leaves the
outputs untouched instead of popping an error at sign-in.

A-95: hall/package/event-type/service/template dropdowns and
checkbox groups were built once from choices computed at app build,
so a hall created, deleted or deactivated on one tab never showed
up (or never disappeared) on the others. One ``ChoiceRegistry``
(``ui.live_refresh``) now collects every such component with the
helper that rebuilds its choices, and a single refresh handler runs
on login and after every catalog/operations mutation. Bookable
dropdowns exclude deactivated halls; configuration dropdowns still
list them (so they can be reactivated).
"""
from __future__ import annotations

# conftest first: it repairs the shell's broken NO_PROXY before any
# Gradio import (see tests/conftest.py).
from tests.conftest import Phase1TestCase

import gradio as gr


def _find_fn(demo, api_name: str):
    for fn in demo.fns.values():
        if fn.get_config().get("api_name") == api_name:
            return fn.fn
    return None


def _find_entry(demo, api_name: str):
    for fn in demo.fns.values():
        if fn.get_config().get("api_name") == api_name:
            return fn
    return None


def _find_app_restore(demo):
    """Same signature match as test_session_persist: the app-level
    restore shares its ``__name__`` with the backup-restore handler,
    so Gradio deduplicates the api_name — match by 2 inputs and
    12 outputs."""
    matches = []
    for fn in demo.fns.values():
        cfg = fn.get_config()
        if (cfg.get("api_name", "").startswith("do_restore")
                and len(fn.inputs) == 2 and len(fn.outputs) == 12):
            matches.append(fn.fn)
    assert len(matches) == 1, f"expected one app restore fn, got {len(matches)}"
    return matches[0]


class EnglishLoginTestCase(Phase1TestCase):
    """A-93: the logged-out screen is English-only and LTR."""

    def _demo(self):
        from wedding_hall_system.ui.app import build_app
        return build_app(self.settings, self.db)

    def test_login_view_is_english_at_build(self):
        from gradio.components import Button, Checkbox, HTML, Markdown, Textbox

        demo = self._demo()
        labels = [b.label for b in demo.blocks.values()
                  if isinstance(b, Textbox)]
        self.assertIn("Username", labels)
        self.assertIn("Password", labels)
        self.assertNotIn("اسم المستخدم", labels)
        self.assertNotIn("كلمة المرور", labels)
        check_labels = [b.label for b in demo.blocks.values()
                        if isinstance(b, Checkbox)]
        self.assertIn("Remember me on this device (30 days)", check_labels)
        button_values = [b.value for b in demo.blocks.values()
                         if isinstance(b, Button)]
        self.assertIn("Log in", button_values)
        self.assertNotIn("تسجيل الدخول", button_values)
        titles = [b.value for b in demo.blocks.values()
                  if isinstance(b, Markdown) and isinstance(b.value, str)
                  and "# Wedding Hall Management System" in b.value]
        self.assertEqual(len(titles), 1)
        self.assertIn('dir="ltr"', titles[0])
        signals = [b for b in demo.blocks.values()
                   if isinstance(b, HTML) and b.elem_id == "whms-dir-signal"]
        self.assertEqual(len(signals), 1)
        self.assertIn('data-lang="en"', signals[0].value)
        self.assertIn('data-dir="ltr"', signals[0].value)

    def test_login_with_wrong_password_raises_english_error(self):
        demo = self._demo()
        login_fn = _find_fn(demo, "do_login")
        self.assertIsNotNone(login_fn)
        with self.assertRaises(gr.Error) as ctx:
            login_fn("owner", "wrong-password", False)
        # str(gr.Error) wraps the message in quotes; substring match.
        self.assertIn("Invalid username or password.", str(ctx.exception))

    def test_restore_with_no_tokens_returns_english_login(self):
        demo = self._demo()
        restore_fn = _find_app_restore(demo)
        outputs = restore_fn(None, None)
        self.assertEqual(len(outputs), 12)
        self.assertEqual(outputs[7], "en")  # lang_state
        self.assertEqual(outputs[8].get("value"), "en")  # lang radio
        self.assertIn('data-lang="en"', outputs[11])  # dir signal
        self.assertIn('data-dir="ltr"', outputs[11])

    def test_restore_with_expired_remember_token_is_english(self):
        demo = self._demo()
        restore_fn = _find_app_restore(demo)
        outputs = restore_fn(None, "bogus-token")
        self.assertEqual(outputs[7], "en")
        self.assertIn("Your session has expired. Please log in again.",
                      outputs[5])
        self.assertIn('data-lang="en"', outputs[11])

    def test_logout_after_arabic_toggle_resets_login_chrome_to_english(self):
        demo = self._demo()
        _, token = self.auth.login("owner", "Owner123!")
        lang_fn = _find_fn(demo, "do_language")
        lang_fn("ar", token)  # renames the login chrome to Arabic too
        logout_fn = _find_fn(demo, "do_logout")
        self.assertIsNotNone(logout_fn)
        outputs = logout_fn(token, None)
        # [session, user, login view, main view, welcome, status, msg,
        # remember, session BrowserState, dir signal (9), title (10),
        # hint (11), username (12), password (13), remember-me (14),
        # login button (15), language radio (16), lang_state (17)].
        self.assertEqual(len(outputs), 18)
        self.assertIn('data-lang="en"', outputs[9])
        self.assertIn('data-dir="ltr"', outputs[9])
        self.assertIn("# Wedding Hall Management System", outputs[10])
        self.assertIn("Please log in.", outputs[11])
        self.assertEqual(outputs[12].get("label"), "Username")
        self.assertEqual(outputs[13].get("label"), "Password")
        self.assertEqual(outputs[14].get("label"),
                         "Remember me on this device (30 days)")
        self.assertEqual(outputs[15].get("value"), "Log in")
        self.assertEqual(outputs[16].get("value"), "en")
        self.assertEqual(outputs[17], "en")

    def test_login_as_owner_still_switches_main_view_to_arabic(self):
        demo = self._demo()
        login_fn = _find_fn(demo, "do_login")
        outputs = login_fn("owner", "Owner123!", False)
        # [session, user, login view, main view, welcome (4), status,
        # msg, lang (7), lang radio, remember, session, dir signal (11)]
        self.assertEqual(len(outputs), 12)
        self.assertEqual(outputs[7], "ar")  # owner's preference
        self.assertIn('data-lang="ar"', outputs[11])
        self.assertIn('dir="rtl"', outputs[4])  # welcome line


class AutoLoadOnLoginTestCase(Phase1TestCase):
    """A-94: every list is populated by the login chain."""

    # (api_name, output count, needles that must appear in the
    # string outputs). Empty needle lists still assert the outputs
    # are non-empty dir-wrapped strings.
    CASES = [
        ("do_refresh_halls_on_login", 1, ["Main Hall (example)"]),
        ("do_refresh_hours_on_login", 1, ["Garden Hall (example)"]),
        ("do_refresh_maint_on_login", 1, []),
        ("do_refresh_et_on_login", 1, ["Wedding"]),
        ("do_refresh_pkg_on_login", 1, ["Classic Wedding (example)"]),
        ("do_refresh_svc_on_login", 1, ["Photographer"]),
        ("do_refresh_rule_on_login", 1, []),
        ("do_refresh_cust_on_login", 1, ["Ahmed Hassan (example)"]),
        ("do_refresh_quot_on_login", 1, ["Q-"]),
        ("do_refresh_books_on_login", 1, []),
        ("do_refresh_contracts_on_login", 1, []),
        ("do_refresh_templates_on_login", 1, ["Standard"]),
        ("do_refresh_task_templates_on_login", 1, []),
        ("do_refresh_staff_on_login", 1, ["owner"]),
        ("do_refresh_notif_on_login", 2, []),
        ("do_list_exp_on_login", 1, []),
        ("do_refresh_dashboard_on_login", 2, []),
        ("do_show_calendar_on_login", 2, []),
        ("do_list_backups_on_login", 1, []),
        ("do_list_settings_on_login", 1, ["الإعداد"]),
        ("do_list_files_on_login", 1, []),
    ]

    def _demo_and_token(self):
        from wedding_hall_system.ui.app import build_app
        demo = build_app(self.settings, self.db)
        _, token = self.auth.login("owner", "Owner123!")
        return demo, token

    @staticmethod
    def _as_list(result):
        if isinstance(result, (tuple, list)):
            return list(result)
        return [result]

    def test_on_login_handlers_return_populated_content(self):
        demo, token = self._demo_and_token()
        for api_name, n_outputs, needles in self.CASES:
            with self.subTest(api_name=api_name):
                fn = _find_fn(demo, api_name)
                self.assertIsNotNone(fn, f"missing handler {api_name}")
                values = self._as_list(fn(token))
                self.assertEqual(len(values), n_outputs, api_name)
                strings = [v for v in values if isinstance(v, str)]
                self.assertGreater(len(strings), 0, api_name)
                for value in strings:
                    self.assertIn('dir="rtl"', value, api_name)
                    self.assertGreater(len(value), 20, api_name)
                joined = "\n".join(strings)
                for needle in needles:
                    self.assertIn(needle, joined, api_name)
                # Multi-output handlers that also refresh a dropdown
                # (calendar) return its fresh choices as an update.
                for value in values:
                    if isinstance(value, dict):
                        self.assertIn("choices", value, api_name)

    def test_on_login_handlers_tolerate_no_session(self):
        demo, _ = self._demo_and_token()
        for api_name, n_outputs, _needles in self.CASES:
            with self.subTest(api_name=api_name):
                fn = _find_fn(demo, api_name)
                values = self._as_list(fn(None))
                self.assertEqual(len(values), n_outputs, api_name)
                for value in values:
                    self.assertEqual(value, {"__type__": "update"},
                                     api_name)

    def test_on_login_handlers_tolerate_insufficient_role(self):
        # Staff holds only task.view_assigned / task.update_own /
        # notification.view (defaults.ROLE_PERMISSIONS): no
        # settings.view and no backup.manage. Those two lists must
        # simply stay empty at login, not raise.
        demo, _ = self._demo_and_token()
        _, staff_token = self.auth.login("staff", "Staff123!")
        for api_name in ("do_list_settings_on_login",
                         "do_list_backups_on_login"):
            with self.subTest(api_name=api_name):
                fn = _find_fn(demo, api_name)
                self.assertIsNotNone(fn, api_name)
                self.assertEqual(fn(staff_token), {"__type__": "update"})


class FreshChoicesTestCase(Phase1TestCase):
    """A-95: dropdown/checkbox choices follow hall mutations."""

    # Labels of the dropdowns/checkbox groups whose options are
    # halls (other option lists hold package/event-type/service ids,
    # which collide numerically with hall ids and must not be
    # compared against them). Choice values are ints everywhere
    # except the calendar filter, which stores them as strings.
    HALL_LABELS = (
        "القاعة",
        "القاعات المناسبة",
        "متاحة في القاعات (فارغ = كل القاعات)",
        "القاعة الجديدة (فارغة = نفس القاعة)",
    )

    @staticmethod
    def _choice_ids(update) -> list[str]:
        return [str(v) for _label, v in update.get("choices", [])]

    def _demo_token_entry(self):
        from wedding_hall_system.ui.app import build_app
        demo = build_app(self.settings, self.db)
        _, token = self.auth.login("owner", "Owner123!")
        entry = _find_entry(demo, "do_refresh_choices_on_login")
        self.assertIsNotNone(entry)
        return demo, token, entry

    def _create_hall(self, token, name="Test Hall A-95"):
        from wedding_hall_system.logic.hall_service import HallService
        emp = self.auth.validate_session(token)
        return HallService(self.db).create_hall(
            emp, name=name, capacity=60, base_price=500000)

    def test_refresh_choices_handler_registers_every_dropdown(self):
        _demo, _token, entry = self._demo_token_entry()
        # 14 catalog + 12 booking + 1 operations + 1 calendar.
        self.assertEqual(len(entry.outputs), 28)
        labels = [getattr(c, "label", None) for c in entry.outputs]
        self.assertIn("القاعات المناسبة", labels)  # package-page halls
        self.assertIn("القالب", labels)  # contract-generation template
        self.assertIn("القاعة الجديدة (فارغة = نفس القاعة)", labels)

    def test_refresh_choices_is_chained_on_login_and_after_mutations(self):
        # 3 login chains (login click, password submit, page-load
        # restore) + 12 mutation chains (9 catalog: halls refresh,
        # hall save/deactivate/reactivate/delete, package, service,
        # event-type and pricing-rule save; 3 operations: template
        # create/update/delete). Adding a mutation event must update
        # this count.
        from wedding_hall_system.ui.app import build_app
        demo = build_app(self.settings, self.db)
        names = [fn.get_config().get("api_name")
                 for fn in demo.fns.values()]
        chained = [n for n in names
                   if n.startswith("do_refresh_choices_on_login")]
        self.assertEqual(len(chained), 15)
        distinct_on_login = {n for n in names if n.endswith("_on_login")}
        # 21 list handlers + the choices refresh itself.
        self.assertEqual(len(distinct_on_login), 22)

    def test_create_hall_appears_in_package_page_dropdown(self):
        _demo, token, entry = self._demo_token_entry()
        pkg_idx = next(
            i for i, c in enumerate(entry.outputs)
            if getattr(c, "label", None) == "القاعات المناسبة")
        hall = self._create_hall(token)
        try:
            updates = entry.fn(token)
            self.assertEqual(len(updates), 28)
            choices = updates[pkg_idx].get("choices", [])
            self.assertIn(str(hall.id), self._choice_ids(updates[pkg_idx]))
            self.assertTrue(any("Test Hall A-95" in label
                                for label, _v in choices))
            # Every hall dropdown on every tab sees it, not just the
            # package page: config dropdowns, bookable dropdowns,
            # the calendar filter and the service-halls group.
            for i, comp in enumerate(entry.outputs):
                label = getattr(comp, "label", None)
                if label in self.HALL_LABELS:
                    self.assertIn(
                        str(hall.id), self._choice_ids(updates[i]),
                        f"new hall missing from output {i} ({label})")
        finally:
            from wedding_hall_system.logic.hall_service import HallService
            emp = self.auth.validate_session(token)
            HallService(self.db).delete_hall(emp, hall.id)

    def test_delete_hall_disappears_from_every_dropdown(self):
        _demo, token, entry = self._demo_token_entry()
        hall = self._create_hall(token)
        from wedding_hall_system.logic.hall_service import HallService
        emp = self.auth.validate_session(token)
        HallService(self.db).delete_hall(emp, hall.id)
        updates = entry.fn(token)
        self.assertEqual(len(updates), 28)
        for i, comp in enumerate(entry.outputs):
            if getattr(comp, "label", None) in self.HALL_LABELS:
                self.assertNotIn(str(hall.id), self._choice_ids(updates[i]),
                                 f"deleted hall still in output {i}")

    def test_deactivate_hall_excluded_from_bookable_choices(self):
        _demo, token, entry = self._demo_token_entry()
        hall = self._create_hall(token)
        try:
            from wedding_hall_system.logic.hall_service import HallService
            emp = self.auth.validate_session(token)
            HallService(self.db).set_hall_active(emp, hall.id, False)
            updates = entry.fn(token)
            # Index 0 is the first catalog config dropdown
            # (hours_hall): inactive halls stay listed there so they
            # can be configured/reactivated. Index 14 is the first
            # booking dropdown (q_hall): bookable choices only.
            # Index 27 is the calendar hall filter: active only.
            self.assertEqual(getattr(entry.outputs[0], "label", None),
                             "القاعة")
            self.assertEqual(getattr(entry.outputs[14], "label", None),
                             "القاعة")
            self.assertIn(str(hall.id),
                          self._choice_ids(updates[0]))
            self.assertNotIn(str(hall.id),
                             self._choice_ids(updates[14]))
            self.assertNotIn(str(hall.id),
                             self._choice_ids(updates[27]))
        finally:
            from wedding_hall_system.logic.hall_service import HallService
            emp = self.auth.validate_session(token)
            HallService(self.db).delete_hall(emp, hall.id)

    def test_refresh_choices_handler_tolerates_no_session(self):
        _demo, _token, entry = self._demo_token_entry()
        updates = entry.fn(None)
        self.assertEqual(len(updates), 28)
        for upd in updates:
            self.assertEqual(upd, {"__type__": "update"})
