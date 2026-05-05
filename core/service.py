from __future__ import annotations

from collections import Counter, defaultdict
import contextvars
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import random
import re
import sqlite3
import threading
from typing import Any, Callable, TypeVar

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from core.auth_pkce import PKCEManager, TokenStore
from core.candidate_builder import build_candidates, rebuild_transition_edges
from core.cloud_sync import CloudSyncEngine
from core.cloud_sync_client import CloudSyncClient
from core.config import Settings
from core.db import Database
from core.history_reconciler import reconcile_recent_history
from core.library_sync import sync_saved_tracks, sync_top_items
from core.playback_poller import PlaybackPoller
from core.playlist_sync import sync_playlists
from core.queue_manager import top_up_queue
from core.rules import load_rules, update_rules
from core.scorer import score_candidates
from core.spotify_client import SpotifyClient
from core.spotify_ingest import upsert_track


T = TypeVar("T")


class InMemoryTokenStore:
    def __init__(self) -> None:
        self._refresh_token: str | None = None

    def load_refresh_token(self) -> str | None:
        return self._refresh_token

    def save_refresh_token(self, token: str) -> None:
        self._refresh_token = token

    def storage_mode(self) -> str:
        return "memory_only"

    def has_persisted_token(self) -> bool:
        return False

    def last_error(self) -> str | None:
        return None


class EncryptedTenantTokenStore:
    def __init__(self, db: Database, user_id: str, master_secret: str) -> None:
        self.db = db
        self.user_id = user_id
        self.master_secret = master_secret
        self._last_error: str | None = None

    def _derive_key(self, salt: bytes) -> bytes:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=390000,
        )
        material = f"{self.master_secret}|{self.user_id}".encode("utf-8")
        return kdf.derive(material)

    def _encrypt_token(self, token: str, salt: bytes, nonce: bytes) -> bytes:
        key = self._derive_key(salt)
        aead = AESGCM(key)
        return aead.encrypt(nonce, token.encode("utf-8"), self.user_id.encode("utf-8"))

    def _decrypt_token(self, ciphertext: bytes, salt: bytes, nonce: bytes) -> str:
        key = self._derive_key(salt)
        aead = AESGCM(key)
        plain = aead.decrypt(nonce, ciphertext, self.user_id.encode("utf-8"))
        return plain.decode("utf-8")

    def _set_error(self, message: str) -> None:
        self._last_error = message
        self.db.execute(
            "UPDATE cloud_spotify_tokens SET last_error = ? WHERE user_id = ?",
            (message, self.user_id),
        )

    def load_refresh_token(self) -> str | None:
        row = self.db.query_one(
            """
          SELECT token_ciphertext, token_salt, token_nonce
          FROM cloud_spotify_tokens
          WHERE user_id = ?
          """,
            (self.user_id,),
        )
        if row is None:
            self._last_error = None
            return None

        try:
            ciphertext = bytes(row.get("token_ciphertext") or b"")
            salt = bytes(row.get("token_salt") or b"")
            nonce = bytes(row.get("token_nonce") or b"")
            token = self._decrypt_token(ciphertext, salt, nonce)
            now = datetime.now(tz=timezone.utc).isoformat()
            self.db.execute(
                "UPDATE cloud_spotify_tokens SET last_loaded_at = ?, last_error = NULL WHERE user_id = ?",
                (now, self.user_id),
            )
            self._last_error = None
            return token
        except Exception as exc:
            self._set_error(f"decrypt failed: {exc}")
            return None

    def save_refresh_token(self, token: str) -> None:
        try:
            salt = os.urandom(16)
            nonce = os.urandom(12)
            ciphertext = self._encrypt_token(token, salt, nonce)
            now = datetime.now(tz=timezone.utc).isoformat()
            self.db.execute(
                """
              INSERT INTO cloud_spotify_tokens(
                user_id,
                token_ciphertext,
                token_salt,
                token_nonce,
                created_at,
                updated_at,
                last_loaded_at,
                last_error
              ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)
              ON CONFLICT(user_id)
              DO UPDATE SET
                token_ciphertext = excluded.token_ciphertext,
                token_salt = excluded.token_salt,
                token_nonce = excluded.token_nonce,
                updated_at = excluded.updated_at,
                last_error = NULL
              """,
                (self.user_id, ciphertext, salt, nonce, now, now),
            )
            self._last_error = None
        except Exception as exc:
            self._last_error = f"encrypt/save failed: {exc}"
            raise RuntimeError(
                "Failed to persist encrypted server Spotify token."
            ) from exc

    def storage_mode(self) -> str:
        return "encrypted_db"

    def has_persisted_token(self) -> bool:
        row = self.db.query_one(
            "SELECT 1 AS ok FROM cloud_spotify_tokens WHERE user_id = ? LIMIT 1",
            (self.user_id,),
        )
        return row is not None

    def last_error(self) -> str | None:
        if self._last_error:
            return self._last_error
        row = self.db.query_one(
            "SELECT last_error FROM cloud_spotify_tokens WHERE user_id = ?",
            (self.user_id,),
        )
        if row is None:
            return None
        error_value = row.get("last_error")
        return str(error_value) if error_value else None


