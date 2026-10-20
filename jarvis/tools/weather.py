import requests

from jarvis.tools import tool

# Kingston, Ontario
LATITUDE, LONGITUDE = 44.23, -76.49

# WMO weather codes used by Open-Meteo, grouped coarsely
CODES = {0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "fog",
         51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle",
         57: "freezing drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
         66: "freezing rain", 67: "freezing rain", 71: "light snow", 73: "snow",
         75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
         82: "heavy showers", 85: "snow showers", 86: "heavy snow showers",
         95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail"}


@tool("Current weather and today's forecast for Kingston, Ontario (metric).")
def get_weather():
    r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=10, params={
        "latitude": LATITUDE, "longitude": LONGITUDE, "timezone": "America/Toronto",
        "forecast_days": 1,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
    })
    r.raise_for_status()
    cur, day = r.json()["current"], r.json()["daily"]
    return {
        "now": f"{CODES.get(cur['weather_code'], 'unknown')}, {cur['temperature_2m']}°C "
               f"(feels like {cur['apparent_temperature']}°C), wind {cur['wind_speed_10m']} km/h",
        "today": f"{CODES.get(day['weather_code'][0], 'unknown')}, high {day['temperature_2m_max'][0]}°C, "
                 f"low {day['temperature_2m_min'][0]}°C, "
                 f"{day['precipitation_probability_max'][0]}% chance of precipitation",
    }
