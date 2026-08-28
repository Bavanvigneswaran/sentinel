/**
 * One device's history over a chosen window. The phone counterpart of
 * web/src/pages/DeviceHistoryPage.tsx, and the largest thing Phase 10a
 * deliberately left in the web console.
 *
 * The rule it inherits unchanged: the screen never asks for a resolution, it
 * asks for a *window*, and reports back whichever source and bucket width the
 * server picked. The caption under the picker is the point — an hour of
 * 1-second samples and a month of hour-wide averages are drawn identically and
 * mean different things.
 *
 * Which charts exist depends on the platform, exactly as the web page decides
 * it (docs/ANDROID_METRICS.md): an Android device has no CPU and no disk-I/O
 * chart because it can never fill one, gains a Battery chart, and the screen
 * says so rather than being quietly shorter.
 *
 * One chart per row rather than the web's two-column grid: at ~330pt a
 * two-across layout gives each series about 150pt of x-axis, which is fewer
 * pixels than a 30-day window has buckets.
 */

import { useEffect, useMemo, useState } from "react"
import { StyleSheet, Text, View } from "react-native"

import { HistoryChart } from "@/components/charts/HistoryChart"
import { EmptyState, Screen } from "@/components/Screen"
import { Card, CardTitle, ErrorNote, Segmented } from "@/components/ui"
import { apiFetch } from "@/lib/api"
import { formatBytesPerSecond, formatMs } from "@/lib/formatters"
import type { HistorySeries } from "@/lib/historyChartFrame"
import { mergePivots, pivotByEntity, pivotColumns } from "@/lib/seriesPivot"
import { DEFAULT_RANGE, describeSource, TIME_RANGES } from "@/lib/timeRanges"
import type { RootStackScreenProps } from "@/navigation/types"
import { spacing, text } from "@/theme"
import type { DeviceSummary } from "@/types/fleet"
import type { Series } from "@/types/series"

const DOMAINS = ["system", "net", "disk_io", "latency", "disk_usage"] as const

const PERCENT_DOMAIN: [number, number] = [0, 100]
const percent = (v: number) => `${v.toFixed(0)}%`

interface ChartSpec {
  title: string
  timestamps: number[]
  series: HistorySeries[]
  format: (v: number) => string
  domain?: [number, number]
}

export function HistoryScreen({ route }: RootStackScreenProps<"History">) {
  const { deviceId } = route.params
  const [rangeKey, setRangeKey] = useState(DEFAULT_RANGE.key)
  const range = TIME_RANGES.find((r) => r.key === rangeKey) ?? DEFAULT_RANGE

  const [platform, setPlatform] = useState<"desktop" | "android" | null>(null)
  // The result carries the request it answers, so "still loading" is derived by
  // comparing it against what is being asked for now rather than tracked as a
  // separate flag an effect has to set and clear. Same shape as the web page.
  const [result, setResult] = useState<{
    key: string
    series: Series | null
    error: string | null
  } | null>(null)

  const requestKey = `${deviceId}:${range.seconds}`
  const loading = result?.key !== requestKey
  const series = loading ? null : result.series
  const error = loading ? null : result.error

  // The platform decides which charts exist, and it arrives on a different
  // request than the data. Building charts before it lands would briefly draw
  // an Android phone the desktop set — an empty CPU chart, no battery chart —
  // which is the exact impression this screen exists to avoid, even for one
  // frame. A failed summary is a resolved answer too: fall back to desktop
  // rather than withholding the charts forever.
  useEffect(() => {
    let cancelled = false
    apiFetch<DeviceSummary>(`/devices/${deviceId}/summary`)
      .then((data) => {
        if (!cancelled) setPlatform(data.device.platform)
      })
      .catch(() => {
        if (!cancelled) setPlatform("desktop")
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

  const charts = useMemo(
    () => (series && platform ? buildCharts(series, platform) : null),
    [series, platform],
  )
  const hasPoints = series !== null && series.system.length > 0

  return (
    <Screen testID="screen-history">
      <Segmented
        options={TIME_RANGES.map((r) => ({ value: r.key, label: r.label }))}
        value={rangeKey}
        onChange={setRangeKey}
      />

      {series && (
        <Text style={text.tiny}>{describeSource(series.source, series.resolution_seconds)}</Text>
      )}

      {error && <ErrorNote message={error} />}

      {series?.truncated && (
        <Text style={text.small}>
          This window returned more rows than the server will send at once, so its oldest part is
          missing. Narrow the range to see all of it.
        </Text>
      )}

      {loading && series === null && <Text style={text.small}>Loading…</Text>}

      {!loading && series !== null && !hasPoints && !error && (
        <EmptyState
          title="No data for this window"
          body={
            "Either the agent was not running, or the samples have passed out of retention. " +
            "Nothing is filled in to cover the gap."
          }
        />
      )}

      {charts &&
        hasPoints &&
        charts.map((chart) =>
          chart.series.length === 0 ? null : (
            <Card key={chart.title}>
              <CardTitle>{chart.title}</CardTitle>
              <HistoryChart
                timestamps={chart.timestamps}
                series={chart.series}
                domain={chart.domain}
                valueFormatter={chart.format}
              />
            </Card>
          ),
        )}

      {charts && hasPoints && platform === "android" && (
        // Shorter than a server's page, and that is the honest outcome rather
        // than a rendering bug. Saying so is what stops it reading as one.
        <View style={styles.note}>
          <Text style={text.tiny}>
            No CPU or disk-I/O charts: Android denies an app /proc/stat and /proc/diskstats, so
            this device measures neither. They are excluded from its health score rather than
            counted as healthy.
          </Text>
        </View>
      )}
    </Screen>
  )
}

/** Which charts a device's platform can actually fill — the same decision
 * web's buildCharts() makes, kept deliberately recognisable against it. */
function buildCharts(series: Series, platform: "desktop" | "android"): ChartSpec[] {
  const isAndroid = platform === "android"

  const cpu = pivotColumns(series.system, [
    { key: "cpu_percent", label: "total" },
    { key: "cpu_user_percent", label: "user" },
    { key: "cpu_system_percent", label: "system" },
  ])
  const memory = pivotColumns(series.system, [
    { key: "mem_percent", label: "memory" },
    { key: "swap_percent", label: "swap" },
  ])
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
      : [{ title: "CPU", ...cpu, format: percent, domain: PERCENT_DOMAIN }]),
    { title: "Memory", ...memory, format: percent, domain: PERCENT_DOMAIN },
    {
      title: "Disk usage",
      ...pivotByEntity(series.disk_usage, "mount", "percent"),
      format: percent,
      domain: PERCENT_DOMAIN,
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
    // Only a device with a battery has one of these. Two different units on
    // one axis — a percentage and degrees Celsius — so the tick carries no
    // unit and each series is labelled instead.
    ...(isAndroid ? [{ title: "Battery", ...battery, format: (v: number) => v.toFixed(0) }] : []),
  ]
}

const styles = StyleSheet.create({
  note: { paddingBottom: spacing.md },
})
