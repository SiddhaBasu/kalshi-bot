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
async def get_candles(interval: str = "1m", lookback: str = "1d"):
    """
    OHLCV candles for the BTC candlestick chart.
    interval: 1m | 5m | 15m | 1h | 6h | 1d (candle width)
    lookback: 1d | 3d | 1w | 1m (how far back to fetch, ending now)
    """
    if interval not in candles_mod.INTERVAL_SECONDS:
        interval = "1m"
    if lookback not in candles_mod.LOOKBACK_TIMEDELTA:
        lookback = "1d"
    return await candles_mod.get_candles(interval, lookback)


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
    """Recorded yes/no probability history: real backfilled candlestick data plus live 1Hz snapshots."""
    lookback = HISTORY_RANGES.get(range, HISTORY_RANGES["1d"])
    return kalshi_poll.get_recent_snapshots(lookback)


@router.get("/kalshi-candles")
async def get_kalshi_candles(range: str = "1d"):
    """Real OHLC candlesticks of the KXBTC15M YES price, stitched across rotating 15-min windows."""
    from backend.btcmarket import kalshi_history
    lookback = HISTORY_RANGES.get(range, HISTORY_RANGES["1d"])
    return kalshi_history.get_kxbtc_candles(lookback)


@router.get("/signal")
async def get_signal():
    """
    Model-vs-market read for the currently-open KXBTC15M window: a driftless-GBM
    probability that BTC settles at/above the strike, estimated from realized
    volatility of recent 1-minute candles, compared against the live orderbook price.
    """
    from backend.btcmarket import model as model_mod
    from dataclasses import asdict

    signal = await model_mod.generate_btc_signal()
    if not signal:
        return {"signal": None}
    return {"signal": asdict(signal)}
