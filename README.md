# AirPulse Global

AirPulse Global is a Streamlit-based air quality intelligence application for live city monitoring, station exploration, wind-aware analytics, pollutant forecasting, action guidance, and export-ready reporting.

The project combines WAQI / AQICN live air-quality feeds, optional Tomorrow.io wind data, cached historical inputs, lightweight forecasting helpers, and a single shared application layer for the deployed user experience.

## Highlights

- Live AQI, PM2.5, PM10, O3, NO2, SO2, and CO monitoring
- WAQI-powered global station map and nearby station exploration
- Wind-aware dashboard and analytics views
- Forecast page with WAQI native daily forecast when available
- Conservative fallback forecasting for incomplete upstream forecast coverage
- Analytics views for temporal patterns, wind dispersion, forecast validation, comparative analysis, and anomaly detection
- PDF and image export support for reporting workflows

## Current Forecasting Approach

The production app currently prioritizes stability and source transparency:

1. Load live city air-quality data from WAQI / AQICN
2. Use the official WAQI daily forecast feed when available
3. Fall back to a conservative Holt-Winters time-series forecast when upstream forecast data is missing
4. Surface forecast diagnostics clearly in the UI so users know whether they are seeing provider data or fallback estimates

This keeps the deployed app aligned with live provider data while preserving forecast continuity.

## Project Structure

```text
AirPulse_Global/
|-- app.py
|-- requirements.txt
|-- README.md
|-- DESIGN_SYSTEM.md
|-- stations.csv
|-- .streamlit/
|-- artifacts/
|-- data/
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
        |-- constants.py
        |-- forecasting.py
        |-- i18n.py
        |-- legacy_app.py
        |-- storage.py
        |-- utils.py
        |-- visitor.py
        |-- weather_integration.py
        `-- __init__.py
```

## Architecture Overview

The repository is organized into three practical layers:

1. Runtime application layer  
   The deployed Streamlit product that users interact with.
2. Supporting data and experimentation layer  
   Local data, artifacts, notebooks, and helper scripts that support analysis and content generation around the app.
3. Shared support layer  
   Configuration, exports, secrets, storage, translations, and shared utilities.

## Runtime Application Files

- [app.py](./app.py)  
  Streamlit entrypoint.

- [src/airpulse/legacy_app.py](./src/airpulse/legacy_app.py)  
  Main multi-page Streamlit application, page composition, dashboard logic, analytics rendering, station views, report exports, and forecast page wiring.

- [src/airpulse/forecasting.py](./src/airpulse/forecasting.py)  
  Forecast bundle generation, WAQI-native forecast handling, and conservative fallback forecasting.

- [src/airpulse/analytics_engine.py](./src/airpulse/analytics_engine.py)  
  Analytics computations for temporal patterns, wind dispersion, forecast validation, comparative analysis, and anomaly detection.

- [src/airpulse/action_engine.py](./src/airpulse/action_engine.py)  
  Action suggestions, sustainability prompts, and checklist scoring logic.

- [src/airpulse/i18n.py](./src/airpulse/i18n.py)  
  Translation and label helpers used by the UI.

- [src/airpulse/visitor.py](./src/airpulse/visitor.py)  
  Lightweight persistent visitor counter helpers.

- [src/airpulse/pages/about.py](./src/airpulse/pages/about.py)  
  Dedicated About page content.

- [src/airpulse/components/maps.py](./src/airpulse/components/maps.py)  
  Active map builders used by station and geographic views.

- [src/airpulse/services/secrets.py](./src/airpulse/services/secrets.py)  
  Secret loading helpers for Streamlit secrets, environment variables, and local fallback files.

- [src/airpulse/services/reporting.py](./src/airpulse/services/reporting.py)  
  PDF and image export helpers for reporting workflows.

## Shared Support Files

- [src/airpulse/storage.py](./src/airpulse/storage.py)  
  Loads and normalizes raw and processed station histories used by the app.

- [src/airpulse/weather_integration.py](./src/airpulse/weather_integration.py)  
  Weather and historical context integration used by forecast and analytics helpers.

- [src/airpulse/utils.py](./src/airpulse/utils.py)  
  Shared low-level helpers used across forecasting and analytics code.

## Package and Support Files

- [src/airpulse/config.py](./src/airpulse/config.py)  
  Central paths, cache TTLs, and forecasting defaults.

- [src/airpulse/constants.py](./src/airpulse/constants.py)  
  Shared project constants still used by reporting and some domain helpers.

- [src/airpulse/__init__.py](./src/airpulse/__init__.py)  
  Minimal package marker for the application modules.

## Cleanup Notes

During cleanup, these modules were removed because they were no longer used by the active application runtime:

- `src/airpulse/components/air_quality.py`
- `src/airpulse/components/ui.py`
- `src/airpulse/services/air_quality.py`
- `src/airpulse/dataset_builder.py`
- `src/airpulse/forecast_evaluation.py`
- `src/airpulse/station_expansion.py`
- `src/airpulse/station_experiment.py`
- `src/airpulse/station_selection.py`
- `src/airpulse/waqi_integration.py`

The current `src/airpulse` package is now focused on the deployed Streamlit app and its directly used helpers.

## Data and Artifacts

- [data/raw](./data/raw)  
  Local raw station history CSV files.

- [data/processed](./data/processed)  
  Cached and processed outputs used by the app and helper workflows.

- [artifacts](./artifacts)  
  Offline model metrics, forecast summaries, and related outputs.

- [notebooks](./notebooks)  
  Project notebooks kept as reference material and exploratory workspace.

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

Copy the template and create your local secrets file:

```powershell
Copy-Item .streamlit\secrets.toml.template .streamlit\secrets.toml
```

Then set your keys in `.streamlit/secrets.toml`:

```toml
WAQI_TOKEN = "your-waqi-token"
TOMORROW_IO_API_KEY = "your-tomorrow-key"
```

Optional local fallback files are also supported, but `st.secrets` is the preferred production path.

### 4. Run the app

```powershell
python -m streamlit run app.py
```

## Secrets and GitHub Safety

The repository is already configured so local secrets do not need to be committed:

- `.streamlit/secrets.toml` is ignored
- `api_token.txt` is ignored
- `wind_api.txt` is ignored
- log files and local environment files are ignored

Recommended deployment approach:

1. Keep real keys out of source control
2. Push only code and templates
3. Add production secrets in your deployment platform

For Streamlit Community Cloud, place these in the app Secrets panel:

```toml
WAQI_TOKEN = "your-waqi-token"
TOMORROW_IO_API_KEY = "your-tomorrow-key"
```

## Deployment Checklist

- `app.py` is the entrypoint
- `requirements.txt` is present
- `.streamlit/secrets.toml` is not committed
- `.streamlit/secrets.toml.template` is present for onboarding
- local token files remain ignored
- large processed outputs and local runtime files stay out of GitHub

## Notes

- The live app favors stability and source transparency over experimental model complexity.
- Notebooks remain in the repository as supporting material, but the active `src/airpulse` package is now trimmed to app-used modules.
- For production reliability, the app currently favors upstream WAQI forecast data over more experimental forecast paths.

## License / Usage

This repository depends on third-party live data providers. Before public deployment, verify that your intended use complies with the provider terms for WAQI / AQICN, Tomorrow.io, and any downstream export or redistribution workflow.
