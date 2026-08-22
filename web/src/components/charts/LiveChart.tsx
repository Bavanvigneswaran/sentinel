import { useEffect, useRef } from "react"
import uPlot from "uplot"
import "uplot/dist/uPlot.min.css"

import type { RingBuffer } from "@/lib/ringBuffer"

export interface SeriesSpec {
  key: string
  label: string
  color: string
}

// A fixed timer rather than requestAnimationFrame — uPlot only needs to be
// told about new data a few times a second for 1Hz samples to read as
// smooth, and a timer keeps redrawing on a predictable cadence regardless of
// compositor/paint scheduling, which rAF is tied to and which can throttle
// or stall in ways unrelated to whether new data has actually arrived.
const REDRAW_INTERVAL_MS = 250

interface LiveChartProps {
  title: string
  /** Mutated in place by the caller's sample stream; read directly on a
   * timer tick rather than through React state — see lib/ringBuffer.ts. */
  buffer: React.RefObject<RingBuffer>
  height?: number
  /** Static series known upfront (CPU, memory). */
  series?: SeriesSpec[]
  /** Or derive series from whatever keys the buffer has actually seen (NICs,
   * disks, latency targets — names not known ahead of time). */
  deriveSeries?: (keys: string[]) => SeriesSpec[]
  valueFormatter?: (v: number) => string
}

function buildOptions(
  title: string,
  width: number,
  height: number,
  series: SeriesSpec[],
  valueFormatter?: (v: number) => string,
): uPlot.Options {
  return {
    title,
    width,
    height,
    padding: [12, 12, 0, 0],
    cursor: { drag: { x: false, y: false } },
    legend: { show: series.length > 0 },
    scales: { x: { time: true } },
    axes: [
      {},
      {
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
        // A missing value is a gap, not a dip to zero — the same no-synthesis
        // rule that governs every metric this product renders.
        spanGaps: false,
      })),
    ],
  }
}

export function LiveChart({
  title,
  buffer,
  height = 200,
  series,
  deriveSeries,
  valueFormatter,
}: LiveChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<uPlot | null>(null)
  const appliedKeysRef = useRef<string>("")

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const initialKeys = deriveSeries ? deriveSeries(buffer.current.seriesKeys()) : (series ?? [])
    appliedKeysRef.current = initialKeys.map((s) => s.key).join(",")

    const width = container.clientWidth || 600
    chartRef.current = new uPlot(
      buildOptions(title, width, height, initialKeys, valueFormatter),
      buffer.current.toAlignedData(initialKeys.map((s) => s.key)) as uPlot.AlignedData,
      container,
    )

    const resizeObserver = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width
      if (w && w > 0) chartRef.current?.setSize({ width: w, height })
    })
    resizeObserver.observe(container)

    const tick = () => {
      const currentBuffer = buffer.current
      const keys = deriveSeries
        ? deriveSeries(currentBuffer.seriesKeys()).map((s) => s.key)
        : (series ?? []).map((s) => s.key)
      const signature = keys.join(",")

      if (signature !== appliedKeysRef.current) {
        // The series set changed (a NIC appeared, a disk vanished) — uPlot
        // doesn't support arbitrary series removal after construction, so
        // rebuild. Rare in a live session: hardware doesn't usually change
        // mid-view.
        chartRef.current?.destroy()
        const specs = deriveSeries ? deriveSeries(currentBuffer.seriesKeys()) : (series ?? [])
        chartRef.current = new uPlot(
          buildOptions(title, container.clientWidth || width, height, specs, valueFormatter),
          currentBuffer.toAlignedData(keys) as uPlot.AlignedData,
          container,
        )
        appliedKeysRef.current = signature
      } else {
        // Default resetScales (omitted, defaults to true): every chart is
        // first constructed with empty data — real samples haven't arrived
        // yet — so the y-scale starts at [null, null]. Without letting
        // setData recompute it, the scale would never fit any data that
        // shows up later and the chart would stay visually blank forever
        // despite holding correct values.
        chartRef.current?.setData(currentBuffer.toAlignedData(keys) as uPlot.AlignedData)
      }
    }

    const interval = setInterval(tick, REDRAW_INTERVAL_MS)

    return () => {
      clearInterval(interval)
      resizeObserver.disconnect()
      chartRef.current?.destroy()
      chartRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- chart owns its own imperative lifecycle
  }, [title, height])

  return <div ref={containerRef} className="w-full" />
}
