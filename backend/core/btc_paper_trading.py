"""
Paper-trading simulator for KXBTC15M, driven by the trained XGBoost model
(backend/core/btc_model_training.py). NEVER places a real order -- every
"trade" is a BtcPaperTrade row, sized with the same Kelly logic the weather
bot uses, journaled at entry and settled later against Kalshi's real result.

This is intentionally separate from backend/btcmarket/model.py (the GBM
analytical baseline used by /api/btc/signal) -- that module stays untouched
per the earlier decision to keep the learned model research-only there. This
module is where the trained model actually gets used, in simulation.
"""
import hashlib
import json
import logging
import math
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from backend.config import settings
from backend.models.database import SessionLocal, BtcPaperTrade, BtcPaperBotState, KxBtcSnapshot
from backend.btcmarket import candles as candles_mod
from backend.btcmarket import kalshi_poll
from backend.btcmarket import orderbook
from backend.btcmarket import derivatives
from backend.btcmarket.fees import trade_fee
from backend.btcmarket.model import gbm_yes_probability
from backend.core.btc_model_training import (
    MODEL_PATH, FEATURE_COLUMNS, rolling_realized_vol_per_second, quote_staleness_now,
    VOL_WINDOW_MINUTES, MIN_VOL_SAMPLES, PATH_EFFICIENCY_WINDOW,
    technical_indicators, get_live_settlement_bias, apply_calibration,
    apply_live_recalibration,
)
from backend.scanner.kalshi_api import KalshiScannerAPI

logger = logging.getLogger("trading_bot")
_api = KalshiScannerAPI()

_model_cache = {"model": None, "mtime": None}


def _load_model():
    import os
    if not os.path.exists(MODEL_PATH):
        return None
    mtime = os.path.getmtime(MODEL_PATH)
    if _model_cache["model"] is not None and _model_cache["mtime"] == mtime:
        return _model_cache["model"]
    import xgboost as xgb
    model = xgb.XGBClassifier()
    model.load_model(MODEL_PATH)
    _model_cache["model"] = model
    _model_cache["mtime"] = mtime
    return model


def _parse_kalshi_time(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)


