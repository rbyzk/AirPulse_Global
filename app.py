"""Streamlit entrypoint for the AirPulse Global application."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_ROOT = PROJECT_ROOT / "src"


def bootstrap_python_path() -> None:
    """Ensure the local ``src`` package directory is importable."""
    src_root_str = str(SRC_ROOT)
    if src_root_str not in sys.path:
        sys.path.insert(0, src_root_str)


bootstrap_python_path()

from airpulse.legacy_app import main as legacy_main


def run() -> None:
    """Launch the Streamlit application."""
    legacy_main()


if __name__ == "__main__":
    run()
