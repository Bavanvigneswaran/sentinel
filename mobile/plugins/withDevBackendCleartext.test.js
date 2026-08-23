/**
 * Which hosts get a plaintext-HTTP exception on the release build, and which
 * do not.
 *
 * Found by installing an actual release build on the emulator and watching
 * sign-in fail with "Could not reach the backend" — Android silently drops
 * cleartext requests rather than raising anything the app could report a
 * clearer reason for. The RN template's debug-only `usesCleartextTraffic`
 * override meant this had never been exercised before a release variant
 * existed at all.
 */

const assert = require("node:assert/strict")
const { describe, it } = require("node:test")

const { buildNetworkSecurityConfigXml, cleartextHostFor } = require("./withDevBackendCleartext.js")

describe("cleartextHostFor", () => {
  it("extracts the host from a plain http:// URL", () => {
    assert.equal(cleartextHostFor("http://192.168.0.4:8000"), "192.168.0.4")
  })

  it("needs no exception for a deployed https:// backend", () => {
    // Android's default — cleartext blocked everywhere except an explicit
    // exception — is the correct, secure behaviour here. Adding one anyway
    // would be a blanket allowance this app never needs.
    assert.equal(cleartextHostFor("https://sentinel.example.com"), null)
  })

  it("falls back to the emulator default, which is also http", () => {
    // src/config.ts's own DEFAULT_API_URL. Must stay in sync — both are
    // exercised the moment EXPO_PUBLIC_API_URL is unset, exactly the state
    // `make mobile-android` leaves a fresh checkout in.
    assert.equal(cleartextHostFor(undefined), "10.0.2.2")
    assert.equal(cleartextHostFor(""), "10.0.2.2")
  })

  it("fails closed on an unparseable URL rather than guessing a host", () => {
    assert.equal(cleartextHostFor("not a url"), null)
  })

  it("handles a bare hostname with no port", () => {
    assert.equal(cleartextHostFor("http://sentinel.local"), "sentinel.local")
  })
})

describe("buildNetworkSecurityConfigXml", () => {
  it("scopes the exception to exactly one domain, not a blanket allowance", () => {
    // The bug class this avoids: android:usesCleartextTraffic="true" on the
    // whole application would silently accept plaintext to ANY host the app
    // ever talks to, forever — not just the one it was configured for today.
    const xml = buildNetworkSecurityConfigXml("192.168.0.4")
    assert.match(xml, /<domain includeSubdomains="false">192\.168\.0\.4<\/domain>/)
    assert.equal((xml.match(/<domain-config/g) || []).length, 1)
  })

  it("produces well-formed XML", () => {
    // A malformed network-security-config fails the whole build with an
    // unhelpful AAPT error, not a runtime symptom — worth checking directly.
    const xml = buildNetworkSecurityConfigXml("example.com")
    assert.match(xml, /^<\?xml version="1\.0" encoding="utf-8"\?>/)
    assert.equal((xml.match(/</g) || []).length, (xml.match(/>/g) || []).length)
  })
})
