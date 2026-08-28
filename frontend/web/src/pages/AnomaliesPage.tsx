import { useEffect, useState } from "react"
import { Link } from "react-router"

import { AlertStatusBadge } from "@/components/AlertStatusBadge"
import { AnomalyEvidenceChart } from "@/components/AnomalyEvidenceChart"
import { AppLayout } from "@/components/AppLayout"
import { SeverityBadge } from "@/components/SeverityBadge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { apiFetch } from "@/lib/api"
import {
  DEVICE_LIST_WITH_REMOVED,
  deviceLabel,
  indexDevices,
} from "@/lib/deviceNames"
import type { AlertEvent, EventStatus } from "@/types/alerts"
import type { Device } from "@/types/api"

const POLL_INTERVAL_MS = 10_000

const FILTERS: { value: EventStatus | "all"; label: string }[] = [
  { value: "firing", label: "Firing" },
  { value: "resolved", label: "Resolved" },
  { value: "all", label: "All" },
]

function formatRelativeTime(iso: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return "just now"
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86_400)}d ago`
}

/**
 * Anomaly-sourced alert events — a filtered view over the same /alerts/events
 * the threshold triage page (AlertsPage) reads, since all rule types feed one
 * alert pipeline. `rule_type` is the explicit "this was an anomaly firing"
 * signal on the event itself (rule_id can be null after the rule is deleted,
 * but the event's own snapshot always carries this) — used here instead of
 * the `comparison === null` heuristic this page used before rule_type
 * existed on AlertEvent. That heuristic still happens to be correct (a
 * forecast event also sets comparison/threshold, so anomaly stays the only
 * kind with both null), but there is no reason to rely on it once the
 * explicit field is available.
 */
export function AnomaliesPage() {
  const [filter, setFilter] = useState<EventStatus | "all">("firing")
  const [events, setEvents] = useState<AlertEvent[] | null>(null)
  const [devicesById, setDevicesById] = useState<Record<string, Device>>({})
  const [error, setError] = useState<string | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  useEffect(() => {
    // Removed devices included: this list is history, and a firing on a
    // machine the user has since removed still has to be able to name it.
    // See lib/deviceNames.ts.
    apiFetch<Device[]>(DEVICE_LIST_WITH_REMOVED)
      .then((devices) => setDevicesById(indexDevices(devices)))
      .catch(() => {
        // Non-fatal: the list still works with a short device id shown.
      })
  }, [])

  useEffect(() => {
    let cancelled = false

    const load = () => {
      apiFetch<AlertEvent[]>(`/alerts/events?status=${filter}`)
        .then((data) => {
          if (!cancelled) {
            setEvents(data.filter((e) => e.rule_type === "anomaly"))
            setError(null)
          }
        })
        .catch(() => {
          if (!cancelled) setError("Could not load anomalies.")
        })
    }

    load()
    const interval = setInterval(load, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [filter])

  return (
    <AppLayout active="anomalies">
      <div className="flex items-center justify-between">
        <div>
          <h1 data-testid="page-title" className="text-xl font-semibold tracking-tight">Anomalies</h1>
          <p className="text-sm text-muted-foreground">
            Statistically unusual readings caught by your adaptive-baseline rules.
          </p>
        </div>
        <Button asChild variant="outline" size="sm">
          <Link to="/alerts/rules">Manage rules</Link>
        </Button>
      </div>

      <div className="flex gap-2">
        {FILTERS.map((f) => (
          <Button
            key={f.value}
            size="sm"
            variant={filter === f.value ? "default" : "outline"}
            onClick={() => setFilter(f.value)}
          >
            {f.label}
          </Button>
        ))}
      </div>

      {error && (
        <Card>
          <CardContent className="pt-6 text-sm text-destructive">{error}</CardContent>
        </Card>
      )}

      {events !== null && events.length === 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Nothing here</CardTitle>
            <CardDescription>
              {filter === "firing"
                ? "No anomalies are currently firing."
                : "No anomalies match this filter."}
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      {events !== null && events.length > 0 && (
        <div className="flex flex-col gap-3">
          {events.map((event) => (
            <Card key={event.id}>
              <CardContent className="flex flex-col gap-3 pt-6">
                <div className="flex items-center justify-between gap-4">
                  <div className="flex flex-col gap-1">
                    <span className="font-medium">{event.rule_name}</span>
                    <span className="text-sm text-muted-foreground">
                      {deviceLabel(devicesById, event.device_id)} · {event.metric} ·
                      z = {event.z_score?.toFixed(1) ?? "—"}
                    </span>
                  </div>
                  <div className="flex flex-col items-end gap-1">
                    <div className="flex items-center gap-3">
                      {event.severity && <SeverityBadge severity={event.severity} />}
                      <AlertStatusBadge status={event.status} />
                    </div>
                    <span className="text-xs text-muted-foreground">
                      {event.status === "firing"
                        ? `fired ${formatRelativeTime(event.fired_at)}`
                        : `resolved ${formatRelativeTime(event.resolved_at ?? event.fired_at)}`}
                    </span>
                  </div>
                </div>

                <div className="flex items-center justify-between gap-4 text-sm text-muted-foreground">
                  <span>
                    expected ~{event.baseline_mean?.toFixed(1) ?? "—"}, saw{" "}
                    {event.observed_value?.toFixed(1) ?? event.value_at_fire}
                    {event.notified_at === null && event.status === "firing" && (
                      <span className="ml-2 text-xs italic">silenced</span>
                    )}
                  </span>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setExpandedId(expandedId === event.id ? null : event.id)}
                  >
                    {expandedId === event.id ? "Hide evidence" : "View evidence"}
                  </Button>
                </div>

                {expandedId === event.id && <AnomalyEvidenceChart event={event} />}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </AppLayout>
  )
}
