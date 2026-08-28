#!/usr/bin/env node
/**
 * Turn mocha's JSON output into the .xlsx the brief asks for.
 *
 *   node tools/test-report/mocha-xlsx.js results.json report.xlsx
 *
 * Shared by every mocha-based suite here — Selenium and Appium both — because
 * it reads *mocha's* reporter format and knows nothing about either driver. A
 * copy per suite would be two files that drift, and the one that drifts is
 * always the one you are not looking at.
 *
 * Two things it does that a naive dump does not:
 *
 *   * It derives a Module column from the test's suite path. Mocha's JSON
 *     flattens `fullTitle` into one string, so without splitting it back out
 *     every row reads "Sentinel Web Frontend — E2E Suite Module 3 — Login ...",
 *     and the summary has nothing to group by.
 *   * A failing run still produces a report. `npm run test:report` ends with
 *     `|| true` for exactly this reason: mocha exits non-zero when tests fail,
 *     and a workflow step that stops there uploads no artifact at all — so the
 *     one run you most want the spreadsheet for is the one that does not
 *     produce it.
 */

const fs = require("node:fs");
const path = require("node:path");

/**
 * Resolve exceljs from whichever suite invoked us, not from here.
 *
 * Node resolves a module's dependencies from the file's *real* path, and this
 * file is shared: `selenium-tests/node_modules` is never on its lookup chain
 * no matter how the suite depends on it (an npm `file:` dependency is a
 * symlink, and symlinks are resolved away before the lookup starts). So a
 * plain `require("exceljs")` fails with "Cannot find module 'exceljs'" naming
 * *this* file, which reads as the shared package being broken rather than as
 * it looking in the wrong place.
 *
 * Each suite keeps exceljs in its own devDependencies and this finds it there.
 */
function loadExcelJS() {
  const searchPaths = [process.cwd(), path.join(process.cwd(), "node_modules"), __dirname];
  try {
    return require(require.resolve("exceljs", { paths: searchPaths }));
  } catch {
    console.error(
      "Cannot find exceljs.\n" +
        "Run `npm install` in the suite directory you are reporting on " +
        `(looked from ${process.cwd()}).`,
    );
    process.exit(2);
  }
}

const ExcelJS = loadExcelJS();

const [, , inputPath = "results.json", outputPath = "test-report.xlsx"] = process.argv;

if (!fs.existsSync(inputPath)) {
  console.error(
    `No mocha results at ${inputPath}.\n` +
      `Run the suite first: npm run test:report`,
  );
  process.exit(1);
}

const raw = JSON.parse(fs.readFileSync(inputPath, "utf8"));

