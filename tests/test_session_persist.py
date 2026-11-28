"""Session persistence across page reloads (A-89).

The footer Settings gear changes the theme by reloading the whole
page, and a reload wipes ``gr.State`` — so before A-89 every
light/dark switch logged the user out. The session token is now
persisted in the browser's localStorage through ``gr.BrowserState``
(storage key ``whms-session``), validated on every load, and cleared
on logout. Remember Me (30-day token) is unchanged and stays the
fallback when the session has expired.
"""
from __future__ import annotations

import hashlib

from tests.conftest import Phase1TestCase
from tests.test_ui_rtl_theme import _find_fn


def _visible(output) -> bool | None:
    return output.get("visible") if isinstance(output, dict) else None


def _find_app_restore(demo):
    """The app-level restore handler (page load). It shares its
    ``__name__`` with the backup-restore handler, so Gradio
    deduplicates the api_name (``do_restore`` vs ``do_restore_1``)
    — match by signature instead: 2 inputs (session + remember
    BrowserStates) and 12 outputs."""
    matches = []
    for fn in demo.fns.values():
        cfg = fn.get_config()
        if (cfg.get("api_name", "").startswith("do_restore")
                and len(fn.inputs) == 2 and len(fn.outputs) == 12):
            matches.append(fn.fn)
    assert len(matches) == 1, f"expected one app restore fn, got {len(matches)}"
    return matches[0]


class SessionPersistTestCase(Phase1TestCase):
    def _demo(self):
        from wedding_hall_system.ui.app import build_app
        return build_app(self.settings, self.db)

    def test_login_writes_session_token_to_browser_state(self):
        demo = self._demo()
        login_fn = _find_fn(demo, "do_login")
        self.assertIsNotNone(login_fn)
        outputs = login_fn("owner", "Owner123!", False)
        # login_outputs: [session gr.State, user, login view, main
        # view, welcome, status, msg, lang, lang radio, remember,
        # session BrowserState, dir signal (last)].
        self.assertEqual(len(outputs), 12)
        token = outputs[0]
        self.assertTrue(token)
        self.assertEqual(outputs[10], token)
        self.assertIn('data-lang="ar"', outputs[11])
        self.assertTrue(_visible(outputs[3]))

    def test_restore_with_valid_session_token_signs_back_in(self):
        demo = self._demo()
        _, token = self.auth.login("owner", "Owner123!")
        restore_fn = _find_app_restore(demo)
        self.assertIsNotNone(restore_fn)
        outputs = restore_fn(token, None)
        self.assertTrue(_visible(outputs[3]))
        self.assertFalse(_visible(outputs[2]))
        # The same session keeps working; nothing is rotated.
        self.assertEqual(outputs[0], token)
        self.assertEqual(outputs[10], token)
        self.assertIsNone(outputs[9])
        self.assertIn('data-lang="ar"', outputs[11])

    def test_restore_with_expired_session_falls_back_to_remember_me(self):
        demo = self._demo()
        employee, token = self.auth.login("owner", "Owner123!")
        remember = self.auth.issue_remember_token(employee)
        # Expire the session row directly.
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        self.db.execute(
            "UPDATE sessions SET expires_at = '2000-01-01T00:00:00' "
            "WHERE token_hash = ?", (token_hash,))
        restore_fn = _find_app_restore(demo)
        outputs = restore_fn(token, remember)
        self.assertTrue(_visible(outputs[3]))
        # A fresh session was created and the remember token rotated.
        self.assertNotEqual(outputs[0], token)
        self.assertTrue(outputs[0])
        self.assertEqual(outputs[10], outputs[0])
        self.assertNotEqual(outputs[9], remember)
        self.assertTrue(outputs[9])

    def test_restore_with_invalid_tokens_shows_login_and_clears_both(self):
        demo = self._demo()
        restore_fn = _find_app_restore(demo)
        outputs = restore_fn("bogus-session", "bogus-remember")
        self.assertTrue(_visible(outputs[2]))
        self.assertFalse(_visible(outputs[3]))
        self.assertIsNone(outputs[0])
        self.assertIsNone(outputs[9])
        self.assertIsNone(outputs[10])

    def test_restore_with_no_tokens_shows_login(self):
        demo = self._demo()
        restore_fn = _find_app_restore(demo)
        outputs = restore_fn(None, None)
        self.assertTrue(_visible(outputs[2]))
        self.assertFalse(_visible(outputs[3]))

    def test_logout_clears_persisted_session_token(self):
        demo = self._demo()
        employee, token = self.auth.login("owner", "Owner123!")
        remember = self.auth.issue_remember_token(employee)
        logout_fn = _find_fn(demo, "do_logout")
        self.assertIsNotNone(logout_fn)
        outputs = logout_fn(token, remember)
        # logout outputs: [session, user, login view, main view,
        # welcome, status, msg, remember, session BrowserState,
        # dir signal (index 9), then the A-93 login-chrome reset:
        # title, hint, username, password, remember-me, login
        # button, language radio, lang_state (index 17)].
        self.assertEqual(len(outputs), 18)
        self.assertTrue(_visible(outputs[2]))
        self.assertFalse(_visible(outputs[3]))
        self.assertIsNone(outputs[8])
        self.assertIsNone(outputs[7])
        self.assertIn('data-lang="en"', outputs[9])
        # The server-side session row is gone too.
        self.assertIsNone(self.auth.validate_session(token))
