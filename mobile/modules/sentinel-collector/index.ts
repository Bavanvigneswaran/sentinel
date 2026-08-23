/**
 * `sentinel-collector` — the phone as a monitored device.
 *
 * A thin remote control over the Kotlin foreground service in `android/`. The
 * service speaks the agent WebSocket protocol itself; nothing here is on the
 * data path, which is the whole point — the JS runtime is gone when the app is
 * backgrounded or closed, and that is exactly when monitoring has to continue.
 *
 * Android-only. `isSupported` is false everywhere else and every call throws a
 * clear error rather than silently doing nothing, the same "unconfigured must
 * degrade visibly" posture the backend takes for SMTP, VAPID and FCM.
 */
import { Platform } from "react-native"
import { requireOptionalNativeModule } from "expo"
import type { EventSubscription } from "expo-modules-core"

import type { CollectorStatus, SentinelCollectorEvents } from "./src/SentinelCollector.types"

export type { CollectorStatus } from "./src/SentinelCollector.types"

interface SentinelCollectorNativeModule {
  enroll(serverUrl: string, code: string, deviceName: string): Promise<string>
  unenroll(): Promise<void>
  start(): Promise<void>
  stop(): Promise<void>
  status(): CollectorStatus
  requestBatteryOptimizationExemption(): Promise<boolean>
  addListener<K extends keyof SentinelCollectorEvents>(
    event: K,
    listener: SentinelCollectorEvents[K],
  ): EventSubscription
}

// requireOptionalNativeModule, not requireNativeModule: this file is imported
// by screens that also run under Expo Go and on iOS, where the native side does
// not exist. A missing module is a supported state, not a crash.
const native = requireOptionalNativeModule<SentinelCollectorNativeModule>("SentinelCollector")

export const isSupported: boolean = Platform.OS === "android" && native !== null

/** The status of a device that has no collector at all — never a fake "stopped
 *  but fine". Everything is false and nothing claims to have been measured. */
export const UNSUPPORTED_STATUS: CollectorStatus = {
  enrolled: false,
  running: false,
  connected: false,
  deviceId: null,
  deviceName: null,
  serverUrl: "",
  mode: "normal",
  pushIntervalSeconds: 10,
  bufferedSamples: 0,
  lastPushAt: null,
  lastSampleAt: null,
  lastError: null,
  unavailableProbes: [],
  batteryOptimizationExempt: false,
}

function requireNative(): SentinelCollectorNativeModule {
  if (!native) {
    throw new Error(
      "The Sentinel collector is only available in the Android dev build. " +
        "Rebuild with `make mobile-android`.",
    )
  }
  return native
}

/**
 * Exchange a one-time enrollment code for an agent token, sealed in the
 * Android Keystore. Returns the server's device id.
 *
 * Does not start collecting: enrolling and running are separate decisions.
 */
export async function enroll(
  serverUrl: string,
  code: string,
  deviceName: string,
): Promise<string> {
  return requireNative().enroll(serverUrl, code, deviceName)
}

/** Forget the token locally. The device row, its history and its token all
 *  survive on the server — revoke from the web console to kill the token. */
export async function unenroll(): Promise<void> {
  return requireNative().unenroll()
}

export async function start(): Promise<void> {
  return requireNative().start()
}

export async function stop(): Promise<void> {
  return requireNative().stop()
}

export function status(): CollectorStatus {
  return native ? native.status() : UNSUPPORTED_STATUS
}

/** Opens the system dialog. Resolves true only if the exemption was already
 *  held — the user's answer arrives later, via the next `status()`. */
export async function requestBatteryOptimizationExemption(): Promise<boolean> {
  return requireNative().requestBatteryOptimizationExemption()
}

export function addStatusListener(
  listener: (status: CollectorStatus) => void,
): EventSubscription | null {
  return native ? native.addListener("onCollectorStatus", listener) : null
}
