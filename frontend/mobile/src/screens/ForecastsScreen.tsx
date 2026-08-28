/**
 * Forecasts — soonest-to-exhaust first. Ported from
 * web/src/pages/ForecastsPage.tsx.
 *
 * Every card below the days-until number now carries a small chart —
 * ForecastChart, the react-native-svg counterpart of web's HistoryChart used
 * with no real series (uPlot does not run here). "This disk fills in four
 * days" as text alone is what a phone showed for a while; the actual
 * predicted trend and its confidence band are real numbers the backend
 * already stores and were simply never drawn on this screen.
 */

import { useEffect, useState } from "react"
import { StyleSheet, Text, View } from "react-native"

import { ForecastChart } from "@/components/charts/ForecastChart"
import { EmptyState, Screen } from "@/components/Screen"
import { Card, ErrorNote } from "@/components/ui"
import { usePolledResource } from "@/hooks/usePolledResource"
import { apiFetch } from "@/lib/api"
import { colorForIndex } from "@/lib/chartColors"
import { withDeviceScope } from "@/lib/deviceScope"
import { formatDaysUntil } from "@/lib/formatters"
import { formatRelative } from "@/lib/timeRanges"
import { colors, spacing, text } from "@/theme"
import type { RootStackScreenProps } from "@/navigation/types"
import type { Device } from "@/types/api"
import type { ExhaustionForecast, MetricForecast } from "@/types/forecast"

const POLL_INTERVAL_MS = 30_000

const METRIC_LABEL: Record<string, string> = {
  mem_percent: "Memory",
  disk_percent: "Disk",
  cpu_percent: "CPU",
  swap_percent: "Swap",
  packet_loss_percent: "Packet loss",
  cpu_iowait_percent: "IO wait",
}

/** Only say something when it is not the reassuring default — a "high"
 *  confidence note beside every row is noise. */
const CONFIDENCE_NOTE: Record<string, string | undefined> = {
  provisional: "Provisional — not much history behind this yet.",
  medium: "Will steady as more history accumulates.",
  high: undefined,
}

/** Matches the colour each metric already has on the fleet card / web's
 * ForecastsPage — the same series should look like itself everywhere. */
const METRIC_COLOR: Record<string, string> = {
  cpu_percent: colorForIndex(0),
  mem_percent: colorForIndex(1),
  disk_percent: colorForIndex(2),
  swap_percent: colorForIndex(3),
  packet_loss_percent: colorForIndex(4),
  cpu_iowait_percent: colorForIndex(5),
}

