"""Secret loading helpers with Streamlit-first resolution."""

from __future__ import annotations

import os

import streamlit as st


def _normalize_secret_value(value):
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip().strip('"').strip("'").strip()
        return cleaned or None
    cleaned = str(value).strip().strip('"').strip("'").strip()
    return cleaned or None


def safe_secret_get(*keys: str):
    try:
        root = st.secrets
    except Exception:
        return None

    for key in keys:
        if not key:
            continue
        try:
            if key in root:
                value = _normalize_secret_value(root[key])
                if value:
                    return value
        except Exception:
            pass
        if "." in key:
            current = root
            ok = True
            for part in key.split("."):
                try:
                    current = current[part]
                except Exception:
                    ok = False
                    break
            value = _normalize_secret_value(current) if ok else None
            if value:
                return value
    return None


def read_local_secret_file(*paths: str):
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                value = handle.read().strip()
            if value:
                return value
        except Exception:
            pass
    return None


def read_api_key(*, secret_keys: tuple[str, ...], env_keys: tuple[str, ...], file_paths: tuple[str, ...], default=None):
    value = safe_secret_get(*secret_keys)
    if value:
        return value

    for env_key in env_keys:
        env_value = (os.getenv(env_key) or "").strip()
        if env_value:
            return env_value

    file_value = read_local_secret_file(*file_paths)
    if file_value:
        return file_value

    return default


def get_waqi_key():
    return read_api_key(
        secret_keys=(
            "WAQI_TOKEN",
            "WAQI_API_KEY",
            "AQICN_TOKEN",
            "AQICN_API_KEY",
            "waqi_token",
            "waqi_api",
            "api_token",
            "api.WAQI_TOKEN",
            "api.WAQI_API_KEY",
            "api.AQICN_TOKEN",
            "api.waqi_token",
            "api.api_token",
        ),
        env_keys=("WAQI_TOKEN", "WAQI_API_KEY", "AQICN_TOKEN", "AQICN_API_KEY"),
        file_paths=("api_token.txt", "./api_token.txt", "waqi_token.txt", "./waqi_token.txt"),
        default="demo",
    )


def get_tomorrow_key():
    return read_api_key(
        secret_keys=(
            "TOMORROW_IO_API_KEY",
            "TOMORROW_API",
            "WIND_API_KEY",
            "tomorrow_io_api_key",
            "tomorrow_api",
            "wind_api",
            "api.TOMORROW_IO_API_KEY",
            "api.TOMORROW_API",
            "api.WIND_API_KEY",
            "api.tomorrow_api",
            "api.wind_api",
        ),
        env_keys=("TOMORROW_IO_API_KEY", "TOMORROW_API", "WIND_API_KEY"),
        file_paths=("tomorrow_api.txt", "./tomorrow_api.txt", "tomorrow_api", "./tomorrow_api", "wind_api.txt", "./wind_api.txt"),
        default=None,
    )


def is_debug_mode() -> bool:
    secret_value = safe_secret_get("DEBUG", "debug", "app.DEBUG", "app.debug")
    if isinstance(secret_value, str):
        return secret_value.strip().lower() in {"1", "true", "yes", "on"}
    env_value = (os.getenv("AIRPULSE_DEBUG") or "").strip().lower()
    return env_value in {"1", "true", "yes", "on"}


def render_secret_warnings(waqi_key: str | None, tomorrow_key: str | None) -> None:
    if waqi_key in (None, "", "demo"):
        st.warning("WAQI API key is not configured. The app is running with limited demo access, so some live station data may be incomplete.")
    if not tomorrow_key:
        st.info("Tomorrow.io API key is not configured. Wind features will stay in fallback mode where possible.")
