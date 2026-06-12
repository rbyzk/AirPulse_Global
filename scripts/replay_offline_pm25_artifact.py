"""Replay the saved offline PM2.5 champion on the notebook feature dataset."""

from __future__ import annotations

import json
import math
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def regression_metrics(y_true: pd.Series, y_pred: pd.Series) -> dict[str, float]:
    frame = pd.DataFrame({"y_true": pd.to_numeric(y_true, errors="coerce"), "y_pred": pd.to_numeric(y_pred, errors="coerce")}).dropna()
    rmse = float(np.sqrt(np.mean(np.square(frame["y_true"] - frame["y_pred"]))))
    mae = float(np.mean(np.abs(frame["y_true"] - frame["y_pred"])))
    denom = frame["y_true"].abs().replace(0, np.nan)
    mape = float((((frame["y_true"] - frame["y_pred"]).abs() / denom) * 100.0).replace([np.inf, -np.inf], np.nan).dropna().mean())
    return {
        "rows_used": int(len(frame)),
        "MAE": mae,
        "RMSE": rmse,
        "MAPE": mape,
    }


def load_pickle(path: Path):
    with open(path, "rb") as handle:
        return pickle.load(handle)


def predict_model(model_obj, x: pd.DataFrame, artifacts_dir: Path) -> np.ndarray:
    def postprocess(name: str, values: np.ndarray) -> np.ndarray:
        if name and name != "prophet_regressor_model":
            return np.expm1(values)
        return values

    if isinstance(model_obj, dict) and model_obj.get("type") == "weighted_ensemble":
        total = np.zeros(len(x), dtype=float)
        for component_name, weight in zip(model_obj.get("components", []), model_obj.get("weights", [])):
            component_path = artifacts_dir / f"pm25_next_day_{component_name}.pkl"
            component_obj = load_pickle(component_path)
            raw = np.asarray(component_obj.predict(x), dtype=float)
            total += float(weight) * postprocess(component_name, raw)
        return total
    return postprocess("champion", np.asarray(model_obj.predict(x), dtype=float))


