"""Signal generator for Kalshi weather temperature markets using ensemble forecasts."""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from backend.config import settings
from backend.data.weather import fetch_ensemble_forecast
from backend.data.kalshi_markets import WeatherMarket, fetch_kalshi_weather_markets
from backend.models.database import SessionLocal, Signal

logger = logging.getLogger("trading_bot")


# ---------------------------------------------------------------------------
# Edge & Kelly helpers (no longer depend on the deleted signals.py)
# ---------------------------------------------------------------------------

def _calculate_edge(model_prob: float, market_prob: float):
    """
    Return (edge, direction) where direction is "yes" or "no".
    Edge is positive — the magnitude of the opportunity.
    """
    if model_prob > market_prob:
        return model_prob - market_prob, "yes"
    else:
        return market_prob - model_prob, "no"


def _calculate_kelly_size(
    edge: float,
    model_prob: float,
    entry_price: float,
    bankroll: float,
) -> float:
    """
    Fractional Kelly criterion for binary markets.
    Payout = (1 - entry_price) / entry_price (i.e., if you buy YES at 60c, payout is 40c per 60c risked)
    """
    if entry_price <= 0 or entry_price >= 1:
        return 0.0

    b = (1.0 - entry_price) / entry_price  # net odds
    q = 1.0 - model_prob
    kelly_full = (model_prob * b - q) / b if b > 0 else 0.0
    kelly_full = max(0.0, kelly_full)

    raw = kelly_full * settings.KELLY_FRACTION * bankroll
    return min(raw, settings.MAX_TRADE_SIZE)


# ---------------------------------------------------------------------------
# Signal dataclass
# ---------------------------------------------------------------------------

@dataclass
class WeatherTradingSignal:
    market: WeatherMarket

    model_probability: float = 0.5
    market_probability: float = 0.5
    edge: float = 0.0
    direction: str = "yes"

    confidence: float = 0.5
    kelly_fraction: float = 0.0
    suggested_size: float = 0.0

    sources: List[str] = field(default_factory=list)
    reasoning: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)

    ensemble_mean: float = 0.0
    ensemble_std: float = 0.0
    ensemble_members: int = 0

    @property
    def passes_threshold(self) -> bool:
        entry_price = self.market.yes_price if self.direction == "yes" else self.market.no_price
        return (
            abs(self.edge) >= settings.MIN_EDGE_THRESHOLD
            and entry_price <= settings.MAX_ENTRY_PRICE
        )


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------

