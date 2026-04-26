CREATE TABLE IF NOT EXISTS track_edges (
  src_track_id TEXT NOT NULL,
  dst_track_id TEXT NOT NULL,
  edge_type TEXT NOT NULL,
  weight REAL NOT NULL,
  PRIMARY KEY (src_track_id, dst_track_id, edge_type),
  FOREIGN KEY (src_track_id) REFERENCES tracks(track_id) ON DELETE CASCADE,
  FOREIGN KEY (dst_track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS avoid_tracks (
  track_id TEXT PRIMARY KEY,
  reason TEXT NOT NULL,
  until_date TEXT,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_track_edges_dst ON track_edges(dst_track_id, edge_type);
