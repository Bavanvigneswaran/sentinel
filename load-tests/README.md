# Load testing

Baseline profile: 100 concurrent virtual users for one minute, reporting
requests/second and the response-time distribution.

`docs/TESTING.md`'s "Load testing" section is the authority for interpreting a
result — read it before drawing a conclusion from a number here, because three
configuration facts about this deployment dominate the output and none of them
are the code under test.

## Run it

k6 is not an npm package; install it once:

```bash
brew install k6            # macOS
# or run it without installing:
# docker run --rm -i -v "$PWD/load-tests:/out" grafana/k6 run - < load-tests/baseline.js
```

Then, with the stack up (`make e2e-db && make e2e-serve`):

```bash
cd load-tests
k6 run baseline.js                 # the real profile: 100 VUs, 1 minute
k6 run --vus 5 --duration 10s baseline.js   # a quick smoke
npm install && npm run report      # summary.json -> load-test-report.xlsx
```

`SENTINEL_URL`, `SENTINEL_E2E_EMAIL` and `SENTINEL_E2E_PASSWORD` override the
defaults. `SUMMARY_OUT` moves `summary.json` — k6 resolves it against the
working directory, not the script, so it is worth setting explicitly from
anywhere but `load-tests/`.

## What it measures, and what it deliberately does not

The token is minted **once** in `setup()` and shared by all 100 VUs. Logging in
inside the loop measures Argon2, not the API: at the parameters this project
actually deploys (64 MiB, 4 lanes, 3 iterations) a login costs 60–100ms of real
CPU, and Starlette's threadpool defaults to 40 threads, so a login-saturated run
holds roughly 2.5 GB of hashing memory and pins every core. The latency you get
describes the hasher.

Login throughput is still worth measuring, so it is a separate scenario:

```bash
k6 run -e AUTH_SCENARIO=true baseline.js
```

It runs at 5 VUs, not 100, because the point is how many logins the deployment
absorbs — 100 would queue on the admin connection pool and report the queue.

Endpoints in the baseline: `/health` (no database at all — the floor everything
else is measured against), `/fleet/overview` (the dashboard's own query, several
rollup reads per device) and `/devices` (a plain tenant-scoped list). The
per-endpoint sheet in the report is where that floor-versus-real comparison
shows up.

Excluded on purpose: `/devices/{id}/series` answers with ~2 MB of JSON and would
turn this into a bandwidth test, and `/reports/export.pdf` runs WeasyPrint for
seconds per call. Both are real behaviour that deserves its own profile;
neither belongs in a baseline.

## Reading the result

**A lone spike in `max` is usually not the request path.** Four background
workers share this process — the alert evaluator ticks every 15 seconds and the
forecast worker every 120 — and a forecast tick inside a 60-second window lands
directly in the reported maximum. That is genuine product behaviour and should
be reported rather than tuned away.

**A 429 anywhere means the run hit the wrong stack.** `.env.ci` disables rate
limiting; `make serve` does not. If the auth scenario reports 429s, every other
number in the run is about the production deployment and its limiter, not about
throughput.

**Two connection pools, not one.** `DB_POOL_SIZE` covers ordinary requests;
signup/login/refresh/logout use the separate `ADMIN_DB_POOL_SIZE`. Raising the
first does nothing for a login-heavy scenario, and past either ceiling
SQLAlchemy queues on checkout for up to 30 seconds — so a multi-second p99 is
usually a queue rather than the database or the handlers.

**One uvicorn process.** No `--workers`; all 100 VUs share one event loop. That
is the deployment, so it is the right thing to measure — but it is why absolute
throughput here says more about this laptop than about the architecture.
