/**
 * Time-range presets and the duration formatters, ported from
 * web/src/lib/timeRanges.ts.
 *
 * The client picks a window; the server picks the resolution. That split is
 * deliberate — only the server knows what has survived retention — so nothing
 * here mentions a bucket width. `describeSource` exists to render whatever
 * the server chose back to the user.
 */

export interface TimeRange {
  key: string
  label: string
  seconds: number
}

/** Fewer presets than the web console's seven. A phone's picker is a row of
 * thumb-sized chips across ~330pt, and 90d/1y are windows you go to a desk
 * for — the same reasoning that put four screens behind a More tab rather
 * than into the bottom bar. */
export const TIME_RANGES: TimeRange[] = [
  { key: "1h", label: "1h", seconds: 3600 },
  { key: "6h", label: "6h", seconds: 6 * 3600 },
  { key: "24h", label: "24h", seconds: 24 * 3600 },
  { key: "7d", label: "7d", seconds: 7 * 86400 },
  { key: "30d", label: "30d", seconds: 30 * 86400 },
]

export const DEFAULT_RANGE = TIME_RANGES[2]

export function rangeByKey(key: string | null): TimeRange {
  return TIME_RANGES.find((r) => r.key === key) ?? DEFAULT_RANGE
}

/** "10s" / "2m" / "13h" — the width of one point, in the largest whole unit. */
export function describeResolution(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) {
    const minutes = seconds / 60
    return `${Number.isInteger(minutes) ? minutes : minutes.toFixed(1)}m`
  }
  const hours = seconds / 3600
  return `${Number.isInteger(hours) ? hours : hours.toFixed(1)}h`
}

/**
 * The sentence under a chart. Says what each point *is*, because a point from
 * the 1h rollup and a point from a raw sample are different measurements even
 * when they are drawn identically.
 */
export function describeSource(source: string, resolutionSeconds: number): string {
  const every = describeResolution(resolutionSeconds)
  return source === "raw"
    ? `Raw samples, averaged into ${every} buckets.`
    : `Averaged from the ${source} rollup into ${every} buckets.`
}

/** Relative "3m ago" for a timestamp, used on cards where an ISO string is noise. */
export function formatRelative(iso: string | null): string {
  if (!iso) return "never"
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return "just now"
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86_400)}d ago`
}

/** "6d 4h" — uptime, in the two largest units that are non-zero. */
export function formatDuration(seconds: number): string {
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (days > 0) return `${days}d ${hours}h`
  if (hours > 0) return `${hours}h ${minutes}m`
  return `${minutes}m`
}
