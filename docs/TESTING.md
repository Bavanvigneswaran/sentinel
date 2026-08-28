# End-to-end testing

How the browser, device, load and security suites are run against this project,
and why they run against a stack of their own rather than against `make serve`.

`docs/PACKAGING.md` is the authority for how anything is built; this is the
authority for how anything is *tested* end to end. The pytest/vitest/JVM unit
suites are not covered here — those are `make test`, they need no stack, and
they are green as a precondition for any of this.

---

## The trap: `make serve` cannot host these suites

`make serve` runs `ENVIRONMENT=prod`, and three of its settings are each on
their own fatal to an end-to-end run. All three fail in the same expensive way:
the browser reports what looks like an application bug, and the suite's failure
message accuses the wrong thing.

**Rate limiting.** `/auth/login` is 10 per minute per IP and 5 per 15 minutes
per email address. A 300-case login suite runs from one address against one
account, so case 11 onward gets a 429 and the console renders "Too many
attempts. Please wait a moment and try again." Every assertion expecting
"Invalid credentials" fails, and the diff points at the login form.

**`COOKIE_SECURE=true`.** The refresh cookie is then dropped by the browser
over `http://localhost`. A driver signs in, gets a 200 and an access token,
navigates once, and is bounced to `/login` — which reads as a broken session,
not a cookie flag.

**The prod validator.** It refuses to start with either of the above relaxed,
which is correct, and is why the answer is a second configuration rather than
an edit to the first.

So: `backend/.env.ci`, and `make e2e-serve`. The limiter is not being switched
off because it is inconvenient — it should still be *tested*, as its own
scenario asserting that the 11th login is refused, rather than as background
noise under every other assertion.

**That scenario does not exist yet, and it cannot live in these suites.** They
run against `.env.ci`, which has the limiter off, so a case asserting a 429 can
only ever fail here — and one written to pass here would be asserting nothing.
Covering it needs a second configuration with `RATE_LIMIT_ENABLED=true` and a
job of its own, not another case in `selenium-tests/`. Recorded as an open gap
in `docs/TEST_BRIEF.md` rather than quietly dropped.

---

## Running the stack locally

```
make up          # TimescaleDB + Redis, if they are not already running
make e2e-db      # drop, recreate, migrate, and seed the CI database
make e2e-serve   # console + API on one origin, 0.0.0.0:8000
```

`make e2e-db` is destructive by design and refuses any database not named
`*_ci` or `*_test`. It is a different database (`sentinel_ci`) and a different
Postgres role (`sentinel_app_ci`) from both the dev deployment and the pytest
suite — a ROLE is cluster-wide while a GRANT is per-database, so sharing one
would mean the next correct password rotation on the real deployment breaks
this. `backend/.env.test` records that argument at length; `.env.ci` inherits
it.

The seeded account is `e2e@example.com` / `e2e-Sentinel-Test-2026` by default, but
the credentials are not really ours to choose — a generated suite hardcodes whatever
pair it was written with, and the account has to exist under *that* pair or every case
fails at sign-in rather than at the thing it is testing:

```
make e2e-db SENTINEL_E2E_EMAIL=testuser@sentinel.dev \
            SENTINEL_E2E_PASSWORD='SecureP@ssw0rd123'
```

`make e2e-serve` binds port 8000, which is what a generated suite tends to default to
and is also where `make serve` listens. Stop that first, or
`make e2e-serve E2E_PORT=8001` and point the suite's base URL at it.

One constraint on the email, which is easy to lose an afternoon to: `/auth/login` parses
it with Pydantic's `EmailStr`, and email-validator rejects the special-use TLDs — `.test`,
`.local`, `.invalid` are all refused. An account seeded at `user@sentinel.test` is
created happily and can then never log in, because the API answers 422 before it ever
looks at the password, which reads as a broken login endpoint. The seed script puts the
credentials through the API's own `SignupRequest` schema first so this cannot happen
silently. `.dev` and `.com` are fine.

## Running the stack in CI

One step, because the fiddly parts — WeasyPrint's system libraries, the
database that Alembic cannot create for itself, building the console so the API
has something to serve — are the parts that get left out and then take an hour
to diagnose:

```yaml
- uses: ./.github/actions/sentinel-stack
  id: stack
  with:
    enrollment-code: "true"   # only if the suite needs a real agent
```

The job must declare the Postgres and Redis service containers itself; a
composite action cannot. The exact block is in the action's own header comment.

It outputs `base-url`, `email`, `password` and `enrollment-code`, and it does
not return until `/health` answers **and** the console is confirmed to be
served — an unbuilt `frontend/web/dist` degrades quietly, and every UI
assertion then fails against a 404 that looks like a routing bug.

