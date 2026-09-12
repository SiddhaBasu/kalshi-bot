"""
Automatic live-vs-offline feature parity check.

Every train/serve skew bug found in this project so far (the realized-vol
estimator mismatch, the live decision gate reading a stale quote source) was
caught by manually recomputing offline features for a live trade and
comparing by hand -- once, after already suspecting something was wrong from
a bad result. This automates that comparison on an ongoing basis so the NEXT
skew bug (if there is one) shows up on its own, rather than requiring another
manual investigation triggered by noticing a suspicious number.

Depends on BtcPaperTrade.features_json (added 2026-09-10) -- trades recorded
before that column existed have nothing to compare and are skipped.
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from backend.models.database import SessionLocal, BtcPaperTrade
from backend.core.btc_model_training import build_feature_frame, FEATURE_COLUMNS

logger = logging.getLogger("trading_bot")

# Per-feature absolute-delta tolerance for "this is a real discrepancy," not
# ordinary noise from the live decision and the offline snapshot landing a
# few seconds apart. Chosen per-feature since they're on wildly different
# scales (probabilities vs. seconds vs. log-returns vs. contract volume) --
# a single relative threshold doesn't work across all of them.
#
# seconds_remaining/quote_age_seconds and the m1_vol_sum_* pair have a
# structural source of live/offline disagreement that isn't a bug: the live
# decision necessarily reads whatever Coinbase has published for the most
# recent 1-2 minutes, which can still be provisional at that instant, while
# the offline recompute (run later, once those minutes have fully closed and
# settled) sees the finalized volume. Combined with the up-to-30s window used
# to match a live trade to its nearest offline snapshot row (see the `dt`
# check below), some daily-routine disagreement here is unavoidable, not a
# regression. Investigated directly 2026-09-11 (Round 16) after
# m1_vol_sum_5m/15m showed suspiciously identical deltas across many trades --
# confirmed both live and offline independently compute correctly *distinct*
# 5m/15m sums; the near-identical deltas were coincidence from the volume gap
# concentrating in the most recent minute, not a duplicated-feature bug.
# Tolerances below were loosened to reflect what's actually achievable given
# this, so a real future skew bug doesn't get lost in permanent, understood,
# unfixable noise on these specific features.
ABSOLUTE_TOLERANCE = {
    "seconds_remaining": 10.0,
    "elapsed_fraction": 0.01,
    "distance_log": 0.0008,
    "yes_mid": 0.015,
    "spread": 0.015,
    "gbm_prob": 0.03,
    "realized_vol_per_second": 2e-6,
    "m1_ret_1m": 0.0008,
    "m1_ret_5m": 0.0008,
    "m1_vol_sum_5m": 20.0,
    "m1_vol_sum_15m": 20.0,
    "h1_log_return": 0.0015,
    "h1_range_pct": 0.0015,
    "quote_age_seconds": 10.0,
    "btc_move_since_quote": 0.0008,
}


def _load_trades_with_features(since: datetime) -> list:
    db = SessionLocal()
    try:
        rows = (
            db.query(BtcPaperTrade)
            .filter(BtcPaperTrade.entry_time >= since, BtcPaperTrade.features_json.isnot(None))
            .order_by(BtcPaperTrade.entry_time.asc())
            .all()
        )
        return [
            {"ticker": r.ticker, "entry_time": r.entry_time, "features": json.loads(r.features_json)}
            for r in rows
        ]
    finally:
        db.close()


async def check_feature_parity(lookback_hours: float = 48) -> dict:
    """
    For each recent paper trade with a saved feature vector, recompute the
    offline feature vector for that exact ticker/timestamp (now that the
    window has settled and its full snapshot history is available) and diff
    against what was actually used live. Flags any feature whose average
    discrepancy exceeds its tolerance -- that's what a real skew bug looks
    like, as opposed to ordinary small noise present on every trade.
    """
    since = datetime.utcnow() - timedelta(hours=lookback_hours)
    trades = _load_trades_with_features(since)
    if not trades:
        return {"status": "no_trades_with_features", "lookback_hours": lookback_hours}

    lookback_days = max(1, int(lookback_hours / 24) + 2)
    df = await build_feature_frame(lookback_days=lookback_days)
    if df.empty:
        return {"status": "no_offline_data"}

    per_feature_deltas = {c: [] for c in FEATURE_COLUMNS}
    compared = 0
    skipped_no_match = 0
    per_trade_rows = []

    for t in trades:
        rows = df[df["ticker"] == t["ticker"]]
        if rows.empty:
            skipped_no_match += 1
            continue
        rows = rows.copy()
        rows["dt"] = (rows["timestamp"] - t["entry_time"]).abs()
        offline_row = rows.sort_values("dt").iloc[0]
        # Only trust the match if it's genuinely close in time -- otherwise
        # this compares against a different moment in the window, not the
        # same decision point, which isn't a fair parity check.
        if offline_row["dt"].total_seconds() > 30:
            skipped_no_match += 1
            continue

        compared += 1
        trade_deltas = {}
        for col in FEATURE_COLUMNS:
            live_val = t["features"].get(col)
            if live_val is None:
                continue
            offline_val = offline_row[col]
            delta = abs(float(live_val) - float(offline_val))
            per_feature_deltas[col].append(delta)
            trade_deltas[col] = round(delta, 6)
        per_trade_rows.append({"ticker": t["ticker"], "deltas": trade_deltas})

    flagged = []
    summary = {}
    for col in FEATURE_COLUMNS:
        deltas = per_feature_deltas[col]
        if not deltas:
            continue
        mean_delta = sum(deltas) / len(deltas)
        max_delta = max(deltas)
        tol = ABSOLUTE_TOLERANCE.get(col, 0.05)
        summary[col] = {
            "mean_abs_delta": round(mean_delta, 6),
            "max_abs_delta": round(max_delta, 6),
            "tolerance": tol,
            "n": len(deltas),
        }
        if mean_delta > tol:
            flagged.append(col)

    if flagged:
        logger.warning(
            f"BTC feature parity check: {flagged} exceed tolerance across {compared} trades -- "
            f"possible live/offline skew bug, see per-feature summary in the report."
        )

    return {
        "status": "ok",
        "checked_at": datetime.utcnow().isoformat(),
        "lookback_hours": lookback_hours,
        "trades_checked": compared,
        "trades_skipped_no_match": skipped_no_match,
        "per_feature_summary": summary,
        "flagged_features": flagged,
        "per_trade": per_trade_rows,
    }
