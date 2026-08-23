import { describe, expect, it } from "vitest"

import { DEFAULT_LIVE_WINDOW_SECONDS } from "@/components/charts/LiveChart"

/**
 * The x-scale range function, extracted here as the pure thing it is.
 *
 * This is what decides whether a live chart reads as live. uPlot auto-fits x
 * to every point it holds, and the buffer accumulates 900 — so with no
 * explicit range the visible span grows from the primer's ~5 minutes towards
 * 15, and one new sample per second moves the trace by 1/300th of the width,
 * then less. The chart was drawing every sample correctly; it just could not
 * be seen doing it.
 */
function xRange(dataMin: number | null, dataMax: number | null, windowSeconds: number) {
  return dataMax == null || Number.isNaN(dataMax)
    ? [dataMin, dataMax]
    : [dataMax - windowSeconds, dataMax]
}

describe("live chart x window", () => {
  it("anchors the right edge to the newest sample", () => {
    expect(xRange(1000, 1500, 120)).toEqual([1380, 1500])
  })

  it("keeps the span fixed however much history the buffer holds", () => {
    // The bug: a buffer holding 15 minutes made the window 15 minutes wide,
    // so the scroll rate fell as the session went on.
    const short = xRange(1490, 1500, 120)
    const long = xRange(0, 1500, 120)
    expect(short[1]! - short[0]!).toBe(120)
    expect(long[1]! - long[0]!).toBe(120)
  })

  it("scrolls one window-width per window of elapsed time", () => {
    const a = xRange(0, 1000, 60)
    const b = xRange(0, 1060, 60)
    expect(b[0]! - a[0]!).toBe(60)
  })

  it("leaves an empty buffer's range alone rather than returning NaN", () => {
    // Same class of bug as passing `false` to setData (Phase 3's uPlot
    // invariant): a broken scale set before data arrives never recovers.
    expect(xRange(null, null, 120)).toEqual([null, null])
    expect(xRange(NaN, NaN, 120)[1]).toBeNaN()
  })

  it("defaults to a window short enough to see move", () => {
    expect(DEFAULT_LIVE_WINDOW_SECONDS).toBeLessThanOrEqual(180)
  })
})
