import requests
import json
from datetime import datetime, timezone

# City → coordinates
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

def get_weather_events():
    """Search for active daily temperature events."""
    url = "https://gamma-api.polymarket.com/public-search"
    params = {"q": "highest temperature", "limit": 15}
    
    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()
    data = response.json()
    
    events = []
    for event in data.get("events", []):
        title = event.get("title", "")
        if "highest temperature" in title.lower():
            events.append(event)
    return events

def get_forecast(city_name: str):
    """Get max temperature forecast from Open-Meteo."""
    city_key = city_name.lower().strip()
    
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
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        daily = data.get("daily", {})
        times = daily.get("time", [])
        temps = daily.get("temperature_2m_max", [])
        if times and temps:
            return list(zip(times, temps))
    except Exception:
        return None
    return None

def extract_city(title: str) -> str:
    title = title.lower()
    if " in " in title and " on " in title:
        return title.split(" in ")[1].split(" on ")[0].strip()
    return "unknown"

def parse_bucket(question: str):
    """Extract the temperature number from a bucket question."""
    q = question.lower()
    try:
        if "or below" in q or "or lower" in q:
            num = ''.join(c for c in q.split("be ")[1] if c.isdigit() or c == '.')
            return float(num), "below"
        if "or higher" in q or "or above" in q:
            num = ''.join(c for c in q.split("be ")[1] if c.isdigit() or c == '.')
            return float(num), "above"
        
        part = q.split("be ")[1].split(" on ")[0]
        digits = ''.join(c for c in part if c.isdigit() or c == '.' or c == '-')
        if "-" in digits:
            low, high = digits.split("-")
            return (float(low) + float(high)) / 2, "range"
        return float(digits), "exact"
    except Exception:
        return None, None

def main():
    print("Weather Scanner + Simple Edge")
    print("Time (UTC):", datetime.now(timezone.utc).isoformat())
    print("=" * 60)

    try:
        events = get_weather_events()
        print(f"Found {len(events)} temperature events\n")

        for event in events:
            title = event.get("title", "")
            city = extract_city(title)
            print(f"• {title}")
            print(f"  City: {city}")

            forecast = get_forecast(city)
            if not forecast:
                print("  Forecast: not available\n")
                continue

            today_forecast = forecast[0][1] if forecast else None
            print(f"  Forecast max temp: {today_forecast}°C")

            print("  Buckets:")
            best_edge = None
            
            for market in event.get("markets", []):
                question = market.get("question", "")
                prices_raw = market.get("outcomePrices")
                
                if not prices_raw:
                    continue
                    
                try:
                    prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
                    yes_price = float(prices[0])
                except Exception:
                    continue

                bucket_temp, bucket_type = parse_bucket(question)
                if bucket_temp is None:
                    continue

                diff = abs(today_forecast - bucket_temp)
                
                if 0.02 < yes_price < 0.95:
                    edge_score = (0.5 - abs(0.5 - yes_price)) - (diff * 0.05)
                    
                    print(f"    {bucket_temp}°C ({bucket_type}) | YES: {yes_price:.2f} | diff: {diff:.1f}°C")
                    
                    if best_edge is None or edge_score > best_edge[0]:
                        best_edge = (edge_score, bucket_temp, yes_price, diff)

            if best_edge:
                score, temp, price, diff = best_edge
                print(f"  → Best looking bucket: {temp}°C at {price:.2f} (diff {diff:.1f}°C)")
            else:
                print("  → No clear open buckets (many may already be resolved)")

            print()

    except Exception as e:
        print("Error:", str(e))

    print("=" * 60)
    print("Done.")

if __name__ == "__main__":
    main()