async def _build_live_features() -> Optional[dict]:
    """Compute the exact FEATURE_COLUMNS set for the currently open window, for live inference."""
    market = await kalshi_poll.get_current_market()
    if not market:
        return None

    ticker = market.get("ticker", "")
    floor_strike = market.get("floor_strike")
    open_time = _parse_kalshi_time(market.get("open_time"))
    close_time = _parse_kalshi_time(market.get("close_time"))
    if not floor_strike or not open_time or not close_time:
        return None

    now = datetime.utcnow()
    seconds_remaining = (close_time - now).total_seconds()
    if seconds_remaining <= 0:
        return None
    total_window = max((close_time - open_time).total_seconds(), 1.0)
    elapsed_fraction = 1.0 - seconds_remaining / total_window

    await candles_mod.ensure_recent_candles(60, timedelta(minutes=VOL_WINDOW_MINUTES + 10))
    candles_1m = candles_mod.get_cached_candles(60, timedelta(minutes=VOL_WINDOW_MINUTES + 10))
    if len(candles_1m) < MIN_VOL_SAMPLES + 1:
        return None

    closes = [c["close"] for c in candles_1m]
    vols = [c["volume"] for c in candles_1m]

    # Same shared function training uses, on a pandas Series built from these
    # same cached candles -- guarantees identical formulas, not a hand-written
    # duplicate (see technical_indicators()'s docstring for why that matters).
    ti = technical_indicators(pd.Series(closes), pd.Series(vols))
    ti_last = ti.iloc[-1]
    if ti_last.isnull().any():
        return None  # not enough candle history for indicator burn-in yet -- same drop-row behavior as training
    btc_price = closes[-1]

    # Price used for distance_log/gbm_prob specifically -- distance_log is the
    # model's single most important feature (~59% importance), so how this is
    # computed matters far more than for a low-importance feature.
    #
    # Two fixes layered here, in order:
    # (1) Round 17/18: closes[-1] (the cached 1-minute candle's close) can lag
    #     the true current price -- caught via a $30/one-full-minute-stale
    #     discrepancy, confirmed the in-memory 1Hz tick buffer is fresher on
    #     nearly every call. Superseded by (2) below, which also uses the tick
    #     buffer but averages instead of taking the single latest point.
    # (2) Round 32/33: Kalshi actually settles KXBTC15M against a 60-second
    #     TWAP of BRTI, not an instantaneous price -- a structural mismatch
    #     with using any single instantaneous tick, however fresh. Validated
    #     via a real historical backtest (105k rows) that a 60s trailing
    #     average improves Brier by ~0.002-0.003 even restricted to realistic
    #     early-window decision points (see prior_findings.md). Must match
    #     training's build_feature_frame() exactly, which computes the same
    #     60s trailing average from its own snapshot history.
    twap_price = candles_mod.get_trailing_average_price()
    if twap_price is not None:
        if abs(twap_price - btc_price) > 5.0:
            logger.warning(
                f"BTC live feature staleness: candle close {btc_price} vs "
                f"60s TWAP {twap_price:.2f} (delta ${twap_price - btc_price:+.2f}) "
                f"-- using the TWAP for distance_log/gbm_prob."
            )
        btc_price = twap_price

    # Must match training's feature exactly (see rolling_realized_vol_per_second's
    # docstring) -- if there isn't enough real history for it, skip the trade
    # rather than fabricate a fallback value the model never trained on.
    sigma = rolling_realized_vol_per_second(closes)
    if sigma is None:
        return None
    gbm_prob = gbm_yes_probability(btc_price, floor_strike, seconds_remaining, sigma)

    m1_ret_1m = math.log(closes[-1] / closes[-2]) if len(closes) >= 2 and closes[-2] > 0 else 0.0
    m1_ret_5m = math.log(closes[-1] / closes[-6]) if len(closes) >= 6 and closes[-6] > 0 else 0.0
    m1_vol_sum_5m = sum(vols[-5:])
    m1_vol_sum_15m = sum(vols[-15:])

    candles_1h = candles_mod.get_cached_candles(3600, timedelta(hours=6))
    if len(candles_1h) >= 2 and candles_1h[-2]["close"] > 0:
        h1_log_return = math.log(candles_1h[-1]["close"] / candles_1h[-2]["close"])
    else:
        h1_log_return = 0.0
    if candles_1h and candles_1h[-1]["close"] > 0:
        h1_range_pct = (candles_1h[-1]["high"] - candles_1h[-1]["low"]) / candles_1h[-1]["close"]
    else:
        h1_range_pct = 0.0

    # Must match training's row-based rolling window exactly (see
    # PATH_EFFICIENCY_WINDOW / _candle_features_1m in btc_model_training.py).
    if len(closes) >= PATH_EFFICIENCY_WINDOW + 1 and closes[-PATH_EFFICIENCY_WINDOW - 1] > 0:
        window_rets = [
            math.log(closes[i] / closes[i - 1])
            for i in range(len(closes) - PATH_EFFICIENCY_WINDOW, len(closes))
            if closes[i - 1] > 0 and closes[i] > 0
        ]
        total_movement = sum(abs(r) for r in window_rets)
        net_displacement = abs(math.log(closes[-1] / closes[-PATH_EFFICIENCY_WINDOW - 1]))
        path_efficiency_15m = min(1.0, net_displacement / total_movement) if total_movement > 0 else 0.0
    else:
        path_efficiency_15m = 0.0

    now_for_hour = datetime.utcnow()
    hour_frac = now_for_hour.hour + now_for_hour.minute / 60.0
    hour_sin = math.sin(2 * math.pi * hour_frac / 24.0)
    hour_cos = math.cos(2 * math.pi * hour_frac / 24.0)

    settlement_bias = await get_live_settlement_bias()
    if settlement_bias is None:
        return None  # not enough recent settlement history yet -- same drop-row behavior as any other insufficient-history feature

    # The model was TRAINED on yes_mid computed from Kalshi's market-summary
    # yes_bid_dollars/yes_ask_dollars fields (via kalshi_poll.poll_and_record),
    # so the model's "yes_mid" FEATURE stays sourced the same way here --
    # feeding it a different distribution now would reintroduce the same
    # train/serve skew that realized_vol_per_second had. But we discovered
    # (2026-09-10) that this summary field can be frozen for several seconds
    # while the real orderbook keeps moving -- so it's unsuitable as the
    # actual trade/edge decision reference. Fix: derive a SEPARATE, fresh
    # "live_yes_mid" from the real orderbook for the entry decision and
    # quoted_price, while the model's own input feature is left alone.
    yes_bid = float(market.get("yes_bid_dollars", 0) or 0)
    yes_ask = float(market.get("yes_ask_dollars", 0) or 0)
    if yes_bid <= 0 or yes_ask <= 0:
        return None
    yes_mid = (yes_bid + yes_ask) / 2

    try:
        ladders = await orderbook.get_ask_ladders(ticker)
        live_best_yes_ask = ladders["yes_asks"][0][0] if ladders["yes_asks"] else None
        live_best_no_ask = ladders["no_asks"][0][0] if ladders["no_asks"] else None
    except Exception as e:
        logger.debug(f"Live orderbook fetch failed for {ticker}: {e}")
        live_best_yes_ask = live_best_no_ask = None

    if live_best_yes_ask is not None and live_best_no_ask is not None:
        live_yes_bid = 1.0 - live_best_no_ask
        live_yes_mid = (live_yes_bid + live_best_yes_ask) / 2
    else:
        live_yes_mid = yes_mid  # fall back to the summary quote if the book fetch failed

    # Same source (KxBtcSnapshot) and same definition as training's
    # add_quote_staleness_features -- how long has this quote been unchanged,
    # and how far has BTC moved since it last repriced.
    db = SessionLocal()
    try:
        snap_rows = (
            db.query(KxBtcSnapshot)
            .filter(KxBtcSnapshot.ticker == ticker)
            .order_by(KxBtcSnapshot.timestamp.asc())
            .all()
        )
        history = [
            {"timestamp": r.timestamp, "yes_bid": r.yes_bid, "yes_ask": r.yes_ask, "btc_price": r.btc_price}
            for r in snap_rows if r.yes_bid is not None and r.yes_ask is not None and r.btc_price is not None
        ]
    finally:
        db.close()
    history.append({"timestamp": now, "yes_bid": yes_bid, "yes_ask": yes_ask, "btc_price": btc_price})
    quote_age_seconds, btc_move_since_quote = quote_staleness_now(history)

    features = {
        "seconds_remaining": seconds_remaining,
        "elapsed_fraction": elapsed_fraction,
        "distance_log": math.log(btc_price / floor_strike),
        "yes_mid": yes_mid,
        "spread": yes_ask - yes_bid,
        "gbm_prob": gbm_prob,
        "realized_vol_per_second": sigma,
        "m1_ret_1m": m1_ret_1m,
        "m1_ret_5m": m1_ret_5m,
        "m1_vol_sum_5m": m1_vol_sum_5m,
        "m1_vol_sum_15m": m1_vol_sum_15m,
        "h1_log_return": h1_log_return,
        "h1_range_pct": h1_range_pct,
        "quote_age_seconds": quote_age_seconds,
        "btc_move_since_quote": btc_move_since_quote,
        "path_efficiency_15m": path_efficiency_15m,
        "hour_sin": hour_sin,
        "hour_cos": hour_cos,
        "rsi_14": float(ti_last["rsi_14"]),
        "macd_histogram_pct": float(ti_last["macd_histogram_pct"]),
        "bollinger_pct_b": float(ti_last["bollinger_pct_b"]),
        "vwap_deviation_60m": float(ti_last["vwap_deviation_60m"]),
        "settlement_bias": settlement_bias,
    }
    return {
        "ticker": ticker,
        "event_ticker": market.get("event_ticker", ""),
        "open_time": open_time,
        "close_time": close_time,
        "yes_mid": yes_mid,            # model's own input feature (matches training source)
        "live_yes_mid": live_yes_mid,  # fresh orderbook-derived reference for the actual trade decision
        "features": features,
    }


