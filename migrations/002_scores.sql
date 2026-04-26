CREATE TABLE IF NOT EXISTS track_scores (
  track_id TEXT PRIMARY KEY,
  score_total REAL,
  score_positive REAL,
  score_negative REAL,
  score_familiarity REAL,
  score_freshness REAL,
  score_revival REAL,
  score_exploration REAL,
  computed_at TEXT,
  FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS today_mix_cache (
  rank INTEGER PRIMARY KEY,
  track_id TEXT NOT NULL,
  bucket TEXT NOT NULL,
  explanation TEXT,
  computed_at TEXT NOT NULL,
  FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_track_scores_total ON track_scores(score_total DESC);
