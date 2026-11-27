"""Phase 6 tests: files, search, dashboards, reports, CSV export, and
the remaining FR-060 notification triggers (SRS §11, §12.1, §13).

Money is integer piasters throughout (1 EGP = 100). The seeded
Friday Classic Wedding booking totals 4,400,000 (44,000 EGP) with a
30% deposit of 1,320,000. Upload fixtures are minimal but
content-valid files (real PDF/JPEG/PNG magic bytes).
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta
from pathlib import Path

from tests.conftest import Phase1TestCase
from wedding_hall_system.data.repositories import system_repository
from wedding_hall_system.data.seed import hash_password
from wedding_hall_system.logic import audit_service
from wedding_hall_system.logic.attachment_service import AttachmentService
from wedding_hall_system.logic.booking_service import BookingService
from wedding_hall_system.logic.contract_service import ContractService
from wedding_hall_system.logic.customer_service import CustomerService
from wedding_hall_system.logic.expense_service import ExpenseService
from wedding_hall_system.logic.hall_service import HallService
from wedding_hall_system.logic.notification_service import NotificationService
from wedding_hall_system.logic.payment_service import PaymentService
from wedding_hall_system.logic.report_service import ReportService, to_csv
from wedding_hall_system.logic.search_service import SearchService
from wedding_hall_system.logic.task_service import TaskService
from wedding_hall_system.utils.exceptions import (
    AttachmentValidationError,
    AuthorizationError,
    NotFoundError,
    ValidationError,
)

FRIDAY = datetime(2026, 12, 4, 18, 0)
FRIDAY_END = datetime(2026, 12, 4, 23, 0)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PDF_BYTES = b"%PDF-1.4\n%fake pdf content\n"
JPG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
EXE_BYTES = b"MZ" + b"\x00" * 32


class TestFilesSearchReports(Phase1TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.login_as("owner", "Owner123!")[0]
        self.manager = self.login_as("manager", "Manager123!")[0]
        self.receptionist = self.login_as("receptionist", "Reception123!")[0]
        self.accountant = self.login_as("accountant", "Account123!")[0]
        self.staff = self.login_as("staff", "Staff123!")[0]
        self.bookings = BookingService(self.db, self.settings)
        self.payments = PaymentService(self.db, self.settings)
        self.expenses = ExpenseService(self.db, self.settings)
        self.contracts = ContractService(self.db, self.settings)
        self.tasks = TaskService(self.db)
        self.halls = HallService(self.db, self.settings)
        self.files = AttachmentService(self.db, self.settings)
        self.search = SearchService(self.db, self.settings)
        self.reports = ReportService(self.db, self.settings)
        self.notifications = NotificationService(self.db)
        customers = CustomerService(self.db, self.settings).list_customers(self.owner)
        self.customer = next(
            c for c in customers if c.full_name.startswith("Ahmed Hassan"))
        self.customer2 = next(
            c for c in customers if c.full_name.startswith("Mona Ali"))

    # -- fixture helpers ------------------------------------------------------
    def _write(self, name: str, data: bytes) -> str:
        path = Path(self._tmp.name) / name
        path.write_bytes(data)
        return str(path)

    def _book(self, start=FRIDAY, end=FRIDAY_END, hall_id=1, guests=200,
              **kwargs):
        kwargs.setdefault("package_id", 1)
        kwargs.setdefault("event_type_id", 1)
        return self.bookings.create_booking(
            self.receptionist, customer_id=self.customer.id, hall_id=hall_id,
            start_at=start, end_at=end, guests=guests, **kwargs)

    def _confirm(self, booking, amount=1_320_000):
        return self.bookings.record_payment(
            self.accountant, booking.id, amount, "cash")

    def _confirmed_booking(self):
        booking = self._book()
        self._confirm(booking)
        return self.bookings.get_booking(self.owner, booking.id)

    def _plan_with_assigned_tasks(self):
        """Confirmed Friday booking + generated plan; two tasks assigned
        to staff (due 17:00 and 18:00). Returns (booking, [task rows])."""
        booking = self._confirmed_booking()
        plan = self.tasks.generate_event_plan(self.manager, booking.id)
        tasks = self.tasks.list_tasks(self.manager, event_plan_id=plan["id"])
        assigned = []
        for task in tasks:
            if task["due_at"] in ("2026-12-04T17:00:00", "2026-12-04T18:00:00"):
                self.tasks.assign_task(self.manager, int(task["id"]),
                                       self.staff.id)
                assigned.append(task)
        return booking, assigned

    def _limited_user(self):
        """A user holding ONLY booking.view (no booking.view_price) to
        prove amounts are hidden from search results."""
        pw_hash, salt = hash_password("Viewer123!", iterations=60_000)
        emp_id = self.db.execute(
            """INSERT INTO employees (username, password_hash, password_salt,
                                      full_name)
               VALUES ('viewer', ?, ?, 'Viewer (test)')""",
            (pw_hash, salt))
        role_id = self.db.execute(
            "INSERT INTO roles (name, description) VALUES ('Viewer', 'test')")
        perm = self.db.query_one(
            "SELECT id FROM permissions WHERE code = 'booking.view'")
        self.db.execute(
            "INSERT INTO role_permissions (role_id, permission_id) VALUES (?, ?)",
            (role_id, int(perm["id"])))
        self.db.execute(
            "INSERT INTO employee_roles (employee_id, role_id) VALUES (?, ?)",
            (emp_id, role_id))
        return self.login_as("viewer", "Viewer123!")[0]

    # -- files (FR-061, T-20) ---------------------------------------------------
    def test_upload_valid_png_to_customer(self):
        src = self._write("id.png", PNG_BYTES)
        result = self.files.upload(
            self.receptionist, entity_type="customer", entity_id=self.customer.id,
            src_path=src, original_filename="id.png",
            category="Identity Documents")
        self.assertIn("attachment_id", result)
        self.assertEqual(result["mime_type"], "image/png")
        self.assertEqual(result["size_bytes"], len(PNG_BYTES))
        self.assertEqual(len(result["sha256"]), 64)
        stored = self.settings.project_root / "storage" / "uploads" / result["storage_filename"]
        self.assertTrue(stored.is_file())
        self.assertNotEqual(result["storage_filename"], "id.png")
        rows = self.files.list_attachments(
            self.manager, entity_type="customer", entity_id=self.customer.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["original_filename"], "id.png")

    def test_upload_bad_extension_rejected(self):
        src = self._write("tool.exe", EXE_BYTES)
        with self.assertRaises(AttachmentValidationError):
            self.files.upload(
                self.receptionist, entity_type="customer",
                entity_id=self.customer.id, src_path=src,
                original_filename="tool.exe", category="Other")
        src = self._write("notes.txt", b"hello")
        with self.assertRaises(AttachmentValidationError):
            self.files.upload(
                self.receptionist, entity_type="customer",
                entity_id=self.customer.id, src_path=src,
                original_filename="notes.txt", category="Other")

    def test_upload_magic_byte_mismatch_rejected(self):
        src = self._write("fake.pdf", b"this is not a pdf at all")
        with self.assertRaises(AttachmentValidationError):
            self.files.upload(
                self.receptionist, entity_type="customer",
                entity_id=self.customer.id, src_path=src,
                original_filename="fake.pdf", category="Other")

    def test_upload_oversize_rejected(self):
        system_repository.set_setting(self.db, "attachments.max_size_mb", "0")
        src = self._write("big.png", PNG_BYTES)
        with self.assertRaises(AttachmentValidationError):
            self.files.upload(
                self.receptionist, entity_type="customer",
                entity_id=self.customer.id, src_path=src,
                original_filename="big.png", category="Other")

    def test_upload_path_traversal_filename_is_safe(self):
        src = self._write("evil.png", PNG_BYTES)
        result = self.files.upload(
            self.receptionist, entity_type="customer",
            entity_id=self.customer.id, src_path=src,
            original_filename="../../etc/evil.png", category="Other")
        self.assertNotIn("..", result["storage_filename"])
        self.assertNotIn("/", result["storage_filename"])

    def test_upload_to_missing_entity_rejected(self):
        src = self._write("id.png", PNG_BYTES)
        with self.assertRaises(NotFoundError):
            self.files.upload(
                self.receptionist, entity_type="booking", entity_id=9999,
                src_path=src, original_filename="id.png", category="Other")
        with self.assertRaises(ValidationError):
            self.files.upload(
                self.receptionist, entity_type="spaceship", entity_id=1,
                src_path=src, original_filename="id.png", category="Other")

    def test_download_authorized_and_staff_denied(self):
        src = self._write("receipt.pdf", PDF_BYTES)
        result = self.files.upload(
            self.accountant, entity_type="customer",
            entity_id=self.customer.id, src_path=src,
            original_filename="receipt.pdf", category="Receipts")
        path = self.files.download(self.manager, result["attachment_id"])
        self.assertTrue(Path(path).is_file())
        self.assertEqual(Path(path).read_bytes(), PDF_BYTES)
        with self.assertRaises(AuthorizationError):
            self.files.download(self.staff, result["attachment_id"])
        with self.assertRaises(AuthorizationError):
            self.files.upload(self.staff, entity_type="customer",
                              entity_id=self.customer.id, src_path=src,
                              original_filename="receipt.pdf",
                              category="Receipts")

    def test_download_path_traversal_row_rejected(self):
        # A crafted row whose storage name escapes the uploads dir.
        row_id = self.db.execute(
            """INSERT INTO attachments
               (entity_type, entity_id, original_filename, storage_filename,
                mime_type, size_bytes, category, uploaded_by)
               VALUES ('customer', ?, 'evil.png', '../evil.png',
                       'image/png', 10, 'Other', ?)""",
            (self.customer.id, self.owner.id))
        with self.assertRaises(ValidationError):
            self.files.download(self.owner, row_id)

    def test_preview_image_and_pdf(self):
        src = self._write("photo.jpg", JPG_BYTES)
        result = self.files.upload(
            self.receptionist, entity_type="customer",
            entity_id=self.customer.id, src_path=src,
            original_filename="photo.jpg", category="Event Photos")
        path, mime = self.files.preview(self.manager, result["attachment_id"])
        self.assertEqual(mime, "image/jpeg")
        self.assertTrue(Path(path).is_file())

    def test_delete_manager_ok_receptionist_denied_and_hidden(self):
        src = self._write("id.png", PNG_BYTES)
        result = self.files.upload(
            self.receptionist, entity_type="customer",
            entity_id=self.customer.id, src_path=src,
            original_filename="id.png", category="Identity Documents")
        att_id = result["attachment_id"]
        with self.assertRaises(AuthorizationError):
            self.files.delete(self.receptionist, att_id)
        self.files.delete(self.manager, att_id)
        self.assertEqual(self.files.list_attachments(
            self.manager, entity_type="customer",
            entity_id=self.customer.id), [])
        with self.assertRaises(NotFoundError):
            self.files.download(self.manager, att_id)
        audit = audit_service.search(
            self.db, self.owner, action="attachment.delete")
        self.assertTrue(any(r["entity_id"] == att_id
                            for r in audit["rows"]))

    def test_expense_attachment_links_to_expense(self):
        expense = self.expenses.record_expense(
            self.accountant, amount_piasters=500_000, date="2026-11-26",
            category="Supplies", description="Test supplies")
        src = self._write("invoice.pdf", PDF_BYTES)
        result = self.files.upload(
            self.accountant, entity_type="expense", entity_id=expense.id,
            src_path=src, original_filename="invoice.pdf",
            category="Receipts")
        rows = self.files.list_attachments(
            self.accountant, entity_type="expense", entity_id=expense.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], result["attachment_id"])

    # -- search (FR-064) -------------------------------------------------------
    def test_search_customers_by_phone_and_masks_national_id(self):
        result = self.search.search_customers(
            self.receptionist, term="0101234")
        self.assertEqual(result["total"], 1)
        row = result["rows"][0]
        self.assertTrue(row["full_name"].startswith("Ahmed Hassan"))
        self.assertNotIn("29001010123456", row["national_id"])
        self.assertTrue(row["national_id"].endswith("3456"))

    def test_search_bookings_filters_and_pagination(self):
        booking = self._book()
        result = self.search.search_bookings(
            self.receptionist, status="pending")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["rows"][0]["number"], booking.number)
        result = self.search.search_bookings(
            self.receptionist, status="confirmed")
        self.assertEqual(result["total"], 0)
        result = self.search.search_bookings(
            self.receptionist, date_from="2026-12-04", date_to="2026-12-04")
        self.assertEqual(result["total"], 1)
        result = self.search.search_bookings(
            self.receptionist, date_from="2026-12-05", date_to="2026-12-06")
        self.assertEqual(result["total"], 0)
        with self.assertRaises(ValidationError):
            self.search.search_bookings(self.receptionist, status="bogus")

    def test_search_bookings_hides_amount_without_price_permission(self):
        self._book()
        viewer = self._limited_user()
        result = self.search.search_bookings(viewer, term="BK-")
        self.assertEqual(result["total"], 1)
        self.assertIsNone(result["rows"][0]["total_amount"])
        self.assertEqual(result["rows"][0]["total_amount_display"], "—")
        result = self.search.search_bookings(self.receptionist, term="BK-")
        self.assertEqual(result["rows"][0]["total_amount"], 4_400_000)

    def test_search_customers_pagination(self):
        page1 = self.search.search_customers(
            self.receptionist, term="", page=1, page_size=2)
        page2 = self.search.search_customers(
            self.receptionist, term="", page=2, page_size=2)
        self.assertEqual(page1["total"], 3)
        self.assertEqual(len(page1["rows"]), 2)
        self.assertEqual(len(page2["rows"]), 1)
        self.assertNotEqual(page1["rows"][0]["id"], page2["rows"][0]["id"])

    def test_search_payments_by_method(self):
        booking = self._book()
        self._confirm(booking)
        self.bookings.record_payment(
            self.accountant, booking.id, 500_000, "bank_transfer")
        result = self.search.search_payments(
            self.accountant, method="bank_transfer")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["rows"][0]["method"], "bank_transfer")
        with self.assertRaises(AuthorizationError):
            self.search.search_payments(self.staff, term="PAY-")

    def test_search_contracts_by_signature_status(self):
        booking = self._confirmed_booking()
        contract = self.contracts.generate_contract(
            self.manager, booking.id, "Standard Wedding Contract")
        result = self.search.search_contracts(
            self.manager, signature_status="draft")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["rows"][0]["number"], contract.number)
        result = self.search.search_contracts(
            self.manager, signature_status="signed")
        self.assertEqual(result["total"], 0)

    def test_search_staff_needs_permission(self):
        result = self.search.search_staff(self.manager, term="Staff")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["rows"][0]["username"], "staff")
        with self.assertRaises(AuthorizationError):
            self.search.search_staff(self.receptionist, term="Staff")

    def test_search_files_rbac_filtered(self):
        expense = self.expenses.record_expense(
            self.accountant, amount_piasters=100_000, date="2026-11-26",
            category="Supplies", description="Receipt test")
        src = self._write("invoice.pdf", PDF_BYTES)
        self.files.upload(
            self.accountant, entity_type="expense", entity_id=expense.id,
            src_path=src, original_filename="invoice.pdf",
            category="Receipts")
        result = self.search.search_files(
            self.accountant, category="Receipts")
        self.assertEqual(result["total"], 1)
        # Receptionist cannot view expense files at all.
        result = self.search.search_files(
            self.receptionist, entity_type="expense")
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["rows"], [])

    def test_global_search_staff_only_own_tasks(self):
        _, assigned = self._plan_with_assigned_tasks()
        self.assertTrue(assigned)
        # Assigned tasks are "Photographer arrival" (17:00) and
        # "Welcome guests" (18:00); the 15:00 "hall setup" task is not
        # assigned to staff, so it must never appear for them.
        result = self.search.global_search(self.staff, "Welcome guests")
        self.assertIn("tasks", result)
        self.assertEqual(result["tasks"]["total"], 1)
        self.assertNotIn("customers", result)
        self.assertNotIn("bookings", result)
        self.assertNotIn("payments", result)
        self.assertNotIn("staff", result)
        result = self.search.global_search(self.staff, "hall setup")
        self.assertEqual(result["tasks"]["total"], 0)

    def test_global_search_receptionist_sections(self):
        self._book()
        result = self.search.global_search(self.receptionist, "Ahmed Hassan")
        self.assertIn("customers", result)
        self.assertIn("bookings", result)
        self.assertNotIn("staff", result)  # no task.view / user.manage
        # Receptionist holds task.view_assigned, so a (here empty)
        # own-tasks section is legitimate.
        self.assertIn("tasks", result)
        self.assertEqual(result["tasks"]["total"], 0)
        with self.assertRaises(ValidationError):
            self.search.global_search(self.receptionist, "   ")

    def test_global_search_manager_sees_payments(self):
        booking = self._book()
        self._confirm(booking)
        result = self.search.global_search(self.manager, "PAY-")
        self.assertIn("payments", result)
        self.assertEqual(result["payments"]["total"], 1)

    # -- dashboards (FR-065) ---------------------------------------------------
    def test_owner_dashboard_widgets_match_seed(self):
        data = self.reports.dashboard(self.owner)
        self.assertIn("revenue", data["widgets"])
        self.assertIn("profit", data["widgets"])
        self.assertIn("bookings", data["widgets"])
        self.assertEqual(data["widgets"]["bookings"]["total"], 0)
        self.assertEqual(data["widgets"]["customers"] if "customers" in data["widgets"] else None,
                         None)  # Owner config has no customers widget
        self.assertEqual(data["widgets"]["revenue"]["recognised_revenue"], 0)
        self.assertEqual(data["widgets"]["profit"]["profit"], 0)

    def test_staff_dashboard_only_own_tasks(self):
        self._plan_with_assigned_tasks()
        data = self.reports.dashboard(self.staff)
        self.assertEqual(set(data["widgets"]),
                         {"my_tasks", "upcoming_tasks", "overdue_tasks"})
        self.assertEqual(data["widgets"]["my_tasks"]["count"], 2)
        self.assertNotIn("revenue", data["widgets"])
        self.assertNotIn("bookings", data["widgets"])

    def test_receptionist_dashboard_holds_expiring(self):
        self._book()
        data = self.reports.dashboard(self.receptionist)
        self.assertIn("holds_expiring", data["widgets"])
        self.assertEqual(data["widgets"]["holds_expiring"]["count"], 0)
        self.assertIn("new_bookings", data["widgets"])
        self.assertEqual(data["widgets"]["new_bookings"]["count"], 1)

    def test_pending_bookings_dashboard(self):
        booking = self._book()
        data = self.reports.pending_bookings(self.owner)
        self.assertEqual(data["count"], 1)
        row = data["rows"][0]
        self.assertEqual(row["number"], booking.number)
        self.assertEqual(row["missing_deposit"], 1_320_000)
        self.assertIn("left", row["hold_remaining"])
        self.assertFalse(row["expiring_soon"])
        self.assertIn("deposit outstanding", row["warnings"])
        self._confirm(booking)
        self.assertEqual(self.reports.pending_bookings(self.owner)["count"], 0)

    def test_dashboard_widget_config_owner_controls(self):
        config = self.reports.get_dashboard_config(self.owner)
        config["Owner"] = [w for w in config["Owner"] if w != "revenue"]
        self.reports.update_dashboard_widgets(self.owner, config)
        data = self.reports.dashboard(self.owner)
        self.assertNotIn("revenue", data["widgets"])
        self.assertIn("profit", data["widgets"])
        with self.assertRaises(ValidationError):
            self.reports.update_dashboard_widgets(
                self.owner, {"Owner": ["not_a_widget"]})
        with self.assertRaises(ValidationError):
            self.reports.update_dashboard_widgets(
                self.owner, {"Nobody": ["revenue"]})
        with self.assertRaises(AuthorizationError):
            self.reports.update_dashboard_widgets(
                self.manager, {"Owner": ["revenue"]})

    def test_conflicts_widget_counts_close_tasks(self):
        _, assigned = self._plan_with_assigned_tasks()
        self.assertEqual(len(assigned), 2)
        data = self.reports.dashboard(self.manager)
        self.assertEqual(data["widgets"]["conflicts"]["count"], 0)
        # Move one task to 15 minutes after the other: a conflict.
        first = min(assigned, key=lambda r: r["due_at"])
        self.tasks.update_task_details(
            self.manager, int(first["id"]), due_at="2026-12-04T17:45:00")
        data = self.reports.dashboard(self.manager)
        self.assertEqual(data["widgets"]["conflicts"]["count"], 1)
        conflict = data["widgets"]["conflicts"]["conflicts"][0]
        self.assertEqual(conflict["employee_id"], self.staff.id)

    # -- reports + CSV (FR-066) ------------------------------------------------
    def test_booking_report_totals_match_fixture(self):
        booking = self._book()
        report = self.reports.booking_report(
            self.manager, date_from="2026-12-01", date_to="2026-12-31")
        self.assertEqual(report["totals"]["bookings"], 1)
        self.assertEqual(report["totals"]["contract_value"], 4_400_000)
        self.assertEqual(report["totals"]["collected"], 0)
        self.assertEqual(report["totals"]["outstanding"], 4_400_000)
        self.assertEqual(report["rows"][0][0], booking.number)
        self._confirm(booking)
        report = self.reports.booking_report(
            self.manager, date_from="2026-12-01", date_to="2026-12-31")
        self.assertEqual(report["totals"]["collected"], 1_320_000)
        self.assertEqual(report["totals"]["outstanding"], 3_080_000)
        # Period comparison: the previous month has no bookings.
        self.assertEqual(report["comparison"]["bookings"], 0)
        self.assertEqual(report["comparison"]["contract_value"], 0)
        with self.assertRaises(AuthorizationError):
            self.reports.booking_report(self.staff)

    def test_financial_report_matches_reconciliation(self):
        booking = self._book()
        self._confirm(booking)
        # The window must cover the payment confirmation date (today)
        # as well as the event date (2026-12-04): the summary filters
        # collected by confirmation date and contract value by event date.
        summary = self.payments.financial_summary(
            self.accountant, date_from="2026-11-01", date_to="2026-12-31")
        report = self.reports.financial_report(
            self.accountant, date_from="2026-11-01", date_to="2026-12-31")
        self.assertEqual(report["totals"]["contract_value"],
                         summary["contract_value"])
        self.assertEqual(report["totals"]["collected"], summary["collected"])
        self.assertEqual(report["totals"]["profit"], summary["profit"])
        self.assertEqual(len(report["rows"]), 1)
        self.assertEqual(report["rows"][0][0], booking.number)
        with self.assertRaises(AuthorizationError):
            self.reports.financial_report(self.receptionist)

    def test_csv_export_content_matches_report(self):
        booking = self._book()
        self._confirm(booking)
        csv_text = self.reports.export_csv(
            self.manager, "bookings",
            date_from="2026-12-01", date_to="2026-12-31")
        rows = list(csv.reader(io.StringIO(csv_text)))
        self.assertEqual(rows[0][:3], ["Booking", "Customer", "Hall"])
        self.assertEqual(len(rows), 2)  # header + 1 booking
        self.assertEqual(rows[1][0], booking.number)
        self.assertEqual(rows[1][5], "44000.00")  # total in EGP
        self.assertEqual(rows[1][6], "13200.00")  # collected in EGP
        with self.assertRaises(ValidationError):
            self.reports.export_csv(self.manager, "no_such_report")
        with self.assertRaises(AuthorizationError):
            self.reports.export_csv(self.receptionist, "financial")

    def test_hall_utilization_numbers(self):
        # Utilization counts active (confirmed/in_progress/completed)
        # bookings only — a Pending hold does not book the hall yet.
        self._confirmed_booking()
        report = self.reports.hall_utilization(
            self.manager, date_from="2026-11-30", date_to="2026-12-06")
        by_hall = {r[0]: r for r in report["rows"]}
        main = by_hall["Main Hall (example)"]
        self.assertEqual(main[1], 1)            # events
        self.assertEqual(main[2], 5.0)          # booked hours (18:00-23:00)
        self.assertEqual(main[3], 77.0)         # 7 days x 11h opening
        self.assertAlmostEqual(main[4], 6.5)    # utilization %
        garden = by_hall["Garden Hall (example)"]
        self.assertEqual(garden[1], 0)
        self.assertEqual(garden[4], 0.0)

    def test_customer_and_package_reports(self):
        self._book()
        report = self.reports.customer_report(
            self.manager, date_from="2026-12-01", date_to="2026-12-31")
        self.assertEqual(len(report["rows"]), 1)
        self.assertTrue(report["rows"][0][0].startswith("Ahmed Hassan"))
        self.assertEqual(report["rows"][0][3], "44000.00")
        report = self.reports.package_report(
            self.manager, date_from="2026-12-01", date_to="2026-12-31")
        self.assertEqual(report["rows"][0][0], "Classic Wedding (example)")
        self.assertEqual(report["rows"][0][2], "44000.00")

    def test_cancellation_report_fee(self):
        booking = self._book()
        self._confirm(booking)
        self.bookings.cancel_booking(
            self.manager, booking.id, reason="test cancel")
        report = self.reports.cancellation_report(
            self.manager, date_from="2026-12-01", date_to="2026-12-31")
        self.assertEqual(len(report["rows"]), 1)
        # 8 days out: 50% tier (2,200,000) capped at paid (1,320,000).
        self.assertEqual(report["rows"][0][5], "13200.00")
        self.assertEqual(report["rows"][0][6], "0.00")

    def test_staff_and_event_reports(self):
        self._plan_with_assigned_tasks()
        report = self.reports.staff_report(
            self.manager, date_from="2026-12-01", date_to="2026-12-31")
        self.assertEqual(len(report["rows"]), 1)
        self.assertEqual(report["rows"][0][1], 2)  # assigned
        self.assertEqual(report["rows"][0][2], 0)  # completed
        report = self.reports.event_report(
            self.manager, date_from="2026-12-01", date_to="2026-12-31")
        self.assertEqual(len(report["rows"]), 1)
        self.assertEqual(report["rows"][0][3], "Wedding")

    def test_report_date_validation(self):
        with self.assertRaises(ValidationError):
            self.reports.booking_report(
                self.manager, date_from="2026-12-31", date_to="2026-12-01")
        with self.assertRaises(ValidationError):
            self.reports.booking_report(self.manager, date_from="2026-13-01")

    # -- audit search (FR-062) ---------------------------------------------------
    def test_audit_search_by_action_employee_and_date(self):
        booking = self._book()
        self._confirm(booking)
        result = audit_service.search(
            self.db, self.owner, action="payment.recorded")
        self.assertGreaterEqual(result["total"], 1)
        self.assertTrue(all(r["action"] == "payment.recorded"
                            for r in result["rows"]))
        result = audit_service.search(
            self.db, self.owner, employee_id=self.accountant.id)
        self.assertTrue(all(r["employee_id"] == self.accountant.id
                            for r in result["rows"]))
        result = audit_service.search(
            self.db, self.owner, entity_type="booking",
            entity_id=booking.id)
        self.assertGreaterEqual(result["total"], 1)
        # Date filter: use today's date (audit rows are stamped with
        # the real clock) — a hard-coded date goes stale overnight.
        today = datetime.now().strftime("%Y-%m-%d")
        result = audit_service.search(
            self.db, self.owner, date_from=today,
            date_to=today)
        self.assertGreaterEqual(result["total"], 1)
        with self.assertRaises(AuthorizationError):
            audit_service.search(self.db, self.receptionist)

    # -- FR-060 leftover triggers ------------------------------------------------
    def test_task_due_soon_and_overdue_notifications(self):
        # The sweep de-duplicates on notifications.created_at (real
        # clock), so the fixture due dates must be relative to the real
        # now — a simulated future "now" would put the window after the
        # rows the sweep itself just wrote (A-37/A-50 skew).
        _, assigned = self._plan_with_assigned_tasks()
        self.assertEqual(len(assigned), 2)
        now = datetime.now()
        soon_id = int(assigned[0]["id"])
        late_id = int(assigned[1]["id"])
        self.tasks.update_task_details(
            self.manager, soon_id,
            due_at=(now + timedelta(hours=12)).isoformat(timespec="seconds"))
        self.tasks.update_task_details(
            self.manager, late_id,
            due_at=(now - timedelta(hours=1)).isoformat(timespec="seconds"))
        counts = self.tasks.sweep_task_notifications(now=now)
        self.assertEqual(counts["task_due_soon"], 1)
        self.assertEqual(counts["task_overdue"], 1)
        inbox = self.notifications.inbox(self.staff)
        due_soon = [n for n in inbox if n["type"] == "task_due_soon"]
        overdue = [n for n in inbox if n["type"] == "task_overdue"]
        self.assertEqual(len(due_soon), 1)
        self.assertEqual(len(overdue), 1)
        self.assertEqual(overdue[0]["priority"], "high")
        # Second sweep with the same real now: de-duplicated, nothing new.
        counts2 = self.tasks.sweep_task_notifications(now=now)
        self.assertEqual(counts2["task_due_soon"], 0)
        self.assertEqual(counts2["task_overdue"], 0)

    def test_hall_maintenance_notification_to_manager(self):
        self.halls.add_maintenance(
            self.manager, 1, type_="cleaning", description="Deep clean",
            start_at=datetime(2026, 12, 10, 9, 0),
            end_at=datetime(2026, 12, 10, 12, 0))
        inbox = self.notifications.inbox(self.manager)
        maintenance = [n for n in inbox if n["type"] == "hall_maintenance"]
        self.assertEqual(len(maintenance), 1)
        self.assertIn("Main Hall", maintenance[0]["title"])

    # -- misc ---------------------------------------------------------------------
    def test_to_csv_quoting(self):
        text = to_csv(["Name", "Note"], [["A, B", 'say "hi"']])
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows[1], ["A, B", 'say "hi"'])
