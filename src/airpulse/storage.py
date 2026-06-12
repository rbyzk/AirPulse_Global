"""Storage helpers for raw and processed station history tables."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from .config import OBSERVATION_CACHE_DIR, POLLUTANTS, PROCESSED_DATA_DIR, RAW_DATA_DIR
from .utils import normalize_station_name


FACT_AIR_QUALITY_DAILY_PATH = PROCESSED_DATA_DIR / "fact_air_quality_daily.parquet"
STATION_EXPANSION_CACHE_PATH = PROCESSED_DATA_DIR / "station_expansion" / "fetched_station_histories.parquet"


def _empty_history_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["station_key", "date", *POLLUTANTS])


def _canonical_station_key(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return normalize_station_name(text)


def _load_raw_csv_histories(raw_dir: Path) -> pd.DataFrame:
    if not raw_dir.exists():
        return _empty_history_frame()

    frames: list[pd.DataFrame] = []
    for fp in sorted(raw_dir.glob("*.csv")):
        try:
            df = pd.read_csv(fp)
        except Exception:
            continue

        station_key = _canonical_station_key(fp.stem.replace("-air-quality", ""))
        if not station_key:
            continue

        out = df.copy()
        out.columns = [str(col).strip().lower() for col in out.columns]
        if "date" not in out.columns:
            continue
        out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize()
        out["station_key"] = station_key
        for pollutant in POLLUTANTS:
            if pollutant not in out.columns:
                out[pollutant] = pd.NA
            out[pollutant] = pd.to_numeric(out[pollutant], errors="coerce")
        frames.append(out[["station_key", "date", *POLLUTANTS]])

    if not frames:
        return _empty_history_frame()
    return (
        pd.concat(frames, ignore_index=True)
        .dropna(subset=["date", "station_key"])
        .sort_values(["station_key", "date"])
        .drop_duplicates(subset=["station_key", "date"], keep="last")
        .reset_index(drop=True)
    )


def _load_observation_cache_csv_histories(cache_dir: Path) -> pd.DataFrame:
    if not cache_dir.exists():
        return _empty_history_frame()

    frames: list[pd.DataFrame] = []
    for fp in sorted(cache_dir.glob("*.csv")):
        try:
            df = pd.read_csv(fp)
        except Exception:
            continue
        if "date" not in df.columns:
            continue

        station_key = _canonical_station_key(fp.stem.replace("-air-quality", ""))
        if not station_key:
            continue

        out = df.copy()
        out.columns = [str(col).strip().lower() for col in out.columns]
        if "date" not in out.columns:
            continue
        out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize()
        out["station_key"] = station_key
        for pollutant in POLLUTANTS:
            if pollutant not in out.columns:
                out[pollutant] = pd.NA
            out[pollutant] = pd.to_numeric(out[pollutant], errors="coerce")
        frames.append(out[["station_key", "date", *POLLUTANTS]])

    if not frames:
        return _empty_history_frame()
    return (
        pd.concat(frames, ignore_index=True)
        .dropna(subset=["date", "station_key"])
        .sort_values(["station_key", "date"])
        .drop_duplicates(subset=["station_key", "date"], keep="last")
        .reset_index(drop=True)
    )


def _load_processed_history(path: Path) -> pd.DataFrame:
    if not path.exists():
        return _empty_history_frame()
    try:
        df = pd.read_parquet(path)
    except Exception:
        return _empty_history_frame()

    if "date" not in df.columns or "station_key" not in df.columns:
        return _empty_history_frame()

    out = df.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize()
    out["station_key"] = out["station_key"].map(_canonical_station_key)
    for pollutant in POLLUTANTS:
        if pollutant not in out.columns:
            out[pollutant] = pd.NA
        out[pollutant] = pd.to_numeric(out[pollutant], errors="coerce")
    return (
        out[["station_key", "date", *POLLUTANTS]]
        .dropna(subset=["date", "station_key"])
        .sort_values(["station_key", "date"])
        .drop_duplicates(subset=["station_key", "date"], keep="last")
        .reset_index(drop=True)
    )


def load_all_raw_station_histories(raw_dir: Optional[Path] = None) -> pd.DataFrame:
    """Load baseline station histories.

    Preference order:
    1. `data/raw/*.csv` if present
    2. `data/processed/observation_cache/*.csv` if manually downloaded histories exist
    3. `data/processed/station_expansion/fetched_station_histories.parquet`
    4. `data/processed/fact_air_quality_daily.parquet`

    This keeps old notebook flows working even when the project stores its main
    history table only in processed parquet form.
    """

    target_dir = raw_dir or RAW_DATA_DIR
    raw_df = _load_raw_csv_histories(target_dir)
    if not raw_df.empty:
        return raw_df

    manual_cache_df = _load_observation_cache_csv_histories(OBSERVATION_CACHE_DIR)
    if not manual_cache_df.empty:
        return manual_cache_df

    expansion_df = _load_processed_history(STATION_EXPANSION_CACHE_PATH)
    if not expansion_df.empty:
        return expansion_df

    return _load_processed_history(FACT_AIR_QUALITY_DAILY_PATH)


def load_raw_station_history(station_key: Optional[str] = None) -> pd.DataFrame:
    history_df = load_all_raw_station_histories()
    normalized_key = _canonical_station_key(station_key)
    if not normalized_key:
        return history_df
    return history_df[history_df["station_key"] == normalized_key].copy().reset_index(drop=True)


def get_observation_cache_path(station_key: Optional[str] = None) -> Path:
    normalized_key = _canonical_station_key(station_key) or "all_stations"
    return PROCESSED_DATA_DIR / "observation_cache" / f"{normalized_key}.parquet"


def load_observation_cache_histories(cache_dir: Optional[Path] = None) -> pd.DataFrame:
    return _load_observation_cache_csv_histories(cache_dir or OBSERVATION_CACHE_DIR)
