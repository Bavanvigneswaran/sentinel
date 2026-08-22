import { useEffect, useMemo, useState } from "react"
import { Link, useParams, useSearchParams } from "react-router"

import { AppLayout } from "@/components/AppLayout"
import { HistoryChart } from "@/components/charts/HistoryChart"
import { DeviceStatusBadge } from "@/components/DeviceStatusBadge"
import { HealthBreakdown, HealthScore } from "@/components/HealthScore"
import { TimeRangePicker } from "@/components/TimeRangePicker"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { apiFetch } from "@/lib/api"
import { formatBytesPerSecond, formatMs } from "@/lib/formatters"
import { mergePivots, pivotByEntity, pivotColumns } from "@/lib/seriesPivot"
import { describeSource, rangeByKey, type TimeRange } from "@/lib/timeRanges"
import type { DeviceSummary } from "@/types/fleet"
import type { Series } from "@/types/series"

const DOMAINS = ["system", "net", "disk_io", "latency", "disk_usage"] as const

/**
 * History for one device, over any window the retention policies still cover.
 *
 * The page never asks for a resolution — it asks for a window, and reports
 * whichever source and bucket width the server chose. That caption under the
 * charts is the point: an hour of 1-second samples and a year of hour-wide
 * averages are drawn the same way and mean different things.
 */
export function DeviceHistoryPage() {
  const { deviceId } = useParams<{ deviceId: string }>()
  if (!deviceId) return null
  return <DeviceHistoryView key={deviceId} deviceId={deviceId} />
}

