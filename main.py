import csv
import json
import math
import os
import re
import smtplib
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText

import requests

LEDGER_PATH = os.environ.get("PAPER_CSV", "paper_ledger.csv")
MIN_EDGE = float(os.environ.get("MIN_EDGE", "0.08"))
PAPER_SIZE = float(os.environ.get("PAPER_SIZE", "10"))
MIN_PRICE = 0.05
MAX_PRICE = 0.90

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
MAIL_FROM = os.environ.get("MAIL_FROM", SMTP_USER)
MAIL_TO = os.environ.get("MAIL_TO", "")

LEDGER_FIELDS = [
    "id",
    "opened_utc",
    "settled_utc",
    "city",
    "event_title",
    "market_date",
    "bucket",
    "kind",
    "side",
    "model_prob",
    "market_price",
    "edge",
    "forecast_c",
    "sigma",
    "paper_size",
    "actual_c",
    "result",
    "pnl",
    "status",
]

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


def bucket_hit(kind, value, actual):
    if actual is None:
        return None
    if kind == "exact":
        return abs(actual - float(value)) <= 0.5
    if kind == "range":
        low, high = value if isinstance(value, (list, tuple)) else (None, None)
        if low is None:
            return None
        return (low - 0.5) <= actual <= (high + 0.5)
    if kind == "below":
        return actual <= float(value) + 0.5
    if kind == "above":
        return actual >= float(value) - 0.5
    return None


def sigma_for_lead(lead_days):
    return 1.4 + 0.35 * max(0, lead_days)


def yes_pnl(price, size, won):
    if price <= 0:
        return 0.0
    shares = size / price
    return round(shares * 1.0 - size, 2) if won else round(-size, 2)


def city_coords(city_name):
    city_key = (city_name or "").lower().strip()
    for key, value in CITY_COORDS.items():
        if key in city_key:
            return value
    return None


def get_max_temps(city_name, past_days=7, forecast_days=7):
    coords = city_coords(city_name)
    if not coords:
        return None
    lat, lon = coords
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max",
        "timezone": "auto",
        "past_days": past_days,
        "forecast_days": forecast_days,
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


def get_upcoming_dates(days=3):
    today = datetime.now(timezone.utc).date()
    return [today + timedelta(days=i) for i in range(0, days + 1)]


