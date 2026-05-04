CREATE TABLE IF NOT EXISTS cloud_spotify_tokens (
  user_id TEXT PRIMARY KEY,
  token_ciphertext BLOB NOT NULL,
  token_salt BLOB NOT NULL,
  token_nonce BLOB NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_loaded_at TEXT,
  last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_cloud_spotify_tokens_updated_at
ON cloud_spotify_tokens(updated_at);
