from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(slots=True)
class Settings:
    spotify_client_id: str | None
    spotify_user_id: str | None
    spotify_redirect_uri: str
    db_path: Path
    api_host: str
    api_port: int
    cloud_sync_enabled: bool
    cloud_api_base_url: str | None
    cloud_client_id: str
    cloud_sync_poll_seconds: int
    cloud_bearer_token: str | None
    cloud_tenant_db_dir: Path
    clerk_auth_enabled: bool
    clerk_jwks_url: str | None
    clerk_issuer: str | None
    clerk_audience: str | None


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    load_dotenv()

    db_path_raw = os.getenv("AFTERTASTE_DB_PATH", "./aftertaste.db")
    db_path = Path(db_path_raw).expanduser().resolve()

    return Settings(
        spotify_client_id=os.getenv("SPOTIFY_CLIENT_ID") or None,
        spotify_user_id=os.getenv("SPOTIFY_USER_ID") or None,
        spotify_redirect_uri=os.getenv(
            "SPOTIFY_REDIRECT_URI",
            "http://127.0.0.1:43821/callback",
        ),
        db_path=db_path,
        api_host=os.getenv("AFTERTASTE_API_HOST", "127.0.0.1"),
        api_port=int(os.getenv("AFTERTASTE_API_PORT", "8765")),
        cloud_sync_enabled=_as_bool(os.getenv("AFTERTASTE_CLOUD_SYNC_ENABLED"), False),
        cloud_api_base_url=os.getenv("AFTERTASTE_CLOUD_API_BASE_URL") or None,
        cloud_client_id=os.getenv("AFTERTASTE_CLOUD_CLIENT_ID", "desktop-local"),
        cloud_sync_poll_seconds=max(
            15,
            int(os.getenv("AFTERTASTE_CLOUD_SYNC_POLL_SECONDS", "60")),
        ),
        cloud_bearer_token=os.getenv("AFTERTASTE_CLOUD_BEARER_TOKEN") or None,
        cloud_tenant_db_dir=Path(
            os.getenv("AFTERTASTE_CLOUD_TENANT_DB_DIR", "./cloud-tenants")
        )
        .expanduser()
        .resolve(),
        clerk_auth_enabled=_as_bool(os.getenv("CLERK_AUTH_ENABLED"), False),
        clerk_jwks_url=os.getenv("CLERK_JWKS_URL") or None,
        clerk_issuer=os.getenv("CLERK_ISSUER") or None,
        clerk_audience=os.getenv("CLERK_AUDIENCE") or None,
    )
