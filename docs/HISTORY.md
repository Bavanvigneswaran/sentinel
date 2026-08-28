# Sentinel — project history

The phase-by-phase record of how this codebase got here: what was built, what broke, and what
running it for real taught that reading it did not. `CLAUDE.md` keeps the rules, the invariants
and the current state; this file keeps the story behind them.

**Read this when you need the *why*.** Almost every invariant in CLAUDE.md was written because
something here went wrong first. When an invariant looks arbitrary, the reasoning is in this file —
search for the phase that introduced it. When the two disagree, CLAUDE.md is current and this is
archive.

Roughly chronological: Phase 5 through the Tailscale Funnel work, then Phase 15 onward.

---

## Phases 5–13: the status log

Phase 5 status: alerts, the evaluator, and notifications complete on top of Phase 4's dashboard.
Continuous aggregates at 1m/5m/1h behind a tenant-scoped wrapper view, a time-range query planner,
and a dull-by-design health score (`app/analysis/health.py`) drive the fleet overview and per-device
history (`app/services/fleet_service.py`, `app/services/series_service.py`). Frontend: `/` is the
real fleet dashboard; `/devices/:id/history` has a time-range picker over real charts; `/settings` has
notification channels. Verified in-browser: settings round-trip (email toggle, override address,
sensitivity), and Web Push's permission/support states.

Phase 6 status: Layer 2 adaptive-baseline anomaly detection (EWMA + MAD z-score) complete, feeding the
same alert pipeline as a second rule source, per the roadmap's "ship it, tune, then layer 3/4" staging —
layers 3 (STL residual) and 4 (HalfSpaceTrees) are not started. `AlertRule.rule_type` discriminates
threshold rules (unchanged from Phase 5) from anomaly rules, which leave `comparison`/`threshold` null
and are judged against a per-`(device, metric)` `AnomalyBaseline` row instead
(`app/analysis/anomaly.py`, `app/alerts/anomaly_eval.py`). Both rule types share one evaluator sweep
(`app/alerts/evaluator.py`) and the same pure OK/PENDING/FIRING state machine
(`app/analysis/alerts.py`); the state-row/event bookkeeping common to both is factored into
`app/alerts/state_apply.py`. A fired anomaly event snapshots `observed_value`/`baseline_mean`/
`baseline_mad`/`z_score`; severity is a read-time `@computed_field` on `AlertEventOut`
(`classify_severity()`), never stored. 317 backend tests green. Frontend: `/alerts` triages both rule
types (an anomaly event has `comparison: null`); `/alerts/rules` has a Threshold/Anomaly toggle;
`/anomalies` is a filtered view of the same event stream with an expandable evidence chart
(`AnomalyEvidenceChart`) drawing the real metric trace plus the baseline-at-fire and current-baseline
reference lines; `/settings`'s anomaly-sensitivity preference is now live. Verified in-browser: create
an anomaly rule, edit it (PATCH round-trips), page rendering and nav, no console errors — the one step
not exercised this session is watching a real spike fire end-to-end against a live agent (the
evaluator's background loop and a multi-minute warmup are outside a single interactive check; the
full lifecycle is covered instead by `tests/test_anomaly_evaluator.py` against a real Postgres/agent
write path).
Still to come in Phase 6, if picked back up: layer 3/4 detectors, and widening anomaly rules past the
6-metric `METRICS` set they currently share with threshold rules.

Phase 7 status: Holt-Winters 24h forecasting and disk/memory time-to-exhaustion complete. A new
periodic `ForecastWorker` (`app/workers/forecast_worker.py`, 15-minute default cadence — CPU-bound
unlike the 15s alert sweep) fits `statsmodels`' `ETSModel` (additive damped trend, daily-seasonal
once there are ≥2 days of hourly buckets) and a Theil-Sen regression per `(device, metric)` across
the same 6-metric `METRICS` set anomaly rules use, storing the result in two new lazily-upserted
tables, `MetricForecast`/`ExhaustionForecast` (`app/models/forecasts.py`,
`app/analysis/forecast.py`). `rule_type` gains a third value, `"forecast"` (migration
`0008_forecast_rules`): same `comparison`/`threshold` shape as `threshold`, but judged against the
stored 24h-ahead forecast instead of a live reading, by `app/alerts/forecast_eval.py` — sharing
`evaluator.py`'s one sweep and `state_apply.py`'s bookkeeping with the other two rule types rather
than adding a second evaluation path. `AlertEvent` now snapshots `rule_type` explicitly (retiring
the `comparison IS NULL` anomaly heuristic, which a forecast event's non-null comparison would
otherwise start to erode) plus `predicted_breach_at`/`predicted_value` evidence. 330 backend tests
green. Frontend: `DeviceHistoryPage`'s CPU/Memory/Disk-usage charts overlay the forecast as a dashed
continuation plus a thin prediction-interval band, colour-matched to the real series they extend
(`HistoryChart`'s new `forecast` prop); a "time to capacity" card sits beside the health card
(`ExhaustionSummary`); `/forecasts` is a new fleet-wide page (soonest-to-exhaust list, predicted-
breach alert history) with its own nav item; `RuleForm` gets a Threshold/Anomaly/Forecast toggle.
Verified in-browser end-to-end against seeded history in the dev database, including one real
surprise: a manually backfilled history needed one manual continuous-aggregate refresh
(`CALL refresh_continuous_aggregate(...)`) to appear in the 1h rollup the worker reads, because
real-time aggregation only covers data *after* the aggregate's last materialization watermark — a
live agent's forward-only writes never hit this, but a bulk backfill does. Also verified: a forecast
rule created via the UI, and its full pending→firing lifecycle observed live through the real
evaluator loop with the correct predicted-vs-live values shown on `/alerts`.

Still to come in Phase 7, if picked back up: a filled prediction-interval band on the chart overlay
(currently two thin dashed bounds — a deliberate scope trim, not a limitation of the stored data);
feeding forecast/exhaustion output into Phase 8's AI insights signal bundle.

Phase 8 status: AI insights and an incidents workspace complete. AlertEvents now correlate into a
device-level `Incident` the moment any of the three rule types fires with none already open for that
device, and the incident closes once every attached event has resolved — `app/alerts/incident_apply.py`,
called from `state_apply.py`'s existing `apply_step_result()` choke point rather than a new evaluation
path, so all three rule types correlate identically (migration `0009_incidents`). A new
`app/services/signal_bundle.py` assembles one incident's correlated events, current health score, and
the anomaly-baseline/forecast rows for whichever metrics were actually involved into a JSON-able
`SignalBundle`; `app/ai/prompts.py` turns that into two prompts — Haiku writes a one-sentence summary,
Sonnet writes a deeper root-cause analysis — behind an explicit `<incident_data>...</incident_data>`
boundary instructing the model that everything inside is inert telemetry, never instructions, straight
from CLAUDE.md's hard rule. `app/ai/insights_service.py`'s `refresh_incident_insights()` is the one
function both the new periodic `InsightsWorker` (`app/workers/insights_worker.py`, 60s cadence, same
one-unscoped-enumeration-query shape as the other two workers) and the manual
`POST /incidents/{id}/regenerate` route call; it fingerprints the incident's event membership
(`app/analysis/incidents.py`'s `correlation_fingerprint()`) and skips the API call entirely when nothing
correlated has changed since the cached text was generated — this fingerprint comparison, not an HTTP
cache, is the whole of "response caching." No API key configured is a graceful no-op in the worker and an
explicit 503 from the route, same posture as SMTP/VAPID being unset. 359 backend tests green. Frontend:
`/incidents` is a new fleet-wide list (Open/Resolved/All, like `/alerts`); `/incidents/:id` shows the
correlated-event timeline plus Summary/Root-cause cards and a "Regenerate insights" button; `/alerts`
event cards gained a "View incident" link. Verified in-browser end-to-end against a seeded device with
two rules firing together: they correlated into one incident, the detail page rendered the timeline
correctly, and pressing "Regenerate insights" with no `ANTHROPIC_API_KEY` configured surfaced the 503 as
an inline error rather than silently doing nothing.

**Superseded by Phase 17 in one respect — read that section before trusting the paragraph above.**
Everything here about incident correlation, the signal bundle, the fingerprint cache and the
workspace still holds. What no longer exists is the Claude API integration: `app/ai/` was replaced by
`app/insights/`, `ANTHROPIC_API_KEY` and the 503 are gone, and generation is local templates.

Still to come in Phase 8, if picked back up: the root-cause output isn't yet fed anywhere but the
incident detail page (no digest email, no push notification carrying it); the signal bundle doesn't yet
include cross-device context (e.g. "three other devices are also degraded right now"), which would need
a deliberate decision about whether an incident can ever span more than one device.

Phase 9 status: historical trend analytics, availability/reliability stats, PDF/CSV report export, and
scheduled email reports complete. `app/services/report_service.py`'s `build_report()` is the one
computation every surface renders from — the JSON `/reports/analytics` endpoint, the `/reports/export.csv`
and `/reports/export.pdf` downloads, and `ReportWorker`'s scheduled emails — exactly Phase 4's
`fleet_service.build_summaries()` "one computation, several callers" shape, so a downloaded PDF and one
emailed the same morning can never disagree about a device's numbers. It reuses rather than reimplements:
`series_service.plan_series`/`fetch_series` at a small `max_points` budget (asking the planner for the
*coarsest* bucket that still covers the whole period, since a report wants a handful of period aggregates,
not a plotted series) for the current-vs-previous-period trend math (`app/analysis/analytics.py`), and
`metrics_read.worst_entity_per_device()` for which mount/target represents a device's `disk_percent`/
`packet_loss_percent`, the same resolution Phase 7's forecast worker uses. Availability
(`app/analysis/availability.py`'s `compute_uptime()`) is *measured*, not inferred from `Device.status`
(which carries no history): it sums the SYSTEM rollup's own `sample_weight` — the real seconds of the
period the agent was actually reporting — as a fraction of the period. Reliability
(`compute_reliability()`) summarises the `Incident`/`AlertEvent` rows already in the period: count,
resolved count, mean time to resolve, alerts fired. PDF rendering (`app/reports/pdf.py`,
`app/reports/templates.py`) is Jinja2 → WeasyPrint; CSV (`app/reports/csv_export.py`) is one flat,
denormalized table via the stdlib `csv` module. `ReportSchedule` (migration `0010_report_schedules`)
carries a `cadence` discriminator (`"weekly"`/`"monthly"`) with a DB CHECK pairing it exclusively with
`day_of_week`/`day_of_month`, mirroring `AlertRule.rule_type`'s discriminator-plus-CHECK pattern from
Phase 6/7. `app/analysis/report_schedule.py`'s pure `is_due()` — given `cadence`, the day fields,
`last_sent_at`, and `now` — decides whether a schedule should fire today; the new periodic `ReportWorker`
(`app/workers/report_worker.py`, hourly cadence, same one-unscoped-enumeration-query shape as the other
three workers) calls it for every enabled schedule and emails whichever are due via
`app/reports/mailer.py`'s `send_report_email()` (SMTP, with a PDF/CSV attachment — same graceful-no-op-
when-unconfigured and best-effort-per-recipient posture as `alerts/notify.py`). There is no
`GeneratedReport` history table: nothing is ever stored server-side, matching that same best-effort,
nothing-to-replay philosophy — a report is rendered fresh on every download or send. 404 backend tests
green. Frontend: `/reports` is a new fleet-wide page — a device/period picker, Download CSV/PDF buttons,
a per-device card (uptime, incident count, mean time to resolve, alerts fired, and a current/min/max/
previous/change trend table), and a "Scheduled reports" section (create/edit/delete, plus a "Send now"
that bypasses `is_due()` the same deliberate way Phase 8's incident "Regenerate insights" bypasses its own
cache check). Verified in-browser end-to-end against a device seeded with real (sparse) history: the CPU/
Memory trend numbers matched the seeded values exactly, Swap/IO wait correctly showed "—" (never
measured, never synthesized) rather than a fabricated zero, `disk_percent`/`packet_loss_percent` were
correctly *omitted* (not zeroed) because the seeded data had no reading inside the 90s freshness window,
uptime% reflected the seeded data's genuinely sparse real coverage rather than being rounded up, both
downloads returned 200 with real content (`%PDF-...` magic bytes; a real CSV header row), and creating a
schedule then pressing "Send now" round-tripped `last_sent_at` through the real worker code path with SMTP
unset (graceful no-op on the actual send, matching `notify.py`'s posture, while still confirming state
was updated). One environment note, not a code issue: **WeasyPrint needs system libraries** (Pango,
cairo, gdk-pixbuf, HarfBuzz — `brew install pango` on macOS pulls in all four) that `pip install` alone
does not provide; without them the import itself fails before any report can render. One real surprise,
re-confirming Phase 7's finding rather than a new one: a bulk-backfilled device's history needed a manual
`CALL refresh_continuous_aggregate(...)` on *all three* rollup tiers (1m/5m/1h), not just 1m — because a
report's `plan_series` call can land on whichever tier is coarse enough for the requested period, and each
tier's continuous aggregate is refreshed independently.

Still to come in Phase 9, if picked back up: a filled prediction-interval-style visual on the trend table
(currently numbers only, no chart); the report's AI-insights context (Phase 8's incident summaries) isn't
pulled into the emailed report, which would need a decision about how much of that per-incident text
belongs in a periodic fleet-wide digest versus staying scoped to one incident's own page.

Phase 10a status: the Android viewer is complete — a React Native dev build in `mobile/` (Expo SDK
57 / RN 0.86, `expo-dev-client` so Phase 10b can add its Kotlin module without changing how the app
is run). Sign-in/sign-up, a fleet dashboard, a per-device health screen, Live Monitoring over the
viewer WebSocket, alerts triage, and FCM push. It renders the *existing* API and WS protocols — the
only backend addition is the FCM registration surface, because the server had no way to send to a
phone at all (`notify.py` did Web Push and SMTP only, and an FCM registration token is not a Web
Push endpoint). That addition is purely additive and mirrors the web-push pair it sits beside:
`FcmToken` (migration `0011_fcm_tokens`, RLS like every other tenant table), `POST`/`DELETE
/notifications/fcm/register` upserting on the token, and `app/alerts/fcm.py` sending through FCM
HTTP v1 behind `asyncio.to_thread` for google-auth's blocking refresh — a logged no-op when
`fcm_project_id`/`fcm_service_account_file` are unset, exactly as SMTP and VAPID already are. One
push payload (`{title, body, url}`) now feeds both channels. 437 backend tests green.

The app reuses `frontend/web/src` rather than reinventing it: `src/types/` is copied byte-for-byte,
and `lib/api.ts`, `stores/auth.ts`, `lib/liveSocket.ts`, `lib/ringBuffer.ts` and
`lib/streamBuffers.ts` are ports kept deliberately recognisable against their web counterparts.
What genuinely differs: no `window` (an absolute base URL and **no `/api` prefix** — that prefix is
purely a Vite-proxy artefact), no DOM (uPlot is replaced by a `react-native-svg` renderer over the
same `RingBuffer`), and an app that gets *suspended* rather than merely hidden. `AppState` replaces
`visibilitychange` for the pre-expiry token re-check, pauses every screen's poll, and — the one
genuinely new behaviour — tears the viewer socket down on background so the agent downshifts out of
1s mode immediately instead of waiting out Phase 3's 30s lease.

Verified end-to-end on a real Android 36 emulator against the real backend and a real Python agent
enrolled to a scratch account: signup, cold-start session restore, fleet numbers matching this Mac's
actual telemetry (health 87, both APFS mounts, "Not measurable on this platform: iowait"), live 1s
streaming with real NIC/disk/latency series, a threshold rule firing and appearing in triage, and
the background→foreground upshift/downshift cycle confirmed in the agent's own log (live at
11:27:57, normal at 11:28:13 — ~2s after backgrounding, not 30s). Four real defects were found by
running it that neither typecheck nor Metro caught: two safe-area bugs (a missing top inset, then a
double one — `HeaderHeightContext` is `0` under a hidden bottom-tab header, not `undefined`), a tab
glyph with no font coverage rendering as tofu, and a crash on resume from background where an empty
chart frame carried its series but an empty `latest` array, so `latest[i]` was `undefined` rather
than `null` and reached the value formatter. The last one now has a regression test.

Still to come in Phase 10a, if picked back up: **push has never been exercised against a live
Firebase project** — there is none configured here, so the app correctly reports push as unavailable
and the send path is covered by unit tests (`test_fcm_transport.py`, `test_fcm_dispatch.py`) rather
than a real notification arriving on a phone; the notification *tap* → deep-link path
(`navigation/linking.ts`) is likewise only covered by `deepLinks.test.ts`. Also deferred by choice,
not blocked: the history view with its time-range picker, incidents, forecasts and reports, and
alert-rule CRUD, all of which stay in the web console.


Phase 10b status: the Android collector is complete — the phone reports its own real metrics to the
same backend, over the same agent protocol, with **no protocol change**: `PROTOCOL_VERSION` stays at
1, `Device.platform` already carried `"android"` end-to-end, and `POST /enroll` already accepted it.
A Kotlin foreground service in `frontend/mobile/modules/sentinel-collector/` samples locally and pushes
outbound over the same WSS ingest endpoint the Python agent uses; an Expo native module exposes
`enroll`/`start`/`stop`/`status` to the RN app so a phone can enrol itself.

