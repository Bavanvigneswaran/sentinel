/**
 * Which headline readings a device's platform can actually answer.
 *
 * A pure module for the same reason `chartFrame.ts` is one: the decision is
 * testable without a React Native runtime, and the component is left with
 * nothing but the rendering. `src/lib/__tests__/deviceReadings.test.ts` is the
 * guard.
 *
 * The rule this encodes is CLAUDE.md's hard rule seen from the UI side. A
 * platform that cannot measure something reports null, and `MetricValue`
 * renders null as "unavailable" — correct, but a *whole grid* of "unavailable"
 * reads as a broken agent rather than a different kind of machine, and it
 * pushes out the metrics the phone genuinely does report. So the grid asks each
 * platform only the questions it can answer, and the screen states the rest in
 * words.
 *
 * The distinction that matters: a key is listed when the platform *could*
 * report it, not when it happens to have a value right now. A desktop keeps
 * "Disk read" on the grid even while it is null, because a null there means
 * "nothing measured in the freshness window" — real information. Android has no
 * disk-IO key at all, because there is no reading to wait for, ever.
 */

import type { Device } from "@/types/api"

export type ReadingKey =
  | "cpu"
  | "memory"
  | "swap"
  | "load1"
  | "battery"
  | "temperature"
  | "netIn"
  | "netOut"
  | "diskRead"
  | "diskWrite"
  | "rtt"
  | "packetLoss"
  | "processes"
  | "uptime"

const DESKTOP: readonly ReadingKey[] = [
  "cpu",
  "memory",
  "swap",
  "load1",
  "netIn",
  "netOut",
  "diskRead",
  "diskWrite",
  "rtt",
  "packetLoss",
  "processes",
  "uptime",
]

/**
 * Android's set. No CPU (/proc/stat denied from API 26), no load average on
 * most builds, no process enumeration, no per-block-device IO — and two the
 * desktop list has no use for.
 */
const ANDROID: readonly ReadingKey[] = [
  "memory",
  "swap",
  "battery",
  "temperature",
  "netIn",
  "netOut",
  "rtt",
  "packetLoss",
  "uptime",
]

export function readingKeysFor(platform: Device["platform"]): readonly ReadingKey[] {
  return platform === "android" ? ANDROID : DESKTOP
}

/**
 * What to say about the gap, for a platform that has one. Null for a platform
 * whose grid is complete — there is nothing to explain, and a reassuring note
 * saying so would just be noise.
 */
export function unmeasurableNoteFor(platform: Device["platform"]): string | null {
  if (platform !== "android") return null
  return (
    "Android exposes no CPU usage, load average, per-process listing or disk I/O to an " +
    "app, so this device reports none of them — they are excluded from its health score " +
    "rather than counted as healthy."
  )
}
