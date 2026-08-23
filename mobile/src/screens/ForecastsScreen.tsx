/**
 * Forecasts — soonest-to-exhaust first. Ported from
 * web/src/pages/ForecastsPage.tsx.
 *
 * The exhaustion list is the half worth carrying on a phone: "this disk fills
 * in four days" is actionable away from a desk in a way a 24h prediction-
 * interval chart is not. The chart overlay stays on the web console's history
 * page, where there is room to read it.
 */

import { useEffect, useState } from "react"
import { StyleSheet, Text, View } from "react-native"

import { EmptyState, Screen } from "@/components/Screen"
import { Card, ErrorNote } from "@/components/ui"
import { usePolledResource } from "@/hooks/usePolledResource"
import { apiFetch } from "@/lib/api"
import { formatDaysUntil } from "@/lib/formatters"
import { formatRelative } from "@/lib/timeRanges"
import { colors, spacing, text } from "@/theme"
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

export function ForecastsScreen() {
  const [devices, setDevices] = useState<Record<string, Device>>({})

  const exhaustion = usePolledResource<ExhaustionForecast[]>(
    "/forecasts/exhaustion",
    POLL_INTERVAL_MS,
    "Could not load projections.",
  )
  const forecasts = usePolledResource<MetricForecast[]>(
    "/forecasts",
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

  return (
    <Screen
      title="Forecasts"
      refreshing={exhaustion.refreshing}
      onRefresh={() => {
        exhaustion.refresh()
        forecasts.refresh()
      }}
    >
      <Text style={text.small}>
        Where each machine is heading, from its own measured trend. Nothing here is a
        threshold anybody set.
      </Text>

      {exhaustion.error && <ErrorNote message={exhaustion.error} />}

      <Text style={text.heading}>Time to capacity</Text>

      {exhaustion.data && rows.length === 0 && (
        <EmptyState
          title="Nothing projected yet"
          body="A projection needs a little real history first. It appears within minutes of a device starting to report."
        />
      )}

      {rows.map((row) => (
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
        </Card>
      ))}

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
