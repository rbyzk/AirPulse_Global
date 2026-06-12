"""Utility helpers for AirPulse."""
import re
from pathlib import Path
from typing import Optional


def normalize_station_name(name: str) -> str:
    turkish_map = {'ç':'c','ğ':'g','ı':'i','ö':'o','ş':'s','ü':'u'}
    name = (name or '').lower().strip()
    for tr, en in turkish_map.items():
        name = name.replace(tr, en)
    return re.sub(r'[^a-z0-9]', '', name)


def safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def station_key_from_name(station_name: Optional[str]) -> Optional[str]:
    if not station_name:
        return None
    return normalize_station_name(station_name)
