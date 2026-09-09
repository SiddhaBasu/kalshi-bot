import type { BtcCandle } from '../types'

export interface PatternMatch {
  time: number
  pattern: string
  bullish: boolean
}

const SMA_WINDOW = 5

function sma(candles: BtcCandle[], endIdx: number, window: number): number {
  const start = Math.max(0, endIdx - window)
  const slice = candles.slice(start, endIdx)
  if (slice.length === 0) return candles[endIdx].close
  return slice.reduce((s, c) => s + c.close, 0) / slice.length
}

/**
 * Detects a small set of classic single/two-candle patterns. Shape-only rules
 * (body vs. wick proportions) disambiguated by local trend (SMA(5) of prior
 * closes) where the same shape means different things in an uptrend vs. downtrend
 * (e.g. hammer vs. shooting star).
 */
export function detectPatterns(candles: BtcCandle[]): PatternMatch[] {
  const matches: PatternMatch[] = []

  for (let i = 1; i < candles.length; i++) {
    const c = candles[i]
    const prev = candles[i - 1]
    const body = Math.abs(c.close - c.open)
    const range = c.high - c.low
    if (range <= 0) continue

    const upperWick = c.high - Math.max(c.open, c.close)
    const lowerWick = Math.min(c.open, c.close) - c.low
    const bullish = c.close > c.open
    const trendUp = c.close > sma(candles, i, SMA_WINDOW)

    // Doji: body is negligible relative to the candle's full range
    if (body <= range * 0.1) {
      matches.push({ time: c.time, pattern: 'Doji', bullish: trendUp })
      continue
    }

    // Hammer / Shooting Star: small body, one long wick, tiny opposite wick
    const smallBody = body <= range * 0.35
    if (smallBody && lowerWick >= body * 2 && upperWick <= body * 0.5) {
      matches.push({
        time: c.time,
        pattern: trendUp ? 'Shooting Star' : 'Hammer',
        bullish: !trendUp,
      })
      continue
    }
    if (smallBody && upperWick >= body * 2 && lowerWick <= body * 0.5) {
      matches.push({
        time: c.time,
        pattern: trendUp ? 'Inverted Hammer' : 'Shooting Star',
        bullish: !trendUp,
      })
      continue
    }

    // Engulfing: current body fully covers the previous body, opposite direction
    const prevBullish = prev.close > prev.open
    if (bullish && !prevBullish && c.open <= prev.close && c.close >= prev.open) {
      matches.push({ time: c.time, pattern: 'Bullish Engulfing', bullish: true })
      continue
    }
    if (!bullish && prevBullish && c.open >= prev.close && c.close <= prev.open) {
      matches.push({ time: c.time, pattern: 'Bearish Engulfing', bullish: false })
      continue
    }
  }

  return matches
}
