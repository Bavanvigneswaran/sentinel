# Sentinel web console

React + TypeScript + Vite + Tailwind + shadcn/ui, with uPlot for the live
charts. This is the browser half of Sentinel; see the repository root
`README.md` for how to run the project, and `docs/ARCHITECTURE.md` for how the
console relates to the API and the agents.

## Running

From the repository root, not from here:

- `make serve` — build this console and serve it together with the API on one
  port, bound to the network. **This is the only way to reach it from another
  device.**
- `make dev-frontend` — Vite dev server on :5173 with hot reload, localhost
  only. It proxies `/api` → :8000 and `/ws` → :8000, so it needs
  `make dev-backend` running alongside it.

`npm test` (vitest) and `npx tsc -b --noEmit` run this package's own checks;
`make test` and `make typecheck` from the root run them alongside everything
else.

## Notes worth knowing before editing

- **Requests carry an `/api` prefix that the server does not.** FastAPI mounts
  `/auth`, `/devices` and the rest at the root. In development the Vite proxy
  strips `/api`; under `make serve` the backend's own `ApiPrefixMiddleware`
  does. See `src/lib/api.ts`.
- **`/auth/refresh` must be single-flighted.** The backend treats a replayed
  refresh token as theft and revokes the whole family, so two concurrent calls
  log the user out. `src/lib/api.ts` and `src/stores/auth.ts` are built around
  this.
- **The access token never leaves memory** — not localStorage, not a cookie.
  A hard refresh recovers the session from the HttpOnly refresh cookie.
- **Types in `src/types/` are hand-written** to mirror `backend/app/schemas/`,
  and are generated from OpenAPI later. They are also copied byte-for-byte into
  the Android app, so keep them plain.

`CLAUDE.md` at the repository root records the phase-by-phase invariants these
notes summarise.
