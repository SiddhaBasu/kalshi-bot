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


class KxBtcCandle(Base):
    """1-minute OHLC of the KXBTC15M YES contract price, stitched across rotating 15-min windows."""
    __tablename__ = "kxbtc_candles"

    id = Column(Integer, primary_key=True, index=True)
    open_time = Column(DateTime, index=True, unique=True)
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


class KxBtcOrderbookSnapshot(Base):
    """
    Top-of-book DEPTH for KXBTC15M, polled on its own (slower) cadence than
    KxBtcSnapshot's top-of-book-only quote. Exists so future backtests can
    simulate realistic (volume-weighted, not naive best-price) fills instead
    of assuming infinite liquidity at the quoted price -- see
    backend/btcmarket/orderbook.py for the live version of this same fill
    simulation, and backend/core/btc_walkforward_equity.py for why that
    matters (unconstrained Kelly sizing produced an absurd backtest result
    until fills were capped/modeled realistically).

    yes_asks_json / no_asks_json: JSON-encoded [[price, size], ...] for the
    top KXBTC_ORDERBOOK_DEPTH_LEVELS price levels on each side, best (cheapest)
    price first -- i.e. already in "ask ladder" form, not raw bid data.
    """
    __tablename__ = "kxbtc_orderbook_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True)
    timestamp = Column(DateTime, index=True)

    yes_asks_json = Column(String)
    no_asks_json = Column(String)
    best_yes_ask = Column(Float, nullable=True)
    best_no_ask = Column(Float, nullable=True)
    imbalance = Column(Float, nullable=True)  # (yes_bid_qty - no_bid_qty)/total across full depth -- see orderbook.book_imbalance()


class BtcPaperTrade(Base):
    """Simulated (paper) trade on a KXBTC15M window, sized/directed by the trained model. Never a real order."""
    __tablename__ = "btc_paper_trades"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True, unique=True)
    event_ticker = Column(String)
    entry_time = Column(DateTime, default=datetime.utcnow, index=True)
    close_time = Column(DateTime, nullable=True)

    direction = Column(String)          # "yes" or "no"
    entry_price = Column(Float)         # 0-1, the REALISTIC volume-weighted fill price from the live orderbook
    quoted_price = Column(Float, nullable=True)  # 0-1, the naive top-of-book price at the moment of decision, for comparison
    size = Column(Float)                # paper USD notional actually filled (may be less than the Kelly target if the book couldn't absorb it)
    model_probability = Column(Float)
    market_probability = Column(Float)
    edge = Column(Float)                # realized edge, computed against entry_price (post-fill), not quoted_price

    fee = Column(Float, nullable=True)  # Kalshi trading fee, charged once at entry -- see backend/btcmarket/fees.py
    features_json = Column(String, nullable=True)  # full FEATURE_COLUMNS vector at decision time, JSON-encoded -- see backend/core/btc_feature_parity.py

    # Informational only, NOT model inputs yet -- recorded starting 2026-09-10
    # so enough history accumulates to eventually add them to FEATURE_COLUMNS
    # and retrain. See orderbook.book_imbalance() / candles.get_short_momentum().
    book_imbalance = Column(Float, nullable=True)
    mom_5s = Column(Float, nullable=True)
    mom_15s = Column(Float, nullable=True)
    mom_30s = Column(Float, nullable=True)
    taker_buy_sell_ratio = Column(Float, nullable=True)
    funding_rate = Column(Float, nullable=True)

    settled = Column(Boolean, default=False, index=True)
    settlement_value = Column(Float, nullable=True)  # 1.0 = YES won, 0.0 = NO won
    pnl = Column(Float, nullable=True)               # NET of fee
    result = Column(String, default="pending")       # pending, win, loss, push


class BtcPaperBotState(Base):
    """Running paper-trading bankroll/stat tracker for the BTC 15m model, separate from the weather bot's BotState."""
    __tablename__ = "btc_paper_bot_state"

    id = Column(Integer, primary_key=True)
    bankroll = Column(Float, default=10000.0)
    total_trades = Column(Integer, default=0)
    winning_trades = Column(Integer, default=0)
    total_pnl = Column(Float, default=0.0)
    last_run = Column(DateTime, nullable=True)


