export interface BotStats {
  bankroll: number
  total_trades: number
  winning_trades: number
  win_rate: number
  total_pnl: number
  is_running: boolean
  last_run: string | null
  simulation_mode: boolean
}

export interface Trade {
  id: number
  market_ticker: string
  platform: string
  direction: string
  entry_price: number
  size: number
  timestamp: string
  settled: boolean
  result: string
  pnl: number | null
  order_id: string | null
  order_status: string | null
}

export interface WeatherForecast {
  city_key: string
  city_name: string
  target_date: string
  mean_high: number
  std_high: number
  mean_low: number
  std_low: number
  num_members: number
  ensemble_agreement: number
}

export interface WeatherSignal {
  market_id: string
  city_key: string
  city_name: string
  target_date: string
  threshold_f: number
  metric: string
  direction: string        // market direction: "above" | "below"
  trade_direction: string  // signal direction: "yes" | "no"
  model_probability: number
  market_probability: number
  edge: number
  confidence: number
  suggested_size: number
  reasoning: string
  ensemble_mean: number
  ensemble_std: number
  ensemble_members: number
  actionable: boolean
}

export interface EquityPoint {
  timestamp: string
  pnl: number
  bankroll: number
}

export interface CalibrationSummary {
  total_signals: number
  total_with_outcome: number
  accuracy: number
  brier_score: number
}

export interface KalshiStatus {
  connected: boolean
  balance?: Record<string, unknown>
  error?: string
}

// ── Backtest ────────────────────────────────────────────────────────────────

export interface BacktestRow {
  ticker: string
  target_date: string
  threshold_f: number
  direction: string       // "above" | "below"
  market_price: number    // 0–1
  actual_outcome: number  // 1=YES, 0=NO
  actual_side: string
  model_probability: number
  ensemble_mean: number
  ensemble_std: number
  ensemble_members: number
  edge: number
  model_correct: boolean
  is_actionable: boolean
  hindcast: boolean
  hypo_pnl: number
}

export interface CalibrationBucket {
  label: string
  predicted_avg: number
  actual_rate: number
  count: number
}

export interface MonthlyBreakdown {
  month: string
  count: number
  accuracy: number
  avg_edge: number
  avg_std: number
  actionable: number
}

export interface EquityPoint2 {
  date: string
  cumulative_pnl: number
}

export interface BacktestData {
  total_fetched: number
  total_analyzed: number
  lookback_days: number
  edge_threshold: number
  model_accuracy: number
  market_accuracy: number
  brier_score: number
  brier_skill_score: number
  avg_ensemble_std: number
  actionable_count: number
  actionable_accuracy: number
  hypo_pnl: number
  hypo_pnl_per_signal: number
  hindcast_count: number
  hindcast_fraction: number
  calibration_buckets: CalibrationBucket[]
  market_calibration_buckets: CalibrationBucket[]
  monthly: MonthlyBreakdown[]
  edge_histogram: { label: string; count: number }[]
  equity_curve: EquityPoint2[]
  rows: BacktestRow[]
  note: string
}

// ── Whale scanner ─────────────────────────────────────────────────────────────

export interface WhaleTrade {
  id: number
  trade_id: string
  ticker: string
  series_ticker: string
  event_ticker: string
  title: string
  taker_side: string       // "yes" | "no"
  count: number
  price_cents: number
  notional_usd: number
  created_time: string
  discovered_at: string
  link: string
}

export interface WhaleTopMarket {
  ticker: string
  title: string
  notional_usd: number
  trades: number
}

export interface WhaleStats {
  count_24h: number
  notional_24h: number
  count_total: number
  top_markets: WhaleTopMarket[]
}

export interface DashboardData {
  stats: BotStats
  weather_signals: WeatherSignal[]
  weather_forecasts: WeatherForecast[]
  recent_trades: Trade[]
  equity_curve: EquityPoint[]
  calibration: CalibrationSummary | null
  kalshi_status: KalshiStatus | null
}