def date_search_strings(d):
    return [d.strftime("%B %-d"), d.strftime("%B %d").replace(" 0", " ")]


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
                if slug in seen or event.get("closed") is True:
                    continue
                events.append(event)
                seen.add(slug)
    return events


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
    for fmt in ("%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(f"{month_name} {day} {year}", fmt).date()
        except ValueError:
            pass
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


def load_ledger(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_ledger(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LEDGER_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in LEDGER_FIELDS})


def parse_bucket_value(row):
    kind = row.get("kind")
    raw = row.get("bucket", "")
    if kind == "range" and "-" in raw:
        a, b = raw.split("-", 1)
        return (float(a), float(b))
    try:
        return float(raw)
    except Exception:
        return None


def settle_open_trades(rows, today):
    settled_today = []
    cache = {}
    for row in rows:
        if row.get("status") != "OPEN":
            continue
        try:
            market_date = datetime.strptime(row["market_date"], "%Y-%m-%d").date()
        except Exception:
            continue
        # Settle the day after the market date
        if market_date >= today:
            continue
        city = row.get("city", "")
        if city not in cache:
            cache[city] = get_max_temps(city)
        temps = cache[city] or {}
        actual = temps.get(row["market_date"])
        value = parse_bucket_value(row)
        hit = bucket_hit(row.get("kind"), value, actual)
        if hit is None or actual is None:
            continue
        won = bool(hit)
        price = float(row.get("market_price") or 0)
        size = float(row.get("paper_size") or PAPER_SIZE)
        pnl = yes_pnl(price, size, won)
        row["actual_c"] = f"{actual:.1f}"
        row["result"] = "WIN" if won else "LOSS"
        row["pnl"] = f"{pnl:.2f}"
        row["status"] = "SETTLED"
        row["settled_utc"] = datetime.now(timezone.utc).isoformat()
        settled_today.append(row)
    return settled_today


def make_id(now, city, bucket, market_date):
    return f"{now.strftime('%Y%m%d%H%M%S')}-{city}-{bucket}-{market_date}"


def already_open(rows, city, bucket, market_date):
    for row in rows:
        if (
            row.get("city") == city
            and str(row.get("bucket")) == str(bucket)
            and row.get("market_date") == str(market_date)
            and row.get("status") in ("OPEN", "SETTLED")
        ):
            return True
    return False


def summarize(rows, today):
    settled = [r for r in rows if r.get("status") == "SETTLED"]
    open_rows = [r for r in rows if r.get("status") == "OPEN"]
    wins = [r for r in settled if r.get("result") == "WIN"]
    losses = [r for r in settled if r.get("result") == "LOSS"]
    pnl = sum(float(r.get("pnl") or 0) for r in settled)
    resolved = len(wins) + len(losses)
    win_rate = (len(wins) / resolved * 100) if resolved else 0.0

    opened_today = []
    for r in rows:
        opened = (r.get("opened_utc") or "")[:10]
        if opened == today.isoformat():
            opened_today.append(r)

    settled_today = []
    for r in settled:
        settled_on = (r.get("settled_utc") or "")[:10]
        if settled_on == today.isoformat():
            settled_today.append(r)

    today_pnl = sum(float(r.get("pnl") or 0) for r in settled_today)
    today_wins = sum(1 for r in settled_today if r.get("result") == "WIN")
    today_losses = sum(1 for r in settled_today if r.get("result") == "LOSS")
    today_resolved = today_wins + today_losses
    today_wr = (today_wins / today_resolved * 100) if today_resolved else 0.0

    return {
        "opened_today": opened_today,
        "settled_today": settled_today,
        "open_rows": open_rows,
        "wins": wins,
        "losses": losses,
        "pnl": pnl,
        "win_rate": win_rate,
        "resolved": resolved,
        "today_pnl": today_pnl,
        "today_wins": today_wins,
        "today_losses": today_losses,
        "today_wr": today_wr,
    }


def format_email(stats, today):
    lines = []
    lines.append(f"Polymarket Weather Bot — Daily Report")
    lines.append(f"Date: {today.isoformat()}")
    lines.append("")
    lines.append("TODAY")
    lines.append(f"- New paper trades: {len(stats['opened_today'])}")
    lines.append(f"- Settled trades: {stats['today_wins'] + stats['today_losses']}")
    lines.append(f"- Wins: {stats['today_wins']}")
    lines.append(f"- Losses: {stats['today_losses']}")
    lines.append(f"- Today PnL: ${stats['today_pnl']:.2f}")
    lines.append(f"- Today win rate: {stats['today_wr']:.1f}%")
    lines.append("")
    lines.append("ALL PAPER TRADES")
    lines.append(f"- Open: {len(stats['open_rows'])}")
    lines.append(f"- Settled: {stats['resolved']}")
    lines.append(f"- Wins: {len(stats['wins'])}")
    lines.append(f"- Losses: {len(stats['losses'])}")
    lines.append(f"- Win rate: {stats['win_rate']:.1f}%")
    lines.append(f"- Total PnL: ${stats['pnl']:.2f}")
    lines.append("")

    if stats["opened_today"]:
        lines.append("New paper trades today:")
        for r in stats["opened_today"]:
            lines.append(
                f"  • BUY YES {r['city']} {r['bucket']} @ {float(r['market_price']):.2f} "
                f"(model {float(r['model_prob']):.2f}, edge {float(r['edge']):+.2f})"
            )
        lines.append("")

    if stats["settled_today"]:
        lines.append("Settled today:")
        for r in stats["settled_today"]:
            lines.append(
                f"  • {r['result']} {r['city']} {r['bucket']} actual={r.get('actual_c')} "
                f"PnL ${float(r['pnl']):+.2f}"
            )
        lines.append("")

    if stats["open_rows"]:
        lines.append("Still open:")
        for r in stats["open_rows"]:
            lines.append(f"  • {r['city']} {r['market_date']} {r['bucket']} @ {float(r['market_price']):.2f}")

    lines.append("")
    lines.append("Paper trading only. No real orders were placed.")
    return "\n".join(lines)


def send_email(subject, body):
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS and MAIL_TO):
        log("Email not sent: missing SMTP_HOST / SMTP_USER / SMTP_PASS / MAIL_TO")
        return False
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = MAIL_FROM or SMTP_USER
    msg["To"] = MAIL_TO
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(msg["From"], [MAIL_TO], msg.as_string())
    return True


