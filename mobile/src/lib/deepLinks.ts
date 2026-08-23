/**
 * Turning a notification's `data.url` into an in-app route.
 *
 * An allow-list, not a pass-through. `data.url` arrives over the network in an
 * FCM payload, and navigating to whatever string turns up there is not
 * something to do on trust — the same reflex CLAUDE.md applies to metric data
 * reaching the LLM. Anything unrecognised resolves to null and the tap just
 * opens the app.
 *
 * Pure, with no expo-notifications import, so navigation/linking.ts stays a
 * thin adapter and this stays testable.
 */

export const APP_SCHEME = "sentinel://"

/** Backend path (as sent in the push payload) → in-app route. The backend
 * sends "/alerts" for every alert notification today; the rest are here so a
 * future payload does not need a client release to be routable.
 *
 * A Map, not an object literal, and not by preference: an object lookup
 * inherits Object.prototype, so a payload of `{"url": "constructor"}` would
 * find a "route" and be navigated to. A Map has no prototype chain to walk. */
const ROUTES = new Map<string, string>([
  ["/", "fleet"],
  ["/alerts", "alerts"],
  ["/devices", "devices"],
  ["/settings", "settings"],
  ["/anomalies", "anomalies"],
  ["/forecasts", "forecasts"],
  ["/incidents", "incidents"],
  ["/reports", "reports"],
])

export function appUrlForPath(url: unknown): string | null {
  if (typeof url !== "string") return null
  const route = ROUTES.get(url)
  return route === undefined ? null : `${APP_SCHEME}${route}`
}
