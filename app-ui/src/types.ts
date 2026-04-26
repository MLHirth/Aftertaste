export type ConfigStatus = {
  has_client_id: boolean
  authorized: boolean
  redirect_uri: string
  db_path: string
  cloud_sync_enabled?: boolean
  cloud_api_base_url?: string | null
  server_master_enabled?: boolean
  spotify_mode?: 'desktop' | 'server_managed'
  cloud_spotify_connected?: boolean
}

export type DashboardData = {
  now_playing: {
    track_id: string
    name: string
    artists: string
    is_playing: boolean
    progress_ms: number
    duration_ms: number
  } | null
  likely_skip_count_today: number
  completions_today: number
  top_negative_artists: Array<{ artist_id: string; name: string; skip_count: number }>
  top_revived_tracks: Array<{ track_id: string; name: string; score_revival: number }>
  next_playlist_refresh_time: string
  poller_running: boolean
}

export type MixTrack = {
  rank: number
  track_id: string
  name: string
  artists: string
  spotify_uri?: string
  bucket: string
  explanation: string
  score_total: number
  score_positive: number
  score_negative: number
  score_familiarity: number
  score_freshness: number
  score_revival: number
  score_exploration: number
}

export type RuleSet = Record<string, number>

export type SourcePreference = {
  playlist_id: string
  name: string
  owner_id: string
  is_spotify_made_guess: 0 | 1
  include_source: 0 | 1
  manually_confirmed: 0 | 1
}

export type NegativeArtist = {
  artist_id: string
  name: string
  early_skip_count: number
  last_skip_at: string
}

export type MemoryTrack = {
  track_id: string
  name: string
  artists: string
  early_skips: number
  completions: number
  last_played: string | null
}
