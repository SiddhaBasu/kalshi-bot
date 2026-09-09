"""
Whale trade scanner.

Polls Kalshi's /markets/trades firehose across *all* markets, flags any fill
whose notional value clears MIN_WHALE_TRADE_USD, and persists it (DB + CSV) so
you can come back after hours/days of running and see which markets are
actually liquid enough to build a strategy around.
"""
import csv
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Deque, List, Optional
from collections import deque

from sqlalchemy.exc import IntegrityError

from backend.config import settings
from backend.models.database import SessionLocal, WhaleTrade
from backend.scanner.kalshi_api import (
    KalshiScannerAPI,
    build_market_link,
    derive_event_ticker,
    derive_series_ticker,
)

logger = logging.getLogger("trading_bot")

# Module-level singletons so the market metadata cache and trade cursor
# persist across scheduler ticks instead of resetting every poll.
_api = KalshiScannerAPI()
_last_max_ts: Optional[int] = None
_seen_trade_ids: Deque[str] = deque(maxlen=5000)
_seen_trade_id_set: set = set()

INITIAL_LOOKBACK_SECONDS = 300  # on first run, only look back this far (not full history)


@dataclass
class WhaleFill:
    trade_id: str
    ticker: str
    series_ticker: str
    event_ticker: str
    title: str
    taker_side: str          # "yes" or "no"
    count: float             # fractional contracts allowed (Kalshi count_fp)
    price_cents: int
    notional_usd: float
    created_time: datetime
    link: str


def _parse_created_time(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)


def _mark_seen(trade_id: str) -> bool:
    """Returns True if this is a new trade_id (not already processed)."""
    if trade_id in _seen_trade_id_set:
        return False
    if len(_seen_trade_ids) == _seen_trade_ids.maxlen:
        oldest = _seen_trade_ids[0]
        _seen_trade_id_set.discard(oldest)
    _seen_trade_ids.append(trade_id)
    _seen_trade_id_set.add(trade_id)
    return True


def _append_csv(fills: List[WhaleFill]) -> None:
    if not fills:
        return
    path = settings.WHALE_CSV_PATH
    write_header = not os.path.exists(path)
    try:
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow([
                    "trade_id", "ticker", "series_ticker", "event_ticker", "title",
                    "taker_side", "count", "price_cents", "notional_usd", "created_time", "link",
                ])
            for fill in fills:
                writer.writerow([
                    fill.trade_id, fill.ticker, fill.series_ticker, fill.event_ticker, fill.title,
                    fill.taker_side, fill.count, fill.price_cents, f"{fill.notional_usd:.2f}",
                    fill.created_time.isoformat(), fill.link,
                ])
    except Exception as e:
        logger.warning(f"Failed to write whale trades to CSV ({path}): {e}")


def _persist_db(fills: List[WhaleFill]) -> None:
    if not fills:
        return
    db = SessionLocal()
    try:
        for fill in fills:
            row = WhaleTrade(
                trade_id=fill.trade_id,
                ticker=fill.ticker,
                series_ticker=fill.series_ticker,
                event_ticker=fill.event_ticker,
                title=fill.title,
                taker_side=fill.taker_side,
                count=fill.count,
                price_cents=fill.price_cents,
                notional_usd=fill.notional_usd,
                created_time=fill.created_time,
                link=fill.link,
            )
            db.add(row)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()  # already recorded (duplicate trade_id) — ignore
    finally:
        db.close()


async def scan_for_whales(min_usd: Optional[float] = None) -> List[WhaleFill]:
    """
    Fetch trades since the last scan, flag ones over the whale threshold, and
    persist them. Safe to call repeatedly (e.g. every 30s from the scheduler).
    """
    global _last_max_ts

    threshold = min_usd if min_usd is not None else settings.MIN_WHALE_TRADE_USD
    now_ts = int(time.time())
    min_ts = _last_max_ts if _last_max_ts is not None else now_ts - INITIAL_LOOKBACK_SECONDS

    try:
        raw_trades = await _api.get_all_trades(min_ts=min_ts)
    except Exception as e:
        logger.warning(f"Whale scan: failed to fetch trades: {e}")
        return []

    whales: List[WhaleFill] = []
    max_ts_seen = _last_max_ts or min_ts

    for trade in raw_trades:
        trade_id = trade.get("trade_id")
        if not trade_id or not _mark_seen(trade_id):
            continue

        created_raw = trade.get("created_time")
        try:
            created_time = _parse_created_time(created_raw) if created_raw else datetime.utcnow()
            ts_epoch = int(created_time.timestamp())
            max_ts_seen = max(max_ts_seen, ts_epoch)
        except Exception:
            pass

        taker_side = trade.get("taker_side", "yes")
        # Kalshi's /markets/trades returns fractional contract counts as a string
        # (count_fp) and per-contract price as a dollar string, not the integer
        # cents fields used by the order-placement endpoints.
        count = float(trade.get("count_fp", 0) or 0)
        price_key = "yes_price_dollars" if taker_side == "yes" else "no_price_dollars"
        price_dollars = float(trade.get(price_key, 0) or 0)
        price_cents = round(price_dollars * 100)
        notional_usd = count * price_dollars

        if notional_usd < threshold:
            continue

        ticker = trade.get("ticker", "")
        market = await _api.get_market(ticker)
        if market:
            series_ticker = market.get("series_ticker") or derive_series_ticker(ticker)
            event_ticker = market.get("event_ticker") or derive_event_ticker(ticker)
            title = market.get("title") or ticker
        else:
            series_ticker = derive_series_ticker(ticker)
            event_ticker = derive_event_ticker(ticker)
            title = ticker

        whales.append(WhaleFill(
            trade_id=trade_id,
            ticker=ticker,
            series_ticker=series_ticker,
            event_ticker=event_ticker,
            title=title,
            taker_side=taker_side,
            count=count,
            price_cents=price_cents,
            notional_usd=notional_usd,
            created_time=created_time,
            link=build_market_link(series_ticker, title, event_ticker),
        ))

    _last_max_ts = max(max_ts_seen, now_ts - INITIAL_LOOKBACK_SECONDS)

    if whales:
        _persist_db(whales)
        _append_csv(whales)
        logger.info(f"Whale scan: found {len(whales)} trade(s) >= ${threshold:.0f}")

    return whales
