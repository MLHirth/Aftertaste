ALTER TABLE play_events ADD COLUMN event_uid TEXT;

UPDATE play_events
SET event_uid = COALESCE(event_uid, 'legacy-' || event_id)
WHERE event_uid IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_play_events_event_uid ON play_events(event_uid);

CREATE TABLE IF NOT EXISTS sync_context (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  suppress_logging INTEGER NOT NULL DEFAULT 0
);

INSERT INTO sync_context(id, suppress_logging)
VALUES (1, 0)
ON CONFLICT(id) DO NOTHING;

CREATE TABLE IF NOT EXISTS sync_log (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  table_name TEXT NOT NULL,
  pk_json TEXT NOT NULL,
  op TEXT NOT NULL,
  changed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sync_log_seq ON sync_log(seq);
CREATE INDEX IF NOT EXISTS idx_sync_log_table_seq ON sync_log(table_name, seq);

CREATE TABLE IF NOT EXISTS cloud_sync_checkpoint (
  remote_name TEXT PRIMARY KEY,
  last_pushed_seq INTEGER NOT NULL DEFAULT 0,
  last_pulled_seq INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS sync_tracks_ai AFTER INSERT ON tracks
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES ('tracks', json_object('track_id', NEW.track_id), 'upsert');
END;

CREATE TRIGGER IF NOT EXISTS sync_tracks_au AFTER UPDATE ON tracks
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES ('tracks', json_object('track_id', NEW.track_id), 'upsert');
END;

CREATE TRIGGER IF NOT EXISTS sync_tracks_ad AFTER DELETE ON tracks
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES ('tracks', json_object('track_id', OLD.track_id), 'delete');
END;

CREATE TRIGGER IF NOT EXISTS sync_artists_ai AFTER INSERT ON artists
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES ('artists', json_object('artist_id', NEW.artist_id), 'upsert');
END;

CREATE TRIGGER IF NOT EXISTS sync_artists_au AFTER UPDATE ON artists
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES ('artists', json_object('artist_id', NEW.artist_id), 'upsert');
END;

CREATE TRIGGER IF NOT EXISTS sync_artists_ad AFTER DELETE ON artists
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES ('artists', json_object('artist_id', OLD.artist_id), 'delete');
END;

CREATE TRIGGER IF NOT EXISTS sync_track_artists_ai AFTER INSERT ON track_artists
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES (
    'track_artists',
    json_object('track_id', NEW.track_id, 'artist_id', NEW.artist_id),
    'upsert'
  );
END;

CREATE TRIGGER IF NOT EXISTS sync_track_artists_au AFTER UPDATE ON track_artists
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES (
    'track_artists',
    json_object('track_id', NEW.track_id, 'artist_id', NEW.artist_id),
    'upsert'
  );
END;

CREATE TRIGGER IF NOT EXISTS sync_track_artists_ad AFTER DELETE ON track_artists
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES (
    'track_artists',
    json_object('track_id', OLD.track_id, 'artist_id', OLD.artist_id),
    'delete'
  );
END;

CREATE TRIGGER IF NOT EXISTS sync_albums_ai AFTER INSERT ON albums
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES ('albums', json_object('album_id', NEW.album_id), 'upsert');
END;

CREATE TRIGGER IF NOT EXISTS sync_albums_au AFTER UPDATE ON albums
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES ('albums', json_object('album_id', NEW.album_id), 'upsert');
END;

CREATE TRIGGER IF NOT EXISTS sync_albums_ad AFTER DELETE ON albums
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES ('albums', json_object('album_id', OLD.album_id), 'delete');
END;

CREATE TRIGGER IF NOT EXISTS sync_saved_tracks_ai AFTER INSERT ON saved_tracks
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES ('saved_tracks', json_object('track_id', NEW.track_id), 'upsert');
END;

CREATE TRIGGER IF NOT EXISTS sync_saved_tracks_au AFTER UPDATE ON saved_tracks
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES ('saved_tracks', json_object('track_id', NEW.track_id), 'upsert');
END;

CREATE TRIGGER IF NOT EXISTS sync_saved_tracks_ad AFTER DELETE ON saved_tracks
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES ('saved_tracks', json_object('track_id', OLD.track_id), 'delete');
END;

CREATE TRIGGER IF NOT EXISTS sync_play_events_ai AFTER INSERT ON play_events
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES (
    'play_events',
    json_object('event_uid', COALESCE(NEW.event_uid, 'legacy-' || NEW.event_id)),
    'upsert'
  );
END;

CREATE TRIGGER IF NOT EXISTS sync_play_events_au AFTER UPDATE ON play_events
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES (
    'play_events',
    json_object('event_uid', COALESCE(NEW.event_uid, 'legacy-' || NEW.event_id)),
    'upsert'
  );
END;

CREATE TRIGGER IF NOT EXISTS sync_play_events_ad AFTER DELETE ON play_events
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES (
    'play_events',
    json_object('event_uid', COALESCE(OLD.event_uid, 'legacy-' || OLD.event_id)),
    'delete'
  );
END;

CREATE TRIGGER IF NOT EXISTS sync_playlists_ai AFTER INSERT ON playlists
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES ('playlists', json_object('playlist_id', NEW.playlist_id), 'upsert');
END;

CREATE TRIGGER IF NOT EXISTS sync_playlists_au AFTER UPDATE ON playlists
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES ('playlists', json_object('playlist_id', NEW.playlist_id), 'upsert');
END;

CREATE TRIGGER IF NOT EXISTS sync_playlists_ad AFTER DELETE ON playlists
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES ('playlists', json_object('playlist_id', OLD.playlist_id), 'delete');
END;

CREATE TRIGGER IF NOT EXISTS sync_playlist_tracks_ai AFTER INSERT ON playlist_tracks
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES (
    'playlist_tracks',
    json_object('playlist_id', NEW.playlist_id, 'position', NEW.position),
    'upsert'
  );
END;

CREATE TRIGGER IF NOT EXISTS sync_playlist_tracks_au AFTER UPDATE ON playlist_tracks
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES (
    'playlist_tracks',
    json_object('playlist_id', NEW.playlist_id, 'position', NEW.position),
    'upsert'
  );
END;

CREATE TRIGGER IF NOT EXISTS sync_playlist_tracks_ad AFTER DELETE ON playlist_tracks
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES (
    'playlist_tracks',
    json_object('playlist_id', OLD.playlist_id, 'position', OLD.position),
    'delete'
  );
END;

CREATE TRIGGER IF NOT EXISTS sync_rules_ai AFTER INSERT ON rules
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES ('rules', json_object('rule_key', NEW.rule_key), 'upsert');
END;

CREATE TRIGGER IF NOT EXISTS sync_rules_au AFTER UPDATE ON rules
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES ('rules', json_object('rule_key', NEW.rule_key), 'upsert');
END;

CREATE TRIGGER IF NOT EXISTS sync_rules_ad AFTER DELETE ON rules
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES ('rules', json_object('rule_key', OLD.rule_key), 'delete');
END;

CREATE TRIGGER IF NOT EXISTS sync_source_preferences_ai AFTER INSERT ON source_preferences
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES (
    'source_preferences',
    json_object('playlist_id', NEW.playlist_id),
    'upsert'
  );
END;

CREATE TRIGGER IF NOT EXISTS sync_source_preferences_au AFTER UPDATE ON source_preferences
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES (
    'source_preferences',
    json_object('playlist_id', NEW.playlist_id),
    'upsert'
  );
END;

CREATE TRIGGER IF NOT EXISTS sync_source_preferences_ad AFTER DELETE ON source_preferences
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES (
    'source_preferences',
    json_object('playlist_id', OLD.playlist_id),
    'delete'
  );
END;

CREATE TRIGGER IF NOT EXISTS sync_top_track_affinity_ai AFTER INSERT ON top_track_affinity
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES (
    'top_track_affinity',
    json_object('track_id', NEW.track_id, 'time_range', NEW.time_range),
    'upsert'
  );
END;

CREATE TRIGGER IF NOT EXISTS sync_top_track_affinity_au AFTER UPDATE ON top_track_affinity
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES (
    'top_track_affinity',
    json_object('track_id', NEW.track_id, 'time_range', NEW.time_range),
    'upsert'
  );
END;

CREATE TRIGGER IF NOT EXISTS sync_top_track_affinity_ad AFTER DELETE ON top_track_affinity
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES (
    'top_track_affinity',
    json_object('track_id', OLD.track_id, 'time_range', OLD.time_range),
    'delete'
  );
END;

CREATE TRIGGER IF NOT EXISTS sync_top_artist_affinity_ai AFTER INSERT ON top_artist_affinity
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES (
    'top_artist_affinity',
    json_object('artist_id', NEW.artist_id, 'time_range', NEW.time_range),
    'upsert'
  );
END;

CREATE TRIGGER IF NOT EXISTS sync_top_artist_affinity_au AFTER UPDATE ON top_artist_affinity
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES (
    'top_artist_affinity',
    json_object('artist_id', NEW.artist_id, 'time_range', NEW.time_range),
    'upsert'
  );
END;

CREATE TRIGGER IF NOT EXISTS sync_top_artist_affinity_ad AFTER DELETE ON top_artist_affinity
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES (
    'top_artist_affinity',
    json_object('artist_id', OLD.artist_id, 'time_range', OLD.time_range),
    'delete'
  );
END;

CREATE TRIGGER IF NOT EXISTS sync_avoid_tracks_ai AFTER INSERT ON avoid_tracks
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES ('avoid_tracks', json_object('track_id', NEW.track_id), 'upsert');
END;

CREATE TRIGGER IF NOT EXISTS sync_avoid_tracks_au AFTER UPDATE ON avoid_tracks
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES ('avoid_tracks', json_object('track_id', NEW.track_id), 'upsert');
END;

CREATE TRIGGER IF NOT EXISTS sync_avoid_tracks_ad AFTER DELETE ON avoid_tracks
WHEN COALESCE((SELECT suppress_logging FROM sync_context WHERE id = 1), 0) = 0
BEGIN
  INSERT INTO sync_log(table_name, pk_json, op)
  VALUES ('avoid_tracks', json_object('track_id', OLD.track_id), 'delete');
END;
