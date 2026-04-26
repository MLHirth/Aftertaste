from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.cloud_auth import CloudAuth, CloudAuthError, CloudPrincipal
from core.config import load_settings
from core.service import AftertasteService


settings = load_settings()
cloud_auth = CloudAuth(settings)
http_bearer = HTTPBearer(auto_error=False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.service = AftertasteService(settings)
    yield
    app.state.service.close()


app = FastAPI(title="Aftertaste Local Service", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _resolve_web_dist() -> Path:
    from_env = os.getenv("AFTERTASTE_WEB_DIST")
    if from_env:
        return Path(from_env).expanduser().resolve()
    return (Path(__file__).resolve().parent.parent / "app-ui" / "dist").resolve()


WEB_DIST = _resolve_web_dist()
SERVE_WEB = os.getenv("AFTERTASTE_SERVE_WEB", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

if SERVE_WEB and WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="web-assets")


class AuthExchangeBody(BaseModel):
    session_id: str
    state: str
    code: str


class GenerateBody(BaseModel):
    write_to_spotify: bool = False


class QueueBody(BaseModel):
    target_depth: int = Field(default=3, ge=1, le=5)


class RulesBody(BaseModel):
    updates: dict[str, float]


class SourceBody(BaseModel):
    include_source: bool
    manually_confirmed: bool = True


class CloudChangeBody(BaseModel):
    table: str
    op: str
    pk: dict[str, Any]
    row: dict[str, Any] | None = None
    seq: int | None = None


class CloudPushBody(BaseModel):
    client_id: str
    changes: list[CloudChangeBody]


class CloudPullBody(BaseModel):
    client_id: str
    since_seq: int = 0
    limit: int = Field(default=500, ge=1, le=2000)


class CloudSpotifyExchangeBody(BaseModel):
    session_id: str
    state: str
    code: str


def service() -> AftertasteService:
    return app.state.service


def _as_http_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def _authenticate_cloud_credentials(
    credentials: HTTPAuthorizationCredentials | None,
) -> CloudPrincipal:
    authorization_header = None
    if credentials is not None:
        authorization_header = f"Bearer {credentials.credentials}"
    try:
        return cloud_auth.authenticate(authorization_header)
    except CloudAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=401, detail=f"Invalid cloud auth token: {exc}"
        ) from exc


def _cloud_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(http_bearer),
) -> CloudPrincipal:
    return _authenticate_cloud_credentials(credentials)


def _cloud_principal_optional(
    credentials: HTTPAuthorizationCredentials | None = Depends(http_bearer),
) -> CloudPrincipal | None:
    if credentials is None:
        return None
    return _authenticate_cloud_credentials(credentials)


