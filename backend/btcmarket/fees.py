"""
Kalshi's real per-trade fee, so backtest and live PnL reflect actual net
economics instead of a gross number that overstates profitability.

fee = round_up_to_cent(0.07 * contracts * price * (1 - price))

Verified via Kalshi's public fee schedule (2026-09-10): 0.07 fee constant,
no separate settlement fee and no membership fee. Peaks at a 50c price
(maximum uncertainty) and falls toward zero at extreme prices -- e.g. 100
contracts at 10c costs 63c in fees, at 50c costs $1.75, at 90c costs 63c again.
Charged once at trade execution, not again at settlement.
"""
import math

FEE_RATE = 0.07


def trade_fee(contracts: float, price: float) -> float:
    if contracts <= 0 or price <= 0 or price >= 1:
        return 0.0
    raw_cents = FEE_RATE * contracts * price * (1.0 - price) * 100
    raw_cents = round(raw_cents, 6)  # kill float noise (e.g. 63.00000000000001) before ceiling, or it rounds up a whole cent too high
    return math.ceil(raw_cents) / 100.0
