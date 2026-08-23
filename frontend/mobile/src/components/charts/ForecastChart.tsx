/**
 * A small, self-contained forecast chart: the predicted trend, its confidence
 * band, and (optionally) a dashed line at the ceiling it's heading towards.
 * The Android counterpart of web's HistoryChart used with no real series —
 * see frontend/web/src/pages/ForecastsPage.tsx.
 *
 * Deliberately not LiveChart: a stored MetricForecast.points array is a
 * finished, fixed-size result the worker wrote once, not something read off a
 * RingBuffer on a timer tick. buildForecastFrame() is a plain one-shot
 * transform rather than something recomputed every second.
 */

import { useState } from "react"
import { StyleSheet, Text, View, type LayoutChangeEvent } from "react-native"
import Svg, { Line, Polygon, Polyline } from "react-native-svg"

import { buildForecastFrame } from "@/lib/forecastChartFrame"
import { colors, radius, spacing, text } from "@/theme"
import type { ForecastPoint } from "@/types/forecast"

interface ForecastChartProps {
  points: ForecastPoint[]
  color: string
  height?: number
  domain: [number, number]
  ceiling?: number
  valueFormatter: (v: number) => string
}

export function ForecastChart({
  points,
  color,
  height = 90,
  domain,
  ceiling,
  valueFormatter,
}: ForecastChartProps) {
  const [width, setWidth] = useState(0)

  const onLayout = (event: LayoutChangeEvent) => setWidth(event.nativeEvent.layout.width)

  const frame = buildForecastFrame(points, { width, height, domain, ceiling })

  return (
    <View onLayout={onLayout} style={[styles.plot, { height }]}>
      {width > 0 && !frame.empty && (
        <Svg width={width} height={height}>
          {frame.referenceY !== null && (
            <Line
              x1={0}
              x2={width}
              y1={frame.referenceY}
              y2={frame.referenceY}
              stroke={colors.mutedDeep}
              strokeWidth={StyleSheet.hairlineWidth}
              strokeDasharray="4,4"
            />
          )}
          <Polygon points={frame.bandPolygon} fill={color} fillOpacity={0.15} stroke="none" />
          <Polyline
            points={frame.predictedPoints}
            fill="none"
            stroke={color}
            strokeWidth={1.5}
            strokeDasharray="5,4"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        </Svg>
      )}
      {frame.empty ? (
        <View style={styles.overlay} pointerEvents="none">
          <Text style={text.tiny}>Not enough history for a chart yet.</Text>
        </View>
      ) : (
        <View style={[styles.overlay, styles.axis]} pointerEvents="none">
          <Text style={text.tiny}>{valueFormatter(frame.hi)}</Text>
          <Text style={text.tiny}>{valueFormatter(frame.lo)}</Text>
        </View>
      )}
    </View>
  )
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
})
