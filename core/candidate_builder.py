from __future__ import annotations

from collections import defaultdict

from core.db import Database


def rebuild_transition_edges(db: Database, lookback_days: int = 120) -> None:
    rows = db.query_all(
        """
    SELECT track_id, ended_reason
    FROM play_events
    WHERE started_at >= datetime('now', ?)
    ORDER BY started_at ASC
    """,
        (f"-{lookback_days} day",),
    )

    counts: dict[tuple[str, str], float] = defaultdict(float)
    for idx in range(len(rows) - 1):
        current = rows[idx]
        nxt = rows[idx + 1]
        src = current["track_id"]
        dst = nxt["track_id"]
        if not src or not dst or src == dst:
            continue

        if current["ended_reason"] == "completed":
            counts[(src, dst)] += 1.0
        elif current["ended_reason"] in {"skip_early", "skip_mid"}:
            counts[(src, dst)] -= 0.4

    db.execute("DELETE FROM track_edges WHERE edge_type = 'played_after'")
    for (src, dst), weight in counts.items():
        db.execute(
            """
      INSERT INTO track_edges(src_track_id, dst_track_id, edge_type, weight)
      VALUES (?, ?, 'played_after', ?)
      """,
            (src, dst, weight),
        )


def build_candidates(
    db: Database, limit: int = 700
) -> tuple[list[str], dict[str, set[str]]]:
    source_map: dict[str, set[str]] = defaultdict(set)

    def add_rows(rows: list[dict[str, object]], source: str) -> None:
        for row in rows:
            track_id = row.get("track_id")
            if isinstance(track_id, str):
                source_map[track_id].add(source)

    library_revival = db.query_all(
        """
    SELECT s.track_id
    FROM saved_tracks s
    LEFT JOIN (
      SELECT
        track_id,
        MAX(ended_at) AS last_played,
        AVG(completion_ratio) AS avg_ratio,
        SUM(CASE WHEN ended_reason = 'completed' THEN 1 ELSE 0 END) AS complete_count
      FROM play_events
      GROUP BY track_id
    ) h ON h.track_id = s.track_id
    WHERE (h.last_played IS NULL OR h.last_played < datetime('now', '-30 day'))
      AND (COALESCE(h.complete_count, 0) > 0 OR COALESCE(h.avg_ratio, 0) >= 0.6)
    LIMIT 300
    """
    )
    add_rows(library_revival, "library_revival")

    playlist_memory = db.query_all(
        """
    SELECT DISTINCT pt.track_id
    FROM playlist_tracks pt
    JOIN source_preferences sp ON sp.playlist_id = pt.playlist_id
    WHERE sp.include_source = 1
    LIMIT 400
    """
    )
    add_rows(playlist_memory, "playlist_memory")

    top_affinity = db.query_all(
        """
    SELECT track_id
    FROM top_track_affinity
    WHERE time_range IN ('short_term', 'medium_term', 'long_term')
    LIMIT 200
    """
    )
    add_rows(top_affinity, "top_affinity")

    continuation = db.query_all(
        """
    SELECT dst_track_id AS track_id
    FROM track_edges
    WHERE edge_type = 'played_after'
      AND weight > 0
    ORDER BY weight DESC
    LIMIT 200
    """
    )
    add_rows(continuation, "session_continuation")

    behavior_memory = db.query_all(
        """
    SELECT track_id
    FROM play_events
    GROUP BY track_id
    HAVING SUM(CASE WHEN ended_reason = 'completed' THEN 1 ELSE 0 END) > 0
       OR COUNT(*) >= 2
    ORDER BY MAX(started_at) DESC
    LIMIT 300
    """
    )
    add_rows(behavior_memory, "behavior_memory")

    context_memory = db.query_all(
        """
    SELECT DISTINCT track_id
    FROM play_events
    WHERE source_id IS NOT NULL
      AND started_at >= datetime('now', '-120 day')
    LIMIT 250
    """
    )
    add_rows(context_memory, "playback_source_history")

    exploration = db.query_all(
        """
    SELECT DISTINCT pt.track_id
    FROM playlist_tracks pt
    JOIN playlists p ON p.playlist_id = pt.playlist_id
    LEFT JOIN play_events pe ON pe.track_id = pt.track_id
    WHERE p.is_spotify_made_guess = 1
      AND pe.track_id IS NULL
    LIMIT 200
    """
    )
    add_rows(exploration, "spotify_made_exploration")

    candidates = list(source_map.keys())[:limit]
    return candidates, source_map
