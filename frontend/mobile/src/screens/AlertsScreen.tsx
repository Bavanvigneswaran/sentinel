/**
 * Alert triage. Ported from web/src/pages/AlertsPage.tsx.
 *
 * All three rule types land in the same list and are told apart by
 * `rule_type`, never by inspecting `comparison` — that heuristic stopped being
 * sufficient in Phase 7, which is exactly why the backend snapshots
 * `rule_type` onto the event (see CLAUDE.md's Phase 7 invariants).
 *
 * This is primarily a triage surface: what is firing, on which machine, since
 * when, and can I silence it. But triage and authoring are the same loop — the
 * useful answer to a 3am page is often "that threshold is wrong" — so the rule
 * that fired is now two taps away rather than back at a desk, and an event's
 * card opens the incident it was correlated into.
 */

import { useCallback, useEffect, useState } from "react"
import { StyleSheet, Text, View } from "react-native"

import { AlertStatusBadge, SeverityBadge } from "@/components/Badges"
import { EmptyState, Screen } from "@/components/Screen"
import { Button, Card, ErrorNote, Segmented } from "@/components/ui"
import { usePolledResource } from "@/hooks/usePolledResource"
import { describeEventCondition, describeEventEvidence } from "@/lib/alertCopy"
import { apiFetch } from "@/lib/api"
import {
  DEVICE_LIST_WITH_REMOVED,
  deviceLabel,
  indexDevices,
} from "@/lib/deviceNames"
import { formatDaysUntil } from "@/lib/formatters"
import { formatRelative } from "@/lib/timeRanges"
import { colors, spacing, text } from "@/theme"
import type { AlertEvent, EventStatus } from "@/types/alerts"
import type { Device } from "@/types/api"
import type { RootTabScreenProps } from "@/navigation/types"

const POLL_INTERVAL_MS = 10_000

/** How long "Silence" mutes a rule for. The web app offers a form with a
 * start and an end; a phone offers the one duration somebody reaching for
 * their phone at 3am actually wants, and the web app can still do the rest. */
const SILENCE_HOURS = 1

type Filter = EventStatus | "all"

const FILTERS: { value: Filter; label: string }[] = [
  { value: "firing", label: "Firing" },
  { value: "resolved", label: "Resolved" },
  { value: "all", label: "All" },
]

export function AlertsScreen({ navigation }: RootTabScreenProps<"Alerts">) {
  const [filter, setFilter] = useState<Filter>("firing")
  const [devices, setDevices] = useState<Record<string, Device>>({})
  const [silencing, setSilencing] = useState<string | null>(null)
  const [silenceError, setSilenceError] = useState<string | null>(null)

  const { data, error, loading, refreshing, refresh } = usePolledResource<AlertEvent[]>(
    `/alerts/events?status=${filter}`,
    POLL_INTERVAL_MS,
    "Could not load alerts.",
  )

  useEffect(() => {
    // Removed devices included: this list is history, and a firing on a
    // machine the user has since removed still has to be able to name it.
    // See lib/deviceNames.ts.
    apiFetch<Device[]>(DEVICE_LIST_WITH_REMOVED)
      .then((list) => setDevices(indexDevices(list)))
      .catch(() => {
        // Non-fatal: the triage list still works with a raw device_id shown.
      })
  }, [])

  const silence = useCallback(
    async (event: AlertEvent) => {
      setSilencing(event.id)
      setSilenceError(null)
      const now = new Date()
      try {
        await apiFetch("/alerts/silences", {
          method: "POST",
          body: {
            device_id: event.device_id,
            rule_id: event.rule_id,
            starts_at: now.toISOString(),
            ends_at: new Date(now.getTime() + SILENCE_HOURS * 3600_000).toISOString(),
            reason: "Silenced from the Android app",
          },
        })
        refresh()
      } catch (err) {
        setSilenceError(err instanceof Error ? err.message : "Could not create the silence.")
      } finally {
        setSilencing(null)
      }
    },
    [refresh],
  )

  return (
    <Screen
      title="Alerts"
      subtitle="Firing and recently resolved alerts across your fleet."
      refreshing={refreshing}
      onRefresh={refresh}
    >
      <Segmented options={FILTERS} value={filter} onChange={setFilter} />

      <Button
        size="sm"
        variant="outline"
        title="Alert rules"
        onPress={() => navigation.navigate("AlertRules")}
      />

      {error && <ErrorNote message={error} />}
      {silenceError && <ErrorNote message={silenceError} />}
      {loading && !data && <Text style={text.small}>Loading…</Text>}

      {data?.length === 0 && (
        <EmptyState
          title="Nothing here"
          body={
            filter === "firing"
              ? "No alerts are currently firing."
              : "No alerts match this filter."
          }
        />
      )}

      {data?.map((event) => (
        <AlertCard
          key={event.id}
          event={event}
          deviceName={deviceLabel(devices, event.device_id)}
          busy={silencing === event.id}
          onSilence={() => void silence(event)}
          onOpenIncident={
            event.incident_id
              ? () =>
                  navigation.navigate("IncidentDetail", { incidentId: event.incident_id as string })
              : undefined
          }
        />
      ))}
    </Screen>
  )
}

