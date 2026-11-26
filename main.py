"""Thin launcher: ``python main.py`` (project root).

Delegates to wedding_hall_system.__main__.py. Keep this file short —
no business logic, no UI code here (SRS §16.5).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["NO_PROXY"] = "localhost,127.0.0.1"
os.environ["no_proxy"] = "localhost,127.0.0.1"

# Run Gradio fully offline: Gradio is a Hugging Face product and its
# bundled huggingface_hub otherwise contacts huggingface.co (version /
# telemetry traffic the project does not need). These must be set before
# importing Gradio. setdefault: a user can opt back into online mode.
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from wedding_hall_system.__main__ import main

if __name__ == "__main__":
    main()
