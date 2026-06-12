"""Core configuration for AirPulse forecasting and integrations."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
EXTERNAL_DATA_DIR = DATA_DIR / "external"
PROCESSED_DATA_DIR = DATA_DIR / "processed"
OBSERVATION_CACHE_DIR = PROCESSED_DATA_DIR / "observation_cache"
WEATHER_CACHE_DIR = PROCESSED_DATA_DIR / "weather_cache"
FORECAST_VALIDATION_FILE = PROCESSED_DATA_DIR / "forecast_validation.json"

STATIONS_FILE = PROJECT_ROOT / "stations.csv"
WAQI_EXTERNAL_DIR = EXTERNAL_DATA_DIR / "waqi"
STATION_EXPANSION_DIR = PROCESSED_DATA_DIR / "station_expansion"

POLLUTANTS = ["pm25", "pm10", "o3", "no2", "so2", "co"]
WEATHER_REGRESSORS = [
    "temperature_2m",
    "relative_humidity_2m",
    "wind_speed_10m",
    "wind_direction_sin",
    "wind_direction_cos",
    "precipitation",
]

FORECAST_DEFAULTS = {
    "history_days": 90,
    "history_cache_ttl_seconds": 21600,
    # Live AQI should still feel current, but a slightly longer cache avoids repeated
    # network bursts when users navigate between pages or tweak nearby controls.
    "live_api_cache_ttl_seconds": 600,
    # Forecast inputs are relatively stable within a session, so keep them sticky.
    "forecast_input_cache_ttl_seconds": 1800,
    # Wind can be refreshed less often without hurting the dashboard experience.
    "wind_cache_ttl_seconds": 900,
    # Station and map data are some of the heaviest calls; cache them longer.
    "station_map_cache_ttl_seconds": 900,
    # Global overview powers the dashboard map, monitor cards, and rankings.
    "global_overview_cache_ttl_seconds": 1800,
    # Analytics views are exploratory and heavier, so a longer cache window is worthwhile.
    "analytics_cache_ttl_seconds": 3600,
    "min_train_days": 21,
    "min_real_history_days": 30,
    "seasonality_mode": "additive",
    "changepoint_prior_scale": 0.08,
    "seasonality_prior_scale": 10.0,
    "interval_width": 0.90,
    "forecast_horizon_days": 7,
    "holt_winters_season_length": 7,
    "forecast_backtest_days": 7,
    "validation_retention_days": 45,
    "min_validation_points_for_accuracy": 7,
    "offline_recent_backtest_days": 21,
    # Raised from 35 → 55 to avoid over-rejecting the champion during high-pollution or
    # volatile periods. Holt-Winters fallback (RMSE ~143) is far worse than the champion
    # (RMSE ~16), so a generous threshold is strongly preferred.
    "offline_champion_max_recent_mape": 55.0,
}

FORECAST_MODEL_PROFILES = {
    # pm25: tuned to match notebook champion 'prophet_additive_smoother_trend'
    # Notebook evaluation: additive RMSE=35.86 vs multiplicative RMSE=38.51
    # changepoint_prior_scale=0.03 from notebook grid search (was wrongly set to 0.05+multiplicative)
    "pm25": {
        "min_train_days": 28,
        "changepoint_prior_scale": 0.03,
        "seasonality_prior_scale": 10.0,
        "seasonality_mode": "additive",
        "holt_winters_season_length": 7,
    },
    # pm10: coarser particle, slightly more flexible changepoint
    "pm10": {
        "min_train_days": 24,
        "changepoint_prior_scale": 0.08,
        "seasonality_prior_scale": 10.0,
        "seasonality_mode": "multiplicative",
        "holt_winters_season_length": 7,
    },
    # o3: photochemical, strong diurnal — additive works better here
    "o3": {
        "min_train_days": 21,
        "changepoint_prior_scale": 0.10,
        "seasonality_prior_scale": 14.0,
        "seasonality_mode": "additive",
        "holt_winters_season_length": 7,
    },
    # no2: traffic-driven, moderate flexibility
    "no2": {
        "min_train_days": 21,
        "changepoint_prior_scale": 0.07,
        "seasonality_prior_scale": 11.0,
        "seasonality_mode": "multiplicative",
        "holt_winters_season_length": 7,
    },
}

# Pollutant-specific offline champion metrics files.
# Keys match POLLUTANTS list; files are written by the training notebooks.
OFFLINE_FORECAST_METRICS_FILES: dict[str, "Path"] = {
    pollutant: ARTIFACTS_DIR / f"{pollutant}_next_day_forecast_metrics.json"
    for pollutant in ["pm25", "pm10", "o3", "no2", "so2", "co"]
}

STATION_SELECTION_DEFAULTS = {
    "target_pollutant": "pm25",
    "minimum_history_days": 180,
    "maximum_missing_ratio": 0.35,
    "minimum_recent_availability": 0.60,
    "minimum_target_coverage": 0.70,
    "maximum_gap_ratio": 0.25,
    "minimum_observations": 120,
    "outlier_zscore_threshold": 3.5,
    "recent_window_days": 30,
}

STATION_EXPANSION_DEFAULTS = {
    "history_days": 365 * 3,
    "max_search_results_per_seed": 10,
    "max_stations_per_seed": 12,
    "request_pause_seconds": 0.75,
    "fetch_max_retries": 3,
    "fetch_retry_pause_seconds": 2.0,
    "minimum_real_history_days": 90,
    "write_raw_csv": True,
}

EVALUATION_DEFAULTS = {
    "horizons": (1, 3, 7),
    "rolling_train_min_days": 365,
    "rolling_test_window_days": 30,
    "rolling_step_days": 30,
    "high_pollution_threshold": 55.0,
    "leaderboard_sort_metric": "rmse",
}
