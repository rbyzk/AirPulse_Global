"""Map and station chart helpers."""

from __future__ import annotations

import folium
import pandas as pd
import plotly.graph_objects as go


def build_station_map(city_coords, stations, aqi_info, wind_data=None, wind_dir_label=None):
    zoom = 10 if len(stations) < 50 else 9 if len(stations) < 150 else 8
    fmap = folium.Map(
        location=[city_coords["lat"], city_coords["lon"]],
        zoom_start=zoom,
        tiles="https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
        attr="© OpenStreetMap © CARTO",
        control_scale=True,
        prefer_canvas=True,
        attributionControl=False,
    )
    if not stations:
        folium.Marker(location=[city_coords["lat"], city_coords["lon"]], tooltip="No station data available", icon=folium.Icon(color="gray", icon="info-sign")).add_to(fmap)
        return fmap

    for station in stations:
        try:
            aqi_val = float(station.get("aqi") or 0)
            lat_val = float(station.get("lat", city_coords["lat"]))
            lon_val = float(station.get("lon", city_coords["lon"]))
        except (TypeError, ValueError):
            continue
        info = aqi_info(aqi_val)
        color = info["color"]
        name = station.get("name", "Station")
        iaqi = station.get("iaqi", {}) or {}

        def _iv(key):
            item = iaqi.get(key, {})
            value = item.get("v") if isinstance(item, dict) else station.get(key)
            return f"{float(value):.1f}" if value is not None else "—"

        wt = "—"
        if wind_data and wind_data.get("speed") and wind_dir_label:
            wt = f"{wind_data['speed']:.1f} m/s {wind_dir_label(wind_data.get('direction') or 0)}"
        popup_html = f"""
        <div style="font-family:Inter,sans-serif;min-width:220px;max-width:280px">
          <div style="background:{color};color:#fff;padding:8px 12px;border-radius:8px 8px 0 0;font-weight:700;font-size:14px">{name}</div>
          <div style="padding:10px 12px;border:1px solid #eee;border-top:none;border-radius:0 0 8px 8px;background:#fff">
            <div style="font-size:28px;font-weight:900;color:{color}">{int(aqi_val)}</div>
            <div style="font-size:12px;color:{color};font-weight:600;margin-bottom:8px">{info["name"]} {info["icon"]}</div>
            <table style="width:100%;font-size:12px;border-collapse:collapse">
              <tr><td style="color:#888;padding:2px 0">PM2.5</td><td style="font-weight:600">{_iv("pm25")} µg/m³</td><td style="color:#888;padding:2px 0">PM10</td><td style="font-weight:600">{_iv("pm10")} µg/m³</td></tr>
              <tr><td style="color:#888;padding:2px 0">O₃</td><td style="font-weight:600">{_iv("o3")} µg/m³</td><td style="color:#888;padding:2px 0">NO₂</td><td style="font-weight:600">{_iv("no2")} µg/m³</td></tr>
              <tr><td style="color:#888;padding:2px 0">Wind</td><td colspan="3" style="font-weight:600">{wt}</td></tr>
            </table>
            <div style="margin-top:6px;font-size:10px;color:#aaa">🕐 {(station.get("time", "") or "")[:16] or "—"}</div>
          </div>
        </div>
        """
        folium.CircleMarker(
            location=[lat_val, lon_val],
            radius=max(8, min(20, 8 + aqi_val / 25)),
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.78,
            weight=2,
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=f"<b>{name}</b><br>AQI {int(aqi_val)} · {info['name']}",
        ).add_to(fmap)
    return fmap


def build_station_bar(stations, aqi_info):
    if not stations:
        return None
    df = pd.DataFrame(stations)
    df["aqi"] = pd.to_numeric(df["aqi"], errors="coerce")
    df = df.dropna(subset=["aqi"]).sort_values("aqi", ascending=False).head(15)
    if df.empty:
        return None
    df["color"] = df["aqi"].apply(lambda x: aqi_info(x)["color"])
    fig = go.Figure(go.Bar(x=df["aqi"], y=df["name"], orientation="h", marker_color=df["color"].tolist(), text=df["aqi"].apply(lambda x: f"{int(x)}"), textposition="inside"))
    fig.update_layout(
        height=max(320, len(df) * 32),
        xaxis_title="AQI",
        yaxis={"categoryorder": "total ascending"},
        margin=dict(l=0, r=0, t=24, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, sans-serif"),
    )
    return fig

