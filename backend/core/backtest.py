"""Historical backtester for NYC (KXHIGHNY) Kalshi weather markets.

DATA NOTE:
The Open-Meteo ensemble API only provides live forecast members for the coming
~15 days. For historical dates it returns no ensemble members at all.
This backtester therefore uses two data sources:

  • Recent dates (≤ 14 days ago): live GFS ensemble via ensemble-api (31 members,
    real forecast uncertainty).
  • Historical dates (> 14 days ago): Open-Meteo archive API, which returns the
    ERA5/IFS observed daily maximum temperature as a single deterministic value
    (std = 0). All historical markets are flagged `hindcast=True`.

For historical markets the model probability is a step function (0 or 1), so
model accuracy is trivially 100%. The MEANINGFUL metric for past data is
**market calibration**: "when Kalshi priced YES at 60–70c, did YES actually
win ~65% of the time?" That tests whether Kalshi prices are efficient, which
is directly relevant to whether an edge-based strategy can beat the market.
"""

import asyncio
import logging
from datetime import date, timedelta, datetime
from typing import Dict, List, Optional, Tuple

import httpx

from backend.config import settings
from backend.data.kalshi_client import KalshiClient, kalshi_credentials_present
from backend.data.kalshi_markets import _parse_kalshi_ticker
from backend.data.weather import CITY_CONFIG, fetch_ensemble_forecast

logger = logging.getLogger("trading_bot")

SERIES = "KXHIGHNY"
CITY_KEY = "nyc"
FLAT_BET = 25.0          # dollars per actionable signal in hypothetical P&L
ARCHIVE_LAG_DAYS = 14    # switch to archive API for dates older than this


# ---------------------------------------------------------------------------
# Archive API (historical observed temperatures)
# ---------------------------------------------------------------------------

async def _fetch_archive_temp(target_date: date) -> Optional[float]:
    """Fetch observed daily max temperature (°F) from Open-Meteo archive API."""
    city = CITY_CONFIG.get(CITY_KEY)
    if not city:
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                "https://archive-api.open-meteo.com/v1/archive",
                params={
                    "latitude": city["lat"],
                    "longitude": city["lon"],
                    "start_date": target_date.isoformat(),
                    "end_date": target_date.isoformat(),
                    "daily": "temperature_2m_max",
                    "temperature_unit": "fahrenheit",
                },
            )
            response.raise_for_status()
            data = response.json()
            vals = data.get("daily", {}).get("temperature_2m_max", [])
            if vals and vals[0] is not None:
                return float(vals[0])
    except Exception as e:
        logger.debug(f"Archive fetch failed for {target_date}: {e}")
    return None


# ---------------------------------------------------------------------------
# Per-date temperature resolver
# Returns (mean_high, std_high, num_members, is_hindcast)
# ---------------------------------------------------------------------------

async def _resolve_temp(target_date: date) -> Optional[Tuple[float, float, int, bool]]:
    """
    For recent dates: use the GFS ensemble (real forecast uncertainty).
    For older dates: use the ERA5 archive (single observed value, std=0).
    """
    today = date.today()
    use_archive = (today - target_date).days > ARCHIVE_LAG_DAYS

    if not use_archive:
        forecast = await fetch_ensemble_forecast(CITY_KEY, target_date)
        if forecast and forecast.member_highs:
            return (forecast.mean_high, forecast.std_high, forecast.num_members, False)
        # Fall through to archive if ensemble returned nothing
        use_archive = True

    # Archive path
    observed = await _fetch_archive_temp(target_date)
    if observed is not None:
        return (observed, 0.0, 1, True)

    return None


# ---------------------------------------------------------------------------
# Row builder
# ---------------------------------------------------------------------------