def _eligible_after_seconds(ticker: str) -> float:
    """
    Deterministic per-window pseudo-random delay before this window is even
    looked at for a trade. Without this, entries cluster in the first ~30s of
    every window: edges are structurally biggest right at open (volatility
    scales with sqrt(seconds_remaining), so the same distance-to-strike gives
    the most room to disagree with the market when the most time is left, and
    that room shrinks as the window runs out). Hashing the ticker gives each
    window an unpredictable-but-stable eligibility time so entries end up
    spread across [MIN_WINDOW_AGE, MAX_ENTRY_DELAY] instead of all at t=0.
    """
    span = max(settings.BTC_PAPER_MAX_ENTRY_DELAY_SECONDS - settings.BTC_PAPER_MIN_WINDOW_AGE_SECONDS, 1)
    offset = int(hashlib.md5(ticker.encode()).hexdigest(), 16) % span
    return settings.BTC_PAPER_MIN_WINDOW_AGE_SECONDS + offset


def _calculate_edge(model_prob: float, market_prob: float):
    if model_prob > market_prob:
        return model_prob - market_prob, "yes"
    return market_prob - model_prob, "no"


def _kelly_size(model_prob: float, entry_price: float, bankroll: float) -> float:
    if entry_price <= 0 or entry_price >= 1:
        return 0.0
    b = (1.0 - entry_price) / entry_price
    q = 1.0 - model_prob
    kelly_full = max(0.0, (model_prob * b - q) / b) if b > 0 else 0.0
    raw = kelly_full * settings.KELLY_FRACTION * bankroll
    return min(raw, settings.BTC_PAPER_MAX_TRADE_SIZE)


