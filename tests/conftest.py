"""Test fixtures (Phase 1).

Every test uses a TEMPORARY database — tests never touch real data
(Brief Rule 6). The project root is added to sys.path so tests import
the package as ``wedding_hall_system.*``.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

# Gradio import workaround (exec-shell quirk): the shell's NO_PROXY can
# contain bracketed IPv6 entries ('[::1]') that httpx cannot parse, so
# any test that imports Gradio fails at import time. Repair only the
# broken value, before any other import.
for _var in ("NO_PROXY", "no_proxy"):
    if "[" in os.environ.get(_var, ""):
        os.environ[_var] = "localhost,127.0.0.1"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Phase 8 (FR-014): field-level encryption needs a key before ANY
# database is initialised (migration 005 encrypts stored National
# IDs). Tests use a FIXED test-only key — never a production key,
# never committed as a real secret; it protects no real data.
os.environ.setdefault(
    "WEDDING_HALL_ENCRYPTION_KEY",
    "CVkSWgd56VHohFFpNwfpwu7gqD-zqYbM887v9XMtyO8=",
)

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