**`docs/ANDROID_METRICS.md` is the authority for what a phone may report**, written before the
collectors were, and every field of `SystemSample` is assigned to exactly one of three categories:
*measured* (memory, uptime, battery level/plugged/temperature, storage, network throughput, TCP-connect
latency), *probed* — attempted at runtime, null when the platform refuses (`/proc/loadavg`,
`/proc/meminfo` swap, `scaling_cur_freq`) — and *never measurable*, which is most of the CPU family
plus process count, open connections, disk IO and the process list. The visible, intended consequence
is that **an Android device gets a health score with no CPU and no iowait component**, renormalised
over the four it did report. That was verified, not assumed: a real phone scored 92 beside this Mac's
84, with `/alerts`-style "Not measurable on this platform: cpu, iowait" naming the exclusions.

The collector is a faithful port of `agent/sentinel_agent/`'s buffering, backoff and live-mode
upshift — `SampleBuffer`/`Aggregation` against `buffer.py`, `AgentSocket` against
`transport/client.py`, `CollectorEngine` against `runner.py` — kept deliberately recognisable against
their reference implementations, the same way Phase 10a's `lib/api.ts` and `lib/liveSocket.ts` are.
What genuinely differs: a phone samples at **10s normally and 1s only in live mode** (a 1s timer
running all day is the real battery cost, and every metric Android can measure is slow-moving), so
`resolution_seconds` is a *variable* here rather than the constant it is in the Python agent — which
is why `Batching` groups the buffer into runs of equal stamped resolution before doing anything else.

The token lives in `TokenStore`, sealed with AES-256-GCM under a non-exportable `AndroidKeyStore`
key; only ciphertext reaches SharedPreferences. Foreground service type is `specialUse`, not
`dataSync` — Android 15 caps a dataSync FGS at ~6 hours a day, which would make the agent go deaf
every afternoon. 36 Kotlin JVM tests (`make mobile-collector-test`, no emulator needed), 445 backend
tests, 34 mobile JS tests, 49 agent tests.

Frontend: platform-awareness landed in two places rather than letting a phone render as a
broken desktop. `mobile`'s `DeviceScreen` picks its readings grid from
`lib/deviceReadings.ts` (a pure, tested module) and states what Android cannot measure; the web
console's `DeviceHistoryPage` drops the CPU and Disk-I/O charts for an Android device, adds a Battery
chart, and captions why. The one additive API change in the whole phase is three columns that were
already stored end-to-end but had no way out of the database: `battery_percent`, `battery_plugged`
and `temperature_celsius` on `LatestReadingsOut`.

Verified end-to-end on a real Android 36 emulator against the real backend, with this Mac's agent
running beside it for comparison: one-tap enrollment (the app mints a short-lived code with its own
access token and hands it to the native module, which redeems it for an opaque agent token — the
collector never sees the JWT), real telemetry landing in TimescaleDB with every unmeasurable column
genuinely NULL, `disk_io_samples` and `process_samples` at **zero rows** rather than rows of zeroes,
the live 1s upshift and its downshift, the persistent notification, and — the core claim — collection
surviving the app being backgrounded, its task being swiped away (MainActivity destroyed, service
still `isForeground=true types=0x40000000`), and a full device reboot, after which `BootReceiver`
unsealed the token from the Keystore and reconnected as the *same* device without the app ever being
opened. Unenroll was exercised too: service stopped, sealed token gone, `enabled=false` so boot does
not resurrect it.

Three real defects were found by running it, none of which any type checker or test would have caught
first:
* **`scaling_cur_freq` returned `2`**, which the collector faithfully turned into `0.001 MHz` and
  wrote to the database. Exactly the "plausible but wrong" trap — and exactly the bug class CLAUDE.md
  already records for psutil's `cpu_freq == 4` on Apple Silicon. `ProcFs` now bounds the reading at
  100 MHz and reports anything below it as unmeasurable.
* **Opening Live Monitoring showed nothing for up to ten seconds.** The mode switched instantly, but
  the sample loop was already inside `delay(10_000)` and changing the interval does not shorten a
  sleep that has already started. The sampler's wait is now interruptible.
* **The foreground service ran invisibly.** Android 13+ gates `POST_NOTIFICATIONS` behind a runtime
  permission that nothing had asked for, so the persistent notification — the whole point of which is
  that the person holding the phone knows something is sampling it — was silently suppressed. The
  permission is now requested at the moment collecting starts.

Still to come in Phase 10b, if picked back up: **re-enrolling after "Forget this phone" creates a new
device row rather than reattaching to the old one's history** — the same behaviour the desktop agent
has, and fixable the same way (mint the code bound to the existing `device_id`, which the web console
already supports and the app does not yet use); thermal *status* (`PowerManager.getCurrentThermalStatus()`)
is shown in the phone's own notification but has no protocol field, deliberately — see
docs/ANDROID_METRICS.md for the four layers of surface that would cost; and the collector has only
been run against an emulator, so the probe results for `/proc/loadavg` and `scaling_cur_freq` on real
OEM hardware are unknown by design rather than assumed.

Phase 11 status: packaging and distribution complete — for one platform, verified, with the other
three written down rather than claimed. **PyInstaller does not cross-compile**: it ships a native
bootloader per platform and freezes the *host's* CPython, so this Mac produces a macOS arm64 binary
and nothing else. That is stated in the three places somebody could hit it — the spec has no target
selector, `build/build.py` prints `Target: macos/arm64 — PyInstaller does not cross-compile` before
it does anything and offers no `--target`, and the Makefile has `agent-build` and deliberately **no**
`agent-build-windows`. The real answer is a four-runner CI matrix
(`.github/workflows/agent-build.yml`), with `ubuntu-22.04` pinned as the *oldest* supported runner
because a PyInstaller binary links against the glibc it was built on. `agent/build/` holds the spec,
the build driver, `signing.py`, and the pure `agent_manifest.py`, whose manifest is the contract
between CI and the download page; `merge_manifests.py` is what lets four machines that never see
each other's output produce one file. All of it is in **`docs/PACKAGING.md`**, which is the authority
for how anything gets built, alongside `docs/INSTALL.md` for users.

The agent grew a scope. `sentinel_agent/paths.py` resolves the config — and the agent token in it —
from `user` or `system` rather than `Path.home()`, because a systemd system unit runs as root and a
Windows task registered `/RU SYSTEM` has its profile under
`C:\Windows\System32\config\systemprofile`; neither is the HOME of the person who typed the
enrollment code. `service/` gained `base.py` (one `agent_command()` all three installers share, which
is also what makes a PyInstaller binary register itself rather than a venv console script),
`systemd.py` and `windows.py`, behind an `installer_for()` dispatch. Every renderer — `build_plist`,
`build_unit`, `build_task_xml` — is **pure**, so a Linux unit and a Windows task are asserted from
this Mac and fed back through the real argument parser. 127 agent tests (was 49), 474 backend, 27 web
(vitest, new), 41 mobile JS, 36 Kotlin.

Windows gets a **Task Scheduler task, not an SCM service**, because a PyInstaller console binary
cannot answer the Service Control Manager and `sc.exe create` yields error 1053. Registration goes
through `/XML` rather than `/TR`: `/TR` breaks on `C:\Program Files\…` quoting and, more
importantly, cannot express `StopIfGoingOnBatteries=false`, `StopOnIdleEnd=false` and
`ExecutionTimeLimit=PT0S` — Task Scheduler's defaults would stop the agent when a laptop is unplugged
and terminate a healthy one after three days. macOS deliberately has **no** system scope: a
LaunchDaemon is root-requiring code that cannot be exercised from a test suite, so `--scope system`
is an actionable error pointing at INSTALL.md's hand-written plist.

Frontend: `/download` is a new authenticated page. `AGENT_DIST_DIR` unset is a supported state — the
page says no build exists and gives the from-source command rather than offering a link that 404s,
the same posture as unset SMTP/VAPID/FCM/ANTHROPIC_API_KEY. `frontend/web/src/lib/platform.ts` is pure and
tested, and its load-bearing field is `archCertain`: Safari and Chrome both report Apple Silicon as
`Intel Mac OS X`, permanently, so a Mac's architecture is *not derivable* from a user agent and the
page offers both builds with an explanation instead of guessing. Chromium's
`getHighEntropyValues()` narrows it when available, which it did in verification.

Verified end-to-end against the real backend: a 12.3MB macOS arm64 binary built here, run
(`--version` and `sample` against this machine, with `cpu_iowait_percent`/`cpu_freq_mhz` correctly
null per Phase 2), served through the authenticated API, downloaded from the browser as a
12,841,200-byte blob matching the manifest's `size_bytes` exactly, checksum-matched against the SHA-256
the page displays, then installed as a real launchd LaunchAgent whose plist pointed at the **frozen
binary** rather than a venv script, observed connecting and pushing real telemetry, and uninstalled
cleanly. The "no build published" state was also seen for real, before `AGENT_DIST_DIR` was set.

**No Windows or Linux binary has ever been produced.** The spec, the matrix and the systemd/Task
Scheduler installers are written and unit-tested, but "the spec file looks right" is not "it builds" —
treat the matrix's first run as the real test.

The security review found three things worth fixing, all now fixed with tests: **an agent would
enrol over plaintext `http://` to a remote host** with no warning, sending the single-use code and
receiving the long-lived token in cleartext (ARCHITECTURE.md has said `wss://`-only since Phase 0,
and nothing enforced it — Phase 11 is exactly when it stops being advice, since the agent becomes a
binary a stranger points at a URL they typed); **`RATE_LIMIT_ENABLED=false` was not checked by the
prod validator**, so a deployment copying `.env.example` and flipping `ENVIRONMENT=prod` would run
with `/auth/login` and `/enroll` unthrottled and nothing would say so; and the download catalogue's
`unavailable_reason` **leaked absolute server paths** to any signed-in user. The `/code-review high`
pass found three more: a Task Scheduler task **discards stdout**, so a Windows agent had no log at
all (now `--log-file`, rotated); a missing `systemctl` **crashed `status` with a traceback** instead
of saying "not installed"; and the rewritten `config.save()` had **reopened the token file by path**
after locking it down, adding a symlink-swap window the original single-fd version never had.
Everything else in auth and the agent protocol came back clean — token in an `Authorization` header
never a query string, never logged, indexed-hash lookup with no timing comparison, TLS verification
nowhere disabled, frames size-capped before parsing, `/enroll` atomically single-use and
rate-limited with invalid/expired/used deliberately indistinguishable.

A follow-up review pass over the whole phase found seven more, all fixed with tests. The one that
mattered: **a malformed build manifest could 500 the catalogue** instead of degrading — `{"os": []}`
makes `raw["os"] not in KNOWN_OS` raise TypeError on an unhashable key, and a non-list `builds`
raises on iteration, both defeating the module's whole "validated, not trusted" contract. The rest
were platform-specific things that only bite where this project cannot run: `check_executable_location()`
matched `/Downloads/` literally, so the "you left it in Downloads" warning **never fired on Windows**,
the one platform where SmartScreen makes a user go and find the binary in the first place;
`agent_executable()` missed a venv's `Scripts\sentinel-agent.exe`; `schtasks /RU` documents only
`""`/`"NT AUTHORITY\SYSTEM"`/`"SYSTEM"` and not a raw SID, so the flag was dropped in favour of the
`/XML` principal (which *is* documented to take one); systemd expands `%`-specifiers in `ExecStart`,
so a path like `/srv/100%backup/` needed `%%`; a fabricated `Documentation=github.com/...` URL pointing
at somebody else's project was removed, and then its `man:` replacement too, since no man page is
installed either; and a `platform.os as BuildOs` cast in `DownloadPage` was a lie that happened to work
for `"ios"`. 482 backend, 139 agent, 28 web, 41 mobile JS, 36 Kotlin.

A second pass, prompted by being asked "can I actually try this on Windows", found one more —
worse than the seven above, because it is not an edge case: **`sentinel-agent run` would crash
immediately on Windows**, every time. `loop.add_signal_handler(sig, agent.stop)` was called
unconditionally; Windows's asyncio event loop does not implement it and raises `NotImplementedError`
unconditionally, before the agent connects. Nothing in this project's own testing could have caught
it — there is no Windows machine here — and it is exactly the gap `docs/PACKAGING.md` exists to be
honest about. Fixed with a fallback to `signal.signal()`, which itself needs care: a signal handler
runs outside the event loop and cannot call `agent.stop` directly, so it hands off through
`loop.call_soon_threadsafe()`. 143 agent tests (was 139).

