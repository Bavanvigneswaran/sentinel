import { useEffect, useMemo, useState } from "react"
import { Link, useParams, useSearchParams } from "react-router"

import { AppLayout } from "@/components/AppLayout"
import { HistoryChart } from "@/components/charts/HistoryChart"
import type { ForecastOverlay } from "@/components/charts/HistoryChart"
import { DeviceStatusBadge } from "@/components/DeviceStatusBadge"
import { ExhaustionSummary } from "@/components/ExhaustionSummary"
import { NoveltyScore } from "@/components/NoveltyScore"
import { HealthBreakdown, HealthScore } from "@/components/HealthScore"
import { TimeRangePicker } from "@/components/TimeRangePicker"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { apiFetch } from "@/lib/api"
import { colorForIndex } from "@/lib/chartColors"
import { forecastCaption, toForecastOverlay } from "@/lib/forecastOverlay"
import { formatBytesPerSecond, formatMs } from "@/lib/formatters"
import { mergePivots, pivotByEntity, pivotColumns } from "@/lib/seriesPivot"
import { describeSource, rangeByKey, type TimeRange } from "@/lib/timeRanges"
import type { DeviceSummary } from "@/types/fleet"
import type { DeviceNovelty } from "@/types/novelty"
import type { ExhaustionForecast, MetricForecast } from "@/types/forecast"
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

  const [forecasts, setForecasts] = useState<MetricForecast[]>([])
  const [exhaustion, setExhaustion] = useState<ExhaustionForecast[]>([])
  const [novelty, setNovelty] = useState<DeviceNovelty | null>(null)

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

  useEffect(() => {
    let cancelled = false
    Promise.all([
      apiFetch<MetricForecast[]>(`/forecasts?device_id=${deviceId}`),
      apiFetch<ExhaustionForecast[]>(`/forecasts/exhaustion?device_id=${deviceId}`),
      apiFetch<DeviceNovelty>(`/devices/${deviceId}/novelty`),
    ])
      .then(([f, e, n]) => {
        if (!cancelled) {
          setForecasts(f)
          setExhaustion(e)
          setNovelty(n)
        }
      })
      .catch(() => {
        // Non-fatal: the history charts and health card still work without
        // a forecast overlay or a time-to-capacity estimate.
      })
    return () => {
      cancelled = true
    }
  }, [deviceId])

  // Which charts exist depends on the platform, and the summary that carries it
  // is a separate request from the series. Building them before it lands would
  // briefly draw an Android device the desktop set — an empty CPU chart and no
  // battery chart — which is the exact impression this page exists to avoid,
  // even for one paint. `summaryError` is a resolved answer too: if the summary
  // will never arrive, fall back rather than withholding the charts forever.
  const platformKnown = summary !== null || summaryError !== null
  const platform = summary?.device.platform ?? "desktop"
  const charts = useMemo(
    () => (series && platformKnown ? buildCharts(series, forecasts, platform) : null),
    [series, forecasts, platform, platformKnown],
  )
  const hasPoints = series !== null && series.system.length > 0

  return (
    <AppLayout active="devices">
      <div>
        <Link to="/" className="text-sm text-muted-foreground hover:text-foreground">
          ← Devices
        </Link>
      </div>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 data-testid="page-title" className="text-xl font-semibold tracking-tight">
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
            {exhaustion.length > 0 && (
              <div className="shrink-0 sm:border-l sm:pl-8">
                <ExhaustionSummary estimates={exhaustion} />
              </div>
            )}
            {novelty && (
              <div className="shrink-0 sm:border-l sm:pl-8">
                <NoveltyScore novelty={novelty} />
              </div>
            )}
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
                    forecast={chart.forecast}
                  />
                  {chart.caption && (
                    <p className="pt-2 text-xs text-muted-foreground">{chart.caption}</p>
                  )}
                </CardContent>
              </Card>
            ),
          )}
        </div>
      )}

      {charts && hasPoints && platform === "android" && (
        // The page is shorter for a phone than for a server, and that is the
        // honest outcome rather than a rendering bug. Saying so is what stops
        // it reading as one.
        <p className="text-xs text-muted-foreground">
          No CPU or disk-I/O charts: Android denies an app <code>/proc/stat</code> and{" "}
          <code>/proc/diskstats</code>, so this device measures neither. They are excluded
          from its health score rather than counted as healthy.
        </p>
      )}
    </AppLayout>
  )
}

interface ChartSpec {
  title: string
  /** Rendered under the chart — currently how far to trust its forecast. */
  caption?: string
  timestamps: number[]
  series: { label: string; color: string; values: (number | null)[] }[]
  format?: (v: number) => string
  domain?: [number, number]
  forecast?: ForecastOverlay
}

