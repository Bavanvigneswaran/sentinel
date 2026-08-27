# Sentinel — Cross-platform infrastructure monitoring

Two-part system: **agents** collect real system metrics on monitored machines and push them outbound over
WebSocket; a **web + Android app** lets a logged-in user view any of their own devices remotely, with
anomaly detection, forecasting, alerting, and generated plain-English insights.

Detection is statistical and machine-learned on the user's *own* data — EWMA/MAD baselines,
Holt-Winters and Theil-Sen fits, and a per-device IsolationForest. Explanation is local templates.
**Nothing in this product calls a hosted AI API**; see the hard rules below.

Read `docs/ARCHITECTURE.md` before making design decisions. Read `docs/ROADMAP.md` to see what phase we're in.
**`docs/HISTORY.md` is the phase-by-phase record** — the reasoning behind every invariant here,
and every defect that was only found by running the thing. Reach for it when a rule below looks
arbitrary; this file wins if the two ever disagree.

## Hard rules

- **No mock/fake/placeholder metrics.** Every number on screen comes from a real agent reading a real
  machine. If something can't be measured on a platform, show it as unavailable — never synthesise it.
- **Tenancy is not optional.** Every query is scoped by `user_id`. A user can only ever see their own devices.
- **Agents never receive user passwords.** Enrollment code → opaque agent token, stored hashed, revocable.
- **No hosted AI APIs.** Nothing in this product calls a third-party model. Detection is statistical
  and always was (EWMA/MAD, Holt-Winters, Theil-Sen); *explanation* is now local templates over the
  signal bundle (`app/insights/generator.py`). Phase 8's Claude Haiku/Sonnet calls were removed —
  see the Phase 17 section. The old rule this replaces read "the LLM explains; it does not detect",
  and its spirit is now structural rather than promised: a template pass cannot detect, cannot
  speculate, and cannot state anything the bundle does not contain. Metric data is still *data* —
  no name, label or reading is ever interpreted as an instruction by anything downstream.

## Stack

Python 3.11+ / FastAPI / TimescaleDB / Redis · React + TS + Vite + Tailwind + shadcn/ui · uPlot for live
charts · Python + psutil agent (PyInstaller) · React Native (Expo dev build, React Navigation,
react-native-svg for charts) in `mobile/`, plus a Kotlin collector module in Phase 10b.

## Commands

- `make install` — create backend venv + install deps, `npm install` for web
- `make up` — start TimescaleDB + Redis via Docker
- `make serve` — **build the console and serve it + the API on one port, bound to 0.0.0.0.**
  The only command that produces something reachable from another machine.
- `make dev-backend` — FastAPI dev server on :8000 (reload, localhost only)
- `make dev-frontend` — Vite dev server on :5173 (proxies `/api` → :8000, `/ws` → :8000)
- `make migrate` — apply Alembic migrations · `make revision m="..."` — autogenerate a new one
- `make test` — backend test suite (pytest) · `make lint` · `make typecheck`
- `make db-shell` / `make redis-shell` · `make reset-db` — wipe volumes and re-migrate
- `make agent-enroll code=X4T9-K2QM-7PDR` — enrol this machine · `make agent` — run it
- `make agent-sample` — print one real sample without connecting · `make agent-status`
- `make agent-install-service` / `agent-uninstall-service` — launchd / systemd / Task Scheduler
  (add `scope=system` for a boot-time install on Linux or Windows; macOS is per-user only)
- `make agent-build` — PyInstaller binary **for this machine only** · `agent-build-check` — say which
- `make mobile-apk` — release APK; refuses without a real keystore. See `docs/PACKAGING.md`.
- `make mobile-android` — build/install/launch the Android dev build · `make mobile` — Metro only
- `make mobile-prebuild` — regenerate `frontend/mobile/android/` from `app.config.ts` · `make mobile-test`
- `make mobile-collector-test` — 40 Kotlin JVM tests, no emulator needed
- `make train-novelty` — fit the layer-4 IsolationForest per device from real history, and print
  what it trained on · `make train-novelty-report` — score against the models already on disk

## Where things stand right now

**The ML work is on `feature/ml-anomaly` and is deliberately NOT merged.** 13 commits ahead of
`review/codebase-fixes`, pushed, reviewed, left on its own branch by choice. `master` and
`review/codebase-fixes` do not have the novelty model, the fourth rule type, or migration `0015`.
If a checkout suddenly has no `app/analysis/multivariate.py`, that is why — check the branch first.

Two things live outside git and will not survive a fresh clone:

* **`NOVELTY_MODEL_DIR=/Users/bavan/PDD_Project/backend/var/models`** is set in `backend/.env`,
  which is gitignored. Without it the API reports no novelty score at all — correctly, and with a
  reason, but it looks like the feature is missing rather than unconfigured.
* **`backend/var/models/*.joblib`** are the trained models themselves, gitignored on purpose
  (generated artifacts, rebuilt from TimescaleDB). `make train-novelty` recreates them; only
  devices with 200+ usable rows get one, which today means this Mac and nothing else.

Also outside git, unchanged from earlier phases: the release keystore in `~/.sentinel-keys/`, and
`agent/dist/` with its five published builds.

**Everything is green as of 2026-08-27.** 613 backend tests, 175 agent, 80 web, 88 mobile JS, 40
Kotlin — 996 in total — plus `make lint` and `make typecheck`. Migrations are at head (`0015`).

**Deployment is this Mac and nothing else.** `make serve` builds the console and serves it, the REST
API and the viewer socket on one origin; Tailscale Funnel gives that port a stable public
`https://` front door so the APK and remote agents keep working when the LAN address moves. It runs
in `ENVIRONMENT=prod`, which means the prod validator's four requirements are genuinely satisfied
(rate limiting on, a ≥32-byte `JWT_SECRET`, `COOKIE_SECURE=true`, a rotated `sentinel_app`
password) — and that **`/docs`, `/redoc` and `/openapi.json` all 404**, deliberately. If you need to
inspect the schema, run a dev server rather than concluding the route is missing.

The honest limits, unchanged: no code-signing certificates exist, so all four desktop builds are
unsigned and Gatekeeper/SmartScreen interrupt every install; and there is no cloud deployment — if
Tailscale or `make serve` is not running on this Mac, nothing is reachable.

### How the history is filed

**`docs/HISTORY.md` is the phase-by-phase record** — Phase 5 through Layer 4, every defect found by
running the thing rather than reading it, and the reasoning behind every invariant below. It was
split out of this file on 2026-08-27, when this file reached 184 KB and was being loaded into
context in full every session.

The division: **this file is the rules and the current state; that file is the story.** Almost every
invariant below exists because something in HISTORY.md went wrong first, so when one looks
arbitrary, the reasoning is there. When they disagree, this file wins.

The other authorities, unchanged: `docs/ARCHITECTURE.md` for design decisions,
`docs/ROADMAP.md` for what phase we're in, `docs/ANDROID_METRICS.md` for what a phone may report,
`docs/PACKAGING.md` for how anything is built or installed as a service.

## Conventions

- Files stay under ~400 lines; split rather than grow.
- SQLAlchemy ORM models in `backend/app/models/`; Pydantic wire schemas in `backend/app/schemas/`.
  The schemas are the contract — TS types in `frontend/web/src/types/` are hand-written for now and generated
  from OpenAPI later.
