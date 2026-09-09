"""Database models for the Kalshi weather trading bot."""
from datetime import datetime
from typing import Optional
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean, JSON, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy import inspect

from backend.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, index=True)
    signal_id = Column(Integer, index=True)
    market_ticker = Column(String, index=True)  # Kalshi ticker e.g. KXHIGHNY-26APR28-B72
    platform = Column(String, default="kalshi")
    event_slug = Column(String, nullable=True)

    direction = Column(String)      # "yes" or "no"
    entry_price = Column(Float)     # 0–1
    size = Column(Float)            # USD notional
    timestamp = Column(DateTime, default=datetime.utcnow)

    # Real order tracking
    order_id = Column(String, nullable=True)       # Kalshi order ID if live
    order_status = Column(String, nullable=True)   # "resting", "filled", "cancelled"

    # Settlement
    settled = Column(Boolean, default=False)
    settlement_time = Column(DateTime, nullable=True)
    settlement_value = Column(Float, nullable=True)  # 1.0=YES won, 0.0=NO won
    result = Column(String, default="pending")       # pending, win, loss, push
    pnl = Column(Float, nullable=True)

    # Model tracking
    model_probability = Column(Float)
    market_price_at_entry = Column(Float)
    edge_at_entry = Column(Float)


class BotState(Base):
    __tablename__ = "bot_state"

    id = Column(Integer, primary_key=True)
    bankroll = Column(Float, default=10000.0)
    total_trades = Column(Integer, default=0)
    winning_trades = Column(Integer, default=0)
    total_pnl = Column(Float, default=0.0)
    last_run = Column(DateTime, nullable=True)
    is_running = Column(Boolean, default=False)


class Signal(Base):
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, index=True)
    market_ticker = Column(String, index=True)
    platform = Column(String, default="kalshi")
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    direction = Column(String)          # "yes" or "no"
    model_probability = Column(Float)
    market_price = Column(Float)
    edge = Column(Float)
    confidence = Column(Float)

    kelly_fraction = Column(Float)
    suggested_size = Column(Float)

    sources = Column(JSON)
    reasoning = Column(String)
    executed = Column(Boolean, default=False)

    # Calibration — filled after settlement
    actual_outcome = Column(String, nullable=True)   # "yes" or "no"
    outcome_correct = Column(Boolean, nullable=True)
    settlement_value = Column(Float, nullable=True)
    settled_at = Column(DateTime, nullable=True)


class WhaleTrade(Base):
    __tablename__ = "whale_trades"

    id = Column(Integer, primary_key=True, index=True)
    trade_id = Column(String, unique=True, index=True)
    ticker = Column(String, index=True)
    series_ticker = Column(String, index=True)
    event_ticker = Column(String, index=True)
    title = Column(String)

    taker_side = Column(String)      # "yes" or "no"
    count = Column(Float)            # fractional contracts allowed (Kalshi count_fp)
    price_cents = Column(Integer)
    notional_usd = Column(Float, index=True)

    created_time = Column(DateTime, index=True)   # when the trade happened on Kalshi
    discovered_at = Column(DateTime, default=datetime.utcnow)
    link = Column(String)


class BtcCandle(Base):
    """Cached OHLCV candle for BTC-USD, sourced from Coinbase."""
    __tablename__ = "btc_candles"

    id = Column(Integer, primary_key=True, index=True)
    granularity_seconds = Column(Integer, index=True)   # 60 = 1m, 3600 = 1h
    open_time = Column(DateTime, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)


class KxBtcSnapshot(Base):
    """A point-in-time snapshot of the live KXBTC15M market (top-of-book + reference price)."""
    __tablename__ = "kxbtc_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True)
    event_ticker = Column(String, index=True)
    timestamp = Column(DateTime, index=True)

    floor_strike = Column(Float)     # target/strike price for this window
    open_time = Column(DateTime)
    close_time = Column(DateTime)

    yes_bid = Column(Float)          # 0-1
    yes_ask = Column(Float)          # 0-1
    no_bid = Column(Float)
    no_ask = Column(Float)

    btc_price = Column(Float, nullable=True)  # BTC-USD spot at snapshot time (Coinbase), for reference


class ScanLog(Base):
    __tablename__ = "scan_logs"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String, unique=True, index=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    markets_found = Column(Integer, default=0)
    signals_generated = Column(Integer, default=0)
    trades_executed = Column(Integer, default=0)

    success = Column(Boolean, default=True)
    error = Column(String, nullable=True)


def init_db():
    Base.metadata.create_all(bind=engine)
    _ensure_schema()


def _ensure_schema():
    """Add columns that may be missing on existing databases."""
    inspector = inspect(engine)
    try:
        trade_cols = {c["name"] for c in inspector.get_columns("trades")}
    except Exception:
        return

    additions = []
    if "order_id" not in trade_cols:
        additions.append("ALTER TABLE trades ADD COLUMN order_id VARCHAR")
    if "order_status" not in trade_cols:
        additions.append("ALTER TABLE trades ADD COLUMN order_status VARCHAR")

    try:
        signal_cols = {c["name"] for c in inspector.get_columns("signals")}
        for col, coltype in [
            ("actual_outcome", "TEXT"),
            ("outcome_correct", "BOOLEAN"),
            ("settlement_value", "FLOAT"),
            ("settled_at", "DATETIME"),
        ]:
            if col not in signal_cols:
                additions.append(f"ALTER TABLE signals ADD COLUMN {col} {coltype}")
    except Exception:
        pass

    if additions:
        with engine.connect() as conn:
            for stmt in additions:
                try:
                    with conn.begin():
                        conn.execute(text(stmt))
                except Exception:
                    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
