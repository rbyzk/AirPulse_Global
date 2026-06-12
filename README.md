# AirPulse Global

![AirPulse Global](https://raw.githubusercontent.com/rbyzk/AirPulse_Global/d01ac8cc6eb974d9a0f021c10331f8d0dbcfd0e6/airpulse.png)

AirPulse Global is an interactive Streamlit application for live air-quality intelligence, station exploration, pollutant forecasting, wind-aware analytics, and clean-air action guidance.

The project combines WAQI / AQICN live air-quality feeds, optional Tomorrow.io wind context, local station-history datasets, forecasting helpers, analytics views, and export-ready reporting tools in one deployable dashboard.

## Live Demo

Streamlit app: [Open AirPulse Global](https://airpulseglobal.streamlit.app/)
Kaggle notebook: [AirPulse: Air Quality Risk Intelligence](https://www.kaggle.com/code/beyzakucuk/airpulse-air-quality-risk-intelligence)

## Repository Description

Interactive air-quality intelligence app with live WAQI data, station maps, wind context, forecasting, and clean-air action guidance.

## Highlights

* Live AQI, PM2.5, PM10, O₃, NO₂, SO₂, and CO monitoring
* WAQI-powered global station map and nearby station exploration
* Wind-aware dashboard views with optional Tomorrow.io integration
* Forecast page using WAQI native daily forecasts when available
* Conservative fallback forecasting when upstream forecast data is incomplete
* Analytics for temporal patterns, pollutant behavior, wind dispersion, forecast validation, and anomaly detection
* Action guidance that translates air-quality signals into clean-air recommendations
* PDF and image export support for reporting workflows
* Turkish and English interface resources
* Companion Kaggle notebook for synthetic-data analysis and product storytelling

## What This Project Does

AirPulse Global helps users move from raw air-quality readings to decision-ready environmental insight.

The application is designed around a practical workflow:

1. Search or select a city
2. Review live AQI and pollutant conditions
3. Explore nearby monitoring stations
4. Add wind and weather context
5. Review forecast signals and uncertainty
6. Compare cities, pollutants, and station behavior
7. Generate action-oriented recommendations
8. Export report-ready outputs

The project is intended for portfolio demonstration, environmental analytics, data-product design, and air-quality decision-support experimentation. It should not be used as official public-health, regulatory, or emergency guidance.

## Companion Kaggle Notebook

A companion Kaggle notebook presents the analytical story behind AirPulse Global using a reproducible synthetic air-quality dataset.

Notebook: [AirPulse: Air Quality Risk Intelligence](https://www.kaggle.com/code/beyzakucuk/airpulse-air-quality-risk-intelligence)

The notebook covers:

* Synthetic global air-quality data intake
* Data governance and quality checks
* City and station risk intelligence
* Pollutant behavior analysis
* Weather-aware exposure context
* Anomaly detection
* Next-day PM2.5 forecasting
* Action recommendation segmentation
* Dashboard-aligned analytical outputs

## Tech Stack

* Python
* Streamlit
* pandas
* NumPy
* Plotly
* Matplotlib
* scikit-learn
* Prophet / cmdstanpy
* Folium and streamlit-folium
* ReportLab
* PyArrow
* WAQI / AQICN API
* Tomorrow.io API, optional

## Project Structure

```text
AirPulse_Global/
|-- app.py
|-- requirements.txt
|-- README.md
|-- DESIGN_SYSTEM.md
|-- stations.csv
|-- .streamlit/
|   |-- config.toml
|   `-- secrets.toml.template
|-- artifacts/
|-- data/
|   |-- raw/
|   `-- external/
|-- locales/
|-- notebooks/
|-- scripts/
`-- src/
    `-- airpulse/
        |-- components/
        |-- pages/
        |-- services/
        |-- action_engine.py
        |-- analytics_engine.py
        |-- config.py
        |-- forecasting.py
        |-- i18n.py
        |-- legacy_app.py
        |-- storage.py
        |-- utils.py
        |-- visitor.py
        `-- weather_integration.py
```

## Core Application Files

* `app.py`
  Streamlit entrypoint for the deployed application.

* `src/airpulse/legacy_app.py`
  Main multi-page Streamlit interface, dashboard composition, station views, analytics rendering, reporting actions, and forecast page wiring.

* `src/airpulse/forecasting.py`
  Forecast bundle generation, WAQI-native forecast handling, conservative fallback forecasting, and forecast diagnostics.

* `src/airpulse/analytics_engine.py`
  Analytics computations for temporal trends, pollutant behavior, wind dispersion, comparative analysis, and anomaly detection.

* `src/airpulse/action_engine.py`
  Clean-air action suggestions, sustainability prompts, checklist scoring, and recommendation logic.

* `src/airpulse/services/secrets.py`
  Secret-loading helpers for Streamlit secrets, environment variables, and local fallback files.

* `src/airpulse/services/reporting.py`
  PDF and image export helpers for reporting workflows.

## Data Sources

The application can use:

* WAQI / AQICN live air-quality data
* WAQI station search and map tiles
* WAQI native forecast feeds when available
* Optional Tomorrow.io wind data
* Local historical station CSV files
* Cached external station snapshots for development and demonstration

Some data in this repository is included for local development and portfolio demonstration. Live deployment requires valid API credentials for the full experience.

## Forecasting Approach

The production app prioritizes stability and source transparency:

1. Load live city air-quality data from WAQI / AQICN
2. Use the official WAQI daily forecast feed when available
3. Fall back to a conservative time-series forecast when upstream forecast data is missing
4. Clearly label whether users are seeing provider data or fallback estimates

This approach keeps the deployed app understandable while preserving continuity when external forecast coverage is incomplete.

## Local Setup

### 1. Create and activate a virtual environment

```powershell
python -m venv .venv
.venv\Scripts\activate
```

### 2. Install dependencies

```powershell
pip install -r requirements.txt
```

### 3. Configure secrets

Copy the template:

```powershell
Copy-Item .streamlit\secrets.toml.template .streamlit\secrets.toml
```

Then add your local credentials:

```toml
WAQI_TOKEN = "your-waqi-token"
TOMORROW_IO_API_KEY = "your-tomorrow-io-api-key"
# Optional alias if your wind provider key is stored separately:
# WIND_API_KEY = "your-wind-api-key"
DEBUG = false

[app]
default_city = "Istanbul, TR"
```

`TOMORROW_IO_API_KEY` is optional. If your wind provider key is stored separately, use `WIND_API_KEY` instead. Without a wind key, wind-specific features use fallback behavior where possible.

### 4. Run the app

```powershell
python -m streamlit run app.py
```

## Streamlit Community Cloud Deployment

Use the following settings when deploying from GitHub:

* Repository: `AirPulse_Global`
* Branch: `main`
* Main file path: `app.py`
* Python dependencies: `requirements.txt`

Add the following values in the Streamlit app Secrets panel:

```toml
WAQI_TOKEN = "your-waqi-token"
TOMORROW_IO_API_KEY = "your-tomorrow-io-api-key"
# Optional alias:
# WIND_API_KEY = "your-wind-api-key"
DEBUG = false
```

Do not commit `.streamlit/secrets.toml` to GitHub.

## GitHub Safety

The repository is configured to keep local secrets and runtime files out of version control:

* `.streamlit/secrets.toml` is ignored
* `api_token.txt` is ignored
* `wind_api.txt` is ignored
* `.env` files are ignored
* virtual environments are ignored
* Python cache files are ignored
* local processed cache files are ignored
* large model artifacts such as `.pkl` files are ignored

Only `.streamlit/secrets.toml.template` is included as the safe onboarding template.

## Suggested GitHub Topics

```text
streamlit
air-quality
environmental-intelligence
data-science
forecasting
waqi
aqicn
plotly
python
dashboard
```

## Deployment Checklist

* `app.py` is present
* `requirements.txt` is present
* `.streamlit/config.toml` is present
* `.streamlit/secrets.toml` is not committed
* API keys are added only through Streamlit secrets
* local token files remain ignored
* the app runs locally with `streamlit run app.py`
* the GitHub repository is public or accessible to Streamlit Community Cloud
* the Streamlit app link is updated in this README
* the Kaggle notebook link is updated in this README

## Disclaimer

AirPulse Global depends on third-party live data providers. Before public or production use, verify that your intended use complies with the terms of WAQI / AQICN, Tomorrow.io, and any other upstream data provider.

The application is a data-product and analytics demonstration. It is not a substitute for official air-quality alerts, public-health guidance, regulatory reporting, or emergency decision-making.
