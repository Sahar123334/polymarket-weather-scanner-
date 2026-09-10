import csv
import json
import math
import os
import re
from datetime import datetime, timezone, timedelta

import requests

CSV_PATH = os.environ.get("PAPER_CSV", "paper_trades.csv")
MIN_EDGE = float(os.environ.get("MIN_EDGE", "0.08"))  # 8%
PAPER_SIZE = float(os.environ.get("PAPER_SIZE", "10"))  # fake $ per signal
MIN_PRICE = 0.05
MAX_PRICE = 0.90

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
    "moscow": (55.76, 37.62),
    "munich": (48.14, 11.58),
    "wellington": (-41.29, 174.78),
    "toronto": (43.65, -79.38),
    "paris": (48.86, 2.35),
    "chengdu": (30.66, 104.06),
    "wuhan": (30.59, 114.31),
    "atlanta": (33.64, -84.43),
    "seattle": (47.45, -122.31),
    "singapore": (1.36, 103.99),
}


def log(*args):
    print(*args, flush=True)


def normal_cdf(x, mu, sigma):
    if sigma <= 0:
        return 1.0 if x >= mu else 0.0
    z = (x - mu) / (sigma * math.sqrt(2.0))
    return 0.5 * (1.0 + math.erf(z))


def bucket_probability(kind, value, mu, sigma):
    """Probability that daily max lands in this bucket."""
    if kind == "exact":
        return max(0.0, normal_cdf(value + 0.5, mu, sigma) - normal_cdf(value - 0.5, mu, sigma))
    if kind == "range":
        low, high = value
        return max(0.0, normal_cdf(high + 0.5, mu, sigma) - normal_cdf(low - 0.5, mu, sigma))
    if kind == "below":
        return max(0.0, normal_cdf(value + 0.5, mu, sigma))
    if kind == "above":
        return max(0.0, 1.0 - normal_cdf(value - 0.5, mu, sigma))
    return 0.0


def sigma_for_lead(lead_days):
    return 1.4 + 0.35 * max(0, lead_days)


def get_upcoming_dates(days=3):
    today = datetime.now(timezone.utc).date()
    return [today + timedelta(days=i) for i in range(0, days + 1)]


def date_search_strings(d):
    return [
        d.strftime("%B %-d"),
        d.strftime("%B %d").replace(" 0", " "),
    ]


def get_weather_events():
    events = []
    seen = set()
    for d in get_upcoming_dates(3):
        for date_str in date_search_strings(d):
            url = "https://gamma-api.polymarket.com/public-search"
            params = {"q": f"highest temperature {date_str}", "limit": 15}
            try:
                r = requests.get(url, params=params, timeout=15)
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                log(f"Search error for {date_str}: {e}")
                continue

            for event in data.get("events", []):
                title = event.get("title") or ""
                slug = event.get("slug") or ""
                if "highest temperature" not in title.lower():
                    continue
                if slug in seen:
                    continue
                if event.get("closed") is True:
                    continue
                events.append(event)
                seen.add(slug)
    return events


def get_forecast(city_name):
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
        "forecast_days": 7,
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        daily = data.get("daily", {})
        times = daily.get("time", [])
        temps = daily.get("temperature_2m_max", [])
        return {t: float(temp) for t, temp in zip(times, temps)}
    except Exception:
        return None


def extract_city(title):
    title_l = title.lower()
    if " in " in title_l and " on " in title_l:
        return title_l.split(" in ", 1)[1].split(" on ", 1)[0].strip()
    return "unknown"


def extract_market_date(title):
    m = re.search(r"on\s+([A-Za-z]+)\s+(\d{1,2})(?:,\s*(\d{4}))?", title, re.I)
    if not m:
        return None
    month_name, day, year = m.group(1), int(m.group(2)), m.group(3)
    year = int(year) if year else datetime.now(timezone.utc).year
    try:
        return datetime.strptime(f"{month_name} {day} {year}", "%B %d %Y").date()
    except ValueError:
        try:
            return datetime.strptime(f"{month_name} {day} {year}", "%b %d %Y").date()
        except ValueError:
            return None


def parse_bucket(question):
    q = question.lower()
    m = re.search(r"be\s+(-?\d+(?:\.\d+)?)\s*°?\s*[cf]\s+or\s+(below|lower|higher|above)", q)
    if m:
        val = float(m.group(1))
        kind = "below" if m.group(2) in ("below", "lower") else "above"
        return val, kind

    m = re.search(r"between\s+(-?\d+)\s*[-–]\s*(-?\d+)\s*°?\s*[cf]", q)
    if m:
        return (float(m.group(1)), float(m.group(2))), "range"

    m = re.search(r"be\s+(-?\d+(?:\.\d+)?)\s*°?\s*[cf]\b", q)
    if m:
        return float(m.group(1)), "exact"

    return None, None


