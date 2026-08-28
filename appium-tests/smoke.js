#!/usr/bin/env node
/**
 * Is the Appium setup actually working?
 *
 *   npm run smoke
 *
 * Deliberately outside tests/ so it never runs as part of the suite — this
 * answers "is my machine set up", not "does the app behave". Run it once after
 * installing Appium, and again whenever the suite starts failing in a way that
 * smells like the emulator or the driver rather than the app.
 *
 * It signs in and walks to the More tab, which exercises the three things that
 * break independently: the driver can reach the emulator, the APK's baked-in
 * EXPO_PUBLIC_API_URL can reach the backend, and the testID hooks resolve as
 * resource-id.
 */

const assert = require("node:assert");
const { execSync } = require("node:child_process");
const path = require("node:path");
const { remote } = require("webdriverio");

const APK = path.resolve(__dirname, "../frontend/mobile/build/app-appium.apk");
const PKG = "com.sentinel.viewer";
const EMAIL = process.env.SENTINEL_E2E_EMAIL || "e2e@example.com";
const PASSWORD = process.env.SENTINEL_E2E_PASSWORD || "e2e-Sentinel-Test-2026";

const capabilities = {
  platformName: "Android",
  "appium:automationName": "UiAutomator2",
  "appium:deviceName": "Android Emulator",
  "appium:appPackage": PKG,
  "appium:appActivity": `${PKG}.MainActivity`,
  "appium:newCommandTimeout": 120,
};

/** `~id` is content-desc; a bare resource-id needs a UiSelector. */
const byId = (id) => `android=new UiSelector().resourceId("${id}")`;

async function main() {
  // The app deliberately does not persist its access token, but the platform
  // cookie jar replays the HttpOnly refresh cookie — so a cold relaunch lands
  // straight on the fleet screen (a Phase 10a property, verified there with
  // `am force-stop`). Clearing data is what actually gets us back to a signed
  // -out state, and without it this script asserts nothing about signing in.
  execSync(`adb shell pm clear ${PKG}`, { stdio: "ignore" });

  const driver = await remote({
    hostname: "127.0.0.1",
    port: 4723,
    logLevel: "error",
    capabilities,
  });

  try {
    const email = await driver.$(byId("auth-email"));
    await email.waitForDisplayed({ timeout: 30000 });
    await email.setValue(EMAIL);
    await (await driver.$(byId("auth-password"))).setValue(PASSWORD);
    await (await driver.$(byId("auth-submit"))).click();
    console.log("  ✓ signed in through the app's own form");

    const fleet = await driver.$(byId("screen-fleet"));
    await fleet.waitForDisplayed({ timeout: 30000 });
    assert.ok(await fleet.isDisplayed(), "fleet screen did not appear");
    console.log("  ✓ screen-fleet resolved (the hook every screen shares)");

    await (await driver.$(byId("tab-more"))).click();
    const more = await driver.$(byId("screen-more"));
    await more.waitForDisplayed({ timeout: 15000 });
    console.log("  ✓ tab-more navigated to screen-more");

    for (const row of ["more-settings", "more-reports", "more-anomalies"]) {
      assert.ok(await (await driver.$(byId(row))).isDisplayed(), `${row} missing`);
    }
    console.log("  ✓ More-tab rows resolved");
    console.log("\nAppium setup is working.\n");
  } finally {
    await driver.deleteSession();
  }
}

main().catch((err) => {
  console.error("\nSmoke failed:", err.message);
  console.error(
    "\nCheck, in order: `appium` is running on :4723, `adb devices` lists an\n" +
      "emulator, the APK exists (make mobile-apk-test), and the backend the APK\n" +
      "was BUILT against is up — EXPO_PUBLIC_API_URL is baked in at build time.",
  );
  process.exit(1);
});
