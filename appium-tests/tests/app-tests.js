#!/usr/bin/env node
/**
 * Sentinel Android app — Appium functional E2E suite.
 *
 * Deliverable 2 of docs/TEST_BRIEF.md: `appium-tests/`, 300+ functional cases,
 * reported into an .xlsx by `npm run test:xlsx`.
 *
 * Read docs/TESTING.md and this folder's README before running it. The short
 * version of the four things that have to be true first:
 *
 *   * `make e2e-db && make e2e-serve` — NOT `make serve`. That stack runs
 *     ENVIRONMENT=prod, whose rate limiter 429s the eleventh login (so every
 *     "Invalid email or password" assertion below fails against "Too many
 *     attempts") and whose COOKIE_SECURE=true makes the refresh cookie get
 *     dropped over http (so every session-persistence assertion fails against
 *     a bounce to the sign-in form).
 *   * `appium` running on :4723 with ANDROID_HOME exported *into it* — the
 *     server shells out to adb itself, so exporting it only for this process
 *     fails at session creation with a message about the variable.
 *   * An emulator booted, and the APK installed from
 *     `EXPO_PUBLIC_API_URL=http://10.0.2.2:8000 make mobile-apk-test`. That URL
 *     is inlined at build time; an APK built against anything else installs,
 *     opens, and cannot sign in.
 *   * The stack reachable from the host as SENTINEL_URL (default
 *     http://localhost:8000) — the fixtures below talk to it directly, while
 *     the app talks to the same server through the emulator's 10.0.2.2 alias.
 *
 * Five structural decisions, all deliberate:
 *
 *   * ONE Appium session for the whole run. Session creation is ~20s on an
 *     emulator; a fresh one per case would dwarf the assertions and buy no
 *     isolation that matters. It also fixes the starting state: the
 *     uiautomator2 driver clears app data when a session begins, so the run
 *     always starts signed out, with no collector token and no cached fleet.
 *   * Expensive setup lives in a module's `before`, and the `it`s assert
 *     facets of the resulting state. A suite that re-signs-in forty times to
 *     check forty properties of the signed-in header measures the emulator.
 *   * Most structural assertions read one page-source snapshot captured after
 *     the interaction that produced it. That snapshot IS the device's real
 *     accessibility tree — the same thing a per-case `$()` lookup would read,
 *     minus a round trip each.
 *   * The fleet starts genuinely empty, and the suite makes it so: `before`
 *     removes any device left behind by an earlier run through the API. It
 *     never writes a metric. There is no metric seeder and there will not be
 *     one — CLAUDE.md's first hard rule is that every number came from a real
 *     agent reading a real machine.
 *   * The device the later modules assert against is therefore a REAL one:
 *     module 19 enrols the emulator itself through the app's own "Monitor this
 *     phone" flow, and every reading modules 20-25 assert is something the
 *     Android collector actually measured. Module 25 removes it again, so the
 *     account ends as it started.
 *
 * Every describe is named `Module N — ...`, because tools/test-report derives
 * the workbook's Module column from that prefix; renaming them collapses the
 * per-module breakdown into one row.
 *
 * Locators are `resource-id` (from `testID`) first and `content-desc` (from
 * `accessibilityLabel`) second — never a list position. Rows are keyed by
 * entity id, so "the third card" starts asserting about a different machine
 * the moment the fleet reorders.
 */

const assert = require("node:assert");
const { execSync } = require("node:child_process");
const { remote } = require("webdriverio");

// --- configuration -----------------------------------------------------------

/** Where the fixtures reach the stack from the host. */
const BASE = (process.env.SENTINEL_URL || "http://localhost:8000").replace(/\/$/, "");
/** Where the APK reaches the same stack from inside the emulator. */
const APP_BASE = process.env.SENTINEL_APP_URL || "http://10.0.2.2:8000";

const EMAIL = process.env.SENTINEL_E2E_EMAIL || "e2e@example.com";
const PASSWORD = process.env.SENTINEL_E2E_PASSWORD || "e2e-Sentinel-Test-2026";

const PKG = "com.sentinel.viewer";
const ACTIVITY = `${PKG}.MainActivity`;

const APPIUM_HOST = process.env.APPIUM_HOST || "127.0.0.1";
const APPIUM_PORT = Number(process.env.APPIUM_PORT || 4723);

/** Long enough for a cold RN bundle parse plus the auth bootstrap. */
const WAIT = 30000;
/** For asserting something is *absent*; a full WAIT here would cost minutes. */
const SHORT_WAIT = 4000;

/**
 * The name the collector gives this phone — Build.MODEL on the emulator.
 *
 * Spaces are allowed because real phones have them: Build.MODEL is "Pixel 7
 * Pro" on a Pixel and "Android SDK built for x86_64" on an AOSP emulator
 * image. The original pattern refused both and happened to pass only because
 * the AVD it was written against reports `sdk_gphone64_arm64`. What this is
 * really asserting is that the name came from the device rather than being a
 * placeholder or blank, so it stays strict about everything except the space.
 */
const EXPECTED_DEVICE_NAME_RE = /^[\w .-]+$/;

let driver;
/** Bearer token for the fixture calls. Minted once in the root `before`. */
let apiToken = null;
/** The device the emulator enrols as, once module 19 has run. */
let enrolled = null;
/** The rule module 16 authors, so its `after` can clean up after a failure. */
let authoredRuleId = null;

// --- locators ----------------------------------------------------------------

/**
 * `~id` is content-desc in webdriverio, and a bare `testID` surfaces as
 * `resource-id` — the two are different attributes and the accessibility-id
 * shorthand silently matches nothing for these. A UiSelector is the locator
 * that actually addresses them.
 */
const byId = (id) => `android=new UiSelector().resourceId("${id}")`;
const byDesc = (d) => `android=new UiSelector().description("${d}")`;
const byText = (t) => `android=new UiSelector().text("${t}")`;
const byIdMatch = (re) => `android=new UiSelector().resourceIdMatches("${re}")`;

/**
 * A text input by its accessibility label, and only an input.
 *
 * `Field` sets content-desc from its visible label, and a `Segmented` chip sets
 * it from the option's label — so a rule form has both a "Threshold" chip and a
 * "Threshold" box, and a plain description() lookup silently returns whichever
 * the tree happens to hold first.
 */
const byLabelledField = (label) =>
  `android=new UiSelector().description("${label}").className("android.widget.EditText")`;

/**
 * A native Alert.alert button.
 *
 * Scoped to the framework's own dialog ids rather than matched on the label
 * alone: a "Delete" dialog is raised from a card that also has a "Delete"
 * control, and a bare text lookup taps the card again.
 */
/** Scroll the screen until a control is in view, then address it. */
const scrollToDesc = (label) =>
  `android=new UiScrollable(new UiSelector().scrollable(true))` +
  `.scrollIntoView(new UiSelector().description("${label}"))`;

const byDialogButton = (label) =>
  `android=new UiSelector().resourceIdMatches("android:id/button[0-9]")` +
  `.textMatches("(?i)${label}")`;

// --- page-source snapshots ---------------------------------------------------

function unescapeXml(s) {
  return s
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&#10;/g, "\n")
    .replace(/&amp;/g, "&");
}

/**
 * Flatten uiautomator2's XML into the four attributes assertions actually use.
 *
 * A regex rather than a parser because Node ships no XML parser and this needs
 * no tree: every assertion here is "is there a node with this id / this text /
 * this name". `>` is escaped inside attribute values (uiautomator emits
 * `segmented-&gt;` for the "above" comparison chip), so stopping the tag match
 * at the first `>` is safe.
 */
function parseNodes(xml) {
  const nodes = [];
  const tagRe = /<([\w.$]+)\s([^>]*?)\/?>/g;
  let tag;
  while ((tag = tagRe.exec(xml)) !== null) {
    const attrs = {};
    const attrRe = /([\w-]+)="([^"]*)"/g;
    let attr;
    while ((attr = attrRe.exec(tag[2])) !== null) attrs[attr[1]] = unescapeXml(attr[2]);
    nodes.push({
      cls: attrs.class || tag[1],
      id: attrs["resource-id"] || "",
      text: attrs.text || "",
      desc: attrs["content-desc"] || "",
      enabled: attrs.enabled === "true",
      password: attrs.password === "true",
      clickable: attrs.clickable === "true",
      displayed: attrs.displayed !== "false",
    });
  }
  return nodes;
}

class Snap {
  constructor(xml) {
    this.xml = xml;
    this.nodes = parseNodes(xml);
  }
  byId(id) {
    return this.nodes.find((n) => n.id === id) || null;
  }
  hasId(id) {
    return this.byId(id) !== null;
  }
  idsMatching(re) {
    return [...new Set(this.nodes.map((n) => n.id).filter((id) => id && re.test(id)))];
  }
  /** Exact text on some node — the copy as a person reads it. */
  hasText(t) {
    return this.nodes.some((n) => n.text === t);
  }
  /** Substring, for copy long enough that uiautomator truncates it. */
  includesText(sub) {
    return this.nodes.some((n) => n.text.includes(sub));
  }
  /** Exact accessibility name on some node. */
  hasDesc(d) {
    return this.nodes.some((n) => n.desc === d);
  }
  includesDesc(sub) {
    return this.nodes.some((n) => n.desc.includes(sub));
  }
  descOf(id) {
    const n = this.byId(id);
    return n ? n.desc : null;
  }
  textOf(id) {
    const n = this.byId(id);
    return n ? n.text : null;
  }
  texts() {
    return this.nodes.map((n) => n.text).filter(Boolean);
  }
  buttons() {
    return this.nodes.filter((n) => n.cls.endsWith("Button"));
  }
  editTexts() {
    return this.nodes.filter((n) => n.cls.endsWith("EditText"));
  }
  /**
   * Every `screen-*` root currently mounted. One at a time, in a healthy app.
   *
   * The `-title` suffix has to be excluded explicitly: `Screen` derives the
   * heading's hook from the root's, so `screen-fleet-title` matches the same
   * shape as a root and a healthy single-screen app reads as two.
   */
  screens() {
    return this.idsMatching(/^screen-[a-z-]+$/).filter((id) => !id.endsWith("-title"));
  }
}

async function snapshot() {
  return new Snap(await driver.getPageSource());
}

/**
 * `getPageSource()` returns what is rendered, and a ScrollView taller than the
 * screen renders only the part in view — so a device screen's Disks and Remove
 * cards are simply absent from a snapshot taken at the top, and an assertion
 * about them reports a missing card that is really just below the fold.
 *
 * This scrolls to the end collecting a source at each step and reads the union.
 * Nodes repeat across steps, which is why every assertion over one of these is
 * an existence check; `idsMatching` de-duplicates, and counting raw `nodes` is
 * not safe here the way it is on a single snapshot.
 */
async function fullSnapshot(maxScrolls = 8) {
  let combined = "";
  let previous = null;
  for (let step = 0; step <= maxScrolls; step += 1) {
    const current = await driver.getPageSource();
    combined += current;
    if (current === previous) break;
    previous = current;
    if (!(await scrollGesture("down"))) break;
    await driver.pause(800);
  }
  // Back to the top, so the next interaction starts where the caller left off.
  for (let step = 0; step <= maxScrolls + 1; step += 1) {
    if (!(await scrollGesture("up"))) break;
    await driver.pause(250);
  }
  await driver.pause(700);
  return new Snap(combined);
}

/** One screenful in `direction`. False when the driver has no gesture support. */
async function scrollGesture(direction) {
  try {
    await driver.execute("mobile: scrollGesture", {
      left: 100,
      top: 600,
      width: 880,
      height: 1400,
      direction,
      percent: 0.9,
    });
    return true;
  } catch {
    return false;
  }
}

// --- driving -----------------------------------------------------------------

async function el(selector, timeout = WAIT) {
  const found = await driver.$(selector);
  await found.waitForDisplayed({ timeout });
  return found;
}

async function tap(selector, timeout = WAIT) {
  const found = await el(selector, timeout);
  await found.click();
  return found;
}

async function isPresent(selector) {
  return (await driver.$(selector)).isExisting();
}

/** True when the selector is gone (or never appeared) inside the window. */
async function isGone(selector, timeout = SHORT_WAIT) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (!(await isPresent(selector))) return true;
    await driver.pause(150);
  }
  return !(await isPresent(selector));
}

async function setField(id, value) {
  const field = await el(byId(id));
  if (value === "") {
    await field.clearValue();
    return;
  }
  await field.setValue(value);
}

/** Read a field back the way the platform reports it — masked stays masked. */
async function fieldText(id) {
  return (await el(byId(id))).getText();
}

// --- flows -------------------------------------------------------------------

/**
 * Submit the auth form and wait for whichever of the two outcomes lands.
 *
 * Polling the source for either `error-note` or `screen-fleet` rather than
 * waiting on one of them: a fixed wait for the error note turns a *successful*
 * sign-in into a timeout that reports the wrong thing entirely, and vice versa.
 */
async function submitAuth(timeout = 25000) {
  await tap(byId("auth-submit"));
  const deadline = Date.now() + timeout;
  let snap = null;
  while (Date.now() < deadline) {
    snap = await snapshot();
    if (snap.hasId("error-note") || snap.hasId("screen-fleet")) return snap;
    await driver.pause(300);
  }
  return snap || (await snapshot());
}

async function attemptSignIn(email, password) {
  await setField("auth-email", email);
  await setField("auth-password", password);
  return submitAuth();
}

/** Signed in and standing on the fleet, whatever the app was showing before. */
async function ensureSignedIn() {
  if (await isPresent(byId("screen-fleet"))) return;
  if (await isPresent(byId("auth-email"))) {
    await attemptSignIn(EMAIL, PASSWORD);
    await el(byId("screen-fleet"));
    return;
  }
  // Somewhere inside the authenticated stack: pop back to the tabs.
  for (let i = 0; i < 6; i += 1) {
    if (await isPresent(byId("tab-devices"))) break;
    await driver.back();
    await driver.pause(600);
  }
  await tap(byId("tab-devices"));
  await el(byId("screen-fleet"));
}

/** Signed out and standing on the auth form. */
async function ensureSignedOut() {
  if (await isPresent(byId("auth-email"))) return;
  await ensureSignedIn();
  await openMoreRow("more-settings", "screen-settings");
  await tap(byDesc("Sign out"));
  await el(byId("auth-email"));
}

async function goTab(tab, screen) {
  await tap(byId(tab));
  await el(byId(screen));
  await driver.pause(400);
}

async function openMoreRow(row, screen) {
  await ensureSignedIn();
  await goTab("tab-more", "screen-more");
  await tap(byId(row));
  await el(byId(screen));
  await driver.pause(1200);
}

async function back(settle = 900) {
  await driver.back();
  await driver.pause(settle);
}

/** Tap a native Alert.alert button by its visible label. */
async function tapDialog(label) {
  await tap(byDialogButton(label), 15000);
  await driver.pause(1500);
}

