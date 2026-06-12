"""
AirPulse Global — Premium Air Quality Intelligence Platform
================================================================
"""

# ---------------------------------------------------------------------------
# IMPORTS
# ---------------------------------------------------------------------------
import streamlit as st
import streamlit.components.v1 as components
from streamlit.errors import StreamlitSecretNotFoundError
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
import requests
import logging
import math
import re
import html
import io
import json
import os
import time
import urllib.parse
from datetime import datetime, timedelta, date
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
import sys
from pathlib import Path as SysPath
from json import JSONDecodeError

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.units import cm, mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from PIL import Image, ImageDraw, ImageFont
import base64
from requests import RequestException

PROJECT_ROOT = SysPath(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
ACTION_TRACKER_FILE = PROJECT_ROOT / "data" / "processed" / "action_tracker.json"
VISITOR_COUNT_FILE = PROJECT_ROOT / "visitor_count.txt"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

try:
    from airpulse.forecasting import generate_forecast_bundle
    from airpulse.config import FORECAST_DEFAULTS, OFFLINE_FORECAST_METRICS_FILES
    from airpulse.action_engine import (
        load_action_profile,
        persist_action_snapshot as persist_action_snapshot_file,
        build_top_actions,
        compute_action_score,
    )
    from airpulse.visitor import read_visitor_count, increment_visitor_count
except ImportError:
    generate_forecast_bundle = None
    FORECAST_DEFAULTS = {
        "live_api_cache_ttl_seconds": 600,
        "forecast_input_cache_ttl_seconds": 1800,
        "wind_cache_ttl_seconds": 900,
        "station_map_cache_ttl_seconds": 900,
        "global_overview_cache_ttl_seconds": 1800,
        "analytics_cache_ttl_seconds": 3600,
    }
    OFFLINE_FORECAST_METRICS_FILES = {}
    load_action_profile = None
    persist_action_snapshot_file = None
    build_top_actions = None
    compute_action_score = None
    read_visitor_count = None
    increment_visitor_count = None

try:
    import airpulse.analytics_engine as analytics_engine
except ImportError:
    analytics_engine = None

try:
    from airpulse.i18n import initialize_i18n, set_language, get_lang, translate as _t, format_date, format_datetime
except ImportError:
    def initialize_i18n():
        st.session_state.lang = st.session_state.get("lang", "en")
        return st.session_state.lang
    def set_language(lang: str):
        st.session_state.lang = lang
        return lang
    def get_lang():
        return st.session_state.get("lang", "en")
    def _t(key: str, **kwargs):
        try:
            return key.format(**kwargs)
        except (KeyError, ValueError, IndexError):
            return key
    def format_date(value):
        return value.strftime("%Y-%m-%d") if value else ""
    def format_datetime(value):
        return value.strftime("%Y-%m-%d %H:%M") if value else ""


def tt(en_text: str, tr_text: str) -> str:
    return en_text

import folium
from streamlit_folium import st_folium
from folium.plugins import HeatMap

from airpulse.pages import render_about_page


LOGGER = logging.getLogger("airpulse.app")


def configure_logging() -> None:
    if LOGGER.handlers:
        return
    level = logging.DEBUG if os.getenv("AIRPULSE_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"} else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def log_event(level: int, event: str, **fields) -> None:
    try:
        payload = json.dumps(fields, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = str(fields)
    LOGGER.log(level, "%s | %s", event, payload)


def debug_exception(context: str, exc: BaseException) -> None:
    log_event(logging.ERROR, context, error_type=type(exc).__name__, error=str(exc))


def ui_data_warning(message: str) -> None:
    st.warning(message)


@st.cache_resource
def get_http_session():
    """Reuse HTTP connections across reruns to reduce request overhead."""
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def http_get_json(
    url: str,
    *,
    params: dict | None = None,
    timeout: int = 12,
    retries: int = 2,
    backoff_seconds: float = 0.6,
    service: str = "external_api",
):
    last_error = None
    session = get_http_session()
    for attempt in range(1, retries + 2):
        try:
            log_event(logging.INFO, "api_request_start", service=service, url=url, params=params, attempt=attempt)
            response = session.get(url, params=params, timeout=timeout)
            if response.status_code >= 500:
                last_error = RuntimeError(f"HTTP {response.status_code}")
                log_event(logging.WARNING, "api_request_retryable_status", service=service, url=url, status_code=response.status_code, attempt=attempt)
                if attempt <= retries:
                    time.sleep(backoff_seconds * attempt)
                    continue
                return None
            if response.status_code != 200:
                log_event(logging.WARNING, "api_request_non_200", service=service, url=url, status_code=response.status_code, attempt=attempt)
                return None
            try:
                payload = response.json()
            except (ValueError, JSONDecodeError) as exc:
                last_error = exc
                log_event(logging.WARNING, "api_parse_failed", service=service, url=url, attempt=attempt, error=str(exc))
                if attempt <= retries:
                    time.sleep(backoff_seconds * attempt)
                    continue
                return None
            log_event(logging.INFO, "api_request_success", service=service, url=url, attempt=attempt)
            return payload
        except RequestException as exc:
            last_error = exc
            log_event(logging.WARNING, "api_request_failed", service=service, url=url, attempt=attempt, error_type=type(exc).__name__, error=str(exc))
            if attempt <= retries:
                time.sleep(backoff_seconds * attempt)
                continue
    if last_error is not None:
        debug_exception("api_request_exhausted", last_error)
    return None


def safe_render_folium_map(map_obj, *, height: int, warning_message: str) -> None:
    try:
        st_folium(map_obj, width=None, height=height, returned_objects=[])
    except (TypeError, ValueError, RuntimeError) as exc:
        debug_exception("map_render_failed", exc)
        ui_data_warning(warning_message)


def safe_generate_report_assets(report_builder, social_builder):
    try:
        pdf_bytes = report_builder()
    except (OSError, ValueError, RuntimeError) as exc:
        debug_exception("report_pdf_generation_failed", exc)
        pdf_bytes = None
    try:
        jpeg_bytes = social_builder()
    except (OSError, ValueError, RuntimeError) as exc:
        debug_exception("report_social_card_generation_failed", exc)
        jpeg_bytes = None
    return pdf_bytes, jpeg_bytes

# ---------------------------------------------------------------------------
# PAGE CONFIG
# ---------------------------------------------------------------------------
st.set_page_config(
    layout="wide",
    page_title="AirPulse Global | Air Quality Intelligence",
    page_icon="🌍",
    initial_sidebar_state="expanded",
)

# ===========================================================================
# DESIGN SYSTEM — CSS
# ===========================================================================
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');

:root {
  --blue:   #007AFF; --green:  #34C759; --yellow: #FFCC00;
  --orange: #FF9500; --red:    #FF3B30; --purple: #AF52DE;
  --gray:   #8E8E93; --bg:     #F2F2F7; --card:   #FFFFFF;
  --text:   #1D1D1F; --text2:  #3A3A3C; --border: rgba(0,0,0,.07);
}
*, *::before, *::after { box-sizing: border-box; }
html, body, [class*="css"] {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  -webkit-font-smoothing: antialiased;
}
.card, .mcard, .info-card, .plant-card, .campaign-card, .checklist-wrap, .model-box,
.card *, .mcard *, .info-card *, .plant-card *, .campaign-card *, .checklist-wrap *, .model-box * {
  min-width: 0;
  word-break: normal;
  overflow-wrap: break-word;
  hyphens: none;
}
.main { background: var(--bg); }
.main .block-container { padding: 1.6rem 2.15rem 4.2rem; max-width: 1600px; }

/* ── SIDEBAR ─────────────────────────────────────── */
[data-testid="stSidebar"] {
  background: linear-gradient(180deg,#1a1a2e 0%,#16213e 60%,#0f3460 100%);
  border-right: none;
}
[data-testid="stSidebar"] * { color: #e8eaf6 !important; }
[data-testid="stSidebar"] .stRadio label {
  padding: .45rem .75rem; border-radius: 10px; transition: background .15s;
}
[data-testid="stSidebar"] .stRadio label:hover { background: rgba(255,255,255,.1); }
[data-testid="stSidebarContent"] hr { border-color: rgba(255,255,255,.12) !important; }

/* ── HERO ─────────────────────────────────────────── */
.hero {
  background: linear-gradient(135deg,#007AFF 0%,#5856D6 50%,#AF52DE 100%);
  border-radius: 24px; padding: 2.5rem 3rem; margin-bottom: 2rem;
  position: relative; overflow: hidden; color: #fff;
}
.hero::after {
  content:''; position:absolute; inset:0;
  background:url("data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%23ffffff' fill-opacity='0.04'%3E%3Cpath d='M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E");
}
.hero-inner { position: relative; z-index:1; }
.hero-title  { font-size:2.4rem; font-weight:900; margin:0; letter-spacing:-.03em; }
.hero-sub    { font-size:1.05rem; opacity:.88; margin-top:.4rem; }
.hero-badge  {
  display:inline-flex; align-items:center; gap:8px; margin-top:1.2rem;
  padding:7px 16px; background:rgba(255,255,255,.18); border-radius:20px;
  font-size:.85rem; font-weight:600; backdrop-filter:blur(8px);
}
.pulse { width:8px;height:8px;border-radius:50%;background:#34C759;animation:pulse 2s infinite; }
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.5;transform:scale(.8)}}
@keyframes airpulseShimmer{0%{background-position:100% 0}100%{background-position:0 0}}

/* ── PAGE GUIDE ───────────────────────────────────── */
.page-guide {
  background: #fff; border-radius: 14px; padding: 1rem 1.5rem;
  border-left: 4px solid var(--blue); margin-bottom: 1.5rem;
  font-size: .9rem; color: var(--text2); line-height: 1.6;
  box-shadow: 0 1px 4px rgba(0,0,0,.05);
}
.page-guide strong {
  color: var(--text) !important;
  font-weight: 800 !important;
}

/* ── LIVE BADGE ───────────────────────────────────── */
.live-badge {
  display:inline-flex; align-items:center; gap:6px;
  background:#e8f5e9; color:#1b5e20;
  padding:4px 12px; border-radius:20px;
  font-size:.72rem; font-weight:700; letter-spacing:.04em;
}

/* ── CARDS ────────────────────────────────────────── */
.card {
  background: var(--card); border-radius: 18px; padding: 1.5rem;
  box-shadow: 0 1px 4px rgba(0,0,0,.06), 0 4px 16px rgba(0,0,0,.04);
  border: 1px solid var(--border); transition: transform .2s, box-shadow .2s;
}
.card:hover { transform:translateY(-2px); box-shadow:0 8px 24px rgba(0,0,0,.1); }
.card-dark {
  background: linear-gradient(135deg,#1d1d1f,#2c2c2e);
  color:#fff; border:none;
}
.section-label {
  font-size:.7rem; font-weight:700; letter-spacing:.08em;
  text-transform:uppercase; color:var(--gray); margin-bottom:.5rem;
}
.section-title {
  font-size:1.5rem; font-weight:800; color:var(--text);
  margin:2.5rem 0 1.25rem; padding-bottom:.6rem;
  border-bottom:3px solid var(--blue); display:inline-block;
}

/* ── AQI WIDGET ───────────────────────────────────── */
.aqi-widget {
  background:var(--card); border-radius:20px; padding:1.5rem;
  box-shadow:0 2px 8px rgba(0,0,0,.07); border:1px solid var(--border);
  position:relative; overflow:hidden; transition:all .25s;
}
.aqi-widget:hover { transform:translateY(-4px); box-shadow:0 12px 28px rgba(0,0,0,.12); }
.equal-height-panel {
  min-height: 318px;
  height: 100%;
}
.aqi-widget.equal-height-panel {
  display:flex;
  flex-direction:column;
}
.aqi-widget.equal-height-panel .pols {
  margin-top:auto;
}
.aqi-widget::before {
  content:''; position:absolute; top:0; left:0; right:0; height:4px;
  background:var(--wcolor,var(--green));
}
.w-head  { display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:.75rem; }
.w-city  { font-size:1rem; font-weight:700; color:var(--text); }
.w-badge {
  padding:3px 10px; border-radius:10px; font-size:.65rem; font-weight:700;
  text-transform:uppercase; background:var(--wbg,#e8f5e9); color:var(--wtext,#1b5e20);
}
.w-aqi   { font-size:3.2rem; font-weight:900; color:var(--wcolor,var(--green)); line-height:1; }
.w-desc  { font-size:.8rem; color:#555; margin:.5rem 0 1rem; min-height:36px; line-height:1.45; }
.pols    { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; padding-top:.9rem; border-top:1px solid var(--border); }
.pol-box { text-align:center; }
.pol-lbl { font-size:.6rem; color:var(--gray); font-weight:700; letter-spacing:.06em; text-transform:uppercase; }
.pol-val { font-size:.95rem; font-weight:800; color:var(--text); margin-top:2px; }
.wind-row{
  display:flex; align-items:center; justify-content:center; gap:6px;
  margin-top:.75rem; padding-top:.75rem; border-top:1px solid var(--border);
  font-size:.8rem; color:#555;
}

/* ── METRIC GRID ──────────────────────────────────── */
.aq-overview {
  background: var(--card);
  border-radius: 22px;
  border: 1px solid var(--border);
  box-shadow: 0 2px 10px rgba(0,0,0,.05);
  overflow: hidden;
  margin-top: 1rem;
}
.aq-overview-top {
  display:grid;
  grid-template-columns: 120px 1.5fr 1fr;
  gap: 1.25rem;
  align-items: center;
  padding: 1.5rem;
}
.aq-ring {
  width: 98px;
  height: 98px;
  border-radius: 50%;
  display:flex;
  align-items:center;
  justify-content:center;
  font-size: 2rem;
  font-weight: 900;
  color: var(--text);
  margin: 0 auto;
  background:
    radial-gradient(closest-side, white 79%, transparent 80% 100%),
    conic-gradient(var(--aq-color, var(--green)) calc(var(--aq-pct, .5) * 1turn), #e9eef5 0);
}
.aq-summary-title {
  font-size: .85rem;
  text-transform: uppercase;
  letter-spacing: .08em;
  color: var(--gray);
  font-weight: 700;
  margin-bottom: .45rem;
}
.aq-summary-level {
  font-size: 2rem;
  font-weight: 900;
  color: var(--text);
  line-height: 1;
  margin-bottom: .45rem;
}
.aq-summary-copy {
  font-size: .95rem;
  color: #3A3A3C;
  line-height: 1.7;
}
.aq-primary {
  border-left: 1px solid var(--border);
  padding-left: 1.25rem;
}
.aq-primary-label {
  font-size: .82rem;
  text-transform: uppercase;
  letter-spacing: .08em;
  color: var(--gray);
  font-weight: 700;
  margin-bottom: .5rem;
}
.aq-primary-name {
  font-size: 1.2rem;
  font-weight: 800;
  color: var(--text);
  margin-bottom: .35rem;
}
.aq-primary-copy {
  font-size: .9rem;
  line-height: 1.65;
  color: #3A3A3C;
}
.aq-pollutants {
  border-top: 1px solid var(--border);
  display:grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.aq-pol-card {
  padding: 1.15rem 1.35rem;
  border-right: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
}
.aq-pol-card:nth-child(2n) {
  border-right: none;
}
.aq-pol-head {
  display:flex;
  align-items:flex-start;
  gap: .9rem;
}
.aq-pol-score {
  flex: 0 0 54px;
  width: 54px;
  height: 54px;
  border-radius: 50%;
  display:flex;
  align-items:center;
  justify-content:center;
  font-size: 1.05rem;
  font-weight: 900;
  color: var(--text);
  background:
    radial-gradient(closest-side, white 77%, transparent 78% 100%),
    conic-gradient(var(--pol-color, var(--green)) calc(var(--pol-pct, .5) * 1turn), #e9eef5 0);
}
.aq-pol-name {
  font-size: 1.05rem;
  font-weight: 800;
  color: var(--text);
  line-height: 1.35;
}
.aq-pol-level {
  font-size: .9rem;
  font-weight: 700;
  margin-top: .15rem;
}
.aq-pol-value {
  font-size: .92rem;
  color: #4b5563;
  margin-top: .25rem;
}
.aq-pol-copy {
  font-size: .85rem;
  color: #6b7280;
  line-height: 1.6;
  margin-top: .55rem;
}
.mcard {
  background:var(--card); border-radius:16px; padding:1.4rem;
  box-shadow:0 1px 4px rgba(0,0,0,.06); border:1px solid var(--border);
  transition:all .2s; margin-bottom:.5rem;
}
.mcard:hover { transform:translateY(-2px); box-shadow:0 6px 18px rgba(0,0,0,.08); }
.m-label { font-size:.68rem; font-weight:700; letter-spacing:.07em; text-transform:uppercase; color:var(--gray); margin-bottom:.4rem; }
.m-value { font-size:2rem; font-weight:800; color:var(--text); line-height:1.1; }
.m-unit  { font-size:.8rem; color:var(--gray); font-weight:500; margin-top:.15rem; }
.metric-grid {
  display:grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 1rem;
  align-items: stretch;
  margin-bottom: .5rem;
}
.metric-grid .mcard {
  height: 100%;
  margin-bottom: 0;
}
.metric-grid .m-value {
  font-size: clamp(1.55rem, 3vw, 2.1rem);
}
.action-card-grid {
  display:grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 1rem;
  align-items: stretch;
  margin-bottom: .8rem;
}
.action-card-title {
  font-size: 1rem;
  font-weight: 800;
  color: #1D1D1F;
  line-height: 1.35;
  margin-bottom: .35rem;
}
.action-card-copy {
  font-size: .86rem;
  line-height: 1.62;
  color: #4B5563;
}
.action-score-mini-grid {
  display:grid;
  grid-template-columns: repeat(auto-fit, minmax(82px, 1fr));
  gap: .7rem;
  margin-top: 1rem;
}

/* ── ACTION HERO ──────────────────────────────────── */
.action-hero {
  border-radius:24px; padding:2.8rem 3rem; margin-bottom:2rem;
  position:relative; overflow:hidden;
}
.ah-good     { background:linear-gradient(135deg,#34C759,#007AFF); color:#fff; }
.ah-moderate { background:linear-gradient(135deg,#FFCC00,#FF9500); color:#1D1D1F; }
.ah-high     { background:linear-gradient(135deg,#FF9500,#FF3B30); color:#fff; }
.ah-badge    {
  display:inline-flex; gap:8px; align-items:center; padding:9px 18px;
  background:rgba(255,255,255,.2); border-radius:20px; font-weight:600;
  backdrop-filter:blur(8px); margin-bottom:1rem;
}

/* ── SAVINGS CARD ─────────────────────────────────── */
.savings-card {
  background:linear-gradient(135deg,#34C759,#30D158); color:#fff;
  border-radius:20px; padding:2rem; text-align:center; margin-bottom:.9rem;
}
.sv-num   { font-size:3rem; font-weight:900; margin:0; }
.sv-label { font-size:.9rem; opacity:.9; }
.sv-extra { margin-top:1rem; padding-top:1rem; border-top:1px solid rgba(255,255,255,.25); font-size:.82rem; opacity:.9; }

/* ── CHECKLIST ────────────────────────────────────── */
.checklist-wrap { background:var(--card); border-radius:18px; padding:1.5rem; box-shadow:0 2px 8px rgba(0,0,0,.06); }
.score-ring-wrap { text-align:center; padding:1.5rem; margin-bottom:.85rem; }
.score-num   { font-size:4rem; font-weight:900; }
.badge-label { font-size:1.2rem; font-weight:800; margin-top:.5rem; }

/* ── PLANT / CAMPAIGN ─────────────────────────────── */
.plant-card, .campaign-card {
  background:var(--card); border-radius:16px; padding:1.3rem;
  box-shadow:0 1px 4px rgba(0,0,0,.06); border:1px solid var(--border);
  height:100%; transition:all .2s;
}
.plant-card:hover, .campaign-card:hover { transform:translateY(-3px); box-shadow:0 8px 22px rgba(0,0,0,.1); }
.p-icon { font-size:2.2rem; margin-bottom:.5rem; }
.p-name { font-weight:700; color:var(--text); margin-bottom:.25rem; }
.p-benefit { color:var(--green); font-size:.82rem; font-weight:600; margin-bottom:.4rem; }
.p-care { font-size:.78rem; color:var(--text2); line-height:1.5; }
.c-tag  { display:inline-block; padding:3px 10px; background:rgba(0,122,255,.1); color:var(--blue); border-radius:10px; font-size:.68rem; font-weight:700; margin-bottom:.5rem; }
.c-name { font-weight:700; color:var(--text); margin-bottom:.3rem; }
.c-desc { font-size:.8rem; color:var(--text2); line-height:1.5; margin-bottom:.7rem; }
.c-link { color:var(--blue); font-size:.82rem; font-weight:600; text-decoration:none; }

/* ── HEALTH GUIDANCE ──────────────────────────────── */
.health-card { border-radius:14px; padding:1.2rem; margin:.4rem 0; }

/* ── SHARE ────────────────────────────────────────── */
.share-row { display:flex; gap:.75rem; margin-top:1rem; flex-wrap:wrap; }
.share-btn {
  padding:.65rem 1.4rem; border-radius:12px; font-size:.85rem; font-weight:700;
  color:#fff; text-decoration:none; display:inline-block; letter-spacing:.01em;
}
.s-tw { background:#000; }
.s-wa { background:#25D366; }
.s-li { background:#0A66C2; }

/* ── INFO CARDS ───────────────────────────────────── */
.info-card { background:var(--card); border-radius:16px; padding:1.4rem; box-shadow:0 1px 4px rgba(0,0,0,.06); border:1px solid var(--border); }
.api-ok  { display:inline-flex; align-items:center; gap:6px; font-size:.75rem; padding:3px 10px; border-radius:10px; background:rgba(52,199,89,.12); color:#1b5e20; font-weight:600; }
.api-err { display:inline-flex; align-items:center; gap:6px; font-size:.75rem; padding:3px 10px; border-radius:10px; background:rgba(255,59,48,.12); color:#FF3B30; font-weight:600; }

/* ── FORECAST MODEL BOX ───────────────────────────── */
.model-box {
  background: #f8f9ff; border: 1px solid #c7d2fe;
  border-radius: 14px; padding: 1.2rem 1.5rem; margin-top: 1rem;
}
.model-box h4 { margin: 0 0 .5rem; color: #1D1D1F; font-size: .95rem; }
.model-box p  { margin: 0; font-size: .85rem; color: #3A3A3C; line-height: 1.7; }

/* ── VISITOR / LIVE row ───────────────────────────── */
.status-bar {
  display:flex; align-items:center; gap:1rem; flex-wrap:wrap;
  padding:.6rem 0; margin-bottom:.5rem; font-size:.8rem;
}

/* ── TAKE ACTION TABS ─────────────────────── */
.stTabs [data-baseweb="tab-list"] {
  gap: .8rem;
  margin-bottom: 1.4rem;
  flex-wrap: wrap;
}
.stTabs [data-baseweb="tab-list"] button[role="tab"] {
  flex: 1 1 180px;
  min-height: 58px;
  padding: .9rem 1.1rem;
  border-radius: 16px;
  border: 1px solid rgba(0,0,0,.08);
  background: rgba(255,255,255,.82);
  box-shadow: 0 1px 4px rgba(0,0,0,.04);
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  transition: all .18s ease;
}
.stTabs [data-baseweb="tab-list"] button[role="tab"]:hover {
  background: #ffffff;
  border-color: rgba(0,122,255,.22);
  box-shadow: 0 6px 18px rgba(0,0,0,.06);
}
.stTabs [data-baseweb="tab-list"] button[role="tab"][aria-selected="true"] {
  background: linear-gradient(135deg, rgba(0,122,255,.12), rgba(88,86,214,.08));
  border-color: rgba(0,122,255,.35);
  box-shadow: 0 8px 22px rgba(0,122,255,.08);
}
.stTabs [data-baseweb="tab-list"] button[role="tab"] p {
  margin: 0;
  font-size: .95rem;
  font-weight: 700;
  line-height: 1.25;
}

/* ── HIDE Folium layer control on Dashboard (no control added there) ──── */
/* Station map: hide the ugly tile URL label from layer control */
.leaflet-control-layers-base label span {
  display: none !important;
}
.leaflet-control-layers-base {
  display: none !important;
}
.leaflet-control-layers-separator {
  display: none !important;
}

/* ── MINIMISE Folium attribution ─────────────────── */
.leaflet-control-attribution {
  display: none !important;
}

/* ── RESPONSIVE ───────────────────────────────────── */
@media(max-width:768px){
  .hero-title{font-size:1.7rem;}
  .aqi-widget{padding:1rem;}
  .aq-overview-top{grid-template-columns:1fr;}
  .aq-primary{border-left:none;padding-left:0;border-top:1px solid var(--border);padding-top:1rem;}
  .aq-pollutants{grid-template-columns:1fr;}
  .aq-pol-card{border-right:none;}
}
</style>
""", unsafe_allow_html=True)


def inject_runtime_styles() -> None:
    """Re-apply critical global styles on every Streamlit rerun."""
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"] {
          background: linear-gradient(180deg,#1a1a2e 0%,#16213e 60%,#0f3460 100%) !important;
          border-right: none !important;
        }
        [data-testid="stSidebar"] > div,
        [data-testid="stSidebarContent"],
        [data-testid="stSidebarUserContent"] {
          background: transparent !important;
        }
        [data-testid="stSidebar"] * {
          color: #e8eaf6 !important;
        }
        [data-testid="stSidebar"] hr {
          border-color: rgba(255,255,255,.12) !important;
        }
        [data-testid="stSidebarNav"] {
          background: transparent !important;
        }
        [data-testid="stSidebarNav"] a,
        [data-testid="stSidebarNav"] a * ,
        [data-testid="stSidebarNav"] button,
        [data-testid="stSidebarNav"] button * {
          color: #e8eaf6 !important;
        }
        [data-testid="stSidebarNav"] ul {
          gap: .32rem !important;
        }
        [data-testid="stSidebarNav"] li {
          margin: 0 !important;
        }
        [data-testid="stSidebarNav"] a,
        [data-testid="stSidebarNav"] button {
          transition: background .18s ease, transform .18s ease, box-shadow .18s ease !important;
          border: 1px solid transparent !important;
        }
        [data-testid="stSidebarNav"] a:hover,
        [data-testid="stSidebarNav"] button:hover {
          background: linear-gradient(135deg, rgba(255,255,255,.10), rgba(255,255,255,.05)) !important;
          border-radius: 12px !important;
          border-color: rgba(255,255,255,.08) !important;
          transform: none !important;
          box-shadow: 0 6px 14px rgba(0,0,0,.10) !important;
        }
        [data-testid="stSidebarNav"] a[aria-current="page"],
        [data-testid="stSidebarNav"] button[aria-current="page"] {
          background: linear-gradient(135deg, rgba(255,255,255,.18), rgba(255,255,255,.10)) !important;
          border-radius: 12px !important;
          border-color: rgba(255,255,255,.12) !important;
          box-shadow: inset 0 1px 0 rgba(255,255,255,.06), 0 8px 18px rgba(0,0,0,.12) !important;
        }
        [data-testid="stSidebarNav"] a span,
        [data-testid="stSidebarNav"] button span {
          font-weight: 600 !important;
          font-size: .98rem !important;
          letter-spacing: -.01em !important;
        }
        .sidebar-brand-shell {
          margin: .15rem 0 1.2rem;
          padding: 1.15rem 1rem 1.1rem;
          text-align: center;
          border-radius: 16px;
          background:
            linear-gradient(180deg, rgba(255,255,255,.07), rgba(255,255,255,.03)),
            radial-gradient(circle at top, rgba(96,165,250,.12), transparent 58%);
          border: 1px solid rgba(255,255,255,.08);
          box-shadow: inset 0 1px 0 rgba(255,255,255,.05), 0 12px 24px rgba(0,0,0,.14);
          backdrop-filter: blur(8px);
        }
        .sidebar-brand-title {
          font-size: 1.8rem;
          font-weight: 900;
          color: #f8fbff;
          letter-spacing: -.03em;
          text-shadow: 0 4px 18px rgba(96,165,250,.16);
        }
        .sidebar-brand-tag {
          font-size: .74rem;
          color: #b7c9f7;
          letter-spacing: .16em;
          text-transform: uppercase;
          margin-top: .3rem;
        }
        .sidebar-brand-pills {
          display:flex;
          justify-content:center;
          align-items:center;
          gap:.5rem;
          flex-wrap:wrap;
          margin-top:.85rem;
        }
        .sidebar-pill-live {
          display:inline-flex;
          align-items:center;
          gap:6px;
          padding:5px 11px;
          border-radius:999px;
          background:rgba(52,199,89,.16);
          color:#d6ffe1;
          font-size:.7rem;
          font-weight:800;
          letter-spacing:.08em;
          text-transform:uppercase;
          border:1px solid rgba(52,199,89,.18);
        }
        .sidebar-pill-views {
          display:inline-flex;
          align-items:center;
          padding:5px 11px;
          border-radius:999px;
          background:rgba(255,255,255,.08);
          color:#e6eeff;
          font-size:.72rem;
          font-weight:700;
          border:1px solid rgba(255,255,255,.08);
        }
        .main {
          background:
            radial-gradient(circle at top left, rgba(255,255,255,.65), transparent 24%),
            linear-gradient(180deg, #f5f7fb 0%, #eef2f8 100%);
        }
        .card, .mcard, .info-card, .plant-card, .campaign-card, .checklist-wrap, .model-box,
        .card *, .mcard *, .info-card *, .plant-card *, .campaign-card *, .checklist-wrap *, .model-box * {
          min-width: 0 !important;
          word-break: normal !important;
          overflow-wrap: break-word !important;
          hyphens: none !important;
        }
        .main .block-container { padding: 1.5rem 2.1rem 4rem; max-width: 1540px; }
        .hero {
          background:
            radial-gradient(circle at top left, rgba(255,255,255,.12), transparent 26%),
            linear-gradient(135deg,#2b6fdd 0%,#4767d2 50%,#6d63c9 100%);
          border-radius: 26px; padding: 2rem 2.5rem; margin-bottom: 1.5rem;
          position: relative; overflow: hidden; color: #fff;
          box-shadow: 0 22px 44px rgba(65,89,163,.16);
        }
        .hero::after {
          content:''; position:absolute; inset:0;
          background:
            linear-gradient(180deg, rgba(255,255,255,.02), rgba(255,255,255,.01)),
            url("data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%23ffffff' fill-opacity='0.025'%3E%3Cpath d='M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E");
        }
        .hero-inner { position: relative; z-index:1; }
        .hero-title { font-size:2.2rem; font-weight:900; margin:0; letter-spacing:-.04em; max-width:14ch; }
        .hero-sub { font-size:1rem; opacity:.88; margin-top:.5rem; color:rgba(255,255,255,.90); max-width:42ch; line-height:1.55; }
        .hero-badge {
          display:inline-flex; align-items:center; gap:8px; margin-top:1.2rem;
          padding:8px 15px; background:rgba(255,255,255,.10); border-radius:999px;
          font-size:.79rem; font-weight:700; backdrop-filter:blur(10px);
          border: 1px solid rgba(255,255,255,.07);
          color: rgba(255,255,255,.94);
        }
        .page-guide {
          background: rgba(255,255,255,.88);
          border-radius: 18px; padding: 1rem 1.25rem;
          border: 1px solid rgba(15,23,42,.06);
          border-left: 4px solid #0f7af7; margin-bottom: 1.6rem;
          font-size: .92rem; color: #425066; line-height: 1.7;
          box-shadow: 0 10px 30px rgba(15,23,42,.05);
        }
        .page-guide strong { color: #1D1D1F !important; font-weight: 800 !important; }
        .status-bar {
          display:flex; align-items:center; gap:.8rem; flex-wrap:wrap;
          padding:.35rem 0 .15rem; margin-bottom:1rem; font-size:.8rem;
        }
        .live-badge {
          display:inline-flex; align-items:center; gap:6px;
          background:#e9f6ec; color:#1b5e20;
          padding:5px 12px; border-radius:999px;
          font-size:.7rem; font-weight:800; letter-spacing:.06em;
          border:1px solid rgba(52,199,89,.10);
        }
        .status-copy { color:#7b8799; font-size:.82rem; }
        .card, .mcard, .info-card, .plant-card, .campaign-card, .checklist-wrap, .model-box {
          background:#FFFFFF; border-radius:18px; border:1px solid rgba(15,23,42,.06);
          box-shadow:0 10px 24px rgba(15,23,42,.05), 0 2px 6px rgba(15,23,42,.04);
        }
        .card, .mcard, .info-card { padding:1.2rem 1.25rem; }
        .card-dark {
          background: linear-gradient(135deg,#1d1d1f,#2c2c2e);
          color:#fff; border:none;
        }
        .plant-card, .campaign-card { padding:1.3rem; height:100%; }
        .model-box {
          background:#f8fbff; border:1px solid rgba(59,130,246,.12); border-radius:16px;
          padding:1.15rem 1.25rem; margin-top:1rem;
        }
        .model-box h4 { margin:0 0 .45rem; color:#162033; font-size:1rem; }
        .model-box p { margin:0; font-size:.88rem; color:#4c5c73; line-height:1.72; }
        .section-title {
          font-size:1.38rem; font-weight:850; color:#162033;
          margin:2.2rem 0 1rem; padding-bottom:.4rem;
          border-bottom:2px solid rgba(15,122,247,.18); display:inline-block;
        }
        .m-label {
          font-size:.67rem; font-weight:800; letter-spacing:.12em;
          text-transform:uppercase; color:#8b96a7; margin-bottom:.55rem;
        }
        .mcard { min-height: 142px; }
        .m-value { font-size:2.1rem; font-weight:850; color:#162033; line-height:1.05; letter-spacing:-.03em; }
        .m-unit { font-size:.84rem; color:#7e8a9f; font-weight:600; margin-top:.35rem; line-height:1.45; }
        .metric-grid {
          display:grid;
          grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
          gap: 1rem;
          align-items: stretch;
          margin-bottom: .5rem;
        }
        .metric-grid .mcard { height:100%; margin-bottom:0; }
        .metric-grid .m-value { font-size: clamp(1.55rem, 3vw, 2.1rem); }
        .action-card-grid {
          display:grid;
          grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
          gap: 1rem;
          align-items: stretch;
          margin-bottom: .8rem;
        }
        .action-card-title {
          font-size: 1rem;
          font-weight: 800;
          color: #1D1D1F;
          line-height: 1.35;
          margin-bottom: .35rem;
        }
        .action-card-copy {
          font-size: .86rem;
          line-height: 1.62;
          color: #4B5563;
        }
        .action-score-mini-grid {
          display:grid;
          grid-template-columns: repeat(auto-fit, minmax(82px, 1fr));
          gap:.7rem;
          margin-top:1rem;
        }
        .aqi-widget {
          background:#FFFFFF; border-radius:20px; padding:1.5rem;
          box-shadow:0 10px 24px rgba(15,23,42,.05); border:1px solid rgba(15,23,42,.06);
          position:relative; overflow:hidden; transition:all .25s;
        }
        .aqi-widget::before {
          content:''; position:absolute; top:0; left:0; right:0; height:4px;
          background:var(--wcolor,#34C759);
        }
        .w-head { display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:.75rem; gap:.75rem; }
        .w-city { font-size:1rem; font-weight:700; color:#1D1D1F; }
        .w-badge {
          padding:3px 10px; border-radius:10px; font-size:.65rem; font-weight:700;
          text-transform:uppercase; background:var(--wbg,#e8f5e9); color:var(--wtext,#1b5e20);
        }
        .w-aqi { font-size:3rem; font-weight:900; color:var(--wcolor,#34C759); line-height:1; }
        .w-desc { font-size:.84rem; color:#555; margin:.55rem 0 1rem; min-height:52px; line-height:1.55; }
        .pols { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; padding-top:.9rem; border-top:1px solid rgba(0,0,0,.07); }
        .pol-box { text-align:center; min-width:0; }
        .pol-lbl { font-size:.6rem; color:#8E8E93; font-weight:700; letter-spacing:.06em; text-transform:uppercase; }
        .pol-val { font-size:.95rem; font-weight:800; color:#1D1D1F; margin-top:2px; }
        .wind-row {
          display:flex; align-items:center; justify-content:center; gap:6px;
          margin-top:.75rem; padding-top:.75rem; border-top:1px solid rgba(0,0,0,.07);
          font-size:.8rem; color:#555;
        }
        .action-hero {
          border-radius:24px; padding:2rem 2.2rem; margin-bottom:1.6rem;
          position:relative; overflow:hidden;
        }
        .ah-good { background:linear-gradient(135deg,#34C759,#007AFF); color:#fff; }
        .ah-moderate { background:linear-gradient(135deg,#FFCC00,#FF9500); color:#1D1D1F; }
        .ah-high { background:linear-gradient(135deg,#FF9500,#FF3B30); color:#fff; }
        .savings-card {
          background:linear-gradient(135deg,#34C759,#30D158); color:#fff;
          border-radius:20px; padding:2rem; text-align:center; margin-bottom:.9rem;
        }
        .sv-num { font-size:3rem; font-weight:900; margin:0; }
        .sv-label { font-size:.9rem; opacity:.9; }
        .sv-extra { margin-top:1rem; padding-top:1rem; border-top:1px solid rgba(255,255,255,.25); font-size:.82rem; opacity:.9; }
        .score-ring-wrap { text-align:center; padding:1.5rem; margin-bottom:.85rem; }
        .score-num { font-size:4rem; font-weight:900; }
        .badge-label { font-size:1.2rem; font-weight:800; margin-top:.5rem; }
        .share-row { display:flex; gap:.75rem; margin-top:1rem; flex-wrap:wrap; }
        .share-btn {
          padding:.72rem 1.15rem; border-radius:12px; font-size:.84rem; font-weight:800;
          color:#fff; text-decoration:none; display:inline-block; letter-spacing:.01em;
          box-shadow:0 8px 18px rgba(15,23,42,.10);
        }
        .s-tw { background:#000; }
        .s-wa { background:#25D366; }
        .s-li { background:#0A66C2; }
        .stButton > button,
        .stDownloadButton > button {
          border-radius: 14px !important;
          border: 1px solid rgba(15,23,42,.08) !important;
          background: linear-gradient(180deg, #ffffff, #f7f9fc) !important;
          color: #162033 !important;
          font-weight: 700 !important;
          min-height: 2.9rem !important;
          box-shadow: 0 8px 18px rgba(15,23,42,.06) !important;
          transition: transform .16s ease, box-shadow .16s ease, border-color .16s ease !important;
        }
        .stButton > button:hover,
        .stDownloadButton > button:hover {
          transform: translateY(-1px) !important;
          border-color: rgba(15,122,247,.18) !important;
          box-shadow: 0 12px 24px rgba(15,23,42,.08) !important;
        }
        .stPlotlyChart,
        [data-testid="stPlotlyChart"] {
          background: rgba(255,255,255,.72);
          border: 1px solid rgba(15,23,42,.05);
          border-radius: 18px;
          padding: .65rem .75rem .45rem;
          box-shadow: 0 10px 24px rgba(15,23,42,.04);
        }
        .action-block-gap { height: 1rem; }
        .action-chart-shell {
          background: rgba(255,255,255,.76);
          border: 1px solid rgba(15,23,42,.05);
          border-radius: 22px;
          box-shadow: 0 12px 28px rgba(15,23,42,.05);
          padding: .45rem .55rem .2rem;
          margin-top: 1rem;
        }
        .action-chart-shell .stPlotlyChart,
        .action-chart-shell [data-testid="stPlotlyChart"] {
          background: transparent;
          border: none;
          box-shadow: none;
          padding: 0;
          border-radius: 0;
        }
        .stCaption, .stMarkdown p {
          color: #516074;
        }
        .stTabs [data-baseweb="tab-list"] {
          gap:.65rem; margin-bottom:1.15rem; flex-wrap:wrap;
        }
        .stTabs [data-baseweb="tab-list"] button[role="tab"] {
          flex:1 1 180px; min-height:54px; padding:.82rem 1rem; border-radius:15px;
          border:1px solid rgba(0,0,0,.08); background:rgba(255,255,255,.82);
          box-shadow:0 1px 4px rgba(0,0,0,.04);
        }
        .stTabs [data-baseweb="tab-list"] button[role="tab"][aria-selected="true"] {
          background:linear-gradient(135deg, rgba(0,122,255,.12), rgba(88,86,214,.08));
          border-color:rgba(0,122,255,.35); box-shadow:0 8px 22px rgba(0,122,255,.08);
        }
        @media(max-width:768px){
          .hero-title{font-size:1.75rem; max-width:none;}
          .hero-sub{max-width:none;}
          .aqi-widget{padding:1rem;}
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

# ===========================================================================
# CONSTANTS
# ===========================================================================
AQI_LEVELS = [
    {"key":"good",           "min":0,   "max":50,   "color":"#34C759","bg":"#e8f5e9","text":"#1B5E20","icon":"😊"},
    {"key":"moderate",       "min":51,  "max":100,  "color":"#FFCC00","bg":"#fff8e1","text":"#F57F17","icon":"😐"},
    {"key":"sensitive",      "min":101, "max":150,  "color":"#FF9500","bg":"#fff3e0","text":"#E65100","icon":"😷"},
    {"key":"unhealthy",      "min":151, "max":200,  "color":"#FF3B30","bg":"#ffebee","text":"#B71C1C","icon":"🤢"},
    {"key":"very_unhealthy", "min":201, "max":300,  "color":"#AF52DE","bg":"#f3e5f5","text":"#4A148C","icon":"😵"},
    {"key":"hazardous",      "min":301, "max":9999, "color":"#8E2B26","bg":"#efebe9","text":"#3E2723","icon":"☠️"},
]

POLLUTANT_INFO = {
    "pm25":{"full":"PM2.5","unit":"µg/m³","who":15},
    "pm10":{"full":"PM10", "unit":"µg/m³","who":45},
    "o3":  {"full":"O₃",   "unit":"µg/m³","who":100},
    "no2": {"full":"NO₂",  "unit":"µg/m³","who":25},
    "so2": {"full":"SO₂",  "unit":"µg/m³","who":40},
    "co":  {"full":"CO",   "unit":"mg/m³","who":4},
}

GLOBAL_CITIES = {
    "Istanbul, TR":   {"lat":41.0082,"lon":28.9784,"region":"Europe"},
    "London, UK":     {"lat":51.5074,"lon":-0.1278,"region":"Europe"},
    "Paris, FR":      {"lat":48.8566,"lon":2.3522, "region":"Europe"},
    "Berlin, DE":     {"lat":52.520, "lon":13.405, "region":"Europe"},
    "Madrid, ES":     {"lat":40.4168,"lon":-3.7038,"region":"Europe"},
    "Rome, IT":       {"lat":41.9028,"lon":12.4964,"region":"Europe"},
    "Warsaw, PL":     {"lat":52.229, "lon":21.012, "region":"Europe"},
    "Amsterdam, NL":  {"lat":52.374, "lon":4.898,  "region":"Europe"},
    "New York, US":   {"lat":40.7128,"lon":-74.006,"region":"N. America"},
    "Los Angeles, US":{"lat":34.052, "lon":-118.24,"region":"N. America"},
    "Chicago, US":    {"lat":41.878, "lon":-87.63, "region":"N. America"},
    "Toronto, CA":    {"lat":43.651, "lon":-79.347,"region":"N. America"},
    "São Paulo, BR":  {"lat":-23.55, "lon":-46.63, "region":"S. America"},
    "Buenos Aires, AR":{"lat":-34.603,"lon":-58.381,"region":"S. America"},
    "Mexico City, MX":{"lat":19.43,  "lon":-99.13, "region":"N. America"},
    "Bogotá, CO":     {"lat":4.711,  "lon":-74.072,"region":"S. America"},
    "Tokyo, JP":      {"lat":35.676, "lon":139.65, "region":"Asia"},
    "Seoul, KR":      {"lat":37.566, "lon":126.978,"region":"Asia"},
    "Beijing, CN":    {"lat":39.904, "lon":116.407,"region":"Asia"},
    "Shanghai, CN":   {"lat":31.23,  "lon":121.47, "region":"Asia"},
    "Delhi, IN":      {"lat":28.614, "lon":77.209, "region":"Asia"},
    "Mumbai, IN":     {"lat":19.076, "lon":72.878, "region":"Asia"},
    "Bangkok, TH":    {"lat":13.756, "lon":100.502,"region":"Asia"},
    "Singapore, SG":  {"lat":1.352,  "lon":103.82, "region":"Asia"},
    "Jakarta, ID":    {"lat":-6.21,  "lon":106.85, "region":"Asia"},
    "Dhaka, BD":      {"lat":23.81,  "lon":90.41,  "region":"Asia"},
    "Karachi, PK":    {"lat":24.86,  "lon":67.01,  "region":"Asia"},
    "Cairo, EG":      {"lat":30.044, "lon":31.236, "region":"Africa"},
    "Lagos, NG":      {"lat":6.455,  "lon":3.384,  "region":"Africa"},
    "Nairobi, KE":    {"lat":-1.292, "lon":36.822, "region":"Africa"},
    "Accra, GH":      {"lat":5.603,  "lon":-0.187, "region":"Africa"},
    "Casablanca, MA": {"lat":33.573, "lon":-7.589, "region":"Africa"},
    "Johannesburg, ZA":{"lat":-26.205,"lon":28.04, "region":"Africa"},
    "Addis Ababa, ET":{"lat":9.025,  "lon":38.747, "region":"Africa"},
    "Sydney, AU":     {"lat":-33.87, "lon":151.21, "region":"Oceania"},
    "Melbourne, AU":  {"lat":-37.81, "lon":144.96, "region":"Oceania"},
    "Dubai, AE":      {"lat":25.20,  "lon":55.27,  "region":"Middle East"},
    "Riyadh, SA":     {"lat":24.688, "lon":46.722, "region":"Middle East"},
    "Tehran, IR":     {"lat":35.694, "lon":51.421, "region":"Middle East"},
}

WIND_DIRECTIONS = {
    (0,22):  "N", (23,67):  "NE", (68,112): "E", (113,157): "SE",
    (158,202):"S",(203,247):"SW",(248,292): "W", (293,337): "NW", (338,360):"N",
}

# ===========================================================================
# SESSION STATE
# ===========================================================================
def init_session_state():
    defaults = {
        "city": "Istanbul, TR",
        "waqi_key": "demo",
        "selected_station_uid": None,
        "selected_station_name": None,
        "forecast_source": "city",
        "commute_mode": "Car",
        "commute_km": 10.0,
        "commute_saved": 0.0,
        "fp_monthly": None,
        "fp_status": None,
        "checklist": {k: False for k in [
            "windows_closed","public_transport","plants_watered","reduced_meat",
            "checked_aqi","avoided_car","shared_awareness","protected_health"]},
        "streak": 0,
        "last_date": None,
        "waqi_city_data": None,
        "nearby_stations": [],
        "global_df": None,
        "global_df_ts": None,
        "visit_count": 0,
        "visitor_tracked": False,
        "current_page": None,
        "scroll_after_render": False,
        "action_profile_name": "Default User",
        "report_user_name": "Default User",
        "action_profile_flags": {
            "asthma": False,
            "child": False,
            "elderly": False,
        },
        "action_history_loaded_for": None,
        "action_history": [],
        "action_score": 0,
        "action_top3": [],
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
    if not st.session_state.visitor_tracked:
        st.session_state.visit_count = increment_visitor_count(VISITOR_COUNT_FILE) if increment_visitor_count else 1
        st.session_state.visitor_tracked = True
    elif st.session_state.visit_count == 0:
        st.session_state.visit_count = read_visitor_count(VISITOR_COUNT_FILE) if read_visitor_count else 1


def current_location_context():
    city = st.session_state.city
    coords = GLOBAL_CITIES.get(city, {"lat": 0.0, "lon": 0.0, "region": "Global"})
    selected_station = None
    if st.session_state.get("forecast_source") == "station" and st.session_state.get("selected_station_uid"):
        selected_station = {
            "uid": st.session_state.selected_station_uid,
            "name": st.session_state.selected_station_name or "Selected station",
        }
    return city, coords, selected_station


def load_action_profile_into_session(profile_name: str) -> None:
    profile = load_action_profile(ACTION_TRACKER_FILE, profile_name) if load_action_profile else {}
    flags = profile.get("flags")
    history = profile.get("history", [])
    if isinstance(flags, dict):
        st.session_state.action_profile_flags.update(flags)
    st.session_state.action_history = history if isinstance(history, list) else []
    st.session_state.action_history_loaded_for = profile_name


def persist_action_snapshot(profile_name: str, payload: dict) -> None:
    if persist_action_snapshot_file:
        history = persist_action_snapshot_file(
            ACTION_TRACKER_FILE,
            profile_name,
            dict(st.session_state.action_profile_flags),
            payload,
        )
    else:
        history = st.session_state.action_history
    st.session_state.action_history = history
    st.session_state.action_history_loaded_for = profile_name

# ===========================================================================
# AQI HELPERS
# ===========================================================================
def aqi_info(aqi: float) -> dict:
    aqi = float(aqi or 0)
    for lvl in AQI_LEVELS:
        if lvl["min"] <= aqi <= lvl["max"]:
            out = dict(lvl)
            out["name"] = _t(f"aqi.{lvl['key']}.name")
            out["desc"] = _t(f"aqi.{lvl['key']}.desc")
            return out
    out = dict(AQI_LEVELS[-1])
    out["name"] = _t(f"aqi.{out['key']}.name")
    out["desc"] = _t(f"aqi.{out['key']}.desc")
    return out

def wind_dir_label(deg: float) -> str:
    deg = float(deg or 0) % 360
    for (lo, hi), lbl in WIND_DIRECTIONS.items():
        if lo <= deg <= hi:
            return lbl
    return "N"

def wind_icon(deg: float) -> str:
    dirs = ["↑","↗","→","↘","↓","↙","←","↖"]
    idx = round((float(deg or 0) % 360) / 45) % 8
    return dirs[idx]

# ===========================================================================
# API KEY HELPERS
# ===========================================================================
# ---------------------------------------------------------------------------
# API KEY HELPERS — imported from airpulse.services.secrets
# ---------------------------------------------------------------------------
from airpulse.services.secrets import (
    safe_secret_get as _safe_secret_get,
    read_local_secret_file as _read_local_secret_file,
    read_api_key as _read_api_key,
    is_debug_mode,
    render_secret_warnings,
    get_tomorrow_key,
    get_waqi_key,
)


# ===========================================================================
# WAQI / AQICN API
# ===========================================================================
@st.cache_data(ttl=FORECAST_DEFAULTS["live_api_cache_ttl_seconds"], show_spinner=False)
def waqi_city(city: str):
    """Live AQI should stay fresh, so city feed uses a short 5-minute TTL."""
    api_key = get_waqi_key()
    city_name = city.split(",")[0].strip()
    coords = GLOBAL_CITIES.get(city, {})
    target_lat = coords.get("lat")
    target_lon = coords.get("lon")
    city_name_lc = city_name.lower()

    def _score_station_candidate(name: str, lat: float | None, lon: float | None) -> float:
        name_lc = str(name or "").lower()
        score = 0.0
        if city_name_lc == name_lc:
            score -= 1000
        elif name_lc.startswith(city_name_lc):
            score -= 500
        elif city_name_lc in name_lc:
            score -= 250
        if lat is not None and lon is not None and target_lat is not None and target_lon is not None:
            try:
                score += abs(float(lat) - float(target_lat)) + abs(float(lon) - float(target_lon))
            except (TypeError, ValueError):
                score += 9999.0
        return score

    # Mirror the JS demo more closely: resolve a concrete station from map bounds,
    # then fetch the station detail with feed/@uid.
    if target_lat is not None and target_lon is not None:
        bounds = f"{target_lat - 0.5},{target_lon - 0.5},{target_lat + 0.5},{target_lon + 0.5}"
        d = http_get_json(
            "https://api.waqi.info/v2/map/bounds/",
            params={"latlng": bounds, "token": api_key},
            timeout=25,
            retries=1,
            service="waqi_city_bounds",
        )
        if isinstance(d, dict) and d.get("status") == "ok":
            ranked = []
            for item in d.get("data", []):
                uid = item.get("uid")
                if uid in (None, "", 0):
                    continue
                station = item.get("station", {}) or {}
                ranked.append((
                    _score_station_candidate(
                        station.get("name", ""),
                        item.get("lat"),
                        item.get("lon"),
                    ),
                    uid,
                ))
            if ranked:
                ranked.sort(key=lambda x: x[0])
                station_data = waqi_station(ranked[0][1])
                if station_data:
                    return station_data

    # Prefer a concrete station feed resolved from AQICN search so city cards
    # stay closer to what users see on aqicn.org station pages.
    search_results = waqi_search(city_name)
    if search_results:
        ranked = []
        for item in search_results:
            station = item.get("station", {}) or {}
            geo = station.get("geo", []) or []
            uid = item.get("uid")
            if uid in (None, "", 0):
                continue
            lat = geo[0] if len(geo) >= 1 else None
            lon = geo[1] if len(geo) >= 2 else None
            ranked.append((_score_station_candidate(station.get("name", ""), lat, lon), uid))
        if ranked:
            ranked.sort(key=lambda x: x[0])
            station_data = waqi_station(ranked[0][1])
            if station_data:
                return station_data

    slug = city_name.lower().replace(" ", "-")
    for target in [slug, city.split(",")[0].strip(), city]:
        d = http_get_json(
            f"https://api.waqi.info/feed/{target}/",
            params={"token": api_key},
            timeout=12,
            retries=1,
            service="waqi_city",
        )
        if isinstance(d, dict) and d.get("status") == "ok":
            return d.get("data", {})
        if isinstance(d, dict):
            log_event(logging.INFO, "fallback_activation", source="waqi_city_target_fallback", target=target, status=d.get("status"))
    return None

@st.cache_data(ttl=FORECAST_DEFAULTS["live_api_cache_ttl_seconds"], show_spinner=False)
def waqi_station(uid):
    """Station detail follows the same freshness policy as other live AQI reads."""
    api_key = get_waqi_key()
    sid = f"@{uid}" if not str(uid).startswith("@") else uid
    d = http_get_json(
        f"https://api.waqi.info/feed/{sid}/",
        params={"token": api_key},
        timeout=12,
        retries=1,
        service="waqi_station",
    )
    if isinstance(d, dict) and d.get("status") == "ok":
        return d.get("data", {})
    return None

@st.cache_data(ttl=FORECAST_DEFAULTS["live_api_cache_ttl_seconds"], show_spinner=False)
def waqi_search(keyword: str):
    """Search results are live-ish but stable enough for a short cache window."""
    api_key = get_waqi_key()
    d = http_get_json(
        "https://api.waqi.info/search/",
        params={"token": api_key, "keyword": keyword},
        timeout=12,
        retries=1,
        service="waqi_search",
    )
    if isinstance(d, dict) and d.get("status") == "ok":
        return d.get("data", [])
    return []

@st.cache_data(ttl=FORECAST_DEFAULTS["station_map_cache_ttl_seconds"], show_spinner=False)
def waqi_nearby(lat: float, lon: float, city_name: str = ""):
    """Nearby station lists are map-oriented live data, cached briefly to cut repeated map calls."""
    api_key = get_waqi_key()
    stations = []
    d = http_get_json(
        f"https://api.waqi.info/feed/geo:{lat};{lon}/",
        params={"token": api_key},
        timeout=12,
        retries=1,
        service="waqi_nearby_geo",
    )
    if isinstance(d, dict) and d.get("status") == "ok":
        raw = d.get("data", {})
        try:
            stations.append(_parse_station_from_feed(raw, lat, lon, nearest=True))
        except (TypeError, ValueError, KeyError) as exc:
            debug_exception("waqi_nearby_geo_parse_failed", exc)
    for off in [2.0]:
        bounds = f"{lat-off},{lon-off},{lat+off},{lon+off}"
        d = http_get_json(
            "https://api.waqi.info/v2/map/bounds/",
            params={"latlng": bounds, "token": api_key},
            timeout=25,
            retries=1,
            service="waqi_nearby_bounds",
        )
        if isinstance(d, dict) and d.get("status") == "ok":
            for s in d.get("data", []):
                try:
                    uid = s.get("uid")
                    if any(x.get("uid") == uid for x in stations):
                        continue
                    stations.append({
                        "name": s.get("station", {}).get("name", "Station"),
                        "aqi":  pd.to_numeric(s.get("aqi", 0), errors="coerce"),
                        "lat":  float(s.get("lat", lat)),
                        "lon":  float(s.get("lon", lon)),
                        "uid":  uid,
                        "is_nearest": False,
                        "dominentpol": None,
                        "iaqi": {},
                        "time": s.get("station", {}).get("time", ""),
                        "pm25": None, "pm10": None, "o3": None, "no2": None,
                    })
                except (TypeError, ValueError, KeyError) as exc:
                    debug_exception("waqi_nearby_bounds_parse_failed", exc)
    if len(stations) < 4 and city_name:
        log_event(logging.INFO, "fallback_activation", source="waqi_nearby_search_fallback", city=city_name, station_count=len(stations))
        for s in waqi_search(city_name.split(",")[0])[:8]:
            uid = s.get("uid")
            if any(x.get("uid") == uid for x in stations):
                continue
            geo = s.get("station", {}).get("geo", [lat, lon])
            stations.append({
                "name": s.get("station", {}).get("name", city_name),
                "aqi":  pd.to_numeric(s.get("aqi", 0), errors="coerce"),
                "lat":  float(geo[0]), "lon": float(geo[1]),
                "uid":  uid, "is_nearest": False,
                "dominentpol": None, "iaqi": {},
                "time": (s.get("time") or {}).get("stime", "") if isinstance(s.get("time"), dict) else "",
                "pm25": None, "pm10": None, "o3": None, "no2": None,
            })
    return [s for s in stations if not pd.isna(s.get("aqi", 0))][:200]

def _parse_station_from_feed(raw, lat, lon, nearest=False):
    geo = raw.get("city", {}).get("geo", [lat, lon])
    iaqi = raw.get("iaqi", {}) or {}
    def iv(k):
        v = iaqi.get(k, {})
        return v.get("v") if isinstance(v, dict) else None
    return {
        "name": raw.get("city", {}).get("name", "Nearest Station"),
        "aqi":  pd.to_numeric(raw.get("aqi", 0), errors="coerce"),
        "lat":  float(geo[0]) if len(geo) > 0 else lat,
        "lon":  float(geo[1]) if len(geo) > 1 else lon,
        "uid":  raw.get("idx", 0),
        "is_nearest": nearest,
        "dominentpol": raw.get("dominentpol", "pm25"),
        "iaqi": iaqi,
        "time": raw.get("time", {}).get("s", ""),
        "pm25": iv("pm25"), "pm10": iv("pm10"),
        "o3":   iv("o3"),   "no2":  iv("no2"),
    }

def process_feed(raw: dict) -> dict:
    if not raw:
        return {}
    try:
        iaqi = raw.get("iaqi", {}) or {}
        def iv(k):
            v = iaqi.get(k, {})
            return float(v.get("v", 0)) if isinstance(v, dict) else 0.0
            
        def iaqi_to_pm25_conc(aqi: float) -> float:
            if aqi <= 50: return aqi * (12.0 / 50.0)
            elif aqi <= 100: return 12.1 + ((aqi - 51) / 49.0) * (35.4 - 12.1)
            elif aqi <= 150: return 35.5 + ((aqi - 101) / 49.0) * (55.4 - 35.5)
            elif aqi <= 200: return 55.5 + ((aqi - 151) / 49.0) * (150.4 - 55.5)
            elif aqi <= 300: return 150.5 + ((aqi - 201) / 99.0) * (250.4 - 150.5)
            elif aqi <= 400: return 250.5 + ((aqi - 301) / 99.0) * (350.4 - 250.5)
            else: return 350.5 + ((max(aqi, 401) - 401) / 99.0) * (500.4 - 350.5)

        geo = raw.get("city", {}).get("geo", [0, 0])
        pm25_iaqi = iv("pm25")
        
        return {
            "aqi":         pd.to_numeric(raw.get("aqi", 0), errors="coerce") or 0,
            "pm25":        iaqi_to_pm25_conc(pm25_iaqi) if pm25_iaqi > 0 else 0, 
            "pm25_iaqi":   pm25_iaqi,
            "pm10":        iv("pm10"),
            "o3":          iv("o3"),   "no2":   iv("no2"),
            "so2":         iv("so2"),  "co":    iv("co"),
            "wind_speed":  iv("w"),    "wind_dir": iv("wg"),
            "dominentpol": raw.get("dominentpol", "pm25"),
            "station_name":raw.get("city", {}).get("name", ""),
            "timestamp":   raw.get("time", {}).get("s", ""),
            "lat":         float(geo[0]) if len(geo) > 0 else 0,
            "lon":         float(geo[1]) if len(geo) > 1 else 0,
            "forecast":    raw.get("forecast", {}).get("daily", {}),
            "iaqi":        iaqi,
        }
    except (TypeError, ValueError, KeyError) as exc:
        debug_exception("process_feed_failed", exc)
        return {}


@st.cache_data(ttl=FORECAST_DEFAULTS["live_api_cache_ttl_seconds"], show_spinner=False)
def get_processed_city_feed(city: str) -> dict:
    """Memoize feed parsing so multiple page sections reuse the same normalized AQI payload."""
    raw = waqi_city(city)
    return process_feed(raw) if raw else {}


@st.cache_data(ttl=FORECAST_DEFAULTS["station_map_cache_ttl_seconds"], show_spinner=False)
def get_live_city_snapshot(city: str, lat: float, lon: float, include_stations: bool = False) -> dict:
    """Bundle live AQI, wind, and optional nearby stations to avoid repeating per-page lookups."""
    snapshot = {
        "waqi": get_processed_city_feed(city),
        "wind": tomorrow_wind(lat, lon),
        "stations": [],
    }
    if include_stations:
        snapshot["stations"] = waqi_nearby(lat, lon, city)
    return snapshot

# ===========================================================================
# TOMORROW.IO WIND
# ===========================================================================
@st.cache_data(ttl=FORECAST_DEFAULTS["wind_cache_ttl_seconds"], show_spinner=False)
def tomorrow_wind(lat: float, lon: float):
    """Wind updates less aggressively than AQI, so a medium TTL reduces noisy reruns."""
    api_key = get_tomorrow_key()
    if not api_key:
        return None
    d = http_get_json(
        "https://api.tomorrow.io/v4/weather/realtime",
        params={"location": f"{lat},{lon}", "apikey": api_key, "units": "metric"},
        timeout=12,
        retries=1,
        service="tomorrow_wind",
    )
    if isinstance(d, dict):
        vals = d.get("data", {}).get("values", {})
        return {
            "speed":     vals.get("windSpeed"),
            "direction": vals.get("windDirection"),
            "gust":      vals.get("windGust"),
        }
    return None

# ===========================================================================
# GLOBAL WAQI OVERVIEW — Parallel fetch from the same city snapshot pipeline
# ===========================================================================
def _fetch_one_city(city_name, coords):
    """Fetch a city row using the same WAQI pipeline used by dashboard cards."""
    lat, lon = coords["lat"], coords["lon"]
    try:
        snapshot = get_live_city_snapshot(city_name, lat, lon, include_stations=False)
        waqi = snapshot.get("waqi") or {}
        if not waqi:
            return None
        wind = snapshot.get("wind") or {}
        aqi_val = pd.to_numeric(waqi.get("aqi", 0), errors="coerce")
        if pd.isna(aqi_val):
            return None
        return {
            "city": city_name,
            "lat": lat, "lon": lon,
            "region": coords["region"],
            "aqi":   float(aqi_val),
            "pm25":  float(waqi.get("pm25") or 0),
            "pm10":  float(waqi.get("pm10") or 0),
            "no2":   float(waqi.get("no2") or 0),
            "o3":    float(waqi.get("o3") or 0),
            "so2":   float(waqi.get("so2") or 0),
            "co":    float(waqi.get("co") or 0),
            "wind_speed": float((wind.get("speed") if wind else waqi.get("wind_speed")) or 0),
            "wind_dir":   float((wind.get("direction") if wind else waqi.get("wind_dir")) or 0),
        }
    except (TypeError, ValueError, KeyError) as exc:
        debug_exception("waqi_city_overview_parse_failed", exc)
        return None

@st.cache_data(ttl=FORECAST_DEFAULTS["global_overview_cache_ttl_seconds"], show_spinner=False)
def fetch_global_overview():
    """
    Fetch all cities in parallel using the same WAQI city snapshot pipeline
    used by the main live cards so AQI stays consistent across pages.
    """
    rows = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(_fetch_one_city, city, coords): city
            for city, coords in GLOBAL_CITIES.items()
        }
        for future in as_completed(futures, timeout=30):
            try:
                result = future.result(timeout=12)
                if result is not None:
                    rows.append(result)
            except TimeoutError as exc:
                debug_exception("global_overview_future_timeout", exc)
            except (RuntimeError, ValueError) as exc:
                debug_exception("global_overview_future_failed", exc)
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("city").reset_index(drop=True)
    return df


@st.cache_data(ttl=FORECAST_DEFAULTS["analytics_cache_ttl_seconds"], show_spinner=False)
def stations_dataframe(stations: list[dict]) -> pd.DataFrame:
    """Normalize station lists once so summaries/charts can reuse the same frame."""
    if not stations:
        return pd.DataFrame()
    df = pd.DataFrame(stations)
    if "aqi" in df.columns:
        df["aqi"] = pd.to_numeric(df["aqi"], errors="coerce")
        df = df.dropna(subset=["aqi"])
    return df


@st.cache_data(ttl=FORECAST_DEFAULTS["analytics_cache_ttl_seconds"], show_spinner=False)
def action_history_dataframe(history: list[dict]) -> pd.DataFrame:
    """Action history changes slowly, so memoizing its frame reduces repeated transforms."""
    if not history:
        return pd.DataFrame()
    df = pd.DataFrame(history)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"]).sort_values("date")
    return df

# ===========================================================================
# FORECAST ENGINE  — Holt-Winters Triple Exponential Smoothing
# ===========================================================================
def _holt_winters(series: np.ndarray, days: int, season_len: int = 7):
    """
    Triple exponential smoothing (additive seasonality).
    Returns forecast array + 90% confidence bands.
    Falls back to double-smoothing if series < 2*season_len.
    """
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
        residuals = np.std(np.diff(series)) if len(series) > 1 else series.mean() * 0.1
        sigma = max(residuals, series.mean() * 0.05)
        ci = 1.645 * sigma
        preds = np.array(preds)
        return preds, preds + ci * np.sqrt(np.arange(1, days + 1)), preds - ci * np.sqrt(np.arange(1, days + 1))

    seasons = np.array([series[i::season_len].mean() for i in range(season_len)])
    baseline = series.mean()
    seasons = seasons - baseline

    level = series[0]
    trend = (series[min(season_len, n-1)] - series[0]) / season_len * 0.2
    smoothed = []

    for t in range(n):
        s_t = seasons[t % season_len]
        prev_level = level
        level = alpha * (series[t] - s_t) + (1 - alpha) * (level + trend)
        trend = beta * (level - prev_level) + (1 - beta) * trend
        seasons[t % season_len] = gamma * (series[t] - level) + (1 - gamma) * s_t
        smoothed.append(level + trend + seasons[t % season_len])

    residuals = series - np.array(smoothed)
    sigma = np.std(residuals)

    preds, upper, lower = [], [], []
    for h in range(1, days + 1):
        s_h = seasons[(n + h - 1) % season_len]
        fc = level + trend * h + s_h
        ci = 1.645 * sigma * np.sqrt(h)
        preds.append(max(0, fc))
        upper.append(max(0, fc + ci))
        lower.append(max(0, fc - ci))

    return np.array(preds), np.array(upper), np.array(lower)


@st.cache_data(ttl=FORECAST_DEFAULTS["forecast_input_cache_ttl_seconds"], show_spinner=False)
def run_forecast(base_val: float, pollutant: str, days: int, label: str):
    """Forecast inputs refresh every 15 minutes to stay current without recomputing on every rerun."""
    seed = abs(hash(pollutant + label)) % 99991
    rng  = np.random.RandomState(seed)
    base_val = max(2.0, float(base_val or 20))

    n_hist = 60
    trend_component  = np.linspace(0, base_val * 0.08, n_hist)
    weekly_season    = np.sin(np.linspace(0, 2 * np.pi * (n_hist / 7), n_hist)) * base_val * 0.18
    weekend_effect   = np.array([(0.12 if i % 7 in (5, 6) else -0.06) * base_val for i in range(n_hist)])
    noise            = rng.normal(0, base_val * 0.07, n_hist)
    hist_vals        = np.clip(base_val + trend_component + weekly_season + weekend_effect + noise, 0.5, None)

    hist_dates = pd.date_range(end=datetime.now(), periods=n_hist, freq="D")
    hist_df    = pd.DataFrame({"date": hist_dates, pollutant: hist_vals})

    try:
        fc_vals, fc_upper, fc_lower = _holt_winters(hist_vals, days, season_len=7)
    except (ValueError, FloatingPointError, IndexError) as exc:
        log_event(logging.INFO, "fallback_activation", source="holt_winters_projection_fallback", error=str(exc))
        last = hist_vals[-1]
        fc_vals  = np.array([last * 0.95 ** i + base_val * (1 - 0.95 ** i) for i in range(days)])
        sigma    = np.std(hist_vals[-14:]) if len(hist_vals) >= 14 else base_val * 0.1
        fc_upper = fc_vals + 1.645 * sigma
        fc_lower = np.clip(fc_vals - 1.645 * sigma, 0, None)

    fc_dates = pd.date_range(start=hist_dates[-1] + timedelta(days=1), periods=days, freq="D")
    fc_df    = pd.DataFrame({"date": fc_dates, pollutant: fc_vals,
                              "upper": fc_upper, "lower": fc_lower})
    return hist_df, fc_df


@st.cache_data(ttl=FORECAST_DEFAULTS["forecast_input_cache_ttl_seconds"], show_spinner=False)
def _load_offline_quality_metrics(pollutant: str) -> dict:
    metrics_path = OFFLINE_FORECAST_METRICS_FILES.get(str(pollutant).lower().strip())
    if metrics_path is None or not metrics_path.exists():
        return {}
    try:
        payload = json.loads(metrics_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


@st.cache_data(ttl=FORECAST_DEFAULTS["forecast_input_cache_ttl_seconds"], show_spinner=False)
def _load_offline_quality_summary(pollutant: str) -> dict:
    payload = _load_offline_quality_metrics(pollutant)
    if not payload:
        return {}
    best_model_name = str(payload.get("best_model_name") or "").strip()
    for row in payload.get("model_tournament_results", []):
        if isinstance(row, dict) and str(row.get("model") or "").strip() == best_model_name:
            out = {
                "model": best_model_name,
                "mae": row.get("MAE"),
                "rmse": row.get("RMSE"),
                "r2": row.get("R2"),
                "mape": row.get("MAPE"),
                "points": payload.get("rows_used_best_model") or payload.get("test_rows"),
                "config": payload.get("selected_station_config"),
            }
            return out
    return {}


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
    except (AttributeError, TypeError, ValueError) as exc:
        debug_exception("waqi_daily_forecast_parse_failed", exc)
        return []

# ===========================================================================
# ACTION CALCULATORS
# ===========================================================================
COMMUTE_FACTORS = {
    "Car": 0.120, "Bus": 0.050, "Metro/Subway": 0.020,
    "Bicycle": 0.0, "Walking": 0.0, "Remote / No commute": 0.0,
}
MEAT_FACTORS = {
    "Every day": 0.30, "3 times/week": 0.20, "1 time/week": 0.10,
    "Vegetarian": 0.05, "Vegan": 0.02,
}

def calc_commute(mode, km):
    car_em  = COMMUTE_FACTORS["Car"] * km
    sel_em  = COMMUTE_FACTORS.get(mode, 0.12) * km
    saved   = max(0, car_em - sel_em)
    return {"daily": saved, "weekly": saved*5, "monthly": saved*22,
            "tree_days": saved/0.025 if saved > 0 else 0}

def calc_footprint(weekly_km, meat, flights_yr):
    drive   = (weekly_km * 4 * 0.12) / 1000
    fly     = (flights_yr * 0.25) / 12
    total   = drive + MEAT_FACTORS.get(meat, 0.10) + fly
    if total < 0.4:  return total, "low",  "🥳 Low Impact",    "low"
    if total <= 0.8: return total, "mod",  "😐 Moderate",      "mod"
    return total, "high", "😟 High Impact", "high"

def action_badge(score):
    if score <= 2:  return "🌱", "Starter",             "Begin building clean-air habits."
    if score <= 5:  return "⚡", "Active",              "Good momentum — habits are forming."
    return              "🏆", "Clean Air Champion", "Excellent discipline. Real impact."


_PDF_FONT_REGISTERED = False


# ---------------------------------------------------------------------------
# PDF / SOCIAL CARD EXPORT — imported from airpulse.services.reporting
# ---------------------------------------------------------------------------
from airpulse.services.reporting import (
    resolve_font_path,
    ensure_pdf_font,
    generate_pdf_report,
    generate_social_card,
)


# ===========================================================================
# STATION MAP
# ===========================================================================
# ---------------------------------------------------------------------------
# MAP HELPERS — imported from airpulse.components.maps
# ---------------------------------------------------------------------------
from airpulse.components.maps import build_station_map, build_station_bar



@st.cache_data(ttl=FORECAST_DEFAULTS["analytics_cache_ttl_seconds"], show_spinner=False)
def build_commute_savings_chart(commute_km: float, commute_mode: str):
    """Commute comparison chart is pure derived UI, so memoizing it removes redundant Plotly work."""
    all_modes = list(COMMUTE_FACTORS.keys())
    display_labels = {
        "Car": "Car",
        "Bus": "Bus",
        "Metro/Subway": "Metro<br>Subway",
        "Bicycle": "Bicycle",
        "Walking": "Walking",
        "Remote / No commute": "Remote<br>No commute",
    }
    savings = [max(0, COMMUTE_FACTORS["Car"] * commute_km - COMMUTE_FACTORS[m] * commute_km) for m in all_modes]
    fig_commute = go.Figure()
    fig_commute.add_trace(go.Bar(
        x=[display_labels.get(mode, mode) for mode in all_modes],
        y=savings,
        marker_color=["#34C759" if m == commute_mode else "#007AFF" for m in all_modes],
        text=[f"{v:.2f} kg" for v in savings],
        textposition="outside",
        cliponaxis=False,
    ))
    fig_commute.update_layout(
        title=f"Daily CO₂ savings by mode ({commute_km:.1f} km)",
        height=340,
        margin=dict(l=0, r=0, t=50, b=78),
        yaxis_title="kg CO₂ saved vs. car",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter"),
        showlegend=False,
        xaxis=dict(
            tickangle=0,
            automargin=True,
            fixedrange=True,
            tickfont=dict(size=12),
        ),
        yaxis=dict(
            automargin=True,
            fixedrange=True,
            tickfont=dict(size=12),
            title_font=dict(size=13),
        ),
        uniformtext_minsize=11,
        uniformtext_mode="hide",
    )
    return fig_commute


@st.cache_data(ttl=FORECAST_DEFAULTS["analytics_cache_ttl_seconds"], show_spinner=False)
def build_report_footprint_charts(fp_cats: list[str], fp_vals: list[float], fp_colors: list[str], fp_total: float):
    """Report footprint visuals depend only on numeric inputs, making them safe and useful to cache."""
    display_cats = {
        "🚗 Driving": "Driving",
        "🥩 Diet": "Diet",
        "✈️ Flights": "Flights",
        "⚡ Electricity": "Electricity",
        "🔥 Heating": "Heating",
    }
    short_cats = [display_cats.get(cat, cat) for cat in fp_cats]
    fig_fp = go.Figure(go.Bar(
        x=short_cats, y=fp_vals, marker_color=fp_colors,
        text=[f"{v:.3f} t" for v in fp_vals], textposition="outside",
        cliponaxis=False,
    ))
    fig_fp.update_layout(
        title=f"Carbon footprint breakdown — {fp_total:.2f} t CO₂/month",
        height=272, margin=dict(l=0, r=0, t=42, b=42),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter"), yaxis_title="t CO₂/month", title_font=dict(size=15),
        xaxis=dict(
            tickangle=0,
            automargin=True,
            fixedrange=True,
        ),
        yaxis=dict(
            automargin=True,
            fixedrange=True,
        ),
    )
    fig_donut = go.Figure(go.Pie(
        labels=["Driving","Diet","Flights","Electricity","Heating"],
        values=fp_vals, hole=0.58, marker_colors=fp_colors,
        textinfo="percent+label",
        textposition="inside",
        insidetextorientation="radial",
        sort=False,
    ))
    fig_donut.update_traces(
        hole=0.56,
        textfont_size=12,
        domain=dict(x=[0.16, 0.84], y=[0.10, 0.90]),
        hovertemplate="%{label}<br>%{value:.3f} t<br>%{percent}<extra></extra>",
    )
    fig_donut.update_layout(
        height=272, margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="rgba(0,0,0,0)", font=dict(family="Inter"), showlegend=False,
        uniformtext_minsize=11,
        uniformtext_mode="hide",
    )
    return fig_fp, fig_donut

# ===========================================================================
# COMPONENT RENDERERS
# ===========================================================================
def render_hero(title="AirPulse Global", subtitle="Professional Air Quality Intelligence"):
    st.markdown(f"""
    <div class="hero">
      <div class="hero-inner">
        <h1 class="hero-title">{title}</h1>
        <div class="hero-sub">{str(subtitle).replace("Â·", "·").replace("â€”", "-")}</div>
        <div class="hero-badge">
          <span class="pulse"></span>
          Live air intelligence · Global station network
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

def render_page_guide(text: str):
    if "<strong>" not in text and ":" in text:
        lead, rest = text.split(":", 1)
        text = f"<strong>{lead}:</strong>{rest}"
    st.markdown(f'<div class="page-guide">{text}</div>', unsafe_allow_html=True)

def render_live_bar(extra_html: str = ""):
    now = format_datetime(datetime.now())
    st.markdown(f"""
    <div class="status-bar">
      <span class="live-badge"><span class="pulse"></span> {_t("common.live")}</span>
      <span class="status-copy">{_t("common.data_refreshed", value=now)}</span>
      {extra_html}
    </div>
    """, unsafe_allow_html=True)

def render_aqi_widget(label, aqi, pm25=0, pm10=0, o3=0, wind_speed=None,
                       wind_dir=None, station_name=None, timestamp=None, extra_class=""):
    info = aqi_info(aqi)
    ws = f"{wind_speed:.1f} m/s" if wind_speed else "—"
    wd = f"{wind_icon(wind_dir or 0)} {wind_dir_label(wind_dir or 0)}" if wind_speed else ""
    ts_line = f"<div style='font-size:.7rem;color:#999;margin-top:.4rem'>🕐 {timestamp}</div>" if timestamp else ""
    widget_class = " ".join(x for x in ["aqi-widget", extra_class] if x)
    st.markdown(f"""
    <div class="{widget_class}" style="--wcolor:{info['color']};--wbg:{info['bg']};--wtext:{info['text']}">
      <div class="w-head">
        <span class="w-city">{label}</span>
        <span class="w-badge">{info['name']}</span>
      </div>
      <div style="display:flex;align-items:baseline;gap:10px">
        <span class="w-aqi">{int(aqi)}</span>
        <span style="font-size:1.6rem">{info['icon']}</span>
      </div>
      <div class="w-desc">{info['desc']}</div>
      <div class="pols">
        <div class="pol-box"><div class="pol-lbl">PM2.5</div><div class="pol-val">{pm25:.1f}</div></div>
        <div class="pol-box"><div class="pol-lbl">PM10</div><div class="pol-val">{pm10:.1f}</div></div>
        <div class="pol-box"><div class="pol-lbl">O₃</div><div class="pol-val">{o3:.1f}</div></div>
      </div>
      <div class="wind-row">💨 {ws} {wd}</div>
      {ts_line}
    </div>
    """, unsafe_allow_html=True)

def render_metric_grid(metrics: list):
    cards = []
    for lbl, val, unit, color in metrics:
        cards.append(f"""
        <div class="mcard">
          <div class="m-label">{html.escape(str(lbl))}</div>
          <div class="m-value" style="color:{html.escape(str(color))}">{html.escape(str(val))}</div>
          <div class="m-unit">{html.escape(str(unit))}</div>
        </div>
        """)
    st.markdown(f"<div class='metric-grid'>{''.join(cards)}</div>", unsafe_allow_html=True)

def render_section(title):
    clean_title = str(title).replace("Â·", "·").replace("â€”", "-")
    clean_title = re.sub(r'^[^A-Za-z0-9<]+', '', clean_title).strip()
    st.markdown(f'<div class="section-title">{clean_title}</div>', unsafe_allow_html=True)


def render_loading_skeleton(cards: int = 3) -> None:
    cols = st.columns(cards)
    for col in cols:
        with col:
            st.markdown(
                """
                <div class="card" style="min-height:116px;background:linear-gradient(90deg,#f3f4f6 25%,#e5e7eb 37%,#f3f4f6 63%);background-size:400% 100%;animation:airpulseShimmer 1.4s ease infinite;">
                  <div style="height:12px;width:38%;background:rgba(255,255,255,.5);border-radius:999px;margin-bottom:18px"></div>
                  <div style="height:28px;width:56%;background:rgba(255,255,255,.65);border-radius:10px;margin-bottom:14px"></div>
                  <div style="height:10px;width:72%;background:rgba(255,255,255,.45);border-radius:999px"></div>
                </div>
                """,
                unsafe_allow_html=True,
            )

# ===========================================================================
# AQI COMMENTARY HELPERS
# ===========================================================================
def _aqi_commentary(aqi: float, city_name: str, wind_speed=None, lang: str | None = None) -> str:
    info = aqi_info(aqi)
    lvl  = info["name"]
    ws   = float(wind_speed or 0)
    lang = lang or get_lang()
    if lang == "tr":
        wind_ctx = (
            "Guclu ruzgar kirleticileri dagitiyor; kosullar kisa surede iyilesebilir." if ws > 6
            else "Orta seviye ruzgar dogal havalandirma sagliyor." if ws > 2
            else "Durgun hava kirleticilerin birikmesine neden oluyor."
        )
        loc = f"{city_name} icin" if city_name else "bugun"
        base = {
            _t("aqi.good.name"): f"Hava kalitesi {loc} cok iyi. Disarida egzersiz, bisiklet ve pencere havalandirmasi uygun. {wind_ctx}",
            _t("aqi.moderate.name"): f"Hava kalitesi {loc} kabul edilebilir; ancak hassas kisiler uzun sureli dis ortam eforunu sinirlamali. {wind_ctx}",
            _t("aqi.sensitive.name"): f"Hassas gruplar {loc} dis ortam maruziyetini azaltmali. Kapali alan aktivitelerini tercih edin. {wind_ctx}",
            _t("aqi.unhealthy.name"): f"Hava kalitesi {loc} herkes icin sagliksiz. Dis sporlari azaltin ve yogun trafik guzergahlarindan kacinin. {wind_ctx}",
            _t("aqi.very_unhealthy.name"): f"{loc} kosullar cok sagliksiz. Mumkun oldugunca kapali alanda kalin. {wind_ctx}",
            _t("aqi.hazardous.name"): f"{loc} hava kirliligi tehlikeli seviyede. Gereksiz disari cikislardan kacinin. {wind_ctx}",
        }
        return base.get(lvl, info["desc"])

    wind_ctx = (
        "Strong winds are helping to disperse pollutants; conditions may improve soon." if ws > 6
        else "Moderate winds offer some natural ventilation." if ws > 2
        else "Calm winds mean pollutants are accumulating with limited dispersion."
    )
    loc = f"in {city_name}" if city_name else "today"
    base = {
        _t("aqi.good.name"): f"Air quality is excellent {loc}. Outdoor exercise, cycling, and open-window ventilation are all recommended. {wind_ctx}",
        _t("aqi.moderate.name"): f"Air quality is acceptable {loc}, though sensitive individuals should avoid prolonged outdoor exertion. {wind_ctx}",
        _t("aqi.sensitive.name"): f"Sensitive groups should reduce outdoor exposure {loc}. Consider indoor activities and keep windows closed. {wind_ctx}",
        _t("aqi.unhealthy.name"): f"Air quality is unhealthy for everyone {loc}. Reduce outdoor sports and avoid traffic-heavy routes. {wind_ctx}",
        _t("aqi.very_unhealthy.name"): f"Hazardous conditions {loc}; all residents should minimize outdoor activity. {wind_ctx}",
        _t("aqi.hazardous.name"): f"Extreme air pollution {loc}. Do not go outdoors unnecessarily. {wind_ctx}",
    }
    return base.get(lvl, info["desc"])

def _aqi_commentary_short(aqi: float, city_name: str) -> str:
    info = aqi_info(aqi)
    lvl  = info["name"]
    shorts = {
        _t("aqi.good.name"): "Harika bir dis ortam gunu." if get_lang() == "tr" else "Great day for outdoor activity.",
        _t("aqi.moderate.name"): "Hassas gruplar eforu azaltmali." if get_lang() == "tr" else "Sensitive groups should limit exertion.",
        _t("aqi.sensitive.name"): "Riskli gruplar disarida daha az kalmali." if get_lang() == "tr" else "At-risk groups: reduce outdoor time.",
        _t("aqi.unhealthy.name"): "Bugun uzun sure dis ortamdan kacin." if get_lang() == "tr" else "Avoid prolonged outdoor exposure today.",
        _t("aqi.very_unhealthy.name"): "Iceride kalin; herkes icin risk yuksek." if get_lang() == "tr" else "Stay indoors; health risk for all.",
        _t("aqi.hazardous.name"): "Acil durum seviyesi; dis aktiviteden kacinin." if get_lang() == "tr" else "Emergency conditions: avoid outdoor activity.",
    }
    return shorts.get(lvl, info["desc"])


POLLUTANT_DISPLAY = {
    "pm25": {
        "name": "PM2.5",
        "title": "PM2.5 (fine particulate matter)",
        "description": "Very small particles that can travel deep into the lungs and contribute most strongly to day-to-day air-quality risk.",
        "unit": "ug/m3",
    },
    "pm10": {
        "name": "PM10",
        "title": "PM10 (coarse particulate matter)",
        "description": "Larger airborne particles linked to road dust, construction activity, and mechanical abrasion.",
        "unit": "ug/m3",
    },
    "o3": {
        "name": "O3",
        "title": "O3 (ozone)",
        "description": "A reactive gas that often rises in sunny conditions and can irritate the airways during outdoor activity.",
        "unit": "ug/m3",
    },
    "no2": {
        "name": "NO2",
        "title": "NO2 (nitrogen dioxide)",
        "description": "A traffic-related gas pollutant commonly associated with combustion sources and roadside exposure.",
        "unit": "ug/m3",
    },
    "so2": {
        "name": "SO2",
        "title": "SO2 (sulfur dioxide)",
        "description": "A sharp, irritating gas that is usually linked to industrial fuel burning and certain heavy-emission sources.",
        "unit": "ug/m3",
    },
    "co": {
        "name": "CO",
        "title": "CO (carbon monoxide)",
        "description": "A combustion-related gas that tends to rise around enclosed traffic corridors and poorly ventilated burning sources.",
        "unit": "mg/m3",
    },
}


def pollutant_display_name(key: str) -> str:
    return POLLUTANT_DISPLAY.get(key, {}).get("name", key.upper())


def pollutant_full_title(key: str) -> str:
    return POLLUTANT_DISPLAY.get(key, {}).get("title", pollutant_display_name(key))


def pollutant_explainer(key: str) -> str:
    return POLLUTANT_DISPLAY.get(key, {}).get("description", "This pollutant is currently being monitored in the live city feed.")


def pollutant_status(value: float | None, who_limit: float | None) -> dict:
    val = float(value or 0)
    if not who_limit:
        return {"score": min(100, int(val)), "label": "Tracked", "color": "#8E8E93"}
    ratio = val / who_limit if who_limit else 0
    score = max(0, min(100, int(round(ratio * 50))))
    if ratio <= 0.5:
        return {"score": score, "label": "Good", "color": "#34C759"}
    if ratio <= 1.0:
        return {"score": score, "label": "Moderate", "color": "#FFCC00"}
    if ratio <= 1.5:
        return {"score": score, "label": "Elevated", "color": "#FF9500"}
    return {"score": score, "label": "High", "color": "#FF3B30"}


def render_air_quality_overview(city: str, waqi: dict, wind: dict | None = None):
    aqi_val = float(waqi.get("aqi", 0) or 0)
    info = aqi_info(aqi_val)
    dominant = (waqi.get("dominentpol") or "pm25").lower()
    dominant_title = pollutant_full_title(dominant)
    dominant_copy = pollutant_explainer(dominant)
    city_label = city.split(",")[0]
    summary = _aqi_commentary(aqi_val, city_label, float(wind["speed"]) if wind and wind.get("speed") else None, lang="en")
    aqi_pct = max(0.08, min(aqi_val, 300) / 300)

    pollutant_order = ["pm25", "co", "no2", "o3", "pm10", "so2"]
    pollutant_cards = []
    for key in pollutant_order:
        raw_value = waqi.get(key)
        value = float(raw_value or 0)
        meta = POLLUTANT_DISPLAY.get(key, {})
        who_limit = POLLUTANT_INFO.get(key, {}).get("who")
        status = pollutant_status(value, who_limit)
        pol_pct = max(0.06, status["score"] / 100)
        pollutant_cards.append(f"""
        <div class="aq-pol-card">
          <div class="aq-pol-head">
            <div class="aq-pol-score" style="--pol-color:{status['color']};--pol-pct:{pol_pct:.3f}">{status['score']}</div>
            <div>
              <div class="aq-pol-name">{meta.get("title", key.upper())}</div>
              <div class="aq-pol-level-row">
                <span class="aq-pol-level" style="color:{status['color']};background:{status['color']}18">{status['label']}</span>
              </div>
              <div class="aq-pol-value">{value:.2f} {meta.get('unit', 'ug/m3')}</div>
            </div>
          </div>
          <div class="aq-pol-copy">{meta.get("description", "")}</div>
        </div>
        """)

    overview_html = f"""
    <style>
      body {{
        margin: 0;
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        background: transparent;
      }}
      .aq-overview {{
        background: #FFFFFF;
        border-radius: 26px;
        border: 1px solid rgba(15,23,42,.06);
        box-shadow: 0 18px 40px rgba(15,23,42,.06), 0 4px 14px rgba(15,23,42,.04);
        overflow: hidden;
        margin-top: 0;
      }}
      .aq-overview-top {{
        display: grid;
        grid-template-columns: 92px 1.5fr 1fr;
        gap: 1.15rem;
        align-items: center;
        padding: 1.3rem 1.35rem;
        background:
          linear-gradient(135deg, rgba(248,250,252,.98), rgba(255,255,255,1) 52%),
          radial-gradient(circle at top left, rgba(0,122,255,.06), transparent 38%);
      }}
      .aq-ring {{
        width: 82px;
        height: 82px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1.45rem;
        font-weight: 900;
        color: #1D1D1F;
        margin: 0 auto;
        background:
          radial-gradient(closest-side, white 72%, transparent 73% 100%),
          conic-gradient(var(--aq-color, #34C759) calc(var(--aq-pct, .5) * 1turn), #e8eef7 0);
        box-shadow: inset 0 0 0 1px rgba(148,163,184,.10);
      }}
      .aq-summary-title {{
        font-size: .7rem;
        text-transform: uppercase;
        letter-spacing: .12em;
        color: #8E8E93;
        font-weight: 700;
        margin-bottom: .35rem;
      }}
      .aq-summary-level {{
        font-size: 1.55rem;
        font-weight: 900;
        color: #1D1D1F;
        line-height: 1.1;
        margin-bottom: .35rem;
      }}
      .aq-summary-copy {{
        font-size: .84rem;
        color: #4B5563;
        line-height: 1.65;
        max-width: 48ch;
      }}
      .aq-primary {{
        border-left: 1px solid rgba(148,163,184,.18);
        padding-left: 1.1rem;
      }}
      .aq-primary-label {{
        font-size: .68rem;
        text-transform: uppercase;
        letter-spacing: .12em;
        color: #8E8E93;
        font-weight: 700;
        margin-bottom: .35rem;
      }}
      .aq-primary-name {{
        font-size: 1.02rem;
        font-weight: 800;
        color: #1D1D1F;
        line-height: 1.35;
        margin-bottom: .3rem;
      }}
      .aq-primary-copy {{
        font-size: .82rem;
        line-height: 1.65;
        color: #4B5563;
      }}
      .aq-pollutants {{
        border-top: 1px solid rgba(148,163,184,.16);
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        background: #FCFDFE;
      }}
      .aq-pol-card {{
        padding: 1.05rem 1.15rem;
        border-right: 1px solid rgba(148,163,184,.14);
        border-bottom: 1px solid rgba(148,163,184,.14);
      }}
      .aq-pol-card:nth-child(2n) {{
        border-right: none;
      }}
      .aq-pol-head {{
        display: flex;
        align-items: flex-start;
        gap: .75rem;
      }}
      .aq-pol-score {{
        flex: 0 0 48px;
        width: 48px;
        height: 48px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: .95rem;
        font-weight: 900;
        color: #1D1D1F;
        background:
          radial-gradient(closest-side, white 73%, transparent 74% 100%),
          conic-gradient(var(--pol-color, #34C759) calc(var(--pol-pct, .5) * 1turn), #e8eef7 0);
        box-shadow: inset 0 0 0 1px rgba(148,163,184,.10);
      }}
      .aq-pol-name {{
        font-size: .92rem;
        font-weight: 800;
        color: #1D1D1F;
        line-height: 1.4;
      }}
      .aq-pol-level-row {{
        margin-top: .4rem;
      }}
      .aq-pol-level {{
        display: inline-flex;
        align-items: center;
        padding: .14rem .5rem;
        border-radius: 999px;
        font-size: .68rem;
        font-weight: 700;
        letter-spacing: .02em;
      }}
      .aq-pol-value {{
        font-size: .82rem;
        color: #475569;
        margin-top: .3rem;
      }}
      .aq-pol-copy {{
        font-size: .78rem;
        color: #667085;
        line-height: 1.55;
        margin-top: .65rem;
      }}
      @media (max-width: 768px) {{
        .aq-overview-top {{ grid-template-columns: 1fr; }}
        .aq-primary {{
          border-left: none;
          padding-left: 0;
          border-top: 1px solid rgba(148,163,184,.16);
          padding-top: 1rem;
        }}
        .aq-pollutants {{ grid-template-columns: 1fr; }}
        .aq-pol-card {{ border-right: none; }}
      }}
    </style>
    <div class="aq-overview">
      <div class="aq-overview-top">
        <div class="aq-ring" style="--aq-color:{info['color']};--aq-pct:{aqi_pct:.3f}">{int(aqi_val)}</div>
        <div>
          <div class="aq-summary-title">Today's Air Quality · {city}</div>
          <div class="aq-summary-level">{info['name']}</div>
          <div class="aq-summary-copy">{summary}</div>
        </div>
      </div>
      <div class="aq-pollutants">
        {''.join(pollutant_cards)}
      </div>
    </div>
    """
    components.html(overview_html, height=760, scrolling=False)

# ===========================================================================
# PAGE: DASHBOARD
# ===========================================================================
def page_dashboard():
    render_hero(tt("AirPulse Global", "AirPulse Global"), tt("Professional Air Quality Intelligence", "Profesyonel Hava Kalitesi Zekasi"))
    render_page_guide(
        tt("<strong>Overview:</strong> Search any city, review live AQI, explore the station map, and compare major cities in one clean view.", "<strong>Bu sayfa ne gosterir:</strong> Canli global hava kalitesi gorunumu. Herhangi bir sehrin guncel AQI degerini arayin, WAQI istasyon haritasini inceleyin ve dunyanin en temiz ya da en kirli sehirlerini gercek zamanli karsilastirin.")
    )

    api_key = get_waqi_key()
    wk      = get_tomorrow_key()

    # ── City selector ──
    c1, c2 = st.columns([3, 1])
    with c1:
        city_input = st.text_input("🔍 Search any global city", value=st.session_state.city, key="dash_city_in")
        st.session_state.city = city_input
        city = city_input
    with c2:
        region = st.selectbox(tt("Region filter", "Bolge filtresi"), ["All"] + sorted({v["region"] for v in GLOBAL_CITIES.values()}), key="dash_region")

    coords = GLOBAL_CITIES.get(city, {"lat": 0, "lon": 0, "region": "Global"})

    # ── Live WAQI data ──
    with st.spinner("Fetching live air quality…"):
        snapshot = get_live_city_snapshot(city, coords["lat"], coords["lon"], include_stations=False)
        waqi = snapshot["waqi"]

    if waqi and coords.get("lat") == 0:
        coords["lat"] = waqi.get("lat", 0)
        coords["lon"] = waqi.get("lon", 0)
        snapshot = get_live_city_snapshot(city, coords["lat"], coords["lon"], include_stations=False)

    aqi_val = waqi.get("aqi", 0)
    info    = aqi_info(aqi_val)
    wind    = snapshot["wind"]
    ws      = wind.get("speed")      if wind else waqi.get("wind_speed")
    wd      = wind.get("direction")  if wind else waqi.get("wind_dir")
    wg      = wind.get("gust")       if wind else None

    # ── Status row ──
    waqi_ok  = bool(waqi)
    wind_ok  = bool(wind)
    badge_waqi = "<span class='api-ok'>✓ WAQI Live</span>" if waqi_ok else "<span class='api-err'>✗ WAQI Offline</span>"
    badge_wind = "<span class='api-ok'>✓ Wind Live</span>" if wind_ok else "<span class='api-err'>— Wind key not set</span>"
    render_live_bar(f"{badge_waqi} &nbsp; {badge_wind}")
    render_secret_warnings(api_key, wk)

    # ── Primary metrics ──
    ws_disp = f"{float(ws):.1f}" if ws is not None else "—"
    wd_disp = wind_dir_label(float(wd)) if wd is not None else "—"
    wg_disp = f"{float(wg):.1f}" if wg is not None else (f"{float(ws):.1f}" if ws is not None else "—")
    render_metric_grid([
        (tt("Current AQI", "Guncel AQI"),   f"{int(aqi_val)}", info["name"],         info["color"]),
        ("PM2.5",         f"{waqi.get('pm25', 0):.1f}", "µg/m³",  "#007AFF"),
        (tt("Wind Speed", "Ruzgar Hizi"),    ws_disp, f"m/s · {wd_disp}",            "#AF52DE"),
        (tt("Wind Gust", "Ruzgar Hamilisi"),     wg_disp, tt("m/s peak", "m/s zirve"),                    "#5856D6"),
    ])
    st.markdown("<br>", unsafe_allow_html=True)

    # ── Wind context banner ──
    if ws is not None:
        ws_v   = float(ws or 0)
        wdir_v = float(wd or 0) if wd is not None else 0.0
        wg_v   = float(wg or 0) if wg is not None else 0.0
        wind_source = "Tomorrow.io" if wind and wind.get("speed") is not None else "WAQI feed"
        disp   = ("🟢 Strong wind — pollutants actively dispersing" if ws_v > 6
                  else "🟡 Moderate wind — partial dispersion" if ws_v > 2
                  else "🔴 Calm — pollutants accumulating")
        st.markdown(f"""
        <div class="card" style="background:linear-gradient(135deg,#007AFF0A,#5856D60A);
             border:1px solid #007AFF20;margin-bottom:1rem;padding:1rem 1.4rem">
          <b>💨 Live Wind · {city.split(',')[0]}</b>
          &nbsp; {wind_icon(wdir_v)}&nbsp;<b>{ws_v:.1f} m/s</b>
          &nbsp;{wind_dir_label(wdir_v)}&nbsp;({wdir_v:.0f}°)
          &nbsp;|&nbsp; Gust: <b>{wg_v:.1f} m/s</b>
          &nbsp;|&nbsp; {disp}
          <span style="font-size:.7rem;color:#8E8E93;margin-left:.5rem">· {wind_source}</span>
        </div>
        """, unsafe_allow_html=True)

    # ── GLOBAL MAP — single WAQI tile layer, no layer control ──


    with st.spinner("Loading global map…"):
        df_global = fetch_global_overview()

    if region != "All" and not df_global.empty:
        df_map = df_global[df_global["region"] == region].copy()
    else:
        df_map = df_global.copy() if not df_global.empty else pd.DataFrame()


    render_section(tt("🌍 Global Station Map", "🌍 Global İstasyon Haritası"))
    st.caption(tt("Live air quality data from WAQI monitoring stations worldwide. Click any marker for details.", "WAQI istasyonlarindan gelen canli hava kalitesi verisi. Ayrintilar icin herhangi bir isaretciye tiklayin."))

    world_map = folium.Map(
        location=[20, 10], zoom_start=2.4,
        tiles="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
        attr="© OpenStreetMap © CARTO",
        prefer_canvas=True,
        control_scale=False,
        attributionControl=False,
    )
    # WAQI tile overlay — shows ALL global monitoring stations
    folium.TileLayer(
        tiles=f"https://tiles.waqi.info/tiles/usepa-aqi/{{z}}/{{x}}/{{y}}.png?token={api_key}",
        attr='Air Quality Tiles © waqi.info',
        name="WAQI Global Stations",
        overlay=True, control=False, opacity=0.9,
    ).add_to(world_map)

    # The WAQI tile layer already carries the global station picture.
    # Skip the extra city-marker overlay so the dashboard map renders faster.
    if False and not df_map.empty:
        for _, row_m in df_map.iterrows():
            inf_m   = aqi_info(row_m["aqi"])
            color_m = inf_m["color"]
            popup_m = f"""<div style='font-family:system-ui;min-width:170px;font-size:12px'>
  <div style='background:{color_m};color:#fff;padding:6px 10px;border-radius:6px 6px 0 0;font-weight:700'>{row_m["city"]}</div>
  <div style='padding:7px 10px;border:1px solid #eee;border-top:none;border-radius:0 0 6px 6px;background:#fff'>
    <div style='font-size:20px;font-weight:900;color:{color_m}'>{int(row_m["aqi"])}</div>
    <div style='font-size:11px;color:{color_m};font-weight:600'>{inf_m["name"]} {inf_m["icon"]}</div>
    <div style='font-size:11px;margin-top:4px'>PM2.5: <b>{row_m["pm25"]:.1f}</b> µg/m³</div>
    <div style='font-size:11px'>Wind: <b>{row_m["wind_speed"]:.1f}</b> m/s</div>
  </div></div>"""
            folium.CircleMarker(
                location=[row_m["lat"], row_m["lon"]], radius=7,
                color=color_m, fill=True, fill_color=color_m,
                fill_opacity=0.75, weight=1.5,
                popup=folium.Popup(popup_m, max_width=200),
                tooltip=f"{row_m['city']}: AQI {int(row_m['aqi'])}",
            ).add_to(world_map)

    safe_render_folium_map(
        world_map,
        height=500,
        warning_message="The global map is temporarily unavailable. Live city comparison data is still shown below.",
    )

    # ── City Rankings — extremes only ──
    render_section(tt("Live City Monitor", "Canli Sehir Izleme"))
    featured_defaults = ["Istanbul, TR", "London, UK", "Delhi, IN", "Beijing, CN",
                         "New York, US", "Tokyo, JP", "Dubai, AE", "Sydney, AU"]
    featured_sel = st.multiselect(
        tt("Select cities to compare (up to 8)", "Karsilastirmak icin sehir secin (en fazla 8)"),
        list(GLOBAL_CITIES.keys()),
        default=[c for c in featured_defaults if c in GLOBAL_CITIES],
        max_selections=8,
        key="dash_featured",
    )
    if featured_sel and not df_global.empty:
        df_feat = df_global[df_global["city"].isin(featured_sel)].copy()
        if not df_feat.empty:
            cols_w = st.columns(min(4, len(df_feat)))
            for i, (_, row_f) in enumerate(df_feat.iterrows()):
                with cols_w[i % len(cols_w)]:
                    inf_f = aqi_info(row_f["aqi"])
                    comm_f = _aqi_commentary_short(row_f["aqi"], row_f["city"].split(",")[0])
                    st.markdown(f"""
                    <div class="aqi-widget" style="--wcolor:{inf_f['color']};--wbg:{inf_f['bg']};--wtext:{inf_f['text']}">
                      <div class="w-head">
                        <span class="w-city">{row_f['city'].split(',')[0]}</span>
                        <span class="w-badge">{inf_f['name']}</span>
                      </div>
                      <div style="display:flex;align-items:baseline;gap:8px">
                        <span class="w-aqi">{int(row_f['aqi'])}</span>
                        <span style="font-size:1.4rem">{inf_f['icon']}</span>
                      </div>
                      <div class="w-desc" style="font-size:.78rem">{comm_f}</div>
                      <div class="pols">
                        <div class="pol-box"><div class="pol-lbl">PM2.5</div><div class="pol-val">{row_f["pm25"]:.1f}</div></div>
                        <div class="pol-box"><div class="pol-lbl">PM10</div><div class="pol-val">{row_f["pm10"]:.1f}</div></div>
                        <div class="pol-box"><div class="pol-lbl">O3</div><div class="pol-val">{row_f["o3"]:.1f}</div></div>
                      </div>
                      <div class="wind-row">💨 {row_f["wind_speed"]:.1f} m/s {wind_icon(row_f["wind_dir"])}</div>
                    </div>
                    """, unsafe_allow_html=True)

    render_section(tt("🏆 City Rankings", "🏆 Şehir Sıralamaları"))
    if not df_global.empty:
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("### 🟢 Cleanest Cities")
            clean = df_global.nsmallest(5, "aqi")[["city", "aqi", "pm25", "wind_speed"]].reset_index(drop=True)
            clean.index += 1
            clean.columns = ["City", "AQI", "PM2.5 (µg/m³)", "Wind (m/s)"]
            for _, row in clean.iterrows():
                inf_c = aqi_info(row["AQI"])
                st.markdown(f"""
                <div style="display:flex;align-items:center;gap:12px;padding:.5rem .8rem;
                     background:#f0fdf4;border-radius:10px;margin-bottom:.4rem;border:1px solid #bbf7d0">
                  <span style="font-size:1.3rem;font-weight:900;color:{inf_c['color']};min-width:36px">{int(row['AQI'])}</span>
                  <div>
                    <div style="font-weight:700;color:#1D1D1F;font-size:.9rem">{row['City']}</div>
                    <div style="font-size:.75rem;color:#16a34a">{inf_c['name']} · PM2.5: {row['PM2.5 (µg/m³)']:.1f} µg/m³</div>
                  </div>
                </div>
                """, unsafe_allow_html=True)
        with col_b:
            st.markdown("### 🔴 Most Polluted Cities")
            dirty = df_global.nlargest(5, "aqi")[["city", "aqi", "pm25", "wind_speed"]].reset_index(drop=True)
            dirty.index += 1
            dirty.columns = ["City", "AQI", "PM2.5 (µg/m³)", "Wind (m/s)"]
            for _, row in dirty.iterrows():
                inf_d = aqi_info(row["AQI"])
                st.markdown(f"""
                <div style="display:flex;align-items:center;gap:12px;padding:.5rem .8rem;
                     background:#fef2f2;border-radius:10px;margin-bottom:.4rem;border:1px solid #fecaca">
                  <span style="font-size:1.3rem;font-weight:900;color:{inf_d['color']};min-width:36px">{int(row['AQI'])}</span>
                  <div>
                    <div style="font-weight:700;color:#1D1D1F;font-size:.9rem">{row['City']}</div>
                    <div style="font-size:.75rem;color:#dc2626">{inf_d['name']} · PM2.5: {row['PM2.5 (µg/m³)']:.1f} µg/m³</div>
                  </div>
                </div>
                """, unsafe_allow_html=True)
    else:
        st.info("Global overview data unavailable — check your network connection.")

# ===========================================================================
# PAGE: STATIONS MAP  (kept exactly as before — approved)
# ===========================================================================
def page_stations_map():
    render_hero(
        tt("Stations Map", "İstasyon Haritası"),
        tt("All WAQI stations · real-time · wind context", "Tüm WAQI istasyonları · gerçek zamanlı · rüzgâr bağlamı"),
    )
    render_page_guide(
        tt("<strong>Overview:</strong> Explore nearby stations, inspect pollutant detail, and choose the source used by Forecast.", "<strong>Bu sayfada neler yapabilirsiniz:</strong> Herhangi bir sehir ya da istasyon arayarak yakindaki tum hava kalitesi olcum noktalarini etkilesimli haritada gorebilirsiniz. Her bir isaretci kirletici ayrintilarini acir. Bir istasyon sectiginizde Forecast sayfasi bu kaynagi kullanir. Bu alan uygulamadaki istasyon kesfi ve kaynak seciminin ana merkezidir.")
    )

    api_key = get_waqi_key()
    wk      = get_tomorrow_key()

    mc1, mc2, mc3 = st.columns([3, 2, 1])
    with mc1:
        city_txt = st.text_input("🔍 City / station search",
                                  value=st.session_state.city.split(",")[0],
                                  placeholder="e.g. Istanbul, Delhi, Tokyo…")
    with mc2:
        results = waqi_search(city_txt) if city_txt else []
        station_opts = {"— show all nearby stations —": None}
        for r in results[:20]:
            sname = r.get("station", {}).get("name", "")
            uid   = r.get("uid")
            aqi_s = r.get("aqi", "?")
            station_opts[f"{sname}  (AQI {aqi_s})"] = uid
        chosen_lbl = st.selectbox(tt("Pin a specific station", "Belirli bir istasyonu sabitle"), list(station_opts.keys()))
        chosen_uid = station_opts.get(chosen_lbl)
    with mc3:
        radius_km = st.selectbox(tt("Search radius", "Arama yaricapi"), [50, 100, 200, 500], index=1)

    matched_city = next((k for k in GLOBAL_CITIES if city_txt.lower() in k.lower()), None)
    if matched_city:
        st.session_state.city = matched_city
        coords = GLOBAL_CITIES[matched_city]
    else:
        st.session_state.city = city_txt
        coords = {"lat": 41.0, "lon": 29.0}
        if results:
            geo = results[0].get("station", {}).get("geo")
            if geo and len(geo) == 2:
                coords = {"lat": geo[0], "lon": geo[1]}

    station_snapshot = get_live_city_snapshot(st.session_state.city, coords["lat"], coords["lon"], include_stations=False)
    wind = station_snapshot["wind"]
    live_waqi = station_snapshot["waqi"]
    render_live_bar()
    render_secret_warnings(api_key, wk)

    if wind and wind.get("speed") is not None:
        ws_v   = float(wind["speed"] or 0)
        wdir_v = float(wind.get("direction") or 0)
        wg_v   = float(wind.get("gust") or 0)
        disp   = ("🟢 Strong — pollutants dispersing" if ws_v > 6
                  else "🟡 Moderate — partial dispersion" if ws_v > 2
                  else "🔴 Calm — pollutants accumulating")
        st.markdown(f"""
        <div class="card" style="background:linear-gradient(135deg,#007AFF0A,#5856D60A);
             border:1px solid #007AFF20;margin-bottom:.8rem;padding:.85rem 1.2rem">
          <b>💨 Live Wind · {st.session_state.city.split(",")[0]}</b>
          &nbsp; {wind_icon(wdir_v)} <b>{ws_v:.1f} m/s</b>
          &nbsp;{wind_dir_label(wdir_v)} ({wdir_v:.0f}°)
          &nbsp;|&nbsp; Gust: <b>{wg_v:.1f} m/s</b>
          &nbsp;|&nbsp; {disp}
          <span style="font-size:.72rem;opacity:.6;margin-left:.5rem">· Tomorrow.io</span>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.info("💨 Wind data is unavailable right now. Add a Tomorrow.io API key in your app secrets to enable real-time wind data.")

    if live_waqi:
        live_aqi = float(live_waqi.get("aqi", 0))
        live_ws = wind.get("speed") if wind else live_waqi.get("wind_speed")
        live_wd = wind.get("direction") if wind else live_waqi.get("wind_dir")

        render_section(f"📍 {st.session_state.city} — Live Reading")
        render_air_quality_overview(st.session_state.city, live_waqi, wind)

    deg_per_km = 1.0 / 111.0
    off = radius_km * deg_per_km
    lat, lon = coords["lat"], coords["lon"]

    with st.spinner(f"Loading all WAQI stations within {radius_km} km…"):
        all_stations = []
        try:
            bounds = f"{lat-off:.4f},{lon-off:.4f},{lat+off:.4f},{lon+off:.4f}"
            d = http_get_json(
                "https://api.waqi.info/v2/map/bounds/",
                params={"latlng": bounds, "token": api_key},
                timeout=30,
                retries=1,
                service="waqi_station_bounds_page",
            )
            if not isinstance(d, dict):
                d = {"status": "error", "data": "Station service unavailable"}
            if d.get("status") == "ok":
                for s in d.get("data", []):
                    aqi_v = pd.to_numeric(s.get("aqi", 0), errors="coerce")
                    if pd.isna(aqi_v):
                        continue
                    all_stations.append({
                        "name": s.get("station", {}).get("name", "Station"),
                        "aqi":  float(aqi_v),
                        "lat":  float(s.get("lat", lat)),
                        "lon":  float(s.get("lon", lon)),
                        "uid":  s.get("uid"),
                        "is_nearest": False,
                        "dominentpol": None,
                        "iaqi": {},
                        "time": s.get("station", {}).get("time", ""),
                        "pm25": None, "pm10": None, "o3": None, "no2": None,
                    })
            elif d.get("status") == "error":
                log_event(logging.WARNING, "waqi_station_bounds_error", data=d.get("data", "Unknown"))
                ui_data_warning("Nearby station data could not be fully loaded right now. Please try again shortly.")
        except (TypeError, ValueError, KeyError) as exc:
            debug_exception("station_fetch_page_failed", exc)
            ui_data_warning("Nearby station data could not be loaded right now.")

        try:
            d2 = http_get_json(
                f"https://api.waqi.info/feed/geo:{lat};{lon}/",
                params={"token": api_key},
                timeout=12,
                retries=1,
                service="waqi_station_geo_page",
            )
            if isinstance(d2, dict) and d2.get("status") == "ok":
                p = _parse_station_from_feed(d2.get("data", {}), lat, lon, nearest=True)
                if not any(s.get("uid") == p.get("uid") for s in all_stations):
                    all_stations.insert(0, p)
        except (TypeError, ValueError, KeyError) as exc:
            debug_exception("station_geo_parse_page_failed", exc)

        st.session_state.nearby_stations = all_stations

    n = len(all_stations)
    if n > 0:
        st.markdown(f"""
        <div style="display:flex;align-items:center;gap:1rem;padding:.5rem 0;margin-bottom:.5rem">
          <span style="background:#007AFF;color:#fff;padding:3px 12px;border-radius:12px;
                font-size:.78rem;font-weight:700">📡 {n} stations loaded</span>
          <span style="font-size:.78rem;color:#8E8E93">
            Bounds: {lat-off:.2f}°–{lat+off:.2f}°N, {lon-off:.2f}°–{lon+off:.2f}°E
          </span>
        </div>""", unsafe_allow_html=True)
    else:
        st.warning("No station data returned. Demo access is limited, so configure a real WAQI API key in app secrets for full coverage.")

    # ── Map ──
    fm = folium.Map(
        location=[lat, lon], zoom_start=9,
        tiles="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
        attr="© OpenStreetMap © CARTO",
        prefer_canvas=True, control_scale=False, zoom_control=True,
        attributionControl=False,
    )
    waqi_tile = f"https://tiles.waqi.info/tiles/usepa-aqi/{{z}}/{{x}}/{{y}}.png?token={api_key}"
    folium.TileLayer(
        tiles=waqi_tile,
        attr='Air Quality Tiles © waqi.info',
        name="WAQI AQI Stations",
        overlay=True, control=False, opacity=0.9,
    ).add_to(fm)

    if all_stations:
        ws_txt = ""
        if wind and wind.get("speed"):
            ws_txt = f"{float(wind['speed']):.1f} m/s {wind_dir_label(float(wind.get('direction', 0)))}"
        for s in all_stations:
            try:
                slat = float(s["lat"]); slon = float(s["lon"])
                aqi_v = float(s.get("aqi", 0) or 0)
                si = aqi_info(aqi_v)
                def _fv(v):
                    return f"{float(v):.1f}" if v is not None else "—"
                ph = f"""<div style='font-family:system-ui;min-width:200px;font-size:12px'>
  <div style='background:{si["color"]};color:#fff;padding:7px 10px;border-radius:7px 7px 0 0;font-weight:700'>{s.get("name","Station")}</div>
  <div style='padding:8px 10px;border:1px solid #eee;border-top:none;border-radius:0 0 7px 7px;background:#fff'>
    <div style='font-size:22px;font-weight:900;color:{si["color"]}'>{int(aqi_v)}</div>
    <div style='font-weight:600;color:{si["color"]};font-size:11px;margin-bottom:5px'>{si["name"]} {si["icon"]}</div>
    <table style='width:100%;font-size:11px'>
      <tr><td style='color:#888'>PM2.5</td><td style='font-weight:700'>{_fv(s.get("pm25"))} µg/m³</td>
          <td style='color:#888'>PM10</td><td style='font-weight:700'>{_fv(s.get("pm10"))} µg/m³</td></tr>
      <tr><td style='color:#888'>Wind</td><td colspan=3 style='font-weight:700'>{ws_txt or "—"}</td></tr>
      <tr><td style='color:#888'>Updated</td><td colspan=3 style='color:#666'>{s.get("time","—")}</td></tr>
    </table>
  </div></div>"""
                folium.CircleMarker(
                    location=[slat, slon], radius=max(6, min(18, 5+aqi_v/20)),
                    color=si["color"], fill=True, fill_color=si["color"],
                    fill_opacity=0.8, weight=1.5,
                    popup=folium.Popup(ph, max_width=240),
                    tooltip=f"{s.get('name','Station')}: AQI {int(aqi_v)} · {si['name']}",
                ).add_to(fm)
            except (TypeError, ValueError, KeyError) as exc:
                debug_exception("station_marker_render_failed", exc)

    safe_render_folium_map(
        fm,
        height=520,
        warning_message="The stations map could not be rendered right now. Station summary data is still available below.",
    )

    if all_stations:
        df_s = stations_dataframe(all_stations)
        if not df_s.empty:
            best  = df_s.nsmallest(1, "aqi").iloc[0]
            worst = df_s.nlargest(1, "aqi").iloc[0]
            render_metric_grid([
                ("Stations",     str(len(df_s)),               "in view",             "#007AFF"),
                ("Average AQI",  f"{df_s['aqi'].mean():.0f}", aqi_info(df_s['aqi'].mean())["name"], aqi_info(df_s['aqi'].mean())["color"]),
                ("Best AQI",     str(int(best["aqi"])),        best["name"][:24],     "#34C759"),
                ("Worst AQI",    str(int(worst["aqi"])),       worst["name"][:24],    "#FF3B30"),
            ])
            with st.expander(f"📋 All {len(df_s)} stations — click to expand"):
                st.dataframe(
                    df_s[["name","aqi"]].sort_values("aqi").reset_index(drop=True),
                    use_container_width=True,
                )

    if st.session_state.get("selected_station_uid"):
        st.markdown(f"""
        <div class="card" style="border-left:4px solid #007AFF;margin-top:1rem">
          <div class="m-label">Linked Forecast Source</div>
          <div style="font-size:1rem;font-weight:700;color:#1D1D1F">{st.session_state.selected_station_name or 'Selected station'}</div>
          <div style="font-size:.85rem;color:#6b7280;margin-top:.25rem">
            Forecast will use this station until you clear it by choosing city mode on the Forecast page.
          </div>
        </div>
        """, unsafe_allow_html=True)

    if chosen_uid:
        st.session_state.selected_station_uid  = chosen_uid
        st.session_state.selected_station_name = chosen_lbl
        st.session_state.forecast_source = "station"
        with st.spinner("Loading station detail…"):
            raw = waqi_station(chosen_uid)
            d   = process_feed(raw) if raw else {}
        if d:
            render_section(f"📡 Station Detail: {d.get('station_name', chosen_lbl)}")
            render_aqi_widget(
                d.get("station_name", chosen_lbl),
                d.get("aqi", 0),
                pm25=d.get("pm25", 0), pm10=d.get("pm10", 0), o3=d.get("o3", 0),
                wind_speed=d.get("wind_speed") or (wind.get("speed") if wind else None),
                wind_dir  =d.get("wind_dir")   or (wind.get("direction") if wind else None),
                timestamp =d.get("timestamp"),
            )
            st.info("✅ Forecast page will now use this station as the source.")
    else:
        st.session_state.forecast_source = "city"

# ===========================================================================
# PAGE: FORECAST
# ===========================================================================
def page_forecast():
    render_hero(_t("forecast.title"), _t("forecast.subtitle"))
    render_page_guide(_t("forecast.guide"))

    api_key = get_waqi_key()
    wk = get_tomorrow_key()
    render_live_bar()
    render_secret_warnings(api_key, wk)

    fc1, fc2 = st.columns([4, 1.4])
    with fc1:
        city = st.text_input(_t("forecast.enter_city"), value=st.session_state.city, key="fc_city_in")
        st.session_state.city = city
    with fc2:
        st.markdown(f"""
        <div class="mcard" style="margin-top:1.75rem">
            <div class="m-label">{_t("common.source")}</div>
            <div style="font-size:.9rem;font-weight:700;color:#007AFF">{_t("common.global_city_search")}</div>
            <div class="m-unit">{city[:32]}</div>
        </div>""", unsafe_allow_html=True)

    source_label = city

    c3, c4 = st.columns(2)
    with c3:
        pollutant = st.selectbox(_t("forecast.pollutant"), ["pm25", "pm10", "o3", "no2"])
    with c4:
        days = st.slider(_t("forecast.horizon"), 3, 14, 7)

    forecast_loading = st.empty()
    with forecast_loading.container():
        render_loading_skeleton(cards=4)
    with st.spinner("Fetching base data..."):
        d = get_processed_city_feed(city)
    forecast_loading.empty()

    base_val = d.get(pollutant, 0) or 0
    diagnostics = {}
    past_forecast_df = pd.DataFrame(columns=["date", "forecast_value", "actual_value"])
    model_accuracy_pct = None
    if generate_forecast_bundle is not None:
        forecast_result = generate_forecast_bundle(
            d,
            pollutant=pollutant,
            days=days,
            station_name=source_label,
            station_key=None,
            prefer_native_waqi=True,
            prefer_offline_champion_only=False,
        )
        hist_df = forecast_result.hist_df.rename(columns={"value": pollutant}).copy()
        fc_df = forecast_result.fc_df.rename(columns={"value": pollutant}).copy()
        data_note = forecast_result.data_note
        hist_df = forecast_result.hist_df.rename(columns={"value": pollutant}).copy()
        fc_df = forecast_result.fc_df.rename(columns={"value": pollutant}).copy()
        data_note = forecast_result.data_note
        model_used = forecast_result.model_used
        diagnostics = forecast_result.diagnostics
        past_forecast_df = forecast_result.past_forecast_df.copy()
        model_accuracy_pct = forecast_result.model_accuracy_pct
    else:
        hist_df, fc_df = run_forecast(base_val, pollutant, days, source_label)
        data_note = "Fallback forecast is being used because the modular forecasting engine is unavailable in this runtime."
        model_used = "HOLT_WINTERS"
        diagnostics = {
            "history_source": "synthetic_fallback",
            "fallback_reason": "forecast_engine_unavailable",
            "observed_label": "Estimated baseline history",
            "observed_note": "Historical baseline is estimated because the full forecasting engine is unavailable in this runtime.",
            "forecast_label": "Fallback model forecast",
            "forecast_note": "Future values are fallback estimates, not live measurements.",
            "fallback_active": "true",
        }

    hist_df["date"] = pd.to_datetime(hist_df["date"], errors="coerce")
    fc_df["date"] = pd.to_datetime(fc_df["date"], errors="coerce")
    hist_df = hist_df.dropna(subset=["date", pollutant]).sort_values("date").tail(90)
    fc_df = fc_df.dropna(subset=["date", pollutant]).sort_values("date")
    if not hist_df.empty and not fc_df.empty:
        original_fc_df = fc_df.copy()
        last_hist_date = pd.to_datetime(hist_df["date"].max()).normalize()
        fc_df = fc_df[pd.to_datetime(fc_df["date"], errors="coerce").dt.normalize() > last_hist_date].copy()
        if fc_df.empty:
            fc_df = original_fc_df.copy()
            fc_df["date"] = pd.to_datetime(hist_df["date"].max()).normalize() + pd.to_timedelta(np.arange(1, len(fc_df) + 1), unit="D")
    if hist_df.empty:
        st.warning("Observed history is not available yet for this source, so the forecast view cannot be shown.")
        st.info("Try another city or pollutant, or return later when more measured history is available.")
        return
    hist_plot_df = hist_df.set_index("date").asfreq("D").reset_index()
    pol_name = POLLUTANT_INFO.get(pollutant, {}).get("full", pollutant.upper())
    pol_unit = POLLUTANT_INFO.get(pollutant, {}).get("unit", "ug/m3")
    who = POLLUTANT_INFO.get(pollutant, {}).get("who")
    observed_label = diagnostics.get("observed_label", "Observed history")
    observed_note = diagnostics.get("observed_note", "Built from measured history.")
    forecast_label = diagnostics.get("forecast_label", "Forecast")
    forecast_note = diagnostics.get("forecast_note", "Future values are predictions, not live readings.")
    fallback_active = diagnostics.get("fallback_active") == "true"
    observed_metric_label = _t("forecast.latest_observed") if observed_label == "Observed history" else "Latest baseline value"

    latest_hist = float(hist_df[pollutant].iloc[-1])
    avg_fc = float(fc_df[pollutant].mean()) if not fc_df.empty else latest_hist
    peak_fc = float(fc_df["upper"].max()) if "upper" in fc_df.columns and not fc_df.empty else avg_fc
    exceed_days = int((fc_df[pollutant] > who).sum()) if who and not fc_df.empty else 0
    validated_points = int(pd.to_numeric(diagnostics.get("validated_points", 0), errors="coerce") or 0)
    approx_mape = diagnostics.get("mape") if validated_points > 0 else None
    if model_used == "WAQI_NATIVE":
        accuracy_value = "Provider"
        accuracy_subtitle = f"Approx MAPE {approx_mape}%" if approx_mape else "WAQI daily forecast"
    elif fallback_active:
        accuracy_value = "Fallback"
        accuracy_subtitle = f"Approx MAPE {approx_mape}%" if approx_mape else "Conservative time-series"
    else:
        accuracy_value = "Model"
        accuracy_subtitle = f"Approx MAPE {approx_mape}%" if approx_mape else "Forecast model active"

    render_metric_grid([
        (observed_metric_label, f"{latest_hist:.1f}", pol_unit, "#007AFF"),
        ("Forecast mean", f"{avg_fc:.1f}", f"next {days} days", "#FF9500"),
        (_t("forecast.peak_risk"), f"{peak_fc:.1f}", pol_unit, "#FF3B30"),
        (_t("forecast.who_breach_days"), str(exceed_days), f"of {len(fc_df)} days", "#AF52DE"),
        ("Forecast mode", accuracy_value, accuracy_subtitle, "#6B7280"),
    ])
    st.markdown("<br>", unsafe_allow_html=True)

    model_used_display = model_used.replace("_", " ").title() if model_used else "Unknown"
    history_source_display = diagnostics.get("history_source", "unknown").replace("_", " ").title()
    status_text = "Live provider forecast" if model_used == "WAQI_NATIVE" else "Conservative live forecast" if fallback_active else "Forecast model active"
    diag_items = [
        f"<b>Model:</b> {model_used_display}",
        f"<b>History:</b> {history_source_display}",
        f"<b>Status:</b> {status_text}",
    ]
    if approx_mape:
        diag_items.append(f"<b>Approx error:</b> MAPE {approx_mape}% over {validated_points} validated day(s)")

    diag_border = "#FF9500" if fallback_active else "#34C759"
    st.markdown(f"""
    <div class="card" style="margin-bottom:1rem;border-left:4px solid {diag_border}">
      <div class="m-label">📊 Forecast Diagnostics</div>
      <div style="font-size:.87rem;color:#1D1D1F;line-height:1.85">
        {"<br>".join(diag_items)}
      </div>
    </div>
    """, unsafe_allow_html=True)

    fc_plot_df = fc_df.copy()
    if not hist_df.empty and not fc_df.empty:
        hist_last_date = pd.to_datetime(hist_df["date"].iloc[-1], errors="coerce")
        hist_last_value = pd.to_numeric(hist_df[pollutant].iloc[-1], errors="coerce")
        if pd.notna(hist_last_date) and pd.notna(hist_last_value):
            fc_plot_df = pd.concat(
                [
                    pd.DataFrame([{
                        "date": hist_last_date,
                        pollutant: float(hist_last_value),
                        "upper": float(hist_last_value),
                        "lower": float(hist_last_value),
                    }]),
                    fc_df,
                ],
                ignore_index=True,
            )

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=hist_plot_df["date"], y=hist_plot_df[pollutant],
        name=observed_label, mode="lines",
        line=dict(color="#007AFF", width=3),
        fill="tozeroy", fillcolor="rgba(0,122,255,0.08)",
        connectgaps=False,
    ))
    fig.add_trace(go.Scatter(
        x=fc_plot_df["date"], y=fc_plot_df[pollutant],
        name=forecast_label, mode="lines+markers",
        line=dict(color="#FF9500", width=3),
        marker=dict(size=8, color="#FF9500", line=dict(color="#FFFFFF", width=1)),
    ))
    fig.add_trace(go.Scatter(
        x=list(fc_plot_df["date"]) + list(fc_plot_df["date"][::-1]),
        y=list(fc_plot_df["upper"]) + list(fc_plot_df["lower"][::-1]),
        fill="toself", fillcolor="rgba(255,149,0,0.12)",
        line=dict(color="rgba(0,0,0,0)"),
        name="Forecast range", showlegend=True,
    ))
    if who:
        fig.add_hline(
            y=who,
            line_dash="dash",
            line_color="#FF3B30",
            opacity=0.5,
            annotation_text=f"WHO guideline · {who} {pol_unit}",
            annotation_position="top right",
        )
    fig.update_layout(
        title=dict(text=f"{pol_name} · {source_label} · {days}-day outlook", font=dict(size=15, family="Inter")),
        xaxis_title=tt("Date", "Tarih"),
        yaxis_title=f"{pol_name} ({pol_unit})",
        height=460,
        margin=dict(l=0, r=0, t=52, b=0),
        legend=dict(orientation="h", y=1.07),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, sans-serif"),
    )
    fig.update_xaxes(showgrid=True, gridcolor="rgba(0,0,0,0.06)")
    fig.update_yaxes(showgrid=True, gridcolor="rgba(0,0,0,0.06)")
    st.plotly_chart(fig, use_container_width=True)

    last_fc = float(fc_df[pollutant].iloc[-1])
    trend = "rising" if last_fc > base_val * 1.05 else "falling" if last_fc < base_val * 0.95 else "stable"
    who_status = (
        _t("forecast.exceeding", who=who, unit=pol_unit)
        if who and last_fc > who else
        _t("forecast.within", who=who, unit=pol_unit)
    )
    summary_intro = "Fallback estimate" if fallback_active else "Forecast summary"
    st.markdown(f"""
    <div class="card" style="margin-top:1rem;border-left:4px solid #FF9500">
      <div class="m-label">{summary_intro}</div>
      <div style="font-size:1rem;color:#1D1D1F;line-height:1.7">
        For the next <b>{days} days</b>, <b>{pol_name}</b> in <em>{source_label}</em> is expected to stay <b>{trend}</b>.
        The final day is currently projected at <b>{last_fc:.1f} {pol_unit}</b>, which is {who_status.lower()}.
      </div>
    </div>
    """, unsafe_allow_html=True)

    if is_debug_mode():
        fallback_reason_labels = {
            "forecast_engine_unavailable": "Forecast engine unavailable",
            "backtest_selected_holt_winters": "Backtest selected Holt-Winters",
            "prophet_package_missing": "Prophet package missing",
            "insufficient_training_rows": "Insufficient training rows",
            "insufficient_future_weather": "Insufficient future weather",
            "prophet_or_weather_pipeline_failed": "Weather-model pipeline failed",
            "insufficient_real_history": "Insufficient real history",
            "waqi_daily_forecast_unavailable": "WAQI daily forecast unavailable",
        }
        history_source_labels = {
            "open_meteo_air_quality_plus_live_waqi": "Open-Meteo hourly history aggregated to daily averages, interpolated where needed, then anchored to the latest WAQI reading",
            "synthetic_fallback_insufficient_real_history": "Synthetic fallback because fewer than 30 real observed days were available",
            "synthetic_fallback": "Synthetic fallback history",
        }
        model_labels = {
            "OFFLINE_CHAMPION_HYBRID": "Offline champion day-1 override plus live production forecast path",
            "PROPHET_WEATHER": "Prophet with weather regressors",
            "TABULAR_GRADIENT_BOOSTING": "HistGradientBoosting with lag, rolling, calendar, and weather features",
            "DIRECT_MULTI_HORIZON": "Direct multi-horizon global station-aware model",
            "HOLT_WINTERS": "Holt-Winters time-series fallback",
            "WAQI_NATIVE": "WAQI native forecast feed",
            "NO_HISTORY": "No forecast model available",
        }
        history_source = history_source_labels.get(diagnostics.get("history_source", ""), diagnostics.get("history_source", "Unknown"))
        model_label = model_labels.get(model_used, model_used)
        regressor_text = diagnostics.get("regressors", "temperature, humidity, wind speed, wind direction, precipitation")
        training_rows = diagnostics.get("training_rows", str(len(hist_df)))
        fallback_reason = fallback_reason_labels.get(diagnostics.get("fallback_reason", ""), "Not applicable")
        real_days = diagnostics.get("real_observed_days", str(len(hist_df)))
        interpolated_days = diagnostics.get("interpolated_days", "0")

        st.markdown(f"""
        <div class="card" style="margin-top:1rem">
          <div class="m-label">{_t("forecast.what_used")} · DEBUG</div>
          <div style="font-size:.92rem;color:#1D1D1F;line-height:1.8">
            <b>Observed history source:</b> {history_source}<br>
            <b>Observed days plotted:</b> {len(hist_df)}<br>
            <b>Real days from API:</b> {real_days}<br>
            <b>Interpolated missing days:</b> {interpolated_days}<br>
            <b>Forecast model:</b> {model_label}<br>
            <b>Model selection:</b> {diagnostics.get("selection_method", "n/a")} · {diagnostics.get("selection_reason", "n/a")}<br>
            <b>Backtest MAPE:</b> Prophet {diagnostics.get("prophet_backtest_mape", "n/a")} · Holt-Winters {diagnostics.get("holt_winters_backtest_mape", "n/a")}<br>
            <b>Training rows:</b> {training_rows}<br>
            <b>Future weather inputs:</b> {regressor_text if (model_used == "PROPHET_WEATHER" or diagnostics.get("hybrid_base_model") == "PROPHET_WEATHER") else "Not used in fallback mode"}<br>
            <b>Hybrid base model:</b> {diagnostics.get("hybrid_base_model", "n/a")}<br>
            <b>Offline champion:</b> {diagnostics.get("offline_champion_name", diagnostics.get("offline_champion_status", "n/a"))}<br>
            <b>Fallback reason:</b> {fallback_reason}<br>
            <b>Model accuracy:</b> {accuracy_value}<br>
            <b>WHO reference line:</b> {who} {pol_unit if who else "Not defined for this pollutant"}
          </div>
        </div>
        """, unsafe_allow_html=True)

# ===========================================================================
# PAGE: TAKE ACTION
# ===========================================================================
def page_action():
    render_hero(
        tt("Take Action", "Harekete Geç"),
        tt("Personalised sustainability intelligence", "Kişiselleştirilmiş sürdürülebilirlik zekâsı"),
    )
    render_page_guide(
        tt(
            f"<strong>What you can do here:</strong> This page converts the live air-quality context for "
            f"<b>{st.session_state.city.split(',')[0]}</b> into practical actions, cleaner travel choices, health guidance, and footprint planning.",
            f"<strong>Bu sayfada neler yapabilirsiniz:</strong> Bu alan, "
            f"<b>{st.session_state.city.split(',')[0]}</b> i?in canl? hava kalitesi ba?lam?n? ki?isel ?nerilere, ula??m kararlar?na, "
            "sa?l?k y?nlendirmesine, ayak izi planlamas?na ve g?nl?k uygulanabilir ad?mlara d?n??t?r?r.",
        )
    )

    api_key = get_waqi_key()
    city    = st.session_state.city
    coords  = GLOBAL_CITIES.get(city, GLOBAL_CITIES["Istanbul, TR"])

    with st.spinner(tt("Loading city data...", "Şehir verisi yükleniyor...")):
        snapshot = get_live_city_snapshot(city, coords["lat"], coords["lon"], include_stations=True)
        waqi = snapshot["waqi"]
        if waqi and coords.get("lat") == 41.0 and coords.get("lon") == 29.0 and city not in GLOBAL_CITIES:
            coords["lat"] = waqi.get("lat", 41.0)
            coords["lon"] = waqi.get("lon", 29.0)
            snapshot = get_live_city_snapshot(city, coords["lat"], coords["lon"], include_stations=True)
            waqi = snapshot["waqi"]
        stations = snapshot["stations"]
        st.session_state.nearby_stations = stations

    wk      = get_tomorrow_key()
    wind    = snapshot["wind"]
    aqi_val = float(waqi.get("aqi", 0))
    info    = aqi_info(aqi_val)
    render_live_bar()
    render_secret_warnings(api_key, wk)

    if aqi_val <= 50:
        hero_cls, action_level = "ah-good", tt("Low-Risk Day", "D???k Riskli G?n")
    elif aqi_val <= 100:
        hero_cls, action_level = "ah-moderate", tt("Moderate Caution", "Orta D?zey Dikkat")
    else:
        hero_cls, action_level = "ah-high", tt("High Protection Day", "Y?ksek Koruma G?n?")

    ws_txt = f" · Wind {wind['speed']:.1f} m/s {wind_dir_label(wind['direction'] or 0)}" if wind and wind.get("speed") else ""
    dom    = (waqi.get("dominentpol") or "pm25").upper()
    full_commentary = _aqi_commentary(aqi_val, city.split(",")[0], float(wind["speed"]) if wind and wind.get("speed") else None)
    wind_context = (
        tt("Strong wind is helping pollutants disperse.", "G??l? r?zg?r kirleticilerin da??lmas?na yard?mc? oluyor.") if wind and float(wind.get("speed") or 0) > 6 else
        tt("Moderate wind offers some natural ventilation.", "Orta d?zey r?zg?r do?al havalanma sa?l?yor.") if wind and float(wind.get("speed") or 0) > 2 else
        tt("Low wind means pollutants can linger for longer.", "D???k r?zg?r kirleticilerin daha uzun s?re kalmas?na yol a??yor.")
    )
    action_intro = (
        tt("Use today to favor lower-exposure routes, reduce avoidable emissions, and shift the highest-risk activities indoors when needed. ", "Bug?n? daha d???k maruziyetli rotalar? se?mek, ka??n?labilir emisyonlar? azaltmak ve gerekti?inde y?ksek riskli aktiviteleri i?eri ta??mak i?in de?erlendirin. ")
        + tt(
            f"Primary pollutant pressure is coming from {dom}, and {wind_context}",
            f"Bask?n kirletici y?k? {dom} kaynakl? ve {wind_context}",
        )
    )
    selected_station = st.session_state.selected_station_name if st.session_state.get("forecast_source") == "station" else None

    txt_color = "#fff" if hero_cls != "ah-moderate" else "#1D1D1F"
    st.markdown(f"""
    <div class="action-hero {hero_cls}" style="color:{txt_color}">
      <h2 style="margin:0 0 .6rem;font-weight:900;color:{txt_color}">{tt("Today's Action Plan", "Bug?n?n Eylem Plan?")}</h2>
      <p style="margin:0;font-size:.98rem;opacity:.95;max-width:760px;line-height:1.6;color:{txt_color}">{action_intro}</p>
      <p style="margin:.6rem 0 0;font-size:.82rem;opacity:.8;color:{txt_color}">
        📡 {len(stations)} {tt("stations", "istasyon")} · {tt("Primary pollutant", "Baskın kirletici")}: {dom}{ws_txt}
      </p>
      <p style="margin:.45rem 0 0;font-size:.82rem;opacity:.86;max-width:760px;color:{txt_color}">
        Take Action is the personal decision layer for this app. The profile name, checklist progress, commute context, and daily behavior you shape here are intended to feed Reports as your user-specific summary.
      </p>
      {"<p style='margin:.45rem 0 0;font-size:.8rem;opacity:.82;color:" + txt_color + "'>" + tt("Forecast source linked to selected station:", "Tahmin kayna?? se?ili istasyona ba?l?:") + " <b>" + selected_station[:48] + "</b></p>" if selected_station else ""}
    </div>
    """, unsafe_allow_html=True)
    st.markdown("""
    <div class="card" style="margin-top:-.8rem;margin-bottom:1rem;border-left:4px solid #007AFF">
      <div class="m-label">Take Action -> Reports</div>
      <div style="font-size:.92rem;color:#1D1D1F;line-height:1.7">
        This page now feeds Reports with your live checklist score, action score, commute choice, commute distance, commute savings, and today's top actions.
      </div>
    </div>
    """, unsafe_allow_html=True)
    st.markdown("<div class='action-block-gap'></div>", unsafe_allow_html=True)

    risk_actions = (
        "Seal windows during peak hours · shift exercise indoors · use a mask on congested routes"
        if aqi_val > 100 else
        "Check AQI before workouts · favor low-traffic streets · limit prolonged exposure if sensitive"
        if aqi_val > 50 else
        "Good day for active travel, open-air breaks, and ventilation"
    )
    render_metric_grid([
        ("AQI Status", f"{int(aqi_val)}", info["name"], info["color"]),
        ("Primary Pollutant", dom.upper(), "live driver", "#007AFF"),
        ("Nearby Stations", str(len(stations)), "local context", "#34C759"),
        ("Today's Focus", "Action", risk_actions[:24] + "…", "#AF52DE"),
    ])
    st.markdown("<div class='action-block-gap'></div>", unsafe_allow_html=True)
    checklist_score_now = int(sum(st.session_state.checklist.values()))
    
    # Build top actions with safety guard
    if build_top_actions:
        top_actions = build_top_actions(
            aqi_val=aqi_val,
            dominant_pollutant=(waqi.get("dominentpol") or "pm25"),
            wind=wind,
            flags=st.session_state.action_profile_flags,
            checklist=st.session_state.checklist,
            commute_mode=st.session_state.commute_mode,
            commute_saved=float(st.session_state.commute_saved or 0.0),
        )
    else:
        top_actions = []
    
    # Compute action score with safety guard
    if compute_action_score:
        action_score = compute_action_score(
            checklist_score=checklist_score_now,
            commute_saved=float(st.session_state.commute_saved or 0.0),
            footprint_monthly=st.session_state.fp_monthly,
            aqi_val=aqi_val,
            flags=st.session_state.action_profile_flags,
            commute_mode=st.session_state.commute_mode,
        )
    else:
        action_score = 0
    st.session_state.action_top3 = top_actions
    st.session_state.action_score = action_score

    history_df = action_history_dataframe(st.session_state.action_history)
    if not history_df.empty:
        yesterday_score = int(history_df.iloc[-2]["action_score"]) if len(history_df) > 1 else int(history_df.iloc[-1]["action_score"])
        weekly_avg = float(history_df.tail(7)["action_score"].mean())
        monthly_avg = float(history_df.tail(30)["action_score"].mean())
    else:
        yesterday_score = action_score
        weekly_avg = float(action_score)
        monthly_avg = float(action_score)

    overview_left, overview_right = st.columns([1.75, 1.1], gap="large")
    with overview_left:
        st.markdown("""
        <div class="card" style="padding:1.35rem 1.4rem;margin-bottom:1rem;min-height:78px;display:flex;align-items:center">
          <div class="m-label">Top 3 Actions for Today</div>
        </div>
        """, unsafe_allow_html=True)
        fallback_actions = top_actions + [{
            "title": "Keep monitoring today's air quality",
            "reason": "As you complete actions, this panel will continue to highlight the next most useful step.",
        }] * max(0, 3 - len(top_actions))
        action_cards = []
        for idx, item in enumerate(fallback_actions[:3], start=1):
            action_cards.append(f"""
            <div class="card" style="background:#F8FAFC;padding:1rem 1.05rem;height:100%;border:1px solid rgba(0,0,0,.06);margin-bottom:0">
              <div style="font-size:.72rem;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:#007AFF;margin-bottom:.45rem">Priority {idx}</div>
              <div class="action-card-title">{html.escape(str(item.get('title', 'Action')))}</div>
              <div class="action-card-copy">{html.escape(str(item.get('reason', 'Review the current air-quality context before planning outdoor activity.')))}</div>
            </div>
            """)
        st.markdown(f"<div class='action-card-grid'>{''.join(action_cards)}</div>", unsafe_allow_html=True)
    with overview_right:
        st.markdown(f"""
        <div class="card" style="height:100%;margin-top:0;min-height:78px">
          <div class="m-label">Action Score</div>
          <div style="font-size:3rem;font-weight:900;color:#007AFF;line-height:1">{action_score}</div>
          <div style="font-size:.9rem;color:#6B7280;margin-top:.25rem">This score reflects the quality of today's choices based on exposure, behavior, and protective actions.</div>
          <div class="action-score-mini-grid">
            <div style="background:#F8FAFC;border-radius:14px;padding:.8rem;text-align:center">
              <div style="font-size:.7rem;color:#6B7280;text-transform:uppercase;font-weight:700">Yesterday</div>
              <div style="font-size:1.25rem;font-weight:800;color:#1D1D1F">{yesterday_score}</div>
            </div>
            <div style="background:#F8FAFC;border-radius:14px;padding:.8rem;text-align:center">
              <div style="font-size:.7rem;color:#6B7280;text-transform:uppercase;font-weight:700">7-Day Avg</div>
              <div style="font-size:1.25rem;font-weight:800;color:#1D1D1F">{weekly_avg:.0f}</div>
            </div>
            <div style="background:#F8FAFC;border-radius:14px;padding:.8rem;text-align:center">
              <div style="font-size:.7rem;color:#6B7280;text-transform:uppercase;font-weight:700">30-Day Avg</div>
              <div style="font-size:1.25rem;font-weight:800;color:#1D1D1F">{monthly_avg:.0f}</div>
            </div>
          </div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<div class='action-block-gap'></div>", unsafe_allow_html=True)
    save_col, save_info_col = st.columns([1, 2.4], gap="large")
    with save_col:
        save_today = st.button(tt("Save Today's Action Snapshot", "Bugunun Aksiyon Kaydini Kaydet"), use_container_width=True)
    with save_info_col:
        st.caption(tt("Snapshots are stored locally per profile name on this device. That gives each user a separate daily trail without needing full authentication.", "Kayitlar bu cihazda profil adina gore yerel olarak tutulur. Boylece tam kimlik dogrulama olmadan her kullanici icin ayri bir gunluk iz olusur."))
    if not history_df.empty:
        fig_hist_actions = go.Figure()
        fig_hist_actions.add_trace(go.Scatter(
            x=history_df["date"],
            y=history_df["action_score"],
            mode="lines+markers",
            line=dict(color="#007AFF", width=3),
            marker=dict(size=7),
            name=tt("Action Score", "Aksiyon Skoru"),
        ))
        fig_hist_actions.update_layout(
            title=tt(f"Recent action trend for {st.session_state.action_profile_name}", f"{st.session_state.action_profile_name} i?in son eylem trendi"),
            height=250,
            margin=dict(l=0, r=0, t=45, b=0),
            yaxis_title=tt("Action Score", "Aksiyon Skoru"),
            xaxis_title=tt("Date", "Tarih"),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(family="Inter"),
            showlegend=False,
        )
        st.plotly_chart(fig_hist_actions, use_container_width=True)
    st.markdown("<br>", unsafe_allow_html=True)

    tabs = st.tabs(["🚲 Smart Commute", "🌱 Carbon Footprint", "✅ Daily Checklist",
                     "🪴 Plants", "🌎 Campaigns", "🩺 Health", "🏃 Exercise"])

    reports_relationship_copy = {
        "commute": (
            "Your commute choice, estimated CO2 savings, and lower-exposure route preference belong in Reports as a clean summary. "
            "This space works better as the place where you compare options and decide today's move."
        ),
        "footprint": (
            "A footprint snapshot makes sense in Reports together with your biggest emission driver and the strongest reduction opportunity. "
            "Here, the experience should stay interactive so you can test scenarios before saving the result."
        ),
        "checklist": (
            "This checklist becomes much more useful in Reports as a daily score and streak summary. "
            "Inside Take Action, it should stay a live habit tracker that reflects what you actually did today."
        ),
        "plants": (
            "Reports can capture the plant choices and care actions that helped improve indoor air conditions. "
            "This tab is strongest when it stays practical, simple, and recommendation-led."
        ),
        "campaigns": (
            "Civic actions such as campaigns opened, followed, or shared can be carried into Reports as a participation summary. "
            "Here, the focus should remain on doing something concrete rather than browsing a static directory."
        ),
        "health": (
            "The daily health guidance level, key precautions, and sensitive-group risk context fit naturally into Reports as a concise advisory snapshot. "
            "This tab should stay centered on immediate protective decisions."
        ),
        "exercise": (
            "Reports can summarize whether outdoor exercise was advisable, which intensity was safest, and how AQI or wind affected that call. "
            "This tab works best as a planning tool first, with the summary saved afterwards."
        ),
    }

    def render_reports_relationship(copy_text: str):
        st.markdown(f"""
        <div class="card" style="margin-top:1.25rem;width:100%;border-left:4px solid #007AFF">
          <div class="m-label">Reports Relationship</div>
          <div style="font-size:.92rem;color:#1D1D1F;line-height:1.7">
            {copy_text}
          </div>
        </div>
        """, unsafe_allow_html=True)

    # ── TAB 1: SMART COMMUTE ──
    with tabs[0]:
        render_section("🚲 Smart Commute CO₂ Savings")
        st.caption(tt("Tune your daily commute and instantly see the carbon and exposure impact versus driving.", "Gunluk ulasiminizi ayarlayin ve suruse gore karbon ile maruziyet etkisini aninda gorun."))

        commute_left, commute_right = st.columns(2, gap="large")
        with commute_left:
            commute_mode = st.selectbox(
                tt("Your commute mode", "Ulasim tercihiniz"),
                list(COMMUTE_FACTORS.keys()),
                index=list(COMMUTE_FACTORS.keys()).index(st.session_state.commute_mode),
                key="ta_commute_mode",
            )
            st.markdown("<div style='height:.45rem'></div>", unsafe_allow_html=True)
            commute_km = st.number_input(
                tt("Daily distance (km)", "Gunluk mesafe (km)"),
                min_value=0.0,
                max_value=200.0,
                value=float(st.session_state.commute_km),
                step=0.5,
                key="ta_commute_km",
            )
            st.markdown("<div style='height:.5rem'></div>", unsafe_allow_html=True)
            commute_res = calc_commute(commute_mode, commute_km)
            st.session_state.commute_mode = commute_mode
            st.session_state.commute_km = commute_km
            st.session_state.commute_saved = commute_res["daily"]
            st.session_state["rep_mode"] = commute_mode
            st.session_state["rep_km"] = commute_km

            exposure_note = (
                "Prefer indoor transfer points and avoid high-traffic corridors."
                if aqi_val > 100 else
                "Active travel is reasonable, but lower-traffic streets are still better."
                if aqi_val > 50 else
                "Excellent day to choose walking or cycling if practical."
            )
            current_mode_emissions = COMMUTE_FACTORS.get(commute_mode, COMMUTE_FACTORS["Car"]) * commute_km
            best_mode = min(COMMUTE_FACTORS, key=lambda m: COMMUTE_FACTORS[m])
            st.markdown(f"""
            <div class="card" style="margin-top:1rem;border-left:4px solid {info['color']};min-height:186px">
              <div class="m-label">{tt("Air-Quality Commute Advice", "Hava Kalitesine G?re Ula??m ?nerisi")}</div>
              <div style="font-size:.95rem;color:#1D1D1F;line-height:1.7">{exposure_note}</div>
              <div style="font-size:.84rem;color:#6b7280;line-height:1.7;margin-top:.7rem">
                Current mode emissions: <b>{current_mode_emissions:.2f} kg CO₂/day</b><br>
                Lowest-emission option in this calculator: <b>{best_mode}</b>
              </div>
            </div>
            """, unsafe_allow_html=True)

        with commute_right:
            commute_res = calc_commute(commute_mode, commute_km)
            st.session_state.commute_saved = commute_res["daily"]
            savings_label = (
                "You are currently using the car baseline, so savings versus driving are 0.00."
                if commute_mode == "Car" else
                f"Compared with driving, this choice avoids {commute_res['daily']:.2f} kg CO₂ today."
            )
            st.markdown(f"""
            <div class="savings-card" style="min-height:186px;display:flex;flex-direction:column;justify-content:center">
              <p class="sv-num">{commute_res['daily']:.2f}</p>
              <p class="sv-label">kg CO₂ saved today vs. driving</p>
              <div class="sv-extra">
                Weekly: <b>{commute_res['weekly']:.1f} kg</b> · Monthly: <b>{commute_res['monthly']:.1f} kg</b><br>
                🌳 {commute_res['tree_days']:.1f} tree-days of oxygen equivalent
              </div>
            </div>
            <div class="card" style="margin-top:1rem;min-height:120px">
              <div class="m-label">{tt("Commute Summary", "Ula??m ?zeti")}</div>
              <div style="font-size:.92rem;color:#1D1D1F;line-height:1.7">{savings_label}</div>
            </div>
            """, unsafe_allow_html=True)

            fig_commute = build_commute_savings_chart(commute_km, commute_mode)
        st.markdown("<div class='action-block-gap'></div>", unsafe_allow_html=True)
        st.plotly_chart(fig_commute, use_container_width=True, config={"displayModeBar": False, "responsive": True})
        render_reports_relationship(reports_relationship_copy["commute"])

    # ── TAB 2: CARBON FOOTPRINT ──
    with tabs[1]:
        render_section("🌱 Carbon Footprint Calculator")
        st.caption(tt("Your carbon footprint updates instantly as you adjust the inputs.", "Degerleri degistirdikce karbon ayak izinizi aninda gorursunuz."))

        HEAT_F = {"Natural gas": 0.18, "Electric": 0.10, "Heat pump": 0.05,
                  "District heating": 0.08, "None": 0.0}

        ta_c1, ta_c2 = st.columns([3, 2])
        with ta_c1:
            ta_wkm  = st.slider(tt("Weekly driving distance (km)", "Haftalik arac kullanimi (km)"), 0, 2000, 100, 10, key="ta_wkm")
            ta_meat = st.select_slider("Meat consumption frequency", options=list(MEAT_FACTORS.keys()), key="ta_meat")
            ta_flts = st.slider(tt("Flights per year", "Yillik ucus sayisi"), 0, 50, 2, 1, key="ta_flts")
            ta_elec = st.slider(tt("Monthly electricity use (kWh)", "Aylik elektrik tuketimi (kWh)"), 0, 1500, 300, 25, key="ta_elec")
            ta_heat = st.selectbox(tt("Home heating type", "Ev isitma tipi"),
                                    ["Natural gas","Electric","Heat pump","District heating","None"], key="ta_heat")

        ta_drv   = (ta_wkm * 4 * 0.12) / 1000
        ta_diet  = MEAT_FACTORS.get(ta_meat, 0.10)
        ta_fly   = (ta_flts * 0.25) / 12
        ta_elec_ = ta_elec * 0.000233
        ta_heat_ = HEAT_F.get(ta_heat, 0.10)
        ta_total = ta_drv + ta_diet + ta_fly + ta_elec_ + ta_heat_
        ta_cls   = "low" if ta_total < 0.4 else "mod" if ta_total <= 0.8 else "high"
        ta_em    = {"low":"🥳","mod":"😐","high":"😟"}[ta_cls]
        ta_lbl   = {"low":"Low Impact","mod":"Moderate","high":"High Impact"}[ta_cls]
        ta_bench = (ta_total - 0.6) / 0.6 * 100
        ta_color = {"low":"#34C759","mod":"#FF9500","high":"#FF3B30"}[ta_cls]

        st.session_state.fp_monthly = ta_total
        st.session_state.fp_status  = ta_cls

        with ta_c2:
            ta_grad = {"low":"linear-gradient(135deg,#34C759,#30D158)",
                       "mod":"linear-gradient(135deg,#FF9500,#FFCC00)",
                       "high":"linear-gradient(135deg,#FF3B30,#FF2D55)"}[ta_cls]
            ta_txt  = "#1D1D1F" if ta_cls == "mod" else "#fff"
            trees_needed = ta_total * 1000 / 21.7
            km_car_equiv = ta_total * 1000 / 0.12
            st.markdown(f"""
            <div style="background:{ta_grad};border-radius:20px;padding:2rem;text-align:center;color:{ta_txt}">
              <div style="font-size:3rem">{ta_em}</div>
              <div style="font-size:3rem;font-weight:900;line-height:1">{ta_total:.2f}</div>
              <div style="font-size:.9rem;opacity:.85">t CO₂ / month</div>
              <div style="font-weight:700;font-size:1.2rem;margin-top:.5rem">{ta_lbl}</div>
              <div style="font-size:.78rem;opacity:.8;margin-top:.4rem">
                {"%.0f%% below" % abs(ta_bench) if ta_bench<=0 else "%.0f%% above" % ta_bench} global benchmark
              </div>
              <div style="margin-top:.8rem;padding-top:.8rem;border-top:1px solid rgba(255,255,255,.25);font-size:.8rem;opacity:.85">
                Annual: <b>{ta_total*12:.1f} t/year</b>
              </div>
            </div>
            <div class="card" style="margin-top:.8rem">
              <div class="m-label">What does this equal?</div>
              <div style="font-size:.9rem;line-height:1.8;color:#3A3A3C">
                🌳 {trees_needed:.0f} trees needed to offset<br>
                🚗 {km_car_equiv:.0f} km driven by car<br>
                💡 {ta_total*1000/0.233:.0f} kWh of electricity
              </div>
            </div>
            """, unsafe_allow_html=True)

        cats   = ["🚗 Driving","🥩 Diet","✈️ Flights","⚡ Electricity","🔥 Heating"]
        vals   = [ta_drv, ta_diet, ta_fly, ta_elec_, ta_heat_]
        colors = ["#007AFF","#34C759","#FF9500","#AF52DE","#FF3B30"]
        fig_ta = go.Figure()
        for cat, val, col in zip(cats, vals, colors):
            fig_ta.add_trace(go.Bar(
                name=cat, y=["Your footprint"], x=[val],
                orientation="h", marker_color=col,
                text=f"{val:.3f} t", textposition="inside" if val > 0.02 else "outside",
            ))
        fig_ta.update_layout(
            barmode="stack",
            title=f"Carbon footprint breakdown — {ta_total:.2f} t CO₂/month total",
            height=160, margin=dict(l=0, r=0, t=44, b=0),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(family="Inter"), xaxis_title="t CO₂/month",
            legend=dict(orientation="h", y=-0.4), showlegend=True,
        )
        st.plotly_chart(fig_ta, use_container_width=True)

        max_cat_idx = vals.index(max(vals))
        tips = [
            ("🚗 Driving",     ["Consider public transport or carpooling.", "Switch to an electric or hybrid vehicle.", "Work from home 1–2 days per week."]),
            ("🥩 Diet",        ["Try meat-free Mondays.", "Swap beef for chicken or plant protein.", "Local and seasonal food reduces transport emissions."]),
            ("✈️ Flights",     ["Take train for trips under 600 km.", "Offset unavoidable flights via verified programmes.", "Bundle trips to reduce total flight count."]),
            ("⚡ Electricity", ["Switch to a renewable energy tariff.", "Replace incandescent bulbs with LEDs.", "Unplug standby devices."]),
            ("🔥 Heating",     ["Lower thermostat by 1–2°C — saves ~10%.", "Improve insulation: roof, walls, windows.", "Consider a heat pump for long-term savings."]),
        ]
        tip_cat, tip_list = tips[max_cat_idx]
        st.markdown(f"""
        <div class="card" style="border-left:4px solid {colors[max_cat_idx]};margin-top:.5rem">
          <div class="m-label">Top Action: reduce your {tip_cat} emissions</div>
          {"".join(f'<div style="padding:.3rem 0;color:#3A3A3C;font-size:.9rem">✓ {t}</div>' for t in tip_list)}
        </div>
        """, unsafe_allow_html=True)
        render_reports_relationship(reports_relationship_copy["footprint"])

    # ── TAB 3: DAILY CHECKLIST ──
    with tabs[2]:
        render_section("✅ Daily Action Checklist")
        checklist_labels = {
            "windows_closed":   "Closed windows during high-AQI hours",
            "public_transport": "Used public transport or walked",
            "plants_watered":   "Watered or maintained indoor plants",
            "reduced_meat":     "Reduced meat consumption today",
            "checked_aqi":      "Checked AQI before outdoor exercise",
            "avoided_car":      "Avoided unnecessary car usage",
            "shared_awareness": "Shared air-quality awareness with someone",
            "protected_health": "Protected respiratory health today",
        }
        c1, c2 = st.columns([3, 2])
        with c1:
            st.markdown("<div class='checklist-wrap'>", unsafe_allow_html=True)
            for key, lbl in checklist_labels.items():
                st.session_state.checklist[key] = st.checkbox(lbl, value=st.session_state.checklist[key], key=f"chk_{key}")
            st.markdown("</div>", unsafe_allow_html=True)
        with c2:
            score    = sum(st.session_state.checklist.values())
            progress = score / len(checklist_labels)
            today    = date.today().isoformat()
            if st.session_state.last_date != today and score >= 5:
                if st.session_state.last_date:
                    prev = datetime.strptime(st.session_state.last_date, "%Y-%m-%d").date()
                    if (date.today() - prev).days == 1:
                        st.session_state.streak += 1
                    else:
                        st.session_state.streak = 1
                else:
                    st.session_state.streak = 1
                st.session_state.last_date = today
            elif st.session_state.last_date != today and score < 5:
                st.session_state.streak = 0
            em, badge, subtitle = action_badge(score)
            st.markdown(f"""
            <div class="score-ring-wrap">
              <div class="score-num" style="color:#007AFF">{score}<span style="font-size:1.5rem;color:#8E8E93">/8</span></div>
              <div class="badge-label">{em} {badge}</div>
              <div style="color:#8E8E93;font-size:.85rem;margin-top:.3rem">{subtitle}</div>
              <div style="margin-top:1rem;font-size:.9rem">🔥 Streak: <b>{st.session_state.streak}</b> day(s)</div>
            </div>
            """, unsafe_allow_html=True)
            st.progress(progress)
            st.markdown(f"""
            <div class="card" style="margin-top:.8rem">
              <div class="m-label">{tt("Why this matters", "Bu neden ?nemli?")}</div>
              <div style="font-size:.9rem;color:#1D1D1F;line-height:1.7">
                This checklist should reflect actions you actually took today. It feeds your streak,
                gives the Reports page a meaningful daily action score, and turns Take Action into a real behavior tracker.
              </div>
            </div>
            """, unsafe_allow_html=True)
        render_reports_relationship(reports_relationship_copy["checklist"])

    # ── TAB 4: PLANTS ──
    with tabs[3]:
        render_section("🪴 Clean Air Plants for Your Home")
        st.markdown(f"""
        <div class="card" style="margin-bottom:1rem">
          <div class="m-label">{tt("Action for Today", "Bug?n?n Eylemi")}</div>
          <div style="font-size:.9rem;color:#1D1D1F;line-height:1.7">
            Pick one plant strategy you can actually do now: add a low-maintenance plant, reposition an existing one for better light,
            or water and clean leaves to keep filtration performance useful.
          </div>
        </div>
        """, unsafe_allow_html=True)
        if aqi_val > 100:
            st.markdown("""<div class="card" style="border-left:4px solid #FF9500;margin-bottom:1rem">
              <b>🌿 Recommendation for today:</b> With elevated outdoor AQI, indoor plants provide
              natural air filtration and improve your indoor environment significantly.
            </div>""", unsafe_allow_html=True)
        plants = [
            ("🐍","Snake Plant","Sansevieria trifasciata","Produces oxygen at night · Removes formaldehyde & benzene","Every 2–3 weeks · Very low light","Bedroom, office"),
            ("🌿","Peace Lily","Spathiphyllum wallisii","Filters benzene, trichloroethylene & ammonia","Keep moist · Indirect light","Living room corners"),
            ("🕷️","Spider Plant","Chlorophytum comosum","Absorbs CO, nitrogen dioxide & xylene","Weekly watering · Partial sun","Kitchen, bathroom"),
            ("🌱","English Ivy","Hedera helix","Reduces airborne mould & fecal particles","Moderate sun · Regular misting","Shelves, hanging baskets"),
            ("🌴","Areca Palm","Dypsis lutescens","Natural humidifier · removes xylene & toluene","Bright filtered light · Moderate watering","Living rooms"),
            ("🪴","Rubber Plant","Ficus elastica","Removes airborne toxins · Low maintenance","Every 1–2 weeks · Bright indirect light","Corners, open spaces"),
        ]
        cols = st.columns(3)
        for i, (icon, name, latin, benefit, care, placement) in enumerate(plants):
            with cols[i % 3]:
                st.markdown(f"""
                <div class="plant-card" style="margin-bottom:1rem">
                  <div class="p-icon">{icon}</div>
                  <div class="p-name">{name}</div>
                  <div style="font-size:.7rem;color:#8E8E93;font-style:italic;margin-bottom:.3rem">{latin}</div>
                  <div class="p-benefit">✨ {benefit}</div>
                  <div class="p-care" style="margin-top:.5rem">💧 {care}</div>
                  <div style="font-size:.75rem;color:#007AFF;margin-top:.3rem">📍 {placement}</div>
                </div>
                """, unsafe_allow_html=True)
        render_reports_relationship(reports_relationship_copy["plants"])

    # ── TAB 5: CAMPAIGNS ──
    with tabs[4]:
        render_section("🌎 Environmental Action Programmes")
        st.markdown(f"""
        <div class="card" style="margin-bottom:1rem">
          <div class="m-label">{tt("Action for Today", "Bug?n?n Eylemi")}</div>
          <div style="font-size:.9rem;color:#1D1D1F;line-height:1.7">
            Choose one program, follow it, subscribe, donate, or share one campaign link. This tab should lead to a concrete civic action,
            not just browsing.
          </div>
        </div>
        """, unsafe_allow_html=True)
        campaigns = [
            ("C40 Cities","Global Cities","City-led climate leadership network.","https://www.c40.org/"),
            ("WHO Air Quality","Health","Global public-health air-quality guidance.","https://www.who.int/"),
            ("WWF","Nature","Climate, biodiversity, and sustainable living.","https://www.worldwildlife.org/"),
            ("Greenpeace","Advocacy","Campaigns for clean energy and clean air.","https://www.greenpeace.org/"),
            ("UNEP","Policy","UN Environment Programme on pollution reduction.","https://www.unep.org/"),
        ]
        if "Istanbul" in city or city.endswith("TR"):
            campaigns += [
                ("İBB Hava Kalitesi","Municipal","Istanbul metropolitan AQI portal.","https://havakalitesi.ibb.gov.tr/"),
                ("TEMA","NGO","Community environmental action in Türkiye.","https://www.tema.org.tr/"),
            ]
        cols = st.columns(3)
        for i, (name, cat, desc, url) in enumerate(campaigns):
            with cols[i % 3]:
                st.markdown(f"""
                <div class="campaign-card">
                  <div class="c-tag">{cat}</div>
                  <div class="c-name">{name}</div>
                  <div class="c-desc">{desc}</div>
                  <a class="c-link" href="{url}" target="_blank">{tt("Learn More ↗", "Daha Fazla Bilgi ↗")}</a>
                </div>
                """, unsafe_allow_html=True)
        render_reports_relationship(reports_relationship_copy["campaigns"])

    # ── TAB 6: HEALTH ──
    with tabs[5]:
        render_section("🩺 AQI-Based Health Guidance")
        st.markdown(f"""
        <div class="card" style="margin-bottom:1rem;border-left:4px solid {info['color']}">
          <div class="m-label">{tt("Protection Priority", "Koruma ?nceli?i")}</div>
          <div style="font-size:.92rem;color:#1D1D1F;line-height:1.7">
            {full_commentary}
          </div>
        </div>
        """, unsafe_allow_html=True)
        guidance = {
            "Good":                    ["Safe for all outdoor activity.","Open windows freely.","Cycling and walking are low-risk."],
            "Moderate":                ["Sensitive groups should limit prolonged outdoor effort.","Prefer low-traffic routes.","Indoor air filtering adds value."],
            "Unhealthy for Sensitive": ["Children, elderly, and respiratory patients should limit exposure.","Wear a mask in congested areas.","Adjust timing of outdoor activity."],
            "Unhealthy":               ["Reduce outdoor sports.","Use an air purifier indoors.","Avoid traffic corridors during rush hour."],
            "Very Unhealthy":          ["Stay indoors as much as possible.","Seal windows during peak pollution.","Use indoor filtration."],
            "Hazardous":               ["Avoid all outdoor activity.","Follow public-health advisories.","Monitor symptoms closely."],
        }
        current = info["name"]
        for lvl, tips in guidance.items():
            is_curr = (lvl == current)
            with st.expander(f"{'▶ ' if is_curr else ''}{lvl}{tt(' ← Current level', ' ← Geçerli seviye') if is_curr else ''}", expanded=is_curr):
                for tip in tips:
                    st.markdown(f"✓ {tip}")
        render_reports_relationship(reports_relationship_copy["health"])

    # ── TAB 7: EXERCISE ──
    with tabs[6]:
        render_section("🏃 AQI-Smart Exercise Planner")
        city_short = city.split(",")[0]
        ws_val = float(wind["speed"]) if wind and wind.get("speed") else None

        if aqi_val <= 50:
            dot_col, alert_bg, border_c = "#34C759", "#f0fdf4", "#34C759"
            headline = tt("✅ All outdoor exercise is safe today.", "✅ Bugün tüm dış mekân egzersizleri güvenli.")
            indoor    = ["Stretching", "Light mobility", "Indoor walking"]
            light_out = ["Walking", "Easy cycling", "Gentle jogging"]
            heavy_out = ["Longer cardio", "Fast-paced exercise", "Extended outdoor sessions"]
            heavy_note = "✅ All activities recommended."
        elif aqi_val <= 100:
            dot_col, alert_bg, border_c = "#FFCC00", "#fffbeb", "#FFCC00"
            headline = tt("😐 Generally fine — sensitive groups take care.", "😐 Genel olarak uygun; hassas gruplar dikkatli olmalı.")
            indoor    = ["Stretching", "Light home exercise", "Indoor walking"]
            light_out = ["Walking", "Slow cycling", "Light stretching outdoors"]
            heavy_out = ["Jogging", "Faster cycling", "Long outdoor exercise sessions"]
            heavy_note = "⚠️ Avoid peak traffic hours for outdoor routes."
        elif aqi_val <= 150:
            dot_col, alert_bg, border_c = "#FF9500", "#fff7ed", "#FF9500"
            headline = "⚠️ Shift heavy cardio indoors. Light walking is fine."
            indoor    = ["Stretching", "Light home exercise", "Short indoor cardio"]
            light_out = ["Walking", "Very light movement outdoors"]
            heavy_out = ["Running", "Cycling", "Fast cardio", "Long exercise sessions"]
            heavy_note = "⛔ Move these indoors today."
        elif aqi_val <= 200:
            dot_col, alert_bg, border_c = "#FF3B30", "#fff1f2", "#FF3B30"
            headline = tt("🚫 Move all exercise indoors.", "🚫 Tüm egzersizi iç mekâna taşıyın.")
            indoor    = ["Gentle stretching", "Very light home exercise", "Slow indoor walking"]
            light_out = ["Very short walk < 15 min only"]
            heavy_out = ["Running", "Cycling", "Any outdoor cardio"]
            heavy_note = "🚫 Avoid entirely today."
        else:
            dot_col, alert_bg, border_c = "#AF52DE", "#fdf4ff", "#AF52DE"
            headline = tt("🚨 Do NOT exercise outdoors. Hazardous air quality.", "🚨 Dışarıda egzersiz yapmayın. Hava kalitesi tehlikeli düzeyde.")
            indoor    = ["Gentle stretching only", "Slow movement indoors"]
            light_out = ["🚫 Not recommended"]
            heavy_out = ["🚫 Absolutely avoid"]
            heavy_note = "Emergency conditions — follow public health advisories."

        st.markdown(f"""
        <div style="display:flex;align-items:center;gap:.6rem;background:{alert_bg};border-radius:12px;
             padding:.85rem 1.2rem;border:1px solid {border_c}40;margin-bottom:1.2rem">
          <div style="width:13px;height:13px;border-radius:50%;background:{dot_col};flex-shrink:0"></div>
          <span style="font-weight:700;color:{dot_col}">{tt("Caution:", "Dikkat:")} </span>
          <span style="color:#1D1D1F;font-size:.9rem">{headline}</span>
        </div>
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:1rem;margin-bottom:1rem">
          <div style="background:#f8f9fa;border-radius:14px;padding:1.2rem;border:1px solid #e5e7eb">
            <div style="font-weight:700;color:#1D1D1F;margin-bottom:.6rem;font-size:.9rem">{tt("Indoor (Recommended)", "İç Mekân (Önerilen)")}</div>
            <div style="font-size:.84rem;color:#3A3A3C;line-height:1.9">{"<br>".join(f"· {x}" for x in indoor)}</div>
          </div>
          <div style="background:#f8f9fa;border-radius:14px;padding:1.2rem;border:1px solid #e5e7eb">
            <div style="font-weight:700;color:#1D1D1F;margin-bottom:.6rem;font-size:.9rem">{tt("Light Outdoor", "Hafif Dış Mekân")}</div>
            <div style="font-size:.84rem;color:#3A3A3C;line-height:1.9">{"<br>".join(f"· {x}" for x in light_out)}</div>
          </div>
          <div style="background:#f8f9fa;border-radius:14px;padding:1.2rem;border:1px solid #e5e7eb">
            <div style="font-weight:700;color:#1D1D1F;margin-bottom:.6rem;font-size:.9rem">{tt("Intense Outdoor", "Yoğun Dış Mekân")}</div>
            <div style="font-size:.84rem;color:#3A3A3C;line-height:1.9">{"<br>".join(f"· {x}" for x in heavy_out)}</div>
            <div style="font-size:.75rem;color:{dot_col};font-weight:600;margin-top:.4rem">{heavy_note}</div>
          </div>
        </div>
        """, unsafe_allow_html=True)

        if ws_val is not None:
            wdir_v = float(wind.get("direction", 0)) if wind else 0
            wind_advice = (
                "Good news — strong winds are dispersing pollutants. Conditions may improve."
                if ws_val > 6 else
                tt("Moderate wind offers some dispersion. Prefer routes away from traffic.", "Orta d?zey r?zg?r bir miktar da??l?m sa?l?yor. Trafikten uzak rotalar? tercih edin.")
                if ws_val > 2 else
                "Little wind today — pollutants are accumulating. Be extra cautious outdoors."
            )
            st.markdown(f"""
            <div class="card" style="border-left:3px solid #007AFF">
              <div class="m-label">💨 Wind Context for Exercise</div>
              <div style="font-size:.9rem;color:#1D1D1F;line-height:1.6">
                <b>{ws_val:.1f} m/s {wind_dir_label(wdir_v)} ({wdir_v:.0f}°)</b> — {wind_advice}
              </div>
            </div>""", unsafe_allow_html=True)
        render_reports_relationship(reports_relationship_copy["exercise"])

    checklist_score_final = int(sum(st.session_state.checklist.values()))
    commute_saved_final = float(st.session_state.commute_saved or 0.0)
    footprint_final = st.session_state.fp_monthly
    
    # Compute final action score with safety guard
    if compute_action_score:
        action_score_final = compute_action_score(
            checklist_score=checklist_score_final,
            commute_saved=commute_saved_final,
            footprint_monthly=footprint_final,
            aqi_val=aqi_val,
            flags=st.session_state.action_profile_flags,
            commute_mode=st.session_state.commute_mode,
        )
    else:
        action_score_final = 0
    st.session_state.action_score = action_score_final

    if save_today:
        snapshot_payload = {
            "date": date.today().isoformat(),
            "city": city,
            "aqi": int(aqi_val),
            "dominant_pollutant": dom.lower(),
            "wind_speed": float((wind or {}).get("speed") or 0.0),
            "commute_mode": st.session_state.commute_mode,
            "commute_saved": round(commute_saved_final, 3),
            "footprint_monthly": round(float(footprint_final or 0.0), 3),
            "checklist_score": checklist_score_final,
            "action_score": action_score_final,
            "top_actions": [item["title"] for item in st.session_state.action_top3],
        }
        persist_action_snapshot(st.session_state.action_profile_name, snapshot_payload)
        st.success(f"Saved today's action snapshot for {st.session_state.action_profile_name}.")


# ===========================================================================
# PAGE: ANALYTICS
# ===========================================================================
def page_analytics_legacy():
    render_hero("📊 Analytics", "Multi-city & pollutant intelligence")
    render_page_guide(
        "<strong>What this page shows:</strong> Regional air quality comparisons across all tracked cities. "
        "Includes a PM2.5 vs AQI scatter plot, a pollutant profile radar for the most polluted cities, "
        "a regional average chart, and a full sortable ranking table."
    )
    render_live_bar()

    with st.spinner("Loading global analytics…"):
        df = fetch_global_overview()
    if df.empty:
        st.error("Could not load analytics data — check your network connection.")
        return

    # ── Scatter ──
    render_section("🔬 PM2.5 vs AQI by Region")
    fig_sc = px.scatter(
        df, x="pm25", y="aqi", color="region",
        size="wind_speed", hover_name="city",
        size_max=20, height=450,
        title="PM2.5 vs AQI by Region — bubble size = wind speed",
        color_discrete_sequence=px.colors.qualitative.Vivid,
        labels={"pm25": "PM2.5 (µg/m³)", "aqi": "AQI"},
    )
    fig_sc.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                          font=dict(family="Inter"))
    st.plotly_chart(fig_sc, use_container_width=True)

    # ── Regional averages bar chart ──
    render_section("🌏 Regional Average AQI")
    df_reg = df.groupby("region")["aqi"].mean().reset_index().sort_values("aqi", ascending=False)
    df_reg["color"] = df_reg["aqi"].apply(lambda x: aqi_info(x)["color"])
    fig_reg = go.Figure(go.Bar(
        x=df_reg["region"], y=df_reg["aqi"],
        marker_color=df_reg["color"].tolist(),
        text=df_reg["aqi"].apply(lambda x: f"{x:.0f}"),
        textposition="outside",
    ))
    fig_reg.update_layout(
        yaxis_title="Average AQI", height=320,
        margin=dict(l=0, r=0, t=24, b=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter"),
    )
    st.plotly_chart(fig_reg, use_container_width=True)

    # ── Pollutant radar (top 5 cities) ──
    render_section("📡 Pollutant Profile — Top 5 Polluted Cities")
    top5 = df.nlargest(5, "aqi")
    fig_r = go.Figure()
    pols  = ["pm25","pm10","no2","o3"]
    for _, row in top5.iterrows():
        vals = [row[p] for p in pols] + [row[pols[0]]]
        fig_r.add_trace(go.Scatterpolar(
            r=vals, theta=["PM2.5","PM10","NO₂","O₃","PM2.5"],
            fill="toself", name=row["city"].split(",")[0],
        ))
    fig_r.update_layout(
        polar=dict(radialaxis=dict(visible=True)),
        title="Pollutant profile radar — top 5 most polluted cities",
        height=420, font=dict(family="Inter"),
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_r, use_container_width=True)

    # ── AQI distribution histogram ──
    render_section("📈 AQI Distribution Across All Cities")
    fig_hist = go.Figure(go.Histogram(
        x=df["aqi"], nbinsx=20,
        marker_color="#007AFF", opacity=0.75,
    ))
    fig_hist.update_layout(
        xaxis_title="AQI", yaxis_title="Number of cities",
        height=300, margin=dict(l=0, r=0, t=24, b=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter"),
    )
    st.plotly_chart(fig_hist, use_container_width=True)

    # ── Full ranking table ──
    render_section("🏆 Full City Ranking Table")
    df_show = df[["city","region","aqi","pm25","pm10","no2","wind_speed"]].sort_values("aqi", ascending=False).reset_index(drop=True)
    df_show.index += 1
    df_show.columns = ["City","Region","AQI","PM2.5","PM10","NO₂","Wind (m/s)"]
    st.dataframe(df_show, use_container_width=True)

# ===========================================================================
# PAGE: ANALYTICS (upgraded override)
# ===========================================================================
def page_analytics():
    render_hero(_t("analytics.title"), _t("analytics.subtitle"))
    render_page_guide(_t("analytics.guide"))
    render_live_bar()

    if analytics_engine is None:
        st.error("Advanced analytics engine could not be loaded.")
        return

    analytics_loading = st.empty()
    with analytics_loading.container():
        render_loading_skeleton(cards=4)
    with st.spinner("Loading global analytics baseline..."):
        df = fetch_global_overview()
    analytics_loading.empty()
    if df.empty:
        st.error("Analytics data could not be loaded right now. Please check your connection and try again.")
        return

    city_options = list(GLOBAL_CITIES.keys())
    default_city = st.session_state.city if st.session_state.city in GLOBAL_CITIES else "Istanbul, TR"
    default_compare = "London, UK" if default_city != "London, UK" else "Paris, FR"

    ctl1, ctl2, ctl3 = st.columns([2.3, 1.4, 1.4])
    with ctl1:
        selected_city = st.selectbox(_t("analytics.primary_city"), city_options, index=city_options.index(default_city), key="an_city")
    with ctl2:
        compare_city = st.selectbox(_t("analytics.compare_against"), city_options, index=city_options.index(default_compare), key="an_compare")
    with ctl3:
        pollutant = st.selectbox(_t("analytics.pollutant_focus"), ["pm25", "pm10", "o3", "no2"], index=0, key="an_pollutant")

    st.session_state.city = selected_city
    city_meta = GLOBAL_CITIES[selected_city]
    compare_meta = GLOBAL_CITIES[compare_city]

    selected_row = df[df["city"] == selected_city]
    current_aqi = float(selected_row["aqi"].iloc[0]) if not selected_row.empty else 0.0
    region_mean = float(df[df["region"] == city_meta["region"]]["aqi"].mean())
    normalized_factor = current_aqi / region_mean if region_mean else 0.0
    region_rank = int(df[df["region"] == city_meta["region"]]["aqi"].rank(method="min", ascending=False).loc[selected_row.index].iloc[0]) if not selected_row.empty else 0

    render_metric_grid([
        (_t("analytics.current_aqi"), f"{current_aqi:.0f}", aqi_info(current_aqi)["name"], aqi_info(current_aqi)["color"]),
        (_t("analytics.region_mean"), f"{region_mean:.0f}", city_meta["region"], "#007AFF"),
        (_t("analytics.normalized_aqi"), f"{normalized_factor:.2f}x", "vs region mean", "#AF52DE"),
        (_t("analytics.regional_rank"), str(region_rank), "higher means worse", "#FF9500"),
    ])
    st.markdown("<br>", unsafe_allow_html=True)

    analysis_view = st.radio(
        _t("analytics.analysis_lens"),
        [_t("analytics.temporal_patterns"), "Wind and Dispersion", _t("analytics.forecast_accuracy"), _t("analytics.comparative_analysis"), _t("analytics.anomaly_detection")],
        horizontal=True,
        key="analytics_lens",
    )

    if analysis_view == _t("analytics.temporal_patterns"):
        with st.spinner("Building temporal pattern analytics..."):
            hourly_df = analytics_engine.fetch_air_quality_hourly(city_meta["lat"], city_meta["lon"], days=90)
            temporal = analytics_engine.build_temporal_patterns(hourly_df, pollutant)
        if temporal.get("error"):
            st.info(temporal["error"])
            return
        render_section("Hourly and weekly temporal patterns")
        t1, t2 = st.columns(2)
        with t1:
            st.plotly_chart(temporal["heatmap_fig"], use_container_width=True, config={"displayModeBar": False, "responsive": True})
        with t2:
            st.plotly_chart(temporal["weekday_weekend_fig"], use_container_width=True, config={"displayModeBar": False, "responsive": True})
        st.plotly_chart(temporal["rolling_fig"], use_container_width=True, config={"displayModeBar": False, "responsive": True})
        st.markdown(f"""
        <div class="card" style="margin-top:1rem">
          <div class="m-label">Temporal reading</div>
          <div style="font-size:.92rem;color:#1D1D1F;line-height:1.8">
            Weekday AQI average: <b>{temporal['weekday_aqi']:.1f}</b><br>
            Weekend AQI average: <b>{temporal['weekend_aqi']:.1f}</b><br>
            The heatmap highlights intraday load by weekday, while the rolling average smooths seasonal baseline.
          </div>
        </div>
        """, unsafe_allow_html=True)

    elif analysis_view == "Wind and Dispersion":
        with st.spinner("Building geospatial diagnostics..."):
            hourly_df = analytics_engine.fetch_air_quality_hourly(city_meta["lat"], city_meta["lon"], days=14)
            weather_df = analytics_engine.fetch_weather_hourly(city_meta["lat"], city_meta["lon"], days=14)
            dispersion_map = analytics_engine.build_dispersion_map(selected_city, city_meta["lat"], city_meta["lon"], hourly_df, weather_df, pollutant)
            lag_analysis = analytics_engine.compute_neighbor_lag_analysis(selected_city, city_meta, GLOBAL_CITIES, pollutant)

        render_section(_t("analytics.wind_dispersion"))
        st.caption("Animated vector traces show recent wind direction and pollutant carriage from the selected city centroid.")
        safe_render_folium_map(
            dispersion_map,
            height=430,
            warning_message="The wind dispersion map is temporarily unavailable. The rest of the analytics remain available.",
        )

        if not lag_analysis.get("error"):
            render_section(_t("analytics.neighbor_lag"))
            st.plotly_chart(lag_analysis["lag_fig"], use_container_width=True, config={"displayModeBar": False, "responsive": True})
            st.dataframe(lag_analysis["lag_df"], use_container_width=True)

    elif analysis_view == _t("analytics.forecast_accuracy"):
        render_section(_t("analytics.forecast_dashboard"))
        forecast_perf = analytics_engine.compute_forecast_accuracy(
            selected_city,
            pollutant,
            lat=city_meta["lat"],
            lon=city_meta["lon"],
        )
        if forecast_perf.get("error"):
            st.info(forecast_perf["error"])
        else:
            if forecast_perf.get("mode") == "backtest":
                st.caption(_t("analytics.fallback_note"))
            render_metric_grid([
                ("MAE", f"{forecast_perf['mae']:.2f}", pollutant.upper(), "#007AFF"),
                ("RMSE", f"{forecast_perf['rmse']:.2f}", pollutant.upper(), "#FF9500"),
                (
                    "Points",
                    str(int(forecast_perf.get("points", 0) or 0)),
                    "validated forecasts" if forecast_perf.get("mode") == "validated" else "historical backtest days",
                    "#AF52DE",
                ),
            ])
            st.markdown("<br>", unsafe_allow_html=True)
            st.plotly_chart(forecast_perf["scatter_fig"], use_container_width=True, config={"displayModeBar": False, "responsive": True})

        if forecast_perf.get("error"):
            st.info(forecast_perf["error"])
        else:
            alert_color = "#FF3B30" if forecast_perf["drift_flag"] else "#34C759"
            st.markdown(f"""
            <div class="card" style="border-left:4px solid {alert_color};margin-top:1rem">
              <div class="m-label">Model drift detection</div>
              <div style="font-size:.95rem;color:#1D1D1F;line-height:1.7">{forecast_perf['drift_text']}</div>
            </div>
            """, unsafe_allow_html=True)
    elif analysis_view == _t("analytics.comparative_analysis"):
        render_section(_t("analytics.comparison"))
        comparison = analytics_engine.build_comparative_analysis(selected_city, compare_city, city_meta, compare_meta, pollutant, df)
        if comparison.get("error"):
            st.info(comparison["error"])
        else:
            c1, c2 = st.columns(2)
            with c1:
                st.plotly_chart(comparison["time_series_fig"], use_container_width=True, config={"displayModeBar": False, "responsive": True})
            with c2:
                st.plotly_chart(comparison["normalized_fig"], use_container_width=True, config={"displayModeBar": False, "responsive": True})

    elif analysis_view == _t("analytics.anomaly_detection"):
        render_section(_t("analytics.anomaly_title"))
        city_parts = [part.strip() for part in str(selected_city).split(",")]
        country_label = city_parts[-1] if len(city_parts) > 1 else city_meta.get("region", "selected region")
        st.caption(f"Anomalies are evaluated against the recent {pollutant.upper()} pattern for {selected_city} ({country_label}).")
        with st.spinner("Detecting anomalies..."):
            hourly_df = analytics_engine.fetch_air_quality_hourly(city_meta["lat"], city_meta["lon"], days=90)
            anomalies = analytics_engine.build_anomaly_detection(hourly_df, pollutant)
        if anomalies.get("error"):
            st.info(anomalies["error"])
        else:
            st.plotly_chart(anomalies["anomaly_fig"], use_container_width=True, config={"displayModeBar": False, "responsive": True})
            tone = "#FF3B30" if anomalies["deviation_pct"] > 0 else "#34C759"
            st.markdown(f"""
            <div class="card" style="border-left:4px solid {tone};margin-top:1rem">
              <div class="m-label">Expected vs actual</div>
              <div style="font-size:.95rem;color:#1D1D1F;line-height:1.7">{anomalies['anomaly_text']}</div>
            </div>
            """, unsafe_allow_html=True)
            anomaly_rows = anomalies["anomaly_df"][["date", "aqi", "zscore"]].tail(12).copy()
            if not anomaly_rows.empty:
                anomaly_rows["date"] = pd.to_datetime(anomaly_rows["date"]).dt.strftime("%Y-%m-%d")
                st.dataframe(anomaly_rows, use_container_width=True)

# ===========================================================================
# PAGE: NETWORK EXPANSION
# ===========================================================================

# ===========================================================================
# PAGE: REPORTS
# ===========================================================================
def page_reports():
    render_hero(_t("reports.title"), _t("reports.subtitle"))
    render_page_guide(
        tt(
            "<strong>Overview:</strong> Build a polished city report with live AQI, personal footprint inputs, commute impact, and export-ready assets.",
            "<strong>Bu sayfada neler yapabilirsiniz:</strong> Herhangi bir şehir için profesyonel bir PDF raporu oluşturabilir; kişisel karbon ayak izinizi, ulaşım tasarrufunuzu ve günlük kontrol listesi skorunuzu ekleyebilirsiniz. Ayrıca sosyal medya kartını (JPEG) ve tam veri CSV dosyasını indirebilirsiniz.",
        )
    )

    api_key = get_waqi_key()
    wk      = get_tomorrow_key()
    render_live_bar()
    render_secret_warnings(api_key, wk)

    st.markdown(f"### {_t('reports.report_city')}")
    report_city = st.text_input(_t("reports.report_city"), value=st.session_state.city, key="rep_city_in")

    if report_city in GLOBAL_CITIES:
        coords = GLOBAL_CITIES[report_city]
    else:
        coords = {"lat": 41.0, "lon": 29.0}

    reports_loading = st.empty()
    with reports_loading.container():
        render_loading_skeleton(cards=3)
    with st.spinner(f"Loading live data for {report_city}…"):
        report_snapshot = get_live_city_snapshot(report_city, coords["lat"], coords["lon"], include_stations=True)
        waqi_d = report_snapshot["waqi"]
        if waqi_d and coords.get("lat") == 41.0:
            coords["lat"] = waqi_d.get("lat", 41.0)
            coords["lon"] = waqi_d.get("lon", 29.0)
            report_snapshot = get_live_city_snapshot(report_city, coords["lat"], coords["lon"], include_stations=True)
            waqi_d = report_snapshot["waqi"]
        stations = report_snapshot["stations"]
        wind     = report_snapshot["wind"]
    reports_loading.empty()

    aqi_val = float(waqi_d.get("aqi", 0))
    info    = aqi_info(aqi_val)
    ws_val  = (wind.get("speed") or 0) if wind else waqi_d.get("wind_speed", 0)
    wd_val  = (wind.get("direction") or 0) if wind else waqi_d.get("wind_dir", 0)

    st.markdown("---")
    st.markdown("### 🌱 Personal Carbon Footprint Inputs")
    st.caption(tt("These values are embedded in your PDF report and used in all calculations.", "Bu degerler PDF raporuna eklenir ve tum hesaplamalarda kullanilir."))

    HEAT_FACTORS = {"Natural gas":0.18,"Electric":0.10,"Heat pump":0.05,"District heating":0.08,"None":0.0}
    if not st.session_state.get("report_user_name"):
        st.session_state.report_user_name = "AirPulse User"
    st.session_state["rep_mode"] = st.session_state.get("commute_mode", st.session_state.get("rep_mode", "Car"))
    st.session_state["rep_km"] = float(st.session_state.get("commute_km", st.session_state.get("rep_km", 10.0)))
    st.session_state["rep_name"] = st.session_state.report_user_name
    rep_name = st.text_input(tt("Report prepared for (your name)", "Raporun hazirlandigi kisi"), key="rep_name")
    st.session_state.report_user_name = rep_name
    report_action_score = int(st.session_state.get("action_score", 0) or 0)
    report_checklist_score = int(sum(st.session_state.checklist.values()))
    report_commute_mode = st.session_state.get("commute_mode", "Car")
    report_commute_km = float(st.session_state.get("commute_km", 10.0) or 0.0)
    report_top_actions = [item.get("title", "") for item in st.session_state.get("action_top3", []) if item.get("title")]
    st.markdown(f"""
    <div class="card" style="margin:.75rem 0 1.1rem;border-left:4px solid #007AFF">
      <div class="m-label">Report Link</div>
      <div style="font-size:.92rem;color:#1D1D1F;line-height:1.7">
        This report is now linked directly to Take Action.<br>
        Imported now: <b>checklist {report_checklist_score}/8</b>, <b>action score {report_action_score}</b>,
        <b>{report_commute_mode}</b> commute for <b>{report_commute_km:.1f} km/day</b>, and live city context.
      </div>
    </div>
    """, unsafe_allow_html=True)
    if report_top_actions:
        report_action_lines = "<br>".join(
            f"{idx + 1}. {title}" for idx, title in enumerate(report_top_actions[:3])
        )
        st.markdown(f"""
        <div class="card" style="margin:-.35rem 0 1.1rem;border-left:4px solid #34C759">
          <div class="m-label">Top Actions Imported From Take Action</div>
          <div style="font-size:.92rem;color:#1D1D1F;line-height:1.8">
            {report_action_lines}
          </div>
        </div>
        """, unsafe_allow_html=True)

    fp_r1c1, fp_r1c2, fp_r1c3 = st.columns(3)
    with fp_r1c1:
        rep_wkm  = st.number_input("Weekly driving (km)", 0, 3000, 100, 10, key="rep_wkm")
        rep_meat = st.selectbox(tt("Meat consumption", "Et tuketimi"), list(MEAT_FACTORS.keys()), key="rep_meat")
        rep_walk = st.number_input("Weekly walking distance (km)", 0, 100, 5, 1, key="rep_walk")
    with fp_r1c2:
        rep_flts = st.number_input(tt("Flights per year", "Yillik ucus sayisi"), 0, 100, 2, 1, key="rep_flts")
        rep_elec = st.number_input("Monthly electricity (kWh)", 0, 2000, 300, 50, key="rep_elec")
        rep_pub  = st.number_input("Weekly public transport use (trips)", 0, 50, 5, 1, key="rep_pub")
    with fp_r1c3:
        rep_heat = st.selectbox(tt("Home heating", "Ev isitmasi"), list(HEAT_FACTORS.keys()), key="rep_heat")
        rep_mode = st.selectbox(tt("Commute mode", "Ulasim sekli"), list(COMMUTE_FACTORS.keys()), key="rep_mode")
        rep_km   = st.number_input(tt("Commute distance (km/day)", "Ulasim mesafesi (km/gun)"), 0.0, 200.0, 10.0, 0.5, key="rep_km")

    fp_drv   = (rep_wkm * 4 * 0.12) / 1000
    fp_diet  = MEAT_FACTORS.get(rep_meat, 0.10)
    fp_fly   = (rep_flts * 0.25) / 12
    fp_elec  = rep_elec * 0.000233
    fp_heat  = HEAT_FACTORS.get(rep_heat, 0.10)
    # Public transport credit (small emission offset vs car)
    fp_pub_credit = (rep_pub * 0.05 * 5 * 0.12 - rep_pub * 0.05 * 5 * 0.05) / 1000
    # Walking credit (offset vs car)
    fp_walk_credit = (rep_walk * 0.12) / 1000
    fp_total = max(0, fp_drv + fp_diet + fp_fly + fp_elec + fp_heat - fp_pub_credit - fp_walk_credit)
    fp_cls   = "low" if fp_total < 0.4 else "mod" if fp_total <= 0.8 else "high"
    fp_emoji = {"low":"🥳","mod":"😐","high":"😟"}[fp_cls]
    fp_label = {"low":"Low Impact","mod":"Moderate","high":"High Impact"}[fp_cls]

    commute_res     = calc_commute(rep_mode, rep_km)
    checklist_score = sum(st.session_state.checklist.values())
    report_action_score = int(st.session_state.get("action_score", 0) or 0)
    report_top_actions = [item.get("title", "") for item in st.session_state.get("action_top3", []) if item.get("title")]
    bench_pct       = (fp_total - 0.6) / 0.6 * 100

    st.markdown("---")
    st.markdown(f"### {tt('Report Preview', 'Rapor Önizlemesi')}")

    st.markdown(f"""
    <div class="card" style="margin:.5rem 0 1rem;border-left:4px solid #34C759">
      <div class="m-label">Imported From Take Action</div>
      <div style="font-size:.92rem;color:#1D1D1F;line-height:1.8">
        Action score: <b>{report_action_score}</b><br>
        Checklist progress: <b>{checklist_score}/8</b><br>
        Current commute plan: <b>{rep_mode}</b> for <b>{rep_km:.1f} km/day</b><br>
        {("Top actions: " + " | ".join(report_top_actions[:3])) if report_top_actions else "Top actions: not available yet"}
      </div>
    </div>
    """, unsafe_allow_html=True)

    prev_left, prev_right = st.columns([3, 2])
    with prev_left:
        fp_clr = "#34C759" if fp_cls == "low" else "#FF9500" if fp_cls == "mod" else "#FF3B30"
        st.markdown(f"""
        <div class="card card-dark">
          <div style="font-size:.65rem;opacity:.68;letter-spacing:.12em;text-transform:uppercase;margin-bottom:.8rem;color:rgba(255,255,255,.78)">
            ✦ AIRPULSE GLOBAL · REPORT FOR {rep_name.upper()} ✦
          </div>
          <div style="display:flex;justify-content:space-between;align-items:flex-start">
            <div>
              <div style="font-size:2rem;font-weight:900;color:#fff">{report_city.split(",")[0]}</div>
              <div style="font-size:1.05rem;font-weight:700;color:{info['color']}">
                AQI {int(aqi_val)} · {info['name']} {info['icon']}
              </div>
            </div>
            <div style="text-align:right">
              <div style="font-size:.65rem;opacity:.72;text-transform:uppercase;color:rgba(255,255,255,.82)">{rep_name}</div>
              <div style="font-size:1.6rem;font-weight:900;color:{fp_clr}">{fp_total:.2f} t</div>
              <div style="font-size:.75rem;color:{fp_clr}">{fp_emoji} {fp_label}</div>
            </div>
          </div>
          <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:.8rem;
                      margin-top:1.2rem;padding-top:1rem;border-top:1px solid rgba(255,255,255,.1)">
            <div><div style="font-size:.6rem;opacity:.72;text-transform:uppercase;color:rgba(255,255,255,.82)">PM2.5</div>
                 <div style="font-weight:800;font-size:1.1rem;color:#fff">{waqi_d.get("pm25",0):.1f}</div></div>
            <div><div style="font-size:.6rem;opacity:.72;text-transform:uppercase;color:rgba(255,255,255,.82)">PM10</div>
                 <div style="font-weight:800;font-size:1.1rem;color:#fff">{waqi_d.get("pm10",0):.1f}</div></div>
            <div><div style="font-size:.6rem;opacity:.72;text-transform:uppercase;color:rgba(255,255,255,.82)">O₃</div>
                 <div style="font-weight:800;font-size:1.1rem;color:#fff">{waqi_d.get("o3",0):.1f}</div></div>
            <div><div style="font-size:.6rem;opacity:.72;text-transform:uppercase;color:rgba(255,255,255,.82)">NO₂</div>
                 <div style="font-weight:800;font-size:1.1rem;color:#fff">{waqi_d.get("no2",0):.1f}</div></div>
            <div><div style="font-size:.6rem;opacity:.72;text-transform:uppercase;color:rgba(255,255,255,.82)">Wind</div>
                 <div style="font-weight:800;font-size:1.1rem;color:#fff">{f"{ws_val:.1f} m/s" if ws_val else "—"}</div></div>
            <div><div style="font-size:.6rem;opacity:.72;text-transform:uppercase;color:rgba(255,255,255,.82)">Stations</div>
                 <div style="font-weight:800;font-size:1.1rem;color:#fff">{len(stations)}</div></div>
            <div><div style="font-size:.6rem;opacity:.72;text-transform:uppercase;color:rgba(255,255,255,.82)">CO₂ Saved</div>
                 <div style="font-weight:800;font-size:1.1rem;color:#fff">{commute_res["daily"]:.2f} kg/d</div></div>
            <div><div style="font-size:.6rem;opacity:.72;text-transform:uppercase;color:rgba(255,255,255,.82)">Checklist</div>
                 <div style="font-weight:800;font-size:1.1rem;color:#fff">{checklist_score}/8</div></div>
          </div>
          <div style="margin-top:1rem;font-size:.65rem;opacity:.72;color:rgba(255,255,255,.82)">
            Generated {datetime.now().strftime("%d %b %Y %H:%M")} · AirPulse Global
          </div>
        </div>
        """, unsafe_allow_html=True)

        fp_cats   = ["🚗 Driving","🥩 Diet","✈️ Flights","⚡ Electricity","🔥 Heating"]
        fp_vals   = [fp_drv, fp_diet, fp_fly, fp_elec, fp_heat]
        fp_colors = ["#007AFF","#34C759","#FF9500","#AF52DE","#FF3B30"]
        fig_fp, fig_donut = build_report_footprint_charts(fp_cats, fp_vals, fp_colors, fp_total)

    with prev_right:
        fp_grad = {"low":"linear-gradient(135deg,#34C759,#30D158)",
                   "mod":"linear-gradient(135deg,#FF9500,#FFCC00)",
                   "high":"linear-gradient(135deg,#FF3B30,#FF2D55)"}[fp_cls]
        fp_txt_clr = "#1D1D1F" if fp_cls == "mod" else "#fff"
        st.markdown(f"""
        <div style="background:{fp_grad};border-radius:20px;padding:1.8rem;text-align:center;
             color:{fp_txt_clr};margin-bottom:1.2rem">
          <div style="font-size:2.8rem">{fp_emoji}</div>
          <div style="font-size:2.5rem;font-weight:900;line-height:1">{fp_total:.2f}</div>
          <div style="font-size:.9rem;opacity:.85">t CO₂ / month</div>
          <div style="font-weight:700;font-size:1.1rem;margin-top:.5rem">{fp_label}</div>
          <div style="font-size:.78rem;opacity:.8;margin-top:.4rem">
            {"%.0f%% below" % abs(bench_pct) if bench_pct<=0 else "%.0f%% above" % bench_pct} global 0.6 t/mo benchmark
          </div>
          <div style="margin-top:.8rem;padding-top:.8rem;border-top:1px solid rgba(255,255,255,.25);font-size:.78rem;opacity:.85">
            Annual: <b>{fp_total*12:.1f} t CO₂/year</b>
          </div>
        </div>
        """, unsafe_allow_html=True)

        if commute_res["daily"] > 0:
            st.markdown(f"""
            <div style="background:linear-gradient(135deg,#007AFF,#5856D6);border-radius:16px;
                 padding:1.2rem;text-align:center;color:#fff;margin-bottom:1.2rem">
              <div style="font-size:.7rem;opacity:.7;text-transform:uppercase;letter-spacing:.08em">Commute CO₂ Saved</div>
              <div style="font-size:2rem;font-weight:900">{commute_res["daily"]:.2f} kg/day</div>
              <div style="font-size:.78rem;opacity:.85;margin-top:.4rem">
                Weekly: {commute_res["weekly"]:.1f} kg · Monthly: {commute_res["monthly"]:.1f} kg
              </div>
              <div style="font-size:.78rem;opacity:.75;margin-top:.3rem">
                🌳 {commute_res["tree_days"]:.1f} tree-days of oxygen equivalent
              </div>
            </div>
            """, unsafe_allow_html=True)

        waqi_for_pdf = dict(waqi_d)
        waqi_for_pdf.update({
            "fp_total": fp_total, "fp_label": fp_label,
            "fp_driving": fp_drv, "fp_diet": fp_diet,
            "fp_flights": fp_fly, "fp_elec": fp_elec, "fp_heat": fp_heat,
            "user_name": rep_name,
            "action_score": report_action_score,
            "top_actions": report_top_actions[:3],
        })

        pdf_bytes, jpeg_bytes = safe_generate_report_assets(
            lambda: generate_pdf_report(
                report_city, waqi_for_pdf, stations, wind,
                rep_mode, rep_km, commute_res["daily"], fp_total, checklist_score,
                aqi_info=aqi_info,
                wind_dir_label=wind_dir_label,
                translate=_t,
                format_datetime=format_datetime,
            ),
            lambda: generate_social_card(
                report_city, aqi_val, info["name"], info["color"],
                waqi_d.get("pm25", 0), waqi_d.get("pm10", 0),
                ws_val, commute_res["daily"], report_action_score,
                translate=_t,
                format_date=format_date,
                format_datetime=format_datetime,
            ),
        )

        csv_df = pd.DataFrame([{
            "date": datetime.now().strftime("%Y-%m-%d"),
            "city": report_city, "aqi": aqi_val,
            "pm25": waqi_d.get("pm25",0), "pm10": waqi_d.get("pm10",0),
            "o3": waqi_d.get("o3",0), "no2": waqi_d.get("no2",0),
            "wind_speed": ws_val, "wind_dir": wd_val,
            "station_count": len(stations),
            "commute_mode": rep_mode, "commute_km": rep_km,
            "co2_saved_daily": commute_res["daily"],
            "co2_saved_monthly": commute_res["monthly"],
            "fp_total_t_per_month": fp_total,
            "fp_driving": fp_drv, "fp_diet": fp_diet,
            "fp_flights": fp_fly, "fp_electricity": fp_elec, "fp_heating": fp_heat,
            "fp_pub_transport_credit": fp_pub_credit, "fp_walking_credit": fp_walk_credit,
            "fp_level": fp_cls,
            "checklist_score": checklist_score,
            "action_score": report_action_score,
            "top_actions": " | ".join(report_top_actions[:3]),
            "streak": st.session_state.streak,
        }])

    st.markdown("<div style='height:.9rem'></div>", unsafe_allow_html=True)
    chart_left, chart_right = st.columns([1.15, 0.85], gap="large")
    with chart_left:
        st.plotly_chart(fig_fp, use_container_width=True, config={"displayModeBar": False, "responsive": True})
    with chart_right:
        st.plotly_chart(fig_donut, use_container_width=True, config={"displayModeBar": False, "responsive": True})

    render_section(_t("reports.downloads"))
    dl1, dl2, dl3 = st.columns(3)
    with dl1:
        if pdf_bytes:
            st.download_button(
                _t("reports.pdf"),
                data=pdf_bytes,
                file_name=f"airpulse_{report_city.replace(',','').replace(' ','_')}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        else:
            st.info("PDF report is temporarily unavailable. Please try again shortly.")
    with dl2:
        if jpeg_bytes:
            st.download_button(
                _t("reports.jpeg"),
                data=jpeg_bytes,
                file_name=f"airpulse_card_{report_city.replace(',','').replace(' ','_')}.jpg",
                mime="image/jpeg",
                use_container_width=True,
            )
        else:
            st.info("Social card export is temporarily unavailable.")
    with dl3:
        st.download_button(
            _t("reports.csv"),
            data=csv_df.to_csv(index=False).encode("utf-8"),
            file_name=f"airpulse_data_{report_city.replace(',','').replace(' ','_')}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    render_section(_t("reports.share"))
    share_txt = _t(
        "reports.share_text",
        city=report_city.split(",")[0],
        aqi=int(aqi_val),
        level=info["name"],
        pm25=float(waqi_d.get("pm25", 0)),
        footprint=fp_total,
    )
    tw = f"https://twitter.com/intent/tweet?text={urllib.parse.quote(share_txt)}"
    wa = f"https://wa.me/?text={urllib.parse.quote(share_txt)}"
    li = (f"https://www.linkedin.com/sharing/share-offsite/?"
          f"url={urllib.parse.quote('https://airpulse.global')}"
          f"&summary={urllib.parse.quote(share_txt)}")
    st.markdown(f"""
    <div class="share-row">
      <a class="share-btn s-tw" href="{tw}" target="_blank">Share on X / Twitter</a>
      <a class="share-btn s-wa" href="{wa}" target="_blank">Share on WhatsApp</a>
      <a class="share-btn s-li" href="{li}" target="_blank">Share on LinkedIn</a>
    </div>
    """, unsafe_allow_html=True)

# ===========================================================================
# SIDEBAR  (no Active City widget — simplified)
# ===========================================================================



def render_sidebar_chrome():
    st.sidebar.markdown(f"""
    <div class="sidebar-brand-shell">
      <div class="sidebar-brand-title">
        {_t("app.brand")}
      </div>
      <div class="sidebar-brand-tag">
        {_t("app.tagline")}
      </div>
      <div class="sidebar-brand-pills">
        <span class="sidebar-pill-live">
          <span style="width:7px;height:7px;border-radius:50%;background:#34C759;display:inline-block"></span>
          {_t("common.live")}
        </span>
        <span class="sidebar-pill-views">
          {_t("common.views", count=f"{st.session_state.visit_count:,}")}
        </span>
      </div>
    </div>
    """, unsafe_allow_html=True)
    st.sidebar.markdown("---")

    if st.session_state.get("forecast_source") == "station" and st.session_state.get("selected_station_uid"):
        st.sidebar.markdown(f"""
        <div style="padding:.6rem;background:rgba(0,122,255,.12);border-radius:10px;margin:.4rem 0;font-size:.78rem;color:#93c5fd">
          {_t("sidebar.station")}: {(st.session_state.selected_station_name or 'Selected')[:28]}
        </div>
        """, unsafe_allow_html=True)


def page_about_extracted():
    render_about_page(
        st=st,
        render_hero=render_hero,
        render_page_guide=render_page_guide,
        render_section=render_section,
        tt=tt,
    )


def build_navigation():
    dashboard_page = st.Page(
        page_dashboard,
        title=_t("nav.dashboard"),
        icon=":material/dashboard:",
        url_path="dashboard",
        default=True,
    )
    stations_page = st.Page(
        page_stations_map,
        title=_t("nav.stations_map"),
        icon=":material/map:",
        url_path="stations-map",
    )
    forecast_page = st.Page(
        page_forecast,
        title=_t("nav.forecast"),
        icon=":material/monitoring:",
        url_path="forecast",
    )
    action_page = st.Page(
        page_action,
        title=_t("nav.take_action"),
        icon=":material/eco:",
        url_path="take-action",
    )
    analytics_page = st.Page(
        page_analytics,
        title=_t("nav.analytics"),
        icon=":material/analytics:",
        url_path="analytics",
    )
    reports_page = st.Page(
        page_reports,
        title=_t("nav.reports"),
        icon=":material/description:",
        url_path="reports",
    )
    about_page = st.Page(
        page_about_extracted,
        title=_t("nav.about"),
        icon=":material/info:",
        url_path="about",
    )

    return st.navigation(
        {
            "AirPulse Pages": [
                dashboard_page,
                stations_page,
                forecast_page,
                action_page,
                reports_page,
                analytics_page,
                about_page,
            ]
        },
        position="sidebar",
    )


def scroll_page_to_top() -> None:
    components.html(
        """
        <script>
        function forceScrollTop() {
          const candidates = [];

          try { candidates.push(window.parent); } catch (e) {}
          try { candidates.push(window); } catch (e) {}

          const selectors = [
            'section.main',
            '.main',
            '[data-testid="stAppViewContainer"]',
            '[data-testid="stAppViewContainer"] > .main',
            '.stApp',
            'body',
            'html'
          ];

          candidates.forEach((winObj) => {
            try {
              const doc = winObj.document;
              selectors.forEach((selector) => {
                const el = doc.querySelector(selector);
                if (el) {
                  try {
                    el.scrollTop = 0;
                    el.scrollTo?.({ top: 0, behavior: "instant" });
                    el.scrollTo?.({ top: 0, behavior: "auto" });
                  } catch (e) {}
                }
              });

              try { doc.documentElement.scrollTop = 0; } catch (e) {}
              try { doc.body.scrollTop = 0; } catch (e) {}
              try { winObj.scrollTo(0, 0); } catch (e) {}
            } catch (e) {}
          });
        }

        forceScrollTop();
        setTimeout(forceScrollTop, 50);
        setTimeout(forceScrollTop, 150);
        setTimeout(forceScrollTop, 300);
        setTimeout(forceScrollTop, 600);
        </script>
        """,
        height=0,
    )


def enable_tab_scroll_reset() -> None:
    components.html(
        """
        <script>
        function scrollTopAirPulse() {
          const targets = [];

          try { targets.push(window.parent); } catch (e) {}
          try { targets.push(window); } catch (e) {}

          const selectors = [
            'section.main',
            '.main',
            '[data-testid="stAppViewContainer"]',
            '[data-testid="stAppViewContainer"] > .main',
            '.stApp',
            'body',
            'html'
          ];

          targets.forEach((winObj) => {
            try {
              const doc = winObj.document;
              selectors.forEach((selector) => {
                const el = doc.querySelector(selector);
                if (el) {
                  try {
                    el.scrollTop = 0;
                    el.scrollTo?.({ top: 0, behavior: "auto" });
                  } catch (e) {}
                }
              });

              try { doc.documentElement.scrollTop = 0; } catch (e) {}
              try { doc.body.scrollTop = 0; } catch (e) {}
              try { winObj.scrollTo(0, 0); } catch (e) {}
            } catch (e) {}
          });
        }

        function bindTabs() {
          const docs = [];
          try { docs.push(window.parent.document); } catch (e) {}
          try { docs.push(document); } catch (e) {}

          docs.forEach((doc) => {
            try {
              doc.querySelectorAll('[data-baseweb="tab-list"] button[role="tab"]').forEach((tab) => {
                if (tab.dataset.airpulseBound === "1") return;
                tab.dataset.airpulseBound = "1";

                tab.addEventListener("click", () => {
                  setTimeout(scrollTopAirPulse, 20);
                  setTimeout(scrollTopAirPulse, 120);
                  setTimeout(scrollTopAirPulse, 260);
                });
              });
            } catch (e) {}
          });
        }

        bindTabs();

        const observer = new MutationObserver(() => {
          bindTabs();
        });

        observer.observe(document.body, { childList: true, subtree: true });
        </script>
        """,
        height=0,
    )

# ===========================================================================
# MAIN
# ===========================================================================
def main():
    configure_logging()
    init_session_state()
    initialize_i18n()
    inject_runtime_styles()
    enable_tab_scroll_reset()
    render_sidebar_chrome()
    page = build_navigation()

    selected_page_title = getattr(page, "title", None)
    page_changed = st.session_state.current_page != selected_page_title
    if page_changed:
        st.session_state.current_page = selected_page_title
        st.session_state.scroll_after_render = True

    page.run()

    if st.session_state.scroll_after_render:
        scroll_page_to_top()
        st.session_state.scroll_after_render = False

if __name__ == "__main__":
    main()
