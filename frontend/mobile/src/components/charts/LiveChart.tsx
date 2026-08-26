/**
 * The streaming chart. The React Native counterpart of
 * web/src/components/charts/LiveChart.tsx.
 *
 * uPlot is a canvas library bound to the DOM and does not exist here, so the
 * renderer is react-native-svg. What is deliberately *identical* is everything
 * around it:
 *
 * - Samples never pass through React state. The RingBuffer is mutated in place
 *   by useDeviceStream and read here on a timer tick, exactly as the web chart
 *   reads it on a requestAnimationFrame tick. Only the finished polyline
 *   strings — a few hundred bytes — reach setState.
 * - **A null is a gap**, and the y-scale is recomputed from the visible window
 *   on every tick. Both live in lib/chartFrame.ts, which is pure and tested;
 *   this file is SVG elements and a timer. The web chart gets its rescaling
 *   from uPlot's default `resetScales` on setData — the Phase 3 invariant
 *   about never passing `false` there exists because a chart first constructed
 *   empty otherwise never learns to scale to real data. A chart that derives
 *   its scale fresh each tick cannot have that bug.
 */

import { useEffect, useState } from "react"
import { StyleSheet, Text, View } from "react-native"
import Svg, { Line, Polyline } from "react-native-svg"

import { PlotFrame } from "@/components/charts/PlotFrame"
import { GRID_FRACTIONS } from "@/lib/plotGeometry"
import type { Frame, LiveSeries } from "@/lib/chartFrame"
import { EMPTY_FRAME, buildFrame } from "@/lib/chartFrame"
import type { RingBuffer } from "@/lib/ringBuffer"
import { colors, spacing, text } from "@/theme"

/** 1 Hz — the live sample rate. Redrawing faster only burns battery on a
 * chart whose data cannot change in between. */
const TICK_MS = 1000

interface LiveChartProps {
  buffer: React.RefObject<RingBuffer>
  height?: number
  /** A fixed set of series (CPU, memory). */
  series?: LiveSeries[]
  /** Or derive them from whatever keys the buffer has discovered — NICs,
   * disks and latency targets are only known once samples arrive. */
  deriveSeries?: (keys: string[]) => LiveSeries[]
  /** Percentages pin to 0–100; byte rates and RTTs autoscale. */
  domain?: [number, number]
  valueFormatter: (v: number) => string
}

export function LiveChart({
  buffer,
  height = 140,
  series,
  deriveSeries,
  domain,
  valueFormatter,
}: LiveChartProps) {
  const [frame, setFrame] = useState<Frame>(EMPTY_FRAME)
  // The *plot's* width, not the card's — PlotFrame reports it, because the
  // gutter holding the axis labels takes a fixed slice off the left. Building
  // points against the card width would push the newest sample under the
  // legend by exactly the gutter's width.
  const [width, setWidth] = useState(0)

  useEffect(() => {
    if (width <= 0) return
    const draw = () => {
      setFrame(buildFrame(buffer.current, { width, height, series, deriveSeries, domain }))
    }
    draw()
    const timer = setInterval(draw, TICK_MS)
    return () => clearInterval(timer)
    // `buffer` is a ref object read fresh on every tick, so it is not a dep;
    // `series`/`deriveSeries` are inline literals at every call site and would
    // restart the timer on each parent render if they were.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [width, height, domain?.[0], domain?.[1]])

  return (
    <View style={{ gap: spacing.sm }}>
      <PlotFrame
        height={height}
        hi={frame.hi}
        lo={frame.lo}
        valueFormatter={valueFormatter}
        empty={frame.empty}
        onWidth={setWidth}
      >
        {(plotWidth) => (
          <Svg width={plotWidth} height={height}>
            {!frame.empty &&
              GRID_FRACTIONS.map((fraction) => (
                <Line
                  key={fraction}
                  x1={0}
                  x2={plotWidth}
                  y1={height * fraction}
                  y2={height * fraction}
                  stroke={colors.border}
                  strokeWidth={StyleSheet.hairlineWidth}
                />
              ))}
            {frame.paths.map((runs, seriesIndex) =>
              runs.map((points, runIndex) => (
                <Polyline
                  key={`${seriesIndex}-${runIndex}`}
                  points={points}
                  fill="none"
                  stroke={frame.series[seriesIndex].color}
                  strokeWidth={1.5}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              )),
            )}
          </Svg>
        )}
      </PlotFrame>

      <View style={styles.legend}>
        {frame.series.map((s, index) => (
          <View key={s.key} style={styles.legendItem}>
            <View style={[styles.swatch, { backgroundColor: s.color }]} />
            <Text style={text.tiny} numberOfLines={1}>
              {s.label}
            </Text>
            {/* An "unavailable" here means this series has reported nothing in
                the visible window — never rendered as a zero. */}
            <Text style={[text.tiny, styles.legendValue]}>
              {latestOf(frame, index, valueFormatter) ?? "unavailable"}
            </Text>
          </View>
        ))}
      </View>
    </View>
  )
}

/** Formats a series' newest reading, or null when it has none. `latest` is
 * index-aligned with `series` by construction (see chartFrame.ts); the nullish
 * check covers both "never reported" and any future misalignment, rather than
 * letting an undefined reach the formatter and crash the screen. */
function latestOf(frame: Frame, index: number, format: (v: number) => string): string | null {
  const value = frame.latest[index]
  return value == null ? null : format(value)
}

const styles = StyleSheet.create({
  legend: { flexDirection: "row", flexWrap: "wrap", gap: spacing.md },
  legendItem: { flexDirection: "row", alignItems: "center", gap: spacing.xs, maxWidth: "48%" },
  swatch: { width: 8, height: 2, borderRadius: 1 },
  legendValue: { color: colors.foreground, fontVariant: ["tabular-nums"] },
})
