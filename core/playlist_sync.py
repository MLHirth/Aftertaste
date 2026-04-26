from __future__ import annotations

import logging
from datetime import datetime, timezone

from core.db import Database
from core.spotify_client import SpotifyClient
from core.spotify_ingest import upsert_track

SPOTIFY_MADE_FAMILIES = (
    "discover weekly",
    "release radar",
    "daily mix",
    "on repeat",
    "repeat rewind",
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def is_likely_spotify_made(name: str, owner_id: str | None) -> int:
    owner = (owner_id or "").lower()
    if owner == "spotify":
        return 1
    lowered = name.lower()
    return 1 if any(family in lowered for family in SPOTIFY_MADE_FAMILIES) else 0


def sync_playlists(db: Database, client: SpotifyClient) -> dict[str, int]:
    offset = 0
    limit = 50
    playlists_synced = 0
    playlist_items_synced = 0
    playlists_item_sync_failed = 0
    playlists_item_sync_forbidden = 0
    now = _now_iso()

    while True:
        payload = client.get_playlists(limit=limit, offset=offset)
        items = payload.get("items") or []
        if not items:
            break

        for playlist in items:
            playlist_id = playlist.get("id")
            if not playlist_id:
                continue

            owner_id = (playlist.get("owner") or {}).get("id")
            guess = is_likely_spotify_made(playlist.get("name") or "", owner_id)

            db.execute(
                """
        INSERT INTO playlists(
          playlist_id,
          name,
          owner_id,
          is_private,
          is_spotify_made_guess,
          snapshot_id,
          last_sync_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(playlist_id)
        DO UPDATE SET
          name = excluded.name,
          owner_id = excluded.owner_id,
          is_private = excluded.is_private,
          is_spotify_made_guess = excluded.is_spotify_made_guess,
          snapshot_id = excluded.snapshot_id,
          last_sync_at = excluded.last_sync_at
        """,
                (
                    playlist_id,
                    playlist.get("name"),
                    owner_id,
                    1 if playlist.get("public") is False else 0,
                    guess,
                    playlist.get("snapshot_id"),
                    now,
                ),
            )

            db.execute(
                """
        INSERT INTO source_preferences(playlist_id, include_source, manually_confirmed, updated_at)
        VALUES (?, 1, 0, ?)
        ON CONFLICT(playlist_id) DO NOTHING
        """,
                (playlist_id, now),
            )

            pref = db.query_one(
                "SELECT include_source FROM source_preferences WHERE playlist_id = ?",
                (playlist_id,),
            )
            include_source = bool(pref and pref["include_source"])

            if include_source:
                try:
                    db.execute(
                        "DELETE FROM playlist_tracks WHERE playlist_id = ?",
                        (playlist_id,),
                    )
                    playlist_offset = 0
                    pos = 0

                    while True:
                        tracks_payload = client.get_playlist_items(
                            playlist_id=playlist_id,
                            limit=100,
                            offset=playlist_offset,
                        )
                        if tracks_payload.get("forbidden"):
                            playlists_item_sync_forbidden += 1
                            db.execute(
                                """
                UPDATE source_preferences
                SET include_source = 0,
                    updated_at = ?
                WHERE playlist_id = ?
                """,
                                (now, playlist_id),
                            )
                            logger.info(
                                "No item access for playlist '%s' (%s). Source auto-disabled.",
                                playlist.get("name") or playlist_id,
                                playlist_id,
                            )
                            break

                        track_items = tracks_payload.get("items") or []
                        if not track_items:
                            break

                        for item in track_items:
                            track = item.get("track")
                            if not track or not track.get("id"):
                                continue
                            upsert_track(db, track)
                            db.execute(
                                """
              INSERT INTO playlist_tracks(playlist_id, track_id, position, added_at)
              VALUES (?, ?, ?, ?)
              ON CONFLICT(playlist_id, position)
              DO UPDATE SET track_id = excluded.track_id, added_at = excluded.added_at
              """,
                                (playlist_id, track["id"], pos, item.get("added_at")),
                            )
                            playlist_items_synced += 1
                            pos += 1

                        playlist_offset += 100
                        if playlist_offset >= int(tracks_payload.get("total", 0)):
                            break
                except Exception as exc:
                    playlists_item_sync_failed += 1
                    logger.warning(
                        "Playlist item sync failed for %s (%s): %s",
                        playlist.get("name") or playlist_id,
                        playlist_id,
                        exc,
                    )

            playlists_synced += 1

        offset += limit
        if offset >= int(payload.get("total", 0)):
            break

    return {
        "playlists_synced": playlists_synced,
        "playlist_items_synced": playlist_items_synced,
        "playlists_item_sync_failed": playlists_item_sync_failed,
        "playlists_item_sync_forbidden": playlists_item_sync_forbidden,
    }
