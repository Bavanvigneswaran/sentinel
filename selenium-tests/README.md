# Selenium E2E suite

Drives the Sentinel web console in a real Chrome. `docs/TESTING.md` is the
authority for how the stack under test is brought up and why it is not
`make serve`; this file is just how to run the suite.

## Run it

`tests/login-tests.js` is the suite: 421 cases in fifteen `Module N — ...`
describes, all passing as of 2026-08-28 (51.8s). The scripts glob `tests/*.js`
rather than naming a file, so a differently-named generated suite dropped in
beside it is picked up too — and would run *as well as* this one, not instead of
it.

```bash
# 1. the stack, in one terminal — NOT `make serve`, see below
make e2e-db SENTINEL_E2E_EMAIL=testuser@sentinel.dev SENTINEL_E2E_PASSWORD='SecureP@ssw0rd123'
make e2e-serve

# 2. the suite, in another
cd selenium-tests
npm install
npm run smoke         # check the machine before blaming the app
npm run test          # spec reporter, human output
npm run test:xlsx     # + selenium-test-report.xlsx
```

**No browser install step, and no chromedriver step.** That is worth stating
because it looks like it should need one: selenium-webdriver 4.6+ ships
Selenium Manager, which resolves *and downloads* both the driver and a matching
Chrome for Testing into a cache on first run. Verified on a machine with no
Chrome, Chromium or Firefox installed at all — only Safari — where the suite
still runs. The first run is just slow.

`smoke.js` lives outside `tests/` so it never runs as part of the suite. It
answers "is my machine set up", not "does the app behave": it loads the login
page, signs in, checks the header shows the right account, navigates, and signs
out. `HEADLESS=0` to watch it.

`SENTINEL_URL` overrides the base URL (default `http://localhost:8000`).

## Why not `make serve`

Three of its production settings each break this suite on their own, and all
three surface in the browser as what looks like an application bug:

* rate limiting 429s the eleventh login, so every "invalid credentials"
  assertion after that fails against "Too many attempts";
* `COOKIE_SECURE=true` means the refresh cookie is dropped over
  `http://localhost`, so a sign-in succeeds and the next navigation bounces to
  `/login`;
* the prod validator refuses to start with either relaxed, which is correct,
  and is why `backend/.env.ci` exists rather than an edit to `backend/.env`.

The limiter is not being switched off because it is inconvenient — it deserves
its own scenario asserting that the eleventh login *is* refused, rather than
being background noise under every other assertion.

## The fleet is empty, and that is a real state

Nothing seeds devices or metrics. CLAUDE.md's first hard rule is that every
number came from a real agent reading a real machine, so there is no fixture
that fabricates CPU history — a suite asserting against invented data reports
"passing" about behaviour nobody observed.

Assertions should therefore accept the empty state, or enrol a real agent
first:

```bash
make e2e-code                      # prints a one-time enrollment code
make agent-enroll code=XXXX-XXXX-XXXX && make agent
```

## Locators

`data-testid` is the hook, `<surface>-<element>`: `login-email`,
`login-password`, `login-submit`, `login-error`, `nav-alerts`, `sign-out`,
`device-list`. Three worth knowing:

* **`page-title`** is on every content page's heading — all thirteen of them,
  so it is a landmark you can wait on after any navigation. (Login and signup
  are the exceptions and use `login-title`/`signup-title`, because they render
  through `AuthForm` rather than the app shell.) It is worth waiting on this
  rather than on a URL change: the router navigates before the page's data
  request resolves, so a URL assertion can pass while the bootstrap loader is
  still on screen.

* **`page-loading`** is on the bootstrap loader that `ProtectedRoute` renders
  while the refresh token is being exchanged. A driver that asserts straight
  after `driver.get("/")` races it. Wait for it to disappear rather than
  sleeping.
* **`device-card-<uuid>`** is keyed by device id, never by list position — a
  suite addressing "the third card" starts asserting about a different machine
  as soon as one goes offline and the fleet reorders.

`docs/TESTING.md` has the full list, including the Android equivalents.

Two hard-won notes on driving this console specifically, both written up in
`docs/HISTORY.md`:

* **Never `element.clear()` a field.** It blanks the DOM without an input event,
  so React restores its own value and what you type next is *appended* — a
  refilled login form submitted the old password concatenated with the new one.
  Empty fields with backspaces.
* **A pathname check does not mean the route rendered.** React Router commits a
  navigation inside a transition, so `history.pushState` has already happened
  while the previous page is still on screen. Wait for the old `page-title` node
  to go stale.

## The report

`npm run report` runs `../tools/test-report/mocha-xlsx.js`, which converts
mocha's JSON output into a two-sheet workbook — Summary (totals, pass rate,
per-module breakdown) and Test Details (every case, its status, duration and
first line of failure). It lives outside this folder on purpose: it reads
*mocha's* format and knows nothing about Selenium, so the Appium suite points at
the same script with its own `results.json` rather than growing a second copy
that drifts.

The Summary's per-module breakdown is derived from the describe titles by
regex, which is why every one of them starts `Module N — `. Rename them and the
breakdown collapses into a single ungrouped row.

Note that `npm run test:report` ends in `|| true`. Mocha exits non-zero when
tests fail, and without that a CI step stops there and uploads no artifact at
all — so the one run you most want the spreadsheet for is the run that would
not produce it. `report.js` re-raises the failure through its own exit code
*after* the file is written, so a workflow can still gate on the result.
