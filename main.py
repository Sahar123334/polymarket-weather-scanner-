import requests
from datetime import datetime, timezone

def get_weather_markets():
    """Search for active daily temperature markets."""
    url = "https://gamma-api.polymarket.com/public-search"
    params = {
        "q": "highest temperature",
        "limit": 30
    }
    
    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()
    data = response.json()
    
    events = data.get("events", [])
    
    markets = []
    for event in events:
        title = event.get("title", "")
        # Only keep daily temperature events
        if "highest temperature" in title.lower() or "high temperature" in title.lower():
            markets.append({
                "title": title,
                "slug": event.get("slug"),
                "markets_count": len(event.get("markets", []))
            })
    
    return markets

def main():
    print("Weather Scanner started...")
    print("Time (UTC):", datetime.now(timezone.utc).isoformat())
    print("-" * 60)

    try:
        markets = get_weather_markets()
        print(f"Found {len(markets)} temperature markets:\n")

        for m in markets:
            print(f"• {m['title']}  ({m['markets_count']} buckets)")

    except Exception as e:
        print("Error:", str(e))

    print("-" * 60)
    print("Scanner finished.")

if __name__ == "__main__":
    main()
