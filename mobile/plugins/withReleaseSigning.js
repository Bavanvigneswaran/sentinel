/**
 * Give the Android release build a real signing key — when one exists.
 *
 * `expo prebuild` generates `android/app/build.gradle` with the release
 * buildType pointing at `signingConfigs.debug`, i.e. React Native's *public*
 * debug keystore. That is fine for `expo run:android` and unacceptable for
 * anything anyone downloads: the key is in every RN checkout on earth, so
 * anybody could build an "update" that Android accepts as the same app and
 * installs straight over it.
 *
 * `android/` is generated and gitignored (a Phase 10a invariant), so this is a
 * config plugin rather than an edit — config plugins are where native changes
 * belong, and a hand-edit disappears at the next prebuild.
 *
 * Unconfigured is a no-op, matching the same posture `app.config.ts` already
 * takes for a missing `google-services.json` and the backend takes for unset
 * SMTP/VAPID/ANTHROPIC_API_KEY. It deliberately does NOT fall back to the
 * debug key with a warning: `make mobile-apk` refuses to run at all without a
 * keystore, so there is no path that quietly produces a debug-signed
 * distributable.
 *
 * Passwords come from the environment, never from `-P` gradle properties —
 * command-line arguments are visible in the process table and in any CI log
 * that echoes commands. Same reasoning as agent/build/signing.py refusing a
 * .pfx password.
 */

const { withAppBuildGradle } = require("expo/config-plugins")

const REQUIRED = [
  "SENTINEL_ANDROID_KEYSTORE",
  "SENTINEL_ANDROID_KEYSTORE_PASSWORD",
  "SENTINEL_ANDROID_KEY_ALIAS",
  "SENTINEL_ANDROID_KEY_PASSWORD",
]

/** True when every keystore variable is present. */
function isConfigured(env = process.env) {
  return REQUIRED.every((name) => Boolean(env[name]))
}

const RELEASE_SIGNING_CONFIG = `
        release {
            // Injected by plugins/withReleaseSigning.js. Read from the
            // environment rather than gradle properties so the passwords never
            // reach the process table or a CI log.
            storeFile file(System.getenv("SENTINEL_ANDROID_KEYSTORE"))
            storePassword System.getenv("SENTINEL_ANDROID_KEYSTORE_PASSWORD")
            keyAlias System.getenv("SENTINEL_ANDROID_KEY_ALIAS")
            keyPassword System.getenv("SENTINEL_ANDROID_KEY_PASSWORD")
        }`

/**
 * Rewrite the generated gradle. Exported so it can be tested against a real
 * captured build.gradle without running prebuild.
 */
function addReleaseSigning(contents) {
  if (contents.includes("SENTINEL_ANDROID_KEYSTORE")) return contents

  const signingAnchor = "    signingConfigs {"
  const buildTypesAt = contents.indexOf("    buildTypes {")
  if (!contents.includes(signingAnchor) || buildTypesAt === -1) {
    throw new Error(
      "withReleaseSigning: could not find the signingConfigs and buildTypes " +
        "blocks in build.gradle. The Expo template changed; update this plugin " +
        "rather than shipping a debug-signed release.",
    )
  }

  // Split at buildTypes and work on the halves separately. Doing this in one
  // pass over the whole file does not work: once a `release {` block has been
  // injected into signingConfigs, a lazy `release \{...signingConfigs.debug`
  // match starts there and runs on to rewrite the *debug* buildType instead —
  // which silently leaves the release build on the public debug key, the exact
  // outcome this plugin exists to prevent. Found by running it.
  const head = contents.slice(0, buildTypesAt)
  const buildTypes = contents.slice(buildTypesAt)

  const releaseUsesDebugKey = /(release \{[\s\S]*?)signingConfig signingConfigs\.debug/
  if (!releaseUsesDebugKey.test(buildTypes)) {
    throw new Error(
      "withReleaseSigning: the release buildType does not reference " +
        "signingConfigs.debug as expected; refusing to guess.",
    )
  }

  return (
    head.replace(signingAnchor, signingAnchor + RELEASE_SIGNING_CONFIG) +
    buildTypes.replace(releaseUsesDebugKey, "$1signingConfig signingConfigs.release")
  )
}

module.exports = function withReleaseSigning(config) {
  if (!isConfigured()) return config

  return withAppBuildGradle(config, (cfg) => {
    cfg.modResults.contents = addReleaseSigning(cfg.modResults.contents)
    return cfg
  })
}

module.exports.addReleaseSigning = addReleaseSigning
module.exports.isConfigured = isConfigured
module.exports.REQUIRED = REQUIRED
