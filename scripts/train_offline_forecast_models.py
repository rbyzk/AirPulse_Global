"""Train reusable offline next-day forecast champions for multiple pollutants.

This script recreates notebook-style offline artifacts from local daily station data
without requiring the original notebook execution state.
"""
from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = PROJECT_ROOT / "data" / "processed" / "fact_air_quality_daily.parquet"
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
STATION_EXPANSION_DIR = PROJECT_ROOT / "data" / "processed" / "station_expansion"
DEFAULT_POLLUTANTS = ("pm25", "pm10", "o3", "no2")
BASE_POLLUTANTS = ["pm25", "pm10", "o3", "no2", "so2", "co"]


def regression_metrics(y_true: pd.Series, y_pred: np.ndarray) -> dict[str, float]:
    actual = pd.to_numeric(y_true, errors="coerce").astype(float)
    pred = pd.Series(y_pred, index=actual.index, dtype=float)
    mae = float(np.mean(np.abs(actual - pred)))
    rmse = float(np.sqrt(np.mean(np.square(actual - pred))))
    nonzero = actual.abs() > 1e-6
    mape = float(np.mean(np.abs((actual[nonzero] - pred[nonzero]) / actual[nonzero])) * 100.0) if nonzero.any() else float("nan")
    return {"MAE": mae, "RMSE": rmse, "MAPE": mape}


def load_station_scope() -> list[str] | None:
    metrics_path = ARTIFACTS_DIR / "pm25_next_day_forecast_metrics.json"
    if not metrics_path.exists():
        return None
    try:
        payload = json.loads(metrics_path.read_text(encoding="utf-8"))
        station_order = payload.get("station_order", [])
        if isinstance(station_order, list) and station_order:
            return [str(item) for item in station_order if str(item).strip()]
    except Exception:
        return None
    return None


def load_recommended_scope_name() -> str | None:
    recommendation_path = STATION_EXPANSION_DIR / "production_recommendation.csv"
    if not recommendation_path.exists():
        return None
    try:
        df = pd.read_csv(recommendation_path)
        if df.empty:
            return None
        value = str(df.iloc[0].get("recommended_scope") or "").strip()
        return value or None
    except Exception:
        return None


def select_station_scope(df: pd.DataFrame, station_limit: int, scope_mode: str) -> tuple[list[str], str]:
    available_stations = set(df["station_key"].astype(str))
    notebook_scope = load_station_scope()
    if scope_mode == "notebook" and notebook_scope:
        eligible = [station for station in notebook_scope if station in set(df["station_key"].astype(str))]
        if eligible:
            return eligible, "notebook_scope"

    if scope_mode == "recommended":
        scope_name = load_recommended_scope_name()
        if scope_name == "baseline_8_station" and notebook_scope:
            eligible = [station for station in notebook_scope if station in available_stations]
            if eligible:
                return eligible, scope_name
        if scope_name == "expanded_curated":
            curated_path = STATION_EXPANSION_DIR / "station_candidates_accepted.csv"
            if curated_path.exists():
                try:
                    curated_df = pd.read_csv(curated_path)
                    curated_keys = [
                        str(item)
                        for item in curated_df.get("station_key", pd.Series(dtype=str)).tolist()
                        if str(item).strip() and str(item) in available_stations
                    ]
                    combined = sorted(set(curated_keys) | set(notebook_scope or []))
                    if combined:
                        return combined, scope_name
                except Exception:
                    pass
        if scope_name:
            return sorted(available_stations), f"{scope_name}_fallback_all_available"

    if scope_mode == "all":
        return sorted(available_stations), "all_available_stations"

    ranked = (
        df.groupby("station_key", dropna=False)
        .agg(rows=("date", "size"))
        .sort_values("rows", ascending=False)
        .head(station_limit)
        .index.astype(str)
        .tolist()
    )
    return ranked, f"top_{station_limit}_by_coverage"


