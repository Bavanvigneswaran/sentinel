/**
 * Incidents — correlated alerts on one device, with the AI summary when there
 * is one. Ported from web/src/pages/IncidentsPage.tsx.
 *
 * The summary is rendered as inert display text and nothing here parses it
 * back into structure, which is CLAUDE.md's "the LLM explains; it does not
 * detect" applied at the render layer: a phone shows the sentence, the state
 * machine that opened the incident is unaffected by it.
 *
 * Root-cause regeneration stays in the web console — it costs an API call, and
 * a button that spends money is not one to put under a thumb at 3am.
 */

import { useEffect, useState } from "react"
import { StyleSheet, Text, View } from "react-native"

import { AlertStatusBadge } from "@/components/Badges"
import { EmptyState, Screen } from "@/components/Screen"
import { Card, ErrorNote, Segmented } from "@/components/ui"
import { usePolledResource } from "@/hooks/usePolledResource"
import { apiFetch } from "@/lib/api"
import { formatRelative } from "@/lib/timeRanges"
import { spacing, text } from "@/theme"
import type { Device } from "@/types/api"
import type { Incident, IncidentStatus } from "@/types/incidents"

const POLL_INTERVAL_MS = 15_000

type Filter = IncidentStatus | "all"

const FILTERS: { value: Filter; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "resolved", label: "Resolved" },
  { value: "all", label: "All" },
]

export function IncidentsScreen() {
  const [filter, setFilter] = useState<Filter>("open")
  const [devices, setDevices] = useState<Record<string, Device>>({})

  const { data, error, refreshing, refresh } = usePolledResource<Incident[]>(
    `/incidents?status=${filter}`,
    POLL_INTERVAL_MS,
    "Could not load incidents.",
  )

  useEffect(() => {
    apiFetch<Device[]>("/devices")
      .then((list) => setDevices(Object.fromEntries(list.map((d) => [d.id, d]))))
      .catch(() => {
        // Non-fatal.
      })
  }, [])

  const incidents = data ?? []

  return (
    <Screen title="Incidents" refreshing={refreshing} onRefresh={refresh}>
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
        <Card key={incident.id}>
          <View style={styles.header}>
            <Text style={text.heading}>
              {devices[incident.device_id]?.name ?? incident.device_id}
            </Text>
            <AlertStatusBadge status={incident.status === "open" ? "firing" : "resolved"} />
          </View>

          <Text style={text.tiny}>
            opened {formatRelative(incident.opened_at)}
            {incident.closed_at ? ` · closed ${formatRelative(incident.closed_at)}` : ""}

          </Text>

          {/* No per-event breakdown here: the list endpoint returns
              Incident, not IncidentDetail, and fetching detail per row to show
              a count would be a request per incident for one number. The web
              console's detail page has the timeline. */}
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
