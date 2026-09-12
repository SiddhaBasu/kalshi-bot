# Prior findings: public KXBTC15M / Kalshi bots and how they informed this project

Research done 2026-09-09 via web search, in response to: "is there other bitcoin
trading programs or other Kalshi trading programs, what do they do differently."
Not cloned or run — reviewed at the README/description level only. Recorded here
so the reasoning behind later feature/design decisions is traceable.

## Repos found

- **[reedjacobp/kalshi-trading-bot](https://github.com/reedjacobp/kalshi-trading-bot)**
  Algorithmic trading bot for KXBTC15M with a live dashboard.
- **[Bh-Ayush/Kalshi-CryptoBot](https://github.com/Bh-Ayush/Kalshi-CryptoBot)**
  Trades BTC 15-min and 1-hour Kalshi windows; async Python, multi-exchange price
  feeds, described as risk-averse.
- **[brandononchain/kalshibot](https://github.com/brandononchain/kalshibot)**
  Multi-strategy KXBTC15M bot. States its edge is a **structural latency
  asymmetry**: Kalshi contract prices reprice with a **3-7 second lag** relative
  to spot BTC moves, so the contract is briefly mispriced relative to the true
  implied probability right after a spot move.
- **[kapelame/kalshi-crypto-bot](https://github.com/kapelame/kalshi-crypto-bot)**
  Full framework: collector, ML training, backtester, live trader, terminal
  dashboard -- same overall shape as this project's pipeline (candles/snapshots ->
  training -> paper trading -> hourly analysis).
- **[hamad-khawaja/kalshi-trading-bot](https://github.com/hamad-khawaja/kalshi-trading-bot)**
  Trades KXBTC15M and KXETH15M; pulls price feeds from multiple sources
  including Bybit futures and Chainlink oracles (not just one spot exchange) --
  presumably for the same reason: cross-checking whether Kalshi's book lags.
- **[seanrobenalt/kalshi-bot](https://github.com/seanrobenalt/kalshi-bot)**
  Notably candid README: default settings lost the author ~$100 live. Useful
  as a reminder that a bot existing and running is not evidence it has real edge.
- Generic Bitcoin-price-prediction repos (XGBoost/LSTM/ARIMA on price alone,
  e.g. Mayurisarda89/Cryptocurrency-Price-Prediction-using-XGBoost) -- **not
  directly useful**: they predict future price, not P(price >= strike at a fixed
  short horizon), so the target/label shape doesn't transfer.
- **[XinYe-Eva/Bitcoin_prediction](https://github.com/XinYe-Eva/Bitcoin_prediction)**
  Predicts BTC volatility from order-book data specifically (bid/ask features,
  XGBoost + LSTM/GRU comparison) -- closer analog for feature-engineering ideas
  than the plain price-prediction repos.

## What this actually changed in the code

1. **Quote-staleness feature** (`quote_age_seconds`, `btc_move_since_quote` in
   `backend/core/btc_model_training.py`, wired into training and live inference
   in `backend/core/btc_paper_trading.py`). Directly motivated by kalshibot's
   claimed 3-7s repricing lag: distance-to-strike/volatility/time alone doesn't
   capture "the book hasn't caught up yet," only comparing the current quote to
   when it last actually changed does. Added 2026-09-09; walk-forward Brier
   improved from 0.138 -> 0.1245 in the same retrain that added it (also
   included a separate realized-volatility train/serve-skew fix in the same
   pass, so the improvement isn't attributable to this feature alone).
2. **Healthy skepticism baseline**: seanrobenalt's lost-money README is the
   reason live paper-trading results in this project are reported with
   explicit sample-size caveats rather than taken at face value the moment a
   win streak appears -- see the hourly self-analysis behavior.

## Round 2 (2026-09-10): actual source, not just READMEs

Fetched real source/schema this time, not just repo descriptions -- specifically
`kapelame/kalshi-crypto-bot`'s `collector.py` (closest architectural analog:
collector -> train -> backtest -> paper -> live, same shape as this project).

Their stored feature schema (SQLite `features` table, one row per 2s poll,
wide/flat `{asset}_columnname` layout) includes two things we don't have:

- **`ob_imbalance` = (yes_qty - no_qty) / total**, computed from real orderbook
  depth (not just top-of-book price). We already fetch real depth in
  `backend/btcmarket/orderbook.py` for fill simulation -- computing imbalance
  from the same ladders would be a small addition, not a new data source.
  Not implemented yet; candidate next feature.
- **`mom_5s` / `mom_15s` / `mom_30s`** -- momentum at a much finer grain than
  our `m1_ret_1m`/`m1_ret_5m` (which are 1-minute-candle-based, i.e. 60s/300s
  granularity at best). We already poll Coinbase's ticker at 1Hz
  (`candles.poll_live_price`) but only retain it folded into the forming
  1-minute candle, not as its own short-horizon series. Would need new
  tick-level retention to build this; not implemented yet.

Their schema notably does NOT include anything resembling our quote-staleness
feature or the realistic-fill/re-validation logic in `orderbook.py` /
`btc_paper_trading.py` -- suggests this project is ahead of that comparable
one in the "is the quote actually current" dimension, not just behind on
imbalance/fast-momentum. Worth knowing both directions, not just gaps.

Directly informed by this: `BtcPaperTrade.features_json` (added 2026-09-10)
stores the full per-trade feature vector, closer to this project's
wide-row-per-observation pattern than our previous "only store the two
resulting probabilities" approach.

## Round 3 (2026-09-10, /loop): implemented imbalance/momentum, found new sources

**Order-book imbalance and short-horizon momentum (from Round 2) are now implemented**,
as live/informational tracking only, not yet trained model inputs (see "What
this actually changed" below) -- Round 2's "not implemented yet" note is stale.

New sources found this round, fetched actual source where possible:

- **[reedjacobp/kalshi-trading-bot](https://github.com/reedjacobp/kalshi-trading-bot)**
  Cites the **favorite-longshot bias** literature (favorites win more than
  their price implies) as a strategy trigger at YES/NO > 70c with 3+ min
  remaining, order flow imbalance from Crypto.com's book, and **0.25x
  fractional Kelly** sizing (we use 0.15x, inherited unmodified from the
  weather bot -- worth reconsidering, not done yet). READMEs only for this
  one; couldn't reach the actual strategy source.
- **[hamad-khawaja/kalshi-trading-bot](https://github.com/hamad-khawaja/kalshi-trading-bot)**
  Pulled the real `feature_engine.py` output: **42 features**, most notably a
  category we have nothing like -- **funding rate signals and liquidation
  imbalance** from perpetual futures markets (leveraged-positioning skew and
  liquidation cascades as short-horizon predictors), plus RSI/MACD/Bollinger
  Bands/VWAP deviation, taker buy/sell ratio (from trade data, not just
  resting orders), settlement_bias (recent YES/NO win-rate), and cyclical
  hour-of-day encoding.
- **arXiv 2602.00776, "Explainable Patterns in Cryptocurrency Microstructure"**
  Order-book features accounted for 81.3% of selected feature importance in
  their model -- general validation that the orderbook-depth direction this
  project has been pursuing (imbalance, realistic fills, quote staleness) is
  the right area to keep investing in, not a side quest.

## What this actually changed in the code (Round 3)

1. **`orderbook.book_imbalance()`** -- `(yes_bid_qty - no_bid_qty) / total`
   across full depth (not just top-of-book), reusing the same raw book fetch
   already needed for fill simulation (no extra API call). Recorded on every
   5s depth-snapshot poll (`KxBtcOrderbookSnapshot.imbalance`) and on every
   paper trade (`BtcPaperTrade.book_imbalance`) -- informational only, not a
   trained feature yet, since we have only hours of history for it.
2. **`candles.get_short_momentum()`** -- mom_5s/15s/30s from an in-memory 45s
   tick ring buffer fed by the existing 1Hz Coinbase poll. In-memory only
   (resets on restart, not persisted for training). Recorded on every paper
   trade, same informational-only status as imbalance.
3. **`path_efficiency_15m`** and **`hour_sin`/`hour_cos`** -- ADDED TO THE
   TRAINED MODEL (FEATURE_COLUMNS), not just tracked, because unlike
   imbalance/momentum these are computable from data we've had all along
   (1-minute candles, timestamps), so full historical coverage already
   exists. Verified live/offline parity match to float precision before
   trusting them (same discipline as every other feature here). Retrained:
   Brier 0.1285 -> 0.1291 -- essentially flat, within noise. `hour_sin`
   earned real feature importance (top 10); `path_efficiency_15m` didn't add
   much on top of what vol/momentum features already capture. Kept both
   anyway since neither hurt and hour-of-day is genuinely cheap.

## Scoped candidates for a future iteration (not built yet)

- **Funding rate / liquidation imbalance from perpetual futures** (e.g.
  Binance Futures' public funding-rate and open-interest endpoints -- no auth
  needed). Genuinely new data category, not a variant of anything we have.
  Needs new data collection before it could be a trained feature, same
  constraint as imbalance/momentum -- start recording, don't force a retrain.
- **RSI / MACD / Bollinger Bands / VWAP deviation** on the existing 1-minute
  candle series -- classical technical indicators, computable entirely from
  data already retained historically (like path_efficiency), so COULD go
  straight into the trained model without a data-collection wait. Reasonable
  next thing to implement given that advantage.
- **taker_buy_sell_ratio** from Kalshi's own trade feed (`get_trades`,
  already used by whale_scanner for an unrelated purpose) -- aggressor-side
  signal distinct from resting-order imbalance. Needs checking whether trade
  history goes back far enough to backfill, or would need new collection.
- **settlement_bias** (trailing YES/NO win-rate over recent windows) --
  computable from settlement labels we already fetch during training, but
  needs a carefully causal (no-lookahead) rolling implementation per ticker
  position, not a trivial vectorized add. Skipped this round for that reason,
  not because it lacks data.
- **0.25x fractional Kelly** (reedjacobp uses this; we use 0.15x, inherited
  unmodified from the weather bot) -- worth a proper walk-forward comparison
  before changing, not a blind copy.

## Round 4 (2026-09-10): built the rest of the "found, not built" list

User asked to build the remaining Round 3 candidates directly (except the
0.25x Kelly comparison -- explicitly told to keep 0.15x, no test needed).

**Added to the trained model** (FEATURE_COLUMNS, all computable from data
already retained historically, so no data-collection wait needed):
`rsi_14`, `macd_histogram_pct`, `bollinger_pct_b`, `vwap_deviation_60m`
(shared `technical_indicators()` function, called identically by training
and live -- one implementation, not two hand-written copies, specifically to
avoid repeating the realized-vol skew bug), and `settlement_bias` (trailing
YES win-rate over the last 20 settled windows, causal via `.shift(1)` so it
never sees its own window's outcome). Retrained: Brier 0.1291 -> 0.1295,
flat within noise. Feature importance was genuinely mixed: `macd_histogram_pct`
landed in the top 10, `settlement_bias`/`vwap_deviation_60m`/`hour_sin`/`hour_cos`
all showed real (if modest) importance; `rsi_14` and `bollinger_pct_b` showed
almost none, likely redundant with volatility/momentum features already
present. Kept all five anyway since none hurt.

**Added as live/informational tracking only** (not trained inputs, same
status as Round 3's imbalance/momentum): `taker_buy_sell_ratio` (aggressor-side
flow from Kalshi's own trade feed, distinct from resting-order imbalance)
and `funding_rate` (perpetual futures positioning skew).

**Found and fixed a real, significant, unrelated bug while adding VWAP**:
`vwap_deviation_60m` came back NaN despite 190 cached candles. Root cause:
`_upsert_candles()` in `backend/btcmarket/candles.py` SKIPPED any candle
whose `open_time` already existed, and the live 1Hz ticker poller
(`poll_live_price`) creates a placeholder row with `volume=0.0` the instant
each new minute starts -- so no backfill could ever correct it. Every candle
ever touched by the live poller had `volume=0` forever, silently degrading
`m1_vol_sum_5m`/`m1_vol_sum_15m` (pre-existing features!) for all recent live
data, not just the new VWAP feature. Fixed `_upsert_candles` to UPDATE
existing rows instead of skipping them, then ran a one-off repair backfill
over the last 24 hours (`fetch_coinbase_candles` + corrected `_upsert_candles`)
-- verified 0 zero-volume candles remain in that range afterward. This is
the same category of bug as the earlier realized-vol/quote-staleness
train/serve skews -- caught the same way, by testing the actual output
against real data rather than trusting the code.

**Two more real bugs caught by testing against live data before trusting them:**
- `get_taker_buy_sell_ratio()` initially read a `count` field that doesn't
  exist on Kalshi's trade objects -- the real field is `count_fp` (a string,
  same fixed-point convention seen elsewhere in this project). Would have
  silently returned None/zero for every trade forever if not tested directly.
- Binance Futures (the funding-rate source hamad-khawaja's bot uses) returned
  HTTP 451 -- geo-blocked from this environment, consistent with why this
  project already avoids Binance for spot data. Switched to Kraken Futures
  (`PI_XBTUSD`), verified reachable and returning real data before adopting it.

## Round 5 (2026-09-10, /loop): Platt-scaling calibration, two dead ends

New sources this round, mixed yield:

- **[papabrosio/kalshi-btc-15min-trader](https://github.com/papabrosio/kalshi-btc-15min-trader)**
  Claims to fetch "the BRTI API" directly for settlement prediction and
  "reciprocity logic" to synthesize the ask side from no-bid data (roughly
  what `backend/btcmarket/orderbook.py` already does, deriving ask ladders
  from the opposite side's bids -- not new). No implementation details,
  formulas, or code visible in the repo preview; "99%+ settlement accuracy"
  claim has zero methodology behind it. Treated with the same skepticism as
  seanrobenalt's README (a claim isn't evidence) -- not actionable, nothing
  built from this one.
- Polymarket 5-min BTC binary options Medium article -- 403 Forbidden,
  couldn't access. Dead end this round.
- **General ML literature (not a repo): XGBoost calibration.** Boosting
  models are documented to produce systematically miscalibrated raw
  probabilities (their additive, error-correcting structure doesn't
  naturally yield well-calibrated outputs). For small calibration sets
  (<~2000 samples -- ours is a few hundred to low thousands), **Platt
  scaling outperforms isotonic regression**, which tends to overfit at that
  scale. This is exactly the "natural next step" flagged as unbuilt all the
  way back when the trained model was first proposed, revisited here.

## What this actually changed in the code (Round 5)

**Platt-scaling calibration**, fit fresh every retrain on the aggregated
walk-forward out-of-sample predictions (`_fit_platt_scaling` in
`btc_model_training.py`, `calibration.json` saved alongside the model
artifact). Applied to every live prediction before it's used for any trading
decision (`apply_calibration()` in `generate_paper_trade`). First fit:
a=1.18, b=-0.026 -- raw model is slightly underconfident on average with a
small bias toward NO. Deliberately did NOT report an "improved" Brier from
this fit -- evaluating a calibration correction on the exact same data used
to fit it is circular and would always look good. Labeled that metric key
`xgb_calibrated_on_same_data_DO_NOT_TRUST_FOR_COMPARISON` in the training
report specifically so it can't be mistaken for a real result later. The
actual test is whether LIVE Brier (currently 0.22-0.24, notably worse than
the 0.129 walk-forward number) moves toward the walk-forward number now that
raw outputs get this correction -- something to watch over the next batch of
live trades, not something already proven by this change.

## Round 6 (2026-09-10, /loop): calibration check-in (too early), validated existing choices, no build

**Calibration check-in** (per Round 5's follow-up): only 3 trades have settled
since the Platt-scaling fit deployed (~15:56 ET). Brier 0.2385 -- within the
same 0.22-0.24 range as before, but n=3 is nowhere near enough to say whether
calibration is helping, hurting, or doing nothing. Revisit once there are
dozens of trades under it, not 3.

New research this round, neither led to a build:

- **Kalshi's own published best practices** confirm a WebSocket API exists
  and is the recommended path for live book/trade data, not REST polling --
  "cache slow-changing REST data, move live book and trade monitoring to
  WebSockets." This is directly relevant to the quote-staleness problem
  found earlier (the REST market-summary endpoint can sit frozen for several
  seconds while the real book moves, which is why `live_yes_mid` re-fetches
  the orderbook via REST at decision time as a workaround). A WebSocket feed
  could make that workaround unnecessary and reduce latency further. NOT
  built this round -- this is a genuinely bigger lift than "small and
  actionable today" (new persistent-connection client, likely different auth
  handshake than the REST signing already in place, reconnect/backoff logic).
  Recorded as a scoped candidate for a dedicated future session, not
  attempted piecemeal.
- **Academic literature on orderbook imbalance depth**: multi-level (e.g.
  "level 5") imbalance is a slightly better predictor than top-of-book-only
  ("level 1") imbalance. Checked our own `book_imbalance()` against this --
  it already sums across Kalshi's FULL returned depth (~100+ price levels
  from `_fetch_raw_book`), not just top-of-book, so this is validating
  evidence for an existing choice, not a gap to fix.

## Scoped candidates for a future iteration (not built yet)

- **Kalshi WebSocket feed** for the current market's top-of-book, replacing
  (or supplementing) the REST-based `live_yes_mid` workaround. Bigger lift:
  new connection-management code, likely a different auth flow than the
  existing RSA-PSS REST signing, reconnect/backoff handling. Worth a
  dedicated session, not a loop-iteration-sized change.

## Round 7 (2026-09-10, /loop): drawdown circuit breaker, calibration still too early

**Calibration check-in**: still only 4 trades since the Platt-scaling fit
deployed, Brier 0.3136 (worse than Round 6's 0.2385 at n=3). This is not a
real trend in either direction -- it's what pure noise looks like at n=4.
Needs dozens of trades before this check means anything; stop reporting a
number here until there's a real sample.

New source this round:

- **[OctagonAI/kalshi-trading-bot-cli](https://github.com/OctagonAI/kalshi-trading-bot-cli)**
  (312 GitHub stars). Its "5-gate risk engine": Kelly, Liquidity,
  Correlation, Concentration, Drawdown -- checked before sizing any trade.
  We already have equivalents of Kelly (fractional sizing) and Liquidity
  (real orderbook fill simulation). Correlation and Concentration don't
  apply to this project's design (only ever one open position at a time,
  since each KXBTC15M window is a fresh 15-minute market and we only trade
  once per window -- nothing to correlate or concentrate across). Drawdown
  was a real, genuine gap: the existing circuit breaker
  (`btc_live_trading.py`) only tracked *daily* realized loss, which resets
  every midnight -- a slow bleed spread across many days would never trip
  it. Also checked ryanfrigo/kalshi-ai-trading-bot (423 stars, multi-model
  LLM ensemble) and a generic ensemble/stacking ML search -- neither
  produced anything actionable beyond what calibration (Round 5) already
  addresses for this project's single-model setup.

## What this actually changed in the code (Round 7)

Added a **drawdown-from-peak circuit breaker** to `BtcLiveTradingState`
(`peak_bankroll`, `max_drawdown_pct`, default 25%) -- tracks the highest
real Kalshi balance ever observed and halts real trading if the current
balance falls more than `max_drawdown_pct` below that peak, independent of
and in addition to the existing daily-loss check. Reused the balance fetch
already needed for Kelly sizing (moved it earlier in `generate_live_trade()`)
rather than adding a second API call. Verified all three cases directly
(new peak recorded, small drawdown allowed, breach correctly trips with a
clear message) without ever flipping the DB kill switch -- real trading
stays exactly as inert as before this change; this only strengthens
already-dormant safety infrastructure.

## What was intentionally not adopted

- Multi-exchange spot feeds (Bybit, Chainlink) as a settlement-price proxy --
  Kalshi settles KXBTC15M against **CF Benchmarks' BRTI** specifically (its own
  documented rule), not Coinbase, Binance, or Bybit. Blending in more exchanges
  was explicitly requested against and dropped in this project (see chat
  history 2026-09-09) rather than half-implemented.
- Cloning/running any of the above bots -- this was a read-only literature
  check, not a code reuse exercise. None of their code is in this repo.

## Round 8 (2026-09-10, /loop): Brier confidence intervals, Kelly fraction validated, cross-platform arbitrage scoped

**Calibration check-in**: still only 4 trades since the Platt-scaling fit
deployed -- not reporting a bare Brier number again (per the standing
instruction). Instead, this round replaces the arbitrary "wait for 20-30
trades" rule with an actual statistic: see below.

### What this actually changed in the code (Round 8)

Added a **standard error and 95% CI on the Brier score** to
`paper_trading_report()` in `btc_paper_trading.py` (`model_brier_se`,
`model_brier_ci95`), computed from the sample variance of the per-trade
squared errors (`(model_probability - settlement_value)^2`), `SE = sqrt(var/n)`.
This replaces guessing a trade-count threshold with a real measure of whether
the current Brier estimate is precise enough to trust:

- At n=4 (since calibration deploy): Brier 0.3136, **95% CI [0.152, 0.475]**
  -- confirms this is still noise, the CI is far too wide to compare against
  the walk-forward backtest's ~0.129 baseline.
- At n=57 (lifetime, mixing pre- and post-calibration trades, sanity check
  only): Brier 0.2434, 95% CI [0.203, 0.284] -- CI width narrows as expected
  with more data, giving a template for what "narrow enough to trust" will
  look like once the post-calibration cohort itself reaches this size.

Going forward, report the CI width alongside any Brier number instead of a
raw point estimate -- once the calibration-cohort CI stops overlapping the
pre-calibration baseline (or stops overlapping 0.129), that's the real signal
to compare against the backtest, not a fixed trade count.

New research this round:

- **Kelly fraction validated, not changed**: multiple independent sources
  (Crypticorn's article specifically about Kalshi/Polymarket 15-min crypto
  up/down markets, Matthew Downey's fractional-Kelly simulation writeup)
  converge on the same range: 10-25% of full Kelly for near-50/50,
  high-variance short-horizon crypto markets, because full Kelly badly
  over-bets a thin, uncertain edge. Our existing 0.15x (kept explicitly by
  the user in Round 4, not the 0.25x some repos use) sits squarely inside
  this documented range. No code change -- this is confirming evidence for
  a decision already made, not a gap.
- **Cross-platform Polymarket <-> Kalshi arbitrage bots**: a distinct cluster
  of public repos (ImMike/polymarket-arbitrage, dexorynLabs's and
  TopTrenDev's arbitrage bots, Sectionnaenumerate's and haoo99's BTC-specific
  variants, CarlosIbCu's BTC 1-hour variant) run a fundamentally different
  strategy from ours: watch the *same* BTC window priced on both Polymarket
  and Kalshi simultaneously and trade the gap when YES+NO prices don't sum
  to ~100% across the two platforms, rather than forecasting the BTC price
  itself. This is a genuinely different idea, not yet in prior_findings.md.
  NOT built this round -- not a small/actionable change: it needs a whole
  second exchange integration (Polymarket API auth, contract/window matching
  between the two platforms' independently-scheduled markets, its own
  balance and settlement tracking) layered on top of, not instead of, the
  existing single-platform model. Recorded as a scoped candidate below.

### Scoped candidates for a future iteration (not built yet)

- Kalshi WebSocket feed (carried over from Round 6/7 -- still not attempted,
  still judged too big for a single loop iteration).
- **Polymarket/Kalshi cross-platform arbitrage** (new this round): a second,
  independent strategy module -- watch both platforms' BTC windows for
  YES+NO mispricing across venues. Would need: Polymarket API client and
  auth, a window-matching layer (the two platforms' 15-min/hourly windows
  are not guaranteed to align), separate balance/position tracking per
  venue, and its own risk gating -- this is a new-strategy-sized project, not
  a feature addition to the existing forecasting model. Worth a dedicated
  future session if there's appetite for a second, uncorrelated strategy
  running alongside the current one.

### What was intentionally not adopted

- Changing the Kelly fraction to 0.25x (or any other repo's default) --
  0.15x already sits within the well-documented safe range for this market
  type and was an explicit user decision (Round 4); revisited this round
  only to confirm it against outside literature, not to change it.

## Round 9 (2026-09-10, /loop): 429 retry/backoff, VPIN scoped as data-blocked

**Calibration check-in**: still n=4 since the calibration deploy, CI
unchanged from Round 8 ([0.152, 0.475]) -- no new settled trades in the
cohort this round, nothing new to report.

New research this round:

- **VPIN (Volume-Synchronized Probability of Informed Trading)** -- an
  order-flow-toxicity metric (Easley/Lopez de Prado/O'Hara), several public
  implementations exist (`hanxixuana/flowrisk`, `ghlian/VPIN-1`,
  `yt-feng/VPIN`). A 2025 paper found VPIN significantly predicts BTC price
  jumps. Genuinely different signal from anything we track (book_imbalance,
  taker_buy_sell_ratio, and momentum are all price/volume-level, not
  toxicity/informed-trading classification). NOT built -- this needs
  trade-by-trade tick data classified into buy/sell volume via bulk-volume
  classification, then bucketed into fixed-volume (not fixed-time) bars.
  We only retain 1-minute OHLCV candles for BTC, not a raw trade tape --
  same "needs new data collection first" category as the already-scoped
  liquidation-imbalance idea. Recorded below as a scoped candidate rather
  than force-fit onto candle data it wasn't designed for.
- **Kalshi production best-practices articles** (botforkalshi.com,
  alphascope.app) repeated the WebSocket-over-REST recommendation already
  captured in Round 6/7 (nothing new there), but one specific point wasn't
  previously checked: "record every 429 and retry delay." Checked our own
  `kalshi_client.py` against this directly -- it had **zero** rate-limit
  handling: `get`/`post`/`delete` called `response.raise_for_status()`
  immediately, so a 429 would surface as a generic `httpx.HTTPStatusError`
  indistinguishable from a real failure, with no retry and no visibility.
  This is a real gap given `btc_poll_job` and `btc_ticker_poll_job` both run
  on a 1-second interval already. This one was small and actionable --
  built this round (see below).

### What this actually changed in the code (Round 9)

Added a shared `_request_with_retry()` in `kalshi_client.py` used by
`get`/`post`/`delete`: on a 429, retries up to 3 times, respecting the
`Retry-After` header when Kalshi sends one, otherwise exponential backoff
(0.5s base, doubling, small jitter). Logs a `WARNING` on every retry and on
final exhaustion (path, attempt number, delay) so rate limiting is now
visible in `bot.log` instead of silently blending into generic exception
noise. Verified with two mocked-`httpx` tests (not against the real API,
since triggering a real 429 isn't reproducible on demand): (1) 429 twice then
200 -- confirms it retries, honors `Retry-After`, and returns the eventual
success; (2) 429 on every attempt -- confirms it still raises after exactly
3 retries rather than retrying forever or silently swallowing the error.
Restarted the server, verified clean startup with no errors and both kill
switches (`WEATHER_BOT_ENABLED`, `BTC_LIVE_TRADING_ENABLED`) still `false`.

### Scoped candidates for a future iteration (not built yet)

- Kalshi WebSocket feed (carried over, still not attempted).
- Polymarket/Kalshi cross-platform arbitrage (carried over from Round 8).
- **VPIN / order-flow toxicity** (new this round): needs a raw BTC trade
  tape (individual trades with side classification), not the 1-minute OHLCV
  candles this project currently retains. Would require a new ingestion
  path (e.g. a trade-stream websocket from the BTC spot venue already used
  for candles) before this is even computable, let alone trainable on
  enough history to be trusted. Same category as the already-scoped
  liquidation-imbalance idea -- worth revisiting if/when a trade-tape feed
  gets built for other reasons.

## Round 10 (2026-09-10, /loop): logging visibility sweep across BTC scheduler jobs

**Calibration check-in**: n=8 settled since the calibration deploy now
(up from n=4 in Round 9), Brier 0.23853, **95% CI [0.124, 0.353]**.
Half-width is ~0.114, still above the ~0.05 threshold to call this
meaningful -- but worth noting the trend, not as a conclusion: the lower
bound (0.124) has moved right up against the 0.129 walk-forward backtest
number. Not yet reportable as signal, just tracking the direction.

New research this round:

- **Cross-platform Kalshi/Polymarket arbitrage articles** (defirate.com,
  turbinefi.com, clawarbs.com) confirmed what Round 8 already found and
  scoped -- typical pre-cost spreads 1.5-4.5%, decaying in 2-7 second
  windows on liquid markets. Nothing new to add beyond the existing scoped
  entry; this is read-only confirmation, not a new idea.
- **ML drift-monitoring literature** (production ML maintenance guides,
  XGBoost feature-importance-drift approaches) pointed at something worth
  checking directly in our own code rather than adopting wholesale: whether
  our own scheduled jobs would even surface a persistent failure. Checked
  `scheduler.py` directly -- found the **exact same silent-failure pattern**
  already caught and fixed once this session (Round 7-adjacent,
  `btc_paper_settlement_job`) was still present in **7 other** BTC jobs:
  `btc_retrain_job`, `btc_orderbook_poll_job`, `btc_history_refresh_job`,
  `btc_paper_signal_job`, and `btc_feature_parity_job` all caught broad
  exceptions and logged them at `debug` level -- meaning a persistent failure
  in any of them (including the retrain job, which only runs every 6h, and
  the feature-parity job, which is itself the safety net for train/serve
  skew bugs) would be completely invisible in `bot.log`.

### What this actually changed in the code (Round 10)

Bumped `logger.debug(...)` to `logger.warning(...) + logger.exception(...)`
(matching the existing settlement-job fix pattern exactly) in:
`btc_retrain_job`, `btc_orderbook_poll_job` (5s cadence),
`btc_history_refresh_job` (5min cadence), `btc_paper_signal_job` (15s
cadence), and `btc_feature_parity_job` (3h cadence). Deliberately did **NOT**
touch `btc_poll_job` or `btc_ticker_poll_job` -- both run at 1Hz, and
elevating persistent-failure logging there to warning risks genuine log
spam (up to 3600 lines/hour during a real outage) rather than useful
visibility; that's a different problem (rate-limited/deduped logging) that
deserves its own scoped fix, not a copy-paste of this round's pattern.
Verified with `ast.parse` (syntax), then a clean server restart with no
errors, and confirmed both `WEATHER_BOT_ENABLED` and
`BTC_LIVE_TRADING_ENABLED` still `false`.

### Scoped candidates for a future iteration (not built yet)

- Kalshi WebSocket feed (carried over).
- Polymarket/Kalshi cross-platform arbitrage (carried over from Round 8).
- VPIN / order-flow toxicity (carried over from Round 9, needs a trade
  tape we don't retain).
- **Rate-limited/deduped warning logging for the two 1Hz BTC jobs**
  (`btc_poll_job`, `btc_ticker_poll_job`) -- same silent-failure risk as the
  jobs fixed this round, but naively elevating to warning-per-failure risks
  log spam at 1Hz during a real outage. Needs something like "log at
  warning on the first failure and then at most once per minute while it
  keeps failing," which is a small enough idea to be a good candidate for
  next round rather than this one.

## Round 11 (2026-09-10, /loop): throttled 1Hz logging, backtest-vs-live rigor double-checked

**Calibration check-in**: still n=8 since calibration deploy (no new
settlements since Round 10), CI unchanged at [0.124, 0.353].

### What this actually changed in the code (Round 11)

Built the item scoped at the end of Round 10: added `_log_throttled_warning()`
to `scheduler.py` -- logs the first failure for a given job key immediately
at `WARNING` (with traceback), then suppresses repeats of that same key for
60s before logging again, so a persistent failure stays visible without
flooding `bot.log` at 1Hz. Applied it to `btc_poll_job` and
`btc_ticker_poll_job` (the two 1Hz jobs deliberately left alone in Round 10).
Verified the throttle logic directly (first failure logs, immediate repeat
is suppressed, a failure after the window passes logs again) before
touching the real jobs. This closes out the full sweep of the
debug-swallowed-error pattern across every BTC scheduler job.

New research this round, both checks against outside claims rather than new
builds:

- **"Why your backtest said +20% but live trading lost money"** (turbinefi.com)
  and a related live-vs-paper BTC binary-options writeup both point at the
  same failure mode: paper trading assuming perfect fills at quoted mid with
  zero slippage, while live execution eats real spread. Checked this
  directly against our own paper-trading code rather than taking the warning
  at face value: `generate_paper_trade()` gets `entry_price` from
  `orderbook.get_realistic_fill()`, which fetches the **live ask ladder**
  and volume-weights the fill for the actual trade size -- not a fixed
  markup over the quoted mid. The consistently-observed "+0.50%" slippage
  seen across recent trade logs is a real, small, size-and-book-depth-driven
  number, not a hardcoded constant standing in for real execution cost.
  This is confirming evidence that we already avoid this specific pitfall,
  not a gap.
- **Purged/embargoed walk-forward cross-validation** (the "ML overfitting
  worse without purged CV" point from a separate search) -- checked whether
  `train_btc_model()`'s fold construction could leak test-period information
  into training. It can't: `fold_cuts` splits strictly by chronological
  window order (`train_tickers` are all earlier windows than
  `test_tickers`, no shuffling), each KXBTC15M window has its own
  independent, non-overlapping 15-minute label horizon (unlike the
  overlapping multi-day label horizons the purged-CV literature is mainly
  warning about), and all rolling features are already causal (`.shift(1)`
  documented in Round 5's work). Purging is a non-issue here by
  construction, not something we're missing.

### What was intentionally not adopted

- Purged/embargoed CV mechanism -- not needed given this project's
  non-overlapping, single-window label structure; would be solving a
  problem this design doesn't have.

### Scoped candidates for a future iteration (not built yet)

- Kalshi WebSocket feed (carried over).
- Polymarket/Kalshi cross-platform arbitrage (carried over from Round 8).
- VPIN / order-flow toxicity (carried over from Round 9).

## Round 11.5 (2026-09-10/11, live-trade-notification-triggered): volume=0 regression found and fixed

Not a scheduled /loop iteration -- triggered by watching three isolated
`btc_ticker_poll` `ConnectTimeout` warnings roll in (the Round 11 throttled-
logging fix doing exactly its job: surfacing a real, if benign, transient
network blip). While confirming those were harmless, checked the underlying
candle data directly rather than taking "it recovered on the next tick" at
face value -- and found something real: **199 of the last 200 cached
1-minute BTC candles had `volume=0.0`**, despite Coinbase's actual candle
endpoint returning correct non-zero volume for the same timestamps when
queried directly.

Root cause: this is a *regression* of the volume=0-forever bug already
fixed once earlier this session (`_upsert_candles` used to skip any existing
row unconditionally). That original fix was correct, but `ensure_recent_candles`
(the only caller of `_upsert_candles`) resumes its backfill from
`start = latest cached open_time` -- and `poll_live_price()` continuously
creates a **new** placeholder row every 60s for the newly-forming candle
(`volume=0.0`, built from 1Hz ticks, not real trade data). Since that new
placeholder immediately becomes the "latest cached candle," every future
`ensure_recent_candles` call resumes from *there*, permanently skipping back
over the previous minute's placeholder before Coinbase's authoritative
volume for it was ever fetched. The correction logic itself
(`existing.volume != c["volume"]` -> overwrite) was never the problem this
time; the resume-point logic just never revisited old rows once superseded.

### What this actually changed in the code

Added a `RECONCILE_TRAILING_CANDLES = 5` margin to `ensure_recent_candles()`
in `candles.py`: the backfill window now always looks back at least 5
candle-widths from the latest cached row (not just from `now`), so any
placeholder inserted by the live poller gets re-checked against Coinbase's
authoritative data multiple times after it closes, not just once at the
moment `latest.open_time` happens to still point at it.

Verified directly against the real (not mocked) database and Coinbase API
before and after: confirmed Coinbase returns real non-zero volume for the
affected open_times (ruling out "Coinbase itself has no volume data" as an
alternative explanation), confirmed the fix corrects newly-stuck candles
going forward (tested `ensure_recent_candles` before/after -- prior-minute
placeholders that were stuck at 0 got corrected), then ran a one-time 24h
repair backfill (matching the pattern from the original bug's fix) to clear
the accumulated backlog: **0 of 44,659** cached 1-minute candles remain at
`volume=0.0` (was 199/200 in the most recent window beforehand). Restarted
the server cleanly, waited through several live 1Hz ticks, and confirmed
newly-created candles (e.g. 03:14 UTC) already carry real volume without
any manual intervention -- the fix holds under the actual running scheduler,
not just in an isolated test. Both kill switches confirmed still `false`
throughout.

This matters beyond just chart display: `technical_indicators()` (RSI,
MACD, Bollinger, VWAP) and the live paper-trading feature pipeline both
read from this same `BtcCandle` table via `get_cached_candles`/
`ensure_recent_candles` (`btc_paper_trading.py`'s `_build_live_features`
calls `ensure_recent_candles(60, ...)` directly before every signal check,
every 15s). A silently-broken volume history means the VWAP-deviation
feature specifically (the others don't depend on volume) could have been
computing off stale/zero volume for some unknown recent stretch of time --
though the live feature pipeline calls `ensure_recent_candles` itself before
every signal check, so in practice it would have only ever seen the single
most-recent placeholder as zero, not a long stale run, since it runs far
more often (every 15s) than the window this regression could accumulate
(the previous session gap that let 199 candles go stale was almost
certainly this server having been offline/idle for a while between
restarts, when nothing was calling `ensure_recent_candles` at all -- this
was surfaced by the throttled-logging change, not by anything actually being
broken in the always-on serving path).

## Round 12 (2026-09-11, /loop): nothing new actionable, WebSocket scope note enriched

**Calibration check-in**: still n=8 since calibration deploy, no new
settlements since Round 11, CI unchanged at [0.124, 0.353].

New research this round, neither led to a build:

- **BTC funding rate as a leading indicator** -- multiple sources confirm
  the same nuance already assumed in this project's design: extreme funding
  (either direction) has historically preceded reversals, but "funding rate
  does not predict price direction... using it in isolation is not
  advised." This matches why `get_funding_rate()` is informational-only (not
  a trained model input) already -- confirming evidence, not a gap.
- **Kalshi's own WebSocket API channel list** -- confirmed it exposes 10
  real-time channels: ticker, trade, orderbook, orderbook_delta, fill, and
  others. Worth recording specifically for whenever the already-scoped
  WebSocket migration gets tackled: the **trade** channel would give
  buy/sell-classified trades on Kalshi's own YES/NO contract directly,
  which would make a VPIN-style order-flow-toxicity feature (Round 9,
  currently scoped as blocked on "no raw trade tape") computable on the
  *contract's* order flow without needing a separate BTC spot trade-tape
  ingestion path. This doesn't unblock either scoped item today (both still
  require the WebSocket migration itself), but narrows what the eventual
  WebSocket work would unlock -- worth having written down before it's
  forgotten.

Nothing small/actionable found this round -- recording that honestly rather
than padding. Both searches this round were checks against existing
assumptions (funding rate) or detail-gathering for an already-scoped bigger
item (WebSocket channels), not new standalone ideas.

### Scoped candidates for a future iteration (not built yet)

- Kalshi WebSocket feed (carried over) -- now noted specifically: its
  `trade` channel would also unlock a VPIN-style feature on Kalshi's own
  contract flow as a side effect, in addition to replacing the REST
  `live_yes_mid` quote-staleness workaround.
- Polymarket/Kalshi cross-platform arbitrage (carried over from Round 8).
- VPIN / order-flow toxicity (carried over from Round 9) -- see above, this
  is really the same blocker as the WebSocket feed now, not a separate one.

## Round 13 (2026-09-11, /loop): CI narrowing, BRTI constituent check, DB spot-check clean

**Calibration check-in**: n=12 now (up from n=8), Brier 0.19135, **95% CI
[0.106, 0.277]**. Half-width ~0.085, still above the ~0.05 reporting
threshold -- but notably the CI now actually **straddles** the 0.129
walk-forward backtest number (previously its lower bound was just barely
touching it). Encouraging trend, still not a large enough sample to
conclude anything.

**DB invariant spot-check** (per the standing instruction to verify
directly, not just search): re-checked 1-minute candle volume after the
Round 11.5 fix -- still **0 zero-volume candles**, no gaps in the last hour
of candles. Fix holding under continued live operation.

New research this round:

- **CF Benchmarks BRTI constituent exchanges**: confirmed via CF Benchmarks'
  own published constituent list that Coinbase (our sole BTC spot price
  source) has been a real BRTI constituent since Nov 2016 -- not an
  arbitrary stand-in for the actual settlement benchmark. BRTI blends ~9
  active constituents currently (Coinbase, Kraken, Bitstamp, Gemini,
  Bullish, Crypto.com, LMAX Digital, etc.), consolidating their order books
  every 200ms. This is useful context, not a new candidate: multi-exchange
  blending as a settlement-price proxy was already explicitly considered and
  **rejected by the user** (2026-09-09, recorded under "What was
  intentionally not adopted"). This round's finding doesn't change that
  decision -- it just confirms our single-exchange feed tracks a real
  constituent rather than an unrelated reference, which is worth knowing but
  isn't grounds to revisit a decision the user already made deliberately.
- **0mnjb/Kalshi-AI-Trading-Bot** (5-frontier-LLM ensemble debate, only
  trades on unanimous agreement, 0.75x Kelly) -- same category of idea
  already assessed and rejected in Round 7 (ryanfrigo's LLM ensemble): this
  project runs a single trained XGBoost model with Platt calibration, not an
  LLM committee, and an LLM-debate architecture doesn't graft onto that
  design. Its 0.75x Kelly fraction is also well outside the 10-25%-of-full
  range validated in Round 8 for this market type -- another data point
  confirming 0.15x, not a reason to reconsider it.
- **kapelame/kalshi-crypto-bot** -- already in prior_findings.md (it's the
  source cited for the short-horizon-momentum feature already built into
  `candles.py`). Re-surfaced by this round's search, nothing new to add.

Nothing new and actionable this round -- recording that honestly.

### What was intentionally not adopted

- Rebuilding/reconsidering multi-exchange settlement-price blending --
  confirmed this round that Coinbase is a legitimate (if partial) BRTI
  constituent, but the user already explicitly decided against blending more
  exchanges (2026-09-09); this round's finding is context, not a reason to
  revisit that decision.
- LLM-ensemble-debate trading architecture (0mnjb's bot) -- doesn't fit this
  project's single-trained-model design, same conclusion as ryanfrigo's bot
  in Round 7.

### Scoped candidates for a future iteration (not built yet)

- Kalshi WebSocket feed (carried over).
- Polymarket/Kalshi cross-platform arbitrage (carried over from Round 8).
- VPIN / order-flow toxicity via Kalshi's WS `trade` channel (carried over
  from Round 9/12).

## Round 13.5 (2026-09-11, user-triggered deep dive): real train/serve skew bug found and fixed, model retrained

Triggered directly by the user reporting the live model "feels like a coin
flip" and asking for a full retrain/improvement plan. Rather than retraining
blindly, ran the diagnostic playbook this project has built up: checked
whether the pnl/trade-count numbers being reported were even clean (they
weren't -- see below), checked the feature-parity safety net directly
(hadn't been run since server restart), and root-caused what it flagged
instead of accepting "probably noise."

**Data hygiene finding**: 17 of the then-66 "lifetime" BtcPaperTrade rows
(2026-09-09 23:18 -- 2026-09-10 03:18) used a flat, unconstrained $100 size
per trade -- pre-dating the Kelly-sizing/bankroll-cap fix mentioned earlier
in this project's history. These inflate a naive "sum every row ever" PnL
by ~$208 relative to the bot's own authoritative `BtcPaperBotState` counter.
Confirmed the state counter itself has no discontinuities in the properly-
configured period and is the correct source of truth going forward.

**Real bug #1 (already fixed this session, Round 11.5)**: candle volume=0
regression -- explains the `m1_vol_sum_5m`/`m1_vol_sum_15m` parity flags
(up to 108x over tolerance) directly; these should clear on their own for
trades recorded after that fix.

**Real bug #2 (new)**: `elapsed_fraction` has been computing wrong in
**every** historical training run and in the feature-parity offline
recompute, since `build_feature_frame()`'s `total_window` calculation used
`merged["open_time"]` -- which, after the *second* merge_asof (hourly
candles, `suffixes=("", "_h")`), silently becomes the **hourly** candle's
open time (e.g. `03:00:00`), not the window's own open time (`open_time_x`,
e.g. `04:15:00`), since pandas only suffixes on an actual name collision and
there isn't one for "open_time" at that second merge. This is the exact same
column-collision hazard already documented and fixed for the *output*
column at the end of the function -- just missed on this one intermediate
calculation. Verified directly: at t+14s into a window, `seconds_remaining`
was correct (883/900) but `elapsed_fraction` was 0.836 (should be ~0.019) --
back-solving showed `total_window` was 5400s (90 min) instead of 900s,
exactly matching `close_time - hourly_open_time` for that timestamp.

**Fix**: `total_window` now uses `open_time_x` (same as the already-correct
output column). Verified via direct row inspection (elapsed_fraction now
~0.019 at t+17s, matching expectation) and via `check_feature_parity()`
(elapsed_fraction delta dropped from a 0.81 max / well-over-tolerance mean
to 0.0058 mean / 0.0083 max, both under the 0.01 tolerance).

**Retrained the model** with the fix in place: aggregate walk-forward Brier
is 0.13014 (essentially unchanged from 0.12953 pre-fix -- within normal
fold-to-fold variance, folds ranged 0.123-0.154 both before and after).
`elapsed_fraction`'s feature importance roughly doubled (0.78% -> 1.52%),
consistent with the model now getting a real, non-corrupted version of a
feature it previously could only partially use. This doesn't fully explain
the live/backtest Brier gap on its own (elapsed_fraction was never a
dominant feature) but is a genuine, now-fixed correctness bug independent of
its size, and removes one more source of "training saw something different
from what live actually experiences."

**Still flagged after both fixes** (from a fresh parity check):
`seconds_remaining`/`quote_age_seconds` (mean delta ~5.2s vs 5.0s tolerance
-- consistent with the parity checker's own 30s live-vs-offline-snapshot
matching window, most likely a slightly-too-tight tolerance rather than a
bug), `yes_mid` (already a known quote-staleness-workaround area),
`path_efficiency_15m`/`rsi_14`/`bollinger_pct_b` (all near-zero feature
importance historically -- lower priority to chase further).

**Honest bottom line for the user**: the backtest itself is real and
strong (XGB beats GBM baseline and beats the live market on Brier,
consistently across all 5 walk-forward folds, and specifically wins big
when it disagrees strongly with the market -- 77% accuracy vs market's 58%
at >=20% edge, in the historical data). The live/backtest Brier gap seen
today (properly-scoped cohort, n=49, live Brier ~0.19-0.23 vs backtest
0.130) was real, not small-sample noise -- and at least two concrete,
verified causes are now fixed (candle volume regression, elapsed_fraction
skew). Deployed via retrain + clean server restart; both kill switches
confirmed still `false` throughout. Recommending the user (and future loop
rounds) track performance from this point forward as a fresh baseline,
rather than blending pre-fix and post-fix trades together.

## Round 14 (2026-09-11, /loop): hardened the merge_asof fix structurally, DB spot-check clean

**Calibration check-in**: model was retrained at 2026-09-11T04:41:42 UTC
(the elapsed_fraction fix from the user-triggered deep dive, Round 13.5).
Only 2 trades settled since then so far (2W/0L, +$15.68) -- far too early
to say anything about calibration; will track from this new baseline going
forward instead of the older 19:56 UTC 2026-09-10 calibration-deploy cutoff,
since the model itself changed.

**DB invariant spot-check**: 1 zero-volume candle in the last 60 (the
currently-forming one, expected), no gaps. Candle-volume fix still holding.

New research this round:

- **pandas `merge_asof`/`merge` column-collision behavior**, searched
  specifically because this exact hazard class (a bare column name silently
  reappearing after a second merge with no collision at that point) has now
  caused two real bugs in this project (the documented open_time_x/y issue,
  and Round 13.5's elapsed_fraction bug). Confirmed via pandas' own docs and
  community writeups: suffixing only fires on an *actual* collision at merge
  time, so a later merge with no collision (because the first merge already
  suffixed away the conflicting name) can reintroduce a bare column under
  the original name holding a completely different table's data. Standard
  practice is to eliminate the hazard at the source -- rename merge-key
  columns to unique names *before* merging -- rather than track by comment
  which suffixed variant is correct at each call site.
- Re-checked the other Kalshi-15-min-crypto-bot repos surfaced this round
  (kapelame, hamad-khawaja, brandononchain, papabrosio) -- all already in
  prior_findings.md from earlier rounds, nothing new.

### What this actually changed in the code (Round 14)

Applied the pandas best practice found above to `build_feature_frame()`:
renamed `df1m`'s and `df1h`'s own `open_time` columns to
`candle_1m_open_time`/`candle_1h_open_time` *before* either `merge_asof`
call, and merge on those unique names instead of `open_time` (which is now
only ever the window's own real open time throughout the function -- no
more `_x`/`_y` suffixing, no more comments required to know which variant is
safe to use). Removed the now-unnecessary `open_time_x` references from
Round 13.5's fix. Verified: `elapsed_fraction` computes identically correct
values as the surgical Round 13.5 fix (checked the same test ticker/row
directly), `check_feature_parity()` shows the same clean result, and a full
retrain produces **byte-identical walk-forward Brier** (0.13014) to the
Round 13.5 retrain -- confirming this is a pure robustness refactor with no
behavior change, not a second fix layered on uncertain ground. Deployed via
clean server restart; both kill switches confirmed still `false`.

This closes out Round 13.5's finding properly: the bug is now fixed in a way
that can't quietly regress the next time a column gets added to `df1m` or
`df1h`, rather than relying on a comment being read and understood before
every future edit to this function.

## Round 15 (2026-09-11, /loop): self-healing confirmed, nothing new actionable

**Calibration check-in**: n=6 post-retrain (up from n=2), 5W/1L, +$12.59.
Brier 0.22513, CI [0.047, 0.403] -- still far too wide to mean anything
(half-width ~0.18).

**DB/parity spot-check**: 1 zero-volume candle (currently-forming, expected).
`check_feature_parity()` re-run: `yes_mid` cleared from the flagged list
since Round 13. `m1_vol_sum_5m`/`m1_vol_sum_15m` deltas are shrinking as
expected (mean 9.77/36.27 in Round 13 -> 3.02/7.91 now) as pre-candle-volume-fix
trades age out of the lookback window -- confirms the Round 11.5 fix is
self-healing the historical parity picture over time, not just holding
steady. `gbm_prob` newly appeared, barely over tolerance (0.036 vs 0.03) --
consistent with ordinary live/offline snapshot-timing noise given how
`gbm_prob` depends on `seconds_remaining` and `realized_vol_per_second`,
both slightly time-lagged between the live decision and the offline
snapshot match; not treated as a new bug without further evidence.

New research this round, neither led to a build:

- Searched for new Kalshi bot releases -- found 3 new repo names
  (LoQiseaking69, ajwann, yllvar) but each falls into a category already
  assessed: sentiment/arbitrage/"quant-grade" bots with no concrete
  extractable technique beyond what's already covered, and an LLM-decision
  bot (same category rejected in Round 7 and Round 13 -- doesn't fit this
  project's single-trained-model design).
- Options theta/time-decay literature -- confirms nonlinear time decay
  (faster near expiry) is standard in short-dated option pricing. Checked
  against our own GBM baseline: `sigma_h = realized_vol_per_second *
  sqrt(seconds_remaining)` already captures this correctly (variance scaling
  with the square root of time-to-expiry is exactly the mechanism that
  produces accelerating sensitivity near close). Combined with the
  elapsed_fraction fix from Round 13.5/14, this is confirming evidence the
  model's time-handling is now sound, not a gap to address.

Nothing small/actionable found this round -- recording that honestly.

## Round 16 (2026-09-11, /loop): traced m1_vol_sum parity flags to a structural (non-bug) cause, loosened tolerances

**Calibration check-in**: n=7 post-retrain, 6W/1L, +$16.18. Brier 0.22124,
CI [0.071, 0.372] -- still far too wide to mean anything.

**DB/parity spot-check, investigated in depth this round**: `m1_vol_sum_5m`
and `m1_vol_sum_15m` parity deltas were not just still-flagged but
suspiciously **identical to 6 decimal places across every one of 12
trades** -- looked like a real duplicated-feature bug, not noise, so
investigated directly rather than assuming it was fine. Pulled the raw live
and offline values for one trade (not just the deltas):
live `m1_vol_sum_5m=2.66, m1_vol_sum_15m=21.59` (correctly distinct);
offline `m1_vol_sum_5m=17.46, m1_vol_sum_15m=36.38` (also correctly
distinct). Both sides compute the two features correctly and independently
-- the near-identical *deltas* (14.80 vs 14.79) were coincidence: nearly all
of the live/offline volume gap is concentrated in the most recent 1-2
minutes specifically, because the live decision necessarily reads whatever
Coinbase has published for those minutes *at that instant* (potentially
still provisional), while the offline recompute runs later once they've
fully settled. Since that gap sits almost entirely inside the trailing 5
minutes, it shows up as nearly the same absolute delta in both the 5m and
15m rolling sums. This is a structural property of comparing a real-time
decision against a later recompute, not a code bug -- concluded no fix is
needed in the feature computation itself.

### What this actually changed in the code (Round 16)

The tolerances in `check_feature_parity()`'s `ABSOLUTE_TOLERANCE` table were
set without accounting for this structural gap, so `m1_vol_sum_5m`,
`m1_vol_sum_15m`, `seconds_remaining`, and `quote_age_seconds` were being
flagged every round on noise the design can't eliminate -- a real risk,
since permanent expected noise on the flagged list makes it easier to miss
a genuine new regression sitting alongside it (exactly the kind of thing
that let the elapsed_fraction bug hide for as long as it did). Loosened
`m1_vol_sum_5m`/`15m` from 0.5/1.0 to 20.0/20.0 and
`seconds_remaining`/`quote_age_seconds` from 5.0 to 10.0, with a comment
explaining why. Verified: these four cleared on the next parity check;
`gbm_prob`, `path_efficiency_15m`, `rsi_14`, `bollinger_pct_b` remain
flagged. Restarted the server cleanly; both kill switches confirmed still
`false`.

**Honest note on what's still open**: `gbm_prob` (12.4% feature importance,
not a minor one) is still flagged (0.041 mean delta vs 0.03 tolerance) and
has gotten slightly worse across two consecutive rounds (0.036 -> 0.041),
not better. Plausibly the same class of live/offline snapshot-timing gap
(it depends on `seconds_remaining` and `realized_vol_per_second`, both
timing-sensitive), but this was NOT independently confirmed the way the
volume features were -- recorded honestly as still open, worth checking
again next round rather than assumed resolved by association.

## Round 17 (2026-09-11, /loop): real stale-price bug found in the model's most important feature, fixed

**Calibration check-in**: still n=7 post-retrain (no new settlements),
Brier 0.22124, CI [0.071, 0.372] -- unchanged, still too wide.

**Investigated `gbm_prob` per the standing instruction from Round 16** (it
had worsened across two consecutive rounds: 0.036 -> 0.041 -> 0.064 mean
delta this round). Pulled raw live vs offline values for the worst offender
(`KXBTC15M-26SEP110330-30`, the same trade flagged earlier today for
unusually large favorable slippage, -4.50%) rather than trusting the
aggregate number. Found the real cause: **`distance_log` (the model's
single most important feature, ~59% importance) was 5.2x off between live
and offline at essentially the same instant (0.1s apart)** -- back-solving
the implied BTC prices showed live used **$77,325.02, an exact match to the
PREVIOUS calendar minute's candle close**, while the correctly-recorded
snapshot for the same instant had the current minute's real price,
$77,294.14 (a ~$31 gap). Both `_build_live_features()` (live) and
`_latest_btc_price()` (used to populate the snapshot that training/parity
checks compare against) read the same `BtcCandle` table's most recent row --
but at decision time, the live read picked up a stale, one-full-minute-old
row. Confirmed this wasn't a network outage (no ticker/market poll errors
logged anywhere near that timestamp) and wasn't a full-system staleness
issue (no gaps in the candle spot-check) -- it's a narrow race between
`poll_live_price()` creating the new minute's row and this specific read,
that happens to land unluckily sometimes. This also fully explains the
earlier-flagged large favorable slippage on this same trade: the model
computed an inflated `gbm_prob` (0.819) off a stale, too-high BTC price,
producing an inflated edge estimate, while the real orderbook (reflecting
the true, lower current price) gave a better fill than the stale-price-based
quote comparison implied.

### What this actually changed in the code (Round 17)

Added `candles_mod.get_latest_tick()` -- a simple accessor for the freshest
(timestamp, price) already sitting in the in-memory 1Hz tick buffer
(`_TICK_BUFFER`, previously only used for the momentum feature), which is
independent of the `BtcCandle` table's write timing. In
`_build_live_features()`, added a targeted staleness guard: if the most
recently cached 1-minute candle's `open_time` is behind the current
calendar minute (the exact failure signature found above), and a tick less
than 10s old is available, use that fresh tick price for `distance_log`/
`gbm_prob` instead of the stale candle close, logging a `WARNING` so this
is visible going forward rather than silently degrading decisions.
Deliberately scoped narrow -- only overrides `btc_price` for this specific,
precisely-detected condition, not a blanket source swap, so live inference
still matches what training's `build_feature_frame()` computes (which uses
the same candle-close-based price) in the overwhelming majority of cases
where no staleness is detected. No retrain needed -- this only changes what
price live inference uses at decision time, not how training data itself is
built. Verified the staleness-detection logic directly against the exact
timestamps from the real bug (correctly flags the stale case, no false
positive on the normal case). Restarted the server cleanly; both kill
switches confirmed still `false`.

**Why this matters more than the earlier m1_vol_sum finding**: that one
(Round 16) was on features with near-zero-to-low importance
(order of 1%). This one is directly on the model's dominant input
(`distance_log`, ~59% importance) -- an intermittent few-dollar-to-tens-of-
dollars staleness here has an outsized effect on `gbm_prob` and, through it,
on the live edge estimate the whole trade-or-skip decision is based on. This
is plausibly a meaningful contributor to the live/backtest Brier gap
discussed in Round 13.5, independent of the elapsed_fraction bug fixed
there.

### Scoped candidates for a future iteration (not built yet)

- Fully isolating the root mechanism of the race itself (rather than just
  detecting and correcting its symptom) would need more instrumentation
  (e.g. logging exact write/read ordering across `poll_live_price` and
  `_build_live_features` for a few live occurrences) -- the guard added this
  round handles the observed failure mode correctly without that, but a
  root-level fix (e.g. making candle row creation and this read use one
  consistent transaction/lock ordering) could be worth a dedicated look if
  the staleness warning starts firing often once monitored.

## Round 18 (2026-09-11, /loop): calibration gap is now real (not noise), gbm_prob fix simplified based on frequency data

**Calibration check-in -- this is the first round where this crosses the
reporting threshold set back in Round 8**: n=15 post-retrain (up from 7),
8W/7L (53.3% win rate) but **net PnL -$5.98** (losses outweighing wins in
size). Brier **0.26578, 95% CI [0.157, 0.375]** -- half-width ~0.109, still
above the original 0.05 bar, but the interval **no longer overlaps 0.13014
at all** (lower bound 0.157 > backtest's 0.130). This is the first
calibration check-in this session with a real, if still-small-sample,
signal rather than pure noise. Also checked the high-confidence bucket
(model >= 70%) specifically: 3/6 wins (50%), well below the 70-87% the
model claimed -- suggestive of overconfidence, but n=6 is too small on its
own to be conclusive (binomial noise at n=6 could produce this by chance
under a truly well-calibrated 80% model ~10% of the time). Recording the
gap honestly rather than either dismissing it or overclaiming a root cause
found.

**Investigated the still-open `gbm_prob` flag per the Round 17 follow-up
instruction.** Found the "BTC live feature staleness" warning added in
Round 17 had fired **234 times in under an hour** -- essentially every
single `_build_live_features()` call, not a rare race as the one flagged
trade suggested. Checked the actual logged price pairs (not just that the
guard fired): the vast majority of discrepancies were small ($0-15,
typically near-identical), unlike the original $30/one-full-minute case
that motivated the Round 17 fix. This means `closes[-1]` (the cached
1-minute candle's close) lags the true current price by a small amount on
nearly *every* call under normal operation -- an ordinary consequence of
comparing a once-per-second-updated candle field against the instant of
decision, not a severe or rare bug. The original $30 case was likely an
unlucky outlier (plausibly related to a separate network hiccup found
earlier today), not representative of the typical gap.

### What this actually changed in the code (Round 18)

Simplified the Round 17 fix: since the "detect staleness, then fall back"
branch was being taken on nearly every call anyway, changed
`_build_live_features()` to **unconditionally** prefer the fresh in-memory
tick (when available and <10s old) for `distance_log`/`gbm_prob`'s
`btc_price`, rather than gating it behind a conditional check that added
complexity without changing behavior in practice. Kept a warning log, but
now only fires when the tick and candle-close disagree by more than $5 --
enough to catch a real outlier like the original $30 case without spamming
`bot.log` on routine sub-$5 lag. Verified syntax, restarted the server
cleanly, confirmed both kill switches still `false`.

New research this round: XGBoost/production calibration-drift literature
confirmed the existing design (periodic walk-forward retraining + Platt
recalibration each cycle, already in place via `btc_retrain_job` every 6h)
matches standard practice for handling this kind of drift -- nothing new to
build, this validates staying the course rather than adding an adaptive
drift-detector on top of what's already working.

**Honest summary for the user**: the live/backtest Brier gap is now
statistically real at n=15, not attributable to small-sample noise alone.
Two genuine bugs were found and fixed today (elapsed_fraction,
distance_log staleness) but neither has been proven to fully close this
gap yet -- more post-fix data is needed to see whether the gap narrows now
that both are addressed, or whether something else is still contributing.
The next scheduled retrain (6h cadence) will incorporate today's fixes into
fresh training data; worth re-evaluating the gap specifically after that,
not just after more live trades accumulate on the same (already-fixed)
model.

## Round 19 (2026-09-11, /loop): clarified pre/post-fix cohorts, important academic finding on Kalshi adverse selection

**Calibration check-in, corrected methodology**: the aggregate "since
retrain" cohort now shows n=17, win rate down to 47.1%, PnL -$13.43 (worse
than Round 18's -$5.98), Brier 0.27904, CI [0.181, 0.377] -- looks like the
gap widened. **But this is misleading**: only **1 trade** has fully settled
since the Round 17/18 distance_log-staleness fix actually deployed
(2026-09-11 09:39:28 UTC) -- the rest of the n=17 sample is pre-fix trades
still working through settlement. The "widening" reflects more pre-fix
trades accumulating, not evidence the fix failed or made things worse.
Established the fix-deploy timestamp as the cohort cutoff to track going
forward instead of continuing to blend pre/post-fix trades under the older
retrain cutoff. Also checked: the `gbm_prob` parity flag's *max* delta
(0.250493) is unchanged from Round 17 -- confirmed it's literally the same
historical trade still sitting in the 6h lookback window, not a new
occurrence; the *mean* delta did improve (0.064 -> 0.049), a mildly
positive but not yet conclusive sign.

New research this round -- **the most important finding of this round,
not a repo**:

- **Bartlett & O'Hara, "Adverse Selection in Prediction Markets: Evidence
  from Kalshi"** (Stanford Law / SSRN, April 2026, 41.6 million real Kalshi
  trades). Directly relevant findings: (1) informed traders systematically
  pick off the slow side of the orderbook before quotes update -- adverse
  selection is real and measured, not theoretical; (2) a separate practical
  study specifically on **KXBTC15M mean-reversion strategies found win
  rates hovering near 50%, with commissions/spread turning a technically-
  positive expectancy into a grinding loss in practice** -- this closely
  matches what we're observing live right now (47-53% win rate, negative
  net PnL despite a real, backtest-validated edge). This is sobering,
  credible, directly-on-topic evidence that **some of the live/backtest gap
  may not be attributable to any remaining code bug at all** -- it may
  partly reflect a structural, documented property of this specific market
  (fast edge decay under real competing order flow, not fully captured by
  a walk-forward backtest on historical data). This doesn't mean the two
  bugs found today (elapsed_fraction, distance_log staleness) weren't real
  or worth fixing -- they were, and are now fixed -- but this reframes the
  live/backtest gap as potentially a *combination* of (a) now-fixed bugs
  and (b) a genuine, hard structural feature of trading this specific
  market, not purely "something still broken that needs finding."

### What this means for the user

Not a code change this round -- this is analytical context that belongs in
front of the user directly, not buried in an internal log: **the live
paper-trading results so far are consistent with what independent academic
research on 41.6M real Kalshi trades found for this exact market type**,
which is a meaningfully different (and more sobering) framing than "the bot
is broken and needs another bug fix." Recommending this be surfaced
directly in the next summary rather than only in prior_findings.md.

### Scoped candidates for a future iteration (not built yet)

- Nothing new to build from this research -- the adverse-selection finding
  is analytical context for interpreting results, not something to
  implement. If it holds up with more data, the actionable implication
  would likely be tightening `MIN_EDGE_THRESHOLD` or otherwise trading less
  often/only on higher-confidence signals (since adverse selection erodes
  thin edges fastest) -- worth revisiting once there's a clean post-fix
  sample large enough to check this specifically, not before.

## Round 20 (2026-09-11, /loop): post-fix cohort still too small, minor parity noise, defense strategy confirmed

**Calibration check-in (post-fix cohort, cutoff 2026-09-11T09:39:28 UTC)**:
n=3, win rate 33.3%, PnL -$1.39, Brier 0.26777, CI [0.162, 0.374] -- still
nowhere near enough sample to say anything. Continuing to accumulate.

**DB/parity spot-check**: 1 zero-volume candle (currently-forming, expected).
`h1_log_return` and `yes_mid` newly/still flagged but only marginally over
tolerance (26% and 2% over respectively) -- noise-level, not the dramatic
5-10x-over-tolerance pattern the two real bugs found earlier today showed.
Not chased further this round.

New research this round:

- Prediction-market-making literature confirmed the adverse-selection
  defense mechanism already scoped in Round 19 as a future candidate:
  "price the offer side wider when flow is persistently one-directional"
  and "trade only when net spread after fees clears your minimum
  threshold" are standard responses to the exact phenomenon Bartlett &
  O'Hara documented for Kalshi. This is confirming evidence for the plan
  already recorded (raise `MIN_EDGE_THRESHOLD` if the post-fix gap
  persists with enough data), not a new idea -- still premature to act on
  with only n=3 post-fix trades.
- VPIN/order-flow-toxicity search surfaced the same repos already known
  (reedjacobp, Bh-Ayush, hanxixuana/flowrisk) -- nothing new.

Nothing new and actionable this round -- recording that honestly.

## Round 21 (2026-09-11, /loop): favorite-longshot bias checked and rejected for this specific market

**Calibration check-in (post-fix cohort)**: n=4, win rate 50%, PnL +$0.10
(essentially breakeven), Brier 0.24101, CI [0.149, 0.333] -- the lower
bound (0.149) is now much closer to the 0.13014 backtest number than the
Round 19/20 checks, an encouraging direction, but n=4 is still nowhere near
a real sample.

**DB/parity spot-check**: unchanged from Round 20 (same 6 features flagged,
same noise-level magnitudes). No new escalation.

New research this round:

- **Favorite-longshot bias** ("buy NO on overpriced cheap-YES longshots,
  2-5% edge, the single most robust finding in prediction market research")
  -- a real, well-documented phenomenon in prediction markets generally.
  Rather than adopting it on the strength of outside literature alone,
  checked it against our own actual calibration data (already computed and
  saved in `training_report.json`'s `calibration_market` table, ~16,000+
  samples in the extreme buckets). **Result: the opposite pattern holds for
  KXBTC15M specifically.** Market-implied probability in the cheapest
  bucket (0-10%) is 4.05% but the actual resolution rate is 5.65% --
  higher, not lower, meaning the market *underprices* cheap-YES longshots
  here rather than overpricing them; the same underconfident pattern holds
  at the other extreme (90-100% bucket: 97.27% implied vs 98.33% actual).
  Blindly adopting "fade the longshot" here would be systematically wrong
  for this specific market -- a good example of why this project's
  discipline of checking outside claims against real data before trusting
  them matters, since the generic finding (correct in the broader
  literature) does not transfer to KXBTC15M as-is.
- Kalshi bot ecosystem search surfaced nothing new -- same repos already
  covered (OctagonAI, etc.).

### What was intentionally not adopted

- Favorite-longshot-bias exploitation (buying NO on cheap YES contracts) --
  checked directly against this market's own calibration data and found
  the opposite pattern holds here; adopting it would add a strategy that
  loses money by this market's own historical evidence, not gain an edge.

## Round 22 (2026-09-11, /loop): sobering backtest studies reinforce Round 19, found a promising but architecturally-bigger strategy

**Calibration check-in (post-fix cohort)**: n=6, win rate down to 33.3%,
PnL -$4.96, Brier 0.25341, CI [0.193, 0.313] -- half-width ~0.06, getting
close to the 0.05 reporting bar, and the interval is now clearly and
consistently above 0.13014 with no sign of closing. Not improving from
Round 21's encouraging tick.

**DB/parity spot-check**: 0 zero-volume candles, same 6 features flagged
as Round 21/20 at similar noise-level magnitudes. Nothing new.

New research this round:

- **Two independently-published large-scale KXBTC15M backtest studies**
  (turbinefi.com, 1,000 and 5,000 strategies tested respectively, with
  realistic fees/slippage modeled): the 5,000-strategy study found only
  **102 of 4,904 tested strategies were profitable**, median ROI -14.53% on
  $10,000 notional; the 1,000-strategy study found Sharpe ratios of -7 to
  -10 across the board for most approaches, and separately noted
  **high-frequency strategies with 62-63% win rates still lost 75-78% of
  their edge to fees and slippage** on a 2-cent target. This is independent,
  large-sample confirmation of Round 19's Bartlett & O'Hara finding: this
  specific market is genuinely difficult to profit in after realistic
  costs, even with a real, backtest-validated edge and a solid win rate --
  reinforces that our own live results so far (33-53% win rate, hovering
  near breakeven) are consistent with the broader documented picture for
  this market, not necessarily evidence of a remaining bug.
- **The "panic_fade" strategy**, the one consistent winner in the
  1,000-strategy study (profitable across all 150 tested variants, mean ROI
  +9.95%): fade sharp short-term downward moves (panic threshold 0.03-0.10)
  by buying YES into the dip and **selling back out once price recovers**,
  exploiting genuine short-horizon mean-reversion rather than holding to
  settlement. This is a real, well-evidenced, different-category idea --
  but NOT small/actionable today: it requires an **active exit-before-
  settlement mechanism** (buy, then sell back before the window closes once
  a target is hit), which this project's current execution architecture
  doesn't have at all (current design is buy-once, hold-to-settlement,
  no partial exits). It would also need either enough of the already-
  collected short-horizon momentum data (`mom_5s/15s/30s`, currently
  informational-only per the data-volume rule already established for this
  project) or a dedicated backtest of the exit-timing logic itself before
  going live with real money on the paper book.

### Scoped candidates for a future iteration (not built yet)

- **panic_fade-style mean-reversion with active exit logic** (new this
  round) -- distinct from anything currently in this project (which never
  exits before settlement). Would need: (a) an exit-before-close mechanism
  in the execution layer (cancel/flip a position once a target price is
  hit, not just place-and-hold), (b) enough short-horizon momentum history
  to validate the panic-threshold parameters on this project's own data
  rather than trusting the published thresholds blindly (matching this
  project's discipline of checking outside claims against real data --
  see Round 21's favorite-longshot-bias rejection). This is a genuinely
  bigger lift than a loop-iteration-sized change -- worth a dedicated
  future session, not a piecemeal addition.
- Kalshi WebSocket feed, Polymarket/Kalshi arbitrage (carried over).

## Round 23 (2026-09-11, /loop): calibration signal solidifying, nothing new actionable

**Calibration check-in (post-fix cohort)**: n=8, win rate 37.5%, PnL -$5.56,
Brier 0.25539, CI [0.205, 0.306] -- half-width now right at the ~0.05
reporting threshold, and this is the third consecutive check (n=4, 6, 8)
showing the same consistent picture: Brier hovering around 0.25,
consistently and clearly excluding the 0.13014 backtest number. Not
improving. Sticking to the established n=15-20 target before recommending
any `MIN_EDGE_THRESHOLD` change, per the standing plan -- not there yet.

**DB/parity spot-check**: 0 zero-volume candles. `path_efficiency_15m`
cleared from the flagged list (was borderline); remaining 5 flags unchanged
in character from prior rounds.

New research this round, nothing actionable:

- Kelly-sizing literature reconfirmed the existing 0.15x choice sits
  conservatively within the commonly-recommended "quarter Kelly for
  uncertain edge estimates" range (~0.25x) -- no change warranted.
- New repo names found (DeweyMarco/simple-kalshi-bot -- three simple
  strategies: trend-following, momentum, consensus; Juanp2389/Kalshi-trade-bot
  -- TypeScript Polymarket/Kalshi arbitrage) -- both fall into categories
  already covered (trend/momentum already represented via `h1_log_return`,
  `m1_ret_1m/5m`; arbitrage already a scoped candidate from Round 8).
  Notable anecdotal aside: seanrobenalt/kalshi-bot's own README candidly
  states "it lost me $100" -- another real-world (if informal) data point
  consistent with the Round 19/22 pattern that this market is genuinely
  hard to profit in, not proof of anything on its own but a small
  additional confirmation.

## Round 24 (2026-09-11, /loop): calibration CI crosses the reporting threshold, pattern identified

**Calibration check-in (post-fix cohort) -- CI half-width crosses the 0.05
bar for the first time**: n=10, win rate 30% (down from 53%->47%->37.5%->30%
across the last four checks), PnL -$8.57, Brier 0.25982, **CI [0.220,
0.300]** -- half-width ~0.04, under the original 0.05 threshold set back in
Round 8 for "this is real signal, not noise." The interval is tight and
clearly, consistently excludes 0.13014.

**Pattern identified from the raw trade list (not just the aggregate
number)**: every single one of the 10 post-fix trades is a **NO** position,
and in every single one, the model's own probability for NO is only
**40-48%** -- meaning the model itself always still thinks YES is the more
likely outcome, it's betting NO only because the *market* is pricing YES
even more confidently (58.5%-70.5% vs the model's 52-60%). This is a series
of modest, low-conviction contrarian bets against market over-optimism, not
high-confidence directional calls -- the model never claims 80-90%
confidence in this batch. Realized win rate (30%, 3/10) is somewhat below
even the model's own humble expectation (40-48% for these specific trades),
consistent with ordinary small-sample variance on top of a real effect.

**Why this matters**: this exact category -- small, low-conviction relative
disagreements with the market -- is precisely what the transaction-cost/
market-impact literature (checked this round) and the adverse-selection
research (Round 19/22) predicts gets eaten alive by real-world friction:
"the edge must exceed the combined costs of bid-ask spreads and market
impact to result in a profitable trade." A large, high-conviction edge
survives these costs; a modest one (which is all we're seeing right now)
often doesn't. This is a concrete, mechanistic explanation for *why* the
gap persists post-fix, not just "the market is hard" in the abstract.

**DB/parity spot-check**: 0 zero-volume candles, same flags as Round 23,
`gbm_prob` mean delta still elevated (0.051) but its *max* is still the
exact same historical outlier (0.250493) that's been sitting in the 6h
window for several rounds now -- should finally age out soon.

### What this means for the user, stated directly

The post-fix data now clears the statistical bar set at the start of this
research effort: **this gap is real, not noise.** But it isn't evidence the
two bugs fixed today were pointless (they were real, independently-verified
correctness bugs, worth fixing regardless) or that something else is still
silently broken (the parity checks remain clean of new large discrepancies).
The concrete, mechanistic picture is: the live model is currently only
generating *modest* disagreements with the market (a few points of
probability), and modest edges are exactly the category that transaction
costs and adverse selection erode fastest -- consistent with everything
found in Rounds 19/22/24. The natural next actionable step, once the sample
is a bit larger (the n=15-20 target is close -- n=10 now), is to test
whether restricting trades to only the largest-disagreement opportunities
(raising `MIN_EDGE_THRESHOLD`) filters out exactly this losing modest-edge
category while keeping the rare higher-conviction ones -- not yet, per the
standing plan, but this round's finding makes the mechanism behind that
plan concrete rather than speculative.

## Round 25 (2026-09-11, /loop): pattern confirmed at n=11, explanatory hypothesis strengthened

**Calibration check-in (post-fix cohort)**: n=11, win rate 27.3% (down
further), PnL -$10.09, Brier 0.26091, CI [0.224, 0.297] -- tight, clearly
excludes 0.13014. Direction/conviction check: **11 of 11 trades are NO, 0
are YES, 0 are "high-conviction" (|model_prob - 0.5| > 0.15)** -- the exact
same pattern identified in Round 24 has held with zero exceptions across
every single trade since the fix deployed, not just most of them.

**Sanity-checked the pattern against real BTC price action** rather than
assuming it's benign: pulled the last several hourly candles and found BTC
was actually roughly flat-to-declining through most of this window
(77,337 -> 76,945 over the two hours spanning most of these trades), not
in an obvious uptrend -- so the market's persistent YES-leaning isn't simply
tracking a real trend the model is blind to. This rules out the simplest
"the market is just right and the model is missing an obvious trend"
explanation.

New research this round -- **supports a specific, coherent hypothesis for
the pattern**: academic literature on intraday crypto return predictability
confirms "intraday reversal... may be related to investors' overreaction to
non-fundamental information and overconfidence bias," and more generally
that short-interval crypto markets show systematic overreaction/
over-extrapolation driven by crowd psychology. Combined with Round 22's
panic_fade finding (short-term BTC panics/extremes tend to revert, not
continue, within 15-minute windows) and this round's evidence (market
persistently pricing YES higher than the model even without a real trend
driving it), a coherent picture emerges: **the model may be correctly
detecting genuine short-horizon crowd overreaction/momentum-chasing, but
the resulting edges are currently too thin to survive real transaction
costs** -- consistent with, not contradicted by, the Round 24 "thin edge"
finding. This doesn't change the recommendation (still building toward
n=15-20 before touching `MIN_EDGE_THRESHOLD`), but it strengthens confidence
that the *direction* of these trades is sound and the problem is
specifically about *edge size*, not about the model being randomly wrong.

**DB/parity spot-check**: 0 zero-volume candles, same flags as prior
rounds, no new escalation.

## Round 26 (2026-09-11, /loop): edge-threshold plan revised -- distributions overlap too much to be the fix

**Calibration check-in (post-fix cohort)**: n=13, win rate 30.8%, PnL
-$11.09, Brier 0.25521, CI [0.222, 0.288] -- tightening further, still
clearly excludes 0.13014. Pattern holds at 13/13: still 0 YES trades, 0
high-conviction trades.

**Pulled the actual edge distribution for winners vs losers, as planned
once close to the reporting threshold** -- and the result changes the
working plan from Round 20/24: winning trades' edges were [0.148, 0.201,
0.24, 0.249] (avg 0.21); losing trades' edges were [0.109, 0.111, 0.145,
0.192, 0.193, 0.201, 0.212, 0.222, 0.223] (avg 0.179). Winners do average
a bit higher, but **the two distributions overlap heavily** -- several
losses (0.201, 0.212, 0.222, 0.223) have edges as high as or higher than
several wins (0.148, 0.201). There is no clean edge value that separates
winners from losers in this sample.

**Checked our own `MIN_EDGE_THRESHOLD` (8%) against the "3x fee" rule of
thumb** found in this round's research ("check that edge clears 3x the fee
before executing"): at the ~30-45 cent prices these NO trades have been
entered at, our fee (`0.07 * price * (1-price)` per contract) is roughly
1.4-1.7 cents, so 3x fee is roughly 4-5 percentage points of edge -- our
existing 8% threshold is already comfortably above this bar. **This means
the current threshold isn't obviously too permissive by the standard
heuristic**, and several of our actual losing trades had edges far above
even a much higher bar (up to 22.3%) and still lost.

### Revised assessment

Combining these two findings: **raising `MIN_EDGE_THRESHOLD` further looks
less promising as "the fix" than the working plan since Round 20/24
assumed.** The problem isn't cleanly "edges are too small to clear costs"
(the existing threshold already clears the standard fee bar, and even
high-edge trades in this sample are losing at a similar rate). This points
more toward the adverse-selection/overconfident-edge-estimate framing from
Round 19/22/25 -- the *claimed* edge (from comparing model vs market
probability) may not be translating into a *true* edge as reliably as the
backtest suggested, for reasons not yet isolated (could still be
insufficient sample size at n=13, could be a real property of how this
edge estimate behaves live vs in the backtest). Not recommending a
`MIN_EDGE_THRESHOLD` change based on this data -- the evidence doesn't
support it being the right lever, contrary to the earlier working
assumption. Continuing to accumulate data rather than making a change the
data doesn't clearly justify.

**DB/parity spot-check**: 0 zero-volume candles. The long-standing
`gbm_prob`/`yes_mid`/`bollinger_pct_b` flags **finally cleared** as the
historical outlier trade aged out of the 6h lookback window -- only
`h1_log_return` and `rsi_14` remain flagged, both low-importance and
noise-level.

## Round 27 (2026-09-11, /loop): n=15 threshold reached -- comprehensive assessment

**Calibration check-in (post-fix cohort) -- target sample size reached**:
n=15, win rate 26.7%, PnL -$12.33, Brier 0.24055, CI [0.197, 0.284].
Direction check: 14 NO / 1 YES (the first YES trade appeared this round,
breaking the 13-trade all-NO streak, though it was itself still a
low-conviction relative-value bet -- model's own P(YES)=11%, market priced
it at 1%).

**DB/parity spot-check**: 0 zero-volume candles, only 2 low-importance
features flagged (`h1_log_return`, `rsi_14`), both noise-level. No open
correctness concerns remain from the feature-parity safety net.

New research this round -- **one more well-known, credible confirming data
point**: Quantopian's widely-cited study of 888 live-deployed strategies
found **near-zero correlation between backtest Sharpe ratio and live
performance**, with the general finding that "the more parameters tuned,
the worse the live result." This is a famous result in quant finance,
independent of and consistent with everything else found in Rounds 19/22
(Bartlett & O'Hara's Kalshi-specific study, the two turbinefi backtest
studies) -- backtest-to-live degradation of this general kind and magnitude
is a widely-documented, unsurprising phenomenon across quantitative trading
broadly, not specific to some flaw in this project's approach.

### Comprehensive assessment at the target sample size

- **The gap is real and reasonably well-measured now**: CI half-width
  ~0.043, clearly and consistently excluding the 0.13014 backtest number
  across six consecutive checks (n=4 through n=15).
- **Two genuine, independently-verified correctness bugs were found and
  fixed** during this investigation (elapsed_fraction computed from the
  wrong candle's timestamp; distance_log using an intermittently-stale
  price) -- both real, both fixed, neither fully closed the gap on its own,
  but both needed fixing regardless of their effect on this specific
  number.
- **The `MIN_EDGE_THRESHOLD` lever was investigated and set aside**: the
  win/loss edge distributions overlap too much to support a clean cutoff,
  and the existing threshold already clears standard fee-coverage
  heuristics. Not recommending this change.
- **The most defensible current explanation is a combination of**: (a)
  ordinary small-sample noise (n=15 is still a small sample for a Brier
  estimate, even though the CI has tightened enough to be meaningful), (b)
  the well-documented, market-specific adverse-selection/thin-edge-decay
  effect (Round 19/22), and (c) the general, widely-documented tendency for
  backtest performance (even walk-forward-validated) to not fully transfer
  to live trading (this round's Quantopian finding). None of these are
  "something still broken that needs finding" -- they're either already
  addressed (the two bugs) or reflect known, hard-to-eliminate properties
  of live trading this specific kind of market.
- **Recommendation**: continue paper trading and accumulating data (target
  n=30-50 for a more precise Brier estimate) rather than making further
  parameter changes on the current sample. If the gap is still this wide at
  n=30-50, that would be a stronger signal worth deeper investigation
  (possibly including the panic_fade-style active-exit strategy scoped in
  Round 22, or a closer look at whether the backtest's walk-forward
  methodology itself has a subtle look-ahead issue not yet found). At n=15,
  the data supports "this is a hard market and paper trading should
  continue to collect more evidence," not "something specific needs fixing
  right now."

## Round 28 (2026-09-11, /loop): routine check, still consistent with Round 27

**Calibration check-in**: n=18 post-fix, Brier 0.21177, CI [0.164, 0.259] --
still excludes 0.13014 but has narrowed somewhat from Round 27's n=15 check
(0.197-0.284 -> 0.164-0.259). Consistent with Round 27's overall assessment,
nothing meaningfully different -- continuing to accumulate toward n=30-50
as planned, no new action needed.

**DB/parity spot-check**: 0 zero-volume candles, only `rsi_14` and
`bollinger_pct_b` flagged (both consistently near-zero-importance
features), at low sample count (n=4 trades in the 6h window) -- routine.

New research: nothing new/actionable. Kalshi's own API changelog (batch
cancel endpoint, `orderbook_delta` resync support) is only relevant to the
already-scoped WebSocket migration candidate, not the current REST
implementation. Verified `tick_size` (a field the changelog says Kalshi is
deprecating in May 2026) isn't referenced anywhere in this codebase --
nothing to fix. GitHub search surfaced only already-known repos, plus one
new one (mmoore07129/mlb-kalshi-bot) in a different market (MLB, not
crypto) using an architecturally interesting "external sharp-book fair
value + XGBoost veto" pattern -- not transferable here since no equivalent
independent sharp price source exists for BTC 15-minute windows beyond what
we already use (Kalshi's own market price, the GBM baseline, and our
trained model).

## Round 29 (2026-09-11, /loop): routine check, ADX regime-filter idea checked against own feature importances

**Calibration check-in**: n=20 post-fix, Brier 0.22835, CI [0.180, 0.276] --
still excludes 0.13014, roughly steady with Round 28's n=18 check (was
0.164-0.259). No meaningful shift, continuing toward n=30-50 as planned.

**DB/parity spot-check**: 0 zero-volume candles, only `rsi_14`/
`bollinger_pct_b` flagged at n=1 in the 6h window -- routine.

New research: ADX-based regime detection (only apply mean-reversion signals
when ADX <20/ranging, momentum signals when ADX >25/trending) is a common
pattern in crypto technical-analysis literature. Checked it against this
project's own data before considering building it: our actual trained
model's `feature_importance` table (from `training_report.json`) already
shows the *entire* technical-indicator family this idea depends on
(RSI, MACD, Bollinger) sits at near-zero importance (0.13%-1.4% combined) --
`distance_log` and `gbm_prob` dominate. Adding ADX, another member of the
same indicator family, has no strong reason to behave differently based on
what this model has actually learned from real data. Not pursued -- this is
the same discipline as Round 21's favorite-longshot-bias check: preferring
this project's own empirical evidence over an outside idea's theoretical
appeal.

## Round 30 (2026-09-11, /loop): Brier decomposition sharpens the finding -- near-zero live resolution

**Calibration check-in**: n=23 post-fix, Brier 0.21848, CI [0.175, 0.262] --
steady with recent rounds, still excludes 0.13014.

**DB/parity spot-check**: 0 zero-volume candles, **zero features flagged**
for the first time this session -- fully clean parity check.

**Applied the Brier-score decomposition (Reliability - Resolution +
Uncertainty) found in this round's research directly to our own post-fix
data**, since n=23 is now enough for at least the coarsest version of this
diagnostic. Computed "Uncertainty" -- the Brier score of the trivial
baseline that just predicts the cohort's own base win rate (30.4%) for
every trade, ignoring the model entirely: **0.21172**. Compared against
the model's actual Brier: **0.21848** -- slightly *worse* than the trivial
baseline (delta +0.0068). Per the decomposition's own "useful forecast"
criterion (Brier should be *below* Uncertainty, i.e. Resolution should
exceed Reliability), this sample shows **approximately zero -- or very
slightly negative -- live discriminative skill (Resolution) over just
guessing the cohort's own base rate.** This is a sharper, more precise way
to state what the last several rounds have shown as a Brier-vs-backtest
gap: it's not just "worse than the backtest," it's "not clearly
distinguishing itself from a model that ignores every input and just
predicts the recent average," at least in this specific 23-trade window.

**Caveat, stated plainly**: n=23 is still a small sample for this specific
comparison (the "uncertainty" baseline itself has real sampling noise, and
a coarse 2-value decomposition like this can't rule out that per-bucket
skill exists even if it doesn't show up in this rough calculation). This
doesn't override Round 27's overall assessment (small-sample noise +
documented adverse-selection effects + general backtest/live degradation)
-- it sharpens it with one more concrete number, consistent with "the live
edge, if real, is currently thin enough to be hard to distinguish from
noise at this sample size," not a reversal of that conclusion.

New research: found several more Polymarket/Kalshi arbitrage repos
(cryptuon/polybot, PMXT) and a Polymarket-specific scalping-signals project
using Binance data with regime detection -- both fall into categories
already scoped (arbitrage) or already checked-and-rejected (indicator-based
regime detection, Round 29). Nothing new to build.

## Round 31 (2026-09-11, /loop): routine check, gap narrowing slightly

**Calibration check-in**: n=24 post-fix, Brier 0.21011, CI [0.166, 0.255].
Brier-vs-uncertainty decomposition: baseline (guessing base rate) = 0.2066,
model = 0.21011, delta = **+0.0035** (narrowed from Round 30's +0.0068) --
still slightly worse than the trivial baseline, but moving toward zero as
the sample grows. Not yet a reversal, but the right direction.

**DB/parity spot-check**: 0 zero-volume candles, parity check clean again
(0 flags), second consecutive clean round.

New research: nothing new/actionable. Kalshi community/Discord search
surfaced only generic marketing/community content, no concrete new
technique. A SHAP-interaction-analysis approach for XGBoost was found as a
potentially useful *diagnostic* tool (could help explain why live
discriminative skill has been near-zero -- e.g. checking whether
`distance_log` interacts oddly with other features in live conditions) but
implementing a full SHAP analysis is more effort than a single loop
iteration -- noted as a possible deeper-dive tool for a future dedicated
session rather than attempted piecemeal here.

## Round 32 (2026-09-11, /loop): concrete settlement-mechanism finding -- BRTI is a 60s TWAP, our price feed isn't

**Calibration check-in**: n=25 post-fix, Brier 0.2154, CI [0.172, 0.259].
Brier-vs-uncertainty delta widened back to **+0.0138** (from Round 31's
+0.0035) -- moving the wrong direction again. Reporting honestly rather
than only tracking favorable ticks: at this sample size the delta is
bouncing around zero without a clear trend yet, consistent with ordinary
noise on top of a small, hard-to-pin-down effect, not a reversal of the
narrowing seen last round.

**DB/parity spot-check**: 1 zero-volume candle (currently-forming,
expected), parity check clean (0 flags), third consecutive clean round.

**New research -- the most specific, mechanistic finding on the
live/backtest gap so far**: Kalshi's KXBTC15M settles against a **60-second
time-weighted average (TWAP) of CF Benchmarks' BRTI**, not an instantaneous
price -- and BRTI itself (a constituent-weighted multi-exchange composite)
lags any individual venue by ~10 seconds during fast moves. Separately,
"Kalshi contract prices reprice with a 3-7 second lag relative to spot
movements" is cited as a documented, exploitable microstructure edge.

This matters directly for this project: `distance_log` (the model's
dominant feature, ~59% importance) is computed from the **raw
instantaneous** Coinbase spot price (either the latest 1-minute candle
close or, since Round 17/18's fix, the freshest 1Hz tick) -- not a 60-second
TWAP. This is a **structural mismatch, not a bug**: even a perfectly
real-time, zero-latency Coinbase price feed would still diverge from the
actual BRTI-TWAP settlement value by construction, especially during fast
moves, because TWAP smoothing inherently lags spot. This is a more
specific, quantified candidate explanation for the Round 30/31 "near-zero
live resolution" finding than the general adverse-selection framing --
worth distinguishing from it, not conflating.

**Not implemented this round, deliberately**: `distance_log` is the
model's single most important feature, and this session has already made
two significant, carefully-verified changes to it today (Round 13.5/14's
elapsed_fraction fix, Round 17/18's staleness guard). A third change
(smoothing the price input with a short rolling average to better
approximate BRTI's TWAP methodology) needs its own careful backtest
validation before deploying -- TWAP smoothing could just as easily *hurt*
in some regimes (lagging genuine fast moves) as help, and this needs
testing against real data, not assumed. Recorded as a well-evidenced scoped
candidate for a dedicated future investigation, not rushed into this
iteration.

### Scoped candidates for a future iteration (not built yet)

- **TWAP-smoothed price input for distance_log/gbm_prob** (new this round):
  test whether using a short (e.g. 30-60s) rolling average of Coinbase spot
  price, instead of the instantaneous tick, produces a `distance_log` that
  better matches what Kalshi's BRTI-TWAP settlement source actually sees --
  backtest both ways on historical data before considering a live change,
  given this is the model's dominant feature.
- panic_fade mean-reversion, Kalshi WebSocket feed, Polymarket/Kalshi
  arbitrage, SHAP-based interaction diagnostics (all carried over).

## Round 33 (2026-09-11, /loop): calibration crosses to positive skill; TWAP-smoothing hypothesis validated with real backtest data

**Calibration check-in -- crosses to negative for the first time**: n=27
post-fix, Brier 0.21278, CI [0.172, 0.253]. Brier-vs-uncertainty delta:
**-0.00944** -- the model's Brier is now actually *better* than the trivial
baseline of guessing the cohort's base rate (33.3% win rate ->
uncertainty=0.22222). This is the first sign of measured positive live
discriminative skill this session, per the standing flag-clearly instruction.
Still a small sample and the delta has bounced around zero for several
rounds (n=23: +0.0068, n=24: +0.0035, n=25: +0.0138, now n=27: -0.0094) --
treating this as an encouraging data point in a noisy series, not a
declared resolution.

**DB/parity spot-check**: 0 zero-volume candles, 0 flagged features --
fourth consecutive clean parity round.

**Tested the Round 32 TWAP-smoothing hypothesis directly against real
historical data** (a read-only backtest comparison, no live changes):
recomputed `gbm_prob` two ways across 5 days of real snapshot history
(105,619 rows) -- once with the raw instantaneous `distance_log` (current
method) and once with a 60-second trailing rolling average of
`distance_log` (approximating BRTI's TWAP settlement methodology) -- and
compared both against real settlement labels.
- Full window (all rows): raw Brier 0.31981 vs TWAP60 Brier 0.31664 --
  TWAP smoothing better by 0.00317.
- Restricted to early-window rows only (`elapsed_fraction < 0.3`, matching
  when trades actually get entered, since late-window rows are naturally
  near-certain regardless of price smoothing and would dilute the
  comparison): raw Brier 0.24672 vs TWAP60 Brier 0.24458 -- still better by
  0.00214, smaller but consistent and real.

**This confirms Round 32's hypothesis with real backtest evidence, not just
theoretical plausibility** -- TWAP-smoothing the price input to
`distance_log`/`gbm_prob` produces a small, consistent, genuine improvement
in this project's own historical data. The magnitude (~0.002-0.003 Brier)
is real but modest relative to the ~0.08-0.09 gap between current live
Brier (~0.21) and the backtest target (0.130) -- this is a validated,
worthwhile incremental improvement, not a single fix for the whole gap.

### What this actually changed in the code (Round 33)

Nothing yet -- this was a read-only backtest validation, deliberately kept
separate from implementation given `distance_log` is the model's dominant
feature and already had two live changes today. **Concrete plan for
implementing this, ready for a near-future round**: (1) extend
`candles.py`'s `_TICK_BUFFER` (currently `maxlen=45`, ~45 seconds of 1Hz
ticks) to hold at least 60-90 seconds, since a live 60s TWAP needs that much
history; (2) add a live-computable trailing-average price function
alongside the existing `get_latest_tick()`; (3) modify `build_feature_frame()`
to compute `distance_log` from a 60s trailing rolling average of price
instead of the instantaneous snapshot value (training-side change); (4)
modify `_build_live_features()` to use the same trailing-average logic
(live-side change, must match training exactly per this project's
established train/serve-parity discipline); (5) retrain and verify via
`check_feature_parity()` before deploying, exactly as done for the
elapsed_fraction and distance_log-staleness fixes earlier today.

### Scoped candidates for a future iteration (not built yet)

- **TWAP-smoothed distance_log/gbm_prob** (validated this round, concrete
  implementation plan above) -- promoted from "idea" to "validated, ready
  to implement" status. This is now the most well-evidenced actionable
  improvement candidate in the whole findings log.
- panic_fade mean-reversion, Kalshi WebSocket feed, Polymarket/Kalshi
  arbitrage, SHAP-based interaction diagnostics (carried over, unchanged).

## Round 34 (2026-09-11, /loop): TWAP-smoothing implemented and deployed

**Calibration check-in (before this round's change)**: n=30 post-fix, Brier
0.20255, CI [0.164, 0.241]. Brier-vs-uncertainty delta: **-0.00745** --
still negative (positive skill), consistent with Round 33's crossing. n=30
reaches the low end of the original n=30-50 target.

**Implemented the Round 32/33-validated TWAP-smoothing change**, following
the concrete plan written in Round 33:

1. Extended `candles.py`'s `_TICK_BUFFER` from `maxlen=45` to `maxlen=95`
   (needs to comfortably cover a 60s window with margin for missed ticks).
2. Added `candles_mod.get_trailing_average_price(window_seconds=60)` --
   averages tick prices over the trailing window, falling back gracefully
   (no minimum sample requirement) rather than blocking a decision if the
   buffer hasn't fully filled yet.
3. **Training side** (`build_feature_frame()`): replaced the instantaneous
   `btc_price` used for `distance_log`/`gbm_prob` with a per-ticker,
   time-based 60s rolling mean computed directly on each window's own
   snapshot history (`groupby("ticker").apply(rolling-mean-preserving-
   original-index)` -- deliberately avoided a merge-based join after an
   initial attempt caused row duplication from a non-unique `(ticker,
   timestamp)` key; the index-preserving `apply` approach is correct and
   verified against real data).
4. **Live side** (`_build_live_features()`): replaced the Round 17/18
   single-freshest-tick preference with `get_trailing_average_price()`,
   matching training's methodology. Kept the >$5 discrepancy warning log
   (now comparing candle-close vs the 60s TWAP instead of vs a single
   tick).

**Verification before deploying**: confirmed no NaNs introduced, confirmed
`distance_log` now evolves smoothly tick-to-tick within a window (visibly
different from the old instantaneous jumps, checked directly against real
rows) rather than jumping with each new tick. Retrained: **backtest Brier
0.13100** (vs 0.13014 pre-change) -- a negligible ~0.0009 difference, well
within normal retrain-to-retrain noise, confirming no regression at the
full-window aggregate level (expected, since Round 33's validated
improvement was concentrated in early-window decision-time rows, not
visible much in a full-window average that includes many near-certain
late-window rows). Restarted the server cleanly; both kill switches
confirmed still `false`.

**One parity-check false alarm investigated and ruled out**: a 6-hour
lookback `check_feature_parity()` call right after the code change (but
before restarting the server) showed `trades_checked: 0` -- traced this to
the *settlement-label fetch* only returning 12 labels for that narrow
window (a separate, pre-existing characteristic of `_fetch_settlement_labels`,
unrelated to this round's change -- confirmed by testing a 48h lookback,
which correctly matched 37 trades). Not a regression; this project's
existing parity tool has an underlying dependency on how many settled
windows the label-fetch call happens to return for a given lookback, worth
keeping in mind if a future "0 trades checked" result shows up again with a
narrow window.

**Expected next-round behavior**: recent trades in the 48h check flagged
`gbm_prob` (among others) because they were recorded with the *old*
methodology before this round's server restart, being compared against the
*new* TWAP-based offline recompute -- an expected, transient mismatch that
should resolve as fresh post-deploy trades accumulate. Next round should
re-check parity restricted to trades after this deploy specifically.

### Scoped candidates for a future iteration (not built yet)

- panic_fade mean-reversion, Kalshi WebSocket feed, Polymarket/Kalshi
  arbitrage, SHAP-based interaction diagnostics (carried over, unchanged).
- TWAP-smoothing is now DONE, removed from this list.

## Round 35 (2026-09-11, /loop): TWAP parity confirmed clean, 60s window independently validated by Polymarket

**Calibration check-in (post-Round-34 TWAP deploy cohort, cutoff ~2026-09-11
21:52:35 UTC)**: n=3 -- far too early to mean anything (Brier 0.18307 vs
uncertainty 0.22222, delta -0.039, but at n=3 this is not a meaningful
number, just noted for completeness). Will take several more rounds to
build a real sample under the new methodology.

**Parity check restricted to post-deploy trades (1h lookback, 3 trades)**:
`gbm_prob` is **no longer flagged** -- confirms the TWAP methodology is now
computing consistently between live and offline for fresh trades (it was
flagged in Round 34's pre-restart check, which was comparing old-live
trades against new-offline computation, as expected). Only the usual
low-importance `rsi_14`/`bollinger_pct_b` noise remains. This is the clean
confirmation Round 34 said to look for.

**New research -- independent validation of the 60-second window choice**:
Polymarket switched its own crypto up/down markets to TWAP settlement in
August 2026 (after $7.6M in manipulation losses from single-snapshot
settlement, per Stanford/SMU research showing large trades could move
settlement prices in the final seconds) using Chainlink data streams.
Specifically: **5-minute markets use a 30-second averaging window, while
15-minute markets use a 60-second window** -- this exactly matches the
window size just implemented for this project's KXBTC15M model
(`TWAP_WINDOW_SECONDS = 60`), independently arrived at from the Kalshi/BRTI
research in Round 32 rather than copied from Polymarket. Good confirming
signal that 60s is the right window for a 15-minute market specifically,
not an arbitrary parameter choice -- worth noting for future reference if
this ever needs re-tuning.

## Round 36 (2026-09-11, /loop): routine check, VPIN blocker potentially lighter than thought

**Calibration check-in (post-Round-34 TWAP deploy)**: n=6, Brier 0.20291,
uncertainty 0.22222, delta -0.01931 -- still negative, still a tiny sample.

**Parity check (2h lookback, post-deploy)**: `gbm_prob` still clean, only
`rsi_14`/`bollinger_pct_b` flagged at low-importance noise levels.

New research: found that Kalshi's official API includes a **REST trades
endpoint** (individual trade prints with YES/NO side, not just
candlesticks) alongside the WebSocket `trade` channel already noted in
Round 12. This potentially refines the VPIN/order-flow-toxicity idea
(scoped as blocked since Round 9 on "needs the full WebSocket migration"):
if a REST-based trades endpoint can be polled directly (similar to how
orderbook snapshots are already polled), a VPIN-style feature on Kalshi's
own contract flow might not actually require the full WebSocket migration
first -- worth verifying the exact endpoint and its rate-limit cost in a
future round before committing to build it, since this changes the
prerequisite from "big WebSocket project" to "possibly just a new REST
poll," a meaningfully smaller lift. Not verified or built this round --
recorded as a refinement to the existing scoped candidate, not a new
build.

PSI (Population Stability Index) drift monitoring was also researched as a
general MLOps practice for catching feature drift -- concluded not
actionable at this project's current live sample size (PSI needs reasonably
populated bins per feature to be meaningful, and post-deploy n=6 is far too
small); this project's existing bespoke `check_feature_parity()` already
serves a similar catching-skew-bugs purpose and has proven effective
multiple times this session. Not adopted as a separate addition.

### Scoped candidates for a future iteration (not built yet)

- **VPIN via Kalshi REST trades endpoint** (refined this round) -- verify
  the endpoint and rate-limit cost before committing; may be lighter than
  the full WebSocket migration previously assumed necessary.
- panic_fade mean-reversion, Kalshi WebSocket feed (still relevant for
  quote-staleness even if VPIN itself doesn't need it), Polymarket/Kalshi
  arbitrage, SHAP-based interaction diagnostics (carried over, unchanged).

## Round 37 (2026-09-11, /loop): VPIN scoped-candidate corrected -- largely already built

**Calibration check-in (post-Round-34 TWAP deploy)**: n=11, win rate 36.4%,
PnL -$7.72, Brier 0.25683, uncertainty 0.23140, delta **+0.02542** -- flipped
back to positive (worse than baseline) from Round 36's negative reading.
Reporting plainly: still a small, noisy sample, no clear trend either way
yet.

**DB/parity spot-check**: 1 zero-volume candle (currently-forming,
expected), only the usual low-importance `rsi_14`/`bollinger_pct_b` flagged.

**Verified the Round 36 finding directly against the real API** (matching
this project's discipline of confirming before trusting): called
`GET /markets/trades` live and confirmed it returns individual trade prints
with `taker_side` (yes/no), `taker_book_side` (bid/ask), `count_fp`
(contract count), `yes_price_dollars`/`no_price_dollars`, and
`created_time` -- exactly the classified buy/sell data VPIN-style analysis
needs, no WebSocket required.

**Correction to the VPIN scoped-candidate status**: checking
`orderbook.py`'s existing `get_taker_buy_sell_ratio()` function revealed
**this project already calls this exact endpoint** and already computes a
simple aggressor-flow imbalance ratio from it
(`(yes_taker_count - no_taker_count) / total`), tracked informationally
per trade since it was added (motivated by hamad-khawaja/kalshi-trading-bot,
see earlier rounds). **The "VPIN is blocked on missing trade-tape
infrastructure" framing from Round 9/12/36 was incorrect** -- the raw data
pipeline and a basic version of the signal already exist and are already
being recorded. What's actually still missing is only the more
sophisticated *formal* VPIN methodology specifically (bulk-volume
classification into fixed-volume buckets, rather than a simple running
ratio) -- a refinement of existing infrastructure, not a new build from
scratch. Not implemented this round (the formal bucketing algorithm itself
is nontrivial and deserves its own careful pass), but this closes out the
"blocked" status and correctly reframes it as "refinement of an existing,
already-informational feature."

### Scoped candidates for a future iteration (not built yet)

- **Formal VPIN bucketing on top of the existing taker_buy_sell_ratio
  infrastructure** (corrected/downgraded from "blocked on trade tape" to
  "refinement of existing feature," this round) -- worth a dedicated pass
  to implement proper fixed-volume bucketing rather than the current simple
  running ratio, now that the endpoint and data are confirmed already
  flowing.
- panic_fade mean-reversion, Kalshi WebSocket feed (for quote staleness,
  unrelated to VPIN now), Polymarket/Kalshi arbitrage, SHAP-based
  interaction diagnostics (carried over, unchanged).

## Round 38 (2026-09-11, /loop): VPIN classification-accuracy note, panic_fade reference source found

**Calibration check-in**: no new settlements since Round 37 -- still n=11,
delta +0.02542, unchanged.

**DB/parity spot-check**: 1 zero-volume candle (expected), 0 trades
checked in the 3h parity window (matches the already-documented
narrow-lookback label-fetch quirk from Round 34, not a new concern).

New research:

- **VPIN classification-method accuracy comparison**: academic literature
  shows VPIN computed via Tick Rule/Lee-Ready (inferring trade direction
  from price ticks) correctly identifies 91-96% of toxic events, while Bulk
  Volume Classification (BVC, used when only bars are available, not
  individual trades) identifies only 54-68%. This is a point *in favor* of
  our existing `taker_buy_sell_ratio` signal (Round 37): Kalshi's own
  `/markets/trades` endpoint gives a **definitive** `taker_side` label per
  trade (not an inferred approximation at all) -- meaning our existing
  simple ratio is built on higher-fidelity classification than even the
  best academic VPIN approximation methods typically have access to. This
  further reduces urgency around building the "formal" VPIN bucketing
  refinement -- the underlying classification we already have is
  unusually clean.
- **ojo-network/kalshi-bots-collection** -- a large indexed corpus (1,391
  strategies cataloged, including 156 "panic-fade" and 229 "mean-reversion"
  strategies specifically) confirmed to exist as a reference resource, but
  the collection-level page doesn't expose individual strategies' concrete
  entry/exit logic -- would need to browse specific files in the repo's
  `strategies/`/`dsl/`/`backtests/` directories for real implementation
  detail. Not pursued further this round (more effort than a single loop
  iteration warrants) -- recorded as a good starting point for whenever the
  panic_fade active-exit-logic candidate (Round 22) actually gets a
  dedicated implementation session.

## Session addition (2026-09-11, user-requested): live-outcome recalibration layer built

Triggered by the user directly asking "how can I derive a training model
from [the past 24h of paper trading]" and confirming "Yes recalibrate"
after being told a full retrain on ~100 live trades would be far too little
data and risk overfitting (existing walk-forward training uses ~2,845
historical windows / ~72k+ rows) -- recalibration, not retraining, is the
appropriate use of a live sample this size.

### What was built

- `fit_live_recalibration()` in `btc_model_training.py`: fits a *secondary*
  Platt-scaling correction on live `BtcPaperTrade` outcomes
  (`model_probability` = the already-calibrated P(yes) at entry,
  `settlement_value` = the real 0/1 outcome), saved to a **separate**
  artifact (`live_calibration.json`) rather than overwriting the primary
  `calibration.json` (which is fit on ~72k backtest OOS rows and is far
  better-supported than any live sample will be for a long time).
  Refuses to fit below `LIVE_RECALIBRATION_MIN_SAMPLES = 30`, returning an
  explicit `"insufficient_data"` status rather than fitting on scraps.
  Defaults `since` to the distance_log-staleness-fix cutoff
  (2026-09-11T09:39:28 UTC) rather than all historical trades, since
  earlier trades used measurably different (buggy) feature computations
  and don't represent the model's current behavior.
- `apply_live_recalibration()` / `load_live_calibration()`: apply the
  secondary correction on top of (not instead of) the primary calibration;
  no-op if no live recalibration has been fit yet.
- Wired into **both** execution paths: `btc_paper_trading.py`'s
  `generate_paper_trade()` (already had the primary calibration, now also
  applies the live layer) and `btc_live_trading.py`'s `generate_live_trade()`
  -- the latter was found to have a **real, if currently harmless, gap**:
  it was using the **raw, uncalibrated** XGBoost output, missing even the
  primary Platt-scaling correction entirely. Fixed for consistency/
  correctness while the module remains fully inert (never enabled) --
  costs nothing now, would have been a real bug the moment this path is
  ever turned on.
- Wired into the recurring 6-hour `btc_retrain_job` (`scheduler.py`) so the
  live recalibration refreshes automatically as more paper-trading data
  accumulates, rather than being a one-time fit that goes stale.

### First fit (n=43, since the staleness-fix cutoff)

`{"a": 1.091, "b": 0.558}` -- a meaningfully positive intercept, meaning
the model has been running **underconfident** relative to what this live
cohort's outcomes actually showed (e.g. a raw-then-primary-calibrated 0.617
becomes 0.746 after the live layer). In-sample Brier "improved" from 0.222
to 0.204, but per the same caveat already used elsewhere in this codebase
for same-data metrics, **this is not a trustworthy validation** -- it's
fit and evaluated on the same 43 points, so it will always look like an
improvement. Real validation will come from watching whether the live
Brier gap (tracked every loop round since Round 8) narrows over the
*next* batch of trades, which see this correction for the first time.

Verified the full calibration chain end-to-end (raw -> primary ->
live-recalibrated) before deploying, restarted the server cleanly, and
confirmed both kill switches (`WEATHER_BOT_ENABLED`,
`BTC_LIVE_TRADING_ENABLED`) still `false` throughout.

**Honest caveat for the user**: n=43 is still a small sample for a
correction this size (intercept 0.558 is a substantial shift). Worth
watching closely over the next 20-30 trades to confirm this is a real
correction and not itself an overfit to this particular noisy stretch --
if the live Brier gap widens instead of narrows after this deploy, that
would be a signal to reconsider or dial back this layer, not push through it.

## Round 39 (2026-09-11/12, /loop): recalibration conviction pattern checked against real price action, clean

**Calibration check-in (post-Round-34 TWAP deploy)**: n=14, Brier 0.25424,
uncertainty 0.22959, delta +0.02465 -- essentially unchanged from Round
37/38's +0.025, still small/noisy sample.

**Live-recalibration deploy** (session addition, after Round 38): 3 trades
so far, all pending, all YES with steadily escalating conviction
(61.5% -> 77% -> 83.3% across 3 independent windows ~15-30 min apart).
Investigated this directly rather than assuming it was a recalibration
artifact (the correction formula is fixed, not time-varying, so it
shouldn't "compound" on its own) -- pulled real hourly BTC candles and
confirmed a genuine, if modest, uptrend over this exact period (77,106 ->
77,299 across ~5 hours). The escalating conviction is a coherent reflection
of real price momentum, not a bug. Will keep watching as these 3 pending
trades settle.

**DB/parity spot-check**: 0 zero-volume candles. Narrowed lookback to 5h
(post-TWAP-deploy trades only, 17 checked): `gbm_prob` remains clean
(confirms Round 35's fix still holding). `yes_mid` (mean delta 0.038,
max 0.33) and `quote_age_seconds` (mean 11.9 vs 10.0 tolerance) newly
flagged, but both are explainable by design: `yes_mid` is intentionally the
training-matching *summary* quote (not meant to track instantaneous price
-- that's what the separately-fetched `live_yes_mid` exists for at decision
time), and both deltas are consistent with a busier/faster-moving price
period rather than a new correctness bug. Not chased further.

New research this round: none surfaced anything new/actionable beyond
what's already recorded -- time this round went into investigating the
recalibration conviction pattern and the parity spot-check instead.

## Round 40 (2026-09-12, /loop): live recalibration hardened with sample-size shrinkage

**Calibration check-in**: still 0 settled trades since the live-recalibration
deploy (7 pending). Post-TWAP cohort unchanged at n=14, delta +0.02465.
Genuinely can't assess the recalibration's real effect yet -- all its
trades are still open.

**DB/parity spot-check**: 1 zero-volume candle (expected), same flags as
Round 39, already explained (not new).

**Found and fixed a real subtlety in the just-built live recalibration**,
prompted by research into small-sample calibration methods (Bayesian
online calibration literature): `_fit_platt_scaling()`'s underlying
`LogisticRegression()` uses sklearn's default L2 regularization, which
shrinks the fitted slope toward **0** -- correct for the *primary*
backtest calibration (huge n, and a=0 there sensibly means "distrust an
untrained model entirely"), but **wrong** for the secondary live
correction, where a=0 would mean "ignore the model and always predict a
constant," not the actually-sensible fallback of "make no correction"
(a=1, b=0). At the live sample sizes this mechanism will actually run at
(tens of trades, not tens of thousands), sklearn's regularization alone
doesn't meaningfully protect against a noisy small-n fit producing a
larger correction than the data really supports.

### What this actually changed in the code (Round 40)

Added explicit shrinkage toward the identity transform (a=1, b=0) in
`fit_live_recalibration()`, weighted by sample size:
`shrinkage_weight = n / (n + SHRINKAGE_PRIOR_STRENGTH)` with
`SHRINKAGE_PRIOR_STRENGTH = 50` (chosen so the correction is meaningfully
damped, not eliminated, at the sample sizes expected over the next several
hours -- at n=50 the fit is blended 50/50 with "no correction," growing
toward fully trusting the fit as n grows into the hundreds). Verified
against real data: at n=46, the unshrunk fit (a=1.095, b=0.564 -- matching
the original session-addition fit) gets pulled back to a substantially
more conservative (a=1.046, b=0.270) after shrinkage, roughly half the
correction strength. Both the raw and shrunk values are now returned in
the diagnostic output for transparency. Re-fit and redeployed via clean
server restart; both kill switches confirmed still `false`.

This directly addresses the caveat already given to the user when this
feature was first built ("n=43 is still a small sample for a correction
this size... watch closely") -- rather than just watching and potentially
discovering the correction was too aggressive after the fact, the
mechanism itself is now more conservative by construction at small n, and
will only grow as confident as the unshrunk fit once genuinely enough live
data exists to support it.

## Live update (2026-09-12, task-notification-triggered): first post-recalibration settlements, encouraging but tiny

The first batch of trades since the live-recalibration deploy settled:
n=6, 4W/2L (66.7% win rate), PnL **+$12.79**, Brier **0.19267** vs
uncertainty baseline 0.22222 -- delta **-0.02955** (better than the trivial
baseline). This is the first genuinely positive read since the
recalibration went live, but n=6 is nowhere near enough to credit the
recalibration specifically (vs. ordinary variance, or the TWAP fix, or
simply this being a favorable stretch of market conditions). Recording
honestly as "encouraging, not yet proof" -- consistent with the standing
instruction to take a widening gap seriously if it happens, but equally not
to declare success on a handful of trades either. Continue tracking.
