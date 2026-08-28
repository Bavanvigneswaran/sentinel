/**
 * Baseline load profile: 100 concurrent virtual users for one minute.
 *
 *   k6 run load-tests/baseline.js
 *   SENTINEL_URL=http://localhost:8000 k6 run load-tests/baseline.js
 *
 * Reports requests/second and the response-time distribution (min, average,
 * p95, max), which is what the brief asks for.
 *
 * Shape of the run, and why
 * -------------------------
 * The token is minted ONCE in setup() and shared by all 100 VUs. That is not a
 * shortcut — logging in inside the loop measures Argon2 rather than the API.
 * At the production parameters this deployment actually runs (64 MiB, 4 lanes,
 * 3 iterations) a login costs 60-100ms of real CPU, and Starlette's threadpool
 * defaults to 40 threads, so a login-saturated run holds ~2.5 GB of hashing
 * memory and pins every core. The resulting latency describes the hasher, not
 * the endpoints under test.
 *
 * Login throughput is worth measuring — but as its own scenario, which is what
 * `auth` below is. It is off by default because mixing it in makes both
 * numbers meaningless. Enable with `-e AUTH_SCENARIO=true`.
 *
 * Endpoints were chosen for what they exercise, not for what looks impressive:
 *
 *   /health          — no database at all; the floor the rest is measured against
 *   /fleet/overview  — the dashboard's own query, several rollup reads per device
 *   /devices         — a plain tenant-scoped list
 *
 * Deliberately excluded: /devices/{id}/series answers with ~2 MB of JSON and
 * would turn this into a bandwidth test, and /reports/export.pdf runs
 * WeasyPrint for seconds per call. Both are real behaviour and worth their own
 * profile; neither belongs in a baseline.
 *
 * Read docs/TESTING.md's "Load testing" section before interpreting a result.
 * The three configuration facts that dominate the numbers — two separate
 * connection pools, Argon2's cost, and four background workers sharing the
 * process — are there.
 */

import http from "k6/http";
import { check, group } from "k6";
import { Rate, Trend } from "k6/metrics";

const BASE = __ENV.SENTINEL_URL || "http://localhost:8000";
const EMAIL = __ENV.SENTINEL_E2E_EMAIL || "e2e@example.com";
const PASSWORD = __ENV.SENTINEL_E2E_PASSWORD || "e2e-Sentinel-Test-2026";
const RUN_AUTH = String(__ENV.AUTH_SCENARIO || "").toLowerCase() === "true";

const errorRate = new Rate("application_errors");
const healthTrend = new Trend("t_health", true);
const fleetTrend = new Trend("t_fleet_overview", true);
const devicesTrend = new Trend("t_devices", true);
const loginTrend = new Trend("t_login", true);

const scenarios = {
  baseline: {
    executor: "constant-vus",
    vus: 100,
    duration: "1m",
    exec: "readPath",
  },
};

if (RUN_AUTH) {
  // Far fewer VUs on purpose: this scenario is measuring how many logins the
  // deployment can absorb, and 100 of them would simply queue on the admin
  // connection pool and the threadpool, reporting the queue rather than the
  // endpoint.
  scenarios.auth = {
    executor: "constant-vus",
    vus: 5,
    duration: "1m",
    exec: "authPath",
    startTime: "0s",
  };
}

export const options = {
  scenarios,
  thresholds: {
    // Deliberately loose, and phrased as a regression guard rather than an
    // SLO: this deployment is one uvicorn process on a laptop, and a threshold
    // tight enough to be an SLO would fail on an unrelated background worker
    // tick. See the note about the forecast worker below.
    http_req_failed: ["rate<0.01"],
    "http_req_duration{scenario:baseline}": ["p(95)<1500"],
    application_errors: ["rate<0.01"],
  },
  // `count` is not optional here even though it looks like decoration: setting
  // summaryTrendStats REPLACES k6's defaults rather than extending them, so
  // omitting it leaves every per-endpoint Trend without a request count — and
  // report.js then has no way to tell a trend that recorded nothing from one
  // that recorded thousands, and renders an empty sheet.
  summaryTrendStats: ["count", "min", "avg", "med", "p(95)", "p(99)", "max"],
};

export function setup() {
  const res = http.post(
    `${BASE}/api/auth/login`,
    JSON.stringify({ email: EMAIL, password: PASSWORD }),
    { headers: { "Content-Type": "application/json" } },
  );

  if (res.status !== 200) {
    // Fail loudly and specifically. An unseeded account produces a 401 here and
    // then 100 VUs of 401s downstream, which reads as a broken API rather than
    // a missing fixture.
    throw new Error(
      `setup: could not sign in as ${EMAIL} (HTTP ${res.status}). ` +
        `Seed the account first:\n` +
        `  make e2e-db SENTINEL_E2E_EMAIL=${EMAIL} SENTINEL_E2E_PASSWORD='<password>'`,
    );
  }
  return { token: res.json("access_token") };
}

