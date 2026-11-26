"""Entry point: ``python -m wedding_hall_system`` or ``python main.py``.

The UI is Gradio (deck pattern: browser screens, gr.Info/gr.Error) —
see docs/ASSUMPTIONS_AND_DECISIONS.md.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Proxy env fix (Linux VM only): the VM's NO_PROXY contains bracketed
# IPv6 entries ('[::1]') that httpx inside Gradio cannot parse, and
# without NO_PROXY Gradio's own startup check goes through the egress
# proxy and fails. Set a plain localhost-only NO_PROXY before importing
# ui.app. Harmless on Windows, where these variables do not exist.
os.environ["NO_PROXY"] = "localhost,127.0.0.1"
os.environ["no_proxy"] = "localhost,127.0.0.1"

# Run Gradio fully offline: Gradio is a Hugging Face product and its
# bundled huggingface_hub otherwise contacts huggingface.co (version /
# telemetry traffic the project does not need). These must be set before
# importing Gradio. setdefault: a user can opt back into online mode.
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

# Allow running from the project root: add it to sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wedding_hall_system.config.settings import AppSettings  # noqa: E402
from wedding_hall_system.ui.app import build_app  # noqa: E402
from wedding_hall_system.ui.theme import launch_kwargs  # noqa: E402
from wedding_hall_system.utils.exceptions import ConfigurationError  # noqa: E402
from wedding_hall_system.utils.logging_config import setup_logging  # noqa: E402


def main(argv: list[str] | None = None) -> None:
    """Launch the app.

    ``python main.py`` serves on 127.0.0.1:7860 (loopback only).
    ``python main.py --lan`` binds 0.0.0.0 so employee devices on the
    venue LAN can reach it — with an explicit warning, because that
    exposes the app on the local network (SRS §16.4: restrict server
    access to the trusted LAN). ``--host`` / ``--port`` override the
    address; env vars GRADIO_SERVER_NAME / GRADIO_SERVER_PORT are the
    defaults (see README: LAN deployment).
    """
    parser = argparse.ArgumentParser(
        description="Wedding Hall Management System")
    parser.add_argument("--host", default=None,
                        help="interface to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None,
                        help="port to listen on (default: 7860)")
    parser.add_argument("--lan", action="store_true",
                        help="bind 0.0.0.0 for LAN access (see README)")
    args = parser.parse_args(argv)
    setup_logging()
    settings = AppSettings.from_env()
    host = args.host or ("0.0.0.0" if args.lan else settings.gradio_server_name)
    port = args.port or settings.gradio_server_port
    if host == "0.0.0.0":
        print("WARNING: LAN mode — the app is reachable from every device "
              "on this network. Restrict access to the trusted venue LAN "
              "(SRS §16.4).")
    app = None
    try:
        app = build_app(settings)
    except ConfigurationError as exc:
        # Phase 8 (FR-014): without WEDDING_HALL_ENCRYPTION_KEY the
        # database cannot be initialised or seeded (National IDs must
        # never be written in plaintext). Say so plainly instead of
        # dumping a traceback.
        print(f"Cannot start: {exc}")
        print("Set WEDDING_HALL_ENCRYPTION_KEY first — generate one with:")
        print("  python scripts/generate_encryption_key.py")
        raise SystemExit(2)
    app.launch(server_name=host, server_port=port, show_error=True,
               **launch_kwargs())


if __name__ == "__main__":
    main()
