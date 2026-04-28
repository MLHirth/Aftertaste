import { useEffect, useState } from 'react'

import { isClerkEnabled } from '../auth/clerk'
import {
  cloudSpotifyAuthExchange,
  cloudSpotifyAuthStart,
  cloudSpotifyStatus,
  cloudSyncStatus,
  runCloudSpotifyAutomationNow,
  syncCloudNow,
} from '../api/client'
import { useAuthStore } from '../state/authStore'
import { usePlaybackStore } from '../state/playbackStore'

function pct(progressMs: number, durationMs: number) {
  if (!durationMs) return 0
  return Math.round((progressMs / durationMs) * 100)
}

function isDesktopApp() {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}

function explainCloudError(message: string) {
  const normalized = message.toLowerCase()
  if (normalized.includes('audience')) {
    return `${message} Check CLERK_AUDIENCE on server and VITE_CLERK_JWT_TEMPLATE in app use the same Clerk JWT template.`
  }
  if (normalized.includes('issuer')) {
    return `${message} Check CLERK_ISSUER matches your Clerk instance issuer URL exactly.`
  }
  if (normalized.includes('signature')) {
    return `${message} Check CLERK_JWKS_URL points to the same Clerk instance that issued the token.`
  }
  return message
}

type CloudSpotifyState = {
  user_id: string
  connected: boolean
  authorized: boolean
  has_refresh_token: boolean
  access_token_expires_at: string | null
  auth_error: string | null
  poller_running: boolean
  server_master_enabled: boolean
  server_master_interval_seconds: number
  automation_thread_running: boolean
  automation_run_count: number
  automation_last_run_at: string | null
  automation_last_ok: boolean
  automation_last_error: string | null
}

const CLOUD_SPOTIFY_SESSION_KEY = 'aftertaste.cloudSpotifySessionId'