def build_feature_dataset(raw_df: pd.DataFrame, pollutant: str, station_keys: list[str]) -> tuple[pd.DataFrame, list[str], list[str], set[str], float]:
    target = pollutant.lower().strip()
    df = raw_df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "station_key", target]).copy()
    df["station_key"] = df["station_key"].astype(str)
    df = df[df["station_key"].isin(station_keys)].copy()
    df = df.sort_values(["station_key", "date"]).reset_index(drop=True)
    group = df.groupby("station_key")[target]

    df[f"{target}_next_day"] = group.shift(-1)
    for lag in (1, 7, 14, 21, 28):
        df[f"{target}_lag_{lag}"] = group.shift(lag)

    shifted = group.shift(1)
    for window, min_periods in ((3, 2), (7, 4), (14, 7), (21, 10)):
        rolling = shifted.groupby(df["station_key"]).rolling(window, min_periods=min_periods)
        df[f"{target}_roll_mean_{window}"] = rolling.mean().reset_index(level=0, drop=True)
        df[f"{target}_roll_std_{window}"] = rolling.std().reset_index(level=0, drop=True)

    rolling7 = shifted.groupby(df["station_key"]).rolling(7, min_periods=4)
    df[f"{target}_roll_min_7"] = rolling7.min().reset_index(level=0, drop=True)
    df[f"{target}_roll_max_7"] = rolling7.max().reset_index(level=0, drop=True)
    df[f"{target}_roll_median_7"] = rolling7.median().reset_index(level=0, drop=True)

    df["day_of_week"] = df["date"].dt.dayofweek
    df["month"] = df["date"].dt.month
    df["is_weekend"] = (df["day_of_week"] >= 5).astype(int)
    df["month_sin"] = np.sin(2 * np.pi * df["month"] / 12.0)
    df["month_cos"] = np.cos(2 * np.pi * df["month"] / 12.0)
    df["weekday_sin"] = np.sin(2 * np.pi * df["day_of_week"] / 7.0)
    df["weekday_cos"] = np.cos(2 * np.pi * df["day_of_week"] / 7.0)

    df[f"{target}_diff_1_7"] = df[f"{target}_lag_1"] - df[f"{target}_lag_7"]
    df[f"{target}_diff_7_14"] = df[f"{target}_lag_7"] - df[f"{target}_lag_14"]
    df[f"{target}_diff_14_28"] = df[f"{target}_lag_14"] - df[f"{target}_lag_28"]
    df[f"{target}_ratio_1_7"] = df[f"{target}_lag_1"] / (df[f"{target}_lag_7"].abs() + 1.0)
    df[f"{target}_ratio_7_21"] = df[f"{target}_lag_7"] / (df[f"{target}_lag_21"].abs() + 1.0)
    df[f"{target}_roll_range_7_14"] = df[f"{target}_roll_mean_7"] - df[f"{target}_roll_mean_14"]
    df[f"{target}_roll_spread_7"] = df[f"{target}_roll_max_7"] - df[f"{target}_roll_min_7"]
    df[f"{target}_volatility_ratio"] = df[f"{target}_roll_std_7"] / (df[f"{target}_roll_mean_7"].abs() + 1.0)
    df[f"{target}_trend_strength"] = (df[f"{target}_lag_1"] - df[f"{target}_lag_21"]) / 20.0

    high_pollution_threshold = float(df[target].quantile(0.85))
    df[f"{target}_high_pollution_lag1"] = (df[f"{target}_lag_1"] >= high_pollution_threshold).astype(int)
    df[f"{target}_high_pollution_lag7"] = (df[f"{target}_lag_7"] >= high_pollution_threshold).astype(int)

    worst_station_keys = set(
        df.groupby("station_key")[target]
        .mean()
        .sort_values(ascending=False)
        .head(min(4, len(station_keys)))
        .index.astype(str)
        .tolist()
    )
    df["worst_station_flag"] = df["station_key"].isin(worst_station_keys).astype(int)
    df["winter_flag"] = df["month"].isin([11, 12, 1, 2]).astype(int)
    df["worst_station_high_pollution_flag"] = df["worst_station_flag"] * df[f"{target}_high_pollution_lag1"]
    df["winter_high_pollution_flag"] = df["winter_flag"] * df[f"{target}_high_pollution_lag1"]
    df["station_code"] = df["station_key"].astype("category").cat.codes
    df["days_since_start"] = (df["date"] - df["date"].min()).dt.days

    feature_cols = [
        *[name for name in BASE_POLLUTANTS if name in df.columns],
        f"{target}_lag_1", f"{target}_lag_7", f"{target}_lag_14", f"{target}_lag_21", f"{target}_lag_28",
        f"{target}_roll_mean_3", f"{target}_roll_std_3",
        f"{target}_roll_mean_7", f"{target}_roll_std_7",
        f"{target}_roll_mean_14", f"{target}_roll_std_14",
        f"{target}_roll_mean_21", f"{target}_roll_std_21",
        f"{target}_roll_min_7", f"{target}_roll_max_7", f"{target}_roll_median_7",
        "day_of_week", "month", "is_weekend", "month_sin", "month_cos", "weekday_sin", "weekday_cos",
        f"{target}_diff_1_7", f"{target}_diff_7_14", f"{target}_diff_14_28",
        f"{target}_ratio_1_7", f"{target}_ratio_7_21",
        f"{target}_roll_range_7_14", f"{target}_roll_spread_7", f"{target}_volatility_ratio",
        f"{target}_trend_strength", f"{target}_high_pollution_lag1", f"{target}_high_pollution_lag7",
        "worst_station_flag", "winter_flag", "worst_station_high_pollution_flag", "winter_high_pollution_flag",
    ]
    tree_feature_cols = [*feature_cols, "station_code", "days_since_start"]
    model_df = df.dropna(subset=[f"{target}_next_day", *tree_feature_cols]).copy()
    model_df = model_df.sort_values("date").reset_index(drop=True)
    model_df = model_df.rename(columns={"date": "ds", f"{target}_next_day": "y"})
    return model_df, feature_cols, tree_feature_cols, worst_station_keys, high_pollution_threshold


