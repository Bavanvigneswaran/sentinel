#!/usr/bin/env node
/**
 * Turn k6's end-of-run summary into the .xlsx the brief asks for.
 *
 *   k6 run load-tests/baseline.js      # writes load-tests/summary.json
 *   node load-tests/report.js load-tests/summary.json load-test-report.xlsx
 *
 * Two sheets: Summary (the headline figures — RPS, min/avg/max response time,
 * error rate, threshold verdicts) and Per-endpoint (the same distribution for
 * each endpoint the profile exercises, which is the sheet that tells you
 * *which* call is slow rather than that something is).
 */

const fs = require("node:fs");
const path = require("node:path");
const ExcelJS = require("exceljs");

const [, , inputPath = "summary.json", outputPath = "load-test-report.xlsx"] = process.argv;

if (!fs.existsSync(inputPath)) {
  console.error(`No k6 summary at ${inputPath}.\nRun the profile first: k6 run load-tests/baseline.js`);
  process.exit(1);
}

const data = JSON.parse(fs.readFileSync(inputPath, "utf8"));
const metrics = data.metrics || {};

const val = (name, key) => {
  const m = metrics[name];
  return m && m.values && typeof m.values[key] === "number" ? m.values[key] : null;
};
const ms = (v) => (v === null ? "—" : `${v.toFixed(1)} ms`);
const num = (v, d = 1) => (v === null ? "—" : v.toFixed(d));

const HEADER = { bold: true, color: { argb: "FFFFFFFF" } };
const HEADER_FILL = { type: "pattern", pattern: "solid", fgColor: { argb: "FF1F2937" } };

function styleHeader(sheet) {
  const row = sheet.getRow(1);
  row.font = HEADER;
  row.fill = HEADER_FILL;
  sheet.views = [{ state: "frozen", ySplit: 1 }];
}

/** k6 records one Trend per endpoint (t_health, t_fleet_overview, ...). */
function endpointTrends() {
  return Object.keys(metrics)
    .filter((k) => k.startsWith("t_"))
    .map((k) => ({
      name: k.replace(/^t_/, "").replace(/_/g, " "),
      min: val(k, "min"),
      avg: val(k, "avg"),
      med: val(k, "med"),
      p95: val(k, "p(95)"),
      p99: val(k, "p(99)"),
      max: val(k, "max"),
      count: val(k, "count"),
    }))
    // Keyed off `avg` rather than `count`: a Trend that recorded samples always
    // has an average, whereas `count` is only present if summaryTrendStats
    // asks for it — and a report that silently drops every endpoint because of
    // a stats-list setting is worse than one that shows a row with a blank
    // count.
    .filter((t) => t.avg !== null);
}

