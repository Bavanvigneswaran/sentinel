/** Every machine you've enrolled, with its live connection status. Ported from
 * web/src/pages/DevicesPage.tsx. */

import { StyleSheet, Text, View } from "react-native"

import { DeviceStatusBadge } from "@/components/Badges"
import { EmptyState, Screen } from "@/components/Screen"
import { Button, Card, ErrorNote } from "@/components/ui"
import { usePolledResource } from "@/hooks/usePolledResource"
import { formatRelative } from "@/lib/timeRanges"
import type { RootTabScreenProps } from "@/navigation/types"
import { spacing, text } from "@/theme"
import type { Device } from "@/types/api"

const POLL_INTERVAL_MS = 10_000

export function DevicesScreen({ navigation }: RootTabScreenProps<"Devices">) {
  const { data, error, loading, refreshing, refresh } = usePolledResource<Device[]>(
    "/devices",
    POLL_INTERVAL_MS,
    "Could not load your devices.",
  )

  return (
    <Screen
      title="Devices"
      subtitle="Every machine you've enrolled an agent on."
      refreshing={refreshing}
      onRefresh={refresh}
    >
      {error && <ErrorNote message={error} />}
      {loading && !data && <Text style={text.small}>Loading…</Text>}

      {data?.length === 0 && (
        <EmptyState
          title="No devices yet"
          body="Enrolment happens on the machine being watched, from the web app. This viewer shows what those agents report."
        />
      )}

      {data?.map((device) => (
        <Card
          key={device.id}
          onPress={() =>
            navigation.navigate("Device", { deviceId: device.id, deviceName: device.name })
          }
        >
          <View style={styles.row}>
            <View style={styles.main}>
              <Text style={text.heading} numberOfLines={1}>
                {device.name}
              </Text>
              <Text style={text.small} numberOfLines={1}>
                {device.hostname ?? "—"}
                {device.os ? ` · ${device.os}` : ""}
                {device.os_version ? ` ${device.os_version}` : ""}
              </Text>
              <Text style={text.tiny}>last seen {formatRelative(device.last_seen_at)}</Text>
            </View>
            <View style={styles.side}>
              <DeviceStatusBadge status={device.status} />
              <Button
                size="sm"
                variant="outline"
                title="Live"
                onPress={() =>
                  navigation.navigate("Live", { deviceId: device.id, deviceName: device.name })
                }
              />
            </View>
          </View>
        </Card>
      ))}
    </Screen>
  )
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", justifyContent: "space-between", gap: spacing.md },
  main: { flex: 1, gap: 2 },
  side: { alignItems: "flex-end", gap: spacing.sm },
})
