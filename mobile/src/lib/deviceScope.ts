/**
 * Scoping a fleet-wide list to one device.
 *
 * Anomalies, Forecasts, Incidents and Reports are reachable two ways: from the
 * More menu (every device) and from a device's own screen (that one). The
 * difference is a single query parameter — every backing endpoint already
 * accepted `device_id` before this app used it, so none of this needed a
 * backend change.
 *
 * Pure and separate from the screens so the ?/& choice is tested once rather
 * than open-coded four times: `/forecasts` has no query string of its own and
 * `/incidents?status=open` does, and getting that wrong produces a URL the
 * server reads as an unfiltered request — a *wrong list*, not an error.
 */

export function withDeviceScope(path: string, deviceId: string | undefined): string {
  if (!deviceId) return path
  const separator = path.includes("?") ? "&" : "?"
  return `${path}${separator}device_id=${encodeURIComponent(deviceId)}`
}
