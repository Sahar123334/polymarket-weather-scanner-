import requests
from datetime import datetime, timezone

def get_weather_markets():
    """Fetch active markets and keep only temperature / weather ones."""
    url = "https://gamma-api.polymarket.com/markets"
    params = {
        "active": "true",
        "closed": "false",
        "limit": 100,
        "order": "volume24hr",
        "ascending": "false"
    }
    
    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()
    all_markets = response.json()
    
    # Keep only markets that look like daily temperature markets
    weather_markets = []
    keywords = ["highest temperature", "high temperature", "lowest temperature", "temperature in"]
    
    for m in all_markets:
        question = (m.get("question") or "").lower()
        if any(kw in question for kw in keywords):
            weather_markets.append(m)
    
    return weather_markets

def main():
    print("Weather Scanner started...")
    print("Time (UTC):", datetime.now(timezone.utc).isoformat())
    print("-" * 60)

    try:
        markets = get_weather_markets()
        print(f"Found {len(markets)} temperature markets:\n")

        for m in markets:
            question = m.get("question", "No question")
            print("•", question)

    except Exception as e:
        print("Error:", str(e))

    print("-" * 60)
    print("Scanner finished.")

if __name__ == "__main__":
    main()