def fit_candidate(name: str, model, train_df: pd.DataFrame, test_df: pd.DataFrame, feature_cols: list[str], *, log_target: bool, sample_weight: np.ndarray | None = None) -> dict[str, object]:
    y_train = np.log1p(train_df["y"]) if log_target else train_df["y"].to_numpy(dtype=float)
    fit_kwargs = {"sample_weight": sample_weight} if sample_weight is not None else {}
    model.fit(train_df[feature_cols], y_train, **fit_kwargs)
    raw_pred = np.asarray(model.predict(test_df[feature_cols]), dtype=float)
    pred = np.expm1(raw_pred) if log_target else raw_pred
    pred = np.clip(pred, 0, None)
    metrics = regression_metrics(test_df["y"], pred)
    return {"name": name, "model": model, "metrics": metrics, "pred": pred}


def train_for_pollutant(raw_df: pd.DataFrame, pollutant: str, station_keys: list[str], scope_name: str) -> dict[str, object]:
    model_df, feature_cols, tree_feature_cols, worst_station_keys, high_pollution_threshold = build_feature_dataset(raw_df, pollutant, station_keys)
    if len(model_df) < 250:
        raise RuntimeError(f"{pollutant}: not enough usable rows after feature engineering ({len(model_df)})")

    split_idx = int(len(model_df) * 0.80)
    train_df = model_df.iloc[:split_idx].copy().reset_index(drop=True)
    test_df = model_df.iloc[split_idx:].copy().reset_index(drop=True)

    stress_weights = (
        np.where(train_df["y"] >= high_pollution_threshold, 2.5, 1.0)
        * np.where(train_df["worst_station_flag"] == 1, 1.5, 1.0)
        * np.where(train_df["winter_flag"] == 1, 1.2, 1.0)
    )

    candidates = [
        fit_candidate(
            "random_forest_benchmark",
            RandomForestRegressor(n_estimators=500, max_depth=18, min_samples_leaf=2, max_features="sqrt", random_state=42, n_jobs=1),
            train_df,
            test_df,
            tree_feature_cols,
            log_target=True,
        ),
        fit_candidate(
            "gradient_boosting_benchmark",
            GradientBoostingRegressor(random_state=42, n_estimators=400, learning_rate=0.04, max_depth=2, subsample=1.0),
            train_df,
            test_df,
            tree_feature_cols,
            log_target=True,
            sample_weight=stress_weights,
        ),
    ]
    lookup = {result["name"]: result for result in candidates}
    top_two = sorted(candidates, key=lambda item: item["metrics"]["RMSE"])[:2]
    inv_rmse = np.array([1.0 / max(item["metrics"]["RMSE"], 1e-6) for item in top_two], dtype=float)
    weights = (inv_rmse / inv_rmse.sum()).tolist()
    ensemble_pred = np.zeros(len(test_df), dtype=float)
    for item, weight in zip(top_two, weights):
        ensemble_pred += weight * item["pred"]
    ensemble_pred = np.clip(ensemble_pred, 0, None)
    ensemble_metrics = regression_metrics(test_df["y"], ensemble_pred)
    lookup["tree_ensemble_stress_mix"] = {
        "name": "tree_ensemble_stress_mix",
        "model": {"type": "weighted_ensemble", "components": [item["name"] for item in top_two], "weights": weights},
        "metrics": ensemble_metrics,
        "pred": ensemble_pred,
    }

    best_result = min(lookup.values(), key=lambda item: item["metrics"]["RMSE"])
    best_name = str(best_result["name"])
    champion_model_path = ARTIFACTS_DIR / f"{pollutant}_next_day_{best_name}.pkl"
    prophet_model_path = ARTIFACTS_DIR / f"{pollutant}_next_day_prophet.json"
    metrics_path = ARTIFACTS_DIR / f"{pollutant}_next_day_forecast_metrics.json"

    with open(champion_model_path, "wb") as handle:
        pickle.dump(best_result["model"], handle)

    if isinstance(best_result["model"], dict) and best_result["model"].get("type") == "weighted_ensemble":
        for component_name in best_result["model"].get("components", []):
            component_path = ARTIFACTS_DIR / f"{pollutant}_next_day_{component_name}.pkl"
            with open(component_path, "wb") as handle:
                pickle.dump(lookup[component_name]["model"], handle)

    payload = {
        "model_type": "ChampionAwareBundle",
        "target": f"{pollutant}_next_day",
        "feature_cols": feature_cols,
        "tree_feature_cols": tree_feature_cols,
        "station_order": station_keys,
        "dataset_start_date": str(pd.to_datetime(model_df["ds"]).min().date()),
        "worst_station_keys": sorted(worst_station_keys),
        "high_pollution_threshold": float(high_pollution_threshold),
        "train_rows": int(len(train_df)),
        "test_rows": int(len(test_df)),
        "best_model_name": best_name,
        "champion_model_path": str(champion_model_path),
        "prophet_model_path": str(prophet_model_path),
        "best_prophet_candidate": "",
        "best_prophet_config": {},
        "prophet_tuning_results": [],
        "model_tournament_results": [
            {"model": name, **{metric_name: float(metric_value) for metric_name, metric_value in result["metrics"].items()}}
            for name, result in sorted(lookup.items(), key=lambda item: item[1]["metrics"]["RMSE"])
        ],
        "rolling_summary": [],
        "selected_station_config": scope_name,
        "baseline_metrics": {},
        "holt_winters_metrics": {},
        "prophet_metrics": {},
        "pollution_segment_metrics": [],
    }
    metrics_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return {
        "pollutant": pollutant,
        "best_model_name": best_name,
        "train_rows": int(len(train_df)),
        "test_rows": int(len(test_df)),
        "metrics_path": str(metrics_path),
        "champion_model_path": str(champion_model_path),
        "leaderboard": payload["model_tournament_results"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train offline next-day forecast artifacts for one or more pollutants.")
    parser.add_argument("--pollutants", nargs="+", default=list(DEFAULT_POLLUTANTS), help="Pollutants to train, e.g. pm25 pm10 o3 no2")
    parser.add_argument("--station-limit", type=int, default=8, help="Fallback station count when notebook scope is unavailable")
    parser.add_argument("--scope-mode", choices=["all", "recommended", "notebook", "top"], default="all", help="How to choose the station training scope")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raw_df = pd.read_parquet(DATASET_PATH)
    station_keys, scope_name = select_station_scope(raw_df, station_limit=int(args.station_limit), scope_mode=str(args.scope_mode))
    results = []
    for pollutant in [item.lower().strip() for item in args.pollutants]:
        results.append(train_for_pollutant(raw_df, pollutant, station_keys, scope_name))
    print(json.dumps({"station_scope_name": scope_name, "station_scope": station_keys, "results": results}, indent=2))


if __name__ == "__main__":
    main()
