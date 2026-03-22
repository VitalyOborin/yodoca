# Yodoca Web Client

Vue 3 + TypeScript frontend for the built-in `web_channel` extension.
This client is intended for local/integrated usage and is not designed as a separately deployed product.

## Run

1. Start backend:
   - `uv run python -m supervisor`
2. Start frontend:
   - `npm --prefix web install`
   - `npm --prefix web run dev`
3. Open:
   - [http://127.0.0.1:5173](http://127.0.0.1:5173)

## API Integration

Vite dev server proxies requests to backend (`web/vite.config.ts`):

- `/api/*` -> `http://127.0.0.1:8080`
- `/agent` -> `http://127.0.0.1:8080`

No backend API contract changes are introduced by this client.
Current HTTP API contract is documented in:

- `docs/api/openapi.yaml`

## Chat Flow

- Thread list and thread metadata are loaded via REST (`/api/threads`).
- Thread message history is loaded from `/api/threads/{thread_id}`.
- Sending a message uses AG-UI streaming endpoint `POST /agent`.
- Stream is considered successful only when `RUN_FINISHED` is received.
- If stream ends without `RUN_FINISHED`, client shows an explicit error (interrupted stream).

## Thread Routing Behavior

- Route and state are synchronized both ways:
  - `/chat/:threadId` updates active thread in store.
  - Store updates route when active thread changes.
- This keeps deep-linking and browser back/forward behavior consistent.

## Auth (Local Embedded Mode)

Auth token is resolved in this order:

1. Runtime token from browser storage keys:
   - `sessionStorage['yodoca.api_token']`
   - `sessionStorage['yodoca.api_key']`
   - `localStorage['yodoca.api_token']`
   - `localStorage['yodoca.api_key']`
2. `VITE_API_KEY` from env (development convenience fallback)

`VITE_API_KEY` is supported for local development only.

## Scripts

- `npm --prefix web run dev` - start dev server
- `npm --prefix web run build` - type-check + production build
- `npm --prefix web run lint` - eslint
- `npm --prefix web run test:run` - run tests once

