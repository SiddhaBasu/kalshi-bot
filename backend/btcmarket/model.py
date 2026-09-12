"""
Probability model for KXBTC15M: "will BTC-USD be at/above floor_strike when
this 15-minute window closes?"

Why not a trained classifier: each window has a different strike and a
15-minute horizon, so the only thing that actually varies market-to-market is
(1) how far spot currently is from the strike, (2) how much time is left, and
(3) how volatile BTC has been recently. That relationship has a well-known
closed form -- it's the same math as a digital/binary option -- so a
zero-drift geometric Brownian motion (GBM) model gets the right functional
shape for free, with a single estimated parameter (volatility), instead of
needing thousands of labeled historical windows to learn a shape a classifier
would otherwise have to discover from scratch.

Model:
    ln(S_T / S_0) ~ Normal(-0.5 * sigma_h^2, sigma_h^2)   (driftless GBM)
    sigma_h = sigma_per_second * sqrt(seconds_remaining)

    P(YES) = P(S_T >= K) = Phi( (ln(S_0/K) - 0.5*sigma_h^2) / sigma_h )

sigma_per_second is estimated from realized volatility of recent 1-minute BTC
log returns (Coinbase candles already cached by backend.btcmarket.candles).
Driftless is a deliberate choice, not an oversight: at a 15-minute horizon any
recent-momentum drift estimate is mostly noise, and baking noise into the
exponent would make the model worse, not better -- see the module docstring
in backend/core/backtest.py for the same principle applied to the weather
model.
"""
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from scipy.stats import norm

from backend.config import settings
from backend.btcmarket import candles as candles_mod
from backend.btcmarket import kalshi_poll

logger = logging.getLogger("trading_bot")

SECONDS_PER_YEAR = 365 * 24 * 3600
VOL_LOOKBACK_MINUTES = 180     # trailing window used to estimate realized volatility
MIN_VOL_SAMPLES = 20           # below this, the sigma estimate is too noisy to trust
FALLBACK_SIGMA_PER_SECOND = 0.30 / math.sqrt(SECONDS_PER_YEAR)  # ~30% annualized, used only if history is thin


# ---------------------------------------------------------------------------
# Volatility estimation
# ---------------------------------------------------------------------------

def realized_volatility_per_second(
    candles: List[dict], lookback_minutes: int = VOL_LOOKBACK_MINUTES
) -> Optional[float]:
    """
    Estimate sigma per sqrt-second from trailing 1-minute BTC closes.
    Returns None if there isn't enough history to trust the estimate.
    """
    if len(candles) < MIN_VOL_SAMPLES + 1:
        return None

    cutoff = candles[-1]["time"] - lookback_minutes * 60
    window = [c for c in candles if c["time"] >= cutoff]
    if len(window) < MIN_VOL_SAMPLES + 1:
        window = candles[-(MIN_VOL_SAMPLES + 1):]

    closes = [c["close"] for c in window if c["close"] > 0]
    if len(closes) < MIN_VOL_SAMPLES + 1:
        return None

    log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    n = len(log_returns)
    mean = sum(log_returns) / n
    variance = sum((r - mean) ** 2 for r in log_returns) / max(1, n - 1)
    sigma_per_minute = math.sqrt(variance)

    return sigma_per_minute / math.sqrt(60)


# ---------------------------------------------------------------------------
# Closed-form digital-option probability
# ---------------------------------------------------------------------------

def gbm_yes_probability(
    spot: float,
    strike: float,
    seconds_remaining: float,
    sigma_per_second: float,
    drift_per_second: float = 0.0,
) -> float:
    """P(BTC spot >= strike at expiry) under driftless GBM. Clipped to [0.01, 0.99]."""
    seconds_remaining = max(seconds_remaining, 1.0)
    if spot <= 0 or strike <= 0:
        return 0.5

    sigma_h = sigma_per_second * math.sqrt(seconds_remaining)
    if sigma_h <= 1e-9:
        return 0.99 if spot >= strike else 0.01

    mu_h = drift_per_second * seconds_remaining
    z = (math.log(spot / strike) + mu_h - 0.5 * sigma_h ** 2) / sigma_h
    prob = float(norm.cdf(z))
    return max(0.01, min(0.99, prob))


# ---------------------------------------------------------------------------
# Signal
# ---------------------------------------------------------------------------

