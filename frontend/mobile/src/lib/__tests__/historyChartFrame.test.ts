import assert from "node:assert/strict"
import { describe, it } from "node:test"

import { buildHistoryFrame, xAxisLabels } from "../historyChartFrame.ts"

const COLOR = "#3b82f6"

/** Pixel coordinates out of a polyline run, as numbers. */
function pointsOf(run: string): { x: number; y: number }[] {
  return run.split(" ").map((pair) => {
    const [x, y] = pair.split(",").map(Number)
    return { x, y }
  })
}

describe("buildHistoryFrame", () => {
  it("spaces points by time, not by index", () => {
    // The bug this exists to prevent: an outage between two buckets is a wide
    // gap in wall-clock time, and spacing by array index would compress it to
    // the same width as a busy minute — drawing a machine that was off for
    // six hours as if it had been reporting all along.
    const frame = buildHistoryFrame({
      width: 100,
      height: 100,
      timestamps: [0, 10, 100],
      series: [{ label: "cpu", color: COLOR, values: [10, 20, 30] }],
      domain: [0, 100],
    })
    const points = pointsOf(frame.paths[0][0])
    assert.equal(points[0].x, 0)
    assert.equal(points[1].x, 10)
    assert.equal(points[2].x, 100)
  })

  it("breaks the line at a null rather than bridging it", () => {
    const frame = buildHistoryFrame({
      width: 100,
      height: 100,
      timestamps: [0, 1, 2, 3, 4, 5],
      series: [{ label: "cpu", color: COLOR, values: [10, 20, null, null, 40, 50] }],
      domain: [0, 100],
    })
    // Two runs, never one polyline drawing a slope through time nobody
    // measured.
    assert.equal(frame.paths[0].length, 2)
  })

  it("drops a run of a single reading, which has no line to draw", () => {
    const frame = buildHistoryFrame({
      width: 100,
      height: 100,
      timestamps: [0, 1, 2],
      series: [{ label: "cpu", color: COLOR, values: [10, null, 40] }],
      domain: [0, 100],
    })
    assert.deepEqual(frame.paths[0], [])
  })

  it("honours a pinned domain instead of autoscaling to the data", () => {
    // A machine idling between 3% and 5% must not be drawn identically to one
    // swinging from 20% to 90%.
    const frame = buildHistoryFrame({
      width: 100,
      height: 100,
      timestamps: [0, 1],
      series: [{ label: "cpu", color: COLOR, values: [3, 5] }],
      domain: [0, 100],
    })
    assert.equal(frame.lo, 0)
    assert.equal(frame.hi, 100)
  })

  it("autoscales from zero, with headroom, when no domain is pinned", () => {
    const frame = buildHistoryFrame({
      width: 100,
      height: 100,
      timestamps: [0, 1],
      series: [{ label: "rx", color: COLOR, values: [100, 200] }],
    })
    // Not 100. A rate chart floored at its own minimum draws a nearly-idle
    // disk sitting on a baseline labelled "53.5 KB/s" — seen on the real
    // screen, and it reads as sustained traffic that is not happening. The
    // live chart has always pinned zero here; this matches it.
    assert.equal(frame.lo, 0)
    assert.ok(frame.hi > 200, "a flat line at the maximum must not sit on the frame edge")
  })

  it("keeps the top of the domain inside the plot, not on its border", () => {
    // Same reasoning as forecastChartFrame's EDGE_PADDING: y=0 is the same
    // pixel row as the container's own top border, and a line drawn there is
    // indistinguishable from the border already occupying it.
    const frame = buildHistoryFrame({
      width: 100,
      height: 100,
      timestamps: [0, 1],
      series: [{ label: "cpu", color: COLOR, values: [100, 100] }],
      domain: [0, 100],
    })
    assert.ok(pointsOf(frame.paths[0][0])[0].y > 0)
  })

  it("is empty — and index-aligned — when every reading is null", () => {
    const frame = buildHistoryFrame({
      width: 100,
      height: 100,
      timestamps: [0, 1],
      series: [
        { label: "a", color: COLOR, values: [null, null] },
        { label: "b", color: COLOR, values: [null, null] },
      ],
    })
    assert.equal(frame.empty, true)
    // paths stays index-aligned with series even when empty, the same rule
    // emptyFrameFor() enforces for the live chart — a renderer indexing
    // paths[i] must never get undefined.
    assert.equal(frame.paths.length, 2)
  })

  it("survives a window holding one bucket", () => {
    const frame = buildHistoryFrame({
      width: 100,
      height: 100,
      timestamps: [1000],
      series: [{ label: "cpu", color: COLOR, values: [50] }],
      domain: [0, 100],
    })
    // One point is not a line; there is nothing to draw and no division by a
    // zero-width time span.
    assert.deepEqual(frame.paths[0], [])
    assert.equal(frame.empty, false)
  })

  it("draws nothing before the plot has been measured", () => {
    const frame = buildHistoryFrame({
      width: 0,
      height: 100,
      timestamps: [0, 1],
      series: [{ label: "cpu", color: COLOR, values: [10, 20] }],
    })
    assert.equal(frame.empty, true)
  })
})

describe("xAxisLabels", () => {
  /** Local midday on a fixed day, so these assertions do not depend on the
   * machine's timezone pushing an hour-long window over midnight. */
  const noon = new Date(2026, 7, 24, 12, 0, 0).getTime() / 1000

  it("labels a window inside one day with times only", () => {
    const hour = xAxisLabels([noon, noon + 3600])
    assert.ok(hour && hour.left.includes(":"))
    // A repeated date on both ends of a one-hour window is noise.
    assert.ok(hour && !/[A-Za-z]{3} \d/.test(hour.left))
  })

  it("names the day once a window crosses midnight", () => {
    // "8:52 PM → 8:32 PM" reads as time running backwards, which is what a
    // 24h window looks like for most of the day. Seen on the real screen.
    const overnight = xAxisLabels([noon, noon + 86_400 - 1200])
    assert.ok(overnight, "a two-bucket window still has labels")
    assert.ok(/[A-Za-z]{3} \d/.test(overnight.left), "the day is named")
    assert.ok(overnight.left.includes(":"), "and the time is kept")
    assert.notEqual(overnight.left, overnight.right)
  })

  it("drops the time entirely once the window is wider than two days", () => {
    // A month of history labelled "14:03" tells the reader nothing.
    const month = xAxisLabels([noon, noon + 30 * 86_400])
    assert.ok(month && !month.left.includes(":"))
  })

  it("has nothing to label for an empty window", () => {
    assert.equal(xAxisLabels([]), null)
  })
})
