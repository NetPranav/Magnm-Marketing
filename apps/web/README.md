# apps/web

Shared React UI for Magnm Studio, built with Vite, React, TypeScript, and the `@magnm/design` monochrome design kit.

## Development

1. Start the local Python engine without a token (omit `MAGNM_ENGINE_TOKEN` so `/api/*` requests work directly in dev).
   Note: `engine/main.py` defaults to `--port 0` (ephemeral, printed in the `MAGNM_ENGINE_READY` line). Pass `--port 8787` to match the Vite proxy default, or set `MAGNM_ENGINE_URL` to whichever port you choose (for example `5050` when running `engine/server.py` directly):

   ```bash
   python3 engine/main.py --port 8787 --data-dir /tmp/magnm-dev
   ```

2. Install dependencies and start the Vite dev server (proxies `/api` to `MAGNM_ENGINE_URL`, default `http://127.0.0.1:8787`):

   ```bash
   pnpm --dir apps/web install
   pnpm --dir apps/web dev
   ```

   To point at a different engine port:

   ```bash
   MAGNM_ENGINE_URL=http://127.0.0.1:5050 pnpm --dir apps/web dev
   ```

## Scripts

- `pnpm --dir apps/web typecheck` — run `tsc --noEmit`
- `pnpm --dir apps/web test` — run Vitest unit tests (`vitest run`)
- `pnpm --dir apps/web build` — typecheck and bundle static assets into `apps/web/dist/`

When `apps/web/dist/index.html` exists, the Python engine serves it on `/` and `/index.html`, and keeps the legacy dashboard available on `/legacy`.

## Folder Layout

```
apps/web/
  index.html              Document shell and pre-mount theme bootstrap
  vite.config.ts          Vite + Vitest config, @magnm/design alias, /api proxy
  tsconfig.json           Strict TypeScript config
  src/
    main.tsx              Applies stored theme and mounts React root
    App.tsx               Top-level state, SSE wiring, 401 session guard, routing
    router.ts             Hash router (#/today, #/review/<id>, ...) and section registry
    api.ts                Typed fetch wrapper (credentials: same-origin, 401 handler)
    events.ts             EventSource(/api/events) hook with backoff reconnect
    studio.css            Responsive shell layout styles using @magnm/design tokens
    components/
      StudioShell.tsx     Desktop sidebar (>= 900px) and mobile sticky tabs (< 900px)
    screens/
      TodayScreen.tsx     Engine status summary and live recent jobs list
      PlaceholderScreen.tsx  Placeholder sheet header for upcoming sections
      SessionEndedScreen.tsx 401 session-expired screen
```