@dataclass
class BtcMarketSignal:
    ticker: str
    floor_strike: float
    spot_price: float
    seconds_remaining: float

    model_probability: float = 0.5
    market_probability: float = 0.5
    edge: float = 0.0
    direction: str = "yes"

    sigma_per_second: float = 0.0
    sigma_annualized: float = 0.0
    z_score: float = 0.0
    confidence: float = 0.5

    kelly_fraction: float = 0.0
    suggested_size: float = 0.0

    reasoning: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @property
    def passes_threshold(self) -> bool:
        entry_price = self.market_probability if self.direction == "yes" else 1.0 - self.market_probability
        return (
            abs(self.edge) >= settings.MIN_EDGE_THRESHOLD
            and entry_price <= settings.MAX_ENTRY_PRICE
        )


def _calculate_edge(model_prob: float, market_prob: float):
    if model_prob > market_prob:
        return model_prob - market_prob, "yes"
    return market_prob - model_prob, "no"


def _calculate_kelly_size(model_prob: float, entry_price: float, bankroll: float) -> float:
    if entry_price <= 0 or entry_price >= 1:
        return 0.0
    b = (1.0 - entry_price) / entry_price
    q = 1.0 - model_prob
    kelly_full = max(0.0, (model_prob * b - q) / b) if b > 0 else 0.0
    raw = kelly_full * settings.KELLY_FRACTION * bankroll
    return min(raw, settings.MAX_TRADE_SIZE)


def _parse_kalshi_time(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=timezone.utc)


async def generate_btc_signal() -> Optional[BtcMarketSignal]:
    """Fetch the current KXBTC15M window and price it with the GBM model."""
    market = await kalshi_poll.get_current_market()
    if not market:
        return None

    ticker = market.get("ticker", "")
    floor_strike = market.get("floor_strike")
    close_time = _parse_kalshi_time(market.get("close_time"))
    if not floor_strike or not close_time:
        return None

    now = datetime.now(timezone.utc)
    seconds_remaining = (close_time - now).total_seconds()
    if seconds_remaining <= 0:
        return None

    await candles_mod.ensure_recent_candles(60, timedelta(minutes=VOL_LOOKBACK_MINUTES + 5))
    candles = candles_mod.get_cached_candles(60, timedelta(minutes=VOL_LOOKBACK_MINUTES + 5))
    if not candles:
        return None

    spot = candles[-1]["close"]
    sigma_per_second = realized_volatility_per_second(candles)
    used_fallback_sigma = sigma_per_second is None
    if sigma_per_second is None:
        sigma_per_second = FALLBACK_SIGMA_PER_SECOND

    model_prob = gbm_yes_probability(spot, floor_strike, seconds_remaining, sigma_per_second)

    yes_bid = float(market.get("yes_bid_dollars", 0) or 0)
    yes_ask = float(market.get("yes_ask_dollars", 0) or 0)
    market_prob = (yes_bid + yes_ask) / 2 if (yes_bid > 0 and yes_ask > 0) else max(yes_bid, yes_ask, 0.5)
    market_prob = max(0.01, min(0.99, market_prob))

    edge, direction = _calculate_edge(model_prob, market_prob)
    entry_price = market_prob if direction == "yes" else 1.0 - market_prob

    sigma_h = sigma_per_second * math.sqrt(seconds_remaining)
    z_score = (math.log(spot / floor_strike) - 0.5 * sigma_h ** 2) / sigma_h if sigma_h > 1e-9 else 0.0
    confidence = min(0.95, abs(model_prob - 0.5) * 2)
    if used_fallback_sigma:
        confidence *= 0.5  # thin history -> distrust the sigma estimate, halve confidence

    bankroll = settings.INITIAL_BANKROLL
    suggested_size = _calculate_kelly_size(model_prob, entry_price, bankroll)

    sigma_annualized = sigma_per_second * math.sqrt(SECONDS_PER_YEAR)

    reasoning = (
        f"{ticker} | spot ${spot:,.0f} vs strike ${floor_strike:,.0f} "
        f"({seconds_remaining/60:.1f}m left) | "
        f"sigma {sigma_annualized:.0%} ann.{' [fallback]' if used_fallback_sigma else ''} | "
        f"z={z_score:+.2f} | Model YES {model_prob:.0%} vs Market {market_prob:.0%} | "
        f"Edge {edge:+.1%} -> {direction.upper()} @ {entry_price:.0%}"
    )

    return BtcMarketSignal(
        ticker=ticker,
        floor_strike=floor_strike,
        spot_price=spot,
        seconds_remaining=seconds_remaining,
        model_probability=model_prob,
        market_probability=market_prob,
        edge=edge,
        direction=direction,
        sigma_per_second=sigma_per_second,
        sigma_annualized=sigma_annualized,
        z_score=z_score,
        confidence=confidence,
        kelly_fraction=suggested_size / bankroll if bankroll > 0 else 0,
        suggested_size=suggested_size,
        reasoning=reasoning,
    )
