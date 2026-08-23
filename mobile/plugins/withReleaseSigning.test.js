/**
 * The release build must not ship signed with React Native's public debug key.
 *
 * The fixture below is the shape `expo prebuild` generates. android/ is
 * gitignored, so a test that read the real file would pass locally and have
 * nothing to read in CI — and the failure mode being guarded here (a rewrite
 * that hits the wrong buildType) is invisible without an assertion.
 */

const assert = require("node:assert/strict")
const { describe, it } = require("node:test")

const { addReleaseSigning, isConfigured, REQUIRED } = require("./withReleaseSigning.js")

const GENERATED = `android {
    signingConfigs {
        debug {
            storeFile file('debug.keystore')
            storePassword 'android'
            keyAlias 'androiddebugkey'
            keyPassword 'android'
        }
    }
    buildTypes {
        debug {
            signingConfig signingConfigs.debug
        }
        release {
            // Caution! In production, you need to generate your own keystore file.
            signingConfig signingConfigs.debug
            minifyEnabled enableMinifyInReleaseBuilds
        }
    }
}
`

function buildTypesOf(gradle) {
  return gradle.slice(gradle.indexOf("    buildTypes {"))
}

describe("addReleaseSigning", () => {
  it("points the release buildType at a real key", () => {
    const out = buildTypesOf(addReleaseSigning(GENERATED))
    assert.match(out, /release \{[\s\S]*?signingConfig signingConfigs\.release/)
  })

  it("leaves the debug buildType on the debug key", () => {
    // The bug this exists for: a lazy `release {...signingConfigs.debug` match
    // over the whole file starts inside the *injected* signingConfigs.release
    // block and runs on to rewrite the debug buildType instead — silently
    // leaving the release build on the public key.
    const out = buildTypesOf(addReleaseSigning(GENERATED))
    assert.match(out, /debug \{\n\s+signingConfig signingConfigs\.debug\n/)
  })

  it("reads the passwords from the environment, not gradle properties", () => {
    // Command-line -P properties are visible in the process table and in any
    // CI log that echoes commands.
    const out = addReleaseSigning(GENERATED)
    assert.match(out, /System\.getenv\("SENTINEL_ANDROID_KEYSTORE_PASSWORD"\)/)
    assert.ok(!out.includes("-PSENTINEL"))
  })

  it("is idempotent, so a re-prebuild does not stack blocks", () => {
    const once = addReleaseSigning(GENERATED)
    assert.equal(addReleaseSigning(once), once)
  })

  it("guards storeFile against an unset keystore variable", () => {
    // Found by running it, not by reasoning about it: Gradle evaluates every
    // project's build.gradle during configuration for EVERY task, not just
    // assembleRelease. An unguarded `file(System.getenv("SENTINEL_ANDROID_KEYSTORE"))`
    // throws "Cannot convert 'null' to File" and takes down a completely
    // unrelated task — :sentinel-collector's own unit tests — the moment
    // those variables are not exported in the shell running Gradle, which is
    // every shell except the one `make mobile-apk` itself opened.
    const out = addReleaseSigning(GENERATED)
    assert.match(out, /if \(sentinelKeystore != null\)/)
    assert.doesNotMatch(out, /storeFile file\(System\.getenv\(/)
  })

  it("refuses to guess when the template has changed", () => {
    // Better a failed build than a release quietly signed with the public key.
    assert.throws(() => addReleaseSigning("android {\n}\n"), /template changed/)
    assert.throws(
      () =>
        addReleaseSigning(
          GENERATED.replace("            signingConfig signingConfigs.debug\n            minifyEnabled", "            minifyEnabled"),
        ),
      /refusing to guess/,
    )
  })
})

describe("isConfigured", () => {
  it("needs every keystore variable, not just some", () => {
    const full = Object.fromEntries(REQUIRED.map((k) => [k, "x"]))
    assert.equal(isConfigured(full), true)

    for (const missing of REQUIRED) {
      const partial = { ...full, [missing]: "" }
      assert.equal(isConfigured(partial), false, `${missing} alone should disable it`)
    }
  })

  it("is off by default", () => {
    assert.equal(isConfigured({}), false)
  })
})
