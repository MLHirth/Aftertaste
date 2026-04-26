from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from core.db import Database
from core.spotify_client import SpotifyClient
from core.spotify_ingest import upsert_track


def reconcile_recent_history(db: Database, client: SpotifyClient) -> dict[str, int]:
    payload = client.get_recently_played(limit=50)
    items = payload.get("items") or []
    inserted = 0

    for item in items:
        track = item.get("track")
        played_at = item.get("played_at")
        if not track or not track.get("id") or not played_at:
            continue

        upsert_track(db, track)
        played_dt = datetime.fromisoformat(played_at.replace("Z", "+00:00"))
        window_start = (played_dt - timedelta(minutes=5)).isoformat()
        window_end = (played_dt + timedelta(minutes=5)).isoformat()

        exists = db.query_one(
            """
      SELECT event_id
      FROM play_events
      WHERE track_id = ?
        AND started_at BETWEEN ? AND ?
      LIMIT 1
      """,
            (track["id"], window_start, window_end),
        )

        if exists:
            continue

        duration_ms = int(track.get("duration_ms") or 0)
        start_guess = (
            played_dt - timedelta(milliseconds=max(1, duration_ms))
        ).astimezone(timezone.utc)
        db.execute(
            """
      INSERT INTO play_events(
        event_uid,
        track_id,
        started_at,
        ended_at,
        duration_listened_ms,
        completion_ratio,
        source_type,
        source_id,
        device_id,
        ended_reason
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      """,
            (
                str(
                    uuid.uuid5(uuid.NAMESPACE_URL, f"recent:{track['id']}:{played_at}")
                ),
                track["id"],
                start_guess.isoformat(),
                played_dt.astimezone(timezone.utc).isoformat(),
                duration_ms,
                1.0,
                "recently_played",
                None,
                None,
                "completed",
            ),
        )
        inserted += 1

    return {"reconciled_events": inserted}
