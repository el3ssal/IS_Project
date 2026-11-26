"""Centralised configuration (Phase 1).

All tunable values live here or in the ``system_settings`` table — never
hard-coded in services or UI code. Environment variables override the
defaults so the same code runs on a developer machine and on the venue's
central host (see README: LAN deployment).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _project_root() -> Path:
    # wedding_hall_system/config/settings.py -> project root
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class AppSettings:
    """Application settings. Frozen: change settings via the DB/settings UI."""

    project_root: Path = field(default_factory=_project_root)
    db_path: Path = field(default_factory=lambda: _project_root() / "data" / "wedding_hall.db")
    # Venue / business defaults (Owner can change these in settings; they are
    # also seeded into system_settings — the DB value wins at runtime).
    venue_name: str = "Benha Wedding Hall"
    currency: str = "EGP"
    timezone: str = "Africa/Cairo"
    default_language: str = "ar"
    # Auth / security
    password_hash_iterations: int = 600_000
    session_expiry_hours: int = 8
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    # Booking policy defaults (seeded into system_settings; Phase 2/3 use them)
    hold_hours: int = 48
    grace_hours: int = 24
    buffer_minutes: int = 60
    deposit_percent: int = 30
    # Gradio server (LAN deployment: set GRADIO_SERVER_NAME to the host LAN IP)
    gradio_server_name: str = "127.0.0.1"
    gradio_server_port: int = 7860

    @classmethod
    def from_env(cls) -> "AppSettings":
        root = _project_root()
        return cls(
            project_root=root,
            db_path=Path(os.environ.get("WEDDING_HALL_DB", str(root / "data" / "wedding_hall.db"))),
            venue_name=os.environ.get("WEDDING_HALL_VENUE", "Benha Wedding Hall"),
            currency=os.environ.get("WEDDING_HALL_CURRENCY", "EGP"),
            timezone=os.environ.get("WEDDING_HALL_TZ", "Africa/Cairo"),
            default_language=os.environ.get("WEDDING_HALL_LANG", "ar"),
            gradio_server_name=os.environ.get("GRADIO_SERVER_NAME", "127.0.0.1"),
            gradio_server_port=int(os.environ.get("GRADIO_SERVER_PORT", "7860")),
        )


# Money: integer minor units (piasters) everywhere in storage and services.
# 1 EGP = 100 piasters. Floats are never used for money (ARCHITECTURE_REVIEW
# §2 rule 5). UI/reports format piasters as EGP.
PIASTERS_PER_EGP = 100


def format_money(piasters: int, currency: str = "EGP") -> str:
    """Format an integer-piasters amount, e.g. 150050 -> '1,500.50 EGP'."""
    sign = "-" if piasters < 0 else ""
    value = abs(piasters)
    return f"{sign}{value // 100:,}.{value % 100:02d} {currency}"
