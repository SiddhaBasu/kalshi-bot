"""FastAPI backend for Kalshi weather temperature trading bot."""
from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import List, Optional
import asyncio
import os

from backend.config import settings
from backend.models.database import get_db, init_db, SessionLocal, Signal, Trade, BotState, WhaleTrade
from pydantic import BaseModel

app = FastAPI(
    title="Kalshi Weather Trading Bot",
    description="Automated weather temperature prediction market trading on Kalshi",
    version="4.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

from backend.api.btc_routes import router as btc_router
app.include_router(btc_router)


# ---------------------------------------------------------------------------
# WebSocket manager
# ---------------------------------------------------------------------------

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for conn in self.active_connections:
            try:
                await conn.send_json(message)
            except Exception:
                pass


ws_manager = ConnectionManager()


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------

class BotStats(BaseModel):
    bankroll: float
    total_trades: int
    winning_trades: int
    win_rate: float
    total_pnl: float
    is_running: bool
    last_run: Optional[datetime]
    simulation_mode: bool


class TradeResponse(BaseModel):
    id: int
    market_ticker: str
    platform: str
    direction: str
    entry_price: float
    size: float
    timestamp: datetime
    settled: bool
    result: str
    pnl: Optional[float]
    order_id: Optional[str] = None
    order_status: Optional[str] = None


class WeatherForecastResponse(BaseModel):
    city_key: str
    city_name: str
    target_date: str
    mean_high: float
    std_high: float
    mean_low: float
    std_low: float
    num_members: int
    ensemble_agreement: float


class WeatherSignalResponse(BaseModel):
    market_id: str
    city_key: str
    city_name: str
    target_date: str
    threshold_f: float
    metric: str
    direction: str           # market direction: "above" or "below"
    trade_direction: str     # signal direction: "yes" or "no"
    model_probability: float
    market_probability: float
    edge: float
    confidence: float
    suggested_size: float
    reasoning: str
    ensemble_mean: float
    ensemble_std: float
    ensemble_members: int
    actionable: bool


class CalibrationSummary(BaseModel):
    total_signals: int
    total_with_outcome: int
    accuracy: float
    brier_score: float


class KalshiStatus(BaseModel):
    connected: bool
    balance: Optional[dict] = None
    error: Optional[str] = None


class WhaleTradeResponse(BaseModel):
    id: int
    trade_id: str
    ticker: str
    series_ticker: str
    event_ticker: str
    title: str
    taker_side: str
    count: float
    price_cents: int
    notional_usd: float
    created_time: datetime
    discovered_at: datetime
    link: str


class WhaleStats(BaseModel):
    count_24h: int
    notional_24h: float
    count_total: int
    top_markets: List[dict]


class DashboardData(BaseModel):
    stats: BotStats
    weather_signals: List[WeatherSignalResponse]
    weather_forecasts: List[WeatherForecastResponse]
    recent_trades: List[TradeResponse]
    equity_curve: List[dict]
    calibration: Optional[CalibrationSummary]
    kalshi_status: Optional[KalshiStatus] = None


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    print("=" * 60)
    print("KALSHI WEATHER TRADING BOT v4.0")
    mode = "SIMULATION" if settings.SIMULATION_MODE else "*** LIVE TRADING ***"
    print(f"Mode: {mode}")
    print("=" * 60)

    init_db()

    db = SessionLocal()
    try:
        state = db.query(BotState).first()
        if not state:
            state = BotState(
                bankroll=settings.INITIAL_BANKROLL,
                total_trades=0,
                winning_trades=0,
                total_pnl=0.0,
                is_running=True,
            )
            db.add(state)
            db.commit()
            print(f"New bot state: ${settings.INITIAL_BANKROLL:,.2f} bankroll")
        else:
            state.is_running = True
            # Auto-correct bankroll if it's clearly a stale default (>100x the configured value).
            # This happens when the DB was first created before the .env was tuned.
            if state.bankroll > settings.INITIAL_BANKROLL * 100:
                old_bankroll = state.bankroll
                state.bankroll = settings.INITIAL_BANKROLL
                print(f"Bankroll corrected: ${old_bankroll:,.2f} → ${settings.INITIAL_BANKROLL:,.2f} (was stale DB default)")
            db.commit()
            print(f"Loaded state: ${state.bankroll:,.2f} bankroll | P&L ${state.total_pnl:+,.2f}")
    finally:
        db.close()

    print(f"Edge threshold: {settings.MIN_EDGE_THRESHOLD:.0%}")
    print(f"Scan interval:  {settings.SCAN_INTERVAL_SECONDS}s")
    print(f"Cities: {settings.WEATHER_CITIES}")
    print("=" * 60)

    from backend.core.scheduler import start_scheduler, log_event
    start_scheduler()
    log_event("success", f"Kalshi weather bot started [{mode}]")


@app.on_event("shutdown")
async def shutdown():
    from backend.core.scheduler import stop_scheduler
    stop_scheduler()


# ---------------------------------------------------------------------------
# Core endpoints
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    return {
        "status": "ok",
        "version": "4.0.0",
        "platform": "kalshi",
        "simulation_mode": settings.SIMULATION_MODE,
    }


@app.get("/api/health")
async def health():
    return {"status": "healthy"}


@app.get("/api/stats", response_model=BotStats)
async def get_stats(db: Session = Depends(get_db)):
    state = db.query(BotState).first()
    if not state:
        raise HTTPException(status_code=404, detail="Bot state not initialized")
    win_rate = state.winning_trades / state.total_trades if state.total_trades > 0 else 0
    return BotStats(
        bankroll=state.bankroll,
        total_trades=state.total_trades,
        winning_trades=state.winning_trades,
        win_rate=win_rate,
        total_pnl=state.total_pnl,
        is_running=state.is_running,
        last_run=state.last_run,
        simulation_mode=settings.SIMULATION_MODE,
    )


@app.get("/api/trades", response_model=List[TradeResponse])
async def get_trades(limit: int = 50, status: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(Trade)
    if status:
        query = query.filter(Trade.result == status)
    trades = query.order_by(Trade.timestamp.desc()).limit(limit).all()
    return [
        TradeResponse(
            id=t.id,
            market_ticker=t.market_ticker,
            platform=t.platform,
            direction=t.direction,
            entry_price=t.entry_price,
            size=t.size,
            timestamp=t.timestamp,
            settled=t.settled,
            result=t.result,
            pnl=t.pnl,
            order_id=getattr(t, "order_id", None),
            order_status=getattr(t, "order_status", None),
        )
        for t in trades
    ]


@app.get("/api/equity-curve")
async def get_equity_curve(db: Session = Depends(get_db)):
    trades = db.query(Trade).filter(Trade.settled == True).order_by(Trade.timestamp).all()
    curve = []
    cumulative_pnl = 0.0
    for t in trades:
        if t.pnl is not None:
            cumulative_pnl += t.pnl
            curve.append({
                "timestamp": t.timestamp.isoformat(),
                "pnl": cumulative_pnl,
                "bankroll": settings.INITIAL_BANKROLL + cumulative_pnl,
            })
    return curve


# ---------------------------------------------------------------------------
# Kalshi endpoints
# ---------------------------------------------------------------------------

@app.get("/api/kalshi/status", response_model=KalshiStatus)
async def kalshi_status():
    from backend.data.kalshi_client import KalshiClient, kalshi_credentials_present
    if not kalshi_credentials_present():
        return KalshiStatus(
            connected=False,
            error="Credentials not set (KALSHI_API_KEY_ID / KALSHI_PRIVATE_KEY_PATH)",
        )
    try:
        client = KalshiClient()
        raw = await client.get_balance()
        # Kalshi returns balance in cents
        balance = {
            "available_usd": raw.get("balance", 0) / 100,
            "portfolio_value_usd": raw.get("portfolio_value", 0) / 100,
        }
        return KalshiStatus(connected=True, balance=balance)
    except Exception as e:
        return KalshiStatus(connected=False, error=str(e))


# ---------------------------------------------------------------------------
# Weather endpoints
# ---------------------------------------------------------------------------

@app.get("/api/weather/signals", response_model=List[WeatherSignalResponse])
async def get_weather_signals():
    try:
        from backend.core.weather_signals import scan_for_weather_signals
        signals = await scan_for_weather_signals()
        return [_signal_to_response(s) for s in signals]
    except Exception:
        return []


@app.get("/api/weather/forecasts", response_model=List[WeatherForecastResponse])
async def get_weather_forecasts():
    try:
        from backend.data.weather import fetch_ensemble_forecast, CITY_CONFIG
        city_keys = [c.strip() for c in settings.WEATHER_CITIES.split(",") if c.strip()]
        forecasts = []
        for city_key in city_keys:
            if city_key not in CITY_CONFIG:
                continue
            forecast = await fetch_ensemble_forecast(city_key)
            if forecast:
                forecasts.append(WeatherForecastResponse(
                    city_key=forecast.city_key,
                    city_name=forecast.city_name,
                    target_date=forecast.target_date.isoformat(),
                    mean_high=forecast.mean_high,
                    std_high=forecast.std_high,
                    mean_low=forecast.mean_low,
                    std_low=forecast.std_low,
                    num_members=forecast.num_members,
                    ensemble_agreement=forecast.ensemble_agreement,
                ))
        return forecasts
    except Exception:
        return []


def _signal_to_response(s) -> WeatherSignalResponse:
    return WeatherSignalResponse(
        market_id=s.market.market_id,
        city_key=s.market.city_key,
        city_name=s.market.city_name,
        target_date=s.market.target_date.isoformat(),
        threshold_f=s.market.threshold_f,
        metric=s.market.metric,
        direction=s.market.direction,
        trade_direction=s.direction,
        model_probability=s.model_probability,
        market_probability=s.market_probability,
        edge=s.edge,
        confidence=s.confidence,
        suggested_size=s.suggested_size,
        reasoning=s.reasoning,
        ensemble_mean=s.ensemble_mean,
        ensemble_std=s.ensemble_std,
        ensemble_members=s.ensemble_members,
        actionable=s.passes_threshold,
    )


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

@app.get("/api/calibration")
async def get_calibration(db: Session = Depends(get_db)):
    signals = db.query(Signal).filter(Signal.outcome_correct.isnot(None)).all()
    if not signals:
        return {"buckets": [], "summary": None}

    from collections import defaultdict
    buckets_data = defaultdict(lambda: {"predicted_sum": 0.0, "correct": 0, "total": 0})
    for s in signals:
        bin_start = int(s.model_probability * 100 // 5) * 5
        key = f"{bin_start}-{bin_start + 5}%"
        buckets_data[key]["predicted_sum"] += s.model_probability
        buckets_data[key]["total"] += 1
        if s.outcome_correct:
            buckets_data[key]["correct"] += 1

    buckets = [
        {
            "bucket": k,
            "predicted_avg": d["predicted_sum"] / d["total"],
            "actual_rate": d["correct"] / d["total"],
            "count": d["total"],
        }
        for k, d in sorted(buckets_data.items())
    ]

    total = db.query(Signal).count()
    settled = db.query(Signal).filter(Signal.outcome_correct.isnot(None)).all()
    correct = sum(1 for s in settled if s.outcome_correct)
    brier = sum((s.model_probability - (s.settlement_value or 0.5)) ** 2 for s in settled) / max(1, len(settled))

    summary = CalibrationSummary(
        total_signals=total,
        total_with_outcome=len(settled),
        accuracy=correct / len(settled) if settled else 0.0,
        brier_score=brier,
    )

    return {"buckets": buckets, "summary": summary}


# ---------------------------------------------------------------------------
# Dashboard (single-call aggregate)
# ---------------------------------------------------------------------------

@app.get("/api/dashboard", response_model=DashboardData)
async def get_dashboard(db: Session = Depends(get_db)):
    stats = await get_stats(db)

    # Weather signals
    weather_signals_data: List[WeatherSignalResponse] = []
    try:
        from backend.core.weather_signals import scan_for_weather_signals
        wx_signals = await scan_for_weather_signals()
        weather_signals_data = [_signal_to_response(s) for s in wx_signals]
    except Exception:
        pass

    # Forecasts
    weather_forecasts_data: List[WeatherForecastResponse] = []
    try:
        from backend.data.weather import fetch_ensemble_forecast, CITY_CONFIG
        city_keys = [c.strip() for c in settings.WEATHER_CITIES.split(",") if c.strip()]
        for city_key in city_keys:
            if city_key not in CITY_CONFIG:
                continue
            forecast = await fetch_ensemble_forecast(city_key)
            if forecast:
                weather_forecasts_data.append(WeatherForecastResponse(
                    city_key=forecast.city_key,
                    city_name=forecast.city_name,
                    target_date=forecast.target_date.isoformat(),
                    mean_high=forecast.mean_high,
                    std_high=forecast.std_high,
                    mean_low=forecast.mean_low,
                    std_low=forecast.std_low,
                    num_members=forecast.num_members,
                    ensemble_agreement=forecast.ensemble_agreement,
                ))
    except Exception:
        pass

    # Trades
    trades = db.query(Trade).order_by(Trade.timestamp.desc()).limit(50).all()
    recent_trades = [
        TradeResponse(
            id=t.id,
            market_ticker=t.market_ticker,
            platform=t.platform,
            direction=t.direction,
            entry_price=t.entry_price,
            size=t.size,
            timestamp=t.timestamp,
            settled=t.settled,
            result=t.result,
            pnl=t.pnl,
            order_id=getattr(t, "order_id", None),
            order_status=getattr(t, "order_status", None),
        )
        for t in trades
    ]

    # Equity curve
    settled_trades = db.query(Trade).filter(Trade.settled == True).order_by(Trade.timestamp).all()
    cumulative_pnl = 0.0
    equity_curve = []
    for t in settled_trades:
        if t.pnl is not None:
            cumulative_pnl += t.pnl
            equity_curve.append({
                "timestamp": t.timestamp.isoformat(),
                "pnl": cumulative_pnl,
                "bankroll": settings.INITIAL_BANKROLL + cumulative_pnl,
            })

    # Calibration
    calibration_data = None
    try:
        settled_sigs = db.query(Signal).filter(Signal.outcome_correct.isnot(None)).all()
        if settled_sigs:
            correct = sum(1 for s in settled_sigs if s.outcome_correct)
            brier = sum((s.model_probability - (s.settlement_value or 0.5)) ** 2 for s in settled_sigs) / len(settled_sigs)
            calibration_data = CalibrationSummary(
                total_signals=db.query(Signal).count(),
                total_with_outcome=len(settled_sigs),
                accuracy=correct / len(settled_sigs),
                brier_score=brier,
            )
    except Exception:
        pass

    # Kalshi connection status
    kalshi_status_data = None
    try:
        kalshi_status_data = await kalshi_status()
    except Exception:
        pass

    return DashboardData(
        stats=stats,
        weather_signals=weather_signals_data,
        weather_forecasts=weather_forecasts_data,
        recent_trades=recent_trades,
        equity_curve=equity_curve,
        calibration=calibration_data,
        kalshi_status=kalshi_status_data,
    )


# ---------------------------------------------------------------------------
# Bot control
# ---------------------------------------------------------------------------

@app.post("/api/bot/start")
async def start_bot(db: Session = Depends(get_db)):
    from backend.core.scheduler import start_scheduler, log_event, is_scheduler_running
    state = db.query(BotState).first()
    if state:
        state.is_running = True
        db.commit()
    if not is_scheduler_running():
        start_scheduler()
    log_event("success", "Bot started")
    return {"status": "started", "is_running": True}


@app.post("/api/bot/stop")
async def stop_bot(db: Session = Depends(get_db)):
    from backend.core.scheduler import log_event
    state = db.query(BotState).first()
    if state:
        state.is_running = False
        db.commit()
    log_event("info", "Bot paused")
    return {"status": "stopped", "is_running": False}


@app.post("/api/bot/reset")
async def reset_bot(db: Session = Depends(get_db)):
    from backend.core.scheduler import log_event
    try:
        trades_deleted = db.query(Trade).delete()
        state = db.query(BotState).first()
        if state:
            state.bankroll = settings.INITIAL_BANKROLL
            state.total_trades = 0
            state.winning_trades = 0
            state.total_pnl = 0.0
            state.is_running = True
        db.commit()
        log_event("success", f"Reset: {trades_deleted} trades cleared, ${settings.INITIAL_BANKROLL:,.0f} bankroll restored")
        return {"status": "reset", "trades_deleted": trades_deleted, "new_bankroll": settings.INITIAL_BANKROLL}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/bot/set-bankroll")
async def set_bankroll(amount: float, db: Session = Depends(get_db)):
    """Update the tracked bankroll without deleting existing trades."""
    from backend.core.scheduler import log_event
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Bankroll must be positive")
    state = db.query(BotState).first()
    if not state:
        raise HTTPException(status_code=404, detail="Bot state not initialized")
    old = state.bankroll
    state.bankroll = amount
    db.commit()
    log_event("info", f"Bankroll manually updated: ${old:.2f} → ${amount:.2f}")
    return {"status": "ok", "old_bankroll": old, "new_bankroll": amount}


@app.post("/api/run-scan")
async def run_scan():
    from backend.core.scheduler import run_manual_scan, log_event
    log_event("info", "Manual scan triggered")
    await run_manual_scan()
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.post("/api/settle-trades")
async def settle_trades_endpoint(db: Session = Depends(get_db)):
    from backend.core.settlement import settle_pending_trades, update_bot_state_with_settlements
    from backend.core.scheduler import log_event
    log_event("info", "Manual settlement triggered")
    settled = await settle_pending_trades(db)
    await update_bot_state_with_settlements(db, settled)
    return {
        "status": "ok",
        "settled_count": len(settled),
        "trades": [{"id": t.id, "result": t.result, "pnl": t.pnl} for t in settled],
    }


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------

@app.get("/api/backtest/nyc")
async def backtest_nyc(lookback_days: int = 365):
    """
    Run historical backtест for NYC (KXHIGHNY) weather markets.
    Fetches finalized Kalshi markets, retrieves Open-Meteo historical ensemble
    data, and returns accuracy / calibration / P&L metrics.
    """
    try:
        from backend.core.backtest import run_nyc_backtest
        result = await run_nyc_backtest(lookback_days=lookback_days)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Whale scanner
# ---------------------------------------------------------------------------

@app.get("/api/whales", response_model=List[WhaleTradeResponse])
async def get_whales(limit: int = 100, min_usd: float = 0.0, db: Session = Depends(get_db)):
    trades = (
        db.query(WhaleTrade)
        .filter(WhaleTrade.notional_usd >= min_usd)
        .order_by(WhaleTrade.created_time.desc())
        .limit(limit)
        .all()
    )
    return [
        WhaleTradeResponse(
            id=t.id,
            trade_id=t.trade_id,
            ticker=t.ticker,
            series_ticker=t.series_ticker,
            event_ticker=t.event_ticker,
            title=t.title,
            taker_side=t.taker_side,
            count=t.count,
            price_cents=t.price_cents,
            notional_usd=t.notional_usd,
            created_time=t.created_time,
            discovered_at=t.discovered_at,
            link=t.link,
        )
        for t in trades
    ]


@app.get("/api/whales/stats", response_model=WhaleStats)
async def get_whale_stats(db: Session = Depends(get_db)):
    from sqlalchemy import func as sa_func

    day_ago = datetime.utcnow() - timedelta(hours=24)
    count_24h = db.query(WhaleTrade).filter(WhaleTrade.discovered_at >= day_ago).count()
    notional_24h = (
        db.query(sa_func.coalesce(sa_func.sum(WhaleTrade.notional_usd), 0.0))
        .filter(WhaleTrade.discovered_at >= day_ago)
        .scalar()
    )
    count_total = db.query(WhaleTrade).count()

    top_rows = (
        db.query(
            WhaleTrade.ticker,
            WhaleTrade.title,
            sa_func.sum(WhaleTrade.notional_usd).label("total"),
            sa_func.count(WhaleTrade.id).label("trades"),
        )
        .group_by(WhaleTrade.ticker, WhaleTrade.title)
        .order_by(sa_func.sum(WhaleTrade.notional_usd).desc())
        .limit(10)
        .all()
    )
    top_markets = [
        {"ticker": r.ticker, "title": r.title, "notional_usd": r.total, "trades": r.trades}
        for r in top_rows
    ]

    return WhaleStats(
        count_24h=count_24h,
        notional_24h=notional_24h,
        count_total=count_total,
        top_markets=top_markets,
    )


@app.post("/api/whales/scan")
async def trigger_whale_scan():
    from backend.core.scheduler import run_manual_whale_scan
    await run_manual_whale_scan()
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


# ---------------------------------------------------------------------------
# Event feed
# ---------------------------------------------------------------------------

@app.get("/api/events")
async def get_events(limit: int = 50):
    from backend.core.scheduler import get_recent_events
    return get_recent_events(limit)


@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        await websocket.send_json({
            "timestamp": datetime.utcnow().isoformat(),
            "type": "success",
            "message": "Connected to Kalshi weather trading bot",
        })

        from backend.core.scheduler import get_recent_events
        for event in get_recent_events(20):
            await websocket.send_json(event)

        last_count = len(get_recent_events(200))
        while True:
            await asyncio.sleep(2)
            current = get_recent_events(200)
            if len(current) > last_count:
                for event in current[last_count - len(current):]:
                    await websocket.send_json(event)
                last_count = len(current)
            await websocket.send_json({"type": "heartbeat", "timestamp": datetime.utcnow().isoformat()})

    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
