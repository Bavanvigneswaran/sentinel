/**
 * Reports — measured availability and reliability per device. Ported from
 * web/src/pages/ReportsPage.tsx.
 *
 * Read-only, and deliberately: PDF/CSV export and schedule CRUD stay in the
 * web console. A phone is where you check whether last week was bad, not where
 * you configure a Monday-morning email.
 *
 * Uptime here is *measured* — the real seconds the agent was reporting as a
 * fraction of the period (Phase 9's compute_uptime), never inferred from a
 * device's present-tense status. A device that never reported reads 0%, not a
 * gap dressed up as either state.
 */

import { useState } from "react"
import { StyleSheet, Text, View } from "react-native"

import { EmptyState, Screen } from "@/components/Screen"
import { Card, ErrorNote, Segmented } from "@/components/ui"
import { usePolledResource } from "@/hooks/usePolledResource"
import { formatDurationSeconds } from "@/lib/formatters"
import { spacing, text } from "@/theme"
import type { AnalyticsReport } from "@/types/reports"

const POLL_INTERVAL_MS = 60_000

const PERIODS = [
  { value: "7", label: "7d" },
  { value: "30", label: "30d" },
  { value: "90", label: "90d" },
]

const METRIC_LABEL: Record<string, string> = {
  cpu_percent: "CPU",
  mem_percent: "Memory",
  swap_percent: "Swap",
  disk_percent: "Disk",
  packet_loss_percent: "Packet loss",
  cpu_iowait_percent: "IO wait",
}

function pct(value: number | null): string {
  return value == null ? "—" : `${value.toFixed(1)}%`
}

export function ReportsScreen() {
  const [period, setPeriod] = useState("7")

  const { data, error, refreshing, refresh } = usePolledResource<AnalyticsReport>(
    `/reports/analytics?period_days=${period}`,
    POLL_INTERVAL_MS,
    "Could not load reports.",
  )

  const devices = data?.devices ?? []

  return (
    <Screen title="Reports" refreshing={refreshing} onRefresh={refresh}>
      <Text style={text.small}>
        How each machine actually behaved over the period — uptime measured from real
        reporting, not inferred from its status now.
      </Text>

      <Segmented options={PERIODS} value={period} onChange={setPeriod} />

      {error && <ErrorNote message={error} />}

      {data && devices.length === 0 && (
        <EmptyState title="Nothing to report" body="No device reported during this period." />
      )}

      {devices.map((device) => (
        <Card key={device.device_id}>
          <Text style={text.heading}>{device.device_name}</Text>

          <View style={styles.stats}>
            <Stat label="Uptime" value={pct(device.availability.uptime_percent)} />
            <Stat label="Incidents" value={String(device.reliability.incident_count)} />
            <Stat label="Alerts" value={String(device.reliability.alert_fired_count)} />
            <Stat
              label="Mean fix"
              value={
                device.reliability.mean_time_to_resolve_seconds == null
                  ? "—"
                  : formatDurationSeconds(device.reliability.mean_time_to_resolve_seconds)
              }
            />
          </View>

          {device.trends.length > 0 && (
            <View style={styles.trends}>
              {device.trends.map((trend) => (
                <View key={`${trend.metric}-${trend.entity ?? ""}`} style={styles.trendRow}>
                  <Text style={styles.trendLabel}>
                    {METRIC_LABEL[trend.metric] ?? trend.metric}
                    {trend.entity ? ` · ${trend.entity}` : ""}
                  </Text>
                  <Text style={styles.trendValue}>{pct(trend.current.avg)}</Text>
                  <Text style={styles.trendDelta}>
                    {/* Null when the previous period is unmeasured — shown as
                        a dash rather than 0%, which would claim "no change"
                        for something never measured. */}
                    {trend.delta_percent == null
                      ? "—"
                      : `${trend.delta_percent >= 0 ? "+" : ""}${trend.delta_percent.toFixed(0)}%`}
                  </Text>
                </View>
              ))}
            </View>
          )}
        </Card>
      ))}

      <Text style={text.tiny}>
        PDF and CSV export, and scheduled email reports, are in the web console.
      </Text>
    </Screen>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.stat}>
      <Text style={styles.statValue}>{value}</Text>
      <Text style={text.tiny}>{label}</Text>
    </View>
  )
}

const styles = StyleSheet.create({
  stats: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: spacing.lg,
    marginTop: spacing.xs,
  },
  stat: { minWidth: 70 },
  statValue: { ...text.heading, fontVariant: ["tabular-nums"] },
  trends: { marginTop: spacing.sm, gap: spacing.xs },
  trendRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  trendLabel: { ...text.small, flex: 1 },
  trendValue: { ...text.small, fontVariant: ["tabular-nums"] },
  trendDelta: { ...text.tiny, minWidth: 44, textAlign: "right" },
})
