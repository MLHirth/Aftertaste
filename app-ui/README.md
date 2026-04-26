# Aftertaste UI

React + TypeScript + Tauri frontend for the Aftertaste desktop app.

## Scripts

- `npm run dev` - run the Vite UI in browser
- `npm run build` - type-check and build frontend assets
- `npm run tauri dev` - run desktop shell against Vite dev server

The UI expects the local backend service at `http://127.0.0.1:8765` by default.

In Tauri mode, the desktop shell auto-starts the backend (`python -m core.api`) and shuts it down when the app closes.

If `VITE_CLERK_PUBLISHABLE_KEY` is set, the web UI enables Clerk sign-in and includes Clerk Bearer tokens on API requests.