- All timestamps UTC, `timestamptz`, ISO-8601 on the wire.
- Async everywhere in the backend; nothing blocking on the event loop.
- Tests alongside features, not after the phase.

## Phase 1 invariants — don't break these

- **Two database roles.** The app connects as `sentinel_app` (NOSUPERUSER, NOBYPASSRLS) so RLS is
  actually enforced; `sentinel` owns the tables and is used only by Alembic, tests, and the pre-auth
  paths. A superuser bypasses RLS silently, which would make every policy decorative.
  **The test suite has a third, `sentinel_app_test`** — same attributes, its own password, created by
  the same migration 0001 from `APP_DB_ROLE`. A ROLE is cluster-wide while a GRANT is per-database,
  so `sentinel_test` being a separate database never isolated the suite from the *credential* it
  logged in with; `tests/conftest.py`'s `_no_shared_credential()` refuses to run as a role that does
  not end in `_test`, and refuses a `DATABASE_URL` whose username disagrees with `APP_DB_ROLE`.
- **`get_unscoped_session()` is for `auth_service` only.** It reaches past RLS. A test walks the AST
  of `app/` to enforce this — if you need it somewhere new, that is a design discussion.
- **Tenant scoping goes through `scope_to_user()`**, never a raw `SET LOCAL` (which cannot take bind
  parameters over asyncpg). An `after_begin` listener reapplies the GUC on every transaction, so
  scoping survives a mid-request commit.
- **The frontend must single-flight `/auth/refresh`.** The backend treats a replayed refresh token as
  theft and revokes the whole family. Two concurrent calls — which is what a `useEffect` bootstrap
  would produce under React 19 StrictMode — log the user out. See `frontend/web/src/lib/api.ts`.
- **The access token never leaves memory.** Not localStorage, not sessionStorage, not a cookie. The
  refresh cookie is HttpOnly with `Path=/` (the Vite proxy strips `/api`, so a narrower path is never
  sent back) and `secure` is off in dev because Safari drops Secure cookies over http://localhost.
- **Routes carry no `/api` prefix in FastAPI** — the dev proxy rewrites `/api/*` to `/*`, and
  production's reverse proxy must do the same.
- **Env config is absolute-path resolved.** `backend/.env` (see `backend/.env.example`). Complex
  settings like `CORS_ORIGINS` are parsed as JSON, so the brackets are required.

## Phase 2 invariants — don't break these

- **Never synthesise a metric.** A value the platform cannot measure is `None` on the wire and NULL
  in the database, and renders as "unavailable". Three real cases live in the collectors, each with
  a comment: psutil reports `cpu_freq` as `4` on Apple Silicon (not MHz), `ctx_switches` is a
  per-interval delta on macOS rather than a cumulative counter, and both obvious filters for APFS's
  hidden volumes are wrong — read-only drops the sealed `/`, and `dontbrowse` drops
  `/System/Volumes/Data` where all user data lives.
- **`app/schemas/protocol.py` is the contract.** Both the agent and the server import it. Bump
  `PROTOCOL_VERSION` for any breaking change; the server refuses a mismatch at handshake.
- **Agent frames are untrusted.** Size-capped before parsing, schema-validated before any database
  work, and samples outside the accepted time window are rejected rather than stored.
- **Ingest writes are idempotent** (`ON CONFLICT DO NOTHING`), because an agent that reconnects
  before its ack arrives will resend the batch.
- **Counters are differentiated on the agent**, not the server, so a reboot or counter wrap is
  detected and dropped instead of drawing a spike that never happened.
- **A batch leaves the agent's buffer only after the server acks it**, and a backlog is chunked into
  push-interval windows — collapsing an outage into one row would claim a resolution it never had.
- **Metric tables carry a denormalized `user_id`** behind the same composite FK as the auth schema,
  so RLS stays one indexed predicate and cannot be aimed at the wrong tenant.

## Phase 3 invariants — don't break these

- **Redis channels embed `user_id` before `device_id`** (`sentinel:metrics:{user_id}:{device_id}`,
  `sentinel:control:{user_id}:{device_id}`). A viewer subscribes using its own authenticated user_id,
  never one taken from the client, so a channel can only ever address a device inside the caller's own
  tenant even if the RLS ownership check upstream were ever bypassed. See `app/live/bus.py`.
- **The live-viewer registry is leased, not just pub/sub.** A crashed browser tab, a dropped control
  message, or a backend restart must not leave an agent stuck pushing 1s samples forever. Membership
  in `sentinel:live:{user_id}:{device_id}` expires on its own (30s TTL against a 10s viewer heartbeat),
  and the ingest-side supervisor reconciles on a periodic tick as well as on control-channel messages —
  the tick, not the message, is what makes the system self-healing. See `app/live/registry.py`,
  `app/live/supervisor.py`.
- **Publish is best-effort and happens after the durable write.** A Redis outage must never turn into
  a rejected agent batch — `write_samples` always runs first, and `publish_samples` failures are
  logged and swallowed. See `app/live/bus.py`, `app/ingest/ws.py`.
- **`LiveSupervisor` only starts after the agent's `WelcomeFrame` has been sent.** The agent's
  handshake expects exactly one reply to `hello`; an unsolicited `mode` or `ping` frame arriving first
  is a fatal protocol violation from the agent's point of view.
- **Every write to the agent or viewer socket goes through a shared `_Sender` lock.** Starlette does
  not serialise concurrent `send_text` calls, and two frames interleaved mid-write corrupt the stream
  for a client parsing each message as one JSON object. Both the ingest supervisor and the viewer
  session's several background tasks write through this lock.
