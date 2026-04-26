PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tracks (
  track_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  album_id TEXT,
  duration_ms INTEGER,
  popularity INTEGER,
  is_playable INTEGER,
  spotify_uri TEXT,
  last_metadata_sync_at TEXT
);

CREATE TABLE IF NOT EXISTS artists (
  artist_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  genres_json TEXT,
  popularity INTEGER,
  last_sync_at TEXT
);

CREATE TABLE IF NOT EXISTS track_artists (
  track_id TEXT NOT NULL,
  artist_id TEXT NOT NULL,
  PRIMARY KEY (track_id, artist_id),
  FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE,
  FOREIGN KEY (artist_id) REFERENCES artists(artist_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS albums (
  album_id TEXT PRIMARY KEY,
  name TEXT,
  release_date TEXT,
  release_date_precision TEXT
);

CREATE TABLE IF NOT EXISTS playlists (
  playlist_id TEXT PRIMARY KEY,
  name TEXT,
  owner_id TEXT,
  is_private INTEGER,
  is_spotify_made_guess INTEGER,
  snapshot_id TEXT,
  last_sync_at TEXT
);

CREATE TABLE IF NOT EXISTS playlist_tracks (
  playlist_id TEXT NOT NULL,
  track_id TEXT NOT NULL,
  position INTEGER NOT NULL,
  added_at TEXT,
  PRIMARY KEY (playlist_id, position),
  FOREIGN KEY (playlist_id) REFERENCES playlists(playlist_id) ON DELETE CASCADE,
  FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS saved_tracks (
  track_id TEXT PRIMARY KEY,
  added_at TEXT,
  FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS play_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  track_id TEXT NOT NULL,
  started_at TEXT,
  ended_at TEXT,
  duration_listened_ms INTEGER,
  completion_ratio REAL,
  source_type TEXT,
  source_id TEXT,
  device_id TEXT,
  ended_reason TEXT,
  FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rules (
  rule_key TEXT PRIMARY KEY,
  rule_value REAL NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_preferences (
  playlist_id TEXT PRIMARY KEY,
  include_source INTEGER NOT NULL DEFAULT 1,
  manually_confirmed INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (playlist_id) REFERENCES playlists(playlist_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS top_track_affinity (
  track_id TEXT NOT NULL,
  time_range TEXT NOT NULL,
  rank INTEGER NOT NULL,
  synced_at TEXT NOT NULL,
  PRIMARY KEY (track_id, time_range),
  FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS top_artist_affinity (
  artist_id TEXT NOT NULL,
  time_range TEXT NOT NULL,
  rank INTEGER NOT NULL,
  synced_at TEXT NOT NULL,
  PRIMARY KEY (artist_id, time_range),
  FOREIGN KEY (artist_id) REFERENCES artists(artist_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_play_events_track_started ON play_events(track_id, started_at);
CREATE INDEX IF NOT EXISTS idx_play_events_reason_started ON play_events(ended_reason, started_at);
CREATE INDEX IF NOT EXISTS idx_playlist_tracks_track ON playlist_tracks(track_id);
