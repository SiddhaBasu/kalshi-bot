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

    class Config:
        env_file = ".env"


settings = Settings()
