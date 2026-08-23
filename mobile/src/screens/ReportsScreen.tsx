/**
 * Reports — measured availability and reliability per device. Ported from
 * web/src/pages/ReportsPage.tsx.
 *
 * Schedule CRUD stays in the web console — a phone is where you check whether
 * last week was bad, not where you configure a Monday-morning email. Export
 * does not: GET /reports/export.{pdf,csv} already existed, and "download" on a
 * phone means write the file then hand it to the share sheet, since there is
 * no downloads bar for it to land in. See lib/reportDownload.ts.
 *
 * Uptime here is *measured* — the real seconds the agent was reporting as a
 * fraction of the period (Phase 9's compute_uptime), never inferred from a
 * device's present-tense status. A device that never reported reads 0%, not a
 * gap dressed up as either state.
 */

import { useState } from "react"
import { StyleSheet, Text, View } from "react-native"

import { EmptyState, Screen } from "@/components/Screen"
import { Button, Card, CardTitle, ErrorNote, Segmented } from "@/components/ui"
import { usePolledResource } from "@/hooks/usePolledResource"
import { withDeviceScope } from "@/lib/deviceScope"
import { formatDurationSeconds } from "@/lib/formatters"
import { downloadReport, type ReportFormat } from "@/lib/reportDownload"
import { spacing, text } from "@/theme"
import type { RootStackScreenProps } from "@/navigation/types"
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

export function ReportsScreen({ route }: RootStackScreenProps<"Reports">) {
  const deviceId = route.params?.deviceId
  const deviceName = route.params?.deviceName
  const [period, setPeriod] = useState("7")

  const { data, error, refreshing, refresh } = usePolledResource<AnalyticsReport>(
    withDeviceScope(`/reports/analytics?period_days=${period}`, deviceId),
    POLL_INTERVAL_MS,
    "Could not load reports.",
  )

  const devices = data?.devices ?? []

  const [exporting, setExporting] = useState<ReportFormat | null>(null)
  const [exportNote, setExportNote] = useState<string | null>(null)
  const [exportError, setExportError] = useState<string | null>(null)

  const exportAs = async (format: ReportFormat) => {
    setExporting(format)
    setExportNote(null)
    setExportError(null)
    try {
      const result = await downloadReport({
        format,
        periodDays: period,
        deviceId,
        deviceName,
      })
      // If the share sheet is unavailable the file still exists, so say where
      // it is rather than reporting a success the user cannot act on.
      setExportNote(
        result.shared
          ? `Saved ${result.filename}.`
          : `Saved ${result.filename} to ${result.file.uri} — this device has no share sheet.`,
      )
    } catch {
      setExportError(
        `Could not export the ${format.toUpperCase()}. The server renders it on demand, so a slow or unreachable backend fails here rather than producing a partial file.`,
      )
    } finally {
      setExporting(null)
    }
  }

  return (
    <Screen title="Reports" refreshing={refreshing} onRefresh={refresh}>
      {deviceName && (
        <Text style={text.tiny}>Showing {deviceName} only. Open this from More for every device.</Text>
      )}
      <Text style={text.small}>
        How each machine actually behaved over the period — uptime measured from real
        reporting, not inferred from its status now.
      </Text>

      <Segmented options={PERIODS} value={period} onChange={setPeriod} />

      {error && <ErrorNote message={error} />}

      {data && devices.length === 0 && (
        <EmptyState title="Nothing to report" body="No device reported during this period." />
      )}

      {devices.length > 0 && (
        <Card>
          <CardTitle>Export</CardTitle>
          <Text style={text.small}>
            Rendered fresh by the server from the same numbers shown here — nothing is stored
            and replayed, so a download and an emailed report cannot disagree.
          </Text>
          <View style={styles.exportRow}>
            <Button
              size="sm"
              variant="outline"
              title="PDF"
              busy={exporting === "pdf"}
              disabled={exporting !== null}
              onPress={() => void exportAs("pdf")}
            />
            <Button
              size="sm"
              variant="outline"
              title="CSV"
              busy={exporting === "csv"}
              disabled={exporting !== null}
              onPress={() => void exportAs("csv")}
            />
          </View>
          {exportNote && <Text style={text.tiny}>{exportNote}</Text>}
          {exportError && <ErrorNote message={exportError} />}
        </Card>
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
        Scheduled email reports are configured in the web console.
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
  exportRow: { flexDirection: "row", gap: spacing.sm },
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
