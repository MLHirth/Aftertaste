from __future__ import annotations

import math
from datetime import datetime, timezone

from core.db import Database
from core.explain import explain_track
from core.models import ScoreParts, ScoredTrack
from core.rules import load_rules


def _to_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _days_since(timestamp: str | None) -> int | None:
    dt = _to_dt(timestamp)
    if dt is None:
        return None
    return (datetime.now(tz=timezone.utc) - dt).days


def _bucket_from_score(
    total: float, negative: float, revival: float, exploration: float
) -> str:
    if negative <= -8 or total <= -5:
        return "Avoided"
    if revival >= 2:
        return "Revived"
    if exploration >= 1.2:
        return "Explore"
    return "Safe"


def _late_skip_decay_penalty(
    rows: list[dict[str, object]],
    *,
    completion_threshold: float,
    mid_skip_threshold: float,
) -> float:
    spread = max(0.05, completion_threshold - mid_skip_threshold)
    total_penalty = 0.0

    for row in rows:
        ratio = float(row.get("completion_ratio") or 0)
        started_at = row.get("started_at")
        days_since = _days_since(str(started_at) if started_at else None) or 0

        # Near-complete skips should barely hurt; lower late skips hurt more.
        closeness = max(0.15, (completion_threshold - ratio) / spread)
        decay = math.exp(-days_since / 21.0)
        total_penalty += 0.8 * closeness * decay

    return min(2.0, total_penalty)


