# Sentinel — Architecture

## System shape

```
[Agent: Win/Linux/macOS (Python+psutil)]  ─┐
[Agent: Android (Kotlin foreground svc)]  ─┼─ WSS (outbound only) ─> [FastAPI ingest]
                                           │                              │
                                           │                    ┌─────────┴─────────┐
                                           │                    │                   │
                                           │             [TimescaleDB]        [Redis pub/sub]
                                           │              (durable)            (live fanout)
                                           │                    │                   │
[Web app: React+TS]  <── WSS + REST ───────┴────────────  [FastAPI API]  <──────────┘
[Android viewer: React Native]                                  │
                                                         [Workers: alerts,
                                                          forecasts, insights,
                                                          reports]
```

Agents make **outbound** WebSocket connections only. No inbound ports, works behind NAT/corporate firewalls.

## Stack decisions

| Layer | Choice | Why |
|---|---|---|
| Agent (desktop/server) | Python 3.11+ / `psutil`, packaged with PyInstaller | One codebase covers Windows, Linux, macOS. `psutil` is the de-facto cross-platform metrics library. |
| Agent (Android) | Kotlin foreground service | Only way to sample in background on Android. |
| Backend | FastAPI + `uvicorn` | Native async WebSockets, same language as agent + ML stack, Pydantic gives you one schema source. |
| Time-series DB | PostgreSQL + TimescaleDB (Docker) | Hypertables, continuous aggregates, retention policies. Also holds users/devices/alerts — one DB, one backup. |
| Live fanout | Redis pub/sub | Decouples ingest from viewers; lets you run >1 backend process later. |
| Job queue | Celery or APScheduler + Redis | Anomaly/forecast/alert evaluation off the request path. |
| Web frontend | React + TypeScript + Vite, Tailwind + shadcn/ui | shadcn gives a professional dark ops-console look without design work. |
| Charts | uPlot (live, 1s, thousands of points) + Recharts (dashboards) | uPlot is the only thing that stays smooth for live streaming. |
| Android app | React Native (dev-build) + one Kotlin native module | Reuses ~60% of web dashboard logic; native module handles the collector service. |
| ML | `numpy`/`scipy`/`statsmodels` for layers 1-3 and 5; `scikit-learn` for layer 4 | Statistical first, ML second. See below. |
| Incident insights | Local templates over the signal bundle (`app/insights/generator.py`) | No hosted model. Deterministic, free, and it cannot state anything the bundle does not contain. |

## Data flow and sampling

The single most important design decision for cost and battery:

- Agent samples locally at **1 s** into a ring buffer.
- Default push: **10 s aggregate** (min / max / avg / p95 per metric).
- When a viewer opens Live Monitoring, backend sends `{"mode":"live"}` down the agent's socket → agent switches to **1 s raw push**. Reverts on viewer disconnect.
- Raw 1 s data → hypertable, 7-day retention. Continuous aggregates roll to 1 m (30 d), 5 m (90 d), 1 h (1 y).

## Metrics collected (v1)

- **CPU** — total %, per-core %, load average (unix), frequency, context switches
- **Memory** — total/used/available/%, swap used/%, page faults
- **Disk** — per-partition used/free/%, read & write bytes/s, IOPS, io-wait
- **Network** — per-NIC tx/rx bytes/s, packets/s, errors, drops, active connections
- **Latency** — ICMP or TCP-connect RTT to configurable targets (default gateway + 1.1.1.1 + user-defined), p50/p95, packet loss %
- **Processes** — top 10 by CPU and by memory (name, pid, %)
- **System** — uptime, boot time, logged-in users, temperatures & fan (where exposed), battery
- **Host meta** — hostname, OS, kernel version, arch, core count, total RAM, agent version

Android exposes far less — see Known Constraints.

## Analysis layers (build in this order)

