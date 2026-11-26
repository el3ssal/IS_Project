"""Database tests (Phase 1, AC-002; SRS §16.2/§18.10)."""
from __future__ import annotations

import sqlite3

from conftest import Phase1TestCase


class TestSchema(Phase1TestCase):
    def test_core_tables_exist(self):
        tables = set(self.db.table_names())
        for expected in (
            "employees", "roles", "permissions", "role_permissions",
            "employee_roles", "login_attempts", "sessions",
            "customers", "bookings", "payments", "halls", "packages",
            "quotations", "contracts", "tasks", "expenses", "refunds",
            "audit_logs", "system_settings", "schema_version",
            # Fresh Phase 0 corrections (ARCHITECTURE_REVIEW AR-06/AR-07):
            "financial_policy_snapshots", "discount_records",
            # Phase 8 (FR-011/FR-026): persistent tokens + service changes.
            "remember_me_tokens", "booking_service_changes",
        ):
            assert expected in tables, f"missing table: {expected}"

    def test_table_count(self):
        # 53 domain tables (ARCHITECTURE_REVIEW §3.2) + schema_version (§3.5)
        # + 2 Phase 8 tables (remember_me_tokens, booking_service_changes).
        assert len(self.db.table_names()) == 56

    def test_no_contract_templates_table(self):
        # Templates are rows in system_settings (ARCHITECTURE_REVIEW §3.5).
        assert "contract_templates" not in set(self.db.table_names())

    def test_schema_version_is_5(self):
        assert self.db.current_version() == 5

    def test_initialize_is_idempotent(self):
        self.db.initialize()
        self.db.initialize()
        assert self.db.current_version() == 5

    def test_seed_is_idempotent(self):
        from wedding_hall_system.data.seed import seed

        before = self.db.query_one("SELECT COUNT(*) AS n FROM employees")["n"]
        seed(self.db)
        after = self.db.query_one("SELECT COUNT(*) AS n FROM employees")["n"]
        assert before == after == 5

    def test_foreign_keys_are_enforced(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO employee_roles (employee_id, role_id) VALUES (99999, 99999)"
            )

    def test_unique_username_enforced(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO employees (username, password_hash, password_salt, full_name)"
                " VALUES ('owner', 'x', 'y', 'Duplicate')"
            )

    def test_booking_end_after_start_check(self):
        # CHECK constraint from schema v1 (needs referenced rows first).
        self.db.execute(
            "INSERT INTO customers (full_name, phone) VALUES ('Test Customer', '+201000000000')"
        )
        self.db.execute(
            "INSERT INTO halls (name, capacity, base_price) VALUES ('Hall A', 200, 100000)"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                """INSERT INTO bookings (number, customer_id, hall_id, start_at, end_at,
                                        guest_count, total_amount)
                   VALUES ('B-1', 1, 1, '2026-12-01T20:00:00', '2026-12-01T18:00:00', 100, 0)"""
            )

    def test_transaction_rolls_back_on_error(self):
        try:
            with self.db.transaction() as conn:
                conn.execute(
                    "INSERT INTO roles (name) VALUES ('Temporary Role')"
                )
                raise RuntimeError("simulated failure")
        except RuntimeError:
            pass
        row = self.db.query_one("SELECT * FROM roles WHERE name = 'Temporary Role'")
        assert row is None

    def test_migration_002_upgrades_v1_database(self):
        """Simulate a v1 database (drop the v2 columns, reset the version
        row), then initialize() must apply migrations 002 + 003 + 004 + 005
        and report v5."""
        with self.db.transaction() as conn:
            conn.execute("ALTER TABLE contracts DROP COLUMN signature_date")
            conn.execute("ALTER TABLE employees DROP COLUMN specialties")
            conn.execute("DELETE FROM schema_version WHERE version IN (2, 3, 4, 5)")
            conn.execute("INSERT INTO schema_version (version) VALUES (1)")
        assert self.db.current_version() == 1
        self.db.initialize()
        assert self.db.current_version() == 5
        contract_cols = [r[1] for r in self.db.query_all(
            "PRAGMA table_info(contracts)")]
        employee_cols = [r[1] for r in self.db.query_all(
            "PRAGMA table_info(employees)")]
        assert "signature_date" in contract_cols
        assert "specialties" in employee_cols
        assert "language" in employee_cols
        # And re-running initialize() stays a no-op.
        self.db.initialize()
        assert self.db.current_version() == 5

    def test_migration_003_upgrades_v2_database(self):
        """Simulate a v2 database (drop the v3 columns, reset the version
        row), then initialize() must apply migrations 003 + 004 + 005 and
        report v5."""
        with self.db.transaction() as conn:
            conn.execute("ALTER TABLE reschedule_history DROP COLUMN due_at")
            conn.execute("ALTER TABLE no_show_records DROP COLUMN fee_amount")
            conn.execute(
                "ALTER TABLE no_show_records DROP COLUMN refundable_amount")
            conn.execute("DELETE FROM schema_version WHERE version IN (3, 4, 5)")
            # The version-2 row from the original initialise() is still
            # there, so the recorded version is now 2 again.
        assert self.db.current_version() == 2
        self.db.initialize()
        assert self.db.current_version() == 5
        reschedule_cols = [r[1] for r in self.db.query_all(
            "PRAGMA table_info(reschedule_history)")]
        no_show_cols = [r[1] for r in self.db.query_all(
            "PRAGMA table_info(no_show_records)")]
        assert "due_at" in reschedule_cols
        assert "fee_amount" in no_show_cols
        assert "refundable_amount" in no_show_cols
        # And re-running initialize() stays a no-op.
        self.db.initialize()
        assert self.db.current_version() == 5

    def test_migration_004_upgrades_v3_database(self):
        """Simulate a v3 database (drop the language column, reset the
        version row), then initialize() must apply migrations 004 + 005 and
        report v5 — existing accounts keep working with the default."""
        with self.db.transaction() as conn:
            conn.execute("ALTER TABLE employees DROP COLUMN language")
            conn.execute("DELETE FROM schema_version WHERE version IN (4, 5)")
        assert self.db.current_version() == 3
        self.db.initialize()
        assert self.db.current_version() == 5
        employee_cols = [r[1] for r in self.db.query_all(
            "PRAGMA table_info(employees)")]
        assert "language" in employee_cols
        row = self.db.query_one(
            "SELECT language FROM employees WHERE username = 'owner'")
        assert row["language"] == "ar"
        # And re-running initialize() stays a no-op.
        self.db.initialize()
        assert self.db.current_version() == 5

    def test_backup_round_trip(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            dest, digest = self.db.backup_to(Path(tmp) / "backup.db")
            assert dest.exists() and len(digest) == 64
            from wedding_hall_system.data.database import DatabaseManager

            restored = DatabaseManager(dest)
            assert restored.current_version() == 5
            assert restored.query_one(
                "SELECT COUNT(*) AS n FROM employees"
            )["n"] == 5


if __name__ == "__main__":
    import unittest

    unittest.main()