function DeviceHistoryView({ deviceId }: { deviceId: string }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const range = rangeByKey(searchParams.get("range"))

  const [summary, setSummary] = useState<DeviceSummary | null>(null)
  const [summaryError, setSummaryError] = useState<string | null>(null)
  // The result carries the request it answers, so "still loading" is derived by
  // comparing it against what is being asked for now rather than tracked as a
  // separate flag that an effect has to remember to set and clear.
  const [result, setResult] = useState<{
    key: string
    series: Series | null
    error: string | null
  } | null>(null)

  const requestKey = `${deviceId}:${range.seconds}`
  const loading = result?.key !== requestKey
  const series = loading ? null : result.series
  const error = (loading ? null : result.error) ?? summaryError

  const setRange = (next: TimeRange) => {
    setSearchParams({ range: next.key }, { replace: true })
  }

  useEffect(() => {
    let cancelled = false
    apiFetch<DeviceSummary>(`/devices/${deviceId}/summary`)
      .then((data) => {
        if (!cancelled) setSummary(data)
      })
      .catch(() => {
        if (!cancelled) setSummaryError("Could not load this device.")
      })
    return () => {
      cancelled = true
    }
  }, [deviceId])

  useEffect(() => {
    let cancelled = false
    const key = `${deviceId}:${range.seconds}`
    const query = DOMAINS.map((d) => `domains=${d}`).join("&")

    apiFetch<Series>(`/devices/${deviceId}/series?range_seconds=${range.seconds}&${query}`)
      .then((data) => {
        if (!cancelled) setResult({ key, series: data, error: null })
      })
      .catch(() => {
        if (!cancelled) {
          setResult({ key, series: null, error: "Could not load history for this window." })
        }
      })
    return () => {
      cancelled = true
    }
  }, [deviceId, range.seconds])

  const charts = useMemo(() => (series ? buildCharts(series) : null), [series])
  const hasPoints = series !== null && series.system.length > 0

  return (
    <AppLayout active="dashboard">
      <div>
        <Link to="/" className="text-sm text-muted-foreground hover:text-foreground">
          ← Fleet
        </Link>
      </div>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">
            {summary?.device.name ?? "Device"}
          </h1>
          <p className="text-sm text-muted-foreground">
            {summary?.device.hostname ?? "—"}
            {summary?.device.os ? ` · ${summary.device.os}` : ""}
            {summary?.device.os_version ? ` ${summary.device.os_version}` : ""}
          </p>
        </div>
        <div className="flex items-center gap-4">
          {summary && <DeviceStatusBadge status={summary.device.status} />}
          <Link
            to={`/devices/${deviceId}/live`}
            className="text-sm text-muted-foreground hover:text-foreground"
          >
            Live monitoring →
          </Link>
        </div>
      </div>

      {summary && (
        <Card>
          <CardContent className="flex flex-col gap-4 pt-6 sm:flex-row sm:items-start sm:gap-8">
            <div className="shrink-0">
              <span className="text-xs text-muted-foreground">Health</span>
              <HealthScore health={summary.health} />
            </div>
            <div className="min-w-0 flex-1">
              <HealthBreakdown health={summary.health} />
            </div>
          </CardContent>
        </Card>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <TimeRangePicker value={range} onChange={setRange} />
        {series && (
          <span className="text-xs text-muted-foreground">
            {describeSource(series.source, series.resolution_seconds)}
          </span>
        )}
      </div>

      {error && (
        <Card>
          <CardContent className="pt-6 text-sm text-destructive">{error}</CardContent>
        </Card>
      )}

      {series?.truncated && (
        <Card>
          <CardContent className="pt-6 text-sm text-amber-500">
            This window returned more rows than the server will send in one response, so the
            oldest part is missing. Narrow the range to see all of it.
          </CardContent>
        </Card>
      )}

      {!loading && series !== null && !hasPoints && !error && (
        <Card>
          <CardContent className="pt-6 text-sm text-muted-foreground">
            No data for this window. Either the agent was not running, or the samples have
            passed out of retention — nothing is filled in to cover the gap.
          </CardContent>
        </Card>
      )}

      {charts && hasPoints && (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          {charts.map((chart) =>
            chart.series.length === 0 ? null : (
              <Card key={chart.title}>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    {chart.title}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <HistoryChart
                    timestamps={chart.timestamps}
                    series={chart.series}
                    height={180}
                    valueFormatter={chart.format}
                    domain={chart.domain}
                  />
                </CardContent>
              </Card>
            ),
          )}
        </div>
      )}
    </AppLayout>
  )
}

interface ChartSpec {
  title: string
  timestamps: number[]
  series: { label: string; color: string; values: (number | null)[] }[]
  format?: (v: number) => string
  domain?: [number, number]
}

const percent = (v: number) => `${v.toFixed(0)}%`

function buildCharts(series: Series): ChartSpec[] {
  const cpu = pivotColumns(series.system, [
    { key: "cpu_percent", label: "total" },
    { key: "cpu_user_percent", label: "user" },
    { key: "cpu_system_percent", label: "system" },
  ])
  const memory = pivotColumns(series.system, [
    { key: "mem_percent", label: "memory" },
    { key: "swap_percent", label: "swap" },
  ])

  return [
    // Percentages get a fixed 0–100 domain: autoscaling makes a machine idling
    // between 3% and 5% look exactly like one swinging from 20% to 90%.
    { title: "CPU", ...cpu, format: percent, domain: [0, 100] },
    { title: "Memory", ...memory, format: percent, domain: [0, 100] },
    {
      title: "Disk usage",
      ...pivotByEntity(series.disk_usage, "mount", "percent"),
      format: percent,
      domain: [0, 100],
    },
    {
      title: "Network in",
      ...pivotByEntity(series.net, "nic", "rx_bytes_per_s"),
      format: formatBytesPerSecond,
    },
    {
      title: "Network out",
      ...pivotByEntity(series.net, "nic", "tx_bytes_per_s"),
      format: formatBytesPerSecond,
    },
    {
      title: "Disk I/O",
      ...mergePivots(
        pivotByEntity(series.disk_io, "disk", "read_bytes_per_s", "read "),
        pivotByEntity(series.disk_io, "disk", "write_bytes_per_s", "write "),
      ),
      format: formatBytesPerSecond,
    },
    {
      title: "Latency",
      ...pivotByEntity(series.latency, "target", "rtt_ms_avg"),
      format: formatMs,
    },
  ]
}