---

## There is no metric seeder, and there will not be one

The seed script creates an account. It does not create a device and it does not
write a single metric row.

That is not an omission. CLAUDE.md's first hard rule is that every number on
screen came from a real agent reading a real machine, and a fixture that
inserts plausible-looking CPU history is precisely what it forbids — worse
here than on a chart, because a suite asserting against invented data reports
"passing" about behaviour nobody has observed.

The supported way to get a device with real history in front of a test is to
enrol a real agent on the machine running the tests:

```
make e2e-code                       # prints a one-time enrollment code
make agent-enroll code=XXXX-XXXX-XXXX
make agent
```

On a CI runner that is a real Linux box reporting its own real CPU, memory and
disk — genuinely the thing this product monitors, just short-lived.

The consequence for suite design is worth stating plainly, because it is a
constraint on the *tests* rather than on the app: a suite that needs a
populated fleet has to either enrol an agent and wait for it, or assert the
empty state. The empty state is a real state this product renders on purpose,
and it is the correct thing to assert when nothing has reported.

---

## Locating elements

**Web.** Every hook is a `data-testid`. The convention is
`<surface>-<element>`, kebab-case: `login-email`, `login-submit`,
`login-error`, `nav-alerts`, `sign-out`, `device-list`. Two are worth knowing
about specifically:

* `page-title` is on every content page's heading (all thirteen; login and
  signup use `login-title`/`signup-title` instead, rendering through
  `AuthForm`). **Neither it nor the URL is a sufficient wait on its own, and the
  two fail in opposite directions.** A URL assertion can pass while the
  bootstrap loader is still on screen, because the router changes the URL before
  the page's data request resolves. But waiting for `page-title` to merely
  *exist* is worse for a click-driven navigation: the previous page's heading is
  still mounted at the moment of the click, so the wait returns instantly and
  the assertion reads the page you just left — `'Devices'` where `'Alerts'` was
  expected, which reads as the app routing wrongly. React Router commits inside
  a transition, so `history.pushState` has already happened while the old page
  is still rendered, and a `pathname` check alone can also pass a frame early.
  What works: capture the `page-title` element *before* the click, then wait for
  it to go stale — that waits for React to unmount the old route and says
  nothing about what replaces it, so the assertion stays honest. See
  `clickNav()` in `selenium-tests/tests/login-tests.js`.
* `page-loading` is on `FullPageLoader`, which `ProtectedRoute` renders while
  the auth bootstrap is in flight. A driver that asserts immediately after
  `driver.get("/")` races it and sees the loader, not the page. Wait for
  `page-loading` to disappear rather than adding a sleep.
* `device-card-<uuid>` is keyed by device id, never by list position. A suite
  addressing "the third card" starts asserting about a different machine the
  moment one goes offline and the fleet reorders.

**Android.** Two locator families, both already in place:

* `testID` surfaces as `resource-id` — `auth-email`, `auth-password`,
  `auth-submit`, `auth-toggle-mode`, `tab-devices`, `tab-alerts`, `tab-more`,
  `device-card-<uuid>`.
* `accessibilityLabel` surfaces as `content-desc`. `Field`/`PasswordField` set
  it from their visible label ("Email", "Password"), and `Button` sets it from
  its title.

Prefer `resource-id`; the accessibility ids are a real accessibility feature
that happens to also be locatable, and they follow the visible copy, so they
change when the wording does.

## The APK Appium installs

`make mobile-apk` refuses to build without a real keystore, and that refusal
stays: a release APK signed with React Native's *public* debug key lets anyone
forge an update Android accepts as the same app. The keystore lives in
`~/.sentinel-keys/`, outside git, so CI cannot produce a release build at all.

`make mobile-apk-test` is the Appium build, and it is a **release** build.
That is not a preference — it was found by installing a debug one.
`assembleDebug` produces a dev-client shell: `expo-dev-client` makes
`DevLauncherActivity` the launcher activity and embeds no JS bundle at all, so
the APK boots into "waiting for Metro" and there is nothing for a driver to
drive. The build succeeds, the artifact is 202 MB, and `unzip -l` shows no
`index.android.bundle`. The release build is 82 MB and has one.

Since a release build needs a key, the target generates its own throwaway
keystore rather than weakening `mobile-apk`'s refusal. That is not the thing
`plugins/withReleaseSigning.js` guards against: that guard is about React
Native's *public* debug key, which is in every RN checkout on earth, so anyone
can forge an update Android installs over the real app. A locally generated key
signs exactly one artifact that is never published — and the output is moved
out of `android/app/build/outputs/apk/release/` precisely so it cannot be
mistaken for a distributable by `register_build.py`.

