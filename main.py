import requests
from datetime import datetime, timezone

# Simple city → coordinates mapping (latitude, longitude)
CITY_COORDS = {
    "warsaw": (52.23, 21.01),
    "hong kong": (22.32, 114.17),
    "seoul": (37.57, 126.98),
    "incheon": (37.46, 126.44),
    "shanghai": (31.23, 121.47),
    "miami": (25.76, -80.19),
    "nyc": (40.71, -74.01),
    "new york": (40.71, -74.01),
    "london": (51.51, -0.13),
    "chicago": (41.88, -87.63),
    "tokyo": (35.68, 139.76),
    "beijing": (39.90, 116.41),
}

def get_weather_markets():
    """Search for active daily temperature markets."""
    url = "https://gamma-api.polymarket.com/public-search"
    params = {
        "q": "highest temperature",
        "limit": 20
    }
    
    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()
    data = response.json()
    
    events = data.get("events", [])
    
    markets = []
    for event in events:
        title = event.get("title", "")
        if "highest temperature" in title.lower():
            markets.append({
                "title": title,
                "slug": event.get("slug"),
                "markets_count": len(event.get("markets", []))
            })
    
    return markets

def get_forecast(city_name: str):
    """Get max temperature forecast from Open-Meteo (free, no key needed)."""
    city_key = city_name.lower().strip()
    
    # Try to find matching coordinates
    coords = None
    for key, value in CITY_COORDS.items():
        if key in city_key:
            coords = value
            break
    
    if not coords:
        return None
    
    lat, lon = coords
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max",
        "timezone": "auto",
        "forecast_days": 3
    }
    
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        daily = data.get("daily", {})
        times = daily.get("time", [])
        temps = daily.get("temperature_2m_max", [])
        
        if times and temps:
            return list(zip(times, temps))
    except Exception:
        return None
    
    return None

def extract_city(title: str) -> str:
    """Very simple city extraction from title."""
    title = title.lower()
    # Example: "Highest temperature in Warsaw on September 10?"
    if " in " in title and " on " in title:
        city_part = title.split(" in ")[1].split(" on ")[0]
        return city_part.strip()
    return "unknown"

def main():
    print("Weather Scanner + Forecast started...")
    print("Time (UTC):", datetime.now(timezone.utc).isoformat())
    print("-" * 60)

    try:
        markets = get_weather_markets()
        print(f"Found {len(markets)} temperature markets:\n")

        for m in markets:
            title = m["title"]
            city = extract_city(title)
            print(f"• {title}")
            print(f"  City detected: {city}")

            forecast = get_forecast(city)
            if forecast:
                print("  Forecast (max temp):")
                for date, temp in forecast:
                    print(f"    {date}: {temp}°C")
            else:
                print("  Forecast: not available for this city yet")
            
            print()

    except Exception as e:
        print("Error:", str(e))

    print("-" * 60)
    print("Scanner finished.")

if __name__ == "__main__":
    main()
