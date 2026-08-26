/**
 * Live Monitoring. Ported from web/src/pages/LiveMonitoringPage.tsx.
 *
 * Opening this screen is what upshifts the agent on the far end from 10s
 * aggregates to 1s raw (Phase 3's live registry); leaving it, or backgrounding
 * the app, downshifts it again — see the AppState handling in
 * hooks/useDeviceStream.ts. Nothing on this screen is synthesised while the
 * device is offline: the charts simply stop advancing and say so.
 */

import { useEffect, useMemo, useState } from "react"
import { StyleSheet, Text, View } from "react-native"

import { DeviceStatusBadge } from "@/components/Badges"
import { LiveChart } from "@/components/charts/LiveChart"
import { MetricValue } from "@/components/MetricValue"
import { Screen } from "@/components/Screen"
import { Card, CardTitle, ErrorNote, Segmented } from "@/components/ui"
import { useDeviceStream } from "@/hooks/useDeviceStream"
import { apiFetch } from "@/lib/api"
import { colorForIndex } from "@/lib/chartColors"
import { formatBytes, formatBytesPerSecond, formatMs } from "@/lib/formatters"
import { subBufferRef } from "@/lib/streamBuffers"
import type { RootStackScreenProps } from "@/navigation/types"
import { colors, radius, spacing, text } from "@/theme"
import type { Device } from "@/types/api"
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

  // Which panels exist depends on the platform, the same decision
  // HistoryScreen already makes (docs/ANDROID_METRICS.md). Android denies an
  // app /proc/stat, /proc/diskstats and any process list but its own, so a CPU
  // chart, a disk-IO chart and a top-processes table have nothing to fill them
  // — ever, not merely right now. Three panels permanently reading "No data
  // yet." are how a working phone comes to look like a broken agent.
  //
  // `null` until the fetch settles, and the panels wait for it: drawing a CPU
  // chart for one frame and then taking it away is the same wrong impression,
  // just briefer. A failed fetch is a settled answer too — fall back to desktop
  // rather than withholding the charts forever.
  const [platform, setPlatform] = useState<"desktop" | "android" | null>(null)

  useEffect(() => {
    let cancelled = false
    apiFetch<Device>(`/devices/${deviceId}`)
      .then((device) => {
        if (!cancelled) setPlatform(device.platform)
      })
      .catch(() => {
        if (!cancelled) setPlatform("desktop")
      })
    return () => {
      cancelled = true
    }
  }, [deviceId])

  const isAndroid = platform === "android"
  // "Appear once known to be a desktop", never "disappear once known to be a
  // phone" — the panels below are the ones a phone can never fill, and drawing
  // one for a frame before removing it is the same wrong impression, briefer.
  // Everything not gated renders immediately, so the screen is never empty
  // waiting on this fetch.
  const desktopOnly = platform !== null && !isAndroid

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

      {desktopOnly && (
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
      )}

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

      {desktopOnly && (
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
      )}

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

      {desktopOnly && (
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
      )}

      {isAndroid && (
        // Shorter than a desktop's page, and that is the honest outcome rather
        // than a rendering bug. Saying so is what stops it reading as one.
        <View style={styles.note}>
          <Text style={text.tiny}>
            No CPU, disk-I/O or process panels: Android denies an app /proc/stat,
            /proc/diskstats and any process list but its own, so this device measures none of
            them. They are excluded from its health score rather than counted as healthy.
          </Text>
        </View>
      )}
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
  note: { paddingBottom: spacing.md },
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
