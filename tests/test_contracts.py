"""Phase 4 tests: contract templates, generation/versioning, signatures,
signed-copy uploads/downloads, and contract notifications."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path

from tests.conftest import Phase1TestCase
from wedding_hall_system.logic.booking_service import BookingService
from wedding_hall_system.logic.contract_service import ContractService
from wedding_hall_system.logic.customer_service import CustomerService
from wedding_hall_system.logic.notification_service import NotificationService
from wedding_hall_system.utils.exceptions import (
    AttachmentValidationError,
    AuthorizationError,
    NotFoundError,
    ValidationError,
)

FRIDAY = datetime(2026, 12, 4, 18, 0)
FRIDAY_END = datetime(2026, 12, 4, 23, 0)
MONDAY = datetime(2026, 12, 7, 18, 0)
MONDAY_END = datetime(2026, 12, 7, 23, 0)


class TestContracts(Phase1TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.login_as("owner", "Owner123!")[0]
        self.manager = self.login_as("manager", "Manager123!")[0]
        self.receptionist = self.login_as("receptionist", "Reception123!")[0]
        self.accountant = self.login_as("accountant", "Account123!")[0]
        self.staff = self.login_as("staff", "Staff123!")[0]
        # Contracts write HTML files + uploads; keep them in the temp dir.
        test_settings = replace(self.settings, project_root=Path(self._tmp.name))
        self.contracts = ContractService(self.db, test_settings)
        self.notifications = NotificationService(self.db)
        self.bookings = BookingService(self.db, self.settings)
        self.customer = next(
            c for c in CustomerService(self.db, self.settings).list_customers(self.owner)
            if c.full_name.startswith("Ahmed Hassan"))

    def _book(self, start=FRIDAY, end=FRIDAY_END, **kwargs):
        kwargs.setdefault("event_type_id", 1)
        kwargs.setdefault("package_id", 1)
        return self.bookings.create_booking(
            self.receptionist, customer_id=self.customer.id, hall_id=1,
            start_at=start, end_at=end, guests=200, **kwargs)

    def _confirmed_booking(self, **kwargs):
        booking = self._book(**kwargs)
        self.bookings.record_payment(self.accountant, booking.id, 1_320_000, "cash")
        return self.bookings.get_booking(self.owner, booking.id)

    def _write_upload(self, name: str, data: bytes) -> str:
        path = Path(self._tmp.name) / name
        path.write_bytes(data)
        return str(path)

    # -- templates -----------------------------------------------------------
    def test_seeded_default_template_exists(self):
        templates = self.contracts.list_templates(self.owner)
        self.assertEqual(len(templates), 1)
        self.assertEqual(templates[0]["name"], "Standard Wedding Contract")
        self.assertIn("{{customer_name}}", templates[0]["body"])

    def test_template_crud_and_permissions(self):
        created = self.contracts.create_template(
            self.manager, name="Short Form",
            body="<html><body>Contract for {{customer_name}}</body></html>")
        self.assertEqual(created["name"], "Short Form")
        with self.assertRaises(ValidationError):  # duplicate name
            self.contracts.create_template(
                self.manager, name="Short Form", body="x {{date}}")
        with self.assertRaises(AuthorizationError):  # receptionist: view only
            self.contracts.create_template(
                self.receptionist, name="Other", body="x {{date}}")
        with self.assertRaises(ValidationError):  # unknown placeholder
            self.contracts.create_template(
                self.manager, name="Bad", body="Hello {{unknown_field}}")
        updated = self.contracts.update_template(
            self.manager, name="Short Form",
            body="<html><body>Updated for {{customer_name}} on {{date}}</body></html>")
        self.assertIn("Updated for", updated["body"])
        with self.assertRaises(NotFoundError):
            self.contracts.update_template(self.manager, name="Nope", body="x")
        self.contracts.delete_template(self.manager, name="Short Form")
        self.assertEqual(len(self.contracts.list_templates(self.owner)), 1)

    def test_template_in_use_cannot_be_deleted(self):
        booking = self._confirmed_booking()
        self.contracts.generate_contract(self.manager, booking.id,
                                         "Standard Wedding Contract")
        with self.assertRaises(ValidationError):
            self.contracts.delete_template(
                self.manager, name="Standard Wedding Contract")

    # -- generation / versioning ----------------------------------------------
    def test_generate_contract_from_confirmed_booking(self):
        booking = self._confirmed_booking()
        contract = self.contracts.generate_contract(
            self.manager, booking.id, "Standard Wedding Contract")
        self.assertTrue(contract.number.startswith("CT-"))
        self.assertEqual(contract.signature_status, "draft")
        body = self.contracts.get_version_body(self.owner, contract.id, 1)
        self.assertIn("Ahmed Hassan (example)", body)
        self.assertIn("Main Hall (example)", body)
        self.assertIn("44,000.00 EGP", body)          # frozen total
        self.assertIn("13,200.00 EGP", body)          # frozen deposit paid
        self.assertIn(contract.number, body)
        self.assertNotIn("{{", body)                 # all placeholders rendered
        # HTML file written next to the DB-facing storage dir.
        stored = Path(self._tmp.name) / "storage" / "contracts" / f"{contract.number}_v1.html"
        self.assertTrue(stored.is_file())
        self.assertEqual(stored.read_text(encoding="utf-8"), body)
        # Contract-awaiting-signature notification reached the Manager.
        inbox = self.notifications.inbox(self.manager)
        self.assertTrue(any(n["type"] == "contract_awaiting_signature"
                            and contract.number in n["title"] for n in inbox))

    def test_generate_rejected_for_pending_booking(self):
        booking = self._book()  # still Pending, no deposit
        with self.assertRaises(ValidationError):
            self.contracts.generate_contract(
                self.manager, booking.id, "Standard Wedding Contract")

    def test_generate_rejected_for_unknown_booking_or_template(self):
        with self.assertRaises(NotFoundError):
            self.contracts.generate_contract(
                self.manager, 99999, "Standard Wedding Contract")
        booking = self._confirmed_booking()
        with self.assertRaises(NotFoundError):
            self.contracts.generate_contract(
                self.manager, booking.id, "No Such Template")

    def test_second_generate_rejected_use_new_version_instead(self):
        booking = self._confirmed_booking()
        self.contracts.generate_contract(
            self.manager, booking.id, "Standard Wedding Contract")
        with self.assertRaises(ValidationError):
            self.contracts.generate_contract(
                self.manager, booking.id, "Standard Wedding Contract")

    def test_new_version_preserves_old_version(self):
        booking = self._confirmed_booking()
        contract = self.contracts.generate_contract(
            self.manager, booking.id, "Standard Wedding Contract")
        v1_body = self.contracts.get_version_body(self.owner, contract.id, 1)
        edited = v1_body.replace("44,000.00 EGP", "44,000.00 EGP (amended)")
        version2 = self.contracts.create_new_version(
            self.manager, contract.id, body=edited)
        self.assertEqual(version2.version, 2)
        versions = self.contracts.list_versions(self.owner, contract.id)
        self.assertEqual([v.version for v in versions], [1, 2])
        # Version 1 is byte-identical to before.
        self.assertEqual(
            self.contracts.get_version_body(self.owner, contract.id, 1), v1_body)
        self.assertIn("(amended)",
                      self.contracts.get_version_body(self.owner, contract.id, 2))
        with self.assertRaises(NotFoundError):
            self.contracts.get_version_body(self.owner, contract.id, 3)

    def test_new_version_on_signed_contract_resets_signature(self):
        booking = self._confirmed_booking()
        contract = self.contracts.generate_contract(
            self.manager, booking.id, "Standard Wedding Contract")
        self.contracts.set_signature_status(
            self.manager, contract.id, "pending_signature")
        signed = self.contracts.set_signature_status(
            self.manager, contract.id, "signed", signature_date="2026-11-27")
        self.assertEqual(signed.signature_status, "signed")
        self.contracts.create_new_version(self.manager, contract.id)
        refreshed = self.contracts.get_contract(self.owner, contract.id)
        self.assertEqual(refreshed.signature_status, "pending_signature")

    # -- signature transitions --------------------------------------------------
    def test_signature_status_transitions(self):
        booking = self._confirmed_booking()
        contract = self.contracts.generate_contract(
            self.manager, booking.id, "Standard Wedding Contract")
        with self.assertRaises(ValidationError):  # draft -> signed is not allowed
            self.contracts.set_signature_status(
                self.manager, contract.id, "signed", signature_date="2026-11-27")
        pending = self.contracts.set_signature_status(
            self.manager, contract.id, "pending_signature")
        self.assertEqual(pending.signature_status, "pending_signature")
        with self.assertRaises(ValidationError):  # signed without a date
            self.contracts.set_signature_status(self.manager, contract.id, "signed")
        with self.assertRaises(ValidationError):  # bad date format
            self.contracts.set_signature_status(
                self.manager, contract.id, "signed", signature_date="27/11/2026")
        signed = self.contracts.set_signature_status(
            self.manager, contract.id, "signed", signature_date="2026-11-27")
        self.assertEqual(signed.signature_date, "2026-11-27")
        with self.assertRaises(ValidationError):  # signed -> pending is final
            self.contracts.set_signature_status(
                self.manager, contract.id, "pending_signature")
        cancelled = self.contracts.set_signature_status(
            self.manager, contract.id, "cancelled")
        self.assertEqual(cancelled.signature_status, "cancelled")

    # -- signed-copy upload / download -------------------------------------------
    def test_signed_upload_happy_path(self):
        booking = self._confirmed_booking()
        contract = self.contracts.generate_contract(
            self.manager, booking.id, "Standard Wedding Contract")
        src = self._write_upload("signed.pdf", b"%PDF-1.4 signed contract bytes")
        result = self.contracts.upload_signed_copy(
            self.manager, contract.id, src, "signed.pdf", "2026-11-27")
        self.assertTrue(result["storage_filename"].endswith(".pdf"))
        self.assertNotEqual(result["storage_filename"], "signed.pdf")  # random name
        self.assertEqual(result["size_bytes"], len(b"%PDF-1.4 signed contract bytes"))
        self.assertEqual(len(result["sha256"]), 64)
        refreshed = self.contracts.get_contract(self.owner, contract.id)
        self.assertEqual(refreshed.signature_status, "signed")
        self.assertEqual(refreshed.signature_date, "2026-11-27")
        attachments = self.contracts.list_attachments(self.owner, contract.id)
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]["original_filename"], "signed.pdf")
        # Authorized download (Accountant) returns the stored file.
        path = self.contracts.download_attachment(
            self.accountant, attachments[0]["id"])
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_bytes(), b"%PDF-1.4 signed contract bytes")
        # Unauthorized download (Staff has no attachment.download).
        with self.assertRaises(AuthorizationError):
            self.contracts.download_attachment(self.staff, attachments[0]["id"])

    def test_signed_upload_validation(self):
        booking = self._confirmed_booking()
        contract = self.contracts.generate_contract(
            self.manager, booking.id, "Standard Wedding Contract")
        # Disallowed extension.
        src = self._write_upload("signed.txt", b"plain text")
        with self.assertRaises(AttachmentValidationError):
            self.contracts.upload_signed_copy(
                self.manager, contract.id, src, "signed.txt", "2026-11-27")
        # Content does not match the extension.
        src = self._write_upload("fake.pdf", b"this is not a pdf")
        with self.assertRaises(AttachmentValidationError):
            self.contracts.upload_signed_copy(
                self.manager, contract.id, src, "fake.pdf", "2026-11-27")
        # Oversized file (> 10 MB).
        big = self._write_upload("big.pdf", b"%PDF" + b"x" * (10 * 1024 * 1024))
        with self.assertRaises(AttachmentValidationError):
            self.contracts.upload_signed_copy(
                self.manager, contract.id, big, "big.pdf", "2026-11-27")
        # Missing signature date.
        src = self._write_upload("signed2.pdf", b"%PDF-1.4 ok")
        with self.assertRaises(ValidationError):
            self.contracts.upload_signed_copy(
                self.manager, contract.id, src, "signed2.pdf", "")
        # Receptionist may upload (has attachment.upload) but may not
        # generate contracts — upload itself is contract.manage-gated.
        with self.assertRaises(AuthorizationError):
            self.contracts.upload_signed_copy(
                self.receptionist, contract.id, src, "signed2.pdf", "2026-11-27")

    def test_contract_history_is_audited(self):
        booking = self._confirmed_booking()
        contract = self.contracts.generate_contract(
            self.manager, booking.id, "Standard Wedding Contract")
        self.contracts.create_new_version(self.manager, contract.id)
        rows = self.db.query_all(
            "SELECT action FROM audit_logs WHERE entity_type = 'contract' "
            "AND entity_id = ? ORDER BY id", (contract.id,))
        self.assertEqual([r["action"] for r in rows],
                         ["contract.generate", "contract.new_version"])
