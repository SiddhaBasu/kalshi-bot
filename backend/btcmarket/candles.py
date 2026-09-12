"""
BTC-USD OHLCV candles, sourced from Coinbase's public candle endpoint (no auth,
US-accessible) and cached in SQLite so repeated requests don't re-hit Coinbase
and so history survives even if Coinbase is briefly unavailable.

Coinbase caps each request at 300 candles, so a month of hourly candles
(~720) or a day of 1-minute candles (~1440) both need pagination.
"""
import logging
import math
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import httpx
from sqlalchemy import and_

from backend.models.database import SessionLocal, BtcCandle

logger = logging.getLogger("trading_bot")

# Short-horizon momentum (mom_5s/15s/30s), motivated by kapelame/kalshi-crypto-bot's
# feature set (see prior_findings.md) -- our 1-minute candles can't see anything
# faster than a ~60s return, but a 15-minute market can move meaningfully in 5-30s.
# In-memory only (resets on restart, not persisted for training) -- this is a live
# signal, not yet a model input; would need a real tick-storage table to backtest.
#
# maxlen=95 (not 45) so the buffer comfortably covers a 60s trailing average
# (TWAP60_WINDOW_SECONDS below) with margin for occasional missed 1Hz ticks --
# needed for get_trailing_average_price(), not just the original mom_5s/15s/30s
# use case.
_TICK_BUFFER: deque = deque(maxlen=95)

# Kalshi settles KXBTC15M against a 60-second TWAP of CF Benchmarks' BRTI, not
# an instantaneous price (see prior_findings.md Round 32/33) -- distance_log
# computed from a raw instantaneous spot price is a structural mismatch with
# what actually determines settlement. Round 33 validated with a real backtest
# (105k historical rows) that smoothing the price with a 60s trailing average
# before computing distance_log/gbm_prob improves Brier by ~0.002-0.003, even
# restricted to realistic early-window decision points.
TWAP_WINDOW_SECONDS = 60


def get_latest_tick() -> Optional[tuple]:
    """Most recent polled (timestamp, price) from the in-memory 1Hz tick buffer,
    or None if empty. Independent of the BtcCandle table's write timing -- see
    its use in btc_paper_trading._build_live_features() for why that matters."""
    if not _TICK_BUFFER:
        return None
    return _TICK_BUFFER[-1]


def get_trailing_average_price(window_seconds: int = TWAP_WINDOW_SECONDS) -> Optional[float]:
    """Mean tick price over the trailing window_seconds, approximating the TWAP
    Kalshi actually settles against (see TWAP_WINDOW_SECONDS docstring above).
    Returns None if the buffer is empty; falls back to whatever history is
    available (no minimum sample requirement) rather than blocking a decision
    just because the buffer hasn't fully filled yet (e.g. right after startup)."""
    if not _TICK_BUFFER:
        return None
    now = datetime.utcnow()
    cutoff = now - timedelta(seconds=window_seconds)
    prices = [p for t, p in _TICK_BUFFER if t >= cutoff]
    if not prices:
        return _TICK_BUFFER[-1][1]
    return sum(prices) / len(prices)


def get_short_momentum() -> dict:
    """Log-return over the trailing ~5s/15s/30s of polled ticks, or None if not enough history yet."""
    now = datetime.utcnow()
    ticks = list(_TICK_BUFFER)
    result = {}
    if not ticks:
        return {"mom_5s": None, "mom_15s": None, "mom_30s": None}
    latest_price = ticks[-1][1]
    for label, window_s in (("mom_5s", 5), ("mom_15s", 15), ("mom_30s", 30)):
        cutoff = now - timedelta(seconds=window_s)
        past = next((p for t, p in ticks if t <= cutoff), None)
        result[label] = round(math.log(latest_price / past), 6) if past and past > 0 else None
    return result

COINBASE_API = "https://api.exchange.coinbase.com"
PRODUCT = "BTC-USD"
MAX_CANDLES_PER_REQUEST = 300

# range key -> (granularity_seconds, lookback) -- kept for backward compatibility
RANGE_CONFIG = {
    "1d": (60, timedelta(days=1)),       # 1-minute candles, past day
    "1m": (3600, timedelta(days=30)),    # 1-hour candles, past month
}

