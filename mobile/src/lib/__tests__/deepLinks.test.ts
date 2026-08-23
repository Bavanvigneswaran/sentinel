/**
 * `data.url` comes off the network in a push payload. These tests are the
 * regression guard on it being an allow-list rather than a pass-through.
 */

import assert from "node:assert/strict"
import { describe, it } from "node:test"

import { appUrlForPath } from "../deepLinks.ts"

describe("appUrlForPath", () => {
  it("maps the alert payload the backend actually sends", () => {
    // app/alerts/notify.py builds {"url": "/alerts"} for both push channels.
    assert.equal(appUrlForPath("/alerts"), "sentinel://alerts")
  })

  it("maps the other known paths", () => {
    assert.equal(appUrlForPath("/"), "sentinel://fleet")
    assert.equal(appUrlForPath("/devices"), "sentinel://devices")
  })

  it("refuses a path it does not know", () => {
    assert.equal(appUrlForPath("/admin"), null)
  })

  it("refuses an absolute URL to somewhere else entirely", () => {
    assert.equal(appUrlForPath("https://evil.example.com/steal"), null)
    assert.equal(appUrlForPath("sentinel://alerts"), null)
    assert.equal(appUrlForPath("javascript:alert(1)"), null)
  })

  it("refuses anything that is not a string", () => {
    assert.equal(appUrlForPath(undefined), null)
    assert.equal(appUrlForPath(null), null)
    assert.equal(appUrlForPath(42), null)
    assert.equal(appUrlForPath({ toString: () => "/alerts" }), null)
  })

  it("does not resolve inherited Object properties as routes", () => {
    // A plain Record lookup would happily return Object.prototype.constructor
    // for "constructor" and turn it into a route.
    assert.equal(appUrlForPath("constructor"), null)
    assert.equal(appUrlForPath("__proto__"), null)
    assert.equal(appUrlForPath("toString"), null)
  })
})

describe("routes reachable from a push payload", () => {
  it("maps every screen the backend could name", () => {
    // These exist so a future payload does not need a client release to be
    // routable. Each must also be in navigation/linking.ts's `screens` — a
    // path here with no screen there resolves to nothing, and a tapped
    // notification lands on whatever was already open.
    for (const path of ["/incidents", "/anomalies", "/forecasts", "/reports", "/settings"]) {
      assert.equal(appUrlForPath(path), `sentinel://${path.slice(1)}`)
    }
  })

  it("still refuses a path nobody registered", () => {
    assert.equal(appUrlForPath("/admin"), null)
  })
})
