# Sentinel — Build Roadmap

Rule: **one phase per Claude Code session.** `/clear` between phases. Commit at every green state.
Each phase ends with something you can actually run and look at.

---

### Phase 0 — Foundations  ·  Sonnet 5  ·  ~1 session
Monorepo layout, `docker-compose.yml` (TimescaleDB + Redis), FastAPI skeleton with `/health`,
Vite+React+Tailwind+shadcn skeleton, `.env.example`, Makefile (`make up`, `make dev`, `make test`), CLAUDE.md.
**Done when:** `make up && make dev` serves an empty styled page and a green `/health`.

### Phase 1 — Auth + schema  ·  Opus (design) → Sonnet (build)  ·  ~1–2 sessions
Tables: `users`, `refresh_tokens`, `devices`, `agent_tokens`, `enrollment_codes`. Alembic migrations.
Argon2id, signup/login/refresh/logout, JWT + rotating refresh cookie, RLS policies.
Login & signup pages, protected routing, auth store.
**Done when:** you can sign up, log in, hard-refresh and stay logged in. Run `/security-review` before moving on.

### Phase 2 — Agent v1 + ingest  ·  Opus (protocol) → Sonnet (build)  ·  ~2 sessions
Python agent: psutil collectors, 1 s ring buffer, 10 s aggregate push, WS client with backoff reconnect,
enrollment via one-time code, config file, `--install-service` (systemd / Windows Service / launchd).
Backend: WS ingest endpoint, token auth, Pydantic validation, batch write to hypertables.
**Done when:** agent runs on this Mac, `SELECT * FROM metrics ORDER BY ts DESC LIMIT 5` shows real numbers.

### Phase 3 — Live pipeline + device list + Live Monitoring  ·  Sonnet 5  ·  ~2 sessions
Redis fanout, viewer WS, live-mode upshift signal, device list page (online/offline/last-seen),
Live Monitoring page with uPlot streaming charts.
**Done when:** real CPU/mem/net/disk/latency of a real machine streaming at 1 s in the browser.
**This is the milestone that proves the whole product.** Do not build anything else before it works.

### Phase 4 — Dashboard (command centre) + history  ·  Sonnet 5  ·  ~2 sessions
Continuous aggregates + retention policies, time-range query API, health-score computation,
fleet overview, per-device summary cards, sparklines, time-range picker.

### Phase 5 — Alerts + settings + notifications  ·  Sonnet 5, Opus for the state machine  ·  ~2 sessions
Rule CRUD, evaluator worker, OK/PENDING/FIRING/RESOLVED with `for:` duration, dedup, silences.
Alerts triage page, Settings (thresholds, anomaly sensitivity, channels). Web Push (VAPID) + email.

### Phase 6 — Anomaly detection  ·  Opus (model design) → Sonnet (build)  ·  ~2 sessions
Layer 2 (EWMA + MAD z-score) first, ship it, look at real output on your own machine, tune.
Then layer 3 (STL residual) and layer 4 (HalfSpaceTrees). Anomalies page with severity + evidence charts.

### Phase 7 — Forecasting  ·  Opus (design) → Sonnet (build)  ·  ~1–2 sessions
Holt-Winters 24 h forecasts with prediction intervals; disk/memory time-to-exhaustion.
Forecast overlay on real telemetry; "predicted breach" alerts feed the alert engine.

### Phase 8 — AI Insights + Incidents  ·  Sonnet 5  ·  ~2 sessions
Signal-bundle builder, Claude API integration (Haiku 4.5 summaries, Sonnet 5 root-cause), response caching,
strict prompt boundary (metrics are data, never instructions). Incidents workspace with correlated alerts + timeline.

### Phase 9 — Analytics + Reports  ·  Sonnet 5  ·  ~1–2 sessions
Historical trend analytics, availability/reliability stats. Report generator → PDF (WeasyPrint) + CSV export,
scheduled email reports.

### Phase 10a — Android viewer  ·  Opus 5  ·  ~1 session  ·  **DONE**
React Native dev-build viewer in `mobile/` (login/signup, device list, fleet + per-device health,
Live Monitoring over the viewer WS, alerts triage, push via FCM). Reuses `web/src`'s API client,
auth store shape and types; renders the existing API/WS as-is. The one backend addition was the FCM
registration surface — the server had no way to reach a phone at all.
**Done when:** real telemetry from a real agent streaming at 1s on a real Android device. Verified.
Known gap: FCM is untested against a live Firebase project (none configured).

### Phase 10b — Android as a monitored device  ·  Opus 5  ·  ~2 sessions  ·  **DONE**
Kotlin foreground-service collector in `mobile/modules/sentinel-collector/` plus an Expo native
module, so a phone enrols itself and reports its own metrics over the existing agent protocol —
`PROTOCOL_VERSION` unchanged at 1, and the only backend addition was exposing three battery/thermal
columns that were already stored. What Android can honestly measure was decided up front and written
down in **`docs/ANDROID_METRICS.md`**; global CPU% is never attempted, so a phone gets a health score
with the CPU and iowait components excluded and the rest renormalised.
**Done when:** a real phone's own telemetry appears beside a desktop in the same fleet, and keeps
arriving with the app closed. Verified — including survival of a task swipe and a full reboot.
Known gap: re-enrolling after "Forget this phone" creates a new device row rather than reattaching
to the old one's history.

### Phase 11 — Packaging + distribution  ·  Sonnet 5  ·  ~1–2 sessions
PyInstaller builds per OS, OS-detecting download page, install docs, signing (when you're ready to pay for certs),
final `/security-review` + `/code-review high` over auth and the agent protocol.

---

## Suggested repo layout

```
PDD_Project/
├─ CLAUDE.md
├─ docker-compose.yml
├─ Makefile
├─ docs/
├─ backend/          FastAPI: api/, ingest/, analysis/, workers/, models/, migrations/
├─ agent/            Python agent: collectors/, transport/, service/, build/
├─ web/              React + TS + Vite
├─ mobile/           React Native
└─ shared/           OpenAPI spec + generated TS types
```
