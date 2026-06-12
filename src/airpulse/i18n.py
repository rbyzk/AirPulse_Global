"""Simple i18n helpers for AirPulse."""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCALES_DIR = PROJECT_ROOT / "locales"
SUPPORTED_LANGS = ("en", "tr")
DEFAULT_LANG = "en"


@st.cache_data(show_spinner=False)
def _load_locale(lang: str) -> dict[str, Any]:
    lang = lang if lang in SUPPORTED_LANGS else DEFAULT_LANG
    path = LOCALES_DIR / f"{lang}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _deep_get(data: dict[str, Any], key: str) -> Any:
    current: Any = data
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def detect_browser_language() -> str:
    try:
        headers = getattr(st.context, "headers", {}) or {}
        header = headers.get("accept-language", "")
        primary = header.split(",")[0].split("-")[0].strip().lower()
        if primary in SUPPORTED_LANGS:
            return primary
    except Exception:
        pass
    return DEFAULT_LANG


def initialize_i18n() -> str:
    st.session_state.lang = DEFAULT_LANG
    return st.session_state.lang


def set_language(lang: str) -> str:
    st.session_state.lang = DEFAULT_LANG
    return st.session_state.lang


def get_lang() -> str:
    return DEFAULT_LANG


def translate(key: str, **kwargs: Any) -> str:
    lang = get_lang()
    locale = _load_locale(lang)
    fallback = _load_locale(DEFAULT_LANG)
    value = _deep_get(locale, key)
    if value is None:
        value = _deep_get(fallback, key)
    if value is None:
        value = key
    if isinstance(value, str) and kwargs:
        try:
            return value.format(**kwargs)
        except Exception:
            return value
    return str(value)


def format_date(value: date | datetime | None) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = value.date()
    return value.strftime("%d.%m.%Y") if get_lang() == "tr" else value.strftime("%Y-%m-%d")


def format_datetime(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.strftime("%d.%m.%Y %H:%M") if get_lang() == "tr" else value.strftime("%Y-%m-%d %H:%M")
