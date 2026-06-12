"""Action tracking and recommendation helpers for AirPulse."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def profile_key(name: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(name or "").strip())
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return cleaned or "default_user"


def load_action_tracker(path: Path) -> dict[str, Any]:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"profiles": {}}


def save_action_tracker(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_action_profile(path: Path, profile_name: str) -> dict[str, Any]:
    tracker = load_action_tracker(path)
    return tracker.get("profiles", {}).get(profile_key(profile_name), {})


def persist_action_snapshot(
    path: Path,
    profile_name: str,
    flags: dict[str, Any],
    payload: dict[str, Any],
    history_limit: int = 45,
) -> list[dict[str, Any]]:
    tracker = load_action_tracker(path)
    profiles = tracker.setdefault("profiles", {})
    key = profile_key(profile_name)
    profile = profiles.setdefault(key, {"display_name": profile_name, "flags": {}, "history": []})
    profile["display_name"] = profile_name
    profile["flags"] = dict(flags)
    history = [row for row in profile.get("history", []) if row.get("date") != payload.get("date")]
    history.append(payload)
    history = sorted(history, key=lambda row: row.get("date", ""))[-history_limit:]
    profile["history"] = history
    save_action_tracker(path, tracker)
    return history


def build_top_actions(
    aqi_val: float,
    dominant_pollutant: str,
    wind: dict | None,
    flags: dict,
    checklist: dict,
    commute_mode: str,
    commute_saved: float,
) -> list[dict[str, Any]]:
    wind_speed = float((wind or {}).get("speed") or 0)
    is_sensitive = any(flags.get(k) for k in ["asthma", "child", "elderly"])
    completed = checklist or {}
    suggestions: list[dict[str, Any]] = []

    if aqi_val > 100:
        priority = 96 + (8 if is_sensitive else 0)
        if not completed.get("windows_closed"):
            suggestions.append({
                "title": "Protect indoor air during peak hours",
                "reason": f"AQI is {int(aqi_val)} and {dominant_pollutant.upper()} is leading today's pollution pressure.",
                "score": priority,
            })
    elif aqi_val > 50:
        suggestions.append({
            "title": "Time outdoor activity around cleaner hours",
            "reason": "Conditions are moderate, so shorter exposure windows and lower-traffic routes will make a meaningful difference.",
            "score": 82 + (4 if is_sensitive else 0),
        })

    if wind_speed <= 2:
        suggestions.append({
            "title": "Choose routes with better airflow",
            "reason": "Low wind means pollutants linger longer, so street canyons and heavy traffic corridors are less forgiving today.",
            "score": 78,
        })

    if commute_mode == "Car":
        suggestions.append({
            "title": "Swap one car trip for a cleaner option",
            "reason": f"Today's commute setup is leaving about {max(commute_saved, 0):.2f} kg CO2/day of avoidable savings on the table.",
            "score": 76,
        })

    if not completed.get("checked_aqi"):
        suggestions.append({
            "title": "Check AQI before outdoor plans",
            "reason": "A quick air-quality check should anchor exercise, windows, and route decisions before the day gets locked in.",
            "score": 74,
        })

    if not completed.get("protected_health") and is_sensitive:
        suggestions.append({
            "title": "Use your sensitive-group protection routine",
            "reason": "Your profile suggests extra respiratory caution, so mask, inhaler access, or filtered indoor time should move up the list.",
            "score": 88,
        })

    if not completed.get("shared_awareness"):
        suggestions.append({
            "title": "Share one practical air-quality action",
            "reason": "Turning today's insight into one message or reminder helps the page become a community action tool, not just a dashboard.",
            "score": 60,
        })

    unique = {item["title"]: item for item in suggestions}
    ranked = sorted(unique.values(), key=lambda item: item["score"], reverse=True)
    return ranked[:3]


def compute_action_score(
    checklist_score: int,
    commute_saved: float,
    footprint_monthly: float | None,
    aqi_val: float,
    flags: dict,
    commute_mode: str,
) -> int:
    del flags, commute_mode
    score = 25
    score += min(30, checklist_score * 3)
    score += min(20, max(0.0, commute_saved) * 4)
    if footprint_monthly is not None:
        score += 15 if footprint_monthly < 0.4 else 8 if footprint_monthly <= 0.8 else 2
    if aqi_val <= 50:
        score += 10
    elif aqi_val <= 100:
        score += 6
    else:
        score += 2 if checklist_score >= 4 else 0
    return int(max(0, min(100, round(score))))
