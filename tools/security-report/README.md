# assessment.json → xlsx

Renders `assessment.json` as the two workbooks deliverable 3 of
`docs/TEST_BRIEF.md` asks for.

```bash
npm install --prefix tools/security-report
node tools/security-report/security-xlsx.js
```

Writes `Vulnerability Test Results/findings.xlsx` (Security Findings, Endpoint
Inventory, Dependency Vulnerabilities, Risk Summary) and
`Vulnerability Test Results/endpoint-inventory.xlsx`. Pass an output directory
as the one argument to write them somewhere else.

It sits here rather than inside `Vulnerability Test Results/` because the brief
names the five files that folder contains and a generator is not one of them —
same reason `tools/test-report/` holds the mocha reporter rather than
`selenium-tests/`.

Three things worth knowing:

* **`assessment.json` is the deliverable's source, not a scan dump.** The
  endpoint rows were derived from the live OpenAPI document and then annotated
  by hand; the findings are analyst judgement over the tool output, not the
  tool output itself. Regenerating the workbooks is reproducible; regenerating
  the *assessment* means running the eight phases again. `security-review.md`
  records what each phase ran so that is possible.
* **The two workbooks share the Endpoint Inventory sheet on purpose.** The
  brief asks for `findings.xlsx` to carry four named sheets, one of which is
  the inventory, *and* for a standalone `endpoint-inventory.xlsx`. Rendering
  both from one array is what stops them disagreeing.
* **Severity drives the row colour, and the order is fixed** —
  Critical, High, Medium, Low — so a reader scanning the sheet sees the same
  ordering the report argues in.
