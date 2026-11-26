"""Phase 8 tests: Remember Me (FR-011), field-level encryption (FR-014),
post-confirmation service changes (FR-026 + BR-13), scheduling advisory
(FR-029), and full-Arabic i18n parity + UI build (NFR-005).

Temp databases only; 60,000 PBKDF2 iterations in tests.
"""
from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from tests.conftest import Phase1TestCase
from wedding_hall_system.config.settings import AppSettings
from wedding_hall_system.data.database import DatabaseManager
from wedding_hall_system.logic.auth_service import AuthService
from wedding_hall_system.logic.availability_service import AvailabilityService
from wedding_hall_system.logic.booking_service import BookingService
from wedding_hall_system.logic.contract_service import ContractService
from wedding_hall_system.logic.customer_service import CustomerService
from wedding_hall_system.logic.models import ServiceSelection
from wedding_hall_system.logic.refund_service import RefundService
from wedding_hall_system.ui.i18n import STRINGS, format_advisory_reason, t
from wedding_hall_system.utils import crypto
from wedding_hall_system.utils.exceptions import (
    AuthorizationError,
    ConfigurationError,
    NotFoundError,
    ValidationError,
)

TEST_KEY = "CVkSWgd56VHohFFpNwfpwu7gqD-zqYbM887v9XMtyO8="
OTHER_KEY = crypto.generate_key()

FRIDAY = datetime(2026, 12, 4, 18, 0)       # Friday: +10% pricing rule
FRIDAY_END = datetime(2026, 12, 4, 23, 0)
MONDAY = datetime(2026, 12, 7, 18, 0)
MONDAY_END = datetime(2026, 12, 7, 23, 0)


def _set_key(key: str | None) -> None:
    """Switch the encryption key for one test (restored by tearDown)."""
    if key is None:
        os.environ.pop("WEDDING_HALL_ENCRYPTION_KEY", None)
    else:
        os.environ["WEDDING_HALL_ENCRYPTION_KEY"] = key
    crypto.reset_fernet_cache()


