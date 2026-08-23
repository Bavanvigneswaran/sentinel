import assert from "node:assert/strict"
import { describe, it } from "node:test"

import { buildForecastFrame } from "../forecastChartFrame.ts"
import type { ForecastPoint } from "@/types/forecast"

const SIZE = { width: 100, height: 50, domain: [0, 100] as [number, number] }

function points(values: { predicted: number; lower: number; upper: number }[]): ForecastPoint[] {
  return values.map((v, i) => ({ offset_seconds: i * 60, ...v }))
}

describe("buildForecastFrame", () => {
  it("is empty with no points, but still reports where the ceiling would sit", () => {
    const frame = buildForecastFrame([], { ...SIZE, ceiling: 100 })
    assert.equal(frame.empty, true)
    assert.equal(frame.predictedPoints, "")
    assert.equal(frame.bandPolygon, "")
    // A ceiling at the top of the domain sits just inside the plot's own
    // edge padding, not literally y=0 — see EDGE_PADDING's own comment for
    // why 0 would be indistinguishable from the plot container's border.
    assert.equal(frame.referenceY, 6)
  })

  it("omits the reference line when the ceiling is outside the domain", () => {
    const frame = buildForecastFrame(points([{ predicted: 5, lower: 4, upper: 6 }]), {
      ...SIZE,
      ceiling: 150,
    })
    assert.equal(frame.referenceY, null)
  })

  it("omits the reference line when there is no ceiling to draw", () => {
    const frame = buildForecastFrame(points([{ predicted: 5, lower: 4, upper: 6 }]), SIZE)
    assert.equal(frame.referenceY, null)
  })

  it("draws the predicted line at the domain's midpoint for a mid-range value", () => {
    const frame = buildForecastFrame(points([{ predicted: 50, lower: 50, upper: 50 }]), SIZE)
    // height=50, domain=[0,100]: the midpoint lands at the padded plot's own
    // midpoint too, regardless of how much edge padding there is, since the
    // padding is symmetric top and bottom.
    assert.equal(frame.predictedPoints, "0,25")
  })

  it("keeps a value at the domain's edge off the container's own border", () => {
    // The bug this guards: EDGE_PADDING=0 would put a ceiling-value point at
    // y=0, the same row as the plot View's own top border, making the two
    // visually indistinguishable — not a missing line, one hidden under an
    // opaque border already there. Found by cropping and upscaling a real
    // screenshot rather than trusting a glance at the full page.
    const frame = buildForecastFrame(points([{ predicted: 100, lower: 100, upper: 100 }]), SIZE)
    const [, y] = frame.predictedPoints.split(",").map(Number)
    assert.ok(y > 0, `expected the top edge value to sit below y=0, got y=${y}`)
  })

  it("closes the confidence band as upper-forward then lower-backward", () => {
    // The failure mode this guards: building the band as upper-then-lower in
    // the SAME index order (rather than reversing the lower half) draws a
    // self-intersecting bowtie instead of a closed band, because the two
    // opposite edges would cross in the middle instead of meeting at the ends.
    const two = points([
      { predicted: 10, lower: 8, upper: 12 },
      { predicted: 20, lower: 18, upper: 22 },
    ])
    const frame = buildForecastFrame(two, SIZE)
    const vertices = frame.bandPolygon.split(" ")
    assert.equal(vertices.length, 4)
    // upper[0], upper[1], lower[1], lower[0] — forward then back.
    const [p0, p1, p2, p3] = vertices.map((v) => v.split(",").map(Number))
    assert.ok(p0[0] < p1[0], "upper half runs left to right")
    assert.ok(p2[0] > p3[0], "lower half runs right to left, closing the shape")
  })

  it("is not empty once a fit exists, even a single point", () => {
    const frame = buildForecastFrame(points([{ predicted: 1, lower: 0, upper: 2 }]), SIZE)
    assert.equal(frame.empty, false)
  })
})
