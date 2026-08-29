# The test brief

Five deliverables, specified by the project owner. This file exists so the work
can be picked up cold — it is the *task*, where `docs/TESTING.md` is the
*machinery* that task runs on. Read both.

**The output is what matters.** These were written as prompts for a code-
generation tool, and the owner's requirement is that the artefacts match what
that tool would have produced: the folders, filenames, sheet names and report
structure below are the deliverable. Do not "improve" the shape of them.

---

## 1. Selenium — web frontend E2E  ·  **DELIVERED 2026-08-28**

* Folder `selenium-tests/`, subfolder `tests/`, file `tests/login-tests.js`.
* Selenium functional E2E tests for the web console.
* **Minimum 300 test cases.**
* An Excel workbook with a **summary of the run** and **details of each test**.

Already in place: `package.json` (mocha + selenium-webdriver + exceljs, scripts
glob `tests/*.js`), `README.md` with the full `data-testid` list, `smoke.js`, and
`npm run test:xlsx` which renders the workbook through `tools/test-report/`.

**421 cases across fifteen `Module N — ...` describes, 421 passing in 51.8s**, run
against `make e2e-db` + `make e2e-serve`. `npm run test:xlsx` writes
`selenium-test-report.xlsx` (Summary + Test Details); both it and `results.json`
are gitignored as generated artefacts, the suite itself is not.

One deliberate departure from the conventions: it is a single ~2,400-line file
rather than the ~400 CLAUDE.md asks for. The brief names that exact path and the
scripts glob `tests/*.js`, so helpers split into a sibling would be collected and
run as a suite of their own.

Two things a cold pickup should know:

* **It found and fixed a real defect** — a signed-out session restored from the
  browser's back/forward cache. See `docs/HISTORY.md`'s "The Selenium suite, and
  a signed-out session the Back button brought back", and the Phase 1 invariant
  it produced in CLAUDE.md. Four *suite* bugs are written up in the same entry,
  each of which first presented as an application defect.
* **The rate limiter has no scenario, and that is a recorded gap, not an
  oversight.** `docs/TESTING.md` asks for the eleventh login to be asserted as
  refused, but `backend/.env.ci` sets `RATE_LIMIT_ENABLED=false` for the reasons
  that file sets out — so the case cannot pass against the stack these suites
  run on. Covering it needs a second configuration, not another test.

## 2. Appium — Android app E2E  ·  **DELIVERED 2026-08-28**

* Folder `appium-tests/`.
* Appium functional E2E tests for the mobile app.
* **Minimum 300 test cases.**
* An Excel workbook, same two-part shape as above.

Already in place: `package.json` (webdriverio + mocha + exceljs), `README.md`
with capabilities and the full locator table, `smoke.js`.

**`appium-tests/tests/app-tests.js`, 560 cases across twenty-eight
`Module N — ...` describes, 560 passing in 594.3s**, run against
`make e2e-db` + `make e2e-serve` with the APK from
`EXPO_PUBLIC_API_URL=http://10.0.2.2:8000 make mobile-apk-test`.
`npm run test:xlsx` writes `appium-test-report.xlsx` (Summary + Test Details);
both it and `results.json` are gitignored, the suite is not.

Four things a cold pickup should know:

* **It runs in one Appium session, and that fixes the starting state.** The
  uiautomator2 driver clears app data when a session begins, so the run always
  starts signed out with no collector token — which is why module 6 tests the
  cookie-jar session restore with `terminateApp`/`activateApp` rather than by
  reconnecting.
* **The device modules assert against a real agent.** Module 19 enrols the
  emulator itself through the app's own More → Settings → Monitor this phone
  flow, and modules 20–24 assert readings the Android collector actually took —
  including the ones it honestly cannot take, which is where the suite is
  sharpest: CPU renders as "unavailable" on the fleet card, temperature is
  labelled "Battery temp", the network entity is `device-total` rather than a
  NIC, and there is no top-processes card. Module 25 removes the device again,
  so the account ends as it started. `before` also clears any device or
  authored rule a crashed run left behind — it removes rows, it never writes a
  metric.