class AftertasteService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._db_context: contextvars.ContextVar[Database | None] = (
            contextvars.ContextVar("aftertaste_db", default=None)
        )
        self._spotify_context: contextvars.ContextVar[SpotifyClient | None] = (
            contextvars.ContextVar("aftertaste_spotify", default=None)
        )
        self.db = Database(settings.db_path)
        self.migrations_dir = Path(__file__).resolve().parent.parent / "migrations"
        self.db.migrate(self.migrations_dir)

        self.token_store = TokenStore()
        self.spotify = SpotifyClient(settings=settings, token_store=self.token_store)
        self.pkce = PKCEManager()
        self.poller = PlaybackPoller(db=self.db, client=self.spotify)
        self.sync_engine = CloudSyncEngine(self.db)
        self.sync_engine.bootstrap_snapshot_if_empty()
        self.cloud_sync_client = CloudSyncClient(settings, self.sync_engine)
        self._last_recent_reconcile_at: datetime | None = None
        self._last_cloud_sync_at: datetime | None = None
        self._tenant_lock = threading.RLock()
        self._tenant_dbs: dict[str, Database] = {}
        self._tenant_engines: dict[str, CloudSyncEngine] = {}
        self._cloud_pkce = PKCEManager()
        self._cloud_pkce_owner: dict[str, str] = {}
        self._cloud_token_stores: dict[str, InMemoryTokenStore] = {}
        self._cloud_spotify_clients: dict[str, SpotifyClient] = {}
        self._cloud_pollers: dict[str, PlaybackPoller] = {}
        self._cloud_automation_state: dict[str, dict[str, Any]] = {}
        self._cloud_manual_run_threads: dict[str, threading.Thread] = {}
        self._cloud_run_locks: dict[str, threading.Lock] = {}
        self._cloud_auth_probe_cache: dict[str, dict[str, Any]] = {}
        self._automation_stop = threading.Event()
        self._automation_thread: threading.Thread | None = None

        if self.spotify.is_authorized():
            self.poller.start()

        self._hydrate_persisted_cloud_spotify_sessions()

        if self.settings.server_master_enabled:
            self._automation_thread = threading.Thread(
                target=self._automation_loop,
                name="aftertaste-server-master",
                daemon=True,
            )
            self._automation_thread.start()

    @property
    def db(self) -> Database:
        return self._db_context.get() or self._default_db

    @db.setter
    def db(self, value: Database) -> None:
        self._default_db = value

    @property
    def spotify(self) -> SpotifyClient:
        return self._spotify_context.get() or self._default_spotify

    @spotify.setter
    def spotify(self, value: SpotifyClient) -> None:
        self._default_spotify = value

    def require_configured(self) -> None:
        if not self.spotify.is_configured():
            raise RuntimeError(
                "SPOTIFY_CLIENT_ID is not configured. Add it to .env later."
            )

    def auth_status(self) -> dict[str, Any]:
        return {
            "has_client_id": self.spotify.is_configured(),
            "authorized": self.spotify.is_authorized(),
            "redirect_uri": self.settings.spotify_redirect_uri,
            "db_path": str(self.settings.db_path),
            "cloud_sync_enabled": self.cloud_sync_client.is_enabled(),
            "cloud_api_base_url": self.settings.cloud_api_base_url,
            "server_master_enabled": self.settings.server_master_enabled,
        }

    def start_auth(self) -> dict[str, str]:
        self.require_configured()
        return self.pkce.start(
            client_id=self.settings.spotify_client_id or "",
            redirect_uri=self.settings.spotify_redirect_uri,
        )

    def exchange_auth_code(
        self, session_id: str, state: str, code: str
    ) -> dict[str, Any]:
        verifier = self.pkce.consume(session_id=session_id, state=state)
        token_payload = self.spotify.exchange_code(code=code, code_verifier=verifier)
        self.poller.start()
        return {
            "authorized": True,
            "token_type": token_payload.get("token_type"),
            "scope": token_payload.get("scope"),
            "expires_in": token_payload.get("expires_in"),
        }

    def sync_library(self) -> dict[str, int]:
        self.require_configured()
        return sync_saved_tracks(self.db, self.spotify)

    def sync_playlists(self) -> dict[str, int]:
        self.require_configured()
        return sync_playlists(self.db, self.spotify)

    def sync_top_items(self) -> dict[str, int]:
        self.require_configured()
        return sync_top_items(self.db, self.spotify)

    def reconcile_recent(self) -> dict[str, int]:
        self.require_configured()
        return reconcile_recent_history(self.db, self.spotify)

    def sync_all(self) -> dict[str, int]:
        results: dict[str, int] = {}
        for block in [
            self.sync_library(),
            self.sync_playlists(),
            self.sync_top_items(),
            self.reconcile_recent(),
        ]:
            results.update(block)
        return results

    def sync_cloud_once(
        self, bearer_token_override: str | None = None
    ) -> dict[str, Any]:
        return self.cloud_sync_client.sync_once(
            remote_name="default",
            bearer_token_override=bearer_token_override,
        )

    def sync_cloud_status(
        self, bearer_token_override: str | None = None
    ) -> dict[str, Any]:
        return self.cloud_sync_client.remote_status(
            bearer_token_override=bearer_token_override,
        )

    def cloud_sync_engine_for_user(self, user_id: str) -> CloudSyncEngine:
        normalized_user = user_id.strip()
        if not normalized_user:
            raise RuntimeError("Cloud sync user id cannot be empty.")

        with self._tenant_lock:
            existing = self._tenant_engines.get(normalized_user)
            if existing is not None:
                return existing

            safe_user = re.sub(r"[^a-zA-Z0-9_.-]", "_", normalized_user)
            db_path = self.settings.cloud_tenant_db_dir / f"{safe_user}.db"
            tenant_db = Database(db_path)
            tenant_db.migrate(self.migrations_dir)

            engine = CloudSyncEngine(tenant_db)
            engine.bootstrap_snapshot_if_empty()
            self._tenant_dbs[normalized_user] = tenant_db
            self._tenant_engines[normalized_user] = engine
            return engine

    def _cloud_spotify_client_for_user(self, user_id: str) -> SpotifyClient:
        normalized_user = user_id.strip()
        if not normalized_user:
            raise RuntimeError("Cloud sync user id cannot be empty.")

        with self._tenant_lock:
            existing = self._cloud_spotify_clients.get(normalized_user)
            if existing is not None:
                return existing

            tenant_db = self.cloud_sync_engine_for_user(normalized_user).db
            if self.settings.server_token_encryption_key:
                token_store: InMemoryTokenStore | EncryptedTenantTokenStore = (
                    EncryptedTenantTokenStore(
                        db=tenant_db,
                        user_id=normalized_user,
                        master_secret=self.settings.server_token_encryption_key,
                    )
                )
            else:
                token_store = InMemoryTokenStore()
            client_settings = replace(
                self.settings,
                spotify_redirect_uri=(
                    self.settings.cloud_spotify_redirect_uri
                    or self.settings.spotify_redirect_uri
                ),
            )
            client = SpotifyClient(settings=client_settings, token_store=token_store)
            self._cloud_token_stores[normalized_user] = token_store
            self._cloud_spotify_clients[normalized_user] = client
            return client

    def _list_persisted_cloud_users(self) -> list[str]:
        users: set[str] = set()
        db_dir = self.settings.cloud_tenant_db_dir
        if not db_dir.exists():
            return []

        for db_path in db_dir.glob("*.db"):
            try:
                conn = sqlite3.connect(db_path)
                try:
                    row = conn.execute(
                        "SELECT 1 FROM cloud_spotify_tokens LIMIT 1"
                    ).fetchone()
                finally:
                    conn.close()
                if row:
                    users.add(db_path.stem)
            except Exception:
                continue

        return sorted(users)

    def _hydrate_persisted_cloud_spotify_sessions(self) -> None:
        if not self.settings.server_token_encryption_key:
            return

        for user_id in self._list_persisted_cloud_users():
            try:
                client = self._cloud_spotify_client_for_user(user_id)
                if client.is_authorized():
                    poller = self._cloud_poller_for_user(user_id)
                    poller.start()
            except Exception as exc:
                self._mark_cloud_automation_result(user_id, ok=False, error=str(exc))

    def _cloud_poller_for_user(self, user_id: str) -> PlaybackPoller:
        normalized_user = user_id.strip()
        if not normalized_user:
            raise RuntimeError("Cloud sync user id cannot be empty.")

        with self._tenant_lock:
            existing = self._cloud_pollers.get(normalized_user)
            if existing is not None:
                return existing

            tenant_db = self.cloud_sync_engine_for_user(normalized_user).db
            spotify = self._cloud_spotify_client_for_user(normalized_user)
            poller = PlaybackPoller(db=tenant_db, client=spotify)
            self._cloud_pollers[normalized_user] = poller
            return poller

    def _cloud_run_lock_for_user(self, user_id: str) -> threading.Lock:
        normalized_user = user_id.strip()
        if not normalized_user:
            raise RuntimeError("Cloud sync user id cannot be empty.")

        with self._tenant_lock:
            lock = self._cloud_run_locks.get(normalized_user)
            if lock is None:
                lock = threading.Lock()
                self._cloud_run_locks[normalized_user] = lock
            return lock

    def _run_cloud_context(self, user_id: str, operation: Callable[[], T]) -> T:
        tenant_db = self.cloud_sync_engine_for_user(user_id).db
        spotify = self._cloud_spotify_client_for_user(user_id)
        db_token = self._db_context.set(tenant_db)
        spotify_token = self._spotify_context.set(spotify)
        try:
            return operation()
        finally:
            self._spotify_context.reset(spotify_token)
            self._db_context.reset(db_token)

    def _mark_cloud_automation_result(
        self,
        user_id: str,
        *,
        ok: bool,
        error: str | None = None,
    ) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        state = self._cloud_automation_state.get(user_id) or {
            "run_count": 0,
            "last_run_at": None,
            "last_error": None,
            "last_ok": False,
        }
        state["run_count"] = int(state.get("run_count") or 0) + 1
        state["last_run_at"] = now
        state["last_ok"] = ok
        state["last_error"] = error
        self._cloud_automation_state[user_id] = state

    def cloud_spotify_status(self, user_id: str) -> dict[str, Any]:
        client = self._cloud_spotify_client_for_user(user_id)
        poller = self._cloud_poller_for_user(user_id)
        token_store = self._cloud_token_stores.get(user_id)
        manual_thread = self._cloud_manual_run_threads.get(user_id)
        automation_state = self._cloud_automation_state.get(user_id) or {}
        now = datetime.now(tz=timezone.utc)
        cached_probe = self._cloud_auth_probe_cache.get(user_id) or {}
        cached_at = cached_probe.get("checked_at")

        auth_error: str | None = None
        connected = bool(client.refresh_token)
        locally_authorized = client.is_authorized()
        live_probe_ok: bool | None = None
        live_probe_checked_at: str | None = None

        if isinstance(cached_at, datetime):
            live_probe_checked_at = cached_at.isoformat()
            live_probe_ok = bool(cached_probe.get("authorized") or False)
            auth_error = cached_probe.get("auth_error")

        probe_pending = self._update_cloud_auth_probe_state(
            user_id=user_id,
            client=client,
            now=now,
            cached_at=cached_at if isinstance(cached_at, datetime) else None,
        )

        if not locally_authorized:
            authorized = False
            live_probe_ok = False
        else:
            authorized = live_probe_ok if live_probe_ok is not None else locally_authorized
        expires_at = (
            client.access_token_expires_at.isoformat()
            if client.access_token_expires_at
            else None
        )
        return {
            "connected": connected,
            "authorized": authorized,
            "has_refresh_token": connected,
            "spotify_refresh_token_present": connected,
            "spotify_live_probe_ok": live_probe_ok,
            "spotify_live_probe_checked_at": live_probe_checked_at,
            "spotify_live_probe_pending": probe_pending,
            "access_token_expires_at": expires_at,
            "auth_error": auth_error,
            "token_storage_mode": (
                token_store.storage_mode() if token_store is not None else "memory_only"
            ),
            "token_persisted": (
                bool(token_store.has_persisted_token())
                if token_store is not None
                else False
            ),
            "token_store_error": (
                token_store.last_error() if token_store is not None else None
            ),
            "poller_running": poller.running,
            "server_master_enabled": self.settings.server_master_enabled,
            "server_master_interval_seconds": self.settings.server_master_interval_seconds,
            "automation_thread_running": bool(
                self._automation_thread and self._automation_thread.is_alive()
            ),
            "automation_run_count": int(automation_state.get("run_count") or 0),
            "automation_last_run_at": automation_state.get("last_run_at"),
            "automation_last_ok": bool(automation_state.get("last_ok") or False),
            "automation_last_error": automation_state.get("last_error"),
            "manual_run_active": bool(manual_thread and manual_thread.is_alive()),
        }

    def _update_cloud_auth_probe_state(
        self,
        *,
        user_id: str,
        client: SpotifyClient,
        now: datetime,
        cached_at: datetime | None,
    ) -> bool:
        if not client.is_authorized():
            self._cloud_auth_probe_cache[user_id] = {
                "checked_at": now,
                "authorized": False,
                "auth_error": None,
            }
            return False

        if cached_at is not None:
            return False

        self._cloud_auth_probe_cache[user_id] = {
            "checked_at": now,
            "authorized": True,
            "auth_error": None,
        }
        return False

    def cloud_spotify_now_playing(self, user_id: str) -> dict[str, Any] | None:
        client = self._cloud_spotify_client_for_user(user_id)
        if not client.is_authorized():
            return None
        try:
            payload = client.get_currently_playing() or {}
            item = payload.get("item") or {}
            if not item.get("id"):
                return None
            return {
                "track_id": item.get("id"),
                "name": item.get("name"),
                "artists": ", ".join(
                    artist.get("name", "") for artist in item.get("artists") or []
                ),
                "is_playing": bool(payload.get("is_playing")),
                "progress_ms": payload.get("progress_ms") or 0,
                "duration_ms": item.get("duration_ms") or 0,
            }
        except Exception:
            return None

    def _dashboard_stats_only(self) -> dict[str, Any]:
        likely_skips = self.db.query_one(
            """
      SELECT COUNT(*) AS c
      FROM play_events
      WHERE ended_reason = 'skip_early'
        AND date(started_at) = date('now')
      """
        )
        completions = self.db.query_one(
            """
      SELECT COUNT(*) AS c
      FROM play_events
      WHERE ended_reason = 'completed'
        AND date(started_at) = date('now')
      """
        )

        top_negative_artists = self.db.query_all(
            """
      SELECT a.artist_id, a.name, COUNT(*) AS skip_count
      FROM play_events pe
      JOIN track_artists ta ON ta.track_id = pe.track_id
      JOIN artists a ON a.artist_id = ta.artist_id
      WHERE pe.ended_reason = 'skip_early'
        AND pe.started_at >= datetime('now', '-7 day')
      GROUP BY a.artist_id, a.name
      ORDER BY skip_count DESC
      LIMIT 5
      """
        )

        top_revived_tracks = self.db.query_all(
            """
      SELECT t.track_id, t.name, s.score_revival
      FROM track_scores s
      JOIN tracks t ON t.track_id = s.track_id
      WHERE s.score_revival > 1.5
      ORDER BY s.score_revival DESC, s.score_total DESC
      LIMIT 5
      """
        )

        next_refresh = datetime.now(tz=timezone.utc).replace(
            hour=8, minute=0, second=0, microsecond=0
        ) + timedelta(days=1)

        return {
            "likely_skip_count_today": int((likely_skips or {"c": 0})["c"]),
            "completions_today": int((completions or {"c": 0})["c"]),
            "top_negative_artists": top_negative_artists,
            "top_revived_tracks": top_revived_tracks,
            "next_playlist_refresh_time": next_refresh.isoformat(),
        }

    def cloud_dashboard(self, user_id: str) -> dict[str, Any]:
        stats = self._run_cloud_context(user_id, self._dashboard_stats_only)
        stats["poller_running"] = self._cloud_poller_for_user(user_id).running
        stats["now_playing"] = self.cloud_spotify_now_playing(user_id)
        stats["cloud_sync_enabled"] = True
        return stats

    def cloud_memory_negative_artists(self, user_id: str) -> list[dict[str, Any]]:
        return self._run_cloud_context(user_id, self.memory_negative_artists)

    def cloud_memory_tracks(
        self, user_id: str, limit: int = 120
    ) -> list[dict[str, Any]]:
        return self._run_cloud_context(user_id, lambda: self.memory_tracks(limit=limit))

    def cloud_get_today_mix(
        self, user_id: str, limit: int = 40
    ) -> list[dict[str, Any]]:
        return self._run_cloud_context(user_id, lambda: self.get_today_mix(limit=limit))

    def cloud_generate_today_mix(
        self, user_id: str, write_to_spotify: bool = False
    ) -> dict[str, Any]:
        return self._run_cloud_context(
            user_id,
            lambda: self.generate_today_mix(write_to_spotify=write_to_spotify),
        )

    def cloud_generate_vibe_revival(
        self, user_id: str, write_to_spotify: bool = False
    ) -> dict[str, Any]:
        return self._run_cloud_context(
            user_id,
            lambda: self.generate_vibe_revival(write_to_spotify=write_to_spotify),
        )

    def cloud_sync_all(self, user_id: str) -> dict[str, int]:
        return self._run_cloud_context(user_id, self.sync_all)

    def cloud_get_rules(self, user_id: str) -> dict[str, float]:
        return self._run_cloud_context(user_id, self.get_rules)

    def cloud_save_rules(
        self, user_id: str, updates: dict[str, float]
    ) -> dict[str, float]:
        return self._run_cloud_context(user_id, lambda: self.save_rules(updates))

    def cloud_list_sources(self, user_id: str) -> list[dict[str, Any]]:
        return self._run_cloud_context(user_id, self.list_sources)

    def cloud_update_source(
        self,
        user_id: str,
        playlist_id: str,
        include_source: bool,
        manually_confirmed: bool,
    ) -> None:
        self._run_cloud_context(
            user_id,
            lambda: self.update_source(
                playlist_id=playlist_id,
                include_source=include_source,
                manually_confirmed=manually_confirmed,
            ),
        )

    def cloud_top_up_live_queue(self, user_id: str, count: int = 3) -> dict[str, int]:
        return self._run_cloud_context(
            user_id, lambda: self.top_up_live_queue(count=count)
        )

    def cloud_start_poller(self, user_id: str) -> dict[str, bool]:
        poller = self._cloud_poller_for_user(user_id)
        poller.start()
        return {"running": poller.running}

    def cloud_stop_poller(self, user_id: str) -> dict[str, bool]:
        poller = self._cloud_poller_for_user(user_id)
        poller.stop()
        return {"running": poller.running}

    def cloud_spotify_start_auth(self, user_id: str) -> dict[str, str]:
        if not self.settings.spotify_client_id:
            raise RuntimeError("SPOTIFY_CLIENT_ID is not configured.")
        redirect_uri = self.settings.cloud_spotify_redirect_uri
        if not redirect_uri:
            raise RuntimeError(
                "AFTERTASTE_CLOUD_SPOTIFY_REDIRECT_URI is not configured."
            )

        session = self._cloud_pkce.start(
            client_id=self.settings.spotify_client_id,
            redirect_uri=redirect_uri,
        )
        session_id = session.get("session_id")
        if session_id:
            self._cloud_pkce_owner[session_id] = user_id
        return session

    def cloud_spotify_exchange_auth(
        self,
        user_id: str,
        *,
        session_id: str,
        state: str,
        code: str,
    ) -> dict[str, Any]:
        owner = self._cloud_pkce_owner.get(session_id)
        if owner != user_id:
            raise RuntimeError(
                "Cloud Spotify auth session does not belong to this user."
            )

        verifier = self._cloud_pkce.consume(session_id=session_id, state=state)
        self._cloud_pkce_owner.pop(session_id, None)

        client = self._cloud_spotify_client_for_user(user_id)
        token_payload = client.exchange_code(code=code, code_verifier=verifier)
        self._cloud_auth_probe_cache.pop(user_id, None)
        poller = self._cloud_poller_for_user(user_id)
        poller.start()
        return {
            "authorized": True,
            "token_type": token_payload.get("token_type"),
            "scope": token_payload.get("scope"),
            "expires_in": token_payload.get("expires_in"),
            "server_master_enabled": self.settings.server_master_enabled,
        }

    def run_cloud_master_once(self, user_id: str) -> dict[str, Any]:
        run_lock = self._cloud_run_lock_for_user(user_id)
        if not run_lock.acquire(blocking=False):
            self._mark_cloud_automation_result(
                user_id,
                ok=False,
                error="Cloud Spotify automation is already running.",
            )
            raise RuntimeError("Cloud Spotify automation is already running.")

        try:
            return self._run_cloud_master_once_locked(user_id)
        finally:
            run_lock.release()

    def _run_cloud_master_once_locked(self, user_id: str) -> dict[str, Any]:
        spotify = self._cloud_spotify_client_for_user(user_id)
        if not spotify.is_authorized():
            self._mark_cloud_automation_result(
                user_id,
                ok=False,
                error="Cloud Spotify is not connected.",
            )
            raise RuntimeError(
                "Cloud Spotify is not connected for this account. Connect Spotify in web mode first."
            )

        tenant_engine = self.cloud_sync_engine_for_user(user_id)
        tenant_db = tenant_engine.db
        poller = self._cloud_poller_for_user(user_id)
        if not poller.running:
            poller.start()

        try:
            sync_summary: dict[str, int] = {}
            sync_summary.update(sync_saved_tracks(tenant_db, spotify))
            sync_summary.update(sync_playlists(tenant_db, spotify))
            sync_summary.update(sync_top_items(tenant_db, spotify))
            sync_summary.update(reconcile_recent_history(tenant_db, spotify))

            generated = self._run_cloud_context(
                user_id,
                lambda: self.generate_today_mix(write_to_spotify=True),
            )

            self._mark_cloud_automation_result(user_id, ok=True)
        except Exception as exc:
            self._mark_cloud_automation_result(user_id, ok=False, error=str(exc))
            raise

        return {
            "ok": True,
            "user_id": user_id,
            "sync": sync_summary,
            "generated": {
                "candidate_count": int(generated.get("candidate_count") or 0),
                "selected_count": int(generated.get("selected_count") or 0),
            },
            "playlists": generated.get("playlists") or {},
        }

    def trigger_cloud_master_now(self, user_id: str) -> dict[str, Any]:
        with self._tenant_lock:
            run_lock = self._cloud_run_lock_for_user(user_id)
            if run_lock.locked():
                return {
                    "ok": True,
                    "started": False,
                    "running": True,
                    "message": "Automation run already in progress.",
                }

            existing = self._cloud_manual_run_threads.get(user_id)
            if existing is not None and existing.is_alive():
                return {
                    "ok": True,
                    "started": False,
                    "running": True,
                    "message": "Automation run already in progress.",
                }

            def _runner() -> None:
                try:
                    self.run_cloud_master_once(user_id)
                except Exception as exc:
                    self._mark_cloud_automation_result(
                        user_id, ok=False, error=str(exc)
                    )

            thread = threading.Thread(
                target=_runner,
                name=f"aftertaste-cloud-run-{user_id}",
                daemon=True,
            )
            self._cloud_manual_run_threads[user_id] = thread
            thread.start()

        return {
            "ok": True,
            "started": True,
            "running": True,
            "message": "Automation run started in background.",
        }

    def _automation_loop(self) -> None:
        interval = max(30, self.settings.server_master_interval_seconds)
        while not self._automation_stop.wait(interval):
            user_ids = list(self._cloud_spotify_clients.keys())
            for user_id in user_ids:
                try:
                    poller = self._cloud_poller_for_user(user_id)
                    if not poller.running:
                        poller.start()
                    self.run_cloud_master_once(user_id)
                except Exception as exc:
                    self._mark_cloud_automation_result(
                        user_id,
                        ok=False,
                        error=str(exc),
                    )
                    continue

    def _maybe_sync_cloud(self) -> None:
        if not self.cloud_sync_client.is_enabled():
            return
        if not (self.settings.cloud_bearer_token or "").strip():
            return

        now = datetime.now(tz=timezone.utc)
        if (
            self._last_cloud_sync_at
            and (now - self._last_cloud_sync_at).total_seconds()
            < self.settings.cloud_sync_poll_seconds
        ):
            return

        try:
            self.cloud_sync_client.sync_once(remote_name="default")
            self._last_cloud_sync_at = now
        except Exception:
            return

    def _maybe_reconcile_recent_history(self, every_seconds: int = 120) -> None:
        if not self.spotify.is_authorized():
            return

        now = datetime.now(tz=timezone.utc)
        if (
            self._last_recent_reconcile_at
            and (now - self._last_recent_reconcile_at).total_seconds() < every_seconds
        ):
            return

        try:
            reconcile_recent_history(self.db, self.spotify)
            self._last_recent_reconcile_at = now
        except Exception:
            return

    def _persist_scores(self, scored: list[Any]) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        for item in scored:
            self.db.execute(
                """
        INSERT INTO track_scores(
          track_id,
          score_total,
          score_positive,
          score_negative,
          score_familiarity,
          score_freshness,
          score_revival,
          score_exploration,
          computed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(track_id)
        DO UPDATE SET
          score_total = excluded.score_total,
          score_positive = excluded.score_positive,
          score_negative = excluded.score_negative,
          score_familiarity = excluded.score_familiarity,
          score_freshness = excluded.score_freshness,
          score_revival = excluded.score_revival,
          score_exploration = excluded.score_exploration,
          computed_at = excluded.computed_at
        """,
                (
                    item.track_id,
                    item.score.total,
                    item.score.positive,
                    item.score.negative,
                    item.score.familiarity,
                    item.score.freshness,
                    item.score.revival,
                    item.score.exploration,
                    now,
                ),
            )

    def _inject_recent_artist_random_candidates(
        self,
        candidates: list[str],
        source_map: dict[str, set[str]],
        rules: dict[str, float],
    ) -> None:
        if int(rules.get("recent_artist_random_enabled", 0)) != 1:
            return
        if not self.spotify.is_authorized():
            return

        random_slots = max(0, int(rules.get("recent_artist_random_slots", 0)))
        if random_slots == 0:
            return

        lookback_days = max(7, int(rules.get("recent_artist_random_days", 14)))
        recent_artists = self.db.query_all(
            """
      SELECT
        a.artist_id,
        a.name,
        COUNT(*) AS play_count,
        MAX(pe.started_at) AS last_played
      FROM play_events pe
      JOIN track_artists ta ON ta.track_id = pe.track_id
      JOIN artists a ON a.artist_id = ta.artist_id
      WHERE pe.started_at >= datetime('now', ?)
      GROUP BY a.artist_id, a.name
      ORDER BY last_played DESC, play_count DESC
      LIMIT ?
      """,
            (f"-{lookback_days} day", max(8, random_slots * 4)),
        )
        if not recent_artists:
            return

        existing = set(candidates)
        max_candidates = max(6, random_slots * 3)
        added = 0

        for artist in recent_artists:
            artist_id = artist.get("artist_id")
            artist_name = str(artist.get("name") or "").strip()
            if not artist_id or not artist_name:
                continue

            try:
                search_payload = self.spotify.search_tracks(
                    query=artist_name,
                    limit=25,
                )
            except Exception:
                continue

            track_items = (search_payload.get("tracks") or {}).get("items") or []
            filtered = [
                track
                for track in track_items
                if any(
                    (artist_ref.get("id") == artist_id)
                    for artist_ref in (track.get("artists") or [])
                )
                and track.get("id")
            ]
            if not filtered:
                continue

            filtered.sort(
                key=lambda track: int(track.get("popularity") or 0), reverse=True
            )
            top_pool = filtered[:5]
            random.shuffle(top_pool)

            for track in top_pool:
                track_id = str(track.get("id"))
                if track_id in existing:
                    continue

                upsert_track(self.db, track)
                candidates.append(track_id)
                existing.add(track_id)
                source_map[track_id].add("recent_artist_random")
                added += 1
                if added >= max_candidates:
                    return

    def _parse_artist_genres(self, genres_json: str | None) -> list[str]:
        if not genres_json:
            return []
        try:
            raw = json.loads(genres_json)
        except json.JSONDecodeError:
            return []
        if not isinstance(raw, list):
            return []
        return [str(value).strip().lower() for value in raw if str(value).strip()]

    def _build_recent_vibe_profile(
        self,
        lookback_days: int,
    ) -> tuple[dict[str, float], dict[str, float]]:
        rows = self.db.query_all(
            """
      SELECT
        ta.artist_id,
        a.genres_json,
        COUNT(*) AS complete_count
      FROM play_events pe
      JOIN track_artists ta ON ta.track_id = pe.track_id
      JOIN artists a ON a.artist_id = ta.artist_id
      WHERE pe.ended_reason = 'completed'
        AND pe.started_at >= datetime('now', ?)
      GROUP BY ta.artist_id, a.genres_json
      ORDER BY complete_count DESC
      LIMIT 80
      """,
            (f"-{lookback_days} day",),
        )

        if not rows:
            rows = self.db.query_all(
                """
      SELECT
        ta.artist_id,
        a.genres_json,
        COUNT(*) AS complete_count
      FROM play_events pe
      JOIN track_artists ta ON ta.track_id = pe.track_id
      JOIN artists a ON a.artist_id = ta.artist_id
      WHERE pe.ended_reason = 'completed'
      GROUP BY ta.artist_id, a.genres_json
      ORDER BY complete_count DESC
      LIMIT 80
      """
            )

        if not rows:
            rows = self.db.query_all(
                """
      SELECT
        a.artist_id,
        a.genres_json,
        CAST((55 - MIN(ta.rank)) AS REAL) AS complete_count
      FROM top_artist_affinity ta
      JOIN artists a ON a.artist_id = ta.artist_id
      WHERE ta.time_range IN ('short_term', 'medium_term')
      GROUP BY a.artist_id, a.genres_json
      ORDER BY complete_count DESC
      LIMIT 80
      """
            )

        artist_weights: dict[str, float] = {}
        genre_weights: dict[str, float] = defaultdict(float)
        for row in rows:
            artist_id = row.get("artist_id")
            complete_count = float(row.get("complete_count") or 0)
            if not artist_id or complete_count <= 0:
                continue
            artist_weights[str(artist_id)] = min(4.5, 1.0 + math.log1p(complete_count))

            genres = self._parse_artist_genres(row.get("genres_json"))[:3]
            for genre in genres:
                genre_weights[genre] += min(2.5, 0.8 + math.log1p(complete_count))

        top_affinity_rows = self.db.query_all(
            """
      SELECT artist_id, MIN(rank) AS best_rank
      FROM top_artist_affinity
      WHERE time_range IN ('short_term', 'medium_term', 'long_term')
      GROUP BY artist_id
      ORDER BY best_rank ASC
      LIMIT 80
      """
        )
        for row in top_affinity_rows:
            artist_id = row.get("artist_id")
            best_rank = int(row.get("best_rank") or 999)
            if not artist_id:
                continue
            affinity_boost = max(0.3, min(2.0, (55 - best_rank) / 35))
            artist_weights[str(artist_id)] = max(
                affinity_boost,
                artist_weights.get(str(artist_id), 0.0),
            )

        return artist_weights, dict(genre_weights)

    def _build_vibe_revival_mix(self, target: int = 40) -> list[dict[str, Any]]:
        rules = load_rules(self.db)
        lookback_days = max(7, int(rules.get("recent_artist_random_days", 14)))
        min_old_days = max(14, int(rules.get("revival_days_30", 30)) - 10)

        artist_weights, genre_weights = self._build_recent_vibe_profile(lookback_days)
        if not artist_weights:
            return []

        blocked_rows = self.db.query_all(
            """
      SELECT DISTINCT track_id
      FROM play_events
      WHERE ended_reason = 'skip_early'
        AND started_at >= datetime('now', '-21 day')
      """
        )
        blocked_track_ids = {
            str(row["track_id"]) for row in blocked_rows if row.get("track_id")
        }

        candidates = self.db.query_all(
            """
      SELECT
        t.track_id,
        t.name,
        t.spotify_uri,
        MAX(pe.ended_at) AS last_played,
        SUM(CASE WHEN pe.ended_reason = 'completed' THEN 1 ELSE 0 END) AS completed_count,
        SUM(CASE WHEN pe.ended_reason = 'skip_early' THEN 1 ELSE 0 END) AS early_skip_count
      FROM tracks t
      LEFT JOIN play_events pe ON pe.track_id = t.track_id
      GROUP BY t.track_id, t.name, t.spotify_uri
      HAVING (last_played IS NULL OR last_played < datetime('now', ?))
        AND (
          completed_count > 0
          OR EXISTS (SELECT 1 FROM saved_tracks s WHERE s.track_id = t.track_id)
        )
      LIMIT 1500
      """,
            (f"-{min_old_days} day",),
        )

        scored: list[dict[str, Any]] = []
        for row in candidates:
            track_id = str(row.get("track_id") or "")
            if not track_id or track_id in blocked_track_ids:
                continue

            artist_rows = self.db.query_all(
                """
        SELECT a.artist_id, a.name, a.genres_json
        FROM track_artists ta
        JOIN artists a ON a.artist_id = ta.artist_id
        WHERE ta.track_id = ?
        """,
                (track_id,),
            )
            if not artist_rows:
                continue

            artist_names = list(
                dict.fromkeys(
                    str(artist.get("name") or "Unknown Artist")
                    for artist in artist_rows
                )
            )
            artist_ids = [
                str(artist.get("artist_id"))
                for artist in artist_rows
                if artist.get("artist_id")
            ]

            matched_weights = sorted(
                [artist_weights.get(artist_id, 0.0) for artist_id in set(artist_ids)],
                reverse=True,
            )
            vibe_artist = 0.0
            if matched_weights:
                vibe_artist = matched_weights[0]
                if len(matched_weights) > 1:
                    vibe_artist += matched_weights[1] * 0.35
            vibe_artist = min(4.0, vibe_artist)

            track_genres: set[str] = set()
            for artist in artist_rows:
                track_genres.update(
                    self._parse_artist_genres(artist.get("genres_json"))
                )

            vibe_genre = min(
                3.0,
                sum(
                    min(1.0, genre_weights.get(genre, 0) / 3.0)
                    for genre in track_genres
                    if genre in genre_weights
                ),
            )

            if vibe_artist <= 0 and vibe_genre <= 0:
                continue

            last_played = row.get("last_played")
            days_old = 999
            if isinstance(last_played, str):
                try:
                    last_played_dt = datetime.fromisoformat(
                        last_played.replace("Z", "+00:00")
                    ).astimezone(timezone.utc)
                    days_old = (datetime.now(tz=timezone.utc) - last_played_dt).days
                except ValueError:
                    days_old = 999

            revival = 1.0
            if days_old > int(rules.get("revival_days_90", 90)):
                revival += 1.2
            if days_old > int(rules.get("revival_days_180", 180)):
                revival += 1.8

            completed_count = float(row.get("completed_count") or 0)
            early_skip_count = float(row.get("early_skip_count") or 0)

            history_bonus = min(2.0, completed_count * 0.5)
            penalty = early_skip_count * 2.5

            total = vibe_artist + vibe_genre + revival + history_bonus - penalty
            if total <= 0:
                continue

            explanation_bits = [
                "revival fit for your recent listening vibe",
                f"artist match {vibe_artist:.1f}",
            ]
            if vibe_genre > 0:
                explanation_bits.append(f"genre match {vibe_genre:.1f}")
            explanation_bits.append(f"last played ~{max(0, days_old)}d ago")

            scored.append(
                {
                    "track_id": track_id,
                    "name": str(row.get("name") or "Unknown Track"),
                    "artists": ", ".join(artist_names),
                    "uri": row.get("spotify_uri"),
                    "bucket": "Revived",
                    "score_total": total,
                    "score_positive": vibe_artist + vibe_genre + history_bonus,
                    "score_negative": -penalty,
                    "score_familiarity": vibe_artist,
                    "score_freshness": 0.0,
                    "score_revival": revival,
                    "score_exploration": 0.0,
                    "explanation": "; ".join(explanation_bits),
                }
            )

        if len(scored) < target and self.spotify.is_authorized():
            recent_artist_rows = self.db.query_all(
                """
        SELECT ta.artist_id, a.name, COUNT(*) AS complete_count
        FROM play_events pe
        JOIN track_artists ta ON ta.track_id = pe.track_id
        JOIN artists a ON a.artist_id = ta.artist_id
        WHERE pe.ended_reason = 'completed'
          AND pe.started_at >= datetime('now', ?)
        GROUP BY ta.artist_id, a.name
        ORDER BY complete_count DESC
        LIMIT 20
        """,
                (f"-{lookback_days} day",),
            )

            if not recent_artist_rows:
                recent_artist_rows = self.db.query_all(
                    """
        SELECT a.artist_id, a.name, (55 - MIN(ta.rank)) AS complete_count
        FROM top_artist_affinity ta
        JOIN artists a ON a.artist_id = ta.artist_id
        WHERE ta.time_range IN ('short_term', 'medium_term')
        GROUP BY a.artist_id, a.name
        ORDER BY complete_count DESC
        LIMIT 20
        """
                )

            existing_ids = {str(item["track_id"]) for item in scored}
            for artist in recent_artist_rows:
                artist_id = artist.get("artist_id")
                artist_name = str(artist.get("name") or "").strip()
                if not artist_id or not artist_name:
                    continue

                try:
                    payload = self.spotify.search_tracks(
                        query=artist_name,
                        limit=25,
                    )
                except Exception:
                    continue

                for track in (payload.get("tracks") or {}).get("items") or []:
                    track_id = str(track.get("id") or "")
                    if not track_id or track_id in existing_ids:
                        continue
                    if track_id in blocked_track_ids:
                        continue
                    if not any(
                        ref.get("id") == artist_id
                        for ref in (track.get("artists") or [])
                    ):
                        continue

                    history = (
                        self.db.query_one(
                            """
            SELECT
              MAX(ended_at) AS last_played,
              SUM(CASE WHEN ended_reason = 'skip_early' THEN 1 ELSE 0 END) AS early_skip_count,
              SUM(CASE WHEN ended_reason = 'completed' THEN 1 ELSE 0 END) AS completed_count
            FROM play_events
            WHERE track_id = ?
            """,
                            (track_id,),
                        )
                        or {}
                    )

                    last_played = history.get("last_played")
                    days_old = 999
                    if isinstance(last_played, str):
                        try:
                            last_played_dt = datetime.fromisoformat(
                                last_played.replace("Z", "+00:00")
                            ).astimezone(timezone.utc)
                            days_old = (
                                datetime.now(tz=timezone.utc) - last_played_dt
                            ).days
                        except ValueError:
                            days_old = 999
                    if days_old < min_old_days:
                        continue

                    early_skip_count = float(history.get("early_skip_count") or 0)
                    if early_skip_count >= 2:
                        continue

                    upsert_track(self.db, track)
                    artist_names = ", ".join(
                        str(ref.get("name") or "Unknown Artist")
                        for ref in (track.get("artists") or [])
                    )
                    popularity = float(track.get("popularity") or 0)
                    popularity_bonus = min(1.5, popularity / 100)

                    total = 2.8 + 1.2 + popularity_bonus - (early_skip_count * 2.0)
                    scored.append(
                        {
                            "track_id": track_id,
                            "name": str(track.get("name") or "Unknown Track"),
                            "artists": artist_names,
                            "uri": track.get("uri"),
                            "bucket": "Revived",
                            "score_total": total,
                            "score_positive": 2.8 + popularity_bonus,
                            "score_negative": -(early_skip_count * 2.0),
                            "score_familiarity": 2.8,
                            "score_freshness": 0.0,
                            "score_revival": 1.2,
                            "score_exploration": 0.0,
                            "explanation": (
                                "revival fit via recent favorite artist; "
                                "popular catalog pick not heard recently"
                            ),
                        }
                    )
                    existing_ids.add(track_id)
                    if len(scored) >= target * 3:
                        break

                if len(scored) >= target * 3:
                    break

        if len(scored) < target:
            existing_ids = {str(item["track_id"]) for item in scored}
            affinity_rows = self.db.query_all(
                """
        SELECT
          t.track_id,
          t.name,
          t.spotify_uri,
          MIN(ta.rank) AS best_rank,
          MAX(pe.ended_at) AS last_played,
          SUM(CASE WHEN pe.ended_reason = 'skip_early' THEN 1 ELSE 0 END) AS early_skip_count,
          GROUP_CONCAT(DISTINCT a.name) AS artists
        FROM top_track_affinity ta
        JOIN tracks t ON t.track_id = ta.track_id
        LEFT JOIN play_events pe ON pe.track_id = t.track_id
        LEFT JOIN track_artists tar ON tar.track_id = t.track_id
        LEFT JOIN artists a ON a.artist_id = tar.artist_id
        WHERE ta.time_range IN ('short_term', 'medium_term', 'long_term')
        GROUP BY t.track_id, t.name, t.spotify_uri
        ORDER BY best_rank ASC
        LIMIT 300
        """
            )

            for row in affinity_rows:
                track_id = str(row.get("track_id") or "")
                if not track_id or track_id in existing_ids:
                    continue
                if track_id in blocked_track_ids:
                    continue

                early_skip_count = float(row.get("early_skip_count") or 0)
                if early_skip_count >= 2:
                    continue

                last_played = row.get("last_played")
                days_old = 999
                if isinstance(last_played, str):
                    try:
                        dt = datetime.fromisoformat(
                            last_played.replace("Z", "+00:00")
                        ).astimezone(timezone.utc)
                        days_old = (datetime.now(tz=timezone.utc) - dt).days
                    except ValueError:
                        days_old = 999
                if days_old < min_old_days:
                    continue

                best_rank = int(row.get("best_rank") or 200)
                affinity_bonus = max(0.2, min(2.3, (80 - best_rank) / 30))
                total = 2.0 + affinity_bonus - (early_skip_count * 2.0)

                scored.append(
                    {
                        "track_id": track_id,
                        "name": str(row.get("name") or "Unknown Track"),
                        "artists": str(row.get("artists") or "Unknown Artist").replace(
                            ",", ", "
                        ),
                        "uri": row.get("spotify_uri"),
                        "bucket": "Revived",
                        "score_total": total,
                        "score_positive": 2.0 + affinity_bonus,
                        "score_negative": -(early_skip_count * 2.0),
                        "score_familiarity": affinity_bonus,
                        "score_freshness": 0.0,
                        "score_revival": 1.0,
                        "score_exploration": 0.0,
                        "explanation": (
                            "revival from your long-term top affinity; "
                            "not heard recently"
                        ),
                    }
                )
                existing_ids.add(track_id)
                if len(scored) >= target * 3:
                    break

        scored.sort(key=lambda item: float(item["score_total"]), reverse=True)

        selected: list[dict[str, Any]] = []
        selected_ids: set[str] = set()
        artist_counts_by_slice: dict[int, Counter[str]] = defaultdict(Counter)
        base_artist_limit = max(1, int(rules["max_same_artist_per_20"]))
        for item in scored:
            track_id = str(item["track_id"])
            if track_id in selected_ids:
                continue

            primary_artist = (
                (str(item.get("artists") or "unknown").split(",", maxsplit=1)[0])
                .strip()
                .lower()
            )
            slice_idx = len(selected) // 20
            early_mix_limit = (
                1 if len(selected) < min(target, 26) else base_artist_limit
            )
            if artist_counts_by_slice[slice_idx][primary_artist] >= early_mix_limit:
                continue

            artist_counts_by_slice[slice_idx][primary_artist] += 1
            selected.append(item)
            selected_ids.add(track_id)
            if len(selected) >= target:
                break

        return selected

    def _select_today_mix(
        self,
        scored: list[Any],
        source_map: dict[str, set[str]],
        rules: dict[str, float],
        target: int = 40,
    ) -> list[Any]:
        def song_identity(track: Any) -> str:
            name = "".join(
                ch.lower()
                for ch in str(track.name or "")
                if ch.isalnum() or ch.isspace()
            ).strip()
            primary_artist = (
                str(track.artists or "unknown")
                .split(",", maxsplit=1)[0]
                .strip()
                .lower()
            )
            return f"{name}|{primary_artist}"

        def append_if_unique(
            track: Any,
            out: list[Any],
            seen_track_ids: set[str],
            seen_uris: set[str],
            seen_song_ids: set[str],
        ) -> bool:
            track_id = str(track.track_id)
            uri = str(track.uri) if getattr(track, "uri", None) else None
            identity = song_identity(track)

            if track_id in seen_track_ids:
                return False
            if uri and uri in seen_uris:
                return False
            if identity in seen_song_ids:
                return False

            out.append(track)
            seen_track_ids.add(track_id)
            if uri:
                seen_uris.add(uri)
            seen_song_ids.add(identity)
            return True

        recent_skip_rows = self.db.query_all(
            """
      SELECT DISTINCT track_id
      FROM play_events
      WHERE ended_reason = 'skip_early'
        AND started_at >= datetime('now', '-7 day')
      """
        )
        blocked_recent_skips = {
            str(row["track_id"]) for row in recent_skip_rows if row.get("track_id")
        }

        eligible = [
            track for track in scored if track.track_id not in blocked_recent_skips
        ]

        safe = [track for track in eligible if track.bucket == "Safe"]
        revived = [track for track in eligible if track.bucket == "Revived"]
        explore = [track for track in eligible if track.bucket == "Explore"]
        fallback = [track for track in eligible if track.bucket != "Avoided"]

        selected: list[Any] = []
        selected_ids: set[str] = set()

        def take_from(pool: list[Any], count: int) -> None:
            for track in pool:
                if len([x for x in selected if x.bucket == track.bucket]) >= count:
                    break
                if track.track_id in selected_ids:
                    continue
                selected.append(track)
                selected_ids.add(track.track_id)

        take_from(safe, 20)
        take_from(revived, 10)
        take_from(explore, 10)

        for track in fallback:
            if len(selected) >= target:
                break
            if track.track_id in selected_ids:
                continue
            selected.append(track)
            selected_ids.add(track.track_id)

        artist_limit = int(rules["max_same_artist_per_20"])
        balanced: list[Any] = []
        artist_counts_by_slice: dict[int, Counter[str]] = defaultdict(Counter)
        for track in selected:
            primary_artist = (
                (track.artists.split(",", maxsplit=1)[0] or "unknown").strip().lower()
            )
            slice_idx = len(balanced) // 20
            if artist_counts_by_slice[slice_idx][primary_artist] >= artist_limit:
                continue
            artist_counts_by_slice[slice_idx][primary_artist] += 1
            balanced.append(track)
            if len(balanced) >= target:
                break

        random_enabled = int(rules.get("recent_artist_random_enabled", 0)) == 1
        random_slots = max(0, int(rules.get("recent_artist_random_slots", 0)))
        if random_enabled and random_slots > 0:
            balanced_ids = {track.track_id for track in balanced}
            random_pool = [
                track
                for track in eligible
                if track.track_id not in balanced_ids
                and "recent_artist_random" in source_map.get(track.track_id, set())
            ]
            random.shuffle(random_pool)

            for random_track in random_pool[:random_slots]:
                if len(balanced) < target:
                    balanced.append(random_track)
                    continue

                replace_index = next(
                    (
                        idx
                        for idx in range(len(balanced) - 1, -1, -1)
                        if "recent_artist_random"
                        not in source_map.get(balanced[idx].track_id, set())
                    ),
                    None,
                )
                if replace_index is None:
                    break
                balanced[replace_index] = random_track

        deduped: list[Any] = []
        seen_track_ids: set[str] = set()
        seen_uris: set[str] = set()
        seen_song_ids: set[str] = set()

        for track in balanced:
            append_if_unique(track, deduped, seen_track_ids, seen_uris, seen_song_ids)

        if len(deduped) < target:
            for track in fallback:
                if len(deduped) >= target:
                    break
                append_if_unique(
                    track, deduped, seen_track_ids, seen_uris, seen_song_ids
                )

        return deduped[:target]

    def _persist_today_mix(self, tracks: list[Any]) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        self.db.execute("DELETE FROM today_mix_cache")
        for rank, track in enumerate(tracks, start=1):
            self.db.execute(
                """
        INSERT INTO today_mix_cache(rank, track_id, bucket, explanation, computed_at)
        VALUES (?, ?, ?, ?, ?)
        """,
                (rank, track.track_id, track.bucket, track.explanation, now),
            )

    def _resolve_user_id(self) -> str:
        if self.settings.spotify_user_id:
            return self.settings.spotify_user_id

        playlist_owner = self.db.query_one(
            """
      SELECT owner_id
      FROM playlists
      WHERE owner_id IS NOT NULL
        AND owner_id <> 'spotify'
      LIMIT 1
      """
        )
        if playlist_owner and playlist_owner.get("owner_id"):
            return str(playlist_owner["owner_id"])

        try:
            me = self.spotify.get_me()
            if me.get("id"):
                return str(me["id"])
        except Exception:
            pass

        raise RuntimeError(
            "Cannot determine Spotify user id. Set SPOTIFY_USER_ID in .env and retry."
        )

    def _ensure_playlist(self, name: str, user_id: str) -> str:
        row = self.db.query_one(
            "SELECT playlist_id FROM playlists WHERE name = ?", (name,)
        )
        if row and row.get("playlist_id"):
            return str(row["playlist_id"])

        created = self.spotify.create_private_playlist(
            user_id=user_id,
            name=name,
            description="Generated by Aftertaste",
        )
        playlist_id = created.get("id")
        if not playlist_id:
            raise RuntimeError(f"Failed to create playlist: {name}")

        now = datetime.now(tz=timezone.utc).isoformat()
        self.db.execute(
            """
      INSERT INTO playlists(
        playlist_id,
        name,
        owner_id,
        is_private,
        is_spotify_made_guess,
        snapshot_id,
        last_sync_at
      ) VALUES (?, ?, ?, 1, 0, ?, ?)
      ON CONFLICT(playlist_id)
      DO UPDATE SET name = excluded.name, owner_id = excluded.owner_id
      """,
            (playlist_id, name, user_id, created.get("snapshot_id"), now),
        )
        self.db.execute(
            """
      INSERT INTO source_preferences(playlist_id, include_source, manually_confirmed, updated_at)
      VALUES (?, 1, 1, ?)
      ON CONFLICT(playlist_id)
      DO UPDATE SET include_source = 1, manually_confirmed = 1, updated_at = excluded.updated_at
      """,
            (playlist_id, now),
        )
        return str(playlist_id)

    def _write_playlists(
        self, today_tracks: list[Any], scored: list[Any]
    ) -> dict[str, str]:
        if not self.spotify.is_authorized():
            raise RuntimeError("Spotify auth required before writing playlists.")

        user_id = self._resolve_user_id()
        today_id = self._ensure_playlist("Aftertaste / Today", user_id)
        holding_id = self._ensure_playlist("Aftertaste / Holding Tank", user_id)
        avoid_id = self._ensure_playlist("Aftertaste / Avoid for Now", user_id)

        today_uris = list(
            dict.fromkeys([track.uri for track in today_tracks if track.uri])
        )
        self.spotify.replace_playlist_items(today_id, today_uris)

        today_ids = {track.track_id for track in today_tracks}
        holding_candidates = [
            track.uri
            for track in scored
            if track.uri
            and track.track_id not in today_ids
            and track.bucket in {"Safe", "Explore"}
        ][:100]
        self.spotify.replace_playlist_items(holding_id, holding_candidates)

        avoid_candidates = [
            track.uri for track in scored if track.uri and track.bucket == "Avoided"
        ]
        if len(avoid_candidates) < 100:
            hard_avoid = self.db.query_all(
                """
        SELECT t.spotify_uri
        FROM play_events pe
        JOIN tracks t ON t.track_id = pe.track_id
        WHERE pe.ended_reason = 'skip_early'
        GROUP BY pe.track_id
        HAVING COUNT(*) >= 5
        LIMIT 100
        """
            )
            for row in hard_avoid:
                uri = row.get("spotify_uri")
                if isinstance(uri, str):
                    avoid_candidates.append(uri)

        deduped_avoid = list(dict.fromkeys(avoid_candidates))[:100]
        self.spotify.replace_playlist_items(avoid_id, deduped_avoid)

        return {
            "today": today_id,
            "holding_tank": holding_id,
            "avoid_for_now": avoid_id,
        }

    def _write_vibe_revival_playlist(
        self, tracks: list[dict[str, Any]]
    ) -> dict[str, str]:
        if not self.spotify.is_authorized():
            raise RuntimeError("Spotify auth required before writing playlists.")

        user_id = self._resolve_user_id()
        revival_id = self._ensure_playlist("Aftertaste / Vibe Revival", user_id)
        revival_uris = [str(track.get("uri")) for track in tracks if track.get("uri")]
        self.spotify.replace_playlist_items(revival_id, revival_uris)
        return {"vibe_revival": revival_id}

    def generate_today_mix(self, write_to_spotify: bool = False) -> dict[str, Any]:
        rebuild_transition_edges(self.db)
        rules = load_rules(self.db)
        candidates, source_map = build_candidates(self.db)
        self._inject_recent_artist_random_candidates(candidates, source_map, rules)
        scored = score_candidates(self.db, candidates, source_map)
        self._persist_scores(scored)

        today = self._select_today_mix(
            scored, source_map=source_map, rules=rules, target=40
        )
        self._persist_today_mix(today)

        result: dict[str, Any] = {
            "candidate_count": len(candidates),
            "selected_count": len(today),
            "tracks": [
                {
                    "track_id": track.track_id,
                    "name": track.name,
                    "artists": track.artists,
                    "uri": track.uri,
                    "bucket": track.bucket,
                    "score_total": track.score.total,
                    "score_positive": track.score.positive,
                    "score_negative": track.score.negative,
                    "score_familiarity": track.score.familiarity,
                    "score_freshness": track.score.freshness,
                    "score_revival": track.score.revival,
                    "score_exploration": track.score.exploration,
                    "explanation": track.explanation,
                }
                for track in today
            ],
        }

        if write_to_spotify:
            self.require_configured()
            result["playlists"] = self._write_playlists(today, scored)

        return result

    def generate_vibe_revival(self, write_to_spotify: bool = False) -> dict[str, Any]:
        tracks = self._build_vibe_revival_mix(target=40)
        result: dict[str, Any] = {
            "selected_count": len(tracks),
            "tracks": tracks,
        }

        if write_to_spotify:
            self.require_configured()
            result["playlists"] = self._write_vibe_revival_playlist(tracks)

        return result

    def get_today_mix(self, limit: int = 40) -> list[dict[str, Any]]:
        return self.db.query_all(
            """
      SELECT
        c.rank,
        c.track_id,
        t.name,
        t.spotify_uri,
        REPLACE(GROUP_CONCAT(DISTINCT a.name), ',', ', ') AS artists,
        c.bucket,
        c.explanation,
        s.score_total,
        s.score_positive,
        s.score_negative,
        s.score_familiarity,
        s.score_freshness,
        s.score_revival,
        s.score_exploration
      FROM today_mix_cache c
      JOIN tracks t ON t.track_id = c.track_id
      LEFT JOIN track_artists ta ON ta.track_id = t.track_id
      LEFT JOIN artists a ON a.artist_id = ta.artist_id
      LEFT JOIN track_scores s ON s.track_id = c.track_id
      GROUP BY c.rank, c.track_id, t.name, t.spotify_uri, c.bucket, c.explanation
      ORDER BY c.rank ASC
      LIMIT ?
      """,
            (limit,),
        )

    def top_up_live_queue(self, count: int = 3) -> dict[str, int]:
        tracks = self.get_today_mix(limit=20)
        uris = [row["spotify_uri"] for row in tracks if row.get("spotify_uri")]
        return top_up_queue(
            client=self.spotify, track_uris=uris, target_depth=max(1, count)
        )

    def dashboard(self) -> dict[str, Any]:
        self._maybe_reconcile_recent_history(every_seconds=120)
        self._maybe_sync_cloud()

        likely_skips = self.db.query_one(
            """
      SELECT COUNT(*) AS c
      FROM play_events
      WHERE ended_reason = 'skip_early'
        AND date(started_at) = date('now')
      """
        )
        completions = self.db.query_one(
            """
      SELECT COUNT(*) AS c
      FROM play_events
      WHERE ended_reason = 'completed'
        AND date(started_at) = date('now')
      """
        )

        top_negative_artists = self.db.query_all(
            """
      SELECT a.artist_id, a.name, COUNT(*) AS skip_count
      FROM play_events pe
      JOIN track_artists ta ON ta.track_id = pe.track_id
      JOIN artists a ON a.artist_id = ta.artist_id
      WHERE pe.ended_reason = 'skip_early'
        AND pe.started_at >= datetime('now', '-7 day')
      GROUP BY a.artist_id, a.name
      ORDER BY skip_count DESC
      LIMIT 5
      """
        )

        top_revived_tracks = self.db.query_all(
            """
      SELECT t.track_id, t.name, s.score_revival
      FROM track_scores s
      JOIN tracks t ON t.track_id = s.track_id
      WHERE s.score_revival > 1.5
      ORDER BY s.score_revival DESC, s.score_total DESC
      LIMIT 5
      """
        )

        now_playing = None
        if self.spotify.is_authorized():
            try:
                payload = self.spotify.get_currently_playing() or {}
                item = payload.get("item") or {}
                if item.get("id"):
                    now_playing = {
                        "track_id": item.get("id"),
                        "name": item.get("name"),
                        "artists": ", ".join(
                            artist.get("name", "")
                            for artist in item.get("artists") or []
                        ),
                        "is_playing": bool(payload.get("is_playing")),
                        "progress_ms": payload.get("progress_ms") or 0,
                        "duration_ms": item.get("duration_ms") or 0,
                    }
            except Exception:
                now_playing = None

        next_refresh = datetime.now(tz=timezone.utc).replace(
            hour=8, minute=0, second=0, microsecond=0
        ) + timedelta(days=1)

        return {
            "now_playing": now_playing,
            "likely_skip_count_today": int((likely_skips or {"c": 0})["c"]),
            "completions_today": int((completions or {"c": 0})["c"]),
            "top_negative_artists": top_negative_artists,
            "top_revived_tracks": top_revived_tracks,
            "next_playlist_refresh_time": next_refresh.isoformat(),
            "poller_running": self.poller.running,
            "cloud_sync_enabled": self.cloud_sync_client.is_enabled(),
        }

    def memory_negative_artists(self) -> list[dict[str, Any]]:
        return self.db.query_all(
            """
      SELECT
        a.artist_id,
        a.name,
        COUNT(*) AS early_skip_count,
        MAX(pe.started_at) AS last_skip_at
      FROM play_events pe
      JOIN track_artists ta ON ta.track_id = pe.track_id
      JOIN artists a ON a.artist_id = ta.artist_id
      WHERE pe.ended_reason = 'skip_early'
      GROUP BY a.artist_id, a.name
      ORDER BY early_skip_count DESC
      LIMIT 50
      """
        )

    def memory_tracks(self, limit: int = 120) -> list[dict[str, Any]]:
        return self.db.query_all(
            """
      SELECT
        t.track_id,
        t.name,
        REPLACE(GROUP_CONCAT(DISTINCT a.name), ',', ', ') AS artists,
        COUNT(DISTINCT CASE WHEN pe.ended_reason = 'skip_early' THEN COALESCE(pe.event_uid, 'legacy-' || pe.event_id) END) AS early_skips,
        COUNT(DISTINCT CASE WHEN pe.ended_reason = 'completed' THEN COALESCE(pe.event_uid, 'legacy-' || pe.event_id) END) AS completions,
        MAX(pe.ended_at) AS last_played
      FROM tracks t
      LEFT JOIN play_events pe ON pe.track_id = t.track_id
      LEFT JOIN track_artists ta ON ta.track_id = t.track_id
      LEFT JOIN artists a ON a.artist_id = ta.artist_id
      GROUP BY t.track_id, t.name
      ORDER BY early_skips DESC, completions DESC, last_played DESC
      LIMIT ?
      """,
            (limit,),
        )

    def get_rules(self) -> dict[str, float]:
        return load_rules(self.db)

    def save_rules(self, updates: dict[str, float]) -> dict[str, float]:
        return update_rules(self.db, updates)

    def list_sources(self) -> list[dict[str, Any]]:
        return self.db.query_all(
            """
      SELECT
        p.playlist_id,
        p.name,
        p.owner_id,
        p.is_spotify_made_guess,
        COALESCE(sp.include_source, 1) AS include_source,
        COALESCE(sp.manually_confirmed, 0) AS manually_confirmed
      FROM playlists p
      LEFT JOIN source_preferences sp ON sp.playlist_id = p.playlist_id
      ORDER BY p.is_spotify_made_guess DESC, p.name ASC
      """
        )

    def update_source(
        self, playlist_id: str, include_source: bool, manually_confirmed: bool
    ) -> None:
        self.db.execute(
            """
      INSERT INTO source_preferences(playlist_id, include_source, manually_confirmed, updated_at)
      VALUES (?, ?, ?, ?)
      ON CONFLICT(playlist_id)
      DO UPDATE SET
        include_source = excluded.include_source,
        manually_confirmed = excluded.manually_confirmed,
        updated_at = excluded.updated_at
      """,
            (
                playlist_id,
                1 if include_source else 0,
                1 if manually_confirmed else 0,
                datetime.now(tz=timezone.utc).isoformat(),
            ),
        )

    def start_poller(self) -> dict[str, bool]:
        self.poller.start()
        return {"running": self.poller.running}

    def stop_poller(self) -> dict[str, bool]:
        self.poller.stop()
        return {"running": self.poller.running}

    def close(self) -> None:
        self._automation_stop.set()
        if self._automation_thread is not None:
            self._automation_thread.join(timeout=3)
        self.poller.stop()
        with self._tenant_lock:
            for poller in self._cloud_pollers.values():
                poller.stop()
            self._cloud_pollers.clear()
            for tenant_db in self._tenant_dbs.values():
                tenant_db.close()
            self._tenant_dbs.clear()
            self._tenant_engines.clear()
            self._cloud_spotify_clients.clear()
            self._cloud_token_stores.clear()
            self._cloud_pkce_owner.clear()
            self._cloud_automation_state.clear()
            self._cloud_manual_run_threads.clear()
            self._cloud_run_locks.clear()
            self._cloud_auth_probe_cache.clear()
        self.db.close()
