/**
 * The maths behind a streaming chart, with no renderer in it.
 *
 * Split out of components/charts/LiveChart.tsx so the two rules that actually
 * matter — a null is a gap, and the y-scale is recomputed from the visible
 * window every tick — are testable in plain Node without a React Native
 * runtime. LiveChart.tsx is then only SVG elements and a timer.
 */

import type { RingBuffer } from "@/lib/ringBuffer"

/**
 * Points actually plotted — the phone's equivalent of the web chart's
 * `windowSeconds`, and the same decision for the same reason.
 *
 * This renderer has no time axis: x is the point's index, so the count *is*
 * the window. At the live 1s cadence 60 points is one minute, which matches
 * web's `DEFAULT_LIVE_WINDOW_SECONDS` — and, as there, the narrowest window is
 * chosen deliberately. Every window draws the same samples at the same rate;
 * all the count changes is how far one new sample moves the trace. At 180 that
 * step is a third of what it is at 60, and a chart advancing 1/180th of its
 * width per second reads as frozen even though it is drawing every point.
 *
 * There is no picker for this on the phone (there is one on the web page), so
 * this value is not a default somebody can move — which is the other half of
 * the argument for it being the lively end of the range rather than the middle.
 *
 * The buffer keeps its full history regardless; only the newest MAX_POINTS are
 * drawn.
 */
export const MAX_POINTS = 60

export interface LiveSeries {
  key: string
  label: string
  color: string
}

export interface Frame {
  series: LiveSeries[]
  /** One entry per series: its polyline runs, already in pixel space. A run
   * per unbroken stretch of readings — never one polyline bridging a null. */
  paths: string[][]
  /** Newest non-null reading per series; null when it has never reported
   * inside the visible window. Rendered as "unavailable", never as 0. */
  latest: (number | null)[]
  lo: number
  hi: number
  empty: boolean
}

export const EMPTY_FRAME: Frame = emptyFrameFor([])

/**
 * An empty frame that still knows its series, so the legend can render their
 * labels while the chart itself has nothing to draw.
 *
 * `paths` and `latest` MUST stay index-aligned with `series` even here. An
 * earlier version spread EMPTY_FRAME and overrode only `series`, leaving
 * `latest` as `[]` — so `latest[i]` was `undefined` rather than `null`, the
 * legend's `=== null` check missed it, and the value formatter was handed
 * undefined. It crashed the Live screen on resume from background, where a
 * freshly-reset buffer is exactly this state.
 */
export function emptyFrameFor(series: LiveSeries[]): Frame {
  return {
    series,
    paths: series.map(() => []),
    latest: series.map(() => null),
    lo: 0,
    hi: 1,
    empty: true,
  }
}

export interface FrameOptions {
  width: number
  height: number
  /** A fixed set of series (CPU, memory). */
  series?: LiveSeries[]
  /** Or derive them from whatever keys the buffer has discovered — NICs,
   * disks and latency targets are only known once samples arrive. */
  deriveSeries?: (keys: string[]) => LiveSeries[]
  /** Percentages pin to 0–100; byte rates and RTTs autoscale. */
  domain?: [number, number]
}

export function buildFrame(buffer: RingBuffer, opts: FrameOptions): Frame {
  const { width, height, domain } = opts
  const resolved = opts.series ?? opts.deriveSeries?.(buffer.seriesKeys()) ?? []
  if (resolved.length === 0) return EMPTY_FRAME

  const aligned = buffer.toAlignedData(resolved.map((s) => s.key))
  const total = aligned[0].length
  if (total === 0) return emptyFrameFor(resolved)

  const start = Math.max(0, total - MAX_POINTS)
  const columns = resolved.map((_, index) => aligned[index + 1].slice(start))
  const count = columns[0]?.length ?? 0

  const finite = columns.flat().filter((v): v is number => v !== null && Number.isFinite(v))
  if (finite.length === 0) return emptyFrameFor(resolved)

  // Autoscale to the visible window, with a little headroom so a flat line at
  // the maximum is not drawn on the frame edge. A percentage series passes a
  // fixed domain instead: a machine idling between 3% and 5% must not look
  // identical to one swinging between 20% and 90%.
  const lo = domain ? domain[0] : 0
  const hi = domain ? domain[1] : Math.max(...finite) * 1.1 || 1
  const span = hi - lo || 1

  const lastIndex = Math.max(count - 1, 1)
  const px = (i: number) => (i / lastIndex) * width
  const py = (v: number) => height - ((v - lo) / span) * height

  const paths = columns.map((column) => {
    const runs: string[] = []
    let current: string[] = []
    column.forEach((value, index) => {
      if (value === null || !Number.isFinite(value)) {
        // The gap. Close the run rather than bridging it: a straight line
        // across a disconnection would draw a slope through time nobody
        // measured. This is `spanGaps: false` restated for a renderer that
        // has no such option.
        if (current.length > 1) runs.push(current.join(" "))
        current = []
        return
      }
      current.push(`${px(index)},${py(value)}`)
    })
    if (current.length > 1) runs.push(current.join(" "))
    return runs
  })

  const latest = columns.map((column) => {
    for (let i = column.length - 1; i >= 0; i -= 1) {
      const value = column[i]
      if (value !== null && Number.isFinite(value)) return value
    }
    return null
  })

  return { series: resolved, paths, latest, lo, hi, empty: false }
}