- **Device status is derived, never trusted.** The ingest socket's `finally` block sets `status =
  'offline'` on graceful disconnect, but a killed backend process never runs it. `DeviceOut` and the
  viewer socket both downgrade a stored "online" to "offline" once `last_seen_at` is older than
  `DEVICE_STALE_AFTER_SECONDS` — see `derive_device_status()` in `app/schemas/devices.py`, the one
  place this rule lives.
- **The viewer WebSocket is authenticated by a single-use ticket, never the access JWT.** A browser
  cannot set an `Authorization` header on a WS handshake, and a 15-minute bearer token has no business
  in a query string, a proxy log, or browser history. `POST /ws/tickets` mints one; `GETDEL` in
  `app/live/tickets.py` makes redemption atomic and single-use.
- **A lagging viewer drops the oldest sample, never buffers.** The outbound queue per viewer socket is
  bounded (64); on overflow the oldest queued sample is discarded so a stalled tab catches up to *now*
  rather than slowly replaying a growing backlog.
- **uPlot's `setData` must be called with its default `resetScales`.** Every chart is first
  constructed with empty data — real samples haven't arrived yet — so passing `false` to preserve
  zoom/pan leaves the y-scale stuck at `[null, null]` forever, even once real data lands. Found the
  hard way verifying against the real agent: the chart held correct data throughout and simply never
  learned to scale to it. See `frontend/web/src/components/charts/LiveChart.tsx`.

## Phase 4 invariants — don't break these

- **A continuous aggregate cannot carry RLS directly.** TimescaleDB refuses `ENABLE ROW LEVEL
  SECURITY` on a continuous aggregate, and a `security_invoker` view over one still runs with the
  aggregate owner's privileges. Migration 0005's answer is a `security_barrier` wrapper view per
  tier carrying an unconditional `user_id = app_current_user_id()`, with the aggregate itself
  revoked from the app role — the app only ever queries `{domain}_{tier}`, never the underlying
  `cagg_*` relation. `tests/test_rollup_tenancy.py` is the reason to trust that answer.
- **Rollup averages are time-weighted, never avg-of-avg.** A device pushes 10s aggregates normally
  and 1s raw while a viewer watches live, so rows inside a bucket never span equal time. Every
  rollup column carries `sample_weight`, and re-aggregating the 1m rollup into 5m must give
  bit-identical numbers to aggregating raw directly — see the `wavg()` family in
  `app/models/rollups.py`. NULL is skipped, never coerced to zero, at every tier.
- **A health-score component with no reading is excluded, not zeroed — and the weights of the
  ones that did report are renormalised.** An offline device gets no score at all (band
  `"unknown"`), not a stale one; its last reading describes a moment that has passed. See
  `app/analysis/health.py` and its `unknown_health()`.
- **The fleet overview and a single device's summary are the same computation.** Both routes call
  `fleet_service.build_summaries()` with a different device set — a per-device summary is a fleet
  of one. Building either any other way would let the dashboard and a device page disagree about
  what "healthy" means.
- **A reading older than `FRESH_WINDOW_SECONDS` (90s) is not "current".** The headline numbers on
  a summary report no reading at all past that window rather than presenting a stale figure as
  live — see `app/services/metrics_read.py`, shared by the dashboard and Phase 5's evaluator.

## Phase 5 invariants — don't break these

- **The state machine is pure — no I/O, no DB — and lives in one place.** `app/analysis/alerts.py`'s
  `step()` takes the current state plus one fresh evaluation and returns the next state and whether
  *this exact tick* should open or close an event. `fire`/`resolve` are true only on the transition
  tick; a caller that re-derives them from `state` alone breaks the dedup guarantee. A rule can never
  fire on the same tick it enters PENDING, even with `for_duration_seconds=0` — that is what stops a
  single noisy sample from ever firing alone.
- **The evaluator's one unscoped query is the entire exemption.** `app.alerts.evaluator` has a
  narrowly-reasoned entry in `tests/test_unscoped_import_guard.py`'s `ALLOWED` dict for exactly one
  query — `SELECT DISTINCT user_id FROM alert_rules WHERE enabled` — because a periodic sweep has no
  request and no JWT to derive a tenant GUC from. Every read or write after that must go through a
  session scoped via `scope_to_user()`, no different from a request's own session. Widening this
  exemption to cover more than that one enumeration query is a design discussion, not a shortcut.
- **A silence suppresses notifications only, never the state machine.** The triage page always shows
  the true FIRING state; a silenced alert just never pages anyone. Resolving that ambiguity the other
  way — silencing the transition itself — would mean the UI stops reflecting reality, which is the
  one thing CLAUDE.md's "never synthesise" rule exists to prevent.
- **Notification dispatch is best-effort and per-channel, per user.** A dead SMTP server or one
  expired push subscription must never stop the sweep for anyone else — same philosophy as Phase 3's
  `publish_samples`. Every send in `app/alerts/notify.py` is individually try/except-logged; a 404/410
  from a push endpoint deletes that subscription as routine cleanup rather than retrying it forever.
- **An already-firing alert is never auto-resolved by a device going quiet.** Missing data leaves the
  prior state exactly as it was (`condition_met=None` is a no-op in `step()`) — resolving it would be
  a claim about a machine nobody is talking to, the same reasoning Phase 4's health score uses for an
  offline device.
- **A `TimestampMixin.updated_at` column needs `session.refresh()` after an UPDATE-path commit,
  before it's returned as a response.** Its `onupdate` is server-evaluated, so the in-memory ORM
  object still holds the pre-update value until refreshed; serializing it as-is raises
  `MissingGreenlet` rather than silently returning stale data. Found the hard way in the browser
  against `PATCH /alerts/rules/{id}` and `PATCH /notifications/settings` — both now refresh before
  returning. Any new PATCH-style route on a `TimestampMixin` model needs the same call.

## Phase 6 invariants — don't break these

- **A baseline is judged against, then updated — never the other way round.** Each tick,
  `app/alerts/anomaly_eval.py` computes the z-score against the `AnomalyBaseline` as it stood *before*
  this tick's value, then folds the value in regardless of the verdict. Updating first would let an
  outlier dampen its own z-score, worst exactly when it matters (a spike or step change) —
  `tests/test_anomaly_evaluator.py`'s full-lifecycle test asserts the fired event's `baseline_mean`
  against the exact pre-update arithmetic as a regression guard on this ordering.
- **A rule_type is a discriminator on one table, not two tables.** `AlertRule.rule_type` (`"threshold"`
  or `"anomaly"`) determines whether `comparison`/`threshold` are populated (enforced by the DB's
  `rule_type_fields` CHECK, and by `AlertRuleCreate`'s/`AlertRuleUpdate`'s Pydantic validators at the
  API boundary). `AlertState`/`AlertEvent` FK straight to a rule row regardless of its type — a sibling
  table for anomaly rules would have needed a polymorphic association to reuse them instead.
- **Below `WARMUP_SAMPLES` (20) observations, an anomaly rule's `condition_met` is `None`, never
  `False`.** A still-forming baseline is unknown, not "currently normal" — the same "never synthesise
  an answer you don't have" logic `step()` already applies to a missing reading. See
  `app/analysis/anomaly.py`.
- **The evaluator's existing unscoped-query exemption already covers anomaly rules; it was not
  widened.** `_enabled_rule_user_ids()` enumerates `alert_rules` regardless of `rule_type`, so no new
  entry in `tests/test_unscoped_import_guard.py`'s `ALLOWED` dict was needed. Do not add one for
  anomaly-specific logic without the same "exactly one enumeration query" justification Phase 5 used.
- **Severity is computed at read time from `z_score`, never stored.** `AlertEventOut.severity` is a
  Pydantic `@computed_field` calling `analysis.anomaly.classify_severity()`; adding a `severity` column
  would create a second, potentially-stale copy of what `z_score` already determines.
- **The state-row/event bookkeeping shared by both rule types lives in exactly one place.**
  `app/alerts/state_apply.py`'s `apply_step_result()` is the only code that opens/closes an
  `AlertEvent` from a `step()` outcome; both `evaluator.py`'s threshold path and `anomaly_eval.py`'s
  anomaly path call it rather than each maintaining their own copy, so the two rule types cannot
  silently drift on dedup or notification semantics.
- **`AnomalyBaseline` stores only current EWMA state, not history.** The Anomalies page's evidence
  chart therefore shows two distinct reference lines — the event's own snapshotted
  `baseline_mean`/`baseline_mad` (authoritative for "what normal looked like right before this fired")
  and the live `AnomalyBaseline` row's current mean (which may have drifted since) — rather than
  conflating them into one line. See `AnomalyEvidenceChart.tsx`.
- **Anomaly rules are scoped to the same 6-metric `METRICS` set threshold rules use.** Widening
  coverage to the full protocol means widening `_read_latest_values`'s hardcoded three-query shape in
  `app/alerts/evaluator.py` too — a deliberately separate, not-yet-started piece of work.

## Phase 7 invariants — don't break these

- **A forecast is judged by the evaluator, never computed there.** `app/workers/forecast_worker.py`
  is the only writer of `MetricForecast`/`ExhaustionForecast`, on its own 15-minute cadence;
  `app/alerts/evaluator.py` only reads whatever the worker last stored, exactly as it already does
  for `AnomalyBaseline`. This is what keeps a slow ETS fit from ever delaying the 15s alert sweep —
  Phase 6's "both rule types share one evaluator sweep" invariant, now extended to three rule types
  through the same sweep.
- **A forecast rule only fires while the device has a fresh live reading**, even though the
  condition it judges depends only on the stored forecast. `app/alerts/forecast_eval.py` gates
  `condition_met` on `value is not None` for exactly this reason — the same "never make a claim
  about a machine nobody is talking to" logic `analysis/health.py`'s `unknown_health()` and Phase
  5's "an already-firing alert is never auto-resolved by a device going quiet" both follow. Without
  this gate, a forecast computed while a device was still reporting could keep firing indefinitely
  after it went offline.
- **A multi-entity metric is forecast against one real entity, never a synthetic composite.**
  `disk_percent`/`packet_loss_percent` are fit against whichever single mount/target is currently
  worst — `app/services/metrics_read.py`'s `worst_entity_per_device()`, shared with the evaluator's
  own worst-of-entity logic so the two can never disagree — and that entity's name is stored on the
  row (`MetricForecast.entity`/`ExhaustionForecast.entity`). A max-across-entities-per-bucket series
  would not describe any one real mount's or target's trend, which is what a forecast is supposed to
  extrapolate.
- **`AlertEvent.rule_type` is snapshotted, not re-derived.** Phase 6's `comparison IS NULL`
  heuristic for "this was an anomaly firing" stops being sufficient once a forecast event also sets
  `comparison`/`threshold` like a threshold event does; `rule_type` is the explicit, permanent
  record instead, mirroring `rule_name`/`metric` already being snapshotted at fire time. See
  `app/models/alerts.py`.
- **Below `MIN_POINTS_TREND` (24) real buckets, a forecast is absent, not attempted.** The same
  "unknown, not synthesized" posture as anomaly's `WARMUP_SAMPLES` — `analysis/forecast.py`'s
  `fit_holt_winters()` returns `None` rather than extrapolating from too little signal, and the
  worker still upserts the row with `points=[]` so `computed_at` can say when that was last checked
  rather than the row simply not existing.
- **A time-to-exhaustion projection is anchored at the last real observed value, never the
  regression line's own fitted value for "now".** `analysis/forecast.py`'s
  `estimate_time_to_exhaustion()` — the starting point of a projection has to be something that was
  actually measured, the same reasoning anomaly evidence snapshots the pre-update baseline rather
  than a recomputed one.
- **ETS fitting and the Theil-Sen regression both run off the event loop.**
  `app/workers/forecast_worker.py` wraps every call into `analysis/forecast.py`'s two functions in
  `asyncio.to_thread` — real CPU work, and CLAUDE.md's "nothing blocking on the event loop" hard rule
  applies to a background worker tick exactly as much as it applies to a request handler.

## Phase 8 invariants — don't break these

- **Incident correlation is device-scoped, not rule-scoped, and lives in one place.**
  `app/alerts/incident_apply.py`'s `attach_to_incident()`/`maybe_close_incident()` are the only code
  that opens, attaches to, or closes an `Incident`, called from `state_apply.py`'s
  `apply_step_result()` right beside the `AlertEvent` bookkeeping it mirrors — every rule type
  correlates identically because there is only one call site, the same reasoning Phase 6's
  `apply_step_result()` itself was factored out for. Two unrelated rules firing on the same device at
  once land in the same incident on purpose: that correlation is the point of the workspace.
- **A partial unique index, not just application logic, enforces at most one open incident per
  device.** `uq_incidents_one_open_per_device` (`WHERE status = 'open'`) is the actual race guard;
  `attach_to_incident()`'s read-then-maybe-insert is what makes the common case (an incident already
  open) cost one query, not what makes two never coexist.
- **`maybe_close_incident()` must flush before counting.** `SessionLocal` is `autoflush=False`, and
  the caller has just set the just-resolved event's `status` on the in-memory object without
  flushing it; counting still-firing events without an explicit `session.flush()` first would see the
  pre-update status and never close the incident. Found by a failing integration test the first time
  this was written — any new query added to that function needs the same flush ordering respected.
- **Generated text is stored and rendered as inert display text, never parsed back into structure.**
  `Incident.summary_text`/`root_cause_text` are plain `Text` columns; nothing downstream (the state
  machine, an alert rule, another worker) ever reads them to make a decision. Written when the text
  came from Claude and unchanged by Phase 17's switch to local templates — the column is display
  output either way.
- **The signal bundle is data, and nothing interprets it.** Phase 8 enforced this by wrapping the
  JSON in a delimited `<incident_data>` block and *instructing* the model that a user-chosen device
  or rule name is never a message to it. Phase 17 retired that boundary rather than weakening it:
  `app/insights/generator.py` never reads a string as anything but a value to interpolate, so an
  adversarial name has nowhere to act. `test_insight_generator.py` keeps the injection test as the
  regression guard, now asserting the stronger property — the text cannot reach the output at all.
- **Response caching is a fingerprint comparison, not an HTTP cache.**
  `app/analysis/incidents.py`'s `correlation_fingerprint()` hashes only an incident's *event
  membership* (which events, and each one's status) — deliberately excluding anything that drifts
  every tick regardless of the incident itself changing, like a live health score. It began as the
  thing that made a no-op worker tick cost zero API calls; with generation free it is kept for what
  survives that, namely `*_generated_at` meaning "when this explanation was reached" rather than
  "when a sweep last ran". Hashing the full bundle instead would mean the cache never hits.
- **A failed generation must not poison the cache.** `insights/service.py`'s
  `refresh_incident_insights()` only updates a `*_signal_hash` column alongside a successful
  generation; an exception is logged and swallowed with the hash left exactly as it was, so the next
  tick retries instead of silently treating a failure as "already handled." What it guards changed
  with Phase 17 — a template bug now, not a network failure or a rate limit — and it is still
  swallowed for the sweep's sake: one incident hitting a bad branch must not stop the remaining
  incidents for that tenant.
- **Insights have nothing to configure, so there is no unconfigured state to degrade into.** Phase
  8's `settings.anthropic_api_key` gate in the worker and `_ai_client_dependency`'s 503 from `POST
  /incidents/{id}/regenerate` are both gone: generation is local and always available. The 503 was
  right while it existed — a user who explicitly asked for a regeneration needs to know it did not
  happen — but the only remaining failure is a bug, which belongs in a 500 rather than in a
  "not configured" message that would be untrue.
- **The insights worker's one unscoped query only enumerates; it never reads or writes incident
  content.** `app.workers.insights_worker`'s entry in `tests/test_unscoped_import_guard.py`'s
  `ALLOWED` dict covers exactly `SELECT DISTINCT user_id FROM incidents WHERE status = 'open'`, the
  same "periodic sweep has no JWT to derive a tenant GUC from" justification Phase 5's evaluator and
  Phase 7's forecast worker already established — every subsequent read or write goes through a
  session scoped via `scope_to_user()`.

## Phase 9 invariants — don't break these

- **Availability is measured from real reporting, never inferred from `Device.status`.**
  `Device.status` is present-tense with no history — there is nowhere to read "was this device online
  at 3pm last Tuesday" from directly. `app/analysis/availability.py`'s `compute_uptime()` instead sums
  the SYSTEM rollup's own `sample_weight` for the period — the real seconds the agent was actually
  reporting — as a fraction of the period's width, capped at 100% to absorb bucket-alignment slop. A
  device that never reported in the period gets 0%, never an unmeasured gap presented as either
  "online" or "offline."
- **One `build_report()` bundle feeds every surface.** `app/services/report_service.py`'s
  `ReportBundle` is rendered four different ways — the JSON analytics endpoint, the CSV export, the
  PDF export, and `ReportWorker`'s scheduled email — but computed exactly once per call, the same
  "one computation, several callers" shape Phase 4's `fleet_service.build_summaries()` established.
  Adding a fifth report surface means rendering this bundle differently, not recomputing the
  analytics a second way.
- **PDF rendering is synchronous, CPU-bound work and is always wrapped in `asyncio.to_thread`.**
  `app/reports/pdf.py`'s `render_report_pdf()` (Jinja2 render + WeasyPrint layout/rasterization) is
  not fast, and every caller — the `/reports/export.pdf` route and `ReportWorker._send_one()` —
  wraps it, the same "nothing blocking on the event loop" posture Phase 7's forecast worker
  established for its ETS fit. CSV rendering is cheap enough (string formatting only) that it does
  not need the same treatment.
- **A `ReportSchedule.cadence` is a discriminator with a paired DB CHECK**, exactly
  `AlertRule.rule_type`'s pattern from Phase 6/7: `"weekly"` requires `day_of_week` set and
  `day_of_month` null, `"monthly"` the reverse, enforced by both `ReportScheduleCreate`/
  `ReportScheduleUpdate`'s Pydantic validators at the API boundary and the DB's `cadence_fields`
  CHECK as the backstop. `day_of_month` is capped at 1–28 so a schedule can never silently skip
  February.
- **`is_due()` is pure and takes `now`/`last_sent_at` as parameters, never reads the clock itself.**
  `app/analysis/report_schedule.py`'s `is_due()` is what lets `ReportWorker`'s hourly sweep call it
  repeatedly through the day without resending: a schedule stops being due the moment `last_sent_at`
  lands on the same UTC calendar date as `now`, and becomes due again only on its next scheduled
  weekday/day-of-month. A caller that instead tracked "already sent" with its own timer would drift
  from this the first time the worker missed a tick.
- **No report is ever stored server-side.** There is no `GeneratedReport` table — a PDF or CSV is
  rendered fresh on every download and every scheduled send, the same best-effort,
  nothing-to-replay philosophy `app/alerts/notify.py` already applies to alert notifications. The
  source data (metrics, alerts, incidents) is the durable record; a rendered snapshot of it is not.
- **The report worker's one unscoped query only enumerates; it never reads report content.**
  `app.workers.report_worker`'s entry in `tests/test_unscoped_import_guard.py`'s `ALLOWED` dict
  covers exactly `SELECT DISTINCT user_id FROM report_schedules WHERE enabled`, the same "periodic
  sweep has no JWT to derive a tenant GUC from" justification every prior worker's exemption used —
  every subsequent read or write goes through a session scoped via `scope_to_user()`.
- **WeasyPrint needs system libraries `pip install` does not provide.** Pango, cairo, gdk-pixbuf, and
  HarfBuzz must be installed at the OS level (`brew install pango` on macOS pulls in all four) before
  `import weasyprint` succeeds at all — a missing dependency here fails at import time, not at
  render time, so it surfaces as the whole backend refusing to start rather than a report-specific
  error.

## Phase 10a invariants — don't break these

- **The access token never touches disk on the phone either.** `frontend/mobile/src/stores/auth.ts` is a
  plain zustand store, deliberately *not* wrapped in `zustand/middleware/persist` and never written
  to AsyncStorage or SecureStore. "Kill the app and stay signed in" works the same way "hard-refresh
  and stay logged in" works in the browser: the HttpOnly refresh cookie is replayed by the platform
  cookie jar (Android: RN's OkHttp stack via `android.webkit.CookieManager`) and exchanged at launch.
  Verified by `am force-stop` followed by a cold relaunch landing straight on the fleet screen.
- **`bootstrapAuth()` is called at module scope in `index.ts`, never from an effect.** Identical
  reasoning to the web client, and easier to trip here: StrictMode double-invokes effects, two
  concurrent `/auth/refresh` calls present the same cookie, and the backend correctly reads the
  second as theft and revokes the family. Refresh is single-flighted in `lib/api.ts` on top of that.
- **A network failure is not a logout.** `lib/api.ts` throws `NetworkError` for an unreachable
  backend and returns `null` only when the server actually rejected the cookie; `renew()` keeps the
  session on the former. Collapsing the two would sign a user out every time they walked into a
  tunnel — the same "never make a claim about something you cannot reach" reflex as Phase 5's
  "an already-firing alert is never auto-resolved by a device going quiet".
- **Backgrounding drops the viewer socket; it does not merely stop reading it.**
  `liveSocket.suspend()`/`resume()` plus the `AppState` handler in `hooks/useDeviceStream.ts`. A
  suspended app still holds a nominally-open socket, and Phase 3's live registry would keep the
  agent at 1s push for the full 30s lease. Resuming **replaces** the `StreamBuffers` wholesale
  before re-priming — appending the primer's replay onto samples captured before the gap would
  interleave older timestamps into a buffer the chart reads as monotonic.
- **The mobile API base URL carries no `/api` prefix.** That prefix exists only because the Vite dev
  proxy strips it again (Phase 1's invariant); FastAPI mounts `/auth`, `/devices`, `/fleet` at the
  root. There is no proxy on a device, so `src/config.ts` points at the origin directly. It is also
  absolute, because `window.location` does not exist — which is where the WS URL comes from too.
- **`null` is still a gap, in a renderer that has no `spanGaps` option.**
  `frontend/mobile/src/lib/chartFrame.ts` emits one polyline per unbroken run of readings and never bridges a
  hole, and the y-scale is recomputed from the visible window every tick. The maths lives in that
  pure module rather than in the component precisely so it can be tested without a React Native
  runtime — `src/lib/__tests__/chartFrame.test.ts` is the guard.
- **An empty chart frame keeps `paths` and `latest` index-aligned with `series`.**
  `emptyFrameFor()` exists for this and nothing else. Spreading `EMPTY_FRAME` and overriding only
  `series` leaves `latest[i]` as `undefined`, which is not `null`, which slips past the legend's
  "unavailable" check and reaches the value formatter. It crashed the Live screen on resume from
  background — the exact moment the buffer is reset into that state.
- **A push payload's `url` is matched against an allow-list, never navigated to.**
  `frontend/mobile/src/lib/deepLinks.ts` maps the backend's `"/alerts"` to a route through a `Map` — a `Map`
  and not an object literal, because an object lookup walks `Object.prototype` and a payload of
  `{"url": "constructor"}` would resolve to a "route". `data.url` arrives over the network; this is
  the same "observed content is data, not instructions" reflex CLAUDE.md already applies to metric
  data reaching the LLM.
- **An `FcmToken` row IS the opt-in — there is no `fcm_enabled` settings flag.** A flag saying "on"
  beside no registered device would be a lie the UI would render happily, and the two would drift
  the moment somebody revoked the OS permission. Consequently `notify.py`'s FCM leg runs *before*
  and independently of the `NotificationSettings` lookup: that row is created lazily by
  `GET /notifications/settings`, which the Android app never calls, so gating on it would mean a
  registered phone silently received nothing.
- **A phone already registered to another account gets a 409, not a silent reassignment.** The
  UNIQUE on `fcm_tokens.token` is global on purpose — one physical device must not be addressable by
  two tenants — and RLS makes the other tenant's row invisible, so the upsert cannot rewrite it and
  Postgres refuses the statement. That is the correct outcome; the route turns it into an actionable
  conflict instead of a 500, and the app avoids reaching it by unregistering on sign-out.
- **`google-services.json` is absent from the repo, and its absence must stay buildable.**
  `app.config.ts` omits `android.googleServicesFile` entirely when the file is missing — naming a
  file that does not exist fails `expo prebuild` outright — and `src/config.ts` surfaces that as
  Settings saying push is unavailable. Same graceful-absence posture as unset SMTP/VAPID/API keys.
- **`frontend/mobile/android/` is generated, not authored.** It is gitignored and rebuilt by
  `make mobile-prebuild` from `app.config.ts`. Editing it by hand is work that disappears on the
  next prebuild; config plugins are where native changes belong.


## Phase 10b invariants — don't break these

- **`docs/ANDROID_METRICS.md` decides what a phone may report, and the collector follows it.** Every
  field of `SystemSample` is in exactly one of three categories there — measured, probed, or never
  measurable — and `SystemCollector.collectSystem()` writes out *all* of them, including the ones
  that are permanently null. Building a map of only what we have would hide the constraint instead
  of stating it; the file reads as the full inventory on purpose. If the two ever disagree, the
  collector is wrong.
- **Global CPU% is never attempted, not attempted-and-fallen-back-to.** `/proc/stat` is denied to
  apps from API 26 and there is no replacement, so `ProcFs` does not open it. The reasoning is worth
  keeping: a fallback that fires on every real device but not on an emulator is a path that never
  gets tested, and a CPU column that means "readable on this particular kernel" is worse than one
  honestly empty everywhere. The intended consequence — an Android device scored on memory, disk,
  swap and packet loss with the weights renormalised — is what `analysis/health.py` was already
  built to do.
- **A readable file is not the same as a real measurement.** `ProcFs.parseCpuFrequencyMhz()` bounds
  `scaling_cur_freq` at 100 MHz because the emulator's stub returns a literal `2`, which divides
  into a perfectly plausible-looking `0.002 MHz` and reaches the database unchallenged. Same bug
  class as psutil's `cpu_freq == 4` on Apple Silicon (Phase 2). Any new `/proc` or `/sys` probe
  needs a plausibility bound as well as a parse.
- **The top-processes list is empty, and that is deliberate.** `/proc/self/stat` is readable, so
  `processes[]` is technically fillable — with exactly one entry, this collector. The protocol's
  `processes[]` means "top N on *this machine*" and the UI renders it under that heading, so
  sending our own process as rank 1 would put a true number under a false claim. Same for
  `disk_io[]`: an empty list, never entries full of zeroes.
- **A metric whose scope differs from a desktop's says so in its name.** `net[]` carries one entity
  called `device-total`, not `wlan0` — `TrafficStats` counts every interface together and labelling
  it Wi-Fi would claim traffic went somewhere it may not have. `temperature_celsius` is the battery
  thermistor, and the UI labels it "Battery temp" rather than "Temperature".
- **`resolution_seconds` is a fact about when a sample was taken, not about the mode it is pushed
  in.** A phone samples at 10s normally and 1s in live mode, so — unlike the Python agent, where the
  cadence is constant — the buffer can hold both at once. `Batching.build()` groups it into runs of
  equal stamped resolution first; relabelling a 10s sample as a 1s point because live mode happens
  to be on would claim a resolution the reading never had, which is the same class of lie as
  synthesising a value.
- **A cadence change must interrupt the sample loop, not wait it out.** `CollectorEngine`'s sampler
  waits on `withTimeoutOrNull(…) { cadenceChanged.receive() }`, never a bare `delay()`. Changing the
  interval does not shorten a sleep that has already started, and the symptom is Live Monitoring
  showing nothing for a full ten seconds after the upshift the log says happened instantly.
- **The agent token is sealed by the Keystore, never stored in plain SharedPreferences.**
  `TokenStore` encrypts with AES-256-GCM under a non-exportable `AndroidKeyStore` key with
  `setRandomizedEncryptionRequired(true)`; only the base64 IV+ciphertext is persisted. No
  `setUserAuthenticationRequired` — the collector has to start at boot and keep running with the
  screen locked, which is exactly when the phone is most likely to be the thing worth monitoring. A
  failed unseal means the key is gone (data cleared, restored to different hardware, keystore
  invalidated) and is reported as "not enrolled", never retried against a dead credential.
- **The foreground service type is `specialUse`, not `dataSync`.** From Android 15 a dataSync FGS is
  capped at roughly six hours of runtime per day and then stopped until the app is foregrounded. A
  monitoring agent that goes deaf every afternoon is not a monitoring agent. The subtype string in
  the module's manifest is the written justification that type requires.
- **`POST_NOTIFICATIONS` is requested when collecting starts, not at launch.** Without it the
  persistent notification is suppressed and the service runs *invisibly* — the precise situation the
  notification exists to prevent. It is asked for, never required: refusing to monitor over a
  notification permission would be the wrong trade, and Android lets the user turn it back on later.
- **`BootReceiver` restarts the collector only when the user had it running.**
  `CollectorConfig.enabled` is set by an explicit start and cleared by an explicit stop; a service
  that resurrected itself after the user turned it off would be malware behaviour. `BOOT_COMPLETED`
  is delivered post-unlock, which is what `TokenStore` needs — the Keystore is unavailable before
  first unlock, so an earlier direct-boot start could not read the token anyway.
- **Only `SentinelCollectorModule` knows React Native exists.** The service, engine, socket and
  collectors run without it, which is what lets monitoring continue when the JS runtime is torn
  down. The service shares the app's process (not a `:collector` one) because that isolation is a
  property of the code rather than of the process, and a process boundary would put
  SharedPreferences — which is explicitly not coherent across processes — between the service and
  the module that reports its status. `android:stopWithTask="false"` is what keeps it alive when the
  task is swiped away.
- **Enrollment from the app hands over a code, never the JWT.** The signed-in app mints a
  short-lived single-use enrollment code with its own access token and passes *that* to the native
  module, which redeems it for an opaque, device-scoped, revocable agent token. This preserves
  ARCHITECTURE.md's "agents never see the user's password" separation while removing the copying;
  the collector never touches the access token.
- **The per-device screen is chosen by `device.platform`, and the gap is stated rather than left
  blank.** `frontend/mobile/src/lib/deviceReadings.ts` is pure and tested for the same reason
  `chartFrame.ts` is. The distinction it encodes: a key stays on the grid when the platform *could*
  report it and happens to be null right now (real information — nothing measured inside the
  freshness window), and is dropped only when there is never going to be anything to draw. Both the
  phone's screen and the web console's history page name what Android cannot measure, because a
  screen that is simply shorter reads as a broken agent.
- **Per-sample `mem_percent_min`/`max` are null on Android, and nothing is lost by it.** One
  instantaneous reading cannot describe a 10s window's extremes, so claiming it as the min would be
  a synthesised answer. The spread survives anyway: the rollups compute
  `lowest("mem_percent", "mem_percent_min")`, folding in the raw gauge, so the 1m tier the charts
  and reports actually read carries real min/max across the minute. Verified against a live phone.


## Phase 11 invariants — don't break these

- **PyInstaller does not cross-compile, and nothing may pretend otherwise.** The spec takes no
  target, `build/build.py` offers no `--target` and prints the target it is about to build before it
  builds, and there is no `make agent-build-windows`. A Makefile target that silently only ever
  works on the machine it was written on is worse than no target — the four-runner matrix in
  `.github/workflows/agent-build.yml` is the answer, and `ubuntu-22.04` is pinned there as the
  *oldest* supported runner because a PyInstaller binary links against the glibc it was built on.
- **`docs/PACKAGING.md` is the authority for how anything is built, signed, or installed as a
  service**, the way `docs/ANDROID_METRICS.md` is for what a phone may report. If the code and that
  file disagree, the code is wrong.
- **The build manifest is validated, not trusted.** It is written by CI on a machine the server
  never sees, so `download_service._parse_build()` drops any entry naming an unknown OS/arch or a
  `filename` that is not a plain filename, and a wrong `schema_version` takes the whole file out of
  service with a reason the page renders. That filename check is what lets `resolve_artifact()`
  treat the catalogue as an **allow-list** — a name is never joined into a path until it has matched
  an entry by exact string equality, with a containment check after it as the second line of defence
  against a symlink planted in the dist directory.
- **`signed: false` is recorded and rendered, never omitted.** An unsigned binary means Gatekeeper or
  SmartScreen will interrupt the install, and the page quotes the dialog verbatim *before* the click
  rather than leaving the user alone with it. No certificates exist; enabling signing later is
  configuration (`build/signing.py` already reads the env vars, the workflow already passes the
  secrets) and not a rewrite. Corollaries worth keeping: **no
  `SENTINEL_WINDOWS_SIGN_PASSWORD`** — signtool would take a .pfx password on the command line,
  putting a signing key's password in the process table and every CI log — and **UPX stays off**,
  because packing is a strong heuristic signal to antivirus and SmartScreen and an already-unsigned
  monitoring agent should not also look packed.
- **An unset `AGENT_DIST_DIR` degrades visibly.** `/download` says no build exists and gives the
  from-source command; it never offers a link that 404s. Same posture as unset SMTP/VAPID/FCM. `DownloadCatalog.unavailable_reason` is the load-bearing field — an empty
  build list with no reason is indistinguishable from one that failed to load, and the page would
  have nothing honest to say. Those reason strings are rendered verbatim, so they never name an
  absolute server path: the path goes to the log, which is where the operator reads it.
- **A Mac's architecture is not derivable from a browser.** Safari and Chrome both report
  `Intel Mac OS X` on Apple Silicon, permanently, for compatibility, so `platform.ts` returns
  `archCertain: false` and the page offers *both* Mac builds. Guessing hands half of all Mac users a
  binary that cannot run — a worse outcome than asking which Mac they have.
- **Where the agent token lives is a scope decision, never `Path.home()`.** A systemd system unit
  runs as root and a Windows `/RU SYSTEM` task's profile is
  `C:\Windows\System32\config\systemprofile`. `paths.config_dir(scope)` resolves it, the installer
  writes the resolved path into the unit/plist/task, and the running service never re-derives it.
  Deliberately **not** XDG-aware on Linux/macOS (`~/.config/sentinel` is what Phase 2 shipped and
  relocating it would strand existing agents), and **`%LOCALAPPDATA%` not `%APPDATA%`** on Windows,
  because a device-scoped token must not roam onto another machine.
- **The token is written through a descriptor whose permissions were already verified.**
  `paths.open_private()` returns an fd, not a path: reopening by name after locking down would let
  the entry be swapped for a symlink in between, landing the token on the symlink's target with the
  0600 check having passed against a file no longer there. On Windows that window is unavoidable
  (icacls takes a path, not a handle) so the file is created empty, locked, then reopened — the
  important half still holds, because a failure to restrict happens before any token exists on disk.
  On Windows the ACL uses **well-known SIDs**, never the localised names "SYSTEM" and
  "Administrators", and `/inheritance:r` is the load-bearing flag: without it the file keeps
  ProgramData's "Users: read" ACE.
- **Enrollment refuses plaintext to a remote host; `run` only warns.** Enrollment is the one exchange
  carrying both the single-use code and the long-lived token it becomes, so `http://` there hands
  anyone on the path a credential good until somebody revokes it. `run` warns instead, because
  killing an already-working monitor over a transport choice its operator already made would be the
  wrong trade. Loopback is exempt or every developer would just disable the guard, and
  `config.is_loopback()` treats anything not *provably* loopback as remote.