def main():
    now = datetime.now(timezone.utc)
    today = now.date()
    log("Weather Scanner + Settlement + Daily Email")
    log("Time (UTC):", now.isoformat())
    log(f"Rules: min edge {MIN_EDGE:.0%}, paper size ${PAPER_SIZE:.0f}")
    log("=" * 64)

    rows = load_ledger(LEDGER_PATH)
    settled_today = settle_open_trades(rows, today)
    log(f"Settled {len(settled_today)} paper trade(s) today")
    for r in settled_today:
        log(f"  {r['result']} {r['city']} {r['bucket']} actual={r.get('actual_c')} PnL ${float(r['pnl']):+.2f}")

    events = get_weather_events()
    log(f"\nFound {len(events)} open temperature events\n")
    new_trades = []

    for event in events:
        title = event.get("title") or ""
        city = extract_city(title)
        market_date = extract_market_date(title)
        log(f"• {title}")
        log(f"  City: {city} | Date: {market_date}")

        temps = get_max_temps(city)
        if not temps:
            log("  Forecast: not available\n")
            continue

        date_key = market_date.isoformat() if market_date else None
        if date_key and date_key in temps:
            mu = temps[date_key]
        else:
            future = [k for k in sorted(temps) if k >= today.isoformat()]
            date_key = future[0] if future else sorted(temps)[0]
            mu = temps[date_key]

        lead = 0
        if market_date:
            lead = max(0, (market_date - today).days)
        sigma = sigma_for_lead(lead)
        log(f"  Model: mu={mu:.1f}C  sigma={sigma:.2f}  lead={lead}d")

        best = None
        open_count = 0
        for market in event.get("markets", []):
            yes_price = parse_yes_price(market)
            if yes_price is None or yes_price <= MIN_PRICE or yes_price >= MAX_PRICE:
                continue
            parsed, kind = parse_bucket(market.get("question") or "")
            if parsed is None:
                continue
            model_p = bucket_probability(kind, parsed, mu, sigma)
            edge = model_p - yes_price
            open_count += 1
            label = parsed if kind != "range" else f"{parsed[0]}-{parsed[1]}"
            log(f"    {label} {kind:5} | YES {yes_price:.2f} | model {model_p:.2f} | edge {edge:+.2f}")
            if best is None or edge > best["edge"]:
                best = {"label": label, "kind": kind, "yes_price": yes_price, "model_p": model_p, "edge": edge}

            if edge >= MIN_EDGE and market_date and not already_open(rows, city, label, market_date):
                trade = {
                    "id": make_id(now, city, label, market_date),
                    "opened_utc": now.isoformat(),
                    "settled_utc": "",
                    "city": city,
                    "event_title": title,
                    "market_date": market_date.isoformat(),
                    "bucket": str(label),
                    "kind": kind,
                    "side": "YES",
                    "model_prob": f"{model_p:.4f}",
                    "market_price": f"{yes_price:.4f}",
                    "edge": f"{edge:.4f}",
                    "forecast_c": f"{mu:.2f}",
                    "sigma": f"{sigma:.2f}",
                    "paper_size": f"{PAPER_SIZE:.2f}",
                    "actual_c": "",
                    "result": "",
                    "pnl": "",
                    "status": "OPEN",
                }
                rows.append(trade)
                new_trades.append(trade)

        if open_count == 0:
            log("    (no open buckets in price band)")
        elif best:
            log(
                f"  Best bucket: {best['label']} {best['kind']} | "
                f"YES {best['yes_price']:.2f} | model {best['model_p']:.2f} | edge {best['edge']:+.2f}"
            )
        log("")

    save_ledger(LEDGER_PATH, rows)
    stats = summarize(rows, today)
    body = format_email(stats, today)
    subject = f"Weather bot {today.isoformat()} | {len(new_trades)} new | {stats['today_wins']}W/{stats['today_losses']}L | ${stats['today_pnl']:+.2f}"

    log("=" * 64)
    log("DAILY EMAIL PREVIEW")
    log(body)
    log("=" * 64)

    try:
        if send_email(subject, body):
            log(f"Email sent to {MAIL_TO}")
        else:
            log("Email skipped. Add Railway variables to enable sending.")
    except Exception as e:
        log(f"Email failed: {e}")

    log(f"Ledger saved to {LEDGER_PATH}")
    log("Done.")


if __name__ == "__main__":
    main()