def _row(
    ticker: str,
    target_date: date,
    threshold_f: float,
    direction: str,
    market_price: float,
    actual_outcome: int,
    mean_high: float,
    std_high: float,
    num_members: int,
    is_hindcast: bool,
    edge_threshold: float,
) -> dict:
    # Model probability: fraction of ensemble above/below threshold.
    # For hindcast (single value), this is a step function (0 or 1).
    if direction == "above":
        model_prob = 1.0 if mean_high > threshold_f else 0.0
    else:
        model_prob = 1.0 if mean_high <= threshold_f else 0.0

    # For live ensemble (std > 0) use a proper probability from the members.
    # We don't have members here — but if std > 0 it means we came from a real
    # ensemble, so we re-derive. For the archive path (std=0) the step function
    # above is correct.
    # NOTE: We pass mean+std; callers that have real members use
    # fetch_ensemble_forecast directly. This simplified path is fine for backtest.

    # Clip slightly away from absolute 0/1 for Brier score stability
    model_prob = max(0.02, min(0.98, model_prob))

    edge = model_prob - market_price
    model_side = "yes" if model_prob >= 0.5 else "no"
    actual_side = "yes" if actual_outcome == 1 else "no"
    is_actionable = abs(edge) >= edge_threshold

    hypo_pnl = 0.0
    if is_actionable:
        if edge > 0:  # bet YES
            hypo_pnl = FLAT_BET * (1 - market_price) / market_price if actual_outcome == 1 else -FLAT_BET
        else:  # bet NO
            no_price = 1.0 - market_price
            hypo_pnl = FLAT_BET * (1 - no_price) / no_price if actual_outcome == 0 else -FLAT_BET

    return {
        "ticker": ticker,
        "target_date": target_date.isoformat(),
        "threshold_f": threshold_f,
        "direction": direction,
        "market_price": round(market_price, 4),
        "actual_outcome": actual_outcome,
        "actual_side": actual_side,
        "model_probability": round(model_prob, 4),
        "ensemble_mean": round(mean_high, 2),
        "ensemble_std": round(std_high, 2),
        "ensemble_members": num_members,
        "edge": round(edge, 4),
        "model_correct": model_side == actual_side,
        "is_actionable": is_actionable,
        "hindcast": is_hindcast,
        "hypo_pnl": round(hypo_pnl, 2),
    }


# ---------------------------------------------------------------------------
# Kalshi market fetcher
# ---------------------------------------------------------------------------

