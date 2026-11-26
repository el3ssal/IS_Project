"""Phase 7 tests: manual backup / restore (SRS §15, FR-068, T-22).

Every test uses a TEMPORARY database — tests never touch real data.
Backups land in the temp dir's storage/backups (see
backup_service.backup_dir), never in the real project tree.
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
from pathlib import Path

from tests.conftest import Phase1TestCase
from wedding_hall_system.data.database import SCHEMA_VERSION, DatabaseManager
from wedding_hall_system.logic.backup_service import BackupService
from wedding_hall_system.logic.settings_service import SettingsService
from wedding_hall_system.utils.exceptions import (
    AuthorizationError,
    NotFoundError,
    ValidationError,
)


class TestBackup(Phase1TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.login_as("owner", "Owner123!")[0]
        self.receptionist = self.login_as("receptionist", "Reception123!")[0]
        self.backups = BackupService(self.db, self.settings)
        self.settings_svc = SettingsService(self.db, self.settings)

    def _change_venue_name(self, name: str) -> None:
        self.settings_svc.update_setting(self.owner, "venue.name", name)

    def _venue_name(self) -> str:
        from wedding_hall_system.data.repositories import system_repository
        return system_repository.get_setting(self.db, "venue.name")

    # -- create ------------------------------------------------------------
    def test_create_backup_valid_file_and_checksum(self):
        result = self.backups.create_backup(self.owner)
        path = self.backups.dir / result["filename"]
        self.assertTrue(path.exists())
        self.assertGreater(path.stat().st_size, 0)
        self.assertEqual(
            hashlib.sha256(path.read_bytes()).hexdigest(),
            result["checksum_sha256"])
        record = self.db.query_one(
            "SELECT * FROM backup_records WHERE id = ?", (result["id"],))
        self.assertIsNotNone(record)
        self.assertEqual(record["checksum"], result["checksum_sha256"])
        self.assertEqual(result["schema_version"], SCHEMA_VERSION)
        manifest = self.backups.dir / (result["filename"] + ".manifest.json")
        self.assertTrue(manifest.exists())
        self.assertEqual(result["id"], record["id"])

    def test_list_backups_shows_created_backup(self):
        result = self.backups.create_backup(self.owner)
        rows = self.backups.list_backups(self.owner)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], result["id"])
        self.assertTrue(rows[0]["exists_on_disk"])

    # -- validate ------------------------------------------------------------
    def test_validate_backup_ok_and_marks_validated_at(self):
        result = self.backups.create_backup(self.owner)
        checks = self.backups.validate_backup(self.owner, result["id"])
        self.assertTrue(checks["valid"])
        self.assertTrue(checks["checksum_matches"])
        self.assertEqual(checks["integrity_check"], "ok")
        self.assertTrue(checks["schema_version_matches"])
        row = self.db.query_one(
            "SELECT validated_at FROM backup_records WHERE id = ?",
            (result["id"],))
        self.assertIsNotNone(row["validated_at"])

    def test_validate_corrupt_backup_refused(self):
        result = self.backups.create_backup(self.owner)
        path = self.backups.dir / result["filename"]
        with path.open("ab") as handle:
            handle.write(b"corruption")
        with self.assertRaises(ValidationError):
            self.backups.validate_backup(self.owner, result["id"])
        # The live database is untouched by the failed validation.
        self.assertEqual(self._venue_name(), "Benha Wedding Hall")

    def test_validate_wrong_schema_version_refused(self):
        # A backup file whose schema version is not this app's version.
        other = Path(self._tmp.name) / "other.db"
        # DELETE journal mode: the file alone must hold every row, so
        # the copied "backup" really carries schema version 99.
        other_db = DatabaseManager(other, journal_mode="DELETE")
        other_db.initialize()
        other_db.execute(
            "INSERT OR IGNORE INTO schema_version (version) VALUES (99)")
        foreign = self.backups.dir / "foreign_v99.db"
        shutil.copyfile(other, foreign)
        checksum = hashlib.sha256(foreign.read_bytes()).hexdigest()
        row_id = self.db.execute(
            """INSERT INTO backup_records (filename, checksum, size_bytes,
                                           created_by)
               VALUES (?, ?, ?, ?)""",
            ("foreign_v99.db", checksum, foreign.stat().st_size,
             self.owner.id))
        with self.assertRaises(ValidationError) as ctx:
            self.backups.validate_backup(self.owner, row_id)
        self.assertIn("v99", str(ctx.exception))

    def test_validate_missing_file_refused(self):
        result = self.backups.create_backup(self.owner)
        (self.backups.dir / result["filename"]).unlink()
        with self.assertRaises(ValidationError):
            self.backups.validate_backup(self.owner, result["id"])

    def test_validate_unknown_backup_id(self):
        with self.assertRaises(NotFoundError):
            self.backups.validate_backup(self.owner, 99999)

    # -- restore -------------------------------------------------------------
    def test_restore_reproduces_backup_state(self):
        self._change_venue_name("Before Backup")
        result = self.backups.create_backup(self.owner)
        self._change_venue_name("After Backup")
        self.assertEqual(self._venue_name(), "After Backup")
        outcome = self.backups.restore_backup(
            self.owner, result["id"], confirm=True)
        self.assertEqual(outcome["restored"], result["filename"])
        self.assertEqual(self._venue_name(), "Before Backup")

    def test_restore_needs_explicit_confirmation(self):
        result = self.backups.create_backup(self.owner)
        self._change_venue_name("Changed")
        with self.assertRaises(ValidationError):
            self.backups.restore_backup(self.owner, result["id"],
                                        confirm=False)
        self.assertEqual(self._venue_name(), "Changed")

    def test_restore_creates_safety_copy(self):
        result = self.backups.create_backup(self.owner)
        self._change_venue_name("Changed")
        outcome = self.backups.restore_backup(
            self.owner, result["id"], confirm=True)
        safety = self.backups.dir / outcome["safety_copy"]
        self.assertTrue(safety.exists())
        self.assertTrue(safety.name.startswith("safety_before_restore_"))
        rows = self.backups.list_backups(self.owner)
        names = [r["filename"] for r in rows]
        self.assertIn(outcome["safety_copy"], names)
        # The safety copy holds the pre-restore state.
        probe = DatabaseManager(Path(self._tmp.name) / "probe.db")
        shutil.copyfile(safety, probe.db_path)
        probe_conn = sqlite3.connect(str(probe.db_path))
        try:
            value = probe_conn.execute(
                "SELECT value FROM system_settings WHERE key = 'venue.name'"
            ).fetchone()[0]
        finally:
            probe_conn.close()
        self.assertEqual(value, "Changed")

    def test_restore_corrupt_backup_refused_and_live_db_intact(self):
        result = self.backups.create_backup(self.owner)
        self._change_venue_name("Live Value")
        path = self.backups.dir / result["filename"]
        with path.open("ab") as handle:
            handle.write(b"corruption")
        with self.assertRaises(ValidationError):
            self.backups.restore_backup(self.owner, result["id"],
                                        confirm=True)
        self.assertEqual(self._venue_name(), "Live Value")

    def test_restore_is_audited(self):
        result = self.backups.create_backup(self.owner)
        self.backups.restore_backup(self.owner, result["id"], confirm=True)
        row = self.db.query_one(
            "SELECT * FROM audit_logs WHERE action = 'backup.restored' "
            "ORDER BY id DESC LIMIT 1")
        self.assertIsNotNone(row)
        self.assertEqual(row["employee_id"], self.owner.id)

    # -- permissions ---------------------------------------------------------
    def test_non_owner_cannot_backup_or_restore(self):
        result = self.backups.create_backup(self.owner)
        with self.assertRaises(AuthorizationError):
            self.backups.create_backup(self.receptionist)
        with self.assertRaises(AuthorizationError):
            self.backups.list_backups(self.receptionist)
        with self.assertRaises(AuthorizationError):
            self.backups.validate_backup(self.receptionist, result["id"])
        with self.assertRaises(AuthorizationError):
            self.backups.restore_backup(self.receptionist, result["id"],
                                        confirm=True)
        with self.assertRaises(AuthorizationError):
            self.backups.download_backup(self.receptionist, result["id"])

    def test_download_returns_path_inside_backup_dir(self):
        result = self.backups.create_backup(self.owner)
        path = self.backups.download_backup(self.owner, result["id"])
        self.assertEqual(path.resolve().parent,
                         self.backups.dir.resolve())

    def test_download_rejects_path_traversal_row(self):
        row_id = self.db.execute(
            """INSERT INTO backup_records (filename, checksum, size_bytes,
                                           created_by)
               VALUES (?, ?, ?, ?)""",
            ("../evil.db", "x" * 64, 0, self.owner.id))
        with self.assertRaises(ValidationError):
            self.backups.download_backup(self.owner, row_id)
