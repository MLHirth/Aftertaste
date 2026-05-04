import type {
  ConfigStatus,
  DashboardData,
  MemoryTrack,
  MixTrack,
  NegativeArtist,
  RuleSet,
  SourcePreference,
} from '../types'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8765'

type TokenProvider = (() => Promise<string | null>) | null

let authTokenProvider: TokenProvider = null

export function setAuthTokenProvider(provider: TokenProvider) {
  authTokenProvider = provider
}

function isLocalApiBase() {
  try {
    const url = new URL(API_BASE)
    return url.hostname === '127.0.0.1' || url.hostname === 'localhost' || url.hostname === '0.0.0.0'
  } catch {
    return API_BASE.includes('127.0.0.1') || API_BASE.includes('localhost')
  }
}

function shouldAttachAuth(path: string) {
  if (!isLocalApiBase()) {
    return true
  }
  return (
    path.startsWith('/sync/cloud-') ||
    path.startsWith('/cloud/sync/') ||
    path.startsWith('/cloud/spotify/')
  )
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const dynamicHeaders: Record<string, string> = {}
  if (authTokenProvider && shouldAttachAuth(path)) {
    try {
      const token = await authTokenProvider()
      if (token) {
        dynamicHeaders.Authorization = `Bearer ${token}`
      }
    } catch {
      // no-op
    }
  }

  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...dynamicHeaders,
      ...(init?.headers ?? {}),
    },
    ...init,
  })

  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    try {
      const payload = await response.json()
      if (payload?.detail) {
        message = payload.detail
      }
    } catch {
      // no-op
    }
    throw new Error(message)
  }

  return (await response.json()) as T
}

export function getConfigStatus() {
  return request<ConfigStatus>('/config/status')
}

export function getDashboard() {
  return request<DashboardData>('/dashboard')
}

export function getTodayMix(limit = 40) {
  return request<MixTrack[]>(`/today-mix?limit=${limit}`)
}

export function startAuth() {
  return request<{ session_id: string; authorize_url: string }>('/auth/start', {
    method: 'POST',
  })
}

export function exchangeAuth(sessionId: string, state: string, code: string) {
  return request<{ authorized: boolean }>('/auth/exchange', {
    method: 'POST',
    body: JSON.stringify({
      session_id: sessionId,
      state,
      code,
    }),
  })
}

export function syncAll() {
  return request<Record<string, number>>('/sync/all', { method: 'POST' })
}

export function syncCloudNow() {
  return request<{
    enabled: boolean
    pushed: number
    pushed_by_table?: Record<string, number>
    pulled: number
    applied: number
    skipped: number
    last_pushed_seq?: number
    last_pulled_seq?: number
    reseeded?: boolean
  }>('/sync/cloud-now', { method: 'POST' })
}

export function cloudSyncStatus() {
  return request<{
    enabled?: boolean
    ok: boolean
    user_id?: string
    latest_seq?: number
    checkpoint?: { last_pushed_seq: number; last_pulled_seq: number }
    error?: string
  }>('/sync/cloud-status')
}

export function cloudSpotifyStatus() {
  return request<{
    user_id: string
    connected: boolean
    authorized: boolean
    has_refresh_token: boolean
    access_token_expires_at: string | null
    auth_error: string | null
    token_storage_mode: string
    token_persisted: boolean
    token_store_error: string | null
    poller_running: boolean
    server_master_enabled: boolean
    server_master_interval_seconds: number
    automation_thread_running: boolean
    automation_run_count: number
    automation_last_run_at: string | null
    automation_last_ok: boolean
    automation_last_error: string | null
  }>('/cloud/spotify/status')
}

export function cloudSpotifyAuthStart() {
  return request<{ session_id: string; authorize_url: string }>('/cloud/spotify/auth/start', {
    method: 'POST',
  })
}

export function cloudSpotifyAuthExchange(sessionId: string, state: string, code: string) {
  return request<{ authorized: boolean; expires_in?: number }>('/cloud/spotify/auth/exchange', {
    method: 'POST',
    body: JSON.stringify({
      session_id: sessionId,
      state,
      code,
    }),
  })
}

export function runCloudSpotifyAutomationNow() {
  return request<{
    ok: boolean
    user_id: string
    sync: Record<string, number>
    generated: { candidate_count: number; selected_count: number }
    playlists: Record<string, string>
  }>('/cloud/spotify/automation/run', {
    method: 'POST',
  })
}

export function generateToday(writeToSpotify: boolean) {
  return request<{ tracks: MixTrack[]; selected_count: number }>('/generate/today', {
    method: 'POST',
    body: JSON.stringify({ write_to_spotify: writeToSpotify }),
  })
}

export function generateVibeRevival(writeToSpotify: boolean) {
  return request<{ tracks: MixTrack[]; selected_count: number }>('/generate/vibe-revival', {
    method: 'POST',
    body: JSON.stringify({ write_to_spotify: writeToSpotify }),
  })
}

export function topUpQueue(targetDepth = 3) {
  return request<{ added: number; depth_before: number; depth_after: number }>('/queue/top-up', {
    method: 'POST',
    body: JSON.stringify({ target_depth: targetDepth }),
  })
}

export function startPoller() {
  return request<{ running: boolean }>('/poller/start', { method: 'POST' })
}

export function stopPoller() {
  return request<{ running: boolean }>('/poller/stop', { method: 'POST' })
}

export function getRules() {
  return request<RuleSet>('/rules')
}

export function saveRules(updates: RuleSet) {
  return request<RuleSet>('/rules', {
    method: 'PUT',
    body: JSON.stringify({ updates }),
  })
}

export function getSources() {
  return request<SourcePreference[]>('/sources')
}

export function saveSource(
  playlistId: string,
  includeSource: boolean,
  manuallyConfirmed = true,
) {
  return request<{ ok: boolean }>(`/sources/${playlistId}`, {
    method: 'PUT',
    body: JSON.stringify({
      include_source: includeSource,
      manually_confirmed: manuallyConfirmed,
    }),
  })
}

export function getNegativeArtists() {
  return request<NegativeArtist[]>('/memory/negative-artists')
}

export function getMemoryTracks(limit = 120) {
  return request<MemoryTrack[]>(`/memory/tracks?limit=${limit}`)
}
