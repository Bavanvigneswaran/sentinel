import { useEffect, useState } from "react"

import { AppLayout } from "@/components/AppLayout"
import { DeviceSummaryCard } from "@/components/DeviceSummaryCard"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { apiFetch } from "@/lib/api"
import { formatRelative } from "@/lib/timeRanges"
import { cn } from "@/lib/utils"
import type { FleetOverview, FleetTotals } from "@/types/fleet"

/**
 * The command centre: every device's health, headline numbers and last hour, in
 * one request.
 *
 * A poll rather than a socket. The live pipeline exists to push an agent to 1s
 * sampling while somebody watches one machine; doing that for every device
 * because a dashboard is open would multiply every agent's push rate by the
 * number of open tabs. Thirty seconds is the right cadence for a page whose
 * job is "is anything wrong", and the per-device live view is one click away.
 */
const POLL_INTERVAL_MS = 30_000

/** Matches the server's default; the sparklines cover this window. */
const WINDOW_SECONDS = 3600

export function DashboardPage() {
  const [overview, setOverview] = useState<FleetOverview | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    const load = () => {
      apiFetch<FleetOverview>(`/fleet/overview?window_seconds=${WINDOW_SECONDS}`)
        .then((data) => {
          if (cancelled) return
          setOverview(data)
          setError(null)
        })
        .catch(() => {
          // Keep the last good payload on screen. A transient failure should
          // not blank a dashboard someone is watching; the banner says so.
          if (!cancelled) setError("Could not refresh the fleet overview.")
        })
    }

    load()
    const interval = setInterval(load, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [])

  return (
    <AppLayout active="dashboard">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Fleet</h1>
          <p className="text-sm text-muted-foreground">
            Health of every machine you've enrolled, from its own real telemetry.
          </p>
        </div>
        {overview && (
          <span className="text-xs text-muted-foreground">
            updated {formatRelative(overview.generated_at)}
          </span>
        )}
      </div>

      {error && (
        <Card>
          <CardContent className="pt-6 text-sm text-destructive">
            {error} Showing the last successful reading.
          </CardContent>
        </Card>
      )}

      {overview && <Totals totals={overview.totals} />}

      {overview !== null && overview.devices.length === 0 && (
        <Card>
          <CardHeader>
            <CardTitle>No devices yet</CardTitle>
            <CardDescription>
              Generate an enrollment code, then run{" "}
              <code className="font-mono text-xs">make agent-enroll code=…</code> followed by{" "}
              <code className="font-mono text-xs">make agent</code> on the machine you want to
              watch.
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      {overview === null && !error && (
        <p className="text-sm text-muted-foreground">Loading…</p>
      )}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {overview?.devices.map((summary) => (
          <DeviceSummaryCard key={summary.device.id} summary={summary} />
        ))}
      </div>
    </AppLayout>
  )
}

function Totals({ totals }: { totals: FleetTotals }) {
  return (
    <Card>
      <CardContent className="grid grid-cols-2 gap-4 pt-6 sm:grid-cols-4 lg:grid-cols-7">
        <Tile label="Devices" value={totals.devices} />
        <Tile label="Online" value={totals.online} tone="text-emerald-500" />
        <Tile label="Offline" value={totals.offline} />
        <Tile label="Awaiting agent" value={totals.pending} tone="text-amber-500" />
        <Tile label="Healthy" value={totals.healthy} tone="text-emerald-500" />
        <Tile label="Degraded" value={totals.degraded} tone="text-amber-500" />
        <Tile label="Critical" value={totals.critical} tone="text-red-500" />
      </CardContent>
    </Card>
  )
}

function Tile({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className="flex flex-col">
      {/* Zero is a real count here, not a missing metric, so it renders as 0 —
          MetricValue's "unavailable" rule is about measurements, not tallies. */}
      <span className={cn("text-2xl font-semibold tabular-nums", value > 0 && tone)}>{value}</span>
      <span className="text-xs text-muted-foreground">{label}</span>
    </div>
  )
}