// --- fixtures ----------------------------------------------------------------

async function api(method, path, { body, token } = {}) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token === undefined ? (apiToken ? { Authorization: `Bearer ${apiToken}` } : {}) : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const raw = await res.text();
  let parsed = null;
  if (raw) {
    try {
      parsed = JSON.parse(raw);
    } catch {
      parsed = raw;
    }
  }
  return { status: res.status, body: parsed };
}

async function apiSignIn() {
  const { status, body } = await api("POST", "/auth/login", {
    body: { email: EMAIL, password: PASSWORD },
    token: null,
  });
  if (status !== 200 || !body || !body.access_token) {
    throw new Error(
      `The fixtures could not sign in to ${BASE} as ${EMAIL} (HTTP ${status}). ` +
        `Is \`make e2e-db && make e2e-serve\` up, and seeded with these credentials?`,
    );
  }
  apiToken = body.access_token;
}

/**
 * Put the account back to the state the suite assumes: no devices, and none of
 * the rules a previous run authored.
 *
 * Removing a device is not seeding one — nothing here writes a metric, and the
 * only device these modules ever assert against is the emulator itself, which
 * module 19 enrols for real. Without this a crashed run leaves a stale offline
 * device behind and module 8's empty fleet fails on the next attempt, naming
 * the fleet screen for what is really leftover state.
 */
async function resetAccount() {
  const { body: devices } = await api("GET", "/devices");
  for (const device of Array.isArray(devices) ? devices : []) {
    await api("DELETE", `/devices/${device.id}`);
  }
  const { body: rules } = await api("GET", "/alerts/rules");
  for (const rule of Array.isArray(rules) ? rules : []) {
    if (rule.source !== "builtin") await api("DELETE", `/alerts/rules/${rule.id}`);
  }
}

/** Wait for the server to agree that something happened, not for a fixed delay. */
async function waitFor(predicate, { timeout = 90000, interval = 2500, what = "condition" } = {}) {
  const deadline = Date.now() + timeout;
  let last;
  while (Date.now() < deadline) {
    last = await predicate();
    if (last) return last;
    await new Promise((resolve) => setTimeout(resolve, interval));
  }
  throw new Error(`timed out after ${timeout}ms waiting for ${what}`);
}

// =============================================================================

