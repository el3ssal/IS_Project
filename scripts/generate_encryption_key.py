"""Generate a field-level encryption key (Phase 8, FR-014).

Usage:
    python scripts/generate_encryption_key.py

Prints ONE key. Set it as the ``WEDDING_HALL_ENCRYPTION_KEY``
environment variable on the computer that runs the app, and store a
copy somewhere safe OFF that computer (the venue's password manager,
a sealed envelope — anywhere but the database and the repository).

- Windows (PowerShell, current session):
      $env:WEDDING_HALL_ENCRYPTION_KEY = "<key>"
  Windows (permanent for the user, new terminals):
      setx WEDDING_HALL_ENCRYPTION_KEY "<key>"
- Linux/macOS (current session):
      export WEDDING_HALL_ENCRYPTION_KEY="<key>"

If the key is lost, encrypted National IDs cannot be recovered —
that is the point of encryption. Never commit a key to git, never
paste it into the database, and never share it in chat.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wedding_hall_system.utils.crypto import ENV_VAR, generate_key


def main() -> None:
    key = generate_key()
    print(f"{ENV_VAR}={key}")
    print()
    print("Set this variable on the computer that runs the app, then")
    print("keep a backup copy somewhere safe OFF that computer.")
    print("If this key is lost, encrypted National IDs cannot be recovered.")


if __name__ == "__main__":
    main()