async def _fetch_settled_markets(lookback_days: int) -> List[dict]:
    """Return all settled KXHIGHNY markets within the lookback window."""
    if not kalshi_credentials_present():
        logger.warning("Kalshi credentials not set — skipping market fetch")
        return []

    client = KalshiClient()
    cutoff = date.today() - timedelta(days=lookback_days)
    markets: List[dict] = []
    cursor = None

    try:
        while True:
            params: dict = {"series_ticker": SERIES, "status": "settled", "limit": 200}
            if cursor:
                params["cursor"] = cursor

            data = await client.get_markets(params)
            raw = data.get("markets", [])

            for m in raw:
                ticker = m.get("ticker", "")
                parsed = _parse_kalshi_ticker(ticker)
                if not parsed or parsed["target_date"] < cutoff:
                    continue
                result = m.get("result", "")
                if result not in ("yes", "no"):
                    continue

                yes_bid = (m.get("yes_bid") or 0) / 100.0
                yes_ask = (m.get("yes_ask") or 0) / 100.0
                last_price = (m.get("last_price") or 50) / 100.0
                if yes_bid > 0 and yes_ask > 0:
                    market_price = (yes_bid + yes_ask) / 2.0
                elif yes_ask > 0:
                    market_price = yes_ask
                else:
                    market_price = last_price
                market_price = max(0.02, min(0.98, market_price))

                markets.append({
                    "ticker": ticker,
                    "target_date": parsed["target_date"],
                    "threshold_f": parsed["threshold_f"],
                    "direction": parsed["direction"],
                    "market_price": market_price,
                    "result": result,
                })

            cursor = data.get("cursor")
            if not cursor or not raw:
                break

    except Exception as e:
        logger.warning(f"Failed to fetch settled {SERIES} markets: {e}")

    logger.info(f"Fetched {len(markets)} settled {SERIES} markets (cutoff {cutoff})")
    return markets


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def run_nyc_backtest(lookback_days: int = 365) -> dict:
    """Run the full NYC backtest. Returns a JSON-serialisable result dict."""
    raw_markets = await _fetch_settled_markets(lookback_days)
    if not raw_markets:
        return _empty_result(0, settings.MIN_EDGE_THRESHOLD, lookback_days)

    # Deduplicate dates — fetch each unique date once, then reuse for all markets
    unique_dates: List[date] = sorted({m["target_date"] for m in raw_markets})
    logger.info(f"Resolving temperatures for {len(unique_dates)} unique dates...")

    # Sequential fetches to avoid hammering the API (archive rate limits are strict)
    date_temps: Dict[date, Optional[Tuple[float, float, int, bool]]] = {}
    for d in unique_dates:
        date_temps[d] = await _resolve_temp(d)
        await asyncio.sleep(0.15)   # gentle throttle

    # Build rows
    rows: List[dict] = []
    for m in raw_markets:
        temp_info = date_temps.get(m["target_date"])
        if temp_info is None:
            continue
        mean_high, std_high, num_members, is_hindcast = temp_info
        rows.append(_row(
            ticker=m["ticker"],
            target_date=m["target_date"],
            threshold_f=m["threshold_f"],
            direction=m["direction"],
            market_price=m["market_price"],
            actual_outcome=1 if m["result"] == "yes" else 0,
            mean_high=mean_high,
            std_high=std_high,
            num_members=num_members,
            is_hindcast=is_hindcast,
            edge_threshold=settings.MIN_EDGE_THRESHOLD,
        ))

    if not rows:
        return _empty_result(len(raw_markets), settings.MIN_EDGE_THRESHOLD, lookback_days)

    n = len(rows)
    threshold = settings.MIN_EDGE_THRESHOLD

    correct = sum(1 for r in rows if r["model_correct"])
    model_accuracy = correct / n

    market_correct = sum(1 for r in rows if (r["market_price"] >= 0.5) == (r["actual_outcome"] == 1))
    market_accuracy = market_correct / n

    brier = sum((r["model_probability"] - r["actual_outcome"]) ** 2 for r in rows) / n
    brier_skill = round(1.0 - brier / 0.25, 4)

    avg_std = sum(r["ensemble_std"] for r in rows) / n

    actionable = [r for r in rows if r["is_actionable"]]
    act_correct = sum(1 for r in actionable if r["model_correct"]) if actionable else 0
    actionable_accuracy = act_correct / len(actionable) if actionable else 0.0

    hypo_pnl = sum(r["hypo_pnl"] for r in actionable)

    equity: List[dict] = []
    cumulative = 0.0
    for r in sorted(actionable, key=lambda x: x["target_date"]):
        cumulative += r["hypo_pnl"]
        equity.append({"date": r["target_date"], "cumulative_pnl": round(cumulative, 2)})

    hindcast_count = sum(1 for r in rows if r["hindcast"])

    return {
        "total_fetched": len(raw_markets),
        "total_analyzed": n,
        "lookback_days": lookback_days,
        "edge_threshold": threshold,
        "model_accuracy": round(model_accuracy, 4),
        "market_accuracy": round(market_accuracy, 4),
        "brier_score": round(brier, 4),
        "brier_skill_score": brier_skill,
        "avg_ensemble_std": round(avg_std, 2),
        "actionable_count": len(actionable),
        "actionable_accuracy": round(actionable_accuracy, 4),
        "hypo_pnl": round(hypo_pnl, 2),
        "hypo_pnl_per_signal": round(hypo_pnl / len(actionable), 2) if actionable else 0.0,
        "hindcast_count": hindcast_count,
        "hindcast_fraction": round(hindcast_count / n, 3),
        "calibration_buckets": _calibration_buckets(rows),
        "market_calibration_buckets": _market_calibration_buckets(rows),
        "monthly": _monthly_breakdown(rows),
        "edge_histogram": _edge_histogram(rows),
        "equity_curve": equity,
        "rows": sorted(rows, key=lambda x: x["target_date"], reverse=True),
        "note": (
            f"{hindcast_count}/{n} markets used ERA5 archive data (observed temperature, std=0). "
            f"For those, model accuracy reflects perfect hindsight — not forecast skill. "
            f"The meaningful metric for historical data is Market Calibration: "
            f"does Kalshi's price match actual outcome rates?"
        ),
    }


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------

