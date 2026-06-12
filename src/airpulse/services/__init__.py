"""
External service and export helpers for AirPulse.

All canonical implementations live in their respective sub-modules.
This __init__ re-exports the public surface for convenient imports.
"""
from __future__ import annotations

from airpulse.services.secrets import (
    get_waqi_key,
    get_tomorrow_key,
    is_debug_mode,
    render_secret_warnings,
    safe_secret_get,
    read_api_key,
)

from airpulse.services.reporting import (
    generate_pdf_report,
    generate_social_card,
    resolve_font_path,
    ensure_pdf_font,
)

__all__ = [
    "get_waqi_key",
    "get_tomorrow_key",
    "is_debug_mode",
    "render_secret_warnings",
    "safe_secret_get",
    "read_api_key",
    "generate_pdf_report",
    "generate_social_card",
    "resolve_font_path",
    "ensure_pdf_font",
]
