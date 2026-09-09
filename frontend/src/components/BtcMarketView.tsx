import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchBtcCandles, fetchBtcMarket, fetchBtcHistory } from '../api'
import { BtcCandlestickChart } from './BtcCandlestickChart'
import { BtcPriceChart } from './BtcPriceChart'
import { BtcProbabilityChart } from './BtcProbabilityChart'

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

function MarketHeader() {
  const { data } = useQuery({
    queryKey: ['btc-market'],
    queryFn: fetchBtcMarket,
    refetchInterval: 5000,
  })
  const market = data?.market

  if (!market) {
    return (
      <div className="shrink-0 border-b border-neutral-800 px-4 py-2 text-[10px] text-neutral-600">
        No open KXBTC15M window right now
      </div>
    )
  }

  const yesMidPct = ((market.yes_bid + market.yes_ask) / 2 * 100).toFixed(1)

  return (
    <div className="shrink-0 border-b border-neutral-800 px-4 py-2 flex items-center gap-5 flex-wrap">
      <div>
        <span className="text-[11px] font-semibold text-neutral-100 uppercase tracking-wider">
          {market.title}
        </span>
        <span className="ml-2 text-[10px] text-neutral-600">{market.ticker}</span>
      </div>
      <div className="text-[10px]">
        <span className="text-neutral-600 mr-1">Target</span>
        <span className="text-amber-400 tabular-nums">{usd(market.floor_strike)}</span>
      </div>
      <div className="text-[10px]">
        <span className="text-neutral-600 mr-1">YES</span>
        <span className="text-green-400 tabular-nums">{yesMidPct}%</span>
        <span className="text-neutral-700 ml-1">
          ({(market.yes_bid * 100).toFixed(0)}/{(market.yes_ask * 100).toFixed(0)}c)
        </span>
      </div>
      <div className="text-[10px]">
        <span className="text-neutral-600 mr-1">Volume</span>
        <span className="text-neutral-300 tabular-nums">{market.volume.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
      </div>
      <div className="text-[10px]">
        <span className="text-neutral-600 mr-1">Closes in</span>
        <span className="text-neutral-100 tabular-nums font-semibold">{timeRemaining(market.close_time)}</span>
      </div>
      <div className="flex-1" />
      <span className="text-[9px] text-neutral-700">Settles on CF Benchmarks BRTI, not raw exchange price</span>
    </div>
  )
}

export function BtcMarketView() {
  const [range, setRange] = useState<'1d' | '1m'>('1d')
  const [showPatterns, setShowPatterns] = useState(true)

  const { data: candles } = useQuery({
    queryKey: ['btc-candles', range],
    queryFn: () => fetchBtcCandles(range),
    refetchInterval: 15000,
  })

  const { data: snapshots } = useQuery({
    queryKey: ['btc-history', range],
    queryFn: () => fetchBtcHistory(range === '1d' ? '1d' : '1m'),
    refetchInterval: 10000,
  })

  const { data: marketData } = useQuery({
    queryKey: ['btc-market'],
    queryFn: fetchBtcMarket,
    refetchInterval: 5000,
  })

  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden bg-black text-neutral-200 font-mono">
      <MarketHeader />

      <div className="shrink-0 border-b border-neutral-800 px-4 py-1.5 flex items-center gap-2">
        <span className="text-[9px] text-neutral-600 uppercase tracking-wider">Range</span>
        {(['1d', '1m'] as const).map(r => (
          <button
            key={r}
            onClick={() => setRange(r)}
            className={`px-2 py-0.5 text-[9px] uppercase tracking-wider border rounded-sm transition-colors ${
              range === r
                ? 'bg-neutral-800 border-neutral-600 text-neutral-200'
                : 'border-neutral-800 text-neutral-600 hover:text-neutral-400'
            }`}
          >
            {r === '1d' ? 'Past day (1m)' : 'Past month (1h)'}
          </button>
        ))}
        <button
          onClick={() => setShowPatterns(v => !v)}
          className={`ml-4 px-2 py-0.5 text-[9px] uppercase tracking-wider border rounded-sm transition-colors ${
            showPatterns
              ? 'bg-neutral-800 border-neutral-600 text-neutral-200'
              : 'border-neutral-800 text-neutral-600 hover:text-neutral-400'
          }`}
        >
          Pattern markers
        </button>
        <div className="flex-1" />
        {snapshots && (
          <span className="text-[9px] text-neutral-700">
            {snapshots.length} recorded orderbook snapshots (live-recorded, no historical Kalshi API)
          </span>
        )}
      </div>

      <div className="flex-1 min-h-0 flex flex-col">
        <div className="border-b border-neutral-800 px-3 py-1 shrink-0">
          <span className="text-[10px] text-neutral-500 uppercase tracking-wider">Candlestick — BTC/USD (Coinbase)</span>
        </div>
        <div style={{ height: '42%' }} className="min-h-0 px-1">
          <BtcCandlestickChart candles={candles ?? []} showPatterns={showPatterns} />
        </div>

        <div className="flex-1 min-h-0 grid grid-cols-2 border-t border-neutral-800">
          <div className="flex flex-col min-h-0 border-r border-neutral-800">
            <div className="border-b border-neutral-800 px-3 py-1 shrink-0">
              <span className="text-[10px] text-neutral-500 uppercase tracking-wider">BTC price vs. target</span>
            </div>
            <div className="flex-1 min-h-0 px-1">
              <BtcPriceChart candles={candles ?? []} floorStrike={marketData?.market?.floor_strike} />
            </div>
          </div>
          <div className="flex flex-col min-h-0">
            <div className="border-b border-neutral-800 px-3 py-1 shrink-0">
              <span className="text-[10px] text-neutral-500 uppercase tracking-wider">YES probability (orderbook mid)</span>
            </div>
            <div className="flex-1 min-h-0 px-1">
              <BtcProbabilityChart snapshots={snapshots ?? []} />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
