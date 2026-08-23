/**
 * Push notifications, the account, and sign-out.
 *
 * A deliberately narrow subset of web/src/pages/SettingsPage.tsx. Email
 * channel config, the anomaly-sensitivity preference and Web Push enrolment
 * all stay on the web: they are account-wide settings you configure once, not
 * things you reach for on a phone. What is here is the one setting that can
 * only be made *from this device* — whether this phone receives push.
 */

import { useCallback, useEffect, useRef, useState } from "react"
import { StyleSheet, Text, View } from "react-native"

import { Screen } from "@/components/Screen"
import { Button, Card, CardTitle, ErrorNote } from "@/components/ui"
import { API_BASE_URL } from "@/config"
import { useCollectorStatus } from "@/hooks/useCollectorStatus"
import {
  PushPermissionDeniedError,
  currentPushState,
  disablePush,
  enablePush,
  watchTokenRotation,
  type PushState,
} from "@/lib/push"
import type { RootStackScreenProps } from "@/navigation/types"
import { useAuth } from "@/stores/auth"
import { spacing, text } from "@/theme"

export function SettingsScreen({ navigation }: RootStackScreenProps<"Settings">) {
  const user = useAuth((s) => s.user)
  const { status: collector } = useCollectorStatus()
  const logout = useAuth((s) => s.logout)

  const [push, setPush] = useState<PushState | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Read by the token-rotation listener, which must not re-register a device
  // the user never opted in on. A ref rather than the state value itself, so
  // the listener below can be registered once instead of being torn down and
  // rebuilt every time `push` changes.
  const enabledRef = useRef(false)
  useEffect(() => {
    enabledRef.current = push?.kind === "on"
  }, [push])

  const reload = useCallback(() => {
    currentPushState()
      .then(setPush)
      .catch(() => setPush({ kind: "unsupported", reason: "Push is unavailable in this build." }))
  }, [])

  useEffect(reload, [reload])
  useEffect(() => watchTokenRotation(() => enabledRef.current), [])

  const toggle = async () => {
    setBusy(true)
    setError(null)
    try {
      if (push?.kind === "on") {
        await disablePush()
      } else {
        await enablePush()
      }
      reload()
    } catch (err) {
      if (err instanceof PushPermissionDeniedError) {
        setError("Notifications are turned off for Sentinel in Android settings.")
      } else {
        setError(err instanceof Error ? err.message : "Could not change the push setting.")
      }
      reload()
    } finally {
      setBusy(false)
    }
  }

  return (
    <Screen title="Settings">
      <Card>
        <CardTitle>Account</CardTitle>
        <Text style={text.body}>{user?.display_name ?? user?.email ?? "—"}</Text>
        {user?.display_name && <Text style={text.small}>{user.email}</Text>}
        <Text style={text.tiny}>{API_BASE_URL}</Text>
      </Card>

      <Card>
        <CardTitle>Push notifications</CardTitle>
        <Text style={text.small}>{describe(push)}</Text>
        {error && <ErrorNote message={error} />}
        {(push?.kind === "on" || push?.kind === "off") && (
          <Button
            title={push.kind === "on" ? "Turn off on this phone" : "Turn on for this phone"}
            variant={push.kind === "on" ? "outline" : "primary"}
            busy={busy}
            onPress={() => void toggle()}
          />
        )}
        {push?.kind === "denied" && (
          <Text style={text.tiny}>
            Re-enable notifications for Sentinel in Android's app settings, then come back here.
          </Text>
        )}
        {push?.kind === "on" && (
          <View style={styles.tokenBox}>
            <Text style={text.tiny} numberOfLines={1} ellipsizeMode="middle">
              token {push.token}
            </Text>
          </View>
        )}
      </Card>

      <Card>
        <CardTitle>This phone as a device</CardTitle>
        <Text style={text.small}>
          {collector.enrolled
            ? collector.running
              ? "This phone is reporting its own metrics."
              : "Enrolled, but the collector is stopped."
            : "This phone can report its own memory, storage, battery and network alongside your other machines."}
        </Text>
        <Button
          title={collector.enrolled ? "Collector settings" : "Monitor this phone"}
          variant="outline"
          onPress={() => navigation.navigate("Collector")}
        />
      </Card>

      <Card>
        <CardTitle>Elsewhere</CardTitle>
        <Text style={text.small}>
          Alert rules, silence windows, email and Web Push channels, report schedules and
          per-device history live in the web console. Incidents, anomalies, forecasts and
          reports are here, under More or on a device's own screen.
        </Text>
      </Card>

      <Button title="Sign out" variant="destructive" onPress={() => void logout()} />
    </Screen>
  )
}

function describe(push: PushState | null): string {
  if (push === null) return "Checking…"
  switch (push.kind) {
    case "unsupported":
      return push.reason
    case "denied":
      return "Android is blocking notifications for Sentinel."
    case "off":
      return "This phone is not registered for alerts."
    case "on":
      return "This phone is registered. Firing and resolved alerts will arrive as notifications."
  }
}

const styles = StyleSheet.create({
  tokenBox: { paddingTop: spacing.xs },
})