def score_candidates(
    db: Database,
    candidates: list[str],
    source_map: dict[str, set[str]],
) -> list[ScoredTrack]:
    rules = load_rules(db)
    completion_threshold = float(rules.get("completion_ratio_threshold", 0.85))
    mid_skip_threshold = float(rules.get("mid_skip_ratio_threshold", 0.75))

    skipped_source_rows = db.query_all(
        """
    SELECT source_id
    FROM play_events
    WHERE ended_reason = 'skip_early'
      AND source_id IS NOT NULL
      AND started_at >= datetime('now', '-21 day')
    GROUP BY source_id
    HAVING COUNT(*) >= 4
    """
    )
    penalized_sources = {
        row["source_id"] for row in skipped_source_rows if row.get("source_id")
    }

    scored: list[ScoredTrack] = []

    for track_id in candidates:
        track_row = db.query_one(
            """
      SELECT
        t.track_id,
        t.name,
        t.spotify_uri,
        GROUP_CONCAT(a.name, ', ') AS artists
      FROM tracks t
      LEFT JOIN track_artists ta ON ta.track_id = t.track_id
      LEFT JOIN artists a ON a.artist_id = ta.artist_id
      WHERE t.track_id = ?
      GROUP BY t.track_id
      """,
            (track_id,),
        )
        if not track_row:
            continue

        history = (
            db.query_one(
                """
      SELECT
        SUM(CASE WHEN ended_reason = 'completed' THEN 1 ELSE 0 END) AS completed_count,
        SUM(CASE WHEN ended_reason = 'skip_early' THEN 1 ELSE 0 END) AS early_skip_count,
        SUM(CASE WHEN ended_reason = 'skip_early' AND started_at >= datetime('now', '-7 day') THEN 1 ELSE 0 END) AS early_skip_count_7d,
        SUM(CASE WHEN ended_reason = 'skip_mid' THEN 1 ELSE 0 END) AS mid_skip_count,
        MAX(ended_at) AS last_played,
        AVG(completion_ratio) AS avg_ratio,
        SUM(CASE WHEN started_at >= datetime('now', '-3 day') THEN 1 ELSE 0 END) AS plays_3d,
        SUM(CASE WHEN started_at >= datetime('now', '-7 day') THEN 1 ELSE 0 END) AS plays_7d
      FROM play_events
      WHERE track_id = ?
      """,
                (track_id,),
            )
            or {}
        )

        replay_row = db.query_one(
            """
      SELECT COUNT(*) AS replay_days
      FROM (
        SELECT date(started_at) AS day
        FROM play_events
        WHERE track_id = ?
        GROUP BY date(started_at)
        HAVING COUNT(*) >= 2
      ) d
      """,
            (track_id,),
        ) or {"replay_days": 0}

        in_saved = db.query_one(
            "SELECT 1 AS ok FROM saved_tracks WHERE track_id = ?", (track_id,)
        )
        in_playlists = db.query_one(
            "SELECT COUNT(*) AS c FROM playlist_tracks WHERE track_id = ?",
            (track_id,),
        ) or {"c": 0}

        long_term_top = db.query_one(
            """
      SELECT 1 AS ok
      FROM top_track_affinity
      WHERE track_id = ?
        AND time_range = 'long_term'
      LIMIT 1
      """,
            (track_id,),
        )

        spotify_made_presence = db.query_one(
            """
      SELECT 1 AS ok
      FROM playlist_tracks pt
      JOIN playlists p ON p.playlist_id = pt.playlist_id
      WHERE pt.track_id = ?
        AND p.is_spotify_made_guess = 1
      LIMIT 1
      """,
            (track_id,),
        )

        connected_recent = db.query_one(
            """
      SELECT COUNT(*) AS c
      FROM track_edges te
      WHERE te.dst_track_id = ?
        AND te.edge_type = 'played_after'
        AND te.weight > 0
      """,
            (track_id,),
        ) or {"c": 0}

        artist_rows = db.query_all(
            "SELECT artist_id FROM track_artists WHERE track_id = ?",
            (track_id,),
        )
        artist_ids = [row["artist_id"] for row in artist_rows if row.get("artist_id")]

        artist_penalty = 0.0
        unseen_artist_bonus = 0.0
        if artist_ids:
            placeholders = ",".join("?" for _ in artist_ids)
            artist_skip_rows = db.query_all(
                f"""
        SELECT ta.artist_id, COUNT(DISTINCT pe.track_id) AS skipped_tracks
        FROM play_events pe
        JOIN track_artists ta ON ta.track_id = pe.track_id
        WHERE pe.ended_reason = 'skip_early'
          AND pe.started_at >= datetime('now', '-14 day')
          AND ta.artist_id IN ({placeholders})
        GROUP BY ta.artist_id
        """,
                tuple(artist_ids),
            )
            if any(int(row["skipped_tracks"]) >= 3 for row in artist_skip_rows):
                artist_penalty = -2.0

            if not history.get("last_played"):
                artist_positive_rows = db.query_all(
                    f"""
          SELECT ta.artist_id, COUNT(*) AS completed_tracks
          FROM play_events pe
          JOIN track_artists ta ON ta.track_id = pe.track_id
          WHERE pe.ended_reason = 'completed'
            AND ta.artist_id IN ({placeholders})
          GROUP BY ta.artist_id
          """,
                    tuple(artist_ids),
                )
                if any(
                    int(row["completed_tracks"]) >= 3 for row in artist_positive_rows
                ):
                    unseen_artist_bonus = 1.0

        source_penalty = 0.0
        if penalized_sources:
            placeholders = ",".join("?" for _ in penalized_sources)
            playlist_hit = db.query_one(
                f"""
        SELECT 1 AS hit
        FROM playlist_tracks
        WHERE track_id = ?
          AND playlist_id IN ({placeholders})
        LIMIT 1
        """,
                (track_id, *penalized_sources),
            )
            if playlist_hit:
                source_penalty = -1.5

        completed_count = float(history.get("completed_count") or 0)
        early_skip_count = float(history.get("early_skip_count") or 0)
        early_skip_count_7d = float(history.get("early_skip_count_7d") or 0)
        mid_skip_count = float(history.get("mid_skip_count") or 0)

        late_skip_rows = db.query_all(
            """
      SELECT started_at, completion_ratio
      FROM play_events
      WHERE track_id = ?
        AND ended_reason = 'skip_late'
        AND started_at >= datetime('now', '-120 day')
      """,
            (track_id,),
        )
        late_skip_penalty = _late_skip_decay_penalty(
            late_skip_rows,
            completion_threshold=completion_threshold,
            mid_skip_threshold=mid_skip_threshold,
        )

        positive = 0.0
        if completed_count >= 1:
            positive += 2.5
        if completed_count >= 2:
            positive += 1.5
        if float(replay_row.get("replay_days") or 0) >= 1:
            positive += 1.0
        if in_saved:
            positive += 3.0
        if long_term_top:
            positive += 1.5
        if spotify_made_presence:
            positive += 0.75

        negative = 0.0
        if early_skip_count >= 1:
            negative += 3.0
            if early_skip_count > 1:
                negative += (early_skip_count - 1) * 2.0
        if early_skip_count_7d >= 1:
            negative += 4.0
        if early_skip_count >= 6:
            negative = max(negative, 25.0)
        negative += mid_skip_count * 1.0
        negative += late_skip_penalty
        negative += abs(artist_penalty)
        negative += abs(source_penalty)

        familiarity = 0.0
        if in_saved:
            familiarity += 1.0
        if float(in_playlists.get("c") or 0) > 0:
            familiarity += 0.75
        if float(connected_recent.get("c") or 0) >= 2:
            familiarity += 1.25
        if "behavior_memory" in source_map.get(track_id, set()):
            familiarity += 0.5
        if "playback_source_history" in source_map.get(track_id, set()):
            familiarity += 0.5

        last_played_days = _days_since(history.get("last_played"))
        avg_ratio = float(history.get("avg_ratio") or 0)
        revival = 0.0
        if last_played_days is not None and last_played_days > 30:
            revival += 1.0
        if last_played_days is not None and last_played_days > 90:
            revival += 2.0
        if last_played_days is not None and last_played_days > 180 and avg_ratio >= 0.8:
            revival += 3.0

        exploration = 0.0
        exploration += unseen_artist_bonus
        if "spotify_made_exploration" in source_map.get(track_id, set()):
            exploration += 1.25
        if "explore_search" in source_map.get(track_id, set()):
            exploration += 0.5
        exploration = min(2.5, exploration)

        fatigue = 0.0
        plays_3d = float(history.get("plays_3d") or 0)
        plays_7d = float(history.get("plays_7d") or 0)
        if plays_3d > 0:
            fatigue += 2.5
        elif plays_7d > 0:
            fatigue += 1.0

        total = positive + familiarity + revival + exploration - negative - fatigue
        parts = ScoreParts(
            total=total,
            positive=positive,
            negative=-negative,
            familiarity=familiarity,
            freshness=-fatigue,
            revival=revival,
            exploration=exploration,
            fatigue=-fatigue,
        )

        bucket = _bucket_from_score(total, -negative, revival, exploration)
        explanation = explain_track(parts, source_map.get(track_id, set()))
        scored.append(
            ScoredTrack(
                track_id=track_id,
                name=track_row["name"],
                artists=track_row.get("artists") or "Unknown Artist",
                uri=track_row.get("spotify_uri"),
                bucket=bucket,
                score=parts,
                explanation=explanation,
            )
        )

    scored.sort(key=lambda item: item.score.total, reverse=True)
    return scored
