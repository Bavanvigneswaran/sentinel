/**
 * Alert rules — the list, and creating, editing and deleting them. The phone
 * counterpart of web/src/pages/AlertRulesPage.tsx.
 *
 * This was left in the web console through Phase 10a–13 on the reasoning that
 * rule authoring is desk work. Triage is not, though, and the two are the same
 * loop: an alert fires at an awkward time, and the useful response is often
 * "that threshold is wrong" — which previously meant waiting until you were
 * back at a computer. Silencing was already possible from here; editing the
 * thing being silenced was not.
 */

import { useCallback, useEffect, useState } from "react"
import { Alert, StyleSheet, Text, View } from "react-native"

import { RuleForm } from "@/components/RuleForm"
import { EmptyState, Screen } from "@/components/Screen"
import { Button, Card, ErrorNote } from "@/components/ui"
import { apiFetch } from "@/lib/api"
import { spacing, text } from "@/theme"
import type { AlertRule } from "@/types/alerts"
import type { Device } from "@/types/api"
import type { NotificationSettings } from "@/types/notifications"
import type { RootStackScreenProps } from "@/navigation/types"

export function AlertRulesScreen({ route }: RootStackScreenProps<"AlertRules">) {
  const deviceId = route.params?.deviceId
  const deviceName = route.params?.deviceName

  const [rules, setRules] = useState<AlertRule[] | null>(null)
  const [devices, setDevices] = useState<Device[]>([])
  const [sensitivity, setSensitivity] =
    useState<NotificationSettings["anomaly_sensitivity"] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  const load = useCallback(async (viaGesture = false) => {
    if (viaGesture) setRefreshing(true)
    try {
      // `/alerts/rules` has no device_id filter — unlike events, forecasts and
      // incidents, a rule can legitimately apply to *every* device
      // (device_id null), so a server-side filter would have to decide whether
      // those count. Filtering here keeps that decision visible: a device's
      // list shows its own rules plus the fleet-wide ones that also govern it.
      const data = await apiFetch<AlertRule[]>("/alerts/rules")
      setRules(data)
      setError(null)
    } catch {
      setError("Could not load your alert rules.")
    } finally {
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    // `load` is async, so every setState inside it lands after an await, on
    // the HTTP response — the network IS the external system this effect
    // exists to synchronise with. Same false positive, same wording, as
    // hooks/usePolledResource.ts.
    // oxlint-disable-next-line react/set-state-in-effect
    void load()
    apiFetch<Device[]>("/devices")
      .then(setDevices)
      .catch(() => {
        // Non-fatal: rule creation still works, just without a device picker.
      })
    // Display only — editing sensitivity stays on Settings. Shown beside each
    // anomaly rule so its firing behaviour is not a mystery.
    apiFetch<NotificationSettings>("/notifications/settings")
      .then((s) => setSensitivity(s.anomaly_sensitivity))
      .catch(() => {
        // Non-fatal: anomaly rules just won't show a sensitivity caption.
      })
  }, [load])

  const confirmDelete = (rule: AlertRule) =>
    Alert.alert(
      `Delete ${rule.name}?`,
      "The rule stops being evaluated. Alerts it has already fired are kept — they are a record of something that really happened.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Delete",
          style: "destructive",
          onPress: () => {
            void apiFetch(`/alerts/rules/${rule.id}`, { method: "DELETE" })
              .then(() => load())
              .catch(() => setError("Could not delete that rule."))
          },
        },
      ],
    )

  const visible =
    rules === null
      ? null
      : deviceId
        ? rules.filter((r) => r.device_id === deviceId || r.device_id === null)
        : rules

  const deviceLabelFor = (id: string | null) =>
    id === null ? "All devices" : (devices.find((d) => d.id === id)?.name ?? id.slice(0, 8))

  // Same split as the web console's rules page, for the same reason: every
  // account is seeded with a default set so it detects things before anybody
  // configures it, and eight unexplained rules mixed in with your own read as
  // clutter rather than as help.
  const builtin = visible?.filter((r) => r.source === "builtin") ?? []
  const mine = visible?.filter((r) => r.source !== "builtin") ?? []

  const renderRule = (rule: AlertRule) =>
        editingId === rule.id ? (
          <Card key={rule.id}>
            <Text style={text.heading}>Edit rule</Text>
            <RuleForm
              devices={devices}
              rule={rule}
              onSaved={() => {
                setEditingId(null)
                void load()
              }}
              onCancel={() => setEditingId(null)}
            />
          </Card>
        ) : (
          <Card key={rule.id}>
            <View style={styles.head}>
              <Text style={text.heading} numberOfLines={1}>
                {rule.name}
              </Text>
              {!rule.enabled && <Text style={text.tiny}>disabled</Text>}
            </View>
            <Text style={text.small}>
              {rule.rule_type === "anomaly"
                ? `${deviceLabelFor(rule.device_id)} · ${rule.metric} anomaly detection` +
                  (sensitivity ? ` · sensitivity: ${sensitivity}` : "")
                : rule.rule_type === "multivariate"
                  ? // `novelty_score` is a schema detail, not something to show a
                    // person, and its threshold is a percentile rather than a
                    // bare number — so it reads "/100" here.
                    `${deviceLabelFor(rule.device_id)} · unusual combination of readings ` +
                    `${rule.comparison} ${rule.threshold}/100 for ${rule.for_duration_seconds}s`
                  : `${deviceLabelFor(rule.device_id)} · ${rule.metric} ` +
                    (rule.rule_type === "forecast" ? "predicted " : "") +
                    `${rule.comparison} ${rule.threshold} for ${rule.for_duration_seconds}s`}
            </Text>
            <View style={styles.actions}>
              <Button
                size="sm"
                variant="outline"
                title="Edit"
                onPress={() => {
                  setCreating(false)
                  setEditingId(rule.id)
                }}
              />
              <Button
                size="sm"
                variant="ghost"
                title="Delete"
                onPress={() => confirmDelete(rule)}
              />
            </View>
          </Card>
        )

  return (
    <Screen refreshing={refreshing} onRefresh={() => void load(true)}>
      {deviceName && (
        <Text style={text.tiny}>
          Showing {deviceName}'s rules and the fleet-wide ones that also apply to it.
        </Text>
      )}
      <Text style={text.small}>
        Static thresholds, adaptive anomaly rules and forecast rules — the three sources that
        drive alerts triage.
      </Text>

      {error && <ErrorNote message={error} />}

      {!creating && editingId === null && (
        <Button title="New rule" onPress={() => setCreating(true)} />
      )}

      {creating && (
        <Card>
          <Text style={text.heading}>New rule</Text>
          <RuleForm
            devices={devices}
            onSaved={() => {
              setCreating(false)
              void load()
            }}
            onCancel={() => setCreating(false)}
          />
        </Card>
      )}

      {visible !== null && mine.length === 0 && !creating && (
        <EmptyState
          title="You haven't written any rules"
          body={
            builtin.length > 0
              ? "The automatic rules below are already watching every machine. Add your own for anything specific to your setup."
              : "Create one to start getting alerted on real metrics."
          }
        />
      )}

      {mine.length > 0 && <Text style={text.tiny}>YOUR RULES</Text>}
      {mine.map(renderRule)}

      {builtin.length > 0 && (
        <>
          <Text style={text.tiny}>AUTOMATIC</Text>
          <Text style={text.tiny}>
            Set up with your account so it detects problems without being configured. They
            apply to every machine you enrol, including ones added later. Edit or disable any
            of them — they are ordinary rules, not something locked.
          </Text>
        </>
      )}
      {builtin.map(renderRule)}
    </Screen>
  )
}

const styles = StyleSheet.create({
  head: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: spacing.sm },
  actions: { flexDirection: "row", gap: spacing.sm },
})
