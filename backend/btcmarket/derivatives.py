"""
Perpetual futures funding rate (Kraken Futures, public, no auth) -- a
genuinely new data category for this project: leveraged-positioning skew
among futures traders, distinct from spot orderbook/price data, sometimes
carries short-horizon predictive information. Motivated by
hamad-khawaja/kalshi-trading-bot's funding_rate_signal feature, see
prior_findings.md.

Binance Futures (the source that project uses) returned HTTP 451 -- geo-
blocked from this environment ("Service unavailable from a restricted
location"), consistent with why this whole project already avoids Binance
for spot data too. Verified Kraken Futures is reachable instead (2026-09-10)
and PI_XBTUSD is their main BTC perpetual (Perpetual Inverse) -- confirmed by
comparing several tickers' symbols/rates before picking one, not guessed.

Informational only -- like book_imbalance/momentum, not yet a trained model
input, since we're only starting to record it now.

Liquidation imbalance (the other half of that project's feature pair) is NOT
implemented here -- exchanges generally only expose aggregate liquidation
data via a WebSocket stream, not a simple polled REST snapshot, which is a
meaningfully bigger lift than one polling function. Recorded as a scoped
candidate in prior_findings.md, not attempted.
"""
import logging
from typing import Optional

import httpx

logger = logging.getLogger("trading_bot")

KRAKEN_FUTURES_API = "https://futures.kraken.com/derivatives/api/v3"
SYMBOL = "PI_XBTUSD"


async def get_funding_rate() -> Optional[float]:
    """
    Current funding rate for Kraken's BTC perpetual (PI_XBTUSD).
    Positive = longs pay shorts (leveraged positioning skewed bullish);
    negative = the reverse. Typically a small number.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{KRAKEN_FUTURES_API}/tickers")
            resp.raise_for_status()
            data = resp.json()
        for t in data.get("tickers", []):
            if t.get("symbol") == SYMBOL:
                rate = t.get("fundingRate")
                return float(rate) if rate is not None else None
        return None
    except Exception as e:
        logger.debug(f"Kraken funding rate fetch failed: {e}")
        return None