- **Every service definition renders purely, and is asserted through the real argument parser.**
  This project is developed on a Mac; a systemd unit or a Task Scheduler XML that is only inspected
  by eye is exactly the thing that looks right and fails on the target. `--config` is a global option
  and must precede `run`, or the service manager retries the failure forever — so
  `tests/test_service_units.py` parses each rendered command back rather than asserting the strings
  are present. `agent_command()` lives in `service/base.py` so all three cannot drift, and so a
  PyInstaller build registers `sys.executable` itself rather than a venv console script that is not
  there.
- **A missing system tool is a failed run, not a traceback.** Not every Linux has systemd (a
  container, WSL1, Alpine). `_run()` in each installer converts `OSError` into a returncode-127
  `CompletedProcess`, so `sentinel-agent status` answers "not installed" instead of dying — an
  `OSError` is not a `ServiceError` and the CLI's handler would not have caught it.
- **Two systemd directives are load-bearing in opposite directions.**
  `RestrictAddressFamilies` **must** include `AF_NETLINK` — psutil's per-NIC counters go through
  `getifaddrs()`, which is a netlink socket, and omitting it yields an agent that connects fine and
  reports no network metrics at all. `ProtectProc=` must **not** be set: `invisible` hides
  `/proc/<pid>/` for processes we do not own, which is precisely what the top-processes collector
  reads. The system unit's empty `CapabilityBoundingSet` is what makes `User=root` mean "owns its
  config file" rather than "can do anything".