def parse_yes_price(market):
    raw = market.get("outcomePrices")
    if not raw:
        return None
    try:
        prices = json.loads(raw) if isinstance(raw, str) else raw
        return float(prices[0])
    except Exception:
        return None


def ensure_csv(path):
    new_file = not os.path.exists(path)
    f = open(path, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(
        f,
        fieldnames=[
            "timestamp_utc",
            "city",
            "event_title",
            "bucket",
            "kind",
            "side",
            "model_prob",
            "market_price",
            "edge",
            "forecast_c",
            "sigma",
            "paper_size",
            "status",
        ],
    )
    if new_file:
        writer.writeheader()
    return f, writer


def main():
    now = datetime.now(timezone.utc)
    log("Weather Scanner + Gaussian Edge + Paper Log")
    log("Time (UTC):", now.isoformat())
    log(f"Rules: min edge {MIN_EDGE:.0%}, paper size ${PAPER_SIZE:.0f}, price band {MIN_PRICE:.2f}-{MAX_PRICE:.2f}")
    log("=" * 64)

    csv_file, writer = ensure_csv(CSV_PATH)
    signals = []

    try:
        events = get_weather_events()
        log(f"Found {len(events)} open temperature events\n")

        for event in events:
            title = event.get("title") or ""
            city = extract_city(title)
            market_date = extract_market_date(title)
            log(f"• {title}")
            log(f"  City: {city} | Date: {market_date}")

            forecast_map = get_forecast(city)
            if not forecast_map:
                log("  Forecast: not available\n")
                continue

            date_key = market_date.isoformat() if market_date else None
            if date_key and date_key in forecast_map:
                mu = forecast_map[date_key]
            else:
                first_key = sorted(forecast_map.keys())[0]
                mu = forecast_map[first_key]
                date_key = first_key

            lead = 0
            if market_date:
                lead = max(0, (market_date - now.date()).days)
            sigma = sigma_for_lead(lead)
            log(f"  Model: mu={mu:.1f}C  sigma={sigma:.2f}  lead={lead}d")

            best = None
            open_count = 0

            for market in event.get("markets", []):
                question = market.get("question") or ""
                yes_price = parse_yes_price(market)
                if yes_price is None:
                    continue
                if yes_price <= MIN_PRICE or yes_price >= MAX_PRICE:
                    continue

                parsed, kind = parse_bucket(question)
                if parsed is None:
                    continue

                model_p = bucket_probability(kind, parsed, mu, sigma)
                edge = model_p - yes_price
                open_count += 1

                label = parsed if kind != "range" else f"{parsed[0]}-{parsed[1]}"
                log(
                    f"    {label} {kind:5} | YES {yes_price:.2f} | model {model_p:.2f} | edge {edge:+.2f}"
                )

                if best is None or edge > best["edge"]:
                    best = {
                        "label": label,
                        "kind": kind,
                        "yes_price": yes_price,
                        "model_p": model_p,
                        "edge": edge,
                    }

                if edge >= MIN_EDGE:
                    signal = {
                        "timestamp_utc": now.isoformat(),
                        "city": city,
                        "event_title": title,
                        "bucket": str(label),
                        "kind": kind,
                        "side": "YES",
                        "model_prob": round(model_p, 4),
                        "market_price": round(yes_price, 4),
                        "edge": round(edge, 4),
                        "forecast_c": round(mu, 2),
                        "sigma": round(sigma, 2),
                        "paper_size": PAPER_SIZE,
                        "status": "PAPER",
                    }
                    writer.writerow(signal)
                    signals.append(signal)

            if open_count == 0:
                log("    (no open buckets in price band)")
            elif best:
                log(
                    f"  Best bucket: {best['label']} {best['kind']} | "
                    f"YES {best['yes_price']:.2f} | model {best['model_p']:.2f} | edge {best['edge']:+.2f}"
                )
            log("")

        log("=" * 64)
        log(f"Paper signals this run: {len(signals)}")
        for s in signals:
            log(
                f"  PAPER BUY YES {s['city']} {s['bucket']} @ {s['market_price']:.2f} "
                f"(model {s['model_prob']:.2f}, edge {s['edge']:+.2f}) size ${s['paper_size']:.0f}"
            )
        log(f"CSV written to {CSV_PATH}")
        log("Done.")
    finally:
        csv_file.close()


if __name__ == "__main__":
    main()
