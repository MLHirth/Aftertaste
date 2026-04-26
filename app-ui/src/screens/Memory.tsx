import { useEffect, useState } from 'react'

import { getMemoryTracks, getNegativeArtists } from '../api/client'
import { SkipHeatmap } from '../components/SkipHeatmap'
import type { MemoryTrack, NegativeArtist } from '../types'

export function Memory() {
  const [negativeArtists, setNegativeArtists] = useState<NegativeArtist[]>([])
  const [tracks, setTracks] = useState<MemoryTrack[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    Promise.all([getNegativeArtists(), getMemoryTracks()])
      .then(([artists, memoryTracks]) => {
        setNegativeArtists(artists)
        setTracks(memoryTracks)
      })
      .catch((reason: unknown) => {
        if (reason instanceof Error) {
          setError(reason.message)
          return
        }
        setError('Failed to load memory data.')
      })
      .finally(() => setLoading(false))
  }, [])

  return (
    <main className="screen">
      <section className="hero-card">
        <div>
          <h2>Taste Memory</h2>
          <p>Track-level and artist-level behavior history with transparent signal counts.</p>
        </div>
      </section>

      {loading && <p className="muted">Loading memory...</p>}
      {error && (
        <section className="panel error">
          <strong>Error</strong>
          <p>{error}</p>
        </section>
      )}

      <SkipHeatmap artists={negativeArtists} />

      <section className="panel">
        <h3>Track Memory</h3>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Track</th>
                <th>Artists</th>
                <th>Early skips</th>
                <th>Completions</th>
                <th>Last played</th>
              </tr>
            </thead>
            <tbody>
              {tracks.map((track) => (
                <tr key={track.track_id}>
                  <td>{track.name}</td>
                  <td>{track.artists}</td>
                  <td>{track.early_skips}</td>
                  <td>{track.completions}</td>
                  <td>{track.last_played ? new Date(track.last_played).toLocaleDateString() : 'Never'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  )
}
