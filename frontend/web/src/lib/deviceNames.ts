import type { Device } from "@/types/api"

/**
 * Naming the device an alert, anomaly or incident happened on.
 *
 * These three lists are *history*, and history can outlive the machine it
 * describes: a removed device's firings are kept deliberately (unlike its
 * forecasts, which are a claim about a future it no longer has, and which
 * `/forecasts` filters out server-side). What was not deliberate was how they
 * read — the pages resolved names against `GET /devices`, which correctly
 * omits removed devices, so every row belonging to one rendered as a bare
 * UUID with nothing to say why.
 *
 * The pages now ask for `?include_removed=true` and come here, so the two
 * genuinely different reasons a lookup can miss stay distinguishable:
 *
 * - the device was removed — a permanent, explainable state, and the row is
 *   still worth showing;
 * - the device list itself failed to load — transient, and the UUID is all
 *   there is until it retries.
 */

export const DEVICE_LIST_WITH_REMOVED = "/devices?include_removed=true"

export function indexDevices(devices: Device[]): Record<string, Device> {
  return Object.fromEntries(devices.map((d) => [d.id, d]))
}

/** A short, stable id to show when there is no name at all. A full UUID is
 * unreadable and its tail is what distinguishes two of them anyway. */
function shortId(deviceId: string): string {
  return deviceId.slice(0, 8)
}

export function deviceLabel(
  devicesById: Record<string, Device>,
  deviceId: string,
): string {
  const device = devicesById[deviceId]
  if (!device) return shortId(deviceId)
  return device.deleted_at ? `${device.name} (removed)` : device.name
}
