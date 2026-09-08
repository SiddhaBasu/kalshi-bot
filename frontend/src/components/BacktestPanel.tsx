import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  LineChart, Line, CartesianGrid, ReferenceLine, Legend,
} from 'recharts'
import { fetchBacktest } from '../api'
import type { BacktestData, BacktestRow } from '../types'

// ── helpers ───────────────────────────────────────────────────────────────────

function pct(n: number, decimals = 1) {
  return `${(n * 100).toFixed(decimals)}%`
}

function usd(n: number) {
  const sign = n >= 0 ? '+' : ''
  return `${sign}$${Math.abs(n).toFixed(2)}`
}

function colorForAccuracy(n: number) {
  if (n >= 0.65) return 'text-green-400'
  if (n >= 0.55) return 'text-emerald-400'
  if (n >= 0.5) return 'text-amber-400'
  return 'text-red-400'
}

function colorForBrier(n: number) {
  if (n <= 0.15) return 'text-green-400'
  if (n <= 0.20) return 'text-emerald-400'
  if (n <= 0.25) return 'text-amber-400'
  return 'text-red-400'
}

function colorForSkill(n: number) {
  if (n >= 0.3) return 'text-green-400'
  if (n >= 0.1) return 'text-emerald-400'
  if (n >= 0) return 'text-amber-400'
  return 'text-red-400'
}

function colorForPnl(n: number) {
  return n >= 0 ? 'text-green-400' : 'text-red-400'
}

// ── Stat card ─────────────────────────────────────────────────────────────────

function StatCard({ label, value, sub, valueClass = 'text-neutral-100', tooltip }:
  { label: string; value: string; sub?: string; valueClass?: string; tooltip?: string }) {
  return (
    <div className="border border-neutral-800 bg-neutral-900/60 p-3 rounded-sm" title={tooltip}>
      <div className="text-[10px] text-neutral-500 uppercase tracking-wider mb-1">{label}</div>
      <div className={`text-xl font-bold tabular-nums font-mono ${valueClass}`}>{value}</div>
      {sub && <div className="text-[10px] text-neutral-600 mt-0.5">{sub}</div>}
    </div>
  )
}

// ── Calibration bar chart ─────────────────────────────────────────────────────

function CalibrationChart({ buckets, title }: {
  buckets: { label: string; predicted_avg: number; actual_rate: number; count: number }[]
  title: string
}) {
  const chartData = buckets.map(b => ({
    label: b.label,
    predicted: +(b.predicted_avg * 100).toFixed(1),
    actual: +(b.actual_rate * 100).toFixed(1),
    count: b.count,
  }))

  return (
    <div>
      <div className="text-[10px] text-neutral-500 uppercase tracking-wider mb-2">{title}</div>
      <ResponsiveContainer width="100%" height={160}>
        <BarChart data={chartData} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
          <XAxis dataKey="label" tick={{ fontSize: 9, fill: '#525252' }} interval={0} />
          <YAxis domain={[0, 100]} tick={{ fontSize: 9, fill: '#525252' }} />
          <Tooltip
            contentStyle={{ background: '#0a0a0a', border: '1px solid #262626', borderRadius: 2 }}
            labelStyle={{ color: '#a3a3a3', fontSize: 10 }}
            itemStyle={{ fontSize: 10 }}
            formatter={(val: number, name: string) => [
              `${val.toFixed(1)}%`,
              name === 'predicted' ? 'Expected' : 'Actual',
            ]}
          />
          <ReferenceLine y={50} stroke="#404040" strokeDasharray="3 3" />
          <Bar dataKey="predicted" fill="#3b82f6" opacity={0.7} name="predicted" radius={[2, 2, 0, 0]} />
          <Bar dataKey="actual" fill="#22c55e" opacity={0.85} name="actual" radius={[2, 2, 0, 0]} />
          <Legend
            wrapperStyle={{ fontSize: 9, color: '#737373' }}
            formatter={(val) => val === 'predicted' ? 'Expected (model)' : 'Actual outcome'}
          />
        </BarChart>
      </ResponsiveContainer>
      <div className="text-[9px] text-neutral-700 mt-1">
        Blue = model's predicted probability · Green = observed win rate. Ideal calibration: bars equal height.
      </div>
    </div>
  )
}

