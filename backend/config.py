"""Configuration settings for the Kalshi weather temperature trading bot."""
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Database (SQLite by default)
    DATABASE_URL: str = "sqlite:///./tradingbot.db"

    # Kalshi API credentials
    KALSHI_API_KEY_ID: Optional[str] = None
    KALSHI_PRIVATE_KEY_PATH: Optional[str] = None

    # Bot behaviour
    SIMULATION_MODE: bool = True        # Set False in .env to place real orders
    WEATHER_BOT_ENABLED: bool = False   # Master switch for the weather scan/trade loop -- code stays in place, just never runs
    INITIAL_BANKROLL: float = 10000.0
    KELLY_FRACTION: float = 0.15        # Fractional Kelly

    # Scan schedule
    SCAN_INTERVAL_SECONDS: int = 300       # Weather scan every 5 min
    SETTLEMENT_INTERVAL_SECONDS: int = 1800  # Settlement check every 30 min

    # Signal filters
    MIN_EDGE_THRESHOLD: float = 0.08    # 8% minimum edge
    MAX_ENTRY_PRICE: float = 0.70       # Don't enter above 70c
    MIN_ENSEMBLE_MEMBERS: int = 20      # Require at least this many ensemble members

    # Confirmation: signal must stay above threshold for this many minutes before trading
    # First scan always just observes — no trades until at least one full confirm window has passed
    CONFIRM_MINUTES: int = 5

    # Risk management
    MAX_TRADE_SIZE: float = 100.0
    MAX_WEATHER_ALLOCATION: float = 500.0  # Max total open exposure
    DAILY_LOSS_LIMIT: float = 300.0
    MAX_TOTAL_PENDING_TRADES: int = 20
    MAX_TRADES_PER_SCAN: int = 3

    # Cities to trade (Kalshi KXHIGH series)
    WEATHER_CITIES: str = "nyc,chicago,miami,los_angeles,denver"

    # Notifications — set to a Discord webhook URL to receive trade/settlement alerts
    DISCORD_WEBHOOK_URL: Optional[str] = None

    # Whale scanner — watches all Kalshi trades for large fills
    WHALE_SCANNER_ENABLED: bool = True
    MIN_WHALE_TRADE_USD: float = 1000.0
    WHALE_SCAN_INTERVAL_SECONDS: int = 30
    WHALE_CSV_PATH: str = "whale_trades.csv"

    # BTC 15-min market data module (KXBTC15M): candles + live orderbook snapshots
    BTC_MARKET_ENABLED: bool = True
    KXBTC15M_SERIES_TICKER: str = "KXBTC15M"
    KXBTC_POLL_INTERVAL_SECONDS: int = 1     # live orderbook snapshot of the open window
    BTC_TICKER_POLL_SECONDS: int = 1         # live Coinbase price -> updates the forming candle
    KXBTC_SNAPSHOT_RETENTION_DAYS: int = 180
    KXBTC_HISTORY_REFRESH_SECONDS: int = 300  # re-check for newly-settled windows to backfill

    # Orderbook DEPTH recording (top N levels each side), separate from the
    # top-of-book-only KxBtcSnapshot poll above -- for backtesting realistic
    # (volume-weighted) fills instead of assuming infinite liquidity at the
    # quoted price. Slower cadence than the 1s top-of-book poll: depth
    # structure changes far less often than the top-of-book quote itself, and
    # the orderbook endpoint is a heavier call we don't want to double up on
    # Kalshi's API every second.
    KXBTC_ORDERBOOK_POLL_INTERVAL_SECONDS: int = 5
    KXBTC_ORDERBOOK_DEPTH_LEVELS: int = 10
    KXBTC_ORDERBOOK_RETENTION_DAYS: int = 60

    # BTC paper trading (simulated only -- never places real orders) + model retraining.
    # Deliberately its own bankroll/size, NOT settings.INITIAL_BANKROLL/MAX_TRADE_SIZE --
    # those are set tiny in .env as a real-money safety cap for the weather bot
    # (SIMULATION_MODE=false there), which would make paper-trade sizes too small
    # to produce a meaningful PnL/Brier signal.
    BTC_PAPER_TRADING_ENABLED: bool = True
    BTC_PAPER_INITIAL_BANKROLL: float = 50.0    # user's real intended stake if this ever goes live -- still simulated only
    BTC_PAPER_MAX_TRADE_SIZE: float = 100.0     # effectively a no-op ceiling at this bankroll (Kelly never gets close); left as a safety cap
    BTC_PAPER_SIGNAL_INTERVAL_SECONDS: int = 15
    # Entries were clustering in the first ~30s of every window (edges are
    # structurally biggest right at open, since vol ~ sigma*sqrt(seconds_remaining)
    # gives the most room to disagree with the market when there's the most time
    # left). To let the model trade at varied points across the window instead of
    # always at open, each window gets its own deterministic-but-effectively-random
    # eligibility time (hashed from its ticker) uniformly spread across this range.
    BTC_PAPER_MIN_WINDOW_AGE_SECONDS: int = 180    # never enter before 3 minutes in
    BTC_PAPER_MAX_ENTRY_DELAY_SECONDS: int = 720   # eligibility spreads up to 12 minutes in
    BTC_PAPER_SETTLEMENT_INTERVAL_SECONDS: int = 60
    BTC_RETRAIN_INTERVAL_SECONDS: int = 21600   # retrain (walk-forward) every 6h as new windows settle
    BTC_HOURLY_ANALYSIS_INTERVAL_SECONDS: int = 3600

    # Genuine held-out validation: every fix/feature so far (vol-calc bug,
    # quote-staleness feature, the entry-delay revert) was validated by
    # watching metrics improve on the SAME historical dataset, repeatedly --
    # real methodological risk. From this timestamp on, settled windows are
    # NEVER used for training/tuning, only for a one-shot forward evaluation
    # via evaluate_holdout() in backend/core/btc_model_training.py. Do not
    # move this forward just because a retrain would like more data.
    BTC_HOLDOUT_START: str = "2026-09-10T04:42:15"

    # REAL BTC trading -- deliberately OFF by default and separate from
    # BTC_PAPER_TRADING_ENABLED above. Two independent switches must both be
    # on for a real order to ever be placed: this config flag (code-level,
    # requires a deploy to flip) AND BtcLiveTradingState.enabled in the DB
    # (runtime-level, toggleable without a deploy -- the actual kill switch).
    # Not wired into the scheduler; nothing here runs until explicitly enabled.
    BTC_LIVE_TRADING_ENABLED: bool = False
    BTC_LIVE_ORDER_MAX_WAIT_SECONDS: float = 20.0   # how long a resting limit order is allowed to wait for a fill before being cancelled
    BTC_LIVE_ORDER_POLL_SECONDS: float = 1.0
    BTC_LIVE_DAILY_LOSS_LIMIT: float = 20.0

    class Config:
        env_file = ".env"


settings = Settings()
