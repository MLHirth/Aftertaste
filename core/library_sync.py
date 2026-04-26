from __future__ import annotations

from datetime import datetime, timezone

from core.db import Database
from core.spotify_client import SpotifyClient
from core.spotify_ingest import upsert_artist, upsert_track


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def sync_saved_tracks(db: Database, client: SpotifyClient) -> dict[str, int]:
    synced = 0
    offset = 0
    limit = 50

    while True:
        payload = client.get_saved_tracks(limit=limit, offset=offset)
        items = payload.get("items") or []
        if not items:
            break

        for entry in items:
            track = entry.get("track")
            if not track:
                continue
            upsert_track(db, track)
            db.execute(
                """
        INSERT INTO saved_tracks(track_id, added_at)
        VALUES (?, ?)
        ON CONFLICT(track_id)
        DO UPDATE SET added_at = excluded.added_at
        """,
                (track.get("id"), entry.get("added_at") or _now_iso()),
            )
            synced += 1

        offset += limit
        if offset >= int(payload.get("total", 0)):
            break

    return {"saved_tracks_synced": synced}


def sync_top_items(db: Database, client: SpotifyClient) -> dict[str, int]:
    synced_tracks = 0
    synced_artists = 0
    now = _now_iso()

    for time_range in ["short_term", "medium_term", "long_term"]:
        tracks = (
            client.get_top_tracks(time_range=time_range, limit=50).get("items") or []
        )
        for rank, track in enumerate(tracks, start=1):
            upsert_track(db, track)
            db.execute(
                """
        INSERT INTO top_track_affinity(track_id, time_range, rank, synced_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(track_id, time_range)
        DO UPDATE SET rank = excluded.rank, synced_at = excluded.synced_at
        """,
                (track.get("id"), time_range, rank, now),
            )
            synced_tracks += 1

        artists = (
            client.get_top_artists(time_range=time_range, limit=50).get("items") or []
        )
        for rank, artist in enumerate(artists, start=1):
            upsert_artist(db, artist)
            db.execute(
                """
        INSERT INTO top_artist_affinity(artist_id, time_range, rank, synced_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(artist_id, time_range)
        DO UPDATE SET rank = excluded.rank, synced_at = excluded.synced_at
        """,
                (artist.get("id"), time_range, rank, now),
            )
            synced_artists += 1

    return {
        "top_tracks_synced": synced_tracks,
        "top_artists_synced": synced_artists,
    }
