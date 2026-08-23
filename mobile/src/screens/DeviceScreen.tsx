/**
 * One machine's health, in detail: the score with its full breakdown, the
 * headline readings, and every mount. The phone counterpart of the web app's
 * per-device summary — it renders `GET /devices/{id}/summary`, which is
 * `fleet_service.build_summaries()` over a fleet of one, so this page and the
 * fleet card can never disagree about what "healthy" means (Phase 4's
 * invariant).
 *
 * Deliberately not a port of DeviceHistoryPage: the time-range history view is
 * outside the Phase 10a viewer's scope, and this screen exists to answer "how
 * is this machine right now" before you tap through to Live.
 *
 * Phase 10b: the readings grid is chosen by `device.platform`, because an
 * Android device honestly reports a different set of metrics than a Linux box
 * (docs/ANDROID_METRICS.md) and a fixed desktop-shaped grid would render as a
 * column of "unavailable" with the phone's real signals — battery, temperature
 * — missing entirely. That reads as a broken agent rather than a different
 * platform. The rows a phone cannot fill are not silently dropped either: the
 * card names them, so the absence is stated rather than hidden.
 */

import type { ReactNode } from "react"
import { StyleSheet, Text, View } from "react-native"

import { DeviceStatusBadge } from "@/components/Badges"
import { HealthBreakdown, HealthScore } from "@/components/HealthScore"
import { MetricValue } from "@/components/MetricValue"
import { Screen } from "@/components/Screen"
import { Button, Card, CardTitle, ErrorNote } from "@/components/ui"
import { usePolledResource } from "@/hooks/usePolledResource"
import {
  readingKeysFor,
  unmeasurableNoteFor,
  type ReadingKey,
} from "@/lib/deviceReadings"
import { formatBytes, formatBytesPerSecond, formatMs } from "@/lib/formatters"
import { formatDuration, formatRelative } from "@/lib/timeRanges"
import type { RootStackScreenProps } from "@/navigation/types"
import { colors, radius, spacing, text } from "@/theme"
import type { DeviceSummary, LatestReadings } from "@/types/fleet"

const POLL_INTERVAL_MS = 15_000

export function DeviceScreen({ route, navigation }: RootStackScreenProps<"Device">) {
  const { deviceId } = route.params
  const { data, error, loading, refreshing, refresh } = usePolledResource<DeviceSummary>(
    `/devices/${deviceId}/summary`,
    POLL_INTERVAL_MS,
    "Could not load this device.",
  )

  const latest = data?.latest ?? null
  const unmeasurableNote = data ? unmeasurableNoteFor(data.device.platform) : null

  return (
    <Screen refreshing={refreshing} onRefresh={refresh}>
      {error && <ErrorNote message={error} />}
      {loading && !data && <Text style={text.small}>Loading…</Text>}

      {data && (
        <>
          <Card>
            <View style={styles.headRow}>
              <View style={styles.headText}>
                <Text style={text.title} numberOfLines={1}>
                  {data.device.name}
                </Text>
                <Text style={text.small} numberOfLines={2}>
                  {data.device.hostname ?? "—"}
                  {data.device.os ? ` · ${data.device.os}` : ""}
                  {data.device.os_version ? ` ${data.device.os_version}` : ""}
                  {data.device.arch ? ` · ${data.device.arch}` : ""}
                </Text>
                <Text style={text.tiny}>
                  last seen {formatRelative(data.device.last_seen_at)}
                  {data.device.agent_version ? ` · agent ${data.device.agent_version}` : ""}
                </Text>
              </View>
              <View style={styles.headRight}>
                <HealthScore health={data.health} />
                <DeviceStatusBadge status={data.device.status} />
              </View>
            </View>
            <Button
              title="Watch live"
              onPress={() =>
                navigation.navigate("Live", {
                  deviceId,
                  deviceName: data.device.name,
                })
              }
            />
          </Card>

          <Card>
            <CardTitle>Health breakdown</CardTitle>
            <HealthBreakdown health={data.health} />
          </Card>

          <Card>
            <CardTitle>Current readings</CardTitle>
            {latest === null ? (
              // Not an error and not zeroes: the server reports no `latest` at
              // all once the newest reading falls outside FRESH_WINDOW_SECONDS,
              // and showing the last thing we happened to have would present a
              // stale figure as live.
              <Text style={text.small}>
                Nothing has been reported inside the server's freshness window, so there are no
                current numbers to show.
              </Text>
            ) : (
              <>
                <View style={styles.stats}>
                  {readingKeysFor(data.device.platform).map((key) => (
                    <Stat key={key} label={RENDERERS[key].label}>
                      {RENDERERS[key].render(latest)}
                    </Stat>
                  ))}
                </View>
                {/* Stated, not silently omitted. The reader should know these
                    are absent because Android does not expose them, rather
                    than wonder whether the agent is broken. */}
                {unmeasurableNote && (
                  <Text style={[text.tiny, styles.footnote]}>{unmeasurableNote}</Text>
                )}
              </>
            )}
          </Card>

          <Card>
            <CardTitle>Disks</CardTitle>
            {data.disks.length === 0 ? (
              <Text style={text.small}>No mounts reported.</Text>
            ) : (
              data.disks.map((disk) => (
                <View key={disk.mount} style={{ gap: spacing.xs }}>
                  <View style={styles.diskHead}>
                    <Text style={[text.small, styles.mount]} numberOfLines={1}>
                      {disk.mount}
                    </Text>
                    <MetricValue value={disk.percent} format={(v) => `${v.toFixed(0)}%`} />
                  </View>
                  <View style={styles.track}>
                    <View
                      style={[
                        styles.fill,
                        { width: `${Math.min(disk.percent ?? 0, 100)}%` },
                        disk.percent === null && styles.fillUnknown,
                      ]}
                    />
                  </View>
                  <Text style={text.tiny}>
                    {disk.used_bytes !== null && disk.total_bytes !== null
                      ? `${formatBytes(disk.used_bytes)} of ${formatBytes(disk.total_bytes)}`
                      : "unavailable"}
                  </Text>
                </View>
              ))
            )}
          </Card>
        </>
      )}
    </Screen>
  )
}