* **It found a real gap: a row's hook does not reach the controls inside it.**
  React Native flattens a `Card`'s accessibility subtree, so `rule-<uuid>`
  arrives childless and its Edit/Delete buttons arrive as *siblings* —
  `UiSelector.childSelector()` matches nothing, and the only alternative was
  counting Edits down the screen. `AlertsScreen` had already solved this with
  `alert-silence-<uuid>`; `AlertRulesScreen` had not, and now carries
  `rule-edit-<uuid>` and `rule-delete-<uuid>`. Written up in `docs/TESTING.md`
  and this folder's README.
* **`getPageSource()` returns only what is rendered.** A ScrollView taller than
  the screen omits everything below the fold, so an assertion about a device's
  Disks card or the last rule's buttons reports a missing element that is
  merely not scrolled to. `fullSnapshot()` exists for those two screens.

Recorded gaps, same class as deliverable 1's: the rate limiter has no scenario
(`.env.ci` disables it), and push notifications cannot be exercised because the
APK is built without `google-services.json` — the suite asserts that the app
*says so* rather than pretending the channel works.

That reason was recorded wrongly here until the CI run of deliverable 5 tested
it. It said the *emulator image* has no Google Play services. The image has
nothing to do with it: `app.config.ts` omits `googleServicesFile` when
`google-services.json` is absent and passes `hasFirebaseConfig: false` through
to `src/config.ts`, which is what makes Settings report push as unavailable —
a build-time fact the emulator cannot change. The local AVD is
`google_apis_playstore` and carries the Play Store, and the suite passes on it.
The mistake was not free: it sent the CI emulator to an AOSP image, whose
`Build.MODEL` is "Android SDK built for x86_64" and whose layout put the rule
form's buttons below the fold, for 18 failures across two jobs.

## 3. Security assessment  ·  **DELIVERED 2026-08-29**

Acting as an application security engineer / pen-tester / DevSecOps engineer,
across eight phases: backend discovery, API discovery, SAST, DAST
(non-destructive, detection only), dependency scanning, a findings report, an
executive summary with a score out of 100, and the file tree below.

    Vulnerability Test Results/
    ├── security-review.md
    ├── executive-summary.md
    ├── dependency-report.md
    ├── endpoint-inventory.xlsx
    └── findings.xlsx

`findings.xlsx` carries four sheets: Security Findings, Endpoint Inventory,
Dependency Vulnerabilities, Risk Summary. Every finding needs severity
(Critical/High/Medium/Low), type, file path, endpoint, description, an
exploitation scenario, impact, and a recommended fix.

Also `.github/workflows/security-review.yml` — on push, pull_request and
workflow_dispatch; detects the stack, runs SAST and dependency scans, runs API
checks when an environment exists, uploads reports as artifacts, publishes a
job summary, and **fails only on Critical**.