// ── Equity curve ──────────────────────────────────────────────────────────────

function EquityCurve({ data }: { data: { date: string; cumulative_pnl: number }[] }) {
  if (data.length === 0) {
    return <div className="text-[10px] text-neutral-700 py-4 text-center">No actionable signals to plot</div>
  }
  const chartData = [{ date: 'Start', cumulative_pnl: 0 }, ...data]
  return (
    <div>
      <div className="text-[10px] text-neutral-500 uppercase tracking-wider mb-2">
        Hypothetical P&L — $25/signal flat bet
      </div>
      <ResponsiveContainer width="100%" height={140}>
        <LineChart data={chartData} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
          <CartesianGrid stroke="#1a1a1a" vertical={false} />
          <XAxis dataKey="date" tick={{ fontSize: 8, fill: '#404040' }} interval={Math.floor(data.length / 6)} />
          <YAxis tick={{ fontSize: 9, fill: '#525252' }} />
          <Tooltip
            contentStyle={{ background: '#0a0a0a', border: '1px solid #262626', borderRadius: 2 }}
            labelStyle={{ color: '#a3a3a3', fontSize: 10 }}
            itemStyle={{ fontSize: 10 }}
            formatter={(val: number) => [`${val >= 0 ? '+' : ''}$${val.toFixed(2)}`, 'P&L']}
          />
          <ReferenceLine y={0} stroke="#404040" />
          <Line
            type="monotone"
            dataKey="cumulative_pnl"
            stroke="#22c55e"
            strokeWidth={1.5}
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

// ── Monthly table ─────────────────────────────────────────────────────────────

function MonthlyTable({ rows }: { rows: BacktestData['monthly'] }) {
  if (rows.length === 0) return null
  return (
    <div>
      <div className="text-[10px] text-neutral-500 uppercase tracking-wider mb-2">Monthly Breakdown</div>
      <div className="overflow-auto">
        <table className="w-full text-[10px] font-mono">
          <thead>
            <tr className="text-neutral-600 border-b border-neutral-800">
              <th className="text-left py-1 pr-3">Month</th>
              <th className="text-right py-1 px-2">Markets</th>
              <th className="text-right py-1 px-2">Accuracy</th>
              <th className="text-right py-1 px-2">Actionable</th>
              <th className="text-right py-1 px-2">Avg Edge</th>
              <th className="text-right py-1 pl-2">Avg Std</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.month} className="border-b border-neutral-900 hover:bg-neutral-900/40">
                <td className="py-1 pr-3 text-neutral-300">{r.month}</td>
                <td className="py-1 px-2 text-right text-neutral-400 tabular-nums">{r.count}</td>
                <td className={`py-1 px-2 text-right tabular-nums ${colorForAccuracy(r.accuracy)}`}>
                  {pct(r.accuracy)}
                </td>
                <td className="py-1 px-2 text-right text-amber-400 tabular-nums">{r.actionable}</td>
                <td className="py-1 px-2 text-right text-cyan-400 tabular-nums">{pct(r.avg_edge)}</td>
                <td className="py-1 pl-2 text-right text-neutral-500 tabular-nums">{r.avg_std.toFixed(1)}°F</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Markets table ─────────────────────────────────────────────────────────────

type SortKey = keyof BacktestRow
type SortDir = 'asc' | 'desc'

function MarketsTable({ rows }: { rows: BacktestRow[] }) {
  const [sortKey, setSortKey] = useState<SortKey>('target_date')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [page, setPage] = useState(0)
  const [filter, setFilter] = useState<'all' | 'actionable' | 'correct' | 'wrong'>('all')
  const PAGE_SIZE = 20

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
    setPage(0)
  }

  const filtered = rows.filter(r => {
    if (filter === 'actionable') return r.is_actionable
    if (filter === 'correct') return r.model_correct
    if (filter === 'wrong') return !r.model_correct
    return true
  })

  const sorted = [...filtered].sort((a, b) => {
    const av = a[sortKey]
    const bv = b[sortKey]
    const cmp = av < bv ? -1 : av > bv ? 1 : 0
    return sortDir === 'asc' ? cmp : -cmp
  })

  const paged = sorted.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)
  const totalPages = Math.ceil(sorted.length / PAGE_SIZE)

  function ColHeader({ label, k }: { label: string; k: SortKey }) {
    const active = sortKey === k
    return (
      <th
        className={`text-right py-1.5 px-2 cursor-pointer select-none whitespace-nowrap
          ${active ? 'text-neutral-200' : 'text-neutral-600 hover:text-neutral-400'}`}
        onClick={() => toggleSort(k)}
      >
        {label}{active ? (sortDir === 'asc' ? ' ↑' : ' ↓') : ''}
      </th>
    )
  }

  return (
    <div>
      {/* filter + pagination controls */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1">
          {(['all', 'actionable', 'correct', 'wrong'] as const).map(f => (
            <button
              key={f}
              onClick={() => { setFilter(f); setPage(0) }}
              className={`px-2 py-0.5 text-[9px] uppercase tracking-wider border rounded-sm transition-colors ${
                filter === f
                  ? 'bg-neutral-800 border-neutral-600 text-neutral-200'
                  : 'border-neutral-800 text-neutral-600 hover:text-neutral-400'
              }`}
            >
              {f} {f !== 'all' ? `(${rows.filter(r =>
                f === 'actionable' ? r.is_actionable : f === 'correct' ? r.model_correct : !r.model_correct
              ).length})` : `(${rows.length})`}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2 text-[10px] text-neutral-600">
          <span>{sorted.length} markets</span>
          {totalPages > 1 && (
            <>
              <button
                disabled={page === 0}
                onClick={() => setPage(p => p - 1)}
                className="px-1.5 border border-neutral-800 hover:border-neutral-600 disabled:opacity-30 text-neutral-400"
              >←</button>
              <span>{page + 1}/{totalPages}</span>
              <button
                disabled={page === totalPages - 1}
                onClick={() => setPage(p => p + 1)}
                className="px-1.5 border border-neutral-800 hover:border-neutral-600 disabled:opacity-30 text-neutral-400"
              >→</button>
            </>
          )}
        </div>
      </div>

      <div className="overflow-auto">
        <table className="w-full text-[10px] font-mono">
          <thead className="sticky top-0 bg-neutral-950 border-b border-neutral-800">
            <tr className="text-neutral-600 uppercase tracking-wider">
              <th className="text-left py-1.5 px-2 cursor-pointer hover:text-neutral-400" onClick={() => toggleSort('target_date')}>
                Date{sortKey === 'target_date' ? (sortDir === 'asc' ? ' ↑' : ' ↓') : ''}
              </th>
              <th className="text-left py-1.5 px-2">Question</th>
              <ColHeader label="Model" k="model_probability" />
              <ColHeader label="Market" k="market_price" />
              <ColHeader label="Edge" k="edge" />
              <th className="text-center py-1.5 px-2">Outcome</th>
              <th className="text-center py-1.5 px-2">Correct</th>
              <ColHeader label="Std" k="ensemble_std" />
              <th className="text-center py-1.5 px-2">Type</th>
              <ColHeader label="Hypo P&L" k="hypo_pnl" />
            </tr>
          </thead>
          <tbody>
            {paged.map(r => (
              <tr
                key={r.ticker}
                className={`border-b border-neutral-900/80 transition-colors ${
                  r.model_correct
                    ? 'hover:bg-green-950/10'
                    : 'hover:bg-red-950/10'
                }`}
              >
                <td className="py-1 px-2 text-neutral-400">{r.target_date.slice(5)}</td>
                <td className="py-1 px-2 text-neutral-300 whitespace-nowrap">
                  High {r.direction} {r.threshold_f.toFixed(0)}°F
                  {r.is_actionable && (
                    <span className="ml-1.5 px-1 py-0 text-[8px] bg-amber-500/15 text-amber-400 border border-amber-500/20 rounded">
                      SIGNAL
                    </span>
                  )}
                </td>
                <td className="py-1 px-2 text-right text-cyan-400 tabular-nums">{pct(r.model_probability)}</td>
                <td className="py-1 px-2 text-right text-neutral-500 tabular-nums">{pct(r.market_price)}</td>
                <td className={`py-1 px-2 text-right tabular-nums ${r.edge >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {r.edge >= 0 ? '+' : ''}{pct(r.edge)}
                </td>
                <td className="py-1 px-2 text-center">
                  <span className={`font-semibold ${r.actual_outcome === 1 ? 'text-green-400' : 'text-neutral-500'}`}>
                    {r.actual_side.toUpperCase()}
                  </span>
                </td>
                <td className="py-1 px-2 text-center">
                  {r.model_correct
                    ? <span className="text-green-500">✓</span>
                    : <span className="text-red-500">✗</span>}
                </td>
                <td className={`py-1 px-2 text-right tabular-nums ${r.ensemble_std < 2 ? 'text-neutral-700' : 'text-neutral-400'}`}>
                  {r.ensemble_std.toFixed(1)}°
                </td>
                <td className="py-1 px-2 text-center">
                  {r.hindcast
                    ? <span className="text-[8px] text-neutral-700 uppercase">hindcast</span>
                    : <span className="text-[8px] text-cyan-700 uppercase">forecast</span>}
                </td>
                <td className={`py-1 px-2 text-right tabular-nums ${r.is_actionable ? (r.hypo_pnl >= 0 ? 'text-green-400' : 'text-red-400') : 'text-neutral-700'}`}>
                  {r.is_actionable ? usd(r.hypo_pnl) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Main panel ────────────────────────────────────────────────────────────────

export function BacktestPanel() {
  const [lookback, setLookback] = useState(365)
  const [triggered, setTriggered] = useState(false)
  const [activeCalChart, setActiveCalChart] = useState<'model' | 'market'>('model')

  const { data, isFetching, isError, refetch } = useQuery<BacktestData>({
    queryKey: ['backtest-nyc', lookback],
    queryFn: () => fetchBacktest(lookback),
    enabled: triggered,
    staleTime: Infinity,
    retry: false,
  })

  const run = () => {
    setTriggered(true)
    if (triggered) refetch()
  }

  const isLoading = isFetching

  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden bg-black text-neutral-200 font-mono">

      {/* ── sub-header ── */}
      <div className="shrink-0 border-b border-neutral-800 px-4 py-2 flex items-center gap-4">
        <div>
          <span className="text-[11px] font-semibold text-neutral-100 uppercase tracking-wider">
            NYC Backtest
          </span>
          <span className="ml-2 text-[10px] text-neutral-600">KXHIGHNY · Open-Meteo GFS ensemble</span>
        </div>
        <div className="flex items-center gap-2 ml-4">
          {([90, 180, 365] as const).map(d => (
            <button
              key={d}
              onClick={() => { setLookback(d); setTriggered(false) }}
              className={`px-2 py-0.5 text-[9px] uppercase tracking-wider border rounded-sm transition-colors ${
                lookback === d
                  ? 'bg-neutral-800 border-neutral-600 text-neutral-200'
                  : 'border-neutral-800 text-neutral-600 hover:text-neutral-400'
              }`}
            >
              {d}d
            </button>
          ))}
        </div>
        <button
          onClick={run}
          disabled={isLoading}
          className="px-3 py-1 border border-cyan-800 text-cyan-400 hover:border-cyan-600 text-[10px] uppercase tracking-wider transition-colors disabled:opacity-40 disabled:cursor-not-allowed ml-2"
        >
          {isLoading ? 'Running…' : triggered && data ? 'Re-run' : 'Run Backtest'}
        </button>
        {data && !isLoading && (
          <span className="text-[10px] text-neutral-600">
            {data.total_analyzed} markets analysed
          </span>
        )}
        <div className="flex-1" />
        {data && (
          <span className="text-[9px] text-neutral-700 max-w-md text-right leading-tight">
            ⚠ {data.hindcast_count}/{data.total_analyzed} hindcast
          </span>
        )}
      </div>

      {/* ── body ── */}
      <div className="flex-1 min-h-0 overflow-y-auto px-4 py-4 space-y-6">

        {/* empty / loading states */}
        {!triggered && !isLoading && (
          <div className="flex flex-col items-center justify-center h-48 text-center space-y-3">
            <div className="text-[11px] text-neutral-600 uppercase tracking-widest">Select lookback window and click Run Backtest</div>
            <div className="text-[10px] text-neutral-700 max-w-lg">
              Fetches all finalized KXHIGHNY markets from Kalshi, retrieves historical Open-Meteo ensemble data for each date, and computes calibration &amp; accuracy metrics.
            </div>
          </div>
        )}

        {isLoading && (
          <div className="flex flex-col items-center justify-center h-48 space-y-3">
            <div className="relative w-8 h-8">
              <div className="absolute inset-0 border border-neutral-800 rounded-full" />
              <div className="absolute inset-0 border border-transparent border-t-cyan-500 rounded-full animate-spin" />
            </div>
            <div className="text-[10px] text-neutral-600 uppercase tracking-widest">
              Fetching {lookback} days of markets…
            </div>
            <div className="text-[9px] text-neutral-700">This may take 30–60 seconds</div>
          </div>
        )}

        {isError && (
          <div className="text-center py-10 space-y-2">
            <div className="text-red-500 text-xs uppercase tracking-wider">Backtest failed</div>
            <div className="text-[10px] text-neutral-600">Check that the backend is running and Kalshi credentials are set.</div>
            <button onClick={run} className="mt-2 px-3 py-1 border border-neutral-700 text-neutral-400 text-[10px] uppercase tracking-wider hover:border-neutral-500">
              Retry
            </button>
          </div>
        )}

        {data && !isLoading && (
          <>
            {/* ── data note ── */}
            <div className="border border-amber-900/40 bg-amber-950/10 px-3 py-2 rounded-sm text-[9px] text-amber-600 leading-relaxed">
              <span className="font-semibold uppercase tracking-wider">Data note · </span>
              {data.note}
            </div>

            {/* ── stat cards ── */}
            <div className="grid grid-cols-4 gap-3">
              <StatCard
                label="Model Accuracy"
                value={pct(data.model_accuracy)}
                sub={`vs ${pct(data.market_accuracy)} market`}
                valueClass={colorForAccuracy(data.model_accuracy)}
                tooltip="Fraction of markets where the model predicted the correct side (YES/NO). Includes hindcast markets."
              />
              <StatCard
                label="Brier Score"
                value={data.brier_score.toFixed(3)}
                sub="0.25 = random baseline"
                valueClass={colorForBrier(data.brier_score)}
                tooltip="Mean squared error of probability forecasts. Lower is better. 0.25 = random (always predict 50%)."
              />
              <StatCard
                label="Brier Skill Score"
                value={data.brier_skill_score >= 0 ? `+${(data.brier_skill_score * 100).toFixed(1)}%` : `${(data.brier_skill_score * 100).toFixed(1)}%`}
                sub="improvement over random"
                valueClass={colorForSkill(data.brier_skill_score)}
                tooltip="1 − (brier / 0.25). Positive = better than random. Negative = worse."
              />
              <StatCard
                label="Actionable Accuracy"
                value={data.actionable_count > 0 ? pct(data.actionable_accuracy) : '—'}
                sub={`${data.actionable_count} signals (edge ≥ ${pct(data.edge_threshold)})`}
                valueClass={data.actionable_count > 0 ? colorForAccuracy(data.actionable_accuracy) : 'text-neutral-600'}
                tooltip="Accuracy only on markets where |model − market| exceeded the edge threshold. Most meaningful metric."
              />
              <StatCard
                label="Hypo P&L"
                value={usd(data.hypo_pnl)}
                sub={`${data.actionable_count > 0 ? usd(data.hypo_pnl_per_signal) : '—'} / signal`}
                valueClass={colorForPnl(data.hypo_pnl)}
                tooltip="Hypothetical profit/loss assuming flat $25 bets on every actionable signal at the market's last price."
              />
              <StatCard
                label="Avg Ensemble Spread"
                value={`${data.avg_ensemble_std.toFixed(1)}°F`}
                sub="std dev across members"
                valueClass="text-neutral-300"
                tooltip="Average standard deviation across GFS ensemble members. <2°F indicates hindcast (reanalysis) data."
              />
              <StatCard
                label="Markets Analysed"
                value={data.total_analyzed.toString()}
                sub={`${data.hindcast_count} hindcast · ${data.total_analyzed - data.hindcast_count} forecast`}
                valueClass="text-neutral-300"
                tooltip="Total finalized KXHIGHNY markets for which Open-Meteo ensemble data was available."
              />
              <StatCard
                label="Market Accuracy"
                value={pct(data.market_accuracy)}
                sub="market's implied side"
                valueClass={colorForAccuracy(data.market_accuracy)}
                tooltip="How often the Kalshi market's implied side (price ≥ 50c = YES) turned out correct. Market efficiency benchmark."
              />
            </div>

            {/* ── calibration + equity ── */}
            <div className="grid grid-cols-2 gap-6">
              <div className="border border-neutral-800 bg-neutral-900/30 p-3 rounded-sm">
                {/* toggle model vs market calibration */}
                <div className="flex items-center gap-2 mb-3">
                  {(['model', 'market'] as const).map(t => (
                    <button
                      key={t}
                      onClick={() => setActiveCalChart(t)}
                      className={`px-2 py-0.5 text-[9px] uppercase tracking-wider border rounded-sm transition-colors ${
                        activeCalChart === t
                          ? 'bg-neutral-800 border-neutral-600 text-neutral-200'
                          : 'border-neutral-800 text-neutral-600 hover:text-neutral-400'
                      }`}
                    >
                      {t === 'model' ? 'Model calibration' : 'Market calibration'}
                    </button>
                  ))}
                </div>
                {activeCalChart === 'model' ? (
                  <CalibrationChart
                    buckets={data.calibration_buckets}
                    title="Model probability vs actual outcome rate"
                  />
                ) : (
                  <CalibrationChart
                    buckets={data.market_calibration_buckets}
                    title="Kalshi market price vs actual outcome rate"
                  />
                )}
              </div>
              <div className="border border-neutral-800 bg-neutral-900/30 p-3 rounded-sm space-y-4">
                <EquityCurve data={data.equity_curve} />
                {data.monthly.length > 0 && <MonthlyTable rows={data.monthly} />}
              </div>
            </div>

            {/* ── markets table ── */}
            <div className="border border-neutral-800 bg-neutral-900/20 p-3 rounded-sm">
              <div className="text-[10px] text-neutral-500 uppercase tracking-wider mb-3">
                All Markets — click column headers to sort
              </div>
              <MarketsTable rows={data.rows} />
            </div>
          </>
        )}

      </div>
    </div>
  )
}
