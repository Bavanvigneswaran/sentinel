/**
 * Live Monitoring. Ported from web/src/pages/LiveMonitoringPage.tsx.
 *
 * Opening this screen is what upshifts the agent on the far end from 10s
 * aggregates to 1s raw (Phase 3's live registry); leaving it, or backgrounding
 * the app, downshifts it again — see the AppState handling in
 * hooks/useDeviceStream.ts. Nothing on this screen is synthesised while the
 * device is offline: the charts simply stop advancing and say so.
 */

import { useMemo, useState } from "react"
import { StyleSheet, Text, View } from "react-native"

import { DeviceStatusBadge } from "@/components/Badges"
import { LiveChart } from "@/components/charts/LiveChart"
import { MetricValue } from "@/components/MetricValue"
import { Screen } from "@/components/Screen"
import { Card, CardTitle, ErrorNote, Segmented } from "@/components/ui"
import { useDeviceStream } from "@/hooks/useDeviceStream"
import { colorForIndex } from "@/lib/chartColors"
import { formatBytes, formatBytesPerSecond, formatMs } from "@/lib/formatters"
import { subBufferRef } from "@/lib/streamBuffers"
import type { RootStackScreenProps } from "@/navigation/types"
import { colors, radius, spacing, text } from "@/theme"
import type { ProcessEntry } from "@/types/protocol"

export function LiveScreen({ route }: RootStackScreenProps<"Live">) {
  const { deviceId } = route.params
  // Keyed by deviceId so navigating between two devices' live screens fully
  // remounts this subtree — fresh ring buffers, a fresh socket subscription —
  // instead of every piece of state needing its own "did deviceId change"
  // reset logic.
  return <LiveView key={deviceId} deviceId={deviceId} />
}

