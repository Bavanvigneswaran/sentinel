/**
 * Browser-side Web Push enrollment: register the service worker, request
 * permission, subscribe via the Push API, and hand the subscription to the
 * backend. All state the caller needs back is returned rather than stored
 * here — SettingsPage owns the UI state machine.
 */

import { apiFetch } from "@/lib/api"
import type { VapidPublicKey } from "@/types/notifications"

export type WebPushSupport = "unsupported" | "denied" | "ready"

export function webPushSupport(): WebPushSupport {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return "unsupported"
  if (Notification.permission === "denied") return "denied"
  return "ready"
}

function urlBase64ToUint8Array(base64Url: string): Uint8Array {
  const padding = "=".repeat((4 - (base64Url.length % 4)) % 4)
  const base64 = (base64Url + padding).replace(/-/g, "+").replace(/_/g, "/")
  const raw = atob(base64)
  return Uint8Array.from(raw, (char) => char.charCodeAt(0))
}

export class WebPushUnavailableError extends Error {}
export class WebPushPermissionDeniedError extends Error {}

/** Registers the service worker, requests permission, subscribes, and POSTs
 * the subscription to the backend. Throws WebPushUnavailableError if the
 * browser lacks support or the server has no VAPID key configured, and
 * WebPushPermissionDeniedError if the user declines the browser prompt. */
export async function enableWebPush(): Promise<void> {
  if (webPushSupport() === "unsupported") {
    throw new WebPushUnavailableError("This browser does not support push notifications.")
  }

  const { public_key } = await apiFetch<VapidPublicKey>("/notifications/vapid-public-key")
  if (!public_key) {
    throw new WebPushUnavailableError("The server has no push notification key configured.")
  }

  const permission = await Notification.requestPermission()
  if (permission !== "granted") {
    throw new WebPushPermissionDeniedError("Notification permission was not granted.")
  }

  const registration = await navigator.serviceWorker.register("/sw.js")
  await navigator.serviceWorker.ready

  const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    // TS's DOM lib types Uint8Array as generic over ArrayBufferLike (which
    // includes SharedArrayBuffer), while PushSubscriptionOptionsInit wants
    // one backed specifically by ArrayBuffer — always true here, since
    // Uint8Array.from never produces a shared buffer.
    applicationServerKey: urlBase64ToUint8Array(public_key) as BufferSource,
  })

  const json = subscription.toJSON()
  if (!json.endpoint || !json.keys?.p256dh || !json.keys?.auth) {
    throw new WebPushUnavailableError("The browser returned an incomplete subscription.")
  }

  await apiFetch("/notifications/web-push/subscribe", {
    method: "POST",
    body: { endpoint: json.endpoint, p256dh: json.keys.p256dh, auth: json.keys.auth },
  })
}

/** Unsubscribes locally and tells the backend to forget the subscription.
 * A no-op (not an error) if the browser never had one. */
export async function disableWebPush(): Promise<void> {
  if (!("serviceWorker" in navigator)) return
  const registration = await navigator.serviceWorker.getRegistration("/sw.js")
  const subscription = await registration?.pushManager.getSubscription()
  if (!subscription) return

  const endpoint = subscription.endpoint
  await subscription.unsubscribe()
  await apiFetch("/notifications/web-push/subscribe", {
    method: "DELETE",
    body: { endpoint },
  })
}
