/**
 * One machine on the fleet screen: its health, its headline numbers, and an
 * hour of CPU and memory. Ported from web/src/components/DeviceSummaryCard.tsx.
 *
 * `latest` is null whenever nothing landed inside the server's freshness
 * window, and the card then shows no numbers at all. That is the honest
 * render: a figure from twenty minutes ago displayed next to a live status dot
 * is a claim about right now that the data does not support.
 */

import { StyleSheet, Text, View } from "react-native"

import { DeviceStatusBadge } from "@/components/Badges"
import { HealthScore } from "@/components/HealthScore"
import { MetricValue } from "@/components/MetricValue"
import { Sparkline } from "@/components/Sparkline"
import { Button, Card } from "@/components/ui"
import { colorForIndex } from "@/lib/chartColors"
import { formatBytes, formatBytesPerSecond } from "@/lib/formatters"
import { formatDuration, formatRelative } from "@/lib/timeRanges"
import { spacing, text } from "@/theme"
import type { DeviceSummary } from "@/types/fleet"

export function DeviceSummaryCard({
  summary,
  onPress,
  onWatchLive,
}: {
  summary: DeviceSummary
  onPress: () => void
  /** The one thing the separate Devices list had that this card did not, kept
   * when the two tabs merged: one tap from the list straight into the 1s live
   * stream, without going through the device screen first. */
  onWatchLive?: () => void
}) {
  const { device, health, latest, disks, sparkline } = summary
  const worstDisk = disks[0] ?? null

  return (
    <Card onPress={onPress}>
      <View style={styles.headRow}>
        <View style={styles.headText}>
          <Text style={text.heading} numberOfLines={1}>
            {device.name}
          </Text>
          <Text style={text.small} numberOfLines={1}>
            {device.hostname ?? "—"}
            {device.os ? ` · ${device.os}` : ""}
            {device.arch ? ` · ${device.arch}` : ""}
          </Text>
        </View>
        <View style={styles.headRight}>
          <HealthScore health={health} size="sm" />
          <DeviceStatusBadge status={device.status} />
        </View>
      </View>

      <View style={styles.stats}>
        <Stat label="CPU">
          <MetricValue value={latest?.cpu_percent} format={(v) => v.toFixed(0)} unit="%" />
        </Stat>
        <Stat label="Memory">
          <MetricValue value={latest?.mem_percent} format={(v) => v.toFixed(0)} unit="%" />
        </Stat>
        <Stat label={worstDisk ? `Disk ${worstDisk.mount}` : "Disk"}>
          <MetricValue value={worstDisk?.percent} format={(v) => v.toFixed(0)} unit="%" />
        </Stat>
        <Stat label="Network in">
          <MetricValue value={latest?.net_rx_bytes_per_s} format={formatBytesPerSecond} />
        </Stat>
      </View>

      <View style={styles.trends}>
        <Trend label="CPU" values={sparkline.cpu_percent} color={colorForIndex(0)} />
        <Trend label="Memory" values={sparkline.mem_percent} color={colorForIndex(1)} />
      </View>

      <View style={styles.footer}>
        <Text style={text.tiny}>last seen {formatRelative(device.last_seen_at)}</Text>
        {latest?.uptime_seconds != null && (
          <Text style={text.tiny}>up {formatDuration(latest.uptime_seconds)}</Text>
        )}
        {latest?.load1 != null && <Text style={text.tiny}>load {latest.load1.toFixed(2)}</Text>}
        {latest?.mem_total_bytes != null && (
          <Text style={text.tiny}>{formatBytes(latest.mem_total_bytes)} RAM</Text>
        )}
      </View>

      {onWatchLive && (
        <Button size="sm" variant="outline" title="Watch live" onPress={onWatchLive} />
      )}
    </Card>
  )
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <View style={styles.stat}>
      <Text style={text.tiny} numberOfLines={1}>
        {label}
      </Text>
      {children}
    </View>
  )
}

function Trend({
  label,
  values,
  color,
}: {
  label: string
  values: (number | null)[]
  color: string
}) {
  return (
    <View style={styles.trend}>
      <Text style={text.tiny}>{label}</Text>
      {/* Fixed 0–100 domain: both series are percentages, and autoscaling them
          would make a machine idling between 3% and 5% look identical to one
          swinging between 20% and 90%. */}
      <Sparkline values={values} domain={[0, 100]} color={color} height={32} label={label} />
    </View>
  )
}

const styles = StyleSheet.create({
  headRow: { flexDirection: "row", justifyContent: "space-between", gap: spacing.md },
  headText: { flex: 1, gap: 2 },
  headRight: { alignItems: "flex-end", gap: spacing.xs },
  stats: { flexDirection: "row", flexWrap: "wrap", rowGap: spacing.sm },
  stat: { width: "50%", gap: 2, paddingRight: spacing.sm },
  trends: { flexDirection: "row", gap: spacing.md },
  trend: { flex: 1, gap: 2 },
  footer: { flexDirection: "row", flexWrap: "wrap", gap: spacing.md },
})
