"""
Analytics engine for AirPulse.

The app currently does not fetch a dedicated historical analytics backend, so
these helpers build stable, presentation-friendly analytics views from
deterministic synthetic signals seeded by location and pollutant. The goal is
to keep the Analytics Lab readable and visually consistent instead of showing
jittery random charts on every rerun.
"""

from __future__ import annotations

from datetime import datetime
import json
from typing import Dict

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from airpulse.config import FORECAST_VALIDATION_FILE
from airpulse.utils import normalize_station_name
from airpulse.weather_integration import fetch_air_quality_history


POLLUTANT_BASELINES = {
    "pm25": {"mean": 28.0, "vol": 8.0, "label": "PM2.5"},
    "pm10": {"mean": 42.0, "vol": 12.0, "label": "PM10"},
    "o3": {"mean": 36.0, "vol": 10.0, "label": "O3"},
    "no2": {"mean": 24.0, "vol": 7.0, "label": "NO2"},
}

DAY_ORDER = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]

COLOR_PRIMARY = "#007AFF"
COLOR_SECONDARY = "#FF9500"
COLOR_ACCENT = "#34C759"
COLOR_ALERT = "#FF3B30"
COLOR_MUTED = "#94A3B8"
COLOR_SURFACE = "#FFFFFF"
COLOR_GRID = "rgba(148, 163, 184, 0.18)"


def _seed_from_parts(*parts: object) -> int:
    text = "|".join(str(part) for part in parts)
    return sum((idx + 1) * ord(char) for idx, char in enumerate(text)) % (2**32 - 1)


def _rng(*parts: object) -> np.random.Generator:
    return np.random.default_rng(_seed_from_parts(*parts))


def _pollutant_meta(pollutant: str) -> dict:
    return POLLUTANT_BASELINES.get(pollutant.lower(), POLLUTANT_BASELINES["pm25"])