const percent = (v: number) => `${v.toFixed(0)}%`

function sortedEntities<T>(points: T[], key: keyof T): string[] {
  return [...new Set(points.map((p) => String(p[key])))].sort((a, b) => a.localeCompare(b))
}

/**
 * Which charts a device's platform can actually fill.
 *
 * Phase 10b: an Android device measures a different set than a Linux box
 * (docs/ANDROID_METRICS.md). Drawing it a permanently blank CPU chart and a
 * permanently blank Disk I/O chart — while the battery and thermal readings it
 * *does* report appear nowhere — reads as a broken agent rather than a
 * different kind of machine. The charts it cannot fill are dropped and named in
 * a caption; the ones only it can fill are added.
 *
 * Note the asymmetry with a *missing* reading: a desktop keeps its CPU chart
 * even when the window happens to be empty, because an empty window there means
 * "the agent reported nothing over this range" — real information. Android has
 * no CPU chart at all, because there is never going to be anything to draw.
 */
function buildCharts(
  series: Series,
  forecasts: MetricForecast[],
  platform: "desktop" | "android",
): ChartSpec[] {
  const cpu = pivotColumns(series.system, [
    { key: "cpu_percent", label: "total" },
    { key: "cpu_user_percent", label: "user" },
    { key: "cpu_system_percent", label: "system" },
  ])
  const memory = pivotColumns(series.system, [
    { key: "mem_percent", label: "memory" },
    { key: "swap_percent", label: "swap" },
  ])
  const diskUsage = pivotByEntity(series.disk_usage, "mount", "percent")

  const forecastFor = (metric: string) => forecasts.find((f) => f.metric === metric)
  const cpuForecast = toForecastOverlay(forecastFor("cpu_percent"), "total", colorForIndex(0))
  const memForecast = toForecastOverlay(forecastFor("mem_percent"), "memory", colorForIndex(0))

  // The disk forecast is fit against one specific mount (the fullest one at
  // computation time — see app/workers/forecast_worker.py) — only overlay it
  // when that mount is still one of the ones this window actually shows, and
  // in the same colour pivotByEntity gave it.
  const diskForecastRow = forecastFor("disk_percent")
  const mountIndex = diskForecastRow?.entity
    ? sortedEntities(series.disk_usage, "mount").indexOf(diskForecastRow.entity)
    : -1
  const diskForecast =
    mountIndex >= 0
      ? toForecastOverlay(diskForecastRow, diskForecastRow!.entity!, colorForIndex(mountIndex))
      : undefined

  const isAndroid = platform === "android"

  const battery = pivotColumns(series.system, [
    { key: "battery_percent", label: "charge" },
    // The battery thermistor, not a CPU package temperature — the only
    // thermometer an unprivileged Android app can read. Labelled for what it is.
    { key: "temperature_celsius", label: "battery °C" },
  ])

  return [
    // Percentages get a fixed 0–100 domain: autoscaling makes a machine idling
    // between 3% and 5% look exactly like one swinging from 20% to 90%.
    ...(isAndroid
      ? []
      : [
          {
            title: "CPU",
            ...cpu,
            format: percent,
            domain: [0, 100] as [number, number],
            forecast: cpuForecast,
            caption: forecastCaption(forecastFor("cpu_percent")),
          },
        ]),
    {
      title: "Memory",
      ...memory,
      format: percent,
      domain: [0, 100],
      forecast: memForecast,
      caption: forecastCaption(forecastFor("mem_percent")),
    },
    {
      title: "Disk usage",
      ...diskUsage,
      format: percent,
      domain: [0, 100],
      forecast: diskForecast,
      caption: forecastCaption(diskForecastRow),
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
    // /proc/diskstats is denied to Android apps, so there is no per-block-device
    // counter to draw and the chart would never have a series in it.
    ...(isAndroid
      ? []
      : [
          {
            title: "Disk I/O",
            ...mergePivots(
              pivotByEntity(series.disk_io, "disk", "read_bytes_per_s", "read "),
              pivotByEntity(series.disk_io, "disk", "write_bytes_per_s", "write "),
            ),
            format: formatBytesPerSecond,
          },
        ]),
    {
      title: "Latency",
      ...pivotByEntity(series.latency, "target", "rtt_ms_avg"),
      format: formatMs,
    },
    // Only a device with a battery has one of these. It goes last because it is
    // the slowest-moving series on the page.
    ...(isAndroid ? [{ title: "Battery", ...battery, format: batteryAxis }] : []),
  ]
}

/** Two different units on one axis — a percentage and degrees Celsius — so the
 *  tick itself cannot carry a unit. Each series is labelled instead. */
function batteryAxis(value: number): string {
  return value.toFixed(0)
}
