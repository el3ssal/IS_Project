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
    def test_rtl_css_is_only_the_two_direction_rules(self):
        from wedding_hall_system.ui.rtl import RTL_CSS
        # Exactly two rules: rtl for Arabic, ltr for English — and
        # nothing else. In particular no colors, no background, no
        # footer rule (the default theme + footer must survive, A-85).
        self.assertIn('.gradio-container:has(#whms-dir-signal [data-lang="ar"])',
                      RTL_CSS)
        self.assertIn("direction: rtl", RTL_CSS)
        self.assertIn('.gradio-container:has(#whms-dir-signal [data-lang="en"])',
                      RTL_CSS)
        self.assertIn("direction: ltr", RTL_CSS)
        self.assertEqual(RTL_CSS.count("{"), 2)
        self.assertEqual(RTL_CSS.count("}"), 2)
        for banned in ("footer", "radial-gradient", "background",
                       "display", "color", "#0F766E", "#EAB308",
                       "#FAF8F2", "box-shadow"):
            self.assertNotIn(banned, RTL_CSS)

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