function authed(data) {
  return {
    headers: { Authorization: `Bearer ${data.token}` },
    tags: { scenario: "baseline" },
  };
}

/**
 * A `default` export, purely so a CLI override works.
 *
 * Passing --vus/--duration makes k6 discard the `scenarios` block entirely and
 * look for `default`; without this, a perfectly reasonable
 * `k6 run --vus 5 --duration 10s` fails with "function 'default' not found in
 * exports", which reads as a broken script rather than as k6 having replaced
 * the configuration. The full profile still runs through `scenarios` when no
 * override is given, so this changes nothing about the real measurement.
 */
export default function (data) {
  readPath(data);
}

export function readPath(data) {
  group("health", () => {
    const res = http.get(`${BASE}/health`, { tags: { endpoint: "health" } });
    healthTrend.add(res.timings.duration);
    errorRate.add(!check(res, { "health 200": (r) => r.status === 200 }));
  });

  group("fleet overview", () => {
    const res = http.get(`${BASE}/api/fleet/overview?window_seconds=3600`, authed(data));
    fleetTrend.add(res.timings.duration);
    errorRate.add(!check(res, { "fleet 200": (r) => r.status === 200 }));
  });

  group("devices", () => {
    const res = http.get(`${BASE}/api/devices`, authed(data));
    devicesTrend.add(res.timings.duration);
    errorRate.add(!check(res, { "devices 200": (r) => r.status === 200 }));
  });
}

export function authPath() {
  const res = http.post(
    `${BASE}/api/auth/login`,
    JSON.stringify({ email: EMAIL, password: PASSWORD }),
    { headers: { "Content-Type": "application/json" }, tags: { scenario: "auth" } },
  );
  loginTrend.add(res.timings.duration);
  // A 429 is the rate limiter working, not an error. It should not appear
  // against .env.ci, where limiting is off — if it does, the stack under test
  // is the production one and every other number here is about the wrong
  // deployment.
  check(res, {
    "login 200": (r) => r.status === 200,
    "not rate limited (wrong stack?)": (r) => r.status !== 429,
  });
}

export function handleSummary(data) {
  // Relative to k6's CWD, not to this file — k6 has no __dirname. A hardcoded
  // "load-tests/summary.json" works only when invoked from the repo root and
  // fails with "no such file or directory" everywhere else, including inside a
  // container, *after* the run has completed and the numbers are gone. So:
  // plain `summary.json` by default (run this from load-tests/), overridable
  // for anything else.
  const out = __ENV.SUMMARY_OUT || "summary.json";
  // Record what was actually measured. report.js otherwise reads SENTINEL_URL
  // from its own environment, which is a different process run at a different
  // time — so a report generated in a second shell, or in a later CI step,
  // confidently names a host the run never touched.
  const enriched = { ...data, sentinel: { baseUrl: BASE, email: EMAIL } };
  return {
    stdout: textSummary(data),
    [out]: JSON.stringify(enriched, null, 2),
  };
}

/** A compact console summary in the terms the brief asks for. */
function textSummary(data) {
  const m = data.metrics;
  const dur = m.http_req_duration ? m.http_req_duration.values : {};
  const reqs = m.http_reqs ? m.http_reqs.values : {};
  const failed = m.http_req_failed ? m.http_req_failed.values : {};
  const ms = (v) => (typeof v === "number" ? `${v.toFixed(1)}ms` : "—");

  // Read back from the run rather than restating the profile: a CLI override
  // changes both, and a header that still says "100 VUs, 1 minute" over a
  // ten-second smoke is a caption that lies about its own figures.
  const vus = m.vus_max && m.vus_max.values ? m.vus_max.values.max : null;
  const seconds = data.state && data.state.testRunDurationMs
    ? Math.round(data.state.testRunDurationMs / 1000)
    : null;
  const heading = `Baseline load — ${vus ?? "?"} VUs, ${seconds ?? "?"}s`;

  return [
    "",
    `  ${heading}`,
    `  ${"─".repeat(heading.length)}`,
    `  Requests/second   ${reqs.rate ? reqs.rate.toFixed(1) : "—"} req/s`,
    `  Requests total    ${reqs.count ?? "—"}`,
    `  Failed            ${failed.rate !== undefined ? (failed.rate * 100).toFixed(2) : "—"}%`,
    "",
    `  Response time     min ${ms(dur.min)}   avg ${ms(dur.avg)}   max ${ms(dur.max)}`,
    `                    med ${ms(dur.med)}   p95 ${ms(dur["p(95)"])}   p99 ${ms(dur["p(99)"])}`,
    "",
    "  A spike in `max` is often a background worker, not the request path:",
    "  the alert evaluator ticks every 15s and the forecast worker every 120s,",
    "  both in this process. See docs/TESTING.md.",
    "",
  ].join("\n");
}
