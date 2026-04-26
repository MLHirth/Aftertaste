from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ScoreParts:
    total: float
    positive: float
    negative: float
    familiarity: float
    freshness: float
    revival: float
    exploration: float
    fatigue: float


@dataclass(slots=True)
class ScoredTrack:
    track_id: str
    name: str
    artists: str
    uri: str | None
    bucket: str
    score: ScoreParts
    explanation: str
