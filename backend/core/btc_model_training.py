"""
Offline training pipeline for a learned KXBTC15M probability model.

Trains an XGBoost classifier on our own recorded history: every ~1-2s
orderbook snapshot (backend.models.database.KxBtcSnapshot) we've captured for
past 15-minute windows, joined against BTC 1-minute/1-hour OHLCV and labeled
with the window's actual Kalshi settlement result ("yes"/"no").

Purpose: quantify where the *analytical* GBM baseline (backend/btcmarket/model.py)
and the live orderbook price diverge from the true outcome -- i.e. find the
market's mispricing, not just replicate it. The GBM probability is included
as an input feature so the trees only have to learn the *residual* on top of
domain knowledge, rather than rediscovering the shape of a digital option from
scratch on a fairly small (~22 days) history.

This is research-only: it trains, evaluates, and saves a model artifact. It
does NOT touch the live signal path in backend/btcmarket/model.py.
"""
import json
import logging
import math
import os
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from scipy.stats import norm

from backend.config import settings
from backend.models.database import SessionLocal, KxBtcSnapshot
from backend.btcmarket import candles as candles_mod
from backend.scanner.kalshi_api import KalshiScannerAPI

logger = logging.getLogger("trading_bot")

ARTIFACT_DIR = os.path.join(os.path.dirname(__file__), "..", "btcmarket", "artifacts")
MODEL_PATH = os.path.join(ARTIFACT_DIR, "kxbtc15m_xgb.json")
REPORT_PATH = os.path.join(ARTIFACT_DIR, "training_report.json")
CALIBRATION_PATH = os.path.join(ARTIFACT_DIR, "calibration.json")

VOL_WINDOW_MINUTES = 180
MIN_VOL_SAMPLES = 20

FEATURE_COLUMNS = [
    "seconds_remaining",
    "elapsed_fraction",
    "distance_log",
    "yes_mid",
    "spread",
    "gbm_prob",
    "realized_vol_per_second",
    "m1_ret_1m",
    "m1_ret_5m",
    "m1_vol_sum_5m",
    "m1_vol_sum_15m",
    "h1_log_return",
    "h1_range_pct",
    "quote_age_seconds",
    "btc_move_since_quote",
    "path_efficiency_15m",
    "hour_sin",
    "hour_cos",
    "rsi_14",
    "macd_histogram_pct",
    "bollinger_pct_b",
    "vwap_deviation_60m",
    "settlement_bias",
]

SETTLEMENT_BIAS_WINDOW = 20  # trailing settled windows

PATH_EFFICIENCY_WINDOW = 15  # minutes

_api = KalshiScannerAPI()


# ---------------------------------------------------------------------------
# Labels: actual settlement result per ticker
# ---------------------------------------------------------------------------

async def _fetch_settlement_labels(min_close_ts: int, max_close_ts: int) -> Dict[str, str]:
    labels: Dict[str, str] = {}
    cursor = None
    for _ in range(50):
        data = await _api.get_markets(
            series_ticker=settings.KXBTC15M_SERIES_TICKER,
            status="settled",
            min_close_ts=min_close_ts,
            max_close_ts=max_close_ts,
            limit=200,
            cursor=cursor,
        )
        for m in data.get("markets", []):
            result = m.get("result", "")
            if result in ("yes", "no"):
                labels[m.get("ticker", "")] = result
        cursor = data.get("cursor")
        if not cursor or not data.get("markets"):
            break
    return labels


async def get_live_settlement_bias(window: int = SETTLEMENT_BIAS_WINDOW) -> Optional[float]:
    """
    Live counterpart to build_feature_frame's settlement_bias -- trailing
    YES win-rate over the most recently settled `window` windows, as of now.
    Same definition, causal by construction (only ever looks at already-
    settled markets, i.e. strictly before the current open window).
    """
    now = datetime.utcnow()
    min_ts = int((now - timedelta(hours=12)).timestamp())
    max_ts = int(now.timestamp())
    markets: List[dict] = []
    cursor = None
    for _ in range(10):
        data = await _api.get_markets(
            series_ticker=settings.KXBTC15M_SERIES_TICKER, status="settled",
            min_close_ts=min_ts, max_close_ts=max_ts, limit=200, cursor=cursor,
        )
        markets.extend(data.get("markets", []))
        cursor = data.get("cursor")
        if not cursor or not data.get("markets"):
            break

    settled = [(m.get("close_time", ""), m.get("result", "")) for m in markets if m.get("result") in ("yes", "no")]
    settled.sort(key=lambda x: x[0], reverse=True)
    recent = settled[:window]
    if len(recent) < 5:
        return None
    return sum(1.0 for _, r in recent if r == "yes") / len(recent)


# ---------------------------------------------------------------------------
# Historical 1-minute candle backfill (the live poller only ever fills the
# gap since its last cached candle, so older windows need an explicit fetch)
# ---------------------------------------------------------------------------

async def _ensure_historical_1m_candles(start: datetime, end: datetime) -> int:
    db = SessionLocal()
    try:
        fetched = await candles_mod.fetch_coinbase_candles(start, end, 60)
        return candles_mod._upsert_candles(db, fetched, 60)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Feature frame construction
# ---------------------------------------------------------------------------

