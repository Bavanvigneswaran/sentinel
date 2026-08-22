import { useEffect, useState } from "react"
import { Link } from "react-router"

import { AlertStatusBadge } from "@/components/AlertStatusBadge"
import { AppLayout } from "@/components/AppLayout"
import { SilenceForm } from "@/components/SilenceForm"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { apiFetch } from "@/lib/api"
import { formatDaysUntil } from "@/lib/formatters"
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

export function AlertsPage() {
  const [filter, setFilter] = useState<EventStatus | "all">("firing")
  const [events, setEvents] = useState<AlertEvent[] | null>(null)
  const [devicesById, setDevicesById] = useState<Record<string, Device>>({})
  const [error, setError] = useState<string | null>(null)
  const [silencing, setSilencing] = useState<string | null>(null)

  useEffect(() => {
    apiFetch<Device[]>("/devices")
      .then((devices) => setDevicesById(Object.fromEntries(devices.map((d) => [d.id, d]))))
      .catch(() => {
        // Non-fatal: the triage list still works with a raw device_id shown.
      })
  }, [])

  useEffect(() => {
    let cancelled = false

    const load = () => {
      apiFetch<AlertEvent[]>(`/alerts/events?status=${filter}`)
        .then((data) => {
          if (!cancelled) {
            setEvents(data)
            setError(null)
          }
        })
        .catch(() => {
          if (!cancelled) setError("Could not load alerts.")
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
    <AppLayout active="alerts">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Alerts</h1>
          <p className="text-sm text-muted-foreground">
            Firing and recently resolved alerts across your fleet.
          </p>
        </div>
        <div className="flex gap-2">
          <Button asChild variant="outline" size="sm">
            <Link to="/anomalies">Anomalies</Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link to="/alerts/rules">Manage rules</Link>
          </Button>
        </div>
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
                ? "No alerts are currently firing."
                : "No alerts match this filter."}
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
                      {devicesById[event.device_id]?.name ?? event.device_id} · {event.metric}{" "}
                      {event.rule_type === "anomaly" ? (
                        <>anomaly ({event.severity})</>
                      ) : (
                        <>
                          {event.rule_type === "forecast" ? "predicted " : ""}
                          {event.comparison} {event.threshold}
                        </>
                      )}
                    </span>
                  </div>
                  <div className="flex flex-col items-end gap-1">
                    <AlertStatusBadge status={event.status} />
                    <span className="text-xs text-muted-foreground">
                      {event.status === "firing"
                        ? `fired ${formatRelativeTime(event.fired_at)}`
                        : `resolved ${formatRelativeTime(event.resolved_at ?? event.fired_at)}`}
                    </span>
                  </div>
                </div>

                <div className="flex items-center justify-between gap-4 text-sm text-muted-foreground">
                  <span>
                    {event.rule_type === "forecast" && event.predicted_value !== null ? (
                      <>
                        predicted to reach {event.predicted_value.toFixed(1)}
                        {event.predicted_breach_at &&
                          ` (${formatDaysUntil(event.predicted_breach_at)})`}
                        {" · live: "}
                        {event.value_at_fire}
                      </>
                    ) : (
                      <>value at fire: {event.value_at_fire}</>
                    )}
                    {event.status === "firing" && event.last_value !== null && (
                      <> · latest: {event.last_value}</>
                    )}
                    {event.notified_at === null && event.status === "firing" && (
                      <span className="ml-2 text-xs italic">silenced</span>
                    )}
                  </span>
                  {event.status === "firing" && silencing !== event.id && (
                    <Button variant="ghost" size="sm" onClick={() => setSilencing(event.id)}>
                      Silence
                    </Button>
                  )}
                </div>

                {silencing === event.id && (
                  <SilenceForm
                    deviceId={event.device_id}
                    ruleId={event.rule_id}
                    onSaved={() => setSilencing(null)}
                    onCancel={() => setSilencing(null)}
                  />
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </AppLayout>
  )
}