# TradingView-style independent interval + lookback selectors
INTERVAL_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "1d": 86400}
LOOKBACK_TIMEDELTA = {
    "1d": timedelta(days=1), "3d": timedelta(days=3),
    "1w": timedelta(days=7), "1m": timedelta(days=30),
}


async def _fetch_coinbase_window(
    start: datetime, end: datetime, granularity_seconds: int
) -> List[dict]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{COINBASE_API}/products/{PRODUCT}/candles",
            params={
                "start": start.isoformat(),
                "end": end.isoformat(),
                "granularity": granularity_seconds,
            },
        )
        resp.raise_for_status()
        rows = resp.json()

    # Coinbase returns [time, low, high, open, close, volume], newest first
    candles = []
    for r in rows:
        candles.append({
            "open_time": datetime.fromtimestamp(r[0], tz=timezone.utc).replace(tzinfo=None),
            "low": float(r[1]),
            "high": float(r[2]),
            "open": float(r[3]),
            "close": float(r[4]),
            "volume": float(r[5]),
        })
    return candles


async def fetch_coinbase_candles(
    start: datetime, end: datetime, granularity_seconds: int
) -> List[dict]:
    """Paginate through Coinbase's 300-candle-per-request limit for the given window."""
    span_seconds = (end - start).total_seconds()
    max_span = MAX_CANDLES_PER_REQUEST * granularity_seconds

    if span_seconds <= max_span:
        return await _fetch_coinbase_window(start, end, granularity_seconds)

    all_candles: List[dict] = []
    window_start = start
    while window_start < end:
        window_end = min(window_start + timedelta(seconds=max_span), end)
        try:
            all_candles.extend(await _fetch_coinbase_window(window_start, window_end, granularity_seconds))
        except Exception as e:
            logger.warning(f"Coinbase candle fetch failed for {window_start}..{window_end}: {e}")
        window_start = window_end

    return all_candles


def _upsert_candles(db, candles: List[dict], granularity_seconds: int) -> int:
    if not candles:
        return 0
    existing_rows = {
        row.open_time: row
        for row in db.query(BtcCandle).filter(
            BtcCandle.granularity_seconds == granularity_seconds,
            BtcCandle.open_time >= min(c["open_time"] for c in candles),
        ).all()
    }
    added = 0
    updated = 0
    seen_this_batch = set()
    for c in candles:
        if c["open_time"] in seen_this_batch:
            continue  # duplicate open_time within one fetch response -- keep the first
        seen_this_batch.add(c["open_time"])
        existing = existing_rows.get(c["open_time"])
        if existing is not None:
            # Overwrite the live poller's provisional placeholder (poll_live_price
            # always writes volume=0.0 and builds OHLC from 1Hz ticks, not real
            # trade data) with Coinbase's authoritative closed-candle values once
            # they're fetchable. Without this fix, ANY candle ever touched by the
            # live poller kept volume=0 forever -- this function used to skip
            # entirely whenever open_time already existed, so a real backfill
            # could never correct it. Found 2026-09-10 while adding a VWAP
            # feature that came back NaN despite 190 cached candles.
            if existing.volume != c["volume"] or existing.close != c["close"]:
                existing.open = c["open"]
                existing.high = c["high"]
                existing.low = c["low"]
                existing.close = c["close"]
                existing.volume = c["volume"]
                updated += 1
            continue
        db.add(BtcCandle(
            granularity_seconds=granularity_seconds,
            open_time=c["open_time"],
            open=c["open"],
            high=c["high"],
            low=c["low"],
            close=c["close"],
            volume=c["volume"],
        ))
        added += 1
    if updated:
        logger.info(f"BTC candles: corrected {updated} stale placeholder rows (volume/close) with real Coinbase data")
    db.commit()
    return added


# How many trailing candles to always re-fetch and reconcile against Coinbase's
# authoritative closed-candle data, even when the cache already has a "latest"
# row for that span. Needed because poll_live_price() continuously advances
# the newest cached open_time forward every granularity_seconds by inserting a
# fresh volume=0.0 placeholder for the newly-forming bar -- without this, a
# naive "start = latest cached open_time" resume point permanently skips
# re-checking the row that placeholder just superseded, so it never gets
# corrected with real volume. Found 2026-09-10 (n+1 hours after the original
# volume=0-forever bug was supposedly fixed) via a direct DB check showing 199
# of the last 200 cached 1-minute candles still stuck at volume=0.0 despite
# Coinbase's real candle endpoint returning correct non-zero volume for the
# same open_times.
RECONCILE_TRAILING_CANDLES = 5


