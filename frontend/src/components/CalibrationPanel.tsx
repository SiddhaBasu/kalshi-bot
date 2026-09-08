import type { CalibrationSummary } from '../types'

interface Props {
  calibration: CalibrationSummary
}

export function CalibrationPanel({ calibration }: Props) {
  const accuracyPct = (calibration.accuracy * 100).toFixed(0)
  const accuracyColor = calibration.accuracy >= 0.55 ? '#22c55e' : calibration.accuracy < 0.50 ? '#dc2626' : '#a1a1aa'
  const brierLabel = calibration.brier_score <= 0.20 ? 'Good' : calibration.brier_score <= 0.25 ? 'OK' : 'Poor'
  const brierColor = calibration.brier_score <= 0.20 ? '#22c55e' : calibration.brier_score <= 0.25 ? '#d97706' : '#dc2626'

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3">
        <div className="text-2xl font-bold tabular-nums" style={{ color: accuracyColor }}>
          {accuracyPct}%
        </div>
        <div className="text-[10px] text-neutral-500 leading-tight">
          <div>Accuracy</div>
          <div className="tabular-nums text-neutral-600">
            {Math.round(calibration.accuracy * calibration.total_with_outcome)}/{calibration.total_with_outcome} settled
          </div>
        </div>
      </div>

      <div className="flex items-center justify-between text-[10px]">
        <div>
          <span className="text-neutral-500">Brier: </span>
          <span className="tabular-nums" style={{ color: brierColor }}>
            {calibration.brier_score.toFixed(3)} ({brierLabel})
          </span>
        </div>
        <div className="text-neutral-600 tabular-nums">
          {calibration.total_signals} signals
        </div>
      </div>
    </div>
  )
}
