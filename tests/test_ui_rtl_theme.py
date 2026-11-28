"""RTL tests (post-Phase 8 polish, theme reverted).

The app is fully bilingual but the page itself stayed LTR: Gradio has
no ``rtl`` flag, so the whole-page direction must come from the
hidden direction signal (``ui/rtl.py``). The primary mechanism is
pure CSS: ``RTL_CSS`` flips ``.gradio-container`` with ``:has()`` on
the signal's ``data-lang`` — the launch JavaScript (a
``MutationObserver`` through the ``<gradio-app>`` shadow DOM setting
``<html dir>``) is only a secondary belt-and-braces, after it proved
unreliable in the user's real browser even though it was present in
the served ``/config``. There is NO custom theme: the app uses
Gradio's default theme, so the "Built with Gradio" footer and its
light/dark settings gear stay visible (A-85/A-86).
"""
from __future__ import annotations

from tests.conftest import Phase1TestCase


def _find_fn(demo, api_name: str):
    for fn in demo.fns.values():
        if fn.get_config().get("api_name") == api_name:
            return fn.fn
    return None


class RtlCssTestCase(Phase1TestCase):
    def test_rtl_css_has_direction_rules_and_label_rules(self):
        from wedding_hall_system.ui.rtl import RTL_CSS
        # Two direction rules (rtl for Arabic, ltr for English) plus
        # the A-88 form-label/input rules — and nothing else. In
        # particular no theme, no background, no footer rule (the
        # default theme + footer must survive, A-85).
        self.assertIn('.gradio-container:has(#whms-dir-signal [data-lang="ar"])',
                      RTL_CSS)
        self.assertIn("direction: rtl", RTL_CSS)
        self.assertIn('.gradio-container:has(#whms-dir-signal [data-lang="en"])',
                      RTL_CSS)
        self.assertIn("direction: ltr", RTL_CSS)
        for banned in ("footer", "radial-gradient", "#0F766E", "#EAB308",
                       "#FAF8F2", "box-shadow", "display: none"):
            self.assertNotIn(banned, RTL_CSS)

    def test_label_rules_flip_block_labels_right_in_arabic(self):
        # A-88: Gradio's floating labels are pinned to the LEFT by the
        # frontend CSS (label.float { left: ... }) and the <label>
        # itself carries dir="ltr" from the component's rtl prop
        # (default False), so the container flip leaves every form
        # label left-aligned in Arabic. Verified against the installed
        # Gradio 6.32 frontend: the shared label renders as
        # <label data-testid="block-label"> (floating when .float).
        from wedding_hall_system.ui.rtl import RTL_CSS
        ar_scope = '.gradio-container:has(#whms-dir-signal [data-lang="ar"])'
        self.assertIn(f'{ar_scope} label[data-testid="block-label"]',
                      RTL_CSS)
        self.assertIn('label[data-testid="block-label"].float', RTL_CSS)
        self.assertIn('right: var(--block-label-margin)', RTL_CSS)
        self.assertIn('left: auto', RTL_CSS)
        self.assertIn('label[data-testid="block-label"]:not(.float)', RTL_CSS)
        self.assertIn('text-align: right', RTL_CSS)
        # Text-like inputs are right-aligned too; number inputs are
        # deliberately NOT in the rule, so digits/spinners stay LTR.
        self.assertIn('input[type="text"]', RTL_CSS)
        self.assertIn('input[type="password"]', RTL_CSS)
        self.assertIn('textarea', RTL_CSS)
        self.assertNotIn('input[type="number"]', RTL_CSS)

    def test_js_flips_direction_through_shadow_dom(self):
        from wedding_hall_system.ui.rtl import RTL_JS
        # Secondary mechanism only (A-86): Gradio renders inside
        # <gradio-app>'s shadow DOM, so plain getElementById cannot
        # see the signal component.
        self.assertIn("gradio-app", RTL_JS)
        self.assertIn("shadowRoot", RTL_JS)
        self.assertIn("whms-dir-signal", RTL_JS)
        self.assertIn("MutationObserver", RTL_JS)
        self.assertIn('setAttribute("dir"', RTL_JS)
        self.assertIn('"rtl"', RTL_JS)
        self.assertIn('"ltr"', RTL_JS)

    def test_dir_signal_html(self):
        from wedding_hall_system.ui.rtl import dir_signal_html
        self.assertIn('data-lang="ar"', dir_signal_html("ar"))
        self.assertIn('data-dir="rtl"', dir_signal_html("ar"))
        self.assertIn('data-lang="en"', dir_signal_html("en"))
        self.assertIn('data-dir="ltr"', dir_signal_html("en"))

    def test_launch_kwargs_cover_css_and_js_only(self):
        from wedding_hall_system.ui.rtl import RTL_CSS, RTL_JS, launch_kwargs
        kwargs = launch_kwargs()
        # No "theme" key: default Gradio theme, footer + light/dark
        # settings gear intact (A-85).
        self.assertEqual(set(kwargs), {"css", "js"})
        self.assertNotIn("theme", kwargs)
        self.assertEqual(kwargs["css"], RTL_CSS)
        self.assertEqual(kwargs["js"], RTL_JS)