def rolling_realized_vol_per_second(closes: List[float]) -> Optional[float]:
    """
    THE canonical realized-vol estimator for the FEATURE_COLUMNS the classifier
    was trained on. Mirrors pandas' `.rolling(VOL_WINDOW_MINUTES,
    min_periods=MIN_VOL_SAMPLES).std()` exactly (row-count window, ddof=1
    sample std) so live inference (backend/core/btc_paper_trading.py) computes
    the identical feature it was trained on, for the latest candle in `closes`.

    Do NOT substitute backend.btcmarket.model.realized_volatility_per_second
    here -- that's a different, time-windowed estimator (with its own fallback
    constant) used only by the analytical GBM baseline behind /api/btc/signal.
    Mixing the two was a real train/serve skew bug: the classifier saw this
    row-count feature during training but was fed the time-windowed one live.
    """
    log_rets = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i - 1] > 0 and closes[i] > 0
    ]
    window = log_rets[-VOL_WINDOW_MINUTES:]
    if len(window) < MIN_VOL_SAMPLES:
        return None
    mean = sum(window) / len(window)
    variance = sum((r - mean) ** 2 for r in window) / (len(window) - 1)
    return math.sqrt(variance) / math.sqrt(60)


def _quote_staleness_for_group(g: pd.DataFrame) -> pd.DataFrame:
    """
    For one ticker's snapshot history (ascending by timestamp), compute per-row:
      quote_age_seconds     -- how long yes_bid/yes_ask have been at their
                                current values (0 if this row IS the change)
      btc_move_since_quote  -- ln(current btc_price / btc_price when the book
                                last repriced)

    This targets the same "Kalshi's quote lags spot" mechanism a couple of the
    public KXBTC15M bots on GitHub explicitly cite as their edge (book takes a
    few seconds to catch up to a BTC move) -- distance/vol/time alone doesn't
    capture staleness, only comparing the current quote to when it last changed does.
    """
    g = g.sort_values("timestamp")
    ts = g["timestamp"].tolist()
    bid = g["yes_bid"].tolist()
    ask = g["yes_ask"].tolist()
    price = g["btc_price"].tolist()

    ages, moves = [], []
    change_idx = 0
    for i in range(len(g)):
        if i > 0 and (bid[i] != bid[i - 1] or ask[i] != ask[i - 1]):
            change_idx = i
        ages.append((ts[i] - ts[change_idx]).total_seconds())
        change_price = price[change_idx]
        moves.append(math.log(price[i] / change_price) if change_price and change_price > 0 and price[i] and price[i] > 0 else 0.0)

    return pd.DataFrame({"quote_age_seconds": ages, "btc_move_since_quote": moves}, index=g.index)


def add_quote_staleness_features(snap_df: pd.DataFrame) -> pd.DataFrame:
    parts = [_quote_staleness_for_group(g) for _, g in snap_df.groupby("ticker", sort=False)]
    return snap_df.join(pd.concat(parts))


def quote_staleness_now(rows: List[dict]) -> "tuple[float, float]":
    """
    Live-inference counterpart to _quote_staleness_for_group -- same definition,
    computed for just the LAST row of `rows` (ascending {"timestamp","yes_bid",
    "yes_ask","btc_price"} dicts for one ticker, the last one standing in for
    "right now"). Must stay defined identically to the training version above
    or this reintroduces the same train/serve skew the realized-vol feature had.
    """
    if not rows:
        return 0.0, 0.0
    last = rows[-1]
    last_bid, last_ask = last["yes_bid"], last["yes_ask"]

    change_idx = len(rows) - 1
    i = len(rows) - 2
    while i >= 0 and rows[i]["yes_bid"] == last_bid and rows[i]["yes_ask"] == last_ask:
        change_idx = i
        i -= 1

    age = max((last["timestamp"] - rows[change_idx]["timestamp"]).total_seconds(), 0.0)
    change_price = rows[change_idx]["btc_price"]
    now_price = last["btc_price"]
    move = math.log(now_price / change_price) if change_price and change_price > 0 and now_price and now_price > 0 else 0.0
    return age, move


def technical_indicators(close: pd.Series, volume: pd.Series) -> pd.DataFrame:
    """
    RSI(14), MACD histogram (as % of price), Bollinger %B(20), rolling
    60-minute VWAP deviation -- classical technical indicators, motivated by
    hamad-khawaja/kalshi-trading-bot's feature set (prior_findings.md).

    Deliberately a SHARED function called identically by both the training
    vectorized path (on the full historical series) and live inference (on a
    short pandas Series built from cached candles) -- two hand-written
    implementations of "the same" formula in different files is exactly what
    caused the realized-vol train/serve skew bug earlier in this project.
    One function, two callers, same pandas ops either way.
    """
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(14, min_periods=14).mean()
    avg_loss = loss.rolling(14, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_14 = 100 - 100 / (1 + rs)
    rsi_14 = rsi_14.where(avg_loss != 0, np.where(avg_gain > 0, 100.0, 50.0))
    rsi_14 = rsi_14.where(avg_gain.notna())  # preserve NaN during burn-in, not just where avg_loss==0

    ema12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = close.ewm(span=26, adjust=False, min_periods=26).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False, min_periods=9).mean()
    macd_histogram_pct = (macd_line - signal_line) / close

    sma20 = close.rolling(20, min_periods=20).mean()
    std20 = close.rolling(20, min_periods=20).std()
    upper = sma20 + 2 * std20
    lower = sma20 - 2 * std20
    bollinger_pct_b = (close - lower) / (upper - lower).replace(0, np.nan)

    pv = close * volume
    rolling_pv = pv.rolling(60, min_periods=20).sum()
    rolling_vol = volume.rolling(60, min_periods=20).sum()
    vwap60 = rolling_pv / rolling_vol.replace(0, np.nan)
    vwap_deviation_60m = (close - vwap60) / vwap60

    return pd.DataFrame({
        "rsi_14": rsi_14,
        "macd_histogram_pct": macd_histogram_pct,
        "bollinger_pct_b": bollinger_pct_b,
        "vwap_deviation_60m": vwap_deviation_60m,
    }, index=close.index)


