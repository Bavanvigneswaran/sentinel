import { useEffect, useState } from "react"
import { Link } from "react-router"

import { AlertStatusBadge } from "@/components/AlertStatusBadge"
import { AppLayout } from "@/components/AppLayout"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { apiFetch } from "@/lib/api"
import { formatDaysUntil } from "@/lib/formatters"
import type { AlertEvent, EventStatus } from "@/types/alerts"
import type { Device } from "@/types/api"
import type { ExhaustionForecast } from "@/types/forecast"

const POLL_INTERVAL_MS = 10_000

const METRIC_LABEL: Record<ExhaustionForecast["metric"], string> = {
  mem_percent: "Memory",
  disk_percent: "Disk",
}

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
 * Fleet-wide forecasting view: every device's memory/disk time-to-capacity
 * projection, soonest first, plus the rule_type="forecast" slice of
 * /alerts/events — same filtered-view-over-one-pipeline structure as
 * AnomaliesPage.tsx. The per-device 24h forecast chart itself lives on
 * DeviceHistoryPage, overlaid on the real telemetry it continues.
 */
export function ForecastsPage() {
  const [exhaustion, setExhaustion] = useState<ExhaustionForecast[] | null>(null)
  const [devicesById, setDevicesById] = useState<Record<string, Device>>({})
  const [exhaustionError, setExhaustionError] = useState<string | null>(null)

  const [filter, setFilter] = useState<EventStatus | "all">("firing")
  const [events, setEvents] = useState<AlertEvent[] | null>(null)
  const [eventsError, setEventsError] = useState<string | null>(null)

  useEffect(() => {
    apiFetch<Device[]>("/devices")
      .then((devices) => setDevicesById(Object.fromEntries(devices.map((d) => [d.id, d]))))
      .catch(() => {
        // Non-fatal: both lists still work with a raw device_id shown.
      })
  }, [])

  useEffect(() => {
    apiFetch<ExhaustionForecast[]>("/forecasts/exhaustion")
      .then((data) => setExhaustion(sortBySoonest(data)))
      .catch(() => setExhaustionError("Could not load capacity projections."))
  }, [])

  useEffect(() => {
    let cancelled = false
    const load = () => {
      apiFetch<AlertEvent[]>(`/alerts/events?status=${filter}`)
        .then((data) => {
          if (!cancelled) {
            setEvents(data.filter((e) => e.rule_type === "forecast"))
            setEventsError(null)
          }
        })
        .catch(() => {
          if (!cancelled) setEventsError("Could not load predicted-breach alerts.")
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
    <AppLayout active="forecasts">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Forecasts</h1>
        <p className="text-sm text-muted-foreground">
          24h-ahead projections from each device's own recent history, and disk/memory time to
          capacity.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Time to capacity</CardTitle>
          <CardDescription>
            Soonest first. A device needs enough real history for a trustworthy projection —
            newly enrolled devices simply don't appear yet.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {exhaustionError && <p className="text-sm text-destructive">{exhaustionError}</p>}
          {exhaustion !== null && exhaustion.length === 0 && !exhaustionError && (
            <p className="text-sm text-muted-foreground">
              No devices have enough history for a projection yet.
            </p>
          )}
          {exhaustion?.map((e) => (
            <div
              key={`${e.device_id}:${e.metric}`}
              className="flex items-center justify-between gap-4 text-sm"
            >
              <Link
                to={`/devices/${e.device_id}/history`}
                className="hover:text-foreground hover:underline"
              >
                {devicesById[e.device_id]?.name ?? e.device_id}
              </Link>
              <span className="text-muted-foreground">
                {METRIC_LABEL[e.metric]}
                {e.entity ? ` (${e.entity})` : ""} · {e.current_value.toFixed(0)}%
              </span>
              <span className={e.projected_at ? "font-medium" : "text-muted-foreground"}>
                {e.projected_at ? `full ${formatDaysUntil(e.projected_at)}` : "steady"}
              </span>
            </div>
          ))}
        </CardContent>
      </Card>

      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium text-muted-foreground">Predicted-breach alerts</h2>
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
      </div>

      {eventsError && (
        <Card>
          <CardContent className="pt-6 text-sm text-destructive">{eventsError}</CardContent>
        </Card>
      )}

      {events !== null && events.length === 0 && !eventsError && (
        <Card>
          <CardHeader>
            <CardTitle>Nothing here</CardTitle>
            <CardDescription>
              {filter === "firing"
                ? "No predicted breaches are currently firing."
                : "No predicted-breach alerts match this filter."}
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      {events !== null && events.length > 0 && (
        <div className="flex flex-col gap-3">
          {events.map((event) => (
            <Card key={event.id}>
              <CardContent className="flex items-center justify-between gap-4 pt-6">
                <div className="flex flex-col gap-1">
                  <span className="font-medium">{event.rule_name}</span>
                  <span className="text-sm text-muted-foreground">
                    {devicesById[event.device_id]?.name ?? event.device_id} · {event.metric}{" "}
                    predicted {event.comparison} {event.threshold}
                    {event.predicted_breach_at &&
                      ` (${formatDaysUntil(event.predicted_breach_at)})`}
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
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </AppLayout>
  )
}

function sortBySoonest(estimates: ExhaustionForecast[]): ExhaustionForecast[] {
  return [...estimates].sort((a, b) => {
    if (a.projected_at === null && b.projected_at === null) return 0
    if (a.projected_at === null) return 1
    if (b.projected_at === null) return -1
    return new Date(a.projected_at).getTime() - new Date(b.projected_at).getTime()
  })
}
