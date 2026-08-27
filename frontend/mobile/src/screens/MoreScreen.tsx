/**
 * The rest of the console, reachable from a phone.
 *
 * A menu rather than five more bottom tabs: a bottom bar stops being usable
 * past about five entries, and these are things you open deliberately rather
 * than flick between. Devices / Alerts stay as tabs because those are the ones
 * you actually flick between when something is wrong.
 *
 * Every entry here is fleet-wide. The same screens are reachable per-device
 * from a device's own screen, which is the more common way to want them.
 */

import { StyleSheet, Text, TouchableOpacity, View } from "react-native"

import { Screen } from "@/components/Screen"
import { Card } from "@/components/ui"
import { colors, spacing, text } from "@/theme"
import type { RootTabScreenProps } from "@/navigation/types"

interface Entry {
  route: "Anomalies" | "Forecasts" | "Incidents" | "Reports" | "AlertRules" | "Settings"
  title: string
  body: string
}

const ENTRIES: Entry[] = [
  {
    route: "Incidents",
    title: "Incidents",
    body: "Alerts that fired together on one machine, with a plain-English summary.",
  },
  {
    route: "Anomalies",
    title: "Anomalies",
    body: "Readings outside what this machine's own baseline calls normal.",
  },
  {
    route: "Forecasts",
    title: "Forecasts",
    body: "Where each machine is heading, and when it runs out of room.",
  },
  {
    route: "Reports",
    title: "Reports",
    body: "Measured uptime and reliability over the last week or month.",
  },
  {
    route: "AlertRules",
    title: "Alert rules",
    body: "The threshold, anomaly, forecast and combination rules that decide what pages you.",
  },
  {
    route: "Settings",
    title: "Settings",
    body: "Account, push notifications, and this phone as a monitored device.",
  },
]

export function MoreScreen({ navigation }: RootTabScreenProps<"More">) {
  return (
    <Screen title="More">
      {ENTRIES.map((entry) => (
        <TouchableOpacity
          key={entry.route}
          accessibilityRole="button"
          onPress={() => navigation.navigate(entry.route)}
        >
          <Card>
            <View style={styles.row}>
              <View style={styles.copy}>
                <Text style={text.heading}>{entry.title}</Text>
                <Text style={text.small}>{entry.body}</Text>
              </View>
              <Text style={styles.chevron}>›</Text>
            </View>
          </Card>
        </TouchableOpacity>
      ))}

      <Text style={text.tiny}>
        Per-device history, anomalies, forecasts, reports and alert rules are also reachable
        from a device's own screen, scoped to that machine. What is still web-only: silence
        windows other than the one-hour mute on the Alerts tab, report schedules, and adding a
        device — an enrollment code's only destination is a terminal on some other machine, and
        this phone enrols itself with one tap and needs no code at all.
      </Text>
    </Screen>
  )
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: spacing.md },
  copy: { flex: 1, gap: 2 },
  chevron: { color: colors.mutedDeep, fontSize: 24 },
})
