import type { NegativeArtist } from '../types'

type Props = {
  artists: NegativeArtist[]
}

function tone(level: number) {
  if (level >= 12) return 'heat-5'
  if (level >= 8) return 'heat-4'
  if (level >= 5) return 'heat-3'
  if (level >= 3) return 'heat-2'
  return 'heat-1'
}

export function SkipHeatmap({ artists }: Props) {
  return (
    <section className="panel">
      <h3>Skip Concentration</h3>
      <div className="heatmap-grid">
        {artists.slice(0, 20).map((artist) => (
          <div key={artist.artist_id} className={`heat-cell ${tone(artist.early_skip_count)}`}>
            <strong>{artist.name}</strong>
            <span>{artist.early_skip_count} early skips</span>
          </div>
        ))}
      </div>
    </section>
  )
}