async def generate_paper_trade() -> Optional[dict]:
    """If the current window shows a real edge (per the trained model) and hasn't been traded yet, journal a simulated trade."""
    model = _load_model()
    if model is None:
        return None  # no trained artifact yet -- nothing to simulate with

    ctx = await _build_live_features()
    if not ctx:
        return None

    # Reverted 2026-09-09: backtesting this same gate against yesterday's data
    # showed it performs meaningfully WORSE than trading the first qualifying
    # edge whenever it appears (see backend/core/btc_entry_timing_backtest.py) --
    # 46% win rate / +$442 vs 68% / +$1,103 on immediate entry, because edges are
    # structurally biggest right at window open and decay as the window runs out,
    # so delaying mostly captures weaker leftover edges instead of "different but
    # equally good" ones. _eligible_after_seconds() is kept for further
    # experiments (e.g. via the backtest script) but is no longer applied live.
    db = SessionLocal()
    try:
        if db.query(BtcPaperTrade).filter(BtcPaperTrade.ticker == ctx["ticker"]).first():
            return None

        X = np.array([[ctx["features"][c] for c in FEATURE_COLUMNS]])
        raw_model_prob = float(model.predict_proba(X)[:, 1][0])
        # Platt-scaling correction fit on the walk-forward OOS predictions
        # every retrain (see _fit_platt_scaling's docstring) -- XGBoost's raw
        # output is known to be systematically miscalibrated; this is a no-op
        # until the first retrain saves calibration.json.
        model_prob = apply_calibration(raw_model_prob)
        # Secondary correction fit on live paper-trading outcomes specifically
        # (see fit_live_recalibration's docstring) -- a no-op until enough
        # live trades have accumulated to fit it (LIVE_RECALIBRATION_MIN_SAMPLES).
        # Applied on top of, not instead of, the backtest-fit calibration above.
        model_prob = apply_live_recalibration(model_prob)
        # live_yes_mid (fresh orderbook), NOT ctx["yes_mid"] (the model's own
        # input feature, sourced from Kalshi's summary quote to match training)
        # -- discovered 2026-09-10 that the summary quote can sit frozen for
        # several seconds while the real book moves, so it's the wrong
        # reference for an actual trade decision even though it's the right
        # one for the model's input feature.
        market_prob = ctx["live_yes_mid"]

        edge, direction = _calculate_edge(model_prob, market_prob)
        quoted_price = market_prob if direction == "yes" else 1.0 - market_prob

        if abs(edge) < settings.MIN_EDGE_THRESHOLD or quoted_price > settings.MAX_ENTRY_PRICE:
            return None

        state = db.query(BtcPaperBotState).first()
        if not state:
            state = BtcPaperBotState(bankroll=settings.BTC_PAPER_INITIAL_BANKROLL)
            db.add(state)
            db.flush()

        target_size = _kelly_size(model_prob, quoted_price, state.bankroll)
        if target_size < 1.0:
            return None

        # Fetch the CURRENT live book right before executing (this is where real
        # latency shows up -- whatever moved between the original decision above
        # and this fresh call is reflected here) and simulate the actual
        # volume-weighted fill for target_size dollars, instead of assuming the
        # naive top-of-book quote holds at any size.
        fill = await orderbook.get_realistic_fill(ctx["ticker"], direction, target_size)
        if fill is None or fill["avg_price"] is None:
            return None

        entry_price = fill["avg_price"]
        size = fill["filled_notional"]  # may be less than target_size if the book couldn't fully absorb it
        if size < 1.0:
            return None

        # Re-validate against the REAL fill price, not the stale quote -- if
        # walking the book made this trade worse than our threshold, don't take
        # it just because it looked good a moment ago.
        yes_equiv_fill = entry_price if direction == "yes" else 1.0 - entry_price
        edge_realized = (model_prob - yes_equiv_fill) if direction == "yes" else (yes_equiv_fill - model_prob)
        if edge_realized < settings.MIN_EDGE_THRESHOLD or entry_price > settings.MAX_ENTRY_PRICE:
            return None

        contracts = fill["contracts"]
        fee = trade_fee(contracts, entry_price)

        # Informational only -- NOT fed to the model yet, just recorded so
        # enough history accumulates to eventually add them as real features.
        imbalance = await orderbook.get_book_imbalance(ctx["ticker"])
        momentum = candles_mod.get_short_momentum()
        taker_ratio = await orderbook.get_taker_buy_sell_ratio(ctx["ticker"])
        funding_rate = await derivatives.get_funding_rate()

        db.add(BtcPaperTrade(
            ticker=ctx["ticker"], event_ticker=ctx["event_ticker"],
            entry_time=datetime.utcnow(), close_time=ctx["close_time"],
            direction=direction, entry_price=entry_price, quoted_price=quoted_price, size=size,
            model_probability=model_prob, market_probability=market_prob, edge=edge_realized,
            fee=fee, features_json=json.dumps(ctx["features"]),
            book_imbalance=imbalance, mom_5s=momentum["mom_5s"], mom_15s=momentum["mom_15s"], mom_30s=momentum["mom_30s"],
            taker_buy_sell_ratio=taker_ratio, funding_rate=funding_rate,
        ))
        db.commit()

        slippage = entry_price - quoted_price if direction == "yes" else quoted_price - entry_price
        logger.info(
            f"PAPER TRADE: {ctx['ticker']} {direction.upper()} ${size:.2f} @ {entry_price:.0%} "
            f"(quoted {quoted_price:.0%}, slippage {slippage:+.2%}) | "
            f"model {model_prob:.0%} vs market {market_prob:.0%} | edge {edge_realized:+.1%}"
        )
        return {
            "ticker": ctx["ticker"], "direction": direction, "size": round(size, 2),
            "entry_price": round(entry_price, 4), "quoted_price": round(quoted_price, 4),
            "edge": round(edge_realized, 4),
        }
    finally:
        db.close()