class BtcLiveTradingState(Base):
    """
    Kill switch + circuit breaker for REAL BTC 15m trading. Deliberately its
    own table, not reusing BotState (weather) or BtcPaperBotState (simulated)
    -- this one gates real money and must be toggleable without a code deploy
    or restart. `enabled` defaults to False: real trading stays off unless
    something explicitly flips this row, independent of BTC_LIVE_TRADING_ENABLED
    in config (both must be true -- config is the code-level switch, this is
    the runtime one).
    """
    __tablename__ = "btc_live_trading_state"

    id = Column(Integer, primary_key=True)
    enabled = Column(Boolean, default=False)
    daily_loss_limit = Column(Float, default=20.0)   # circuit breaker: halt new orders once today's realized loss exceeds this
    peak_bankroll = Column(Float, nullable=True)      # highest real Kalshi balance observed -- for the drawdown gate below
    max_drawdown_pct = Column(Float, default=0.25)    # circuit breaker: halt if balance falls this far below peak_bankroll (a slow multi-day bleed trips this even though it'd never trip the daily-loss limit)
    halted_at = Column(DateTime, nullable=True)       # set when the circuit breaker trips; cleared manually to resume
    halted_reason = Column(String, nullable=True)
    last_run = Column(DateTime, nullable=True)


class BtcLiveTrade(Base):
    """A REAL Kalshi order for the BTC 15m model. Mirrors BtcPaperTrade's shape but tracks real order lifecycle."""
    __tablename__ = "btc_live_trades"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True, unique=True)
    client_order_id = Column(String, index=True, unique=True)  # deterministic, e.g. "btc15m-<ticker>" -- Kalshi's own idempotency key
    order_id = Column(String, nullable=True, index=True)       # Kalshi's assigned order id, once known

    entry_time = Column(DateTime, default=datetime.utcnow, index=True)
    close_time = Column(DateTime, nullable=True)

    direction = Column(String)              # "yes" or "no"
    limit_price = Column(Float)             # 0-1, the price we submitted the limit order at
    target_contracts = Column(Integer)
    filled_contracts = Column(Float, default=0.0)
    avg_fill_price = Column(Float, nullable=True)   # 0-1, actual VWAP of confirmed fills
    model_probability = Column(Float)
    market_probability = Column(Float)
    edge = Column(Float)
    fee = Column(Float, nullable=True)

    order_status = Column(String, default="pending")  # pending, resting, filled, partially_filled, cancelled, expired, rejected

    settled = Column(Boolean, default=False, index=True)
    settlement_value = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)
    result = Column(String, default="pending")


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

    try:
        paper_trade_cols = {c["name"] for c in inspector.get_columns("btc_paper_trades")}
        if "quoted_price" not in paper_trade_cols:
            additions.append("ALTER TABLE btc_paper_trades ADD COLUMN quoted_price FLOAT")
        if "fee" not in paper_trade_cols:
            additions.append("ALTER TABLE btc_paper_trades ADD COLUMN fee FLOAT")
        if "features_json" not in paper_trade_cols:
            additions.append("ALTER TABLE btc_paper_trades ADD COLUMN features_json VARCHAR")
        for col in ("book_imbalance", "mom_5s", "mom_15s", "mom_30s", "taker_buy_sell_ratio", "funding_rate"):
            if col not in paper_trade_cols:
                additions.append(f"ALTER TABLE btc_paper_trades ADD COLUMN {col} FLOAT")
    except Exception:
        pass

    try:
        ob_snap_cols = {c["name"] for c in inspector.get_columns("kxbtc_orderbook_snapshots")}
        if "imbalance" not in ob_snap_cols:
            additions.append("ALTER TABLE kxbtc_orderbook_snapshots ADD COLUMN imbalance FLOAT")
    except Exception:
        pass

    try:
        live_state_cols = {c["name"] for c in inspector.get_columns("btc_live_trading_state")}
        if "peak_bankroll" not in live_state_cols:
            additions.append("ALTER TABLE btc_live_trading_state ADD COLUMN peak_bankroll FLOAT")
        if "max_drawdown_pct" not in live_state_cols:
            additions.append("ALTER TABLE btc_live_trading_state ADD COLUMN max_drawdown_pct FLOAT DEFAULT 0.25")
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
