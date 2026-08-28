import assert from "node:assert/strict"
import { describe, it } from "node:test"

import { describeBuffer } from "../bufferPressure.ts"

/**
 * The distinction being guarded: a backlog is a delay and will resolve itself;
 * a buffer at its cap is discarding the oldest sample on every new one and the
 * history behind it is gone. A plain count renders both as a number going up,
 * which is how a real phone said "400 buffered" for hours and was read as fine.
 */
describe("describeBuffer", () => {
  const capacity = 400

  it("says nothing is waiting rather than reporting a zero", () => {
    const { text, losing } = describeBuffer({
      bufferedSamples: 0,
      bufferCapacity: capacity,
      droppedSamples: 0,
    })
    assert.equal(text, "nothing waiting")
    assert.equal(losing, false)
  })

  it("reports an ordinary backlog as a plain count", () => {
    assert.deepEqual(
      describeBuffer({ bufferedSamples: 12, bufferCapacity: capacity, droppedSamples: 0 }),
      { text: "12 samples", losing: false },
    )
  })

  it("does not pluralise a single sample", () => {
    assert.equal(
      describeBuffer({ bufferedSamples: 1, bufferCapacity: capacity, droppedSamples: 0 }).text,
      "1 sample",
    )
  })

  it("names the cap before it is reached", () => {
    // While there is still something to do about it — grant the battery
    // exemption, wake the phone, get it back on a network.
    const { text, losing } = describeBuffer({
      bufferedSamples: 380,
      bufferCapacity: capacity,
      droppedSamples: 0,
    })
    assert.equal(text, "380 of 400 — nearly full")
    assert.equal(losing, false) // approaching is not arriving
  })

  it("says a full buffer is dropping, not just how much it holds", () => {
    const { text, losing } = describeBuffer({
      bufferedSamples: 400,
      bufferCapacity: capacity,
      droppedSamples: 57,
    })
    assert.equal(text, "400 — full, dropping oldest · 57 lost")
    assert.equal(losing, true)
  })

  it("keeps reporting the loss once the backlog has drained", () => {
    // The buffer emptying looks like a full recovery. The samples that fell
    // off the front are still gone, and the charts will have a hole in them
    // that nothing else on the screen explains.
    assert.deepEqual(
      describeBuffer({ bufferedSamples: 0, bufferCapacity: capacity, droppedSamples: 57 }),
      { text: "57 lost while full", losing: true },
    )
    assert.deepEqual(
      describeBuffer({ bufferedSamples: 8, bufferCapacity: capacity, droppedSamples: 57 }),
      { text: "8 · 57 lost", losing: true },
    )
  })

  it("never claims a capacity on a device that has no collector", () => {
    // UNSUPPORTED_STATUS reports a zero capacity rather than 400, and "0 of 0"
    // would be a ring buffer that does not exist describing how full it is.
    assert.equal(
      describeBuffer({ bufferedSamples: 0, bufferCapacity: 0, droppedSamples: 0 }).text,
      "nothing waiting",
    )
  })
})
