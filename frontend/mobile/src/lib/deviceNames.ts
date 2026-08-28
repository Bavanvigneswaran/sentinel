import type { Device } from "@/types/api"

/**
 * Naming the device an alert, anomaly or incident happened on.
 *
 * Ported from web/src/lib/deviceNames.ts and deliberately identical: the
 * problem is a property of the data, not of the platform. These three lists
 * are *history*, and history outlives the machine it describes — a removed
 * device's firings are kept on purpose (unlike its forecasts, which
 * `/forecasts` filters server-side, because a forecast is a claim about a
 * future the machine no longer has). They just could not be *named*, because
 * the screens resolved names against `GET /devices`, which correctly omits
 * removed devices, so those rows rendered as a bare UUID.
 *
 * The two reasons a lookup can miss stay distinguishable: the device was
 * removed (permanent, explainable), or the device list has not loaded
 * (transient, and a short id is all there is to show).
 */

export const DEVICE_LIST_WITH_REMOVED = "/devices?include_removed=true"

export function indexDevices(devices: Device[]): Record<string, Device> {
  return Object.fromEntries(devices.map((d) => [d.id, d]))
}

/** A full UUID does not fit on a phone and its head is enough to tell two
 * apart. */
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
