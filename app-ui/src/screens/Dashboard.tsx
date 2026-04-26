import { useEffect, useState } from 'react'

import { isClerkEnabled } from '../auth/clerk'
import { cloudSyncStatus, syncCloudNow } from '../api/client'
import { useAuthStore } from '../state/authStore'
import { usePlaybackStore } from '../state/playbackStore'

function pct(progressMs: number, durationMs: number) {
  if (!durationMs) return 0
  return Math.round((progressMs / durationMs) * 100)
}

export function Dashboard() {
  const [callbackText, setCallbackText] = useState('')
  const [displayProgressMs, setDisplayProgressMs] = useState(0)
  const [displayTrackId, setDisplayTrackId] = useState<string | null>(null)
  const [cloudNote, setCloudNote] = useState<string | null>(null)
  const auth = useAuthStore()
  const playback = usePlaybackStore()

  useEffect(() => {
    void auth.refreshStatus()
    void playback.refreshDashboard()

    const dashboardInterval = window.setInterval(() => {
      void playback.refreshDashboard()
    }, 5000)

    const authInterval = window.setInterval(() => {
      void auth.refreshStatus()
    }, 15000)

    return () => {
      window.clearInterval(dashboardInterval)
      window.clearInterval(authInterval)
    }
  }, [])

  useEffect(() => {
    const nowPlaying = playback.dashboard?.now_playing
    if (!nowPlaying) {
      setDisplayTrackId(null)
      setDisplayProgressMs(0)
      return
    }

    if (displayTrackId !== nowPlaying.track_id) {
      setDisplayTrackId(nowPlaying.track_id)
      setDisplayProgressMs(nowPlaying.progress_ms)
      return
    }

    if (nowPlaying.is_playing) {
      setDisplayProgressMs(nowPlaying.progress_ms)
      return
    }

    setDisplayProgressMs((current) => Math.min(current, nowPlaying.progress_ms))
  }, [playback.dashboard?.now_playing, displayTrackId])

  const status = auth.status
  const dash = playback.dashboard

  return (
    <main className="screen">
      <section className="hero-card">
        <div>
          <h2>Aftertaste Dashboard</h2>
          <p>
            Local-first listening memory. It scores from your own completions, skips,
            replays, saves, and recency.
          </p>
        </div>

        <div className="hero-actions">
          <button onClick={() => void playback.runSync()} disabled={playback.syncing}>
            {playback.syncing ? 'Syncing...' : 'Sync Sources'}
          </button>
          <button
            className="button-secondary"
            onClick={() => void playback.setPoller(!(dash?.poller_running ?? false))}
          >
            {(dash?.poller_running ?? false) ? 'Stop Monitor' : 'Start Monitor'}
          </button>
          {status?.cloud_sync_enabled && (
            <button
              className="button-secondary"
              onClick={() => {
                void syncCloudNow()
                  .then((result) => {
                    if (!result.enabled) {
                      setCloudNote('Cloud sync is not configured on this client yet.')
                      return
                    }
                    setCloudNote(
                      `Cloud sync: pushed ${result.pushed}, pulled ${result.pulled}, applied ${result.applied}`,
                    )
                  })
                  .catch((error: unknown) => {
                    setCloudNote(error instanceof Error ? error.message : 'Cloud sync failed.')
                  })
              }}
            >
              Sync Cloud Now
            </button>
          )}
          {isClerkEnabled() && (
            <button
              className="button-secondary"
              onClick={() => {
                void cloudSyncStatus()
                  .then((result) => {
                    setCloudNote(
                      `Cloud account ${result.user_id}: remote seq ${result.latest_seq}, pulled ${result.checkpoint.last_pulled_seq}`,
                    )
                  })
                  .catch((error: unknown) => {
                    setCloudNote(error instanceof Error ? error.message : 'Cloud status failed.')
                  })
              }}
            >
              Cloud Account Check
            </button>
          )}
        </div>
      </section>

      {cloudNote && <p className="muted">{cloudNote}</p>}

      {status && !status.has_client_id && (
        <section className="panel warning">
          <h3>Spotify Credentials Missing</h3>
          <p>
            Add `SPOTIFY_CLIENT_ID` to your `.env`, then refresh this page.
          </p>
        </section>
      )}

      {status?.has_client_id && !status.authorized && (
        <section className="panel auth-panel">
          <h3>Connect Spotify (PKCE)</h3>
          <p>
            Start login in your browser, approve access, then return to the app.
            If auto-return is blocked, paste the callback URL manually.
          </p>
          <div className="row-actions">
            <button onClick={() => void auth.beginAuth()} disabled={auth.loading}>
              {auth.loading ? 'Starting...' : 'Start Spotify Login'}
            </button>
          </div>
          {auth.pendingAuthUrl && (
            <div className="panel">
              <p className="muted">If automatic open fails, use this URL manually:</p>
              <label className="input-group">
                Spotify authorize URL
                <textarea readOnly value={auth.pendingAuthUrl} rows={2} />
              </label>
              <div className="row-actions">
                <button
                  className="button-secondary"
                  onClick={() => {
                    void navigator.clipboard.writeText(auth.pendingAuthUrl ?? '')
                  }}
                >
                  Copy URL
                </button>
              </div>
            </div>
          )}
          <label className="input-group">
            Callback URL or query string
            <textarea
              placeholder="aftertaste://callback?code=...&state=..."
              value={callbackText}
              onChange={(event) => setCallbackText(event.target.value)}
              rows={2}
            />
          </label>
          <button
            className="button-secondary"
            onClick={() => void auth.completeAuth(callbackText)}
            disabled={!callbackText.trim() || auth.loading}
          >
            Complete Login
          </button>
        </section>
      )}

      {status?.authorized && (
        <section className="stats-grid">
          <article className="stat-card">
            <span>Likely skips today</span>
            <strong>{dash?.likely_skip_count_today ?? 0}</strong>
          </article>
          <article className="stat-card">
            <span>Completions today</span>
            <strong>{dash?.completions_today ?? 0}</strong>
          </article>
          <article className="stat-card">
            <span>Next playlist refresh</span>
            <strong>
              {dash?.next_playlist_refresh_time
                ? new Date(dash.next_playlist_refresh_time).toLocaleString()
                : 'Not scheduled'}
            </strong>
          </article>
        </section>
      )}

      <section className="panel">
        <h3>Now Playing</h3>
        {dash?.now_playing ? (
          <div className="now-playing">
            <div>
              <strong>{dash.now_playing.name}</strong>
              <p>{dash.now_playing.artists}</p>
            </div>
            <span>{pct(displayProgressMs, dash.now_playing.duration_ms)}%</span>
          </div>
        ) : (
          <p className="muted">No active playback detected.</p>
        )}
      </section>

      <section className="two-col">
        <article className="panel">
          <h3>Top negative artists (7d)</h3>
          <ul className="simple-list">
            {(dash?.top_negative_artists ?? []).map((artist) => (
              <li key={artist.artist_id}>
                <span>{artist.name}</span>
                <strong>{artist.skip_count}</strong>
              </li>
            ))}
          </ul>
        </article>

        <article className="panel">
          <h3>Top revived tracks</h3>
          <ul className="simple-list">
            {(dash?.top_revived_tracks ?? []).map((track) => (
              <li key={track.track_id}>
                <span>{track.name}</span>
                <strong>{Number(track.score_revival).toFixed(1)}</strong>
              </li>
            ))}
          </ul>
        </article>
      </section>

      {(auth.error || playback.error) && (
        <section className="panel error">
          <strong>Error</strong>
          <p>{auth.error ?? playback.error}</p>
          <div className="row-actions">
            <button
              className="button-secondary"
              onClick={() => {
                void auth.refreshStatus()
                void playback.refreshDashboard()
              }}
            >
              Retry Connection
            </button>
          </div>
        </section>
      )}
    </main>
  )
}