/**
 * How to draw each reading. Which of these appear is decided by
 * `readingKeysFor()` in @/lib/deviceReadings — a pure module, so the
 * platform decision is tested without a React Native runtime.
 */
const RENDERERS: Record<ReadingKey, { label: string; render: (l: LatestReadings) => ReactNode }> = {
  cpu: {
    label: "CPU",
    render: (l) => <MetricValue value={l.cpu_percent} format={(v) => v.toFixed(1)} unit="%" />,
  },
  memory: {
    label: "Memory",
    render: (l) => <MetricValue value={l.mem_percent} format={(v) => v.toFixed(1)} unit="%" />,
  },
  swap: {
    label: "Swap",
    render: (l) => <MetricValue value={l.swap_percent} format={(v) => v.toFixed(1)} unit="%" />,
  },
  load1: {
    label: "Load (1m)",
    render: (l) => <MetricValue value={l.load1} format={(v) => v.toFixed(2)} />,
  },
  battery: {
    label: "Battery",
    render: (l) => (
      <MetricValue
        value={l.battery_percent}
        format={(v) => `${v.toFixed(0)}%${l.battery_plugged ? " charging" : ""}`}
      />
    ),
  },
  temperature: {
    // Named for what it actually is: Android exposes no CPU package
    // temperature to an app, only the battery thermistor.
    label: "Battery temp",
    render: (l) => (
      <MetricValue value={l.temperature_celsius} format={(v) => v.toFixed(1)} unit="°C" />
    ),
  },
  netIn: {
    label: "Net in",
    render: (l) => <MetricValue value={l.net_rx_bytes_per_s} format={formatBytesPerSecond} />,
  },
  netOut: {
    label: "Net out",
    render: (l) => <MetricValue value={l.net_tx_bytes_per_s} format={formatBytesPerSecond} />,
  },
  diskRead: {
    label: "Disk read",
    render: (l) => <MetricValue value={l.disk_read_bytes_per_s} format={formatBytesPerSecond} />,
  },
  diskWrite: {
    label: "Disk write",
    render: (l) => <MetricValue value={l.disk_write_bytes_per_s} format={formatBytesPerSecond} />,
  },
  rtt: { label: "RTT", render: (l) => <MetricValue value={l.rtt_ms_avg} format={formatMs} /> },
  packetLoss: {
    label: "Packet loss",
    render: (l) => (
      <MetricValue value={l.packet_loss_percent} format={(v) => v.toFixed(1)} unit="%" />
    ),
  },
  processes: {
    label: "Processes",
    render: (l) => <MetricValue value={l.process_count} format={(v) => v.toFixed(0)} />,
  },
  uptime: {
    label: "Uptime",
    render: (l) => <MetricValue value={l.uptime_seconds} format={formatDuration} />,
  },
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

const styles = StyleSheet.create({
  headRow: { flexDirection: "row", justifyContent: "space-between", gap: spacing.md },
  headText: { flex: 1, gap: 2 },
  headRight: { alignItems: "flex-end", gap: spacing.xs },
  stats: { flexDirection: "row", flexWrap: "wrap", rowGap: spacing.md },
  stat: { width: "50%", gap: 2, paddingRight: spacing.sm },
  footnote: { marginTop: spacing.sm },
  diskHead: { flexDirection: "row", justifyContent: "space-between", gap: spacing.sm },
  mount: { flex: 1, fontVariant: ["tabular-nums"] },
  track: {
    height: 6,
    borderRadius: radius.full,
    backgroundColor: colors.border,
    overflow: "hidden",
  },
  fill: { height: "100%", backgroundColor: colors.primary, borderRadius: radius.full },
  // A mount with no percentage draws nothing, rather than an empty bar that
  // reads as 0% used.
  fillUnknown: { width: 0 },
})