class Phase8TestCase(Phase1TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.login_as("owner", "Owner123!")[0]
        self.manager = self.login_as("manager", "Manager123!")[0]
        self.receptionist = self.login_as("receptionist", "Reception123!")[0]
        self.accountant = self.login_as("accountant", "Account123!")[0]
        self.staff = self.login_as("staff", "Staff123!")[0]
        self.bookings = BookingService(self.db, self.settings)
        self.customers = CustomerService(self.db, self.settings)
        self.customer = next(
            c for c in self.customers.list_customers(self.owner)
            if c.full_name.startswith("Ahmed Hassan"))

    def tearDown(self) -> None:
        _set_key(TEST_KEY)
        super().tearDown()

    def _book(self, start=FRIDAY, end=FRIDAY_END, hall_id=1, guests=200,
              **kwargs):
        return self.bookings.create_booking(
            self.receptionist, customer_id=self.customer.id, hall_id=hall_id,
            package_id=1, event_type_id=1, start_at=start, end_at=end,
            guests=guests, **kwargs)

    def _confirm(self, booking, amount=1_320_000):
        return self.bookings.record_payment(
            self.accountant, booking.id, amount, "cash")


# -- FR-011 Remember Me -------------------------------------------------------
class TestRememberMe(Phase8TestCase):
    def test_issue_and_validate_rotates(self):
        token = self.auth.issue_remember_token(self.owner)
        self.assertTrue(token)
        employee, new_token = self.auth.validate_remember_token(token)
        self.assertIsNotNone(employee)
        self.assertEqual(employee.username, "owner")
        self.assertIsNotNone(new_token)
        self.assertNotEqual(new_token, token)
        # The presented token died on use (rotation).
        self.assertEqual(self.auth.validate_remember_token(token), (None, None))
        # The rotated token works.
        employee2, _ = self.auth.validate_remember_token(new_token)
        self.assertEqual(employee2.username, "owner")

    def test_wrong_and_empty_token_rejected(self):
        self.assertEqual(self.auth.validate_remember_token("not-a-token"),
                         (None, None))
        self.assertEqual(self.auth.validate_remember_token(None), (None, None))
        self.assertEqual(self.auth.validate_remember_token(""), (None, None))

    def test_expired_token_rejected_and_removed(self):
        token = self.auth.issue_remember_token(self.owner)
        past = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
        self.db.execute(
            "UPDATE remember_me_tokens SET expires_at = ?",
            (past,))
        self.assertEqual(self.auth.validate_remember_token(token), (None, None))
        rows = self.db.query_all("SELECT * FROM remember_me_tokens")
        self.assertEqual(rows, [])

    def test_at_most_three_live_tokens(self):
        tokens = [self.auth.issue_remember_token(self.owner) for _ in range(4)]
        rows = self.db.query_all(
            "SELECT * FROM remember_me_tokens WHERE employee_id = ?",
            (self.owner.id,))
        self.assertEqual(len(rows), 3)
        # The oldest token was revoked.
        self.assertEqual(self.auth.validate_remember_token(tokens[0]),
                         (None, None))
        self.assertIsNotNone(self.auth.validate_remember_token(tokens[3])[0])

    def test_logout_revokes_single_token(self):
        token = self.auth.issue_remember_token(self.owner)
        other = self.auth.issue_remember_token(self.owner)
        self.auth.revoke_remember_token(token)
        self.assertEqual(self.auth.validate_remember_token(token), (None, None))
        self.assertIsNotNone(self.auth.validate_remember_token(other)[0])
        # Revoking an unknown token is a no-op, never an error.
        self.auth.revoke_remember_token("not-a-token")

    def test_password_change_revokes_all_tokens(self):
        t1 = self.auth.issue_remember_token(self.owner)
        t2 = self.auth.issue_remember_token(self.owner)
        self.auth.change_password(self.owner, "Owner123!", "NewOwner123!")
        self.assertEqual(self.auth.validate_remember_token(t1), (None, None))
        self.assertEqual(self.auth.validate_remember_token(t2), (None, None))

    def test_create_session_after_restore_validates(self):
        token = self.auth.issue_remember_token(self.owner)
        employee, new_token = self.auth.validate_remember_token(token)
        session = self.auth.create_session(employee)
        self.assertIsNotNone(new_token)
        restored = self.auth.validate_session(session)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.username, "owner")

    def test_audit_records_issue_and_revoke_without_token_value(self):
        token = self.auth.issue_remember_token(self.owner)
        self.auth.revoke_remember_token(token)
        rows = self.db.query_all(
            "SELECT action FROM audit_logs WHERE action LIKE 'auth.remember_%'")
        actions = [r["action"] for r in rows]
        self.assertIn("auth.remember_issued", actions)
        self.assertIn("auth.remember_revoked", actions)
        rows = self.db.query_all(
            "SELECT description, old_value_json, new_value_json "
            "FROM audit_logs")
        hits = [r for r in rows if token in (r["description"] or "")
                or token in (r["old_value_json"] or "")
                or token in (r["new_value_json"] or "")]
        self.assertEqual(hits, [])


