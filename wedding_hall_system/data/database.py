"""SQLite connection management (Phase 1, data tier).

Owns: connecting with the right pragmas, applying schema/migrations,
transactions (including BEGIN IMMEDIATE for booking writes), and safe
backup. Contains no business rules and no SQL for a specific feature —
feature SQL lives in repositories.
"""
from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..utils.exceptions import DatabaseOperationError
from ..utils.logging_config import get_logger

logger = get_logger(__name__)

SCHEMA_VERSION = 4


class DatabaseManager:
    """One manager per database file. Connections are short-lived: a new
    connection is opened per operation (``check_same_thread=False`` is
    therefore unnecessary) and always carries the same pragmas."""

    def __init__(self, db_path: str | Path, *, busy_timeout_ms: int = 5000,
                 journal_mode: str = "WAL") -> None:
        self.db_path = Path(db_path)
        self.busy_timeout_ms = busy_timeout_ms
        self.journal_mode = journal_mode

    # -- connections -----------------------------------------------------
    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=self.busy_timeout_ms / 1000)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout_ms)}")
        # WAL is safe here: the DB lives on the central host's local disk and
        # the Gradio server is the only process writing to it (SRS §16.2,
        # GAP G-12). Never point this at a network share.
        conn.execute(f"PRAGMA journal_mode = {self.journal_mode}")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        """A write transaction. ``immediate=True`` takes the write lock up
        front (BEGIN IMMEDIATE) — required for booking conflict checks so
        two writers cannot both pass a stale availability check (SRS §3.2)."""
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield conn
            conn.commit()
        except sqlite3.OperationalError as exc:
            conn.rollback()
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                raise DatabaseOperationError(
                    "The database is busy. Please try again in a moment."
                ) from exc
            raise DatabaseOperationError(f"Database operation failed: {exc}") from exc
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # -- schema / migrations ----------------------------------------------
    @property
    def schema_path(self) -> Path:
        return Path(__file__).resolve().parent / "schema.sql"

    @property
    def migrations_dir(self) -> Path:
        return Path(__file__).resolve().parent / "migrations"

    def initialize(self) -> None:
        """Create the schema and apply pending migrations. Idempotent:
        running it on an existing database applies only migrations newer
        than the recorded version (or none at all).

        Migration files are ``migrations/NNN_name.sql``. ``001`` is the
        initial schema copy (already covered by schema.sql); files with a
        higher number run in order and record their version. A statement
        that fails only because its column already exists (fresh DBs get
        the columns from schema.sql itself) is skipped, so re-running a
        migration is safe; any other failure aborts the whole migration.
        """
        sql = self.schema_path.read_text(encoding="utf-8")
        with self.connection() as conn:
            conn.executescript(sql)
            current = self._recorded_version(conn)
            for path in sorted(self.migrations_dir.glob("[0-9]*_*.sql")):
                try:
                    number = int(path.name.split("_", 1)[0])
                except ValueError:
                    continue
                if number <= current or number <= 1:
                    continue
                self._apply_migration(conn, path)
                conn.execute(
                    "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
                    (number,),
                )
                current = number
                logger.info("Applied migration %s", path.name)
            conn.execute(
                "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
        logger.info("Database initialised at %s (schema v%s)", self.db_path, SCHEMA_VERSION)

    @staticmethod
    def _recorded_version(conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        return int(row["v"]) if row and row["v"] is not None else 0

    @staticmethod
    def _apply_migration(conn: sqlite3.Connection, path: Path) -> None:
        """Run one migration file statement by statement. Statements are
        separated by ';' (migration SQL must not contain ';' inside string
        literals). A 'duplicate column name' failure is skipped — it only
        means a fresh database already has the column from schema.sql."""
        sql = path.read_text(encoding="utf-8")
        for statement in sql.split(";"):
            statement = statement.strip()
            if not statement or statement.startswith("--"):
                # Skip blanks and whole-line comments (a comment may sit
                # above a statement in the same chunk after the split).
                lines = [ln for ln in statement.splitlines()
                         if not ln.strip().startswith("--")]
                statement = "\n".join(lines).strip()
                if not statement:
                    continue
            try:
                conn.execute(statement)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" in str(exc).lower():
                    continue
                raise DatabaseOperationError(
                    f"Migration {path.name} failed: {exc}"
                ) from exc

    def current_version(self) -> int:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT MAX(version) AS v FROM schema_version"
            ).fetchone()
        return int(row["v"]) if row and row["v"] is not None else 0

    def table_names(self) -> list[str]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        return [r["name"] for r in rows]

    # -- helpers -----------------------------------------------------------
    def execute(self, sql: str, params: tuple = ()) -> int:
        """Run one write statement; return the last row id. Parameterised only."""
        with self.connection() as conn:
            cur = conn.execute(sql, params)
            return int(cur.lastrowid)

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(sql, params).fetchone()

    def query_all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return conn.execute(sql, params).fetchall()

    # -- backup ------------------------------------------------------------
    def backup_to(self, dest_path: str | Path) -> tuple[Path, str]:
        """SQLite-consistent backup via Connection.backup (never a blind
        file copy of a live DB). Returns (path, sha256 hex)."""
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        source = self.connect()
        try:
            target = sqlite3.connect(str(dest))
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()
        digest = hashlib.sha256(dest.read_bytes()).hexdigest()
        logger.info("Database backed up to %s (sha256 %s)", dest, digest[:12])
        return dest, digest
