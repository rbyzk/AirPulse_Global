"""Forecasting engine: real observed history first, Prophet with weather regressors, Holt-Winters fallback."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import importlib.util
from pathlib import Path
from typing import Dict, Optional, Tuple
import math
import json
import pickle

import numpy as np
import pandas as pd

from .config import (
    ARTIFACTS_DIR,
    OFFLINE_FORECAST_METRICS_FILES,
    FORECAST_DEFAULTS,
    FORECAST_MODEL_PROFILES,
    FORECAST_VALIDATION_FILE,
    WEATHER_REGRESSORS,
    STATIONS_FILE,
)
from .storage import load_all_raw_station_histories
from .utils import normalize_station_name, safe_float
from .weather_integration import (
    fetch_air_quality_history,
    fetch_historical_weather,
    fetch_weather_forecast,
)

PROPHET_AVAILABLE = importlib.util.find_spec("prophet") is not None
Prophet = None


@dataclass
class ForecastResult:
    hist_df: pd.DataFrame
    fc_df: pd.DataFrame
    model_used: str
    data_note: str
    diagnostics: Dict[str, str]
    past_forecast_df: pd.DataFrame
    model_accuracy_pct: Optional[float]


def _transparency_diagnostics(model_used: str, history_diag: Dict[str, str], data_note: str) -> Dict[str, str]:
    """Build consistent metadata so the UI can clearly distinguish observed, predicted, and fallback-estimated data."""
    history_source = history_diag.get("history_source", "")
    fallback_reason = history_diag.get("fallback_reason", "")

    observed_label = "Observed history"
    observed_note = "Built from measured history."
    if history_source == "open_meteo_air_quality_plus_live_waqi":
        observed_note = "Measured history from Open-Meteo, anchored to the latest live reading."
    elif history_source.startswith("synthetic_fallback"):
        observed_label = "Estimated baseline history"
        observed_note = "Historical baseline is estimated because measured history was incomplete."

    if model_used == "WAQI_NATIVE":
        forecast_label = "Provider forecast"
        forecast_note = "Future values come from the upstream WAQI daily forecast feed."
    elif model_used == "PROPHET_WEATHER":
        forecast_label = "Model forecast"
        forecast_note = "Future values are model predictions built from observed history and weather inputs."
    elif model_used == "TABULAR_GRADIENT_BOOSTING":
        forecast_label = "Tree model forecast"
        forecast_note = "Future values are model predictions built from lagged pollutant structure and weather inputs."
    elif model_used == "DIRECT_MULTI_HORIZON":
        forecast_label = "Station-aware model forecast"
        forecast_note = "Future values are direct multi-horizon predictions built from lagged history, rolling context, weather, and nearby stations."
    elif model_used == "OFFLINE_CHAMPION_HYBRID":
        forecast_label = "Champion hybrid forecast"
        forecast_note = "Day 1 uses the notebook champion model, and later days use the live production forecast path."
    elif model_used == "HOLT_WINTERS":
        forecast_label = "Fallback model forecast"
        forecast_note = "Future values are fallback estimates, not live measurements."
    else:
        forecast_label = "Forecast unavailable"
        forecast_note = "A reliable forecast could not be assembled for this source."

    return {
        "observed_label": observed_label,
        "observed_note": observed_note,
        "forecast_label": forecast_label,
        "forecast_note": forecast_note,
        "fallback_active": "true" if bool(fallback_reason or model_used == "HOLT_WINTERS" or history_source.startswith("synthetic_fallback")) else "false",
        "transparency_note": data_note,
    }


def _forecast_profile(pollutant: str) -> Dict[str, float | str | int]:
    """Return pollutant-specific tuning while preserving global defaults as a fallback."""
    base_profile: Dict[str, float | str | int] = {
        "min_train_days": int(FORECAST_DEFAULTS["min_train_days"]),
        "changepoint_prior_scale": float(FORECAST_DEFAULTS["changepoint_prior_scale"]),
        "seasonality_prior_scale": float(FORECAST_DEFAULTS["seasonality_prior_scale"]),
        "seasonality_mode": str(FORECAST_DEFAULTS["seasonality_mode"]),
        "holt_winters_season_length": int(FORECAST_DEFAULTS["holt_winters_season_length"]),
    }
    pollutant_profile = FORECAST_MODEL_PROFILES.get(pollutant.lower().strip(), {})
    return {**base_profile, **pollutant_profile}


def _build_result(
    *,
    history_df: pd.DataFrame,
    fc_df: pd.DataFrame,
    pollutant: str,
    model_used: str,
    data_note: str,
    diagnostics: Dict[str, str],
    past_forecast_df: pd.DataFrame,
    model_accuracy_pct: Optional[float],
) -> ForecastResult:
    merged_diagnostics = {**diagnostics, **_transparency_diagnostics(model_used, diagnostics, data_note)}
    hist_value_df = history_df.rename(columns={pollutant: "value"}).copy() if pollutant in history_df.columns else history_df.copy()
    return ForecastResult(
        hist_df=hist_value_df,
        fc_df=fc_df,
        model_used=model_used,
        data_note=data_note,
        diagnostics=merged_diagnostics,
        past_forecast_df=past_forecast_df,
        model_accuracy_pct=model_accuracy_pct,
    )


def _empty_validation_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "source_key",
        "pollutant",
        "generated_on",
        "target_date",
        "predicted_value",
        "actual_value",
        "horizon_day",
    ])


def _resolve_artifact_path(raw_path: str) -> str:
    """Re-root a potentially hardcoded absolute path to the current ARTIFACTS_DIR.

    The training notebook saves champion_model_path as an absolute path on the
    training machine (e.g. C:\\Users\\rbeyz\\...). When the app runs on any other
    machine or directory layout, that path is invalid. We always prefer locating
    the file by name inside ARTIFACTS_DIR, falling back to the raw path only if
    the filename is not found there.
    """
    filename = Path(raw_path).name if raw_path else ""
    resolved = ARTIFACTS_DIR / filename
    if resolved.exists():
        return str(resolved)
    # fallback: try the original path (same machine, same layout)
    return raw_path


def _load_offline_champion_metadata(pollutant: str = "pm25") -> Dict[str, object]:
    """Load champion model metadata for the given pollutant.

    Uses OFFLINE_FORECAST_METRICS_FILES so each pollutant has its own metrics
    file (written by the training notebook). Falls back gracefully when the file
    does not exist yet.
    """
    metrics_file = OFFLINE_FORECAST_METRICS_FILES.get(pollutant.lower().strip())
    try:
        if metrics_file is None or not metrics_file.exists():
            return {}
        payload = json.loads(metrics_file.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _load_pickled_artifact(path_str: str) -> object | None:
    try:
        resolved = _resolve_artifact_path(path_str)
        with open(resolved, "rb") as handle:
            return pickle.load(handle)
    except Exception:
        return None


def _offline_rmse(metadata: Dict[str, object], model_name: str) -> Optional[float]:
    for row in metadata.get("model_tournament_results", []):
        if isinstance(row, dict) and row.get("model") == model_name:
            try:
                return float(row.get("RMSE"))
            except (TypeError, ValueError):
                return None
    return None


def _offline_best_metrics(metadata: Dict[str, object]) -> Dict[str, float]:
    best_model_name = str(metadata.get("best_model_name") or "").strip()
    for row in metadata.get("model_tournament_results", []):
        if isinstance(row, dict) and str(row.get("model") or "").strip() == best_model_name:
            out: Dict[str, float] = {}
            for src, dest in [("MAE", "offline_benchmark_mae"), ("RMSE", "offline_benchmark_rmse"), ("R2", "offline_benchmark_r2"), ("MAPE", "offline_benchmark_mape")]:
                try:
                    value = row.get(src)
                    if value is not None:
                        out[dest] = float(value)
                except (TypeError, ValueError):
                    pass
            return out
    return {}


def _offline_station_code_map(metadata: Dict[str, object]) -> Dict[str, int]:
    station_order = metadata.get("station_order", [])
    if isinstance(station_order, list) and station_order:
        return {
            normalize_station_name(str(station_key)): index
            for index, station_key in enumerate(station_order)
            if str(station_key).strip()
        }
    return {}


def _offline_station_order_set(metadata: Dict[str, object]) -> set[str]:
    return {
        normalize_station_name(str(station_key))
        for station_key in metadata.get("station_order", [])
        if str(station_key).strip()
    }


def _offline_target_feature_prefix(pollutant: str) -> str:
    return pollutant.lower().strip()


def _series_last(values: np.ndarray, lag: int) -> Optional[float]:
    if len(values) < lag:
        return None
    return float(values[-lag])


def _series_window(values: np.ndarray, window: int) -> Optional[np.ndarray]:
    if len(values) < window:
        return None
    return values[-window:].astype(float)


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator is None or abs(float(denominator)) < 1e-6:
        return 1.0
    return float(numerator) / float(denominator)


def _offline_feature_frame(
    history_df: pd.DataFrame,
    feed_data: Dict,
    station_key: Optional[str],
    metadata: Dict[str, object],
    *,
    pollutant: str = "pm25",
) -> Optional[pd.DataFrame]:
    feature_cols = metadata.get("tree_feature_cols", [])
    if not isinstance(feature_cols, list) or not feature_cols:
        return None
    target = _offline_target_feature_prefix(pollutant)
    if history_df.empty or target not in history_df.columns:
        return None

    history = history_df.copy()
    history["date"] = pd.to_datetime(history["date"], errors="coerce").dt.normalize()
    history[target] = pd.to_numeric(history[target], errors="coerce")
    history = history.dropna(subset=["date", target]).sort_values("date")
    if history.empty:
        return None

    target_values = history[target].to_numpy(dtype=float)
    lag_1 = _series_last(target_values, 1)
    lag_7 = _series_last(target_values, 7)
    lag_14 = _series_last(target_values, 14)
    lag_21 = _series_last(target_values, 21)
    lag_28 = _series_last(target_values, 28)
    if None in {lag_1, lag_7, lag_14, lag_21, lag_28}:
        return None

    roll_3 = _series_window(target_values, 3)
    roll_7 = _series_window(target_values, 7)
    roll_14 = _series_window(target_values, 14)
    roll_21 = _series_window(target_values, 21)
    if any(window is None for window in (roll_3, roll_7, roll_14, roll_21)):
        return None

    current_date = pd.to_datetime(history["date"].max()).normalize()
    dataset_start_date = pd.to_datetime(metadata.get("dataset_start_date"), errors="coerce")
    station_code_map = _offline_station_code_map(metadata)
    normalized_station_key = normalize_station_name(station_key or "")
    station_code = station_code_map.get(normalized_station_key, -1)
    worst_station_keys = {
        normalize_station_name(str(item))
        for item in metadata.get("worst_station_keys", [])
        if str(item).strip()
    }

    latest_row = history.iloc[-1].to_dict()
    base_row = {
        target: lag_1,
        "pm25": safe_float(latest_row.get("pm25", feed_data.get("pm25")), 0.0),
        "pm10": safe_float(latest_row.get("pm10", feed_data.get("pm10")), 0.0),
        "o3": safe_float(latest_row.get("o3", feed_data.get("o3")), 0.0),
        "no2": safe_float(latest_row.get("no2", feed_data.get("no2")), 0.0),
        "so2": safe_float(latest_row.get("so2", feed_data.get("so2")), 0.0),
        "co": safe_float(latest_row.get("co", feed_data.get("co")), 0.0),
        f"{target}_lag_1": lag_1,
        f"{target}_lag_7": lag_7,
        f"{target}_lag_14": lag_14,
        f"{target}_lag_21": lag_21,
        f"{target}_lag_28": lag_28,
        f"{target}_roll_mean_3": float(np.mean(roll_3)),
        f"{target}_roll_std_3": float(np.std(roll_3)),
        f"{target}_roll_mean_7": float(np.mean(roll_7)),
        f"{target}_roll_std_7": float(np.std(roll_7)),
        f"{target}_roll_mean_14": float(np.mean(roll_14)),
        f"{target}_roll_std_14": float(np.std(roll_14)),
        f"{target}_roll_mean_21": float(np.mean(roll_21)),
        f"{target}_roll_std_21": float(np.std(roll_21)),
        f"{target}_roll_min_7": float(np.min(roll_7)),
        f"{target}_roll_max_7": float(np.max(roll_7)),
        f"{target}_roll_median_7": float(np.median(roll_7)),
        "day_of_week": int(current_date.dayofweek),
        "month": int(current_date.month),
        "is_weekend": int(current_date.dayofweek >= 5),
        "month_sin": float(np.sin(2 * np.pi * current_date.month / 12.0)),
        "month_cos": float(np.cos(2 * np.pi * current_date.month / 12.0)),
        "weekday_sin": float(np.sin(2 * np.pi * current_date.dayofweek / 7.0)),
        "weekday_cos": float(np.cos(2 * np.pi * current_date.dayofweek / 7.0)),
        f"{target}_diff_1_7": float(lag_1 - lag_7),
        f"{target}_diff_7_14": float(lag_7 - lag_14),
        f"{target}_diff_14_28": float(lag_14 - lag_28),
        f"{target}_ratio_1_7": _safe_ratio(lag_1, lag_7),
        f"{target}_ratio_7_21": _safe_ratio(lag_7, lag_21),
        f"{target}_roll_range_7_14": float(np.mean(roll_7) - np.mean(roll_14)),
        f"{target}_roll_spread_7": float(np.max(roll_7) - np.min(roll_7)),
        f"{target}_volatility_ratio": _safe_ratio(float(np.std(roll_7)), float(np.std(roll_14))),
        f"{target}_trend_strength": _safe_ratio(float(lag_1 - lag_7), float(np.std(roll_7) + 1e-6)),
        f"{target}_high_pollution_lag1": int(lag_1 >= float(metadata.get("high_pollution_threshold", 55.0) or 55.0)),
        f"{target}_high_pollution_lag7": int(lag_7 >= float(metadata.get("high_pollution_threshold", 55.0) or 55.0)),
        "worst_station_flag": int(normalized_station_key in worst_station_keys),
        "winter_flag": int(current_date.month in {12, 1, 2}),
        "station_code": int(station_code),
        "days_since_start": int((current_date - dataset_start_date).days) if pd.notna(dataset_start_date) else int(len(history)),
    }
    base_row["worst_station_high_pollution_flag"] = int(base_row["worst_station_flag"] * base_row[f"{target}_high_pollution_lag1"])
    base_row["winter_high_pollution_flag"] = int(base_row["winter_flag"] * base_row[f"{target}_high_pollution_lag1"])

    frame = pd.DataFrame([{col: base_row.get(col, 0.0) for col in feature_cols}])
    return frame.apply(pd.to_numeric, errors="coerce").fillna(0.0)


def _offline_feature_frame_from_history(
    history_df: pd.DataFrame,
    metadata: Dict[str, object],
    station_key: Optional[str],
    *,
    history_end_date: Optional[pd.Timestamp] = None,
    pollutant: str = "pm25",
) -> Optional[pd.DataFrame]:
    """Rebuild offline champion features from API history only."""
    target = _offline_target_feature_prefix(pollutant)
    if history_df.empty or target not in history_df.columns:
        return None

    hist = history_df.copy()
    hist["date"] = pd.to_datetime(hist["date"], errors="coerce").dt.normalize()
    if history_end_date is not None:
        cutoff = pd.to_datetime(history_end_date, errors="coerce")
        hist = hist[hist["date"] <= cutoff]
    hist = hist.sort_values("date").reset_index(drop=True)
    if hist.empty:
        return None

    latest_row = hist.iloc[-1].to_dict()
    latest_feed = {key: latest_row.get(key) for key in ["pm25", "pm10", "o3", "no2", "so2", "co"]}
    cols = [col for col in ["date", "pm25", "pm10", "o3", "no2", "so2", "co"] if col in hist.columns]
    return _offline_feature_frame(hist[cols], latest_feed, station_key, metadata, pollutant=pollutant)


def _offline_recent_backtest(
    history_df: pd.DataFrame,
    metadata: Dict[str, object],
    station_key: Optional[str],
    *,
    lookback_days: int,
    pollutant: str = "pm25",
) -> Dict[str, str]:
    """Validate the offline champion on recent API-backed day-1 history."""
    target = _offline_target_feature_prefix(pollutant)
    if history_df.empty or target not in history_df.columns:
        return {"offline_recent_backtest_status": "history_unavailable"}

    hist = history_df.copy()
    hist["date"] = pd.to_datetime(hist["date"], errors="coerce").dt.normalize()
    hist[target] = pd.to_numeric(hist[target], errors="coerce")
    hist = hist.dropna(subset=["date", target]).sort_values("date").reset_index(drop=True)
    if len(hist) < 35:
        return {"offline_recent_backtest_status": "insufficient_history"}

    start_idx = max(28, len(hist) - int(lookback_days) - 1)
    actuals: list[float] = []
    preds: list[float] = []
    for idx in range(start_idx, len(hist) - 1):
        feature_frame = _offline_feature_frame_from_history(
            hist,
            metadata,
            station_key,
            history_end_date=hist.loc[idx, "date"],
            pollutant=pollutant,
        )
        prediction, _ = _predict_offline_champion_value(metadata, feature_frame, pollutant=pollutant)
        if prediction is None:
            continue
        actual = float(hist.loc[idx + 1, target])
        actuals.append(actual)
        preds.append(float(prediction))

    if not actuals:
        return {"offline_recent_backtest_status": "prediction_unavailable"}

    actual_series = pd.Series(actuals, dtype=float)
    pred_series = pd.Series(preds, dtype=float)
    mape = _series_mape(actual_series, pred_series)
    mae = float(np.mean(np.abs(actual_series - pred_series)))
    rmse = float(np.sqrt(np.mean(np.square(actual_series - pred_series))))

    diagnostics = {
        "offline_recent_backtest_status": "ready",
        "offline_recent_backtest_points": str(len(actuals)),
        "offline_recent_backtest_mae": f"{mae:.2f}",
        "offline_recent_backtest_rmse": f"{rmse:.2f}",
    }
    if mape is not None:
        diagnostics["offline_recent_backtest_mape"] = f"{mape:.1f}"
    return diagnostics


def _predict_offline_champion_value(
    metadata: Dict[str, object],
    feature_frame: pd.DataFrame,
    *,
    pollutant: str = "pm25",
) -> Tuple[Optional[float], Dict[str, str]]:
    diagnostics: Dict[str, str] = {}
    if feature_frame is None or feature_frame.empty:
        diagnostics["offline_champion_status"] = "feature_build_failed"
        return None, diagnostics

    best_model_name = str(metadata.get("best_model_name") or "").strip()
    champion_model_path = str(metadata.get("champion_model_path") or "").strip()
    if not best_model_name or not champion_model_path:
        diagnostics["offline_champion_status"] = "metadata_missing"
        return None, diagnostics

    champion_obj = _load_pickled_artifact(champion_model_path)
    if champion_obj is None:
        diagnostics["offline_champion_status"] = "champion_load_failed"
        return None, diagnostics

    def postprocess_prediction(model_name: str, raw_prediction: float) -> float:
        # Notebook tabular models were trained on log1p(target), so inference must reverse it.
        if model_name and model_name != "prophet_regressor_model":
            return float(np.expm1(raw_prediction))
        return float(raw_prediction)

    if isinstance(champion_obj, dict) and champion_obj.get("type") == "weighted_ensemble":
        predictions = []
        components = champion_obj.get("components", [])
        weights = champion_obj.get("weights", [])
        for component_name, weight in zip(components, weights):
            component_path = ARTIFACTS_DIR / f"{pollutant}_next_day_{component_name}.pkl"
            component_model = _load_pickled_artifact(str(component_path))
            if component_model is None or not hasattr(component_model, "predict"):
                diagnostics["offline_champion_status"] = "ensemble_component_missing"
                diagnostics["offline_missing_component"] = str(component_name)
                return None, diagnostics
            raw_prediction = float(np.asarray(component_model.predict(feature_frame))[0])
            prediction = postprocess_prediction(str(component_name), raw_prediction)
            predictions.append(float(weight) * prediction)
        diagnostics["offline_champion_status"] = "ready"
        diagnostics["offline_champion_name"] = best_model_name
        return float(np.sum(predictions)), diagnostics

    if hasattr(champion_obj, "predict"):
        diagnostics["offline_champion_status"] = "ready"
        diagnostics["offline_champion_name"] = best_model_name
        raw_prediction = float(np.asarray(champion_obj.predict(feature_frame))[0])
        return postprocess_prediction(best_model_name, raw_prediction), diagnostics

    diagnostics["offline_champion_status"] = "unsupported_artifact"
    return None, diagnostics


def _build_offline_champion_override(
    history_df: pd.DataFrame,
    feed_data: Dict,
    station_key: Optional[str],
    pollutant: str,
    days: int,
) -> Tuple[Optional[pd.DataFrame], Dict[str, str]]:
    diagnostics: Dict[str, str] = {}
    if days < 1:
        diagnostics["offline_champion_status"] = "not_applicable"
        return None, diagnostics

    metadata = _load_offline_champion_metadata(pollutant)
    if not metadata:
        diagnostics["offline_champion_status"] = "metadata_unavailable"
        return None, diagnostics

    trained_station_keys = _offline_station_order_set(metadata)
    normalized_station_key = normalize_station_name(station_key or "")
    if not normalized_station_key:
        diagnostics["offline_champion_status"] = "source_not_in_offline_training_scope"
        diagnostics["offline_recent_backtest_status"] = "not_applicable_for_source"
        return None, diagnostics
    if normalized_station_key not in trained_station_keys:
        # Station was not seen during training. We still attempt inference with
        # station_code=-1 (the model's out-of-scope sentinel) rather than hard-
        # rejecting. The recent_backtest gate below acts as the quality guard.
        diagnostics["offline_champion_scope_note"] = "out_of_training_scope_station_code_minus1"

    full_history_df = pd.DataFrame()
    lat = safe_float(feed_data.get("lat"))
    lon = safe_float(feed_data.get("lon"))
    if lat != 0 and lon != 0:
        try:
            full_history_df = fetch_air_quality_history(lat, lon, days=max(int(FORECAST_DEFAULTS["history_days"]), 120))
        except Exception:
            full_history_df = pd.DataFrame()

    recent_backtest = _offline_recent_backtest(
        full_history_df if not full_history_df.empty else history_df,
        metadata,
        station_key,
        lookback_days=int(FORECAST_DEFAULTS.get("offline_recent_backtest_days", 21)),
        pollutant=pollutant,
    )
    diagnostics.update(recent_backtest)
    recent_mape_raw = recent_backtest.get("offline_recent_backtest_mape")
    recent_points = int(pd.to_numeric(recent_backtest.get("offline_recent_backtest_points"), errors="coerce") or 0)
    max_recent_mape = float(FORECAST_DEFAULTS.get("offline_champion_max_recent_mape", 35.0))
    if recent_mape_raw is not None and recent_points >= 7:
        try:
            recent_mape = float(recent_mape_raw)
            if recent_mape > max_recent_mape:
                diagnostics["offline_champion_status"] = "recent_backtest_rejected"
                diagnostics["offline_champion_reject_reason"] = f"recent_mape_gt_{max_recent_mape:.1f}"
                return None, diagnostics
        except (TypeError, ValueError):
            pass

    feature_frame = (
        _offline_feature_frame_from_history(full_history_df, metadata, station_key, pollutant=pollutant)
        if not full_history_df.empty
        else _offline_feature_frame(history_df, feed_data, station_key, metadata, pollutant=pollutant)
    )
    prediction, prediction_diag = _predict_offline_champion_value(metadata, feature_frame, pollutant=pollutant)
    diagnostics.update(prediction_diag)
    if prediction is None:
        return None, diagnostics

    current_date = pd.to_datetime(history_df["date"], errors="coerce").max()
    if pd.isna(current_date):
        diagnostics["offline_champion_status"] = "history_date_missing"
        return None, diagnostics
    forecast_date = pd.to_datetime(current_date).normalize() + timedelta(days=1)
    best_model_name = str(metadata.get("best_model_name") or "")
    rmse = _offline_rmse(metadata, best_model_name)
    interval_half_width = 1.645 * float(rmse) if rmse is not None else max(prediction * 0.15, 5.0)
    override_df = pd.DataFrame({
        "date": [forecast_date],
        "value": [max(0.0, prediction)],
        "upper": [max(0.0, prediction + interval_half_width)],
        "lower": [max(0.0, prediction - interval_half_width)],
    })
    diagnostics["offline_champion_status"] = "ready"
    diagnostics["offline_champion_name"] = best_model_name
    diagnostics["offline_champion_horizon"] = "1"
    if rmse is not None:
        diagnostics["offline_champion_rmse"] = f"{rmse:.3f}"
    return override_df, diagnostics


def _apply_day_one_override(
    fc_df: pd.DataFrame,
    day_one_override: Optional[pd.DataFrame],
    diagnostics: Dict[str, str],
    model_used: str,
    data_note: str,
) -> Tuple[pd.DataFrame, Dict[str, str], str, str]:
    if day_one_override is None or day_one_override.empty or fc_df.empty:
        return fc_df, diagnostics, model_used, data_note

    forecast_df = fc_df.copy()
    forecast_df["date"] = pd.to_datetime(forecast_df["date"], errors="coerce")
    forecast_df = forecast_df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    if forecast_df.empty:
        return fc_df, diagnostics, model_used, data_note

    override_row = day_one_override.iloc[0]
    forecast_df.loc[0, ["value", "upper", "lower"]] = [
        float(override_row["value"]),
        float(override_row["upper"]),
        float(override_row["lower"]),
    ]
    diagnostics = {
        **diagnostics,
        "hybrid_base_model": model_used,
        "selection_method": diagnostics.get("selection_method", "hybrid_day1_override"),
        "selection_reason": "offline_champion_day1_override",
    }
    return (
        forecast_df,
        diagnostics,
        "OFFLINE_CHAMPION_HYBRID",
        "Day 1 uses the offline notebook champion model for the selected pollutant, while later days follow the live production forecast path.",
    )


def _load_validation_store() -> pd.DataFrame:
    try:
        if not FORECAST_VALIDATION_FILE.exists():
            return _empty_validation_frame()
        payload = json.loads(FORECAST_VALIDATION_FILE.read_text(encoding="utf-8"))
        records = payload.get("records", []) if isinstance(payload, dict) else []
        df = pd.DataFrame(records)
        if df.empty:
            return _empty_validation_frame()
        for col in ["generated_on", "target_date"]:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.normalize()
        for col in ["predicted_value", "actual_value", "horizon_day"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna(subset=["source_key", "pollutant", "generated_on", "target_date", "predicted_value"])
    except Exception:
        return _empty_validation_frame()


def _save_validation_store(df: pd.DataFrame) -> None:
    try:
        FORECAST_VALIDATION_FILE.parent.mkdir(parents=True, exist_ok=True)
        serializable = df.copy()
        for col in ["generated_on", "target_date"]:
            serializable[col] = pd.to_datetime(serializable[col], errors="coerce").dt.strftime("%Y-%m-%d")
        FORECAST_VALIDATION_FILE.write_text(
            json.dumps({"records": serializable.to_dict(orient="records")}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _source_key(lat: float, lon: float, station_key: Optional[str], station_name: Optional[str]) -> str:
    if station_key:
        return f"station:{normalize_station_name(station_key)}"
    normalized_name = normalize_station_name(station_name or "")
    if normalized_name:
        return f"city:{normalized_name}"
    return f"geo:{lat:.4f}:{lon:.4f}"


def _validation_scope_key(source_key: str, model_used: str, diagnostics: Dict[str, str]) -> str:
    """Keep validation histories model-aware so hybrid, Prophet, and fallback paths do not pollute each other."""
    offline_name = str(diagnostics.get("offline_champion_name") or "").strip()
    hybrid_base_model = str(diagnostics.get("hybrid_base_model") or "").strip()
    if model_used == "OFFLINE_CHAMPION_HYBRID":
        champion_label = offline_name or "offline_champion"
        base_label = hybrid_base_model or "hybrid_base"
        return f"{source_key}|model:{model_used}|champion:{champion_label}|base:{base_label}"
    return f"{source_key}|model:{model_used}"


def _build_synthetic_history(base_val: float, pollutant: str, history_days: int, label: str) -> pd.DataFrame:
    seed = abs(hash(f"{pollutant}:{label}")) % 99991
    rng = np.random.RandomState(seed)
    base_val = max(2.0, float(base_val or 20.0))
    trend_component = np.linspace(0, base_val * 0.08, history_days)
    weekly_season = np.sin(np.linspace(0, 2 * np.pi * (history_days / 7), history_days)) * base_val * 0.18
    weekend_effect = np.array([(0.12 if i % 7 in (5, 6) else -0.06) * base_val for i in range(history_days)])
    noise = rng.normal(0, base_val * 0.07, history_days)
    hist_vals = np.clip(base_val + trend_component + weekly_season + weekend_effect + noise, 0.5, None)
    hist_dates = pd.date_range(end=pd.Timestamp.utcnow().normalize(), periods=history_days, freq="D")
    return pd.DataFrame({"date": hist_dates, pollutant: hist_vals})


def _update_validation_metrics(
    source_key: str,
    pollutant: str,
    hist_df: pd.DataFrame,
    fc_df: pd.DataFrame,
    *,
    model_used: str,
    diagnostics: Optional[Dict[str, str]] = None,
) -> Tuple[pd.DataFrame, Optional[float], Dict[str, str]]:
    diagnostics = dict(diagnostics or {})
    validation_key = _validation_scope_key(source_key, model_used, diagnostics)
    min_points = int(FORECAST_DEFAULTS.get("min_validation_points_for_accuracy", 7))
    store = _load_validation_store()
    if not hist_df.empty and pollutant in hist_df.columns:
        actual_map = hist_df.copy()
        actual_map["date"] = pd.to_datetime(actual_map["date"], errors="coerce").dt.normalize()
        actual_map[pollutant] = pd.to_numeric(actual_map[pollutant], errors="coerce")
        actual_map = actual_map.dropna(subset=["date", pollutant]).drop_duplicates(subset=["date"], keep="last")
        if not actual_map.empty and not store.empty:
            actual_lookup = actual_map.set_index("date")[pollutant].to_dict()
            mask = (store["source_key"] == validation_key) & (store["pollutant"] == pollutant)
            for idx, row in store.loc[mask].iterrows():
                actual_value = actual_lookup.get(row["target_date"])
                if actual_value is not None and not pd.isna(actual_value):
                    store.at[idx, "actual_value"] = float(actual_value)

    if not fc_df.empty:
        generated_on = pd.Timestamp.now().normalize()
        new_rows = pd.DataFrame({
            "source_key": validation_key,
            "pollutant": pollutant,
            "generated_on": generated_on,
            "target_date": pd.to_datetime(fc_df["date"], errors="coerce").dt.normalize(),
            "predicted_value": pd.to_numeric(fc_df["value"], errors="coerce"),
            "actual_value": np.nan,
            "horizon_day": np.arange(1, len(fc_df) + 1),
        }).dropna(subset=["target_date", "predicted_value"])
        store = pd.concat([store, new_rows], ignore_index=True)
        # Sort so that rows with actual_value come LAST within each group
        # (NaN sorts last in ascending, so sort actual_value ascending puts
        # non-NaN first; we then keep='last' to get the non-NaN row).
        # This prevents new fc_df rows (actual_value=NaN) from overwriting
        # already-validated backfill rows that have real actual_value.
        store = store.sort_values(
            ["source_key", "pollutant", "generated_on", "target_date", "actual_value"],
            na_position="first",   # NaN first → non-NaN last → keep='last' wins
        )
        store = store.drop_duplicates(
            subset=["source_key", "pollutant", "generated_on", "target_date"],
            keep="last",
        )

    if not store.empty:
        store["target_date"] = pd.to_datetime(store["target_date"], errors="coerce").dt.tz_localize(None)
        cutoff = pd.Timestamp.now().normalize() - pd.Timedelta(days=int(FORECAST_DEFAULTS["validation_retention_days"]))
        store = store[store["target_date"] >= cutoff].copy()

    _save_validation_store(store)

    comparison = store[
        (store["source_key"] == validation_key) &
        (store["pollutant"] == pollutant) &
        store["actual_value"].notna()
    ].copy()
    comparison = comparison.sort_values(["target_date", "generated_on"])
    comparison = comparison.drop_duplicates(subset=["target_date"], keep="last").tail(7)

    accuracy_pct: Optional[float] = None
    if not comparison.empty:
        # Clamp very small denominators so percent errors do not explode for low-pollution days.
        denom = comparison["actual_value"].abs().clip(lower=5.0).replace(0, np.nan)
        ape = ((comparison["actual_value"] - comparison["predicted_value"]).abs() / denom) * 100.0
        ape = ape.replace([np.inf, -np.inf], np.nan).dropna()
        if not ape.empty:
            mape = float(ape.mean())
            diagnostics["validated_points"] = str(len(ape))
            diagnostics["validation_scope_key"] = validation_key
            if len(ape) >= min_points:
                diagnostics["mape"] = f"{mape:.1f}"
                accuracy_pct = max(0.0, 100.0 - mape)
            else:
                diagnostics["accuracy_pending_until_points"] = str(min_points)

    if accuracy_pct is None:
        backtest = _history_backtest_accuracy(hist_df, pollutant, min_points=min_points)
        if backtest:
            diagnostics.update({k: str(v) for k, v in backtest.items() if k != "history_backtest_accuracy_pct"})
            accuracy_pct = float(backtest["history_backtest_accuracy_pct"])

    past_forecast_df = comparison.rename(columns={
        "target_date": "date",
        "predicted_value": "forecast_value",
        "actual_value": "actual_value",
    })[["date", "forecast_value", "actual_value"]].copy() if not comparison.empty else pd.DataFrame(
        columns=["date", "forecast_value", "actual_value"]
    )
    return past_forecast_df, accuracy_pct, diagnostics


def parse_waqi_daily_forecast(forecast_raw, pollutant: str):
    if not forecast_raw:
        return []
    try:
        if isinstance(forecast_raw, dict):
            data = forecast_raw.get(pollutant, [])
            if isinstance(data, dict):
                data = data.get("data", [])
            return data if isinstance(data, list) else []
        return []
    except Exception:
        return []


def _prepare_history_frame(df: pd.DataFrame, pollutant: str, history_days: int) -> pd.DataFrame:
    if df.empty or pollutant not in df.columns:
        return pd.DataFrame(columns=["date", pollutant])
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize()
    out[pollutant] = pd.to_numeric(out[pollutant], errors="coerce")
    out = out.dropna(subset=["date", pollutant]).sort_values("date")
    if out.empty:
        return pd.DataFrame(columns=["date", pollutant])
    out = out.groupby("date", as_index=False)[pollutant].mean()
    return out.tail(history_days).reset_index(drop=True)


def _is_flat_series(df: pd.DataFrame, pollutant: str) -> bool:
    if df.empty or pollutant not in df.columns:
        return True
    series = pd.to_numeric(df[pollutant], errors="coerce").dropna()
    if len(series) < 3:
        return True
    return float(series.nunique()) <= 2 or float(series.std()) < 0.01


def _holt_winters(series: np.ndarray, days: int, season_len: int = 7):
    n = len(series)
    alpha, beta, gamma = 0.35, 0.10, 0.25
    if n < 2 * season_len:
        level = series[0]
        trend = (series[-1] - series[0]) / max(n - 1, 1) * 0.3
        preds = []
        for i in range(days):
            level_new = alpha * (level + trend) + (1 - alpha) * level
            trend = beta * (level_new - level) + (1 - beta) * trend
            level = level_new
            preds.append(max(0, level + trend * (i + 1)))
        residuals = np.std(np.diff(series)) if len(series) > 1 else np.nanmean(series) * 0.1
        sigma = max(float(residuals or 0), float(np.nanmean(series) or 1) * 0.05)
        ci = 1.645 * sigma
        preds = np.array(preds)
        upper = preds + ci * np.sqrt(np.arange(1, days + 1))
        lower = np.clip(preds - ci * np.sqrt(np.arange(1, days + 1)), 0, None)
        return preds, upper, lower

    seasons = np.array([np.nanmean(series[i::season_len]) for i in range(season_len)])
    baseline = np.nanmean(series)
    seasons = seasons - baseline
    level = series[0]
    trend = (series[min(season_len, n - 1)] - series[0]) / season_len * 0.2
    smoothed = []
    for t in range(n):
        s_t = seasons[t % season_len]
        prev_level = level
        level = alpha * (series[t] - s_t) + (1 - alpha) * (level + trend)
        trend = beta * (level - prev_level) + (1 - beta) * trend
        seasons[t % season_len] = gamma * (series[t] - level) + (1 - gamma) * s_t
        smoothed.append(level + trend + seasons[t % season_len])
    residuals = series - np.array(smoothed)
    sigma = float(np.nanstd(residuals) or max(np.nanmean(series) * 0.05, 1.0))
    preds, upper, lower = [], [], []
    for h in range(1, days + 1):
        s_h = seasons[(n + h - 1) % season_len]
        fc = level + trend * h + s_h
        ci = 1.645 * sigma * np.sqrt(h)
        preds.append(max(0, fc))
        upper.append(max(0, fc + ci))
        lower.append(max(0, fc - ci))
    return np.array(preds), np.array(upper), np.array(lower)


def _merge_history_with_weather(history_df: pd.DataFrame, weather_df: pd.DataFrame, pollutant: str) -> pd.DataFrame:
    if history_df.empty:
        return pd.DataFrame(columns=["ds", "y"] + WEATHER_REGRESSORS)
    hist = history_df.copy()
    hist["date"] = pd.to_datetime(hist["date"], errors="coerce").dt.normalize()
    if pollutant not in hist.columns:
        hist[pollutant] = np.nan
    hist[pollutant] = pd.to_numeric(hist[pollutant], errors="coerce")
    merged = hist[["date", pollutant]].merge(weather_df, on="date", how="left")
    merged = merged.rename(columns={"date": "ds", pollutant: "y"})
    for col in WEATHER_REGRESSORS:
        if col not in merged.columns:
            merged[col] = np.nan
        merged[col] = pd.to_numeric(merged[col], errors="coerce")
        merged[col] = merged[col].interpolate(limit_direction="both").bfill().ffill()
    merged = merged.dropna(subset=["ds", "y"]).sort_values("ds").reset_index(drop=True)
    return merged


def _prophet_forecast(train_df: pd.DataFrame, future_weather: pd.DataFrame, days: int, pollutant: str) -> Tuple[pd.DataFrame, Dict[str, str]]:
    from prophet import Prophet

    profile = _forecast_profile(pollutant)
    model = Prophet(
        daily_seasonality=False,
        weekly_seasonality=True,
        yearly_seasonality=False,
        seasonality_mode=str(profile["seasonality_mode"]),
        changepoint_prior_scale=float(profile["changepoint_prior_scale"]),
        seasonality_prior_scale=float(profile["seasonality_prior_scale"]),
        interval_width=FORECAST_DEFAULTS["interval_width"],
    )
    for reg in WEATHER_REGRESSORS:
        model.add_regressor(reg, mode="additive")
    model.fit(train_df[["ds", "y"] + WEATHER_REGRESSORS])
    future = future_weather.copy()
    future["ds"] = pd.to_datetime(future["date"])
    forecast = model.predict(future[["ds"] + WEATHER_REGRESSORS])
    fc_df = pd.DataFrame({
        "date": future["ds"],
        "value": forecast["yhat"].clip(lower=0),
        "upper": forecast["yhat_upper"].clip(lower=0),
        "lower": forecast["yhat_lower"].clip(lower=0),
    }).head(days)
    diagnostics = {
        "training_rows": str(len(train_df)),
        "future_rows": str(len(future)),
        "regressors": ", ".join(WEATHER_REGRESSORS),
        "profile_pollutant": pollutant,
    }
    return fc_df, diagnostics


def _holt_winters_forecast(train_df: pd.DataFrame, days: int, pollutant: str) -> Tuple[pd.DataFrame, Dict[str, str]]:
    profile = _forecast_profile(pollutant)
    series = train_df["y"].astype(float).to_numpy()
    vals, upper, lower = _holt_winters(series, days, season_len=int(profile["holt_winters_season_length"]))
    start = pd.to_datetime(train_df["ds"].max()) + timedelta(days=1)
    dates = pd.date_range(start=start, periods=days, freq="D")
    fc_df = pd.DataFrame({"date": dates, "value": vals, "upper": upper, "lower": lower})
    diagnostics = {"training_rows": str(len(train_df)), "fallback": "holt_winters", "profile_pollutant": pollutant}
    return fc_df, diagnostics


def _single_point_forecast(train_df: pd.DataFrame, days: int, value_col: str = "y", date_col: str = "ds") -> Tuple[pd.DataFrame, Dict[str, str]]:
    last_value = float(pd.to_numeric(train_df[value_col], errors="coerce").dropna().iloc[-1])
    start_date = pd.to_datetime(train_df[date_col].max()) + timedelta(days=1)
    fc_df = pd.DataFrame({
        "date": pd.date_range(start=start_date, periods=days, freq="D"),
        "value": np.full(days, last_value),
        "upper": np.full(days, last_value * 1.10),
        "lower": np.full(days, max(0.0, last_value * 0.90)),
    })
    return fc_df, {"training_rows": str(len(train_df)), "fallback": "persistence_single_point"}


def _series_mape(actual: pd.Series, predicted: pd.Series) -> Optional[float]:
    actual_series = pd.to_numeric(actual, errors="coerce")
    predicted_series = pd.to_numeric(predicted, errors="coerce")
    denom = actual_series.abs().replace(0, np.nan)
    ape = ((actual_series - predicted_series).abs() / denom) * 100.0
    ape = ape.replace([np.inf, -np.inf], np.nan).dropna()
    if ape.empty:
        return None
    return float(ape.mean())


def _history_backtest_accuracy(
    history_df: pd.DataFrame,
    pollutant: str,
    *,
    min_points: int = 21,
) -> Dict[str, str | float]:
    if history_df.empty or pollutant not in history_df.columns:
        return {}
    hist = history_df.copy()
    hist["date"] = pd.to_datetime(hist["date"], errors="coerce").dt.normalize()
    hist[pollutant] = pd.to_numeric(hist[pollutant], errors="coerce")
    hist = hist.dropna(subset=["date", pollutant]).sort_values("date")
    if len(hist) < max(min_points, 30):
        return {}

    daily = hist[["date", pollutant]].drop_duplicates(subset=["date"], keep="last").set_index("date")[pollutant]
    predicted = daily.shift(1).rolling(3, min_periods=1).mean().bfill()
    aligned = pd.DataFrame({"actual": daily, "predicted": predicted}).dropna().tail(30)
    if len(aligned) < min_points:
        return {}

    denom = aligned["actual"].abs().clip(lower=5.0).replace(0, np.nan)
    ape = ((aligned["actual"] - aligned["predicted"]).abs() / denom) * 100.0
    ape = ape.replace([np.inf, -np.inf], np.nan).dropna()
    if len(ape) < min_points:
        return {}

    mape = float(ape.mean())
    return {
        "history_backtest_mape": f"{mape:.1f}",
        "history_backtest_points": str(len(ape)),
        "history_backtest_accuracy_pct": max(0.0, 100.0 - mape),
    }


def _build_tabular_training_frame(
    history_df: pd.DataFrame,
    weather_df: pd.DataFrame,
    pollutant: str,
) -> pd.DataFrame:
    """Create leakage-safe supervised features for single-series daily forecasting."""
    if history_df.empty:
        return pd.DataFrame()

    hist = history_df.copy()
    hist["date"] = pd.to_datetime(hist["date"], errors="coerce").dt.normalize()
    hist[pollutant] = pd.to_numeric(hist[pollutant], errors="coerce")
    hist = hist.dropna(subset=["date", pollutant]).sort_values("date")
    if hist.empty:
        return pd.DataFrame()

    weather = weather_df.copy() if not weather_df.empty else pd.DataFrame({"date": hist["date"]})
    weather["date"] = pd.to_datetime(weather["date"], errors="coerce").dt.normalize()
    weather = weather.dropna(subset=["date"]).sort_values("date")
    for col in WEATHER_REGRESSORS:
        if col not in weather.columns:
            weather[col] = np.nan
        weather[col] = pd.to_numeric(weather[col], errors="coerce")
        weather[col] = weather[col].interpolate(limit_direction="both").bfill().ffill()

    df = hist[["date", pollutant]].merge(weather[["date"] + WEATHER_REGRESSORS], on="date", how="left")
    df = df.sort_values("date").reset_index(drop=True)

    target_series = df[pollutant].astype(float)
    for lag in (1, 2, 3, 7, 14, 21, 28):
        df[f"lag_{lag}"] = target_series.shift(lag)
    for window in (3, 7, 14, 21, 28):
        shifted = target_series.shift(1)
        df[f"roll_mean_{window}"] = shifted.rolling(window, min_periods=max(2, window // 2)).mean()
        df[f"roll_std_{window}"] = shifted.rolling(window, min_periods=max(2, window // 2)).std()
        df[f"roll_min_{window}"] = shifted.rolling(window, min_periods=max(2, window // 2)).min()
        df[f"roll_max_{window}"] = shifted.rolling(window, min_periods=max(2, window // 2)).max()

    df["lag_diff_1_7"] = df["lag_1"] - df["lag_7"]
    df["lag_diff_7_14"] = df["lag_7"] - df["lag_14"]
    df["dow"] = df["date"].dt.dayofweek
    df["month"] = df["date"].dt.month
    df["day_of_year"] = df["date"].dt.dayofyear
    df["is_weekend"] = (df["dow"] >= 5).astype(int)
    df["dow_sin"] = np.sin(2 * np.pi * df["dow"] / 7.0)
    df["dow_cos"] = np.cos(2 * np.pi * df["dow"] / 7.0)
    df["month_sin"] = np.sin(2 * np.pi * df["month"] / 12.0)
    df["month_cos"] = np.cos(2 * np.pi * df["month"] / 12.0)
    df["doy_sin"] = np.sin(2 * np.pi * df["day_of_year"] / 365.25)
    df["doy_cos"] = np.cos(2 * np.pi * df["day_of_year"] / 365.25)
    df["target"] = target_series
    return df


def _tabular_feature_columns(df: pd.DataFrame) -> list[str]:
    excluded = {"date", "target"}
    return [col for col in df.columns if col not in excluded and pd.api.types.is_numeric_dtype(df[col])]


def _tabular_forecast(
    history_df: pd.DataFrame,
    weather_history_df: pd.DataFrame,
    future_weather: pd.DataFrame,
    days: int,
    pollutant: str,
) -> Tuple[pd.DataFrame, Dict[str, str]]:
    """Recursive gradient-boosting forecast using lag, rolling, calendar, and weather features."""
    from sklearn.ensemble import HistGradientBoostingRegressor

    training_frame = _build_tabular_training_frame(history_df, weather_history_df, pollutant)
    if training_frame.empty:
        raise ValueError("tabular_training_frame_empty")

    feature_cols = _tabular_feature_columns(training_frame)
    model_frame = training_frame.dropna(subset=["target"]).copy()
    model_frame = model_frame.dropna(subset=[col for col in feature_cols if col.startswith("lag_")])
    if len(model_frame) < max(int(_forecast_profile(pollutant)["min_train_days"]), 35):
        raise ValueError("tabular_insufficient_training_rows")

    x_train = model_frame[feature_cols].apply(pd.to_numeric, errors="coerce")
    fill_values = x_train.median(numeric_only=True).fillna(0.0)
    x_train = x_train.fillna(fill_values).fillna(0.0)
    y_train = pd.to_numeric(model_frame["target"], errors="coerce")

    model = HistGradientBoostingRegressor(
        learning_rate=0.05,
        max_depth=6,
        max_iter=300,
        min_samples_leaf=12,
        l2_regularization=0.05,
        random_state=42,
    )
    model.fit(x_train, y_train)

    residuals = y_train - model.predict(x_train)
    sigma = float(np.nanstd(residuals) or max(float(np.nanmean(y_train) or 1.0) * 0.08, 1.0))

    hist_values = history_df.copy()
    hist_values["date"] = pd.to_datetime(hist_values["date"], errors="coerce").dt.normalize()
    hist_values[pollutant] = pd.to_numeric(hist_values[pollutant], errors="coerce")
    hist_values = hist_values.dropna(subset=["date", pollutant]).sort_values("date").tail(90)

    future = future_weather.copy().head(days)
    future["date"] = pd.to_datetime(future["date"], errors="coerce").dt.normalize()
    for col in WEATHER_REGRESSORS:
        if col not in future.columns:
            future[col] = np.nan
        future[col] = pd.to_numeric(future[col], errors="coerce")
        future[col] = future[col].interpolate(limit_direction="both").bfill().ffill()

    predictions: list[float] = []
    uppers: list[float] = []
    lowers: list[float] = []
    pred_dates: list[pd.Timestamp] = []

    for horizon, (_, row) in enumerate(future.iterrows(), start=1):
        series = hist_values[pollutant].astype(float).to_numpy()
        current_date = pd.to_datetime(row["date"]).normalize()
        features = {
            "lag_1": float(series[-1]) if len(series) >= 1 else np.nan,
            "lag_2": float(series[-2]) if len(series) >= 2 else np.nan,
            "lag_3": float(series[-3]) if len(series) >= 3 else np.nan,
            "lag_7": float(series[-7]) if len(series) >= 7 else np.nan,
            "lag_14": float(series[-14]) if len(series) >= 14 else np.nan,
            "lag_21": float(series[-21]) if len(series) >= 21 else np.nan,
            "lag_28": float(series[-28]) if len(series) >= 28 else np.nan,
        }
        for window in (3, 7, 14, 21, 28):
            recent = series[-window:] if len(series) >= window else series
            if len(recent) == 0:
                features[f"roll_mean_{window}"] = np.nan
                features[f"roll_std_{window}"] = np.nan
                features[f"roll_min_{window}"] = np.nan
                features[f"roll_max_{window}"] = np.nan
            else:
                features[f"roll_mean_{window}"] = float(np.nanmean(recent))
                features[f"roll_std_{window}"] = float(np.nanstd(recent))
                features[f"roll_min_{window}"] = float(np.nanmin(recent))
                features[f"roll_max_{window}"] = float(np.nanmax(recent))
        features["lag_diff_1_7"] = features["lag_1"] - features["lag_7"] if not pd.isna(features["lag_7"]) else np.nan
        features["lag_diff_7_14"] = features["lag_7"] - features["lag_14"] if not pd.isna(features["lag_14"]) else np.nan
        features["dow"] = current_date.dayofweek
        features["month"] = current_date.month
        features["day_of_year"] = current_date.dayofyear
        features["is_weekend"] = 1 if current_date.dayofweek >= 5 else 0
        features["dow_sin"] = math.sin(2 * math.pi * current_date.dayofweek / 7.0)
        features["dow_cos"] = math.cos(2 * math.pi * current_date.dayofweek / 7.0)
        features["month_sin"] = math.sin(2 * math.pi * current_date.month / 12.0)
        features["month_cos"] = math.cos(2 * math.pi * current_date.month / 12.0)
        features["doy_sin"] = math.sin(2 * math.pi * current_date.dayofyear / 365.25)
        features["doy_cos"] = math.cos(2 * math.pi * current_date.dayofyear / 365.25)
        for col in WEATHER_REGRESSORS:
            features[col] = float(pd.to_numeric(row.get(col), errors="coerce")) if not pd.isna(pd.to_numeric(row.get(col), errors="coerce")) else np.nan

        x_future = pd.DataFrame([features], columns=feature_cols)
        x_future = x_future.fillna(fill_values).fillna(0.0)
        pred = max(0.0, float(model.predict(x_future)[0]))
        ci = 1.645 * sigma * math.sqrt(horizon)
        predictions.append(pred)
        uppers.append(max(0.0, pred + ci))
        lowers.append(max(0.0, pred - ci))
        pred_dates.append(current_date)
        hist_values = pd.concat([hist_values, pd.DataFrame({"date": [current_date], pollutant: [pred]})], ignore_index=True)

    fc_df = pd.DataFrame({"date": pred_dates, "value": predictions, "upper": uppers, "lower": lowers})
    diagnostics = {
        "training_rows": str(len(model_frame)),
        "future_rows": str(len(future)),
        "regressors": ", ".join(WEATHER_REGRESSORS),
        "tabular_feature_count": str(len(feature_cols)),
        "tabular_model": "hist_gradient_boosting",
    }
    return fc_df, diagnostics


def _backtest_candidate_models(train_df: pd.DataFrame, pollutant: str) -> Dict[str, str]:
    """Compare candidate models on the latest holdout window when enough data exists."""
    profile = _forecast_profile(pollutant)
    backtest_days = int(min(FORECAST_DEFAULTS["forecast_backtest_days"], max(len(train_df) // 4, 3)))
    min_train_days = int(profile["min_train_days"])
    if len(train_df) < max(min_train_days + backtest_days, 18):
        return {"selected_model": "", "selection_reason": "insufficient_rows_for_backtest"}

    dev_df = train_df.iloc[:-backtest_days].copy()
    holdout_df = train_df.iloc[-backtest_days:].copy()
    if len(dev_df) < max(min_train_days, 12) or holdout_df.empty:
        return {"selected_model": "", "selection_reason": "insufficient_holdout_window"}

    diagnostics: Dict[str, str] = {
        "selection_method": "recent_holdout_backtest",
        "holdout_days": str(len(holdout_df)),
    }

    hw_fc, _ = _holt_winters_forecast(dev_df, len(holdout_df), pollutant)
    hw_mape = _series_mape(holdout_df["y"], hw_fc["value"])
    if hw_mape is not None:
        diagnostics["holt_winters_backtest_mape"] = f"{hw_mape:.1f}"

    prophet_mape: Optional[float] = None
    if PROPHET_AVAILABLE:
        future_weather = holdout_df.rename(columns={"ds": "date"})[["date"] + WEATHER_REGRESSORS].copy()
        try:
            prophet_fc, _ = _prophet_forecast(dev_df, future_weather, len(holdout_df), pollutant)
            prophet_mape = _series_mape(holdout_df["y"], prophet_fc["value"])
            if prophet_mape is not None:
                diagnostics["prophet_backtest_mape"] = f"{prophet_mape:.1f}"
        except (ValueError, KeyError):
            prophet_mape = None

    tabular_mape: Optional[float] = None
    try:
        dev_history = dev_df.rename(columns={"ds": "date", "y": pollutant})[["date", pollutant]].copy()
        dev_weather = dev_df.rename(columns={"ds": "date"})[["date"] + WEATHER_REGRESSORS].copy()
        holdout_weather = holdout_df.rename(columns={"ds": "date"})[["date"] + WEATHER_REGRESSORS].copy()
        tabular_fc, _ = _tabular_forecast(dev_history, dev_weather, holdout_weather, len(holdout_df), pollutant)
        tabular_mape = _series_mape(holdout_df["y"], tabular_fc["value"])
        if tabular_mape is not None:
            diagnostics["tabular_backtest_mape"] = f"{tabular_mape:.1f}"
    except (ValueError, KeyError):
        tabular_mape = None

    candidates = {
        "PROPHET_WEATHER": prophet_mape,
        "HOLT_WINTERS": hw_mape,
        "TABULAR_GRADIENT_BOOSTING": tabular_mape,
    }
    available = {name: score for name, score in candidates.items() if score is not None}
    if available:
        best_model, best_score = min(available.items(), key=lambda item: float(item[1]))
        diagnostics["selected_model"] = best_model
        diagnostics["selection_reason"] = "lower_recent_mape"
        diagnostics["selected_model_backtest_mape"] = f"{best_score:.1f}"
    else:
        diagnostics["selected_model"] = ""
        diagnostics["selection_reason"] = "candidate_backtest_failed"
    return diagnostics


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(max(a, 0.0)))


def _nearest_station_key(lat: float, lon: float, max_km: float = 60.0) -> Optional[str]:
    try:
        if not STATIONS_FILE.exists() or lat == 0 or lon == 0:
            return None
        stations = pd.read_csv(STATIONS_FILE)
        if stations.empty or "station_key" not in stations.columns:
            return None
        stations["latitude"] = pd.to_numeric(stations["latitude"], errors="coerce")
        stations["longitude"] = pd.to_numeric(stations["longitude"], errors="coerce")
        stations = stations.dropna(subset=["latitude", "longitude", "station_key"]).copy()
        if stations.empty:
            return None
        stations["distance_km"] = stations.apply(
            lambda r: _haversine_km(lat, lon, float(r["latitude"]), float(r["longitude"])),
            axis=1,
        )
        best = stations.sort_values("distance_km").iloc[0]
        if float(best["distance_km"]) <= max_km:
            return normalize_station_name(str(best["station_key"]))
    except Exception:
        return None
    return None


def _resolve_station_key(station_name: Optional[str], explicit_station_key: Optional[str],
                         lat: float = 0.0, lon: float = 0.0) -> Optional[str]:
    if explicit_station_key:
        return normalize_station_name(explicit_station_key)
    candidate = normalize_station_name(station_name or "") or None
    nearest = _nearest_station_key(lat, lon)
    if nearest:
        if not candidate or candidate in {"istanbultr", "global", "selectedstation"}:
            return nearest
        if candidate.startswith("istanbul") and nearest.startswith("istanbul"):
            return nearest
    return candidate


def _station_metadata_frame() -> pd.DataFrame:
    try:
        if not STATIONS_FILE.exists():
            return pd.DataFrame(columns=["station_key", "latitude", "longitude"])
        stations = pd.read_csv(STATIONS_FILE)
        if stations.empty or "station_key" not in stations.columns:
            return pd.DataFrame(columns=["station_key", "latitude", "longitude"])
        stations["station_key"] = stations["station_key"].map(normalize_station_name)
        stations["latitude"] = pd.to_numeric(stations.get("latitude"), errors="coerce")
        stations["longitude"] = pd.to_numeric(stations.get("longitude"), errors="coerce")
        return stations.dropna(subset=["station_key"]).copy()
    except Exception:
        return pd.DataFrame(columns=["station_key", "latitude", "longitude"])


def _neighbor_station_keys(
    station_key: Optional[str],
    *,
    lat: float,
    lon: float,
    history_df: pd.DataFrame,
    max_neighbors: int = 3,
    max_distance_km: float = 80.0,
) -> list[str]:
    normalized_key = normalize_station_name(station_key or "")
    if history_df.empty:
        return []
    available = set(history_df["station_key"].astype(str).map(normalize_station_name))
    meta = _station_metadata_frame()
    if meta.empty:
        return []
    meta = meta[meta["station_key"].isin(available)].copy()
    if meta.empty:
        return []

    if normalized_key and normalized_key in set(meta["station_key"]):
        anchor = meta.loc[meta["station_key"] == normalized_key].head(1)
        anchor_lat = float(anchor["latitude"].iloc[0])
        anchor_lon = float(anchor["longitude"].iloc[0])
    elif lat != 0 and lon != 0:
        anchor_lat = float(lat)
        anchor_lon = float(lon)
    else:
        return []

    meta["distance_km"] = meta.apply(
        lambda row: _haversine_km(anchor_lat, anchor_lon, float(row["latitude"]), float(row["longitude"])),
        axis=1,
    )
    meta = meta[(meta["distance_km"] > 0) & (meta["distance_km"] <= max_distance_km)].copy()
    if normalized_key:
        meta = meta[meta["station_key"] != normalized_key].copy()
    if meta.empty:
        return []
    return meta.sort_values("distance_km")["station_key"].head(max_neighbors).tolist()


def _merge_neighbor_history_features(
    history_df: pd.DataFrame,
    target_history_df: pd.DataFrame,
    *,
    pollutant: str,
    station_key: Optional[str],
    lat: float,
    lon: float,
) -> pd.DataFrame:
    if target_history_df.empty:
        return target_history_df.copy()
    all_history = history_df.copy()
    all_history["station_key"] = all_history["station_key"].astype(str).map(normalize_station_name)
    all_history["date"] = pd.to_datetime(all_history["date"], errors="coerce").dt.normalize()
    all_history[pollutant] = pd.to_numeric(all_history[pollutant], errors="coerce")
    neighbor_keys = _neighbor_station_keys(
        station_key,
        lat=lat,
        lon=lon,
        history_df=all_history[["station_key", "date", pollutant]],
    )
    if not neighbor_keys:
        return target_history_df.copy()

    merged = target_history_df.copy()
    diagnostics_cols: list[str] = []
    for idx, neighbor_key in enumerate(neighbor_keys, start=1):
        neighbor_df = (
            all_history[all_history["station_key"] == neighbor_key][["date", pollutant]]
            .rename(columns={pollutant: f"neighbor_{idx}_value"})
            .sort_values("date")
        )
        if neighbor_df.empty:
            continue
        merged = merged.merge(neighbor_df, on="date", how="left")
        value_col = f"neighbor_{idx}_value"
        merged[value_col] = pd.to_numeric(merged[value_col], errors="coerce")
        merged[f"neighbor_{idx}_lag_1"] = merged[value_col].shift(1)
        merged[f"neighbor_{idx}_roll_mean_7"] = merged[value_col].shift(1).rolling(7, min_periods=2).mean()
        merged[f"neighbor_{idx}_roll_std_7"] = merged[value_col].shift(1).rolling(7, min_periods=2).std()
        diagnostics_cols.append(neighbor_key)

    usable_neighbor_cols = [col for col in merged.columns if col.startswith("neighbor_")]
    if usable_neighbor_cols:
        merged["neighbor_mean_current"] = pd.to_numeric(merged.filter(regex=r"^neighbor_\d+_value$").mean(axis=1), errors="coerce")
        merged["neighbor_mean_lag_1"] = pd.to_numeric(merged.filter(regex=r"^neighbor_\d+_lag_1$").mean(axis=1), errors="coerce")
        merged["neighbor_mean_roll_7"] = pd.to_numeric(merged.filter(regex=r"^neighbor_\d+_roll_mean_7$").mean(axis=1), errors="coerce")
    merged.attrs["neighbor_station_keys"] = diagnostics_cols
    return merged


def _build_direct_multi_horizon_training_frame(
    history_df: pd.DataFrame,
    weather_df: pd.DataFrame,
    *,
    pollutant: str,
    station_key: Optional[str],
    lat: float,
    lon: float,
    horizons: list[int],
) -> pd.DataFrame:
    base = _build_tabular_training_frame(history_df, weather_df, pollutant)
    if base.empty:
        return pd.DataFrame()

    merged = _merge_neighbor_history_features(
        load_all_raw_station_histories(),
        base,
        pollutant=pollutant,
        station_key=station_key,
        lat=lat,
        lon=lon,
    )
    if merged.empty:
        return pd.DataFrame()

    for horizon in horizons:
        merged[f"target_t{horizon}"] = pd.to_numeric(merged["target"], errors="coerce").shift(-int(horizon))
        for col in WEATHER_REGRESSORS:
            if col in merged.columns:
                merged[f"{col}_future_t{horizon}"] = pd.to_numeric(merged[col], errors="coerce").shift(-int(horizon))

    return merged


def _direct_multi_horizon_forecast(
    history_df: pd.DataFrame,
    weather_history_df: pd.DataFrame,
    future_weather: pd.DataFrame,
    *,
    days: int,
    pollutant: str,
    station_key: Optional[str],
    lat: float,
    lon: float,
) -> Tuple[pd.DataFrame, Dict[str, str]]:
    from sklearn.ensemble import GradientBoostingRegressor

    horizons = list(range(1, int(days) + 1))
    training_frame = _build_direct_multi_horizon_training_frame(
        history_df,
        weather_history_df,
        pollutant=pollutant,
        station_key=station_key,
        lat=lat,
        lon=lon,
        horizons=horizons,
    )
    if training_frame.empty:
        raise ValueError("direct_training_frame_empty")

    future = future_weather.copy().head(days)
    future["date"] = pd.to_datetime(future["date"], errors="coerce").dt.normalize()
    for col in WEATHER_REGRESSORS:
        if col not in future.columns:
            future[col] = np.nan
        future[col] = pd.to_numeric(future[col], errors="coerce").interpolate(limit_direction="both").bfill().ffill()
    if future.empty:
        raise ValueError("direct_future_weather_empty")

    latest_row = training_frame.dropna(subset=["target"]).sort_values("date").tail(1)
    if latest_row.empty:
        raise ValueError("direct_latest_row_missing")

    feature_excluded = {"date", "target", *{f"target_t{h}" for h in horizons}}
    base_feature_cols = [col for col in training_frame.columns if col not in feature_excluded and pd.api.types.is_numeric_dtype(training_frame[col])]
    if not base_feature_cols:
        raise ValueError("direct_feature_cols_empty")

    horizon_predictions: list[dict[str, float | pd.Timestamp]] = []
    model_count = 0
    training_rows_used: list[int] = []
    neighbor_keys = training_frame.attrs.get("neighbor_station_keys", [])

    for horizon in horizons:
        target_col = f"target_t{horizon}"
        horizon_feature_cols = [col for col in base_feature_cols if not col.endswith(tuple(f"_future_t{h}" for h in horizons if h != horizon))]
        if f"{WEATHER_REGRESSORS[0]}_future_t{horizon}" not in horizon_feature_cols:
            horizon_feature_cols.extend([col for col in training_frame.columns if col.endswith(f"_future_t{horizon}") and pd.api.types.is_numeric_dtype(training_frame[col])])
        model_frame = training_frame.dropna(subset=[target_col]).copy()
        lag_required = [col for col in horizon_feature_cols if col.startswith("lag_")]
        if lag_required:
            model_frame = model_frame.dropna(subset=lag_required)
        if len(model_frame) < max(int(_forecast_profile(pollutant)["min_train_days"]), 28):
            continue

        x_train = model_frame[horizon_feature_cols].apply(pd.to_numeric, errors="coerce")
        fill_values = x_train.median(numeric_only=True).fillna(0.0)
        x_train = x_train.fillna(fill_values).fillna(0.0)
        y_train = pd.to_numeric(model_frame[target_col], errors="coerce")

        model = GradientBoostingRegressor(
            learning_rate=0.05,
            max_depth=4,
            n_estimators=220,
            min_samples_leaf=8,
            random_state=42 + horizon,
        )
        model.fit(x_train, y_train)
        residuals = y_train - model.predict(x_train)
        sigma = float(np.nanstd(residuals) or max(float(np.nanmean(y_train) or 1.0) * 0.10, 1.0))

        future_row = latest_row[horizon_feature_cols].copy()
        for col in WEATHER_REGRESSORS:
            future_feature_col = f"{col}_future_t{horizon}"
            if future_feature_col in future_row.columns and len(future) >= horizon:
                future_row.iloc[0, future_row.columns.get_loc(future_feature_col)] = float(future.iloc[horizon - 1][col])
        x_future = future_row.apply(pd.to_numeric, errors="coerce").fillna(fill_values).fillna(0.0)
        pred = max(0.0, float(model.predict(x_future)[0]))
        ci = 1.645 * sigma
        forecast_date = pd.to_datetime(latest_row["date"].iloc[0]) + timedelta(days=horizon)
        horizon_predictions.append({
            "date": forecast_date,
            "value": pred,
            "upper": max(0.0, pred + ci),
            "lower": max(0.0, pred - ci),
        })
        model_count += 1
        training_rows_used.append(len(model_frame))

    if not horizon_predictions:
        raise ValueError("direct_models_unavailable")

    fc_df = pd.DataFrame(horizon_predictions).sort_values("date").reset_index(drop=True)
    diagnostics = {
        "training_rows": str(max(training_rows_used) if training_rows_used else 0),
        "direct_model_count": str(model_count),
        "neighbor_station_count": str(len(neighbor_keys)),
        "neighbor_station_keys": ", ".join(neighbor_keys),
        "regressors": ", ".join(WEATHER_REGRESSORS),
        "direct_strategy": "per_horizon_gradient_boosting",
    }
    return fc_df, diagnostics


def _build_observed_history(feed_data: Dict, pollutant: str, station_key: Optional[str]) -> Tuple[pd.DataFrame, Dict[str, str]]:
    diagnostics: Dict[str, str] = {}
    history_days = int(FORECAST_DEFAULTS["history_days"])
    lat = safe_float(feed_data.get("lat"))
    lon = safe_float(feed_data.get("lon"))

    if lat != 0 and lon != 0:
        aq_history = fetch_air_quality_history(lat, lon, days=history_days)
        real_observed_days = int(pd.to_numeric(aq_history.get("real_observed_days"), errors="coerce").dropna().iloc[0]) if "real_observed_days" in aq_history and not aq_history.empty else 0
        interpolated_days = int(pd.to_numeric(aq_history.get("interpolated_days"), errors="coerce").dropna().iloc[0]) if "interpolated_days" in aq_history and not aq_history.empty else 0
        aq_history = _prepare_history_frame(aq_history, pollutant, history_days)
        diagnostics["real_observed_days"] = str(real_observed_days)
        diagnostics["interpolated_days"] = str(interpolated_days)
        if not aq_history.empty and real_observed_days >= int(FORECAST_DEFAULTS["min_real_history_days"]):
            live_row = pd.DataFrame([{
                "date": pd.Timestamp.utcnow().normalize(),
                pollutant: safe_float(feed_data.get(pollutant), float("nan")),
            }])
            aq_history = pd.concat([aq_history, live_row], ignore_index=True)
            aq_history = _prepare_history_frame(aq_history, pollutant, history_days)
            diagnostics["history_source"] = "open_meteo_air_quality_plus_live_waqi"
            diagnostics["observed_days"] = str(len(aq_history))
            return aq_history, diagnostics
        diagnostics["history_source"] = "synthetic_fallback_insufficient_real_history"
        diagnostics["observed_days"] = str(len(aq_history))
        diagnostics["fallback_reason"] = "insufficient_real_history"

    base_val = safe_float(feed_data.get(pollutant), 0.0)
    fallback = _build_synthetic_history(
        base_val=base_val,
        pollutant=pollutant,
        history_days=history_days,
        label=str(feed_data.get("station_name") or station_key or "global"),
    )
    diagnostics["history_source"] = diagnostics.get("history_source", "synthetic_fallback")
    diagnostics["observed_days"] = str(len(fallback))
    return fallback, diagnostics


def generate_forecast_bundle(feed_data: Dict, pollutant: str, days: int = 7,
                             station_name: Optional[str] = None,
                             station_key: Optional[str] = None,
                             prefer_native_waqi: bool = False,
                             prefer_offline_champion_only: bool = False) -> ForecastResult:
    pollutant = pollutant.lower().strip()
    lat = safe_float(feed_data.get("lat"))
    lon = safe_float(feed_data.get("lon"))
    station_key = _resolve_station_key(station_name, station_key, lat=lat, lon=lon)

    waqi_daily = parse_waqi_daily_forecast(feed_data.get("forecast", {}), pollutant)
    waqi_daily = [row for row in waqi_daily if isinstance(row, dict) and "day" in row and "avg" in row]
    if prefer_native_waqi and waqi_daily:
        fc_dates = [pd.to_datetime(row["day"]) for row in waqi_daily[:days]]
        fc_avg = [max(0, safe_float(row.get("avg"))) for row in waqi_daily[:days]]
        fc_max = [max(0, safe_float(row.get("max"), fc_avg[i])) for i, row in enumerate(waqi_daily[:days])]
        fc_min = [max(0, safe_float(row.get("min"), fc_avg[i])) for i, row in enumerate(waqi_daily[:days])]
        fc_df = pd.DataFrame({"date": fc_dates, "value": fc_avg, "upper": fc_max, "lower": fc_min})
        history_df, history_diag = _build_observed_history(feed_data, pollutant, station_key)
        history_df = _prepare_history_frame(history_df, pollutant, int(FORECAST_DEFAULTS["history_days"]))
        return _build_result(
            history_df=history_df,
            fc_df=fc_df,
            pollutant=pollutant,
            model_used="WAQI_NATIVE",
            data_note="Official WAQI provider forecast feed is being used for the future days shown here.",
            diagnostics={**history_diag, "station_key": station_key or "unknown"},
            past_forecast_df=_empty_validation_frame(),
            model_accuracy_pct=None,
        )

    history_df, history_diag = _build_observed_history(feed_data, pollutant, station_key)
    history_df = _prepare_history_frame(history_df, pollutant, int(FORECAST_DEFAULTS["history_days"]))
    if history_df.empty:
        return _build_result(
            history_df=pd.DataFrame(columns=["date", pollutant]),
            fc_df=pd.DataFrame(columns=["date", "value", "upper", "lower"]),
            pollutant=pollutant,
            model_used="NO_HISTORY",
            data_note="Observed history is not sufficient yet, so no forecast is being shown.",
            diagnostics={**history_diag, "station_key": station_key or "unknown"},
            past_forecast_df=_empty_validation_frame(),
            model_accuracy_pct=None,
        )

    hw_train = history_df.rename(columns={"date": "ds", pollutant: "y"})
    fc_df, diagnostics = _single_point_forecast(hw_train, days) if len(hw_train) < 2 else _holt_winters_forecast(hw_train, days, pollutant)
    diagnostics["safe_mode"] = "true"
    if prefer_native_waqi:
        diagnostics["fallback_reason"] = "waqi_daily_forecast_unavailable"
        data_note = "WAQI daily forecast was unavailable, so AirPulse is using a conservative time-series fallback."
    else:
        data_note = "AirPulse is using a lightweight fallback forecast built from the recent observed history."
    return _build_result(
        history_df=history_df,
        fc_df=fc_df,
        pollutant=pollutant,
        model_used="HOLT_WINTERS",
        data_note=data_note,
        diagnostics={**history_diag, **diagnostics, "station_key": station_key or "unknown"},
        past_forecast_df=_empty_validation_frame(),
        model_accuracy_pct=None,
    )