# -- FR-014 field-level encryption --------------------------------------------
class TestEncryption(Phase8TestCase):
    def test_round_trip_and_format(self):
        stored = crypto.encrypt_text("29901011234567")
        self.assertTrue(stored.startswith("enc:v1:"))
        self.assertNotIn("29901011234567", stored)
        self.assertEqual(crypto.decrypt_text(stored), "29901011234567")
        self.assertEqual(crypto.encrypt_text(""), "")
        self.assertEqual(crypto.decrypt_text(""), "")
        # Legacy plaintext stays readable (migration tolerance).
        self.assertEqual(crypto.decrypt_text("29901011234567"), "29901011234567")
        self.assertTrue(crypto.is_encrypted(stored))
        self.assertFalse(crypto.is_encrypted("29901011234567"))

    def test_wrong_key_fails_loudly(self):
        stored = crypto.encrypt_text("29901011234567")
        _set_key(OTHER_KEY)
        with self.assertRaises(ConfigurationError):
            crypto.decrypt_text(stored)

    def test_missing_key_fails_loudly_no_plaintext_fallback(self):
        _set_key(None)
        with self.assertRaises(ConfigurationError):
            crypto.encrypt_text("29901011234567")

    def test_seeded_customers_stored_encrypted(self):
        row = self.db.query_one(
            "SELECT national_id, national_id_hash FROM customers WHERE id = ?",
            (self.customer.id,))
        self.assertTrue(row["national_id"].startswith("enc:v1:"))
        self.assertNotIn("29001010123456", row["national_id"])
        self.assertEqual(len(row["national_id_hash"]), 64)
        self.assertEqual(
            row["national_id_hash"],
            crypto.blind_index("29001010123456"))

    def test_create_customer_encrypts_and_duplicate_still_blocked(self):
        created = self.customers.create_customer(
            self.owner, full_name="Test Person (example)",
            phone="01099998888", national_id="29901011234567")
        row = self.db.query_one(
            "SELECT national_id FROM customers WHERE id = ?",
            (created.id,))
        self.assertTrue(row["national_id"].startswith("enc:v1:"))
        with self.assertRaises(ValidationError) as ctx:
            self.customers.create_customer(
                self.owner, full_name="Test Person 2 (example)",
                phone="01099997777", national_id="29901011234567")
        self.assertTrue(getattr(ctx.exception, "duplicates", []))

    def test_masked_views_still_mask(self):
        full = self.customers.get_customer(self.owner, self.customer.id)
        self.assertEqual(full.national_id, "29001010123456")
        masked = self.customers.get_customer(
            self.receptionist, self.customer.id)
        self.assertEqual(masked.national_id, "****3456")
        listed = self.customers.list_customers(self.receptionist)
        mine = next(c for c in listed if c.id == self.customer.id)
        self.assertEqual(mine.national_id, "****3456")

    def test_audit_contains_no_plaintext_national_id(self):
        created = self.customers.create_customer(
            self.owner, full_name="Audit Probe (example)",
            phone="01088887777", national_id="29901011234567")
        self.customers.update_customer(
            self.owner, created.id, notes="probe update")
        hits = self.db.query_all(
            "SELECT id FROM audit_logs WHERE description LIKE ? "
            "OR old_value_json LIKE ? OR new_value_json LIKE ?",
            ("%29901011234567%", "%29901011234567%", "%29901011234567%"))
        self.assertEqual(hits, [])

    def test_migration_005_encrypts_existing_plaintext(self):
        # Build a schema-v4 database by hand: migrations 001-004, then
        # a plaintext customer row exactly as a pre-Phase-8 app stored it.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "v4.db"
        db = DatabaseManager(path)
        migrations_dir = Path("wedding_hall_system/data/migrations")
        with db.connection() as conn:
            # 001 is the full v1 schema copy; the migration runner
            # splits on ';' and cannot parse it (a ';' inside a
            # literal), so apply it with executescript like
            # initialize() does for schema.sql.
            conn.executescript(
                (migrations_dir / "001_initial.sql").read_text(
                    encoding="utf-8"))
            for name in ("002_contract_signature_and_specialties.sql",
                         "003_finance_policy_fields.sql",
                         "004_employee_language.sql"):
                DatabaseManager._apply_migration(conn, migrations_dir / name)
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (4)")
            conn.execute(
                """INSERT INTO customers
                   (full_name, phone, phone_normalized, email,
                    email_normalized, address, national_id, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("Legacy Customer (example)", "01011112222", "01011112222",
                 "", "", "Benha", "29001010123456", "pre-migration row"))
        self.assertEqual(db.current_version(), 4)
        db.initialize()  # Applies migration 005 + the Python step.
        self.assertEqual(db.current_version(), 5)
        row = db.query_one(
            "SELECT national_id, national_id_hash FROM customers "
            "WHERE full_name LIKE 'Legacy%'")
        self.assertTrue(row["national_id"].startswith("enc:v1:"))
        self.assertNotIn("29001010123456", row["national_id"])
        self.assertEqual(len(row["national_id_hash"]), 64)
        self.assertEqual(crypto.decrypt_text(row["national_id"]),
                         "29001010123456")
        # Re-running initialize is idempotent: already-encrypted rows
        # are only re-indexed, never double-encrypted.
        db.initialize()
        row2 = db.query_one(
            "SELECT national_id FROM customers WHERE full_name LIKE 'Legacy%'")
        self.assertEqual(row2["national_id"], row["national_id"])


# -- FR-026 service changes ---------------------------------------------------
class TestServiceChanges(Phase8TestCase):
    # DJ (id 2, per_event 2,500 EGP) is NOT in the Classic Wedding
    # package — Photographer (id 1) is included in it, so BR-12
    # ("never double-charge") prices it at 0 and the delta would be 0.
    EXTRA_SERVICE = 2

    def _confirmed_booking(self, pay_full=False):
        booking = self._book()
        self._confirm(booking)
        if pay_full:
            self.bookings.record_payment(
                self.accountant, booking.id, 4_400_000 - 1_320_000, "cash")
        return self.bookings.get_booking(self.owner, booking.id)

    def test_increase_becomes_outstanding_with_due_date(self):
        booking = self._confirmed_booking()
        result = self.bookings.change_services(
            self.manager, booking.id,
            service_selections=[ServiceSelection(self.EXTRA_SERVICE)],
            reason="Customer added a DJ.")
        self.assertGreater(result["delta"], 0)
        self.assertEqual(result["new_total"],
                         result["old_total"] + result["delta"])
        self.assertEqual(result["old_total"], 4_400_000)
        self.assertIsNotNone(result["due_at"])
        self.assertIsNone(result["refund_id"])
        # Due before the event, after now.
        due = datetime.fromisoformat(result["due_at"])
        self.assertLess(due, booking.start_at)
        # The booking total really changed; frozen items sum to it.
        updated = self.bookings.get_booking(self.owner, booking.id)
        self.assertEqual(updated.total_amount, result["new_total"])
        items = self.bookings.list_price_items(self.owner, booking.id)
        self.assertEqual(sum(i["amount"] for i in items),
                         result["new_total"])

    def test_decrease_creates_pending_refund_never_auto_paid(self):
        booking = self._confirmed_booking(pay_full=True)
        added = self.bookings.change_services(
            self.manager, booking.id,
            service_selections=[ServiceSelection(self.EXTRA_SERVICE)],
            reason="Add a DJ.")
        self.assertIsNone(added["refund_id"])
        # Pay the increase too, so removing it creates an overpayment.
        self.bookings.record_payment(
            self.accountant, booking.id, added["delta"], "cash")
        removed = self.bookings.change_services(
            self.manager, booking.id, service_selections=[],
            reason="Customer dropped the DJ.")
        self.assertLess(removed["delta"], 0)
        self.assertEqual(removed["delta"], -added["delta"])
        self.assertIsNotNone(removed["refund_id"])
        refund = RefundService(self.db, self.settings).get_refund(
            self.accountant, removed["refund_id"])
        self.assertEqual(refund.status, "pending_approval")
        self.assertEqual(refund.amount, -removed["delta"])
        # No payment was created for it: it waits for approval. The
        # only payments are the ones the customer actually made.
        payments = self.db.query_all(
            "SELECT * FROM payments WHERE booking_id = ? AND status = 'recorded'",
            (booking.id,))
        self.assertEqual(sum(int(p["amount"]) for p in payments),
                         4_400_000 + added["delta"])

    def test_change_records_old_and_new(self):
        booking = self._confirmed_booking()
        result = self.bookings.change_services(
            self.manager, booking.id,
            service_selections=[ServiceSelection(self.EXTRA_SERVICE)],
            reason="Add a DJ.")
        changes = self.bookings.list_service_changes(self.owner, booking.id)
        self.assertEqual(len(changes), 1)
        change = changes[0]
        self.assertEqual(change["old_total"], 4_400_000)
        self.assertEqual(change["new_total"], result["new_total"])
        self.assertEqual(change["delta"], result["delta"])
        import json
        old_services = json.loads(change["old_services_json"])
        new_services = json.loads(change["new_services_json"])
        self.assertEqual(old_services, [])
        self.assertEqual(len(new_services), 1)
        self.assertEqual(new_services[0]["service_id"], self.EXTRA_SERVICE)
        self.assertEqual(new_services[0]["amount"], result["delta"])
        self.assertEqual(change["reason"], "Add a DJ.")
        timeline = self.bookings.timeline(self.owner, booking.id)
        self.assertIn("services_changed", [t["action"] for t in timeline])
        audits = self.db.query_all(
            "SELECT * FROM audit_logs WHERE action = 'booking.services_changed'")
        self.assertEqual(len(audits), 1)

    def test_contract_version_rule(self):
        booking = self._confirmed_booking()
        contracts = ContractService(self.db, self.settings)
        contract = contracts.generate_contract(
            self.owner, booking.id, "Standard Wedding Contract")
        self.assertEqual(len(contracts.list_versions(
            self.owner, contract.id)), 1)
        # A price-changing service change requires a new version.
        result = self.bookings.change_services(
            self.manager, booking.id,
            service_selections=[ServiceSelection(self.EXTRA_SERVICE)],
            reason="Add a DJ.")
        self.assertEqual(result["contract_action"], "new_version")
        versions = contracts.list_versions(self.owner, contract.id)
        self.assertEqual(len(versions), 2)
        # A zero-delta re-apply is a note only: no new version.
        same = self.bookings.change_services(
            self.manager, booking.id,
            service_selections=[ServiceSelection(self.EXTRA_SERVICE)],
            reason="Re-apply same services.")
        self.assertEqual(same["delta"], 0)
        self.assertEqual(same["contract_action"], "note")
        self.assertEqual(len(contracts.list_versions(
            self.owner, contract.id)), 2)

    def test_unauthorized_change_rejected(self):
        booking = self._confirmed_booking()
        for emp in (self.receptionist, self.staff, self.accountant):
            with self.assertRaises(AuthorizationError):
                self.bookings.change_services(
                    emp, booking.id,
                    service_selections=[ServiceSelection(self.EXTRA_SERVICE)])

    def test_change_on_non_confirmed_rejected(self):
        pending = self._book(start=MONDAY, end=MONDAY_END)
        with self.assertRaises(ValidationError):
            self.bookings.change_services(
                self.manager, pending.id,
                service_selections=[ServiceSelection(self.EXTRA_SERVICE)])
        with self.assertRaises(NotFoundError):
            self.bookings.change_services(
                self.manager, 99999,
                service_selections=[ServiceSelection(self.EXTRA_SERVICE)])


# -- FR-029 scheduling advisory -----------------------------------------------
class TestAdvisory(Phase8TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.availability = AvailabilityService(self.db, self.settings)

    def test_suggests_same_hall_earlier_time_first(self):
        self._book()  # Friday 18:00-23:00, hall 1 (buffer 60 min).
        suggestions = self.availability.suggest_alternatives(
            self.owner, hall_id=1, start_at=FRIDAY, end_at=FRIDAY_END,
            guests=200, package_id=1)
        self.assertTrue(suggestions)
        first = suggestions[0]
        self.assertEqual(first.hall_id, 1)
        self.assertEqual(first.kind, "same_hall_earlier")
        self.assertEqual(first.start_at, datetime(2026, 12, 4, 12, 0))
        self.assertEqual(first.end_at, datetime(2026, 12, 4, 17, 0))
        self.assertTrue(first.reason_key)
        # Second suggestion: same hall, same time, next day.
        self.assertEqual(suggestions[1].kind, "same_hall_another_day")
        self.assertEqual(suggestions[1].start_at, datetime(2026, 12, 5, 18, 0))

    def test_suggestions_respect_all_constraints(self):
        self._book()
        suggestions = self.availability.suggest_alternatives(
            self.owner, hall_id=1, start_at=FRIDAY, end_at=FRIDAY_END,
            guests=200, package_id=1)
        for s in suggestions:
            self.assertTrue(self.availability.check_availability(
                self.owner, hall_id=s.hall_id, start_at=s.start_at,
                end_at=s.end_at).available)
            duration = (s.end_at - s.start_at).total_seconds() / 60
            self.assertGreaterEqual(duration, 180)  # package minimum
            self.assertTrue(duration == 300 or s.kind == "shorter_duration")
            self.assertEqual(s.start_at.date(), s.end_at.date())
            self.assertTrue(s.reason_key)
            ar_reason = format_advisory_reason("ar", s)
            self.assertTrue(ar_reason)
            # No English duration fragment leaks into the Arabic text.
            self.assertNotIn(" min", ar_reason)
            if s.reason_key == "advisory.same_hall_time":
                self.assertNotIn(" h ", ar_reason)
            self.assertTrue(format_advisory_reason("en", s))

    def test_deterministic_order(self):
        self._book()
        kwargs = dict(hall_id=1, start_at=FRIDAY, end_at=FRIDAY_END,
                      guests=200, package_id=1)
        first = self.availability.suggest_alternatives(self.owner, **kwargs)
        second = self.availability.suggest_alternatives(self.owner, **kwargs)
        self.assertEqual(
            [(s.hall_id, s.start_at, s.kind) for s in first],
            [(s.hall_id, s.start_at, s.kind) for s in second])

    def test_never_mutates_a_booking(self):
        booking = self._book()
        before = self.bookings.get_booking(self.owner, booking.id)
        self.availability.suggest_alternatives(
            self.owner, hall_id=1, start_at=FRIDAY, end_at=FRIDAY_END,
            guests=200, package_id=1)
        after = self.bookings.get_booking(self.owner, booking.id)
        self.assertEqual(before.start_at, after.start_at)
        self.assertEqual(before.total_amount, after.total_amount)
        self.assertEqual(
            len(self.db.query_all("SELECT * FROM bookings")), 1)

    def test_other_hall_suggested_when_first_hall_busy(self):
        # Block hall 1 for the whole of Friday; 100 guests fit the
        # Garden Hall (capacity 150).
        self._book(start=datetime(2026, 12, 4, 12, 0),
                   end=datetime(2026, 12, 4, 23, 0), guests=100)
        suggestions = self.availability.suggest_alternatives(
            self.owner, hall_id=1, start_at=FRIDAY, end_at=FRIDAY_END,
            guests=100, package_id=1, days_ahead=1, limit=20)
        self.assertTrue(any(s.hall_id == 2 for s in suggestions))
        other = next(s for s in suggestions if s.hall_id == 2)
        self.assertIn(other.kind,
                      ("other_hall_same_day", "other_hall_another_day"))

    def test_below_package_minimum_returns_empty(self):
        suggestions = self.availability.suggest_alternatives(
            self.owner, hall_id=1,
            start_at=datetime(2026, 12, 7, 18, 0),
            end_at=datetime(2026, 12, 7, 19, 0),
            guests=100, package_id=1)
        self.assertEqual(suggestions, [])


# -- NFR-005 full Arabic ------------------------------------------------------
class TestI18nPhase8(Phase8TestCase):
    ARABIC_RE = re.compile(r"[\u0600-\u06FF]")

    def test_key_parity_both_languages(self):
        en_keys = set(STRINGS["en"])
        ar_keys = set(STRINGS["ar"])
        self.assertEqual(en_keys, ar_keys)

    def test_all_tab_titles_arabic(self):
        tab_keys = [k for k in STRINGS["en"] if k.startswith("tab.")]
        self.assertGreaterEqual(len(tab_keys), 24)
        for key in tab_keys:
            ar = STRINGS["ar"][key]
            self.assertRegex(ar, self.ARABIC_RE, key)
            self.assertNotEqual(ar, STRINGS["en"][key], key)

    def test_key_screens_have_arabic_strings(self):
        # One representative key per screen family; Arabic must differ
        # from English and contain Arabic script.
        sample = [
            "catalog.halls_heading", "booking.customers_heading",
            "ops.contracts_heading", "fin.heading",
            "cal.heading", "bkp.heading", "set.heading", "dashboard.title",
            "search.heading", "files.heading", "reports.heading",
            "login.button", "login.remember_me", "login.session_restored",
            "booking.change_services", "advisory.title",
            "notif.booking_services_changed.title",
        ]
        for key in sample:
            self.assertIn(key, STRINGS["en"], key)
            self.assertIn(key, STRINGS["ar"], key)
            self.assertRegex(STRINGS["ar"][key], self.ARABIC_RE, key)
            self.assertNotEqual(STRINGS["ar"][key], STRINGS["en"][key], key)

    def test_notification_bodies_carry_params_in_arabic(self):
        from wedding_hall_system.ui.i18n import format_notification
        row = {"title": "Task completed", "body": "Task X is completed.",
               "type": "task_status_changed",
               "data_json": '{"title_key": "notif.task_status_changed.title",'
                            ' "body_key": "notif.task_status_changed.body",'
                            ' "params": {"title": "Setup",'
                            ' "status": "completed"}}'}
        title, body = format_notification("ar", row)
        self.assertRegex(title + " " + body, self.ARABIC_RE)
        self.assertIn("مكتملة", body)

    def test_language_toggle_executes_and_switches_everything(self):
        # Regression test (Phase 8): the merged toggle must EXECUTE,
        # not just be wired. gr.update(label=...) on a Button raises
        # on the server (Button has no label), which the config-only
        # check above could not catch.
        from wedding_hall_system.ui.app import build_app
        demo = build_app(self.settings, self.db)
        _, token = self.auth.login("owner", "Owner123!")
        lang_fn = None
        for fn in demo.fns.values():
            if fn.get_config().get("api_name") == "do_language":
                lang_fn = fn.fn
                break
        self.assertIsNotNone(lang_fn)
        outputs = lang_fn("en", token)
        self.assertEqual(outputs[0], "en")
        updates = [o for o in outputs if isinstance(o, dict)]
        self.assertGreater(len(updates), 100)
        labels = [o.get("label") for o in updates if "label" in o]
        values = [o.get("value") for o in updates if "value" in o]
        self.assertIn("Bookings", labels)  # tab title switches
        self.assertIn("Log in", values)  # button text switches
        outputs_ar = lang_fn("ar", token)
        labels_ar = [o.get("label") for o in outputs_ar
                     if isinstance(o, dict) and "label" in o]
        values_ar = [o.get("value") for o in outputs_ar
                     if isinstance(o, dict) and "value" in o]
        self.assertIn("الحجوزات", labels_ar)
        self.assertIn("تسجيل الدخول", values_ar)

    def test_app_builds_with_arabic_tabs_and_remember_me_wiring(self):
        # Needs the proxy workaround (conftest repairs NO_PROXY first).
        from gradio.components import BrowserState
        from gradio.layouts import Tab

        from wedding_hall_system.ui.app import build_app
        demo = build_app(self.settings, self.db)
        tabs = [b.label for b in demo.blocks.values() if isinstance(b, Tab)]
        self.assertGreaterEqual(len(tabs), 24)
        for label in tabs:
            self.assertRegex(label, self.ARABIC_RE, label)
        states = [b for b in demo.blocks.values()
                  if isinstance(b, BrowserState)]
        self.assertEqual(len(states), 1)
        self.assertEqual(states[0].storage_key, "whms-remember-me")
        load_fns = []
        for fn in demo.fns.values():
            for target in (fn.targets or []):
                name = (target[1] if isinstance(target, tuple)
                        else getattr(target, "event_name", None))
                if name == "load":
                    load_fns.append(fn)
        self.assertEqual(len(load_fns), 1)
        bs_id = states[0]._id
        self.assertIn(bs_id, [getattr(c, "_id", c)
                              for c in load_fns[0].inputs])
        self.assertIn(bs_id, [getattr(c, "_id", c)
                              for c in load_fns[0].outputs])
        self.assertEqual(len(load_fns[0].outputs), 11)
