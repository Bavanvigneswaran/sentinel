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
import { StyleSheet } from "react-native"
import Svg, { Line, Polygon, Polyline } from "react-native-svg"

import { PlotFrame } from "@/components/charts/PlotFrame"
import { buildForecastFrame } from "@/lib/forecastChartFrame"
import { colors } from "@/theme"
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

  const frame = buildForecastFrame(points, { width, height, domain, ceiling })

  return (
    <PlotFrame
      height={height}
      hi={frame.hi}
      lo={frame.lo}
      valueFormatter={valueFormatter}
      empty={frame.empty}
      emptyLabel="Not enough history for a chart yet."
      onWidth={setWidth}
    >
      {(plotWidth) => (
        <Svg width={plotWidth} height={height}>
          {frame.referenceY !== null && (
            <Line
              x1={0}
              x2={plotWidth}
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
    </PlotFrame>
  )
}
