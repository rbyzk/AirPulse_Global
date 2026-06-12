"""Small helpers for persistent visitor counting."""
from __future__ import annotations

from pathlib import Path


def read_visitor_count(path: Path) -> int:
    try:
        if not path.exists():
            path.write_text("0", encoding="utf-8")
            return 0
        raw = path.read_text(encoding="utf-8").strip() or "0"
        return max(0, int(raw))
    except Exception:
        return 0


def increment_visitor_count(path: Path) -> int:
    current = read_visitor_count(path)
    updated = current + 1
    try:
        path.write_text(str(updated), encoding="utf-8")
    except Exception:
        return current
    return updated
