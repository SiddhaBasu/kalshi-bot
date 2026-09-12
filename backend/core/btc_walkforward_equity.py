"""
Genuinely out-of-sample equity-curve check: unlike btc_entry_timing_backtest.py
(which scores historical windows with the CURRENT production model -- largely
in-sample, since that model was trained on ~90% of all windows), this refits a
fresh model per walk-forward fold and only ever scores a fold's test windows
with a model that never saw them during training. This is what "would this
have made money out-of-sample" actually has to be measured with.

Research-only script, run via `python -m backend.core.btc_walkforward_equity`.
"""
import asyncio
from datetime import datetime

import numpy as np
import pandas as pd

from backend.config import settings
from backend.btcmarket.fees import trade_fee
from backend.core.btc_model_training import (
    build_feature_frame, FEATURE_COLUMNS, _time_split_by_ticker, _fit_xgb,
)


def _edge_and_direction(model_prob: float, market_prob: float):
    if model_prob > market_prob:
        return model_prob - market_prob, "yes"
    return market_prob - model_prob, "no"


def _kelly_size(model_prob: float, entry_price: float, bankroll: float, max_trade_size: float) -> float:
    if entry_price <= 0 or entry_price >= 1:
        return 0.0
    b = (1.0 - entry_price) / entry_price
    q = 1.0 - model_prob
    kelly_full = max(0.0, (model_prob * b - q) / b) if b > 0 else 0.0
    return min(kelly_full * settings.KELLY_FRACTION * bankroll, max_trade_size)