def _candle_features_1m(lookback: timedelta) -> pd.DataFrame:
    rows = candles_mod.get_cached_candles(60, lookback)
    df = pd.DataFrame(rows)
    df["open_time"] = pd.to_datetime(df["time"], unit="s")
    df = df.sort_values("open_time").reset_index(drop=True)

    log_return = np.log(df["close"] / df["close"].shift(1))
    df["m1_ret_1m"] = log_return
    df["m1_ret_5m"] = np.log(df["close"] / df["close"].shift(5))
    df["m1_vol_sum_5m"] = df["volume"].rolling(5, min_periods=1).sum()
    df["m1_vol_sum_15m"] = df["volume"].rolling(15, min_periods=1).sum()
    # Same definition as rolling_realized_vol_per_second() above, vectorized
    # for the whole historical series instead of one scalar at a time.
    df["realized_vol_per_min"] = log_return.rolling(VOL_WINDOW_MINUTES, min_periods=MIN_VOL_SAMPLES).std()
    df["realized_vol_per_second"] = df["realized_vol_per_min"] / math.sqrt(60)

    # Path efficiency (net displacement / total movement, over PATH_EFFICIENCY_WINDOW
    # minutes): distinct from realized vol -- two windows with identical vol can have
    # very different efficiency (one trending straight through, one whipsawing back
    # and forth). Motivated by hamad-khawaja/kalshi-trading-bot's feature set, see
    # prior_findings.md. In [0, 1]; NaN (dropped downstream) until enough history exists.
    net_log_displacement = (np.log(df["close"]) - np.log(df["close"].shift(PATH_EFFICIENCY_WINDOW))).abs()
    total_movement = log_return.abs().rolling(PATH_EFFICIENCY_WINDOW, min_periods=PATH_EFFICIENCY_WINDOW).sum()
    df["path_efficiency_15m"] = (net_log_displacement / total_movement.replace(0, np.nan)).clip(0, 1)

    ti = technical_indicators(df["close"], df["volume"])
    df = pd.concat([df, ti], axis=1)

    return df[["open_time", "close", "realized_vol_per_second",
               "m1_ret_1m", "m1_ret_5m", "m1_vol_sum_5m", "m1_vol_sum_15m", "path_efficiency_15m",
               "rsi_14", "macd_histogram_pct", "bollinger_pct_b", "vwap_deviation_60m"]]


def _candle_features_1h(lookback: timedelta) -> pd.DataFrame:
    rows = candles_mod.get_cached_candles(3600, lookback)
    df = pd.DataFrame(rows)
    df["open_time"] = pd.to_datetime(df["time"], unit="s")
    df = df.sort_values("open_time").reset_index(drop=True)

    df["h1_log_return"] = np.log(df["close"] / df["close"].shift(1))
    df["h1_range_pct"] = (df["high"] - df["low"]) / df["close"]

    return df[["open_time", "h1_log_return", "h1_range_pct"]]


def _load_snapshots(since: datetime) -> pd.DataFrame:
    db = SessionLocal()
    try:
        rows = (
            db.query(KxBtcSnapshot)
            .filter(KxBtcSnapshot.timestamp >= since)
            .order_by(KxBtcSnapshot.timestamp.asc())
            .all()
        )
        return pd.DataFrame([{
            "ticker": r.ticker,
            "timestamp": r.timestamp,
            "floor_strike": r.floor_strike,
            "open_time": r.open_time,
            "close_time": r.close_time,
            "yes_bid": r.yes_bid,
            "yes_ask": r.yes_ask,
            "btc_price": r.btc_price,
        } for r in rows])
    finally:
        db.close()


