import { useEffect, useState } from 'react'

import { topUpQueue } from '../api/client'
import { TrackCard } from '../components/TrackCard'
import { useScoringStore } from '../state/scoringStore'

export function TodayMix() {
  const scoring = useScoringStore()
  const [queueNote, setQueueNote] = useState<string | null>(null)
  const [modeLabel, setModeLabel] = useState("Today's Mix")

  useEffect(() => {
    void scoring.refreshMix()
  }, [])

  const tracks = scoring.tracks

  return (
    <main className="screen">
      <section className="hero-card">
        <div>
          <h2>Today's Mix</h2>
          <p>40-track blend: familiar anchors, revived gems, and cautious exploration.</p>
        </div>
        <div className="hero-actions">
          <button
            onClick={() => {
              setModeLabel("Today's Mix")
              void scoring.generateMix(false)
            }}
            disabled={scoring.generating}
          >
            {scoring.generating ? 'Generating...' : 'Generate Today'}
          </button>
          <button
            className="button-secondary"
            onClick={() => {
              setModeLabel("Today's Mix")
              void scoring.generateMix(true)
            }}
            disabled={scoring.generating}
          >
            Write Private Playlists
          </button>
          <button
            className="button-secondary"
            onClick={() => {
              setModeLabel('Vibe Revival')
              void scoring.generateVibeRevivalMix(false)
            }}
            disabled={scoring.generating}
          >
            Vibe Revival Refresh
          </button>
          <button
            className="button-secondary"
            onClick={() => {
              setModeLabel('Vibe Revival')
              void scoring.generateVibeRevivalMix(true)
            }}
            disabled={scoring.generating}
          >
            Write Vibe Revival Playlist
          </button>
          <button
            className="button-secondary"
            onClick={() => {
              void topUpQueue(3).then((response) => {
                setQueueNote(`Queue: +${response.added} (depth ${response.depth_before} -> ${response.depth_after})`)
              })
            }}
          >
            Push Next to Queue
          </button>
        </div>
      </section>

      <p className="muted">Mode: {modeLabel}</p>
      {queueNote && <p className="muted">{queueNote}</p>}
      {scoring.error && (
        <section className="panel error">
          <strong>Error</strong>
          <p>{scoring.error}</p>
        </section>
      )}

      <section className="track-grid">
        {tracks.map((track, index) => (
          <TrackCard
            key={track.track_id}
            rank={track.rank ?? index + 1}
            name={track.name}
            artists={track.artists}
            bucket={track.bucket}
            explanation={track.explanation}
            score_total={Number(track.score_total ?? 0)}
            score_positive={Number(track.score_positive ?? 0)}
            score_negative={Number(track.score_negative ?? 0)}
            score_familiarity={Number(track.score_familiarity ?? 0)}
            score_revival={Number(track.score_revival ?? 0)}
            score_exploration={Number(track.score_exploration ?? 0)}
          />
        ))}
      </section>
    </main>
  )
}
