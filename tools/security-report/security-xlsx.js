#!/usr/bin/env node
/**
 * Render assessment.json as the two workbooks the test brief asks for.
 *
 *   node tools/security-report/security-xlsx.js ["Vulnerability Test Results"]
 *
 * findings.xlsx carries the four sheets the brief names — Security Findings,
 * Endpoint Inventory, Dependency Vulnerabilities, Risk Summary — and
 * endpoint-inventory.xlsx carries the inventory on its own. Both render the
 * same `endpoints` array, which is what stops the two from drifting.
 *
 * The long prose columns (description, exploitation, impact, fix) are the
 * point of the sheet rather than an afterthought, so they get real width and
 * wrapped text. A findings sheet that truncates the exploitation scenario to
 * one visible line is a list of labels, not a report.
 */

const fs = require("node:fs");
const path = require("node:path");

const ExcelJS = require("exceljs");

const OUT_DIR = process.argv[2] || path.join(__dirname, "..", "..", "Vulnerability Test Results");
const ASSESSMENT = path.join(__dirname, "assessment.json");

if (!fs.existsSync(ASSESSMENT)) {
  console.error(`No assessment data at ${ASSESSMENT}`);
  process.exit(2);
}

const data = JSON.parse(fs.readFileSync(ASSESSMENT, "utf8"));

const SEVERITY_ORDER = ["Critical", "High", "Medium", "Low", "Informational"];
const SEVERITY_FILL = {
  Critical: "FFB71C1C",
  High: "FFE65100",
  Medium: "FFF9A825",
  Low: "FF9E9E9E",
  Informational: "FF546E7A",
};
const HEADER_FILL = "FF1F3864";

function styleHeader(sheet) {
  const row = sheet.getRow(1);
  row.font = { bold: true, color: { argb: "FFFFFFFF" }, size: 11 };
  row.fill = { type: "pattern", pattern: "solid", fgColor: { argb: HEADER_FILL } };
  row.alignment = { vertical: "middle", horizontal: "left", wrapText: true };
  row.height = 26;
  sheet.views = [{ state: "frozen", ySplit: 1 }];
}

function wrapBody(sheet, fromColumn = 1) {
  sheet.eachRow((row, n) => {
    if (n === 1) return;
    row.alignment = { vertical: "top", wrapText: true };
    for (let c = 1; c < fromColumn; c += 1) {
      row.getCell(c).alignment = { vertical: "top", wrapText: false };
    }
  });
}

/** Sort findings the way the report argues them: worst first, then by id. */
function bySeverity(a, b) {
  const d = SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity);
  return d !== 0 ? d : a.id.localeCompare(b.id);
}

function addFindings(book) {
  const sheet = book.addWorksheet("Security Findings", {
    properties: { tabColor: { argb: "FFB71C1C" } },
  });
  sheet.columns = [
    { header: "ID", key: "id", width: 10 },
    { header: "Severity", key: "severity", width: 11 },
    { header: "Type", key: "type", width: 40 },
    { header: "File path", key: "file", width: 38 },
    { header: "Endpoint", key: "endpoint", width: 34 },
    { header: "CWE", key: "cwe", width: 11 },
    { header: "CVSS", key: "cvss", width: 16 },
    { header: "Description", key: "description", width: 78 },
    { header: "Exploitation scenario", key: "exploitation", width: 78 },
    { header: "Impact", key: "impact", width: 60 },
    { header: "Recommended fix", key: "fix", width: 78 },
    { header: "Exposure", key: "exposure", width: 40 },
    { header: "Status", key: "status", width: 46 },
    { header: "Verified by", key: "verified", width: 26 },
  ];
  [...data.findings].sort(bySeverity).forEach((f) => {
    const row = sheet.addRow(f);
    const cell = row.getCell("severity");
    cell.fill = { type: "pattern", pattern: "solid", fgColor: { argb: SEVERITY_FILL[f.severity] } };
    cell.font = { bold: true, color: { argb: "FFFFFFFF" } };
    cell.alignment = { vertical: "top", horizontal: "center" };

    // Grey for a deliberate position, amber for something blocked on a third
    // party. A reader scanning the sheet should see which is which without
    // reading a word of the Status text.
    const status = row.getCell("status");
    const blocked = /no upstream|blocked on|not a code defect|not a downgrade/i.test(f.status);
    status.fill = {
      type: "pattern",
      pattern: "solid",
      fgColor: { argb: blocked ? "FFF9A825" : "FF616161" },
    };
    status.font = { bold: true, color: { argb: "FFFFFFFF" } };
  });
  styleHeader(sheet);
  wrapBody(sheet, 8);
  sheet.autoFilter = { from: "A1", to: "N1" };
  return sheet;
}

