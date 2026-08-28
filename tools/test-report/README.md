# mocha → xlsx

One reporter, shared by every mocha-based suite in this repo.

```bash
node tools/test-report/mocha-xlsx.js results.json report.xlsx
```

Both `selenium-tests/` and `appium-tests/` depend on it as a `file:` package, so
`npm install` in either one links it and `npm run report` just works. It lives
here rather than in one of them because it reads *mocha's* output format and
knows nothing about Selenium or Appium — a copy in each suite would be two
files that drift, and the one that drifts is always the one you are not looking
at.

Two behaviours worth knowing:

* **A failing run still produces a report.** Each suite's `test:report` script
  ends in `|| true`, because mocha exits non-zero on failure and a CI step that
  stops there uploads no artifact — so the run you most want the spreadsheet
  for is the one that would not produce it. This script re-raises the failure
  through its own exit code *after* the file is written, so a workflow can
  still gate on the result.
* **It derives a Module column from the suite path.** Mocha's JSON flattens the
  describe chain into one `fullTitle` string; without splitting it back out,
  every row repeats the whole prefix and the summary has nothing to group by.