async def build_feature_frame(lookback_days: int = 60) -> pd.DataFrame:
    """Join recorded orderbook snapshots with candle features and settlement labels."""
    since = datetime.utcnow() - timedelta(days=lookback_days)

    snap = _load_snapshots(since)
    if snap.empty:
        return snap

    earliest = snap["timestamp"].min().to_pydatetime()
    # Backfill any 1-minute candle history missing before the live poller's cache window.
    await _ensure_historical_1m_candles(earliest - timedelta(minutes=5), datetime.utcnow())

    df1m = _candle_features_1m(timedelta(days=lookback_days + 1))
    df1h = _candle_features_1h(timedelta(days=lookback_days + 1))

    snap = snap.dropna(subset=["floor_strike", "btc_price", "yes_bid", "yes_ask", "close_time", "open_time"])
    snap = snap[(snap["yes_bid"] > 0) & (snap["yes_ask"] > 0)]
    snap = add_quote_staleness_features(snap)

    # Rename each candle frame's own "open_time" (its merge key) to a unique
    # name BEFORE merging, rather than relying on pandas' _x/_y suffixing to
    # keep the window's real open_time distinguishable. Suffixing only fires
    # on an actual name collision, so the second merge_asof (which has no
    # collision against the *already-renamed* first-merge result) would
    # otherwise reintroduce a bare "open_time" column holding the HOURLY
    # candle's open time -- exactly the bug found 2026-09-11 (elapsed_fraction
    # computed from the wrong open_time, wrong in every historical training
    # run until fixed). Renaming up front eliminates the whole hazard class
    # instead of requiring every future column-add here to remember which
    # suffixed variant is the real one.
    df1m = df1m.rename(columns={"open_time": "candle_1m_open_time"})
    df1h = df1h.rename(columns={"open_time": "candle_1h_open_time"})

    merged = pd.merge_asof(
        snap.sort_values("timestamp"), df1m.sort_values("candle_1m_open_time"),
        left_on="timestamp", right_on="candle_1m_open_time", direction="backward",
        tolerance=pd.Timedelta(minutes=10),
    )
    merged = pd.merge_asof(
        merged.sort_values("timestamp"), df1h.sort_values("candle_1h_open_time"),
        left_on="timestamp", right_on="candle_1h_open_time", direction="backward",
        tolerance=pd.Timedelta(hours=2),
    )

    min_ts = int((since - timedelta(hours=1)).timestamp())
    max_ts = int(datetime.utcnow().timestamp())
    labels = await _fetch_settlement_labels(min_ts, max_ts)
    merged["label"] = merged["ticker"].map(labels)
    merged = merged.dropna(subset=["label", "realized_vol_per_second"])

    # Trailing YES win-rate over the SETTLEMENT_BIAS_WINDOW settled windows
    # immediately before this one (shift(1) excludes the window's own label --
    # causal by construction, since it only ever uses windows that closed
    # strictly earlier). Motivated by hamad-khawaja/kalshi-trading-bot's
    # settlement_bias feature, see prior_findings.md.
    ticker_close_order = merged.groupby("ticker")["close_time"].first().sort_values()
    label_by_ticker = merged.groupby("ticker")["label"].first().reindex(ticker_close_order.index)
    label_numeric = (label_by_ticker == "yes").astype(float)
    trailing_bias = label_numeric.rolling(SETTLEMENT_BIAS_WINDOW, min_periods=5).mean().shift(1)
    merged["settlement_bias"] = merged["ticker"].map(trailing_bias.to_dict())

    seconds_remaining = (merged["close_time"] - merged["timestamp"]).dt.total_seconds().clip(lower=1.0)
    # merged["open_time"] is unambiguously the window's own real open time now
    # that the candle frames' merge keys were renamed before merging (see
    # above) -- no more _x/_y suffix hazard here.
    total_window = (merged["close_time"] - merged["open_time"]).dt.total_seconds().clip(lower=1.0)
    elapsed_fraction = 1.0 - (seconds_remaining / total_window)

    sigma_h = merged["realized_vol_per_second"] * np.sqrt(seconds_remaining)
    # Kalshi settles KXBTC15M against a 60-second TWAP of BRTI, not an
    # instantaneous price -- using the raw instantaneous btc_price for
    # distance_log is a structural mismatch with what actually determines
    # settlement (see prior_findings.md Round 32/33). Validated via a
    # real historical backtest (105k rows, Round 33) that a 60s trailing
    # average improves Brier by ~0.002-0.003 even restricted to realistic
    # early-window decision points. Computed per-ticker (each KXBTC15M
    # window has its own independent snapshot series) using a time-based
    # rolling window on each ticker's own timestamp-sorted rows.
    def _rolling_twap(group: pd.DataFrame) -> pd.Series:
        # Returns a Series keyed by the group's ORIGINAL row index (not
        # timestamp) so pandas can align it straight back to `merged` via
        # groupby(...).apply(...) below -- avoids the row-duplication risk
        # of a merge on a (ticker, timestamp) key that isn't guaranteed
        # unique.
        s = group.set_index("timestamp")["btc_price"]
        rolled = s.rolling(f"{candles_mod.TWAP_WINDOW_SECONDS}s", min_periods=1).mean()
        return pd.Series(rolled.values, index=group.index)

    btc_price_twap = merged.groupby("ticker", group_keys=False)[["timestamp", "btc_price"]].apply(_rolling_twap)
    distance_log = np.log(btc_price_twap / merged["floor_strike"])
    z = (distance_log - 0.5 * sigma_h ** 2) / sigma_h
    gbm_prob = pd.Series(norm.cdf(z), index=merged.index).clip(0.01, 0.99)

    yes_mid = (merged["yes_bid"] + merged["yes_ask"]) / 2
    spread = merged["yes_ask"] - merged["yes_bid"]

    # Cyclical hour-of-day (UTC) -- crypto liquidity/behavior isn't uniform
    # across the day; sin/cos encoding avoids the 23:00->00:00 discontinuity
    # a raw hour number would create. Motivated by hamad-khawaja/kalshi-trading-bot,
    # see prior_findings.md.
    hour_frac = merged["timestamp"].dt.hour + merged["timestamp"].dt.minute / 60.0
    hour_sin = np.sin(2 * np.pi * hour_frac / 24.0)
    hour_cos = np.cos(2 * np.pi * hour_frac / 24.0)

    out = pd.DataFrame({
        "ticker": merged["ticker"],
        "timestamp": merged["timestamp"],
        "open_time": merged["open_time"],
        "close_time": merged["close_time"],
        "seconds_remaining": seconds_remaining,
        "elapsed_fraction": elapsed_fraction,
        "distance_log": distance_log,
        "yes_mid": yes_mid,
        "spread": spread,
        "gbm_prob": gbm_prob,
        "realized_vol_per_second": merged["realized_vol_per_second"],
        "m1_ret_1m": merged["m1_ret_1m"],
        "m1_ret_5m": merged["m1_ret_5m"],
        "m1_vol_sum_5m": merged["m1_vol_sum_5m"],
        "m1_vol_sum_15m": merged["m1_vol_sum_15m"],
        "h1_log_return": merged["h1_log_return"],
        "h1_range_pct": merged["h1_range_pct"],
        "quote_age_seconds": merged["quote_age_seconds"],
        "btc_move_since_quote": merged["btc_move_since_quote"],
        "path_efficiency_15m": merged["path_efficiency_15m"],
        "hour_sin": hour_sin,
        "hour_cos": hour_cos,
        "rsi_14": merged["rsi_14"],
        "macd_histogram_pct": merged["macd_histogram_pct"],
        "bollinger_pct_b": merged["bollinger_pct_b"],
        "vwap_deviation_60m": merged["vwap_deviation_60m"],
        "settlement_bias": merged["settlement_bias"],
        "label": (merged["label"] == "yes").astype(int),
    })
    return out.dropna(subset=FEATURE_COLUMNS)