async def run(lookback_days: int = 90, n_folds: int = 5, min_train_windows: int = 150,
              max_trade_size: float = None) -> dict:
    max_trade_size = settings.BTC_PAPER_MAX_TRADE_SIZE if max_trade_size is None else max_trade_size
    df = await build_feature_frame(lookback_days=lookback_days)
    if df.empty:
        return {"status": "no_data"}

    window_order = df.groupby("ticker")["close_time"].first().sort_values().index.tolist()
    n_windows = len(window_order)
    if n_windows < min_train_windows * 2:
        return {"status": "insufficient_data", "windows": n_windows}

    fold_cuts = np.linspace(min_train_windows, n_windows, n_folds + 1).astype(int)
    scored_folds = []

    for i in range(n_folds):
        train_tickers = set(window_order[:fold_cuts[i]])
        test_tickers = set(window_order[fold_cuts[i]:fold_cuts[i + 1]])
        if not test_tickers:
            continue

        fold_train_full = df[df["ticker"].isin(train_tickers)]
        fold_test = df[df["ticker"].isin(test_tickers)].copy()
        fold_train, fold_val = _time_split_by_ticker(fold_train_full, train_frac=0.85)
        if fold_train.empty or fold_val.empty or fold_test.empty:
            continue

        model = _fit_xgb(
            fold_train[FEATURE_COLUMNS].values, fold_train["label"].values,
            fold_val[FEATURE_COLUMNS].values, fold_val["label"].values,
        )
        fold_test["model_prob"] = model.predict_proba(fold_test[FEATURE_COLUMNS].values)[:, 1]
        fold_test["fold"] = i + 1
        scored_folds.append(fold_test)

    if not scored_folds:
        return {"status": "no_folds"}

    combined = pd.concat(scored_folds)
    edges, directions = [], []
    for mp, ma in zip(combined["model_prob"], combined["yes_mid"]):
        e, d = _edge_and_direction(mp, ma)
        edges.append(e)
        directions.append(d)
    combined["edge"] = edges
    combined["direction"] = directions
    combined["entry_price"] = np.where(combined["direction"] == "yes", combined["yes_mid"], 1.0 - combined["yes_mid"])
    combined["qualifies"] = (combined["edge"] >= settings.MIN_EDGE_THRESHOLD) & (combined["entry_price"] <= settings.MAX_ENTRY_PRICE)

    ticker_order = combined.groupby("ticker")["timestamp"].min().sort_values().index.tolist()

    bankroll = settings.BTC_PAPER_INITIAL_BANKROLL
    peak = bankroll
    max_drawdown_pct = 0.0
    max_drawdown_dollars = 0.0
    equity_curve = []
    trades = []

    for ticker in ticker_order:
        g = combined[combined["ticker"] == ticker].sort_values("timestamp")
        candidates = g[g["qualifies"]]
        if candidates.empty:
            continue
        row = candidates.iloc[0]
        entry_price = row["entry_price"]
        size = _kelly_size(row["model_prob"], entry_price, bankroll, max_trade_size)
        if size < 0.01:  # allow sub-$1 sizes here since bankroll is small; floor only excludes literal noise
            continue

        label = row["label"]
        if row["direction"] == "yes":
            gross_pnl = size * (1.0 - entry_price) if label == 1 else -size * entry_price
        else:
            gross_pnl = size * (1.0 - entry_price) if label == 0 else -size * entry_price
        contracts = size / entry_price if entry_price > 0 else 0.0
        fee = trade_fee(contracts, entry_price)
        pnl = gross_pnl - fee
        bankroll += pnl
        peak = max(peak, bankroll)
        drawdown_pct = (peak - bankroll) / peak if peak > 0 else 0.0
        max_drawdown_pct = max(max_drawdown_pct, drawdown_pct)
        max_drawdown_dollars = max(max_drawdown_dollars, peak - bankroll)

        trades.append({
            "ticker": ticker, "fold": int(row["fold"]), "timestamp": str(row["timestamp"]),
            "direction": row["direction"], "entry_price": round(float(entry_price), 4),
            "size": round(float(size), 4), "model_prob": round(float(row["model_prob"]), 4),
            "market_prob": round(float(row["yes_mid"]), 4), "edge": round(float(row["edge"]), 4),
            "label": int(label), "pnl": round(float(pnl), 4), "bankroll_after": round(float(bankroll), 4),
            "realized_vol_per_second": float(row["realized_vol_per_second"]),
        })
        equity_curve.append({"ticker": ticker, "bankroll": round(float(bankroll), 4)})

    n = len(trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    brier = sum((t["model_prob"] - t["label"]) ** 2 for t in trades) / n if n else None

    # per-fold breakdown, to check the edge isn't concentrated in one lucky stretch
    fold_breakdown = []
    for i in range(1, n_folds + 1):
        fold_trades = [t for t in trades if t["fold"] == i]
        if not fold_trades:
            continue
        fw = sum(1 for t in fold_trades if t["pnl"] > 0)
        fold_breakdown.append({
            "fold": i, "n_trades": len(fold_trades),
            "win_rate": round(fw / len(fold_trades), 4),
            "total_pnl": round(sum(t["pnl"] for t in fold_trades), 4),
        })

    # weekly breakdown -- is the edge stable over calendar time, or concentrated in one stretch?
    weekly_breakdown = []
    if trades:
        weeks = sorted({t["timestamp"][:10] for t in trades})  # coarse: bucket by date, grouped below
        by_week = {}
        for t in trades:
            wk = pd.Timestamp(t["timestamp"]).to_period("W").start_time.date().isoformat()
            by_week.setdefault(wk, []).append(t)
        for wk in sorted(by_week):
            wt = by_week[wk]
            ww = sum(1 for t in wt if t["pnl"] > 0)
            weekly_breakdown.append({
                "week_of": wk, "n_trades": len(wt),
                "win_rate": round(ww / len(wt), 4),
                "total_pnl": round(sum(t["pnl"] for t in wt), 4),
            })

    # volatility-regime breakdown -- is the edge stable across calm vs turbulent BTC periods,
    # or is it concentrated in one regime that might not recur?
    vol_breakdown = []
    if trades:
        vols = sorted(t["realized_vol_per_second"] for t in trades)
        t1, t2 = vols[len(vols) // 3], vols[2 * len(vols) // 3]
        for label_name, lo, hi in [("low_vol", -1, t1), ("mid_vol", t1, t2), ("high_vol", t2, float("inf"))]:
            bucket = [t for t in trades if lo < t["realized_vol_per_second"] <= hi]
            if not bucket:
                continue
            bw = sum(1 for t in bucket if t["pnl"] > 0)
            vol_breakdown.append({
                "regime": label_name, "n_trades": len(bucket),
                "win_rate": round(bw / len(bucket), 4),
                "total_pnl": round(sum(t["pnl"] for t in bucket), 4),
            })

    return {
        "status": "ok",
        "lookback_days": lookback_days,
        "total_windows": n_windows,
        "n_trades": n,
        "wins": wins,
        "losses": n - wins,
        "win_rate": round(wins / n, 4) if n else None,
        "starting_bankroll": settings.BTC_PAPER_INITIAL_BANKROLL,
        "final_bankroll": round(bankroll, 4),
        "total_pnl": round(bankroll - settings.BTC_PAPER_INITIAL_BANKROLL, 4),
        "max_drawdown_pct": round(max_drawdown_pct, 4),
        "max_drawdown_dollars": round(max_drawdown_dollars, 4),
        "model_brier": round(brier, 5) if brier is not None else None,
        "fold_breakdown": fold_breakdown,
        "weekly_breakdown": weekly_breakdown,
        "vol_regime_breakdown": vol_breakdown,
        "trades": trades,
    }


if __name__ == "__main__":
    import json
    result = asyncio.run(run())
    print(json.dumps({k: v for k, v in result.items() if k != "trades"}, indent=2))
