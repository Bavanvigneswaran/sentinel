#!/usr/bin/env node
/**
 * Is the Selenium setup actually working?
 *
 *   npm run smoke
 *
 * Deliberately outside tests/ so it never runs as part of the suite — this
 * answers "is my machine set up", not "does the app behave". Run it once, and
 * again whenever the suite starts failing in a way that smells like the driver
 * or the stack rather than the app.
 *
 * No browser install is needed, which is worth stating because it looks like it
 * should be: this machine has no Chrome, Chromium or Firefox at all, only
 * Safari, and this still passes. selenium-webdriver 4.6+ ships Selenium
 * Manager, which resolves *and downloads* both the driver and a matching
 * Chrome for Testing into a cache on first run. So there is no chromedriver
 * step and no "install Chrome first" step; the first run is just slow.
 */

const assert = require("node:assert");
const { Builder, By, until } = require("selenium-webdriver");
const chrome = require("selenium-webdriver/chrome");

const BASE = process.env.SENTINEL_URL || "http://localhost:8000";
const EMAIL = process.env.SENTINEL_E2E_EMAIL || "e2e@example.com";
const PASSWORD = process.env.SENTINEL_E2E_PASSWORD || "e2e-Sentinel-Test-2026";
const HEADLESS = process.env.HEADLESS !== "0";

const byTestId = (id) => By.css(`[data-testid="${id}"]`);

async function main() {
  const options = new chrome.Options();
  if (HEADLESS) options.addArguments("--headless=new");
  options.addArguments("--no-sandbox", "--window-size=1280,900");

  const driver = await new Builder().forBrowser("chrome").setChromeOptions(options).build();

  try {
    await driver.get(`${BASE}/login`);
    await driver.wait(until.elementLocated(byTestId("login-form")), 20000);
    console.log("  ✓ login page served and rendered");

    await driver.findElement(byTestId("login-email")).sendKeys(EMAIL);
    await driver.findElement(byTestId("login-password")).sendKeys(PASSWORD);
    await driver.findElement(byTestId("login-submit")).click();

    // Wait on the dashboard's own hook rather than on a URL change: the router
    // navigates before the fleet request resolves, so a URL assertion can pass
    // while the page is still the bootstrap loader.
    await driver.wait(until.elementLocated(byTestId("page-title")), 20000);
    const email = await driver.findElement(byTestId("current-user-email")).getText();
    assert.strictEqual(email, EMAIL, `header showed ${email}`);
    console.log("  ✓ signed in; header shows the right account");

    await driver.wait(until.elementLocated(byTestId("nav-alerts")), 10000);
    await driver.findElement(byTestId("nav-alerts")).click();
    await driver.wait(until.elementLocated(byTestId("page-title")), 15000);
    console.log("  ✓ nav-alerts navigated");

    await driver.findElement(byTestId("sign-out")).click();
    await driver.wait(until.elementLocated(byTestId("login-form")), 15000);
    console.log("  ✓ sign-out returned to the login form");

    console.log("\nSelenium setup is working.\n");
  } finally {
    await driver.quit();
  }
}

main().catch((err) => {
  console.error("\nSmoke failed:", err.message);
  console.error(
    `\nCheck, in order: the stack is up at ${BASE} (make e2e-serve — NOT make\n` +
      "serve, which rate-limits and drops the refresh cookie over http), and the\n" +
      "account is seeded (make e2e-db). Set HEADLESS=0 to watch it.",
  );
  process.exit(1);
});
