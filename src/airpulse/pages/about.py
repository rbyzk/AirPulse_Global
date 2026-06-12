"""About page extracted from the legacy app."""

from __future__ import annotations


def render_about_page(*, st, render_hero, render_page_guide, render_section, tt) -> None:
    render_hero(
        "About AirPulse Global",
        "Environmental Intelligence Platform",
    )
    render_page_guide(
        "<strong>Overview:</strong> See how AirPulse works, which data powers it, and the product stack behind the experience."
    )

    st.markdown(
        """
    <div style="background:linear-gradient(135deg,#007AFF0C,#5856D60C);
         border:1px solid #007AFF25;border-radius:20px;padding:2rem;margin-bottom:2rem">
      <h2 style="margin:0 0 .75rem;font-size:1.5rem;font-weight:900;color:#1D1D1F">
        What is AirPulse Global?
      </h2>
      <p style="margin:0;font-size:1rem;color:#3A3A3C;line-height:1.8;max-width:860px">
        AirPulse Global is a Streamlit-based environmental intelligence application that turns
        live air-quality signals into clear operational guidance. It combines real-time city and
        station readings, wind-aware interpretation, transparent forecast views, practical action
        guidance, and export-ready reporting inside a single interface.
      </p>
    </div>
    """,
        unsafe_allow_html=True,
    )

    render_section("Showcase Snapshot")
    snap_cols = st.columns(4)
    snap_items = [
        ("Live Intelligence", "Real-time AQI, pollutant, station, and wind context in one flow."),
        ("Forecast Layer", "Observed-history forecasting with transparent model explanations."),
        ("Action Engine", "Personalised daily actions, scoring, and lightweight habit tracking."),
        ("Export Ready", "A4 PDF, JPEG social card, and CSV outputs for sharing and reporting."),
    ]
    for i, (title, desc) in enumerate(snap_items):
        with snap_cols[i]:
            st.markdown(
                f"""
            <div class="info-card" style="min-height:152px;border-top:4px solid #007AFF;display:flex;flex-direction:column;justify-content:flex-start">
              <div style="font-size:1rem;font-weight:800;color:#1D1D1F;margin-bottom:.45rem">{title}</div>
              <div style="font-size:.84rem;color:#3A3A3C;line-height:1.65">{desc}</div>
            </div>
            """,
                unsafe_allow_html=True,
            )

    render_section("Technologies Used")
    tc1, tc2, tc3 = st.columns(3)
    stack = [
        (
            "Code",
            "Python 3.10+",
            "Core language for the app runtime, analytics helpers, forecast logic, and reporting services.",
        ),
        (
            "UI",
            "Streamlit",
            "Application framework. Sidebar navigation and session state keep city, station, checklist, commute, and reporting context connected across pages.",
        ),
        (
            "Map",
            "Folium + Leaflet",
            "Interactive map layer for city and station views. Popups surface AQI, major pollutants, and wind context without requiring a Mapbox token.",
        ),
        (
            "Chart",
            "Plotly",
            "Primary charting library for forecast trends, analytics comparisons, distributions, and reporting visuals.",
        ),
        (
            "Air",
            "WAQI / AQICN API",
            "Primary live air-quality source. Used for city feeds, global station discovery, pollutant snapshots, and native daily forecast data where available.",
        ),
        (
            "Wind",
            "Tomorrow.io",
            "Optional live wind provider for speed, direction, and gust data. Wind-dependent features gracefully fall back when no key is configured.",
        ),
        (
            "Weather",
            "Open-Meteo",
            "No-key weather and history support used by forecast and analytics helpers, plus fallback data paths in selected views.",
        ),
        (
            "PDF",
            "ReportLab",
            "PDF reporting engine for export-ready city summaries with AQI, pollutant, wind, and personal action context.",
        ),
        (
            "Image",
            "Pillow (PIL)",
            "Image generation for social sharing cards and report-style visual assets.",
        ),
    ]
    cols3 = [tc1, tc2, tc3]
    for i, (badge, title, desc) in enumerate(stack):
        with cols3[i % 3]:
            st.markdown(
                f"""
            <div class="info-card" style="margin-bottom:1rem;min-height:168px;display:flex;flex-direction:column;justify-content:flex-start">
              <div style="display:inline-flex;align-items:center;justify-content:center;width:auto;align-self:flex-start;margin-bottom:.55rem;padding:.22rem .55rem;border-radius:999px;background:#EEF4FF;color:#007AFF;font-size:.68rem;font-weight:800;letter-spacing:.06em;text-transform:uppercase;white-space:nowrap">{badge}</div>
              <div style="font-weight:800;color:#1D1D1F;margin-bottom:.4rem;font-size:.95rem">{title}</div>
              <div style="font-size:.82rem;color:#3A3A3C;line-height:1.6">{desc}</div>
            </div>""",
                unsafe_allow_html=True,
            )

    render_section("Platform Features")
    feat_cols = st.columns(2)
    features = [
        (
            "Station-Rich Interactive Map",
            "Live city and station views combine WAQI search results with map markers and quick pollutant context for fast exploration.",
        ),
        (
            "Pollutant Forecast",
            "Uses WAQI daily forecasts when available and falls back to the app forecast engine when upstream forecast coverage is incomplete.",
        ),
        (
            "Take Action Hub",
            "Personal action layer with checklist scoring, action recommendations, commute impact, and carbon-footprint context.",
        ),
        (
            "Professional Reports",
            "Export-ready outputs include PDF, JPEG social card, and CSV so live environmental context can be shared or documented quickly.",
        ),
        (
            "Global Coverage",
            "Predefined city views and live search make it possible to inspect air quality across a broad global footprint.",
        ),
        (
            "Wind Intelligence",
            "Wind context is surfaced across dashboard cards, commentary, maps, forecast interpretation, and planning flows.",
        ),
    ]
    for i, (ftitle, fdesc) in enumerate(features):
        with feat_cols[i % 2]:
            st.markdown(
                f"""
            <div class="info-card" style="margin-bottom:1rem;border-left:3px solid #007AFF;min-height:122px;display:flex;flex-direction:column;justify-content:flex-start">
              <div style="font-weight:800;color:#007AFF;margin-bottom:.4rem">{ftitle}</div>
              <div style="font-size:.85rem;color:#3A3A3C;line-height:1.6">{fdesc}</div>
            </div>""",
                unsafe_allow_html=True,
            )

    render_section("Configuration & API Keys")
    st.markdown(
        """
    <div class="card" style="font-size:.9rem;line-height:2.1">
      <div style="margin-bottom:.5rem"><b>WAQI / AQICN token</b><br>
        Configure your WAQI key in Streamlit secrets for deployment and local development.
        Get a free token at <a href="https://aqicn.org/api/" target="_blank">aqicn.org/api</a>.
        The <code>demo</code> token works for limited testing only.
      </div>
      <div style="margin-bottom:.5rem"><b>Tomorrow.io key (optional)</b><br>
        Configure your Tomorrow.io key in Streamlit secrets when you want real-time wind features.
        Get a free-tier key at <a href="https://www.tomorrow.io/" target="_blank">tomorrow.io</a>.
        The app runs fully without this key. Wind data will show as unavailable.
      </div>
      <div><b>Open-Meteo</b><br>
        No key required. Used automatically for the global overview and UV or hydration data.
      </div>
    </div>
    """,
        unsafe_allow_html=True,
    )
