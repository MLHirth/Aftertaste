import { useEffect, useState } from 'react'

import { getSources, saveSource } from '../api/client'
import type { SourcePreference } from '../types'

export function Sources() {
  const [sources, setSources] = useState<SourcePreference[]>([])
  const [savingId, setSavingId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = async () => {
    try {
      const list = await getSources()
      setSources(list)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Failed to load sources.')
    }
  }

  useEffect(() => {
    void refresh()
  }, [])

  return (
    <main className="screen">
      <section className="hero-card">
        <div>
          <h2>Sources</h2>
          <p>
            Include or exclude playlists used for candidate generation. Spotify-made status is
            inferred and manually confirmable.
          </p>
        </div>
      </section>

      <section className="panel">
        <h3>Playlist Sources</h3>
        <div className="source-list">
          {sources.map((source) => (
            <label className="source-item" key={source.playlist_id}>
              <div>
                <strong>{source.name}</strong>
                <p>
                  Owner: {source.owner_id}
                  {source.is_spotify_made_guess ? ' | likely Spotify-made' : ''}
                </p>
              </div>
              <div className="source-actions">
                <input
                  type="checkbox"
                  checked={Boolean(source.include_source)}
                  onChange={async (event) => {
                    setSavingId(source.playlist_id)
                    try {
                      await saveSource(
                        source.playlist_id,
                        event.target.checked,
                        Boolean(source.manually_confirmed),
                      )
                      await refresh()
                    } catch (reason) {
                      setError(reason instanceof Error ? reason.message : 'Failed to update source.')
                    } finally {
                      setSavingId(null)
                    }
                  }}
                />
                <span>{savingId === source.playlist_id ? 'Saving...' : 'Included'}</span>
              </div>
            </label>
          ))}
        </div>
      </section>

      {error && (
        <section className="panel error">
          <strong>Error</strong>
          <p>{error}</p>
        </section>
      )}
    </main>
  )
}
