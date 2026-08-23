/**
 * A tiny trend line. Ported from web/src/components/Sparkline.tsx, inline SVG
 * to inline SVG — react-native-svg's <Polyline> takes the same points string
 * the DOM one does, so the segment maths is unchanged.
 *
 * The one rule that matters: **a null is a gap, never a zero and never a
 * straight line across.** A device that was disconnected for ten minutes did
 * not sit at the value it had before it vanished, and joining across the hole
 * would draw a slope through time nobody measured. Nulls therefore split the
 * data into separate polylines, exactly as `spanGaps: false` does on the live
 * charts.
 */

import { useState } from "react"
import { View } from "react-native"
import Svg, { Circle, Polyline } from "react-native-svg"

import { colors } from "@/theme"

interface SparklineProps {
  values: (number | null)[]
  /** Fixed domain, e.g. [0, 100] for a percentage. Omit to fit the data. */
  domain?: [number, number]
  color?: string
  height?: number
  /** Read out to assistive tech in place of the drawing itself. */
  label?: string
}

function segmentsOf(values: (number | null)[]): { x: number; y: number }[][] {
  const segments: { x: number; y: number }[][] = []
  let current: { x: number; y: number }[] = []
  values.forEach((value, index) => {
    if (value === null || Number.isNaN(value)) {
      if (current.length > 0) segments.push(current)
      current = []
      return
    }
    current.push({ x: index, y: value })
  })
  if (current.length > 0) segments.push(current)
  return segments
}

export function Sparkline({
  values,
  domain,
  color = colors.primary,
  height = 36,
  label,
}: SparklineProps) {
  // Measured rather than assumed: unlike an SVG in the DOM, react-native-svg
  // needs real pixel dimensions to place points, and a phone's card width
  // depends on the device.
  const [width, setWidth] = useState(0)

  const segments = segmentsOf(values)
  const finite = values.filter((v): v is number => v !== null && Number.isFinite(v))

  const [lo, hi] = domain ?? [Math.min(...finite, 0), Math.max(...finite, 1)]
  const span = hi - lo || 1
  const lastIndex = Math.max(values.length - 1, 1)

  const px = (x: number) => (x / lastIndex) * width
  const py = (y: number) => height - ((y - lo) / span) * height

  const lastPoint = segments.at(-1)?.at(-1)

  return (
    <View
      accessible
      accessibilityLabel={
        finite.length === 0 ? `${label ?? "Trend"}: no readings` : `${label ?? "Trend"} sparkline`
      }
      onLayout={(event) => setWidth(event.nativeEvent.layout.width)}
      style={{ height }}
    >
      {width > 0 && (
        <Svg width={width} height={height}>
          {segments.map((segment, index) => (
            <Polyline
              key={index}
              points={segment.map((p) => `${px(p.x)},${py(p.y)}`).join(" ")}
              fill="none"
              stroke={color}
              strokeWidth={1.5}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          ))}
          {lastPoint && <Circle cx={px(lastPoint.x)} cy={py(lastPoint.y)} r={2} fill={color} />}
        </Svg>
      )}
    </View>
  )
}
