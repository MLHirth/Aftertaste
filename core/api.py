from __future__ import annotations

from contextlib import asynccontextmanager
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
    return service().cloud_dashboard(user_id)


def _memory_negative_artists_for_cloud_user(user_id: str) -> list[dict[str, Any]]:
    return service().cloud_memory_negative_artists(user_id)


def _memory_tracks_for_cloud_user(user_id: str, limit: int) -> list[dict[str, Any]]:
    return service().cloud_memory_tracks(user_id, limit=limit)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/config/status")
def config_status(
    principal: CloudPrincipal | None = Depends(_cloud_principal_optional),
) -> dict[str, Any]:
    status = service().auth_status()
    if principal is None:
        status["spotify_mode"] = "desktop"
        return status

    cloud = service().cloud_spotify_status(principal.user_id)
    status["authorized"] = bool(cloud.get("connected"))
    status["db_path"] = f"cloud-tenant:{principal.user_id}"
    status["spotify_mode"] = "server_managed"
    status["cloud_spotify_connected"] = bool(cloud.get("connected"))
    status["server_master_enabled"] = bool(cloud.get("server_master_enabled"))
    return status


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
def sync_all(
    principal: CloudPrincipal | None = Depends(_cloud_principal_optional),
) -> dict[str, int]:
    try:
        if principal is not None:
            return service().cloud_sync_all(principal.user_id)
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
def poller_start(
    principal: CloudPrincipal | None = Depends(_cloud_principal_optional),
) -> dict[str, bool]:
    try:
        if principal is not None:
            return service().cloud_start_poller(principal.user_id)
        return service().start_poller()
    except Exception as exc:
        raise _as_http_error(exc) from exc


@app.post("/poller/stop")
def poller_stop(
    principal: CloudPrincipal | None = Depends(_cloud_principal_optional),
) -> dict[str, bool]:
    try:
        if principal is not None:
            return service().cloud_stop_poller(principal.user_id)
        return service().stop_poller()
    except Exception as exc:
        raise _as_http_error(exc) from exc


@app.post("/generate/today")
def generate_today(
    body: GenerateBody,
    principal: CloudPrincipal | None = Depends(_cloud_principal_optional),
) -> dict[str, Any]:
    try:
        if principal is not None:
            return service().cloud_generate_today_mix(
                principal.user_id,
                write_to_spotify=body.write_to_spotify,
            )
        return service().generate_today_mix(write_to_spotify=body.write_to_spotify)
    except Exception as exc:
        raise _as_http_error(exc) from exc


@app.post("/generate/vibe-revival")
def generate_vibe_revival(
    body: GenerateBody,
    principal: CloudPrincipal | None = Depends(_cloud_principal_optional),
) -> dict[str, Any]:
    try:
        if principal is not None:
            return service().cloud_generate_vibe_revival(
                principal.user_id,
                write_to_spotify=body.write_to_spotify,
            )
        return service().generate_vibe_revival(write_to_spotify=body.write_to_spotify)
    except Exception as exc:
        raise _as_http_error(exc) from exc


@app.post("/queue/top-up")
def queue_top_up(
    body: QueueBody,
    principal: CloudPrincipal | None = Depends(_cloud_principal_optional),
) -> dict[str, int]:
    try:
        if principal is not None:
            return service().cloud_top_up_live_queue(
                principal.user_id,
                count=body.target_depth,
            )
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
def today_mix(
    limit: int = 40,
    principal: CloudPrincipal | None = Depends(_cloud_principal_optional),
) -> list[dict[str, Any]]:
    if principal is not None:
        return service().cloud_get_today_mix(principal.user_id, limit=limit)
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
def rules(
    principal: CloudPrincipal | None = Depends(_cloud_principal_optional),
) -> dict[str, float]:
    if principal is not None:
        return service().cloud_get_rules(principal.user_id)
    return service().get_rules()


@app.put("/rules")
def save_rules(
    body: RulesBody,
    principal: CloudPrincipal | None = Depends(_cloud_principal_optional),
) -> dict[str, float]:
    if principal is not None:
        return service().cloud_save_rules(principal.user_id, body.updates)
    return service().save_rules(body.updates)


@app.get("/sources")
def sources(
    principal: CloudPrincipal | None = Depends(_cloud_principal_optional),
) -> list[dict[str, Any]]:
    if principal is not None:
        return service().cloud_list_sources(principal.user_id)
    return service().list_sources()


@app.put("/sources/{playlist_id}")
def save_source(
    playlist_id: str,
    body: SourceBody,
    principal: CloudPrincipal | None = Depends(_cloud_principal_optional),
) -> dict[str, bool]:
    if principal is not None:
        service().cloud_update_source(
            principal.user_id,
            playlist_id=playlist_id,
            include_source=body.include_source,
            manually_confirmed=body.manually_confirmed,
        )
        return {"ok": True}

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
