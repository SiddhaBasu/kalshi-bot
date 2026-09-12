"""
REAL Kalshi execution for the BTC 15m model.

Deliberately inert: requires BOTH settings.BTC_LIVE_TRADING_ENABLED
(code-level, requires a deploy to flip) AND BtcLiveTradingState.enabled (a DB
row, toggleable without a deploy -- the actual kill switch) to be true before
any real order is placed. Not wired into the scheduler; nothing in this
module runs on its own until something explicitly calls generate_live_trade().

Mirrors backend/core/btc_paper_trading.py's decision logic exactly (same
feature computation, same edge calculation, same Kelly sizing) but places a
REAL limit order instead of simulating a fill, and adds everything real money
requires that simulation didn't need:

  - Real balance checks before sizing (not a virtual bankroll)
  - Crash-safe idempotency via Kalshi's own order history, not just local DB
    state -- a process crash right after submitting an order but before
    recording it locally must not be able to cause a duplicate real order
  - A circuit breaker (daily realized-loss limit)
  - Automatic cancellation of a resting limit order once EITHER a time budget
    runs out OR the edge itself decays below threshold -- an order sitting on
    the book after the model's edge over the market has evaporated is a bad
    trade even if it eventually fills
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from backend.config import settings
from backend.models.database import SessionLocal, BtcLiveTrade, BtcLiveTradingState
from backend.data.kalshi_client import KalshiClient
from backend.btcmarket import orderbook
from backend.btcmarket.fees import trade_fee
from backend.core.btc_paper_trading import _build_live_features, _load_model, _calculate_edge, _kelly_size
from backend.core.btc_model_training import apply_calibration, apply_live_recalibration

logger = logging.getLogger("trading_bot")
_client = KalshiClient()


def _client_order_id_for(ticker: str) -> str:
    """
    Deterministic, not random. If we crash and this function runs again for
    the same ticker, resubmitting with the SAME client_order_id lets Kalshi's
    own idempotency handling reject the duplicate, instead of us silently
    doubling a real position.
    """
    return f"btc15m-{ticker}"


async def get_available_balance_usd() -> float:
    bal = await _client.get_balance()
    return float(bal.get("balance", 0) or 0) / 100.0


def _get_or_create_state(db) -> BtcLiveTradingState:
    state = db.query(BtcLiveTradingState).first()
    if not state:
        state = BtcLiveTradingState(enabled=False, daily_loss_limit=settings.BTC_LIVE_DAILY_LOSS_LIMIT)
        db.add(state)
        db.flush()
    return state


def _check_circuit_breaker(db, state: BtcLiveTradingState, current_balance: float) -> Optional[str]:
    """
    Returns a halt reason string if trading should stop, else None. Trips
    once and stays tripped until cleared manually. Two independent gates:

    1. Daily realized loss (existing) -- resets naturally each day, so a
       slow bleed spread across many days would never trip it.
    2. Drawdown-from-peak (new) -- tracks the highest real balance ever
       observed and halts if current balance falls more than
       max_drawdown_pct below it, regardless of how many days that took.
       Motivated by OctagonAI/kalshi-trading-bot-cli's "5-gate risk engine"
       (Kelly/Liquidity/Correlation/Concentration/Drawdown) -- we already
       have equivalents of Kelly and Liquidity; Correlation/Concentration
       don't apply since this bot only ever holds one position at a time,
       but Drawdown was a real gap. See prior_findings.md.
    """
    if state.halted_at is not None:
        return state.halted_reason or "circuit breaker previously tripped"

    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_trades = (
        db.query(BtcLiveTrade)
        .filter(BtcLiveTrade.settled == True, BtcLiveTrade.entry_time >= today_start)
        .all()
    )
    total = sum(t.pnl for t in today_trades if t.pnl is not None)
    if total <= -abs(state.daily_loss_limit):
        return f"daily realized loss ${total:.2f} breached limit -${state.daily_loss_limit:.2f}"

    if state.peak_bankroll is None or current_balance > state.peak_bankroll:
        state.peak_bankroll = current_balance
    elif state.peak_bankroll > 0:
        drawdown_pct = (state.peak_bankroll - current_balance) / state.peak_bankroll
        if drawdown_pct >= abs(state.max_drawdown_pct or 0.25):
            return f"drawdown {drawdown_pct:.1%} from peak ${state.peak_bankroll:.2f} breached limit {state.max_drawdown_pct:.0%}"

    return None


async def _existing_order_for_ticker(ticker: str) -> bool:
    """
    Crash-safe idempotency: ask Kalshi, not just our local DB, whether we
    already have an order on this ticker. A local-DB-only check can't detect
    an order that was submitted right before a crash and never got recorded.
    Fails CLOSED -- if we can't verify, assume yes and refuse to trade,
    because the cost of a missed trade is nothing and the cost of an
    accidental duplicate real order is real money.
    """
    try:
        data = await _client.get_orders(ticker=ticker)
        return len(data.get("orders", [])) > 0
    except Exception as e:
        logger.warning(f"Could not verify existing Kalshi orders for {ticker} -- refusing to trade to be safe: {e}")
        return True


async def _wait_for_fill_or_expire(order_id: str, ticker: str, model_prob: float, direction: str) -> dict:
    """
    Poll the order until it fills, the time budget runs out, or the edge
    itself decays below threshold -- whichever comes first. Cancels any
    unfilled remainder before returning in the non-fill cases.
    """
    deadline = datetime.utcnow() + timedelta(seconds=settings.BTC_LIVE_ORDER_MAX_WAIT_SECONDS)

    while True:
        order_data = await _client.get_order(order_id)
        order = order_data.get("order", order_data)
        # Verified against https://docs.kalshi.com/api-reference/orders/get-order
        # (2026-09-10) -- these are the REAL field names, both fixed-point
        # strings. Kalshi's status enum is resting/canceled/executed, not
        # "filled" -- an earlier version of this code checked "filled" and
        # would never have matched a real response.
        status = order.get("status", "")
        filled = float(order.get("fill_count_fp") or 0)
        remaining = float(order.get("remaining_count_fp") or 0)

        if status == "executed" or (remaining == 0 and filled > 0):
            return {"status": "filled", "order": order}

        now = datetime.utcnow()

        # Same edge convention as btc_paper_trading's re-validation step --
        # "entry_price" for `direction` is the live ask on that side.
        edge_still_valid = True
        try:
            ladders = await orderbook.get_ask_ladders(ticker)
            ladder = ladders["yes_asks"] if direction == "yes" else ladders["no_asks"]
            if ladder:
                current_price = ladder[0][0]
                yes_equiv = current_price if direction == "yes" else 1.0 - current_price
                current_edge = (model_prob - yes_equiv) if direction == "yes" else (yes_equiv - model_prob)
                edge_still_valid = current_edge >= settings.MIN_EDGE_THRESHOLD and current_price <= settings.MAX_ENTRY_PRICE
        except Exception:
            pass  # can't check right now -- don't force a cancel on that basis alone

        if now >= deadline or not edge_still_valid:
            try:
                await _client.cancel_order(order_id)
            except Exception as e:
                logger.warning(f"Cancel failed for order {order_id} ({ticker}): {e}")
            return {
                "status": "cancelled",
                "reason": "timeout" if now >= deadline else "edge_expired",
                "order": order,
                "filled_at_cancel": filled,
            }

        await asyncio.sleep(settings.BTC_LIVE_ORDER_POLL_SECONDS)


async def generate_live_trade() -> Optional[dict]:
    """Top-level entry point. Returns None on any gate failure or non-fill; a summary dict only on an actual (partial or full) fill."""
    if not settings.BTC_LIVE_TRADING_ENABLED:
        return None  # code-level switch off -- nothing below this line ever runs

    model = _load_model()
    if model is None:
        return None

    ctx = await _build_live_features()
    if not ctx:
        return None

    ticker = ctx["ticker"]

    db = SessionLocal()
    try:
        state = _get_or_create_state(db)
        if not state.enabled:
            return None  # DB-level kill switch off

        balance = await get_available_balance_usd()
        halt_reason = _check_circuit_breaker(db, state, balance)
        db.commit()  # persist any peak_bankroll update even if we return below
        if halt_reason:
            if state.halted_at is None:
                state.halted_at = datetime.utcnow()
                state.halted_reason = halt_reason
                db.commit()
                logger.error(f"BTC LIVE TRADING HALTED: {halt_reason}")
            return None

        if db.query(BtcLiveTrade).filter(BtcLiveTrade.ticker == ticker).first():
            return None  # fast local check

        import numpy as np
        from backend.core.btc_model_training import FEATURE_COLUMNS
        X = np.array([[ctx["features"][c] for c in FEATURE_COLUMNS]])
        raw_model_prob = float(model.predict_proba(X)[:, 1][0])
        # Same two-stage calibration as btc_paper_trading.py's generate_paper_trade()
        # -- this was previously missing here entirely (used the raw, uncalibrated
        # XGBoost output), a real gap found and fixed 2026-09-11 while this module
        # remains fully inert (never enabled). Fixing it now for correctness/
        # consistency, since it costs nothing while dormant but would have been a
        # real bug the moment this path were ever turned on.
        model_prob = apply_live_recalibration(apply_calibration(raw_model_prob))
        market_prob = ctx["live_yes_mid"]

        edge, direction = _calculate_edge(model_prob, market_prob)
        quoted_price = market_prob if direction == "yes" else 1.0 - market_prob
        if abs(edge) < settings.MIN_EDGE_THRESHOLD or quoted_price > settings.MAX_ENTRY_PRICE:
            return None

        # Slow, authoritative crash-safe check -- only after the cheap checks
        # above pass, since it's a real network round trip.
        if await _existing_order_for_ticker(ticker):
            return None

        # `balance` was already fetched above for the drawdown gate -- reuse it, don't fetch twice.
        target_size = _kelly_size(model_prob, quoted_price, balance)
        if target_size < 1.0:
            return None
        if target_size > balance:
            logger.warning(f"BTC live trade sized ${target_size:.2f} exceeds available balance ${balance:.2f} -- skipping")
            return None

        price_cents = max(1, min(99, round(quoted_price * 100)))
        count = int(target_size / (price_cents / 100))
        if count < 1:
            return None

        client_order_id = _client_order_id_for(ticker)
        try:
            response = await _client.place_order(
                ticker=ticker, side=direction, count=count,
                price_cents=price_cents, client_order_id=client_order_id,
            )
        except Exception as e:
            logger.error(f"BTC live order placement FAILED for {ticker}: {e}")
            return None

        order = response.get("order", response)
        order_id = order.get("order_id") or order.get("id")

        # Record BEFORE waiting for a fill -- if the process crashes during
        # the wait, this row (plus Kalshi's own order record) is what the
        # next startup's idempotency check relies on.
        trade = BtcLiveTrade(
            ticker=ticker, client_order_id=client_order_id, order_id=order_id,
            entry_time=datetime.utcnow(), close_time=ctx["close_time"],
            direction=direction, limit_price=quoted_price, target_contracts=count,
            model_probability=model_prob, market_probability=market_prob, edge=edge,
            order_status="resting",
        )
        db.add(trade)
        db.commit()

        result = await _wait_for_fill_or_expire(order_id, ticker, model_prob, direction)

        order_info = result.get("order", {})
        filled = float(order_info.get("fill_count_fp") or result.get("filled_at_cancel") or 0)
        # yes_price_dollars/no_price_dollars are ALREADY dollar-denominated
        # (fixed-point strings), not cents -- dividing by 100 here would have
        # been a second, silent /100 on top of an already-correct value.
        price_field = "yes_price_dollars" if direction == "yes" else "no_price_dollars"
        raw_price = order_info.get(price_field)
        avg_fill_price = float(raw_price) if raw_price else quoted_price

        trade.filled_contracts = filled
        trade.avg_fill_price = avg_fill_price if filled > 0 else None
        if result["status"] == "filled":
            trade.order_status = "filled"
        elif filled > 0:
            trade.order_status = "partially_filled"
        else:
            trade.order_status = "cancelled" if result["status"] == "cancelled" else "expired"

        if filled > 0:
            trade.fee = trade_fee(filled, avg_fill_price)

        db.commit()

        logger.info(
            f"LIVE TRADE: {ticker} {direction.upper()} target={count} filled={filled} "
            f"status={trade.order_status} @ ~{avg_fill_price:.0%} | edge {edge:+.1%}"
        )
        if filled <= 0:
            return None
        return {
            "ticker": ticker, "direction": direction, "filled_contracts": filled,
            "avg_fill_price": round(avg_fill_price, 4), "status": trade.order_status,
        }
    finally:
        db.close()


async def settle_live_trades() -> list:
    """Settle live trades whose window has closed and that actually got filled (partially or fully)."""
    from backend.scanner.kalshi_api import KalshiScannerAPI
    _scanner = KalshiScannerAPI()

    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(seconds=5)
        pending = db.query(BtcLiveTrade).filter(
            BtcLiveTrade.settled == False,
            BtcLiveTrade.close_time <= cutoff,
            BtcLiveTrade.filled_contracts > 0,
        ).all()
        if not pending:
            return []

        settled = []
        for trade in pending:
            market = await _scanner.get_market(trade.ticker, use_cache=False)
            if not market:
                continue
            result = market.get("result", "")
            if result not in ("yes", "no"):
                continue

            settlement_value = 1.0 if result == "yes" else 0.0
            notional = trade.filled_contracts * trade.avg_fill_price
            if trade.direction == "yes":
                gross_pnl = trade.filled_contracts * (1.0 - trade.avg_fill_price) if settlement_value == 1.0 else -notional
            else:
                gross_pnl = trade.filled_contracts * (1.0 - trade.avg_fill_price) if settlement_value == 0.0 else -notional
            pnl = gross_pnl - (trade.fee or 0.0)

            trade.settled = True
            trade.settlement_value = settlement_value
            trade.pnl = round(pnl, 2)
            trade.result = "win" if pnl > 0 else ("loss" if pnl < 0 else "push")
            settled.append({"ticker": trade.ticker, "result": trade.result, "pnl": trade.pnl})

        db.commit()
        return settled
    finally:
        db.close()
