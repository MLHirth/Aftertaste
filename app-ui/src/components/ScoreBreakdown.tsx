type Props = {
  total: number
  positive: number
  negative: number
  familiarity: number
  revival: number
  exploration: number
}

function barWidth(value: number, scale = 10) {
  const clamped = Math.max(0, Math.min(scale, Math.abs(value)))
  return `${(clamped / scale) * 100}%`
}

export function ScoreBreakdown({
  total,
  positive,
  negative,
  familiarity,
  revival,
  exploration,
}: Props) {
  return (
    <div className="score-breakdown">
      <div className="score-row">
        <span>Total</span>
        <strong>{total.toFixed(2)}</strong>
      </div>
      <div className="score-bar score-bar-positive">
        <span style={{ width: barWidth(positive) }} />
      </div>
      <div className="score-row score-caption">
        <span>Positive</span>
        <span>{positive.toFixed(2)}</span>
      </div>

      <div className="score-bar score-bar-negative">
        <span style={{ width: barWidth(negative) }} />
      </div>
      <div className="score-row score-caption">
        <span>Negative</span>
        <span>{negative.toFixed(2)}</span>
      </div>

      <div className="score-tags">
        <small>Fam {familiarity.toFixed(1)}</small>
        <small>Revive {revival.toFixed(1)}</small>
        <small>Explore {exploration.toFixed(1)}</small>
      </div>
    </div>
  )
}
