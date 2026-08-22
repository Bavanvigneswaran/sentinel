import { useEffect, useState } from "react"
import { Link } from "react-router"

import { AppLayout } from "@/components/AppLayout"
import { DeviceStatusBadge } from "@/components/DeviceStatusBadge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { apiFetch } from "@/lib/api"
import type { Device } from "@/types/api"

/** No live socket for the list itself in Phase 3 — a plain poll is enough
 * for "online/offline/last-seen" and keeps this page simple. */
const POLL_INTERVAL_MS = 10_000

function formatLastSeen(iso: string | null): string {
  if (!iso) return "never"
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return "just now"
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86_400)}d ago`
}

export function DevicesPage() {
  const [devices, setDevices] = useState<Device[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    const load = () => {
      apiFetch<Device[]>("/devices")
        .then((data) => {
          if (!cancelled) {
            setDevices(data)
            setError(null)
          }
        })
        .catch(() => {
          if (!cancelled) setError("Could not load your devices.")
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
    <AppLayout active="devices">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Devices</h1>
        <p className="text-sm text-muted-foreground">
          Every machine you've enrolled an agent on, with its live connection status.
        </p>
      </div>

      {error && (
        <Card>
          <CardContent className="pt-6 text-sm text-destructive">{error}</CardContent>
        </Card>
      )}

      {devices !== null && devices.length === 0 && (
        <Card>
          <CardHeader>
            <CardTitle>No devices yet</CardTitle>
            <CardDescription>
              Enrol a machine with{" "}
              <code className="font-mono text-xs">make agent-enroll code=X4T9-K2QM-7PDR</code>{" "}
              after generating a code, then <code className="font-mono text-xs">make agent</code>{" "}
              to start pushing real metrics.
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      {devices !== null && devices.length > 0 && (
        <div className="flex flex-col gap-3">
          {devices.map((device) => (
            <Card key={device.id} className="transition-colors hover:bg-accent/30">
              <CardContent className="flex items-center justify-between gap-4 pt-6">
                <div className="flex flex-col gap-1">
                  <Link
                    to={`/devices/${device.id}/history`}
                    className="font-medium hover:underline"
                  >
                    {device.name}
                  </Link>
                  <span className="text-sm text-muted-foreground">
                    {device.hostname ?? "—"}
                    {device.os ? ` · ${device.os}` : ""}
                    {device.os_version ? ` ${device.os_version}` : ""}
                  </span>
                </div>
                <div className="flex flex-col items-end gap-1">
                  <DeviceStatusBadge status={device.status} />
                  <span className="text-xs text-muted-foreground">
                    last seen {formatLastSeen(device.last_seen_at)}
                  </span>
                  <div className="flex gap-3 text-xs">
                    <Link
                      to={`/devices/${device.id}/history`}
                      className="text-muted-foreground hover:text-foreground"
                    >
                      History
                    </Link>
                    <Link
                      to={`/devices/${device.id}/live`}
                      className="text-muted-foreground hover:text-foreground"
                    >
                      Live
                    </Link>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </AppLayout>
  )
}
