/**
 * A tiny inline-SVG trend line. No charting dependency: a sparkline is a
 * polyline and a viewBox, and one of these renders per device on the fleet
 * page — pulling a chart library into that path would cost far more than it
 * saves.
 *
 * The one rule that matters: **a null is a gap, never a zero and never a
 * straight line across.** A device that was disconnected for ten minutes did
 * not sit at the value it had before it vanished, and joining across the hole
 * would draw a slope through time nobody measured. Nulls therefore split the
 * data into separate polylines, exactly as `spanGaps: false` does on the live
 * uPlot charts.
 */

interface SparklineProps {
  values: (number | null)[]
  /** Fixed domain, e.g. [0, 100] for a percentage. Omit to fit the data. */
  domain?: [number, number]
  color?: string
  height?: number
  className?: string
  /** Read out to assistive tech in place of the drawing itself. */
  label?: string
}

/** Viewbox width. The SVG scales to its container; only the aspect ratio here
 * is real, and 240×40 is the shape a sparkline wants. */
const VIEW_WIDTH = 240

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
  color = "var(--color-chart-line, #3b82f6)",
  height = 40,
  className,
  label,
}: SparklineProps) {
  const present = values.filter((v): v is number => v !== null && !Number.isNaN(v))

  if (present.length === 0) {
    // Nothing was measured in this window. Say so rather than drawing a flat
    // line at the bottom of the box, which reads as "idle".
    return (
      <div
        className={className}
        style={{ height }}
        role="img"
        aria-label={label ? `${label}: no data` : "no data"}
      >
        <span className="text-xs text-muted-foreground">no data in this window</span>
      </div>
    )
  }

  const [low, high] = domain ?? [Math.min(...present), Math.max(...present)]
  // A flat series would divide by zero; centre it instead of stretching it to
  // fill the box, which would turn rounding noise into a dramatic waveform.
  const span = high - low || 1
  const stepX = values.length > 1 ? VIEW_WIDTH / (values.length - 1) : 0

  const toPoint = (p: { x: number; y: number }) =>
    `${(p.x * stepX).toFixed(2)},${(height - ((p.y - low) / span) * height).toFixed(2)}`

  return (
    <svg
      className={className}
      viewBox={`0 0 ${VIEW_WIDTH} ${height}`}
      width="100%"
      height={height}
      preserveAspectRatio="none"
      role="img"
      aria-label={label}
    >
      {segmentsOf(values).map((segment, index) =>
        segment.length === 1 ? (
          // A lone reading between two gaps has no line to be part of, but it
          // is still a real measurement and should not silently disappear.
          <circle
            key={index}
            cx={(segment[0].x * stepX).toFixed(2)}
            cy={(height - ((segment[0].y - low) / span) * height).toFixed(2)}
            r={1.5}
            fill={color}
          />
        ) : (
          <polyline
            key={index}
            points={segment.map(toPoint).join(" ")}
            fill="none"
            stroke={color}
            strokeWidth={1.5}
            strokeLinejoin="round"
            strokeLinecap="round"
            vectorEffect="non-scaling-stroke"
          />
        ),
      )}
    </svg>
  )
}
