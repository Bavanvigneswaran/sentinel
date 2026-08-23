/**
 * "Monitor this phone" — the Phase 10b half of the app.
 *
 * Everything else in this app is a *viewer* of other machines. This screen is
 * the one place the phone is the subject: it enrols itself as a device and runs
 * the Kotlin foreground-service collector.
 *
 * Enrollment is one tap rather than a code to type. The user is already signed
 * in here, so the app mints a one-time enrollment code with its own access
 * token and hands *that* to the native module, which redeems it for an opaque,
 * device-scoped agent token. The collector never sees the JWT and never sees a
 * password — the same separation the desktop agent gets from a code copied out
 * of the web console, minus the copying.
 */

import * as Device from "expo-device"
import * as Notifications from "expo-notifications"
import { useCallback, useState } from "react"
import { Alert, StyleSheet, Text, View } from "react-native"

import { Screen } from "@/components/Screen"
import { Button, Card, CardTitle, ErrorNote } from "@/components/ui"
import { API_BASE_URL } from "@/config"
import { useCollectorStatus } from "@/hooks/useCollectorStatus"
import { apiFetch } from "@/lib/api"
import { formatRelative } from "@/lib/timeRanges"
import type { RootStackScreenProps } from "@/navigation/types"
import { colors, spacing, text } from "@/theme"
import {
  enroll,
  requestBatteryOptimizationExemption,
  start,
  stop,
  unenroll,
} from "sentinel-collector"

interface EnrollmentCode {
  id: string
  code: string
  expires_at: string
}

/**
 * The metrics a phone can never report, stated plainly rather than left for the
 * reader to infer from a screen full of "unavailable". The authority for this
 * list is docs/ANDROID_METRICS.md.
 */
const NEVER_MEASURABLE =
  "CPU usage, load average on most builds, per-process listings, disk I/O and open " +
  "connection counts are not readable by any app since Android 8 — they show as " +
  "unavailable rather than as zero."

