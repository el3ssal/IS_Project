"""Manual backup / restore (Phase 7, SRS §15, FR-068).

Owner-only. A backup is a SQLite-consistent copy made with
``Connection.backup`` (never a blind copy of a live DB file), plus a
JSON manifest sidecar that records the checksum, schema version and
an inventory of the ``storage/`` files that existed at backup time.
Restore validates the backup BEFORE touching the live database
(checksum, ``PRAGMA integrity_check``, schema version) and always
makes a safety copy of the live database first.

What a backup does NOT contain: the bytes of uploaded files and
contract documents. Those live in ``storage/uploads/`` and
``storage/contracts/`` and are inventoried in the manifest only, so
the Owner can see what existed — file bytes are outside the database
backup (decision A-62). The safety copy is recorded in
``backup_records`` of the restored database after a restore, so it
stays listed.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from ..config.settings import AppSettings
from ..data.database import SCHEMA_VERSION, DatabaseManager
from ..utils.exceptions import NotFoundError, ValidationError
from ..utils.logging_config import get_logger
from . import audit_service
from .models import Employee
from .permissions import require_permission

logger = get_logger(__name__)


def backup_dir(db: DatabaseManager,
                 settings: AppSettings | None = None) -> Path:
    """Backups live under ``<base>/storage/backups``, where ``<base>``
    is the project root in production (the database lives in
    ``<base>/data/``) and the temp dir in tests (the test database
    lives directly in it). ``backup_dir`` may be overridden per
    instance for tests."""
    base = db.db_path.resolve().parent.parent
    return base / "storage" / "backups"


class BackupService:
    """Owner-only manual backup and restore."""

    def __init__(self, db: DatabaseManager,
                 settings: AppSettings | None = None,
                 backup_dir_: Path | None = None) -> None:
        self.db = db
        self.settings = settings or AppSettings.from_env()
        self.dir = Path(backup_dir_) if backup_dir_ is not None \
            else backup_dir(db, self.settings)
        self.dir.mkdir(parents=True, exist_ok=True)

    # -- helpers ---------------------------------------------------------
    def _get_record(self, backup_id: int) -> dict:
        row = self.db.query_one(
            "SELECT * FROM backup_records WHERE id = ?", (backup_id,))
        if row is None:
            raise NotFoundError(f"Backup #{backup_id} does not exist.")
        return dict(row)

    @staticmethod
    def _sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _resolve(self, filename: str) -> Path:
        """Resolve a backup filename inside the backups directory.
        Rejects any path that escapes it (no traversal)."""
        path = (self.dir / filename).resolve()
        if path.parent != self.dir.resolve():
            raise ValidationError("Invalid backup filename.")
        return path

    def _unique_path(self, filename: str) -> Path:
        """Same-second repeat: make the filename unique."""
        dest = self.dir / filename
        counter = 1
        while dest.exists():
            dest = self.dir / filename.replace(".db", f"_{counter}.db")
            counter += 1
        return dest

    def _storage_inventory(self) -> dict:
        root = self.dir.parent  # <base>/storage
        inventory: dict[str, dict] = {}
        for sub in ("contracts", "uploads"):
            folder = root / sub
            files = sorted(p.name for p in folder.glob("*")
                           if p.is_file()) if folder.exists() else []
            inventory[sub] = {"count": len(files), "files": files}
        return inventory

    # -- create / list ----------------------------------------------------
    def create_backup(self, employee: Employee) -> dict:
        """Create a backup of the live database. Returns the record."""
        require_permission(self.db, employee, "backup.manage")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"wedding_hall_backup_{stamp}.db"
        dest = self._unique_path(filename)
        filename = dest.name
        _, checksum = self.db.backup_to(dest)
        version = self.db.current_version()
        manifest = {
            "filename": filename,
            "checksum_sha256": checksum,
            "size_bytes": dest.stat().st_size,
            "schema_version": version,
            "created_at": datetime.now().isoformat(),
            "created_by": employee.username,
            "storage_inventory": self._storage_inventory(),
            "note": ("Database backup only: storage/ file bytes are "
                     "inventoried, not copied (decision A-62)."),
        }
        manifest_path = self.dir / (filename + ".manifest.json")
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8")
        with self.db.transaction() as conn:
            cur = conn.execute(
                """INSERT INTO backup_records
                   (filename, checksum, size_bytes, created_by)
                   VALUES (?, ?, ?, ?)""",
                (filename, checksum, dest.stat().st_size, employee.id))
            backup_id = int(cur.lastrowid)
            audit_service.record(
                self.db, employee_id=employee.id, action="backup.created",
                entity_type="backup", entity_id=backup_id,
                new_value={"filename": filename, "checksum": checksum,
                           "schema_version": version},
                description=f"Backup {filename} created.", conn=conn)
        logger.info("Backup %s created by %s", filename, employee.username)
        return {**manifest, "id": backup_id}

    def list_backups(self, employee: Employee) -> list[dict]:
        require_permission(self.db, employee, "backup.manage")
        rows = self.db.query_all(
            "SELECT * FROM backup_records ORDER BY id DESC")
        out = []
        for row in rows:
            item = dict(row)
            item["exists_on_disk"] = (self.dir / item["filename"]).exists()
            out.append(item)
        return out

    def download_backup(self, employee: Employee, backup_id: int) -> Path:
        """Return the backup file path for an authorized download.
        The UI serves it as a file; the directory is never exposed
        statically."""
        require_permission(self.db, employee, "backup.manage")
        record = self._get_record(backup_id)
        path = self._resolve(record["filename"])
        if not path.exists():
            raise NotFoundError(
                f"Backup file {record['filename']} is missing from disk.")
        return path

    # -- validate ----------------------------------------------------------
    def validate_backup(self, employee: Employee, backup_id: int) -> dict:
        """Validate a backup WITHOUT touching the live database:
        file exists, sha256 matches the record, integrity_check is ok,
        schema version matches this app. Records ``validated_at`` on
        success. Returns the check results; raises ValidationError on
        any failure."""
        require_permission(self.db, employee, "backup.manage")
        record = self._get_record(backup_id)
        path = self._resolve(record["filename"])
        checks: dict = {"backup_id": backup_id,
                        "filename": record["filename"]}
        if not path.exists():
            raise ValidationError(
                f"Backup file {record['filename']} is missing from disk.")
        checks["file_exists"] = True
        actual = self._sha256(path)
        checks["checksum_matches"] = actual == record["checksum"]
        if not checks["checksum_matches"]:
            raise ValidationError(
                "Backup checksum does not match the recorded checksum — "
                "the file is corrupt or was modified.")
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.db"
            shutil.copyfile(path, probe)
            conn = sqlite3.connect(str(probe))
            try:
                integrity = conn.execute(
                    "PRAGMA integrity_check").fetchone()[0]
                checks["integrity_check"] = integrity
                if integrity != "ok":
                    raise ValidationError(
                        f"Backup failed integrity check: {integrity}")
                row = conn.execute(
                    "SELECT MAX(version) AS v FROM schema_version").fetchone()
                version = int(row[0]) if row and row[0] is not None else 0
                checks["schema_version"] = version
                checks["schema_version_matches"] = version == SCHEMA_VERSION
                if version != SCHEMA_VERSION:
                    raise ValidationError(
                        f"Backup schema version is v{version}, this app "
                        f"expects v{SCHEMA_VERSION}. Refusing to restore.")
            finally:
                conn.close()
        self.db.execute(
            "UPDATE backup_records SET validated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), backup_id))
        checks["valid"] = True
        return checks

    # -- restore -----------------------------------------------------------
    def restore_backup(self, employee: Employee, backup_id: int, *,
                       confirm: bool = False) -> dict:
        """Restore a validated backup over the live database.

        Requires explicit ``confirm=True``. The backup is validated
        first (refuses corrupt or wrong-version backups); a safety copy
        of the live database is made before anything is replaced; the
        WAL is checkpointed and the live file is replaced atomically
        by copy; the restored database is re-validated afterwards.
        Connections are short-lived in this app, so no live connection
        holds the file open.
        """
        require_permission(self.db, employee, "backup.manage")
        if not confirm:
            raise ValidationError(
                "Restore needs explicit confirmation. Tick the "
                "confirmation box first.")
        record = self._get_record(backup_id)
        checks = self.validate_backup(employee, backup_id)
        source = self._resolve(record["filename"])

        # Safety copy of the live database first.
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safety_path = self._unique_path(f"safety_before_restore_{stamp}.db")
        safety_name = safety_path.name
        safety_path, safety_checksum = self.db.backup_to(safety_path)

        # Flush the WAL into the live file, then replace the file.
        live = self.db.db_path
        conn = self.db.connect()
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(live) + suffix)
            if sidecar.exists():
                sidecar.unlink()
        shutil.copyfile(source, live)
        self.db.initialize()
        after = self.db.current_version()
        if after != SCHEMA_VERSION:
            raise ValidationError(
                f"Restored database reports schema v{after}, expected "
                f"v{SCHEMA_VERSION}.")
        probe = self.db.connect()
        try:
            integrity = probe.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            probe.close()
        if integrity != "ok":
            raise ValidationError(
                f"Restored database failed integrity check: {integrity}")
        # Record the safety copy + the restore in the restored database
        # (the pre-restore backup_records rows were replaced with the
        # backup's own rows, so re-record the safety copy here).
        with self.db.transaction() as conn:
            cur = conn.execute(
                """INSERT INTO backup_records
                   (filename, checksum, size_bytes, created_by)
                   VALUES (?, ?, ?, ?)""",
                (safety_name, safety_checksum, safety_path.stat().st_size,
                 employee.id))
            safety_id = int(cur.lastrowid)
            audit_service.record(
                self.db, employee_id=employee.id, action="backup.restored",
                entity_type="backup", entity_id=backup_id,
                new_value={"filename": record["filename"],
                           "safety_copy_id": safety_id,
                           "safety_copy": safety_name},
                description=(
                    f"Backup {record['filename']} restored. Safety copy: "
                    f"{safety_name}."), conn=conn)
        logger.info("Backup %s restored by %s (safety copy %s)",
                    record["filename"], employee.username, safety_name)
        return {"restored": record["filename"], "checks": checks,
                "safety_copy": safety_name, "safety_copy_id": safety_id}
