/** Status pills: device connection state and alert event state. Mirrors
 * web/src/components/DeviceStatusBadge.tsx and AlertStatusBadge.tsx. */

import { StyleSheet, Text, View } from "react-native"

import { colors, radius, spacing } from "@/theme"
import type { EventStatus, Severity } from "@/types/alerts"

type DeviceStatus = "pending" | "online" | "offline"

const DEVICE_TONE: Record<DeviceStatus, string> = {
  online: colors.healthy,
  offline: colors.mutedDeep,
  pending: colors.degraded,
}

const DEVICE_LABEL: Record<DeviceStatus, string> = {
  online: "Online",
  offline: "Offline",
  pending: "Awaiting agent",
}

export function DeviceStatusBadge({ status }: { status: DeviceStatus }) {
  return <Pill tone={DEVICE_TONE[status]} label={DEVICE_LABEL[status]} dot />
}

const EVENT_TONE: Record<EventStatus, string> = {
  firing: colors.critical,
  resolved: colors.healthy,
}

export function AlertStatusBadge({ status }: { status: EventStatus }) {
  return <Pill tone={EVENT_TONE[status]} label={status === "firing" ? "Firing" : "Resolved"} />
}

const SEVERITY_TONE: Record<Severity, string> = {
  watch: colors.muted,
  warning: colors.degraded,
  critical: colors.critical,
}

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <Pill tone={SEVERITY_TONE[severity]} label={severity} />
}

function Pill({ tone, label, dot = false }: { tone: string; label: string; dot?: boolean }) {
  return (
    <View style={[styles.pill, { borderColor: tone }]}>
      {dot && <View style={[styles.dot, { backgroundColor: tone }]} />}
      <Text style={[styles.text, { color: tone }]}>{label}</Text>
    </View>
  )
}

const styles = StyleSheet.create({
  pill: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.xs,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: radius.full,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    alignSelf: "flex-start",
  },
  dot: { width: 6, height: 6, borderRadius: 3 },
  text: { fontSize: 11, fontWeight: "600", textTransform: "capitalize" },
})
