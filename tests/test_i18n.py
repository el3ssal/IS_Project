"""Phase 6 tests: bilingual UI strings + per-user language preference
(SRS §14, §16.3). The i18n module itself has no Gradio dependency, so
these tests do not need the proxy workaround.
"""
from __future__ import annotations

from tests.conftest import Phase1TestCase
from wedding_hall_system.logic import preferences
from wedding_hall_system.ui.i18n import dir_for, dir_wrap, t
from wedding_hall_system.utils.exceptions import ValidationError


class TestI18n(Phase1TestCase):
    def test_arabic_and_english_strings_differ(self):
        self.assertEqual(t("ar", "dashboard.title"), "لوحة المتابعة")
        self.assertEqual(t("en", "dashboard.title"), "Dashboard")
        self.assertEqual(t("ar", "login.button"), "تسجيل الدخول")
        self.assertEqual(t("en", "login.button"), "Log in")
        self.assertEqual(t("ar", "reports.export_csv"), "تصدير CSV")
        self.assertEqual(t("en", "reports.export_csv"), "Export CSV")

    def test_missing_key_falls_back_to_english_then_key(self):
        self.assertEqual(t("ar", "common.refresh"), "تحديث")
        # A key missing everywhere returns the key itself (visible, never silent).
        self.assertEqual(t("ar", "does.not.exist"), "does.not.exist")
        # Unknown language falls back to English.
        self.assertEqual(t("fr", "dashboard.title"), "Dashboard")

    def test_rtl_flag_arabic_only(self):
        self.assertEqual(dir_for("ar"), "rtl")
        self.assertEqual(dir_for("en"), "ltr")
        wrapped = dir_wrap("ar", "## hello")
        self.assertIn('<div dir="rtl">', wrapped)
        self.assertIn('<div dir="ltr">', dir_wrap("en", "## hello"))

    def test_language_preference_defaults_to_arabic(self):
        owner = self.login_as("owner", "Owner123!")[0]
        self.assertEqual(preferences.get_language(self.db, owner), "ar")

    def test_set_language_persists_per_user(self):
        owner = self.login_as("owner", "Owner123!")[0]
        manager = self.login_as("manager", "Manager123!")[0]
        preferences.set_language(self.db, owner, "en")
        self.assertEqual(preferences.get_language(self.db, owner), "en")
        # The manager's preference is untouched.
        self.assertEqual(preferences.get_language(self.db, manager), "ar")
        preferences.set_language(self.db, owner, "ar")
        self.assertEqual(preferences.get_language(self.db, owner), "ar")

    def test_set_language_rejects_unknown(self):
        owner = self.login_as("owner", "Owner123!")[0]
        with self.assertRaises(ValidationError):
            preferences.set_language(self.db, owner, "fr")