A real Android release build followed, at the user's request: a self-generated release keystore
(`~/.sentinel-keys/`, outside the repo), `frontend/mobile/plugins/withReleaseSigning.js` swapping it into
`android/app/build.gradle` in place of React Native's public debug key, `make mobile-apk`, and the
resulting APK registered into the same manifest `/download` already reads — `os: "android"` was
already a first-class citizen there, not a special case. Verified past "it built": `apksigner
verify`'s SHA-256 was checked against the keystore's own fingerprint (not merely "some signature
exists"), `aapt2 dump badging` confirmed the package id/version/label, and the browser downloaded it
through the real authenticated route with byte-for-byte matching size. **Building it surfaced a real
regression the writing-and-testing pass had not**: the injected `storeFile file(System.getenv(...))`
crashes with "Cannot convert 'null' to File" the instant that variable is unset — and Gradle
evaluates every project's `build.gradle` during configuration for *any* task, not just
`assembleRelease`, so it took down `:sentinel-collector`'s own unit tests, a target that never
touches signing at all. `make test` in a plain shell (no keystore exported) is what caught it. Fixed
with a `def sentinelKeystore = System.getenv(...); if (sentinelKeystore != null) { ... }` guard —
unset now means "no release signing configured," never "crash," consistent with every other
optional-integration pattern in this codebase. Re-verified in a shell with the four keystore
variables explicitly unset: 42 Kotlin tests (was 36) pass, and `make mobile-apk` alone — with them
set — still produces a correctly-signed APK. **No Play Store listing, no Google-verified identity**:
this is a self-signed key good for installing the app "anywhere," the same trust model as the
desktop binaries, and Android's install prompt says so on first open exactly like Gatekeeper/
SmartScreen do for the others.

The user then asked whether it would actually run, which it would not have: two more real defects
turned up, both found only by *installing the built APK on the real emulator and driving it*, not by
any test or code review. First, `/download`'s Android card rendered desktop CLI install steps —
`chmod +x sentinel.apk`, `./sentinel.apk enroll --code …` — commands that cannot be typed anywhere on
a phone; `agentInstall.ts` gained `isCliInstall()`/`androidInstallSteps()` with the app's real
one-tap enrolment (the signed-in app mints its own code internally, per Phase 10b — nothing to
paste), and `DownloadPage` branches on it. Second, and worse: **the release APK could not reach any
backend at all.** Android has blocked plaintext HTTP by default since API 28; the RN/Expo template
only re-enables it for the **debug** build variant
(`android/app/src/debug/AndroidManifest.xml`'s `usesCleartextTraffic="true"`), so every prior
"verified end-to-end on a real emulator" claim in this file — Phase 10a, Phase 10b — was against a
*debug* build, and nobody had ever produced a release build before this session to find out it
silently could not talk to `http://` at all. A signed-in screen with a red "Could not reach the
backend" banner is what surfaced it. `frontend/mobile/plugins/withDevBackendCleartext.js` fixes this the
narrow way: a Network Security Config scoped to exactly `EXPO_PUBLIC_API_URL`'s host, generated at
`expo prebuild` time — not `usesCleartextTraffic="true"` app-wide, which would silently accept
plaintext to *any* host the app ever talks to, forever. A `https://` backend needs no exception and
gets none. Verified for real, not merely rebuilt: `uvicorn` restarted bound to `0.0.0.0` (the
Makefile's `dev-backend` binds to loopback only, which a physical phone — or the emulator over its
real network path rather than the `10.0.2.2` host alias — cannot reach either), the emulator's own
`nc` confirmed raw HTTP reachability independent of the app, then the *rebuilt* release APK was
installed fresh via `adb install`, launched, and signed in through the real UI against
`phase10a@example.com` — rendering this Mac's actual live CPU/memory/health numbers, pulled over
plaintext through the new exception. `mobile/.env` and `android/` (generated, gitignored) were
reverted to the documented emulator-default state afterward so the normal `make mobile-android` dev
loop is unaffected; the corrected, working APK was re-registered in the manifest with its real
(different — APK builds are not byte-reproducible) checksum. `frontend/mobile/README.md` now documents both
gaps — the `--host 0.0.0.0` requirement and the release-only cleartext exception — since neither was
written down anywhere before. 49 mobile JS tests (was 41).

Asked to make both halves actually work rather than merely build, a full audit found the gap that
made "just download it and it works" false for *both*: **there was nowhere to point anything at.**
`vite dev` binds to localhost, so a second machine could not load the console at all, and the API
bound to loopback by default, so a phone could not reach it either — Phase 10a/10b had only ever been
exercised from this Mac, against `10.0.2.2`, an alias that exists solely inside the Android emulator.
`backend/app/webapp.py` closes it: `make serve` builds the console and the API process serves it, so
one origin (`http://<lan-ip>:8000`) is the console, the REST API and the viewer socket together —
which is also what makes the browser's address bar and `EXPO_PUBLIC_API_URL` the same string, and
removes CORS from a real deployment. `ApiPrefixMiddleware` performs the `/api` strip that Phase 1
always said a reverse proxy would, as pure ASGI so it covers the WebSocket scope too.

Getting the console served surfaced two design errors, both caught by running it rather than by
review. The obvious implementation — a catch-all route returning index.html — **cannot** work here:
`/devices` is simultaneously a REST endpoint (the Android app calls it, per Phase 10a's no-`/api`
invariant) and a page in the client-side router, so a catch-all never sees it (the real route matches
first, and a browser gets the API's 401 JSON), while blocking API prefixes from the catch-all breaks
the mirror image and 404s a hard refresh on `/devices`. The test suite caught both, and the fix is to
split on **what the client asked for**: `Sec-Fetch-Mode: navigate`, which every current browser sends
on a top-level navigation and no `fetch`/`XHR` ever does. Then gating *file* serving on that same
header 404ed the bundle's own `<script>` and `<link>` — subresources send `no-cors` — and rendered a
blank page with two 404s, which only a browser could show. A third defect came from an existing
download-API test once a built console made the middleware active: `%00` in a path makes
`Path.resolve()` raise `ValueError`, which reached the client as a 500 rather than a 404.

Verified end to end, over the LAN, on both halves. Web: all 11 routes render with zero console errors
and zero failed requests, every client-side route survives a hard refresh (server-side SPA fallback),
and Live Monitoring streams real 1s uPlot charts. Android: the release APK installed via `adb
install`, signed in, listed the fleet, opened Live Monitoring on the *Mac* from the phone ("Streaming
at 1s", real per-NIC throughput), and then enrolled the phone itself through "Monitor this phone" —
the foreground service came up `isForeground=true types=0x40000000` (`specialUse`, the Phase 10b
decision), reported health 92 from memory/disk/swap/network with **cpu and iowait excluded rather
than zeroed**, and the web console's Android page said so in as many words. 517 backend tests (was
482), 143 agent, 32 web, 49 mobile JS, 42 Kotlin.

The honest remaining limit, unchanged by any of this: **there is no deployed backend.** `make serve`
binds to the LAN, so the APK and the console work from any device on the same network as this Mac,
and the APK has that machine's address baked in at build time. Reaching it from cellular or another
network needs the backend deployed somewhere public with a domain and TLS — at which point the
`https://` path needs no cleartext exception and `ApiPrefixMiddleware` keeps working unchanged.

Still to come in Phase 11, if picked back up: run the CI matrix and publish real Windows and Linux
binaries (and find out what breaks); a `.dmg` so a notarized macOS build can be *stapled* — a bare
binary cannot be, so Gatekeeper checks it online and an air-gapped machine still warns; and a real
Windows service via pywin32 if `services.msc` visibility ever matters more than the Task Scheduler
trade-off documented in PACKAGING.md.

Phase 12 (feature parity + real-time fixes), on request after using both halves:

* **The live charts looked frozen and were not.** uPlot auto-fits x to whatever it holds and nothing
  constrained it, so the visible span started at the primer's ~300s and grew towards the ring
  buffer's 900 — one new sample per second moved the trace by 1/300th of the width, then less. A
  direct WebSocket client measured steady 1.03s delivery throughout; the backend was never at fault.
  `LiveChart` now pins x to `[newest - windowSeconds, newest]` so the scroll rate is constant however
  long the screen is open, with a 1m/2m/5m/15m picker.
* **Forecasts needed a full day before anything appeared**, which read as broken rather than
  pending. The cause was the *window*, not the 24-point threshold: a flat 14-day plan made
  `plan_series` choose hourly buckets, so an hour-old device had ~1 row. The window now starts at
  `enrolled_at`, and `plan_series` already picks 10s buckets for a 20-minute span. What makes that
  honest is `MAX_HORIZON_RATIO`: a forecast never projects further ahead than the history behind it,
  so four minutes of data forecasts four minutes, and `history_seconds` (migration 0012) drives a
  `confidence` computed field the history page captions.
* **The Android app reached feature parity for viewing**: Anomalies, Forecasts, Incidents and
  Reports, behind a **More** tab rather than four more bottom tabs. Settings moved onto the stack
  with them — which silently broke its deep link until `navigation/linking.ts` moved too, since a
  path under `Tabs` that is no longer a tab resolves to nothing.
* **Android sampling cadence is now the user's choice.** `CollectorConfig.highFrequency` samples at
  1s continuously instead of 10s-with-1s-on-demand. Off by default and stated plainly on screen: a
  1s timer all day is the collector's dominant battery cost and nothing Android lets an app read
  moves fast enough to need it. Live mode is unaffected either way — the preference is a floor on
  resolution, never a ceiling.

Still true, and not fixable: **Android denies apps `/proc/stat` and `/proc/diskstats`**, so global
CPU% and disk I/O have no workaround short of root. They stay excluded from the health score rather
than counted as zero — see docs/ANDROID_METRICS.md.

Phase 13 (device management, the real CI matrix, and a frontend/ regrouping):

**The repo is now `backend/ · agent/ · frontend/{web,mobile}`.** Moved with `git mv`, so 201 files
keep their history. One line in the move was load-bearing rather than cosmetic:
`webapp.py`'s `DEFAULT_WEB_DIST`, because a stale one fails *silently* — `make serve` starts, the
API answers, and only the console 404s, a state no test covers since the middleware tests point at
a temp directory rather than the repo's own. The Kotlin leg of `make test` did break, and only
running it found it: `frontend/mobile/android/` is generated and bakes in **absolute** paths, so it
went on pointing at the old `node_modules` and failed configuring `:react-native-screens`, a
project unrelated to the move. `make mobile-prebuild` is the fix, which is what "android/ is
generated, not authored" already implied for anyone who moves or re-clones.

**Adding and removing devices exists on both surfaces now.** The backend had supported both since
Phase 1 and nothing called either — `/download` even told the user to "mint a code on the Devices
page", pointing at a button that did not exist. The web console gets `NewDevicePanel` (mint, show
once, live expiry countdown) and a two-step inline confirm per row. The phone gets **removal only**,
on the device's own screen rather than the list, and that is a decision rather than an omission: a
minted code's only destination is a terminal on some other machine, and nothing about a phone
screen beats opening the console on that machine — while the phone enrols *itself* with one tap and
needs no code at all.

**Fleet and Devices merged into one phone tab.** The fleet card was already a superset of the plain
list's row; the only things the list had to itself were "last seen" (which the card also showed)
and a Live shortcut (now a button on it). A bottom bar has about four usable slots. The web console
keeps `/` and `/devices` separate — a sidebar has room a bottom bar does not.

**Anomalies/Forecasts/Incidents/Reports are reachable per device**, from the device screen, while
More keeps the fleet-wide entry. This needed **no backend change**: every one of those endpoints
already took `device_id`. `lib/deviceScope.ts` makes the `?`/`&` choice once and tests it, because
getting it wrong yields a URL the server reads as unfiltered — a *wrong list*, not an error.

**Reports download on the phone.** `File.downloadFileAsync` streams to disk in native code and
throws on a non-2xx rather than saving the error body as `report.pdf`; the share sheet is what
"download" means where there is no downloads bar. Cache, not documents — Phase 9 stores no rendered
report and this must not become the exception. Verified by pulling the files back off the device: a
27,218-byte `PDF document, version 1.7` containing the device name, and a CSV whose CPU row is
*empty* rather than zero.

Four real defects, each found by running the thing rather than reading it:

* **A revoked token did not close the socket it had already opened.** An agent authenticates once,
  at the handshake, and nothing re-checked; a soft-deleted device kept ingesting until the agent
  happened to reconnect, which for a desktop agent may be days. Found by removing a phone in the
  app and watching its collector report "Collecting and connected — last push just now" into a
  device that no longer existed. The re-check now rides on the throttled `last_seen` write in
  `ingest/ws.py` — same question, so the "yes" path costs what it did before, and a revoked agent
  has at most one 15s window left. Writing it also exposed that `_reject` built its `ErrorFrame`
  *inside* a try/except: `code` is a closed Literal, `"revoked"` is not one of its values, and the
  resulting ValidationError was swallowed as "the peer may already be gone", sending a bare 1008
  with no reason. The frame is built outside the try now, and the code sent is the existing
  `"unauthorized"` — adding a Literal value would be a protocol change every deployed agent
  validates against.
* **The dev build could not reach Metro at all.** Phase 12's Network Security Config went into
  `src/main/res`, which every variant inherits, and a networkSecurityConfig outranks
  `usesCleartextTraffic` — so it silently revoked the blanket permission the RN template gives
  *debug*, and `expo run:android` (which serves the bundle from the machine's LAN address, not the
  backend host) died on launch. A second config in `src/debug/res` wins for that variant only.
  Last session asserted the dev loop was unaffected without re-running it.
* **Removing this phone left its own collector running** — server-side revoked and forgotten, while
  the foreground service kept sampling and Settings kept saying "this phone is reporting its own
  metrics". Remove now stops and unenrols the collector when the device is the one holding the app.
* **The list still showed the device you just removed** for up to a 30s poll. `AppState` covers
  backgrounding and says nothing about moving between screens, so the merged tab reloads on focus,
  via a new `reload()` that skips the pull-to-refresh spinner nobody gestured for.

**Forecasts were never history-gated; they were sweep-gated.** Reproduced before changing anything:
a phone enrolled at 20:47:59Z got its first row at 20:59:31Z — **11m32s** — and that row carried 68
points fitted on 680s of history, i.e. every second the device had been alive. So Phase 12's
adaptive early forecasting works exactly as intended; the device qualified after ~4 minutes (24
buckets at 10s) and then waited out the forecast worker's 15-minute cadence. The page said the
opposite — "newly enrolled devices simply don't appear yet" names the one thing that is not the
constraint and describes an open-ended bar rather than a timer. The page now also reads
`/forecasts`, whose rows exist even when the fit was empty, which separates two states that were
sharing one message and are opposite news: *nothing computed yet* (appears at the next sweep) from
*computed, and nothing is trending towards a limit* (good news, previously rendered as a failure to
qualify). Lower `forecast_worker_interval_seconds` if 15 minutes is too long to wait.

Looking at that real page found a bug it was not about: it listed four **removed** phones as "full
in 22h", each a bare UUID, because the forecast endpoints never filtered soft-deleted devices while
`/devices` correctly does, so there was no name to render. A forecast is a claim about where a
machine is *heading*; keeping one for a machine the user removed states a future for something that
does not exist. `test_forecast_api.py` is new — the worker was covered and the *reading* of what it
wrote was not, which is how a forecast outliving its device went unnoticed.

**`/download` no longer presents the Android app as a fourth agent.** Two sections now — "Desktop
agents" (a headless binary for the machine you watch) and "The Android app" (not an agent; the
console itself, whose collector is a switch inside it, not an install). The page also says up front
that you need none of it to use the web console, which was always true and stated nowhere.
Rendering it turned up a smaller lie: the APK's card read "Android · Apple Silicon / ARM64", so
`archLabelFor()` keeps that desktop phrasing where the ambiguous-Mac card needs it and gives the
phone a plain "ARM64".

**This Mac is now a real launchd service**, not a foreground process someone started by hand. It
had been running `sentinel-agent run` from source against a scratch config for 11 hours; the
working config moved to the canonical `~/.config/sentinel/agent.toml` (same `device_id`, so history
is continuous) and `install-service` registered a LaunchAgent pointing at the venv console script.

### The CI matrix, and what its first real run cost

`.github/workflows/agent-build.yml` had **never been indexed by GitHub**, so `gh workflow run`
404'd while the file sat on master, byte-identical, with Actions enabled. A push to the default
branch is not enough: GitHub indexes workflows on a push that actually *changes* a file under
`.github/workflows/`.

The first run then did exactly what docs/PACKAGING.md said it would — it was the real test, and
three defects fell out of it that nothing here could have caught:

* **The Windows agent bricked its own config.** `AgentConfig.save()` opened its descriptor with no
  `encoding=`, so Python wrote in the locale encoding — cp1252 on a default Windows install — and
  the em-dash in the header comment save() itself writes landed as a bare 0x97 byte. tomllib
  accepts UTF-8 only, so `enroll` wrote a file `run` could never read back.
* **`psutil.cpu_freq` may not exist at all on Apple Silicon.** psutil defines it only
  `if cext.has_cpu_freq()`, and `_safe(psutil.cpu_freq)` evaluates the attribute lookup while
  building the argument, before `_safe` can catch anything. Same psutil (7.2.2) has it on this Mac
  and lacks it on GitHub's macos-14 runner, so no version pin would have found this — only another
  machine. Same family as Phase 2's `cpu_freq == 4`: the platform lying about frequency, this time
  by omission.
* **A Windows service registered `python -m`, never the console script.** `agent_executable()`
  searched only beside the interpreter, and on Windows python.exe sits at the venv root with
  scripts in `Scripts/`. Phase 11 added the `.exe` *name* but not the directory, and its test put
  python.exe *inside* `Scripts/` — a layout no real venv has — so it passed against the broken
  lookup. The test now models the real layout and no longer accepts the `-m` fallback, because that
  fallthrough is silent and a test tolerating it cannot tell "found it" from "gave up looking".

The remaining Windows failures were tests asserting POSIX truths on NTFS (`chmod`/`fchmod`/`st_mode`
where the icacls branch is what runs) and `str(Path)` comparisons rendering `/etc/sentinel` as
`\etc\sentinel`, because a Path takes the *host's* flavour, not the one `config_dir()` was asked to
render. Placement assertions compare `as_posix()` now; the mode ones skip on Windows, as does
`test_add_signal_handler_is_tried_first`, whose docstring claimed every platform this suite runs on
supports `add_signal_handler` — Windows does not, which is the entire reason the fallback beside it
exists.

`macos-13` was retired by GitHub and a job requesting a retired label is *accepted and then queues
forever*: two runs sat pending over two hours while the other three finished in minutes.
`macos-15-intel` is the current x86_64 label. `fail-fast: false` did its job throughout.

**All four platforms now build and are published**, and each was verified rather than assumed:
macOS arm64 run natively on this Mac (`cpu_iowait_percent`/`cpu_freq_mhz` correctly null), macOS
x64 run under Rosetta, Linux x64 run in `ubuntu:22.04` *and* `debian:12` — confirming the
oldest-runner glibc pin forward-compatible — with real `iowait` and `cpu_freq` where macOS reports
neither, and Windows via its own CI step, which runs under `bash -e -o pipefail` and so proves both
`--version` and `sample` exited 0 on a real Windows runner. `sample`, not `--version`, is the
check that matters: it is what proves psutil's compiled extension made it into the bundle. Every
checksum and byte-count was matched against its manifest, and the Windows binary was then pulled
through the real authenticated download route and matched again, byte for byte.

One thing observed while verifying and deliberately not fixed, because it is worth a decision
rather than a patch: **a device whose clock is skewed by more than `FRESH_WINDOW_SECONDS` (90s)
renders as "Online · last seen just now" with every headline reading "unavailable"**, while its
health score and sparklines — which read rollups over a wider window — are fine. The emulator's
clock drifted 1m43s behind this Mac and reproduced it exactly. Nothing is wrong: `last_seen_at` is
stamped server-side at ingest and is current, while the sample's own `ts` comes from the agent and
falls outside the window, so Phase 4's "a reading older than 90s is not current" rule correctly
refuses to present it. But the combination reads as a broken agent, and nothing anywhere says
"this machine's clock is wrong" — the skew is measurable at ingest (`ts` versus server `now()`)
and currently nothing looks.

### A correction pass, prompted by "nothing has changed"

Three things from Phase 13 above were incomplete or half-done, all caught by the user pointing at
the actual running app rather than the summary:

* **The web console's `/` and `/devices` were still two pages.** The mobile Fleet/Devices merge had
  a web-side twin — same redundancy shape (`/` = rich cards, `/devices` = a thinner list) — and it
  had been left alone on the reasoning "a sidebar has room a bottom bar does not." That reasoning
  was never what was asked for. Fixed the same way as mobile: one page at `/`, `/devices` redirects
  to it, `DeviceSummaryCard` grew a self-contained remove flow (own confirm state, own DELETE call)
  matching the shape the phone's `DeviceScreen` already used.
* **Forecasts got better messaging, not a shorter wait.** The prior fix correctly diagnosed the
  15-minute worker sweep as the real gate and then left the 15 minutes in place — explaining a
  problem is not fixing it. `forecast_worker_interval_seconds` is now 120 (was 900), re-measured
  with a fresh scratch agent: 5m26s from enrollment to a fully-fitted forecast, down from 11m32s.
  Real trade-off, stated in the config comment: eight times the tick rate, real CPU per tick, fine
  for the number of devices this project actually runs, not free at scale.
* **"Fix the Android app" meant the release APK someone downloads and puts on a real phone — not
  the emulator's dev build.** The registered APK on `/download` was `agent/dist/sentinel-agent-0.1.0-
  android-arm64.apk`, built the *previous* session at 21:40, before any of this session's device
  removal, per-device screens, merged tab, report download, or cleartext fix. Every verification this
  session had been against the emulator's separately-installed debug build, which picks up JS changes
  through Metro and never touches that stale file. Rebuilt with the real release keystore
  (`~/.sentinel-keys/`) via `make mobile-apk`, with `EXPO_PUBLIC_API_URL` pointed at this Mac's LAN
  address rather than the emulator-only `10.0.2.2` alias — that address is what a real phone needs,
  and is exactly the distinction Phase 11's cleartext work exists to get right. Verified past "it
  built": `apksigner verify` matched the keystore's own SHA-256 fingerprint, then the rebuilt APK was
  installed fresh on the emulator (its network path reaches the host's real LAN IP the same way it
  reaches `10.0.2.2`, so this is a faithful stand-in for a physical phone) and signed in for real — the
  backend's own log shows `192.168.0.4:53731 - "POST /auth/login HTTP/1.1" 200 OK`, not a guess. The
  stale APK was deleted from `dist/`, the new one re-registered, and the served bytes were pulled back
  through the real authenticated route and matched SHA-256 against the manifest. `frontend/mobile/.env`
  was reverted to the emulator default afterward, same as Phase 11 documented doing.

### Forecasts, drawn rather than just described

Two more rounds after the correction pass above: the user first reported still seeing no forecasts
at all, which turned out to be my own leftover scratch test agent and device from the timing
measurement, still running and cluttering the account — real forecasts existed the whole time for
the real devices once that was cleaned up. Then, once forecasts were visible, the actual ask: "full
in 3.8 days" as text was not what "graphical representation" meant.

**Web's `/forecasts` now draws each row** — the predicted trend, its confidence band, and a dashed
line at the 100% ceiling — reusing `HistoryChart` with no real series behind it (fetching a live
series for every device×metric on a fleet page would be one API call per row; the forecast alone
needs none). `toForecastOverlay()`/`forecastCaption()` moved out of `DeviceHistoryPage` into
`lib/forecastOverlay.ts` so a device's own chart and the fleet-wide one can never disagree about the
same stored row. Building this surfaced a real bug: the first pass's reference line used
`ctx.strokeStyle = "color-mix(in srgb, currentColor 35%, transparent)"`, and a canvas 2D context
never resolves CSS — `currentColor`/`color-mix()` are DOM/SVG concepts, and assigning either to
`strokeStyle` is silently a no-op, no exception, nothing drawn. Found by reading the canvas's own
pixel data rather than trusting a screenshot; fixed by resolving `--muted-foreground` via
`getComputedStyle` and using `globalAlpha` for the transparency canvas actually implements.

**Android got the same treatment on request**, since uPlot does not run in React Native: a small
hand-built `ForecastChart` (react-native-svg) plus `forecastChartFrame.ts`, the pure geometry split
out the same way `chartFrame.ts` already is — except this one is a one-shot transform over a stored
`points` array, not something read off a `RingBuffer` on a timer. Verified against the real backend's
actual forecast rows first (mem_percent predicted ~60.8%, confirmed by `curl`), then checked the
rendered app against that known-correct number — which is how a second real bug surfaced: the
ceiling reference line at `y=0` coincided exactly with the plot `View`'s own top border, so it was
genuinely being drawn, just visually indistinguishable from a border already occupying that pixel
row. Confirmed by cropping and upscaling a real screenshot rather than glancing at the full page —
the predicted line was correct all along. `EDGE_PADDING` (6px) fixes it, mirroring uPlot's own
`padding: [12, 12, 0, 0]` on the web chart this mirrors. 65 mobile JS tests (was 58), verified live
via Metro fast refresh against the real emulator both before and after the fix.

Still to come, if picked back up: **no code-signing certificates exist**, so all four desktop
builds are unsigned and Gatekeeper/SmartScreen will interrupt every install — the page quotes the
dialog before the click rather than leaving the user alone with it. And **there is still no deployed
backend** — everything above is reachable over the LAN from this Mac and nowhere else.

### A whole-codebase review pass

Asked to re-read everything and fix what was wrong. Every suite was green going in (535 backend,
145 agent, 145 JS, 42 Kotlin, ruff and tsc clean), so nothing here was found by running the tests —
each came out of reading the code and then reproducing it.

* **Anomaly notifications said `(None None)`.** `notify.py`'s `_body` read
  `comparison`/`threshold` unconditionally, and an anomaly event leaves both null by design
  (models/alerts.py) — so every email and push for the entire Phase 6 rule type rendered
  `mem_percent = 87.3 (None None)`. Both frontends had been taught to branch on `rule_type` when
  anomaly and forecast rules landed; this one surface never was. `_condition()` now branches the
  same way, so an anomaly reads "unusual for this device — normally around 42.1, now +5.4 sigma
  out" and a forecast says it is a forecast rather than implying the threshold is already crossed.
* **The Live Monitoring primer returned the *oldest* rows of its window.**
  `/devices/{id}/samples/recent` ordered ascending and LIMITed at 900, with a comment reasoning
  about "900s of 1s rows" — true for one series, and `net`/`disk_io`/`latency` are multi-entity.
  This Mac reports **six NICs**, so a 300s live window is 1800 rows against that cap: the network
  chart opened showing the first half of the window and then jumped when the socket delivered
  `now`. It only bites in live mode, which is the only time the endpoint is called. Now ordered
  descending, reversed, with a per-entity budget — a truncated primer loses its oldest end, which
  is the only end it may lose.
* **A removed device's name could not be reused.** `uq_devices_user_id_name` covered soft-deleted
  rows, so remove-then-re-add was refused with "You already have a device with that name" against
  a device list that no longer held one — the soft delete existing so metric rows keep a valid FK
  is a storage decision, and it had been leaking out as a rule about names. Migration `0013` makes
  it a partial unique index (`WHERE deleted_at IS NULL`); `_unique_device_name` now ignores removed
  devices too, so a re-enrolling machine gets its own name back instead of a `-2` suffix.
* **Alert/anomaly/incident history rendered removed devices as bare UUIDs** — the open decision
  recorded above. Resolved by keeping the rows (a firing that really happened is history worth
  keeping, unlike a forecast, which is a claim about a future the machine no longer has and which
  `/forecasts` already filters) and giving the client a way to *name* them: `GET
  /devices?include_removed=true`, plus a `deleted_at` on `DeviceOut`. `lib/deviceNames.ts` on both
  frontends keeps the two reasons a lookup can miss distinct — "removed" is permanent and
  explainable, a not-yet-loaded device list is transient and gets a short id, and conflating them
  would invent an explanation.
* **Two agent config values could brick the agent.** `sample_interval_seconds` and
  `push_interval_seconds` are both stamped onto samples as `resolution_seconds`, which the protocol
  declares `Literal[1, 10]` — so editing either to any other value produced an agent that
  connected, handshook, pushed once, and was killed by a non-retryable `invalid_frame` whose
  message names no field. `validate_intervals()` now runs before `run` connects (and warns in
  `status`, which must keep describing a broken config rather than refusing to).
* **`AgentConfig.save()` wrote TOML it could not read back.** Values went straight into
  `key = "{value}"`, and every one of them is user-supplied (`--server`, the latency targets), so a
  quote or a backslash produced a file `enroll` wrote happily — token and all — and `run` then died
  in tomllib. Exactly the write-succeeds/read-fails shape as the cp1252 bug the CI matrix found,
  reached by a different route. `toml_string()` escapes properly.
* **A live-viewer lease key could outlive everything that would clean it up.** `live_count()`
  prunes and deletes the ZSET, but only an agent's supervisor calls it — so a viewer that watched a
  device whose agent never reconnected left the key in Redis forever. `claim()` now sets a TTL
  alongside the member score, refreshed on every renewal.

Verified in the browser against the real backend, on a seeded account holding one live and one
removed device with alert history on each: `/alerts`, `/incidents` and an incident detail page all
render "retired-pixel (removed)" where they used to print a UUID, with every request 200 and no
console errors. 546 backend tests (was 535), 156 agent (was 145), 51 web, 68 mobile JS, 42 Kotlin.

### Running the agent on a real Windows machine for the first time

A second machine was added to the fleet, which is the first time the Windows
agent has been run by someone who is not its author. Three defects in a row,
each only reachable by doing it:

* **`make mobile-apk` died on Gradle's "SDK location not found"** on a machine
  that had built the app many times. `expo prebuild --clean` regenerates
  `android/` including `local.properties`, which was the only record of the SDK
  path. `mobile-collector-test` had solved this in Phase 10b; the variable just
  sat below `mobile-apk` in the Makefile and was never wired into it.

* **The APK's backend address is welded in, and nobody had written that down.**
  `EXPO_PUBLIC_API_URL` is inlined into the JS bundle *and* its host is written
  into the Network Security Config, so a release APK is only valid while the
  backend keeps the address it was built against — and a laptop's LAN IP
  changes with every network it joins. The app's own "Could not reach the
  backend at ..." is accurate and reads as a stale *build*, which is the wrong
  conclusion. `frontend/mobile/README.md` now says so, and says the cheap fix
  is to stop letting the address move: run the laptop on the phone's own
  hotspot and pin that. There is deliberately still no server-URL field in
  Settings — it would fix the bundle's copy of the address and not the
  OS-level cleartext allow-list, so the request would still be blocked.

* **`enroll` locked the user out of their own token file, permanently.** This
  is the one worth remembering. `windows_acl_argv()` read `%USERNAME%` itself
  and, when it came back empty, appended no grant at all — *after*
  `/inheritance:r` had stripped every inherited permission. `icacls` exits 0,
  because it did exactly what it was asked; `secure_file()`'s returncode check
  therefore passed; and the token file ended up granted to SYSTEM and
  Administrators and to nobody else. Every subsequent run failed on a plain
  *read*, and `Remove-Item` needed an elevated shell — which is the tell, since
  Administrators was the one principal left on the ACL.

  `tests/test_paths.py` had asserted this behaviour as correct, under the name
  `test_the_windows_acl_survives_no_username`. It does not survive. A test can
  pin a bug in place as easily as it can catch one, and the giveaway here was
  the word "survives" describing a branch nobody had reasoned about the
  consequences of.

  The fix grants the caller's **SID** (`whoami /user`, falling back to
  `%USERDOMAIN%\%USERNAME%`), makes `principal` a required argument so no
  caller can ever omit it again, and **refuses to write the token** when it
  cannot determine who to grant to — the one case where failing loudly is
  strictly better, because the alternative is not a failure at all but a
  successful lockout. The owner now gets `(F)` rather than `(R,W)`: `save()`
  rewrites the file on every re-enrolment, and `(R,W)` does not include DELETE.

  One dead end is recorded on purpose. The first fix attempted was a bounded
  retry around the reopen, on the theory that Windows Defender was briefly
  locking a file it had just watched an unsigned, freshly-SmartScreen-flagged
  process re-ACL. It was published through the CI matrix and changed nothing:
  the failure reproduced on a brand-new file, exhausting the full backoff every
  time. **Deterministic is not transient** — that observation is what killed
  the theory, and it should have been the first question asked. The retry is
  kept, deliberately not load-bearing, with a docstring that says it did not
  fix what it was written for.

`docs/INSTALL.md` no longer claims no Windows `.exe` exists; the CI matrix has
been building and testing one on a real `windows-2022` runner since Phase 13.

**Verified for real, not just in CI:** the rebuilt binary enrolled cleanly on
the same physical machine that produced every failure above — no crash, token
written, `run` connected, and the fleet dashboard showed it as a genuine
second device. The whole chain (SID-based grant → CI → download → enroll →
connect) is confirmed working end to end on real Windows hardware, which is
the first time any of it has been.

### Open: live-mode flapping, not yet diagnosed with real evidence

Found immediately after the enrollment above, watching that same device:
`sentinel_agent.transport.client` logged repeated `switched to live mode (1s)`
/ `switched to normal mode (10s)` pairs, sometimes twice within the same
displayed second. The mechanism is not in question — the supervisor only
sends a `mode` frame when `registry.live_count()` actually changes
(`app/live/supervisor.py`), so two flips a second apart means a viewer's
Redis lease is genuinely appearing and disappearing that fast, which only
happens if the browser's `/ws/viewer` WebSocket is dropping and reconnecting
(`ViewerSession.stop()` releases the lease and publishes `viewer_left` on
disconnect; reconnecting resubscribes and re-claims within its ~1s first
backoff step).

The *why* is not established. The leading theory — this Mac, the Windows
agent, and whichever browser had Live Monitoring open were all sharing one
phone's hotspot radio simultaneously, and mobile hotspots are known to be
worse than a home router at holding a long-lived, mostly-idle connection
steady under multi-device load — fits the ~15–40s cadence better than a code
bug would, but it is a theory, not a finding. Given this session's own
"deterministic is not transient" lesson two sections up, it is deliberately
**not** being acted on without first confirming it: the concrete next step is
opening the browser's DevTools Network→WS tab while it is flapping and
watching whether `/ws/viewer` shows one connection staying open the whole
time (a genuinely different, server-side bug) or a new one appearing every
15–40s (confirms the reconnect-cycle theory, and turns this into an
environment limitation rather than something a code change fixes). Nothing
has been changed in `app/live/` or `frontend/web/src/lib/liveSocket.ts` for
this — there is no fix yet because there is no confirmed cause yet.

### Phase 14: adding a device is one page, and the phone stops being a viewer-only

Four things reported from using both halves, all fixed and all verified against the real backend.

* **The Copy button on a freshly minted enrollment code did nothing.** Not "failed" — *nothing*:
  no copy, no error, no feedback. `navigator.clipboard?.writeText(code).then(ok).catch(fail)`
  looks defensive and is the bug, because optional chaining short-circuits the **entire** chain.
  `navigator.clipboard` is gated on a secure context, and `make serve` binds to the LAN, so the
  real origin is `http://192.168.x.y:8000` — not https, not loopback — where the whole object is
  `undefined` and neither callback ever ran. `lib/clipboard.ts`'s `copyText()` falls back to
  `document.execCommand("copy")`, which is deprecated but is *not* secure-context-gated, and
  **returns whether the text actually arrived** so `CopyButton` can say "select it by hand"
  rather than flashing "Copied" over a clipboard holding something else.

* **`/download` and the "New device" panel were two halves of one task, in two tabs.** Neither
  worked alone: the download page's own instructions told you to go and mint a code, and the mint
  panel linked back for a binary. They are one page now — `/devices/new`, "Add a device" in the
  nav, with `/download` redirecting — and merging them bought something the split could not
  offer at all: the code and the chosen build are in one component's state, so
  `enrollmentPlan()` renders **the real commands with the real code already in them**, one
  copyable line at a time. Two steps that were prose in a warning box are now lines in the
  sequence, because both make a correct-looking command fail: `cd ~/Downloads` (the shell opens
  in `$HOME`, the binary does not live there) and `xattr -d com.apple.quarantine` (Gatekeeper
  blocks an unsigned download *before* the process starts, so `enroll` produces no output of its
  own). The platform picker is a choice rather than a detection, because the machine being
  enrolled is very often not the machine the browser is on.

* **The Android app was missing most of the console.** Now added: **History** (per device, its own
  time-range picker, every chart the web page draws, platform-gated the same way), **Alert
  rules** (full CRUD — the same three rule types, chips instead of `<select>`), and **Incident
  detail** (the correlated timeline plus both AI cards and Regenerate). Reachable per-device from
  a device's own screen and fleet-wide from More; an alert card also opens the incident it
  correlated into. What stays web-only is now a short, justified list rather than an accident:
  silence windows beyond the one-hour mute, report schedules, and adding a device — an enrollment
  code's only destination is a terminal on another machine, and this phone enrols itself with one
  tap and needs no code.

* **Chart axis labels were drawn on top of the trace.** Both mobile charts pinned their high and
  low values into an `absoluteFill` overlay at the plot's own corners. On a quiet chart that
  reads fine, which is why it survived; on this Mac's Live Monitoring — twelve NIC series, most
  of them near zero — the traces run straight through "0 B/s". `components/charts/PlotFrame.tsx`
  puts them in a gutter *beside* the plot, and owns the frame for all three charts so they cannot
  drift. The gutter reports the plot's real width back to its caller, which is load-bearing:
  building points against the card's width would push the newest sample under the legend by
  exactly the gutter.

**`/download`'s checksum command was keyed on the wrong machine.** `verifyCommand()` picked its
tool from `build.os`, so the Android card told a Mac user to run `sha256sum app-release.apk` — a
command macOS does not ship. The checksum is taken on the machine that *downloaded* the file, which
for the APK is never the machine it runs on, so the command is keyed on the browser's detected
platform now and falls back to the build's OS only when detection failed. Seen on the real page,
not in review.

Three more defects came out of looking at the running app rather than the code, all in the new
history charts and none catchable by a type checker:

* **A 24h window read "8:52 PM → 8:32 PM"** — time running backwards, which is what a day-wide
  window looks like for most of the day. `xAxisLabels()` now names the day whenever the two ends
  fall on different calendar dates, and only then.
* **`146.1 MB/s` rendered as `146.1 M…`.** The gutter had been sized against `"1.4 MB/s"` and
  `"100%"`. A y-axis whose top value is unreadable is not a y-axis; it is sized against the
  longest string the formatters actually produce now.
* **An autoscaled rate chart floored at its own minimum, not at zero**, so a nearly-idle disk sat
  on a baseline labelled "53.5 KB/s" and read as sustained write traffic that was not happening.
  `chartFrame.ts` had always pinned zero for the live chart; `historyChartFrame.ts` now matches it.

Verified on the real Android emulator against the real backend and this Mac's live agent: the
History screen over 24h with all seven charts and their real rollup caption, Live Monitoring's
Network and Disk I/O panels — the exact two from the bug report — with every label clear of the
trace, an alert rule created *and deleted* through the real API, and a real incident's timeline
with both AI cards correctly reading "Not generated yet" (no `ANTHROPIC_API_KEY` configured).

Verified in the browser against the real backend: `/devices/new` mints a real code
(`4CA9-PWJ2-B1SX`) and the enrol command reads `enroll --code 4CA9-PWJ2-B1SX` — the substitution
is the whole point of the merge. The clipboard fix was proved **under the condition that actually
fails**, which localhost is not: `navigator.clipboard` was deleted from the page to reproduce a
plain-http LAN origin, and the fallback then reported `execCommand("copy") === true` with the
selected text equal to the real code. Under the old optional-chained call that same condition
produced no call, no promise and no callback at all.

546 backend tests, 165 agent (was 156), 65 web (was 51), 81 mobile JS (was 68), and 40 Kotlin —
40, not the 42 recorded above: nothing here touches Kotlin, and counting the suite's own
`test-results` XML gives 40 across its eight classes, so the earlier figure was miscounted rather
than something regressing.

The APK was rebuilt so `/download` serves the app this work is actually in — the registered one
predated all of it, the same staleness trap Phase 13 records. Checked past "it built":
`apksigner`'s certificate SHA-256 matched the keystore's own fingerprint (`a6a78f80…`, so the real
release key and not React Native's public debug key), `strings` on the **Hermes bytecode** — plain
`grep` silently finds nothing in it, which is its own small trap — confirmed the new screens are
in the shipped bundle, the manifest/dist/page all agree on `6c76e4d2…`, and the authenticated
route returned 200 for it. The release-only failure mode Phase 11 exists to catch was confirmed
statically rather than by uninstalling the emulator's dev build: the APK's Network Security Config
permits cleartext to exactly `10.23.132.19` and nothing else, with no blanket
`usesCleartextTraffic` and no `debuggable` flag.

### A stable address: Tailscale Funnel, and the hardening a public link actually requires

Prompted by a real failure: the Windows agent, freshly enrolled, started failing every reconnect
with `timed out during opening handshake`. Nothing had changed in the agent or the server — the
Mac's LAN IP had. Chasing *why* surfaced the real mechanism, not just the symptom: `ifconfig`
showed `en0` broadcasting a MAC address that did not match the Wi-Fi hardware's own
(`networksetup -listallhardwareports`) — macOS's Private Wi-Fi Address feature presents a
rotating, per-network-random MAC by default, so a phone hotspot's DHCP server sees what looks like
a brand-new device each rotation and hands out a fresh lease. This happens even when nothing about
*which* network you're on ever changes, which is what made it confusing: "same hotspot" and "same
address" are not the same guarantee, and a hotspot has no reservation feature to fall back on the
way a home router would.

Pinning the MAC (or the IP) only helps for one network at a time anyway, and the actual goal — a
link that works from wherever the Mac happens to be, hotspot or otherwise, with **nothing to
install beyond the agent itself** on any other device — ruled out the first plan (Tailscale on
every device). **Tailscale Funnel** is the fit instead: only the Mac needs Tailscale. Funnel turns
`make serve`'s local port into a real `https://` URL —
`https://bavans-macbook-pro.tail9d00e9.ts.net` — that every other device reaches as an ordinary
HTTPS endpoint, no client software involved. The Windows agent's `agent.toml` and the mobile app's
`EXPO_PUBLIC_API_URL` both just point at it now, exactly the "real answer" `mobile/README.md`
already named: *"an `https://` backend on a real domain never moves and needs no cleartext
exception at all."*

That link is genuinely public, though, which is a different exposure than a hotspot ever was, and
`app/config.py`'s own `_refuse_insecure_production()` exists precisely to keep a `dev`-configured
backend from being mistaken for a safe one. All four of its prod requirements were satisfied for
real rather than bypassed: `RATE_LIMIT_ENABLED=true`, a fresh 32-byte `JWT_SECRET` (rotating it logs
out every existing session, expected and one-time), `COOKIE_SECURE=true` (only possible now because
Funnel terminates real TLS — it was correctly `false` before, over plain LAN `http://`), and the
`sentinel_app` Postgres role's password rotated with `ALTER ROLE` directly against the database —
migration `0001`'s `CREATE ROLE` is idempotent and only ever runs once, so editing `.env` alone
would have left the database still expecting the old password and every connection failing.

Turning rate limiting on surfaced a real bug before it ever reached a real user: `ratelimit.py`'s
`_client_ip()` deliberately trusts only `request.client.host`, never `X-Forwarded-For`, with a
comment already warning that production needs `--proxy-headers --forwarded-allow-ips=<lb>` for this
to key on the right address. Funnel proxies locally to `127.0.0.1:8000`, so without that flag every
request — from anywhere on the internet — would have landed in the *same* shared bucket, meaning a
handful of failed logins from one stranger would have locked out every legitimate user. Not a
theoretical gap: confirmed by checking uvicorn's own access log before and after adding
`--proxy-headers --forwarded-allow-ips=127.0.0.1` to `make serve`'s command — the logged client
address changed from `127.0.0.1` to the real forwarded one. Trusting only loopback keeps this safe:
nothing off-box can forge a connection that originates from `127.0.0.1`.

The APK was rebuilt against the Funnel URL and re-registered in `agent/dist/manifest.json`,
replacing the stale entry. Because the URL is `https://`, `withDevBackendCleartext.js` correctly
emitted no Network Security Config exception at all — confirmed by finding no
`network_security_config` resource anywhere in the built project, where past sessions always found
one scoped to a bare IP. Verified past "it built": `apksigner`'s certificate SHA-256 matched the
keystore's own fingerprint, `aapt2 dump badging` confirmed package/version, and — the check that
actually proves the public path works, not just the build — a real signup, login, and authenticated
`GET /downloads/agent/app-release.apk` against the live Funnel URL returned the exact SHA-256 the
manifest now records.

What this is not: a traditional cloud deployment. `make serve` still runs from this Mac exactly as
before; Funnel just gives its port a stable public front door instead of a LAN IP that moves with
every network. It depends on Tailscale actually being connected on the Mac (menu-bar app, "Open at
Login" enabled) — if Tailscale isn't running, the Funnel link goes down right along with it, no
differently than `make serve` itself needing to be running for any of this to work at all.

One rough edge, found immediately by actually looking at the terminal during a demo: `make serve`'s
own startup banner still only printed the LAN address, because that `echo` predates Funnel and was
never taught to look for it — the Funnel URL worked the entire time, the terminal just never
mentioned it, which reads as "did this actually work?" at exactly the moment you're trying to show
someone it did. The banner now shells out to `tailscale funnel status --json` and prints a `Public
(Tailscale Funnel): https://...` line whenever one is active, alongside the LAN address as before —
detected, not configured; nothing to set up beyond having Funnel running.


### Phase 15: detection that runs itself, and two real-time defects behind it

Three things reported from using both halves. All three turned out to be one theme — the system
was doing less on its own than it looked like it was.

**Live Monitoring sat still for several seconds before moving, and that was the agent, not the
chart.** Phase 12 already fixed a *cosmetic* version of this (uPlot auto-fitting x). This one was
real data not arriving. `_push_loop` read `connection.push_interval_seconds` once and then slept on
it, and **nothing was reading the socket during that sleep** — so the `mode` frame the supervisor
sends within a round trip of a viewer opening the page sat unread for up to the full 10s interval.
Worse, the push that finally woke up was still built in the old mode: one 10s-collapsed row, with
live mode only taking effect on the iteration after that. This is character-for-character the bug
Phase 10b found in the Kotlin collector — *changing an interval does not shorten a sleep that has
already started* — reached from the pusher's side instead of the sampler's, which is why the same
lesson did not transfer: the fix went in one implementation and the reference implementation it was
ported from kept the defect. `AgentConnection.idle()` now reads frames while it waits and returns
early when the cadence actually changes (only when it *changes* — returning for every mode frame
would rebuild the same batch a moment early for nothing). Measured against the real agent: last
10s-resolution row at `17:37:06`, first 1s row at `17:37:07`, **no gap** — on the mode frame the
agent flushes the 1s samples it had already buffered, so the viewer gets 1s history right back to
where the 10s rows stopped instead of waiting a cycle for it.

**The Windows agent was not sampling slowly; it was being asked to do too much per sample.** The
sample interval was never the problem — `DEFAULT_SAMPLE_INTERVAL` has been `1` since Phase 2, on
every platform. What differs is what one sample *costs*. `collect_processes()` (a walk of every
process, opening each for name/user/memory) and `psutil.net_connections()` ran on **every** sample,
i.e. once a second. On this Mac that measures 13.4ms and is invisible. The trap is why: `strings`
of the numbers shows `net_connections()` at **0.1ms here — because macOS *denies* it without root
and it fails fast.** On Windows the same call succeeds and walks the whole TCP table, and
`process_iter` opens a handle per process. A cost this machine literally cannot observe is still a
cost, and at a 1s budget it is the difference between keeping time and not. Both are now throttled
to their own cadence (10s for processes, 15s for connections/users), carrying the last **real**
measurement between probes — the pattern `runner.py` already used for latency, and allowed for the
same reason: the value is always something that was measured, just measured slightly earlier.
Process collection also moved to `asyncio.to_thread`, since a blocking multi-hundred-millisecond
walk on the event loop stalls the very socket read that carries the live-mode frame. Per-sample
cost here fell from ~13.4ms to **0.8ms**. `sentinel-agent sample --timing` is new, and exists
because this question can only be answered on the machine being asked about — it times five real
samples against the configured budget.

**Nothing detected anything until you told it to, and that was the actual gap.** The threshold
evaluator, the adaptive anomaly baselines and the forecast-breach check all worked and all sat idle
until somebody hand-wrote a rule, so a new account with machines enrolled was silent through a disk
filling to 100% — not a failure to detect, a failure to have been asked to look. Every account now
gets a default rule set (`app/alerts/defaults.py`, migration `0014`): four static thresholds, two
adaptive anomaly rules, two forecast rules. They are **ordinary `AlertRule` rows**, deliberately not
a parallel mechanism — the evaluator sweep, the OK/PENDING/FIRING machine, incident correlation and
notification dispatch treat them exactly like hand-written rules, which is the same refusal to add a
second evaluation path that Phases 6, 7 and 8 each made. `device_id` is `None` on all of them, which
is load-bearing: `_rule_device_pairs` fans a null-device rule across the account's devices at sweep
time, so a machine enrolled tomorrow is covered by rules seeded today with nothing re-running.

`source` (`"user"` / `"builtin"`) is presentation only — the evaluator never reads it — and both
clients group on it, because eight rules somebody did not write, mixed in with the ones they did,
read as clutter rather than as help. Builtin rules stay fully editable and deletable: a default you
cannot turn off is a worse default. `ensure_default_rules()` asks whether an account has *ever* been
seeded rather than whether it currently has all eight, so **a default you delete stays deleted** —
the alternative resurrects it on every run, which is not a default but a bug the user cannot work
around. The migration backfills existing accounts and skips any that already have one.

Verified against the real backend rather than assumed: every existing account picked up its eight
rules with hand-written ones left untouched and separately marked, and one sweep later the
`anomaly_baselines` table showed **new `cpu_percent` baselines** (`sample_count=33`, updating live)
for devices whose account had never had a CPU anomaly rule — detection that genuinely was not
happening before. The OPPO phone correctly got **no** `cpu_percent` baseline at all, because Android
denies `/proc/stat`: the automatic rules do not fabricate detection on a device that cannot measure
the thing, which is docs/ANDROID_METRICS.md holding through a feature written long after it.

One trap worth recording: the running `uvicorn` had been started **without `--reload`** (it is the
LAN-bound one from the APK verification), so the page kept rendering every rule as user-written
against a stale `AlertRuleOut` with no `source` field. The symptom — a heading that would not appear
— looked like a frontend bug and was a stale server; `/openapi.json` is what settled it in one
request.

553 backend tests (was 546), 170 agent (was 165), 65 web, 81 mobile JS, 40 Kotlin.

A self-inflicted scare worth recording, since it will happen again: running `pytest` in one shell
while `make test` was still going in another produced five failures in `test_alert_rules_crud.py`
that looked exactly like a regression from the seeding change. Both runs share one Postgres and
truncate each other's tables between tests. Neither run is wrong; running them concurrently is.
Serially, both are green.

**The Windows machine will not get the sampling fix until its binary is rebuilt.** The agent it is
running came from the CI matrix and predates all of the above; the fix is in the source, and reaching
that machine means re-running `.github/workflows/agent-build.yml` and re-downloading. Same for the
live-upshift fix on any enrolled desktop.

**The process refresh was later decoupled from the sample's critical path.** Phase 15 moved
`collect_processes()` to a thread and throttled it to 10s, but the sample loop still awaited it
inline, which meant every 10th sample's *entire* payload (not just processes) was stamped 1–2s late,
causing Live Monitoring to freeze every 10 seconds on Windows. Now the refresh fires as its own
background task so the process list can lag by design without delaying CPU/memory/disk/net or the
sample timestamp itself. Same pattern as latency probes — the measurement is always real, just
slightly older. Added a regression test and cleanup of the background task on shutdown.


### Phase 16: a phone that sleeps, panels that can never fill, and a window narrow enough to see move

Four things reported from using both halves, plus a question about the Windows machine.

**A phone went "offline · not currently connected" the moment its screen was turned off**, and came
back the moment it was turned on. Nothing was killed and nothing crashed: `FOREGROUND_SERVICE` keeps
the *process* from being reclaimed and says nothing about the application processor, which suspends
within seconds of the screen going off unless something holds a wake lock. A suspended SoC does not
run kotlinx's timer thread, so the sample loop's `delay(10s)` and the push loop's
`delay(pushInterval)` simply never fire — and the socket goes unread and unwritten with them. The
service was there the whole time, asleep, exactly as the OS intends by default.

`CollectorService` now holds a `PARTIAL_WAKE_LOCK` for as long as it is collecting, tagged
`sentinel:collector` so it is identifiable in `dumpsys power`, not reference-counted (START_STICKY
can redeliver a start, and a surplus acquisition would outlive the single release), with no timeout
(a timeout reintroduces this exact bug, later and harder to reproduce), and released on every path
that stops collecting. Failing to acquire it is logged and survived rather than fatal — a collector
that samples only while the screen is on is degraded; one that refuses to start is useless.

The battery cost is real and is the point of the trade, not a side effect: a partial wake lock keeps
the CPU out of suspend, which is the largest thing an app can do to a phone's battery. It is right
here because the alternative is not a cheaper collector, it is one that reports nothing for the
majority of a phone's day — and the manifest's own specialUse justification ("monitoring must
continue while the app is backgrounded or closed") is simply false without it. The cheaper mechanism
does not reach: `setExactAndAllowWhileIdle` is throttled to roughly one firing per app per minute
awake and far less under Doze, so it cannot drive a 10s cadence, let alone live mode's 1s. And the
wake lock does **not** buy immunity from Doze, which ignores wake locks outright once a device has
been stationary long enough — the battery-optimisation exemption the collector screen already offers
when `batteryOptimizationExempt` is false is the other half of the same guarantee, not an
alternative to it.

Not covered by a test, and worth saying so: the collector's 40 Kotlin tests are pure JVM unit tests
with no Android framework in them (`make mobile-collector-test` needs no emulator, deliberately), and
a `PowerManager.WakeLock` cannot be exercised there. It compiles and the suite is green; whether the
lock actually holds through a screen-off is a claim only a real phone can settle.

**Live Monitoring's default window is 1m, on both halves.** Every window draws the same samples at
the same rate; all the width changes is how far one new second moves the trace, so the narrowest
option is the one that reads as live and the widest is the one that reads as frozen. Somebody who
wants more context can widen it; somebody staring at a chart wondering whether it has stopped cannot
tell that the fix is to narrow it. `DEFAULT_LIVE_WINDOW_SECONDS` is 60 (was 120) and the web picker's
"2m" is now a literal rather than the default it used to name.

The phone has no picker, which is the other half of the argument for putting it at the lively end
rather than the middle: `chartFrame.ts`'s `MAX_POINTS` is 60 (was 180). That renderer has no time
axis — x is the point's index, so the count *is* the window, and at the live 1s cadence 60 points is
the same one minute the web page now defaults to. At 180 each new sample moved the trace by a third
of what it does at 60, which is a chart drawing every point and visibly doing nothing.

**Android's CPU, disk-I/O and process panels are gone from Live Monitoring rather than permanently
empty.** `HistoryScreen` and `DeviceHistoryPage` have made this decision since Phase 10b; the two
live surfaces never did, so a phone rendered three panels reading "No data yet." forever — which is
how a working collector comes to look like a broken agent, the precise impression
`lib/deviceReadings.ts` exists to prevent. Both live surfaces now fetch the device and gate on
`platform`, and both state the exclusion in words rather than being quietly shorter.

The gate is deliberately "appear when known to be a desktop", never "disappear when known to be a
phone": a CPU card drawn for one frame and then taken away is the same wrong impression, just
briefer. The panels every platform can fill are not gated at all, so the page is never blank while
that fetch is in flight, and a failed fetch is a settled answer that falls back to desktop.

**A sample that overruns its own interval now says so in the agent's log.** This is the one agent
failure mode invisible from every other vantage point: it connects, handshakes and pushes exactly as
a healthy agent does, and the only symptom is samples stamped further apart than the resolution they
claim — which surfaces as a live chart advancing in jumps and reads as a slow network, a server
problem, or a chart bug. `_warn_if_overrunning()` logs the measured cost, the budget it missed, and
`sentinel-agent sample --timing` as the next step, once immediately and then at most once a minute
(a machine that is slow is slow on every sample; a warning per second buries the log it is trying to
make readable). Recovering re-arms it, so a burst that clears on its own is two events rather than
one open-ended one.

It exists because of where this project is developed. The two most expensive probes in a sample cost
almost nothing on macOS *because* the OS denies them and they fail fast, and real milliseconds on
Windows where they succeed — so the machine with the problem is the only one that can report it, and
until now it had no way to.

174 agent tests (was 170), 81 mobile JS, 40 Kotlin, and 73 web — 73, not the 65 recorded in Phase 15:
nothing here adds a web test, and `vitest run` counts 73 across six files, so that earlier figure was
miscounted the same way the Kotlin one was. The backend was not touched and its suite was not re-run.

**The test suite has its own database role now, and that is a correctness fix rather than
housekeeping.** Rotating `sentinel_app`'s password for the public Funnel front door — an `ALTER
ROLE`, which is the correct way to rotate it — broke 222 backend tests that had nothing to do with
it. A Postgres role is cluster-wide and a grant is per-database, so `sentinel_test` being its own
database bought no isolation at all from the login it connected with: the suite and the real
deployment were sharing one credential on one cluster, and `.env.test` is committed while a real
deployment's password is not. The only two repairs available were to commit the live credential or
to hand-patch `.env.test` after every rotation, which is a chore that silently becomes 222 confusing
failures the one time somebody forgets.

The fix cost no code: migration 0001 has taken its role name from `APP_DB_ROLE` since Phase 1 and
creates it when missing (roles being cluster-wide is exactly what makes that `IF NOT EXISTS` guard
idempotent), and 0003 and 0005 grant to the same setting — so pointing `.env.test` at
`sentinel_app_test` was configuration. Verified at the cluster: the new role is NOSUPERUSER,
NOBYPASSRLS, NOCREATEROLE, and `test_rollup_tenancy.py` passes against it, which is the real proof
RLS is still enforced rather than the attribute flags being the claim.

What is new is `conftest.py`'s `_no_shared_credential()`, the twin the existing "database name must
end in `_test`" assertion never had. Its second check is the one that earns its place: migrations
grant to `APP_DB_ROLE` while the engine connects as whoever `DATABASE_URL` names, so a half-finished
edit to that file grants one role and logs in as another — and fails as an authentication error
naming neither setting. Both assertions were proved by reproducing the conditions they exist for:
`APP_DB_ROLE=sentinel_app` now stops the run with a sentence naming the cause, where before it
produced 222 `InvalidPasswordError`s.

### The Windows agent, and whether the project should move there

Asked whether to install Claude Code on the Windows machine and move the project to it. Mostly
overkill, for a specific reason: **the defect it would be moved to fix is already fixed in this
tree**. The binary that machine is running was built by the CI matrix on 2026-08-25 and predates
every Phase 15 sampling change — the process walk and `net_connections()` throttles, the
`asyncio.to_thread` move, and `idle()` reading the socket while it waits. Those are exactly the
changes that stop a Windows agent from being unable to keep a 1s budget, which is what a trace that
crawls and occasionally stalls looks like. The path to that machine is a rebuild and a re-download,
not a second development environment.

Moving the tree would also split a setup that is entirely anchored here: the release keystore in
`~/.sentinel-keys/`, the Tailscale Funnel front door, TimescaleDB and Redis in Docker on this Mac,
and this Mac's own launchd agent. Two working copies of a repo whose halves are wired to one host is
a cost paid continuously for a benefit needed occasionally.

What a Windows checkout *would* genuinely buy is a minutes-long edit/run loop against Windows-only
behaviour instead of a CI round trip — which is real, and is why `sample --timing` and the overrun
warning above exist: both were built so that question can be answered from the machine that has the
problem without a development environment on it.

**The matrix was run and all four binaries are published**, which retires Phase 15's standing note
that the Windows machine could not get the sampling fix. Run 33007520006 off
`review/codebase-fixes`: four green jobs plus the merge, windows-x64 in 1m3s, and its smoke step ran
`--version` *and* `sample` under `bash -e -o pipefail` — `sample` being the one that proves psutil's
compiled extension made it into the bundle. Every artifact's SHA-256 and byte count was matched
against the manifest on download and again after publishing, and the freshly built macOS arm64
binary was run here: it answers `sample --timing`, a subcommand that exists only in the new code, so
the fix demonstrably shipped rather than merely having been committed before the build.

The manifest was **merged, not replaced** — CI does not build the APK, and `merge_manifests()` keys
on `(os, arch)` with the newest `built_at` winning, so the android entry survived while all four
desktop entries were superseded. The running server needed no restart: `download_service` documents
its refusal to cache the manifest for exactly this case, and its catalogue was re-read in-process to
confirm `unavailable_reason` is None, all five builds resolve, and a `../../../etc/passwd` filename
is still refused by the allow-list.

This Mac's own LaunchAgent was restarted onto the fixed code (an editable install, so the source was
already there and only the running process was stale) — reconnected as the same `device_id`, so its
history is continuous, and its log carries no overrun warnings, consistent with the 1.0ms it
measures.

**The APK was rebuilt and re-registered**, because the wake lock is a native manifest change and
Metro fast-refresh cannot carry one — the registered APK would otherwise have been the app without
the fix this phase is mostly about. `make mobile-apk` runs `expo prebuild --clean` itself, which is
what merges the collector module's manifest, and the check that matters is that
`aapt2 dump permissions` on the *built* APK lists `android.permission.WAKE_LOCK`: the module's
manifest being right is not the same claim as the merge having happened. `apksigner`'s certificate
SHA-256 was matched against `keytool`'s own fingerprint for the keystore rather than against the
value written down here (`A6:A7:8F:80…8469`, the real release key, not React Native's public debug
one), and the Funnel URL plus this session's new Android-gating copy were both found in the shipped
**Hermes bytecode** via `strings` — plain `grep` finds nothing in it, which is its own small trap
already recorded in Phase 14.

Two absences were verified as well, since both are failure modes this project has actually shipped
before. The APK carries **no** `networkSecurityConfig` and **no** `usesCleartextTraffic` at the
application level, which is correct now that `EXPO_PUBLIC_API_URL` is the `https://` Funnel address —
Phase 11's cleartext exception exists for plain-http backends and an https one needs none. And
because no config is generated at all, the RN template's own debug-variant `usesCleartextTraffic`
is left unopposed, so the Metro dev loop still works: that is precisely the Phase 13 defect where a
config dropped into `src/main/res` silently outranked it and killed `expo run:android`. The Kotlin
suite was re-run against the regenerated project too, since `android/` is generated and Phase 13's
other lesson is that regenerating it is exactly when unrelated Gradle targets break.

`agent/dist/` now holds five artifacts whose manifest matches the bytes on disk for every one, the
served APK is byte-identical to the one just built, and the server's own catalogue re-reads all five
with `unavailable_reason` None.

### Phase 17: the insights layer stops calling a hosted model

Asked to remove the dependency on paid/hosted AI APIs. Only one thing in the product had one: Phase
8's incident summary and root-cause analysis. **Detection never did** — Phase 6's EWMA/MAD baselines,
Phase 7's Holt-Winters and Theil-Sen fits, and Phase 15's default rule set are all statistical models
fit on the account's own data, which is what CLAUDE.md's original "the LLM explains; it does not
detect" rule was protecting. So the change is confined to the explaining half.

`app/ai/` is gone — `prompts.py`, `client.py` and `insights_service.py` — replaced by
`app/insights/`, holding `generator.py` (pure templates over the `SignalBundle`) and `service.py`
(the orchestration, moved unchanged). The `anthropic` dependency is dropped from `pyproject.toml`
and the three `ANTHROPIC_*` settings from `config.py`; one left in an existing `.env` is ignored
rather than read.

**The boundary moved down a level rather than being swapped in place.** `AIClient`'s Protocol took a
system prompt and a user prompt, which is the wrong shape for a template pass: it wants the
structured bundle, not prose assembled for a model to re-read. `InsightGenerator` takes the
`SignalBundle` directly. The Protocol is kept even though there is one implementation, because the
constraint is no *hosted* model rather than no model — a locally-run one could be substituted without
the service, the worker or the route changing.

Three things got structurally stronger, not merely cheaper:

* **The prompt-injection boundary is retired rather than weakened.** Phase 8 fenced the bundle in
  `<incident_data>` and *instructed* the model that a user-chosen device or rule name is never a
  message to it. Nothing interprets a string now, so an adversarial name has nowhere to act. The
  injection test survives in `test_insight_generator.py` asserting the stronger property: the text
  cannot reach the output at all.
* **"Never synthesise" became enforceable in prose.** Every clause is dropped when its field is None
  — a thin bundle yields fewer sentences, never vaguer ones — which is `analysis/health.py`'s
  "exclude an unreported component, don't score it 0" applied to language. A template cannot
  hallucinate, but it can very easily print "None" or a fabricated zero, which is the same lie by a
  different route; that is what most of the 21 new tests are about.
* **The route and its test lost a whole layer.** `_ai_client_dependency`, the 503, the substituted
  `FakeAIClient` and the second `create_app()` the old test needed are all gone: the plain test
  client now exercises the real path end to end and asserts the real sentence.

**The text stays stored rather than computed at read time.** Tempting, now that generation is free
and pure, and wrong for the same reason `AnomalyEvidenceChart` draws the snapshotted and live
baselines as two lines: the bundle drifts, so a resolved incident recomputed today would describe the
machine's present state instead of the incident's. The fingerprint cache is kept for what survives
the API bill going away — `*_generated_at` meaning "when this explanation was reached" rather than
"when a sweep last ran". `*_model` keeps its meaning too, now carrying `sentinel-templates/v1`, so
rows written by Haiku/Sonnet before the switch stay distinguishable.

**Two real defects came out of running it against the real database, neither reachable from the
tests.** Generating text for the six most recent real incidents is what found them:

* Every incident read **"health score is unavailable (device not found)"**. Not a fault: all six
  belonged to removed devices, and `build_summaries()` correctly filters soft-deleted rows while
  `session.get()` above it still finds them. The *wording* was the bug — the same conflation
  `lib/deviceNames.ts` exists to prevent on both frontends, where "removed" is permanent and
  explainable and "missing" is a fault. `build_signal_bundle()` now checks `deleted_at` and says
  "device has been removed", keeping the vaguer phrase for the genuinely unexplained case.
* A device removed days earlier still carried an exhaustion row projecting +237.6 points/day, which
  rendered as **"reaches capacity in about 0s"** — a claim about a future already overtaken, and one
  that in the disk case would have sent somebody to go and free space. Nothing recomputes a stored
  forecast once its device stops reporting, so both the trajectory sentence and the next-step advice
  now require the projection to still be ahead of `now`.

Both fixes were confirmed against those same real incidents afterward, not just against the new
tests. Docs were corrected where they had become instructions to do the wrong thing —
`ARCHITECTURE.md`'s stack table and layer 6, `WORKING_WITH_CLAUDE.md`'s "put a key in `backend/.env`"
section, `ROADMAP.md`'s Phase 8 line, and `PACKAGING.md`'s list of optional integrations — and both
frontends stopped captioning the cards "Generated by Claude Haiku/Sonnet", which was by then simply
false on screen.

571 backend tests (was 553: −4 for the deleted `test_ai_prompts.py`, −1 for the 503 test that no
longer has a reachable condition, +23 new), 73 web, 81 mobile JS. The agent and Kotlin suites were
not touched and not re-run.

**Verified against the restarted server, not just the tests.** `make serve` was restarted onto the new
code — and the strongest evidence arrived unprompted: within its first 60s sweep the `InsightsWorker`
had already written all 12 open incidents with `summary_model = sentinel-templates/v1`, so the
background path works end to end with nothing asking it to. Then in the browser: the list renders a
real generated summary per card, the detail page captions both cards "Generated from this incident's
evidence by sentinel-templates/v1", the root cause ends "(device has been removed)" — this session's
own fix, on a real page rather than in a test — and **"Regenerate insights" returned
`POST .../regenerate → 200 OK`**, the call that returned 503 for the whole of Phase 8. A fresh tab
loads with zero console logs.

Worth knowing for next time: all 12 incidents in the dev database belong to **removed** devices, which
is why every card reads "(removed)" and no root cause has a health score. That is real data state, and
it is what made the "device not found" wording bug visible at all.

### The nav bar overflowed, for the oldest reason in flexbox

Reported from looking at the console at pane width. `AppLayout`'s header was a single non-shrinking
flex row: brand + eight nav links on the left, email + Sign out on the right. Three symptoms, one
cause — **a flex item's default `min-width` is `auto`, i.e. its content**, so the left group could not
shrink and pushed the *page* sideways instead. "Settings" ran straight into the email because
`justify-between` leaves no gap at all once its two groups overflow, and "Add a device" broke across
two lines because nothing stopped a label wrapping mid-phrase.

`min-w-0` on the left group lets it shrink, `overflow-x-auto` on the `<nav>` puts the excess in the
nav's own scroll region instead of the document's, `gap-x-6` is a real gap the two groups cannot
close, and `whitespace-nowrap` keeps a label on one line. The email is `truncate max-w-56` and
`hidden sm:inline` — truncated rather than dropped at desktop widths, because which account you are
signed into is worth keeping on screen, but an unbounded address would push the sign-out button off
the edge by itself.

Measured at three widths rather than eyeballed, since "looks fine" is exactly how the original
survived: 1440 unchanged (all eight fit, no scroll, 485px between Settings and the email), 744
(`scrollWidth === clientWidth`, so the page no longer scrolls sideways; nav scrolls and all eight stay
reachable with the active item still bold), 375 (no page scroll, email correctly hidden). The fix only
engages when there is not room.

Not done, and worth knowing: **`AppLayout` has no test.** The web suite is pure-module tests
(`lib/*`), and a layout rule like this is only observable in a real browser at a real width — which is
how it was verified, and why the numbers above are recorded here rather than asserted anywhere.

### The APK, rebuilt so the phone stops crediting Claude

The registered APK still captioned both incident cards "Generated by Claude Haiku/Sonnet" — false the
moment the backend stopped calling either. A JS-only change, so unlike Phase 16's wake lock this one
*could* have ridden Metro fast-refresh to the emulator, which is exactly the trap: the emulator's dev
build is not the artifact `/download` serves, and the registered file predated the change.

Checked past "it built", in the order that catches the most:
`apksigner`'s V2 certificate SHA-256 matched `keytool`'s own fingerprint for the keystore
(`a6a78f80…8469`, the real release key rather than React Native's public debug one) — matched against
the keystore, never against the value written down here. `aapt2 dump badging` confirmed
`com.sentinel.viewer` / 0.1.0 / label Sentinel, and `dump permissions` confirmed `WAKE_LOCK` survived
the prebuild merge, since `--clean` regenerates the manifest that Phase 16's lock lives in. In the
shipped **Hermes bytecode**, `strings` found "Generated from this incident's evidence." once and both
Claude captions zero times — plain `grep` finds nothing in Hermes and silently reports success, the
trap already recorded in Phase 14, re-confirmed here by running it and getting no match on a string
that is demonstrably present.

Two absences were verified too, both being failure modes this project has actually shipped: the APK
carries **no** `networkSecurityConfig` and **no** application-level `usesCleartextTraffic`, correct
now that `EXPO_PUBLIC_API_URL` is the `https://` Funnel address — and because no config is generated
at all, the RN template's debug-variant cleartext permission is left unopposed, which is the Phase 13
defect where a config in `src/main/res` silently outranked it and killed `expo run:android`.

The Kotlin suite was re-run because `prebuild --clean` regenerates `android/`, and Phase 13's other
lesson is that regenerating it is exactly when unrelated Gradle targets break: 40 tests across 8
classes, 0 failures.

Then the chain end to end: registered into `agent/dist/manifest.json` (merged — still 5 builds, the
four CI desktop binaries untouched), the running server re-read the catalogue **without a restart**
exactly as `download_service` documents, and the APK was pulled back through the real authenticated
route. Four SHA-256s identical — what Gradle built, what is in `dist/`, what the manifest records, and
what the route actually served (`9102e567…93dd`, 85,523,179 bytes; a different hash from the previous
build, because APK builds are not byte-reproducible). `/download`'s Android card renders the new
checksum, "signed", and the Android install steps rather than desktop CLI commands, with the
`shasum` command keyed to the *browser's* macOS rather than the build's Android — Phase 14's fix,
still holding.

Nothing here is committable: `agent/dist/` is gitignored in full, so the rebuild is a local artifact
change and only this record lands in git.

### Layer 4: a model that is actually trained

Asked for a genuinely built-and-trained ML model, completable in a day. The
honest starting point was that this project had none: detection is EWMA/MAD
(Phase 6) and Holt-Winters/Theil-Sen (Phase 7), both refit from scratch on every
tick with no learned artifact anywhere, and explanation became templates in
Phase 17. Nothing here was ever a trained model, and `find` for `.pkl`/`.joblib`
/`.onnx` across the repo confirmed it.

**The pick was layer 4, because it was already designed and never built.**
docs/ARCHITECTURE.md has said since Phase 0: "`river.anomaly.HalfSpaceTrees`
online, or nightly-retrained IsolationForest over the joint metric vector.
Catches correlated weirdness no single threshold sees." Building it closes a
documented gap rather than bolting ML onto a system that did not ask for it —
and it is a real capability gain, not a duplicate, because every existing
detector is *univariate*. `app/analysis/multivariate.py` is the pure half:
feature assembly, `IsolationForest.fit()`, scoring. There is a test asserting
the thing the layer exists for — a vector whose every component is individually
ordinary, flagged because the *combination* never occurs.

**Feasibility was measured before anything was written**, because the whole plan
dies without data: this Mac had 3,361 real 1m rows over 4.7 days. That check
also decided the feature set. `ctx_switches_per_s` and `active_connections` sit
in the same rollup table and look useful, and are **100% populated on Windows
and 0% on macOS** — psutil's `net_connections()` needs root there (Phase 15) and
`ctx_switches` is a per-interval delta (Phase 2). Including either would drop
every macOS row under the no-imputation rule, i.e. no model at all on the
platform this is developed on. Seven features survive; the exclusion is
recorded beside them.

Two rules are this codebase's rather than scikit-learn's, and both are the same
rule twice. **A row missing any feature is dropped, never imputed** — a column
mean or a zero is exactly the synthesised metric CLAUDE.md forbids, and it is
worse here than on a chart, because the model would *learn* the fabricated value
as normal and then never flag it. **Below 200 usable rows there is no model
rather than a bad one**, matching anomaly.py's WARMUP_SAMPLES.

**Scores are a percentile against the device's own training distribution**, not
sklearn's raw `score_samples()` output, which is unbounded, dataset-specific and
not comparable between two machines. The stored quantiles are what make "more
unusual than 99.4% of this machine's own history" sayable.

Training is `backend/scripts/train_novelty_model.py` (`make train-novelty`), run
**by hand, not by a worker** — deliberately, as the first step. A scheduled
retrain is a small addition on top, but a model whose training nobody watches is
a bad thing to introduce to a system whose premise is that every number came
from somewhere real. It lives under `scripts/` rather than `app/` because it
enumerates every device across every tenant, which is precisely what
`test_unscoped_import_guard.py` exists to keep out of `app/`.

**Validated past "it fit."** Scoring the training history back and reading the
top-scoring real moments gives unmistakable load events — `cpu 99.8% / load1
23.4`, `cpu 97.3% / load1 44.6` — against a median row of `cpu 12.1% / load1
1.99`. Those spikes are this project's own build and test runs. Worth being
precise about what proves what: the ~1% of history scoring >=99 is *by
construction* of the percentile mapping and confirms the calibration, not the
detection; the top-moments check is what confirms the detection.

Serving is `GET /devices/{id}/novelty`, 200 with `available: false` rather than
404 when there is no score — the device exists, and "nothing trained yet" is a
state to explain. Each of the four no-score cases carries its own reason, and
the last one names the missing columns, which is docs/ANDROID_METRICS.md's rule
arriving in a feature written long after it. Models are cached by
`(path, mtime_ns)`, so a retrain is picked up on the next request with no
restart — download_service's posture with the build manifest. `joblib.load` is
pickle, and the docstring says plainly why that is acceptable (operator-written
files, never uploaded, never fetched) and what would have to change if it
stopped being true. Unset `NOVELTY_MODEL_DIR` is supported, like AGENT_DIST_DIR.

Verified through the live server against the real models: this Mac scores **95.2
on a model fitted to 2,827 of its own rows** — it was busy running these builds —
while the Android phone and the Windows box return the honest "no model yet".

One bug caught before it shipped, worth recording because the fix looked
harmless: the first attempt at silencing ruff's S608 put the `# noqa` comment
*inside* the triple-quoted SQL, making it part of the query text — and `#` is not
a Postgres comment, so every call would have been a syntax error. The
interpolation is guarded at import instead (FEATURES must all be identifiers),
mirroring config.py's ROLE_PASSWORD_RE.

593 backend tests (was 571), 73 web. Built on `feature/ml-anomaly` in four
commits so any slice can be dropped on its own.

Still to come, if picked back up: a retraining worker (the shape is the four
existing workers'); a `"multivariate"` fourth `rule_type` so a novelty spike can
open an incident through the same evaluator sweep and `state_apply.py` the other
three share; and per-platform feature sets so Android — which can fill neither
CPU nor load1 — gets a model over what it *can* measure rather than none.

**Seen rendered, and the number moved.** The card sits in its own column beside
Health and Time-to-capacity on /devices/:id/history, at both pane width and
1440, in a clean tab with zero console logs and `GET .../novelty -> 200`. The
best evidence was accidental: the score read **95.2 while this Mac was running
the builds, 48 once they finished, and 43 a few minutes later** — the model
tracking a machine that genuinely changed, rather than printing a constant. It
reads "ordinary for this machine · vs 2,827 of its own readings" at rest.

### Layer 4 can alert: a fourth rule type

The novelty model detected and told nobody — readable at
`GET /devices/{id}/novelty` and nowhere else. A multivariate rule is now an
ordinary `alert_rules` row: same evaluator sweep, same OK/PENDING/FIRING
machine, same incident correlation, same notification path. That refusal to add
a second evaluation path is what Phases 6, 7 and 8 each made, and it is why this
was a small change rather than a large one.

**No new tables and no new columns.** Migration `0015` widens three CHECKs and
adds one. The added one is the interesting half: `(rule_type = 'multivariate') =
(metric = 'novelty_score')` makes the type and the reserved metric imply each
other **in both directions**. Without the reverse, a *threshold* rule could be
pointed at `novelty_score` — accepted, perfectly plausible on the page, and
silently never firing, because nothing writes that column. There is a test for
that direction specifically, and a second asserting the database refuses it even
past the Pydantic validator.

A nullable `metric` would have been the more honest model, since a multivariate
rule genuinely judges the joint vector. It was weighed and rejected: `metric` is
read for display by both frontends, `notify.py` and the insights generator, so a
null pushes a check into all four for one rule type. A reserved name keeps them
working, and `RULE_METRICS` widens **only** the alert_rules CHECK —
`anomaly_baselines` and both forecast tables keep their narrower one, so nothing
can create a baseline or a forecast for what is a model output rather than a
measurement. `AnomalyBaselineOut.metric` stays the narrow type for the same
reason, on both the wire schema and the web's types.

`score_devices()` reads every scorable device in one query, matching the "at most
three queries regardless of how many rules or devices" budget the sweep already
keeps, and runs only when the user actually has a multivariate rule. A device
with no model is **absent** from the result rather than present with a null, so
the state machine sees unknown rather than normal — an open alert is not
auto-resolved by a model being retrained away, matching Phase 5's rule.

**Running it immediately found two defects, both the same class as Phase 17's
"(None None)" bug** — a new rule type reaching a display surface nobody taught
about it. The insights generator printed the raw key ("CPU, memory and
novelty_score alerted") and recited a hardcoded "across threshold, anomaly and
forecast rules", which described the wrong set the moment a fourth type existed;
it names the kinds actually present now. And both `_condition()` implementations
fell through to the threshold shape, rendering `> 71.0` — 71 of what? A
percentile is not a reading, and saying so is the whole difference. `notify.py`'s
`_body` branches too, because `novelty_score = 80.9` leaks a schema detail into
an email. The firing notification then dropped its `_condition` clause after
reading it back: stacking the two said "unusual" three times.

Verified end to end against a real rule on this Mac rather than fixtures: live
novelty **80.9**, rule at `> 71`, **pending** on the first sweep, **firing** on
the second, deduped on the third, and the event **correlated into an incident** —
the same pipeline, demonstrably. The notification reads "Its readings together
score 79/100 for how unusual they are for this machine, past the 71 you set."

605 backend tests (was 593), 73 web, 81 mobile JS. Both rule forms gained a
fourth "Combination" toggle, and both hide the metric picker for it — a
combination rule judges all of them, and a select with one option is worse than
a sentence saying so. Adding it made the typechecker find two places that had
assumed `metric` is always measured (`AnomalyEvidenceChart`, and RuleForm's
metric state); both are narrowed rather than cast, so the reasoning stays
checkable.

The verification rule was retuned rather than deleted: **"Unusual combination",
`novelty_score > 95 for 300s`** on the `phase10a@example.com` account. 71 was
picked to make it fire during verification; 95 is the threshold worth actually
running, and it resolves the event that firing opened.

**Seen in the browser**, and it found one more raw-key leak that three earlier
passes had missed: both rules *lists* rendered "novelty_score > 71 for 0s". The
generator, notify.py and the rule form had each been taught about the new type;
the list summaries had not. They now read "unusual combination of readings >
71/100" — with the /100, because the threshold is a percentile and a bare 71 is
71 of nothing. That makes five display surfaces this rule type had to reach, and
the fourth and fifth were both found by looking rather than by a test.

The form itself renders as designed: four toggles, and selecting Combination
replaces the metric `<select>` with "All of them at once — the novelty score."

One tooling note, recorded so it is not re-diagnosed as a product bug: while the
Browser pane had an *emulated* viewport (`resize_window` with explicit
width/height), pointer clicks landed on the right CSS coordinates and had no
effect — neither "New rule" nor "Edit" opened anything, with every API call 200
and no console error. Clearing the emulation (`preset: "desktop"`) fixed it
immediately. Nothing wrong with the page.


### Finishing: the phone, and a second Hermes trap

**The mobile app was verified against the running Metro bundle**, not just
typechecked. The dev client was pointed at `10.0.2.2:8081` — the emulator's host
alias — after its saved entry turned out to name a *stale* LAN IP
(`10.23.132.19`, where this Mac is now `10.233.129.19`), which is the same
address-drift Phase 14 already records for the APK. The app loaded against the
Funnel URL and every string added this session is in the bundle it is serving.

**The APK was rebuilt**, because the registered one predated the whole session —
the insights templates, the nav fix, the novelty card and the fourth rule type.
The same chain as before: `apksigner`'s certificate SHA-256 matched `keytool`'s
own fingerprint for the keystore (`a6a78f80…8469`), `WAKE_LOCK` survived the
prebuild merge, no `networkSecurityConfig` and no `usesCleartextTraffic` (correct
for an `https://` backend, and it leaves the RN template's debug-variant
permission unopposed), 40 Kotlin tests green after `prebuild --clean` regenerated
`android/`, and all four SHA-256s agree — built, on disk, in the manifest, and
served through the real authenticated route.

**A second Hermes trap, one layer past the one Phase 14 records.** That entry
says plain `grep` silently finds nothing in Hermes bytecode and to use `strings`.
That is necessary and not sufficient: **Hermes stores any string containing a
non-ASCII character as UTF-16LE**, so `strings` cannot see it either. Two of this
session's strings — "All of them at once — the novelty score." and " · unusual
combination of readings " — contain an em-dash and a middle dot, and both came
back *zero* under `strings` while "Combination" (pure ASCII) came back one.
Scanning the raw bytes for both encodings settled it: `ascii=0 utf16le=1` for the
first two, `ascii=1 utf16le=0` for the third. Trusting `strings` would have meant
concluding a shipped change had not shipped. On macOS `strings` has no `-e`
flag, so the check has to be a byte scan rather than a different flag.

Also recorded so it is not re-diagnosed: the `served` hash in the first
verification pass was the hash of a **401 error body**, because the minted access
token had expired inside its 15-minute TTL. The command reported a hash either
way; only the HTTP status showed it was worthless.

**Seen on the phone.** Driven through the real emulator against the real
backend: More → Alert rules shows the retuned rule as "unusual combination of
readings > 95/100 for 300s", New rule offers four toggles, and selecting
Combination replaces the metric row with "All of them at once — the novelty
score." and renders the explanation — em-dash, en-dash and curly quotes all
correct, which is what confirms the JSX-escape fix from two commits earlier.

Doing that found three more strings that predated a change rather than broke
with one, all the same class as everything else this session: the phone's More
menu described Incidents as carrying "the AI summary" (false since Phase 17) and
alert rules as "the thresholds, anomaly and forecast rules" (three of four), and
both rules screens' subtitles named three sources. Fixed on both halves, plus
three stale module docstrings. The web one is confirmed rendering "the four
sources"; the three mobile strings are typechecked and were not re-seen on the
device — the dev launcher would not reconnect afterwards, and they are text in
files whose neighbours were just watched rendering.

### The APK, once more, for three strings

Rebuilt again immediately after the copy fixes, because the previous rebuild
landed **twelve minutes before them** — the registered artifact was stale by
exactly one commit, which is the same trap Phase 13 and Phase 14 each record and
which is easiest to fall into right at the end, when the work feels finished.
Checking the manifest's `built_at` against the last commit's timestamp is what
caught it, and is the cheap check worth doing before calling an APK current.

The bytecode check was written as **six assertions rather than three**: the new
strings present *and* the old ones absent. A "present" check alone cannot tell a
rebuild from a no-op, since the new copy could be there while the old copy also
still is, in a screen that was missed. `the three sources` and `with the AI
summary` both come back zero.

It also re-confirmed the UTF-16 finding rather than taking it on trust: `the four
sources` and `All of them at once` are UTF-16LE (both share a sentence with an
em-dash), while `plain-English summary` and `threshold, anomaly, forecast and
combination` are plain ASCII. Same file, both encodings, exactly as the rule
predicts.

Signature matched the keystore's own fingerprint, `WAKE_LOCK` present, no
cleartext config, and built/manifest/served SHA-256s all agree.

### A verification pass over the whole codebase, and what "measure it" cost twice

Asked to check everything works and suggest improvements. Every suite was run rather than trusted:
606 backend, 174 agent, 73 web, 81 mobile JS, 40 Kotlin, plus both typecheckers, both linters, the
production web build, and the live server's auth gating. The live system was checked too — uvicorn
serving on :8000, this Mac's launchd agent connected, migrations at head, every authenticated route
401ing without a token, and `/docs`, `/redoc` and `/openapi.json` all correctly 404 under
`ENVIRONMENT=prod`.

That last one retired a debugging note this file used to carry: Phase 15 recorded "`/openapi.json`
is what settled it in one request", which stopped being true when the server moved to prod mode for
the Funnel front door. The technique now needs a dev server.

**`make lint` was red**, on an import-order error in `agent/sentinel_agent/cli.py` — `collectors`
sorting after `config`. Trivial, auto-fixable, and worth recording only because a red lint is a
broken tripwire: everything it would have caught next goes unnoticed too.

**The novelty model cache was a leak wearing a cache's clothes.** `_CACHE` is keyed
`(path, mtime_ns)` with no eviction, and the only `.clear()` calls in the tree are in tests. A
retrain writes a new mtime, so the superseded model stays pinned forever despite being unreachable —
nothing can ask for an mtime the file no longer has. Measured rather than estimated: **26.5 MB per
unpickled forest**, so a nightly retrain over two devices adds ~53 MB a night to a process that runs
for weeks. Fixed by dropping other mtimes for the same path on insert.

The regression test was checked against the bug before being kept — eviction removed, it fails with
`assert 2 == 1: the superseded model is still pinned in memory`. This file already records that a
test can pin a bug in place as easily as it can catch one (the Windows ACL lockout, whose test was
named "survives"), so a new test that has never been seen failing is not yet evidence of anything.

**`novelty_score > 95` was leaking onto four more display surfaces, live.** The commit twelve hours
earlier that fixed both rules lists named five surfaces for this rule type. There were nine. The
alerts triage page and the incident timeline, on *both* clients, still fell through to the generic
`{metric} {comparison} {threshold}` branch — and this was not theoretical: the dev database had a
combination alert **firing** (`multivariate | novelty_score | > | 95 | firing | 98.8`), so `/alerts`
was rendering the raw schema key and a bare 95 at the moment it was looked at.

Rather than patch a fourth and fifth copy of the same conditional, the copy moved into
`lib/alertCopy.ts` on each side — pure, string-returning, tested, the shape `deviceNames.ts` and
`deviceReadings.ts` already have. Eight tests per client pin both failure modes this shared branch
has actually shipped: an anomaly event's null comparison/threshold rendering as "null null", and a
multivariate event's reserved metric key reaching the screen. That directly answers this file's own
complaint that the fourth and fifth surfaces "were both found by looking rather than by a test."

#### Two claims that were wrong, and the measurement that corrected each

The first pass reported that a 29-byte `JWT_SECRET` in `.env.test` produced roughly 3,000 of the
suite's warnings. Both halves were wrong. `.env.test`'s secret is 34 bytes; the warning comes from
exactly one test (`test_jwt.py::test_wrong_secret_is_rejected`) that signs with a deliberately wrong
key, and it fires **once**. The actual noise was **4,808 of 4,811 warnings from joblib**, which sets
`array.shape` directly when unpickling — deprecated in NumPy 2.5, and emitted once per array, so one
forest emits hundreds. joblib 1.5.3 is the latest and still does it, so there is no upgrade to take.
Ignored narrowly by module and message in `pyproject.toml`, with the reason written down: this is a
real forward-compatibility problem, and when NumPy *removes* the behaviour, loading a model breaks.
4,811 warnings → 3, which is the point — the three real ones were invisible.

The second was worse, because the fix was already written. The blocking-work-on-the-event-loop
finding was correct — `joblib.load` and `score_row` both ran unwrapped in a request handler and in
the 15s sweep, which is exactly what Phase 7 moved the ETS fit off the loop for. The remedy was
assumed to be `asyncio.to_thread` on both. Measuring it with a 1ms ticker coroutine watching for the
worst stall showed the opposite:

```
score 50 devices INLINE              median  91.67 ms worst stall
score 50 devices via to_thread       median 109.58 ms worst stall
joblib.load INLINE                     median  9.87 ms
joblib.load via to_thread              median  5.07 ms
```

`to_thread` only pays off for work that releases the GIL. `joblib.load` does file I/O and roughly
halves; sklearn's `score_samples()` holds the GIL, so a worker thread does not free the loop — it
adds a hop and GIL contention on top of the same block. So the load is offloaded and the scoring is
deliberately not, with the numbers in the docstring so the "obvious" change is not made again.

Profiling the scoring then explained its shape: `score_samples()` costs **1.76ms for one row and
1.75ms for fifty**, i.e. almost entirely fixed per-call overhead. Since every device has its *own*
forest there is nothing to batch across devices, so a sweep pays ~1.8ms per device with both a model
and a multivariate rule. Two devices here. At fifty it wants a process pool or a different schedule,
not another `to_thread`.

One structural note worth keeping: **ruff's `ASYNC` rules are enabled and could not have caught any
of this.** They fire on known-blocking calls written directly inside an `async def`, and every
blocking call here sits in a *sync* helper that async code calls. A clean lint run is not evidence
that nothing blocks the loop.

#### The file this is in

`CLAUDE.md` had reached 184 KB and was being loaded into context in full, every session. It is now
62 KB — the hard rules, stack, commands, conventions, current state, and every "don't break these"
invariants section, including a new one promoting layer 4's rules out of the narrative where they
would otherwise have been lost in the split. Everything else moved here, byte-for-byte.

Not verified, and worth saying plainly: the four frontend changes are typechecked, linted and
unit-tested, and the rebuilt bundle was confirmed being served by the live server without a restart
(static files are read per request), but **none of them was seen in a browser** — the firing event
belongs to an existing account whose password is not available here. Given how many of this rule
type's surfaces were found by looking rather than by testing, that is a real gap, not a formality.

988 tests green in total: 606 backend (+1 for the cache guard), 174 agent, 80 web (+7), 88 mobile JS
(+7), 40 Kotlin. `make lint` and `make typecheck` both clean.

### The APK, a fourth time: a download link that isn't a fetch()

Started from a real user report: the Funnel download page showed "The download failed. The build
may have been removed from this server" on a phone, while the server logs showed the exact opposite
— the request had reached `/downloads/agent/app-release.apk` and been served with a `200`. The page
served fetching the 85 MB binary into a `Blob` via `apiDownload()`, then handing the finished blob to
an `<a download>` link — meaning the *entire* file had to arrive in one unbroken JS-visible request
before anything reached disk, and any interruption on a mobile connection failed the whole thing with
a message that conflated "gone from the server" with "the network hiccuped," because the `catch`
block couldn't tell the two apart.

The fix minted a short-lived, filename-scoped ticket over the authenticated REST API and handed *that*
to a real `<a download>` link, mirroring the WebSocket ticket in `app/live/tickets.py` but not
single-use: a stalled transfer resumes with an HTTP Range request against the same URL, and `GETDEL`
would have burned the ticket on the download manager's own first partial request. The browser's own
download manager then owns the transfer — Range-resume included — which is exactly what a `Blob` in
JS memory could never do.

That got the transfer right and still didn't work, for two reasons that took a real phone to find
because `curl` could not reproduce either:

First, the ticket was minted *inside* the click handler and the resulting link clicked by JS
afterward. The theory was that a synthetic click issued after an `await` loses whatever "real user
gesture" a mobile browser needs to treat a same-origin download as a save rather than a navigation —
plausible, unfalsifiable from here, and wrong, or at least not the actual cause: minting the ticket
ahead of time so a genuine tap landed on an already-built `<a href>` changed nothing. Same for the
follow-up guess that Chrome auto-opens a finished download for preview in whichever tab requested it
and has no viewer for an `.apk`, so `target="_blank"` was added to point that at a background tab —
also no change. Two rounds of plausible browser-internals reasoning, neither one checkable without a
device, both wrong.

The actual cause surfaced only when the user checked what Chrome had actually saved: not
`app-release.apk`, but `app-release.apk.html`. `AgentBuildOut.download_url` is deliberately
unprefixed (Phase 1's "routes carry no `/api` prefix in FastAPI") — correct for `apiFetch`/
`apiDownload`, which add the prefix themselves, and exactly what the original blob-based version had
been going through the whole time. A real `<a href>` a user taps is a browser *navigation*, not a
`fetch()`, so nothing added that prefix for it. `WebConsoleMiddleware` runs before
`ApiPrefixMiddleware` strips anything (`app/main.py`, deliberately, so `/devices` can be both a route
and a page) and reads a bare `/downloads/agent/...` as a client-side route the SPA owns, serving
`index.html` — 471 bytes of the console's own shell — instead of ever reaching the download route.
Confirmed by replaying the exact request with `Sec-Fetch-Mode: navigate` and `Accept: text/html`,
the two headers a real link click sends and `curl` does not by default: the unprefixed URL came back
`text/html`, 471 bytes; the same URL with `/api` folded in came back the real 85 MB binary with the
correct `Content-Disposition`. Every `curl` check run earlier in this same investigation — and there
were several, all reporting the headers as correct — had used default `curl` headers throughout, so
none of them ever exercised the code path a real browser navigation takes. The blank white page the
user kept seeing was never a Chrome download-UI quirk at all; it was the console's own React shell,
loaded from a `content://` URI where its relative asset paths could not resolve, rendering nothing.

One structural note worth keeping alongside Layer 4's ASYNC-rules one: **a passing `curl` check is
not evidence a same-origin download link works**, for the same reason a clean lint run was not
evidence nothing blocked the event loop — the tool doesn't exercise the code path that matters.
`curl -H "Sec-Fetch-Mode: navigate" -H "Accept: text/html"` against a URL is the way to actually
check what `WebConsoleMiddleware` will do with it before believing a live phone test.

995 tests green in total: 613 backend (+7 for the ticket mint/redeem/scoping and the APK MIME type),
174 agent, 80 web, 88 mobile JS, 40 Kotlin. `make lint` and `make typecheck` both clean.

### Notification channels: three that existed, none that had ever delivered

The question was plain enough — "does any of this actually work?" — and the answer was no, for all
three channels, in a way that every surface had been reporting honestly the whole time.

Web push, email and FCM had been code-complete since Phase 5 and Phase 10a: correct best-effort
dispatch, correct per-channel isolation, correct gating, correct error copy. What none of them had
was a credential. `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` and `SMTP_HOST` were all
blank in `backend/.env`, and `frontend/mobile/google-services.json` did not exist. Every "firing"
notification since Phase 5 had been logged and dropped by the three unconfigured-guard branches in
`notify.py` — which is exactly the graceful-absence posture those branches were written for, and
also exactly why nobody had noticed: the system says "not configured" in a tone indistinguishable
from "not implemented", and the difference only shows up if you go and read the env file.

The account email was the sharper version of the same problem. The console offered to fall back to
the signed-in user's own address, which was `phase10a@example.com` — a real `users` row created
during Phase 10a's mobile-auth work, still the account in daily use, on IANA's reserved
documentation domain. It has no MX record and never will. So even a fully configured SMTP server
would have delivered to nowhere, and the UI's "we'll email your account address" would have been
true and useless at the same time.

**Setting up web push, and not trusting the key format.** The keypair is two different encodings of
the same secret, and getting either wrong fails silently in a place that looks like a server
problem: the private key is the raw 32-byte scalar, base64url unpadded, which is what pywebpush
hands to `Vapid.from_string()`; the public key is the uncompressed P-256 point (65 bytes, leading
`0x04`), which is what the browser wants as its `applicationServerKey`. This py_vapid has no
`public_key_urlsafe_base64()`, so the app-server key is derived directly with
`public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)`.

Rather than paste the keys in and let the browser be the test, the generation script asserted the
private key round-trips to the same public key, and a second script ran pywebpush's own
`webpush(..., curl=True)` — which builds the encrypted payload and signs the VAPID JWT but returns
the request instead of sending it. That caught nothing in the end, but it is the check worth keeping:
it proves the format against the library that will actually consume it, offline, before any state
exists in a browser. Two incidental findings from it — the signed header is lowercase
`authorization: vapid`, and `curl=True` writes the encrypted payload to `encrypted.data` in the
current working directory, which is stray repo litter if you run it from `backend/`.

`VAPID_SUBJECT` took the deployment's own Tailscale Funnel `https://` URL rather than a personal
`mailto:`. The spec accepts either; the subject is transmitted to the push service in the signed JWT
on every single send, so the Funnel URL says the same "here is who operates this sender" without
putting a personal address in front of Google and Apple a few thousand times a day.

**Verified by firing a real rule on real data.** The Mac was sitting at ~74.8% memory, so a
threshold rule at `mem_percent > 70` was genuinely true — no synthesised reading, no hand-inserted
event, no shortcut past the evaluator. It walked `ok → pending → firing` across two 15s sweeps
exactly as `step()` requires (`for_duration_seconds=0` still cannot fire on the tick it enters
PENDING), fired at `value_at_fire=74.9`, and pushed through `notify._dispatch` to
`web.push.apple.com` — Safari, so Apple's push service rather than Google's. Confirmed on screen.
Worth noting the gate that a direct `_send_web_push` call skips: the real path checks
`NotificationSettings.web_push_enabled` first, so that column being `true` had to be confirmed
separately from the subscription row existing.

**What the cleanup found.** Deleting the test rule afterwards surfaced an edge worth knowing about.
`alert_events.rule_id` is `ON DELETE SET NULL` and `alert_states.rule_id` is `ON DELETE CASCADE` —
both deliberate, because firing history is meant to outlive its rule (which is why `rule_name` and
`rule_type` are snapshotted onto the event) while live evaluation state is not. The consequence is
that **deleting a rule while it is FIRING leaves an event that nothing will ever resolve**: no rule,
no evaluator tick, `status` stuck at `firing` forever.

There were fifteen of these, and fourteen predated this session — `Memory above 50%` twelve times
from Aug 23–25, `Swap` twice from Aug 25, all from earlier test rules deleted mid-fire. Between them
they had been holding **eleven incidents open** since Aug 23, which is most of why the incident
workspace looked permanently busy.

Closing them out had one decision in it. `resolved_value` was left NULL on all fifteen, because no
reading resolved them — they were closed administratively, and writing the last known value or the
threshold there would have been a synthesised measurement that was indistinguishable from a real one
the moment it landed. The contrast is visible in the data and is the point: a `Swap` event that
resolved naturally on Aug 25 carries `42.57`, and the ones closed by hand carry nothing.
`resolved_at` is the close time rather than a backdated guess, because they were genuinely open
until then. The scope was `status='firing' AND rule_id IS NULL`, so nothing whose rule still exists
was touched — the evaluator owns those, and the next sweep would have contradicted any hand-close
anyway. Incident bookkeeping went through the real `maybe_close_incident()` rather than an UPDATE,
so the flush-before-counting ordering held; eleven closed and one correctly stayed open on a
genuinely-firing multivariate event.

**Why Android still needs Firebase, and why that reads as overkill until you look at it.** Two
reasonable objections came up. The first: the APK already shows a notification without any Firebase
at all. It does — `CollectorNotification.kt` builds it on-device with Android's local
`NotificationManager`, and it is the foreground service's mandatory status line, not a message from
anywhere. Nothing about it involves a server. The second: web push needed no Firebase project, so
why should the app. The answer is that web push *does* go through FCM for Chrome — the endpoint is
literally `fcm.googleapis.com` — but the W3C standardised VAPID so any server can push through it
without registering a Google Cloud project, and that is the whole reason browser push is
config-free. Native Android has no VAPID equivalent. Waking a closed app from outside is a
capability the OS grants through FCM (or UnifiedPush, which needs a distributor app the user
installs); `expo-notifications` speaks only the former. So the Firebase project is a platform
constraint, not a choice this codebase made, and it buys exactly one thing the other two channels
do not already cover: alerts reaching the phone with no browser open.

Windows, by contrast, needs nothing built. VAPID keys identify the *application server*, not a
browser or a machine, so the keypair configured here already covers Chrome and Edge on any machine
that can reach the Funnel URL — each browser simply creates its own `web_push_subscriptions` row and
a firing alert fans out to all of them. The only requirement is loading the console over the Funnel
`https://` origin rather than a LAN IP, since the Push API is not exposed on an insecure origin at
all and its absence is reported as "this browser does not support push notifications", which reads
like a browser problem and is not one.

### Email, and the case against Firebase

Email was the piece deferred at the end of the previous session, blocked only on a credential the
user had to generate. It surfaced one Google-account wrinkle before it worked at all: the first
attempt at generating a Gmail App Password failed with "The setting you are looking for is not
available for your account", tried against two different accounts. The cause was the same both
times and is worth naming precisely, because the error message does not — Google hides the App
Passwords page entirely until 2-Step Verification is turned on for the account, and reports it as an
unavailable setting rather than "turn on 2FA first". A college/Workspace account can additionally
have App Passwords disabled outright by an admin, which is a different failure with the identical
error text and nothing the account holder can fix. Turning on 2FA on the personal account resolved
it immediately.

Verified before trusting Gmail with it: a ~40-line hand-rolled SMTP server stood in for a real
mailbox, so `notify._send_email` could be exercised — message construction, the aiosmtplib call,
the AUTH negotiation — with no credential and no network involved. It caught a real bug on the first
run, in the test harness rather than in `notify.py`: setting `SMTP_USERNAME=""` still made
aiosmtplib attempt AUTH, because empty-string is not `None` to it, and the fake server hadn't
advertised AUTH at all. `backend/.env`'s shipped default is exactly `SMTP_USERNAME=` — blank — so
this is a real latent bug in `notify.py` for anyone pointing `SMTP_HOST` at an unauthenticated relay
(MailHog, a bare local postfix): the send fails and is swallowed into a `logger.warning`, which
looks identical to the intentional "not configured" no-op it sits right next to. Doesn't affect
Gmail, where the username is always set, so it was flagged as a follow-up rather than fixed inline.
Fixing the dry-run server to advertise AUTH (as Gmail does) reproduced the real login path and
delivered a clean message with correct headers — proof the code path worked before a single byte
touched Google's servers.

The account email was the other half. `phase10a@example.com` — a Phase 10a test row on IANA's
reserved documentation domain — was still the signed-in account, so even perfect SMTP would have
delivered nowhere; the console's "we'll email your account address" would have been true and
useless simultaneously. Changed to the user's real Gmail address directly in `users.email` via a
scoped session (there is no product "change email" route — this is a login field set at signup, not
a self-service setting), normalized through the same `normalize_email()` signup uses rather than
trusting the string typed in chat verbatim. Confirmed the address wasn't already claimed by another
tenant's row first, since `email` carries a unique constraint. The change doesn't invalidate an
existing session — the JWT is keyed to `user_id`, not email — only the next fresh sign-in uses the
new address.

Real delivery, not a stand-in this time: `notify._send_email` called directly against
`smtp.gmail.com:587` with the App Password, asserted against `get_settings()` first so the script
couldn't silently run against stale cached config, and the absence of the `"email dispatch failed"`
warning line (which carries a full traceback on any failure) was the confirmation, the same
completeness-by-absence check the web push verification used. The user confirmed the message in
their inbox.

**Then FCM came up a third time, from a sharper angle: not "should we," but "how long, and is it
worth the risk today."** The user had rigorous testing to finish by early afternoon and asked for
honest numbers rather than a vibe. Two things shaped the answer. First, a check the previous
sessions' Firebase discussion hadn't run: does `webPush.ts` gate on platform at all? It doesn't — no
`Device`/`Platform`/user-agent check anywhere in it, only feature detection for
`serviceWorker`/`PushManager`. Chrome on Android implements both identically to desktop Chrome, so
the VAPID keys already sitting in `backend/.env` already push to an Android phone today, through the
browser, with literally nothing left to configure. That reframes what FCM actually buys: not "push
notifications on Android" — already true — but specifically "push attributed to the native app
instead of Chrome, opening the app's own Alerts screen instead of a browser tab." Second, this
project's own history was read before estimating the build side, rather than assuming a `gradle
build` is a formality: three separate prior sessions ("The APK, rebuilt so the phone stops crediting
Claude", "once more, for three strings", "a fourth time") each record an APK that looked finished
and wasn't — a stale artifact, a missed screen, a registered file twelve minutes older than the fix
it was meant to carry — caught only by verification steps this codebase treats as mandatory, not by
the build succeeding. That made "45 minutes to 1.5 hours, with real risk of costing more" the honest
estimate, not "a few minutes," and set against a same-day deadline for unrelated testing work, the
call was to skip it — not deferred vaguely this time, but a settled decision with the reasoning
written down in CLAUDE.md so a future session doesn't re-litigate it from zero.

Every stray file this session's own verification created was cleaned up rather than left for the
next person to puzzle over: `backend/encrypted.data` (`pywebpush(curl=True)`'s output file) from the
push verification, and none from the email one — the dry-run SMTP server lived entirely in-process.

Notification channels stand at two of three, both proven by an actual message arriving rather than
a config file that looked plausible: web push (Mac, Windows, and Android via browser, no native
build needed) and email (Gmail, the account address itself replaced along the way). FCM is the one
piece left undone, and for the first time in this project's notification story, "undone" is a
decision rather than a gap.
