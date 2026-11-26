"""RTL + theme tests (post-Phase 8 polish).

The app is fully bilingual but the page itself stayed LTR: Gradio has
no ``rtl`` flag, so the whole-page direction must come from the
``<html dir>`` attribute, flipped live by ``ui/theme.py`` (a hidden
``gr.HTML`` direction signal + a ``MutationObserver`` in the launch
JavaScript that reads it through the ``<gradio-app>`` shadow DOM).
"""
from __future__ import annotations

import gradio as gr
from gradio.themes.base import Base as GradioThemeBase

from tests.conftest import Phase1TestCase


def _find_fn(demo, api_name: str):
    for fn in demo.fns.values():
        if fn.get_config().get("api_name") == api_name:
            return fn.fn
    return None


class ThemeTestCase(Phase1TestCase):
    def test_theme_is_soft_based_with_warm_white_and_petroleum(self):
        from wedding_hall_system.ui.theme import BODY_BG, build_theme
        theme = build_theme()
        self.assertIsInstance(theme, GradioThemeBase)
        css = theme._get_theme_css()
        # Warm eye-comfortable white, never pure white, as the page
        # background; petroleum as the primary-button fill.
        self.assertEqual(BODY_BG, "#FAF8F2")
        self.assertIn("--body-background-fill: #FAF8F2", css)
        self.assertIn("#0F766E", css)
        self.assertNotIn("--body-background-fill: #FFFFFF", css)
        self.assertNotIn("--body-background-fill: #ffffff", css)

    def test_css_has_subtle_dotted_background_and_hides_signal(self):
        from wedding_hall_system.ui.theme import APP_CSS
        self.assertIn("radial-gradient", APP_CSS)
        self.assertIn("26px", APP_CSS)  # dot grid spacing
        self.assertIn("#whms-dir-signal", APP_CSS)
        self.assertIn("display: none", APP_CSS)
        self.assertIn("#EAB308", APP_CSS)  # yellow accent rule

    def test_js_flips_direction_through_shadow_dom(self):
        from wedding_hall_system.ui.theme import RTL_JS
        # Gradio renders inside <gradio-app>'s shadow DOM, so plain
        # getElementById cannot see the signal component.
        self.assertIn("gradio-app", RTL_JS)
        self.assertIn("shadowRoot", RTL_JS)
        self.assertIn("whms-dir-signal", RTL_JS)
        self.assertIn("MutationObserver", RTL_JS)
        self.assertIn('setAttribute("dir"', RTL_JS)
        self.assertIn('"rtl"', RTL_JS)
        self.assertIn('"ltr"', RTL_JS)

    def test_dir_signal_html(self):
        from wedding_hall_system.ui.theme import dir_signal_html
        self.assertIn('data-lang="ar"', dir_signal_html("ar"))
        self.assertIn('data-dir="rtl"', dir_signal_html("ar"))
        self.assertIn('data-lang="en"', dir_signal_html("en"))
        self.assertIn('data-dir="ltr"', dir_signal_html("en"))

    def test_launch_kwargs_cover_theme_css_js(self):
        from wedding_hall_system.ui.theme import APP_CSS, RTL_JS, launch_kwargs
        kwargs = launch_kwargs()
        self.assertEqual(set(kwargs), {"theme", "css", "js"})
        self.assertIsInstance(kwargs["theme"], GradioThemeBase)
        self.assertEqual(kwargs["css"], APP_CSS)
        self.assertEqual(kwargs["js"], RTL_JS)


class RtlToggleTestCase(Phase1TestCase):
    def test_dir_signal_component_is_present_with_arabic_default(self):
        from gradio.components import HTML

        from wedding_hall_system.ui.app import build_app
        demo = build_app(self.settings, self.db)
        signals = [b for b in demo.blocks.values()
                   if isinstance(b, HTML) and b.elem_id == "whms-dir-signal"]
        self.assertEqual(len(signals), 1)
        self.assertIn('data-lang="ar"', signals[0].value)

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
        # Last login output is the direction signal (Arabic default).
        self.assertIn('data-lang="ar"', outputs[-1])
        logout_fn = _find_fn(demo, "do_logout")
        self.assertIsNotNone(logout_fn)
        _, token = self.auth.login("owner", "Owner123!")
        logged_out = logout_fn(token, None)
        self.assertIn('data-lang="ar"', logged_out[-1])


if __name__ == "__main__":
    import unittest
    unittest.main()
