import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchBtcCandles, fetchBtcMarket, fetchBtcHistory, fetchKalshiCandles, type BtcInterval, type BtcLookback } from '../api'

import { BtcCandlestickChart } from './BtcCandlestickChart'
import { BtcProbabilityChart } from './BtcProbabilityChart'

const POLL_MS = 1000 // 1Hz, per spec: orderbook, BTC price, and candles all refresh every second

function usd(n: number | null | undefined, decimals = 2) {
  if (n === null || n === undefined) return '—'
  return `$${n.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}`
}

function timeRemaining(closeTimeIso?: string): string {
  if (!closeTimeIso) return '—'
  const secs = Math.max(0, (new Date(closeTimeIso).getTime() - Date.now()) / 1000)
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

// ── toolbar button ───────────────────────────────────────────────────────────

function ToolbarButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`px-2 py-1 text-[11px] font-medium rounded transition-colors ${
        active
          ? 'bg-[#2962ff] text-white'
          : 'text-[#787b86] hover:bg-[#f0f3fa] hover:text-[#131722]'
      }`}
    >
      {children}
    </button>
  )
}

// ── header ────────────────────────────────────────────────────────────────────

function MarketHeader() {
  const { data } = useQuery({
    queryKey: ['btc-market'],
    queryFn: fetchBtcMarket,
    refetchInterval: POLL_MS,
  })
  const market = data?.market

  if (!market) {
    return (
      <div className="shrink-0 border-b border-[#e0e3eb] bg-white px-4 py-2 text-[11px] text-[#787b86]">
        No open KXBTC15M window right now
      </div>
    )
  }

  const yesMidPct = ((market.yes_bid + market.yes_ask) / 2 * 100).toFixed(1)

  return (
    <div className="shrink-0 border-b border-[#e0e3eb] bg-white px-4 py-2 flex items-center gap-5 flex-wrap">
      <div>
        <span className="text-[13px] font-semibold text-[#131722]">
          {market.title}
        </span>
        <span className="ml-2 text-[10px] text-[#787b86]">{market.ticker}</span>
      </div>
      <div className="text-[11px]">
        <span className="text-[#787b86] mr-1">Target</span>
        <span className="text-[#ff9800] font-medium tabular-nums">{usd(market.floor_strike)}</span>
      </div>
      <div className="text-[11px]">
        <span className="text-[#787b86] mr-1">YES</span>
        <span className="text-[#26a69a] font-medium tabular-nums">{yesMidPct}%</span>
        <span className="text-[#9598a1] ml-1">
          ({(market.yes_bid * 100).toFixed(0)}/{(market.yes_ask * 100).toFixed(0)}c)
        </span>
      </div>
      <div className="text-[11px]">
        <span className="text-[#787b86] mr-1">Volume</span>
        <span className="text-[#131722] tabular-nums">{market.volume.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
      </div>
      <div className="text-[11px]">
        <span className="text-[#787b86] mr-1">Closes in</span>
        <span className="text-[#131722] tabular-nums font-semibold">{timeRemaining(market.close_time)}</span>
      </div>
      <div className="flex-1" />
      <span className="text-[10px] text-[#9598a1]">Settles on CF Benchmarks BRTI · BTC price feed below is a Coinbase spot proxy, not licensed BRTI data</span>
    </div>
  )
}

// ── chart panel wrapper ───────────────────────────────────────────────────────

function ChartPanel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="h-full flex flex-col min-h-0">
      <div className="border-b border-[#e0e3eb] px-3 py-1 shrink-0 bg-white">
        <span className="text-[10px] text-[#787b86] font-medium uppercase tracking-wide">{title}</span>
      </div>
      <div className="flex-1 min-h-0">{children}</div>
    </div>
  )
}

// ── main panel ────────────────────────────────────────────────────────────────

const INTERVALS: { key: BtcInterval; label: string }[] = [
  { key: '1m', label: '1m' }, { key: '5m', label: '5m' }, { key: '15m', label: '15m' },
  { key: '1h', label: '1H' }, { key: '6h', label: '6H' }, { key: '1d', label: '1D' },
]
const LOOKBACKS: { key: BtcLookback; label: string }[] = [
  { key: '1d', label: '1D' }, { key: '3d', label: '3D' }, { key: '1w', label: '1W' }, { key: '1m', label: '1M' },
]