The first pass found 18 findings (0 Critical, 1 High, 6 Medium, 11 Low) at a
weighted score of 87/100. Everything that could be closed in code was, and the
documents were regenerated to describe the fixed state as a single assessment
rather than a before/after diary — that is the shape a rubric or a fresh
reviewer should see, not a change log. Current state: **4 findings — 0
Critical, 0 High, 1 Medium, 1 Low, 2 Informational — at 98/100.** The one
Medium (four unsigned desktop agent binaries) needs a purchased code-signing
certificate; every other finding that named a real weakness is fixed, with the
work covered by 91 new tests and both the 421-case Selenium suite and the
560-case Appium suite re-run and passing against the final code and dependency
state. See CLAUDE.md's "Deliverable 3" note and `Vulnerability Test
Results/security-review.md` for the full account.

Two of the four findings `docs/TESTING.md`'s "Security testing" section
originally flagged as accepted are no longer findings at all, and the other
two changed shape rather than disappearing — all four are still covered there
because a scan will still raise the underlying pattern:

* `joblib.load` is pickle (`app/services/novelty_service.py`) — the
  operator-written precondition is now enforced twice: file/directory
  permissions, and an HMAC-SHA256 sidecar verified before any unpickle.
* No CSRF token — `SameSite=strict` remains the primary defence, now backed by
  a second layer (`Sec-Fetch-Site`, checked in `app/api/csrf.py`) that a
  browser cannot forge and that needed no client change.
* The rate limiter fails open on a Redis outage — still true by design, but it
  now degrades to a per-process counter rather than removing the limit
  entirely; alerting on the fail-open log line remains an operational task.
* Gitleaks will flag `backend/.env.test` and `backend/.env.ci`. The JWT
  signing keys those files used to carry are gone — generated per run instead
  — so what remains is `docker-compose.yml`'s own published database
  password on roles that exist only for databases dropped on every run. True
  positives for the pattern, false positives for the finding.

## 4. Baseline load test  ·  **DELIVERED 2026-08-29**

100 concurrent virtual users, one minute, continuous. Report requests/second
and the response-time distribution (min, average, max).

Already in place: `load-tests/baseline.js` (k6 v2.2.0 installed), `README.md`
explaining why the token is minted once in `setup()`, and `npm run report`.

**Run against `make e2e-db` + `make e2e-serve`, 100 VUs for 60s: 127,330
requests at 2,114.4 req/s, 0.00% failed.** Response time min 0.2 ms, avg
47.1 ms, median 57.8 ms, p95 87.7 ms, p99 162.6 ms, max 567.6 ms; 96.1 MB
received. All three thresholds passed. `npm run report` renders
`load-test-report.xlsx` (Summary + Per-endpoint) from `summary.json`; both are
gitignored as run artefacts for the reason `.gitignore` gives — a committed
workbook is a stale claim about a pass rate.

Per endpoint, 42,443 requests each:

| Endpoint | Avg | p95 | Max |
| --- | --- | --- | --- |
| `/health` | 5.6 ms | 7.8 ms | 521.3 ms |
| `/fleet/overview` | 68.7 ms | 108.2 ms | 527.7 ms |
| `/devices` | 67.1 ms | 102.1 ms | 567.6 ms |

Two things a cold pickup should know:

* **The `max` column is one shared stall, not three slow handlers.** All three
  endpoints peak within 46 ms of each other, and one of them is `/health`,
  which touches no database at all — so whatever cost half a second cannot have
  been a query, a pool checkout or any one handler. It is the single event loop
  stopping for everything at once, which is exactly the shape `README.md` and
  `docs/TESTING.md` predict for a background worker tick: four of them share
  this process, and the forecast worker's ETS fit lands inside any 60-second
  window. Genuine product behaviour, reported rather than tuned away. It is
  also one occurrence in 127,330 requests — the p99 is 162.6 ms.
* **The fleet was not empty.** `sentinel_ci` held 19 real device rows, all
  offline, left by the Selenium and Appium runs that enrolled and removed real
  agents. So `/fleet/overview` was answering over a real fleet rather than the
  empty state, which is what makes the 5.6 ms floor and the ~68 ms real query
  worth comparing. Nothing was seeded to achieve that, and nothing could be:
  there is no metric seeder and there will not be one.

## 5. One GitHub Actions workflow covering all four  ·  **DELIVERED 2026-08-29**

A single `.yml` running all four suites, **with every Excel report downloadable
as an artifact**. Must be written last, since the other four have to exist.

Use `./.github/actions/sentinel-stack` to bring the stack up rather than
re-writing those steps; its header comment carries the `services:` block the
job must declare. `.github/workflows/e2e-stack.yml` is a separate self-test —
leave it alone. Note `upload-artifact` rejects duplicate artifact names across
jobs.

**`.github/workflows/test-suites.yml`**, five jobs, five workbooks:

| Job | Suite | Artifact | Workbook |
| --- | --- | --- | --- |
| `selenium` | 421 browser cases | `selenium-report` | `selenium-test-report.xlsx` |
| `appium` | 560 Android cases | `appium-report` | `appium-test-report.xlsx` |
| `security` | the eight-phase assessment | `security-assessment` | `findings.xlsx`, `endpoint-inventory.xlsx` |
| `load` | 100 VUs, 1 minute | `load-test-report` | `load-test-report.xlsx` |
| `summary` | — | — | a step summary naming all of them |

Four decisions worth knowing about:

* **The security suite is called, not copied.** `security-review.yml` already
  runs all eight phases and already uploads both workbooks. It gained a
  `workflow_call` trigger — the input is redeclared under it, because `inputs`
  resolves against whichever trigger fired and `api_checks` would otherwise
  read an undefined `inputs.run-api-checks` on a called run and skip itself
  silently. Nothing else about it changed. Duplicating those five jobs would
  have meant two definitions of one assessment drifting apart.
* **It does not run on push.** The Appium job builds an 82 MB release APK
  through `expo prebuild` + `assembleRelease` and boots an emulator; a full run
  is around 40 minutes. Nightly at 03:00 UTC and on demand, with a
  `run-appium` dispatch input that drops it to about six. Per-commit feedback
  is what `e2e-stack.yml` and `security-review.yml` (which does run on push)
  already provide.
* **Each suite job brings up its own stack.** A service container lives for one
  job, so there is no handing a running database to the next — and sharing
  would be wrong anyway, since the Appium suite enrols and removes a real
  device and the load profile's numbers describe whatever fleet it found.
* **The verdict is re-derived from `results.json`.** Both suites' `test:report`
  script ends in `|| true` so a failing run still renders its workbook — which
  is precisely the run somebody wants the workbook for. `mocha-xlsx.js` does
  exit 1 when cases failed, so the step is not silent; what the gate adds is a
  step summary naming *which* cases went red, and a clear message for the one
  thing npm cannot report — a suite that died before writing `results.json` at
  all.

Two things about the emulator job are load-bearing and easy to get wrong:
`target: default` (an AOSP image), because the suite asserts that the app
*reports push as unavailable* and a `google_apis` image carries Play services,
which would make a correct app fail a correct test; and the Appium server is
started inside the emulator action's own script rather than as a step of its
own, so that `ANDROID_HOME` is in *its* environment — the server shells out to
adb itself and exporting the SDK path only for the test process fails at
session creation.

**It has not been executed on a runner.** Everything checkable off a runner was
checked — both workflows parse, no two artifact names collide across the whole
`.github/workflows/` tree, the three embedded Python steps were run against the
real `results.json` and `summary.json` from the runs above (pass, fail and
empty paths), the pinned k6 tarball resolves, and
`system-images;android-35;default;x86_64` exists in Google's repository. What
cannot be verified from here is a GitHub-hosted run: the APK build and the
emulator boot in particular have never run in CI on this project.

---

## Running the suites

`docs/TESTING.md` is the authority. The short version:

```
tailscale funnel reset          # the test stack must not be public
# stop `make serve`
make e2e-db && make e2e-serve   # console + API on :8000, limiter off
cd selenium-tests && npm run smoke
```

Account: `e2e@example.com` / `e2e-Sentinel-Test-2026`.

For Appium, additionally: `appium` running on :4723, an emulator booted, and

```
EXPO_PUBLIC_API_URL=http://10.0.2.2:8000 make mobile-apk-test
adb uninstall com.sentinel.viewer && adb install frontend/mobile/build/app-appium.apk
```

Afterwards, restore the real deployment: `make serve`, then
`tailscale funnel --bg 8000`.

## Two constraints the suites have to be written around

**The fleet is empty, and that is a real state.** Nothing seeds devices or
metrics — the first hard rule is that every number came from a real agent
reading a real machine. Assertions accept the empty state
(`devices-empty` / `empty-state`) or enrol a real agent first (`make e2e-code`).

**Never assert on a Tailwind class or a list position.** Every element a test
drives carries an explicit hook; both READMEs list them. Rows are keyed by
entity id, never index.
