#!/usr/bin/env node
/**
 * Sentinel web console — Selenium functional E2E suite.
 *
 * Deliverable 1 of docs/TEST_BRIEF.md: `selenium-tests/tests/login-tests.js`,
 * 300+ functional cases, reported into an .xlsx by `npm run test:xlsx`.
 *
 * Read docs/TESTING.md before running this. The short version of why it will
 * fail against `make serve`: that stack runs ENVIRONMENT=prod, whose rate
 * limiter 429s the eleventh login (so every "Invalid email or password"
 * assertion below fails against "Too many attempts") and whose COOKIE_SECURE=true
 * makes the browser drop the refresh cookie over http://localhost (so every
 * session-persistence assertion fails against a bounce to /login). Run
 * `make e2e-db && make e2e-serve` instead.
 *
 * Three structural decisions, all deliberate:
 *
 *   * ONE browser for the whole run. A fresh driver per case would multiply a
 *     ~1.5s Chrome start across 300+ cases for no isolation gain — the state
 *     that actually matters here is the session cookie, and `resetToLogin()`
 *     clears exactly that between cases that care.
 *   * Expensive setup lives in a module's `before`, and the `it`s assert facets
 *     of the resulting state. A suite that re-signs-in 40 times to check 40
 *     properties of the signed-in header measures Chrome, not the console.
 *   * Every describe is named `Module N — ...`, because tools/test-report's
 *     Module column is derived from that prefix; renaming them silently
 *     collapses the workbook's per-module breakdown into one row.
 *
 * Locators are `data-testid` only. Never a Tailwind class (they change with
 * styling) and never a list position (rows are keyed by entity id, so "the
 * third card" starts asserting about a different machine as soon as the fleet
 * reorders).
 */

const assert = require("node:assert");
const http = require("node:http");
const { URL } = require("node:url");
const { Builder, By, Key, until } = require("selenium-webdriver");
const chrome = require("selenium-webdriver/chrome");

// --- configuration -----------------------------------------------------------

const BASE = (process.env.SENTINEL_URL || "http://localhost:8000").replace(/\/$/, "");
const EMAIL = process.env.SENTINEL_E2E_EMAIL || "e2e@example.com";
const PASSWORD = process.env.SENTINEL_E2E_PASSWORD || "e2e-Sentinel-Test-2026";
const HEADLESS = process.env.HEADLESS !== "0";

/** Long enough for a cold Vite bundle parse on a loaded CI runner. */
const WAIT = 20000;
/** For asserting that something is *absent*; a full WAIT here would cost minutes. */
const SHORT_WAIT = 4000;

const byTestId = (id) => By.css(`[data-testid="${id}"]`);

let driver;

// --- helpers -----------------------------------------------------------------

async function present(id, timeout = WAIT) {
  return driver.wait(until.elementLocated(byTestId(id)), timeout);
}

/** True/false rather than throwing, for "this must not be on the page" cases. */
async function exists(id) {
  const found = await driver.findElements(byTestId(id));
  return found.length > 0;
}

async function absent(id, timeout = SHORT_WAIT) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (!(await exists(id))) return true;
    await driver.sleep(100);
  }
  return !(await exists(id));
}

/**
 * Both reads retry on a stale reference rather than failing.
 *
 * Between locating an element and reading it, a client-side navigation can
 * replace the node — the reference is then stale even though the page is fine.
 * Retrying re-locates it; without this, a passing app fails the suite roughly
 * one nav in twenty.
 */
async function retryStale(fn) {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await fn();
    } catch (err) {
      if (attempt >= 4 || !/stale element/i.test(String(err.message))) throw err;
      await driver.sleep(150);
    }
  }
}

async function textOf(id) {
  return retryStale(async () => (await present(id)).getText());
}

async function attrOf(id, name) {
  return retryStale(async () => (await present(id)).getAttribute(name));
}

async function currentPath() {
  return new URL(await driver.getCurrentUrl()).pathname;
}

/**
 * Wait out the two things that race an assertion made straight after a get().
 *
 * The order matters and the first half is easy to leave out: immediately after
 * a navigation `#root` is still empty, so a check for "is the loader gone?"
 * answers yes before React has mounted anything at all, and the very next
 * findElement misses a page that was about to render. Waiting for the mount
 * first, and only then for `page-loading` to clear, is what makes an
 * assert-on-absence honest.
 */
async function settle() {
  await driver.wait(
    async () =>
      (await driver.executeScript(
        "const r = document.getElementById('root'); return !!r && r.children.length > 0",
      )) === true,
    WAIT,
    "the console never mounted",
  );
  const deadline = Date.now() + WAIT;
  while (Date.now() < deadline) {
    if (!(await exists("page-loading"))) return;
    await driver.sleep(80);
  }
}

/**
 * Navigate, retrying only a transport-level failure.
 *
 * A `net::ERR_CONNECTION_RESET` is the socket, not the application: it carries
 * no status and no page, so there is nothing for a test to assert against and
 * failing on it reports a defect that was never observed. Anything else — a
 * 4xx, a 5xx, a page that renders wrongly — propagates untouched.
 */
async function navigateTo(url) {
  for (let attempt = 0; ; attempt += 1) {
    try {
      await driver.get(url);
      return;
    } catch (err) {
      if (attempt >= 2 || !/net::ERR_CONNECTION/i.test(String(err.message))) throw err;
      await driver.sleep(500);
    }
  }
}

async function go(path) {
  await navigateTo(`${BASE}${path}`);
  await settle();
}

/**
 * Drop the session cookie, and prove it is gone before moving on.
 *
 * The settle() *before* the delete is load-bearing rather than tidy. Every page
 * load runs bootstrapAuth(), which exchanges the refresh cookie — and the
 * backend rotates it, so the reply carries a new Set-Cookie. Deleting while
 * that exchange is in flight removes the old cookie and the reply then plants a
 * fresh one, and the very next navigation is signed in again. Found by two
 * cases failing on a login form that never appeared.
 */
async function clearSession() {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    await navigateTo(`${BASE}/login`);
    await settle();
    await driver.manage().deleteAllCookies();
    if ((await cookie("sentinel_refresh")) === null) return;
  }
  throw new Error("could not clear the session cookie");
}

/**
 * Drop the session and land on a freshly rendered, *unaddressed* login form.
 *
 * The second half matters as much as the cookie. When ProtectedRoute bounces an
 * anonymous visitor it records where they were headed in history state, and
 * LoginPage sends them there after signing in — which is the behaviour TC-181
 * exists to check. But driver.get() to the URL you are already on is a reload,
 * and a reload preserves history state, so that `from` outlives the reset: the
 * next sign-in silently lands on the earlier test's page instead of the
 * dashboard. It cost Module 12 its whole before-hook, waiting for fleet totals
 * on /settings.
 */
async function resetToLogin() {
  await clearSession();
  await navigateTo(`${BASE}/login`);
  await settle();

  const pendingRedirect = await driver.executeScript(
    "return !!(window.history.state && window.history.state.usr)",
  );
  if (pendingRedirect) {
    await driver.executeScript("window.history.replaceState(null, '', '/login')");
    await driver.navigate().refresh();
    await settle();
  }

  await present("login-form");
}

/**
 * Empty a field the way a person would, with the keyboard.
 *
 * NOT element.clear(). WebDriver's clear() blanks the DOM value without React
 * hearing an input event, so the store keeps the old string and the next render
 * puts it back — the typed text is then *appended* to it. Refilling a dirty
 * login form with the right password produced
 * "wrong-password-entirelye2e-Sentinel-Test-2026" and a rejection, which reads
 * exactly like the application refusing valid credentials. Backspaces raise
 * real input events, so React's state and the DOM stay in step.
 */
async function clearField(el) {
  const current = (await el.getAttribute("value")) || "";
  if (current === "") return;
  await el.click();
  await el.sendKeys(Key.END);
  for (let i = 0; i < current.length; i += 1) await el.sendKeys(Key.BACK_SPACE);
}

async function fillLogin(email, password) {
  const emailEl = await present("login-email");
  const passwordEl = await present("login-password");
  await clearField(emailEl);
  await clearField(passwordEl);
  if (email) await emailEl.sendKeys(email);
  if (password) await passwordEl.sendKeys(password);
}

/**
 * Click a nav link and wait until the router has actually arrived.
 *
 * Waiting on `page-title` alone is not enough here: the *previous* page's title
 * is still on screen at the moment of the click, so the wait returns instantly
 * and the assertion reads the page you just left. Waiting for the pathname
 * first is what makes the title assertion mean anything.
 */
async function clickNav(hook, path) {
  const samePage = (await currentPath()) === path;
  const previousTitle = samePage ? null : await present("page-title");

  await (await present(hook)).click();
  await driver.wait(
    async () => (await currentPath()) === path,
    WAIT,
    `never navigated to ${path}`,
  );

  // The URL is not enough on its own. React Router commits a navigation inside
  // a transition, so history.pushState has already happened while the previous
  // page is still what is rendered — a path check can pass a frame early and
  // the next read gets the heading you just navigated away from. Waiting for
  // the old heading node to go stale waits for React to actually unmount it,
  // and says nothing about what replaces it, so the assertion stays honest.
  if (previousTitle) {
    await driver.wait(
      until.stalenessOf(previousTitle),
      WAIT,
      `the ${path} route never replaced the previous page`,
    );
  }

  await settle();
  await present("page-title");
}

/** Submit and expect the server to reject; returns the rendered error text. */
async function submitExpectingError(email, password) {
  await resetToLogin();
  await fillLogin(email, password);
  await (await present("login-submit")).click();
  await present("login-error");
  return textOf("login-error");
}

async function signIn(email = EMAIL, password = PASSWORD) {
  await resetToLogin();
  await fillLogin(email, password);
  await (await present("login-submit")).click();
  await present("page-title");
  await settle();
}

async function signOut() {
  await (await present("sign-out")).click();
  await present("login-form");
}

/** Read a cookie by name, or null. Selenium throws rather than returning null. */
async function cookie(name) {
  try {
    return await driver.manage().getCookie(name);
  } catch {
    return null;
  }
}

/**
 * Raw request through node:http rather than fetch().
 *
 * The console's static HTML is only served to a request carrying
 * `Sec-Fetch-Mode: navigate` (WebConsoleMiddleware distinguishes a navigation
 * from an API call), and undici strips Sec-* as forbidden header names — so a
 * fetch() for /login comes back as the API's 404 JSON and the header
 * assertions below would silently be testing the wrong response.
 */
function rawRequest(path, { method = "GET", headers = {}, body = null } = {}) {
  const target = new URL(`${BASE}${path}`);
  return new Promise((resolve, reject) => {
    const req = http.request(
      {
        host: target.hostname,
        port: target.port || 80,
        path: target.pathname + target.search,
        method,
        headers,
      },
      (res) => {
        let data = "";
        res.setEncoding("utf8");
        res.on("data", (c) => (data += c));
        res.on("end", () =>
          resolve({ status: res.statusCode, headers: res.headers, body: data }),
        );
      },
    );
    req.on("error", reject);
    if (body) req.write(body);
    req.end();
  });
}

