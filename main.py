import requests
from datetime import datetime

def get_weather_markets():
    url = "https://gamma-api.polymarket.com/markets"
    params = {
        "tag": "weather",
        "active": "true",
        "closed": "false",
        "limit": 30
    }
    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    return response.json()

def main():
    print("Weather Scanner started...")
    print("Time (UTC):", datetime.utcnow().isoformat())
    print("-" * 50)

    try:
        markets = get_weather_markets()
        print(f"Found {len(markets)} active weather markets:\n")

        for m in markets:
            question = m.get("question", "No question")
            print("•", question)

    except Exception as e:
        print("Error:", str(e))

    print("-" * 50)
    print("Scanner finished.")

if __name__ == "__main__":
    main()