function addEndpoints(book) {
  const sheet = book.addWorksheet("Endpoint Inventory", {
    properties: { tabColor: { argb: "FF1F3864" } },
  });
  sheet.columns = [
    { header: "Method", key: "method", width: 11 },
    { header: "Path", key: "path", width: 44 },
    { header: "Module", key: "module", width: 15 },
    { header: "Authentication", key: "auth", width: 30 },
    { header: "Rate limit", key: "rate_limit", width: 30 },
    { header: "Tenant scoping", key: "tenancy", width: 26 },
    { header: "Parameters", key: "params", width: 34 },
    { header: "Request body", key: "request_body", width: 13 },
    { header: "Why that is safe", key: "exposure", width: 62 },
    { header: "Source", key: "source", width: 40 },
    { header: "Security notes", key: "notes", width: 90 },
  ];
  data.endpoints.forEach((e) => {
    const row = sheet.addRow(e);
    // An unauthenticated row is not a hole here — four of the six are the
    // credential exchange itself, one is the enrollment code, one is a
    // liveness probe. Colouring them red said the opposite, so they are
    // marked as deliberate and the Exposure column says what guards each.
    if (e.auth.startsWith("None")) {
      row.getCell("auth").font = { bold: true, color: { argb: "FF1B5E20" } };
    }
  });
  styleHeader(sheet);
  wrapBody(sheet, 11);
  sheet.autoFilter = { from: "A1", to: "K1" };
  return sheet;
}

function addDependencies(book) {
  const sheet = book.addWorksheet("Dependency Vulnerabilities", {
    properties: { tabColor: { argb: "FFF9A825" } },
  });
  sheet.columns = [
    { header: "Project", key: "project", width: 22 },
    { header: "Manifest scanned", key: "manifest", width: 44 },
    { header: "Scanner", key: "scanner", width: 18 },
    { header: "Vulnerable packages", key: "vulnerable", width: 20 },
    { header: "Severity", key: "severity", width: 26 },
    { header: "Advisories", key: "advisory", width: 62 },
    { header: "Fix", key: "fix", width: 44 },
    { header: "Reaches a user?", key: "ships", width: 20 },
    { header: "Notes", key: "notes", width: 76 },
  ];
  data.dependencies.forEach((d) => {
    const row = sheet.addRow(d);
    if (d.vulnerable === 0) {
      row.getCell("vulnerable").font = { bold: true, color: { argb: "FF1B5E20" } };
    } else {
      row.getCell("vulnerable").font = { bold: true, color: { argb: "FFE65100" } };
    }
  });
  styleHeader(sheet);
  wrapBody(sheet, 6);
  return sheet;
}

