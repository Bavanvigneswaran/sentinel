import { useEffect, useState } from "react"
import { Link, useParams } from "react-router"

import { AlertStatusBadge } from "@/components/AlertStatusBadge"
import { AppLayout } from "@/components/AppLayout"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { describeEventCondition } from "@/lib/alertCopy"
import { apiFetch, ApiError } from "@/lib/api"
import { DEVICE_LIST_WITH_REMOVED, deviceLabel } from "@/lib/deviceNames"
import { cn } from "@/lib/utils"
import type { Device } from "@/types/api"
import type { IncidentDetail } from "@/types/incidents"

function formatRelativeTime(iso: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return "just now"
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86_400)}d ago`
}

/**
 * One incident's correlated-alert timeline plus its generated summary
 * (Haiku) and root-cause analysis (Sonnet). Both are stored as plain text —
 * rendered as such below, never interpreted as markup — see
 * app/ai/client.py's reasoning for why the model's output is inert display
 * text and nothing more.
 */
export function IncidentDetailPage() {
  const { incidentId } = useParams<{ incidentId: string }>()
  const [incident, setIncident] = useState<IncidentDetail | null>(null)
  const [device, setDevice] = useState<Device | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [regenerating, setRegenerating] = useState(false)
  const [regenerateError, setRegenerateError] = useState<string | null>(null)

  const load = () => {
    if (!incidentId) return
    apiFetch<IncidentDetail>(`/incidents/${incidentId}`)
      .then((data) => {
        setIncident(data)
        setError(null)
      })
      .catch(() => setError("Could not load this incident."))
  }

  useEffect(load, [incidentId])

  useEffect(() => {
    // Removed devices included: an incident is history and outlives the
    // machine it happened on. See lib/deviceNames.ts.
    apiFetch<Device[]>(DEVICE_LIST_WITH_REMOVED)
      .then((devices) => {
        const match = devices.find((d) => d.id === incident?.device_id)
        if (match) setDevice(match)
      })
      .catch(() => {
        // Non-fatal: the header falls back to a short device id.
      })
  }, [incident?.device_id])

  const handleRegenerate = async () => {
    if (!incidentId) return
    setRegenerating(true)
    setRegenerateError(null)
    try {
      const updated = await apiFetch<IncidentDetail>(`/incidents/${incidentId}/regenerate`, {
        method: "POST",
      })
      setIncident(updated)
    } catch (err) {
      setRegenerateError(
        err instanceof ApiError ? err.message : "Could not regenerate insights.",
      )
    } finally {
      setRegenerating(false)
    }
  }

  if (error) {
    return (
      <AppLayout active="incidents">
        <Card>
          <CardContent className="pt-6 text-sm text-destructive">{error}</CardContent>
        </Card>
      </AppLayout>
    )
  }

  if (!incident) {
    return (
      <AppLayout active="incidents">
        <p className="text-sm text-muted-foreground">Loading…</p>
      </AppLayout>
    )
  }

  return (
    <AppLayout active="incidents">
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Link
              to={`/devices/${incident.device_id}/history`}
              className="text-xl font-semibold tracking-tight hover:underline"
              data-testid="page-title"
            >
              {deviceLabel(device ? { [device.id]: device } : {}, incident.device_id)}
            </Link>
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
            opened {formatRelativeTime(incident.opened_at)}
            {incident.closed_at && ` · closed ${formatRelativeTime(incident.closed_at)}`}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={handleRegenerate} disabled={regenerating}>
          {regenerating ? "Regenerating…" : "Regenerate insights"}
        </Button>
      </div>

      {regenerateError && <p className="text-sm text-destructive">{regenerateError}</p>}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Summary</CardTitle>
          <CardDescription>
            {incident.summary_model
              ? `Generated from this incident's evidence by ${incident.summary_model}.`
              : "Generated from this incident's evidence."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <p className="whitespace-pre-wrap text-sm">
            {incident.summary_text ?? "Not generated yet."}
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Root cause analysis</CardTitle>
          <CardDescription>
            {incident.root_cause_model
              ? `Generated from this incident's evidence by ${incident.root_cause_model}.`
              : "Generated from this incident's evidence."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <p className="whitespace-pre-wrap text-sm">
            {incident.root_cause_text ?? "Not generated yet."}
          </p>
        </CardContent>
      </Card>

      <div>
        <h2 className="mb-2 text-sm font-medium text-muted-foreground">Timeline</h2>
        <div className="flex flex-col gap-3">
          {incident.events.map((event) => (
            <Card key={event.id}>
              <CardContent className="flex items-center justify-between gap-4 pt-6">
                <div className="flex flex-col gap-1">
                  <span className="font-medium">{event.rule_name}</span>
                  <span className="text-sm text-muted-foreground">
                    {describeEventCondition(event)}
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
      </div>
    </AppLayout>
  )
}
