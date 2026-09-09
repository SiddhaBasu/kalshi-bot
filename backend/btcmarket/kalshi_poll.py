"""
Live Kalshi KXBTC15M market poller.

Kalshi's REST API has no historical-orderbook endpoint, so "past day"/"past
month" history for the yes/no implied-probability chart does not exist upstream
-- it only exists for as long as we've been recording it ourselves. This module
polls the current window's top-of-book every KXBTC_POLL_INTERVAL_SECONDS and
persists a snapshot, building that history up over time.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from backend.config import settings
from backend.models.database import SessionLocal, KxBtcSnapshot, BtcCandle
from backend.scanner.kalshi_api import KalshiScannerAPI

logger = logging.getLogger("trading_bot")

_api = KalshiScannerAPI()
_last_prune: Optional[datetime] = None


def _parse_kalshi_time(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)


async def get_current_market() -> Optional[dict]:
    """Fetch the single currently-open KXBTC15M market."""
    data = await _api.get_markets(series_ticker=settings.KXBTC15M_SERIES_TICKER, status="open")
    markets = data.get("markets", [])
    if not markets:
        return None
    return markets[0]


def _latest_btc_price() -> Optional[float]:
    db = SessionLocal()
    try:
        row = (
            db.query(BtcCandle)
            .filter(BtcCandle.granularity_seconds == 60)
            .order_by(BtcCandle.open_time.desc())
            .first()
        )
        return row.close if row else None
    finally:
        db.close()


def _prune_old_snapshots():
    global _last_prune
    now = datetime.utcnow()
    if _last_prune and (now - _last_prune) < timedelta(hours=1):
        return
    _last_prune = now
    cutoff = now - timedelta(days=settings.KXBTC_SNAPSHOT_RETENTION_DAYS)
    db = SessionLocal()
    try:
        deleted = db.query(KxBtcSnapshot).filter(KxBtcSnapshot.timestamp < cutoff).delete()
        db.commit()
        if deleted:
            logger.info(f"KXBTC15M snapshots: pruned {deleted} rows older than {settings.KXBTC_SNAPSHOT_RETENTION_DAYS}d")
    finally:
        db.close()


async def poll_and_record() -> Optional[dict]:
    """Fetch the current KXBTC15M market and persist a snapshot. Returns the snapshot dict, or None."""
    market = await get_current_market()
    if not market:
        return None

    snapshot = {
        "ticker": market.get("ticker", ""),
        "event_ticker": market.get("event_ticker", ""),
        "timestamp": datetime.utcnow(),
        "floor_strike": market.get("floor_strike"),
        "open_time": _parse_kalshi_time(market.get("open_time")),
        "close_time": _parse_kalshi_time(market.get("close_time")),
        "yes_bid": float(market.get("yes_bid_dollars", 0) or 0),
        "yes_ask": float(market.get("yes_ask_dollars", 0) or 0),
        "no_bid": float(market.get("no_bid_dollars", 0) or 0),
        "no_ask": float(market.get("no_ask_dollars", 0) or 0),
        "btc_price": _latest_btc_price(),
    }

    db = SessionLocal()
    try:
        db.add(KxBtcSnapshot(**snapshot))
        db.commit()
    finally:
        db.close()

    _prune_old_snapshots()
    return snapshot


def get_recent_snapshots(lookback: timedelta) -> List[dict]:
    db = SessionLocal()
    try:
        since = datetime.utcnow() - lookback
        rows = (
            db.query(KxBtcSnapshot)
            .filter(KxBtcSnapshot.timestamp >= since)
            .order_by(KxBtcSnapshot.timestamp.asc())
            .all()
        )
        return [
            {
                "time": int(r.timestamp.replace(tzinfo=timezone.utc).timestamp()),
                "ticker": r.ticker,
                "event_ticker": r.event_ticker,
                "floor_strike": r.floor_strike,
                "yes_bid": r.yes_bid,
                "yes_ask": r.yes_ask,
                "no_bid": r.no_bid,
                "no_ask": r.no_ask,
                "yes_mid": (r.yes_bid + r.yes_ask) / 2 if r.yes_bid is not None and r.yes_ask is not None else None,
                "btc_price": r.btc_price,
            }
            for r in rows
        ]
    finally:
        db.close()
