import assert from "node:assert/strict"
import { describe, it } from "node:test"

import { withDeviceScope } from "../deviceScope.ts"

describe("withDeviceScope", () => {
  it("is a no-op when the screen was opened fleet-wide", () => {
    assert.equal(withDeviceScope("/incidents?status=open", undefined), "/incidents?status=open")
  })

  it("starts a query string when the path has none", () => {
    assert.equal(withDeviceScope("/forecasts", "abc"), "/forecasts?device_id=abc")
  })

  it("appends to a query string that already exists", () => {
    // The failure mode this guards is silent: a second "?" makes the server
    // read device_id as part of the previous value and return the whole fleet,
    // which looks like a working screen showing the wrong list.
    assert.equal(
      withDeviceScope("/alerts/events?status=firing", "abc"),
      "/alerts/events?status=firing&device_id=abc",
    )
  })

  it("encodes the id rather than trusting it into the URL", () => {
    assert.equal(withDeviceScope("/forecasts", "a b&c"), "/forecasts?device_id=a%20b%26c")
  })
})