1. **Static thresholds** — user-set per metric, with a `for:` duration so a 2-second spike doesn't page anyone. Ships in Phase 5.
2. **Adaptive baseline** — EWMA mean + MAD-based robust z-score, per metric per device. Online, no training, no cold start. Catches "this box is at 60% CPU and it's never above 20%". Ships in Phase 6.
3. **Seasonal residual** — STL decompose hourly rollups, flag residual outliers. Needs ~2–3 weeks of history to be meaningful.
4. **Multivariate** — a per-device `IsolationForest` (scikit-learn) over the joint metric vector, trained on that machine's own 1m history by `backend/scripts/train_novelty_model.py` and served at `GET /devices/{id}/novelty`. Catches correlated weirdness no single threshold sees: high CPU alongside an idle load average, where neither value is individually outside its own range. Built — see `app/analysis/multivariate.py`. Two rules follow this project's own posture rather than scikit-learn's: a row missing any feature is dropped, never imputed, and below 200 usable rows there is no model rather than a bad one. Scores are a percentile against the device's own training distribution, not sklearn's raw score. Training is run by hand, not by a worker.
5. **Forecasting** — Holt-Winters (statsmodels) per metric, 24 h horizon with prediction intervals. Plus **time-to-exhaustion** linear/robust regression on disk and memory growth — this is the single highest-value forecast in the whole product, and the easiest.
6. **Incident insights** — a *structured bundle* (recent anomalies, alert states, forecast breaches, correlated metrics) is rendered into plain language by `app/insights/generator.py`. Detection is layers 1–5 above; this step only explains it, and being templates rather than a model it *cannot* do otherwise. Originally Claude Haiku/Sonnet over the same bundle; replaced so the product depends on no hosted AI API. A field the bundle leaves null becomes a dropped clause, never a hedge or a zero.

## Alerting model

State machine per rule per device: `OK → PENDING → FIRING → RESOLVED`. `PENDING` requires the condition to hold for `for_duration` before firing. Dedup key = (device, rule). Notification channels: Web Push (VAPID), FCM (Android), SMTP email.

**Four rule types, one evaluation path.** `AlertRule.rule_type` discriminates `threshold` (a live reading against a fixed bound), `anomaly` (layer 2's adaptive baseline), `forecast` (layer 5's stored 24h projection) and `multivariate` (layer 4's novelty percentile). All four share one evaluator sweep, one state machine, one set of event/incident bookkeeping and one notification path — adding a rule type means a new judgment function, never a second pipeline.

**Incidents** = alerts correlated by device + time window (default 10 min) + optional dependency graph. The summary and root-cause analysis are generated from the incident's signal bundle by `app/insights/generator.py` — local templates, no hosted model. They were Claude Haiku/Sonnet until that was replaced; see CLAUDE.md's Phase 17.

## Security

- Passwords: **Argon2id** (`argon2-cffi`), never bcrypt-by-default, never anything home-rolled.
- Sessions: short-lived JWT access (15 min) + rotating refresh token in `HttpOnly; Secure; SameSite=Strict` cookie. Refresh reuse detection revokes the family.
- Agents: never see the user's password. Enrollment = user generates a one-time code in the UI → agent exchanges it for a long-lived opaque **agent token** (stored hashed, `sha256`, scoped to one device_id, revocable from the UI).
- Tenancy: every query filtered by `user_id`. Add a Postgres RLS policy as a second line of defence — one missed `WHERE` should not leak another user's infrastructure.
- Rate limits on `/auth/*` and enrollment. Agent WS messages size-capped and schema-validated.
- TLS everywhere in deployment; `wss://` only outside localhost.

## Known constraints (decide now, not in month three)

- **Android global CPU% is not readable** since API 26 — `/proc/stat` is blocked to apps. You get: per-app CPU, memory, battery/thermal, network per-uid, storage, uptime. Design the Android device page to show *those* rather than pretending it matches a Linux box.
  *Resolved in Phase 10b:* the full field-by-field decision lives in **`docs/ANDROID_METRICS.md`**, which is the authority for the Kotlin collector. Two findings worth carrying forward: "per-app CPU" turns out to mean *this app only*, so `processes[]` is sent empty rather than putting a true number under a "top processes on this machine" heading; and `TrafficStats` is device-wide rather than per-interface, so the single NIC entity is named `device-total` rather than guessing `wlan0`.
- **Unsigned agent binaries** trigger SmartScreen (Windows) and Gatekeeper (macOS). Fine locally; budget for an Apple Developer ID (~$99/yr) and a Windows code-signing cert before anyone else installs it.
  *Addressed in Phase 11:* still unsigned — no certificates have been bought — but the constraint is now
  surfaced rather than discovered. Every build carries `signed: false` through the manifest, and the
  download page quotes the exact dialog the user is about to see and gives them a SHA-256 to check
  instead. Turning signing on is configuration (`agent/build/signing.py` already looks for the env vars,
  and the CI workflow already passes the secrets through), not a rewrite. The wider packaging decision —
  that **PyInstaller does not cross-compile**, so each platform's binary comes from its own CI runner —
  lives in **`docs/PACKAGING.md`**, along with why Windows gets a Task Scheduler task rather than a real
  service and why no Android APK ships.
- **Seasonality-aware ML needs history.** For the first two weeks, forecasts and seasonal anomalies will be weak no matter how good the code is. The adaptive baseline (layer 2) is what carries the demo.
- **macOS temperature/fan** requires elevated helpers; treat as optional/missing.
