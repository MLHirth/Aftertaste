from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
import uuid

from core.db import Database
from core.rules import load_rules
from core.spotify_client import SpotifyClient
from core.spotify_ingest import upsert_track


@dataclass(slots=True)
class SessionState:
    track_id: str
    started_at: datetime
    duration_ms: int
    max_progress_ms: int
    source_type: str | None
    source_id: str | None
    device_id: str | None


class PlaybackPoller:
    def __init__(self, db: Database, client: SpotifyClient) -> None:
        self.db = db
        self.client = client
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._active: SessionState | None = None
        self._last_seen_device: datetime | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)
        self._thread = None
        with self._lock:
            if self._active:
                self._finalize_active(reason_hint="unknown")

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                payload = self.client.get_currently_playing() or {}
            except Exception:
                time.sleep(10)
                continue

            is_playing = bool(payload.get("is_playing"))
            device = payload.get("device") or {}
            if device.get("id"):
                self._last_seen_device = datetime.now(tz=timezone.utc)

            self._process_snapshot(payload)

            if (
                self._last_seen_device
                and (
                    datetime.now(tz=timezone.utc) - self._last_seen_device
                ).total_seconds()
                > 300
            ):
                time.sleep(20)
                continue

            time.sleep(self._next_interval_seconds(is_playing))

    def _next_interval_seconds(self, is_playing: bool) -> int:
        if not is_playing:
            return 20

        with self._lock:
            if not self._active:
                return 2
            if self._active.max_progress_ms < 30_000:
                return 2
        return 5

    def _process_snapshot(self, payload: dict[str, Any]) -> None:
        item = payload.get("item") or {}
        track_id = item.get("id")
        is_playing = bool(payload.get("is_playing"))
        progress_ms = int(payload.get("progress_ms") or 0)
        duration_ms = int(item.get("duration_ms") or 0)
        timestamp_ms = int(payload.get("timestamp") or int(time.time() * 1000))
        started_at = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)

        context = payload.get("context") or {}
        source_uri = context.get("uri")
        source_type = context.get("type")
        source_id = source_uri.split(":")[-1] if source_uri else None
        device_id = (payload.get("device") or {}).get("id")

        with self._lock:
            if self._active and (not track_id or self._active.track_id != track_id):
                self._finalize_active(reason_hint="track_changed")

            if not track_id:
                if self._active and not payload.get("is_playing"):
                    idle_seconds = (
                        datetime.now(tz=timezone.utc) - self._active.started_at
                    ).total_seconds()
                    if idle_seconds > 300:
                        self._finalize_active(reason_hint="pause_abandon")
                return

            upsert_track(self.db, item)

            if not self._active:
                self._active = SessionState(
                    track_id=track_id,
                    started_at=started_at,
                    duration_ms=duration_ms,
                    max_progress_ms=progress_ms,
                    source_type=source_type,
                    source_id=source_id,
                    device_id=device_id,
                )
            else:
                self._active.duration_ms = duration_ms
                self._active.device_id = device_id

                if is_playing:
                    self._active.max_progress_ms = max(
                        self._active.max_progress_ms, progress_ms
                    )
                elif progress_ms < self._active.max_progress_ms:
                    self._active.max_progress_ms = progress_ms

    def _finalize_active(self, reason_hint: str) -> None:
        if not self._active:
            return

        rules = load_rules(self.db)
        now = datetime.now(tz=timezone.utc)

        duration = max(1, self._active.duration_ms)
        listened = max(0, min(self._active.max_progress_ms, duration))
        ratio = listened / duration

        cutoff_ms = min(
            int(rules["early_skip_cutoff_seconds"] * 1000), int(duration * 0.20)
        )
        ended_reason = "unknown"
        if reason_hint == "pause_abandon":
            ended_reason = "pause_abandon"
        elif ratio >= float(rules["completion_ratio_threshold"]):
            ended_reason = "completed"
        elif listened < cutoff_ms:
            ended_reason = "skip_early"
        elif ratio < float(rules["mid_skip_ratio_threshold"]):
            ended_reason = "skip_mid"
        elif ratio < float(rules["completion_ratio_threshold"]):
            ended_reason = "skip_late"

        self.db.execute(
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
      )
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      """,
            (
                str(uuid.uuid4()),
                self._active.track_id,
                self._active.started_at.isoformat(),
                now.isoformat(),
                listened,
                ratio,
                self._active.source_type,
                self._active.source_id,
                self._active.device_id,
                ended_reason,
            ),
        )

        self._active = None