function LiveView({ deviceId }: { deviceId: string }) {
  const stream = useDeviceStream(deviceId)
  const systemRef = useMemo(() => subBufferRef(stream.buffers, "system"), [stream.buffers])
  const netRef = useMemo(() => subBufferRef(stream.buffers, "net"), [stream.buffers])
  const diskIoRef = useMemo(() => subBufferRef(stream.buffers, "diskIo"), [stream.buffers])
  const latencyRef = useMemo(() => subBufferRef(stream.buffers, "latency"), [stream.buffers])

  const [rankBy, setRankBy] = useState<"cpu" | "memory">("cpu")
  const status = stream.status ?? (stream.online === null ? "pending" : stream.online ? "online" : "offline")

  return (
    <Screen
      // No title: the stack header already carries the device name. The
      // subtitle is here because "why is this screen making the agent work
      // harder" is worth saying once, on the screen that causes it.
      subtitle="Streaming at 1s while this screen is open."
      right={<DeviceStatusBadge status={status} />}
    >
      {status !== "online" && (
        <ErrorNote
          message={
            "This device is not currently connected. Charts will populate once its agent reconnects — nothing shown here is synthesised while it's offline."
          }
        />
      )}

      <Card>
        <CardTitle>CPU</CardTitle>
        <LiveChart
          buffer={systemRef}
          domain={[0, 100]}
          series={[
            { key: "cpu_percent", label: "total", color: colorForIndex(0) },
            { key: "cpu_user_percent", label: "user", color: colorForIndex(1) },
            { key: "cpu_system_percent", label: "system", color: colorForIndex(2) },
          ]}
          valueFormatter={(v) => `${v.toFixed(0)}%`}
        />
      </Card>

      <Card>
        <CardTitle>Memory</CardTitle>
        <LiveChart
          buffer={systemRef}
          domain={[0, 100]}
          series={[
            { key: "mem_percent", label: "used", color: colorForIndex(0) },
            { key: "swap_percent", label: "swap", color: colorForIndex(1) },
          ]}
          valueFormatter={(v) => `${v.toFixed(0)}%`}
        />
      </Card>

      <Card>
        <CardTitle>Network</CardTitle>
        <LiveChart
          buffer={netRef}
          deriveSeries={(keys) =>
            keys.map((key, index) => ({ key, label: key, color: colorForIndex(index) }))
          }
          valueFormatter={formatBytesPerSecond}
        />
      </Card>

      <Card>
        <CardTitle>Disk I/O</CardTitle>
        <LiveChart
          buffer={diskIoRef}
          deriveSeries={(keys) =>
            keys.map((key, index) => ({ key, label: key, color: colorForIndex(index) }))
          }
          valueFormatter={formatBytesPerSecond}
        />
      </Card>

      <Card>
        <CardTitle>Latency</CardTitle>
        <LiveChart
          buffer={latencyRef}
          deriveSeries={(keys) =>
            keys.map((key, index) => ({ key, label: key, color: colorForIndex(index) }))
          }
          valueFormatter={formatMs}
        />
      </Card>

      <Card>
        <CardTitle>Disk usage</CardTitle>
        {stream.diskUsage.length === 0 ? (
          <Text style={text.small}>{stream.primed ? "No data yet." : "Loading…"}</Text>
        ) : (
          stream.diskUsage.map((disk) => (
            <View key={disk.mount} style={{ gap: spacing.xs }}>
              <View style={styles.diskHead}>
                <Text style={[text.small, styles.mount]} numberOfLines={1}>
                  {disk.mount}
                </Text>
                <MetricValue value={disk.percent} format={(v) => `${v.toFixed(0)}%`} />
              </View>
              <View style={styles.track}>
                <View style={[styles.fill, { width: `${Math.min(disk.percent ?? 0, 100)}%` }]} />
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

      <Card>
        <View style={styles.processHead}>
          <CardTitle>Top processes</CardTitle>
          <Segmented
            value={rankBy}
            onChange={setRankBy}
            options={[
              { value: "cpu", label: "CPU" },
              { value: "memory", label: "Memory" },
            ]}
          />
        </View>
        <ProcessTable
          processes={stream.processes.filter((p) => p.rank_by === rankBy)}
          rankBy={rankBy}
          primed={stream.primed}
        />
      </Card>
    </Screen>
  )
}

function ProcessTable({
  processes,
  rankBy,
  primed,
}: {
  processes: ProcessEntry[]
  rankBy: "cpu" | "memory"
  primed: boolean
}) {
  if (processes.length === 0) {
    return <Text style={text.small}>{primed ? "No data yet." : "Loading…"}</Text>
  }
  return (
    <View>
      {processes
        .slice()
        .sort((a, b) => a.rank - b.rank)
        .map((process) => (
          <View key={`${process.rank_by}-${process.pid}`} style={styles.processRow}>
            <Text style={[text.tiny, styles.pid]}>{process.pid}</Text>
            <Text style={[text.body, styles.processName]} numberOfLines={1}>
              {process.name}
            </Text>
            {rankBy === "cpu" ? (
              <MetricValue
                value={process.cpu_percent}
                format={(v) => `${v.toFixed(1)}%`}
                style={styles.processValue}
              />
            ) : (
              <MetricValue
                value={process.memory_bytes}
                format={formatBytes}
                style={styles.processValue}
              />
            )}
          </View>
        ))}
    </View>
  )
}

const styles = StyleSheet.create({
  diskHead: { flexDirection: "row", justifyContent: "space-between", gap: spacing.sm },
  mount: { flex: 1 },
  track: {
    height: 6,
    borderRadius: radius.full,
    backgroundColor: colors.border,
    overflow: "hidden",
  },
  fill: { height: "100%", backgroundColor: colors.primary, borderRadius: radius.full },
  processHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: spacing.sm,
  },
  processRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    paddingVertical: 6,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
  },
  pid: { width: 52, fontVariant: ["tabular-nums"] },
  processName: { flex: 1 },
  processValue: { width: 84, textAlign: "right", fontVariant: ["tabular-nums"], fontSize: 13 },
})