class RtlToggleTestCase(Phase1TestCase):
    def test_dir_signal_component_is_present_with_english_default(self):
        # A-93: the login screen is English-only and LTR, so the
        # initial direction signal is "en" even though the venue
        # default language is Arabic.
        from gradio.components import HTML

        from wedding_hall_system.ui.app import build_app
        demo = build_app(self.settings, self.db)
        signals = [b for b in demo.blocks.values()
                   if isinstance(b, HTML) and b.elem_id == "whms-dir-signal"]
        self.assertEqual(len(signals), 1)
        self.assertIn('data-lang="en"', signals[0].value)
        self.assertIn('data-dir="ltr"', signals[0].value)

    def test_language_toggle_flips_dir_signal_both_ways(self):
        from wedding_hall_system.ui.app import build_app
        demo = build_app(self.settings, self.db)
        _, token = self.auth.login("owner", "Owner123!")
        lang_fn = _find_fn(demo, "do_language")
        self.assertIsNotNone(lang_fn)

        def signal_value(outputs):
            hits = [o.get("value") for o in outputs
                    if isinstance(o, dict) and "value" in o
                    and isinstance(o["value"], str)
                    and "data-lang=" in o["value"]]
            return hits

        en_hits = signal_value(lang_fn("en", token))
        self.assertEqual(len(en_hits), 1)
        self.assertIn('data-lang="en"', en_hits[0])
        self.assertIn('data-dir="ltr"', en_hits[0])
        ar_hits = signal_value(lang_fn("ar", token))
        self.assertEqual(len(ar_hits), 1)
        self.assertIn('data-lang="ar"', ar_hits[0])
        self.assertIn('data-dir="rtl"', ar_hits[0])

    def test_login_and_logout_carry_the_dir_signal(self):
        from wedding_hall_system.ui.app import build_app
        demo = build_app(self.settings, self.db)
        login_fn = _find_fn(demo, "do_login")
        self.assertIsNotNone(login_fn)
        outputs = login_fn("owner", "Owner123!", False)
        # Last login output is the direction signal — the owner's
        # preference is Arabic, so the main view is RTL after login.
        self.assertIn('data-lang="ar"', outputs[-1])
        logout_fn = _find_fn(demo, "do_logout")
        self.assertIsNotNone(logout_fn)
        _, token = self.auth.login("owner", "Owner123!")
        logged_out = logout_fn(token, None)
        # A-93: logout resets the screen to the English LTR login;
        # the signal is outputs[9], followed by the login-chrome
        # reset (title, hint, labels, button, language, lang_state).
        self.assertEqual(len(logged_out), 18)
        self.assertIn('data-lang="en"', logged_out[9])
        self.assertIn('data-dir="ltr"', logged_out[9])
        self.assertEqual(logged_out[-1], "en")


