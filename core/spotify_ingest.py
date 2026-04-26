from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core.db import Database


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def upsert_artist(db: Database, artist: dict[str, Any]) -> None:
    artist_id = artist.get("id")
    if not artist_id:
        return
    db.execute(
        """
    INSERT INTO artists(artist_id, name, genres_json, popularity, last_sync_at)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(artist_id)
    DO UPDATE SET
      name = excluded.name,
      genres_json = excluded.genres_json,
      popularity = excluded.popularity,
      last_sync_at = excluded.last_sync_at
    """,
        (
            artist_id,
            artist.get("name") or "",
            json.dumps(artist.get("genres") or []),
            artist.get("popularity"),
            now_iso(),
        ),
    )


def upsert_track(db: Database, track: dict[str, Any]) -> None:
    track_id = track.get("id")
    if not track_id:
        return

    album = track.get("album") or {}
    album_id = album.get("id")
    if album_id:
        db.execute(
            """
      INSERT INTO albums(album_id, name, release_date, release_date_precision)
      VALUES (?, ?, ?, ?)
      ON CONFLICT(album_id)
      DO UPDATE SET
        name = excluded.name,
        release_date = excluded.release_date,
        release_date_precision = excluded.release_date_precision
      """,
            (
                album_id,
                album.get("name"),
                album.get("release_date"),
                album.get("release_date_precision"),
            ),
        )

    db.execute(
        """
    INSERT INTO tracks(
      track_id,
      name,
      album_id,
      duration_ms,
      popularity,
      is_playable,
      spotify_uri,
      last_metadata_sync_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(track_id)
    DO UPDATE SET
      name = excluded.name,
      album_id = excluded.album_id,
      duration_ms = excluded.duration_ms,
      popularity = excluded.popularity,
      is_playable = excluded.is_playable,
      spotify_uri = excluded.spotify_uri,
      last_metadata_sync_at = excluded.last_metadata_sync_at
    """,
        (
            track_id,
            track.get("name") or "",
            album_id,
            track.get("duration_ms"),
            track.get("popularity"),
            1
            if track.get("is_playable")
            else 0
            if track.get("is_playable") is False
            else None,
            track.get("uri"),
            now_iso(),
        ),
    )

    db.execute("DELETE FROM track_artists WHERE track_id = ?", (track_id,))
    for artist in track.get("artists") or []:
        upsert_artist(db, artist)
        artist_id = artist.get("id")
        if artist_id:
            db.execute(
                """
        INSERT OR IGNORE INTO track_artists(track_id, artist_id)
        VALUES (?, ?)
        """,
                (track_id, artist_id),
            )
