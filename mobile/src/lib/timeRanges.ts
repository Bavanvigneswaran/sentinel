/**
 * The two duration formatters the fleet card needs, lifted from
 * web/src/lib/timeRanges.ts. The time-range presets and resolution helpers
 * stay behind with the history view, which is not part of the Phase 10a
 * viewer.
 */

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