def _dashboard_for_cloud_user(user_id: str) -> dict[str, Any]:
    engine = service().cloud_sync_engine_for_user(user_id)
    db = engine.db
    spotify_status = service().cloud_spotify_status(user_id)
    now_playing = service().cloud_spotify_now_playing(user_id)

    likely_skips = db.query_one(
        """
      SELECT COUNT(*) AS c
      FROM play_events
      WHERE ended_reason = 'skip_early'
        AND date(started_at) = date('now')
      """
    )
    completions = db.query_one(
        """
      SELECT COUNT(*) AS c
      FROM play_events
      WHERE ended_reason = 'completed'
        AND date(started_at) = date('now')
      """
    )

    top_negative_artists = db.query_all(
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

    top_revived_tracks = db.query_all(
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
        "now_playing": now_playing,
        "likely_skip_count_today": int((likely_skips or {"c": 0})["c"]),
        "completions_today": int((completions or {"c": 0})["c"]),
        "top_negative_artists": top_negative_artists,
        "top_revived_tracks": top_revived_tracks,
        "next_playlist_refresh_time": next_refresh.isoformat(),
        "poller_running": bool(spotify_status.get("poller_running")),
        "cloud_sync_enabled": True,
    }


def _memory_negative_artists_for_cloud_user(user_id: str) -> list[dict[str, Any]]:
    engine = service().cloud_sync_engine_for_user(user_id)
    db = engine.db
    return db.query_all(
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


def _memory_tracks_for_cloud_user(user_id: str, limit: int) -> list[dict[str, Any]]:
    engine = service().cloud_sync_engine_for_user(user_id)
    db = engine.db
    return db.query_all(
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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/config/status")
def config_status() -> dict[str, Any]:
    return service().auth_status()


@app.post("/auth/start")
def auth_start() -> dict[str, str]:
    try:
        return service().start_auth()
    except Exception as exc:
        raise _as_http_error(exc) from exc


@app.post("/auth/exchange")
def auth_exchange(body: AuthExchangeBody) -> dict[str, Any]:
    try:
        return service().exchange_auth_code(
            session_id=body.session_id,
            state=body.state,
            code=body.code,
        )
    except Exception as exc:
        raise _as_http_error(exc) from exc


@app.post("/sync/library")
def sync_library() -> dict[str, int]:
    try:
        return service().sync_library()
    except Exception as exc:
        raise _as_http_error(exc) from exc


@app.post("/sync/playlists")
def sync_playlists() -> dict[str, int]:
    try:
        return service().sync_playlists()
    except Exception as exc:
        raise _as_http_error(exc) from exc


@app.post("/sync/top")
def sync_top() -> dict[str, int]:
    try:
        return service().sync_top_items()
    except Exception as exc:
        raise _as_http_error(exc) from exc


@app.post("/sync/recent")
def sync_recent() -> dict[str, int]:
    try:
        return service().reconcile_recent()
    except Exception as exc:
        raise _as_http_error(exc) from exc


@app.post("/sync/all")
def sync_all() -> dict[str, int]:
    try:
        return service().sync_all()
    except Exception as exc:
        raise _as_http_error(exc) from exc


@app.post("/sync/cloud-now")
def sync_cloud_now(
    credentials: HTTPAuthorizationCredentials | None = Depends(http_bearer),
) -> dict[str, Any]:
    try:
        token_override = credentials.credentials if credentials is not None else None
        return service().sync_cloud_once(bearer_token_override=token_override)
    except Exception as exc:
        raise _as_http_error(exc) from exc


@app.get("/sync/cloud-status")
def sync_cloud_status(
    credentials: HTTPAuthorizationCredentials | None = Depends(http_bearer),
) -> dict[str, Any]:
    try:
        token_override = credentials.credentials if credentials is not None else None
        return service().sync_cloud_status(bearer_token_override=token_override)
    except Exception as exc:
        raise _as_http_error(exc) from exc


@app.post("/poller/start")
def poller_start() -> dict[str, bool]:
    try:
        return service().start_poller()
    except Exception as exc:
        raise _as_http_error(exc) from exc


@app.post("/poller/stop")
def poller_stop() -> dict[str, bool]:
    try:
        return service().stop_poller()
    except Exception as exc:
        raise _as_http_error(exc) from exc


@app.post("/generate/today")
def generate_today(body: GenerateBody) -> dict[str, Any]:
    try:
        return service().generate_today_mix(write_to_spotify=body.write_to_spotify)
    except Exception as exc:
        raise _as_http_error(exc) from exc


@app.post("/generate/vibe-revival")
def generate_vibe_revival(body: GenerateBody) -> dict[str, Any]:
    try:
        return service().generate_vibe_revival(write_to_spotify=body.write_to_spotify)
    except Exception as exc:
        raise _as_http_error(exc) from exc


@app.post("/queue/top-up")
def queue_top_up(body: QueueBody) -> dict[str, int]:
    try:
        return service().top_up_live_queue(count=body.target_depth)
    except Exception as exc:
        raise _as_http_error(exc) from exc


@app.get("/dashboard")
def dashboard(
    principal: CloudPrincipal | None = Depends(_cloud_principal_optional),
) -> dict[str, Any]:
    if principal is not None:
        return _dashboard_for_cloud_user(principal.user_id)
    return service().dashboard()


@app.get("/today-mix")
def today_mix(limit: int = 40) -> list[dict[str, Any]]:
    return service().get_today_mix(limit=limit)


@app.get("/memory/negative-artists")
def memory_negative_artists(
    principal: CloudPrincipal | None = Depends(_cloud_principal_optional),
) -> list[dict[str, Any]]:
    if principal is not None:
        return _memory_negative_artists_for_cloud_user(principal.user_id)
    return service().memory_negative_artists()


@app.get("/memory/tracks")
def memory_tracks(
    limit: int = 120,
    principal: CloudPrincipal | None = Depends(_cloud_principal_optional),
) -> list[dict[str, Any]]:
    if principal is not None:
        return _memory_tracks_for_cloud_user(principal.user_id, limit)
    return service().memory_tracks(limit=limit)


@app.get("/rules")
def rules() -> dict[str, float]:
    return service().get_rules()


@app.put("/rules")
def save_rules(body: RulesBody) -> dict[str, float]:
    return service().save_rules(body.updates)


@app.get("/sources")
def sources() -> list[dict[str, Any]]:
    return service().list_sources()


@app.put("/sources/{playlist_id}")
def save_source(playlist_id: str, body: SourceBody) -> dict[str, bool]:
    service().update_source(
        playlist_id=playlist_id,
        include_source=body.include_source,
        manually_confirmed=body.manually_confirmed,
    )
    return {"ok": True}


@app.post("/cloud/spotify/auth/start")
def cloud_spotify_auth_start(
    principal: CloudPrincipal = Depends(_cloud_principal),
) -> dict[str, str]:
    try:
        return service().cloud_spotify_start_auth(principal.user_id)
    except Exception as exc:
        raise _as_http_error(exc) from exc


@app.post("/cloud/spotify/auth/exchange")
def cloud_spotify_auth_exchange(
    body: CloudSpotifyExchangeBody,
    principal: CloudPrincipal = Depends(_cloud_principal),
) -> dict[str, Any]:
    try:
        return service().cloud_spotify_exchange_auth(
            principal.user_id,
            session_id=body.session_id,
            state=body.state,
            code=body.code,
        )
    except Exception as exc:
        raise _as_http_error(exc) from exc


@app.get("/cloud/spotify/status")
def cloud_spotify_status(
    principal: CloudPrincipal = Depends(_cloud_principal),
) -> dict[str, Any]:
    try:
        payload = service().cloud_spotify_status(principal.user_id)
        payload["user_id"] = principal.user_id
        return payload
    except Exception as exc:
        raise _as_http_error(exc) from exc


@app.post("/cloud/spotify/automation/run")
def cloud_spotify_automation_run(
    principal: CloudPrincipal = Depends(_cloud_principal),
) -> dict[str, Any]:
    try:
        return service().run_cloud_master_once(principal.user_id)
    except Exception as exc:
        raise _as_http_error(exc) from exc


@app.post("/cloud/sync/push")
def cloud_sync_push(
    body: CloudPushBody,
    principal: CloudPrincipal = Depends(_cloud_principal),
) -> dict[str, Any]:
    engine = service().cloud_sync_engine_for_user(principal.user_id)
    result = engine.apply_changes([change.model_dump() for change in body.changes])
    return {
        "ok": True,
        "user_id": principal.user_id,
        "client_id": body.client_id,
        "applied": result["applied"],
        "skipped": result["skipped"],
    }


@app.post("/cloud/sync/pull")
def cloud_sync_pull(
    body: CloudPullBody,
    principal: CloudPrincipal = Depends(_cloud_principal),
) -> dict[str, Any]:
    engine = service().cloud_sync_engine_for_user(principal.user_id)
    payload = engine.export_changes(
        since_seq=max(0, int(body.since_seq)),
        limit=int(body.limit),
    )
    payload["user_id"] = principal.user_id
    payload["client_id"] = body.client_id
    return payload


@app.get("/cloud/sync/status")
def cloud_sync_status(
    principal: CloudPrincipal = Depends(_cloud_principal),
) -> dict[str, Any]:
    engine = service().cloud_sync_engine_for_user(principal.user_id)
    checkpoint = engine.load_checkpoint("default")
    latest = engine.db.query_one("SELECT MAX(seq) AS max_seq FROM sync_log") or {
        "max_seq": 0
    }
    return {
        "ok": True,
        "user_id": principal.user_id,
        "latest_seq": int(latest.get("max_seq") or 0),
        "checkpoint": checkpoint,
    }


if SERVE_WEB and WEB_DIST.exists():

    @app.get("/")
    def web_index() -> FileResponse:
        return FileResponse(WEB_DIST / "index.html")

    @app.get("/{path_name:path}")
    def web_spa(path_name: str) -> FileResponse:
        if path_name.startswith("api"):
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(WEB_DIST / "index.html")


if __name__ == "__main__":
    uvicorn.run(
        "core.api:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )
