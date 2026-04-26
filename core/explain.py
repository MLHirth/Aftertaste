from __future__ import annotations

from core.models import ScoreParts


def explain_track(score: ScoreParts, sources: set[str]) -> str:
    reasons: list[str] = []

    if score.positive >= 2:
        reasons.append("strong completion/save history")
    if score.revival >= 2:
        reasons.append("revival candidate from older listens")
    if score.exploration >= 1:
        reasons.append("adjacent exploration signal")
    if score.negative <= -4:
        reasons.append("skip-heavy penalty applied")
    if score.fatigue <= -2:
        reasons.append("recent play fatigue")

    if "session_continuation" in sources:
        reasons.append("frequently follows recently completed songs")
    if "behavior_memory" in sources:
        reasons.append("learned from your direct listening history")
    if "playback_source_history" in sources:
        reasons.append("seen in your real playback source history")
    if "spotify_made_exploration" in sources:
        reasons.append("seen in your Spotify-made list memory")
    if "recent_artist_random" in sources:
        reasons.append("random popular pick from your recent artist rotation")

    if not reasons:
        reasons.append("balanced by your current rule set")

    return "; ".join(reasons)
