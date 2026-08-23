/**
 * The maths behind a small forecast-only chart, with no renderer in it —
 * same split as chartFrame.ts, for the same reason: the geometry is what's
 * worth testing without a React Native runtime, and the component that reads
 * this should be nothing but SVG elements.
 *
 * Unlike chartFrame.ts, there is no RingBuffer and no live tick here: a
 * MetricForecast's `points` array is a fixed, already-finished array the
 * worker wrote once, so this is a pure one-shot transform rather than
 * something recomputed on a timer.
 */

import type { ForecastPoint } from "@/types/forecast"

export interface ForecastFrame {
  /** The predicted line, as one polyline run (a stored forecast has no gaps —
   * the worker writes a dense array or nothing at all). Empty string when
   * there are no points. */
  predictedPoints: string
  /** A closed polygon tracing the upper bound forward then the lower bound
   * back — the confidence band, filled. Empty string when there are no
   * points. */
  bandPolygon: string
  /** Pixel y for `ceiling`, or null when the ceiling is off the visible
   * domain (or there is no ceiling to draw). */
  referenceY: number | null
  lo: number
  hi: number
  empty: boolean
}

export interface ForecastFrameOptions {
  width: number
  height: number
  /** Fixed y-domain — every metric this draws (mem/disk %) is a percentage,
   * pinned 0–100 the same way the fleet cards pin their sparklines, so a
   * forecast hovering at 3% is never drawn the same size as one at 90%. */
  domain: [number, number]
  /** The value a reference line should mark — 100 for "full", omitted for a
   * metric with no ceiling. */
  ceiling?: number
}

/** Vertical room reserved at the very top and bottom of the plot, in pixels.
 * Without it, a value sitting exactly on the domain's edge — the common case
 * here, since a metric's ceiling (100%) is drawn as `domain[1]` — maps to
 * y=0, which is the same pixel row as the plot container's own top border.
 * The two are visually indistinguishable at that point: not a missing line,
 * a line drawn exactly underneath an opaque border already occupying that
 * row. Found by reading a cropped, upscaled screenshot rather than trusting
 * a quick glance — the predicted trend line was rendering correctly all
 * along; only the reference line was ever actually missing. Same idea as
 * uPlot's `padding: [12, 12, 0, 0]` on the web chart this mirrors.
 */
const EDGE_PADDING = 6

export function buildForecastFrame(
  points: ForecastPoint[],
  opts: ForecastFrameOptions,
): ForecastFrame {
  const { width, height, domain, ceiling } = opts
  const [lo, hi] = domain
  const span = hi - lo || 1
  const plotHeight = Math.max(height - EDGE_PADDING * 2, 1)

  const py = (v: number) => EDGE_PADDING + plotHeight - ((v - lo) / span) * plotHeight
  const referenceY =
    ceiling === undefined || ceiling < lo || ceiling > hi ? null : py(ceiling)

  if (points.length === 0) {
    return { predictedPoints: "", bandPolygon: "", referenceY, lo, hi, empty: true }
  }

  const lastIndex = Math.max(points.length - 1, 1)
  const px = (i: number) => (i / lastIndex) * width

  const predictedPoints = points.map((p, i) => `${px(i)},${py(p.predicted)}`).join(" ")

  // Upper bound left-to-right, then lower bound right-to-left, closing the
  // shape into a single filled band rather than two separate dashed lines —
  // SVG has no equivalent of uPlot's "fill between two series" band fill, so
  // the polygon is built by hand here.
  const upperHalf = points.map((p, i) => `${px(i)},${py(p.upper)}`)
  const lowerHalf = [...points].reverse().map((p, i) => {
    const originalIndex = points.length - 1 - i
    return `${px(originalIndex)},${py(p.lower)}`
  })
  const bandPolygon = [...upperHalf, ...lowerHalf].join(" ")

  return { predictedPoints, bandPolygon, referenceY, lo, hi, empty: false }
}
