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
                                                         [Worker: anomalies,
                                                          forecasts, alerts,
                                                          AI insights]
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
| ML | `numpy`/`statsmodels`/`river`/`scikit-learn` | Statistical first, ML second. See below. |
| LLM (AI Insights only) | Claude API — Haiku 4.5 for summaries, Sonnet 5 for root-cause | Cheap, and separate from your Claude Code budget. |

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
4. **Multivariate** — `river.anomaly.HalfSpaceTrees` online, or nightly-retrained IsolationForest over the joint metric vector. Catches correlated weirdness no single threshold sees.
5. **Forecasting** — Holt-Winters (statsmodels) per metric, 24 h horizon with prediction intervals. Plus **time-to-exhaustion** linear/robust regression on disk and memory growth — this is the single highest-value forecast in the whole product, and the easiest.
6. **AI insights** — Claude summarises a *structured bundle* (recent anomalies, alert states, forecast breaches, correlated metrics) in plain language and suggests actions. The LLM never does the detection — it explains it.

## Alerting model

State machine per rule per device: `OK → PENDING → FIRING → RESOLVED`. `PENDING` requires the condition to hold for `for_duration` before firing. Dedup key = (device, rule). Notification channels: Web Push (VAPID), FCM (Android), SMTP email.

**Incidents** = alerts correlated by device + time window (default 10 min) + optional dependency graph. AI-suggested root cause runs on the incident's signal bundle.

## Security

- Passwords: **Argon2id** (`argon2-cffi`), never bcrypt-by-default, never anything home-rolled.
- Sessions: short-lived JWT access (15 min) + rotating refresh token in `HttpOnly; Secure; SameSite=Strict` cookie. Refresh reuse detection revokes the family.
- Agents: never see the user's password. Enrollment = user generates a one-time code in the UI → agent exchanges it for a long-lived opaque **agent token** (stored hashed, `sha256`, scoped to one device_id, revocable from the UI).
- Tenancy: every query filtered by `user_id`. Add a Postgres RLS policy as a second line of defence — one missed `WHERE` should not leak another user's infrastructure.
- Rate limits on `/auth/*` and enrollment. Agent WS messages size-capped and schema-validated.
- TLS everywhere in deployment; `wss://` only outside localhost.

## Known constraints (decide now, not in month three)

- **Android global CPU% is not readable** since API 26 — `/proc/stat` is blocked to apps. You get: per-app CPU, memory, battery/thermal, network per-uid, storage, uptime. Design the Android device page to show *those* rather than pretending it matches a Linux box.
- **Unsigned agent binaries** trigger SmartScreen (Windows) and Gatekeeper (macOS). Fine locally; budget for an Apple Developer ID (~$99/yr) and a Windows code-signing cert before anyone else installs it.
- **Seasonality-aware ML needs history.** For the first two weeks, forecasts and seasonal anomalies will be weak no matter how good the code is. The adaptive baseline (layer 2) is what carries the demo.
- **macOS temperature/fan** requires elevated helpers; treat as optional/missing.