# ---------------------------------------------------------------------------
# Train / evaluate
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Post-hoc probability calibration (Platt scaling)
#
# XGBoost's raw output is known to be systematically over/under-confident
# (its additive, error-correcting structure doesn't naturally produce
# well-calibrated probabilities -- this is a documented general property, not
# specific to this project). Fit on the aggregated walk-forward OOS
# predictions every retrain, since that's the least-overfit probability
# estimate we have. Platt scaling (a 2-parameter logistic regression on the
# model's own logit), not isotonic regression -- our calibration set is a
# few hundred to low thousands of rows, well under the ~2000-sample point
# where isotonic's non-parametric flexibility starts overfitting instead of
# helping (verified via literature search 2026-09-10, see prior_findings.md).
# ---------------------------------------------------------------------------

def _fit_platt_scaling(prob: np.ndarray, label: np.ndarray) -> dict:
    from sklearn.linear_model import LogisticRegression
    eps = 1e-6
    p = np.clip(prob, eps, 1 - eps)
    logit = np.log(p / (1 - p)).reshape(-1, 1)
    lr = LogisticRegression()
    lr.fit(logit, label)
    return {"a": float(lr.coef_[0][0]), "b": float(lr.intercept_[0])}


def apply_calibration(raw_prob: float, calib: Optional[dict] = None) -> float:
    """Apply the saved Platt-scaling correction to a raw model probability. No-op if no calibration has been fit yet."""
    if calib is None:
        calib = load_calibration()
    if calib is None:
        return raw_prob
    eps = 1e-6
    p = min(max(raw_prob, eps), 1 - eps)
    logit = math.log(p / (1 - p))
    calibrated_logit = calib["a"] * logit + calib["b"]
    return 1.0 / (1.0 + math.exp(-calibrated_logit))


def load_calibration() -> Optional[dict]:
    if not os.path.exists(CALIBRATION_PATH):
        return None
    try:
        with open(CALIBRATION_PATH) as f:
            return json.load(f)
    except Exception:
        return None


# Separate artifact from CALIBRATION_PATH -- that one is fit on ~72k backtest
# OOS rows and is the primary correction. This is a much smaller, secondary
# correction fit specifically on live paper-trading outcomes, applied ON TOP
# of the primary calibration rather than replacing it, since a live sample of
# a few dozen trades is nowhere near enough to trust as the sole calibration
# source (would risk overfitting away the much better-supported backtest fit).
# Built 2026-09-11 at the user's request after discussing the day's paper
# trading results, see prior_findings.md.
LIVE_CALIBRATION_PATH = os.path.join(ARTIFACT_DIR, "live_calibration.json")
LIVE_RECALIBRATION_MIN_SAMPLES = 30

# Shrinkage strength for fit_live_recalibration(), in units of "equivalent
# prior samples" -- at n=SHRINKAGE_PRIOR_STRENGTH the fitted correction is
# blended 50/50 with "no correction" (a=1, b=0); shrinkage weight -> 1 (fully
# trust the fit) as n grows much larger. Needed because sklearn's default L2
# regularization inside _fit_platt_scaling shrinks toward (a=0, b=0) --
# correct for the PRIMARY backtest calibration (huge n, and a=0 there would
# just mean "distrust an untrained model," a reasonable default) but WRONG
# for this secondary correction, where a=0 means "ignore the model entirely
# and always predict a constant," not "make no correction." The sensible
# prior here is the identity transform (a=1, b=0), and at the live sample
# sizes this will actually run at (tens of trades), sklearn's regularization
# alone isn't enough to prevent a noisy small-n fit from producing a
# larger-than-warranted correction -- explicit shrinkage toward identity
# is a more principled fix than just raising min_samples further.
SHRINKAGE_PRIOR_STRENGTH = 50


