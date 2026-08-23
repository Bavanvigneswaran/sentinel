/**
 * The two chart rules that would be silently wrong if nobody checked: a null
 * is drawn as a gap, and the y-scale follows the visible window.
 */

import assert from "node:assert/strict"
import { describe, it } from "node:test"

import { MAX_POINTS, buildFrame, emptyFrameFor } from "../chartFrame.ts"
import { RingBuffer } from "../ringBuffer.ts"

const SERIES = [{ key: "cpu", label: "cpu", color: "#000" }]
const SIZE = { width: 100, height: 50 }

function bufferOf(values: (number | null)[]): RingBuffer {
  const buffer = new RingBuffer(1000)
  values.forEach((value, index) => buffer.push(index, { cpu: value }))
  return buffer
}

describe("buildFrame", () => {
  it("is empty with no series configured", () => {
    assert.equal(buildFrame(bufferOf([1, 2]), { ...SIZE }).empty, true)
  })

  it("is empty with a series but no samples", () => {
    const frame = buildFrame(new RingBuffer(10), { ...SIZE, series: SERIES })
    assert.equal(frame.empty, true)
    assert.deepEqual(frame.series, SERIES)
  })

  it("keeps latest and paths index-aligned with series even when empty", () => {
    // Regression guard. An empty frame that carried its series but an empty
    // `latest` made `latest[i]` undefined rather than null; the legend's null
    // check missed it and handed undefined to the value formatter, crashing
    // the Live screen on resume from background — exactly the moment the
    // buffer is reset and this state occurs.
    for (const frame of [
      buildFrame(new RingBuffer(10), { ...SIZE, series: SERIES }),
      buildFrame(bufferOf([null, null]), { ...SIZE, series: SERIES }),
      emptyFrameFor(SERIES),
    ]) {
      assert.equal(frame.latest.length, frame.series.length)
      assert.equal(frame.paths.length, frame.series.length)
      assert.deepEqual(frame.latest, [null])
      for (const value of frame.latest) assert.notEqual(value, undefined)
    }
  })

  it("is empty when every reading in the window is null", () => {
    // Not a flat line at zero: the agent reported nothing measurable, and the
    // chart says "No data yet" rather than drawing a floor.
    const frame = buildFrame(bufferOf([null, null, null]), { ...SIZE, series: SERIES })
    assert.equal(frame.empty, true)
  })

  it("draws one unbroken run as a single polyline", () => {
    const frame = buildFrame(bufferOf([10, 20, 30]), { ...SIZE, series: SERIES })
    assert.equal(frame.paths[0].length, 1)
    assert.equal(frame.paths[0][0].split(" ").length, 3)
  })

  it("splits a series at a null rather than bridging it", () => {
    // The whole point. Two readings, a disconnection, two more readings —
    // that is two polylines, never one line sloping across the hole.
    const frame = buildFrame(bufferOf([10, 20, null, 40, 50]), { ...SIZE, series: SERIES })
    assert.equal(frame.paths[0].length, 2)
    assert.equal(frame.paths[0][0].split(" ").length, 2)
    assert.equal(frame.paths[0][1].split(" ").length, 2)
  })

  it("drops a single isolated reading rather than drawing a one-point line", () => {
    const frame = buildFrame(bufferOf([null, 42, null]), { ...SIZE, series: SERIES })
    assert.deepEqual(frame.paths[0], [])
    // …but it is still the latest known value, and is reported as such.
    assert.equal(frame.latest[0], 42)
  })

  it("honours a fixed domain instead of autoscaling a percentage", () => {
    // A machine idling between 3% and 5% must not look identical to one
    // swinging between 20% and 90%.
    const frame = buildFrame(bufferOf([3, 5]), { ...SIZE, series: SERIES, domain: [0, 100] })
    assert.deepEqual([frame.lo, frame.hi], [0, 100])
  })

  it("autoscales to the visible window with headroom when no domain is given", () => {
    const frame = buildFrame(bufferOf([100, 200]), { ...SIZE, series: SERIES })
    assert.equal(frame.lo, 0)
    assert.ok(frame.hi > 200, "the top of the scale must clear the largest reading")
  })

  it("rescales as the window moves, so an old peak does not pin the axis forever", () => {
    const buffer = new RingBuffer(2)
    buffer.push(0, { cpu: 1000 })
    buffer.push(1, { cpu: 10 })
    buffer.push(2, { cpu: 20 })
    const frame = buildFrame(buffer, { ...SIZE, series: SERIES })
    assert.ok(frame.hi < 100, `expected the scale to follow the window, got ${frame.hi}`)
  })

  it("maps the newest sample to the right edge and the largest to the top", () => {
    const frame = buildFrame(bufferOf([0, 50]), { ...SIZE, series: SERIES, domain: [0, 50] })
    const points = frame.paths[0][0].split(" ")
    assert.equal(points[0], "0,50") // oldest, at the value floor → bottom-left
    assert.equal(points[1], "100,0") // newest, at the ceiling → top-right
  })

  it("plots at most MAX_POINTS, keeping the newest", () => {
    const values = Array.from({ length: MAX_POINTS + 40 }, (_, i) => i)
    const frame = buildFrame(bufferOf(values), { ...SIZE, series: SERIES })
    assert.equal(frame.paths[0][0].split(" ").length, MAX_POINTS)
    assert.equal(frame.latest[0], MAX_POINTS + 39)
  })

  it("reports a never-reported series as null, not zero", () => {
    const buffer = new RingBuffer(10)
    buffer.push(0, { cpu: 10 })
    const frame = buildFrame(buffer, {
      ...SIZE,
      series: [...SERIES, { key: "swap", label: "swap", color: "#111" }],
    })
    assert.deepEqual(frame.latest, [10, null])
  })

  it("derives its series from whatever keys the buffer has discovered", () => {
    const buffer = new RingBuffer(10)
    buffer.push(0, { "eth0:rx": 1, "eth0:tx": 2 })
    const frame = buildFrame(buffer, {
      ...SIZE,
      deriveSeries: (keys) => keys.map((key) => ({ key, label: key, color: "#000" })),
    })
    assert.deepEqual(
      frame.series.map((s) => s.key),
      ["eth0:rx", "eth0:tx"],
    )
  })
})