async def ensure_recent_candles(granularity_seconds: int, lookback: timedelta) -> int:
    """
    Backfill the cache up to now. Fetches the gap since the latest cached
    candle (or the full lookback window on first run), plus always re-fetches
    the last few candles to reconcile any live-poller placeholder against
    Coinbase's authoritative volume once that bar has closed. Returns candles
    added (does not count in-place volume/close corrections -- see
    _upsert_candles' own log line for those).
    """
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        latest = (
            db.query(BtcCandle)
            .filter(BtcCandle.granularity_seconds == granularity_seconds)
            .order_by(BtcCandle.open_time.desc())
            .first()
        )
        reconcile_margin = timedelta(seconds=granularity_seconds * RECONCILE_TRAILING_CANDLES)
        start = max(latest.open_time - reconcile_margin, now - lookback) if latest else now - lookback
        if (now - start).total_seconds() < granularity_seconds:
            return 0

        candles = await fetch_coinbase_candles(start, now, granularity_seconds)
        added = _upsert_candles(db, candles, granularity_seconds)
        if added:
            logger.info(f"BTC candles: cached {added} new {granularity_seconds}s candles")
        return added
    finally:
        db.close()


def get_cached_candles(granularity_seconds: int, lookback: timedelta) -> List[dict]:
    db = SessionLocal()
    try:
        since = datetime.utcnow() - lookback
        rows = (
            db.query(BtcCandle)
            .filter(
                BtcCandle.granularity_seconds == granularity_seconds,
                BtcCandle.open_time >= since,
            )
            .order_by(BtcCandle.open_time.asc())
            .all()
        )
        return [
            {
                "time": int(r.open_time.replace(tzinfo=timezone.utc).timestamp()),
                "open": r.open,
                "high": r.high,
                "low": r.low,
                "close": r.close,
                "volume": r.volume,
            }
            for r in rows
        ]
    finally:
        db.close()


async def get_candles_for_range(range_key: str) -> List[dict]:
    granularity_seconds, lookback = RANGE_CONFIG.get(range_key, RANGE_CONFIG["1d"])
    await ensure_recent_candles(granularity_seconds, lookback)
    return get_cached_candles(granularity_seconds, lookback)


async def get_candles(interval: str, lookback_key: str) -> List[dict]:
    """Independent timeframe (interval) + lookback range, TradingView-toolbar style."""
    granularity_seconds = INTERVAL_SECONDS.get(interval, 60)
    lookback = LOOKBACK_TIMEDELTA.get(lookback_key, timedelta(days=1))
    await ensure_recent_candles(granularity_seconds, lookback)
    return get_cached_candles(granularity_seconds, lookback)


async def poll_live_price() -> Optional[float]:
    """
    Coinbase's REST candles endpoint has no sub-minute granularity, so a real
    OHLC bar can't literally be "1 second wide". Instead, this polls the
    latest trade price at 1Hz and folds it into the currently-forming
    1-minute candle (updating high/low/close in place), which is how
    TradingView-style platforms make the rightmost candle look live.
    """
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(f"{COINBASE_API}/products/{PRODUCT}/ticker")
        resp.raise_for_status()
        data = resp.json()
    price = float(data.get("price", 0) or 0)
    if price <= 0:
        return None

    now = datetime.utcnow()
    _TICK_BUFFER.append((now, price))
    bucket = now.replace(second=0, microsecond=0)

    db = SessionLocal()
    try:
        row = (
            db.query(BtcCandle)
            .filter(BtcCandle.granularity_seconds == 60, BtcCandle.open_time == bucket)
            .first()
        )
        if row:
            row.high = max(row.high, price)
            row.low = min(row.low, price)
            row.close = price
        else:
            db.add(BtcCandle(
                granularity_seconds=60, open_time=bucket,
                open=price, high=price, low=price, close=price, volume=0.0,
            ))
        db.commit()
    finally:
        db.close()

    return price
