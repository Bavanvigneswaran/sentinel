import { useEffect, useState } from "react"
import { Link } from "react-router"

import { AppLayout } from "@/components/AppLayout"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"
import { apiFetch } from "@/lib/api"
import {
  DEVICE_LIST_WITH_REMOVED,
  deviceLabel,
  indexDevices,
} from "@/lib/deviceNames"
import type { Device } from "@/types/api"
import type { Incident, IncidentStatus } from "@/types/incidents"

const POLL_INTERVAL_MS = 10_000

const FILTERS: { value: IncidentStatus | "all"; label: string }[] = [
  { value: "open", label: "Open" },
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
 * Fleet-wide incidents workspace: every device-level grouping of correlated
 * alerts, newest first, with whatever summary the insights worker (or a
 * manual regenerate on the detail page) has produced so far. The full
 * correlated-event timeline lives on IncidentDetailPage.
 */
export function IncidentsPage() {
  const [filter, setFilter] = useState<IncidentStatus | "all">("open")
  const [incidents, setIncidents] = useState<Incident[] | null>(null)
  const [devicesById, setDevicesById] = useState<Record<string, Device>>({})
  const [error, setError] = useState<string | null>(null)

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
      apiFetch<Incident[]>(`/incidents?status=${filter}`)
        .then((data) => {
          if (!cancelled) {
            setIncidents(data)
            setError(null)
          }
        })
        .catch(() => {
          if (!cancelled) setError("Could not load incidents.")
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
    <AppLayout active="incidents">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Incidents</h1>
        <p className="text-sm text-muted-foreground">
          Correlated alerts grouped by device, with a generated summary and root-cause
          analysis for each.
        </p>
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

      {incidents !== null && incidents.length === 0 && !error && (
        <Card>
          <CardHeader>
            <CardTitle>Nothing here</CardTitle>
            <CardDescription>
              {filter === "open"
                ? "No incidents are currently open."
                : "No incidents match this filter."}
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      {incidents !== null && incidents.length > 0 && (
        <div className="flex flex-col gap-3">
          {incidents.map((incident) => (
            <Link key={incident.id} to={`/incidents/${incident.id}`}>
              <Card className="transition-colors hover:bg-muted/50">
                <CardContent className="flex flex-col gap-2 pt-6">
                  <div className="flex items-center justify-between gap-4">
                    <span className="font-medium">
                      {deviceLabel(devicesById, incident.device_id)}
                    </span>
                    <span className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
                      <span
                        className={cn(
                          "size-2 rounded-full",
                          incident.status === "open" ? "bg-destructive" : "bg-emerald-500",
                        )}
                        aria-hidden
                      />
                      {incident.status === "open" ? "Open" : "Resolved"}
                    </span>
                  </div>
                  <p className="text-sm text-muted-foreground">
                    {incident.summary_text ?? "Insight not generated yet."}
                  </p>
                  <span className="text-xs text-muted-foreground">
                    opened {formatRelativeTime(incident.opened_at)}
                    {incident.closed_at && ` · closed ${formatRelativeTime(incident.closed_at)}`}
                  </span>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </AppLayout>
  )
}
