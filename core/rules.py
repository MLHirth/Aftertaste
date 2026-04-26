from __future__ import annotations

from datetime import datetime, timezone

from core.db import Database


DEFAULT_RULES: dict[str, float] = {
    "early_skip_cutoff_seconds": 30.0,
    "completion_ratio_threshold": 0.85,
    "mid_skip_ratio_threshold": 0.75,
    "revival_days_30": 30.0,
    "revival_days_90": 90.0,
    "revival_days_180": 180.0,
    "exploration_ratio": 0.25,
    "max_same_artist_per_20": 2.0,
    "recent_artist_random_enabled": 0.0,
    "recent_artist_random_slots": 2.0,
    "recent_artist_random_days": 14.0,
}


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def ensure_defaults(db: Database) -> None:
    now = _now_iso()
    for key, value in DEFAULT_RULES.items():
        db.execute(
            """
      INSERT INTO rules(rule_key, rule_value, updated_at)
      VALUES (?, ?, ?)
      ON CONFLICT(rule_key) DO NOTHING
      """,
            (key, value, now),
        )


def load_rules(db: Database) -> dict[str, float]:
    ensure_defaults(db)
    rows = db.query_all("SELECT rule_key, rule_value FROM rules")
    values = {row["rule_key"]: float(row["rule_value"]) for row in rows}
    merged = dict(DEFAULT_RULES)
    merged.update(values)
    return merged


def update_rules(db: Database, updates: dict[str, float]) -> dict[str, float]:
    now = _now_iso()
    for key, value in updates.items():
        if key not in DEFAULT_RULES:
            continue
        db.execute(
            """
      INSERT INTO rules(rule_key, rule_value, updated_at)
      VALUES (?, ?, ?)
      ON CONFLICT(rule_key)
      DO UPDATE SET rule_value = excluded.rule_value, updated_at = excluded.updated_at
      """,
            (key, float(value), now),
        )
    return load_rules(db)