def _apply_chart_style(fig: go.Figure, *, height: int = 420, title: str | None = None) -> go.Figure:
    fig.update_layout(
        title=title,
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor=COLOR_SURFACE,
        margin=dict(l=24, r=24, t=70, b=24),
        hoverlabel=dict(bgcolor="white", font_size=12),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    fig.update_xaxes(showgrid=True, gridcolor=COLOR_GRID, zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor=COLOR_GRID, zeroline=False)
    return fig


def _build_series(lat: float, lon: float, days: int, pollutant: str) -> pd.DataFrame:
    dates = pd.date_range(end=datetime.now(), periods=days * 24, freq="h")
    meta = _pollutant_meta(pollutant)
    rng = _rng(lat, lon, days, pollutant)

    idx = np.arange(len(dates))
    hour = dates.hour.to_numpy()
    day = dates.dayofyear.to_numpy()

    daily_cycle = np.sin((hour - 7) / 24 * 2 * np.pi)
    weekly_cycle = np.sin((dates.dayofweek.to_numpy()) / 7 * 2 * np.pi)
    seasonal = np.sin(day / 365 * 2 * np.pi)
    drift = np.linspace(-0.8, 1.2, len(dates))
    noise = rng.normal(0, meta["vol"] * 0.28, len(dates))

    pollutant_values = (
        meta["mean"]
        + meta["vol"] * 0.60 * daily_cycle
        + meta["vol"] * 0.35 * weekly_cycle
        + meta["vol"] * 0.22 * seasonal
        + meta["vol"] * 0.25 * drift
        + noise
    ).clip(0, None)

    pm25 = (
        POLLUTANT_BASELINES["pm25"]["mean"]
        + 6.5 * np.sin((hour - 8) / 24 * 2 * np.pi)
        + 4.2 * weekly_cycle
        + rng.normal(0, 2.2, len(dates))
    ).clip(0, None)
    pm10 = (
        POLLUTANT_BASELINES["pm10"]["mean"]
        + 8.5 * np.sin((hour - 9) / 24 * 2 * np.pi)
        + 5.5 * weekly_cycle
        + rng.normal(0, 3.0, len(dates))
    ).clip(0, None)
    o3 = (
        POLLUTANT_BASELINES["o3"]["mean"]
        + 9.5 * np.sin((hour - 13) / 24 * 2 * np.pi)
        - 3.0 * weekly_cycle
        + rng.normal(0, 2.6, len(dates))
    ).clip(0, None)
    no2 = (
        POLLUTANT_BASELINES["no2"]["mean"]
        + 6.0 * np.sin((hour - 6) / 24 * 2 * np.pi)
        + 4.0 * np.sin((hour - 18) / 24 * 2 * np.pi)
        + 4.4 * weekly_cycle
        + rng.normal(0, 2.1, len(dates))
    ).clip(0, None)

    df = pd.DataFrame(
        {
            "timestamp": dates,
            "aqi": (0.95 * pm25 + 0.25 * pm10 + 0.15 * no2 + 0.08 * o3).clip(0, 500),
            "pm25": pm25,
            "pm10": pm10,
            "o3": o3,
            "no2": no2,
        }
    )
    df[pollutant] = pollutant_values
    return df


def _expand_daily_history_to_hourly(daily_df: pd.DataFrame, days: int, lat: float, lon: float) -> pd.DataFrame:
    """Approximate hourly structure from real daily history while preserving daily means."""
    if daily_df.empty:
        return pd.DataFrame()

    df = daily_df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    df = df.dropna(subset=["date"]).sort_values("date")
    if df.empty:
        return pd.DataFrame()

    pollutants = [col for col in ["aqi", "pm25", "pm10", "o3", "no2"] if col in df.columns]
    rows: list[dict] = []
    for _, row in df.tail(days).iterrows():
        current_date = pd.to_datetime(row["date"])
        day_rng = _rng("hourly-real", lat, lon, current_date.date().isoformat())
        for hour in range(24):
            timestamp = current_date + pd.Timedelta(hours=hour)
            diurnal = np.sin((hour - 8) / 24 * 2 * np.pi)
            evening = np.sin((hour - 18) / 24 * 2 * np.pi)
            record = {"timestamp": timestamp}
            for pollutant in pollutants:
                base_raw = pd.to_numeric(row.get(pollutant), errors="coerce")
                base = 0.0 if pd.isna(base_raw) else float(base_raw)
                if pollutant == "o3":
                    shaped = base + diurnal * max(base * 0.18, 2.0) - evening * max(base * 0.05, 0.8)
                elif pollutant == "no2":
                    shaped = base + diurnal * max(base * 0.10, 1.2) + evening * max(base * 0.16, 1.5)
                else:
                    shaped = base + diurnal * max(base * 0.12, 1.0) + evening * max(base * 0.08, 0.8)
                shaped += day_rng.normal(0, max(base * 0.03, 0.35))
                record[pollutant] = max(0.0, shaped)
            rows.append(record)
    return pd.DataFrame(rows)


def fetch_air_quality_hourly(lat: float, lon: float, days: int = 14) -> pd.DataFrame:
    """Prefer real daily history and expand it to hourly structure, fallback to synthetic series."""
    try:
        daily_history = fetch_air_quality_history(lat, lon, days=days)
    except Exception:
        daily_history = pd.DataFrame()

    hourly_history = _expand_daily_history_to_hourly(daily_history, days, lat, lon)
    if not hourly_history.empty:
        return hourly_history
    return _build_series(lat, lon, days, "pm25")


def fetch_weather_hourly(lat: float, lon: float, days: int = 14) -> pd.DataFrame:
    """Build stable hourly weather context for a location."""
    dates = pd.date_range(end=datetime.now(), periods=days * 24, freq="h")
    rng = _rng("weather", lat, lon, days)
    hour = dates.hour.to_numpy()
    day = dates.dayofyear.to_numpy()

    temperature = 16 + 7 * np.sin((hour - 14) / 24 * 2 * np.pi) + 4 * np.sin(day / 365 * 2 * np.pi) + rng.normal(0, 1.2, len(dates))
    humidity = 62 - 10 * np.sin((hour - 11) / 24 * 2 * np.pi) + rng.normal(0, 4, len(dates))
    wind_speed = np.clip(2.6 + 1.4 * np.sin((hour - 15) / 24 * 2 * np.pi) + rng.normal(0, 0.55, len(dates)), 0.2, None)
    wind_direction = (210 + np.linspace(-55, 65, len(dates)) + rng.normal(0, 18, len(dates))) % 360
    precipitation = np.clip(rng.gamma(shape=1.5, scale=0.18, size=len(dates)) - 0.1, 0, None)

    return pd.DataFrame(
        {
            "timestamp": dates,
            "temperature": temperature,
            "humidity": humidity.clip(20, 100),
            "wind_speed": wind_speed,
            "wind_direction": wind_direction,
            "precipitation": precipitation,
        }
    )


def build_temporal_patterns(hourly_df: pd.DataFrame, pollutant: str = "pm25") -> Dict:
    """Build temporal pattern visualizations (hourly heatmap, weekday/weekend, rolling avg)."""
    try:
        df = hourly_df.copy()
        if pollutant not in df.columns:
            return {"error": f"{pollutant.upper()} series is not available for temporal analysis."}

        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["hour"] = df["timestamp"].dt.hour
        df["dayofweek"] = pd.Categorical(df["timestamp"].dt.day_name(), categories=DAY_ORDER, ordered=True)
        df["is_weekend"] = df["timestamp"].dt.dayofweek >= 5

        pivot_data = (
            df.pivot_table(values=pollutant, index="hour", columns="dayofweek", aggfunc="mean")
            .reindex(columns=DAY_ORDER)
            .round(1)
        )

        heatmap_fig = go.Figure(
            data=go.Heatmap(
                z=pivot_data.values,
                x=list(pivot_data.columns),
                y=list(pivot_data.index),
                colorscale=[[0.0, "#E0F2FE"], [0.35, "#7DD3FC"], [0.65, "#FBBF24"], [1.0, "#F97316"]],
                colorbar=dict(title=pollutant.upper()),
                hovertemplate="Day=%{x}<br>Hour=%{y}:00<br>Avg=%{z:.1f}<extra></extra>",
            )
        )
        heatmap_fig.update_layout(
            xaxis_title="Day of week",
            yaxis_title="Hour of day",
        )
        _apply_chart_style(heatmap_fig, title=f"{pollutant.upper()} hourly signature")

        weekday_mean = float(df.loc[~df["is_weekend"], pollutant].mean())
        weekend_mean = float(df.loc[df["is_weekend"], pollutant].mean())
        hourly_profile = (
            df.groupby("hour")[pollutant]
            .mean()
            .reindex(range(24))
            .fillna(method="ffill")
            .fillna(method="bfill")
        )

        weekday_weekend_fig = go.Figure()
        weekday_weekend_fig.add_trace(
            go.Bar(
                x=["Weekday average", "Weekend average"],
                y=[weekday_mean, weekend_mean],
                name="Day type average",
                marker=dict(color=[COLOR_PRIMARY, COLOR_SECONDARY]),
                text=[f"{weekday_mean:.1f}", f"{weekend_mean:.1f}"],
                textposition="outside",
            )
        )
        weekday_weekend_fig.add_trace(
            go.Scatter(
                x=[f"{h:02d}:00" for h in hourly_profile.index],
                y=hourly_profile.values,
                mode="lines+markers",
                name="Average day profile",
                yaxis="y2",
                line=dict(color=COLOR_ACCENT, width=3),
                marker=dict(size=5),
            )
        )
        weekday_weekend_fig.update_layout(
            yaxis=dict(title=f"{pollutant.upper()} average"),
            yaxis2=dict(title="", overlaying="y", side="right", showgrid=False, automargin=True),
            margin=dict(l=24, r=72, t=70, b=24),
        )
        _apply_chart_style(weekday_weekend_fig, title="Week structure vs intraday pattern")

        df = df.sort_values("timestamp")
        df["rolling_avg"] = df[pollutant].rolling(window=24 * 7, min_periods=12).mean()
        df["rolling_p90"] = df[pollutant].rolling(window=24 * 7, min_periods=12).quantile(0.90)

        rolling_fig = go.Figure()
        rolling_fig.add_trace(
            go.Scatter(
                x=df["timestamp"],
                y=df[pollutant],
                mode="lines",
                name="Hourly reading",
                opacity=0.24,
                line=dict(color="rgba(0,122,255,0.35)", width=1),
            )
        )
        rolling_fig.add_trace(
            go.Scatter(
                x=df["timestamp"],
                y=df["rolling_avg"],
                mode="lines",
                name="7-day rolling mean",
                line=dict(color=COLOR_PRIMARY, width=3),
            )
        )
        rolling_fig.add_trace(
            go.Scatter(
                x=df["timestamp"],
                y=df["rolling_p90"],
                mode="lines",
                name="7-day rolling p90",
                line=dict(color=COLOR_ALERT, width=2, dash="dot"),
            )
        )
        rolling_fig.update_layout(
            xaxis_title="Date",
            yaxis_title=pollutant.upper(),
            hovermode="x unified",
        )
        _apply_chart_style(rolling_fig, title="Rolling baseline and upper-band stress")

        return {
            "heatmap_fig": heatmap_fig,
            "weekday_weekend_fig": weekday_weekend_fig,
            "rolling_fig": rolling_fig,
            "weekday_aqi": weekday_mean,
            "weekend_aqi": weekend_mean,
            "error": None,
        }
    except Exception as exc:
        return {"error": f"Temporal analysis failed: {exc}"}


def build_dispersion_map(city: str, lat: float, lon: float, hourly_df: pd.DataFrame, weather_df: pd.DataFrame, pollutant: str = "pm25"):
    """Build an interactive wind-driven dispersion map."""
    try:
        from branca.element import Element
        import folium
        from folium import PolyLine
        from folium.plugins import AntPath, Fullscreen, HeatMap, MiniMap, MousePosition

        m = folium.Map(location=[lat, lon], zoom_start=11, tiles="CartoDB positron", control_scale=True)
        m.get_root().header.add_child(Element("""
        <style>
        .leaflet-control-attribution {
          display: none !important;
        }
        </style>
        """))
        Fullscreen(position="topright").add_to(m)
        MiniMap(toggle_display=True).add_to(m)
        MousePosition(position="bottomright").add_to(m)

        latest = weather_df.sort_values("timestamp").tail(8)
        avg_speed = float(latest["wind_speed"].mean()) if not latest.empty else 2.0
        avg_dir = float(latest["wind_direction"].mean()) if "wind_direction" in latest.columns and not latest.empty else 210.0
        latest_pollutant = 0.0
        if pollutant in hourly_df.columns and not hourly_df.empty:
            latest_series = pd.to_numeric(hourly_df[pollutant], errors="coerce").dropna()
            if not latest_series.empty:
                latest_pollutant = float(latest_series.iloc[-1])

        plume_layer = folium.FeatureGroup(name="Wind plume", show=True)
        corridor_layer = folium.FeatureGroup(name="Transport corridor", show=True)
        intensity_layer = folium.FeatureGroup(name="Dispersion intensity", show=True)

        folium.CircleMarker(
            location=[lat, lon],
            radius=16,
            popup=f"{city} centroid<br>{pollutant.upper()}: {latest_pollutant:.1f}<br>Wind: {avg_speed:.1f} m/s @ {avg_dir:.0f}°",
            color=COLOR_PRIMARY,
            fill=True,
            fillColor=COLOR_PRIMARY,
            fill_opacity=0.95,
            weight=3,
        ).add_to(plume_layer)

        heat_points = []
        corridor_points = [[lat, lon]]

        for step in range(1, 6):
            distance_scale = (avg_speed * step) / 160.0
            angle = np.deg2rad(avg_dir + (step - 3) * 8)
            end_lat = lat + distance_scale * np.cos(angle)
            end_lon = lon + distance_scale * np.sin(angle)
            corridor_points.append([end_lat, end_lon])
            plume_strength = max(latest_pollutant * (1 - step * 0.12), latest_pollutant * 0.22, 1.0)

            PolyLine(
                locations=[[lat, lon], [end_lat, end_lon]],
                color=COLOR_SECONDARY,
                weight=max(2, 7 - step),
                opacity=max(0.25, 0.75 - step * 0.08),
                tooltip=f"{pollutant.upper()} carriage path {step}",
            ).add_to(corridor_layer)
            folium.CircleMarker(
                location=[end_lat, end_lon],
                radius=max(4, 10 - step),
                color=COLOR_SECONDARY,
                fill=True,
                fillColor=COLOR_SECONDARY,
                fill_opacity=0.55,
                weight=1,
                popup=f"Downwind node {step}<br>{pollutant.upper()} intensity proxy: {plume_strength:.1f}",
            ).add_to(plume_layer)
            heat_points.append([end_lat, end_lon, plume_strength])

        AntPath(
            locations=corridor_points,
            color=COLOR_ALERT,
            pulse_color=COLOR_SECONDARY,
            weight=5,
            delay=800,
            opacity=0.7,
        ).add_to(corridor_layer)

        if heat_points:
            HeatMap(
                heat_points,
                min_opacity=0.25,
                radius=28,
                blur=22,
                gradient={0.2: "#93C5FD", 0.45: "#60A5FA", 0.7: "#F59E0B", 1.0: "#EF4444"},
            ).add_to(intensity_layer)

        plume_layer.add_to(m)
        corridor_layer.add_to(m)
        intensity_layer.add_to(m)
        folium.LayerControl(collapsed=False).add_to(m)

        return m
    except Exception:
        return None


def compute_neighbor_lag_analysis(city: str, city_meta: Dict, global_cities: Dict, pollutant: str = "pm25") -> Dict:
    """Analyze lag between the selected city and nearby peers."""
    try:
        neighbors = []
        city_coords = (city_meta.get("lat", 0.0), city_meta.get("lon", 0.0))

        for other_city, other_meta in global_cities.items():
            if other_city == city:
                continue
            other_coords = (other_meta.get("lat", 0.0), other_meta.get("lon", 0.0))
            dist = np.sqrt((city_coords[0] - other_coords[0]) ** 2 + (city_coords[1] - other_coords[1]) ** 2)
            if dist < 8:
                neighbors.append((other_city, dist))

        neighbors = sorted(neighbors, key=lambda item: item[1])[:6]
        if not neighbors:
            return {"error": "No neighboring cities found", "lag_df": pd.DataFrame(), "lag_fig": None}

        rows = []
        for neighbor, dist in neighbors:
            rng = _rng(city, neighbor, pollutant)
            lag_hours = int(rng.choice([-18, -12, -6, 0, 6, 12, 18]))
            correlation = float(np.clip(0.85 - dist * 0.05 + rng.normal(0, 0.05), 0.15, 0.97))
            rows.append({"city": neighbor, "lag_hours": lag_hours, "correlation": correlation, "distance_proxy": dist})

        lag_df = pd.DataFrame(rows).sort_values(["correlation", "distance_proxy"], ascending=[False, True])

        lag_fig = go.Figure()
        lag_fig.add_trace(
            go.Bar(
                x=lag_df["city"],
                y=lag_df["correlation"],
                marker=dict(
                    color=lag_df["lag_hours"],
                    colorscale=[[0.0, "#1D4ED8"], [0.5, "#94A3B8"], [1.0, "#EA580C"]],
                    colorbar=dict(title="Lag (h)"),
                ),
                text=[f"{val:+d}h" for val in lag_df["lag_hours"]],
                textposition="outside",
            )
        )
        lag_fig.update_layout(
            xaxis_title="Nearby city",
            yaxis_title="Correlation strength",
            yaxis_range=[0, 1],
        )
        _apply_chart_style(lag_fig, title=f"{city} neighbor spillover signature")

        return {"lag_fig": lag_fig, "lag_df": lag_df.drop(columns=["distance_proxy"]), "error": None}
    except Exception as exc:
        return {"error": str(exc), "lag_df": pd.DataFrame(), "lag_fig": None}


def build_station_correlation_matrix(nearby_stations: list, pollutant: str = "pm25") -> Dict:
    """Build a clean symmetric correlation matrix between nearby monitoring stations."""
    try:
        if len(nearby_stations) < 2:
            return {"error": "Need at least 2 stations for correlation analysis", "corr_fig": None}

        station_names = [station.get("name", f"Station {idx + 1}")[:18] for idx, station in enumerate(nearby_stations[:8])]
        n_stations = len(station_names)

        matrix = np.eye(n_stations)
        for i in range(n_stations):
            for j in range(i + 1, n_stations):
                rng = _rng(station_names[i], station_names[j], pollutant)
                value = float(np.clip(rng.normal(0.72, 0.11), 0.25, 0.96))
                matrix[i, j] = value
                matrix[j, i] = value

        corr_fig = go.Figure(
            data=go.Heatmap(
                z=matrix,
                x=station_names,
                y=station_names,
                zmin=0,
                zmax=1,
                colorscale=[[0.0, "#FEE2E2"], [0.5, "#FDE68A"], [1.0, "#86EFAC"]],
                hovertemplate="%{x} vs %{y}<br>corr=%{z:.2f}<extra></extra>",
            )
        )
        corr_fig.update_layout(
            xaxis_title="Monitoring station",
            yaxis_title="Monitoring station",
        )
        _apply_chart_style(corr_fig, height=500, title=f"{pollutant.upper()} station correlation matrix")
        return {"corr_fig": corr_fig, "error": None}
    except Exception as exc:
        return {"error": str(exc), "corr_fig": None}


def _compute_history_backtest_accuracy(history_df: pd.DataFrame, pollutant: str = "pm25") -> Dict:
    """Compute deterministic backtest metrics from an hourly pollutant history."""
    df = history_df.copy().sort_values("timestamp")
    if pollutant not in df.columns or len(df) < 72:
        return {"error": "Not enough history for backtest metrics."}

    daily = df.set_index("timestamp")[pollutant].resample("D").mean().dropna()
    if len(daily) < 14:
        return {"error": "Not enough daily points for backtest metrics."}

    predicted = daily.shift(1).rolling(3, min_periods=1).mean().fillna(method="bfill")
    actual = daily
    aligned = pd.DataFrame({"actual": actual, "predicted": predicted}).dropna().tail(30)
    if aligned.empty:
        return {"error": "Unable to align actual and predicted history."}

    mae = float(np.mean(np.abs(aligned["actual"] - aligned["predicted"])))
    rmse = float(np.sqrt(np.mean((aligned["actual"] - aligned["predicted"]) ** 2)))
    denominator = aligned["actual"].replace(0, np.nan)
    mape = float((np.abs((aligned["actual"] - aligned["predicted"]) / denominator)).dropna().mean() * 100) if denominator.notna().any() else 0.0

    scatter_fig = go.Figure()
    scatter_fig.add_trace(
        go.Scatter(
            x=aligned["actual"],
            y=aligned["predicted"],
            mode="markers",
            marker=dict(size=10, color=COLOR_PRIMARY, opacity=0.85),
            name="Validated points",
        )
    )
    bounds_min = float(min(aligned["actual"].min(), aligned["predicted"].min()))
    bounds_max = float(max(aligned["actual"].max(), aligned["predicted"].max()))
    scatter_fig.add_trace(
        go.Scatter(
            x=[bounds_min, bounds_max],
            y=[bounds_min, bounds_max],
            mode="lines",
            line=dict(color=COLOR_MUTED, dash="dash"),
            name="Perfect fit",
        )
    )
    scatter_fig.update_layout(
        xaxis_title="Observed",
        yaxis_title="Predicted",
    )
    _apply_chart_style(scatter_fig, title=f"{pollutant.upper()} forecast validation")

    drift_flag = mape > 18
    drift_text = (
        f"Model drift risk is elevated: recent validation error is {mape:.1f}%."
        if drift_flag
        else f"Model behavior is stable: recent validation error is {mape:.1f}%."
    )

    return {
        "mae": mae,
        "rmse": rmse,
        "mape": mape,
        "points": len(aligned),
        "scatter_fig": scatter_fig,
        "drift_flag": drift_flag,
        "drift_text": drift_text,
        "mode": "backtest",
        "error": None,
    }


def compute_forecast_accuracy(city: str, pollutant: str = "pm25", lat: float | None = None, lon: float | None = None) -> Dict:
    """Compute forecast accuracy from real validation records when available."""
    try:
        aligned = pd.DataFrame()
        validation_points = 0
        source_key_prefix = f"city:{normalize_station_name(city)}|"
        if FORECAST_VALIDATION_FILE.exists():
            payload = json.loads(FORECAST_VALIDATION_FILE.read_text(encoding="utf-8"))
            store = pd.DataFrame(payload if isinstance(payload, list) else [])
            required_cols = {"source_key", "pollutant", "generated_on", "target_date", "predicted_value", "actual_value"}
            if not store.empty and required_cols.issubset(store.columns):
                store["generated_on"] = pd.to_datetime(store["generated_on"], errors="coerce")
                store["target_date"] = pd.to_datetime(store["target_date"], errors="coerce")
                store["predicted_value"] = pd.to_numeric(store["predicted_value"], errors="coerce")
                store["actual_value"] = pd.to_numeric(store["actual_value"], errors="coerce")
                store = store.dropna(subset=["source_key", "pollutant", "generated_on", "target_date", "predicted_value"])
                store = store[
                    store["source_key"].astype(str).str.startswith(source_key_prefix)
                    & (store["pollutant"].astype(str).str.lower() == pollutant.lower())
                    & store["actual_value"].notna()
                ].copy()
                if not store.empty:
                    store = store.sort_values(["target_date", "generated_on"])
                    store = store.drop_duplicates(subset=["target_date"], keep="last").tail(30)
                    aligned = store.rename(
                        columns={
                            "target_date": "date",
                            "actual_value": "actual",
                            "predicted_value": "predicted",
                        }
                    )[["date", "actual", "predicted"]].dropna()
                    validation_points = len(aligned)

        if validation_points < 7:
            if lat is None or lon is None:
                return {
                    "error": "Not enough validated forecasts yet for this city and pollutant.",
                    "mae": 0,
                    "rmse": 0,
                    "mape": 0,
                }
            history_df = fetch_air_quality_hourly(float(lat), float(lon), days=90)
            fallback = _compute_history_backtest_accuracy(history_df, pollutant)
            if fallback.get("error"):
                return {
                    "error": "Not enough validated forecasts yet for this city and pollutant.",
                    "mae": 0,
                    "rmse": 0,
                    "mape": 0,
                }
            return fallback

        mae = float(np.mean(np.abs(aligned["actual"] - aligned["predicted"])))
        rmse = float(np.sqrt(np.mean((aligned["actual"] - aligned["predicted"]) ** 2)))
        denominator = aligned["actual"].abs().clip(lower=5.0).replace(0, np.nan)
        mape = float((np.abs((aligned["actual"] - aligned["predicted"]) / denominator)).dropna().mean() * 100)

        scatter_fig = go.Figure()
        scatter_fig.add_trace(
            go.Scatter(
                x=aligned["actual"],
                y=aligned["predicted"],
                mode="markers",
                marker=dict(
                    size=11,
                    color=aligned.index,
                    colorscale="Blues",
                    showscale=False,
                    line=dict(color="white", width=0.8),
                ),
                text=aligned["date"].dt.strftime("%Y-%m-%d"),
                hovertemplate="Date=%{text}<br>Observed=%{x:.1f}<br>Predicted=%{y:.1f}<extra></extra>",
                name="Validation points",
            )
        )
        bounds_min = float(min(aligned["actual"].min(), aligned["predicted"].min()))
        bounds_max = float(max(aligned["actual"].max(), aligned["predicted"].max()))
        scatter_fig.add_trace(
            go.Scatter(
                x=[bounds_min, bounds_max],
                y=[bounds_min, bounds_max],
                mode="lines",
                line=dict(dash="dash", color=COLOR_MUTED, width=2),
                name="Perfect fit",
            )
        )
        scatter_fig.update_layout(
            xaxis_title="Observed value",
            yaxis_title="Predicted value",
        )
        _apply_chart_style(scatter_fig, title=f"{city} forecast accuracy for {pollutant.upper()}")

        drift_flag = mape > 18
        drift_text = (
            f"Model drift risk is elevated: MAPE is {mape:.1f}% and forecast spread should be reviewed."
            if drift_flag
            else f"Model performance is stable: MAPE is {mape:.1f}% and the latest validation cloud remains tight."
        )

        return {
            "mae": mae,
            "rmse": rmse,
            "mape": mape,
            "points": validation_points,
            "scatter_fig": scatter_fig,
            "drift_flag": drift_flag,
            "drift_text": drift_text,
            "mode": "validated",
            "validated_points": validation_points,
            "error": None,
        }
    except Exception as exc:
        return {"error": str(exc), "mae": 0, "rmse": 0, "mape": 0}


def build_comparative_analysis(city1: str, city2: str, meta1: Dict, meta2: Dict, pollutant: str = "pm25", df: pd.DataFrame | None = None) -> Dict:
    """Compare AQI-style trends between two cities."""
    try:
        dates = pd.date_range(end=datetime.now(), periods=30, freq="D")
        meta = _pollutant_meta(pollutant)
        rng1 = _rng("compare", city1, pollutant)
        rng2 = _rng("compare", city2, pollutant)

        anchor1 = meta["mean"] + rng1.normal(0, 4)
        anchor2 = meta["mean"] + rng2.normal(0, 4)
        if df is not None and not df.empty and "city" in df.columns and "aqi" in df.columns:
            row1 = df.loc[df["city"] == city1, "aqi"]
            row2 = df.loc[df["city"] == city2, "aqi"]
            if not row1.empty:
                anchor1 = float(row1.iloc[0])
            if not row2.empty:
                anchor2 = float(row2.iloc[0])

        city1_vals = anchor1 + np.cumsum(rng1.normal(0, meta["vol"] * 0.18, 30)) + np.sin(np.linspace(0, 2.5 * np.pi, 30)) * meta["vol"] * 0.55
        city2_vals = anchor2 + np.cumsum(rng2.normal(0, meta["vol"] * 0.16, 30)) + np.sin(np.linspace(0.5, 3.0 * np.pi, 30)) * meta["vol"] * 0.45
        city1_vals = np.clip(city1_vals, 1, None)
        city2_vals = np.clip(city2_vals, 1, None)

        time_series_fig = go.Figure()
        time_series_fig.add_trace(go.Scatter(x=dates, y=city1_vals, mode="lines+markers", name=city1, line=dict(color=COLOR_PRIMARY, width=3)))
        time_series_fig.add_trace(go.Scatter(x=dates, y=city2_vals, mode="lines+markers", name=city2, line=dict(color=COLOR_SECONDARY, width=3)))
        time_series_fig.update_layout(
            xaxis_title="Date",
            yaxis_title=pollutant.upper(),
            hovermode="x unified",
        )
        _apply_chart_style(time_series_fig, title=f"{city1} vs {city2}: 30-day trajectory")

        city1_normalized = (city1_vals - city1_vals.min()) / max(city1_vals.max() - city1_vals.min(), 1e-6)
        city2_normalized = (city2_vals - city2_vals.min()) / max(city2_vals.max() - city2_vals.min(), 1e-6)

        normalized_fig = go.Figure()
        normalized_fig.add_trace(go.Scatter(x=dates, y=city1_normalized, mode="lines+markers", name=city1, line=dict(color=COLOR_PRIMARY, width=3)))
        normalized_fig.add_trace(go.Scatter(x=dates, y=city2_normalized, mode="lines+markers", name=city2, line=dict(color=COLOR_SECONDARY, width=3)))
        normalized_fig.update_layout(
            xaxis_title="Date",
            yaxis_title="Normalized intensity",
            yaxis_range=[0, 1.05],
            hovermode="x unified",
        )
        _apply_chart_style(normalized_fig, title="Normalized movement comparison")

        return {"time_series_fig": time_series_fig, "normalized_fig": normalized_fig, "error": None}
    except Exception as exc:
        return {"error": str(exc)}


def build_anomaly_detection(hourly_df: pd.DataFrame, pollutant: str = "pm25") -> Dict:
    """Detect anomalies in air-quality data using z-scores and rolling baseline."""
    try:
        df = hourly_df.copy().sort_values("timestamp")
        if pollutant not in df.columns:
            return {"error": f"{pollutant.upper()} series is not available for anomaly detection."}

        df["timestamp"] = pd.to_datetime(df["timestamp"])
        rolling_mean = df[pollutant].rolling(24 * 5, min_periods=24).mean()
        rolling_std = df[pollutant].rolling(24 * 5, min_periods=24).std().replace(0, np.nan)
        df["baseline"] = rolling_mean.fillna(df[pollutant].expanding().mean())
        df["zscore"] = ((df[pollutant] - df["baseline"]) / rolling_std.fillna(df[pollutant].std() or 1)).abs()

        threshold = 2.5
        anomaly_mask = df["zscore"] > threshold

        anomaly_fig = go.Figure()
        anomaly_fig.add_trace(
            go.Scatter(
                x=df["timestamp"],
                y=df[pollutant],
                mode="lines",
                name="Observed",
                line=dict(color=COLOR_PRIMARY, width=2),
            )
        )
        anomaly_fig.add_trace(
            go.Scatter(
                x=df["timestamp"],
                y=df["baseline"],
                mode="lines",
                name="Rolling baseline",
                line=dict(color=COLOR_ACCENT, width=2, dash="dot"),
            )
        )
        if anomaly_mask.any():
            anomalies = df.loc[anomaly_mask]
            anomaly_fig.add_trace(
                go.Scatter(
                    x=anomalies["timestamp"],
                    y=anomalies[pollutant],
                    mode="markers",
                    name="Anomaly",
                    marker=dict(color=COLOR_ALERT, size=10, line=dict(color="white", width=1)),
                )
            )

        anomaly_fig.update_layout(
            xaxis_title="Date",
            yaxis_title=pollutant.upper(),
            hovermode="x unified",
        )
        _apply_chart_style(anomaly_fig, title=f"{pollutant.upper()} anomaly detection")

        latest = float(df[pollutant].iloc[-1])
        baseline = float(df["baseline"].iloc[-1]) if not df["baseline"].empty else latest
        deviation_pct = ((latest - baseline) / baseline * 100) if baseline else 0.0
        anomaly_text = (
            f"Latest reading is {abs(deviation_pct):.1f}% above the rolling baseline."
            if deviation_pct > 0
            else f"Latest reading is {abs(deviation_pct):.1f}% below the rolling baseline."
        )

        anomaly_df = df[["timestamp", pollutant, "zscore"]].rename(columns={"timestamp": "date", pollutant: "aqi"})
        return {
            "anomaly_fig": anomaly_fig,
            "anomaly_df": anomaly_df,
            "deviation_pct": deviation_pct,
            "anomaly_text": anomaly_text,
            "error": None,
        }
    except Exception as exc:
        return {"error": str(exc)}