- **Windows Task Scheduler defaults would break the agent, and it discards its output.**
  `StopIfGoingOnBatteries`/`StopOnIdleEnd` default true and `ExecutionTimeLimit` defaults to three
  days; all three are overridden. And because a task's stdout goes nowhere — unlike launchd's plist
  redirect or systemd's journal — the installer passes `--log-file` so Windows is not the one
  platform where a failing agent leaves no record.
- **`RATE_LIMIT_ENABLED=false` is refused in prod.** `.env.example` ships it false for real dev
  reasons (the Vite proxy puts every request in the 127.0.0.1 bucket), which is exactly why a
  deployment that copies it and flips `ENVIRONMENT=prod` must not start: `/auth/login` guards a
  password and `/enroll` is the only unauthenticated write in the system.
- **The download endpoints are authenticated.** The binary is not secret, but making it public would
  add a second unauthenticated route beside `/enroll` — which this codebase keeps as the only one —
  for no gain, since an agent is useless without an enrollment code and minting one requires signing
  in. `AGENT_DOWNLOAD_BASE_URL` is the escape hatch for scale, and the page switches to a plain
  anchor for an absolute URL rather than trying to fetch it with a bearer token it has no business
  sending cross-origin. For a same-origin build, the anchor carries a short-lived, filename-scoped
  ticket (`app/services/download_tickets.py`) instead of a bearer token — `CurrentUserOrNone` in
  `app/api/deps.py` accepts either, so a real `<a download>` click still authenticates without an
  Authorization header a plain link can't send. Unlike the WebSocket ticket in `app/live/tickets.py`,
  this one is **not** single-use: a mobile connection resuming a stalled transfer replays the same
  URL as an HTTP Range request, and `GETDEL` would burn the ticket on the download manager's own
  first byte-range request, breaking resume before it started.
