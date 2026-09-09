import { useEffect, useRef } from 'react'
import { createChart, type IChartApi, type ISeriesApi, type Time } from 'lightweight-charts'
import type { KxBtcSnapshot } from '../types'

export function BtcProbabilityChart({ snapshots }: { snapshots: KxBtcSnapshot[] }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Area'> | null>(null)

  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      layout: { background: { color: 'transparent' }, textColor: '#a3a3a3', fontFamily: 'monospace', fontSize: 10 },
      grid: { vertLines: { color: '#1a1a1a' }, horzLines: { color: '#1a1a1a' } },
      rightPriceScale: { borderColor: '#262626' },
      timeScale: { borderColor: '#262626', timeVisible: true, secondsVisible: true },
      autoSize: true,
    })

    const series = chart.addAreaSeries({
      lineColor: '#22c55e',
      topColor: 'rgba(34,197,94,0.28)',
      bottomColor: 'rgba(34,197,94,0.02)',
      lineWidth: 2,
      priceFormat: { type: 'custom', formatter: (p: number) => `${p.toFixed(1)}%`, minMove: 0.1 },
    })
    series.createPriceLine({ price: 50, color: '#404040', lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: '' })

    chartRef.current = chart
    seriesRef.current = series

    return () => {
      chart.remove()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!seriesRef.current) return
    const points = snapshots
      .filter(s => s.yes_mid !== null)
      .map(s => ({ time: s.time as Time, value: (s.yes_mid as number) * 100 }))
    seriesRef.current.setData(points)
    chartRef.current?.timeScale().fitContent()
  }, [snapshots])

  return <div ref={containerRef} className="w-full h-full" />
}
