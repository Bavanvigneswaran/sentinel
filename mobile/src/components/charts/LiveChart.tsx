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

import { useEffect, useRef, useState } from "react"
import { StyleSheet, Text, View, type LayoutChangeEvent } from "react-native"
import Svg, { Line, Polyline } from "react-native-svg"

import type { Frame, LiveSeries } from "@/lib/chartFrame"
import { EMPTY_FRAME, buildFrame } from "@/lib/chartFrame"
import type { RingBuffer } from "@/lib/ringBuffer"
import { colors, radius, spacing, text } from "@/theme"

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
  const [width, setWidth] = useState(0)
  const [frame, setFrame] = useState<Frame>(EMPTY_FRAME)
  const widthRef = useRef(0)

  useEffect(() => {
    const draw = () => {
      const w = widthRef.current
      if (w <= 0) return
      setFrame(buildFrame(buffer.current, { width: w, height, series, deriveSeries, domain }))
    }
    draw()
    const timer = setInterval(draw, TICK_MS)
    return () => clearInterval(timer)
    // `buffer` is a ref object read fresh on every tick, so it is not a dep;
    // `series`/`deriveSeries` are inline literals at every call site and would
    // restart the timer on each parent render if they were.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [height, domain?.[0], domain?.[1]])

  const onLayout = (event: LayoutChangeEvent) => {
    const next = event.nativeEvent.layout.width
    widthRef.current = next
    setWidth(next)
  }

  return (
    <View style={{ gap: spacing.sm }}>
      <View onLayout={onLayout} style={[styles.plot, { height }]}>
        {width > 0 && (
          <Svg width={width} height={height}>
            {gridLines(frame, width, height).map((y, index) => (
              <Line
                key={index}
                x1={0}
                x2={width}
                y1={y}
                y2={y}
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
        {frame.empty ? (
          <View style={styles.overlay} pointerEvents="none">
            <Text style={text.small}>No data yet.</Text>
          </View>
        ) : (
          <View style={[styles.overlay, styles.axis]} pointerEvents="none">
            <Text style={text.tiny}>{valueFormatter(frame.hi)}</Text>
            <Text style={text.tiny}>{valueFormatter(frame.lo)}</Text>
          </View>
        )}
      </View>

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

function gridLines(frame: Frame, width: number, height: number): number[] {
  if (frame.empty || width <= 0) return []
  return [0.25, 0.5, 0.75].map((fraction) => height * fraction)
}

const styles = StyleSheet.create({
  plot: {
    backgroundColor: colors.background,
    borderRadius: radius.sm,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    overflow: "hidden",
  },
  overlay: { ...StyleSheet.absoluteFill, alignItems: "center", justifyContent: "center" },
  axis: {
    alignItems: "flex-start",
    justifyContent: "space-between",
    paddingHorizontal: spacing.xs,
    paddingVertical: 2,
  },
  legend: { flexDirection: "row", flexWrap: "wrap", gap: spacing.md },
  legendItem: { flexDirection: "row", alignItems: "center", gap: spacing.xs, maxWidth: "48%" },
  swatch: { width: 8, height: 2, borderRadius: 1 },
  legendValue: { color: colors.foreground, fontVariant: ["tabular-nums"] },
})
