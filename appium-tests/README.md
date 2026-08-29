# Appium E2E suite

Drives the Sentinel Android app on an emulator. `docs/TESTING.md` is the
authority for the stack under test; this file is the Android-specific half.

## Setup

Three things, none of which the repo can provide for you:

```bash
npm install -g appium
appium driver install uiautomator2
appium                                  # leave running on :4723
```

An emulator must be booted (`emulator -avd <name>` or Android Studio), and
`adb devices` must list it. Verified against Appium 3.7.0 and
uiautomator2 8.5.0.

Then check the machine before blaming the app:

```bash
npm install
npm run smoke
```

`smoke.js` signs in and walks to the More tab. It lives outside `tests/` so it
never runs as part of the suite — it answers "is my machine set up", not "does
the app behave", and it exercises the three things that break independently:
the driver reaching the emulator, the APK's baked-in `EXPO_PUBLIC_API_URL`
reaching the backend, and the `testID` hooks resolving as `resource-id`.

## The APK

```bash
EXPO_PUBLIC_API_URL=http://10.0.2.2:8000 make mobile-apk-test
```

Two things about that command are load-bearing:

**`10.0.2.2`, not `localhost`.** That is the address an Android emulator reaches
the host machine on; `localhost` inside the emulator is the emulator. And it is
read at *build* time — `EXPO_PUBLIC_*` is inlined into the bundle — so setting
it afterwards does nothing, and a wrong value means an app that installs, opens,
and cannot sign in.

**It is a release build, and it has to be.** `assembleDebug` produces a
dev-client shell: `expo-dev-client` makes `DevLauncherActivity` the launcher
activity and no JS bundle is embedded at all, so the APK boots into "waiting for
Metro" and there is no app for a driver to drive. That was discovered by
installing one — the build succeeds, the APK is 202 MB, and it contains no
`index.android.bundle`. The test build is 82 MB and does.

It is signed with a throwaway keystore generated on first use, which is *not*
the thing `plugins/withReleaseSigning.js` refuses to fall back to — that guard
is about React Native's public debug key, which everyone has. This key exists
only on your machine and signs one artifact that is never published.

It carries the same `applicationId` as the real app, so a device holding a
differently-signed build has to uninstall first:

```bash
adb uninstall com.sentinel.viewer
adb install frontend/mobile/build/app-appium.apk
```

## Capabilities

```js
{
  platformName: "Android",
  "appium:automationName": "UiAutomator2",
  "appium:deviceName": "Android Emulator",
  "appium:appPackage": "com.sentinel.viewer",
  "appium:appActivity": "com.sentinel.viewer.MainActivity",
  "appium:newCommandTimeout": 600,
}
```

**There is deliberately no `appium:app`, and the APK must already be
installed.** The suite launches a build that is on the device rather than
handing one to the driver, which is what the `adb install` in Setup above is
for. This block used to list an `appium:app` the suite has never sent, and the
cost of believing it was a CI job that built the APK, never installed it, and
failed at session creation with "Either provide 'app' option to install
com.sentinel.viewer or consider setting 'noReset' to 'true'" — before a single
case ran.

## Locators

Two families, both verified against a real `uiautomator dump` on an emulator
rather than assumed:

**`testID` → `resource-id`.** Note it is the bare string — `auth-email`, not
`com.sentinel.viewer:id/auth-email` — so `~` accessibility-id syntax is wrong
for these. In webdriverio that means
`driver.$('android=new UiSelector().resourceId("auth-email")')`; in a Java or
Python client, `AppiumBy.id("auth-email")`.

| Hook | Where |
| --- | --- |
| `auth-email`, `auth-password`, `auth-submit`, `auth-toggle-mode`, `auth-display-name` | sign in / sign up |
| `tab-devices`, `tab-alerts`, `tab-more` | bottom tab bar |
| `screen-fleet`, `screen-alerts`, `screen-more`, `screen-settings`, `screen-device`, `screen-live`, `screen-history`, `screen-incidents`, `screen-incident-detail`, `screen-anomalies`, `screen-forecasts`, `screen-reports`, `screen-alert-rules`, `screen-collector`, `screen-collector-unsupported` | every screen's root scroll view |
| `<screen-id>-title` | that screen's title text |
| `more-anomalies`, `more-forecasts`, `more-reports`, `more-alertrules`, `more-settings` | the More tab's rows — the only route to those screens on a phone |
| `device-section-<route>` | the same destinations scoped to one device |
| `device-card-<uuid>`, `incident-<uuid>`, `alert-event-<uuid>`, `rule-<uuid>` | list rows, keyed by entity id |
| `alert-silence-<uuid>`, `alert-open-incident-<uuid>` | per-alert actions |
| `rule-edit-<uuid>`, `rule-delete-<uuid>` | per-rule actions |
| `empty-state`, `error-note`, `segmented-<value>` | shared states and controls |

The `Screen` component takes `testID` as a **required** prop, so a new screen
cannot ship without one — that is the mechanism keeping this table complete
rather than a convention someone has to remember.

**A row's own hook does not reach the controls inside it.** React Native
flattens a `Card`'s accessibility subtree, so `rule-<uuid>` arrives as a
*childless* node and the rule's Edit and Delete buttons arrive as siblings of
it, not descendants. `UiSelector.childSelector()` therefore matches nothing,
and the only id-free way to reach "this rule's Edit" is to count Edits down the
screen — the positional locator this project forbids. That is why the per-row
actions in the table above each carry an id of their own; `AlertsScreen` had
already established the pattern with `alert-silence-<uuid>`, and the rules
screen was simply missing it until the suite went looking. Assume the same is
true of any new row: give its buttons their own hooks rather than expecting the
card's to scope them.

**`accessibilityLabel` → `content-desc`.** `Field`/`PasswordField` set it from
their visible label ("Email", "Password") and `Button` from its title ("Sign
in"). Prefer `resource-id`: these follow user-facing copy and change when the
wording does.

## The fleet is empty, and that is a real state

Nothing seeds devices or metrics — CLAUDE.md's first hard rule is that every
number came from a real agent reading a real machine. Assertions should accept
the empty state, or enrol a real agent first (`make e2e-code`). The app can
also enrol the phone itself: More → Settings → Monitor this phone.

## The report

`npm run test:xlsx` runs the suite and renders `appium-test-report.xlsx` via
the shared reporter in `tools/test-report/`. See that directory's README for
why `test:report` ends in `|| true`.
