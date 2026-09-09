import { useEffect, useRef } from 'react'
import { createChart, type IChartApi, type ISeriesApi, type IPriceLine, type Time } from 'lightweight-charts'
import type { BtcCandle } from '../types'

export function BtcPriceChart({ candles, floorStrike }: { candles: BtcCandle[]; floorStrike?: number | null }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Area'> | null>(null)
  const strikeLineRef = useRef<IPriceLine | null>(null)

  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      layout: { background: { color: 'transparent' }, textColor: '#a3a3a3', fontFamily: 'monospace', fontSize: 10 },
      grid: { vertLines: { color: '#1a1a1a' }, horzLines: { color: '#1a1a1a' } },
      rightPriceScale: { borderColor: '#262626' },
      timeScale: { borderColor: '#262626', timeVisible: true, secondsVisible: false },
      autoSize: true,
    })

    const series = chart.addAreaSeries({
      lineColor: '#3b82f6',
      topColor: 'rgba(59,130,246,0.28)',
      bottomColor: 'rgba(59,130,246,0.02)',
      lineWidth: 2,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    })

    chartRef.current = chart
    seriesRef.current = series

    return () => {
      chart.remove()
      chartRef.current = null
      strikeLineRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!seriesRef.current || candles.length === 0) return
    seriesRef.current.setData(candles.map(c => ({ time: c.time as Time, value: c.close })))
    chartRef.current?.timeScale().fitContent()
  }, [candles])

  useEffect(() => {
    if (!seriesRef.current) return
    if (strikeLineRef.current) {
      seriesRef.current.removePriceLine(strikeLineRef.current)
      strikeLineRef.current = null
    }
    if (floorStrike) {
      strikeLineRef.current = seriesRef.current.createPriceLine({
        price: floorStrike,
        color: '#f59e0b',
        lineWidth: 1,
        lineStyle: 2, // dashed
        axisLabelVisible: true,
        title: 'Target',
      })
    }
  }, [floorStrike])

  return <div ref={containerRef} className="w-full h-full" />
}
