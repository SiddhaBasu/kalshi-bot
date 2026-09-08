"""Trade settlement logic for Kalshi weather markets."""
import logging
from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from backend.models.database import Trade, BotState, Signal

logger = logging.getLogger("trading_bot")


async def _fetch_kalshi_resolution(ticker: str) -> Tuple[bool, Optional[float]]:
    """
    Fetch resolution status for a Kalshi market.
    Returns (is_resolved, settlement_value) where settlement_value is 1.0=YES won, 0.0=NO won.
    """
    try:
        from backend.data.kalshi_client import KalshiClient, kalshi_credentials_present
        if not kalshi_credentials_present():
            return False, None

        client = KalshiClient()
        data = await client.get_market(ticker)
        market = data.get("market", data)

        status = market.get("status", "")
        result = market.get("result", "")

        if status in ("finalized", "determined") and result:
            if result == "yes":
                return True, 1.0
            elif result == "no":
                return True, 0.0

        return False, None

    except Exception as e:
        logger.warning(f"Failed to fetch Kalshi resolution for {ticker}: {e}")
        return False, None


def _calculate_pnl(trade: Trade, settlement_value: float) -> float:
    """
    Calculate P&L given settlement outcome.
    settlement_value: 1.0 = YES won, 0.0 = NO won.
    """
    direction = trade.direction  # "yes" or "no"

    if direction == "yes":
        pnl = trade.size * (1.0 - trade.entry_price) if settlement_value == 1.0 else -trade.size * trade.entry_price
    else:
        pnl = trade.size * (1.0 - trade.entry_price) if settlement_value == 0.0 else -trade.size * trade.entry_price

    return round(pnl, 2)


async def settle_pending_trades(db: Session) -> List[Trade]:
    """Check all pending Kalshi trades for settlement and update the DB."""
    try:
        pending = db.query(Trade).filter(Trade.settled == False).all()
    except Exception as e:
        logger.error(f"Failed to query pending trades: {e}")
        return []

    if not pending:
        return []

    logger.info(f"Checking {len(pending)} pending trades...")
    settled_trades: List[Trade] = []

    for trade in pending:
        try:
            is_resolved, settlement_value = await _fetch_kalshi_resolution(trade.market_ticker)

            if not is_resolved or settlement_value is None:
                continue

            pnl = _calculate_pnl(trade, settlement_value)

            trade.settled = True
            trade.settlement_value = settlement_value
            trade.pnl = pnl
            trade.settlement_time = datetime.utcnow()
            trade.result = "win" if pnl > 0 else ("loss" if pnl < 0 else "push")

            settled_trades.append(trade)

            # Update linked Signal for calibration
            if trade.signal_id:
                linked = db.query(Signal).filter(Signal.id == trade.signal_id).first()
                if linked:
                    actual = "yes" if settlement_value == 1.0 else "no"
                    linked.actual_outcome = actual
                    linked.outcome_correct = (linked.direction == actual)
                    linked.settlement_value = settlement_value
                    linked.settled_at = datetime.utcnow()

            direction_label = trade.direction.upper()
            outcome_label = "YES" if settlement_value == 1.0 else "NO"
            result_label = "WIN" if trade.result == "win" else "LOSS"
            logger.info(
                f"Trade {trade.id} ({trade.market_ticker}): "
                f"{direction_label} @ {trade.entry_price:.0%} → {outcome_label} → {result_label} ${pnl:+.2f}"
            )

        except Exception as e:
            logger.error(f"Failed to settle trade {trade.id}: {e}")

    if settled_trades:
        try:
            db.commit()
            logger.info(f"Settled {len(settled_trades)} trades")
        except Exception as e:
            logger.error(f"Failed to commit settlements: {e}")
            db.rollback()
            return []

    return settled_trades


async def update_bot_state_with_settlements(db: Session, settled_trades: List[Trade]) -> None:
    """Apply P&L from settled trades to the bot's bankroll."""
    if not settled_trades:
        return
    try:
        state = db.query(BotState).first()
        if not state:
            return
        for trade in settled_trades:
            if trade.pnl is not None:
                state.total_pnl += trade.pnl
                state.bankroll += trade.pnl
                if trade.result == "win":
                    state.winning_trades += 1
        db.commit()
        logger.info(f"Bankroll updated: ${state.bankroll:.2f} (P&L ${state.total_pnl:+.2f})")
    except Exception as e:
        logger.error(f"Failed to update bot state: {e}")
        db.rollback()
