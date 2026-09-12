import { useEffect, useRef } from 'react'
import { createChart, CrosshairMode, type IChartApi, type ISeriesApi, type IPriceLine, type SeriesMarker, type Time } from 'lightweight-charts'
import type { BtcCandle } from '../types'
import { detectPatterns } from '../utils/candlePatterns'
import { dedupeByTime } from '../utils/dedupeByTime'

const PATTERN_COLORS: Record<string, string> = {
  'Doji': '#787b86',
  'Hammer': '#26a69a',
  'Inverted Hammer': '#26a69a',
  'Shooting Star': '#ef5350',
  'Bullish Engulfing': '#26a69a',
  'Bearish Engulfing': '#ef5350',
}

export function BtcCandlestickChart({ candles, showPatterns = true, viewKey, targetPrice, targetLabel = 'Target' }: {
  candles: BtcCandle[]; showPatterns?: boolean; viewKey?: string; targetPrice?: number | null; targetLabel?: string
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const targetLineRef = useRef<IPriceLine | null>(null)
  const hasFitRef = useRef(false)

  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      layout: { background: { color: '#ffffff' }, textColor: '#131722', fontFamily: "-apple-system, 'Trebuchet MS', Roboto, Ubuntu, sans-serif", fontSize: 11 },
      grid: { vertLines: { color: '#f0f3fa' }, horzLines: { color: '#f0f3fa' } },
      crosshair: { mode: CrosshairMode.Normal, vertLine: { color: '#9598a1', labelBackgroundColor: '#131722' }, horzLine: { color: '#9598a1', labelBackgroundColor: '#131722' } },
      rightPriceScale: { borderColor: '#e0e3eb' },
      timeScale: { borderColor: '#e0e3eb', timeVisible: true, secondsVisible: false },
      autoSize: true,
    })

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#26a69a',
      downColor: '#ef5350',
      borderVisible: false,
      wickUpColor: '#26a69a',
      wickDownColor: '#ef5350',
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
    hasFitRef.current = false

    return () => {
      chart.remove()
      chartRef.current = null
    }
  }, [])

  // A deliberate filter change (interval/lookback) should re-fit the view;
  // a background data refresh at the same filter should never touch it.
  useEffect(() => {
    hasFitRef.current = false
  }, [viewKey])

  useEffect(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current || !chartRef.current) return
    if (candles.length === 0) return

    const deduped = dedupeByTime(candles)

    // Preserve whatever the user was looking at (pan/zoom) across data refreshes.
    const savedRange = hasFitRef.current ? chartRef.current.timeScale().getVisibleLogicalRange() : null

    candleSeriesRef.current.setData(
      deduped.map(c => ({ time: c.time as Time, open: c.open, high: c.high, low: c.low, close: c.close }))
    )
    volumeSeriesRef.current.setData(
      deduped.map(c => ({
        time: c.time as Time,
        value: c.volume,
        color: c.close >= c.open ? 'rgba(38,166,154,0.4)' : 'rgba(239,83,80,0.4)',
      }))
    )

    if (showPatterns) {
      const patterns = detectPatterns(deduped)
      const markers: SeriesMarker<Time>[] = patterns.map(p => ({
        time: p.time as Time,
        position: p.bullish ? 'belowBar' : 'aboveBar',
        color: PATTERN_COLORS[p.pattern] ?? '#787b86',
        shape: p.bullish ? 'arrowUp' : 'arrowDown',
        text: p.pattern,
      }))
      candleSeriesRef.current.setMarkers(markers)
    } else {
      candleSeriesRef.current.setMarkers([])
    }

    if (savedRange) {
      chartRef.current.timeScale().setVisibleLogicalRange(savedRange)
    } else {
      chartRef.current.timeScale().fitContent()
      hasFitRef.current = true
    }
  }, [candles, showPatterns])

  useEffect(() => {
    if (!candleSeriesRef.current) return
    if (targetLineRef.current) {
      candleSeriesRef.current.removePriceLine(targetLineRef.current)
      targetLineRef.current = null
    }
    if (targetPrice) {
      targetLineRef.current = candleSeriesRef.current.createPriceLine({
        price: targetPrice,
        color: '#ff9800',
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: targetLabel,
      })
    }
  }, [targetPrice, targetLabel])

  return <div ref={containerRef} className="w-full h-full" />
}