class DirWrapRegressionTestCase(Phase1TestCase):
    """A-87: every dynamic Markdown value must carry its own dir=.

    Container ``direction`` alone does not flip Markdown text blocks
    in the user's real browser, so each rendered value is wrapped
    with ``dir_wrap`` (``<div dir="rtl">`` for Arabic, ``<div
    dir="ltr">`` for English). These tests pin the wrapped outputs
    for the welcome/status lines, the headings, and every table list.
    """

    def _demo_and_token(self):
        from wedding_hall_system.ui.app import build_app
        demo = build_app(self.settings, self.db)
        _, token = self.auth.login("owner", "Owner123!")
        return demo, token

    def _fn(self, demo, api_name):
        fn = _find_fn(demo, api_name)
        self.assertIsNotNone(fn, f"missing handler {api_name}")
        return fn

    def test_language_toggle_wraps_welcome_and_status(self):
        demo, token = self._demo_and_token()
        lang_fn = self._fn(demo, "do_language")
        # do_language outputs: [lang, welcome, status, ...]
        ar = lang_fn("ar", token)
        self.assertIn('dir="rtl"', ar[1])
        self.assertIn('dir="rtl"', ar[2])
        en = lang_fn("en", token)
        self.assertIn('dir="ltr"', en[1])
        self.assertIn('dir="ltr"', en[2])

    def _toggle_value(self, outputs, needle):
        hits = [o["value"] for o in outputs
                if isinstance(o, dict) and isinstance(o.get("value"), str)
                and needle in o["value"]]
        self.assertEqual(len(hits), 1,
                         f"expected one toggle update containing {needle!r}")
        return hits[0]

    def test_language_toggle_wraps_headings(self):
        from wedding_hall_system.ui.i18n import t
        demo, token = self._demo_and_token()
        lang_fn = self._fn(demo, "do_language")
        ar = lang_fn("ar", token)
        cp = self._toggle_value(ar, t("ar", "login.change_password_heading"))
        self.assertIn('dir="rtl"', cp)
        halls = self._toggle_value(ar, t("ar", "catalog.halls_heading"))
        self.assertIn('dir="rtl"', halls)
        en = lang_fn("en", token)
        cp_en = self._toggle_value(
            en, t("en", "login.change_password_heading"))
        self.assertIn('dir="ltr"', cp_en)
        halls_en = self._toggle_value(en, t("en", "catalog.halls_heading"))
        self.assertIn('dir="ltr"', halls_en)

    def test_table_lists_wrapped_both_languages(self):
        demo, token = self._demo_and_token()
        lang_fn = self._fn(demo, "do_language")
        lang_fn("ar", token)
        single = [
            ("do_refresh_halls", (token,)),
            ("do_refresh_hours", (token,)),
            ("do_refresh_maint", (token,)),
            ("do_refresh_et", (token,)),
            ("do_refresh_pkg", (token,)),
            ("do_refresh_svc", (token,)),
            ("do_refresh_rule", (token,)),
            ("do_refresh_quot", (token,)),
            ("do_refresh_books", (token,)),
            ("do_list_exp", (token,)),
            ("do_refresh_contracts", (token,)),
            ("do_refresh_templates", (token,)),
            ("do_refresh_task_templates", (token,)),
            ("do_refresh_staff", (token,)),
        ]
        for name, args in single:
            out = self._fn(demo, name)(*args)
            text = out[0] if isinstance(out, (list, tuple)) else out
            self.assertIsInstance(text, str, name)
            self.assertIn('dir="rtl"', text, name)
        cust = self._fn(demo, "do_refresh_cust")(token, "")
        self.assertIn('dir="rtl"', cust)
        notif = self._fn(demo, "do_refresh_notif")(token, False)
        text = notif[0] if isinstance(notif, (list, tuple)) else notif
        self.assertIn('dir="rtl"', text)
        # English: the same handlers must render ltr.
        lang_fn("en", token)
        for name, args in single:
            out = self._fn(demo, name)(*args)
            text = out[0] if isinstance(out, (list, tuple)) else out
            self.assertIn('dir="ltr"', text, name)
        self.assertIn('dir="ltr"',
                      self._fn(demo, "do_refresh_cust")(token, ""))

    def test_duplicate_customer_message_wrapped(self):
        # The duplicate-customer message is a plain translated string
        # rendered into a Markdown output, so it needs dir_wrap too.
        demo, token = self._demo_and_token()
        create = self._fn(demo, "do_create_cust")
        args = (token, "Test RTL", "01000000001", "", "", "", "", False)
        first = create(*args)
        self.assertEqual(first[1], "")
        dup = create(*args)
        self.assertIn('dir="rtl"', dup[0])
        self.assertIn('dir="rtl"', dup[1])


if __name__ == "__main__":
    import unittest
    unittest.main()
