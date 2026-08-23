/**
 * The buffer behind every live chart. Node's built-in test runner — these are
 * pure data-structure tests with no React Native in them, so they need no
 * native runtime and no jest transform.
 *
 * Relative imports rather than "@/…": the `@` alias is a Metro/tsc concern and
 * plain `node --test` does not resolve it.
 */

import assert from "node:assert/strict"
import { describe, it } from "node:test"

import { RingBuffer } from "../ringBuffer.ts"

describe("RingBuffer", () => {
  it("keeps series aligned with the shared timestamp axis", () => {
    const buffer = new RingBuffer(10)
    buffer.push(1, { cpu: 10, mem: 20 })
    buffer.push(2, { cpu: 11, mem: 21 })

    assert.deepEqual(buffer.toAlignedData(["cpu", "mem"]), [
      [1, 2],
      [10, 11],
      [20, 21],
    ])
  })

  it("backfills a late-appearing series with nulls, never zeroes", () => {
    // A NIC that comes up mid-session, or a latency target added to the
    // agent's config. The history before it existed is unmeasured, not idle.
    const buffer = new RingBuffer(10)
    buffer.push(1, { eth0: 100 })
    buffer.push(2, { eth0: 200 })
    buffer.push(3, { eth0: 300, wlan0: 5 })

    const [, , wlan] = buffer.toAlignedData(["eth0", "wlan0"])
    assert.deepEqual(wlan, [null, null, 5])
  })

  it("records an absent key for a tick as null, not as the previous value", () => {
    const buffer = new RingBuffer(10)
    buffer.push(1, { cpu: 10 })
    buffer.push(2, {})

    assert.deepEqual(buffer.toAlignedData(["cpu"])[1], [10, null])
  })

  it("preserves an explicit null as a null", () => {
    // An unreachable latency target arrives as null, and must stay null —
    // streamBuffers.ts relies on this to avoid drawing a "perfect connection"
    // line for a target that is down.
    const buffer = new RingBuffer(10)
    buffer.push(1, { target: null })
    assert.deepEqual(buffer.toAlignedData(["target"])[1], [null])
  })

  it("drops the oldest point once capacity is exceeded, across every series", () => {
    const buffer = new RingBuffer(2)
    buffer.push(1, { cpu: 1 })
    buffer.push(2, { cpu: 2 })
    buffer.push(3, { cpu: 3 })

    assert.equal(buffer.length, 2)
    assert.deepEqual(buffer.toAlignedData(["cpu"]), [
      [2, 3],
      [2, 3],
    ])
  })

  it("returns an all-null column for a series it has never seen", () => {
    const buffer = new RingBuffer(10)
    buffer.push(1, { cpu: 10 })
    assert.deepEqual(buffer.toAlignedData(["swap"])[1], [null])
  })

  it("sorts series keys so a chart's colour assignment is stable", () => {
    const buffer = new RingBuffer(10)
    buffer.push(1, { "z:tx": 1, "a:rx": 2 })
    assert.deepEqual(buffer.seriesKeys(), ["a:rx", "z:tx"])
  })
})