async function main() {
  const wb = new ExcelJS.Workbook();
  wb.creator = "Sentinel test tooling";
  wb.created = new Date();

  const summary = wb.addWorksheet("Summary");
  summary.columns = [
    { header: "Metric", key: "k", width: 34 },
    { header: "Value", key: "v", width: 30 },
    { header: "Note", key: "n", width: 66 },
  ];
  styleHeader(summary);

  const failRate = val("http_req_failed", "rate");

  // Read back from the run, never restated from the script: a CLI override
  // changes both, and a "Profile" row that says 100 VUs directly above a
  // "Virtual users (peak): 10" row is a caption contradicting its own figures.
  const peakVus = val("vus_max", "max");
  const seconds =
    data.state && typeof data.state.testRunDurationMs === "number"
      ? Math.round(data.state.testRunDurationMs / 1000)
      : null;

  const rows = [
    [
      "Profile",
      `${peakVus === null ? "?" : Math.round(peakVus)} VUs, ${seconds === null ? "?" : seconds}s`,
      "constant-vus; see load-tests/baseline.js",
    ],
    ["Run at", new Date().toISOString(), ""],
    [
      "Base URL",
      (data.sentinel && data.sentinel.baseUrl) || process.env.SENTINEL_URL || "(unknown)",
      "as recorded by the run itself, not by this process's environment",
    ],
    ["", "", ""],
    ["Requests/second", `${num(val("http_reqs", "rate"))} req/s`, "throughput across every endpoint"],
    ["Requests total", num(val("http_reqs", "count"), 0), ""],
    [
      "Failed requests",
      failRate === null ? "—" : `${(failRate * 100).toFixed(2)}%`,
      "transport and non-2xx; a 429 here means the run hit the production stack",
    ],
    ["", "", ""],
    ["Response time — min", ms(val("http_req_duration", "min")), ""],
    ["Response time — avg", ms(val("http_req_duration", "avg")), ""],
    ["Response time — median", ms(val("http_req_duration", "med")), ""],
    ["Response time — p95", ms(val("http_req_duration", "p(95)")), ""],
    ["Response time — p99", ms(val("http_req_duration", "p(99)")), ""],
    [
      "Response time — max",
      ms(val("http_req_duration", "max")),
      "a lone spike is usually a background worker: the alert evaluator ticks every 15s, the forecast worker every 120s, both in this process",
    ],
    ["", "", ""],
    ["Virtual users (peak)", num(peakVus, 0), ""],
    [
      "Data received",
      val("data_received", "count") === null
        ? "—"
        : `${(val("data_received", "count") / 1048576).toFixed(1)} MB`,
      "",
    ],
  ];
  for (const [k, v, n] of rows) summary.addRow({ k, v, n });

  // Threshold verdicts, if k6 recorded any.
  const thresholds = [];
  for (const [name, m] of Object.entries(metrics)) {
    if (!m.thresholds) continue;
    for (const [expr, result] of Object.entries(m.thresholds)) {
      const ok = result && result.ok !== undefined ? result.ok : !result;
      thresholds.push([`${name} — ${expr}`, ok ? "PASS" : "FAIL"]);
    }
  }
  if (thresholds.length) {
    summary.addRow({});
    const head = summary.addRow({ k: "Threshold", v: "Verdict" });
    head.font = { bold: true };
    for (const [k, v] of thresholds) {
      const row = summary.addRow({ k, v });
      row.getCell("v").font = {
        bold: true,
        color: { argb: v === "PASS" ? "FF16A34A" : "FFDC2626" },
      };
    }
  }

  const perEndpoint = wb.addWorksheet("Per-endpoint");
  perEndpoint.columns = [
    { header: "Endpoint", key: "name", width: 26 },
    { header: "Requests", key: "count", width: 12 },
    { header: "Min (ms)", key: "min", width: 12 },
    { header: "Avg (ms)", key: "avg", width: 12 },
    { header: "Median (ms)", key: "med", width: 13 },
    { header: "p95 (ms)", key: "p95", width: 12 },
    { header: "p99 (ms)", key: "p99", width: 12 },
    { header: "Max (ms)", key: "max", width: 12 },
  ];
  styleHeader(perEndpoint);

  const trends = endpointTrends();
  if (trends.length === 0) {
    perEndpoint.addRow({ name: "(no per-endpoint trends recorded)" });
  }
  for (const t of trends) {
    perEndpoint.addRow({
      name: t.name,
      count: Math.round(t.count),
      min: t.min === null ? null : Number(t.min.toFixed(1)),
      avg: t.avg === null ? null : Number(t.avg.toFixed(1)),
      med: t.med === null ? null : Number(t.med.toFixed(1)),
      p95: t.p95 === null ? null : Number(t.p95.toFixed(1)),
      p99: t.p99 === null ? null : Number(t.p99.toFixed(1)),
      max: t.max === null ? null : Number(t.max.toFixed(1)),
    });
  }

  await wb.xlsx.writeFile(outputPath);
  console.log(
    `${path.resolve(outputPath)}\n` +
      `  ${num(val("http_reqs", "rate"))} req/s, avg ${ms(val("http_req_duration", "avg"))}, ` +
      `max ${ms(val("http_req_duration", "max"))}`,
  );
}

main().catch((err) => {
  console.error(err);
  process.exit(2);
});
