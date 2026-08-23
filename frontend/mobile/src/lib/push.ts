/**
 * FCM enrolment, from the app's side.
 *
 * `expo-notifications` rather than `@react-native-firebase/messaging`: on
 * Android `getDevicePushTokenAsync()` returns the *native* FCM registration
 * token — the same string a Firebase SDK would hand you — so the backend still
 * talks plain FCM HTTP v1 and the app avoids a second Firebase runtime it has
 * no other use for. Nothing here goes through Expo's push service.
 *
 * Registration is the enable flag. There is deliberately no `fcm_enabled`
 * column beside the backend's `web_push_enabled`: a settings row saying "on"
 * with no device token registered would be a lie the UI would happily render,
 * and the two would drift the first time someone revoked the OS permission.
 * A registered token means opted in; deleting it means opted out.
 *
 * Everything degrades rather than throwing when push is not configured, the
 * same posture the backend takes when VAPID or SMTP is unset.
 */

import * as Device from "expo-device"
import * as Notifications from "expo-notifications"
import { Platform } from "react-native"

import { HAS_FIREBASE_CONFIG } from "@/config"
import { apiFetch } from "@/lib/api"
import type { FcmRegisterRequest } from "@/types/notifications"

const CHANNEL_ID = "alerts"

export type PushState =
  | { kind: "unsupported"; reason: string }
  | { kind: "denied" }
  | { kind: "off" }
  | { kind: "on"; token: string }

/** Foreground behaviour. An alert arriving while someone is staring at the
 * fleet screen should still surface — they may be looking at a different
 * device than the one that just broke. */
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
})

export async function ensureChannel(): Promise<void> {
  if (Platform.OS !== "android") return
  await Notifications.setNotificationChannelAsync(CHANNEL_ID, {
    name: "Alerts",
    importance: Notifications.AndroidImportance.HIGH,
    lightColor: "#3b82f6",
    vibrationPattern: [0, 250, 250, 250],
  })
}

function unsupportedReason(): string | null {
  if (!Device.isDevice) {
    // An emulator without Play Services cannot obtain an FCM token at all.
    return "Push needs a real device (or an emulator image with Google Play services)."
  }
  if (!HAS_FIREBASE_CONFIG) {
    return "This build has no google-services.json, so it cannot obtain an FCM token."
  }
  return null
}

/** What to show in Settings, without prompting for anything. */
export async function currentPushState(): Promise<PushState> {
  const reason = unsupportedReason()
  if (reason) return { kind: "unsupported", reason }

  const permissions = await Notifications.getPermissionsAsync()
  if (permissions.status === "denied") return { kind: "denied" }
  if (permissions.status !== "granted") return { kind: "off" }

  const token = await safeDeviceToken()
  return token ? { kind: "on", token } : { kind: "off" }
}

async function safeDeviceToken(): Promise<string | null> {
  try {
    const token = await Notifications.getDevicePushTokenAsync()
    return typeof token.data === "string" ? token.data : null
  } catch {
    // Play Services missing, Firebase misconfigured, no network on first
    // registration — all of them mean "no token", none of them mean "crash".
    return null
  }
}

export class PushPermissionDeniedError extends Error {}
export class PushUnavailableError extends Error {}

/** Request permission, obtain the FCM token, and register it with the backend.
 * Returns the token so Settings can show the state it just reached. */
export async function enablePush(): Promise<string> {
  const reason = unsupportedReason()
  if (reason) throw new PushUnavailableError(reason)

  await ensureChannel()

  const existing = await Notifications.getPermissionsAsync()
  const granted =
    existing.status === "granted"
      ? existing
      : await Notifications.requestPermissionsAsync()
  if (granted.status !== "granted") {
    throw new PushPermissionDeniedError("Notification permission was not granted.")
  }

  const token = await safeDeviceToken()
  if (!token) {
    throw new PushUnavailableError("Firebase did not return a registration token.")
  }

  await registerToken(token)
  return token
}

async function registerToken(token: string): Promise<void> {
  const body: FcmRegisterRequest = {
    token,
    device_label: Device.deviceName ?? Device.modelName ?? null,
  }
  await apiFetch("/notifications/fcm/register", { method: "POST", body })
}

/** Tell the backend to forget this device. The OS-level permission is left
 * alone: revoking that is the user's business, in system settings. */
export async function disablePush(): Promise<void> {
  const token = await safeDeviceToken()
  if (!token) return
  await apiFetch("/notifications/fcm/register", { method: "DELETE", body: { token } })
}

/**
 * FCM rotates registration tokens on its own schedule (app restore, data
 * clear, its own housekeeping). A rotated token silently stops receiving
 * anything unless the new one is re-registered, so this listener is not
 * optional bookkeeping — it is what stops push from quietly dying weeks later.
 *
 * Re-registers only if the backend already knew about this device: `enabled`
 * is passed in so a user who never opted in does not get silently enrolled by
 * a token rotation.
 */
export function watchTokenRotation(isEnabled: () => boolean): () => void {
  const subscription = Notifications.addPushTokenListener((token) => {
    if (!isEnabled()) return
    if (typeof token.data !== "string") return
    void registerToken(token.data).catch(() => {
      // Best-effort, same as every other notification path in this product.
      // The next Settings visit re-registers.
    })
  })
  return () => subscription.remove()
}
