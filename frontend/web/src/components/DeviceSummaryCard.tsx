import { useState } from "react"
import { Link } from "react-router"

import { DeviceStatusBadge } from "@/components/DeviceStatusBadge"
import { HealthScore } from "@/components/HealthScore"
import { MetricValue } from "@/components/MetricValue"
import { Sparkline } from "@/components/Sparkline"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { apiFetch } from "@/lib/api"
import { formatBytes, formatBytesPerSecond } from "@/lib/formatters"
import { formatDuration, formatRelative } from "@/lib/timeRanges"
import { colorForIndex } from "@/lib/chartColors"
import type { DeviceSummary } from "@/types/fleet"

/**
 * One machine on the merged Devices page: its health, its headline numbers,
 * and an hour of CPU and memory.
 *
 * `latest` is null whenever nothing landed inside the server's freshness
 * window, and the card then shows no numbers at all. That is the honest render:
 * a figure from twenty minutes ago displayed next to a live status dot is a
 * claim about right now that the data does not support.
 *
 * Removal lives on the card itself, self-contained (own confirm state, own
 * DELETE call), rather than plumbed through the page as a callback prop — the
 * same shape the mobile app's DeviceScreen uses. `onRemoved` is the one thing
 * the page needs to know: refetch the list.
 */
export function DeviceSummaryCard({
  summary,
  onRemoved,
}: {
  summary: DeviceSummary
  onRemoved?: () => void
}) {
  const { device, health, latest, disks, sparkline } = summary
  const worstDisk = disks[0] ?? null

  const [confirming, setConfirming] = useState(false)
  const [removing, setRemoving] = useState(false)
  const [removeError, setRemoveError] = useState<string | null>(null)

  const remove = async () => {
    setRemoving(true)
    setRemoveError(null)
    try {
      await apiFetch(`/devices/${device.id}`, { method: "DELETE" })
      setConfirming(false)
      onRemoved?.()
    } catch {
      setRemoveError("Could not remove this device.")
      setRemoving(false)
    }
  }

  return (
    // The device id, not the index: a suite that addresses the third card by
    // position starts asserting about a different machine the moment the fleet
    // is reordered or one goes offline.
    <Card className="transition-colors hover:bg-accent/30" data-testid={`device-card-${device.id}`}>
      <CardContent className="flex flex-col gap-4 pt-6">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <Link
              to={`/devices/${device.id}/history`}
              className="font-medium hover:underline"
              data-testid="device-name"
            >
              {device.name}
            </Link>
            <p className="truncate text-sm text-muted-foreground">
              {device.hostname ?? "—"}
              {device.os ? ` · ${device.os}` : ""}
              {device.arch ? ` · ${device.arch}` : ""}
            </p>
          </div>
          <div className="flex flex-col items-end gap-1">
            <HealthScore health={health} />
            <DeviceStatusBadge status={device.status} />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
          <Stat label="CPU">
            <MetricValue
              value={latest?.cpu_percent}
              format={(v) => v.toFixed(0)}
              unit="%"
            />
          </Stat>
          <Stat label="Memory">
            <MetricValue
              value={latest?.mem_percent}
              format={(v) => v.toFixed(0)}
              unit="%"
            />
          </Stat>
          <Stat label={worstDisk ? `Disk ${worstDisk.mount}` : "Disk"}>
            <MetricValue value={worstDisk?.percent} format={(v) => v.toFixed(0)} unit="%" />
          </Stat>
          <Stat label="Network in">
            <MetricValue value={latest?.net_rx_bytes_per_s} format={formatBytesPerSecond} />
          </Stat>
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <TrendPanel
            label="CPU"
            values={sparkline.cpu_percent}
            color={colorForIndex(0)}
          />
          <TrendPanel
            label="Memory"
            values={sparkline.mem_percent}
            color={colorForIndex(1)}
          />
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <span>last seen {formatRelative(device.last_seen_at)}</span>
          {latest?.uptime_seconds != null && <span>up {formatDuration(latest.uptime_seconds)}</span>}
          {latest?.load1 != null && <span>load {latest.load1.toFixed(2)}</span>}
          {latest?.mem_total_bytes != null && <span>{formatBytes(latest.mem_total_bytes)} RAM</span>}
          <Link
            to={`/devices/${device.id}/live`}
            className="text-foreground hover:underline"
          >
            Live →
          </Link>
          <button
            type="button"
            onClick={() => setConfirming(true)}
            className="ml-auto text-muted-foreground hover:text-destructive"
          >
            Remove
          </button>
        </div>

        {removeError && <p className="text-xs text-destructive">{removeError}</p>}
      </CardContent>

      {confirming && (
        <CardContent className="flex flex-wrap items-center justify-end gap-3 border-t border-border pt-4">
          <span className="text-xs text-muted-foreground">
            Remove <strong className="font-medium text-foreground">{device.name}</strong>? Its
            agent token is revoked immediately, so the agent stops reporting. History already
            collected is kept.
          </span>
          <div className="flex gap-2">
            <Button size="sm" variant="destructive" disabled={removing} onClick={() => void remove()}>
              {removing ? "Removing…" : "Remove device"}
            </Button>
            <Button size="sm" variant="outline" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </div>
        </CardContent>
      )}
    </Card>
  )
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col">
      <span className="truncate text-xs text-muted-foreground">{label}</span>
      <span className="tabular-nums">{children}</span>
    </div>
  )
}

function TrendPanel({
  label,
  values,
  color,
}: {
  label: string
  values: (number | null)[]
  color: string
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      {/* Fixed 0–100 domain: both series are percentages, and autoscaling them
          would make a machine idling between 3% and 5% look identical to one
          swinging between 20% and 90%. */}
      <Sparkline values={values} domain={[0, 100]} color={color} height={36} label={label} />
    </div>
  )
}
