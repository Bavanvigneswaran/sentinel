/**
 * A static multi-series chart over a time window. The Android counterpart of
 * web/src/components/charts/HistoryChart.tsx.
 *
 * uPlot does not run here, so the geometry lives in lib/historyChartFrame.ts
 * and this file is SVG elements and a legend.
 *
 * Deliberately without the web chart's forecast overlay: that overlay is a
 * dashed continuation past the newest real reading, and at a phone's width a
 * 24h projection drawn on top of a 30d window is a few pixels of dashes
 * nobody can read. The phone reaches forecasts through their own screen —
 * scoped to this device, from this device's own screen — where they get a
 * full-width chart each instead of a sliver.
 */

import { useState } from "react"
import { StyleSheet, Text, View } from "react-native"
import Svg, { Line, Polyline } from "react-native-svg"

import { PlotFrame } from "@/components/charts/PlotFrame"
import { GRID_FRACTIONS, GUTTER_WIDTH } from "@/lib/plotGeometry"
import {
  buildHistoryFrame,
  xAxisLabels,
  type HistorySeries,
} from "@/lib/historyChartFrame"
import { colors, spacing, text } from "@/theme"

interface HistoryChartProps {
  timestamps: number[]
  series: HistorySeries[]
  height?: number
  domain?: [number, number]
  valueFormatter: (v: number) => string
}

export function HistoryChart({
  timestamps,
  series,
  height = 150,
  domain,
  valueFormatter,
}: HistoryChartProps) {
  const [width, setWidth] = useState(0)

  const frame = buildHistoryFrame({ width, height, timestamps, series, domain })
  const axis = xAxisLabels(timestamps)

  return (
    <View style={{ gap: spacing.sm }}>
      <PlotFrame
        height={height}
        hi={frame.hi}
        lo={frame.lo}
        valueFormatter={valueFormatter}
        empty={frame.empty}
        emptyLabel="Nothing reported in this window."
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
                  stroke={series[seriesIndex].color}
                  strokeWidth={1.5}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              )),
            )}
          </Svg>
        )}
      </PlotFrame>

      {axis && !frame.empty && (
        <View style={styles.xAxis}>
          <Text style={text.tiny}>{axis.left}</Text>
          <Text style={text.tiny}>{axis.right}</Text>
        </View>
      )}

      <View style={styles.legend}>
        {series.map((s) => (
          <View key={s.label} style={styles.legendItem}>
            <View style={[styles.swatch, { backgroundColor: s.color }]} />
            <Text style={text.tiny} numberOfLines={1}>
              {s.label}
            </Text>
          </View>
        ))}
      </View>
    </View>
  )
}

const styles = StyleSheet.create({
  // Indented to line up with the plot, which starts after PlotFrame's gutter.
  // Taken from PlotFrame rather than repeated, so the two cannot drift.
  xAxis: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingLeft: GUTTER_WIDTH + spacing.xs,
  },
  legend: { flexDirection: "row", flexWrap: "wrap", gap: spacing.md },
  legendItem: { flexDirection: "row", alignItems: "center", gap: spacing.xs, maxWidth: "48%" },
  swatch: { width: 8, height: 2, borderRadius: 1 },
})