function AlertCard({
  event,
  deviceName,
  busy,
  onSilence,
  onOpenIncident,
}: {
  event: AlertEvent
  deviceName: string
  busy: boolean
  onSilence: () => void
  /** Absent for an event with no incident — which is only ever an event whose
   * correlation predates Phase 8, since every firing has opened or joined one
   * since. Rendering a dead button for those would be worse than omitting it. */
  onOpenIncident?: () => void
}) {
  return (
    <Card>
      <View style={styles.head}>
        <View style={styles.headText}>
          <Text style={text.heading} numberOfLines={2}>
            {event.rule_name}
          </Text>
          <Text style={text.small} numberOfLines={2}>
            {deviceName} · {describeEventCondition(event)}
          </Text>
        </View>
        <View style={styles.headRight}>
          <AlertStatusBadge status={event.status} />
          {event.severity && <SeverityBadge severity={event.severity} />}
        </View>
      </View>

      <Text style={text.small}>{describeEvidence(event)}</Text>

      <View style={styles.footer}>
        <Text style={text.tiny}>
          {event.status === "firing"
            ? `fired ${formatRelative(event.fired_at)}`
            : `resolved ${formatRelative(event.resolved_at ?? event.fired_at)}`}
          {event.status === "firing" && event.notified_at === null ? " · silenced" : ""}
        </Text>
        <View style={styles.footerActions}>
          {onOpenIncident && (
            <Button size="sm" variant="ghost" title="Incident" onPress={onOpenIncident} />
          )}
          {event.status === "firing" && (
            <Button
              size="sm"
              variant="outline"
              title={`Silence ${SILENCE_HOURS}h`}
              busy={busy}
              onPress={onSilence}
            />
          )}
        </View>
      </View>
    </Card>
  )
}

function describeEvidence(event: AlertEvent): string {
  const parts: string[] = []
  if (event.rule_type === "forecast" && event.predicted_value !== null) {
    parts.push(`predicted to reach ${event.predicted_value.toFixed(1)}`)
    if (event.predicted_breach_at) parts.push(formatDaysUntil(event.predicted_breach_at))
    parts.push(`live: ${event.value_at_fire}`)
  } else {
    parts.push(describeEventEvidence(event))
  }
  if (event.rule_type === "anomaly" && event.baseline_mean !== null) {
    parts.push(`baseline was ${event.baseline_mean.toFixed(1)}`)
    if (event.z_score !== null) parts.push(`z = ${event.z_score.toFixed(1)}`)
  }
  if (event.status === "firing" && event.last_value !== null) {
    parts.push(`latest: ${event.last_value}`)
  }
  return parts.join(" · ")
}

const styles = StyleSheet.create({
  head: { flexDirection: "row", justifyContent: "space-between", gap: spacing.md },
  headText: { flex: 1, gap: 2 },
  headRight: { alignItems: "flex-end", gap: spacing.xs },
  footer: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: spacing.sm,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.border,
    paddingTop: spacing.sm,
  },
  footerActions: { flexDirection: "row", alignItems: "center", gap: spacing.xs },
})
