# Aftertaste

Aftertaste is a local-first macOS desktop utility that learns your own listening behavior and writes a private daily playlist back to Spotify.

This build follows the plan in `ProjectPlan.txt` and keeps Spotify credentials external so you can provide them later.

## What is implemented

- Tauri + React + TypeScript desktop UI (`app-ui/`)
- Python local service (`core/`) with FastAPI + SQLite
- PKCE auth flow with refresh token persisted in macOS Keychain (fallback file if Keychain is unavailable)
- Spotify request manager with 429 handling, endpoint cooldowns, and ETag cache
- Jobs and modules for:
  - library sync
  - playlist sync
  - playback polling + skip/completion inference
  - recent history reconciliation
  - candidate building
  - transparent additive scoring
  - private playlist writing for `Aftertaste / Today`, `Aftertaste / Holding Tank`, and `Aftertaste / Avoid for Now`
- UI screens from the plan: Dashboard, Today's Mix, Memory, Rules, Sources
- Phase 1 cloud sync foundation:
  - non-destructive SQLite migration backup before new migrations
  - change-log based sync engine (`sync_log`) with table triggers
  - optional push/pull sync between desktop and hosted API
  - Clerk JWT verification for hosted cloud sync endpoints
- Phase 2 cloud multi-user isolation:
  - per-user server-side SQLite databases (`AFTERTASTE_CLOUD_TENANT_DB_DIR`)
  - cloud sync push/pull endpoints scoped by Clerk `sub`
  - no shared family token required

## Configure credentials later

Copy `.env.example` to `.env` and fill values when you are ready:

```bash
cp .env.example .env
```

Required later:

- `SPOTIFY_CLIENT_ID`

Optional but useful:

- `SPOTIFY_USER_ID` (playlist creation fallback if user id cannot be inferred)
- `SPOTIFY_REDIRECT_URI` (default desktop deep-link callback: `aftertaste://callback`)

For desktop OAuth auto-return, add `aftertaste://callback` to Spotify Dashboard redirect URIs.

## Cloud mode (Phase 1)

Cloud mode can coexist with desktop mode. Desktop remains fully usable locally.

To enable desktop -> cloud sync from the app instance:

- set `AFTERTASTE_CLOUD_SYNC_ENABLED=1`
- set `AFTERTASTE_CLOUD_API_BASE_URL` to your hosted API URL

Desktop sync auth options:

- preferred: sign in with Clerk in the app UI, then use `Sync Cloud Now` (token forwarded automatically)
- fallback: set `AFTERTASTE_CLOUD_BEARER_TOKEN` for headless/background sync from local backend

Hosted API auth options for cloud endpoints:

- Clerk JWT validation (`CLERK_AUTH_ENABLED=1` + Clerk envs)

## Cloud mode (Phase 2)

Cloud sync server now isolates data per authenticated Clerk user by storing each user's sync state and tables in a separate SQLite file under `AFTERTASTE_CLOUD_TENANT_DB_DIR`.

This avoids overlap across family accounts while keeping SQLite for local + hosted mode.

## Sparse clone (server)

From your parent repository clone, you can fetch only this folder:

```bash
git clone --filter=blob:none --sparse <repo-url>
cd <repo-dir>
git sparse-checkout set aftertaste
```

Then on server use only `aftertaste/`.

## Just commands

Use `just` to avoid manual setup steps:

- install `just` first (macOS: `brew install just`)

- `just setup` install backend + frontend deps
- `just api` run backend service
- `just web` run web UI in browser
- `just desktop` run Tauri app
- `just build` build frontend
- `just sync-cloud` trigger one local cloud sync now
- `just docker-up` build + run server container
- `just docker-logs` tail server logs
- `just docker-down` stop server container

## Web login with Clerk

Web mode uses Clerk sign-in when `VITE_CLERK_PUBLISHABLE_KEY` is set at build time.

- local web dev: set `VITE_CLERK_PUBLISHABLE_KEY` in environment before `npm run dev`
- Docker deploy: set `VITE_CLERK_PUBLISHABLE_KEY` in compose environment so build args include it
- desktop app sign-in bridge URL uses `VITE_CLOUD_SIGNIN_BASE_URL` (for example `https://aftertaste.mhirth.com`)
- optional Clerk JWT template name for API auth tokens: `VITE_CLERK_JWT_TEMPLATE`

Desktop Clerk flow:

1. In desktop app, click `Sign In In Browser`.
2. Browser opens `/#/desktop-auth` on your hosted web app.
3. Sign in there and click `Open in app`.
4. App receives a deep link (`aftertaste://clerk-callback`) and stores auth token for cloud sync.

If cloud sync returns `401 Unauthorized`, make sure desktop and server agree on Clerk token shape:

- set `VITE_CLERK_JWT_TEMPLATE` in desktop/web build env
- set matching `CLERK_AUDIENCE` on server (or leave it empty if your template has no audience)
- ensure `CLERK_ISSUER` and `CLERK_JWKS_URL` come from the same Clerk instance

Backend cloud endpoints verify Clerk JWTs with:

- `CLERK_AUTH_ENABLED=1`
- `CLERK_JWKS_URL`
- `CLERK_ISSUER`
- `CLERK_AUDIENCE` (if your token template uses audience)

## Deploy on server (Docker)

1. Sparse clone only this folder:

```bash
git clone --filter=blob:none --sparse <repo-url>
cd <repo-dir>
git sparse-checkout set aftertaste
cd aftertaste
```

2. Create server env file:

```bash
cp .env.server.example .env
```

3. Fill `.env` with Spotify + Clerk values.

4. Start server:

```bash
just docker-up
```

Server runs on `http://<server>:8765` with API + web UI served from one container.

## Backend setup (once)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Run backend service directly (optional)

```bash
source .venv/bin/activate
python -m core.api
```

Backend runs on `http://127.0.0.1:8765` by default.

## Run UI

```bash
cd app-ui
npm install
npm run dev
```

For desktop shell development:

```bash
npm run tauri dev
```

When launched via Tauri, the app auto-starts the local Python backend and stops it when the app window is closed or the app quits.

## Notes

- No Spotify secret is required: this uses Authorization Code + PKCE for a public desktop client.
- Access token is only in memory.
- Refresh token is stored in Keychain when possible.
- If you need a custom interpreter path, set `AFTERTASTE_PYTHON_BIN`.
- If your project root is non-standard, set `AFTERTASTE_ROOT`.
- Before applying any pending migrations, the app now auto-creates a backup SQLite file named like `aftertaste.pre-migrate-<timestamp>.db`.
