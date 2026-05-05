from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any

import requests

from core.auth_pkce import TokenStore
from core.config import Settings


class SpotifyClient:
    API_BASE = "https://api.spotify.com/v1"
    AUTH_BASE = "https://accounts.spotify.com/api/token"

    def __init__(self, settings: Settings, token_store: TokenStore) -> None:
        self.settings = settings
        self.token_store = token_store
        self.access_token: str | None = None
        self.access_token_expires_at: datetime | None = None
        self.refresh_token: str | None = token_store.load_refresh_token()

        self._cooldowns: dict[str, float] = {}
        self._backoffs: dict[str, int] = defaultdict(int)
        self._etags: dict[str, str] = {}
        self._user_market: str | None = None
        self._lock = RLock()

    def is_configured(self) -> bool:
        return bool(self.settings.spotify_client_id)

    def is_authorized(self) -> bool:
        return bool(self.refresh_token or self._access_token_valid())

    def _access_token_valid(self) -> bool:
        return bool(
            self.access_token
            and self.access_token_expires_at
            and datetime.now(tz=timezone.utc) < self.access_token_expires_at
        )

    def _require_client_id(self) -> str:
        if not self.settings.spotify_client_id:
            raise RuntimeError("Missing SPOTIFY_CLIENT_ID in environment.")
        return self.settings.spotify_client_id

    def exchange_code(self, code: str, code_verifier: str) -> dict[str, Any]:
        client_id = self._require_client_id()
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.settings.spotify_redirect_uri,
            "client_id": client_id,
            "code_verifier": code_verifier,
        }
        response = requests.post(self.AUTH_BASE, data=payload, timeout=20)
        response.raise_for_status()
        data = response.json()
        self._set_tokens(data)
        return data

    def _set_tokens(self, token_payload: dict[str, Any]) -> None:
        self.access_token = token_payload["access_token"]
        expires_in = int(token_payload.get("expires_in", 3600))
        self.access_token_expires_at = datetime.now(tz=timezone.utc) + timedelta(
            seconds=max(0, expires_in - 30)
        )
        refresh = token_payload.get("refresh_token")
        if refresh:
            self.refresh_token = refresh
            self.token_store.save_refresh_token(refresh)

    def refresh_access_token(self, timeout_seconds: float = 20) -> None:
        client_id = self._require_client_id()
        if not self.refresh_token:
            raise RuntimeError("No refresh token found. Login again.")

        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": client_id,
        }
        response = requests.post(self.AUTH_BASE, data=payload, timeout=timeout_seconds)
        response.raise_for_status()
        data = response.json()
        if "refresh_token" not in data:
            data["refresh_token"] = self.refresh_token
        self._set_tokens(data)

    def _family_key(self, path: str) -> str:
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 2:
            return "/".join(parts[:2])
        if parts:
            return parts[0]
        return "root"

    def _wait_for_cooldown(self, family: str) -> None:
        until = self._cooldowns.get(family)
        if not until:
            return
        now = time.time()
        if until > now:
            time.sleep(until - now)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
        etag_key: str | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            if not self._access_token_valid():
                self.refresh_access_token()

            if not self.access_token:
                raise RuntimeError("No valid access token available.")

            family = self._family_key(path)
            self._wait_for_cooldown(family)

            headers: dict[str, str] = {"Authorization": f"Bearer {self.access_token}"}
            if etag_key and etag_key in self._etags:
                headers["If-None-Match"] = self._etags[etag_key]

            url = f"{self.API_BASE}{path}"
            refreshed_once = False

            for _ in range(5):
                request_kwargs: dict[str, Any] = {
                    "params": params,
                    "headers": headers,
                    "timeout": 25,
                }
                if json_payload is not None:
                    request_kwargs["json"] = json_payload

                response = requests.request(
                    method,
                    url,
                    **request_kwargs,
                )

                if response.status_code == 304:
                    return None

                if response.status_code == 401 and not refreshed_once:
                    self.refresh_access_token()
                    headers["Authorization"] = f"Bearer {self.access_token}"
                    refreshed_once = True
                    continue

                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", "1"))
                    self._backoffs[family] += 1
                    exp = min(8.0, 2 ** self._backoffs[family])
                    cooldown = retry_after + exp
                    self._cooldowns[family] = time.time() + cooldown
                    time.sleep(cooldown)
                    continue

                response.raise_for_status()
                self._backoffs[family] = 0

                if etag_key:
                    etag = response.headers.get("ETag")
                    if etag:
                        self._etags[etag_key] = etag

                if not response.content:
                    return {}
                return response.json()

            raise RuntimeError(f"Spotify request failed after retries: {method} {path}")

    def get_me(self) -> dict[str, Any]:
        me = self.request("GET", "/me") or {}
        country = me.get("country")
        if isinstance(country, str) and country:
            self._user_market = country
        return me

    def probe_me(self, timeout_seconds: float = 3) -> dict[str, Any]:
        with self._lock:
            if not self._access_token_valid():
                self.refresh_access_token(timeout_seconds=timeout_seconds)

            if not self.access_token:
                raise RuntimeError("No valid access token available.")

            headers: dict[str, str] = {"Authorization": f"Bearer {self.access_token}"}
            response = requests.get(
                f"{self.API_BASE}/me",
                headers=headers,
                timeout=timeout_seconds,
            )

            if response.status_code == 401:
                self.refresh_access_token(timeout_seconds=timeout_seconds)
                headers["Authorization"] = f"Bearer {self.access_token}"
                response = requests.get(
                    f"{self.API_BASE}/me",
                    headers=headers,
                    timeout=timeout_seconds,
                )

            response.raise_for_status()
            me = response.json() if response.content else {}
            country = me.get("country")
            if isinstance(country, str) and country:
                self._user_market = country
            return me

    def get_currently_playing(self) -> dict[str, Any] | None:
        return self.request("GET", "/me/player/currently-playing")

    def get_player_state(self) -> dict[str, Any] | None:
        return self.request("GET", "/me/player")

    def get_recently_played(self, limit: int = 50) -> dict[str, Any]:
        return (
            self.request("GET", "/me/player/recently-played", params={"limit": limit})
            or {}
        )

    def get_saved_tracks(self, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        return (
            self.request("GET", "/me/tracks", params={"limit": limit, "offset": offset})
            or {}
        )

    def get_playlists(self, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        return (
            self.request(
                "GET", "/me/playlists", params={"limit": limit, "offset": offset}
            )
            or {}
        )

    def get_playlist_items(
        self, playlist_id: str, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        try:
            return (
                self.request(
                    "GET",
                    f"/playlists/{playlist_id}/items",
                    params={"limit": limit, "offset": offset},
                )
                or {}
            )
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code != 403:
                raise

            return {
                "items": [],
                "total": 0,
                "forbidden": True,
            }

    def get_top_tracks(self, time_range: str, limit: int = 50) -> dict[str, Any]:
        return (
            self.request(
                "GET",
                "/me/top/tracks",
                params={"time_range": time_range, "limit": limit},
            )
            or {}
        )

    def get_top_artists(self, time_range: str, limit: int = 50) -> dict[str, Any]:
        return (
            self.request(
                "GET",
                "/me/top/artists",
                params={"time_range": time_range, "limit": limit},
            )
            or {}
        )

    def get_track(self, track_id: str) -> dict[str, Any]:
        return self.request("GET", f"/tracks/{track_id}") or {}

    def get_artist(self, artist_id: str) -> dict[str, Any]:
        return self.request("GET", f"/artists/{artist_id}") or {}

    def search_tracks(self, query: str, limit: int = 10) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 10))
        params: dict[str, Any] = {"q": query, "type": "track", "limit": safe_limit}
        if self._user_market:
            params["market"] = self._user_market
        try:
            return self.request("GET", "/search", params=params) or {}
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code != 400:
                raise

            sanitized = " ".join(
                part
                for part in query.replace('"', " ").replace(":", " ").split()
                if part
            )

            if not self._user_market:
                try:
                    me = self.get_me()
                    country = me.get("country")
                    if isinstance(country, str) and country:
                        self._user_market = country
                except Exception:
                    pass

            fallback_params = {
                "q": sanitized,
                "type": "track",
                "limit": safe_limit,
            }
            if self._user_market:
                fallback_params["market"] = self._user_market
            return self.request("GET", "/search", params=fallback_params) or {}

    def create_private_playlist(
        self, user_id: str, name: str, description: str = ""
    ) -> dict[str, Any]:
        payload = {
            "name": name,
            "public": False,
            "description": description,
        }

        try:
            return self.request("POST", "/me/playlists", json_payload=payload) or {}
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code not in {404, 405, 501}:
                raise

        return (
            self.request(
                "POST",
                f"/users/{user_id}/playlists",
                json_payload=payload,
            )
            or {}
        )

    def add_playlist_items(self, playlist_id: str, uris: list[str]) -> dict[str, Any]:
        return (
            self.request(
                "POST",
                f"/playlists/{playlist_id}/items",
                json_payload={"uris": uris[:100]},
            )
            or {}
        )

    def replace_playlist_items(self, playlist_id: str, uris: list[str]) -> None:
        chunks = [uris[idx : idx + 100] for idx in range(0, len(uris), 100)] or [[]]
        first_chunk = chunks[0]
        self.request(
            "PUT",
            f"/playlists/{playlist_id}/items",
            json_payload={"uris": first_chunk},
        )
        for chunk in chunks[1:]:
            self.add_playlist_items(playlist_id, chunk)

    def add_to_queue(self, uri: str) -> None:
        self.request("POST", "/me/player/queue", params={"uri": uri})

    def get_queue(self) -> dict[str, Any]:
        return self.request("GET", "/me/player/queue") or {}
