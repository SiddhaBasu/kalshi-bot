import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { fetchWhales, fetchWhaleStats, runWhaleScan } from '../api'
import type { WhaleTrade } from '../types'

// ── helpers ───────────────────────────────────────────────────────────────────

function usd(n: number) {
  return `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
}

function timeAgo(iso: string) {
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (secs < 60) return `${Math.floor(secs)}s ago`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

const MIN_USD_FILTERS = [0, 1000, 3000, 5000, 10000] as const

// ── stat card ─────────────────────────────────────────────────────────────────

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="border border-neutral-800 bg-neutral-900/60 p-3 rounded-sm">
      <div className="text-[10px] text-neutral-500 uppercase tracking-wider mb-1">{label}</div>
      <div className="text-xl font-bold tabular-nums font-mono text-neutral-100">{value}</div>
      {sub && <div className="text-[10px] text-neutral-600 mt-0.5">{sub}</div>}
    </div>
  )
}

// ── whale trades table ────────────────────────────────────────────────────────

function WhalesTable({ trades }: { trades: WhaleTrade[] }) {
  if (trades.length === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-[11px] text-neutral-600 uppercase tracking-wider">
        No whale trades yet — the scanner runs every {' '}
        <span className="text-neutral-400 mx-1">30s</span> in the background
      </div>
    )
  }
  return (
    <div className="overflow-auto">
      <table className="w-full text-[10px] font-mono">
        <thead className="sticky top-0 bg-neutral-950 border-b border-neutral-800">
          <tr className="text-neutral-600 uppercase tracking-wider">
            <th className="text-left py-1.5 px-2">Time</th>
            <th className="text-left py-1.5 px-2">Market</th>
            <th className="text-center py-1.5 px-2">Side</th>
            <th className="text-right py-1.5 px-2">Count</th>
            <th className="text-right py-1.5 px-2">Price</th>
            <th className="text-right py-1.5 px-2">Size</th>
            <th className="text-left py-1.5 px-2">Series</th>
          </tr>
        </thead>
        <tbody>
          {trades.map(t => (
            <tr key={t.id} className="border-b border-neutral-900 hover:bg-neutral-900/40 transition-colors">
              <td className="py-1.5 px-2 text-neutral-500 whitespace-nowrap">{timeAgo(t.created_time)}</td>
              <td className="py-1.5 px-2 text-neutral-200 max-w-md">
                <a
                  href={t.link}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:text-cyan-400 hover:underline"
                  title={t.ticker}
                >
                  {t.title}
                </a>
              </td>
              <td className="py-1.5 px-2 text-center">
                <span className={`px-1.5 py-0.5 text-[9px] font-bold uppercase border rounded ${
                  t.taker_side === 'yes'
                    ? 'bg-green-500/15 text-green-400 border-green-500/30'
                    : 'bg-red-500/15 text-red-400 border-red-500/30'
                }`}>
                  {t.taker_side}
                </span>
              </td>
              <td className="py-1.5 px-2 text-right text-neutral-400 tabular-nums">{t.count.toLocaleString()}</td>
              <td className="py-1.5 px-2 text-right text-neutral-400 tabular-nums">{t.price_cents}c</td>
              <td className="py-1.5 px-2 text-right text-amber-400 tabular-nums font-semibold">
                {usd(t.notional_usd)}
              </td>
              <td className="py-1.5 px-2 text-neutral-600 whitespace-nowrap">{t.series_ticker}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── main panel ────────────────────────────────────────────────────────────────

export function WhaleScannerPanel() {
  const [minUsd, setMinUsd] = useState<number>(1000)
  const queryClient = useQueryClient()

  const { data: trades, isLoading } = useQuery({
    queryKey: ['whales', minUsd],
    queryFn: () => fetchWhales(minUsd),
    refetchInterval: 15000,
  })

  const { data: stats } = useQuery({
    queryKey: ['whale-stats'],
    queryFn: fetchWhaleStats,
    refetchInterval: 15000,
  })

  const scanMutation = useMutation({
    mutationFn: runWhaleScan,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['whales'] })
      queryClient.invalidateQueries({ queryKey: ['whale-stats'] })
    },
  })

  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden bg-black text-neutral-200 font-mono">

      {/* ── sub-header ── */}
      <div className="shrink-0 border-b border-neutral-800 px-4 py-2 flex items-center gap-4">
        <div>
          <span className="text-[11px] font-semibold text-neutral-100 uppercase tracking-wider">
            Whale Scanner
          </span>
          <span className="ml-2 text-[10px] text-neutral-600">All Kalshi markets · trade firehose</span>
        </div>
        <div className="flex items-center gap-2 ml-4">
          <span className="text-[9px] text-neutral-600 uppercase tracking-wider">Min size</span>
          {MIN_USD_FILTERS.map(v => (
            <button
              key={v}
              onClick={() => setMinUsd(v)}
              className={`px-2 py-0.5 text-[9px] uppercase tracking-wider border rounded-sm transition-colors ${
                minUsd === v
                  ? 'bg-neutral-800 border-neutral-600 text-neutral-200'
                  : 'border-neutral-800 text-neutral-600 hover:text-neutral-400'
              }`}
            >
              {v === 0 ? 'All' : `$${v.toLocaleString()}+`}
            </button>
          ))}
        </div>
        <button
          onClick={() => scanMutation.mutate()}
          disabled={scanMutation.isPending}
          className="px-3 py-1 border border-cyan-800 text-cyan-400 hover:border-cyan-600 text-[10px] uppercase tracking-wider transition-colors disabled:opacity-40 disabled:cursor-not-allowed ml-2"
        >
          {scanMutation.isPending ? 'Scanning…' : 'Scan Now'}
        </button>
        <div className="flex-1" />
        {trades && <span className="text-[10px] text-neutral-600">{trades.length} shown</span>}
      </div>

      {/* ── body ── */}
      <div className="flex-1 min-h-0 overflow-y-auto px-4 py-4 space-y-4">

        {/* stat cards */}
        {stats && (
          <div className="grid grid-cols-4 gap-3">
            <StatCard label="Whale trades (24h)" value={stats.count_24h.toString()} />
            <StatCard label="Volume (24h)" value={usd(stats.notional_24h)} />
            <StatCard label="Total tracked" value={stats.count_total.toString()} />
            <StatCard
              label="Top market"
              value={stats.top_markets[0] ? usd(stats.top_markets[0].notional_usd) : '—'}
              sub={stats.top_markets[0]?.title}
            />
          </div>
        )}

        {isLoading && (
          <div className="flex flex-col items-center justify-center h-48 space-y-3">
            <div className="relative w-8 h-8">
              <div className="absolute inset-0 border border-neutral-800 rounded-full" />
              <div className="absolute inset-0 border border-transparent border-t-cyan-500 rounded-full animate-spin" />
            </div>
            <div className="text-[10px] text-neutral-600 uppercase tracking-widest">Loading whale trades…</div>
          </div>
        )}

        {!isLoading && (
          <div className="border border-neutral-800 bg-neutral-900/20 p-3 rounded-sm">
            <WhalesTable trades={trades ?? []} />
          </div>
        )}
      </div>
    </div>
  )
}
