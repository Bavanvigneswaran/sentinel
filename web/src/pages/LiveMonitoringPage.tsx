import { useEffect, useMemo, useState } from "react"
import { Link, useParams } from "react-router"

import { AppLayout } from "@/components/AppLayout"
import { LiveChart } from "@/components/charts/LiveChart"
import { DeviceStatusBadge } from "@/components/DeviceStatusBadge"
import { MetricValue } from "@/components/MetricValue"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useDeviceStream } from "@/hooks/useDeviceStream"
import { ApiError, apiFetch } from "@/lib/api"
import { colorForIndex } from "@/lib/chartColors"
import { formatBytes, formatBytesPerSecond, formatMs } from "@/lib/formatters"
import { subBufferRef } from "@/lib/streamBuffers"
import type { Device } from "@/types/api"

function ChartCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  )
}

export function LiveMonitoringPage() {
  const { deviceId } = useParams<{ deviceId: string }>()
  if (!deviceId) return null
  // Keyed by deviceId so navigating between two devices' live pages fully
  // remounts this subtree — fresh ring buffers, a fresh socket subscription,
  // a fresh uPlot instance — instead of every piece of state needing its own
  // "did deviceId change" reset logic.
  return <LiveMonitoringView key={deviceId} deviceId={deviceId} />
}

