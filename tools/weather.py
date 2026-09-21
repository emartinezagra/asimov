"""Open-Meteo (https://open-meteo.com) — free, no API key needed. Two calls:
geocode the place name to coordinates, then fetch the forecast for that day.
"""
import logging
import requests

logger = logging.getLogger("asimov")

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

WEATHER_CODES = {
    0: "despejado", 1: "mayormente despejado", 2: "parcialmente nublado", 3: "nublado",
    45: "niebla", 48: "niebla escarchada",
    51: "llovizna ligera", 53: "llovizna", 55: "llovizna intensa",
    56: "llovizna helada", 57: "llovizna helada intensa",
    61: "lluvia ligera", 63: "lluvia", 65: "lluvia intensa",
    66: "lluvia helada", 67: "lluvia helada intensa",
    71: "nieve ligera", 73: "nieve", 75: "nieve intensa", 77: "granos de nieve",
    80: "chubascos ligeros", 81: "chubascos", 82: "chubascos intensos",
    85: "chubascos de nieve ligeros", 86: "chubascos de nieve intensos",
    95: "tormenta", 96: "tormenta con granizo ligero", 99: "tormenta con granizo intenso",
}


def geocode(location):
    try:
        resp = requests.get(GEOCODE_URL, params={
            "name": location, "count": 1, "language": "es", "format": "json"
        }, timeout=10)
        resp.raise_for_status()
        results = resp.json().get("results")
    except Exception:
        logger.error(f"Open-Meteo geocoding failed for '{location}'", exc_info=True)
        return None
    if not results:
        return None
    r = results[0]
    return {"lat": r["latitude"], "lon": r["longitude"], "name": r["name"]}


def get_weather(location, date_str):
    geo = geocode(location)
    if not geo:
        return {"status": "location_not_found", "location": location}

    try:
        resp = requests.get(FORECAST_URL, params={
            "latitude": geo["lat"], "longitude": geo["lon"],
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode",
            "timezone": "auto",
            "start_date": date_str, "end_date": date_str,
        }, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.error(f"Open-Meteo forecast failed for {geo}", exc_info=True)
        return {"status": "service_error"}

    daily = data.get("daily", {})
    if not daily.get("time"):
        return {"status": "no_data"}

    return {
        "status": "ok",
        "location": geo["name"],
        "date": date_str,
        "temp_min": daily["temperature_2m_min"][0],
        "temp_max": daily["temperature_2m_max"][0],
        "rain_probability": daily["precipitation_probability_max"][0],
        "condition": WEATHER_CODES.get(daily["weathercode"][0], "condición desconocida"),
    }
