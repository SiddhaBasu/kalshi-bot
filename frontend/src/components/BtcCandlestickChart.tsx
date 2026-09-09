import { useEffect, useRef } from 'react'
import { createChart, CrosshairMode, type IChartApi, type ISeriesApi, type SeriesMarker, type Time } from 'lightweight-charts'
import type { BtcCandle } from '../types'
import { detectPatterns } from '../utils/candlePatterns'

const PATTERN_COLORS: Record<string, string> = {
  'Doji': '#a3a3a3',
  'Hammer': '#22c55e',
  'Inverted Hammer': '#22c55e',
  'Shooting Star': '#ef4444',
  'Bullish Engulfing': '#22c55e',
  'Bearish Engulfing': '#ef4444',
}

export function BtcCandlestickChart({ candles, showPatterns = true }: { candles: BtcCandle[]; showPatterns?: boolean }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)

  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      layout: { background: { color: 'transparent' }, textColor: '#a3a3a3', fontFamily: 'monospace', fontSize: 10 },
      grid: { vertLines: { color: '#1a1a1a' }, horzLines: { color: '#1a1a1a' } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#262626' },
      timeScale: { borderColor: '#262626', timeVisible: true, secondsVisible: false },
      autoSize: true,
    })

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderVisible: false,
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    })
    candleSeries.priceScale().applyOptions({ scaleMargins: { top: 0.05, bottom: 0.25 } })

    const volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: '',
    })
    volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } })

    chartRef.current = chart
    candleSeriesRef.current = candleSeries
    volumeSeriesRef.current = volumeSeries

    return () => {
      chart.remove()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current) return
    if (candles.length === 0) return

    candleSeriesRef.current.setData(
      candles.map(c => ({ time: c.time as Time, open: c.open, high: c.high, low: c.low, close: c.close }))
    )
    volumeSeriesRef.current.setData(
      candles.map(c => ({
        time: c.time as Time,
        value: c.volume,
        color: c.close >= c.open ? 'rgba(34,197,94,0.4)' : 'rgba(239,68,68,0.4)',
      }))
    )

    if (showPatterns) {
      const patterns = detectPatterns(candles)
      const markers: SeriesMarker<Time>[] = patterns.map(p => ({
        time: p.time as Time,
        position: p.bullish ? 'belowBar' : 'aboveBar',
        color: PATTERN_COLORS[p.pattern] ?? '#a3a3a3',
        shape: p.bullish ? 'arrowUp' : 'arrowDown',
        text: p.pattern,
      }))
      candleSeriesRef.current.setMarkers(markers)
    } else {
      candleSeriesRef.current.setMarkers([])
    }

    chartRef.current?.timeScale().fitContent()
  }, [candles, showPatterns])

  return <div ref={containerRef} className="w-full h-full" />
}