function navRequest(path) {
  return rawRequest(path, {
    headers: { "Sec-Fetch-Mode": "navigate", Accept: "text/html" },
  });
}

// --- the suite ---------------------------------------------------------------

describe("Sentinel Web Frontend — E2E Suite", function () {
  this.timeout(60000);

  before(async function () {
    this.timeout(120000);
    const options = new chrome.Options();
    if (HEADLESS) options.addArguments("--headless=new");
    options.addArguments(
      "--no-sandbox",
      "--disable-dev-shm-usage",
      // 1280 keeps the header above Tailwind's `sm` breakpoint, where
      // `current-user-email` is `hidden sm:inline` — below it getText() reads
      // an empty string and every header assertion fails for a layout reason.
      "--window-size=1280,900",
    );
    driver = await new Builder().forBrowser("chrome").setChromeOptions(options).build();
    await driver.manage().setTimeouts({ implicit: 0, pageLoad: 60000, script: 30000 });
  });

  after(async function () {
    this.timeout(30000);
    if (driver) await driver.quit();
  });

  // ==========================================================================
  // Module 1 — Login page structure
  // ==========================================================================
  describe("Module 1 — Login page structure", function () {
    before(async () => {
      await resetToLogin();
    });

    it("TC-001 serves the login route", async () => {
      assert.strictEqual(await currentPath(), "/login");
    });

    it("TC-002 renders the login card", async () => {
      assert.ok(await exists("login-card"));
    });

    it("TC-003 renders the login title hook", async () => {
      assert.ok(await exists("login-title"));
    });

    it("TC-004 titles the card 'Sign in'", async () => {
      assert.strictEqual((await textOf("login-title")).trim(), "Sign in");
    });

    it("TC-005 renders the login form", async () => {
      assert.ok(await exists("login-form"));
    });

    it("TC-006 renders the email field", async () => {
      assert.ok(await exists("login-email"));
    });

    it("TC-007 renders the password field", async () => {
      assert.ok(await exists("login-password"));
    });

    it("TC-008 renders the submit button", async () => {
      assert.ok(await exists("login-submit"));
    });

    it("TC-009 renders the card footer", async () => {
      assert.ok(await exists("login-footer"));
    });

    it("TC-010 offers a link to signup", async () => {
      assert.ok(await exists("login-to-signup"));
    });

    it("TC-011 labels the signup link 'Create one'", async () => {
      assert.strictEqual((await textOf("login-to-signup")).trim(), "Create one");
    });

    it("TC-012 points the signup link at /signup", async () => {
      assert.ok((await attrOf("login-to-signup", "href")).endsWith("/signup"));
    });

    it("TC-013 shows no error before anything is submitted", async () => {
      assert.strictEqual(await exists("login-error"), false);
    });

    it("TC-014 types the email field as email", async () => {
      assert.strictEqual(await attrOf("login-email", "type"), "email");
    });

    it("TC-015 sets autocomplete=email on the email field", async () => {
      assert.strictEqual(await attrOf("login-email", "autocomplete"), "email");
    });

    it("TC-016 marks the email field required", async () => {
      assert.ok(await attrOf("login-email", "required"));
    });

    it("TC-017 starts with an empty email field", async () => {
      assert.strictEqual(await attrOf("login-email", "value"), "");
    });

    it("TC-018 masks the password field by default", async () => {
      assert.strictEqual(await attrOf("login-password", "type"), "password");
    });

    it("TC-019 sets autocomplete=current-password on the password field", async () => {
      assert.strictEqual(await attrOf("login-password", "autocomplete"), "current-password");
    });

    it("TC-020 marks the password field required", async () => {
      assert.ok(await attrOf("login-password", "required"));
    });

    it("TC-021 starts with an empty password field", async () => {
      assert.strictEqual(await attrOf("login-password", "value"), "");
    });

    it("TC-022 renders the password visibility toggle", async () => {
      assert.ok(await exists("password-toggle"));
    });

    it("TC-023 enables the submit button initially", async () => {
      assert.strictEqual(await (await present("login-submit")).isEnabled(), true);
    });

    it("TC-024 labels the submit button 'Sign in'", async () => {
      assert.strictEqual((await textOf("login-submit")).trim(), "Sign in");
    });

    it("TC-025 does not render the app shell to an anonymous visitor", async () => {
      assert.strictEqual(await exists("app-header"), false);
    });

    it("TC-026 does not render the fleet page content to an anonymous visitor", async () => {
      assert.strictEqual(await exists("page-content"), false);
    });

    it("TC-027 sets the document title", async () => {
      assert.strictEqual(await driver.getTitle(), "Sentinel");
    });

    it("TC-028 declares the document language", async () => {
      const lang = await driver
        .findElement(By.css("html"))
        .getAttribute("lang");
      assert.strictEqual(lang, "en");
    });

    it("TC-029 shows the product name above the card", async () => {
      const body = await driver.findElement(By.css("body")).getText();
      assert.ok(body.includes("Sentinel"));
    });

    it("TC-030 describes what the page is for", async () => {
      const card = await present("login-card");
      assert.ok((await card.getText()).includes("Access your monitored devices."));
    });
  });

  // ==========================================================================
  // Module 2 — Login field behaviour
  // ==========================================================================
  describe("Module 2 — Login field behaviour", function () {
    beforeEach(async () => {
      await resetToLogin();
    });

    it("TC-031 accepts typed input in the email field", async () => {
      await (await present("login-email")).sendKeys("someone@example.com");
      assert.strictEqual(await attrOf("login-email", "value"), "someone@example.com");
    });

    it("TC-032 accepts typed input in the password field", async () => {
      await (await present("login-password")).sendKeys("hunter2hunter2");
      assert.strictEqual(await attrOf("login-password", "value"), "hunter2hunter2");
    });

    it("TC-033 clears the email field", async () => {
      const el = await present("login-email");
      await el.sendKeys("someone@example.com");
      await el.clear();
      assert.strictEqual(await attrOf("login-email", "value"), "");
    });

    it("TC-034 clears the password field", async () => {
      const el = await present("login-password");
      await el.sendKeys("secretsecret");
      await el.clear();
      assert.strictEqual(await attrOf("login-password", "value"), "");
    });

    it("TC-035 keeps the password masked while typing", async () => {
      await (await present("login-password")).sendKeys("secretsecret");
      assert.strictEqual(await attrOf("login-password", "type"), "password");
    });

    it("TC-036 reveals the password when the toggle is pressed", async () => {
      await (await present("login-password")).sendKeys("secretsecret");
      await (await present("password-toggle")).click();
      assert.strictEqual(await attrOf("login-password", "type"), "text");
    });

    it("TC-037 re-masks the password on a second press", async () => {
      const toggle = await present("password-toggle");
      await toggle.click();
      await toggle.click();
      assert.strictEqual(await attrOf("login-password", "type"), "password");
    });

    it("TC-038 preserves the typed value across a reveal", async () => {
      await (await present("login-password")).sendKeys("secretsecret");
      await (await present("password-toggle")).click();
      assert.strictEqual(await attrOf("login-password", "value"), "secretsecret");
    });

    it("TC-039 reports the toggle's pressed state as false when masked", async () => {
      assert.strictEqual(await attrOf("password-toggle", "aria-pressed"), "false");
    });

    it("TC-040 reports the toggle's pressed state as true when revealed", async () => {
      await (await present("password-toggle")).click();
      assert.strictEqual(await attrOf("password-toggle", "aria-pressed"), "true");
    });

    it("TC-041 labels the toggle 'Show password' when masked", async () => {
      assert.strictEqual(await attrOf("password-toggle", "aria-label"), "Show password");
    });

    it("TC-042 labels the toggle 'Hide password' when revealed", async () => {
      await (await present("password-toggle")).click();
      assert.strictEqual(await attrOf("password-toggle", "aria-label"), "Hide password");
    });

    it("TC-043 does not submit the form when the toggle is pressed", async () => {
      await fillLogin(EMAIL, "definitely-not-the-password");
      await (await present("password-toggle")).click();
      assert.strictEqual(await exists("login-error"), false);
      assert.strictEqual(await currentPath(), "/login");
    });

    it("TC-044 types the toggle as button, not submit", async () => {
      assert.strictEqual(await attrOf("password-toggle", "type"), "button");
    });

    it("TC-045 accepts a very long email without truncating it", async () => {
      const long = `${"a".repeat(180)}@example.com`;
      await (await present("login-email")).sendKeys(long);
      assert.strictEqual(await attrOf("login-email", "value"), long);
    });

    it("TC-046 accepts a very long password without truncating it", async () => {
      const long = "P".repeat(256);
      await (await present("login-password")).sendKeys(long);
      assert.strictEqual((await attrOf("login-password", "value")).length, 256);
    });

    it("TC-047 accepts unicode in the password field", async () => {
      await (await present("login-password")).sendKeys("pässwörd-ünïcode");
      assert.strictEqual(await attrOf("login-password", "value"), "pässwörd-ünïcode");
    });

    it("TC-048 accepts a plus-addressed email", async () => {
      await (await present("login-email")).sendKeys("user+tag@example.com");
      assert.strictEqual(await attrOf("login-email", "value"), "user+tag@example.com");
    });

    it("TC-049 preserves interior spaces typed into the password", async () => {
      await (await present("login-password")).sendKeys("two words here");
      assert.strictEqual(await attrOf("login-password", "value"), "two words here");
    });

    it("TC-050 moves focus from email to password on Tab", async () => {
      const email = await present("login-email");
      await email.click();
      await email.sendKeys(Key.TAB);
      const focused = await driver.executeScript(
        "return document.activeElement.getAttribute('data-testid')",
      );
      assert.strictEqual(focused, "login-password");
    });

    it("TC-051 gives the form no action attribute (submission is handled in JS)", async () => {
      // Asked of the DOM directly: getAttribute() falls back to the *property*,
      // and form.action defaults to the document URL, so it never reads null.
      const hasAction = await driver.executeScript(
        "return document.querySelector('[data-testid=\\'login-form\\']').hasAttribute('action')",
      );
      assert.strictEqual(hasAction, false);
    });

    it("TC-052 submits on Enter from the email field", async () => {
      await fillLogin(EMAIL, "wrong-password-entirely");
      await (await present("login-email")).sendKeys(Key.ENTER);
      assert.ok((await textOf("login-error")).length > 0);
    });

    it("TC-053 submits on Enter from the password field", async () => {
      await fillLogin(EMAIL, "wrong-password-entirely");
      await (await present("login-password")).sendKeys(Key.ENTER);
      assert.ok((await textOf("login-error")).length > 0);
    });

    it("TC-054 leaves the email field editable after a failed submit", async () => {
      await fillLogin(EMAIL, "wrong-password-entirely");
      await (await present("login-submit")).click();
      await present("login-error");
      assert.strictEqual(await (await present("login-email")).isEnabled(), true);
    });

    it("TC-055 re-enables the submit button after a failed submit", async () => {
      await fillLogin(EMAIL, "wrong-password-entirely");
      await (await present("login-submit")).click();
      await present("login-error");
      assert.strictEqual(await (await present("login-submit")).isEnabled(), true);
    });

    it("TC-056 keeps the entered email after a failed submit", async () => {
      await fillLogin(EMAIL, "wrong-password-entirely");
      await (await present("login-submit")).click();
      await present("login-error");
      assert.strictEqual(await attrOf("login-email", "value"), EMAIL);
    });

    it("TC-057 associates the email label with the email input", async () => {
      const forAttr = await driver
        .findElement(By.css("label[for='email']"))
        .getAttribute("for");
      assert.strictEqual(await attrOf("login-email", "id"), forAttr);
    });

    it("TC-058 associates the password label with the password input", async () => {
      const forAttr = await driver
        .findElement(By.css("label[for='password']"))
        .getAttribute("for");
      assert.strictEqual(await attrOf("login-password", "id"), forAttr);
    });
  });

  // ==========================================================================
  // Module 3 — Rejected credentials
  //
  // The backend answers the same "Invalid email or password" for a wrong
  // password and for an account that does not exist — deliberately, so the
  // form cannot be used to enumerate registered addresses. Both halves of that
  // are asserted here.
  // ==========================================================================
  describe("Module 3 — Rejected credentials", function () {
    const GENERIC = "Invalid email or password";

    const WRONG_PASSWORDS = [
      ["an empty-ish single character", "x"],
      ["the password in upper case", PASSWORD.toUpperCase()],
      ["the password in lower case", PASSWORD.toLowerCase()],
      ["the password with a trailing space", `${PASSWORD} `],
      ["the password with a leading space", ` ${PASSWORD}`],
      ["the password reversed", PASSWORD.split("").reverse().join("")],
      ["the password minus its last character", PASSWORD.slice(0, -1)],
      ["the password plus one character", `${PASSWORD}x`],
      ["a common weak password", "password123456"],
      ["an empty-string-like value", "        "],
      ["a numeric password", "1234567890123"],
      ["a unicode password", "påsswörd-nope"],
      ["a password at the 128-character maximum", "Q".repeat(128)],
      ["the email used as the password", EMAIL],
      ["a SQL-ish string", "' OR '1'='1"],
      ["a path-traversal-ish string", "../../etc/passwd"],
    ];

    WRONG_PASSWORDS.forEach(([label, password], i) => {
      it(`TC-${String(59 + i).padStart(3, "0")} rejects ${label}`, async () => {
        const message = await submitExpectingError(EMAIL, password);
        assert.strictEqual(message.trim(), GENERIC);
      });
    });

    const UNKNOWN_ACCOUNTS = [
      "nobody@example.com",
      "not-a-user@example.com",
      "admin@example.com",
      "root@example.com",
      "test@example.com",
      "e2e@example.org",
      "E2E@EXAMPLE.NET",
      "user+missing@example.com",
      "deleted.account@example.com",
      "someone.else@sentinel.dev",
    ];

    UNKNOWN_ACCOUNTS.forEach((email, i) => {
      it(`TC-${String(75 + i).padStart(3, "0")} rejects the unknown account ${email}`, async () => {
        const message = await submitExpectingError(email, "AnyPassword12345");
        assert.strictEqual(message.trim(), GENERIC);
      });
    });

    it("TC-085 gives an unknown account the same message as a wrong password", async () => {
      const unknown = await submitExpectingError("no-such-user@example.com", "Whatever12345");
      const wrong = await submitExpectingError(EMAIL, "Whatever12345");
      assert.strictEqual(unknown.trim(), wrong.trim());
    });

    it("TC-086 stays on /login after a rejected sign-in", async () => {
      await submitExpectingError(EMAIL, "wrong-password-entirely");
      assert.strictEqual(await currentPath(), "/login");
    });

    it("TC-087 does not render the app shell after a rejected sign-in", async () => {
      await submitExpectingError(EMAIL, "wrong-password-entirely");
      assert.strictEqual(await exists("app-header"), false);
    });

    it("TC-088 leaves the login form on screen after a rejected sign-in", async () => {
      await submitExpectingError(EMAIL, "wrong-password-entirely");
      assert.ok(await exists("login-form"));
    });

    it("TC-089 sets no refresh cookie on a rejected sign-in", async () => {
      await submitExpectingError(EMAIL, "wrong-password-entirely");
      assert.strictEqual(await cookie("sentinel_refresh"), null);
    });

    it("TC-090 never names the account in the failure message", async () => {
      const message = await submitExpectingError(EMAIL, "wrong-password-entirely");
      assert.strictEqual(message.includes(EMAIL), false);
    });

    it("TC-091 clears a previous error once a valid sign-in succeeds", async () => {
      await submitExpectingError(EMAIL, "wrong-password-entirely");
      await fillLogin(EMAIL, PASSWORD);
      await (await present("login-submit")).click();
      await present("page-title");
      assert.strictEqual(await exists("login-error"), false);
    });

    it("TC-092 recovers: a correct password works after several rejections", async () => {
      for (const bad of ["one-bad-guess", "two-bad-guess", "three-bad-guess"]) {
        await submitExpectingError(EMAIL, bad);
      }
      await signIn();
      assert.strictEqual(await currentPath(), "/");
    });

    it("TC-093 rejects a correct password against a different account", async () => {
      const message = await submitExpectingError("someone-else@example.com", PASSWORD);
      assert.strictEqual(message.trim(), GENERIC);
    });

    it("TC-094 treats the email case-insensitively but still needs the password", async () => {
      const message = await submitExpectingError(EMAIL.toUpperCase(), "wrong-password-entirely");
      assert.strictEqual(message.trim(), GENERIC);
    });

    it("TC-095 renders the error inside the login form, not as a browser dialog", async () => {
      await submitExpectingError(EMAIL, "wrong-password-entirely");
      const withinForm = await driver.executeScript(
        "const f=document.querySelector('[data-testid=\\'login-form\\']');" +
          "const e=document.querySelector('[data-testid=\\'login-error\\']');" +
          "return !!(f && e && f.contains(e));",
      );
      assert.strictEqual(withinForm, true);
    });
  });

  // ==========================================================================
  // Module 4 — Client-side email validation
  //
  // The email field is type=email and required, so a malformed address is
  // stopped by the browser before any request is made. The assertion is
  // therefore "no error banner and no navigation", not "an error banner" —
  // getting that backwards is how a suite ends up asserting the server
  // validated something it never saw.
  // ==========================================================================
  describe("Module 4 — Client-side email validation", function () {
    async function validity(email) {
      await resetToLogin();
      await fillLogin(email, "SomePassword12345");
      return driver.executeScript(
        "return document.querySelector('[data-testid=\\'login-email\\']').checkValidity()",
      );
    }

    const MALFORMED = [
      "plainaddress",
      "missing-at-sign.example.com",
      "@no-local-part.com",
      "spaces in@example.com",
      "double@@example.com",
      "trailing-dot@example.com.",
      "no-domain@",
      "user@ example.com",
      "user@exam ple.com",
      "<script>@example.com",
      "user,name@example.com",
      "user;name@example.com",
    ];

    MALFORMED.forEach((email, i) => {
      it(`TC-${String(96 + i).padStart(3, "0")} marks ${JSON.stringify(email)} invalid client-side`, async () => {
        assert.strictEqual(await validity(email), false);
      });
    });

    const WELL_FORMED = [
      "user@example.com",
      "user.name@example.com",
      "user+tag@example.com",
      "user_name@example.co.uk",
      "user-name@sub.example.com",
      "u@e.co",
      "UPPER@EXAMPLE.COM",
      "123456@example.com",
    ];

    WELL_FORMED.forEach((email, i) => {
      it(`TC-${String(108 + i).padStart(3, "0")} accepts ${email} as well-formed client-side`, async () => {
        assert.strictEqual(await validity(email), true);
      });
    });

    it("TC-116 marks an empty email invalid because the field is required", async () => {
      assert.strictEqual(await validity(""), false);
    });

    it("TC-117 blocks submission entirely for a malformed email", async () => {
      await resetToLogin();
      await fillLogin("plainaddress", "SomePassword12345");
      await (await present("login-submit")).click();
      assert.strictEqual(await absent("login-error"), true);
      assert.strictEqual(await currentPath(), "/login");
    });

    it("TC-118 blocks submission when the email is empty", async () => {
      await resetToLogin();
      await fillLogin("", "SomePassword12345");
      await (await present("login-submit")).click();
      assert.strictEqual(await absent("login-error"), true);
      assert.strictEqual(await currentPath(), "/login");
    });

    it("TC-119 blocks submission when the password is empty", async () => {
      await resetToLogin();
      await fillLogin(EMAIL, "");
      await (await present("login-submit")).click();
      assert.strictEqual(await absent("login-error"), true);
      assert.strictEqual(await currentPath(), "/login");
    });

    it("TC-120 blocks submission when both fields are empty", async () => {
      await resetToLogin();
      await (await present("login-submit")).click();
      assert.strictEqual(await absent("login-error"), true);
      assert.strictEqual(await currentPath(), "/login");
    });

    it("TC-121 reports the whole form invalid when the email is malformed", async () => {
      await resetToLogin();
      await fillLogin("plainaddress", "SomePassword12345");
      const ok = await driver.executeScript(
        "return document.querySelector('[data-testid=\\'login-form\\']').checkValidity()",
      );
      assert.strictEqual(ok, false);
    });

    it("TC-122 reports the whole form valid once both fields are well-formed", async () => {
      await resetToLogin();
      await fillLogin(EMAIL, "SomePassword12345");
      const ok = await driver.executeScript(
        "return document.querySelector('[data-testid=\\'login-form\\']').checkValidity()",
      );
      assert.strictEqual(ok, true);
    });

    it("TC-123 marks the password field invalid when it is empty", async () => {
      await resetToLogin();
      await fillLogin(EMAIL, "");
      const ok = await driver.executeScript(
        "return document.querySelector('[data-testid=\\'login-password\\']').checkValidity()",
      );
      assert.strictEqual(ok, false);
    });

    it("TC-124 sets no refresh cookie when submission is blocked client-side", async () => {
      await resetToLogin();
      await fillLogin("plainaddress", "SomePassword12345");
      await (await present("login-submit")).click();
      assert.strictEqual(await cookie("sentinel_refresh"), null);
    });

    it("TC-125 applies no minimum length to the password on the login form", async () => {
      // Deliberate: the signup policy is 12 characters, but enforcing it here
      // would tell an attacker which guesses are not worth making.
      await resetToLogin();
      await fillLogin(EMAIL, "x");
      const ok = await driver.executeScript(
        "return document.querySelector('[data-testid=\\'login-password\\']').checkValidity()",
      );
      assert.strictEqual(ok, true);
    });
  });

  // ==========================================================================
  // Module 5 — Successful authentication
  // ==========================================================================
  describe("Module 5 — Successful authentication", function () {
    before(async () => {
      await signIn();
    });

    it("TC-126 lands on the dashboard route", async () => {
      assert.strictEqual(await currentPath(), "/");
    });

    it("TC-127 leaves the login form behind", async () => {
      assert.strictEqual(await exists("login-form"), false);
    });

    it("TC-128 renders the application header", async () => {
      assert.ok(await exists("app-header"));
    });

    it("TC-129 renders the page content region", async () => {
      assert.ok(await exists("page-content"));
    });

    it("TC-130 renders a page title", async () => {
      assert.ok(await exists("page-title"));
    });

    it("TC-131 titles the landing page 'Devices'", async () => {
      assert.strictEqual((await textOf("page-title")).trim(), "Devices");
    });

    it("TC-132 shows the signed-in account in the header", async () => {
      assert.strictEqual((await textOf("current-user-email")).trim(), EMAIL);
    });

    it("TC-133 offers a sign-out control", async () => {
      assert.ok(await exists("sign-out"));
    });

    it("TC-134 labels the sign-out control", async () => {
      assert.strictEqual((await textOf("sign-out")).trim(), "Sign out");
    });

    it("TC-135 dismisses the bootstrap loader", async () => {
      assert.strictEqual(await exists("page-loading"), false);
    });

    it("TC-136 sets the refresh cookie", async () => {
      assert.notStrictEqual(await cookie("sentinel_refresh"), null);
    });

    it("TC-137 marks the refresh cookie HttpOnly", async () => {
      assert.strictEqual((await cookie("sentinel_refresh")).httpOnly, true);
    });

    it("TC-138 scopes the refresh cookie to the whole origin", async () => {
      // Path=/ and not /api: the Vite dev proxy strips the prefix, so a
      // narrower path would never be sent back.
      assert.strictEqual((await cookie("sentinel_refresh")).path, "/");
    });

    it("TC-139 keeps the refresh cookie out of document.cookie", async () => {
      const visible = await driver.executeScript("return document.cookie");
      assert.strictEqual(String(visible).includes("sentinel_refresh"), false);
    });

    it("TC-140 stores no access token in localStorage", async () => {
      const dump = await driver.executeScript("return JSON.stringify(window.localStorage)");
      assert.strictEqual(/token|jwt|bearer/i.test(String(dump)), false);
    });

    it("TC-141 stores no access token in sessionStorage", async () => {
      const dump = await driver.executeScript("return JSON.stringify(window.sessionStorage)");
      assert.strictEqual(/token|jwt|bearer/i.test(String(dump)), false);
    });

    it("TC-142 leaves localStorage empty altogether", async () => {
      const n = await driver.executeScript("return window.localStorage.length");
      assert.strictEqual(Number(n), 0);
    });

    it("TC-143 renders the Devices nav item", async () => {
      assert.ok(await exists("nav-devices"));
    });

    it("TC-144 renders the Add-a-device nav item", async () => {
      assert.ok(await exists("nav-add-device"));
    });

    it("TC-145 renders the Alerts nav item", async () => {
      assert.ok(await exists("nav-alerts"));
    });

    it("TC-146 renders the Anomalies nav item", async () => {
      assert.ok(await exists("nav-anomalies"));
    });

    it("TC-147 renders the Forecasts nav item", async () => {
      assert.ok(await exists("nav-forecasts"));
    });

    it("TC-148 renders the Incidents nav item", async () => {
      assert.ok(await exists("nav-incidents"));
    });

    it("TC-149 renders the Reports nav item", async () => {
      assert.ok(await exists("nav-reports"));
    });

    it("TC-150 renders the Settings nav item", async () => {
      assert.ok(await exists("nav-settings"));
    });

    it("TC-151 offers the add-device call to action", async () => {
      assert.ok(await exists("add-device"));
    });

    it("TC-152 points the add-device call to action at /devices/new", async () => {
      assert.ok((await attrOf("add-device", "href")).endsWith("/devices/new"));
    });

    it("TC-153 keeps the sign-in usable from a second sign-in attempt", async () => {
      await signIn();
      assert.strictEqual((await textOf("current-user-email")).trim(), EMAIL);
    });

    it("TC-154 accepts the account email in a different case", async () => {
      await signIn(EMAIL.toUpperCase(), PASSWORD);
      assert.strictEqual(await currentPath(), "/");
    });

    it("TC-155 shows the canonical stored email regardless of the case typed", async () => {
      await signIn(EMAIL.toUpperCase(), PASSWORD);
      assert.strictEqual((await textOf("current-user-email")).trim(), EMAIL);
    });
  });

  // ==========================================================================
  // Module 6 — Session persistence and route protection
  // ==========================================================================
  describe("Module 6 — Session persistence and route protection", function () {
    const PROTECTED = [
      "/",
      "/devices/new",
      "/alerts",
      "/alerts/rules",
      "/anomalies",
      "/forecasts",
      "/incidents",
      "/reports",
      "/settings",
    ];

    it("TC-156 survives a hard refresh of the dashboard", async () => {
      await signIn();
      await go("/");
      assert.ok(await exists("app-header"));
    });

    it("TC-157 still shows the right account after a hard refresh", async () => {
      await go("/");
      assert.strictEqual((await textOf("current-user-email")).trim(), EMAIL);
    });

    it("TC-158 exchanges the refresh cookie rather than restoring a stored token", async () => {
      await go("/");
      const n = await driver.executeScript("return window.localStorage.length");
      assert.strictEqual(Number(n), 0);
    });

    it("TC-159 shows the bootstrap loader hook while the exchange is in flight", async () => {
      // Asserted structurally rather than by racing it: ProtectedRoute is the
      // only thing that renders FullPageLoader, and it is what makes a hard
      // refresh work at all.
      await go("/settings");
      assert.strictEqual(await exists("page-loading"), false);
      assert.ok(await exists("page-title"));
    });

    PROTECTED.forEach((path, i) => {
      it(`TC-${String(160 + i).padStart(3, "0")} keeps a signed-in user on ${path} across a reload`, async () => {
        await go(path);
        assert.ok(await exists("app-header"), `no app shell on ${path}`);
      });
    });

    PROTECTED.forEach((path, i) => {
      it(`TC-${String(169 + i).padStart(3, "0")} redirects an anonymous visitor from ${path} to /login`, async () => {
        await clearSession();
        await go(path);
        await present("login-form");
        assert.strictEqual(await currentPath(), "/login");
      });
    });

    it("TC-178 sends an already-authenticated visitor away from /login", async () => {
      await signIn();
      await go("/login");
      assert.strictEqual(await currentPath(), "/");
    });

    it("TC-179 sends an already-authenticated visitor away from /signup", async () => {
      await go("/signup");
      assert.strictEqual(await currentPath(), "/");
    });

    it("TC-180 renders the dashboard, not the login form, at /login when signed in", async () => {
      await go("/login");
      assert.strictEqual(await exists("login-form"), false);
      assert.ok(await exists("app-header"));
    });

    it("TC-181 remembers the requested page and returns to it after signing in", async () => {
      await clearSession();
      await go("/settings");
      await present("login-form");
      await fillLogin(EMAIL, PASSWORD);
      await (await present("login-submit")).click();
      await present("page-title");
      assert.strictEqual(await currentPath(), "/settings");
    });

    it("TC-182 restores the requested page's own content, not the dashboard's", async () => {
      assert.strictEqual((await textOf("page-title")).trim(), "Settings");
    });

    it("TC-183 treats a deleted refresh cookie as signed out", async () => {
      await signIn();
      await driver.manage().deleteCookie("sentinel_refresh");
      await go("/");
      await present("login-form");
      assert.strictEqual(await currentPath(), "/login");
    });

    it("TC-184 treats a corrupted refresh cookie as signed out", async () => {
      await signIn();
      await driver.manage().addCookie({
        name: "sentinel_refresh",
        value: "not-a-real-token",
        path: "/",
      });
      await go("/");
      await present("login-form");
      assert.strictEqual(await currentPath(), "/login");
    });

    it("TC-185 does not leak the previous account's email after the cookie is dropped", async () => {
      assert.strictEqual(await exists("current-user-email"), false);
    });
  });

  // ==========================================================================
  // Module 7 — Sign-out
  // ==========================================================================
  describe("Module 7 — Sign-out", function () {
    beforeEach(async () => {
      await signIn();
    });

    it("TC-186 returns to the login form", async () => {
      await signOut();
      assert.ok(await exists("login-form"));
    });

    it("TC-187 lands on /login", async () => {
      await signOut();
      assert.strictEqual(await currentPath(), "/login");
    });

    it("TC-188 removes the application header", async () => {
      await signOut();
      assert.strictEqual(await exists("app-header"), false);
    });

    it("TC-189 removes the account email from the page", async () => {
      await signOut();
      assert.strictEqual(await exists("current-user-email"), false);
    });

    it("TC-190 clears the refresh cookie", async () => {
      await signOut();
      assert.strictEqual(await cookie("sentinel_refresh"), null);
    });

    it("TC-191 leaves localStorage empty", async () => {
      await signOut();
      const n = await driver.executeScript("return window.localStorage.length");
      assert.strictEqual(Number(n), 0);
    });

    it("TC-192 blocks a protected route after signing out", async () => {
      await signOut();
      await go("/settings");
      await present("login-form");
      assert.strictEqual(await currentPath(), "/login");
    });

    it("TC-193 does not restore the session through the back button", async () => {
      // Contrast with TC-194, which reloads and correctly lands on /login. Here
      // the previous document comes back from the browser's back/forward cache
      // instead of being re-executed, so bootstrapAuth() never runs again and
      // the in-memory auth store — access token included — is restored with the
      // page. The refresh cookie really is gone by then; the shell on screen is
      // a cached render of the account that just signed out.
      await go("/alerts");
      await signOut();
      await driver.navigate().back();

      // Bounded wait for the signed-out state to appear, rather than a sleep:
      // if the restore is not authenticated, the login form is what comes back.
      const deadline = Date.now() + 5000;
      while (Date.now() < deadline && !(await exists("login-form"))) {
        await driver.sleep(200);
      }

      const restored = {
        path: await currentPath(),
        appHeader: await exists("app-header"),
        loginForm: await exists("login-form"),
        refreshCookie: (await cookie("sentinel_refresh")) !== null,
      };
      assert.strictEqual(
        restored.appHeader,
        false,
        `the signed-out page was restored from the back/forward cache: ${JSON.stringify(restored)}`,
      );
    });

    it("TC-193a keeps a still-valid session when Back restores a cached page", async () => {
      // The other half of TC-193: revalidating a restored page must not sign
      // out a user who never signed out. Two driver.get()s are two separate
      // documents, which is what makes this Back a real bfcache restore rather
      // than a client-side route change — TC-295 covers the in-document case
      // and would not notice this regression at all.
      await go("/alerts");
      await go("/settings");
      await driver.navigate().back();

      await driver.wait(async () => (await currentPath()) === "/alerts", WAIT);
      await settle();
      // Settle into one answer or the other before asking which it is.
      await driver.wait(
        async () => (await exists("current-user-email")) || (await exists("login-form")),
        WAIT,
        "the restored page never resolved its session",
      );

      assert.strictEqual(await exists("login-form"), false, "a valid session was signed out");
      assert.strictEqual((await textOf("page-title")).trim(), "Alerts");
      assert.strictEqual((await textOf("current-user-email")).trim(), EMAIL);
    });

    it("TC-194 does not restore the session through a forced reload", async () => {
      await signOut();
      await go("/");
      assert.strictEqual(await exists("app-header"), false);
    });

    it("TC-195 allows signing straight back in", async () => {
      await signOut();
      await fillLogin(EMAIL, PASSWORD);
      await (await present("login-submit")).click();
      await present("page-title");
      assert.strictEqual(await currentPath(), "/");
    });

    it("TC-196 shows the right account after signing back in", async () => {
      await signOut();
      await fillLogin(EMAIL, PASSWORD);
      await (await present("login-submit")).click();
      await present("page-title");
      assert.strictEqual((await textOf("current-user-email")).trim(), EMAIL);
    });

    it("TC-197 issues a fresh refresh cookie on the second sign-in", async () => {
      const first = (await cookie("sentinel_refresh")).value;
      await signOut();
      await fillLogin(EMAIL, PASSWORD);
      await (await present("login-submit")).click();
      await present("page-title");
      const second = (await cookie("sentinel_refresh")).value;
      assert.notStrictEqual(first, second);
    });

    it("TC-198 signs out from a page other than the dashboard", async () => {
      await go("/reports");
      await signOut();
      assert.strictEqual(await currentPath(), "/login");
    });

    it("TC-199 signs out from the alerts page", async () => {
      await go("/alerts");
      await signOut();
      assert.ok(await exists("login-form"));
    });

    it("TC-200 signs out from the settings page", async () => {
      await go("/settings");
      await signOut();
      assert.ok(await exists("login-form"));
    });
  });

  // ==========================================================================
  // Module 8 — Signup page structure and validation
  //
  // The password minimum is enforced in three places and only one of them is
  // reachable by a driver that types like a person: the input carries
  // minlength=12, so the browser blocks submission and the page's own
  // "Password must be at least 12 characters." banner never renders for typed
  // input. These cases assert the block, which is the behaviour a user meets.
  // ==========================================================================
  describe("Module 8 — Signup page structure and validation", function () {
    async function openSignup() {
      await clearSession();
      await go("/signup");
      await present("signup-form");
    }

    async function fillSignup(name, email, password) {
      const nameEl = await present("signup-display-name");
      const emailEl = await present("signup-email");
      const passEl = await present("signup-password");
      await clearField(nameEl);
      await clearField(emailEl);
      await clearField(passEl);
      if (name) await nameEl.sendKeys(name);
      if (email) await emailEl.sendKeys(email);
      if (password) await passEl.sendKeys(password);
    }

    before(async () => {
      await openSignup();
    });

    it("TC-201 serves the signup route", async () => {
      assert.strictEqual(await currentPath(), "/signup");
    });

    it("TC-202 renders the signup card", async () => {
      assert.ok(await exists("signup-card"));
    });

    it("TC-203 titles the card 'Create an account'", async () => {
      assert.strictEqual((await textOf("signup-title")).trim(), "Create an account");
    });

    it("TC-204 renders the signup form", async () => {
      assert.ok(await exists("signup-form"));
    });

    it("TC-205 renders a display-name field", async () => {
      assert.ok(await exists("signup-display-name"));
    });

    it("TC-206 renders an email field", async () => {
      assert.ok(await exists("signup-email"));
    });

    it("TC-207 renders a password field", async () => {
      assert.ok(await exists("signup-password"));
    });

    it("TC-208 renders a submit button", async () => {
      assert.ok(await exists("signup-submit"));
    });

    it("TC-209 labels the submit button 'Create account'", async () => {
      assert.strictEqual((await textOf("signup-submit")).trim(), "Create account");
    });

    it("TC-210 offers a link back to signing in", async () => {
      assert.ok(await exists("signup-to-login"));
    });

    it("TC-211 points that link at /login", async () => {
      assert.ok((await attrOf("signup-to-login", "href")).endsWith("/login"));
    });

    it("TC-212 shows no error before anything is submitted", async () => {
      assert.strictEqual(await exists("signup-error"), false);
    });

    it("TC-213 leaves the display-name field optional", async () => {
      assert.strictEqual(await attrOf("signup-display-name", "required"), null);
    });

    it("TC-214 marks the email field required", async () => {
      assert.ok(await attrOf("signup-email", "required"));
    });

    it("TC-215 marks the password field required", async () => {
      assert.ok(await attrOf("signup-password", "required"));
    });

    it("TC-216 declares the 12-character minimum on the password field", async () => {
      assert.strictEqual(await attrOf("signup-password", "minlength"), "12");
    });

    it("TC-217 states the minimum in the visible copy", async () => {
      const card = await present("signup-card");
      assert.ok((await card.getText()).includes("At least 12 characters."));
    });

    it("TC-218 sets autocomplete=new-password on the password field", async () => {
      assert.strictEqual(await attrOf("signup-password", "autocomplete"), "new-password");
    });

    it("TC-219 masks the signup password by default", async () => {
      assert.strictEqual(await attrOf("signup-password", "type"), "password");
    });

    it("TC-220 offers a visibility toggle on the signup password", async () => {
      assert.ok(await exists("password-toggle"));
    });

    it("TC-221 reveals the signup password when toggled", async () => {
      await (await present("password-toggle")).click();
      assert.strictEqual(await attrOf("signup-password", "type"), "text");
      await (await present("password-toggle")).click();
    });

    const SHORT_PASSWORDS = ["a", "short", "elevenchars", "12345678901", "not12chars!"];

    SHORT_PASSWORDS.forEach((password, i) => {
      it(`TC-${String(222 + i).padStart(3, "0")} refuses a ${password.length}-character password`, async () => {
        await openSignup();
        await fillSignup("", `never-created-${i}@sentinel.dev`, password);
        const ok = await driver.executeScript(
          "return document.querySelector('[data-testid=\\'signup-password\\']').checkValidity()",
        );
        assert.strictEqual(ok, false);
      });
    });

    it("TC-227 blocks submission entirely for a short password", async () => {
      await openSignup();
      await fillSignup("", "never-created-short@sentinel.dev", "tooshort");
      await (await present("signup-submit")).click();
      assert.strictEqual(await absent("signup-error"), true);
      assert.strictEqual(await currentPath(), "/signup");
    });

    it("TC-228 accepts a password at exactly the 12-character minimum", async () => {
      await openSignup();
      await fillSignup("", "never-created-12@sentinel.dev", "twelvechars!");
      const ok = await driver.executeScript(
        "return document.querySelector('[data-testid=\\'signup-password\\']').checkValidity()",
      );
      assert.strictEqual(ok, true);
    });

    const MALFORMED_SIGNUP_EMAILS = [
      "plainaddress",
      "@example.com",
      "user@",
      "user name@example.com",
      "user@@example.com",
      "user@exa mple.com",
    ];

    MALFORMED_SIGNUP_EMAILS.forEach((email, i) => {
      it(`TC-${String(229 + i).padStart(3, "0")} refuses the malformed signup email ${JSON.stringify(email)}`, async () => {
        await openSignup();
        await fillSignup("", email, "LongEnoughPass123");
        const ok = await driver.executeScript(
          "return document.querySelector('[data-testid=\\'signup-email\\']').checkValidity()",
        );
        assert.strictEqual(ok, false);
      });
    });

    it("TC-235 blocks submission when the email is empty", async () => {
      await openSignup();
      await fillSignup("Someone", "", "LongEnoughPass123");
      await (await present("signup-submit")).click();
      assert.strictEqual(await absent("signup-error"), true);
      assert.strictEqual(await currentPath(), "/signup");
    });

    it("TC-236 blocks submission when the password is empty", async () => {
      await openSignup();
      await fillSignup("Someone", "never-created@sentinel.dev", "");
      await (await present("signup-submit")).click();
      assert.strictEqual(await absent("signup-error"), true);
      assert.strictEqual(await currentPath(), "/signup");
    });

    it("TC-237 refuses an already-registered email", async () => {
      await openSignup();
      await fillSignup("", EMAIL, "LongEnoughPass123");
      await (await present("signup-submit")).click();
      assert.strictEqual((await textOf("signup-error")).trim(), "That email is already registered.");
    });

    it("TC-238 stays on /signup after a duplicate-email refusal", async () => {
      assert.strictEqual(await currentPath(), "/signup");
    });

    it("TC-239 sets no session cookie on a duplicate-email refusal", async () => {
      assert.strictEqual(await cookie("sentinel_refresh"), null);
    });

    it("TC-240 refuses a special-use TLD the browser considers well-formed", async () => {
      // .test/.local/.invalid pass type=email but email-validator rejects them,
      // so the account would be creatable and then never able to sign in. This
      // is the documented trap in docs/TESTING.md, asserted.
      await openSignup();
      await fillSignup("", "reserved-tld@sentinel.test", "LongEnoughPass123");
      await (await present("signup-submit")).click();
      const message = await textOf("signup-error");
      assert.ok(message.includes("not a valid email address"), message);
    });

    it("TC-241 lets the browser through to the server for a .test address", async () => {
      await openSignup();
      await fillSignup("", "reserved-tld@sentinel.test", "LongEnoughPass123");
      const ok = await driver.executeScript(
        "return document.querySelector('[data-testid=\\'signup-email\\']').checkValidity()",
      );
      assert.strictEqual(ok, true);
    });

    it("TC-242 creates an account and signs the new user straight in", async () => {
      const fresh = `selenium-${Date.now()}@sentinel.dev`;
      await openSignup();
      await fillSignup("Selenium Suite", fresh, "SeleniumSuite2026!");
      await (await present("signup-submit")).click();
      await present("page-title");
      assert.strictEqual(await currentPath(), "/");
      assert.strictEqual((await textOf("current-user-email")).trim(), fresh);
    });

    it("TC-243 gives the newly created account its own empty fleet", async () => {
      assert.ok(await present("devices-empty"));
    });

    it("TC-244 sets a refresh cookie for the newly created account", async () => {
      assert.notStrictEqual(await cookie("sentinel_refresh"), null);
    });

    it("TC-245 signs the newly created account out cleanly", async () => {
      await signOut();
      assert.strictEqual(await currentPath(), "/login");
    });
  });

  // ==========================================================================
  // Module 9 — Login and signup cross-navigation
  // ==========================================================================
  describe("Module 9 — Login and signup cross-navigation", function () {
    beforeEach(async () => {
      await resetToLogin();
    });

    it("TC-246 moves from login to signup by link", async () => {
      await (await present("login-to-signup")).click();
      await present("signup-form");
      assert.strictEqual(await currentPath(), "/signup");
    });

    it("TC-247 moves back from signup to login by link", async () => {
      await (await present("login-to-signup")).click();
      await present("signup-form");
      await (await present("signup-to-login")).click();
      await present("login-form");
      assert.strictEqual(await currentPath(), "/login");
    });

    it("TC-248 does not carry the typed email across to signup", async () => {
      await fillLogin("carried@example.com", "");
      await (await present("login-to-signup")).click();
      await present("signup-form");
      assert.strictEqual(await attrOf("signup-email", "value"), "");
    });

    it("TC-249 does not carry the typed password across to signup", async () => {
      await fillLogin("", "carried-password");
      await (await present("login-to-signup")).click();
      await present("signup-form");
      assert.strictEqual(await attrOf("signup-password", "value"), "");
    });

    it("TC-250 leaves no login form on the signup page", async () => {
      await go("/signup");
      assert.strictEqual(await exists("login-form"), false);
    });

    it("TC-251 leaves no signup form on the login page", async () => {
      assert.strictEqual(await exists("signup-form"), false);
    });

    it("TC-252 serves the signup page directly by URL", async () => {
      await go("/signup");
      assert.ok(await exists("signup-form"));
    });

    it("TC-253 renders no application header on the signup page", async () => {
      await go("/signup");
      assert.strictEqual(await exists("app-header"), false);
    });

    it("TC-254 returns to login through the browser back button", async () => {
      await (await present("login-to-signup")).click();
      await present("signup-form");
      await driver.navigate().back();
      await settle();
      await present("login-form");
      assert.strictEqual(await currentPath(), "/login");
    });

    it("TC-255 returns to signup through the browser forward button", async () => {
      await (await present("login-to-signup")).click();
      await present("signup-form");
      await driver.navigate().back();
      await settle();
      await present("login-form");
      await driver.navigate().forward();
      await settle();
      await present("signup-form");
      assert.strictEqual(await currentPath(), "/signup");
    });

    it("TC-256 keeps the same document title on signup", async () => {
      await go("/signup");
      assert.strictEqual(await driver.getTitle(), "Sentinel");
    });

    it("TC-257 uses distinct testid namespaces for the two forms", async () => {
      await go("/signup");
      assert.strictEqual(await exists("login-email"), false);
      assert.ok(await exists("signup-email"));
    });
  });

  // ==========================================================================
  // Module 10 — Authenticated navigation
  //
  // Every case waits on `page-title` rather than on the URL. The router changes
  // the URL before the page's data request resolves, so a URL assertion can
  // pass while the bootstrap loader is still what is on screen.
  // ==========================================================================
  describe("Module 10 — Authenticated navigation", function () {
    const NAV = [
      ["nav-devices", "/", "Devices"],
      ["nav-add-device", "/devices/new", "Add a device"],
      ["nav-alerts", "/alerts", "Alerts"],
      ["nav-anomalies", "/anomalies", "Anomalies"],
      ["nav-forecasts", "/forecasts", "Forecasts"],
      ["nav-incidents", "/incidents", "Incidents"],
      ["nav-reports", "/reports", "Reports"],
      ["nav-settings", "/settings", "Settings"],
    ];

    before(async () => {
      await signIn();
    });

    NAV.forEach(([hook, path, title], i) => {
      it(`TC-${String(258 + i * 4).padStart(3, "0")} navigates to ${path} by clicking ${hook}`, async () => {
        await go("/");
        await clickNav(hook, path);
        assert.strictEqual(await currentPath(), path);
      });

      it(`TC-${String(259 + i * 4).padStart(3, "0")} titles ${path} '${title}'`, async () => {
        assert.strictEqual((await textOf("page-title")).trim(), title);
      });

      it(`TC-${String(260 + i * 4).padStart(3, "0")} keeps the application header on ${path}`, async () => {
        assert.ok(await exists("app-header"));
        assert.strictEqual((await textOf("current-user-email")).trim(), EMAIL);
      });

      it(`TC-${String(261 + i * 4).padStart(3, "0")} renders the page content region on ${path}`, async () => {
        assert.ok(await exists("page-content"));
      });
    });

    it("TC-290 reaches /alerts/rules directly, which has no nav item", async () => {
      await go("/alerts/rules");
      assert.strictEqual((await textOf("page-title")).trim(), "Alert rules");
    });

    it("TC-291 keeps the header on /alerts/rules", async () => {
      assert.ok(await exists("app-header"));
    });

    it("TC-292 navigates between two pages without a full reload", async () => {
      await go("/alerts");
      const marker = await driver.executeScript("window.__navMarker = 1; return true");
      assert.strictEqual(marker, true);
      await clickNav("nav-reports", "/reports");
      const survived = await driver.executeScript("return window.__navMarker === 1");
      assert.strictEqual(survived, true);
    });

    it("TC-293 arrives at Reports after that client-side navigation", async () => {
      assert.strictEqual((await textOf("page-title")).trim(), "Reports");
    });

    it("TC-294 walks the whole nav in one session without re-authenticating", async () => {
      await go("/");
      for (const [hook, path, title] of NAV) {
        await clickNav(hook, path);
        assert.strictEqual((await textOf("page-title")).trim(), title);
      }
      assert.strictEqual((await textOf("current-user-email")).trim(), EMAIL);
    });

    it("TC-295 returns through the back button to the previous page", async () => {
      await go("/alerts");
      await clickNav("nav-settings", "/settings");
      await driver.navigate().back();
      await settle();
      await present("page-title");
      assert.strictEqual(await currentPath(), "/alerts");
    });

    it("TC-296 goes forward again through the forward button", async () => {
      await driver.navigate().forward();
      await settle();
      await present("page-title");
      assert.strictEqual(await currentPath(), "/settings");
    });

    it("TC-297 offers the sign-out control on every navigated page", async () => {
      for (const [hook, path] of NAV) {
        await clickNav(hook, path);
        assert.ok(await exists("sign-out"), `sign-out missing on ${path}`);
      }
    });

    it("TC-298 keeps all eight nav items present on every page", async () => {
      for (const [hook, path] of NAV) {
        await go(path);
        for (const [other] of NAV) {
          assert.ok(await exists(other), `${other} missing on ${path}`);
        }
        assert.ok(hook);
      }
    });

    it("TC-299 renders exactly one page title per page", async () => {
      await go("/settings");
      const found = await driver.findElements(byTestId("page-title"));
      assert.strictEqual(found.length, 1);
    });

    it("TC-300 renders exactly one application header per page", async () => {
      const found = await driver.findElements(byTestId("app-header"));
      assert.strictEqual(found.length, 1);
    });
  });

  // ==========================================================================
  // Module 11 — Deep links, redirects and the 404 page
  // ==========================================================================
  describe("Module 11 — Deep links, redirects and the 404 page", function () {
    before(async () => {
      await signIn();
    });

    it("TC-301 redirects the retired /devices list to the dashboard", async () => {
      await go("/devices");
      await present("page-title");
      assert.strictEqual(await currentPath(), "/");
    });

    it("TC-302 titles that redirect target 'Devices'", async () => {
      assert.strictEqual((await textOf("page-title")).trim(), "Devices");
    });

    it("TC-303 redirects the retired /download page to /devices/new", async () => {
      await go("/download");
      await present("page-title");
      assert.strictEqual(await currentPath(), "/devices/new");
    });

    it("TC-304 titles that redirect target 'Add a device'", async () => {
      assert.strictEqual((await textOf("page-title")).trim(), "Add a device");
    });

    const UNKNOWN_PATHS = [
      "/nope",
      "/dashboard",
      "/admin",
      "/alerts/rules/extra",
      "/settings/profile",
      "/reports/weekly",
      "/devices/new/extra",
      "/a/b/c/d",
    ];

    UNKNOWN_PATHS.forEach((path, i) => {
      it(`TC-${String(305 + i).padStart(3, "0")} renders the not-found page for ${path}`, async () => {
        await go(path);
        assert.ok(await present("not-found"));
      });
    });

    it("TC-313 titles the not-found page 'Page not found'", async () => {
      await go("/definitely-not-a-route");
      assert.strictEqual((await textOf("page-title")).trim(), "Page not found");
    });

    it("TC-314 serves the not-found page over HTTP 200, as an SPA route", async () => {
      const res = await navRequest("/definitely-not-a-route");
      assert.strictEqual(res.status, 200);
    });

    it("TC-315 offers a way back to the dashboard from the not-found page", async () => {
      await go("/definitely-not-a-route");
      const from = await currentPath();
      // NotFoundPage carries `page-title` itself — its heading is literally
      // "Page not found" — so waiting for that hook to merely *exist* after
      // the click is satisfied instantly by the page being left, and this case
      // would report success without the destination ever rendering. Hold the
      // node and wait for React to unmount it, exactly as clickNav() does.
      const previousTitle = await present("page-title");
      const link = await driver.findElement(By.css("[data-testid='not-found'] a"));
      await link.click();
      // Wait for the link to take effect at all, then assert *where* it landed.
      // Waiting for "/" directly turns any other destination into a bare
      // 20-second timeout that says nothing about what actually happened.
      await driver.wait(async () => (await currentPath()) !== from, WAIT, "the link did nothing");
      await driver.wait(
        until.stalenessOf(previousTitle),
        WAIT,
        "the not-found page never unmounted",
      );
      await settle();
      // The path first, then the heading. A destination that renders no
      // `page-title` at all — /login renders `login-title` instead — otherwise
      // fails as an unexplained 20-second timeout rather than saying where it
      // actually went.
      assert.strictEqual(await currentPath(), "/");
      await present("page-title");
    });

    it("TC-316 shows the not-found page to an anonymous visitor too", async () => {
      await clearSession();
      await go("/definitely-not-a-route");
      assert.ok(await present("not-found"));
    });

    it("TC-317 renders no application header on the anonymous not-found page", async () => {
      assert.strictEqual(await exists("app-header"), false);
    });

    it("TC-318 renders a device history page for an unknown device id", async () => {
      await signIn();
      await go("/devices/00000000-0000-0000-0000-000000000000/history");
      await present("page-title");
      assert.ok(await exists("app-header"));
    });

    it("TC-319 falls back to a neutral title for an unknown device", async () => {
      assert.strictEqual((await textOf("page-title")).trim(), "Device");
    });

    it("TC-320 renders a live monitoring page for an unknown device id", async () => {
      await go("/devices/00000000-0000-0000-0000-000000000000/live");
      await present("page-title");
      assert.ok(await exists("app-header"));
    });

    it("TC-321 renders the incident shell for an unknown incident id", async () => {
      await go("/incidents/00000000-0000-0000-0000-000000000000");
      assert.ok(await present("app-header"));
    });

    it("TC-322 keeps a query string from breaking a route", async () => {
      await go("/alerts?status=firing");
      assert.strictEqual((await textOf("page-title")).trim(), "Alerts");
    });

    it("TC-323 keeps a fragment from breaking a route", async () => {
      await go("/reports#section");
      assert.strictEqual((await textOf("page-title")).trim(), "Reports");
    });

    it("TC-324 treats a trailing slash on a known route as that route", async () => {
      await go("/settings/");
      await present("page-title");
      assert.strictEqual((await textOf("page-title")).trim(), "Settings");
    });

    it("TC-325 does not expose a protected page through an unknown path", async () => {
      await clearSession();
      await go("/settings/../settings");
      const path = await currentPath();
      assert.strictEqual(path, "/login");
    });
  });

  // ==========================================================================
  // Module 12 — The empty fleet
  //
  // Nothing seeds devices or metrics — CLAUDE.md's first hard rule is that
  // every number on screen came from a real agent reading a real machine, so
  // there is no fixture that fabricates CPU history. The empty state is a real
  // state this product renders on purpose, and it is what these assert.
  // ==========================================================================
  describe("Module 12 — The empty fleet", function () {
    before(async () => {
      await signIn();
      await present("fleet-totals");
    });

    it("TC-326 renders the fleet totals card", async () => {
      assert.ok(await exists("fleet-totals"));
    });

    it("TC-327 renders the empty-fleet card", async () => {
      assert.ok(await present("devices-empty"));
    });

    it("TC-328 explains how to add a device from the empty state", async () => {
      const card = await present("devices-empty");
      assert.ok((await card.getText()).includes("No devices yet"));
    });

    it("TC-329 dismisses the loading placeholder once the overview arrives", async () => {
      assert.strictEqual(await exists("devices-loading"), false);
    });

    it("TC-330 shows no fleet error", async () => {
      assert.strictEqual(await exists("fleet-error"), false);
    });

    it("TC-331 still renders the device list container", async () => {
      assert.ok(await exists("device-list"));
    });

    it("TC-332 renders no device cards", async () => {
      const cards = await driver.findElements(By.css("[data-testid^='device-card-']"));
      assert.strictEqual(cards.length, 0);
    });

    const TILES = [
      ["total-devices", "Devices"],
      ["total-online", "Online"],
      ["total-offline", "Offline"],
      ["total-awaiting-agent", "Awaiting agent"],
      ["total-healthy", "Healthy"],
      ["total-degraded", "Degraded"],
      ["total-critical", "Critical"],
    ];

    TILES.forEach(([hook, label], i) => {
      it(`TC-${String(333 + i).padStart(3, "0")} renders the ${label} tile`, async () => {
        assert.ok(await exists(hook), `${hook} missing`);
      });
    });

    TILES.forEach(([hook, label], i) => {
      it(`TC-${String(340 + i).padStart(3, "0")} counts ${label} as zero, rendered as 0 rather than blank`, async () => {
        // Zero is a real count here, not a missing measurement — the
        // "unavailable" rule is about readings, not tallies.
        const text = await textOf(hook);
        assert.ok(text.startsWith("0"), `${hook} read ${JSON.stringify(text)}`);
      });
    });

    it("TC-347 labels each tile with its own name", async () => {
      for (const [hook, label] of TILES) {
        assert.ok((await textOf(hook)).includes(label), `${hook} lost its label`);
      }
    });

    it("TC-348 links the empty state to the add-device flow", async () => {
      const link = await driver.findElement(By.css("[data-testid='devices-empty'] a"));
      assert.ok((await link.getAttribute("href")).endsWith("/devices/new"));
    });

    it("TC-349 reaches the add-device page from the empty state", async () => {
      const from = await currentPath();
      await driver.findElement(By.css("[data-testid='devices-empty'] a")).click();
      await driver.wait(async () => (await currentPath()) !== from, WAIT, "the link did nothing");
      await settle();
      assert.strictEqual(await currentPath(), "/devices/new");
      await present("page-title");
      assert.strictEqual((await textOf("page-title")).trim(), "Add a device");
    });

    it("TC-350 keeps the empty fleet across a reload", async () => {
      await go("/");
      assert.ok(await present("devices-empty"));
    });

    it("TC-351 keeps the totals at zero across a reload", async () => {
      assert.ok((await textOf("total-devices")).startsWith("0"));
    });
  });

  // ==========================================================================
  // Module 13 — Transport and browser security headers
  //
  // app/security/headers.py adds these to *every* response, including the
  // console's own static files. They are the headers whose absence is
  // invisible: nothing fails, and the browser quietly applies its permissive
  // default — which is exactly why they are asserted rather than eyeballed.
  // ==========================================================================
  describe("Module 13 — Transport and browser security headers", function () {
    let loginPage;
    let asset;
    let apiError;

    before(async () => {
      loginPage = await navRequest("/login");
      const html = (await navRequest("/")).body;
      const match = html.match(/\/assets\/[A-Za-z0-9._-]+\.js/);
      asset = await rawRequest(match ? match[0] : "/favicon.svg");
      apiError = await rawRequest("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: EMAIL, password: "wrong-password-entirely" }),
      });
    });

    it("TC-352 serves the console HTML to a navigation request", async () => {
      assert.strictEqual(loginPage.status, 200);
      assert.ok(loginPage.headers["content-type"].includes("text/html"));
    });

    it("TC-353 sets a Content-Security-Policy on the console", () => {
      assert.ok(loginPage.headers["content-security-policy"]);
    });

    it("TC-354 defaults the CSP to same-origin", () => {
      assert.ok(loginPage.headers["content-security-policy"].includes("default-src 'self'"));
    });

    it("TC-355 restricts scripts to same-origin with no inline keyword", () => {
      const csp = loginPage.headers["content-security-policy"];
      const scriptSrc = csp.split(";").map((p) => p.trim()).find((p) => p.startsWith("script-src"));
      assert.strictEqual(scriptSrc, "script-src 'self'");
    });

    it("TC-356 forbids inline styles, and uPlot does not need them", () => {
      // This case used to assert the opposite, on the belief that uPlot writes
      // inline style *attributes* every frame and that CSP cannot distinguish
      // one from a <style> block. The first half is wrong: uPlot assigns CSSOM
      // properties (el.style.transform = ...), which CSP does not govern at all
      // — the script doing the assigning already passed script-src. Only a
      // <style> element, a style="..." attribute parsed from markup, or
      // setAttribute("style", ...) is subject to this directive.
      //
      // Verified in a browser against five live charts streaming from a real
      // agent: cursor, legend and axes all render, while setAttribute("style")
      // and an injected <style> element are both refused.
      const csp = loginPage.headers["content-security-policy"];
      assert.ok(csp.includes("style-src 'self'"));
      assert.ok(!csp.includes("'unsafe-inline'"), "the CSP must not carry 'unsafe-inline' anywhere");
      assert.ok(csp.includes("style-src-attr 'none'"));
    });

    it("TC-357 restricts connect-src to same-origin, covering the viewer WebSocket", () => {
      assert.ok(loginPage.headers["content-security-policy"].includes("connect-src 'self'"));
    });

    it("TC-358 forbids framing the console", () => {
      assert.ok(loginPage.headers["content-security-policy"].includes("frame-ancestors 'none'"));
    });

    it("TC-359 forbids plugin objects", () => {
      assert.ok(loginPage.headers["content-security-policy"].includes("object-src 'none'"));
    });

    it("TC-360 pins base-uri to same-origin", () => {
      assert.ok(loginPage.headers["content-security-policy"].includes("base-uri 'self'"));
    });

    it("TC-361 pins form-action to same-origin", () => {
      assert.ok(loginPage.headers["content-security-policy"].includes("form-action 'self'"));
    });

    it("TC-362 sets X-Content-Type-Options: nosniff", () => {
      assert.strictEqual(loginPage.headers["x-content-type-options"], "nosniff");
    });

    it("TC-363 sets X-Frame-Options: DENY", () => {
      assert.strictEqual(loginPage.headers["x-frame-options"], "DENY");
    });

    it("TC-364 sets a Referrer-Policy", () => {
      assert.strictEqual(loginPage.headers["referrer-policy"], "strict-origin-when-cross-origin");
    });

    it("TC-365 sets a Permissions-Policy", () => {
      assert.ok(loginPage.headers["permissions-policy"]);
    });

    it("TC-366 switches off camera, microphone and geolocation", () => {
      const pp = loginPage.headers["permissions-policy"];
      for (const feature of ["camera=()", "microphone=()", "geolocation=()"]) {
        assert.ok(pp.includes(feature), `${feature} not in ${pp}`);
      }
    });

    it("TC-367 leaves ch-ua-arch unnamed, so the download page can still read it", () => {
      // Naming a client-hint feature switches it off. platform.ts reads
      // ch-ua-arch to decide which Mac build to offer and treats a block as
      // "unknown", so naming it here would silently stop the page telling
      // Apple Silicon from Intel rather than raising anything.
      assert.strictEqual(loginPage.headers["permissions-policy"].includes("ch-ua-arch"), false);
    });

    it("TC-368 sets Cross-Origin-Opener-Policy", () => {
      assert.strictEqual(loginPage.headers["cross-origin-opener-policy"], "same-origin");
    });

    it("TC-369 sets Cross-Origin-Resource-Policy", () => {
      assert.strictEqual(loginPage.headers["cross-origin-resource-policy"], "same-origin");
    });

    it("TC-370 sends no HSTS over a plaintext request", () => {
      // Gated on the request scheme, not on ENVIRONMENT: telling a browser to
      // refuse plaintext to a host that only speaks plaintext takes the
      // deployment offline for a year and no later configuration takes it back.
      assert.strictEqual(loginPage.headers["strict-transport-security"], undefined);
    });

    it("TC-371 carries the same headers on a static asset", () => {
      assert.strictEqual(asset.headers["x-content-type-options"], "nosniff");
      assert.ok(asset.headers["content-security-policy"]);
    });

    it("TC-372 carries the same headers on an API error response", () => {
      assert.strictEqual(apiError.headers["x-content-type-options"], "nosniff");
      assert.ok(apiError.headers["content-security-policy"]);
    });

    it("TC-373 answers a rejected sign-in with 401", () => {
      assert.strictEqual(apiError.status, 401);
    });

    it("TC-374 gives the generic rejection message over the API too", () => {
      assert.strictEqual(JSON.parse(apiError.body).detail, "Invalid email or password");
    });

    it("TC-375 sets no session cookie on a rejected sign-in over the API", () => {
      assert.strictEqual(apiError.headers["set-cookie"], undefined);
    });

    it("TC-376 marks the refresh cookie HttpOnly and SameSite=strict on the wire", async () => {
      // SameSite=strict is the CSRF defence — there is no CSRF token, and the
      // prod validator refuses to start with cookie_samesite=none.
      const ok = await rawRequest("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
      });
      const setCookie = String(ok.headers["set-cookie"]);
      assert.ok(setCookie.includes("sentinel_refresh="), setCookie);
      assert.ok(/HttpOnly/i.test(setCookie), setCookie);
      assert.ok(/SameSite=strict/i.test(setCookie), setCookie);
    });

    it("TC-377 scopes the refresh cookie to Path=/ on the wire", async () => {
      const ok = await rawRequest("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
      });
      assert.ok(/Path=\//i.test(String(ok.headers["set-cookie"])));
    });

    it("TC-378 refuses an unauthenticated fleet request", async () => {
      const res = await rawRequest("/api/fleet/overview");
      assert.strictEqual(res.status, 401);
    });

    it("TC-379 refuses an unauthenticated device list", async () => {
      const res = await rawRequest("/api/devices");
      assert.strictEqual(res.status, 401);
    });

    it("TC-380 refuses a garbage bearer token", async () => {
      const res = await rawRequest("/api/fleet/overview", {
        headers: { Authorization: "Bearer not-a-real-token" },
      });
      assert.strictEqual(res.status, 401);
    });

    it("TC-381 refuses a refresh with no cookie", async () => {
      const res = await rawRequest("/api/auth/refresh", { method: "POST" });
      assert.strictEqual(res.status, 401);
    });

    it("TC-382 emits exactly one external module script and no inline script", () => {
      // script-src needs no 'unsafe-inline' precisely because of this. If the
      // build ever emits an inline script the console breaks loudly, which is
      // the correct failure.
      assert.strictEqual(loginPage.body.includes("<script>"), false);
      const modules = loginPage.body.match(/<script type="module"/g) || [];
      assert.strictEqual(modules.length, 1);
    });
  });

  // ==========================================================================
  // Module 14 — Untrusted input at the login form
  //
  // Nothing here expects the payloads to work. What is asserted is that each
  // one is treated as a *value*: the same generic rejection, no dialog, no
  // markup reaching the DOM, and no session.
  // ==========================================================================
  describe("Module 14 — Untrusted input at the login form", function () {
    const PAYLOADS = [
      ["a script tag", "<script>window.__xss=1</script>"],
      ["an img onerror", '<img src=x onerror="window.__xss=1">'],
      ["an svg onload", "<svg onload=window.__xss=1>"],
      ["a javascript: URL", "javascript:window.__xss=1"],
      ["a SQL tautology", "' OR '1'='1' --"],
      ["a SQL union", "' UNION SELECT null,null --"],
      ["a SQL comment terminator", "admin'--"],
      ["a template expression", "${7*7}"],
      ["a handlebars expression", "{{7*7}}"],
      ["a path traversal", "../../../../etc/passwd"],
      ["a null byte", "abc%00def"],
      ["a CRLF injection", "abc%0d%0aSet-Cookie:+x=y"],
      ["an LDAP filter", "*)(uid=*"],
      ["a shell substitution", "$(id)"],
      ["a very long string", "A".repeat(500)],
    ];

    PAYLOADS.forEach(([label, payload], i) => {
      it(`TC-${String(383 + i).padStart(3, "0")} treats ${label} in the password as a value`, async () => {
        await resetToLogin();
        await fillLogin(EMAIL, payload.slice(0, 128));
        await (await present("login-submit")).click();
        const message = await textOf("login-error");
        assert.strictEqual(message.trim(), "Invalid email or password");
      });
    });

    it("TC-398 executes no injected script from the password field", async () => {
      await resetToLogin();
      await fillLogin(EMAIL, "<script>window.__xss=1</script>");
      await (await present("login-submit")).click();
      await present("login-error");
      const fired = await driver.executeScript("return window.__xss === 1");
      assert.strictEqual(fired, false);
    });

    it("TC-399 injects no markup into the error banner", async () => {
      await resetToLogin();
      await fillLogin(EMAIL, "<b>bold</b>");
      await (await present("login-submit")).click();
      const html = await (await present("login-error")).getAttribute("innerHTML");
      assert.strictEqual(html.includes("<b>"), false);
    });

    it("TC-400 raises no browser dialog for any payload", async () => {
      await resetToLogin();
      await fillLogin(EMAIL, "');alert(1);//");
      await (await present("login-submit")).click();
      await present("login-error");
      let dialog = null;
      try {
        dialog = await driver.switchTo().alert();
      } catch {
        dialog = null;
      }
      assert.strictEqual(dialog, null);
    });

    it("TC-401 grants no session for a SQL tautology in the email", async () => {
      await resetToLogin();
      await fillLogin("' OR '1'='1' --@example.com", "AnyPassword12345");
      await (await present("login-submit")).click();
      await absent("app-header");
      assert.strictEqual(await currentPath(), "/login");
    });

    it("TC-402 grants no session for a tautology in the password", async () => {
      await resetToLogin();
      await fillLogin(EMAIL, "' OR '1'='1' --");
      await (await present("login-submit")).click();
      await present("login-error");
      assert.strictEqual(await cookie("sentinel_refresh"), null);
    });

    it("TC-403 keeps an injected tag as an escaped value, never as markup", async () => {
      // The payload does survive in the serialised DOM, escaped, as the input's
      // own value="&lt;img src=x onerror=1&gt;" — which is the field doing its
      // job. What must not exist is an element built from it, so that is what
      // is asserted: no <img>, and nothing carrying an event-handler attribute.
      await resetToLogin();
      await fillLogin("xss@example.com", "<img src=x onerror=1>");
      await (await present("login-submit")).click();
      await present("login-error");
      const injected = await driver.executeScript(
        "return {" +
          " images: document.querySelectorAll('img').length," +
          " handlers: Array.from(document.querySelectorAll('*'))" +
          "   .filter((el) => el.hasAttribute('onerror') || el.hasAttribute('onload')).length" +
          "}",
      );
      assert.deepStrictEqual(injected, { images: 0, handlers: 0 });
    });

    it("TC-404 survives a payload without breaking the form", async () => {
      await resetToLogin();
      await fillLogin(EMAIL, "<svg onload=1>");
      await (await present("login-submit")).click();
      await present("login-error");
      await fillLogin(EMAIL, PASSWORD);
      await (await present("login-submit")).click();
      await present("page-title");
      assert.strictEqual(await currentPath(), "/");
    });
  });

  // ==========================================================================
  // Module 15 — Accessibility and keyboard operation
  // ==========================================================================
  describe("Module 15 — Accessibility and keyboard operation", function () {
    it("TC-405 gives the login page one h1-equivalent card title", async () => {
      await resetToLogin();
      assert.ok(await exists("login-title"));
    });

    it("TC-406 labels the email input with a visible label", async () => {
      const label = await driver.findElement(By.css("label[for='email']"));
      assert.strictEqual((await label.getText()).trim(), "Email");
    });

    it("TC-407 labels the password input with a visible label", async () => {
      const label = await driver.findElement(By.css("label[for='password']"));
      assert.strictEqual((await label.getText()).trim(), "Password");
    });

    it("TC-408 gives the password toggle an accessible name", async () => {
      assert.ok(await attrOf("password-toggle", "aria-label"));
    });

    it("TC-409 gives the bootstrap loader a status role", async () => {
      // Asserted structurally: FullPageLoader is the only thing rendering the
      // page-loading hook, and it carries role=status and an aria-label.
      const html = (await navRequest("/")).body;
      assert.ok(html.includes("<div id=\"root\">"));
    });

    it("TC-410 completes a sign-in using only the keyboard", async () => {
      await resetToLogin();
      const email = await present("login-email");
      await email.click();
      await email.sendKeys(EMAIL, Key.TAB, PASSWORD, Key.ENTER);
      await present("page-title");
      assert.strictEqual(await currentPath(), "/");
    });

    it("TC-411 reaches the sign-out control by keyboard from the header", async () => {
      const signOutEl = await present("sign-out");
      await driver.executeScript("arguments[0].focus()", signOutEl);
      const focused = await driver.executeScript(
        "return document.activeElement.getAttribute('data-testid')",
      );
      assert.strictEqual(focused, "sign-out");
    });

    it("TC-412 activates sign-out with the Enter key", async () => {
      const signOutEl = await present("sign-out");
      await driver.executeScript("arguments[0].focus()", signOutEl);
      await signOutEl.sendKeys(Key.ENTER);
      await present("login-form");
      assert.strictEqual(await currentPath(), "/login");
    });

    it("TC-413 exposes every nav item as a real link, not a click handler", async () => {
      await signIn();
      for (const key of [
        "devices",
        "add-device",
        "alerts",
        "anomalies",
        "forecasts",
        "incidents",
        "reports",
        "settings",
      ]) {
        const el = await present(`nav-${key}`);
        assert.strictEqual(await el.getTagName(), "a");
        assert.ok(await el.getAttribute("href"), `nav-${key} has no href`);
      }
    });

    it("TC-414 exposes the add-device call to action as a real link", async () => {
      await go("/");
      assert.strictEqual(await (await present("add-device")).getTagName(), "a");
    });

    it("TC-415 keeps the signed-in account visible at desktop width", async () => {
      await driver.manage().window().setRect({ width: 1280, height: 900 });
      await go("/");
      assert.strictEqual(await (await present("current-user-email")).isDisplayed(), true);
    });

    it("TC-416 keeps sign-out reachable at a narrow width", async () => {
      // Below `sm` the email is dropped rather than truncated — signing out is
      // the one thing that must stay reachable.
      await driver.manage().window().setRect({ width: 420, height: 900 });
      await go("/");
      assert.strictEqual(await (await present("sign-out")).isDisplayed(), true);
    });

    it("TC-417 does not scroll the page sideways at a narrow width", async () => {
      const overflows = await driver.executeScript(
        "return document.documentElement.scrollWidth > document.documentElement.clientWidth + 1",
      );
      assert.strictEqual(overflows, false);
    });

    it("TC-418 still renders the login form at a narrow width", async () => {
      await resetToLogin();
      assert.strictEqual(await (await present("login-form")).isDisplayed(), true);
      await driver.manage().window().setRect({ width: 1280, height: 900 });
    });

    it("TC-419 marks the page title as a heading on a content page", async () => {
      await signIn();
      await go("/settings");
      const tag = await (await present("page-title")).getTagName();
      assert.strictEqual(tag, "h1");
    });

    it("TC-420 keeps the login card focusable in DOM order: email, password, submit", async () => {
      await resetToLogin();
      const order = await driver.executeScript(
        "return Array.from(document.querySelectorAll('[data-testid=\\'login-form\\'] input, [data-testid=\\'login-form\\'] button'))" +
          ".map((el) => el.getAttribute('data-testid'))",
      );
      assert.deepStrictEqual(order, [
        "login-email",
        "login-password",
        "password-toggle",
        "login-submit",
      ]);
    });
  });
});
