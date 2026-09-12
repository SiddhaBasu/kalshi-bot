import { useEffect, useRef } from 'react'
import { createChart, type IChartApi, type ISeriesApi, type Time } from 'lightweight-charts'
import type { KxBtcSnapshot } from '../types'
import { dedupeByTime } from '../utils/dedupeByTime'

export function BtcProbabilityChart({ snapshots, viewKey }: { snapshots: KxBtcSnapshot[]; viewKey?: string }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Area'> | null>(null)
  const hasFitRef = useRef(false)

  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      layout: { background: { color: '#ffffff' }, textColor: '#131722', fontFamily: "-apple-system, 'Trebuchet MS', Roboto, Ubuntu, sans-serif", fontSize: 11 },
      grid: { vertLines: { color: '#f0f3fa' }, horzLines: { color: '#f0f3fa' } },
      rightPriceScale: { borderColor: '#e0e3eb' },
      timeScale: { borderColor: '#e0e3eb', timeVisible: true, secondsVisible: true },
      autoSize: true,
    })

    const series = chart.addAreaSeries({
      lineColor: '#26a69a',
      topColor: 'rgba(38,166,154,0.18)',
      bottomColor: 'rgba(38,166,154,0.02)',
      lineWidth: 2,
      priceFormat: { type: 'custom', formatter: (p: number) => `${p.toFixed(1)}%`, minMove: 0.1 },
    })
    series.createPriceLine({ price: 50, color: '#9598a1', lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: '' })

    chartRef.current = chart
    seriesRef.current = series
    hasFitRef.current = false

    return () => {
      chart.remove()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    hasFitRef.current = false
  }, [viewKey])

  useEffect(() => {
    if (!seriesRef.current || !chartRef.current) return
    const savedRange = hasFitRef.current ? chartRef.current.timeScale().getVisibleLogicalRange() : null

    const points = dedupeByTime(
      snapshots
        .filter(s => s.yes_mid !== null)
        .map(s => ({ time: s.time, value: (s.yes_mid as number) * 100 }))
    )
    seriesRef.current.setData(points.map(p => ({ time: p.time as Time, value: p.value })))

    if (savedRange) {
      chartRef.current.timeScale().setVisibleLogicalRange(savedRange)
    } else if (points.length > 0) {
      chartRef.current.timeScale().fitContent()
      hasFitRef.current = true
    }
  }, [snapshots])

  return <div ref={containerRef} className="w-full h-full" />
}