function LiveMonitoringView({ deviceId }: { deviceId: string }) {
  const [device, setDevice] = useState<Device | null>(null)
  const [deviceError, setDeviceError] = useState<string | null>(null)

  const stream = useDeviceStream(deviceId)
  const systemRef = useMemo(() => subBufferRef(stream.buffers, "system"), [stream.buffers])
  const netRef = useMemo(() => subBufferRef(stream.buffers, "net"), [stream.buffers])
  const diskIoRef = useMemo(() => subBufferRef(stream.buffers, "diskIo"), [stream.buffers])
  const latencyRef = useMemo(() => subBufferRef(stream.buffers, "latency"), [stream.buffers])

  useEffect(() => {
    let cancelled = false
    // Fetched once for the header. Live status updates come from the socket
    // (stream.status), not from repolling this.
    apiFetch<Device>(`/devices/${deviceId}`)
      .then((found) => {
        if (!cancelled) setDevice(found)
      })
      .catch((err) => {
        if (cancelled) return
        setDeviceError(
          err instanceof ApiError && err.status === 404
            ? "Device not found."
            : "Could not load this device.",
        )
      })
    return () => {
      cancelled = true
    }
  }, [deviceId])

  const effectiveStatus = stream.status ?? device?.status ?? "pending"
  const cpuProcesses = stream.processes.filter((p) => p.rank_by === "cpu")
  const memProcesses = stream.processes.filter((p) => p.rank_by === "memory")

  return (
    <AppLayout active="devices">
      <div className="flex items-center gap-4">
        <Link to="/devices" className="text-sm text-muted-foreground hover:text-foreground">
          ← Devices
        </Link>
        <Link
          to={`/devices/${deviceId}/history`}
          className="text-sm text-muted-foreground hover:text-foreground"
        >
          History →
        </Link>
      </div>

      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">{device?.name ?? "Device"}</h1>
          <p className="text-sm text-muted-foreground">
            {device?.hostname ?? "—"}
            {device?.os ? ` · ${device.os}` : ""}
            {device?.os_version ? ` ${device.os_version}` : ""}
          </p>
        </div>
        <DeviceStatusBadge status={effectiveStatus} />
      </div>

      {deviceError && (
        <Card>
          <CardContent className="pt-6 text-sm text-destructive">{deviceError}</CardContent>
        </Card>
      )}

      {effectiveStatus !== "online" && !deviceError && (
        <Card>
          <CardContent className="pt-6 text-sm text-muted-foreground">
            This device is not currently connected. Charts will populate once its agent
            reconnects — nothing shown here is synthesised while it's offline.
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ChartCard title="CPU">
          <LiveChart
            title="CPU"
            buffer={systemRef}
            height={180}
            series={[
              { key: "cpu_percent", label: "total", color: colorForIndex(0) },
              { key: "cpu_user_percent", label: "user", color: colorForIndex(1) },
              { key: "cpu_system_percent", label: "system", color: colorForIndex(2) },
            ]}
            valueFormatter={(v) => `${v.toFixed(0)}%`}
          />
        </ChartCard>

        <ChartCard title="Memory">
          <LiveChart
            title="Memory"
            buffer={systemRef}
            height={180}
            series={[
              { key: "mem_percent", label: "used", color: colorForIndex(0) },
              { key: "swap_percent", label: "swap", color: colorForIndex(1) },
            ]}
            valueFormatter={(v) => `${v.toFixed(0)}%`}
          />
        </ChartCard>

        <ChartCard title="Network">
          <LiveChart
            title="Network"
            buffer={netRef}
            height={180}
            deriveSeries={(keys) =>
              keys.map((k, i) => ({ key: k, label: k, color: colorForIndex(i) }))
            }
            valueFormatter={(v) => formatBytesPerSecond(v)}
          />
        </ChartCard>

        <ChartCard title="Disk I/O">
          <LiveChart
            title="Disk I/O"
            buffer={diskIoRef}
            height={180}
            deriveSeries={(keys) =>
              keys.map((k, i) => ({ key: k, label: k, color: colorForIndex(i) }))
            }
            valueFormatter={(v) => formatBytesPerSecond(v)}
          />
        </ChartCard>

        <ChartCard title="Latency">
          <LiveChart
            title="Latency"
            buffer={latencyRef}
            height={180}
            deriveSeries={(keys) =>
              keys.map((k, i) => ({ key: k, label: k, color: colorForIndex(i) }))
            }
            valueFormatter={(v) => formatMs(v)}
          />
        </ChartCard>

        <ChartCard title="Disk usage">
          <div className="flex flex-col gap-3">
            {stream.diskUsage.length === 0 ? (
              <p className="text-sm text-muted-foreground">No data yet.</p>
            ) : (
              stream.diskUsage.map((d) => (
                <div key={d.mount} className="flex flex-col gap-1">
                  <div className="flex items-center justify-between text-sm">
                    <span className="font-mono text-xs">{d.mount}</span>
                    <MetricValue value={d.percent} format={(v) => `${v.toFixed(0)}%`} />
                  </div>
                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full bg-primary"
                      style={{ width: `${Math.min(d.percent ?? 0, 100)}%` }}
                    />
                  </div>
                  <span className="text-xs text-muted-foreground">
                    {d.used_bytes !== null && d.total_bytes !== null
                      ? `${formatBytes(d.used_bytes)} of ${formatBytes(d.total_bytes)}`
                      : "unavailable"}
                  </span>
                </div>
              ))
            )}
          </div>
        </ChartCard>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ProcessTable title="Top by CPU" processes={cpuProcesses} metric="cpu" />
        <ProcessTable title="Top by memory" processes={memProcesses} metric="memory" />
      </div>
    </AppLayout>
  )
}

function ProcessTable({
  title,
  processes,
  metric,
}: {
  title: string
  processes: { rank: number; pid: number; name: string; cpu_percent: number | null; memory_bytes: number | null }[]
  metric: "cpu" | "memory"
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {processes.length === 0 ? (
          <p className="text-sm text-muted-foreground">No data yet.</p>
        ) : (
          <table className="w-full text-sm">
            <tbody>
              {processes
                .slice()
                .sort((a, b) => a.rank - b.rank)
                .map((p) => (
                  <tr key={p.pid} className="border-b border-border last:border-0">
                    <td className="py-1.5 pr-2 font-mono text-xs text-muted-foreground">
                      {p.pid}
                    </td>
                    <td className="py-1.5 pr-2">{p.name}</td>
                    <td className="py-1.5 text-right">
                      {metric === "cpu" ? (
                        <MetricValue value={p.cpu_percent} format={(v) => `${v.toFixed(1)}%`} />
                      ) : (
                        <MetricValue
                          value={p.memory_bytes}
                          format={(v) => formatBytes(v)}
                        />
                      )}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  )
}