/** Mocha flattens the suite path into fullTitle; split the module back out. */
function moduleOf(test) {
  const full = test.fullTitle || test.title || "";
  const title = test.title || "";
  const prefix = full.endsWith(title) ? full.slice(0, -title.length).trim() : full;
  // The outermost describe is the suite name and is the same for every row,
  // so the useful grouping is the *last* segment before the test itself.
  const match = prefix.match(/Module\s+\d+[^"]*/);
  return match ? match[0].trim() : prefix || "(ungrouped)";
}

function rowsFrom(list, status) {
  return (list || []).map((t) => ({
    module: moduleOf(t),
    title: t.title || "(untitled)",
    status,
    durationMs: typeof t.duration === "number" ? t.duration : null,
    error: t.err && t.err.message ? String(t.err.message).split("\n")[0].slice(0, 500) : "",
  }));
}

// `passes`, `failures` and `pending` are disjoint in mocha's JSON; `tests` is
// the union, so using it as well would double-count every row.
const rows = [
  ...rowsFrom(raw.passes, "PASS"),
  ...rowsFrom(raw.failures, "FAIL"),
  ...rowsFrom(raw.pending, "SKIP"),
];

const stats = raw.stats || {};
const total = rows.length;
const passed = rows.filter((r) => r.status === "PASS").length;
const failed = rows.filter((r) => r.status === "FAIL").length;
const skipped = rows.filter((r) => r.status === "SKIP").length;
// Skipped tests did not assert anything, so counting them as passes would
// inflate the rate; counting them as failures would punish a deliberate skip.
const executed = passed + failed;
const passRate = executed > 0 ? passed / executed : 0;

const byModule = new Map();
for (const r of rows) {
  const m = byModule.get(r.module) || { total: 0, pass: 0, fail: 0, skip: 0, ms: 0 };
  m.total += 1;
  if (r.status === "PASS") m.pass += 1;
  else if (r.status === "FAIL") m.fail += 1;
  else m.skip += 1;
  m.ms += r.durationMs || 0;
  byModule.set(r.module, m);
}

const HEADER = { bold: true, color: { argb: "FFFFFFFF" } };
const HEADER_FILL = { type: "pattern", pattern: "solid", fgColor: { argb: "FF1F2937" } };
const TONE = { PASS: "FF16A34A", FAIL: "FFDC2626", SKIP: "FFCA8A04" };

function styleHeader(sheet) {
  const row = sheet.getRow(1);
  row.font = HEADER;
  row.fill = HEADER_FILL;
  row.alignment = { vertical: "middle" };
  sheet.views = [{ state: "frozen", ySplit: 1 }];
}

async function main() {
  const wb = new ExcelJS.Workbook();
  wb.creator = "Sentinel test tooling";
  wb.created = new Date();

  // --- Sheet 1: Summary ----------------------------------------------------
  const summary = wb.addWorksheet("Summary");
  summary.columns = [
    { header: "Metric", key: "k", width: 34 },
    { header: "Value", key: "v", width: 46 },
  ];
  styleHeader(summary);

  const durationSec = stats.duration ? (stats.duration / 1000).toFixed(1) : "—";
  for (const [k, v] of [
    ["Suite", process.env.TEST_SUITE_NAME || "Sentinel E2E"],
    ["Run at", stats.end || new Date().toISOString()],
    ["Base URL", process.env.SENTINEL_URL || "http://localhost:8000"],
    ["Total test cases", total],
    ["Passed", passed],
    ["Failed", failed],
    ["Skipped", skipped],
    ["Pass rate (of executed)", `${(passRate * 100).toFixed(1)}%`],
    ["Duration", `${durationSec}s`],
  ]) {
    summary.addRow({ k, v });
  }

  summary.addRow({});
  const head = summary.addRow({ k: "Module", v: "Passed / Failed / Skipped" });
  head.font = { bold: true };
  for (const [name, m] of [...byModule.entries()].sort()) {
    const row = summary.addRow({ k: name, v: `${m.pass} / ${m.fail} / ${m.skip}` });
    if (m.fail > 0) row.getCell("v").font = { color: { argb: TONE.FAIL } };
  }

  // --- Sheet 2: Test Details ----------------------------------------------
  const details = wb.addWorksheet("Test Details");
  details.columns = [
    { header: "#", key: "n", width: 6 },
    { header: "Module", key: "module", width: 38 },
    { header: "Test case", key: "title", width: 74 },
    { header: "Status", key: "status", width: 10 },
    { header: "Duration (ms)", key: "durationMs", width: 15 },
    { header: "Failure", key: "error", width: 90 },
  ];
  styleHeader(details);
  details.autoFilter = { from: "A1", to: "F1" };

  rows.forEach((r, i) => {
    const row = details.addRow({ n: i + 1, ...r });
    row.getCell("status").font = { bold: true, color: { argb: TONE[r.status] } };
    row.getCell("error").alignment = { wrapText: true, vertical: "top" };
  });

  await wb.xlsx.writeFile(outputPath);
  console.log(
    `${path.resolve(outputPath)}\n` +
      `  ${total} cases — ${passed} passed, ${failed} failed, ${skipped} skipped`,
  );

  // Exit non-zero on failures so a workflow step can still gate on the result
  // *after* the artifact has been written.
  process.exit(failed > 0 ? 1 : 0);
}

main().catch((err) => {
  console.error(err);
  process.exit(2);
});
