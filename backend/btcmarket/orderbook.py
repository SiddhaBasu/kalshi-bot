"""
Real orderbook depth + fill simulation for KXBTC15M, so paper-trading entry
prices reflect what could ACTUALLY be filled at the requested size and at the
current moment, not the naive top-of-book quote captured earlier in the
decision pipeline.

Kalshi's orderbook only ever lists BID ladders for each side (yes_dollars,
no_dollars) -- there's no separate "ask" side, because a market is
peer-to-peer: someone buying YES at 70c is the exact same resting order as
someone selling NO at 30c. So the ask ladder for one side is derived from the
BID ladder of the other: yes_ask_price = 1 - no_bid_price (and vice versa).

Verified empirically against a live market (2026-09-09): max(yes_dollars
price) == the market's quoted yes_bid_dollars, max(no_dollars price) == the
market's quoted no_bid_dollars, and yes_bid + no_bid sums to ~0.99 (the usual
1c spread). Not guessed -- confirmed against real API responses before
trusting this.
"""
import json
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from backend.config import settings
from backend.models.database import SessionLocal, KxBtcOrderbookSnapshot
from backend.scanner.kalshi_api import KalshiScannerAPI

logger = logging.getLogger("trading_bot")
_api = KalshiScannerAPI()
_last_prune: Optional[datetime] = None


async def _fetch_raw_book(ticker: str) -> dict:
    """Raw resting-bid ladders straight from Kalshi, before any yes/no-ask derivation."""
    data = await _api.get_orderbook(ticker)
    fp = (data or {}).get("orderbook_fp") or {}
    return {"yes_bids": fp.get("yes_dollars") or [], "no_bids": fp.get("no_dollars") or []}


def book_imbalance(yes_bids: list, no_bids: list) -> Optional[float]:
    """
    (total resting YES-bid volume - total resting NO-bid volume) / total,
    across ALL depth levels, not just top-of-book -- a raw demand-pressure
    signal distinct from price itself. Same definition kapelame/kalshi-crypto-bot
    uses (see prior_findings.md); +1 = all resting demand is on YES, -1 = all on NO.
    Not yet a trained model input -- we only started recording enough
    historical depth to train on as of 2026-09-10.
    """
    yes_qty = sum(float(s) for _, s in yes_bids)
    no_qty = sum(float(s) for _, s in no_bids)
    total = yes_qty + no_qty
    if total <= 0:
        return None
    return (yes_qty - no_qty) / total


async def get_ask_ladders(ticker: str) -> dict:
    """Returns {'yes_asks': [(price, size), ...], 'no_asks': [...]}, both sorted best (cheapest) price first."""
    raw = await _fetch_raw_book(ticker)
    yes_bids, no_bids = raw["yes_bids"], raw["no_bids"]

    yes_asks = sorted(
        ((round(1.0 - float(p), 4), float(s)) for p, s in no_bids if float(p) > 0),
        key=lambda x: x[0],
    )
    no_asks = sorted(
        ((round(1.0 - float(p), 4), float(s)) for p, s in yes_bids if float(p) > 0),
        key=lambda x: x[0],
    )
    return {"yes_asks": yes_asks, "no_asks": no_asks}


def simulate_fill(ask_ladder: List[Tuple[float, float]], target_notional: float) -> dict:
    """
    Walk the ask ladder (best/cheapest price first), spending up to
    target_notional dollars, and return the volume-weighted average price
    actually achievable -- not the naive best-price-only assumption. If the
    book can't fully absorb target_notional, fully_filled=False and the
    caller should treat filled_notional as the real (smaller) trade size.
    """
    remaining = target_notional
    total_cost = 0.0
    total_contracts = 0.0

    for price, size in ask_ladder:
        if price <= 0 or remaining <= 0:
            continue
        level_notional = price * size
        if level_notional >= remaining:
            total_contracts += remaining / price
            total_cost += remaining
            remaining = 0.0
            break
        total_cost += level_notional
        total_contracts += size
        remaining -= level_notional

    filled_notional = target_notional - remaining
    avg_price = (total_cost / total_contracts) if total_contracts > 0 else None
    return {
        "avg_price": avg_price,
        "requested_notional": target_notional,
        "filled_notional": round(filled_notional, 6),
        "contracts": round(total_contracts, 4),
        "fully_filled": remaining <= 1e-6,
        "best_price": ask_ladder[0][0] if ask_ladder else None,
    }


async def get_book_imbalance(ticker: str) -> Optional[float]:
    """Live convenience wrapper -- fetches the current book and returns book_imbalance() for it."""
    try:
        raw = await _fetch_raw_book(ticker)
    except Exception as e:
        logger.debug(f"Book imbalance fetch failed for {ticker}: {e}")
        return None
    return book_imbalance(raw["yes_bids"], raw["no_bids"])