def fit_live_recalibration(since: Optional[datetime] = None, min_samples: int = LIVE_RECALIBRATION_MIN_SAMPLES) -> dict:
    """
    Fit a secondary Platt-scaling correction on live BtcPaperTrade outcomes
    (model_probability = the already-calibrated P(yes) at entry time,
    settlement_value = the real 0/1 outcome). Refuses to fit -- and refuses
    to overwrite any existing live_calibration.json -- below min_samples,
    since a tiny live sample is more likely to encode noise than a real
    correction. `since` defaults to the most recent known correctness-fix
    boundary (the distance_log staleness fix) rather than all historical
    trades, since earlier trades used measurably different (buggy) feature
    computations and don't represent the model's current behavior.

    The fitted (a, b) are shrunk toward the identity transform (a=1, b=0 --
    "trust the model's existing calibration as-is") in proportion to sample
    size, via SHRINKAGE_PRIOR_STRENGTH -- see its docstring for why this is
    needed on top of (not instead of) the min_samples gate.
    """
    from backend.models.database import BtcPaperTrade

    if since is None:
        since = datetime(2026, 9, 11, 9, 39, 28)

    db = SessionLocal()
    try:
        trades = (
            db.query(BtcPaperTrade)
            .filter(BtcPaperTrade.entry_time >= since, BtcPaperTrade.settled == True)
            .all()
        )
        n = len(trades)
        if n < min_samples:
            return {
                "status": "insufficient_data",
                "n": n,
                "min_samples": min_samples,
                "note": f"Only {n} settled trades since {since.isoformat()} -- need at least {min_samples} before fitting a live recalibration layer.",
            }

        prob = np.array([t.model_probability for t in trades])
        label = np.array([t.settlement_value for t in trades])

        pre_brier = float(np.mean((prob - label) ** 2))
        raw_calib = _fit_platt_scaling(prob, label)

        shrinkage_weight = n / (n + SHRINKAGE_PRIOR_STRENGTH)
        calib = {
            "a": shrinkage_weight * raw_calib["a"] + (1 - shrinkage_weight) * 1.0,
            "b": shrinkage_weight * raw_calib["b"] + (1 - shrinkage_weight) * 0.0,
        }

        recalibrated = np.array([apply_calibration(p, calib) for p in prob])
        post_brier_same_data = float(np.mean((recalibrated - label) ** 2))

        with open(LIVE_CALIBRATION_PATH, "w") as f:
            json.dump(calib, f, indent=2)

        return {
            "status": "ok",
            "n": n,
            "since": since.isoformat(),
            "calibration": calib,
            "unshrunk_calibration": raw_calib,
            "shrinkage_weight": round(shrinkage_weight, 4),
            "pre_recalibration_brier": round(pre_brier, 5),
            # Same caveat as train_btc_model()'s xgb_calibrated_on_same_data metric --
            # this is fit and evaluated on the SAME n trades, so it will always look
            # like an improvement. Not a genuine out-of-sample validation; useful only
            # to confirm the fit ran and moved in a sane direction, not as a real
            # performance claim.
            "post_recalibration_brier_on_same_data_DO_NOT_TRUST_FOR_COMPARISON": round(post_brier_same_data, 5),
        }
    finally:
        db.close()


def load_live_calibration() -> Optional[dict]:
    if not os.path.exists(LIVE_CALIBRATION_PATH):
        return None
    try:
        with open(LIVE_CALIBRATION_PATH) as f:
            return json.load(f)
    except Exception:
        return None


def apply_live_recalibration(calibrated_prob: float, calib: Optional[dict] = None) -> float:
    """Apply the secondary live-outcome correction on top of an already
    backtest-calibrated probability. No-op if no live recalibration has been
    fit yet (the common case until fit_live_recalibration() has enough data)."""
    if calib is None:
        calib = load_live_calibration()
    if calib is None:
        return calibrated_prob
    return apply_calibration(calibrated_prob, calib)


def _time_split_by_ticker(df: pd.DataFrame, train_frac: float = 0.8):
    window_order = (
        df.groupby("ticker")["close_time"].first().sort_values().index.tolist()
    )
    cutoff = int(len(window_order) * train_frac)
    train_tickers = set(window_order[:cutoff])
    test_tickers = set(window_order[cutoff:])
    return df[df["ticker"].isin(train_tickers)], df[df["ticker"].isin(test_tickers)]


def _brier(prob: np.ndarray, label: np.ndarray) -> float:
    return float(np.mean((prob - label) ** 2))


def _log_loss(prob: np.ndarray, label: np.ndarray) -> float:
    p = np.clip(prob, 1e-6, 1 - 1e-6)
    return float(-np.mean(label * np.log(p) + (1 - label) * np.log(1 - p)))


def _calibration_buckets(prob: np.ndarray, label: np.ndarray, n_bins: int = 10) -> List[dict]:
    bins = np.clip((prob * n_bins).astype(int), 0, n_bins - 1)
    out = []
    for b in range(n_bins):
        mask = bins == b
        if mask.sum() == 0:
            continue
        out.append({
            "bucket": f"{b*100//n_bins}-{(b+1)*100//n_bins}%",
            "n": int(mask.sum()),
            "predicted_avg": round(float(prob[mask].mean()), 4),
            "actual_rate": round(float(label[mask].mean()), 4),
        })
    return out