It carries the same `applicationId` as the real app, so a device holding a
differently-signed build must `adb uninstall com.sentinel.viewer` first.

The emulator reaches the runner at `10.0.2.2`, never `localhost`, which is why
`make e2e-serve` and the composite action both bind `0.0.0.0`. Point
`EXPO_PUBLIC_API_URL` at `http://10.0.2.2:8000` before building the APK — it is
read at build time, so setting it afterwards does nothing.

---

## Load testing

The baseline profile is 100 concurrent virtual users for one minute. Four
things about this deployment shape the numbers, and three of them are
configuration rather than the code under test:

**Connection pools.** Two, not one. `DB_POOL_SIZE` + `DB_MAX_OVERFLOW`
(default 10 + 5) covers ordinary requests. The pre-auth paths — signup, login,
refresh, logout — use a *separate* owner-role pool, `ADMIN_DB_POOL_SIZE` +
`ADMIN_DB_MAX_OVERFLOW` (default 3 + 2), because there is no tenant to scope to
yet. That second pool is the ceiling on concurrent *logins* specifically, and
raising the first one does nothing for a login-heavy scenario. `.env.ci` raises
both.

Past a pool's ceiling SQLAlchemy queues on checkout for up to 30 seconds, and
the run reports multi-second p99s that are entirely the queue — not the
database, and not the handlers.

**Argon2.** Login costs 60–100ms of real CPU per attempt, at 64 MiB × 4 lanes.
It is correctly offloaded to Starlette's threadpool, which defaults to 40
threads — so a login-saturated run can hold ~2.5 GB of hashing memory and pin
every core. `.env.ci` deliberately does **not** weaken the parameters the way
`.env.test` does: a latency distribution measured against a configuration
nobody deploys is not a measurement.

The practical shape of a useful run: mint one token in `setup()` and reuse it,
target `/health`, `/fleet/overview` and `/devices`, and keep `/auth/login` in
its own scenario. Avoid `/devices/{id}/series` (~2 MB of JSON) and
`/reports/export.pdf` (WeasyPrint, seconds per call) unless that is what the
run is about.

**One process.** `uvicorn` runs a single worker; all 100 VUs share one event
loop.

**Four background workers share it.** The alert evaluator ticks every 15s and
the forecast worker every 120s, both doing real CPU. A forecast tick inside a
60-second window lands directly in the reported `max` response time. That is
genuine product behaviour and should be reported, not tuned away.

---

## Security testing

Static analysis, dependency scanning and DAST all work against this repo, with
two prerequisites:

**The pinned set is tied to one interpreter.** `backend/requirements.txt`'s
header records which — `make deps-lock` stamps it — and CI has to install on a
matching Python. pyproject.toml's `requires-python` floor is 3.11 and stays the
source of truth for what the *code* supports, but a resolution of it is not
portable across versions: numpy 2.5 needs >= 3.12, so a 3.11 runner fails with
"No matching distribution found for numpy==2.5.2", which names the package and
says nothing about the interpreter. Found by running the workflow, not by
reading it.

**Dependency scanning needs `backend/requirements.txt`.** `pyproject.toml`
declares ranges, and no scanner can match a CVE against `fastapi>=0.115` — it
reports nothing, which looks exactly like a clean result. `make deps-lock`
regenerates the pinned closure. It is **not** `pip freeze`: the development
venv still carries `anthropic` from before Phase 17 removed the hosted-API
calls, and freezing would declare a dependency on a client this product
deliberately does not use.

**Gitleaks will flag `backend/.env.test` and `backend/.env.ci`.** Both are
committed, both contain credentials, and both are throwaways for databases that
are dropped and recreated — each file says so at the top. They are true
positives for the pattern and false positives for the finding.

Findings a scan should be expected to raise, all known and none of them new:

| Finding | Status |
| --- | --- |
| `joblib.load` is pickle (`app/services/novelty_service.py`) | Accepted. Operator-written files, never uploaded, never user-supplied, never fetched. The file records the condition under which that stops being acceptable. |
| No CSRF token on `/auth/refresh`, `/auth/logout` | `SameSite=strict` is the defence, and the prod validator refuses to start with `cookie_samesite=none`. |
| The rate limiter fails open on a Redis outage | Deliberate, and documented in `app/api/ratelimit.py`: a Redis outage must not lock every user out of the product. Production must alert on that log line. |

The browser security headers a scan checks for are set by
`app/security/headers.py` on every response, including the console's own static
files and the agent binary downloads. `/docs` and `/redoc` are exempt from the
CSP only — Swagger UI boots from an inline script and a CDN bundle — and are
unreachable in prod regardless.
