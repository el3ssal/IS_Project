"""Test fixtures (Phase 1).

Every test uses a TEMPORARY database — tests never touch real data
(Brief Rule 6). The project root is added to sys.path so tests import
the package as ``wedding_hall_system.*``.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wedding_hall_system.config.settings import AppSettings  # noqa: E402
from wedding_hall_system.data.database import DatabaseManager  # noqa: E402
from wedding_hall_system.data.seed import seed  # noqa: E402
from wedding_hall_system.logic.auth_service import AuthService  # noqa: E402


class Phase1TestCase(unittest.TestCase):
    """Base case: fresh temp DB, schema applied, seed data loaded."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.db"
        # Lower PBKDF2 iterations in tests only, so the suite runs in
        # seconds instead of a minute. Production keeps 600,000.
        self.settings = AppSettings(
            db_path=self.db_path, password_hash_iterations=60_000,
        )
        self.db = DatabaseManager(self.db_path)
        self.db.initialize()
        seed(self.db, iterations=60_000)
        self.auth = AuthService(self.db, self.settings)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def login_as(self, username: str, password: str):
        return self.auth.login(username, password)