def main() -> None:
    artifacts_dir = PROJECT_ROOT / "artifacts"
    metrics_path = artifacts_dir / "pm25_next_day_forecast_metrics.json"
    dataset_path = PROJECT_ROOT / "data" / "processed" / "featured_dataset.parquet"

    metadata = json.loads(metrics_path.read_text(encoding="utf-8"))
    df = pd.read_parquet(dataset_path).copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.sort_values(["station_key", "date"]).reset_index(drop=True)

    trained_station_order = metadata.get("station_order", [])
    if trained_station_order:
        df = df[df["station_key"].astype(str).isin(trained_station_order)].copy()

    for col in [
        "pm25_lag_21",
        "pm25_lag_28",
        "pm25_roll_mean_21",
        "pm25_roll_std_21",
        "pm25_roll_min_7",
        "pm25_roll_max_7",
        "pm25_roll_median_7",
        "is_weekend",
        "month_sin",
        "month_cos",
        "weekday_sin",
        "weekday_cos",
        "pm25_diff_1_7",
        "pm25_diff_7_14",
        "pm25_diff_14_28",
        "pm25_ratio_1_7",
        "pm25_ratio_7_21",
        "pm25_roll_range_7_14",
        "pm25_roll_spread_7",
        "pm25_volatility_ratio",
        "pm25_trend_strength",
        "pm25_high_pollution_lag1",
        "pm25_high_pollution_lag7",
        "worst_station_flag",
        "winter_flag",
        "worst_station_high_pollution_flag",
        "winter_high_pollution_flag",
        "station_code",
        "days_since_start",
    ]:
        if col not in df.columns:
            df[col] = np.nan

    pm25_group = df.groupby("station_key")["pm25"]
    if df["pm25_lag_21"].isna().all():
        df["pm25_lag_21"] = pm25_group.shift(21)
    if df["pm25_lag_28"].isna().all():
        df["pm25_lag_28"] = pm25_group.shift(28)
    shifted_pm25 = pm25_group.shift(1)
    if df["pm25_roll_mean_21"].isna().all():
        df["pm25_roll_mean_21"] = shifted_pm25.groupby(df["station_key"]).rolling(21, min_periods=10).mean().reset_index(level=0, drop=True)
    if df["pm25_roll_std_21"].isna().all():
        df["pm25_roll_std_21"] = shifted_pm25.groupby(df["station_key"]).rolling(21, min_periods=10).std().reset_index(level=0, drop=True)
    if df["pm25_roll_min_7"].isna().all():
        df["pm25_roll_min_7"] = shifted_pm25.groupby(df["station_key"]).rolling(7, min_periods=4).min().reset_index(level=0, drop=True)
    if df["pm25_roll_max_7"].isna().all():
        df["pm25_roll_max_7"] = shifted_pm25.groupby(df["station_key"]).rolling(7, min_periods=4).max().reset_index(level=0, drop=True)
    if df["pm25_roll_median_7"].isna().all():
        df["pm25_roll_median_7"] = shifted_pm25.groupby(df["station_key"]).rolling(7, min_periods=4).median().reset_index(level=0, drop=True)

    df["is_weekend"] = (pd.to_numeric(df["day_of_week"], errors="coerce") >= 5).astype(int)
    df["month_sin"] = np.sin(2 * np.pi * pd.to_numeric(df["month"], errors="coerce") / 12.0)
    df["month_cos"] = np.cos(2 * np.pi * pd.to_numeric(df["month"], errors="coerce") / 12.0)
    df["weekday_sin"] = np.sin(2 * np.pi * pd.to_numeric(df["day_of_week"], errors="coerce") / 7.0)
    df["weekday_cos"] = np.cos(2 * np.pi * pd.to_numeric(df["day_of_week"], errors="coerce") / 7.0)
    df["pm25_diff_1_7"] = pd.to_numeric(df["pm25_lag_1"], errors="coerce") - pd.to_numeric(df["pm25_lag_7"], errors="coerce")
    df["pm25_diff_7_14"] = pd.to_numeric(df["pm25_lag_7"], errors="coerce") - pd.to_numeric(df["pm25_lag_14"], errors="coerce")
    df["pm25_diff_14_28"] = pd.to_numeric(df["pm25_lag_14"], errors="coerce") - pd.to_numeric(df["pm25_lag_28"], errors="coerce")
    df["pm25_ratio_1_7"] = pd.to_numeric(df["pm25_lag_1"], errors="coerce") / (pd.to_numeric(df["pm25_lag_7"], errors="coerce").abs() + 1)
    df["pm25_ratio_7_21"] = pd.to_numeric(df["pm25_lag_7"], errors="coerce") / (pd.to_numeric(df["pm25_lag_21"], errors="coerce").abs() + 1)
    df["pm25_roll_range_7_14"] = pd.to_numeric(df["pm25_roll_mean_7"], errors="coerce") - pd.to_numeric(df["pm25_roll_mean_14"], errors="coerce")
    df["pm25_roll_spread_7"] = pd.to_numeric(df["pm25_roll_max_7"], errors="coerce") - pd.to_numeric(df["pm25_roll_min_7"], errors="coerce")
    df["pm25_volatility_ratio"] = pd.to_numeric(df["pm25_roll_std_7"], errors="coerce") / (pd.to_numeric(df["pm25_roll_mean_7"], errors="coerce").abs() + 1)
    df["pm25_trend_strength"] = (pd.to_numeric(df["pm25_lag_1"], errors="coerce") - pd.to_numeric(df["pm25_lag_21"], errors="coerce")) / 20.0
    df["pm25_high_pollution_lag1"] = (pd.to_numeric(df["pm25_lag_1"], errors="coerce") >= 55).astype(int)
    df["pm25_high_pollution_lag7"] = (pd.to_numeric(df["pm25_lag_7"], errors="coerce") >= 55).astype(int)
    worst_station_keys = set(metadata.get("worst_station_keys", []))
    df["worst_station_flag"] = df["station_key"].astype(str).isin(worst_station_keys).astype(int)
    df["winter_flag"] = pd.to_numeric(df["month"], errors="coerce").isin([11, 12, 1, 2]).astype(int)
    df["worst_station_high_pollution_flag"] = df["worst_station_flag"] * df["pm25_high_pollution_lag1"]
    df["winter_high_pollution_flag"] = df["winter_flag"] * df["pm25_high_pollution_lag1"]
    df["station_code"] = df["station_key"].astype("category").cat.codes
    df["days_since_start"] = (pd.to_datetime(df["date"]) - pd.to_datetime(df["date"]).min()).dt.days

    tree_feature_cols = metadata.get("tree_feature_cols", [])
    target_col = "pm25_next_day"
    model_df = df.dropna(subset=[target_col] + tree_feature_cols).copy()
    model_df = model_df.sort_values("date").reset_index(drop=True)
    split_idx = int(len(model_df) * 0.80)
    train_df = model_df.iloc[:split_idx].copy()
    test_df = model_df.iloc[split_idx:].copy()

    champion_model_name = str(metadata.get("best_model_name"))
    champion_model_path = artifacts_dir / f"pm25_next_day_{champion_model_name}.pkl"
    champion_obj = load_pickle(champion_model_path)

    x_test = test_df[tree_feature_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    y_pred = np.clip(predict_model(champion_obj, x_test, artifacts_dir), 0, None)
    result = {
        "model": champion_model_name,
        "train_rows": int(len(train_df)),
        "test_rows": int(len(test_df)),
        "metrics": regression_metrics(test_df[target_col], pd.Series(y_pred, index=test_df.index)),
        "trained_stations": sorted(set(trained_station_order)),
        "date_range": {
            "train_end": str(pd.to_datetime(train_df["date"]).max().date()) if not train_df.empty else None,
            "test_start": str(pd.to_datetime(test_df["date"]).min().date()) if not test_df.empty else None,
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
