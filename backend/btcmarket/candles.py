"""
BTC-USD OHLCV candles, sourced from Coinbase's public candle endpoint (no auth,
US-accessible) and cached in SQLite so repeated requests don't re-hit Coinbase
and so history survives even if Coinbase is briefly unavailable.

Coinbase caps each request at 300 candles, so a month of hourly candles
(~720) or a day of 1-minute candles (~1440) both need pagination.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import httpx
from sqlalchemy import and_

from backend.models.database import SessionLocal, BtcCandle

logger = logging.getLogger("trading_bot")

COINBASE_API = "https://api.exchange.coinbase.com"
PRODUCT = "BTC-USD"
MAX_CANDLES_PER_REQUEST = 300

# range key -> (granularity_seconds, lookback)
RANGE_CONFIG = {
    "1d": (60, timedelta(days=1)),       # 1-minute candles, past day
    "1m": (3600, timedelta(days=30)),    # 1-hour candles, past month
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
    existing_times = {
        row.open_time
        for row in db.query(BtcCandle.open_time).filter(
            BtcCandle.granularity_seconds == granularity_seconds,
            BtcCandle.open_time >= min(c["open_time"] for c in candles),
        ).all()
    }
    added = 0
    for c in candles:
        if c["open_time"] in existing_times:
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
        existing_times.add(c["open_time"])
        added += 1
    db.commit()
    return added


async def ensure_recent_candles(granularity_seconds: int, lookback: timedelta) -> int:
    """
    Backfill the cache up to now. Only fetches the gap since the latest cached
    candle (or the full lookback window on first run). Returns candles added.
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
        start = max(latest.open_time, now - lookback) if latest else now - lookback
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
