import { Link } from "react-router"

import { DeviceStatusBadge } from "@/components/DeviceStatusBadge"
import { HealthScore } from "@/components/HealthScore"
import { MetricValue } from "@/components/MetricValue"
import { Sparkline } from "@/components/Sparkline"
import { Card, CardContent } from "@/components/ui/card"
import { formatBytes, formatBytesPerSecond } from "@/lib/formatters"
import { formatDuration, formatRelative } from "@/lib/timeRanges"
import { colorForIndex } from "@/lib/chartColors"
import type { DeviceSummary } from "@/types/fleet"

/**
 * One machine on the fleet page: its health, its headline numbers, and an hour
 * of CPU and memory.
 *
 * `latest` is null whenever nothing landed inside the server's freshness
 * window, and the card then shows no numbers at all. That is the honest render:
 * a figure from twenty minutes ago displayed next to a live status dot is a
 * claim about right now that the data does not support.
 */
export function DeviceSummaryCard({ summary }: { summary: DeviceSummary }) {
  const { device, health, latest, disks, sparkline } = summary
  const worstDisk = disks[0] ?? null

  return (
    <Card className="transition-colors hover:bg-accent/30">
      <CardContent className="flex flex-col gap-4 pt-6">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <Link
              to={`/devices/${device.id}/history`}
              className="font-medium hover:underline"
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
            className="ml-auto text-foreground hover:underline"
          >
            Live →
          </Link>
        </div>
      </CardContent>
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