- **A same-origin download link needs its `/api` prefix added explicitly — nothing does it for you.**
  `AgentBuildOut.download_url` is deliberately unprefixed (Phase 1's "routes carry no `/api` prefix
  in FastAPI"), which is fine for `apiFetch`/`apiDownload`, since those add the prefix themselves. A
  real `<a href>` a user clicks is a browser *navigation*, not a `fetch()`, so nothing adds it
  automatically — and `WebConsoleMiddleware` runs before `ApiPrefixMiddleware` strips anything
  (`app/main.py`), so a bare `/downloads/agent/...` URL reads to it as a client-side route the SPA
  owns and serves `index.html` instead of ever reaching the download route. This shipped once and
  passed every test and every `curl` check, because `curl` doesn't send the `Sec-Fetch-Mode:
  navigate` header a real link click does — it only surfaced on a real phone, as a downloaded file
  literally named `app-release.apk.html`. Any new same-origin download-by-link needs `/api` folded
  into its `href` by hand.
- **No APK is published, and the release build must never fall back to the debug key.** `expo
  prebuild` points the release buildType at React Native's *public* debug keystore, which would let
  anyone forge an update Android accepts as the same app. `plugins/withReleaseSigning.js` swaps in a
  real key when configured — a config plugin, because `frontend/mobile/android/` is generated and gitignored
  and hand-edits vanish at the next prebuild — and `make mobile-apk` **refuses to build** when it is
  not, rather than warning and proceeding. Its gradle rewrite is split at `buildTypes` for a reason:
  a single lazy regex over the whole file starts inside the injected `signingConfigs.release` block
  and rewrites the *debug* buildType instead, silently leaving release on the public key. Passwords
  come from the environment, never `-P` gradle properties.


## Layer 4 (multivariate novelty) invariants — don't break these

- **A row missing any feature is dropped, never imputed.** A column mean or a zero is exactly the
  synthesised metric the hard rules forbid, and it is worse here than on a chart: the model would
  *learn* the fabricated value as normal and then never flag it. Below `MIN_TRAINING_SAMPLES` (200)
  usable rows there is no model rather than a bad one, matching anomaly.py's `WARMUP_SAMPLES` and
  forecast.py's `MIN_POINTS_TREND`.
- **`FEATURES` is chosen by what every desktop platform actually measures**, not by what looks
  useful. `ctx_switches_per_s` and `active_connections` sit in the same rollup table and are 100%
  populated on Windows and 0% on macOS (psutil's `net_connections()` needs root there; `ctx_switches`
  is a per-interval delta), so including either would drop every macOS row under the no-imputation
  rule — i.e. no model at all on the platform this is developed on. Measured before choosing.
- **A score is a percentile against the device's own training distribution, never sklearn's raw
  `score_samples()` output**, which is unbounded, dataset-specific and not comparable between two
  machines. `TrainedModel.score_quantiles` is what makes "more unusual than 99.4% of this machine's
  own history" sayable, and what keeps a threshold meaningful across a retrain.
- **`rule_type = 'multivariate'` and `metric = 'novelty_score'` imply each other in both
  directions**, enforced by migration 0015's `multivariate_metric` CHECK. Without the reverse
  direction a *threshold* rule could be pointed at `novelty_score`: accepted, plausible on the page,
  and silently never firing, because nothing writes that column. `RULE_METRICS` widens only the
  alert_rules CHECK — `anomaly_baselines` and both forecast tables keep the narrower one, so nothing
  can create a baseline or a forecast for what is a model output rather than a measurement.
- **A device with no model is absent from `score_devices()`, never present with a null**, so the
  state machine sees unknown rather than normal — an open alert is not auto-resolved by a model being
  retrained away, matching Phase 5's rule.
- **The model cache evicts by path, not just by mtime.** `_CACHE` is keyed `(path, mtime_ns)` so a
  retrain is picked up with no restart; without dropping the *previous* mtime for that path it is a
  leak wearing a cache's clothes, because an unpickled forest is ~26 MB and `make serve` runs for
  weeks. Nothing can ever ask for an mtime the file no longer has.
- **`joblib.load` is offloaded to a thread and scoring is not, and that asymmetry is measured
  rather than assumed.** `to_thread` only pays off for work that releases the GIL: `joblib.load`
  does file I/O (9.8ms inline vs 4.2ms offloaded), while sklearn's `score_samples()` holds the GIL,
  so offloading it made a 50-device sweep *worse* (91.7ms → 109.6ms of stalled loop). Scoring cost is
  per *model*, not per row — 1.76ms for one row and 1.75ms for fifty — so a sweep pays ~1.8ms per
  device that has both a model and a multivariate rule. Do not "fix" this with another `to_thread`;
  at fifty devices it wants a process pool or a different schedule.
- **Ruff's `ASYNC` rules cannot see any of this.** They fire on known-blocking calls written
  directly inside an `async def`, and every blocking call here sits in a *sync* helper that async
  code calls. A clean lint run is not evidence that nothing blocks the loop.
- **`joblib.load` is pickle, and that is acceptable only because these files are operator-written.**
  They are never uploaded, never user-supplied, never fetched over a network. If that stops being
  true, this needs a serialisation format that is not pickle — not a sandbox.
- **Training is `make train-novelty`, run by hand, and lives in `backend/scripts/` rather than
  `app/`** because it enumerates every device across every tenant — precisely what
  `tests/test_unscoped_import_guard.py` exists to keep out of `app/`.
- **A new rule type has to reach every display surface, and there are more of them than anyone
  expects.** The multivariate type needed nine: the rule form and rules list on both clients, the
  insights generator, `notify.py`, and the alerts triage and incident timeline on both clients. Four
  of the nine were found by looking at a running page rather than by a test. Both clients now route
  event copy through `lib/alertCopy.ts` so a tenth surface cannot drift; add new wording there, not
  inline in a page.
