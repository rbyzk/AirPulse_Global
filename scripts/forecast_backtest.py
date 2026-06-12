"""Run a quick rolling leaderboard for a city/pollutant using real AirPulse history."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from airpulse.forecasting import (  # noqa: E402
    WEATHER_REGRESSORS,
    _backtest_candidate_models,
    _build_observed_history,
    _merge_history_with_weather,
)
from airpulse.weather_integration import fetch_historical_weather  # noqa: E402


def run(city_payload: dict, pollutant: str) -> dict:
    history_df, history_diag = _build_observed_history(city_payload, pollutant, station_key=None)
    if history_df.empty:
        return {"error": "history_empty", "history_diag": history_diag}

    start_date = pd.to_datetime(history_df["date"]).min().strftime("%Y-%m-%d")
    end_date = pd.to_datetime(history_df["date"]).max().strftime("%Y-%m-%d")
    weather_df = fetch_historical_weather(float(city_payload["lat"]), float(city_payload["lon"]), start_date, end_date)
    train_df = _merge_history_with_weather(history_df, weather_df, pollutant)
    diagnostics = _backtest_candidate_models(train_df, pollutant)

    return {
        "pollutant": pollutant,
        "history_rows": int(len(history_df)),
        "train_rows": int(len(train_df)),
        "history_diag": history_diag,
        "candidate_metrics": {
            "prophet_backtest_mape": diagnostics.get("prophet_backtest_mape"),
            "tabular_backtest_mape": diagnostics.get("tabular_backtest_mape"),
            "holt_winters_backtest_mape": diagnostics.get("holt_winters_backtest_mape"),
        },
        "selected_model": diagnostics.get("selected_model"),
        "selection_reason": diagnostics.get("selection_reason"),
        "selected_model_backtest_mape": diagnostics.get("selected_model_backtest_mape"),
        "regressors": list(WEATHER_REGRESSORS),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run AirPulse forecast backtest leaderboard.")
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument("--lon", type=float, required=True)
    parser.add_argument("--city", type=str, default="Istanbul, TR")
    parser.add_argument("--pollutant", type=str, default="pm25", choices=["pm25", "pm10", "o3", "no2"])
    args = parser.parse_args()

    payload = {
        "lat": args.lat,
        "lon": args.lon,
        "station_name": args.city,
        "pm25": 0,
        "pm10": 0,
        "o3": 0,
        "no2": 0,
    }
    result = run(payload, args.pollutant)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
