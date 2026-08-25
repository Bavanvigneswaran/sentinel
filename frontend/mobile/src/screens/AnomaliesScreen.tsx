/**
 * Anomalies — the `rule_type: "anomaly"` slice of the same event stream
 * AlertsScreen triages. Ported from web/src/pages/AnomaliesPage.tsx.
 *
 * Deliberately without the web page's evidence chart: that chart's job is to
 * show the metric trace against the baseline-at-fire and the current baseline,
 * which needs the width to be readable at all. On a phone the numbers say the
 * same thing in less space — how far out the reading was, and what normal
 * looked like right before it fired.
 */

import { useEffect, useState } from "react"
import { StyleSheet, Text, View } from "react-native"

import { AlertStatusBadge, SeverityBadge } from "@/components/Badges"
import { EmptyState, Screen } from "@/components/Screen"
import { Card, ErrorNote, Segmented } from "@/components/ui"
import { usePolledResource } from "@/hooks/usePolledResource"
import { apiFetch } from "@/lib/api"
import {
  DEVICE_LIST_WITH_REMOVED,
  deviceLabel,
  indexDevices,
} from "@/lib/deviceNames"
import { withDeviceScope } from "@/lib/deviceScope"
import { formatRelative } from "@/lib/timeRanges"
import { colors, spacing, text } from "@/theme"
import type { RootStackScreenProps } from "@/navigation/types"
import type { AlertEvent, EventStatus } from "@/types/alerts"
import type { Device } from "@/types/api"

const POLL_INTERVAL_MS = 10_000

type Filter = EventStatus | "all"

const FILTERS: { value: Filter; label: string }[] = [
  { value: "firing", label: "Firing" },
  { value: "resolved", label: "Resolved" },
  { value: "all", label: "All" },
]

function round(value: number | null | undefined, digits = 1): string {
  return value == null ? "—" : value.toFixed(digits)
}

export function AnomaliesScreen({ route }: RootStackScreenProps<"Anomalies">) {
  const deviceId = route.params?.deviceId
  const deviceName = route.params?.deviceName
  const [filter, setFilter] = useState<Filter>("firing")
  const [devices, setDevices] = useState<Record<string, Device>>({})

  const { data, error, refreshing, refresh } = usePolledResource<AlertEvent[]>(
    withDeviceScope(`/alerts/events?status=${filter}&rule_type=anomaly`, deviceId),
    POLL_INTERVAL_MS,
    "Could not load anomalies.",
  )

  useEffect(() => {
    // Removed devices included: this list is history, and a firing on a
    // machine the user has since removed still has to be able to name it.
    // See lib/deviceNames.ts.
    apiFetch<Device[]>(DEVICE_LIST_WITH_REMOVED)
      .then((list) => setDevices(indexDevices(list)))
      .catch(() => {
        // Non-fatal: the list still reads with a raw device id.
      })
  }, [])

  const events = data ?? []

  return (
    <Screen title="Anomalies" refreshing={refreshing} onRefresh={refresh}>
      {deviceName && (
        <Text style={text.tiny}>Showing {deviceName} only. Open this from More for every device.</Text>
      )}
      <Text style={text.small}>
        Readings that departed from what this machine's own baseline says is normal — not a
        threshold anybody set.
      </Text>

      <Segmented options={FILTERS} value={filter} onChange={setFilter} />

      {error && <ErrorNote message={error} />}

      {data && events.length === 0 && (
        <EmptyState
          title="No anomalies"
          body={
            filter === "firing"
              ? "Nothing is currently outside its baseline."
              : "No anomaly has been recorded in this range."
          }
        />
      )}

      {events.map((event) => (
        <Card key={event.id}>
          <View style={styles.header}>
            <Text style={text.heading}>{event.rule_name}</Text>
            <View style={styles.badges}>
              {/* Null for a threshold event; an anomaly always has one, but
                  the type allows null so the guard is real rather than a cast. */}
              {event.severity && <SeverityBadge severity={event.severity} />}
              <AlertStatusBadge status={event.status} />
            </View>
          </View>

          <Text style={text.small}>
            {deviceLabel(devices, event.device_id)} · {event.metric}
          </Text>

          {/* The event's own snapshot, not the live baseline: this is what
              normal looked like immediately before it fired, which is the
              question anyone reading an anomaly is actually asking. */}
          <View style={styles.evidence}>
            <Text style={styles.evidenceItem}>
              observed <Text style={styles.value}>{round(event.value_at_fire)}</Text>
            </Text>
            <Text style={styles.evidenceItem}>
              normal <Text style={styles.value}>{round(event.baseline_mean)}</Text>
            </Text>
            <Text style={styles.evidenceItem}>
              z <Text style={styles.value}>{round(event.z_score, 2)}</Text>
            </Text>
          </View>

          <Text style={text.tiny}>
            fired {formatRelative(event.fired_at)}
            {event.resolved_at ? ` · resolved ${formatRelative(event.resolved_at)}` : ""}
          </Text>
        </Card>
      ))}
    </Screen>
  )
}

const styles = StyleSheet.create({
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    gap: spacing.sm,
  },
  badges: { flexDirection: "row", gap: spacing.xs, alignItems: "center" },
  evidence: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: spacing.md,
    marginTop: spacing.xs,
  },
  evidenceItem: { ...text.small, color: colors.muted },
  value: { color: colors.foreground, fontVariant: ["tabular-nums"] },
})