async def generate_weather_signal(market: WeatherMarket) -> Optional[WeatherTradingSignal]:
    """Generate a trading signal for a single Kalshi weather market."""
    forecast = await fetch_ensemble_forecast(market.city_key, market.target_date)
    if not forecast or not forecast.member_highs:
        return None

    if forecast.num_members < settings.MIN_ENSEMBLE_MEMBERS:
        logger.debug(f"Too few ensemble members for {market.market_id}: {forecast.num_members} < {settings.MIN_ENSEMBLE_MEMBERS}")
        return None

    # Kalshi only has daily-high markets
    members = forecast.member_highs
    if market.direction == "above":
        model_yes_prob = forecast.probability_high_above(market.threshold_f)
    else:
        model_yes_prob = forecast.probability_high_below(market.threshold_f)

    # Clip — don't bet 100% even when ensemble is unanimous
    model_yes_prob = max(0.05, min(0.95, model_yes_prob))
    market_yes_prob = market.yes_price

    edge, direction = _calculate_edge(model_yes_prob, market_yes_prob)
    entry_price = market.yes_price if direction == "yes" else market.no_price

    # Ensemble agreement (confidence proxy)
    above_count = sum(1 for m in members if m > market.threshold_f)
    agreement_frac = max(above_count, len(members) - above_count) / len(members)
    confidence = min(0.9, agreement_frac)

    bankroll = settings.INITIAL_BANKROLL
    suggested_size = _calculate_kelly_size(edge, model_yes_prob, entry_price, bankroll)

    mean_val = forecast.mean_high
    std_val = forecast.std_high

    actionable = (
        abs(edge) >= settings.MIN_EDGE_THRESHOLD
        and entry_price <= settings.MAX_ENTRY_PRICE
    )
    status = "ACTIONABLE" if actionable else "FILTERED"
    filter_note = ""
    if entry_price > settings.MAX_ENTRY_PRICE:
        filter_note = f" [entry {entry_price:.0%} > {settings.MAX_ENTRY_PRICE:.0%}]"

    reasoning = (
        f"[{status}]{filter_note} "
        f"{market.city_name} high {market.direction} {market.threshold_f:.0f}F on {market.target_date} | "
        f"Ensemble: {mean_val:.1f}F ±{std_val:.1f}F ({forecast.num_members} members) | "
        f"Model YES: {model_yes_prob:.0%} vs Market: {market_yes_prob:.0%} | "
        f"Edge: {edge:+.1%} → {direction.upper()} @ {entry_price:.0%} | "
        f"Agreement: {agreement_frac:.0%}"
    )

    return WeatherTradingSignal(
        market=market,
        model_probability=model_yes_prob,
        market_probability=market_yes_prob,
        edge=edge,
        direction=direction,
        confidence=confidence,
        kelly_fraction=suggested_size / bankroll if bankroll > 0 else 0,
        suggested_size=suggested_size,
        sources=[f"open_meteo_ensemble_{forecast.num_members}m"],
        reasoning=reasoning,
        ensemble_mean=mean_val,
        ensemble_std=std_val,
        ensemble_members=forecast.num_members,
    )


async def scan_for_weather_signals() -> List[WeatherTradingSignal]:
    """Fetch Kalshi markets, run ensemble signals, return sorted by edge."""
    city_keys = [c.strip() for c in settings.WEATHER_CITIES.split(",") if c.strip()]

    logger.info("WEATHER SCAN: fetching Kalshi markets...")
    markets = await fetch_kalshi_weather_markets(city_keys)
    logger.info(f"Found {len(markets)} Kalshi weather markets")

    signals: List[WeatherTradingSignal] = []
    for market in markets:
        try:
            signal = await generate_weather_signal(market)
            if signal:
                signals.append(signal)
        except Exception as e:
            logger.debug(f"Signal generation failed for {market.title}: {e}")

    signals.sort(key=lambda s: abs(s.edge), reverse=True)

    actionable = [s for s in signals if s.passes_threshold]
    logger.info(f"SCAN COMPLETE: {len(signals)} signals, {len(actionable)} actionable")
    for s in actionable[:5]:
        logger.info(
            f"  {s.market.city_name}: {s.market.direction} {s.market.threshold_f:.0f}F | "
            f"Edge: {s.edge:+.1%} → {s.direction.upper()}"
        )

    _persist_signals(signals)
    return signals


def _persist_signals(signals: List[WeatherTradingSignal]):
    """Save signals to DB for calibration tracking."""
    to_save = [s for s in signals if abs(s.edge) > 0]
    if not to_save:
        return

    db = SessionLocal()
    try:
        for signal in to_save:
            existing = db.query(Signal).filter(
                Signal.market_ticker == signal.market.market_id,
                Signal.timestamp >= signal.timestamp.replace(second=0, microsecond=0),
            ).first()
            if existing:
                continue

            db.add(Signal(
                market_ticker=signal.market.market_id,
                platform="kalshi",
                timestamp=signal.timestamp,
                direction=signal.direction,
                model_probability=signal.model_probability,
                market_price=signal.market_probability,
                edge=signal.edge,
                confidence=signal.confidence,
                kelly_fraction=signal.kelly_fraction,
                suggested_size=signal.suggested_size,
                sources=signal.sources,
                reasoning=signal.reasoning,
                executed=False,
            ))

        db.commit()
    except Exception as e:
        logger.warning(f"Failed to persist signals: {e}")
        db.rollback()
    finally:
        db.close()