async def settle_paper_trades() -> list:
    """Check pending paper trades past close_time and settle them against Kalshi's actual result."""
    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(seconds=5)
        pending = db.query(BtcPaperTrade).filter(
            BtcPaperTrade.settled == False, BtcPaperTrade.close_time <= cutoff,
        ).all()
        if not pending:
            return []

        settled = []
        for trade in pending:
            market = await _api.get_market(trade.ticker, use_cache=False)
            if not market:
                continue
            result = market.get("result", "")
            if result not in ("yes", "no"):
                continue

            settlement_value = 1.0 if result == "yes" else 0.0
            if trade.direction == "yes":
                gross_pnl = trade.size * (1.0 - trade.entry_price) if settlement_value == 1.0 else -trade.size * trade.entry_price
            else:
                gross_pnl = trade.size * (1.0 - trade.entry_price) if settlement_value == 0.0 else -trade.size * trade.entry_price
            pnl = gross_pnl - (trade.fee or 0.0)  # fee predates trades recorded before 2026-09-10; None -> 0

            trade.settled = True
            trade.settlement_value = settlement_value
            trade.pnl = round(pnl, 2)
            trade.result = "win" if pnl > 0 else ("loss" if pnl < 0 else "push")
            settled.append(trade)

        if settled:
            state = db.query(BtcPaperBotState).first()
            if not state:
                state = BtcPaperBotState(bankroll=settings.BTC_PAPER_INITIAL_BANKROLL)
                db.add(state)
                db.flush()
            for t in settled:
                state.total_trades += 1
                state.total_pnl += t.pnl
                state.bankroll += t.pnl
                if t.result == "win":
                    state.winning_trades += 1
            state.last_run = datetime.utcnow()

        # Snapshot plain dicts before commit/close expires these ORM objects --
        # accessing attributes on a detached instance after the session closes
        # raises DetachedInstanceError, which was previously silently swallowed
        # by the caller's broad except-and-log-at-debug.
        result = [{"ticker": t.ticker, "result": t.result, "pnl": t.pnl} for t in settled]
        db.commit()
        return result
    finally:
        db.close()


