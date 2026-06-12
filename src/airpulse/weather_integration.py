"""Open-Meteo integration for daily historical weather, forecast weather, and air-quality backfill."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import hashlib
import json
import requests
import pandas as pd

from .config import FORECAST_DEFAULTS, WEATHER_CACHE_DIR
from .utils import ensure_dir

HISTORICAL_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
HOURLY_VARS = [
    "temperature_2m",
    "relative_humidity_2m",
    "wind_speed_10m",
    "wind_direction_10m",
    "precipitation",
]


def _cache_path(prefix: str, lat: float, lon: float, start_date: str, end_date: str) -> Path:
    ensure_dir(WEATHER_CACHE_DIR)
    key = f"{prefix}:{lat:.4f}:{lon:.4f}:{start_date}:{end_date}"
    digest = hashlib.md5(key.encode('utf-8')).hexdigest()[:16]
    return WEATHER_CACHE_DIR / f"{digest}.json"


def _aggregate_daily(hourly: dict) -> pd.DataFrame:
    if not hourly or 'time' not in hourly:
        return pd.DataFrame(columns=['date'])
    df = pd.DataFrame(hourly)
    df['time'] = pd.to_datetime(df['time'], errors='coerce')
    df = df.dropna(subset=['time'])
    if df.empty:
        return pd.DataFrame(columns=['date'])
    df['date'] = df['time'].dt.normalize()
    daily = df.groupby('date', as_index=False).agg({
        'temperature_2m': 'mean',
        'relative_humidity_2m': 'mean',
        'wind_speed_10m': 'mean',
        'wind_direction_10m': 'mean',
        'precipitation': 'sum',
    })
    daily['wind_direction_sin'] = (daily['wind_direction_10m'].fillna(0).apply(lambda x: __import__('math').sin(__import__('math').radians(x))))
    daily['wind_direction_cos'] = (daily['wind_direction_10m'].fillna(0).apply(lambda x: __import__('math').cos(__import__('math').radians(x))))
    return daily.rename(columns={'wind_direction_10m': 'wind_direction'})


def _fetch_json(url: str, params: dict, cache_prefix: str) -> dict:
    start_date = params.get('start_date') or params.get('past_days') or 'na'
    end_date = params.get('end_date') or params.get('forecast_days') or 'na'
    cache = _cache_path(cache_prefix, float(params['latitude']), float(params['longitude']), str(start_date), str(end_date))
    if cache.exists():
        try:
            cache_age_seconds = (datetime.utcnow() - datetime.utcfromtimestamp(cache.stat().st_mtime)).total_seconds()
            if cache_age_seconds <= FORECAST_DEFAULTS["history_cache_ttl_seconds"]:
                return json.loads(cache.read_text(encoding='utf-8'))
        except Exception:
            pass
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()
    cache.write_text(json.dumps(payload), encoding='utf-8')
    return payload


def _complete_daily_range(df: pd.DataFrame, value_columns: list[str], start_date: str, end_date: str) -> pd.DataFrame:
    start_ts = pd.to_datetime(start_date, errors='coerce')
    end_ts = pd.to_datetime(end_date, errors='coerce')
    if pd.isna(start_ts) or pd.isna(end_ts):
        return df

    full_dates = pd.date_range(start=start_ts.normalize(), end=end_ts.normalize(), freq='D')
    if df.empty:
        completed = pd.DataFrame({'date': full_dates})
    else:
        completed = pd.DataFrame({'date': full_dates}).merge(df, on='date', how='left')

    for col in value_columns:
        if col not in completed.columns:
            completed[col] = pd.NA
        completed[col] = pd.to_numeric(completed[col], errors='coerce')
        completed[col] = completed[col].interpolate(method='linear', limit_direction='both')

    return completed


def fetch_historical_weather(lat: float, lon: float, start_date: str, end_date: str, timezone: str = 'auto') -> pd.DataFrame:
    params = {
        'latitude': lat,
        'longitude': lon,
        'start_date': start_date,
        'end_date': end_date,
        'hourly': ','.join(HOURLY_VARS),
        'timezone': timezone,
    }
    payload = _fetch_json(HISTORICAL_URL, params, 'hist')
    return _aggregate_daily(payload.get('hourly', {}))


def fetch_weather_forecast(lat: float, lon: float, forecast_days: int = 7, timezone: str = 'auto') -> pd.DataFrame:
    params = {
        'latitude': lat,
        'longitude': lon,
        'hourly': ','.join(HOURLY_VARS),
        'timezone': timezone,
        'forecast_days': int(forecast_days),
        'past_days': 0,
    }
    payload = _fetch_json(FORECAST_URL, params, 'forecast')
    daily = _aggregate_daily(payload.get('hourly', {}))
    return daily.head(forecast_days)


def fetch_air_quality_history(
    lat: float,
    lon: float,
    days: int = 90,
    timezone: str = 'auto',
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    hourly_vars = [
        "pm2_5",
        "pm10",
        "ozone",
        "nitrogen_dioxide",
        "sulphur_dioxide",
        "carbon_monoxide",
        "us_aqi",
    ]
    if not end_date:
        end_date = datetime.utcnow().date().isoformat()
    if not start_date:
        start_date = (datetime.utcnow().date() - timedelta(days=max(int(days) - 1, 0))).isoformat()
    params = {
        'latitude': lat,
        'longitude': lon,
        'hourly': ','.join(hourly_vars),
        'timezone': timezone,
        'start_date': start_date,
        'end_date': end_date,
    }
    payload = _fetch_json(AIR_QUALITY_URL, params, 'air_quality_hist')
    hourly = payload.get('hourly', {})
    if not hourly or 'time' not in hourly:
        return pd.DataFrame(columns=['date', 'real_observed_days', 'interpolated_days'])
    df = pd.DataFrame(hourly)
    df['time'] = pd.to_datetime(df['time'], errors='coerce')
    df = df.dropna(subset=['time'])
    if df.empty:
        return pd.DataFrame(columns=['date', 'real_observed_days', 'interpolated_days'])
    df['date'] = df['time'].dt.normalize()
    rename_map = {
        'pm2_5': 'pm25',
        'pm10': 'pm10',
        'ozone': 'o3',
        'nitrogen_dioxide': 'no2',
        'sulphur_dioxide': 'so2',
        'carbon_monoxide': 'co',
        'us_aqi': 'aqi',
    }
    for src, dest in rename_map.items():
        if src in df.columns:
            df[src] = pd.to_numeric(df[src], errors='coerce')
    agg_cols = {src: 'mean' for src in rename_map if src in df.columns}
    daily = df.groupby('date', as_index=False).agg(agg_cols)
    daily = daily.rename(columns=rename_map).sort_values('date').reset_index(drop=True)
    pollutant_columns = [col for col in ['pm25', 'pm10', 'o3', 'no2', 'so2', 'co', 'aqi'] if col in daily.columns]
    real_observed_days = int(daily[pollutant_columns].notna().any(axis=1).sum()) if pollutant_columns else 0
    completed = _complete_daily_range(daily, pollutant_columns, start_date, end_date)
    interpolated_days = int(len(completed) - real_observed_days)
    completed['real_observed_days'] = real_observed_days
    completed['interpolated_days'] = max(interpolated_days, 0)
    return completed.tail(days).reset_index(drop=True)