function addRiskSummary(book) {
  const sheet = book.addWorksheet("Risk Summary", {
    properties: { tabColor: { argb: "FF1B5E20" } },
  });
  sheet.columns = [
    { header: "Item", key: "item", width: 46 },
    { header: "Value", key: "value", width: 22 },
    { header: "Detail", key: "detail", width: 110 },
  ];

  const counts = Object.fromEntries(
    SEVERITY_ORDER.map((s) => [s, data.findings.filter((f) => f.severity === s).length]),
  );

  sheet.addRow({ item: "Product", value: "", detail: data.meta.product });
  sheet.addRow({ item: "Commit assessed", value: data.meta.commit, detail: `branch ${data.meta.branch}` });
  sheet.addRow({ item: "Assessment date", value: data.meta.assessed_at, detail: data.meta.target });
  sheet.addRow({ item: "", value: "", detail: "" });

  SEVERITY_ORDER.forEach((s) => {
    const row = sheet.addRow({
      item: `${s} findings`,
      value: counts[s],
      detail:
        data.findings
          .filter((f) => f.severity === s)
          .map((f) => `${f.id} ${f.type}`)
          .join("; ") || "none",
    });
    row.getCell("value").fill = {
      type: "pattern", pattern: "solid", fgColor: { argb: SEVERITY_FILL[s] },
    };
    row.getCell("value").font = { bold: true, color: { argb: "FFFFFFFF" } };
    row.getCell("value").alignment = { horizontal: "center" };
  });

  sheet.addRow({ item: "Total findings", value: data.findings.length, detail: "" }).font = {
    bold: true,
  };
  sheet.addRow({
    item: "Blocked on a third party",
    value: data.findings.filter((f) => /no upstream|blocked on|not a code defect|not a downgrade/i.test(f.status))
      .length,
    detail:
      "A certificate that does not exist, a dependency with no patched release, and a charting " +
      "library's CSP requirement. No code change reaches any of them.",
  });
  sheet.addRow({
    item: "Accepted with the reasoning recorded",
    value: data.findings.filter((f) => /accepted|Informational|defence in depth|mitigated/i.test(f.status))
      .length,
    detail: "Each has a working control doing the job instead. See the Status column.",
  });
  sheet.addRow({ item: "", value: "", detail: "" });
  sheet.addRow({ item: "Endpoints inventoried", value: data.endpoints.length,
    detail: `${data.endpoints.filter((e) => e.auth === "None").length} unauthenticated (/health, /enroll, and the four /auth entry points)` });
  sheet.addRow({ item: "DAST checks executed", value: data.dast.length,
    detail: SEVERITY_ORDER.length ? Object.entries(
      data.dast.reduce((acc, d) => ({ ...acc, [d.verdict]: (acc[d.verdict] || 0) + 1 }), {}),
    ).map(([k, v]) => `${k} ${v}`).join(", ") : "" });
  sheet.addRow({ item: "", value: "", detail: "" });

  const header = sheet.addRow({ item: "Control domain", value: "Score / 100", detail: "Weight and what was deducted" });
  header.font = { bold: true };
  header.fill = { type: "pattern", pattern: "solid", fgColor: { argb: "FFD9E1F2" } };

  data.domains.forEach((d) => {
    sheet.addRow({ item: d.domain, value: d.score, detail: `weight ${d.weight}% — ${d.deductions}` });
  });

  const total = sheet.addRow({
    item: "Weighted security score",
    value: data.meta.score,
    detail:
      "Sum of each domain's score times its weight. The missing points are the three " +
      "third-party blockers above plus four accepted positions — see executive-summary.md " +
      "for what each one would take.",
  });
  total.font = { bold: true, size: 12 };
  total.getCell("value").fill = { type: "pattern", pattern: "solid", fgColor: { argb: "FF1B5E20" } };
  total.getCell("value").font = { bold: true, size: 12, color: { argb: "FFFFFFFF" } };
  total.getCell("value").alignment = { horizontal: "center" };

  styleHeader(sheet);
  wrapBody(sheet, 3);
  return sheet;
}

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });

  const findings = new ExcelJS.Workbook();
  findings.creator = "Sentinel security assessment";
  findings.created = new Date(data.meta.assessed_at);
  addFindings(findings);
  addEndpoints(findings);
  addDependencies(findings);
  addRiskSummary(findings);
  const findingsPath = path.join(OUT_DIR, "findings.xlsx");
  await findings.xlsx.writeFile(findingsPath);

  const inventory = new ExcelJS.Workbook();
  inventory.creator = "Sentinel security assessment";
  inventory.created = new Date(data.meta.assessed_at);
  addEndpoints(inventory);
  const inventoryPath = path.join(OUT_DIR, "endpoint-inventory.xlsx");
  await inventory.xlsx.writeFile(inventoryPath);

  console.log(`wrote ${findingsPath} (4 sheets, ${data.findings.length} findings)`);
  console.log(`wrote ${inventoryPath} (${data.endpoints.length} endpoints)`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