def _mismatch_buckets(model_prob: np.ndarray, market_prob: np.ndarray, label: np.ndarray) -> List[dict]:
    """Bucket rows by |model - market| disagreement and report each side's hit rate."""
    edge = model_prob - market_prob
    bins = [0, 0.02, 0.05, 0.10, 0.20, 1.01]
    out = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        mask = (np.abs(edge) >= lo) & (np.abs(edge) < hi)
        n = int(mask.sum())
        if n == 0:
            continue
        model_side = (model_prob[mask] >= 0.5).astype(int)
        market_side = (market_prob[mask] >= 0.5).astype(int)
        out.append({
            "abs_edge_range": f"{lo:.0%}-{hi:.0%}" if hi <= 1 else f">= {lo:.0%}",
            "n": n,
            "model_accuracy": round(float((model_side == label[mask]).mean()), 4),
            "market_accuracy": round(float((market_side == label[mask]).mean()), 4),
        })
    return out


def _fit_xgb(X_train, y_train, X_val, y_val):
    import xgboost as xgb
    # Shallow + heavily regularized: with only a few dozen-to-hundred training
    # windows, deeper/larger ensembles overfit hard (verified empirically --
    # depth=4/n=400 scored *worse than a coin flip* out of sample). These
    # defaults are tuned to be conservative for a small-n regime, not to
    # maximize training-set fit.
    model = xgb.XGBClassifier(
        n_estimators=150,
        max_depth=2,
        learning_rate=0.05,
        subsample=0.7,
        colsample_bytree=0.7,
        reg_lambda=5.0,
        objective="binary:logistic",
        eval_metric="logloss",
        early_stopping_rounds=20,
    )
    model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)
    return model


def _metrics_of(prob: np.ndarray, label: np.ndarray) -> dict:
    return {
        "brier": round(_brier(prob, label), 5),
        "log_loss": round(_log_loss(prob, label), 5),
        "accuracy": round(float(((prob >= 0.5).astype(int) == label).mean()), 4),
    }


