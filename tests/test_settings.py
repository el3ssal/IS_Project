"""Phase 7 tests: Owner settings screen (SRS §14, FR-067).

Every test uses a TEMPORARY database — tests never touch real data.
"""
from __future__ import annotations

from tests.conftest import Phase1TestCase
from wedding_hall_system.data.repositories import system_repository
from wedding_hall_system.logic.settings_service import SettingsService
from wedding_hall_system.utils.exceptions import (
    AuthorizationError,
    NotFoundError,
    ValidationError,
)


class TestSettings(Phase1TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.login_as("owner", "Owner123!")[0]
        self.manager = self.login_as("manager", "Manager123!")[0]
        self.receptionist = self.login_as("receptionist", "Reception123!")[0]
        self.svc = SettingsService(self.db, self.settings)

    def _value(self, key: str) -> str:
        return system_repository.get_setting(self.db, key, "")

    # -- list ----------------------------------------------------------------
    def test_list_settings_returns_seeded_values(self):
        rows = self.svc.list_settings(self.owner)
        by_key = {r["key"]: r["value"] for r in rows}
        self.assertEqual(by_key["venue.name"], "Benha Wedding Hall")
        self.assertEqual(by_key["booking.hold_hours"], "48")
        self.assertEqual(by_key["booking.deposit_percent"], "30")
        self.assertEqual(by_key["finance.installments_count"], "3")
        self.assertTrue(all(r["label"] and r["group"] for r in rows))

    def test_manager_can_view_but_not_edit(self):
        rows = self.svc.list_settings(self.manager)
        self.assertTrue(len(rows) > 10)
        with self.assertRaises(AuthorizationError):
            self.svc.update_setting(self.manager, "venue.name", "X")

    def test_receptionist_cannot_view_settings(self):
        with self.assertRaises(AuthorizationError):
            self.svc.list_settings(self.receptionist)

    # -- update ---------------------------------------------------------------
    def test_update_int_setting_in_range(self):
        updated = self.svc.update_setting(
            self.owner, "booking.hold_hours", "72")
        self.assertEqual(updated["value"], "72")
        self.assertEqual(self._value("booking.hold_hours"), "72")

    def test_update_int_rejects_out_of_range(self):
        for bad in ("0", "721", "-5"):
            with self.assertRaises(ValidationError, msg=bad):
                self.svc.update_setting(
                    self.owner, "booking.hold_hours", bad)
        self.assertEqual(self._value("booking.hold_hours"), "48")

    def test_update_int_rejects_non_number(self):
        with self.assertRaises(ValidationError):
            self.svc.update_setting(
                self.owner, "booking.deposit_percent", "thirty")
        self.assertEqual(self._value("booking.deposit_percent"), "30")

    def test_update_deposit_percent_boundaries(self):
        self.svc.update_setting(self.owner, "booking.deposit_percent", "0")
        self.assertEqual(self._value("booking.deposit_percent"), "0")
        self.svc.update_setting(self.owner, "booking.deposit_percent", "100")
        self.assertEqual(self._value("booking.deposit_percent"), "100")
        with self.assertRaises(ValidationError):
            self.svc.update_setting(
                self.owner, "booking.deposit_percent", "101")

    def test_update_currency_must_be_three_letters(self):
        self.svc.update_setting(self.owner, "venue.currency", "usd")
        self.assertEqual(self._value("venue.currency"), "USD")
        with self.assertRaises(ValidationError):
            self.svc.update_setting(self.owner, "venue.currency", "EG")
        with self.assertRaises(ValidationError):
            self.svc.update_setting(self.owner, "venue.currency", "E£P")

    def test_update_timezone_must_be_iana(self):
        self.svc.update_setting(
            self.owner, "venue.timezone", "Europe/Paris")
        self.assertEqual(self._value("venue.timezone"), "Europe/Paris")
        with self.assertRaises(ValidationError):
            self.svc.update_setting(
                self.owner, "venue.timezone", "Not/AZone")

    def test_update_language_choice(self):
        self.svc.update_setting(
            self.owner, "venue.default_language", "en")
        self.assertEqual(self._value("venue.default_language"), "en")
        with self.assertRaises(ValidationError):
            self.svc.update_setting(
                self.owner, "venue.default_language", "fr")

    def test_update_cancellation_policy_json_validated(self):
        good = ('[{"min_days_before_event": 30, "fee_percent_of_total": 10},'
                ' {"min_days_before_event": 0, "fee_percent_of_total": 50}]')
        self.svc.update_setting(
            self.owner, "finance.cancellation_policy", good)
        bad_cases = [
            "not json",
            "[]",  # empty list
            '[{"min_days_before_event": 30}]',  # missing fee
            '[{"min_days_before_event": 30, "fee_percent_of_total": 101}]',
            ('[{"min_days_before_event": 30, "fee_percent_of_total": 10},'
             ' {"min_days_before_event": 30, "fee_percent_of_total": 20}]'),
        ]
        for bad in bad_cases:
            with self.assertRaises(ValidationError, msg=bad):
                self.svc.update_setting(
                    self.owner, "finance.cancellation_policy", bad)

    def test_update_no_show_policy_json_validated(self):
        self.svc.update_setting(
            self.owner, "finance.no_show_policy",
            '{"fee_basis": "total", "refundable_percent": 25}')
        with self.assertRaises(ValidationError):
            self.svc.update_setting(
                self.owner, "finance.no_show_policy",
                '{"fee_basis": "everything", "refundable_percent": 0}')
        with self.assertRaises(ValidationError):
            self.svc.update_setting(
                self.owner, "finance.no_show_policy",
                '{"fee_basis": "deposit", "refundable_percent": 150}')

    def test_update_unknown_setting_rejected(self):
        with self.assertRaises(NotFoundError):
            self.svc.update_setting(self.owner, "no.such.key", "1")

    def test_update_is_audited_with_old_and_new(self):
        self.svc.update_setting(self.owner, "booking.hold_hours", "60")
        row = self.db.query_one(
            "SELECT * FROM audit_logs WHERE action = 'settings.changed' "
            "ORDER BY id DESC LIMIT 1")
        self.assertIsNotNone(row)
        self.assertEqual(row["employee_id"], self.owner.id)
        self.assertIn("48", row["old_value_json"])
        self.assertIn("60", row["new_value_json"])
        self.assertEqual(row["entity_type"], "setting")
