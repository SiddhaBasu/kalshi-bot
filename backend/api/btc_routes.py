"""API routes for the KXBTC15M market data module: candles, live market, and orderbook history."""
from datetime import timedelta

from fastapi import APIRouter

from backend.btcmarket import candles as candles_mod
from backend.btcmarket import kalshi_poll

router = APIRouter(prefix="/api/btc", tags=["btc"])

HISTORY_RANGES = {
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
    "1w": timedelta(days=7),
    "1m": timedelta(days=30),
}


@router.get("/candles")
async def get_candles(range: str = "1d"):
    """OHLCV candles for the BTC candlestick chart. range: '1d' (1m candles) or '1m' (1h candles)."""
    return await candles_mod.get_candles_for_range(range if range in candles_mod.RANGE_CONFIG else "1d")


@router.get("/market")
async def get_market():
    """The currently-open KXBTC15M market, plus the most recent recorded snapshot."""
    market = await kalshi_poll.get_current_market()
    if not market:
        return {"market": None}

    return {
        "market": {
            "ticker": market.get("ticker"),
            "event_ticker": market.get("event_ticker"),
            "title": market.get("title"),
            "floor_strike": market.get("floor_strike"),
            "open_time": market.get("open_time"),
            "close_time": market.get("close_time"),
            "yes_bid": float(market.get("yes_bid_dollars", 0) or 0),
            "yes_ask": float(market.get("yes_ask_dollars", 0) or 0),
            "no_bid": float(market.get("no_bid_dollars", 0) or 0),
            "no_ask": float(market.get("no_ask_dollars", 0) or 0),
            "volume": float(market.get("volume_fp", 0) or 0),
            "rules_primary": market.get("rules_primary"),
        }
    }


@router.get("/history")
async def get_history(range: str = "1d"):
    """Recorded yes/no probability history from our own orderbook snapshots (live-recorded, not backfilled)."""
    lookback = HISTORY_RANGES.get(range, HISTORY_RANGES["1d"])
    return kalshi_poll.get_recent_snapshots(lookback)
