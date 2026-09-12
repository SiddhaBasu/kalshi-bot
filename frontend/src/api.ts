import axios from 'axios'
import type { DashboardData, Trade, BotStats, WeatherForecast, WeatherSignal, BacktestData, WhaleTrade, WhaleStats, BtcCandle, KxBtcMarket, KxBtcSnapshot } from './types'

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000'

const api = axios.create({
  baseURL: `${API_BASE}/api`,
})

export async function fetchDashboard(): Promise<DashboardData> {
  const { data } = await api.get<DashboardData>('/dashboard')
  return data
}

export async function fetchStats(): Promise<BotStats> {
  const { data } = await api.get<BotStats>('/stats')
  return data
}

export async function fetchTrades(): Promise<Trade[]> {
  const { data } = await api.get<Trade[]>('/trades')
  return data
}

export async function fetchWeatherForecasts(): Promise<WeatherForecast[]> {
  const { data } = await api.get<WeatherForecast[]>('/weather/forecasts')
  return data
}

export async function fetchWeatherSignals(): Promise<WeatherSignal[]> {
  const { data } = await api.get<WeatherSignal[]>('/weather/signals')
  return data
}

export async function runScan(): Promise<{ status: string; timestamp: string }> {
  const { data } = await api.post('/run-scan')
  return data
}

export async function startBot(): Promise<{ status: string; is_running: boolean }> {
  const { data } = await api.post('/bot/start')
  return data
}

export async function stopBot(): Promise<{ status: string; is_running: boolean }> {
  const { data } = await api.post('/bot/stop')
  return data
}

export async function resetBot(): Promise<{ status: string; trades_deleted: number; new_bankroll: number }> {
  const { data } = await api.post('/bot/reset')
  return data
}

export async function settleTradesApi(): Promise<{ settled_count: number }> {
  const { data } = await api.post('/settle-trades')
  return data
}

export async function fetchBacktest(lookbackDays = 365): Promise<BacktestData> {
  const { data } = await api.get<BacktestData>('/backtest/nyc', { params: { lookback_days: lookbackDays } })
  return data
}

export async function fetchWhales(minUsd = 0, limit = 200): Promise<WhaleTrade[]> {
  const { data } = await api.get<WhaleTrade[]>('/whales', { params: { min_usd: minUsd, limit } })
  return data
}

export async function fetchWhaleStats(): Promise<WhaleStats> {
  const { data } = await api.get<WhaleStats>('/whales/stats')
  return data
}

export async function runWhaleScan(): Promise<{ status: string; timestamp: string }> {
  const { data } = await api.post('/whales/scan')
  return data
}

export type BtcInterval = '1m' | '5m' | '15m' | '1h' | '6h' | '1d'
export type BtcLookback = '1d' | '3d' | '1w' | '1m'

export async function fetchBtcCandles(interval: BtcInterval = '1m', lookback: BtcLookback = '1d'): Promise<BtcCandle[]> {
  const { data } = await api.get<BtcCandle[]>('/btc/candles', { params: { interval, lookback } })
  return data
}

export async function fetchBtcMarket(): Promise<{ market: KxBtcMarket | null }> {
  const { data } = await api.get<{ market: KxBtcMarket | null }>('/btc/market')
  return data
}

export async function fetchBtcHistory(range: '1h' | '1d' | '1w' | '1m' = '1d'): Promise<KxBtcSnapshot[]> {
  const { data } = await api.get<KxBtcSnapshot[]>('/btc/history', { params: { range } })
  return data
}

export async function fetchKalshiCandles(range: '1h' | '1d' | '1w' | '1m' = '1d'): Promise<BtcCandle[]> {
  const { data } = await api.get<BtcCandle[]>('/btc/kalshi-candles', { params: { range } })
  return data
}