export function BtcMarketView() {
  const [interval, setInterval_] = useState<BtcInterval>('1m')
  const [lookback, setLookback] = useState<BtcLookback>('1d')
  const [historyRange, setHistoryRange] = useState<'1h' | '1d' | '1w' | '1m'>('1d')
  const [showPatterns, setShowPatterns] = useState(true)
  const viewKey = `${interval}-${lookback}`

  const { data: candles } = useQuery({
    queryKey: ['btc-candles', interval, lookback],
    queryFn: () => fetchBtcCandles(interval, lookback),
    refetchInterval: POLL_MS,
  })

  const { data: kalshiCandles } = useQuery({
    queryKey: ['btc-kalshi-candles', historyRange],
    queryFn: () => fetchKalshiCandles(historyRange),
    refetchInterval: POLL_MS,
  })

  const { data: snapshots } = useQuery({
    queryKey: ['btc-history', historyRange],
    queryFn: () => fetchBtcHistory(historyRange),
    refetchInterval: POLL_MS,
  })

  const { data: marketData } = useQuery({
    queryKey: ['btc-market'],
    queryFn: fetchBtcMarket,
    refetchInterval: POLL_MS,
  })

  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden bg-white text-[#131722]" style={{ fontFamily: "-apple-system, 'Trebuchet MS', Roboto, Ubuntu, sans-serif" }}>
      <MarketHeader />

      {/* toolbar: TradingView-style filter row */}
      <div className="shrink-0 border-b border-[#e0e3eb] bg-white px-3 py-1.5 flex items-center gap-4 flex-wrap">
        <div className="flex items-center gap-0.5 border border-[#e0e3eb] rounded p-0.5">
          {INTERVALS.map(i => (
            <ToolbarButton key={i.key} active={interval === i.key} onClick={() => setInterval_(i.key)}>{i.label}</ToolbarButton>
          ))}
        </div>
        <div className="flex items-center gap-0.5 border border-[#e0e3eb] rounded p-0.5">
          {LOOKBACKS.map(l => (
            <ToolbarButton
              key={l.key}
              active={lookback === l.key}
              onClick={() => { setLookback(l.key); setHistoryRange(l.key === '3d' ? '1w' : l.key) }}
            >
              {l.label}
            </ToolbarButton>
          ))}
        </div>
        <ToolbarButton active={showPatterns} onClick={() => setShowPatterns(v => !v)}>
          Pattern markers
        </ToolbarButton>
        <div className="flex-1" />
        <span className="text-[10px] text-[#9598a1]">
          1Hz live · {snapshots?.length ?? 0} orderbook points · {kalshiCandles?.length ?? 0} Kalshi candles loaded
        </span>
      </div>

      {/* 2x2 grid: all four charts share the same filters, theme, pattern markers, and view-state preservation */}
      <div className="flex-1 min-h-0 grid grid-cols-2 grid-rows-2">
        <div className="border-b border-r border-[#e0e3eb] min-h-0">
          <ChartPanel title="BTC/USD Candlestick (Coinbase)">
            <BtcCandlestickChart candles={candles ?? []} showPatterns={showPatterns} viewKey={viewKey} />
          </ChartPanel>
        </div>

        <div className="border-b border-[#e0e3eb] min-h-0">
          <ChartPanel title="BTC/USD vs. Kalshi Target (Candlestick)">
            <BtcCandlestickChart
              candles={candles ?? []}
              showPatterns={showPatterns}
              viewKey={viewKey}
              targetPrice={marketData?.market?.floor_strike}
              targetLabel="Target"
            />
          </ChartPanel>
        </div>

        <div className="border-r border-[#e0e3eb] min-h-0">
          <ChartPanel title="Kalshi YES Price (Candlestick, real orderbook OHLC)">
            <BtcCandlestickChart
              candles={kalshiCandles ?? []}
              showPatterns={showPatterns}
              viewKey={historyRange}
              targetPrice={0.5}
              targetLabel="50%"
            />
          </ChartPanel>
        </div>

        <div className="min-h-0">
          <ChartPanel title="Kalshi YES Probability (smoothed, orderbook mid)">
            <BtcProbabilityChart snapshots={snapshots ?? []} viewKey={historyRange} />
          </ChartPanel>
        </div>
      </div>
    </div>
  )
}