async def get_taker_buy_sell_ratio(ticker: str) -> Optional[float]:
    """
    (yes_taker_count - no_taker_count) / total, over this window's trade
    history so far. Distinct from book_imbalance (resting orders, i.e. passive
    intent) -- this is aggressor-side flow, actual executed trades. Motivated
    by hamad-khawaja/kalshi-trading-bot's taker_buy_sell_ratio feature, see
    prior_findings.md. Informational only, not a trained model input yet.
    """
    try:
        data = await _api.get_trades(ticker=ticker, limit=1000)
    except Exception as e:
        logger.debug(f"Trade fetch failed for {ticker}: {e}")
        return None
    trades = data.get("trades", [])
    if not trades:
        return None
    yes_count = sum(float(t.get("count_fp", 0) or 0) for t in trades if t.get("taker_side") == "yes")
    no_count = sum(float(t.get("count_fp", 0) or 0) for t in trades if t.get("taker_side") == "no")
    total = yes_count + no_count
    if total <= 0:
        return None
    return (yes_count - no_count) / total


async def get_realistic_fill(ticker: str, direction: str, target_notional: float) -> Optional[dict]:
    """
    Fetches the CURRENT live book (a fresh API call, made right before
    execution -- this is what actually captures latency: whatever moved
    between the original decision and this call is reflected here) and
    simulates the real fill for target_notional dollars on `direction`
    ("yes" or "no").
    """
    try:
        ladders = await get_ask_ladders(ticker)
    except Exception as e:
        logger.debug(f"Orderbook fetch failed for {ticker}: {e}")
        return None
    ladder = ladders["yes_asks"] if direction == "yes" else ladders["no_asks"]
    if not ladder:
        return None
    return simulate_fill(ladder, target_notional)


# ---------------------------------------------------------------------------
# Depth recording -- builds the history future backtests need to simulate
# realistic fills, instead of assuming infinite liquidity at the quoted price.
# ---------------------------------------------------------------------------

def _prune_old_orderbook_snapshots():
    global _last_prune
    now = datetime.utcnow()
    if _last_prune and (now - _last_prune) < timedelta(hours=1):
        return
    _last_prune = now
    cutoff = now - timedelta(days=settings.KXBTC_ORDERBOOK_RETENTION_DAYS)
    db = SessionLocal()
    try:
        deleted = db.query(KxBtcOrderbookSnapshot).filter(KxBtcOrderbookSnapshot.timestamp < cutoff).delete()
        db.commit()
        if deleted:
            logger.info(f"KXBTC15M orderbook snapshots: pruned {deleted} rows older than {settings.KXBTC_ORDERBOOK_RETENTION_DAYS}d")
    finally:
        db.close()


async def record_orderbook_snapshot() -> Optional[dict]:
    """
    Fetch the current window's book and persist its top N levels (each side)
    as one row. Called on its own slower cadence
    (KXBTC_ORDERBOOK_POLL_INTERVAL_SECONDS) from the scheduler.
    """
    from backend.btcmarket import kalshi_poll

    market = await kalshi_poll.get_current_market()
    if not market:
        return None
    ticker = market.get("ticker", "")
    if not ticker:
        return None

    try:
        raw = await _fetch_raw_book(ticker)
    except Exception as e:
        logger.debug(f"Orderbook snapshot fetch failed for {ticker}: {e}")
        return None

    yes_bids, no_bids = raw["yes_bids"], raw["no_bids"]
    imbalance = book_imbalance(yes_bids, no_bids)

    n = settings.KXBTC_ORDERBOOK_DEPTH_LEVELS
    yes_asks = sorted(((round(1.0 - float(p), 4), float(s)) for p, s in no_bids if float(p) > 0), key=lambda x: x[0])[:n]
    no_asks = sorted(((round(1.0 - float(p), 4), float(s)) for p, s in yes_bids if float(p) > 0), key=lambda x: x[0])[:n]
    if not yes_asks and not no_asks:
        return None

    best_yes_ask = yes_asks[0][0] if yes_asks else None
    best_no_ask = no_asks[0][0] if no_asks else None

    db = SessionLocal()
    try:
        db.add(KxBtcOrderbookSnapshot(
            ticker=ticker,
            timestamp=datetime.utcnow(),
            yes_asks_json=json.dumps(yes_asks),
            no_asks_json=json.dumps(no_asks),
            best_yes_ask=best_yes_ask,
            best_no_ask=best_no_ask,
            imbalance=imbalance,
        ))
        db.commit()
    finally:
        db.close()

    _prune_old_orderbook_snapshots()
    return {"ticker": ticker, "best_yes_ask": best_yes_ask, "best_no_ask": best_no_ask, "imbalance": imbalance}


def get_ask_ladder_at(ticker: str, at_or_before: datetime, direction: str) -> Optional[List[Tuple[float, float]]]:
    """
    Reconstruct the ask ladder for `direction` as it was recorded at the
    nearest snapshot at-or-before `at_or_before`, for backtesting realistic
    fills against recorded depth instead of assuming infinite liquidity.
    """
    db = SessionLocal()
    try:
        row = (
            db.query(KxBtcOrderbookSnapshot)
            .filter(KxBtcOrderbookSnapshot.ticker == ticker, KxBtcOrderbookSnapshot.timestamp <= at_or_before)
            .order_by(KxBtcOrderbookSnapshot.timestamp.desc())
            .first()
        )
        if not row:
            return None
        raw = row.yes_asks_json if direction == "yes" else row.no_asks_json
        if not raw:
            return None
        return [(float(p), float(s)) for p, s in json.loads(raw)]
    finally:
        db.close()