async def train_btc_model(lookback_days: int = 60, n_folds: int = 5, min_train_windows: int = 150) -> dict:
    """
    Walk-forward retrain + evaluate: fold the settled windows into chronological
    train/test splits that roll forward through time (never a single static
    80/20 split), so the reported accuracy reflects "how would this have done
    if retrained periodically as new windows settled" rather than one lucky
    (or unlucky) train/test partition. The final production artifact is then
    retrained on ALL available data (most recent windows held out only for
    early-stopping) and saved for live paper-trading inference.
    """
    df = await build_feature_frame(lookback_days=lookback_days)

    # Never train/tune on the held-out forward-validation period -- see
    # settings.BTC_HOLDOUT_START's docstring. evaluate_holdout() below is the
    # only thing allowed to look at this data, and only for scoring, once.
    holdout_start = datetime.fromisoformat(settings.BTC_HOLDOUT_START)
    if not df.empty:
        df = df[df["close_time"] < holdout_start]

    window_order = df.groupby("ticker")["close_time"].first().sort_values().index.tolist() if not df.empty else []
    n_windows = len(window_order)

    if n_windows < min_train_windows * 2:
        return {
            "status": "insufficient_data",
            "rows": len(df),
            "windows": n_windows,
            "note": f"Need >= {min_train_windows*2} settled windows for a {n_folds}-fold walk-forward run "
                    f"(have {n_windows}). Keep the live poller running -- KXBTC15M settles every 15 minutes.",
        }

    fold_cuts = np.linspace(min_train_windows, n_windows, n_folds + 1).astype(int)
    fold_reports, agg_prob, agg_market, agg_gbm, agg_label = [], [], [], [], []

    for i in range(n_folds):
        train_tickers = set(window_order[:fold_cuts[i]])
        test_tickers = set(window_order[fold_cuts[i]:fold_cuts[i + 1]])
        if not test_tickers:
            continue

        fold_train_full = df[df["ticker"].isin(train_tickers)]
        fold_test = df[df["ticker"].isin(test_tickers)]
        fold_train, fold_val = _time_split_by_ticker(fold_train_full, train_frac=0.85)
        if fold_train.empty or fold_val.empty or fold_test.empty:
            continue

        model = _fit_xgb(
            fold_train[FEATURE_COLUMNS].values, fold_train["label"].values,
            fold_val[FEATURE_COLUMNS].values, fold_val["label"].values,
        )
        prob = model.predict_proba(fold_test[FEATURE_COLUMNS].values)[:, 1]
        label = fold_test["label"].values
        market_prob = fold_test["yes_mid"].values
        gbm_prob = fold_test["gbm_prob"].values

        fold_reports.append({
            "fold": i + 1,
            "train_windows": int(fold_train_full["ticker"].nunique()),
            "test_windows": int(fold_test["ticker"].nunique()),
            "test_rows": len(fold_test),
            "xgb_brier": round(_brier(prob, label), 5),
            "gbm_brier": round(_brier(gbm_prob, label), 5),
            "market_brier": round(_brier(market_prob, label), 5),
        })
        agg_prob.append(prob); agg_market.append(market_prob); agg_gbm.append(gbm_prob); agg_label.append(label)

    if not fold_reports:
        return {
            "status": "insufficient_data",
            "rows": len(df),
            "windows": n_windows,
            "note": "Folds produced no usable train/val/test split -- need more settled windows.",
        }

    prob_all = np.concatenate(agg_prob)
    market_all = np.concatenate(agg_market)
    gbm_all = np.concatenate(agg_gbm)
    label_all = np.concatenate(agg_label)

    metrics = {
        "xgb": _metrics_of(prob_all, label_all),
        "gbm_baseline": _metrics_of(gbm_all, label_all),
        "market": _metrics_of(market_all, label_all),
    }

    # Fit Platt scaling on these SAME aggregated OOS predictions -- can't
    # honestly report an "improved" Brier from this fit (that would be
    # circular, evaluating the correction on the exact data used to fit it).
    # The real test is whether LIVE Brier improves going forward now that
    # raw model outputs get this correction applied, not a number computed here.
    calib = _fit_platt_scaling(prob_all, label_all)
    with open(CALIBRATION_PATH, "w") as f:
        json.dump(calib, f, indent=2)
    calibrated_prob_all = np.array([apply_calibration(p, calib) for p in prob_all])
    metrics["xgb_calibrated_on_same_data_DO_NOT_TRUST_FOR_COMPARISON"] = _metrics_of(calibrated_prob_all, label_all)

    # Final production model: trained on every settled window we have, most
    # recent 10% held out only for early-stopping -- this is what paper
    # trading actually loads for live inference, not any individual fold's model.
    prod_train, prod_val = _time_split_by_ticker(df, train_frac=0.9)
    final_model = _fit_xgb(
        prod_train[FEATURE_COLUMNS].values, prod_train["label"].values,
        prod_val[FEATURE_COLUMNS].values, prod_val["label"].values,
    )
    importances = sorted(
        zip(FEATURE_COLUMNS, final_model.feature_importances_.tolist()),
        key=lambda t: t[1], reverse=True,
    )

    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    final_model.save_model(MODEL_PATH)

    xgb_beats_baseline = metrics["xgb"]["brier"] < metrics["gbm_baseline"]["brier"]
    xgb_beats_market = metrics["xgb"]["brier"] < metrics["market"]["brier"]
    note = (
        f"Walk-forward across {len(fold_reports)} fold(s), {n_windows} total settled windows: "
        f"XGBoost {'beats' if xgb_beats_baseline else 'does NOT beat'} the GBM baseline "
        f"and {'beats' if xgb_beats_market else 'does NOT beat'} the live market on aggregated held-out Brier score. "
        f"Production model retrained on all {int(prod_train['ticker'].nunique())} windows for live paper-trading inference."
    )

    report = {
        "status": "ok",
        "method": "walk_forward",
        "note": note,
        "fold_reports": fold_reports,
        "trained_at": datetime.utcnow().isoformat(),
        "lookback_days": lookback_days,
        "total_windows": n_windows,
        "production_windows": int(prod_train["ticker"].nunique()),
        "metrics": metrics,
        "feature_importance": [{"feature": f, "importance": round(v, 4)} for f, v in importances],
        "calibration_xgb": _calibration_buckets(prob_all, label_all),
        "calibration_market": _calibration_buckets(market_all, label_all),
        "mismatch_vs_market": _mismatch_buckets(prob_all, market_all, label_all),
        "model_path": os.path.abspath(MODEL_PATH),
    }

    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)

    return report


async def evaluate_holdout(lookback_days: int = 90) -> dict:
    """
    One-shot forward validation: score the CURRENT saved production model
    (whatever it is at call time) against settled windows on/after
    settings.BTC_HOLDOUT_START -- data that has NEVER been used to train or
    tune anything. This is the only honest test of whether the pipeline
    generalizes, as opposed to having been shaped by repeated iteration on
    the same historical stretch. Does not retrain; does not modify the
    holdout cutoff. Safe to call as often as you want to check progress --
    it never touches training, only reads the holdout and scores it.
    """
    import xgboost as xgb

    if not os.path.exists(MODEL_PATH):
        return {"status": "no_model"}

    holdout_start = datetime.fromisoformat(settings.BTC_HOLDOUT_START)
    df = await build_feature_frame(lookback_days=lookback_days)
    if df.empty:
        return {"status": "no_data"}

    df = df[df["close_time"] >= holdout_start]
    if df.empty:
        return {
            "status": "no_holdout_data_yet",
            "holdout_start": holdout_start.isoformat(),
            "note": "No settled windows past the holdout cutoff yet -- check back as live trading accumulates.",
        }

    model = xgb.XGBClassifier()
    model.load_model(MODEL_PATH)
    prob = model.predict_proba(df[FEATURE_COLUMNS].values)[:, 1]
    label = df["label"].values
    market_prob = df["yes_mid"].values
    gbm_prob = df["gbm_prob"].values

    return {
        "status": "ok",
        "holdout_start": holdout_start.isoformat(),
        "evaluated_at": datetime.utcnow().isoformat(),
        "windows": int(df["ticker"].nunique()),
        "rows": len(df),
        "metrics": {
            "xgb": _metrics_of(prob, label),
            "gbm_baseline": _metrics_of(gbm_prob, label),
            "market": _metrics_of(market_prob, label),
        },
        "calibration_xgb": _calibration_buckets(prob, label),
        "mismatch_vs_market": _mismatch_buckets(prob, market_prob, label),
    }
