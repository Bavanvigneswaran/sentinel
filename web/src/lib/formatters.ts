export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes.toFixed(0)} B`
  const units = ["KB", "MB", "GB", "TB", "PB"]
  let value = bytes
  let unitIndex = -1
  do {
    value /= 1024
    unitIndex += 1
  } while (value >= 1024 && unitIndex < units.length - 1)
  return `${value.toFixed(1)} ${units[unitIndex]}`
}

export function formatBytesPerSecond(bytesPerSecond: number): string {
  return `${formatBytes(bytesPerSecond)}/s`
}

export function formatMs(ms: number): string {
  return `${ms.toFixed(1)} ms`
}

/** A future ISO timestamp as a rough "in N days/hours" duration from now. */
export function formatDaysUntil(iso: string): string {
  const seconds = (new Date(iso).getTime() - Date.now()) / 1000
  if (seconds <= 0) return "already past"
  if (seconds < 3600) return `in ${Math.round(seconds / 60)}m`
  if (seconds < 86_400) return `in ${Math.round(seconds / 3600)}h`
  const days = seconds / 86_400
  return `in ${days < 10 ? days.toFixed(1) : Math.round(days)} days`
}