export function CollectorScreen({ navigation }: RootStackScreenProps<"Collector">) {
  const { status, refresh, supported } = useCollectorStatus()
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const run = useCallback(
    async (label: string, action: () => Promise<unknown>) => {
      setBusy(label)
      setError(null)
      try {
        await action()
      } catch (err) {
        setError(err instanceof Error ? err.message : "Something went wrong.")
      } finally {
        setBusy(null)
        refresh()
      }
    },
    [refresh],
  )

  /**
   * Android 13+ gates notifications behind a runtime permission, and a
   * foreground service whose notification cannot be shown runs *invisibly* —
   * the service works, but the person holding the phone has no indication that
   * something is sampling it. That is precisely the situation the persistent
   * notification exists to prevent, so the permission is asked for at the
   * moment collecting starts.
   *
   * Not a blocker, though: a user who says no still gets a working collector,
   * and Android will let them turn the notification back on from app settings.
   * Refusing to monitor over a notification permission would be the wrong
   * trade.
   */
  const ensureNotificationPermission = async () => {
    try {
      const existing = await Notifications.getPermissionsAsync()
      if (existing.status !== "granted" && existing.canAskAgain) {
        await Notifications.requestPermissionsAsync()
      }
    } catch {
      // No notification stack in this build. The service is unaffected.
    }
  }

  const startCollecting = async () => {
    await ensureNotificationPermission()
    await start()
  }

  const enrolThisPhone = () =>
    run("enrol", async () => {
      // Minted with the signed-in user's own token, spent immediately by the
      // native module. It is short-lived and single-use, so nothing durable is
      // sitting in the JS heap even for the moment it exists there.
      const issued = await apiFetch<EnrollmentCode>("/enrollment-codes", {
        method: "POST",
        body: { ttl_seconds: 300 },
      })
      await enroll(API_BASE_URL, issued.code, defaultDeviceName())
      await startCollecting()
    })

  const confirmUnenrol = () =>
    Alert.alert(
      "Stop monitoring this phone?",
      "The collector stops and this phone forgets its agent token. The device and its " +
        "history stay on the server — revoke the token from the web console to retire it " +
        "for good.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Forget",
          style: "destructive",
          onPress: () => void run("unenrol", unenroll),
        },
      ],
    )

  if (!supported) {
    return (
      <Screen>
        <Card>
          <CardTitle>Not available in this build</CardTitle>
          <Text style={text.small}>
            The collector is a native Android module. Rebuild the dev build with{" "}
            <Text style={styles.mono}>make mobile-android</Text> to enable it.
          </Text>
        </Card>
      </Screen>
    )
  }

  return (
    <Screen>
      {error && <ErrorNote message={error} />}

      <Card>
        <CardTitle>This phone as a device</CardTitle>
        <Text style={text.small}>{describe(status)}</Text>

        {status.enrolled ? (
          <>
            <View style={styles.rows}>
              <Row label="Device" value={status.deviceName ?? "—"} />
              <Row label="Server" value={status.serverUrl || API_BASE_URL} />
              <Row
                label="Cadence"
                value={
                  status.connected
                    ? `every ${status.pushIntervalSeconds}s${status.mode === "live" ? " · live" : ""}`
                    : "—"
                }
              />
              <Row label="Last push" value={formatRelative(status.lastPushAt)} />
              <Row label="Last sample" value={formatRelative(status.lastSampleAt)} />
              {/* A backlog is the honest signal that pushes are failing, and it
                  is visible here before it is visible as a gap on a chart. */}
              <Row
                label="Buffered"
                value={
                  status.bufferedSamples === 0
                    ? "nothing waiting"
                    : `${status.bufferedSamples} sample${status.bufferedSamples === 1 ? "" : "s"}`
                }
              />
            </View>

            <Button
              title={status.running ? "Stop collecting" : "Start collecting"}
              variant={status.running ? "outline" : "primary"}
              busy={busy === "toggle"}
              onPress={() => void run("toggle", status.running ? stop : startCollecting)}
            />
            {status.deviceId && (
              <Button
                title="Open this device"
                variant="outline"
                onPress={() =>
                  navigation.navigate("Device", {
                    deviceId: status.deviceId as string,
                    deviceName: status.deviceName ?? "This phone",
                  })
                }
              />
            )}
            <Button
              title="Forget this phone"
              variant="destructive"
              busy={busy === "unenrol"}
              onPress={confirmUnenrol}
            />
          </>
        ) : (
          <Button
            title="Monitor this phone"
            busy={busy === "enrol"}
            onPress={() => void enrolThisPhone()}
          />
        )}
      </Card>

      {status.enrolled && !status.batteryOptimizationExempt && (
        <Card>
          <CardTitle>Battery optimisation</CardTitle>
          <Text style={text.small}>
            Android is allowed to defer this app's background work. The collector still runs,
            but pushes bunch up after the phone has been idle for a while, which shows on the
            charts as gaps followed by a burst. Exempting Sentinel keeps the 10s cadence
            steady.
          </Text>
          <Button
            title="Ask Android to exempt Sentinel"
            variant="outline"
            busy={busy === "battery"}
            onPress={() => void run("battery", requestBatteryOptimizationExemption)}
          />
        </Card>
      )}

      <Card>
        <CardTitle>What this phone can measure</CardTitle>
        <Text style={text.small}>
          Memory, storage, battery level and temperature, network throughput, round-trip
          latency and uptime — all real readings from this device.
        </Text>
        <Text style={[text.small, styles.spaced]}>{NEVER_MEASURABLE}</Text>
        {status.unavailableProbes.length > 0 && (
          <Text style={[text.tiny, styles.spaced]}>
            This device also refuses: {status.unavailableProbes.join(", ")}.
          </Text>
        )}
        <Text style={[text.tiny, styles.spaced]}>
          Its health score is computed from the components it did report, with their weights
          renormalised — not from zeroes standing in for the rest.
        </Text>
      </Card>
    </Screen>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.row}>
      <Text style={text.tiny}>{label}</Text>
      <Text style={[text.small, styles.rowValue]} numberOfLines={1} ellipsizeMode="middle">
        {value}
      </Text>
    </View>
  )
}

function describe(status: {
  enrolled: boolean
  running: boolean
  connected: boolean
  lastError: string | null
}): string {
  if (!status.enrolled) {
    return (
      "This phone is not reporting anything yet. Enrolling it creates a device on your " +
      "account and starts a foreground service that keeps sampling while the app is closed."
    )
  }
  if (!status.running) return "Enrolled, but the collector is stopped."
  if (status.connected) return "Collecting and connected."
  // The real transport message, not a generic "offline" — a revoked token and
  // an unreachable server need different answers from the user.
  return status.lastError ? `Reconnecting — ${status.lastError}` : "Reconnecting…"
}

/**
 * What the device is called on the server. The user-set device name first —
 * it is what the phone already calls itself everywhere else — with the model
 * as the fallback. The backend uniquifies it if the account already has one by
 * that name, so two identical handsets do not collide.
 */
function defaultDeviceName(): string {
  return Device.deviceName?.trim() || Device.modelName?.trim() || "Android phone"
}

const styles = StyleSheet.create({
  rows: { gap: spacing.xs, paddingVertical: spacing.xs },
  row: { flexDirection: "row", justifyContent: "space-between", gap: spacing.md },
  rowValue: { flex: 1, textAlign: "right", color: colors.foreground },
  spaced: { marginTop: spacing.xs },
  mono: { fontVariant: ["tabular-nums"], color: colors.foreground },
})
