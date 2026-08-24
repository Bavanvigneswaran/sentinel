export interface ChartTheme {
  axisStroke: string
  gridStroke: string
}

/**
 * uPlot draws axis ticks, values and gridlines on a canvas, which — like the
 * dashed reference line in HistoryChart — never resolves CSS custom
 * properties: assigning `"var(--muted-foreground)"` as a canvas strokeStyle
 * is silently a no-op, not an error, and uPlot's own default axis stroke is
 * near-black. Against this app's always-dark background that rendered every
 * chart's axis values invisible. Resolving the real tokens once via
 * getComputedStyle (the same technique already proven for the forecast
 * ceiling line) is what makes axis text and gridlines track the theme
 * instead of uPlot's default.
 */
export function resolveChartTheme(el: Element): ChartTheme {
  const style = getComputedStyle(el)
  const muted = style.getPropertyValue("--muted-foreground").trim()
  const border = style.getPropertyValue("--border").trim()
  return {
    axisStroke: muted || "#888",
    gridStroke: border || "rgba(255, 255, 255, 0.12)",
  }
}

const PALETTE = [
  "#3b82f6", // blue
  "#f59e0b", // amber
  "#10b981", // emerald
  "#ef4444", // red
  "#8b5cf6", // violet
  "#06b6d4", // cyan
  "#ec4899", // pink
  "#84cc16", // lime
]

/** Deterministic color assignment so a NIC/disk/target keeps the same color
 * across renders even as other series come and go. */
export function colorForIndex(index: number): string {
  return PALETTE[index % PALETTE.length]
}
