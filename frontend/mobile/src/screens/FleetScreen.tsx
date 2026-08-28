/**
 * The Devices tab: every device's health, headline numbers and last hour, in
 * one request. Ported from web/src/pages/DashboardPage.tsx.
 *
 * This was the Fleet tab, beside a separate Devices tab that listed the same
 * machines with less on each row. They merged — see TabParamList in
 * navigation/types.ts for why the plain list had nothing this does not.
 *
 * A poll rather than a socket, for the same reason as the web dashboard: the
 * live pipeline exists to push one agent to 1s sampling while somebody watches
 * one machine, and doing that for every device because a dashboard is open
 * would multiply every agent's push rate by the number of open viewers. The
 * per-device live view is one tap away.
 */

import { useFocusEffect } from "@react-navigation/native"
import { useCallback } from "react"
import { StyleSheet, Text, View } from "react-native"

import { DeviceSummaryCard } from "@/components/DeviceSummaryCard"
import { EmptyState, Screen } from "@/components/Screen"
import { Card, ErrorNote } from "@/components/ui"
import { usePolledResource } from "@/hooks/usePolledResource"
import { formatRelative } from "@/lib/timeRanges"
import type { RootTabScreenProps } from "@/navigation/types"
import { colors, spacing, text } from "@/theme"
import type { FleetOverview, FleetTotals } from "@/types/fleet"

const POLL_INTERVAL_MS = 30_000

/** Matches the server's default; the sparklines cover this window. */
const WINDOW_SECONDS = 3600

export function FleetScreen({ navigation }: RootTabScreenProps<"Devices">) {
  const { data, error, loading, refreshing, refresh, reload } = usePolledResource<FleetOverview>(
    `/fleet/overview?window_seconds=${WINDOW_SECONDS}`,
    POLL_INTERVAL_MS,
    "Could not refresh the fleet overview.",
  )

  // Refetch whenever this screen comes back into focus, on top of the 30s
  // poll. Removing a device happens on its own screen and pops back to here,
  // and a list that still shows the machine you just removed — for up to a
  // full poll interval — reads as the removal having failed. AppState covers
  // backgrounding; it says nothing about moving between screens inside the
  // app, which is the case that actually surprised somebody.
  useFocusEffect(
    useCallback(() => {
      reload()
    }, [reload]),
  )

  return (
    <Screen testID="screen-fleet"
      title="Devices"
      subtitle="Health of every machine you've enrolled, from its own real telemetry."
      right={data ? <Text style={text.tiny}>{formatRelative(data.generated_at)}</Text> : undefined}
      refreshing={refreshing}
      onRefresh={refresh}
    >
      {error && <ErrorNote message={`${error} Showing the last successful reading.`} />}

      {data && <Totals totals={data.totals} />}

      {loading && !data && <Text style={text.small}>Loading…</Text>}

      {data?.devices.length === 0 && (
        <EmptyState
          title="No devices yet"
          body="Add a machine from the web console's Devices page — it mints an enrollment code for the agent to redeem. To monitor this phone instead, open More → Settings → Monitor this phone; it enrols itself and needs no code."
        />
      )}

      {data?.devices.map((summary) => (
        <DeviceSummaryCard
          key={summary.device.id}
          summary={summary}
          onPress={() =>
            navigation.navigate("Device", {
              deviceId: summary.device.id,
              deviceName: summary.device.name,
            })
          }
          onWatchLive={() =>
            navigation.navigate("Live", {
              deviceId: summary.device.id,
              deviceName: summary.device.name,
            })
          }
        />
      ))}
    </Screen>
  )
}

function Totals({ totals }: { totals: FleetTotals }) {
  return (
    <Card>
      <View style={styles.tiles}>
        <Tile label="Devices" value={totals.devices} />
        <Tile label="Online" value={totals.online} tone={colors.healthy} />
        <Tile label="Offline" value={totals.offline} />
        <Tile label="Awaiting" value={totals.pending} tone={colors.degraded} />
        <Tile label="Healthy" value={totals.healthy} tone={colors.healthy} />
        <Tile label="Degraded" value={totals.degraded} tone={colors.degraded} />
        <Tile label="Critical" value={totals.critical} tone={colors.critical} />
        <Tile label="Unknown" value={totals.unknown} />
      </View>
    </Card>
  )
}

function Tile({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <View style={styles.tile}>
      {/* Zero is a real count here, not a missing metric, so it renders as 0 —
          MetricValue's "unavailable" rule is about measurements, not tallies. */}
      <Text style={[styles.tileValue, value > 0 && tone ? { color: tone } : null]}>{value}</Text>
      <Text style={text.tiny} numberOfLines={1}>
        {label}
      </Text>
    </View>
  )
}

const styles = StyleSheet.create({
  tiles: { flexDirection: "row", flexWrap: "wrap", rowGap: spacing.md },
  tile: { width: "25%", gap: 2 },
  tileValue: {
    fontSize: 20,
    fontWeight: "600",
    color: colors.foreground,
    fontVariant: ["tabular-nums"],
  },
})
