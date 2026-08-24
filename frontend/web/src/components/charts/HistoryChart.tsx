import { useEffect, useMemo, useRef } from "react"
import uPlot from "uplot"
import "uplot/dist/uPlot.min.css"

import { resolveChartTheme, type ChartTheme } from "@/lib/chartColors"

/**
 * uPlot over a fixed array, for the history view.
 *
 * Deliberately not LiveChart: that one reads a ring buffer on a timer because
 * its data changes ten times a second. This one draws a finished query result
 * and only redraws when the range or the device changes, so there is no timer,
 * no buffer, and nothing to tear down between frames.
 *
 * `spanGaps: false` is the same no-synthesis rule the live charts follow. A
 * bucket with no reading is a hole in the line; joining across it would draw a
 * trend through a window the device was not reporting in.
 */

export interface HistorySeries {
  label: string
  color: string
  values: (number | null)[]
}

/**
 * A 24h-ahead prediction continuing past the real series' last timestamp.
 * Rendered as a dashed line (plus two thin dotted bounds) rather than a
 * filled band — a deliberate scope trim; see DeviceHistoryPage.
 */
export interface ForecastOverlay {
  label: string
  color: string
  /** Unix seconds, ascending, every one strictly after the last real
   * timestamp in `timestamps`. */
  timestamps: number[]
  predicted: (number | null)[]
  lower: (number | null)[]
  upper: (number | null)[]
}

interface HistoryChartProps {
  /** Unix seconds, ascending. */
  timestamps: number[]
  series: HistorySeries[]
  height?: number
  valueFormatter?: (v: number) => string
  /** Fixed y-domain, e.g. [0, 100] for a percentage. */
  domain?: [number, number]
  forecast?: ForecastOverlay
  /** A dashed horizontal line at this y-value — the ceiling a forecast is
   * heading towards (100% for a percentage metric). Drawn via a uPlot draw
   * hook rather than a fifth series, since it has no x-extent of its own and
   * a real series would need it padded, spanGaps-broken, or legend-hidden to
   * avoid rendering as a flat trend line the device never reported. */
  referenceLine?: number
}

function buildOptions(
  width: number,
  height: number,
  series: HistorySeries[],
  theme: ChartTheme,
  valueFormatter?: (v: number) => string,
  domain?: [number, number],
  forecast?: ForecastOverlay,
  referenceLine?: number,
): uPlot.Options {
  return {
    width,
    height,
    padding: [12, 12, 0, 0],
    legend: { show: series.length > 1 || forecast !== undefined },
    cursor: { drag: { x: false, y: false } },
    scales: {
      x: { time: true },
      y: domain ? { range: domain } : {},
    },
    hooks:
      referenceLine === undefined
        ? undefined
        : {
            draw: [
              (u: uPlot) => {
                const y = u.valToPos(referenceLine, "y", true)
                if (y < u.bbox.top || y > u.bbox.top + u.bbox.height) return
                const ctx = u.ctx
                ctx.save()
                // A canvas 2D context never resolves CSS — `currentColor` and
                // `color-mix()` are DOM/SVG concepts, and assigning either as
                // strokeStyle is silently a no-op rather than an error, which
                // is how this line went missing the first time: no exception,
                // just nothing drawn. --muted-foreground is a real resolved
                // colour read off the chart's own root, so it tracks the
                // light/dark theme the same way the CSS token does; the
                // transparency that color-mix would have added comes from
                // globalAlpha instead, which canvas actually understands.
                const muted = getComputedStyle(u.root).getPropertyValue("--muted-foreground")
                ctx.strokeStyle = muted || "#888"
                ctx.globalAlpha = 0.5
                ctx.setLineDash([4, 4])
                ctx.lineWidth = 1
                ctx.beginPath()
                ctx.moveTo(u.bbox.left, y)
                ctx.lineTo(u.bbox.left + u.bbox.width, y)
                ctx.stroke()
                ctx.restore()
              },
            ],
          },
    axes: [
      {
        stroke: theme.axisStroke,
        grid: { stroke: theme.gridStroke },
        ticks: { stroke: theme.gridStroke },
      },
      {
        stroke: theme.axisStroke,
        grid: { stroke: theme.gridStroke },
        ticks: { stroke: theme.gridStroke },
        values: valueFormatter ? (_u, vals) => vals.map((v) => valueFormatter(v)) : undefined,
      },
    ],
    series: [
      { label: "time" },
      ...series.map((s) => ({
        label: s.label,
        stroke: s.color,
        width: 1.5,
        points: { show: false },
        spanGaps: false,
      })),
      ...(forecast
        ? [
            {
              label: `${forecast.label} (forecast)`,
              stroke: forecast.color,
              width: 1.5,
              dash: [6, 4],
              points: { show: false },
              spanGaps: false,
            },
            {
              label: `${forecast.label} (low/high)`,
              stroke: forecast.color,
              width: 1,
              dash: [1, 3],
              points: { show: false },
              spanGaps: false,
            },
            {
              label: "",
              stroke: forecast.color,
              width: 1,
              dash: [1, 3],
              points: { show: false },
              spanGaps: false,
            },
          ]
        : []),
    ],
  }
}

export function HistoryChart({
  timestamps,
  series,
  height = 200,
  valueFormatter,
  domain,
  forecast,
  referenceLine,
}: HistoryChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<uPlot | null>(null)

  // The chart is rebuilt when the *shape* changes (a NIC appeared, a mount
  // vanished, a forecast series appeared/disappeared) and only re-fed when
  // the numbers change. uPlot cannot add or remove series after
  // construction, so the two cases genuinely differ.
  const shape = useMemo(
    () =>
      series.map((s) => `${s.label}:${s.color}`).join("|") +
      (forecast ? `|forecast:${forecast.label}:${forecast.color}` : "") +
      (referenceLine === undefined ? "" : `|ref:${referenceLine}`),
    [series, forecast, referenceLine],
  )

  const data = useMemo(() => {
    if (!forecast) {
      return [timestamps, ...series.map((s) => s.values)] as uPlot.AlignedData
    }
    const allTimestamps = [...timestamps, ...forecast.timestamps]
    const pad = (values: (number | null)[]) => [
      ...values,
      ...forecast.timestamps.map(() => null),
    ]
    const futurePad = timestamps.map(() => null)
    return [
      allTimestamps,
      ...series.map((s) => pad(s.values)),
      [...futurePad, ...forecast.predicted],
      [...futurePad, ...forecast.lower],
      [...futurePad, ...forecast.upper],
    ] as uPlot.AlignedData
  }, [timestamps, series, forecast])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const width = container.clientWidth || 600
    const theme = resolveChartTheme(container)
    chartRef.current = new uPlot(
      buildOptions(width, height, series, theme, valueFormatter, domain, forecast, referenceLine),
      data,
      container,
    )

    const resizeObserver = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width
      if (w && w > 0) chartRef.current?.setSize({ width: w, height })
    })
    resizeObserver.observe(container)

    return () => {
      resizeObserver.disconnect()
      chartRef.current?.destroy()
      chartRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- rebuild only on shape/geometry
  }, [shape, height])

  useEffect(() => {
    // Default resetScales: a range change can move the data an order of
    // magnitude, and holding the previous scale would draw the new window off
    // the top of the box. The same trap that kept the live charts blank in
    // Phase 3, from the other direction.
    chartRef.current?.setData(data)
  }, [data])

  return <div ref={containerRef} className="w-full" />
}
