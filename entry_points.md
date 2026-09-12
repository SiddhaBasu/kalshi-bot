# Entry timing: what we found and what's live

## The observed pattern (before any changes)

Across the first ~16 live paper trades, entries clustered almost entirely in
the first minute of each 15-minute KXBTC15M window:

- Median time into window at entry: **34 seconds**
- Mean: 50 seconds
- 14 of 16 trades entered within 60 seconds of window open
- Every trade entered within the first 26% of the window's lifetime

### Why this happens (mechanically)

`backend/core/btc_paper_trading.py`'s `generate_paper_trade()` checks the
currently open window every 15 seconds and takes the **first** qualifying
edge it sees, one trade per window, then stops evaluating that window.

Edges are structurally largest right at window open. The model's GBM-style
volatility term scales as `sigma_h = sigma_per_second * sqrt(seconds_remaining)`
-- with the full 15 minutes still on the clock, the same distance-to-strike
produces much more room for the model and the live market price to disagree
than it does with 2 minutes left. As the window runs out, both the model's
output and the market's own price converge toward the obvious answer, so
disagreement (edge) mechanically shrinks over the window's life. If an edge
is going to appear at all, it's almost always visible immediately.

## Attempt 1: randomized entry delay (implemented, then reverted)

Tried: give every window a deterministic-but-unpredictable eligibility time
(hash of the ticker), uniformly spread across 3-12 minutes elapsed, and don't
even look at a window before that. Goal was to spread entries across the
window instead of bunching at t=0.

**Backtested against 2026-09-09's data (96 windows) before trusting it live**,
same model/edge/Kelly rules for both, only entry-timing logic differed:

| | Immediate (first qualifying edge) | Delayed (randomized 3-12min) |
|---|---|---|
| Trades taken | 47 | 37 |
| Win rate | **68.1%** | **46.0%** |
| Total PnL | **+$1,102.75** | **+$442.30** |
| Avg PnL/trade | $23.46 | $11.95 |
| Model Brier at entry | 0.171 | 0.218 |
| Avg entry time | 1.6 min | 8.5 min |

**Result: the delay strategy was worse on every metric**, not just
different. It skipped ~10 windows entirely (the good early edge had already
faded by the delayed eligibility time) and, for the trades it did take,
picked systematically weaker, later-window edges. This directly confirms the
mechanism above: within a single window, earlier is not a coincidence, it's
structural. Forcing a wait doesn't find "different but equally good"
opportunities spread through the window -- it mostly finds the leftovers
after the good ones decayed.

**Decision: reverted.** Live behavior is back to "trade the first qualifying
edge, whenever it appears." The delay code
(`_eligible_after_seconds()` in `backend/core/btc_paper_trading.py`) and the
backtest harness (`backend/core/btc_entry_timing_backtest.py`) are both kept
in the codebase for further experiments -- just not applied to the live path.

## Ideas considered for diversifying entry points without repeating that mistake

The lesson from Attempt 1: diversification has to come from *adding*
opportunities, not from *withholding* the good early one. Candidates, none
implemented yet:

1. **Scale-in instead of one-shot.** Take a partial position on the first
   qualifying edge (keeps the reliable early entry), then allow additional
   smaller entries later in the same window only if the edge holds or grows.
   Gets some early-window and some later-window exposure instead of an
   either/or choice.
2. **Short confirmation window, not a long delay.** Require an edge to
   persist across 2-3 consecutive 15-second checks (30-45s) before firing,
   instead of acting on the very first tick. Filters noise without
   discarding genuinely strong early signals. Modeled on the weather bot's
   own `CONFIRM_MINUTES` pattern, just scaled to a 15-minute market.
3. **Best-of-first-N-checks instead of first-to-clear.** Compare qualifying
   candidates over a short initial window (e.g. first 60-90s) and take the
   strongest one seen, rather than firing the instant the threshold is
   crossed.
4. **Diversify across windows/markets, not within one.** If Kalshi lists
   an equivalent short-horizon market for another asset (e.g. an ETH 15-min
   series), entry-time diversification might be better sought there than by
   fighting the mechanics of a single window.

Any of these should be backtested the same way Attempt 1 was -- same model,
same edge/Kelly rules, only the entry-timing logic swapped -- before being
applied live.
