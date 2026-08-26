/**
 * The maths behind a static, multi-series history chart — no renderer, no
 * RingBuffer, no timer. Third in the family beside `chartFrame.ts` (live, read
 * off a ring buffer every second) and `forecastChartFrame.ts` (one stored
 * forecast); split out for the same reason both of those are, so the two rules
 * that actually matter are testable in plain Node.
 *
 * Those rules, restated for a window that may be a month wide:
 *
 * - **A null is a gap.** A bucket a series did not report in is a hole in the
 *   line, never a zero and never a straight segment bridging it. A device that
 *   was off for six hours did not sit at the value it had before it vanished.
 * - **The y-scale comes from the data actually on screen**, unless the caller
 *   pins a domain (percentages do: a machine idling between 3% and 5% must not
 *   be drawn identically to one swinging from 20% to 90%). An autoscaled axis
 *   still starts at zero, exactly as the live chart's does — a byte rate or an
 *   RTT has a meaningful zero, and floating the floor up to the window's own
 *   minimum draws a nearly-idle disk sitting on a baseline labelled
 *   "53.5 KB/s", which reads as sustained write traffic that is not happening.
 *
 * Unlike the live chart, x is *time*, not sample index: history buckets are
 * evenly spaced only when the device reported continuously, and spacing them
 * by index would silently compress an outage into the same width as a busy
 * minute.
 */

export interface HistorySeries {
  label: string
  color: string
  values: (number | null)[]
}

export interface HistoryFrame {
  /** One entry per series: its polyline runs, in pixel space. A run per
   * unbroken stretch of readings. */
  paths: string[][]
  lo: number
  hi: number
  empty: boolean
}

export interface HistoryFrameOptions {
  width: number
  height: number
  /** Unix seconds, ascending — the shared x-axis. */
  timestamps: number[]
  series: HistorySeries[]
  /** Percentages pin to 0–100; byte rates and RTTs autoscale. */
  domain?: [number, number]
}

/** Matches forecastChartFrame's EDGE_PADDING, and for the same reason: a value
 * sitting exactly on the domain's edge otherwise maps to the same pixel row as
 * the plot container's own border and is indistinguishable from it. A pinned
 * 0–100 domain makes that the common case, not an edge case. */
const EDGE_PADDING = 4

export const EMPTY_HISTORY_FRAME: HistoryFrame = { paths: [], lo: 0, hi: 1, empty: true }

export function buildHistoryFrame(opts: HistoryFrameOptions): HistoryFrame {
  const { width, height, timestamps, series, domain } = opts

  if (width <= 0 || timestamps.length === 0 || series.length === 0) {
    return { ...EMPTY_HISTORY_FRAME, paths: series.map(() => []) }
  }

  const finite = series
    .flatMap((s) => s.values)
    .filter((v): v is number => v !== null && Number.isFinite(v))
  if (finite.length === 0) return { ...EMPTY_HISTORY_FRAME, paths: series.map(() => []) }

  // Zero, not the window's own minimum — same rule as chartFrame.ts's live
  // autoscale, and for the same reason: these axes are rates and latencies,
  // where zero is a real value and a floating floor overstates every trace.
  const lo = domain ? domain[0] : 0
  // Headroom so a flat line at the window's maximum is not drawn on the frame
  // edge. Only for an autoscaled axis — a pinned 0–100 must stay 0–100.
  const hi = domain ? domain[1] : Math.max(...finite) * 1.05 || 1
  const span = hi - lo || 1

  const first = timestamps[0]
  const last = timestamps[timestamps.length - 1]
  // A single-bucket window has no width to spread across; drawing its one
  // point at x=0 is honest — there is no second point to draw a line to.
  const timeSpan = last - first || 1

  const plotHeight = Math.max(height - EDGE_PADDING * 2, 1)
  const px = (ts: number) => ((ts - first) / timeSpan) * width
  const py = (v: number) =>
    EDGE_PADDING + plotHeight - ((v - lo) / span) * plotHeight

  const paths = series.map((s) => {
    const runs: string[] = []
    let current: string[] = []
    s.values.forEach((value, index) => {
      const ts = timestamps[index]
      if (value === null || !Number.isFinite(value) || ts === undefined) {
        if (current.length > 1) runs.push(current.join(" "))
        current = []
        return
      }
      current.push(`${px(ts)},${py(value)}`)
    })
    if (current.length > 1) runs.push(current.join(" "))
    return runs
  })

  return { paths, lo, hi, empty: false }
}

/**
 * Tick labels for the x-axis: the first and last bucket.
 *
 * Which parts of the timestamp appear is decided by what the reader would
 * otherwise have to guess:
 *
 * - A month of history labelled "14:03" tells them nothing, so a window wider
 *   than two days gets dates only.
 * - A window that **crosses midnight** labelled "8:52 PM → 8:32 PM" reads as
 *   time running backwards, which is what a 24h window looks like for most of
 *   the day. Seen on the real screen; the day is added whenever the two ends
 *   fall on different calendar dates, which is the only case where the times
 *   alone are ambiguous.
 * - Anything else is a window inside one day, where the time is the whole
 *   answer and a repeated date is noise.
 */
export function xAxisLabels(timestamps: number[]): { left: string; right: string } | null {
  if (timestamps.length === 0) return null

  const first = new Date(timestamps[0] * 1000)
  const last = new Date(timestamps[timestamps.length - 1] * 1000)
  const spanSeconds = timestamps[timestamps.length - 1] - timestamps[0]

  const day = (d: Date) => d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
  const time = (d: Date) => d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })

  if (spanSeconds > 2 * 86_400) {
    return { left: day(first), right: day(last) }
  }
  if (first.toDateString() !== last.toDateString()) {
    return { left: `${day(first)} ${time(first)}`, right: `${day(last)} ${time(last)}` }
  }
  return { left: time(first), right: time(last) }
}