def paper_trading_report(lookback_hours: Optional[float] = None) -> dict:
    """Performance snapshot: win rate, PnL, and probability calibration (Brier score) of settled paper trades."""
    db = SessionLocal()
    try:
        q = db.query(BtcPaperTrade).filter(BtcPaperTrade.settled == True)
        if lookback_hours is not None:
            since = datetime.utcnow() - timedelta(hours=lookback_hours)
            q = q.filter(BtcPaperTrade.entry_time >= since)
        trades = q.all()
        pending = db.query(BtcPaperTrade).filter(BtcPaperTrade.settled == False).count()
        state = db.query(BtcPaperBotState).first()

        n = len(trades)
        wins = sum(1 for t in trades if t.result == "win")
        losses = sum(1 for t in trades if t.result == "loss")
        total_pnl = sum(t.pnl for t in trades if t.pnl is not None)
        avg_edge = sum(abs(t.edge) for t in trades) / n if n else 0.0

        brier_terms = [(t.model_probability - t.settlement_value) ** 2 for t in trades]
        brier = sum(brier_terms) / n if n else None
        # Standard error of the mean squared error, from the per-trade variance --
        # gives an actual confidence interval instead of an arbitrary trade-count
        # threshold for "is this Brier number signal or noise yet."
        brier_se = None
        if n and n >= 2:
            mean_term = brier
            var = sum((bt - mean_term) ** 2 for bt in brier_terms) / (n - 1)
            brier_se = (var / n) ** 0.5

        return {
            "window_hours": lookback_hours,
            "trades_settled": n,
            "trades_pending": pending,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / n, 4) if n else None,
            "total_pnl": round(total_pnl, 2),
            "avg_pnl_per_trade": round(total_pnl / n, 2) if n else None,
            "avg_edge": round(avg_edge, 4),
            "model_brier": round(brier, 5) if brier is not None else None,
            "model_brier_se": round(brier_se, 5) if brier_se is not None else None,
            "model_brier_ci95": (
                [round(brier - 1.96 * brier_se, 5), round(brier + 1.96 * brier_se, 5)]
                if brier_se is not None else None
            ),
            "bankroll": round(state.bankroll, 2) if state else settings.BTC_PAPER_INITIAL_BANKROLL,
            "lifetime_trades": state.total_trades if state else 0,
            "lifetime_pnl": round(state.total_pnl, 2) if state else 0.0,
        }
    finally:
        db.close()
