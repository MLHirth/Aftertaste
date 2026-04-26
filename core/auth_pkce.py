from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import urlencode

import keyring


SCOPES: Final[list[str]] = [
    "user-read-currently-playing",
    "user-read-playback-state",
    "user-read-recently-played",
    "user-top-read",
    "user-library-read",
    "playlist-read-private",
    "playlist-read-collaborative",
    "playlist-modify-private",
    "user-modify-playback-state",
]

AUTH_URL: Final[str] = "https://accounts.spotify.com/authorize"


def _urlsafe(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def generate_code_verifier() -> str:
    return _urlsafe(secrets.token_bytes(64))


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return _urlsafe(digest)


@dataclass(slots=True)
class PendingAuthSession:
    session_id: str
    state: str
    verifier: str


class PKCEManager:
    def __init__(self) -> None:
        self._sessions: dict[str, PendingAuthSession] = {}

    def start(self, client_id: str, redirect_uri: str) -> dict[str, str]:
        verifier = generate_code_verifier()
        state = _urlsafe(secrets.token_bytes(32))
        session_id = _urlsafe(secrets.token_bytes(18))

        self._sessions[session_id] = PendingAuthSession(
            session_id=session_id,
            state=state,
            verifier=verifier,
        )

        query = urlencode(
            {
                "client_id": client_id,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "code_challenge_method": "S256",
                "code_challenge": code_challenge(verifier),
                "scope": " ".join(SCOPES),
                "state": state,
            }
        )
        return {
            "session_id": session_id,
            "authorize_url": f"{AUTH_URL}?{query}",
        }

    def consume(self, session_id: str, state: str) -> str:
        pending = self._sessions.get(session_id)
        if pending is None:
            raise ValueError("Unknown auth session. Start login again.")
        if pending.state != state:
            raise ValueError("OAuth state mismatch.")
        del self._sessions[session_id]
        return pending.verifier


class TokenStore:
    def __init__(self, service_name: str = "aftertaste") -> None:
        self.service_name = service_name
        self.username = "spotify_refresh_token"
        self.fallback_path = Path.home() / ".aftertaste-refresh-token.json"

    def load_refresh_token(self) -> str | None:
        try:
            token = keyring.get_password(self.service_name, self.username)
            if token:
                return token
        except Exception:
            pass

        if self.fallback_path.exists():
            payload = json.loads(self.fallback_path.read_text(encoding="utf-8"))
            return payload.get("refresh_token")
        return None

    def save_refresh_token(self, token: str) -> None:
        try:
            keyring.set_password(self.service_name, self.username, token)
            return
        except Exception:
            pass

        self.fallback_path.write_text(
            json.dumps({"refresh_token": token}, indent=2),
            encoding="utf-8",
        )
