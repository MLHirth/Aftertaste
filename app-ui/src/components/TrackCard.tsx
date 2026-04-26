import { ScoreBreakdown } from './ScoreBreakdown'

type Props = {
  rank: number
  name: string
  artists: string
  bucket: string
  explanation: string
  score_total: number
  score_positive: number
  score_negative: number
  score_familiarity: number
  score_revival: number
  score_exploration: number
}

export function TrackCard(props: Props) {
  return (
    <article className="track-card">
      <header className="track-header">
        <p className="track-rank">#{props.rank}</p>
        <span className={`bucket bucket-${props.bucket.toLowerCase()}`}>{props.bucket}</span>
      </header>

      <h3>{props.name}</h3>
      <p className="track-artists">{props.artists}</p>
      <p className="track-explainer">{props.explanation}</p>

      <ScoreBreakdown
        total={props.score_total}
        positive={props.score_positive}
        negative={props.score_negative}
        familiarity={props.score_familiarity}
        revival={props.score_revival}
        exploration={props.score_exploration}
      />
    </article>
  )
}