export function ForecastsScreen({ route }: RootStackScreenProps<"Forecasts">) {
  const deviceId = route.params?.deviceId
  const deviceName = route.params?.deviceName
  const [devices, setDevices] = useState<Record<string, Device>>({})

  const exhaustion = usePolledResource<ExhaustionForecast[]>(
    withDeviceScope("/forecasts/exhaustion", deviceId),
    POLL_INTERVAL_MS,
    "Could not load projections.",
  )
  const forecasts = usePolledResource<MetricForecast[]>(
    withDeviceScope("/forecasts", deviceId),
    POLL_INTERVAL_MS,
    "Could not load forecasts.",
  )

  useEffect(() => {
    apiFetch<Device[]>("/devices")
      .then((list) => setDevices(Object.fromEntries(list.map((d) => [d.id, d]))))
      .catch(() => {
        // Non-fatal.
      })
  }, [])

  // Soonest first; anything not trending towards a limit sorts to the end
  // rather than being hidden — "not filling up" is an answer too, and one
  // worth being able to see.
  const rows = [...(exhaustion.data ?? [])].sort((a, b) => {
    const av = a.projected_at ? new Date(a.projected_at).getTime() : Number.POSITIVE_INFINITY
    const bv = b.projected_at ? new Date(b.projected_at).getTime() : Number.POSITIVE_INFINITY
    return av - bv
  })

  const fitted = (forecasts.data ?? []).filter((f) => f.points.length > 0)

  /* The worker upserts a row per (device, metric) even when it found too
   * little history to fit anything, so "are there any rows at all" tells the
   * two empty states apart — nothing computed yet, versus computed and nothing
   * is heading for a limit. They are opposite pieces of news and were sharing
   * one message. */
  const stored = forecasts.data ?? []
  const lastComputedAt =
    stored.length > 0
      ? stored.reduce((newest, f) => (f.computed_at > newest ? f.computed_at : newest), stored[0].computed_at)
      : null

  return (
    <Screen testID="screen-forecasts"
      title="Forecasts"
      refreshing={exhaustion.refreshing}
      onRefresh={() => {
        exhaustion.refresh()
        forecasts.refresh()
      }}
    >
      {deviceName && (
        <Text style={text.tiny}>Showing {deviceName} only. Open this from More for every device.</Text>
      )}
      <Text style={text.small}>
        Where each machine is heading, from its own measured trend. Nothing here is a
        threshold anybody set.
      </Text>

      {exhaustion.error && <ErrorNote message={exhaustion.error} />}

      <Text style={text.heading}>Time to capacity</Text>

      {exhaustion.data && rows.length === 0 && (
        <EmptyState
          title={lastComputedAt === null ? "Nothing projected yet" : "Nothing filling up"}
          body={
            lastComputedAt === null
              ? "Forecasts are recomputed on a timer rather than on every reading, so a freshly enrolled device appears at the next sweep — within a couple of minutes."
              : `No device is trending towards its memory or disk limit. Last recomputed ${formatRelative(lastComputedAt)}.`
          }
        />
      )}

      {rows.map((row) => {
        // Theil-Sen (this ETA) and Holt-Winters (the plotted trend) are
        // separate fits off the same underlying samples — see
        // ForecastsPage.tsx's identical match on web. A row can have a real
        // ETA a tick or two before the trend has enough points to plot.
        const matchingForecast = (forecasts.data ?? []).find(
          (f) =>
            f.device_id === row.device_id && f.metric === row.metric && f.entity === row.entity,
        )
        return (
          <Card key={`${row.device_id}-${row.metric}-${row.entity ?? ""}`}>
            <View style={styles.header}>
              <Text style={text.heading}>
                {METRIC_LABEL[row.metric] ?? row.metric}
                {row.entity ? ` · ${row.entity}` : ""}
              </Text>
              <Text
                style={[
                  styles.days,
                  { color: row.projected_at == null ? colors.muted : colors.degraded },
                ]}
              >
                {/* Null is "not heading for a limit on any horizon worth acting
                    on" — see analysis/forecast.py. Saying so beats an em dash. */}
                {row.projected_at ? formatDaysUntil(row.projected_at) : "not trending up"}
              </Text>
            </View>
            <Text style={text.small}>{devices[row.device_id]?.name ?? row.device_id}</Text>
            <Text style={text.tiny}>
              now {row.current_value.toFixed(1)}% · {row.slope_per_day >= 0 ? "+" : ""}
              {row.slope_per_day.toFixed(2)}%/day · checked {formatRelative(row.computed_at)}
            </Text>
            <ForecastChart
              points={matchingForecast?.points ?? []}
              color={METRIC_COLOR[row.metric] ?? colorForIndex(0)}
              domain={[0, 100]}
              ceiling={100}
              valueFormatter={(v) => `${v.toFixed(0)}%`}
            />
          </Card>
        )
      })}

      {fitted.length > 0 && (
        <>
          <Text style={text.heading}>24h forecasts</Text>
          {fitted.map((f) => {
            const note = CONFIDENCE_NOTE[f.confidence]
            const last = f.points[f.points.length - 1]
            // Points carry an offset from computed_at, not an absolute time.
            const at = new Date(
              new Date(f.computed_at).getTime() + last.offset_seconds * 1000,
            ).toISOString()
            return (
              <Card key={`${f.device_id}-${f.metric}`}>
                <Text style={text.heading}>
                  {METRIC_LABEL[f.metric] ?? f.metric}
                  {f.entity ? ` · ${f.entity}` : ""}
                </Text>
                <Text style={text.small}>{devices[f.device_id]?.name ?? f.device_id}</Text>
                <Text style={text.small}>
                  {formatDaysUntil(at)}:{" "}
                  <Text style={styles.value}>{last.predicted.toFixed(1)}%</Text>{" "}
                  <Text style={text.tiny}>
                    ({last.lower.toFixed(1)}–{last.upper.toFixed(1)})
                  </Text>
                </Text>
                {note && <Text style={text.tiny}>{note}</Text>}
                <ForecastChart
                  points={f.points}
                  color={METRIC_COLOR[f.metric] ?? colorForIndex(0)}
                  domain={[0, 100]}
                  ceiling={100}
                  valueFormatter={(v) => `${v.toFixed(0)}%`}
                />
              </Card>
            )
          })}
        </>
      )}
    </Screen>
  )
}

const styles = StyleSheet.create({
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: spacing.sm,
  },
  days: { ...text.body, fontWeight: "600", fontVariant: ["tabular-nums"] },
  value: { color: colors.foreground, fontVariant: ["tabular-nums"] },
})