export function Dashboard() {
  const [callbackText, setCallbackText] = useState('')
  const [displayProgressMs, setDisplayProgressMs] = useState(0)
  const [displayTrackId, setDisplayTrackId] = useState<string | null>(null)
  const [cloudNote, setCloudNote] = useState<string | null>(null)
  const [cloudSpotify, setCloudSpotify] = useState<CloudSpotifyState | null>(null)
  const [cloudSpotifyLoading, setCloudSpotifyLoading] = useState(false)
  const auth = useAuthStore()
  const playback = usePlaybackStore()

  useEffect(() => {
    void auth.refreshStatus()
    void playback.refreshDashboard()

    if (!isDesktopApp() && isClerkEnabled()) {
      setCloudSpotifyLoading(true)
      void cloudSpotifyStatus()
        .then((statusPayload) => {
          setCloudSpotify(statusPayload)
        })
        .catch((error: unknown) => {
          const message = error instanceof Error ? error.message : 'Cloud Spotify status failed.'
          setCloudNote(explainCloudError(message))
        })
        .finally(() => setCloudSpotifyLoading(false))
    }

    if (!isDesktopApp()) {
      const params = new URLSearchParams(window.location.search)
      const code = params.get('code')
      const state = params.get('state')
      const sessionId = window.sessionStorage.getItem(CLOUD_SPOTIFY_SESSION_KEY)
      if (code && state && sessionId) {
        setCloudSpotifyLoading(true)
        void cloudSpotifyAuthExchange(sessionId, state, code)
          .then(() => cloudSpotifyStatus())
          .then((statusPayload) => {
            setCloudSpotify(statusPayload)
            setCloudNote('Server Spotify connected. Automation can now run from the hosted server.')
          })
          .catch((error: unknown) => {
            const message = error instanceof Error ? error.message : 'Cloud Spotify connect failed.'
            setCloudNote(explainCloudError(message))
          })
          .finally(() => {
            window.sessionStorage.removeItem(CLOUD_SPOTIFY_SESSION_KEY)
            setCloudSpotifyLoading(false)
            const clean = `${window.location.origin}${window.location.pathname}${window.location.hash}`
            window.history.replaceState({}, document.title, clean)
          })
      }
    }

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
                    const pushedEvents = result.pushed_by_table?.play_events ?? 0
                    const reseedNote = result.reseeded
                      ? ' (server reseeded from full local history)'
                      : ''
                    setCloudNote(
                      `Cloud sync: pushed ${result.pushed} (play events ${pushedEvents}), pulled ${result.pulled}, applied ${result.applied}${reseedNote}`,
                    )
                  })
                  .catch((error: unknown) => {
                    const message = error instanceof Error ? error.message : 'Cloud sync failed.'
                    setCloudNote(explainCloudError(message))
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
                    if (!result.enabled) {
                      setCloudNote(result.error ?? 'Cloud sync is not configured on this client yet.')
                      return
                    }
                    if (!result.ok || !result.user_id || !result.checkpoint) {
                      setCloudNote(result.error ?? 'Cloud status check failed.')
                      return
                    }
                    setCloudNote(
                      `Cloud account ${result.user_id}: remote seq ${result.latest_seq ?? 0}, pulled ${result.checkpoint.last_pulled_seq}`,
                    )
                  })
                  .catch((error: unknown) => {
                    const message = error instanceof Error ? error.message : 'Cloud status failed.'
                    setCloudNote(explainCloudError(message))
                  })
              }}
            >
              Cloud Account Check
            </button>
          )}
        </div>
      </section>

      {cloudNote && <p className="muted">{cloudNote}</p>}

      {!isDesktopApp() && isClerkEnabled() && (
        <section className="panel">
          <h3>Server Spotify Automation</h3>
          <p>
            Connect Spotify once in web mode so the hosted server can keep playlists updated
            even while your desktop app is closed.
          </p>
          <p className="muted">
            Status:{' '}
            {cloudSpotify?.connected
              ? `Connected${cloudSpotify?.access_token_expires_at ? `, token refresh active` : ''}`
              : 'Not connected'}
            {cloudSpotify?.connected
              ? cloudSpotify.poller_running
                ? ', playback monitor running'
                : ', playback monitor idle'
              : ''}
            {cloudSpotify?.server_master_enabled
              ? `, server auto-run every ${cloudSpotify.server_master_interval_seconds}s`
              : ', server auto-run is disabled'}
          </p>
          {cloudSpotify && (
            <p className="muted">
              Automation:{' '}
              {cloudSpotify.automation_thread_running
                ? 'scheduler active'
                : 'scheduler inactive'}
              {cloudSpotify.automation_last_run_at
                ? `, last run ${new Date(cloudSpotify.automation_last_run_at).toLocaleString()}`
                : ', no run yet'}
              {cloudSpotify.automation_last_error
                ? `, last error: ${cloudSpotify.automation_last_error}`
                : cloudSpotify.automation_run_count > 0
                  ? cloudSpotify.automation_last_ok
                    ? ', last run ok'
                    : ', last run failed'
                  : ''}
            </p>
          )}
          {cloudSpotify?.auth_error && <p className="muted">Spotify auth error: {cloudSpotify.auth_error}</p>}
          <div className="row-actions">
            <button
              onClick={() => {
                setCloudSpotifyLoading(true)
                void cloudSpotifyAuthStart()
                  .then((payload) => {
                    window.sessionStorage.setItem(CLOUD_SPOTIFY_SESSION_KEY, payload.session_id)
                    window.location.href = payload.authorize_url
                  })
                  .catch((error: unknown) => {
                    const message = error instanceof Error ? error.message : 'Cloud Spotify start failed.'
                    setCloudNote(explainCloudError(message))
                    setCloudSpotifyLoading(false)
                  })
              }}
              disabled={cloudSpotifyLoading}
            >
              {cloudSpotifyLoading ? 'Starting...' : 'Connect Spotify on Server'}
            </button>
            <button
              className="button-secondary"
              onClick={() => {
                setCloudSpotifyLoading(true)
                void runCloudSpotifyAutomationNow()
                  .then((result) => {
                    setCloudNote(
                      `Server automation run: selected ${result.generated.selected_count}, playlists updated.`
                    )
                    return cloudSpotifyStatus()
                  })
                  .then((statusPayload) => {
                    setCloudSpotify(statusPayload)
                  })
                  .catch((error: unknown) => {
                    const message = error instanceof Error ? error.message : 'Server automation failed.'
                    setCloudNote(explainCloudError(message))
                  })
                  .finally(() => setCloudSpotifyLoading(false))
              }}
              disabled={cloudSpotifyLoading || !cloudSpotify?.connected}
            >
              Run Server Automation Now
            </button>
          </div>
        </section>
      )}

      {status && !status.has_client_id && (
        <section className="panel warning">
          <h3>Spotify Credentials Missing</h3>
          <p>
            Add `SPOTIFY_CLIENT_ID` to your `.env`, then refresh this page.
          </p>
        </section>
      )}

      {isDesktopApp() && status?.has_client_id && !status.authorized && (
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

      {(status?.authorized || (!isDesktopApp() && Boolean(dash))) && (
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
          <p className="muted">
            {isDesktopApp()
              ? 'No active playback detected.'
              : cloudSpotify?.connected
                ? 'No active playback detected from server-linked Spotify right now.'
                : 'Connect Spotify on Server to enable live playback and autonomous updates in web mode.'}
          </p>
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
