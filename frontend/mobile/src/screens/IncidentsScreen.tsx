/**
 * Incidents — correlated alerts on one device, with the generated summary when
 * there is one. Ported from web/src/pages/IncidentsPage.tsx.
 *
 * The summary is rendered as inert display text and nothing here parses it
 * back into structure, which is CLAUDE.md's "the LLM explains; it does not
 * detect" applied at the render layer: a phone shows the sentence, the state
 * machine that opened the incident is unaffected by it.
 *
 * Tapping a card opens IncidentDetailScreen, which is where the correlated
 * timeline and the root-cause text live — the list endpoint returns
 * `Incident`, not `IncidentDetail`, and fetching the timeline per row would
 * be one extra request for every card on screen.
 */

import { useEffect, useState } from "react"
import { StyleSheet, Text, View } from "react-native"

import { AlertStatusBadge } from "@/components/Badges"
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
import { spacing, text } from "@/theme"
import type { Device } from "@/types/api"
import type { RootStackScreenProps } from "@/navigation/types"
import type { Incident, IncidentStatus } from "@/types/incidents"

const POLL_INTERVAL_MS = 15_000

type Filter = IncidentStatus | "all"

const FILTERS: { value: Filter; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "resolved", label: "Resolved" },
  { value: "all", label: "All" },
]

export function IncidentsScreen({ route, navigation }: RootStackScreenProps<"Incidents">) {
  const deviceId = route.params?.deviceId
  const deviceName = route.params?.deviceName
  const [filter, setFilter] = useState<Filter>("open")
  const [devices, setDevices] = useState<Record<string, Device>>({})

  const { data, error, refreshing, refresh } = usePolledResource<Incident[]>(
    withDeviceScope(`/incidents?status=${filter}`, deviceId),
    POLL_INTERVAL_MS,
    "Could not load incidents.",
  )

  useEffect(() => {
    // Removed devices included: this list is history, and a firing on a
    // machine the user has since removed still has to be able to name it.
    // See lib/deviceNames.ts.
    apiFetch<Device[]>(DEVICE_LIST_WITH_REMOVED)
      .then((list) => setDevices(indexDevices(list)))
      .catch(() => {
        // Non-fatal.
      })
  }, [])

  const incidents = data ?? []

  return (
    <Screen testID="screen-incidents" title="Incidents" refreshing={refreshing} onRefresh={refresh}>
      {deviceName && (
        <Text style={text.tiny}>Showing {deviceName} only. Open this from More for every device.</Text>
      )}
      <Text style={text.small}>
        Alerts that fired together on one machine, grouped into the thing that was actually
        happening.
      </Text>

      <Segmented options={FILTERS} value={filter} onChange={setFilter} />

      {error && <ErrorNote message={error} />}

      {data && incidents.length === 0 && (
        <EmptyState
          title="No incidents"
          body={
            filter === "open"
              ? "Nothing is going wrong right now."
              : "No incident has been recorded in this range."
          }
        />
      )}

      {incidents.map((incident) => (
        <Card
          key={incident.id}
          testID={`incident-${incident.id}`}
          onPress={() => navigation.navigate("IncidentDetail", { incidentId: incident.id })}
        >
          <View style={styles.header}>
            <Text style={text.heading}>
              {deviceLabel(devices, incident.device_id)}
            </Text>
            <AlertStatusBadge status={incident.status === "open" ? "firing" : "resolved"} />
          </View>

          <Text style={text.tiny}>
            opened {formatRelative(incident.opened_at)}
            {incident.closed_at ? ` · closed ${formatRelative(incident.closed_at)}` : ""}

          </Text>

          {/* No per-event breakdown here: the list endpoint returns
              Incident, not IncidentDetail, and fetching detail per row to show
              a count would be a request per incident for one number. Tapping
              the card is what makes that second request, once. */}
          {incident.summary_text ? (
            <Text style={styles.summary}>{incident.summary_text}</Text>
          ) : (
            <Text style={text.tiny}>
              No AI summary yet — either the insights worker has not run for this incident, or
              no API key is configured on the server.
            </Text>
          )}
        </Card>
      ))}
    </Screen>
  )
}

const styles = StyleSheet.create({
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: spacing.sm,
  },
  summary: { ...text.body, marginTop: spacing.xs },
})
