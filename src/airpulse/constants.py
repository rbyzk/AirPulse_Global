"""Shared UI and domain constants for the Streamlit app."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
ACTION_TRACKER_FILE = PROJECT_ROOT / "data" / "processed" / "action_tracker.json"
VISITOR_COUNT_FILE = PROJECT_ROOT / "visitor_count.txt"

AQI_LEVELS = [
    {"key": "good", "min": 0, "max": 50, "color": "#34C759", "bg": "#e8f5e9", "text": "#1B5E20", "icon": "😊"},
    {"key": "moderate", "min": 51, "max": 100, "color": "#FFCC00", "bg": "#fff8e1", "text": "#F57F17", "icon": "😐"},
    {"key": "sensitive", "min": 101, "max": 150, "color": "#FF9500", "bg": "#fff3e0", "text": "#E65100", "icon": "😷"},
    {"key": "unhealthy", "min": 151, "max": 200, "color": "#FF3B30", "bg": "#ffebee", "text": "#B71C1C", "icon": "🤢"},
    {"key": "very_unhealthy", "min": 201, "max": 300, "color": "#AF52DE", "bg": "#f3e5f5", "text": "#4A148C", "icon": "😵"},
    {"key": "hazardous", "min": 301, "max": 9999, "color": "#8E2B26", "bg": "#efebe9", "text": "#3E2723", "icon": "☠️"},
]

POLLUTANT_INFO = {
    "pm25": {"full": "PM2.5", "unit": "µg/m³", "who": 15},
    "pm10": {"full": "PM10", "unit": "µg/m³", "who": 45},
    "o3": {"full": "O₃", "unit": "µg/m³", "who": 100},
    "no2": {"full": "NO₂", "unit": "µg/m³", "who": 25},
    "so2": {"full": "SO₂", "unit": "µg/m³", "who": 40},
    "co": {"full": "CO", "unit": "mg/m³", "who": 4},
}

GLOBAL_CITIES = {
    "Istanbul, TR": {"lat": 41.0082, "lon": 28.9784, "region": "Europe"},
    "London, UK": {"lat": 51.5074, "lon": -0.1278, "region": "Europe"},
    "Paris, FR": {"lat": 48.8566, "lon": 2.3522, "region": "Europe"},
    "Berlin, DE": {"lat": 52.520, "lon": 13.405, "region": "Europe"},
    "Madrid, ES": {"lat": 40.4168, "lon": -3.7038, "region": "Europe"},
    "Rome, IT": {"lat": 41.9028, "lon": 12.4964, "region": "Europe"},
    "Warsaw, PL": {"lat": 52.229, "lon": 21.012, "region": "Europe"},
    "Amsterdam, NL": {"lat": 52.374, "lon": 4.898, "region": "Europe"},
    "New York, US": {"lat": 40.7128, "lon": -74.006, "region": "N. America"},
    "Los Angeles, US": {"lat": 34.052, "lon": -118.24, "region": "N. America"},
    "Chicago, US": {"lat": 41.878, "lon": -87.63, "region": "N. America"},
    "Toronto, CA": {"lat": 43.651, "lon": -79.347, "region": "N. America"},
    "São Paulo, BR": {"lat": -23.55, "lon": -46.63, "region": "S. America"},
    "Buenos Aires, AR": {"lat": -34.603, "lon": -58.381, "region": "S. America"},
    "Mexico City, MX": {"lat": 19.43, "lon": -99.13, "region": "N. America"},
    "Bogotá, CO": {"lat": 4.711, "lon": -74.072, "region": "S. America"},
    "Tokyo, JP": {"lat": 35.676, "lon": 139.65, "region": "Asia"},
    "Seoul, KR": {"lat": 37.566, "lon": 126.978, "region": "Asia"},
    "Beijing, CN": {"lat": 39.904, "lon": 116.407, "region": "Asia"},
    "Shanghai, CN": {"lat": 31.23, "lon": 121.47, "region": "Asia"},
    "Delhi, IN": {"lat": 28.614, "lon": 77.209, "region": "Asia"},
    "Mumbai, IN": {"lat": 19.076, "lon": 72.878, "region": "Asia"},
    "Bangkok, TH": {"lat": 13.756, "lon": 100.502, "region": "Asia"},
    "Singapore, SG": {"lat": 1.352, "lon": 103.82, "region": "Asia"},
    "Jakarta, ID": {"lat": -6.21, "lon": 106.85, "region": "Asia"},
    "Dhaka, BD": {"lat": 23.81, "lon": 90.41, "region": "Asia"},
    "Karachi, PK": {"lat": 24.86, "lon": 67.01, "region": "Asia"},
    "Cairo, EG": {"lat": 30.044, "lon": 31.236, "region": "Africa"},
    "Lagos, NG": {"lat": 6.455, "lon": 3.384, "region": "Africa"},
    "Nairobi, KE": {"lat": -1.292, "lon": 36.822, "region": "Africa"},
    "Accra, GH": {"lat": 5.603, "lon": -0.187, "region": "Africa"},
    "Casablanca, MA": {"lat": 33.573, "lon": -7.589, "region": "Africa"},
    "Johannesburg, ZA": {"lat": -26.205, "lon": 28.04, "region": "Africa"},
    "Addis Ababa, ET": {"lat": 9.025, "lon": 38.747, "region": "Africa"},
    "Sydney, AU": {"lat": -33.87, "lon": 151.21, "region": "Oceania"},
    "Melbourne, AU": {"lat": -37.81, "lon": 144.96, "region": "Oceania"},
    "Dubai, AE": {"lat": 25.20, "lon": 55.27, "region": "Middle East"},
    "Riyadh, SA": {"lat": 24.688, "lon": 46.722, "region": "Middle East"},
    "Tehran, IR": {"lat": 35.694, "lon": 51.421, "region": "Middle East"},
}

WIND_DIRECTIONS = {
    (0, 22): "N",
    (23, 67): "NE",
    (68, 112): "E",
    (113, 157): "SE",
    (158, 202): "S",
    (203, 247): "SW",
    (248, 292): "W",
    (293, 337): "NW",
    (338, 360): "N",
}

COMMUTE_FACTORS = {
    "Car": 0.120,
    "Bus": 0.050,
    "Metro/Subway": 0.020,
    "Bicycle": 0.0,
    "Walking": 0.0,
    "Remote / No commute": 0.0,
}

MEAT_FACTORS = {
    "Every day": 0.30,
    "3 times/week": 0.20,
    "1 time/week": 0.10,
    "Vegetarian": 0.05,
    "Vegan": 0.02,
}

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