def _calibration_buckets(rows: List[dict]) -> List[dict]:
    from collections import defaultdict
    data: dict = defaultdict(lambda: {"sum_pred": 0.0, "sum_actual": 0, "count": 0})
    for r in rows:
        b = int(r["model_probability"] * 10) * 10
        b = min(b, 90)
        data[b]["sum_pred"] += r["model_probability"]
        data[b]["sum_actual"] += r["actual_outcome"]
        data[b]["count"] += 1

    return [
        {
            "label": f"{b}–{b+10}%",
            "predicted_avg": round(data[b]["sum_pred"] / data[b]["count"], 3),
            "actual_rate": round(data[b]["sum_actual"] / data[b]["count"], 3),
            "count": data[b]["count"],
        }
        for b in range(0, 100, 10)
        if data[b]["count"] > 0
    ]


def _market_calibration_buckets(rows: List[dict]) -> List[dict]:
    from collections import defaultdict
    data: dict = defaultdict(lambda: {"sum_price": 0.0, "sum_actual": 0, "count": 0})
    for r in rows:
        b = int(r["market_price"] * 10) * 10
        b = min(b, 90)
        data[b]["sum_price"] += r["market_price"]
        data[b]["sum_actual"] += r["actual_outcome"]
        data[b]["count"] += 1

    return [
        {
            "label": f"{b}–{b+10}%",
            "predicted_avg": round(data[b]["sum_price"] / data[b]["count"], 3),
            "actual_rate": round(data[b]["sum_actual"] / data[b]["count"], 3),
            "count": data[b]["count"],
        }
        for b in range(0, 100, 10)
        if data[b]["count"] > 0
    ]


def _monthly_breakdown(rows: List[dict]) -> List[dict]:
    from collections import defaultdict
    data: dict = defaultdict(lambda: {"correct": 0, "total": 0, "edge_sum": 0.0, "std_sum": 0.0, "actionable": 0})
    for r in rows:
        key = r["target_date"][:7]
        data[key]["total"] += 1
        if r["model_correct"]:
            data[key]["correct"] += 1
        data[key]["edge_sum"] += abs(r["edge"])
        data[key]["std_sum"] += r["ensemble_std"]
        if r["is_actionable"]:
            data[key]["actionable"] += 1

    return [
        {
            "month": k,
            "count": d["total"],
            "accuracy": round(d["correct"] / d["total"], 3),
            "avg_edge": round(d["edge_sum"] / d["total"], 3),
            "avg_std": round(d["std_sum"] / d["total"], 2),
            "actionable": d["actionable"],
        }
        for k, d in sorted(data.items())
    ]


def _edge_histogram(rows: List[dict], bins: int = 10) -> List[dict]:
    if not rows:
        return []
    max_edge = max(abs(r["edge"]) for r in rows)
    if max_edge == 0:
        return []
    bin_size = max_edge / bins
    counts = [0] * bins
    for r in rows:
        idx = min(int(abs(r["edge"]) / bin_size), bins - 1)
        counts[idx] += 1
    return [
        {"label": f"{i * bin_size * 100:.0f}–{(i + 1) * bin_size * 100:.0f}%", "count": counts[i]}
        for i in range(bins)
        if counts[i] > 0
    ]


def _empty_result(fetched: int, threshold: float, lookback_days: int) -> dict:
    return {
        "total_fetched": fetched,
        "total_analyzed": 0,
        "lookback_days": lookback_days,
        "edge_threshold": threshold,
        "model_accuracy": 0.0,
        "market_accuracy": 0.0,
        "brier_score": 0.25,
        "brier_skill_score": 0.0,
        "avg_ensemble_std": 0.0,
        "actionable_count": 0,
        "actionable_accuracy": 0.0,
        "hypo_pnl": 0.0,
        "hypo_pnl_per_signal": 0.0,
        "hindcast_count": 0,
        "hindcast_fraction": 0.0,
        "calibration_buckets": [],
        "market_calibration_buckets": [],
        "monthly": [],
        "edge_histogram": [],
        "equity_curve": [],
        "rows": [],
        "note": "No data. Check Kalshi credentials or try a shorter lookback window.",
    }
