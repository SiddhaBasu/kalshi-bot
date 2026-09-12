"""
One-off backtest: does the randomized entry-delay strategy (backend/core/
btc_paper_trading.py's _eligible_after_seconds) actually change/improve
results versus trading on the first qualifying edge at any point in the
window ("immediate")? Same current model, same edge/Kelly/sizing rules for
both -- the ONLY thing that differs is which row within each window is
picked as the entry point. Standalone research script; not wired into the
scheduler or API.
"""
import asyncio
from datetime import datetime, timedelta

import numpy as np

from backend.config import settings
from backend.core.btc_model_training import build_feature_frame, FEATURE_COLUMNS, MODEL_PATH
from backend.core.btc_paper_trading import _eligible_after_seconds


def _edge_and_direction(model_prob: float, market_prob: float):
    if model_prob > market_prob:
        return model_prob - market_prob, "yes"
    return market_prob - model_prob, "no"


def _kelly_size(model_prob: float, entry_price: float, bankroll: float) -> float:
    if entry_price <= 0 or entry_price >= 1:
        return 0.0
    b = (1.0 - entry_price) / entry_price
    q = 1.0 - model_prob
    kelly_full = max(0.0, (model_prob * b - q) / b) if b > 0 else 0.0
    return min(kelly_full * settings.KELLY_FRACTION * bankroll, settings.BTC_PAPER_MAX_TRADE_SIZE)


def _simulate(df, strategy: str):
    bankroll = settings.BTC_PAPER_INITIAL_BANKROLL
    trades = []
    for ticker, g in df.groupby("ticker", sort=False):
        g = g.sort_values("timestamp")
        if strategy == "immediate":
            candidates = g[g["qualifies"]]
        else:
            eligible_after = _eligible_after_seconds(ticker)
            candidates = g[g["qualifies"] & (g["elapsed_seconds"] >= eligible_after)]
        if candidates.empty:
            continue

        row = candidates.iloc[0]
        entry_price = row["entry_price"]
        size = _kelly_size(row["model_prob"], entry_price, bankroll)
        if size < 1.0:
            continue

        label = row["label"]
        if row["direction"] == "yes":
            pnl = size * (1.0 - entry_price) if label == 1 else -size * entry_price
        else:
            pnl = size * (1.0 - entry_price) if label == 0 else -size * entry_price
        bankroll += pnl

        trades.append({
            "ticker": ticker,
            "direction": row["direction"],
            "entry_price": round(float(entry_price), 4),
            "size": round(float(size), 2),
            "model_prob": round(float(row["model_prob"]), 4),
            "market_prob": round(float(row["yes_mid"]), 4),
            "edge": round(float(row["edge"]), 4),
            "elapsed_min": round(float(row["elapsed_seconds"]) / 60, 2),
            "label": int(label),
            "pnl": round(float(pnl), 2),
        })
    return trades, bankroll


def _summarize(trades, final_bankroll):
    n = len(trades)
    if n == 0:
        return {"n_trades": 0}
    wins = sum(1 for t in trades if t["pnl"] > 0)
    total_pnl = sum(t["pnl"] for t in trades)
    brier = sum((t["model_prob"] - t["label"]) ** 2 for t in trades) / n
    avg_elapsed = sum(t["elapsed_min"] for t in trades) / n
    return {
        "n_trades": n,
        "wins": wins,
        "losses": n - wins,
        "win_rate": round(wins / n, 4),
        "total_pnl": round(total_pnl, 2),
        "avg_pnl_per_trade": round(total_pnl / n, 2),
        "brier": round(brier, 5),
        "avg_entry_elapsed_min": round(avg_elapsed, 2),
        "final_bankroll": round(final_bankroll, 2),
    }


async def run_backtest(target_date: str = None) -> dict:
    """
    target_date: 'YYYY-MM-DD' (UTC) to backtest. Defaults to yesterday
    (the last full UTC calendar day before now).
    """
    import xgboost as xgb

    now = datetime.utcnow()
    if target_date:
        day_start = datetime.strptime(target_date, "%Y-%m-%d")
    else:
        day_start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    lookback_days = (now - day_start).days + 1
    df = await build_feature_frame(lookback_days=lookback_days)
    if df.empty:
        return {"status": "no_data"}

    df = df[(df["close_time"] >= day_start) & (df["close_time"] < day_end)].copy()
    if df.empty:
        return {"status": "no_data_for_date", "date": day_start.date().isoformat()}

    model = xgb.XGBClassifier()
    model.load_model(MODEL_PATH)
    df["model_prob"] = model.predict_proba(df[FEATURE_COLUMNS].values)[:, 1]

    df["total_window_seconds"] = (df["close_time"] - df["open_time"]).dt.total_seconds()
    df["elapsed_seconds"] = df["total_window_seconds"] - df["seconds_remaining"]

    edges, directions = [], []
    for mp, ma in zip(df["model_prob"], df["yes_mid"]):
        e, d = _edge_and_direction(mp, ma)
        edges.append(e)
        directions.append(d)
    df["edge"] = edges
    df["direction"] = directions
    df["entry_price"] = np.where(df["direction"] == "yes", df["yes_mid"], 1.0 - df["yes_mid"])
    df["qualifies"] = (df["edge"] >= settings.MIN_EDGE_THRESHOLD) & (df["entry_price"] <= settings.MAX_ENTRY_PRICE)

    immediate_trades, immediate_bankroll = _simulate(df, "immediate")
    delayed_trades, delayed_bankroll = _simulate(df, "delayed")

    return {
        "status": "ok",
        "date": day_start.date().isoformat(),
        "windows_available": int(df["ticker"].nunique()),
        "immediate_strategy": _summarize(immediate_trades, immediate_bankroll),
        "delayed_strategy": _summarize(delayed_trades, delayed_bankroll),
        "immediate_trades": immediate_trades,
        "delayed_trades": delayed_trades,
    }


if __name__ == "__main__":
    import json
    result = asyncio.run(run_backtest())
    print(json.dumps({k: v for k, v in result.items() if k not in ("immediate_trades", "delayed_trades")}, indent=2))