describe("Sentinel Android App — E2E Suite", function () {
  this.timeout(180000);

  before(async function () {
    this.timeout(240000);
    await apiSignIn();
    await resetAccount();

    driver = await remote({
      hostname: APPIUM_HOST,
      port: APPIUM_PORT,
      logLevel: "error",
      capabilities: {
        platformName: "Android",
        "appium:automationName": "UiAutomator2",
        "appium:deviceName": "Android Emulator",
        "appium:appPackage": PKG,
        "appium:appActivity": ACTIVITY,
        // Long, because a module's `before` can legitimately spend a minute
        // waiting on the server rather than on the device.
        "appium:newCommandTimeout": 600,
      },
    });

    /*
     * Granted here rather than left to the runtime prompt.
     *
     * Starting the collector asks for POST_NOTIFICATIONS (a foreground service
     * whose notification cannot be shown runs invisibly, which is the thing
     * that notification exists to prevent). The permission dialog belongs to
     * com.android.permissioncontroller, so it sits on top of the app and every
     * `resource-id` lookup underneath it misses — module 19 would fail on the
     * dialog rather than on anything the app did. The grant has to happen
     * AFTER the session starts: session creation clears app data, which
     * revokes it again.
     */
    try {
      execSync(`adb shell pm grant ${PKG} android.permission.POST_NOTIFICATIONS`, {
        stdio: "ignore",
      });
    } catch {
      // Older images have no runtime notification permission to grant. The
      // dialog simply never appears there.
    }

    await el(byId("auth-email"), 60000);
  });

  after(async function () {
    this.timeout(120000);
    try {
      if (apiToken) {
        if (authoredRuleId) await api("DELETE", `/alerts/rules/${authoredRuleId}`);
        await resetAccount();
      }
    } finally {
      if (driver) await driver.deleteSession();
    }
  });

  // ===========================================================================
  describe("Module 1 — Sign-in screen structure", function () {
    let snap;

    before(async function () {
      await ensureSignedOut();
      snap = await snapshot();
    });

    it("renders the Sentinel wordmark", function () {
      assert.ok(snap.hasText("Sentinel"));
    });

    it("says what signing in is for", function () {
      assert.ok(snap.hasText("Sign in to watch your own machines."));
    });

    it("exposes the email field as auth-email", function () {
      assert.ok(snap.hasId("auth-email"));
    });

    it("labels the email field for a screen reader", function () {
      assert.strictEqual(snap.descOf("auth-email"), "Email");
    });

    it("renders the email field as a text input", function () {
      assert.match(snap.byId("auth-email").cls, /EditText$/);
    });

    it("shows a visible Email label above it", function () {
      assert.ok(snap.hasText("Email"));
    });

    it("suggests an address as the email placeholder", function () {
      assert.strictEqual(snap.textOf("auth-email"), "you@example.com");
    });

    it("exposes the password field as auth-password", function () {
      assert.ok(snap.hasId("auth-password"));
    });

    it("labels the password field for a screen reader", function () {
      assert.strictEqual(snap.descOf("auth-password"), "Password");
    });

    it("flags the password field as a password field", function () {
      assert.strictEqual(snap.byId("auth-password").password, true);
    });

    it("shows a visible Password label above it", function () {
      assert.ok(snap.hasText("Password"));
    });

    it("offers a control to reveal the password", function () {
      assert.ok(snap.hasDesc("Show password"));
    });

    it("exposes the submit button as auth-submit", function () {
      assert.ok(snap.hasId("auth-submit"));
    });

    it("names the submit button 'Sign in'", function () {
      assert.strictEqual(snap.descOf("auth-submit"), "Sign in");
    });

    it("disables the submit button while the form is empty", function () {
      assert.strictEqual(snap.byId("auth-submit").enabled, false);
    });

    it("exposes the mode toggle as auth-toggle-mode", function () {
      assert.ok(snap.hasId("auth-toggle-mode"));
    });

    it("offers signing up from the sign-in screen", function () {
      assert.strictEqual(snap.descOf("auth-toggle-mode"), "No account yet? Sign up");
    });

    it("hides the display-name field in sign-in mode", function () {
      assert.ok(!snap.hasId("auth-display-name"));
    });

    it("prints the API base URL the APK was built against", function () {
      assert.ok(snap.hasText(APP_BASE), `expected ${APP_BASE} on screen`);
    });

    it("points that base URL at the emulator's host alias, not localhost", function () {
      assert.ok(!snap.includesText("http://localhost"));
    });

    it("renders no bottom tab bar while signed out", function () {
      assert.ok(!snap.hasId("tab-devices"));
      assert.ok(!snap.hasId("tab-alerts"));
      assert.ok(!snap.hasId("tab-more"));
    });

    it("mounts no authenticated screen root while signed out", function () {
      assert.deepStrictEqual(snap.screens(), []);
    });

    it("does not render the fleet behind the form", function () {
      assert.ok(!snap.hasId("screen-fleet"));
    });

    it("shows no error note before an attempt has been made", function () {
      assert.ok(!snap.hasId("error-note"));
    });

    it("shows no empty state on the auth screen", function () {
      assert.ok(!snap.hasId("empty-state"));
    });

    it("gives every button on the screen an accessible name", function () {
      const unnamed = snap.buttons().filter((b) => b.desc === "");
      assert.deepStrictEqual(unnamed, []);
    });

    it("offers exactly two text inputs in sign-in mode", function () {
      assert.strictEqual(snap.editTexts().length, 2);
    });
  });

  // ===========================================================================
  describe("Module 2 — Sign-in field behaviour", function () {
    before(async function () {
      await ensureSignedOut();
      await setField("auth-email", "");
      await setField("auth-password", "");
    });

    afterEach(async function () {
      // Every case below leaves the form in a state the next one does not want
      // — including its mode, since two of them toggle it.
      if (await isPresent(byId("auth-display-name"))) {
        await tap(byId("auth-toggle-mode"));
        await isGone(byId("auth-display-name"));
      }
      await setField("auth-email", "");
      await setField("auth-password", "");
    });

    it("accepts typing into the email field", async function () {
      await setField("auth-email", "typed@example.com");
      assert.strictEqual(await fieldText("auth-email"), "typed@example.com");
    });

    it("replaces the previous value rather than appending to it", async function () {
      await setField("auth-email", "first@example.com");
      await setField("auth-email", "second@example.com");
      assert.strictEqual(await fieldText("auth-email"), "second@example.com");
    });

    it("restores the placeholder when the email is cleared", async function () {
      await setField("auth-email", "typed@example.com");
      await setField("auth-email", "");
      assert.strictEqual(await fieldText("auth-email"), "you@example.com");
    });

    it("accepts typing into the password field", async function () {
      await setField("auth-password", "some-password");
      assert.notStrictEqual(await fieldText("auth-password"), "");
    });

    it("masks the password instead of showing the characters", async function () {
      await setField("auth-password", "some-password");
      const shown = await fieldText("auth-password");
      assert.ok(!shown.includes("some-password"), `password was readable: ${shown}`);
    });

    it("masks it one bullet per character", async function () {
      await setField("auth-password", "abcdefgh");
      assert.strictEqual((await fieldText("auth-password")).length, 8);
    });

    it("enables the submit button once both fields are filled", async function () {
      await setField("auth-email", "user@example.com");
      await setField("auth-password", "a-password");
      assert.strictEqual((await snapshot()).byId("auth-submit").enabled, true);
    });

    it("leaves the submit button disabled with only an email", async function () {
      await setField("auth-email", "user@example.com");
      assert.strictEqual((await snapshot()).byId("auth-submit").enabled, false);
    });

    it("leaves the submit button disabled with only a password", async function () {
      await setField("auth-password", "a-password");
      assert.strictEqual((await snapshot()).byId("auth-submit").enabled, false);
    });

    it("treats a whitespace-only email as empty", async function () {
      await setField("auth-email", "   ");
      await setField("auth-password", "a-password");
      assert.strictEqual((await snapshot()).byId("auth-submit").enabled, false);
    });

    it("reveals the password when the eye control is tapped", async function () {
      await setField("auth-password", "revealed-text");
      await tap(byDesc("Show password"));
      assert.strictEqual(await fieldText("auth-password"), "revealed-text");
    });

    it("renames the eye control once the password is showing", async function () {
      assert.ok((await snapshot()).hasDesc("Hide password"));
    });

    it("masks the password again when the control is tapped back", async function () {
      await tap(byDesc("Hide password"));
      const shown = await fieldText("auth-password");
      assert.ok(!shown.includes("revealed-text"));
    });

    it("accepts a long email address without truncating it", async function () {
      const long = `${"a".repeat(120)}@example.com`;
      await setField("auth-email", long);
      assert.strictEqual(await fieldText("auth-email"), long);
    });

    it("accepts a long password", async function () {
      await setField("auth-password", "z".repeat(180));
      assert.strictEqual((await fieldText("auth-password")).length, 180);
    });

    it("accepts non-ASCII characters in the email field", async function () {
      await setField("auth-email", "ünïcode@example.com");
      assert.strictEqual(await fieldText("auth-email"), "ünïcode@example.com");
    });

    it("keeps what was typed when the mode is toggled to sign-up", async function () {
      await setField("auth-email", "kept@example.com");
      await tap(byId("auth-toggle-mode"));
      await el(byId("auth-display-name"));
      assert.strictEqual(await fieldText("auth-email"), "kept@example.com");
    });

    it("keeps it again when the mode is toggled back", async function () {
      // Self-contained rather than continuing the case above: the afterEach
      // clears the fields between them, which is what a suite that leans on
      // the previous case's leftovers discovers the hard way.
      await setField("auth-email", "kept@example.com");
      await tap(byId("auth-toggle-mode"));
      await el(byId("auth-display-name"));
      await tap(byId("auth-toggle-mode"));
      await isGone(byId("auth-display-name"));
      assert.strictEqual(await fieldText("auth-email"), "kept@example.com");
    });

    it("raises no error note merely from typing", async function () {
      await setField("auth-email", "typing@example.com");
      await setField("auth-password", "typing");
      assert.ok(!(await snapshot()).hasId("error-note"));
    });

    it("keeps the API base URL visible while the form is being filled in", async function () {
      assert.ok((await snapshot()).hasText(APP_BASE));
    });
  });

  // ===========================================================================
  describe("Module 3 — Rejected credentials", function () {
    // One attempt per payload, in `before`, because each is a real round trip
    // to the API. The `it`s assert facets of the results.
    const results = {};

    before(async function () {
      this.timeout(240000);
      await ensureSignedOut();
      results.wrongPassword = await attemptSignIn(EMAIL, "definitely-not-the-password");
      results.unknownEmail = await attemptSignIn("nobody-here@example.com", PASSWORD);
      results.caseChangedPassword = await attemptSignIn(EMAIL, PASSWORD.toUpperCase());
      results.sqlInPassword = await attemptSignIn(EMAIL, "' OR '1'='1");
      results.sqlInEmail = await attemptSignIn("' OR 1=1 --@example.com", PASSWORD);
      results.malformedEmail = await attemptSignIn("not-an-email", PASSWORD);
      results.paddedEmail = await attemptSignIn(`  ${EMAIL}  `, "still-the-wrong-password");
      results.second = await attemptSignIn(EMAIL, "wrong-again");
      results.after = await snapshot();
    });

    it("refuses a wrong password", function () {
      assert.ok(results.wrongPassword.hasId("error-note"));
    });

    it("renders the refusal through the shared error-note hook", function () {
      assert.match(results.wrongPassword.byId("error-note").cls, /ViewGroup$/);
    });

    it("says the credentials were invalid", function () {
      assert.ok(results.wrongPassword.includesText("Invalid email or password"));
    });

    it("does not reach the fleet on a wrong password", function () {
      assert.ok(!results.wrongPassword.hasId("screen-fleet"));
    });

    it("stays on the sign-in form", function () {
      assert.ok(results.wrongPassword.hasId("auth-email"));
    });

    it("refuses an email with no account behind it", function () {
      assert.ok(results.unknownEmail.hasId("error-note"));
    });

    it("gives an unknown email the same message as a wrong password", function () {
      assert.ok(results.unknownEmail.includesText("Invalid email or password"));
    });

    it("never says which half of the pair was wrong", function () {
      const leaky = /no such (user|account)|user not found|email not registered|wrong password/i;
      assert.ok(!leaky.test(results.unknownEmail.xml));
    });

    it("treats the password as case-sensitive", function () {
      assert.ok(results.caseChangedPassword.includesText("Invalid email or password"));
    });

    it("refuses an SQL payload in the password rather than acting on it", function () {
      assert.ok(results.sqlInPassword.includesText("Invalid email or password"));
    });

    it("refuses an SQL payload in the email", function () {
      assert.ok(results.sqlInEmail.hasId("error-note"));
    });

    it("does not sign anybody in on an SQL payload", function () {
      assert.ok(!results.sqlInPassword.hasId("screen-fleet"));
      assert.ok(!results.sqlInEmail.hasId("screen-fleet"));
    });

    it("refuses an address the API will not parse as an email", function () {
      assert.ok(results.malformedEmail.hasId("error-note"));
    });

    it("trims surrounding whitespace before sending the email", function () {
      // A padded address that reaches the credential check comes back with the
      // credential message; one that did not would fail validation instead.
      assert.ok(results.paddedEmail.includesText("Invalid email or password"));
    });

    it("refuses a second attempt just as it refused the first", function () {
      assert.ok(results.second.includesText("Invalid email or password"));
    });

    it("leaves the submit button usable after a refusal", function () {
      assert.strictEqual(results.after.byId("auth-submit").enabled, true);
    });

    it("keeps the typed email after a refusal", async function () {
      assert.strictEqual(await fieldText("auth-email"), EMAIL);
    });

    it("keeps the password field populated after a refusal", async function () {
      assert.notStrictEqual(await fieldText("auth-password"), "");
    });

    it("shows no tab bar after a refusal", function () {
      assert.ok(!results.after.hasId("tab-devices"));
    });

    it("clears the error when the form switches to sign-up", async function () {
      await tap(byId("auth-toggle-mode"));
      await el(byId("auth-display-name"));
      assert.ok(!(await snapshot()).hasId("error-note"));
    });

    it("leaves it cleared when the form switches back", async function () {
      await tap(byId("auth-toggle-mode"));
      await isGone(byId("auth-display-name"));
      assert.ok(!(await snapshot()).hasId("error-note"));
    });
  });

  // ===========================================================================
  describe("Module 4 — Sign-up mode", function () {
    const results = {};
    let signup;

    before(async function () {
      this.timeout(240000);
      await ensureSignedOut();
      await setField("auth-email", "");
      await setField("auth-password", "");
      await tap(byId("auth-toggle-mode"));
      await el(byId("auth-display-name"));
      signup = await snapshot();

      results.duplicate = await attemptSignIn(EMAIL, "a-perfectly-long-password");
      results.shortPassword = await attemptSignIn("brand-new-user@example.com", "short");
      results.badEmail = await attemptSignIn("not-an-email", "a-perfectly-long-password");
    });

    after(async function () {
      // Back to sign-in mode for whatever runs next.
      if (await isPresent(byId("auth-display-name"))) {
        await tap(byId("auth-toggle-mode"));
        await isGone(byId("auth-display-name"));
      }
    });

    it("changes the subtitle to describe creating an account", function () {
      assert.ok(signup.includesText("Create an account."));
    });

    it("says an account only ever sees devices it enrolled itself", function () {
      assert.ok(signup.includesText("You only ever see devices you enrolled yourself."));
    });

    it("adds the display-name field", function () {
      assert.ok(signup.hasId("auth-display-name"));
    });

    it("marks the display name as optional", function () {
      assert.ok(signup.hasText("Display name (optional)"));
    });

    it("labels the display-name field for a screen reader", function () {
      assert.strictEqual(signup.descOf("auth-display-name"), "Display name (optional)");
    });

    it("states the minimum password length", function () {
      assert.ok(signup.hasText("At least 12 characters."));
    });

    it("renames the submit button 'Create account'", function () {
      assert.strictEqual(signup.descOf("auth-submit"), "Create account");
    });

    it("offers signing in from the mode toggle", function () {
      assert.strictEqual(signup.descOf("auth-toggle-mode"), "Already have an account? Sign in");
    });

    it("keeps the email field", function () {
      assert.ok(signup.hasId("auth-email"));
    });

    it("keeps the password field masked", function () {
      assert.strictEqual(signup.byId("auth-password").password, true);
    });

    it("offers three text inputs in sign-up mode", function () {
      assert.strictEqual(signup.editTexts().length, 3);
    });

    it("keeps the API base URL on screen", function () {
      assert.ok(signup.hasText(APP_BASE));
    });

    it("shows no error before anything is submitted", function () {
      assert.ok(!signup.hasId("error-note"));
    });

    it("refuses an email that already has an account", function () {
      assert.ok(results.duplicate.hasId("error-note"));
    });

    it("does not sign the caller in on a duplicate email", function () {
      assert.ok(!results.duplicate.hasId("screen-fleet"));
    });

    it("refuses a password under the stated minimum", function () {
      assert.ok(results.shortPassword.hasId("error-note"));
    });

    it("keeps the sign-up form on screen after a refusal", function () {
      assert.ok(results.shortPassword.hasId("auth-display-name"));
    });

    it("refuses an address that is not an email", function () {
      assert.ok(results.badEmail.hasId("error-note"));
    });

    it("creates no account from any of the refused attempts", async function () {
      const { body } = await api("POST", "/auth/login", {
        body: { email: "brand-new-user@example.com", password: "short" },
        token: null,
      });
      assert.ok(!body || !body.access_token);
    });

    it("is still signed out after all three refusals", async function () {
      assert.ok(await isPresent(byId("auth-email")));
    });
  });

  // ===========================================================================
  describe("Module 5 — Successful authentication", function () {
    let snap;

    before(async function () {
      this.timeout(180000);
      await ensureSignedOut();
      await attemptSignIn(EMAIL, PASSWORD);
      await el(byId("screen-fleet"));
      await driver.pause(1500);
      snap = await snapshot();
    });

    it("accepts the seeded credentials", function () {
      assert.ok(snap.hasId("screen-fleet"));
    });

    it("lands on the Devices tab", function () {
      assert.strictEqual(snap.textOf("screen-fleet-title"), "Devices");
    });

    it("derives the title hook from the screen hook", function () {
      assert.ok(snap.hasId("screen-fleet-title"));
    });

    it("says where the fleet's numbers come from", function () {
      assert.ok(
        snap.includesText("Health of every machine you've enrolled, from its own real telemetry."),
      );
    });

    it("takes the sign-in form off screen", function () {
      assert.ok(!snap.hasId("auth-email"));
    });

    it("takes the submit button off screen", function () {
      assert.ok(!snap.hasId("auth-submit"));
    });

    it("mounts the bottom tab bar", function () {
      assert.ok(snap.hasId("tab-devices"));
    });

    it("offers an Alerts tab", function () {
      assert.ok(snap.hasId("tab-alerts"));
    });

    it("offers a More tab", function () {
      assert.ok(snap.hasId("tab-more"));
    });

    it("offers exactly three tabs", function () {
      assert.strictEqual(snap.idsMatching(/^tab-/).length, 3);
    });

    it("names the Devices tab for a screen reader", function () {
      assert.ok(snap.includesDesc("Devices"));
    });

    it("names the Alerts tab for a screen reader", function () {
      assert.ok(snap.includesDesc("Alerts"));
    });

    it("names the More tab for a screen reader", function () {
      assert.ok(snap.includesDesc("More"));
    });

    it("leaves no error note behind from the earlier refusals", function () {
      assert.ok(!snap.hasId("error-note"));
    });

    it("mounts exactly one screen root", function () {
      assert.deepStrictEqual(snap.screens(), ["screen-fleet"]);
    });

    it("stamps the overview with when it was generated", function () {
      assert.ok(snap.includesText("just now") || snap.includesText("ago"));
    });
  });

  // ===========================================================================
  describe("Module 6 — Backgrounding and a cold restart", function () {
    const results = {};

    before(async function () {
      this.timeout(240000);
      await ensureSignedIn();

      await driver.background(4);
      await el(byId("screen-fleet"));
      await driver.pause(1200);
      results.resumed = await snapshot();

      // A real cold start: force-stop and relaunch, which is how Phase 10a
      // verified that the platform cookie jar replays the refresh cookie.
      await driver.terminateApp(PKG);
      await driver.activateApp(PKG);
      await el(byId("screen-fleet"), 60000);
      await driver.pause(2000);
      results.relaunched = await snapshot();

      await driver.terminateApp(PKG);
      await driver.activateApp(PKG);
      await el(byId("screen-fleet"), 60000);
      await driver.pause(1500);
      results.relaunchedTwice = await snapshot();
    });

    it("keeps the session across a background and resume", function () {
      assert.ok(results.resumed.hasId("screen-fleet"));
    });

    it("does not show the sign-in form on resume", function () {
      assert.ok(!results.resumed.hasId("auth-email"));
    });

    it("keeps the tab bar across a resume", function () {
      assert.ok(results.resumed.hasId("tab-devices"));
    });

    it("keeps the session across a force-stop and cold relaunch", function () {
      assert.ok(results.relaunched.hasId("screen-fleet"));
    });

    it("does not fall back to the sign-in form after a relaunch", function () {
      assert.ok(!results.relaunched.hasId("auth-email"));
    });

    it("restores the Devices tab as the landing screen", function () {
      assert.strictEqual(results.relaunched.textOf("screen-fleet-title"), "Devices");
    });

    it("restores all three tabs after a relaunch", function () {
      assert.strictEqual(results.relaunched.idsMatching(/^tab-/).length, 3);
    });

    it("raises no error note during the relaunch", function () {
      assert.ok(!results.relaunched.hasId("error-note"));
    });

    it("survives a second relaunch in a row", function () {
      assert.ok(results.relaunchedTwice.hasId("screen-fleet"));
    });

    it("never leaves two screen roots mounted after a relaunch", function () {
      assert.strictEqual(results.relaunchedTwice.screens().length, 1);
    });
  });

  // ===========================================================================
  describe("Module 7 — Bottom tab navigation", function () {
    const seen = {};

    before(async function () {
      this.timeout(180000);
      await ensureSignedIn();
      await goTab("tab-alerts", "screen-alerts");
      seen.alerts = await snapshot();
      await goTab("tab-more", "screen-more");
      seen.more = await snapshot();
      await goTab("tab-devices", "screen-fleet");
      seen.backToFleet = await snapshot();
      // Tapping the tab you are already on must be a no-op, not a reset.
      await goTab("tab-devices", "screen-fleet");
      seen.sameTabAgain = await snapshot();
    });

    it("opens the alerts screen from the Alerts tab", function () {
      assert.ok(seen.alerts.hasId("screen-alerts"));
    });

    it("titles the alerts screen 'Alerts'", function () {
      assert.strictEqual(seen.alerts.textOf("screen-alerts-title"), "Alerts");
    });

    it("unmounts the fleet when the Alerts tab is shown", function () {
      assert.deepStrictEqual(seen.alerts.screens(), ["screen-alerts"]);
    });

    it("opens the More screen from the More tab", function () {
      assert.ok(seen.more.hasId("screen-more"));
    });

    it("titles the More screen 'More'", function () {
      assert.strictEqual(seen.more.textOf("screen-more-title"), "More");
    });

    it("unmounts the alerts screen when More is shown", function () {
      assert.deepStrictEqual(seen.more.screens(), ["screen-more"]);
    });

    it("returns to the fleet from the Devices tab", function () {
      assert.ok(seen.backToFleet.hasId("screen-fleet"));
    });

    it("completes a full Devices → Alerts → More → Devices round trip", function () {
      assert.strictEqual(seen.backToFleet.textOf("screen-fleet-title"), "Devices");
    });

    it("treats a tap on the active tab as a no-op", function () {
      assert.ok(seen.sameTabAgain.hasId("screen-fleet"));
    });

    it("keeps the tab bar mounted on the fleet", function () {
      assert.strictEqual(seen.backToFleet.idsMatching(/^tab-/).length, 3);
    });

    it("keeps the tab bar mounted on alerts", function () {
      assert.strictEqual(seen.alerts.idsMatching(/^tab-/).length, 3);
    });

    it("keeps the tab bar mounted on More", function () {
      assert.strictEqual(seen.more.idsMatching(/^tab-/).length, 3);
    });

    it("gives every tab screen its own screen-<id> root", function () {
      assert.ok(seen.alerts.hasId("screen-alerts"));
      assert.ok(seen.more.hasId("screen-more"));
      assert.ok(seen.backToFleet.hasId("screen-fleet"));
    });

    it("gives every tab screen its own derived title hook", function () {
      assert.ok(seen.alerts.hasId("screen-alerts-title"));
      assert.ok(seen.more.hasId("screen-more-title"));
      assert.ok(seen.backToFleet.hasId("screen-fleet-title"));
    });

    it("does not sign the user out while switching tabs", function () {
      assert.ok(!seen.more.hasId("auth-email"));
    });
  });

  // ===========================================================================
  describe("Module 8 — The empty fleet", function () {
    // An empty fleet is a real state this product renders on purpose, and it is
    // the correct thing to assert when nothing has reported. Nothing here
    // fabricates a device to make the screen look busier.
    let snap;
    let revisited;

    before(async function () {
      this.timeout(180000);
      await resetAccount();
      await ensureSignedIn();
      await goTab("tab-alerts", "screen-alerts");
      await goTab("tab-devices", "screen-fleet");
      await driver.pause(2500);
      snap = await snapshot();
      await goTab("tab-more", "screen-more");
      await goTab("tab-devices", "screen-fleet");
      await driver.pause(2000);
      revisited = await snapshot();
    });

    it("renders the totals card", function () {
      assert.ok(snap.hasText("Devices"));
    });

    it("labels a Devices total", function () {
      assert.ok(snap.hasText("Devices"));
    });

    it("labels an Online total", function () {
      assert.ok(snap.hasText("Online"));
    });

    it("labels an Offline total", function () {
      assert.ok(snap.hasText("Offline"));
    });

    it("labels an Awaiting total", function () {
      assert.ok(snap.hasText("Awaiting"));
    });

    it("labels a Healthy total", function () {
      assert.ok(snap.hasText("Healthy"));
    });

    it("labels a Degraded total", function () {
      assert.ok(snap.hasText("Degraded"));
    });

    it("labels a Critical total", function () {
      assert.ok(snap.hasText("Critical"));
    });

    it("labels an Unknown total", function () {
      assert.ok(snap.hasText("Unknown"));
    });

    it("renders eight zeroes, one per tile", function () {
      const zeroes = snap.nodes.filter((n) => n.text === "0").length;
      assert.strictEqual(zeroes, 8);
    });

    it("renders a zero count as 0 rather than as unavailable", function () {
      // A tally of nothing is a real zero; "unavailable" is reserved for a
      // measurement the platform could not take.
      assert.ok(!snap.includesText("unavailable"));
    });

    it("renders the shared empty-state hook", function () {
      assert.ok(snap.hasId("empty-state"));
    });

    it("headlines the empty state 'No devices yet'", function () {
      assert.ok(snap.hasText("No devices yet"));
    });

    it("says where an enrollment code comes from", function () {
      assert.ok(snap.includesText("enrollment code"));
    });

    it("points at the phone's own one-tap route as the alternative", function () {
      assert.ok(snap.includesText("Monitor this phone"));
    });

    it("renders no device card", function () {
      assert.deepStrictEqual(snap.idsMatching(/^device-card-/), []);
    });

    it("offers no Watch live control with nothing to watch", function () {
      assert.ok(!snap.hasDesc("Watch live"));
    });

    it("still stamps the overview with a generated-at time", function () {
      assert.ok(snap.includesText("just now") || snap.includesText("ago"));
    });

    it("shows no error note for an empty fleet", function () {
      assert.ok(!snap.hasId("error-note"));
    });

    it("keeps the empty state after leaving the tab and coming back", function () {
      assert.ok(revisited.hasId("empty-state"));
    });

    it("re-renders no device card on the refetch a refocus triggers", function () {
      assert.deepStrictEqual(revisited.idsMatching(/^device-card-/), []);
    });
  });

  // ===========================================================================
  describe("Module 9 — Alerts triage with nothing firing", function () {
    const seen = {};

    before(async function () {
      this.timeout(180000);
      await ensureSignedIn();
      await goTab("tab-alerts", "screen-alerts");
      await driver.pause(2500);
      seen.firing = await snapshot();

      await tap(byId("segmented-resolved"));
      await driver.pause(2500);
      seen.resolved = await snapshot();

      await tap(byId("segmented-all"));
      await driver.pause(2500);
      seen.all = await snapshot();

      await tap(byId("segmented-firing"));
      await driver.pause(2500);
      seen.backToFiring = await snapshot();
    });

    it("renders the alerts screen", function () {
      assert.ok(seen.firing.hasId("screen-alerts"));
    });

    it("titles it 'Alerts'", function () {
      assert.strictEqual(seen.firing.textOf("screen-alerts-title"), "Alerts");
    });

    it("says the list spans the whole fleet", function () {
      assert.ok(seen.firing.includesText("across your fleet"));
    });

    it("offers a Firing filter", function () {
      assert.ok(seen.firing.hasId("segmented-firing"));
    });

    it("offers a Resolved filter", function () {
      assert.ok(seen.firing.hasId("segmented-resolved"));
    });

    it("offers an All filter", function () {
      assert.ok(seen.firing.hasId("segmented-all"));
    });

    it("offers exactly three filters", function () {
      assert.strictEqual(seen.firing.idsMatching(/^segmented-/).length, 3);
    });

    it("names each filter for a screen reader", function () {
      assert.strictEqual(seen.firing.descOf("segmented-firing"), "Firing");
      assert.strictEqual(seen.firing.descOf("segmented-resolved"), "Resolved");
      assert.strictEqual(seen.firing.descOf("segmented-all"), "All");
    });

    it("opens on the Firing filter", function () {
      assert.ok(seen.firing.hasText("No alerts are currently firing."));
    });

    it("headlines the empty state 'Nothing here'", function () {
      assert.ok(seen.firing.hasText("Nothing here"));
    });

    it("changes the empty copy when Resolved is chosen", function () {
      assert.ok(seen.resolved.hasText("No alerts match this filter."));
    });

    it("keeps an empty state under the All filter", function () {
      assert.ok(seen.all.hasId("empty-state"));
    });

    it("restores the firing copy when Firing is chosen again", function () {
      assert.ok(seen.backToFiring.hasText("No alerts are currently firing."));
    });

    it("renders no alert event card", function () {
      assert.deepStrictEqual(seen.all.idsMatching(/^alert-event-/), []);
    });

    it("offers no silence control with nothing firing", function () {
      assert.deepStrictEqual(seen.all.idsMatching(/^alert-silence-/), []);
    });

    it("offers no incident shortcut with nothing firing", function () {
      assert.deepStrictEqual(seen.all.idsMatching(/^alert-open-incident-/), []);
    });

    it("offers a route through to the alert rules", function () {
      assert.ok(seen.firing.hasDesc("Alert rules"));
    });

    it("opens the alert rules from that route", async function () {
      await tap(byDesc("Alert rules"));
      await el(byId("screen-alert-rules"));
      assert.ok(await isPresent(byId("screen-alert-rules")));
    });

    it("returns to Alerts when that screen is dismissed", async function () {
      await back(1500);
      assert.ok(await isPresent(byId("screen-alerts")));
    });

    it("keeps the tab bar on the alerts screen", function () {
      assert.strictEqual(seen.firing.idsMatching(/^tab-/).length, 3);
    });
  });

  // ===========================================================================
  describe("Module 10 — The More menu", function () {
    let snap;

    before(async function () {
      this.timeout(180000);
      await ensureSignedIn();
      await goTab("tab-more", "screen-more");
      await driver.pause(1200);
      snap = await snapshot();
    });

    it("renders the More screen", function () {
      assert.ok(snap.hasId("screen-more"));
    });

    it("titles it 'More'", function () {
      assert.strictEqual(snap.textOf("screen-more-title"), "More");
    });

    it("offers an Incidents row", function () {
      assert.ok(snap.hasId("more-incidents"));
    });

    it("offers an Anomalies row", function () {
      assert.ok(snap.hasId("more-anomalies"));
    });

    it("offers a Forecasts row", function () {
      assert.ok(snap.hasId("more-forecasts"));
    });

    it("offers a Reports row", function () {
      assert.ok(snap.hasId("more-reports"));
    });

    it("offers an Alert rules row", function () {
      assert.ok(snap.hasId("more-alertrules"));
    });

    it("offers a Settings row", function () {
      assert.ok(snap.hasId("more-settings"));
    });

    it("offers exactly six rows", function () {
      assert.strictEqual(snap.idsMatching(/^more-/).length, 6);
    });

    it("keys every row by its route rather than its position", function () {
      const ids = snap.idsMatching(/^more-/).sort();
      assert.deepStrictEqual(ids, [
        "more-alertrules",
        "more-anomalies",
        "more-forecasts",
        "more-incidents",
        "more-reports",
        "more-settings",
      ]);
    });

    it("describes what Incidents are", function () {
      assert.ok(snap.includesText("Alerts that fired together on one machine"));
    });

    it("describes Anomalies against the machine's own baseline", function () {
      assert.ok(snap.includesText("own baseline calls normal"));
    });

    it("describes Forecasts as where each machine is heading", function () {
      assert.ok(snap.includesText("Where each machine is heading"));
    });

    it("describes Reports as measured uptime", function () {
      assert.ok(snap.includesText("Measured uptime and reliability"));
    });

    it("names all four rule types under Alert rules", function () {
      assert.ok(snap.includesText("threshold, anomaly, forecast and combination"));
    });

    it("describes Settings as account, push and this phone", function () {
      assert.ok(snap.includesText("Account, push notifications"));
    });

    it("names each row for a screen reader by its visible title", function () {
      assert.strictEqual(snap.descOf("more-incidents"), "Incidents");
      assert.strictEqual(snap.descOf("more-anomalies"), "Anomalies");
      assert.strictEqual(snap.descOf("more-forecasts"), "Forecasts");
      assert.strictEqual(snap.descOf("more-reports"), "Reports");
      assert.strictEqual(snap.descOf("more-alertrules"), "Alert rules");
      assert.strictEqual(snap.descOf("more-settings"), "Settings");
    });

    it("exposes the rows as buttons rather than as plain views", function () {
      for (const id of snap.idsMatching(/^more-/)) {
        assert.match(snap.byId(id).cls, /Button$/, id);
      }
    });

    it("says the same screens are reachable per device", function () {
      assert.ok(snap.includesText("also reachable"));
    });

    it("names what is still web-only", function () {
      assert.ok(snap.includesText("web-only"));
    });
  });

  // ===========================================================================
  describe("Module 11 — Incidents, fleet-wide", function () {
    const seen = {};

    before(async function () {
      this.timeout(180000);
      await openMoreRow("more-incidents", "screen-incidents");
      await driver.pause(2000);
      seen.open = await snapshot();

      await tap(byId("segmented-resolved"));
      await driver.pause(2500);
      seen.resolved = await snapshot();

      await tap(byId("segmented-all"));
      await driver.pause(2500);
      seen.all = await snapshot();

      await tap(byId("segmented-open"));
      await driver.pause(2500);
      seen.backToOpen = await snapshot();
    });

    it("opens the incidents screen from the More menu", function () {
      assert.ok(seen.open.hasId("screen-incidents"));
    });

    it("names the screen in the stack header", function () {
      assert.ok(seen.open.hasText("Incidents"));
    });

    it("titles the screen through the derived title hook", function () {
      assert.strictEqual(seen.open.textOf("screen-incidents-title"), "Incidents");
    });

    it("explains that an incident groups alerts that fired together", function () {
      assert.ok(seen.open.includesText("fired together on one machine"));
    });

    it("offers an Open filter", function () {
      assert.ok(seen.open.hasId("segmented-open"));
    });

    it("offers a Resolved filter", function () {
      assert.ok(seen.open.hasId("segmented-resolved"));
    });

    it("offers an All filter", function () {
      assert.ok(seen.open.hasId("segmented-all"));
    });

    it("opens on the Open filter", function () {
      assert.ok(seen.open.hasText("Nothing is going wrong right now."));
    });

    it("headlines the empty state 'No incidents'", function () {
      assert.ok(seen.open.hasText("No incidents"));
    });

    it("changes the copy under the Resolved filter", function () {
      assert.ok(seen.resolved.hasText("No incident has been recorded in this range."));
    });

    it("keeps an empty state under the All filter", function () {
      assert.ok(seen.all.hasId("empty-state"));
    });

    it("restores the open copy when Open is chosen again", function () {
      assert.ok(seen.backToOpen.hasText("Nothing is going wrong right now."));
    });

    it("renders no incident card", function () {
      assert.deepStrictEqual(seen.all.idsMatching(/^incident-/), []);
    });

    it("shows no device-scope note when opened fleet-wide", function () {
      assert.ok(!seen.open.includesText("Showing"));
    });

    it("offers a stack up-control", function () {
      assert.ok(seen.open.hasDesc("Navigate up"));
    });

    it("hides the bottom tab bar on a pushed stack screen", function () {
      assert.strictEqual(seen.open.idsMatching(/^tab-/).length, 0);
    });

    it("returns to the More menu when dismissed", async function () {
      await back(1500);
      assert.ok(await isPresent(byId("screen-more")));
    });
  });

  // ===========================================================================
  describe("Module 12 — Anomalies, fleet-wide", function () {
    const seen = {};

    before(async function () {
      this.timeout(180000);
      await openMoreRow("more-anomalies", "screen-anomalies");
      await driver.pause(2000);
      seen.firing = await snapshot();

      await tap(byId("segmented-resolved"));
      await driver.pause(2500);
      seen.resolved = await snapshot();

      await tap(byId("segmented-all"));
      await driver.pause(2500);
      seen.all = await snapshot();

      await tap(byId("segmented-firing"));
      await driver.pause(2500);
      seen.backToFiring = await snapshot();
    });

    it("opens the anomalies screen from the More menu", function () {
      assert.ok(seen.firing.hasId("screen-anomalies"));
    });

    it("names the screen in the stack header", function () {
      assert.ok(seen.firing.hasText("Anomalies"));
    });

    it("titles the screen through the derived title hook", function () {
      assert.strictEqual(seen.firing.textOf("screen-anomalies-title"), "Anomalies");
    });

    it("explains an anomaly against the machine's own baseline", function () {
      assert.ok(seen.firing.includesText("own baseline"));
    });

    it("distinguishes it from a threshold somebody set", function () {
      assert.ok(seen.firing.includesText("not a threshold"));
    });

    it("offers a Firing filter", function () {
      assert.ok(seen.firing.hasId("segmented-firing"));
    });

    it("offers a Resolved filter", function () {
      assert.ok(seen.firing.hasId("segmented-resolved"));
    });

    it("offers an All filter", function () {
      assert.ok(seen.firing.hasId("segmented-all"));
    });

    it("headlines the empty state 'No anomalies'", function () {
      assert.ok(seen.firing.hasText("No anomalies"));
    });

    it("says nothing is outside its baseline", function () {
      assert.ok(seen.firing.hasText("Nothing is currently outside its baseline."));
    });

    it("keeps an empty state under Resolved", function () {
      assert.ok(seen.resolved.hasId("empty-state"));
    });

    it("keeps an empty state under All", function () {
      assert.ok(seen.all.hasId("empty-state"));
    });

    it("restores the firing copy when Firing is chosen again", function () {
      assert.ok(seen.backToFiring.hasText("Nothing is currently outside its baseline."));
    });

    it("renders no anomaly event card", function () {
      assert.deepStrictEqual(seen.all.idsMatching(/^alert-event-/), []);
    });

    it("returns to the More menu when dismissed", async function () {
      await back(1500);
      assert.ok(await isPresent(byId("screen-more")));
    });
  });

  // ===========================================================================
  describe("Module 13 — Forecasts, fleet-wide", function () {
    let snap;

    before(async function () {
      this.timeout(180000);
      await openMoreRow("more-forecasts", "screen-forecasts");
      await driver.pause(2500);
      snap = await snapshot();
    });

    it("opens the forecasts screen from the More menu", function () {
      assert.ok(snap.hasId("screen-forecasts"));
    });

    it("names the screen in the stack header", function () {
      assert.ok(snap.hasText("Forecasts"));
    });

    it("titles the screen through the derived title hook", function () {
      assert.strictEqual(snap.textOf("screen-forecasts-title"), "Forecasts");
    });

    it("says the projection comes from the machine's own measured trend", function () {
      assert.ok(snap.includesText("own measured trend"));
    });

    it("distinguishes it from a threshold anybody set", function () {
      assert.ok(snap.includesText("Nothing here is a threshold"));
    });

    it("renders a time-to-capacity section", function () {
      assert.ok(snap.hasText("Time to capacity"));
    });

    it("renders an empty state with nothing projected", function () {
      assert.ok(snap.hasId("empty-state"));
    });

    it("headlines it 'Nothing projected yet'", function () {
      assert.ok(snap.hasText("Nothing projected yet"));
    });

    it("explains that forecasts are recomputed on a timer", function () {
      assert.ok(snap.includesText("recomputed on a timer"));
    });

    it("offers no status filter, unlike the alert-shaped screens", function () {
      assert.deepStrictEqual(snap.idsMatching(/^segmented-/), []);
    });

    it("shows no device-scope note when opened fleet-wide", function () {
      assert.ok(!snap.includesText("Showing"));
    });

    it("returns to the More menu when dismissed", async function () {
      await back(1500);
      assert.ok(await isPresent(byId("screen-more")));
    });
  });

  // ===========================================================================
  describe("Module 14 — Reports, fleet-wide", function () {
    const seen = {};

    before(async function () {
      this.timeout(180000);
      await openMoreRow("more-reports", "screen-reports");
      await driver.pause(2500);
      seen.week = await snapshot();

      await tap(byId("segmented-30"));
      await driver.pause(3000);
      seen.month = await snapshot();

      await tap(byId("segmented-90"));
      await driver.pause(3000);
      seen.quarter = await snapshot();

      await tap(byId("segmented-7"));
      await driver.pause(3000);
      seen.backToWeek = await snapshot();
    });

    it("opens the reports screen from the More menu", function () {
      assert.ok(seen.week.hasId("screen-reports"));
    });

    it("names the screen in the stack header", function () {
      assert.ok(seen.week.hasText("Reports"));
    });

    it("titles the screen through the derived title hook", function () {
      assert.strictEqual(seen.week.textOf("screen-reports-title"), "Reports");
    });

    it("says uptime is measured from real reporting", function () {
      assert.ok(seen.week.includesText("uptime measured from real reporting"));
    });

    it("offers a 7-day period", function () {
      assert.ok(seen.week.hasId("segmented-7"));
    });

    it("offers a 30-day period", function () {
      assert.ok(seen.week.hasId("segmented-30"));
    });

    it("offers a 90-day period", function () {
      assert.ok(seen.week.hasId("segmented-90"));
    });

    it("offers exactly three periods", function () {
      assert.strictEqual(seen.week.idsMatching(/^segmented-/).length, 3);
    });

    it("names each period for a screen reader", function () {
      assert.strictEqual(seen.week.descOf("segmented-7"), "7d");
      assert.strictEqual(seen.week.descOf("segmented-30"), "30d");
      assert.strictEqual(seen.week.descOf("segmented-90"), "90d");
    });

    it("renders an empty state with no device reporting", function () {
      assert.ok(seen.week.hasId("empty-state"));
    });

    it("headlines it 'Nothing to report'", function () {
      assert.ok(seen.week.hasText("Nothing to report"));
    });

    it("says no device reported during the period", function () {
      assert.ok(seen.week.hasText("No device reported during this period."));
    });

    it("keeps the empty state over 30 days", function () {
      assert.ok(seen.month.hasId("empty-state"));
    });

    it("keeps the empty state over 90 days", function () {
      assert.ok(seen.quarter.hasId("empty-state"));
    });

    it("returns to the 7-day period when it is chosen again", function () {
      assert.ok(seen.backToWeek.hasId("empty-state"));
    });

    it("offers no export controls with nothing to export", function () {
      assert.ok(!seen.week.hasDesc("PDF"));
      assert.ok(!seen.week.hasDesc("CSV"));
    });

    it("says scheduled email reports are configured on the web", function () {
      assert.ok(seen.week.includesText("Scheduled email reports are configured in the web console."));
    });

    it("returns to the More menu when dismissed", async function () {
      await back(1500);
      assert.ok(await isPresent(byId("screen-more")));
    });
  });

  // ===========================================================================
  describe("Module 15 — The built-in alert rules", function () {
    let snap;
    let ruleIds;

    before(async function () {
      this.timeout(180000);
      await openMoreRow("more-alertrules", "screen-alert-rules");
      await driver.pause(2500);
      // The whole list, not the part above the fold: the account is seeded
      // with more built-in rules than fit on a phone, and the last card's own
      // buttons are simply not rendered until it is scrolled to.
      snap = await fullSnapshot();
      ruleIds = snap.idsMatching(/^rule-[0-9a-f-]{36}$/);
    });

    it("opens the alert rules screen from the More menu", function () {
      assert.ok(snap.hasId("screen-alert-rules"));
    });

    it("names the screen in the stack header", function () {
      assert.ok(snap.hasText("Alert rules"));
    });

    it("names all four rule sources", function () {
      assert.ok(snap.includesText("Static thresholds, adaptive anomaly rules, forecast breaches"));
    });

    it("offers a control to write a new rule", function () {
      assert.ok(snap.hasDesc("New rule"));
    });

    it("shows an empty state for the account's own rules", function () {
      assert.ok(snap.hasId("empty-state"));
    });

    it("headlines it 'You haven't written any rules'", function () {
      assert.ok(snap.hasText("You haven't written any rules"));
    });

    it("points at the automatic set in that empty state", function () {
      assert.ok(snap.includesText("The automatic rules below are already watching every machine."));
    });

    it("heads the automatic set 'AUTOMATIC'", function () {
      assert.ok(snap.hasText("AUTOMATIC"));
    });

    it("explains that the automatic set needs no configuring", function () {
      assert.ok(snap.includesText("without being configured"));
    });

    it("renders a card per built-in rule", function () {
      assert.ok(ruleIds.length > 0, "no rule cards were rendered");
    });

    it("seeds at least four built-in rules with the account", function () {
      assert.ok(ruleIds.length >= 4, `only ${ruleIds.length} rules rendered`);
    });

    it("keys every rule card by its uuid rather than its position", function () {
      for (const id of ruleIds) {
        assert.match(id, /^rule-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
      }
    });

    it("renders the ids the API reports, and no others", async function () {
      const { body: rules } = await api("GET", "/alerts/rules");
      const fromApi = new Set(rules.map((r) => `rule-${r.id}`));
      for (const id of ruleIds) assert.ok(fromApi.has(id), `${id} is not a rule the API knows`);
    });

    it("names a CPU rule", function () {
      assert.ok(snap.hasText("CPU pinned"));
    });

    it("names a memory rule", function () {
      assert.ok(snap.hasText("Memory nearly full"));
    });

    it("names a disk rule", function () {
      assert.ok(snap.hasText("Disk nearly full"));
    });

    it("names a packet-loss rule", function () {
      assert.ok(snap.hasText("Network dropping packets"));
    });

    it("reads a rule's condition back in full", function () {
      assert.ok(snap.hasText("All devices · cpu_percent > 90 for 600s"));
    });

    it("scopes the built-in rules to every device", function () {
      assert.ok(snap.includesText("All devices"));
    });

    it("offers an Edit control on every rule, keyed by that rule's id", function () {
      // Not just "an Edit exists": Android flattens a Card's accessibility
      // subtree, so a rule's own buttons arrive as siblings of `rule-{id}`
      // rather than under it. Without an id apiece there is no way to address
      // *this* rule's Edit except by counting, which is the locator the
      // conventions forbid.
      for (const id of ruleIds) assert.ok(snap.hasId(id.replace("rule-", "rule-edit-")), id);
    });

    it("offers a Delete control on every rule, keyed by that rule's id", function () {
      for (const id of ruleIds) assert.ok(snap.hasId(id.replace("rule-", "rule-delete-")), id);
    });

    it("shows no rule in its editing state until Edit is tapped", function () {
      assert.deepStrictEqual(snap.idsMatching(/^rule-editing-/), []);
    });
  });

  // ===========================================================================
  describe("Module 16 — Authoring an alert rule", function () {
    const RULE_NAME = "Appium suite rule";
    const seen = {};
    let created = null;

    before(async function () {
      this.timeout(300000);
      await ensureSignedIn();
      if (!(await isPresent(byId("screen-alert-rules")))) {
        await openMoreRow("more-alertrules", "screen-alert-rules");
      }
      await tap(byDesc("New rule"));
      await el(byId("segmented-threshold"));
      await driver.pause(1200);
      seen.threshold = await snapshot();

      await tap(byId("segmented-anomaly"));
      await driver.pause(1200);
      seen.anomaly = await snapshot();

      await tap(byId("segmented-multivariate"));
      await driver.pause(1200);
      seen.combination = await snapshot();

      await tap(byId("segmented-forecast"));
      await driver.pause(1200);
      seen.forecast = await snapshot();

      await tap(byId("segmented-threshold"));
      await driver.pause(1200);
      seen.backToThreshold = await snapshot();
    });

    after(async function () {
      this.timeout(120000);
      // Belt and braces: if a case below failed before the delete, the rule is
      // still on the account and module 15's built-ins-only assertions would
      // start failing on the next run.
      if (authoredRuleId) {
        await api("DELETE", `/alerts/rules/${authoredRuleId}`);
        authoredRuleId = null;
      }
    });

    it("opens a form when New rule is tapped", function () {
      assert.ok(seen.threshold.hasText("New rule"));
    });

    it("offers a Name field", function () {
      assert.ok(seen.threshold.hasDesc("Name"));
    });

    it("offers a Threshold rule type", function () {
      assert.ok(seen.threshold.hasId("segmented-threshold"));
    });

    it("offers an Anomaly rule type", function () {
      assert.ok(seen.threshold.hasId("segmented-anomaly"));
    });

    it("offers a Forecast rule type", function () {
      assert.ok(seen.threshold.hasId("segmented-forecast"));
    });

    it("offers a Combination rule type", function () {
      assert.ok(seen.threshold.hasId("segmented-multivariate"));
    });

    it("labels the combination type in words rather than by its schema name", function () {
      assert.strictEqual(seen.threshold.descOf("segmented-multivariate"), "Combination");
    });

    it("scopes a new rule to all devices by default", function () {
      assert.strictEqual(seen.threshold.descOf("segmented-"), "All devices");
    });

    it("offers the six metrics the evaluator actually reads", function () {
      for (const metric of [
        "cpu_percent",
        "mem_percent",
        "swap_percent",
        "disk_percent",
        "packet_loss_percent",
        "cpu_iowait_percent",
      ]) {
        assert.ok(seen.threshold.hasId(`segmented-${metric}`), metric);
      }
    });

    it("labels the metrics for a person rather than by their column names", function () {
      assert.strictEqual(seen.threshold.descOf("segmented-cpu_percent"), "CPU %");
      assert.strictEqual(seen.threshold.descOf("segmented-packet_loss_percent"), "Loss %");
    });

    it("offers the five comparisons", function () {
      for (const cmp of [">", ">=", "<", "<=", "=="]) {
        assert.ok(seen.threshold.hasId(`segmented-${cmp}`), cmp);
      }
    });

    it("reads the comparisons out in words", function () {
      assert.strictEqual(seen.threshold.descOf("segmented->"), "above");
      assert.strictEqual(seen.threshold.descOf("segmented-<"), "below");
      assert.strictEqual(seen.threshold.descOf("segmented-=="), "equals");
    });

    it("offers a Threshold field defaulted to 90", function () {
      const field = seen.threshold.nodes.find((n) => n.desc === "Threshold" && n.cls.endsWith("EditText"));
      assert.ok(field, "no threshold input");
      assert.strictEqual(field.text, "90");
    });

    it("offers a duration field defaulted to 60 seconds", function () {
      const field = seen.threshold.nodes.find((n) => n.desc === "For (seconds)");
      assert.ok(field, "no duration input");
      assert.strictEqual(field.text, "60");
    });

    it("explains that a threshold rule can never fire on one sample", function () {
      assert.ok(seen.threshold.includesText("can never fire on a single sample"));
    });

    it("offers an enabled switch", function () {
      assert.ok(seen.threshold.hasText("Enabled"));
    });

    it("offers Create rule and Cancel", function () {
      assert.ok(seen.threshold.hasDesc("Create rule"));
      assert.ok(seen.threshold.hasDesc("Cancel"));
    });

    it("hides the threshold field for an anomaly rule", function () {
      const field = seen.anomaly.nodes.find((n) => n.desc === "Threshold" && n.cls.endsWith("EditText"));
      assert.strictEqual(field, undefined);
    });

    it("explains an anomaly rule against its own recent normal", function () {
      assert.ok(seen.anomaly.includesText("drifts unusually far from its own recent normal"));
    });

    it("keeps the metric picker for an anomaly rule", function () {
      assert.ok(seen.anomaly.hasId("segmented-cpu_percent"));
    });

    it("hides the metric picker for a combination rule", function () {
      assert.ok(!seen.combination.hasId("segmented-cpu_percent"));
    });

    it("says a combination rule uses all the readings at once", function () {
      assert.ok(seen.combination.includesText("All of them at once"));
    });

    it("explains the 0-100 novelty score in words", function () {
      assert.ok(seen.combination.includesText("scored 0"));
    });

    it("says a device with no trained model never fires a combination rule", function () {
      assert.ok(seen.combination.includesText("no trained model never fires this rule"));
    });

    it("restores the threshold field for a forecast rule", function () {
      const field = seen.forecast.nodes.find((n) => n.desc === "Threshold" && n.cls.endsWith("EditText"));
      assert.ok(field);
    });

    it("says a forecast rule judges the forecast, not the live reading", function () {
      assert.ok(seen.forecast.includesText("not when the live"));
    });

    it("restores the threshold explanation when the type goes back", function () {
      assert.ok(seen.backToThreshold.includesText("can never fire on a single sample"));
    });

    it("refuses to save a rule with no name", async function () {
      await tap(byDesc("Create rule"));
      await driver.pause(1500);
      assert.ok((await snapshot()).hasId("error-note"));
    });

    it("says which field is missing", async function () {
      assert.ok((await snapshot()).includesText("Give the rule a name."));
    });

    it("keeps the form open after that refusal", async function () {
      assert.ok(await isPresent(byId("segmented-threshold")));
    });

    it("creates a rule once it is named", async function () {
      this.timeout(120000);
      await (await el(byLabelledField("Name"))).setValue(RULE_NAME);
      await (await el(byLabelledField("Threshold"))).setValue("77");
      await (await el(byLabelledField("For (seconds)"))).setValue("45");
      await tap(byDesc("Create rule"));
      created = await waitFor(
        async () => {
          const { body } = await api("GET", "/alerts/rules");
          return (Array.isArray(body) ? body : []).find((r) => r.name === RULE_NAME) || null;
        },
        { timeout: 60000, interval: 2000, what: "the authored rule to reach the API" },
      );
      authoredRuleId = created.id;
      assert.strictEqual(created.name, RULE_NAME);
    });

    it("stores it as a threshold rule", function () {
      assert.strictEqual(created.rule_type, "threshold");
    });

    it("stores the threshold that was typed", function () {
      assert.strictEqual(created.threshold, 77);
    });

    it("stores the duration that was typed", function () {
      assert.strictEqual(created.for_duration_seconds, 45);
    });

    it("stores it as the account's own rule, not a built-in", function () {
      assert.notStrictEqual(created.source, "builtin");
    });

    it("renders it as a card keyed by its own id", async function () {
      await el(byId(`rule-${created.id}`), 30000);
      assert.ok(await isPresent(byId(`rule-${created.id}`)));
    });

    it("files it under the account's own rules", async function () {
      assert.ok((await snapshot()).hasText("YOUR RULES"));
    });

    it("reads its condition back on the card", async function () {
      assert.ok(
        (await snapshot()).hasText("All devices · cpu_percent > 77 for 45s"),
      );
    });

    it("opens an editing form for it", async function () {
      this.timeout(120000);
      await el(byId(`rule-${created.id}`));
      await tap(byId(`rule-edit-${created.id}`));
      await el(byId(`rule-editing-${created.id}`), 20000);
      assert.ok(await isPresent(byId(`rule-editing-${created.id}`)));
    });

    it("loads the stored values into the editing form", async function () {
      const field = (await snapshot()).nodes.find(
        (n) => n.desc === "Threshold" && n.cls.endsWith("EditText"),
      );
      assert.strictEqual(field.text, "77");
    });

    it("saves an edited threshold", async function () {
      this.timeout(120000);
      await (await el(byLabelledField("Threshold"))).setValue("88");
      await tap(byDesc("Save changes"));
      const updated = await waitFor(
        async () => {
          const { body } = await api("GET", `/alerts/rules`);
          const found = (Array.isArray(body) ? body : []).find((r) => r.id === created.id);
          return found && found.threshold === 88 ? found : null;
        },
        { timeout: 60000, interval: 2000, what: "the edited threshold to reach the API" },
      );
      assert.strictEqual(updated.threshold, 88);
    });

    it("reads the new threshold back on the card", async function () {
      await el(byId(`rule-${created.id}`), 30000);
      assert.ok((await snapshot()).hasText("All devices · cpu_percent > 88 for 45s"));
    });

    it("asks for confirmation before deleting a rule", async function () {
      this.timeout(120000);
      await el(byId(`rule-${created.id}`));
      await tap(byId(`rule-delete-${created.id}`));
      await driver.pause(1500);
      assert.ok((await snapshot()).includesText(`Delete ${RULE_NAME}?`));
    });

    it("says alerts the rule already fired are kept", async function () {
      assert.ok((await snapshot()).includesText("Alerts it has already fired are kept"));
    });

    it("deletes the rule when the deletion is confirmed", async function () {
      this.timeout(120000);
      await tapDialog("Delete");
      await waitFor(
        async () => {
          const { body } = await api("GET", "/alerts/rules");
          return !(Array.isArray(body) ? body : []).some((r) => r.id === created.id);
        },
        { timeout: 60000, interval: 2000, what: "the rule to be deleted" },
      );
      authoredRuleId = null;
      assert.ok(true);
    });

    it("takes the card off the screen", async function () {
      assert.ok(await isGone(byId(`rule-${created.id}`), 20000));
    });

    it("brings back the empty state for the account's own rules", async function () {
      await driver.pause(1500);
      assert.ok((await snapshot()).hasText("You haven't written any rules"));
    });

    it("leaves the built-in rules untouched", async function () {
      const { body } = await api("GET", "/alerts/rules");
      assert.ok(body.every((r) => r.source === "builtin"));
      assert.ok(body.length >= 4);
    });
  });

  // ===========================================================================
  describe("Module 17 — Settings", function () {
    let snap;

    before(async function () {
      this.timeout(180000);
      await openMoreRow("more-settings", "screen-settings");
      await driver.pause(2000);
      snap = await snapshot();
    });

    it("opens the settings screen from the More menu", function () {
      assert.ok(snap.hasId("screen-settings"));
    });

    it("names the screen in the stack header", function () {
      assert.ok(snap.hasText("Settings"));
    });

    it("titles the screen through the derived title hook", function () {
      assert.strictEqual(snap.textOf("screen-settings-title"), "Settings");
    });

    it("renders an Account card", function () {
      assert.ok(snap.hasText("ACCOUNT"));
    });

    it("names the signed-in account", function () {
      assert.ok(snap.hasText(EMAIL));
    });

    it("shows which server this build talks to", function () {
      assert.ok(snap.hasText(APP_BASE));
    });

    it("points that server at the emulator's host alias", function () {
      assert.ok(!snap.includesText("http://localhost"));
    });

    it("renders a Push notifications card", function () {
      assert.ok(snap.hasText("PUSH NOTIFICATIONS"));
    });

    it("says why push is unavailable rather than leaving the card blank", function () {
      // No Google Play services on this image, and an unconfigured channel
      // degrades visibly instead of silently — the same posture as an unset
      // SMTP or VAPID credential on the server.
      assert.ok(snap.includesText("Push needs a real device"));
    });

    it("offers no push toggle when push is unsupported", function () {
      assert.ok(!snap.hasDesc("Turn on for this phone"));
      assert.ok(!snap.hasDesc("Turn off on this phone"));
    });

    it("renders a card for this phone as a device", function () {
      assert.ok(snap.hasText("THIS PHONE AS A DEVICE"));
    });

    it("names what the phone itself can report", function () {
      assert.ok(snap.includesText("memory, storage, battery and network"));
    });

    it("offers to monitor this phone while it is not enrolled", function () {
      assert.ok(snap.hasDesc("Monitor this phone"));
    });

    it("renders an Elsewhere card", function () {
      assert.ok(snap.hasText("ELSEWHERE"));
    });

    it("names what stays in the web console", function () {
      assert.ok(snap.includesText("silence windows"));
    });

    it("offers a sign-out control", function () {
      assert.ok(snap.hasDesc("Sign out"));
    });

    it("keeps the sign-out control out of the tab bar's reach", function () {
      assert.strictEqual(snap.idsMatching(/^tab-/).length, 0);
    });

    it("returns to the More menu when dismissed", async function () {
      await back(1500);
      assert.ok(await isPresent(byId("screen-more")));
    });
  });

  // ===========================================================================
  describe("Module 18 — The collector screen before enrolling", function () {
    let snap;

    before(async function () {
      this.timeout(180000);
      await openMoreRow("more-settings", "screen-settings");
      await tap(byDesc("Monitor this phone"));
      await el(byIdMatch("screen-collector.*"));
      await driver.pause(2500);
      snap = await snapshot();
    });

    it("opens the collector screen from Settings", function () {
      assert.ok(snap.hasId("screen-collector") || snap.hasId("screen-collector-unsupported"));
    });

    it("finds the native collector module in this build", function () {
      assert.ok(
        snap.hasId("screen-collector"),
        "the APK reports no native collector — it was built without the Kotlin module",
      );
    });

    it("names the screen 'Monitor this phone' in the stack header", function () {
      assert.ok(snap.hasText("Monitor this phone"));
    });

    it("renders a card for this phone as a device", function () {
      assert.ok(snap.hasText("THIS PHONE AS A DEVICE"));
    });

    it("says nothing is being reported yet", function () {
      assert.ok(snap.includesText("not reporting anything yet"));
    });

    it("explains what enrolling will do", function () {
      assert.ok(snap.includesText("creates a device on your account"));
    });

    it("offers to enrol this phone", function () {
      assert.ok(snap.hasDesc("Monitor this phone"));
    });

    it("offers no stop control before anything has started", function () {
      assert.ok(!snap.hasDesc("Stop collecting"));
    });

    it("offers no forget control before anything is enrolled", function () {
      assert.ok(!snap.hasDesc("Forget this phone"));
    });

    it("offers no route to a device that does not exist yet", function () {
      assert.ok(!snap.hasDesc("Open this device"));
    });

    it("renders the measurement inventory", function () {
      assert.ok(snap.hasText("WHAT THIS PHONE CAN MEASURE"));
    });

    it("lists what an Android phone can measure", function () {
      assert.ok(snap.includesText("Memory, storage, battery level and temperature"));
    });

    it("names what no app can read since Android 8", function () {
      assert.ok(snap.includesText("CPU usage, load average"));
    });

    it("says those show as unavailable rather than as zero", function () {
      assert.ok(snap.includesText("unavailable rather than as zero"));
    });

    it("explains that the health score renormalises the weights", function () {
      assert.ok(snap.includesText("renormalised"));
    });

    it("shows no sampling-rate card before enrolment", function () {
      assert.ok(!snap.hasText("SAMPLING RATE"));
    });

    it("shows no battery-optimisation card before enrolment", function () {
      assert.ok(!snap.hasText("BATTERY OPTIMISATION"));
    });

    it("reports no push or sample statistics before enrolment", function () {
      assert.ok(!snap.hasText("Last push"));
      assert.ok(!snap.hasText("Buffered"));
    });
  });

  // ===========================================================================
  describe("Module 19 — Enrolling this phone as a real device", function () {
    // Everything from here to module 25 asserts against a device that is
    // genuinely reporting: the emulator, enrolled through the app's own flow,
    // pushing real Android readings. Nothing below is seeded.
    let snap;
    let device;
    let summary;

    before(async function () {
      this.timeout(300000);
      if (!(await isPresent(byId("screen-collector")))) {
        await openMoreRow("more-settings", "screen-settings");
        await tap(byDesc("Monitor this phone"));
        await el(byId("screen-collector"));
      }
      await tap(byDesc("Monitor this phone"));

      /*
       * Two waits, because enrolment and the handshake are two events.
       *
       * Redeeming the enrollment code creates the row; the agent's `hello`
       * frame is what fills in os, kernel, arch, cores and RAM. Reading the
       * row at the first moment /devices is non-empty gets a device with all
       * of those still null, and every assertion about them fails naming the
       * collector rather than the read that was simply too early.
       */
      device = await waitFor(
        async () => {
          const { body } = await api("GET", "/devices");
          const found = (Array.isArray(body) ? body : [])[0];
          return found && found.os && found.arch && found.agent_version ? found : null;
        },
        { timeout: 120000, interval: 2500, what: "the phone to enrol and hand over its system facts" },
      );
      enrolled = device;

      summary = await waitFor(
        async () => {
          const { body } = await api("GET", `/devices/${device.id}/summary`);
          return body && body.latest ? body : null;
        },
        { timeout: 150000, interval: 5000, what: "the phone's first fresh reading" },
      );

      await driver.pause(3000);
      snap = await snapshot();
    });

    it("creates a device on the account", function () {
      assert.ok(device.id);
    });

    it("names it after the phone itself", function () {
      assert.match(device.name, EXPECTED_DEVICE_NAME_RE);
    });

    it("records it as an Android platform", function () {
      assert.strictEqual(device.platform, "android");
    });

    it("records the OS the phone actually runs", function () {
      assert.strictEqual(device.os, "Android");
    });

    it("records the kernel the phone actually runs", function () {
      assert.ok(device.kernel_version && device.kernel_version.length > 0);
    });

    it("records the phone's real architecture", function () {
      assert.ok(device.arch && device.arch.length > 0);
    });

    it("records a real core count", function () {
      assert.ok(device.cpu_cores > 0);
    });

    it("records the phone's real total memory", function () {
      assert.ok(device.total_memory_bytes > 0);
    });

    it("records the agent version that enrolled", function () {
      assert.ok(device.agent_version);
    });

    it("reports readings inside the server's freshness window", function () {
      assert.ok(summary.latest !== null);
    });

    it("reports a real memory percentage", function () {
      assert.ok(typeof summary.latest.mem_percent === "number");
      assert.ok(summary.latest.mem_percent > 0 && summary.latest.mem_percent <= 100);
    });

    it("reports no CPU percentage, because Android cannot measure one", function () {
      // /proc/stat is denied to apps from API 26, and the collector does not
      // attempt it. Null on the wire, never a synthesised zero.
      assert.strictEqual(summary.latest.cpu_percent, null);
    });

    it("scores its health from the components it did report", function () {
      assert.notStrictEqual(summary.health.band, "unknown");
    });

    it("says on screen that it is collecting and connected", function () {
      assert.ok(snap.includesText("Collecting"));
    });

    it("names the device it enrolled as", function () {
      assert.ok(snap.hasText(device.name));
    });

    it("names the server it pushes to", function () {
      assert.ok(snap.hasText(APP_BASE));
    });

    it("reports its sample cadence", function () {
      assert.ok(snap.hasText("Samples"));
      assert.ok(snap.includesText("every 10s"));
    });

    it("reports its push cadence", function () {
      assert.ok(snap.hasText("Pushes"));
    });

    it("reports when it last sampled", function () {
      assert.ok(snap.hasText("Last sample"));
    });

    it("reports how deep its buffer is", function () {
      assert.ok(snap.hasText("Buffered"));
    });

    it("now offers to stop collecting", function () {
      assert.ok(snap.hasDesc("Stop collecting"));
    });

    it("now offers to forget this phone", function () {
      assert.ok(snap.hasDesc("Forget this phone"));
    });

    it("now offers a route to the device it created", function () {
      assert.ok(snap.hasDesc("Open this device"));
    });

    it("offers the one-second sampling upshift", function () {
      assert.ok(snap.hasDesc("Sample every second"));
    });

    it("renders the sampling-rate card once it is enrolled", function () {
      assert.ok(snap.hasText("SAMPLING RATE"));
    });

    it("explains that a 1s timer is the collector's largest battery cost", function () {
      assert.ok(snap.includesText("largest battery cost"));
    });

    it("renders the battery-optimisation card once it is enrolled", function () {
      assert.ok(snap.hasText("BATTERY OPTIMISATION"));
    });
  });

  // ===========================================================================
  describe("Module 20 — A fleet with one real device", function () {
    let snap;

    before(async function () {
      this.timeout(240000);
      await back(1500);
      await back(1500);
      await goTab("tab-devices", "screen-fleet");
      await el(byId(`device-card-${enrolled.id}`), 60000);
      await driver.pause(2500);
      snap = await snapshot();
    });

    it("replaces the empty state with a real device", function () {
      assert.ok(!snap.hasId("empty-state"));
    });

    it("renders a card for the enrolled device", function () {
      assert.ok(snap.hasId(`device-card-${enrolled.id}`));
    });

    it("keys the card by the device's uuid, never by its position", function () {
      assert.match(
        snap.idsMatching(/^device-card-/)[0],
        /^device-card-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
      );
    });

    it("renders exactly one card for one device", function () {
      assert.strictEqual(snap.idsMatching(/^device-card-/).length, 1);
    });

    it("names the device on the card", function () {
      assert.ok(snap.hasText(enrolled.name));
    });

    it("names the platform and architecture on the card", function () {
      assert.ok(snap.includesText("Android"));
      assert.ok(snap.includesText(enrolled.arch));
    });

    it("names the device for a screen reader", function () {
      assert.ok(snap.includesDesc(enrolled.name));
    });

    it("counts one device in the totals", function () {
      assert.ok(snap.hasText("Devices"));
      assert.ok(snap.nodes.some((n) => n.text === "1"));
    });

    it("counts it as online while it is pushing", function () {
      assert.ok(snap.hasText("Online"));
    });

    it("shows a health band for it", function () {
      const bands = ["Healthy", "Degraded", "Critical", "Unknown"];
      assert.ok(bands.some((b) => snap.hasText(b)));
    });

    it("shows a numeric health score", function () {
      assert.ok(snap.nodes.some((n) => /^\d{1,3}$/.test(n.text) && Number(n.text) <= 100));
    });

    it("labels a CPU reading on the card", function () {
      assert.ok(snap.hasText("CPU"));
    });

    it("renders that CPU reading as unavailable, not as a number", function () {
      // The hard rule at its most visible: a metric this platform cannot
      // measure is shown as unavailable rather than synthesised.
      assert.ok(snap.hasText("unavailable"));
    });

    it("labels a Memory reading on the card", function () {
      assert.ok(snap.hasText("Memory"));
    });

    it("renders memory as a real percentage", function () {
      assert.ok(snap.nodes.some((n) => /^\d{1,3}%$/.test(n.text)));
    });

    it("names the mount it reports disk usage for", function () {
      assert.ok(snap.includesText("Disk /"));
    });

    it("reports network throughput in bytes per second", function () {
      assert.ok(snap.nodes.some((n) => /B\/s$/.test(n.text)));
    });

    it("says when the device was last seen", function () {
      assert.ok(snap.includesText("last seen"));
    });

    it("reports the phone's total RAM on the card", function () {
      assert.ok(snap.includesText("RAM"));
    });

    it("offers to watch the device live", function () {
      assert.ok(snap.hasDesc("Watch live"));
    });
  });

  // ===========================================================================
  describe("Module 21 — One real device in detail", function () {
    let snap;

    before(async function () {
      this.timeout(240000);
      await ensureSignedIn();
      await el(byId(`device-card-${enrolled.id}`), 60000);
      await tap(byId(`device-card-${enrolled.id}`));
      await el(byId("screen-device"), 45000);
      await driver.pause(5000);
      snap = await fullSnapshot();
    });

    it("opens the device screen from its card", function () {
      assert.ok(snap.hasId("screen-device"));
    });

    it("names the device in the stack header", function () {
      assert.ok(snap.hasText(enrolled.name));
    });

    it("names the hostname, OS and architecture", function () {
      assert.ok(snap.includesText(enrolled.arch));
      assert.ok(snap.includesText("Android"));
    });

    it("names the agent version that is reporting", function () {
      assert.ok(snap.includesText(`agent ${enrolled.agent_version}`));
    });

    it("says when it was last seen", function () {
      assert.ok(snap.includesText("last seen"));
    });

    it("shows a health score", function () {
      assert.ok(snap.nodes.some((n) => /^\d{1,3}$/.test(n.text) && Number(n.text) <= 100));
    });

    it("shows a health band beside it", function () {
      const bands = ["Healthy", "Degraded", "Critical", "Unknown"];
      assert.ok(bands.some((b) => snap.hasText(b)));
    });

    it("shows the device status", function () {
      assert.ok(snap.hasText("Online") || snap.hasText("Offline"));
    });

    it("offers to watch it live", function () {
      assert.ok(snap.hasDesc("Watch live"));
    });

    it("offers its history", function () {
      assert.ok(snap.hasDesc("History"));
    });

    it("renders a card of per-machine destinations", function () {
      assert.ok(snap.hasText("FOR THIS MACHINE"));
    });

    it("scopes History to this machine", function () {
      assert.ok(snap.hasId("device-section-history"));
    });

    it("scopes Incidents to this machine", function () {
      assert.ok(snap.hasId("device-section-incidents"));
    });

    it("scopes Anomalies to this machine", function () {
      assert.ok(snap.hasId("device-section-anomalies"));
    });

    it("scopes Forecasts to this machine", function () {
      assert.ok(snap.hasId("device-section-forecasts"));
    });

    it("scopes Reports to this machine", function () {
      assert.ok(snap.hasId("device-section-reports"));
    });

    it("scopes Alert rules to this machine", function () {
      assert.ok(snap.hasId("device-section-alertrules"));
    });

    it("offers exactly six per-machine destinations", function () {
      assert.strictEqual(snap.idsMatching(/^device-section-/).length, 6);
    });

    it("keys those destinations by route rather than by position", function () {
      assert.deepStrictEqual(snap.idsMatching(/^device-section-/).sort(), [
        "device-section-alertrules",
        "device-section-anomalies",
        "device-section-forecasts",
        "device-section-history",
        "device-section-incidents",
        "device-section-reports",
      ]);
    });

    it("renders a health breakdown", function () {
      assert.ok(snap.hasText("HEALTH BREAKDOWN"));
    });

    it("scores memory in the breakdown", function () {
      assert.ok(snap.hasText("Memory"));
    });

    it("scores disk capacity in the breakdown", function () {
      assert.ok(snap.hasText("Disk capacity"));
    });

    it("scores swap in the breakdown", function () {
      assert.ok(snap.hasText("Swap"));
    });

    it("scores packet loss in the breakdown", function () {
      assert.ok(snap.hasText("Packet loss"));
    });

    it("names what this platform cannot measure rather than hiding it", function () {
      assert.ok(snap.includesText("Not measurable on this platform"));
    });

    it("says CPU is among what it cannot measure", function () {
      assert.ok(snap.includesText("cpu"));
    });

    it("says the excluded components were excluded, not zeroed", function () {
      assert.ok(snap.includesText("Excluded from the score"));
    });

    it("renders the current readings", function () {
      assert.ok(snap.hasText("CURRENT READINGS"));
    });

    it("reports memory as a percentage", function () {
      assert.ok(snap.nodes.some((n) => /^\d{1,3}\.\d%$/.test(n.text)));
    });

    it("reports the battery level", function () {
      assert.ok(snap.hasText("Battery"));
    });

    it("labels temperature as the battery thermistor, not as a CPU package", function () {
      // Android exposes no CPU package temperature to an app, so calling this
      // "Temperature" would claim a measurement nobody took.
      assert.ok(snap.hasText("Battery temp"));
    });

    it("reports network in and out", function () {
      assert.ok(snap.hasText("Net in"));
      assert.ok(snap.hasText("Net out"));
    });

    it("reports round-trip latency", function () {
      assert.ok(snap.hasText("RTT"));
    });

    it("reports packet loss", function () {
      assert.ok(snap.hasText("Packet loss"));
    });

    it("offers no CPU reading in the grid for an Android device", function () {
      assert.ok(!snap.hasText("CPU"));
    });

    it("offers no load average for an Android device", function () {
      assert.ok(!snap.hasText("Load (1m)"));
    });

    it("offers no per-process listing for an Android device", function () {
      assert.ok(!snap.hasText("Processes"));
    });

    it("renders the mounts it reported", function () {
      assert.ok(snap.hasText("DISKS"));
    });

    it("names a real mount point", function () {
      assert.ok(snap.nodes.some((n) => /^\//.test(n.text)));
    });

    it("reports that mount's used and total bytes", function () {
      assert.ok(snap.includesText(" of "));
    });

    it("offers a way to remove the device", function () {
      assert.ok(snap.hasText("REMOVE THIS DEVICE"));
      assert.ok(snap.hasDesc("Remove device"));
    });

    it("explains what removal does before it is tapped", function () {
      assert.ok(snap.includesText("Revokes its agent token"));
    });

    it("says the metrics already collected are kept", function () {
      assert.ok(snap.includesText("metrics already"));
    });
  });

  // ===========================================================================
  describe("Module 22 — History for a real device", function () {
    const seen = {};

    before(async function () {
      this.timeout(240000);
      await tap(byId("device-section-history"));
      await el(byId("screen-history"), 45000);
      await driver.pause(6000);
      seen.hour = await snapshot();

      await tap(byId("segmented-6h"));
      await driver.pause(5000);
      seen.sixHours = await snapshot();

      await tap(byId("segmented-24h"));
      await driver.pause(5000);
      seen.day = await snapshot();

      await tap(byId("segmented-1h"));
      await driver.pause(5000);
      seen.backToHour = await snapshot();
    });

    it("opens the history screen from the device", function () {
      assert.ok(seen.hour.hasId("screen-history"));
    });

    it("scopes the header title to this device", function () {
      assert.ok(seen.hour.includesText(`History · ${enrolled.name}`));
    });

    it("offers a one-hour window", function () {
      assert.ok(seen.hour.hasId("segmented-1h"));
    });

    it("offers a six-hour window", function () {
      assert.ok(seen.hour.hasId("segmented-6h"));
    });

    it("offers a 24-hour window", function () {
      assert.ok(seen.hour.hasId("segmented-24h"));
    });

    it("offers a seven-day window", function () {
      assert.ok(seen.hour.hasId("segmented-7d"));
    });

    it("offers a 30-day window", function () {
      assert.ok(seen.hour.hasId("segmented-30d"));
    });

    it("offers exactly five windows", function () {
      assert.strictEqual(seen.hour.idsMatching(/^segmented-/).length, 5);
    });

    it("says which rollup tier the chart was averaged from", function () {
      assert.ok(seen.hour.includesText("Averaged from the"));
    });

    it("names the bucket width it aggregated into", function () {
      assert.ok(seen.hour.includesText("buckets."));
    });

    it("charts memory", function () {
      assert.ok(seen.hour.hasText("MEMORY"));
    });

    it("names the series on the memory chart", function () {
      assert.ok(seen.hour.hasText("memory"));
      assert.ok(seen.hour.hasText("swap"));
    });

    it("charts disk usage", function () {
      assert.ok(seen.hour.hasText("DISK USAGE"));
    });

    it("names the mount on the disk chart", function () {
      assert.ok(seen.hour.nodes.some((n) => /^\//.test(n.text)));
    });

    it("charts network throughput", function () {
      assert.ok(seen.hour.hasText("NETWORK IN"));
    });

    it("scales the axes to real values", function () {
      assert.ok(seen.hour.hasText("100%"));
      assert.ok(seen.hour.hasText("0%"));
    });

    it("charts no CPU for a device that cannot measure it", function () {
      assert.ok(!seen.hour.hasText("CPU"));
    });

    it("keeps the screen when a six-hour window is chosen", function () {
      assert.ok(seen.sixHours.hasId("screen-history"));
    });

    it("keeps the screen when a 24-hour window is chosen", function () {
      assert.ok(seen.day.hasId("screen-history"));
    });

    it("re-states the rollup tier for a wider window", function () {
      assert.ok(seen.day.includesText("Averaged from the"));
    });

    it("returns to the one-hour window when it is chosen again", function () {
      assert.ok(seen.backToHour.hasId("screen-history"));
    });

    it("returns to the device when dismissed", async function () {
      await back(2500);
      assert.ok(await isPresent(byId("screen-device")));
    });
  });

  // ===========================================================================
  describe("Module 23 — Live monitoring a real device", function () {
    let snap;

    before(async function () {
      this.timeout(240000);
      await tap(byDesc("Watch live"));
      await el(byId("screen-live"), 45000);
      // Long enough for the agent to be upshifted to 1s and for samples to
      // arrive over the viewer socket — the whole point of this screen.
      await driver.pause(20000);
      snap = await snapshot();
    });

    it("opens the live screen from the device", function () {
      assert.ok(snap.hasId("screen-live"));
    });

    it("names the device in the stack header", function () {
      assert.ok(snap.hasText(enrolled.name));
    });

    it("says it streams at one second while the screen is open", function () {
      assert.ok(snap.includesText("Streaming at 1s while this screen is open."));
    });

    it("shows the device status", function () {
      assert.ok(snap.hasText("Online") || snap.hasText("Offline"));
    });

    it("charts memory", function () {
      assert.ok(snap.hasText("MEMORY"));
    });

    it("names the used and swap series", function () {
      assert.ok(snap.hasText("used"));
      assert.ok(snap.hasText("swap"));
    });

    it("reports a live memory percentage", function () {
      assert.ok(snap.nodes.some((n) => /^\d{1,3}%$/.test(n.text)));
    });

    it("charts network", function () {
      assert.ok(snap.hasText("NETWORK"));
    });

    it("names the network entity for what it actually counts", function () {
      // TrafficStats counts every interface together, so the entity is
      // `device-total` rather than a NIC name it cannot honestly claim.
      assert.ok(snap.includesText("device-total"));
    });

    it("charts latency", function () {
      assert.ok(snap.hasText("LATENCY"));
    });

    it("names the target it probed", function () {
      assert.ok(snap.nodes.some((n) => /:\d+$/.test(n.text)));
    });

    it("charts disk usage", function () {
      assert.ok(snap.hasText("DISK USAGE"));
    });

    it("names the mount it charts", function () {
      assert.ok(snap.nodes.some((n) => /^\//.test(n.text)));
    });

    it("charts no CPU for a device that cannot measure it", function () {
      assert.ok(!snap.hasText("CPU"));
    });

    it("lists no top processes for a device that cannot enumerate them", function () {
      // /proc/self/stat would yield exactly one entry — this collector — and
      // the UI renders that list as "top N on this machine". An empty list is
      // the honest answer.
      assert.ok(!snap.hasText("TOP PROCESSES"));
    });

    it("charts no disk I/O for a device that cannot measure it", function () {
      assert.ok(!snap.hasText("DISK I/O"));
    });

    it("returns to the device when dismissed", async function () {
      await back(3000);
      assert.ok(await isPresent(byId("screen-device")));
    });
  });

  // ===========================================================================
  describe("Module 24 — The same screens scoped to one device", function () {
    const seen = {};

    /**
     * `settled` is polled instead of slept through, for a screen whose content
     * does not exist yet when the screen does. The flat 3s is enough wherever
     * the data is already in the database by the time the row is tapped; the
     * Forecasts screen is the exception, because the forecast worker writes it
     * on a 120s tick and the screen renders neither its rows nor its empty
     * state until the first response lands.
     */
    async function openSection(route, screen, settled = null) {
      if (!(await isPresent(byId("screen-device")))) {
        await ensureSignedIn();
        await tap(byId(`device-card-${enrolled.id}`));
        await el(byId("screen-device"), 45000);
      }
      await tap(byId(`device-section-${route}`));
      await el(byId(screen), 45000);
      await driver.pause(3000);
      let snap = await snapshot();
      if (settled) {
        const deadline = Date.now() + 30000;
        while (!settled(snap) && Date.now() < deadline) {
          await driver.pause(2000);
          snap = await snapshot();
        }
      }
      await back(2500);
      return snap;
    }

    before(async function () {
      this.timeout(300000);
      seen.incidents = await openSection("incidents", "screen-incidents");
      seen.anomalies = await openSection("anomalies", "screen-anomalies");
      // Either outcome is a settled screen; what must not be snapshotted is
      // the moment before the first response, when the screen renders its
      // headings and nothing else.
      seen.forecasts = await openSection(
        "forecasts",
        "screen-forecasts",
        (snap) => snap.hasId("empty-state") || snap.includesText("%/day"),
      );
      seen.reports = await openSection("reports", "screen-reports");
      seen.rules = await openSection("alertrules", "screen-alert-rules");
    });

    it("opens Incidents scoped to the device", function () {
      assert.ok(seen.incidents.hasId("screen-incidents"));
    });

    it("scopes the incidents header to the device's name", function () {
      assert.ok(seen.incidents.includesText(`Incidents · ${enrolled.name}`));
    });

    it("says on screen that it is showing one device only", function () {
      assert.ok(seen.incidents.includesText(`Showing ${enrolled.name} only`));
    });

    it("says where the fleet-wide list lives instead", function () {
      assert.ok(seen.incidents.includesText("Open this from More for every device."));
    });

    it("opens Anomalies scoped to the device", function () {
      assert.ok(seen.anomalies.hasId("screen-anomalies"));
    });

    it("scopes the anomalies header to the device's name", function () {
      assert.ok(seen.anomalies.includesText(`Anomalies · ${enrolled.name}`));
    });

    it("keeps the anomaly filters on the scoped screen", function () {
      assert.ok(seen.anomalies.hasId("segmented-firing"));
    });

    it("opens Forecasts scoped to the device", function () {
      assert.ok(seen.forecasts.hasId("screen-forecasts"));
    });

    it("scopes the forecasts header to the device's name", function () {
      assert.ok(seen.forecasts.includesText(`Forecasts · ${enrolled.name}`));
    });

    it("never presents a freshly enrolled device's projection as settled", function () {
      /*
       * This used to assert the empty state outright, citing MIN_POINTS_TREND
       * — 24 buckets — as the reason a device minutes old could not have a
       * projection. That is the wrong constant. It governs the Holt-Winters
       * trend fit behind the "24h forecasts" charts, while the empty state
       * here is driven by the *exhaustion* rows, whose floor is
       * MIN_POINTS_EXHAUSTION = 8; and the worker plans its bucket window from
       * when the device first reported, so — in analysis/forecast.py's own
       * words — "a four-minute-old device gets 10s buckets and enough points
       * to fit". A freshly enrolled device legitimately has rows here, and
       * whether it does depends on where the worker's 120s tick fell. The
       * assertion passed on one CI run and failed on the next for exactly
       * that reason.
       *
       * What is invariant is not the absence of a projection but the refusal
       * to dress one up: forecast_confidence() returns "provisional" below six
       * hours of history, so anything this device manages to fit must say so.
       */
      if (seen.forecasts.hasId("empty-state")) return;

      assert.ok(
        seen.forecasts.includesText("%/day"),
        "the screen showed neither an empty state nor a projection",
      );
      if (seen.forecasts.hasText("24h forecasts")) {
        assert.ok(
          seen.forecasts.includesText("Provisional"),
          "a trend forecast for a device minutes old must be labelled provisional",
        );
      }
    });

    it("opens Reports scoped to the device", function () {
      assert.ok(seen.reports.hasId("screen-reports"));
    });

    it("scopes the reports header to the device's name", function () {
      assert.ok(seen.reports.includesText(`Reports · ${enrolled.name}`));
    });

    it("keeps the report periods on the scoped screen", function () {
      assert.ok(seen.reports.hasId("segmented-7"));
    });

    it("opens Alert rules scoped to the device", function () {
      assert.ok(seen.rules.hasId("screen-alert-rules"));
    });

    it("scopes the rules header to the device's name", function () {
      assert.ok(seen.rules.includesText(`Alert rules · ${enrolled.name}`));
    });

    it("explains that the fleet-wide rules apply here too", function () {
      assert.ok(seen.rules.includesText("and the fleet-wide ones that also apply to it"));
    });

    it("still lists the built-in fleet-wide rules under a device scope", function () {
      assert.ok(seen.rules.idsMatching(/^rule-[0-9a-f-]{36}$/).length >= 4);
    });

    it("returns to the device screen after each section", async function () {
      assert.ok(await isPresent(byId("screen-device")));
    });
  });

  // ===========================================================================
  describe("Module 25 — Removing the device", function () {
    const seen = {};

    before(async function () {
      this.timeout(300000);
      if (!(await isPresent(byId("screen-device")))) {
        await ensureSignedIn();
        await tap(byId(`device-card-${enrolled.id}`));
        await el(byId("screen-device"), 45000);
      }
      await driver.pause(2500);

      await tap(scrollToDesc("Remove device"));
      await driver.pause(1800);
      seen.confirm = await snapshot();

      await tapDialog("Cancel");
      seen.cancelled = await snapshot();

      await tap(scrollToDesc("Remove device"));
      await driver.pause(1800);
      await tapDialog("Remove");

      await waitFor(
        async () => {
          const { body } = await api("GET", "/devices");
          return Array.isArray(body) && body.length === 0;
        },
        { timeout: 90000, interval: 2500, what: "the device to be removed" },
      );

      await el(byId("screen-fleet"), 45000);
      await driver.pause(4000);
      seen.fleet = await snapshot();
    });

    it("asks for confirmation before removing anything", function () {
      assert.ok(seen.confirm.includesText(`Remove ${enrolled.name}?`));
    });

    it("says the agent token is revoked immediately", function () {
      assert.ok(seen.confirm.includesText("agent token is revoked immediately"));
    });

    it("says the history already collected is kept", function () {
      assert.ok(seen.confirm.includesText("already collected is kept"));
    });

    it("warns that this is the phone being held", function () {
      assert.ok(seen.confirm.includesText("This is the phone you are holding"));
    });

    it("offers a way out of the confirmation", function () {
      // Case-insensitively: AlertDialog upper-cases its button labels, so the
      // node reads CANCEL even though Alert.alert was given "Cancel".
      assert.ok(/cancel/i.test(seen.confirm.xml));
    });

    it("leaves the device in place when the confirmation is cancelled", function () {
      assert.ok(seen.cancelled.hasId("screen-device"));
    });

    it("removes the device when the confirmation is accepted", async function () {
      const { body } = await api("GET", "/devices");
      assert.deepStrictEqual(body, []);
    });

    it("returns to the fleet once the device is gone", function () {
      assert.ok(seen.fleet.hasId("screen-fleet"));
    });

    it("takes the device's card off the fleet", function () {
      assert.deepStrictEqual(seen.fleet.idsMatching(/^device-card-/), []);
    });

    it("brings the empty state back", function () {
      assert.ok(seen.fleet.hasId("empty-state"));
    });

    it("returns every total to zero", function () {
      assert.strictEqual(seen.fleet.nodes.filter((n) => n.text === "0").length, 8);
    });

    it("makes the phone forget its agent token as well", async function () {
      // Removing the device that IS this phone tears its collector down too:
      // without that the server revokes the token while the foreground service
      // keeps sampling and reconnecting with a credential nothing will accept.
      await openMoreRow("more-settings", "screen-settings");
      const snap = await snapshot();
      assert.ok(snap.hasDesc("Monitor this phone"));
    });

    it("no longer claims the phone is reporting its own metrics", async function () {
      const snap = await snapshot();
      assert.ok(!snap.includesText("This phone is reporting its own metrics."));
    });
  });

  // ===========================================================================
  describe("Module 26 — Sign-out", function () {
    const seen = {};

    before(async function () {
      this.timeout(240000);
      await ensureSignedIn();
      await openMoreRow("more-settings", "screen-settings");
      await tap(byDesc("Sign out"));
      await el(byId("auth-email"), 45000);
      await driver.pause(1500);
      seen.signedOut = await snapshot();

      await attemptSignIn(EMAIL, PASSWORD);
      await el(byId("screen-fleet"), 45000);
      await driver.pause(1500);
      seen.backIn = await snapshot();

      await openMoreRow("more-settings", "screen-settings");
      await tap(byDesc("Sign out"));
      await el(byId("auth-email"), 45000);
      await driver.pause(1500);
      seen.signedOutAgain = await snapshot();
    });

    it("returns to the sign-in form", function () {
      assert.ok(seen.signedOut.hasId("auth-email"));
    });

    it("unmounts the bottom tab bar", function () {
      assert.strictEqual(seen.signedOut.idsMatching(/^tab-/).length, 0);
    });

    it("unmounts every authenticated screen root", function () {
      assert.deepStrictEqual(seen.signedOut.screens(), []);
    });

    it("leaves no fleet behind the sign-in form", function () {
      assert.ok(!seen.signedOut.hasId("screen-fleet"));
    });

    it("clears the email field", function () {
      assert.strictEqual(seen.signedOut.textOf("auth-email"), "you@example.com");
    });

    it("leaves the password field empty", function () {
      assert.strictEqual(seen.signedOut.textOf("auth-password"), "");
    });

    it("raises no error note on a clean sign-out", function () {
      assert.ok(!seen.signedOut.hasId("error-note"));
    });

    it("still names the server this build talks to", function () {
      assert.ok(seen.signedOut.hasText(APP_BASE));
    });

    it("signs back in with the same credentials", function () {
      assert.ok(seen.backIn.hasId("screen-fleet"));
    });

    it("lands back on the Devices tab", function () {
      assert.strictEqual(seen.backIn.textOf("screen-fleet-title"), "Devices");
    });

    it("signs out a second time just as cleanly", function () {
      assert.ok(seen.signedOutAgain.hasId("auth-email"));
    });

    it("leaves no session behind after the second sign-out", function () {
      assert.deepStrictEqual(seen.signedOutAgain.screens(), []);
    });
  });

  // ===========================================================================
  describe("Module 27 — Untrusted input at the sign-in form", function () {
    const results = {};

    before(async function () {
      this.timeout(300000);
      await ensureSignedOut();
      results.script = await attemptSignIn("<script>alert(1)</script>@example.com", PASSWORD);
      results.sqlComment = await attemptSignIn(`${EMAIL}'--`, PASSWORD);
      results.sqlUnion = await attemptSignIn(EMAIL, "' UNION SELECT 1,2,3--");
      results.traversal = await attemptSignIn("../../etc/passwd", PASSWORD);
      results.template = await attemptSignIn(EMAIL, "${7*7}");
      results.longEmail = await attemptSignIn(`${"a".repeat(500)}@example.com`, PASSWORD);
      results.longPassword = await attemptSignIn(EMAIL, "z".repeat(500));
      results.emoji = await attemptSignIn("🙂🙃@example.com", PASSWORD);
      results.after = await snapshot();
    });

    it("refuses a script tag in the email", function () {
      assert.ok(results.script.hasId("error-note"));
    });

    it("never signs anybody in on a script payload", function () {
      assert.ok(!results.script.hasId("screen-fleet"));
    });

    it("refuses an SQL comment appended to a real address", function () {
      assert.ok(results.sqlComment.hasId("error-note"));
    });

    it("does not let an SQL comment truncate the credential check", function () {
      assert.ok(!results.sqlComment.hasId("screen-fleet"));
    });

    it("refuses an SQL union in the password", function () {
      assert.ok(results.sqlUnion.includesText("Invalid email or password"));
    });

    it("refuses a path-traversal string as an email", function () {
      assert.ok(results.traversal.hasId("error-note"));
    });

    it("treats a template-injection payload as a password, not as an expression", function () {
      assert.ok(results.template.includesText("Invalid email or password"));
      assert.ok(!results.template.includesText("49"));
    });

    it("refuses a 500-character email without crashing", function () {
      assert.ok(results.longEmail.hasId("error-note"));
    });

    it("refuses a 500-character password without crashing", function () {
      // Refused on length before the credential check ever runs — the API caps
      // the field at 128 characters — so the message is a validation one and
      // asserting the credential wording here would be asserting the wrong
      // defence.
      assert.ok(results.longPassword.hasId("error-note"));
      assert.ok(!results.longPassword.hasId("screen-fleet"));
    });

    it("refuses emoji in an email address without crashing", function () {
      assert.ok(results.emoji.hasId("error-note"));
    });

    it("renders every refusal inline rather than in a modal", function () {
      // An error on a monitoring screen must not hide what is behind it, so
      // the form stays readable underneath the strip.
      assert.ok(results.after.hasId("auth-email"));
      assert.ok(results.after.hasId("auth-submit"));
    });

    it("never navigates away from the sign-in form", function () {
      assert.deepStrictEqual(results.after.screens(), []);
    });

    it("is still signed out after every payload", function () {
      assert.ok(!results.after.hasId("tab-devices"));
    });

    it("leaves the app responsive to a real sign-in afterwards", async function () {
      this.timeout(120000);
      const snap = await attemptSignIn(EMAIL, PASSWORD);
      assert.ok(snap.hasId("screen-fleet"));
    });
  });

  // ===========================================================================
  describe("Module 28 — Accessibility of the controls", function () {
    const seen = {};

    before(async function () {
      this.timeout(240000);
      await ensureSignedIn();
      await goTab("tab-more", "screen-more");
      seen.more = await snapshot();
      await openMoreRow("more-settings", "screen-settings");
      seen.settings = await snapshot();
      await back(1500);
      await ensureSignedOut();
      await driver.pause(1200);
      seen.auth = await snapshot();
    });

    it("labels the email field for a screen reader", function () {
      assert.strictEqual(seen.auth.descOf("auth-email"), "Email");
    });

    it("labels the password field for a screen reader", function () {
      assert.strictEqual(seen.auth.descOf("auth-password"), "Password");
    });

    it("gives the submit button its title as its accessible name", function () {
      assert.strictEqual(seen.auth.descOf("auth-submit"), "Sign in");
    });

    it("gives the mode toggle its title as its accessible name", function () {
      assert.strictEqual(seen.auth.descOf("auth-toggle-mode"), "No account yet? Sign up");
    });

    it("names the password-reveal control by what it does", function () {
      assert.ok(seen.auth.hasDesc("Show password") || seen.auth.hasDesc("Hide password"));
    });

    it("flags the password field as a password field for the platform", function () {
      assert.strictEqual(seen.auth.byId("auth-password").password, true);
    });

    it("leaves no button on the sign-in screen unnamed", function () {
      assert.deepStrictEqual(seen.auth.buttons().filter((b) => b.desc === ""), []);
    });

    it("labels every text input on the sign-in screen", function () {
      assert.deepStrictEqual(seen.auth.editTexts().filter((f) => f.desc === ""), []);
    });

    it("names the Devices tab beyond its glyph", function () {
      assert.ok(/Devices/.test(seen.more.descOf("tab-devices")));
    });

    it("names the Alerts tab beyond its glyph", function () {
      assert.ok(/Alerts/.test(seen.more.descOf("tab-alerts")));
    });

    it("names the More tab beyond its glyph", function () {
      assert.ok(/More/.test(seen.more.descOf("tab-more")));
    });

    it("names every More row by its visible title", function () {
      for (const id of seen.more.idsMatching(/^more-/)) {
        assert.notStrictEqual(seen.more.descOf(id), "", id);
      }
    });

    it("names the sign-out control", function () {
      assert.ok(seen.settings.hasDesc("Sign out"));
    });

    it("names the stack's up-control", function () {
      assert.ok(seen.settings.hasDesc("Navigate up"));
    });

    it("leaves no button on the settings screen unnamed", function () {
      assert.deepStrictEqual(seen.settings.buttons().filter((b) => b.desc === ""), []);
    });

    it("keeps every accessible name in step with the visible copy", function () {
      // Field/PasswordField take their name from the visible label and Button
      // from its title, so a name that has drifted from the copy is a bug in
      // one of them rather than a missing label.
      for (const row of seen.more.idsMatching(/^more-/)) {
        assert.ok(seen.more.hasText(seen.more.descOf(row)), row);
      }
    });
  });
});
