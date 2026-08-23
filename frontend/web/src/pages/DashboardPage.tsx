import { useCallback, useEffect, useState } from "react"

import { AppLayout } from "@/components/AppLayout"
import { DeviceSummaryCard } from "@/components/DeviceSummaryCard"
import { NewDevicePanel } from "@/components/NewDevicePanel"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { apiFetch } from "@/lib/api"
import { formatRelative } from "@/lib/timeRanges"
import { cn } from "@/lib/utils"
import type { FleetOverview, FleetTotals } from "@/types/fleet"

/**
 * The Devices page: every machine's health, headline numbers and last hour,
 * plus adding and removing devices, in one place.
 *
 * This used to be two pages — a rich "Dashboard" at `/` and a plain
 * online/offline list at `/devices` — mirroring the redundancy the Android
 * app had between its Fleet and Devices tabs before they merged. The plain
 * list had nothing this view doesn't already show (name, hostname, OS,
 * status, last seen), so rather than keep both, `/devices` now redirects
 * here and this is the one page. See routes.tsx.
 *
 * A poll rather than a socket. The live pipeline exists to push an agent to 1s
 * sampling while somebody watches one machine; doing that for every device
 * because this page is open would multiply every agent's push rate by the
 * number of open tabs. Thirty seconds is the right cadence for a page whose
 * job is "is anything wrong, and is there anything to add or remove", and the
 * per-device live view is one click away.
 */
const POLL_INTERVAL_MS = 30_000

/** Matches the server's default; the sparklines cover this window. */
const WINDOW_SECONDS = 3600

export function DashboardPage() {
  const [overview, setOverview] = useState<FleetOverview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)

  const load = useCallback(() => {
    return apiFetch<FleetOverview>(`/fleet/overview?window_seconds=${WINDOW_SECONDS}`)
      .then((data) => {
        setOverview(data)
        setError(null)
      })
      .catch(() => {
        // Keep the last good payload on screen. A transient failure should
        // not blank a page someone is watching; the banner says so.
        setError("Could not refresh the fleet overview.")
      })
  }, [])

  useEffect(() => {
    let cancelled = false

    const poll = () => {
      apiFetch<FleetOverview>(`/fleet/overview?window_seconds=${WINDOW_SECONDS}`)
        .then((data) => {
          if (cancelled) return
          setOverview(data)
          setError(null)
        })
        .catch(() => {
          if (!cancelled) setError("Could not refresh the fleet overview.")
        })
    }

    poll()
    const interval = setInterval(poll, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [])

  return (
    <AppLayout active="devices">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Devices</h1>
          <p className="text-sm text-muted-foreground">
            Health of every machine you've enrolled, from its own real telemetry.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {overview && (
            <span className="text-xs text-muted-foreground">
              updated {formatRelative(overview.generated_at)}
            </span>
          )}
          {!adding && (
            <Button size="sm" onClick={() => setAdding(true)}>
              New device
            </Button>
          )}
        </div>
      </div>

      {adding && <NewDevicePanel onClose={() => setAdding(false)} />}

      {error && (
        <Card>
          <CardContent className="pt-6 text-sm text-destructive">
            {error} Showing the last successful reading.
          </CardContent>
        </Card>
      )}

      {overview && <Totals totals={overview.totals} />}

      {overview !== null && overview.devices.length === 0 && !adding && (
        <Card>
          <CardHeader>
            <CardTitle>No devices yet</CardTitle>
            <CardDescription>
              Press <strong className="font-medium text-foreground">New device</strong> to mint
              an enrollment code, then redeem it from the agent on the machine you want to
              monitor.
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      {overview === null && !error && (
        <p className="text-sm text-muted-foreground">Loading…</p>
      )}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {overview?.devices.map((summary) => (
          <DeviceSummaryCard key={summary.device.id} summary={summary} onRemoved={load} />
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
