"""Kalshi weather temperature market fetcher and types."""
import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional

from backend.data.kalshi_client import KalshiClient, kalshi_credentials_present

logger = logging.getLogger("trading_bot")

# Kalshi KXHIGH series tickers by city key
CITY_SERIES: Dict[str, str] = {
    "nyc": "KXHIGHNY",
    "chicago": "KXHIGHCHI",
    "miami": "KXHIGHMIA",
    "los_angeles": "KXHIGHLAX",
    "denver": "KXHIGHDEN",
}

CITY_NAMES: Dict[str, str] = {
    "nyc": "New York",
    "chicago": "Chicago",
    "miami": "Miami",
    "los_angeles": "Los Angeles",
    "denver": "Denver",
}

MONTH_ABBR = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


@dataclass
class WeatherMarket:
    """A Kalshi weather temperature prediction market."""
    slug: str
    market_id: str          # Kalshi ticker e.g. KXHIGHNY-26APR28-B72
    platform: str           # always "kalshi"
    title: str
    city_key: str
    city_name: str
    target_date: date
    threshold_f: float      # Temperature threshold in Fahrenheit
    metric: str             # "high" (Kalshi only supports daily high)
    direction: str          # "above" (B-bracket) or "below" (T-bracket)
    yes_price: float        # 0–1
    no_price: float         # 0–1
    volume: float = 0.0


def _parse_kalshi_ticker(ticker: str) -> Optional[dict]:
    """
    Parse a Kalshi bracket ticker into market parameters.

    Format examples:
      KXHIGHNY-26APR28-B72     → above 72°F on 2026-04-28
      KXHIGHMIA-26MAY01-T80    → below 80°F on 2026-05-01
    """
    match = re.match(
        r'^[A-Z]+-(\d{2})([A-Z]{3})(\d{2})-([BT])([\d.]+)$',
        ticker,
    )
    if not match:
        return None

    yy, mon_str, dd = int(match.group(1)), match.group(2), int(match.group(3))
    boundary_type, threshold = match.group(4), float(match.group(5))

    month = MONTH_ABBR.get(mon_str)
    if not month:
        return None

    try:
        target_date = date(2000 + yy, month, dd)
    except ValueError:
        return None

    # B = bottom boundary → YES means "above"; T = top boundary → YES means "below"
    direction = "above" if boundary_type == "B" else "below"

    return {"target_date": target_date, "threshold_f": threshold, "direction": direction}


async def fetch_kalshi_weather_markets(
    city_keys: Optional[List[str]] = None,
) -> List[WeatherMarket]:
    """
    Fetch open weather temperature markets from Kalshi for the given cities.
    Handles cursor-based pagination.
    """
    if not kalshi_credentials_present():
        logger.warning("Kalshi credentials not set — skipping market fetch")
        return []

    client = KalshiClient()
    markets: List[WeatherMarket] = []
    today = date.today()
    cities = city_keys or list(CITY_SERIES.keys())

    for city_key in cities:
        series = CITY_SERIES.get(city_key)
        if not series:
            continue

        city_name = CITY_NAMES.get(city_key, city_key)
        cursor = None

        try:
            while True:
                params: dict = {"series_ticker": series, "status": "open", "limit": 200}
                if cursor:
                    params["cursor"] = cursor

                data = await client.get_markets(params)
                raw_markets = data.get("markets", [])

                for m in raw_markets:
                    ticker = m.get("ticker", "")
                    parsed = _parse_kalshi_ticker(ticker)
                    if not parsed or parsed["target_date"] < today:
                        continue

                    yes_price = (m.get("yes_ask") or 0) / 100.0
                    no_price = (m.get("no_ask") or 0) / 100.0

                    if yes_price <= 0:
                        yes_price = (m.get("last_price") or 50) / 100.0
                    if no_price <= 0:
                        no_price = 1.0 - yes_price

                    # Skip resolved or illiquid
                    if yes_price > 0.98 or yes_price < 0.02:
                        continue

                    markets.append(WeatherMarket(
                        slug=ticker,
                        market_id=ticker,
                        platform="kalshi",
                        title=m.get("title", ticker),
                        city_key=city_key,
                        city_name=city_name,
                        target_date=parsed["target_date"],
                        threshold_f=parsed["threshold_f"],
                        metric="high",
                        direction=parsed["direction"],
                        yes_price=yes_price,
                        no_price=no_price,
                        volume=float(m.get("volume", 0) or 0),
                    ))

                cursor = data.get("cursor")
                if not cursor or not raw_markets:
                    break

        except Exception as e:
            logger.warning(f"Failed to fetch Kalshi markets for {city_key} ({series}): {e}")

    logger.info(f"Found {len(markets)} Kalshi weather markets across {len(cities)} cities")
    return markets
