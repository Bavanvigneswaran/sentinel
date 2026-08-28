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

## 2. Appium — Android app E2E

* Folder `appium-tests/`.
* Appium functional E2E tests for the mobile app.
* **Minimum 300 test cases.**
* An Excel workbook, same two-part shape as above.

Already in place: `package.json` (webdriverio + mocha + exceljs), `README.md`
with capabilities and the full locator table, `smoke.js`.

## 3. Security assessment

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

Findings a scan will legitimately raise, all known and none of them new — see
`docs/TESTING.md`'s "Security testing" section for the reasoning, which belongs
in the report rather than being silently omitted:

* `joblib.load` is pickle (`app/services/novelty_service.py`) — accepted.
* No CSRF token; `SameSite=strict` is the defence, and the prod validator
  refuses to start with `cookie_samesite=none`.
* The rate limiter fails open on a Redis outage — deliberate.
* Gitleaks will flag `backend/.env.test` and `backend/.env.ci`. Both are
  committed throwaways for databases that get dropped; each file says so at the
  top. True positives for the pattern, false positives for the finding.

## 4. Baseline load test

100 concurrent virtual users, one minute, continuous. Report requests/second
and the response-time distribution (min, average, max).

Already in place: `load-tests/baseline.js` (k6 v2.2.0 installed), `README.md`
explaining why the token is minted once in `setup()`, and `npm run report`.

## 5. One GitHub Actions workflow covering all four

A single `.yml` running all four suites, **with every Excel report downloadable
as an artifact**. Must be written last, since the other four have to exist.

Use `./.github/actions/sentinel-stack` to bring the stack up rather than
re-writing those steps; its header comment carries the `services:` block the
job must declare. `.github/workflows/e2e-stack.yml` is a separate self-test —
leave it alone. Note `upload-artifact` rejects duplicate artifact names across
jobs.

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
