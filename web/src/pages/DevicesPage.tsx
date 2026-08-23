import { useCallback, useEffect, useState } from "react"
import { Link } from "react-router"

import { AppLayout } from "@/components/AppLayout"
import { DeviceStatusBadge } from "@/components/DeviceStatusBadge"
import { NewDevicePanel } from "@/components/NewDevicePanel"
import { Button } from "@/components/ui/button"
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
  const [adding, setAdding] = useState(false)
  /** Which row is mid-confirmation. There is no dialog primitive in this
   * codebase, and a two-step inline confirm needs no new dependency while
   * still making a destructive click deliberate. */
  const [confirming, setConfirming] = useState<string | null>(null)
  const [removing, setRemoving] = useState<string | null>(null)

  const load = useCallback(() => {
    return apiFetch<Device[]>("/devices")
      .then((data) => {
        setDevices(data)
        setError(null)
      })
      .catch(() => setError("Could not load your devices."))
  }, [])

  useEffect(() => {
    let cancelled = false

    const poll = () => {
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

    poll()
    const interval = setInterval(poll, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [])

  const remove = async (id: string) => {
    setRemoving(id)
    setError(null)
    try {
      await apiFetch(`/devices/${id}`, { method: "DELETE" })
      setConfirming(null)
      await load()
    } catch {
      setError("Could not remove that device.")
    } finally {
      setRemoving(null)
    }
  }

  return (
    <AppLayout active="devices">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Devices</h1>
          <p className="text-sm text-muted-foreground">
            Every machine you've enrolled an agent on, with its live connection status.
          </p>
        </div>
        {!adding && (
          <Button size="sm" onClick={() => setAdding(true)}>
            New device
          </Button>
        )}
      </div>

      {adding && <NewDevicePanel onClose={() => setAdding(false)} />}

      {error && (
        <Card>
          <CardContent className="pt-6 text-sm text-destructive">{error}</CardContent>
        </Card>
      )}

      {devices !== null && devices.length === 0 && !adding && (
        <Card>
          <CardHeader>
            <CardTitle>No devices yet</CardTitle>
            <CardDescription>
              Press <strong className="font-medium text-foreground">New device</strong> to mint an
              enrollment code, then redeem it from the agent on the machine you want to monitor.
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
                  <div className="flex items-center gap-3 text-xs">
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
                    <button
                      type="button"
                      onClick={() => setConfirming(device.id)}
                      className="text-muted-foreground hover:text-destructive"
                    >
                      Remove
                    </button>
                  </div>
                </div>
              </CardContent>

              {confirming === device.id && (
                <CardContent className="flex flex-wrap items-center justify-end gap-3 border-t border-border pt-4">
                  <span className="text-xs text-muted-foreground">
                    Remove <strong className="font-medium text-foreground">{device.name}</strong>?
                    Its agent token is revoked immediately, so the agent stops reporting. History
                    already collected is kept.
                  </span>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="destructive"
                      disabled={removing === device.id}
                      onClick={() => remove(device.id)}
                    >
                      {removing === device.id ? "Removing…" : "Remove device"}
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => setConfirming(null)}>
                      Cancel
                    </Button>
                  </div>
                </CardContent>
              )}
            </Card>
          ))}
        </div>
      )}
    </AppLayout>
  )
}
