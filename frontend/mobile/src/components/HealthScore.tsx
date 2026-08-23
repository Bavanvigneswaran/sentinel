/**
 * The health score badge and its breakdown. Ported from
 * web/src/components/HealthScore.tsx.
 *
 * A null score is a first-class state, not an error and not a zero. It means
 * the device is offline or reported nothing measurable, and it renders as "—"
 * with the server's own reason beside it — see app/analysis/health.py.
 *
 * The breakdown exists because a bare number is not actionable. Every
 * component shows its measured value, and the ones the platform could not
 * measure are listed as unavailable rather than quietly dropped, so a score of
 * 88 on macOS is visibly a score computed without iowait.
 */

import { StyleSheet, Text, View } from "react-native"

import { colors, radius, spacing, text } from "@/theme"
import type { Health, HealthBand } from "@/types/fleet"

const BAND_TONE: Record<HealthBand, string> = {
  healthy: colors.healthy,
  degraded: colors.degraded,
  critical: colors.critical,
  unknown: colors.unknown,
}

const BAND_LABEL: Record<HealthBand, string> = {
  healthy: "Healthy",
  degraded: "Degraded",
  critical: "Critical",
  unknown: "Unknown",
}

export function HealthScore({ health, size = "md" }: { health: Health; size?: "sm" | "md" }) {
  const tone = BAND_TONE[health.band]
  return (
    <View style={styles.scoreRow}>
      <Text style={[styles.score, size === "sm" && styles.scoreSm, { color: tone }]}>
        {health.score === null ? "—" : health.score}
      </Text>
      <Text style={[styles.band, { color: tone }]}>{BAND_LABEL[health.band]}</Text>
    </View>
  )
}

export function HealthBreakdown({ health }: { health: Health }) {
  if (health.score === null) {
    return (
      <Text style={text.small}>
        {health.reason ?? "Nothing measurable has been reported for this device."}
      </Text>
    )
  }

  const scored = health.components.filter((c) => c.score !== null)

  return (
    <View style={{ gap: spacing.sm }}>
      {scored.map((component) => (
        <View key={component.key} style={styles.componentRow}>
          <Text style={[text.small, styles.componentLabel]} numberOfLines={1}>
            {component.label}
          </Text>
          <View style={styles.track}>
            <View
              style={[
                styles.fill,
                {
                  width: `${Math.max(2, component.score ?? 0)}%`,
                  backgroundColor: BAND_TONE[bandOf(component.score ?? 0)],
                },
              ]}
            />
          </View>
          <Text style={[text.small, styles.componentValue]}>
            {component.value === null
              ? "—"
              : `${component.value.toFixed(component.value < 10 ? 1 : 0)}${component.unit}`}
          </Text>
        </View>
      ))}

      {health.unavailable.length > 0 && (
        <Text style={text.tiny}>
          Not measurable on this platform: {health.unavailable.join(", ")}. Excluded from the
          score rather than counted as healthy.
        </Text>
      )}
    </View>
  )
}

/** Mirrors band_for() in app/analysis/health.py — the same two thresholds. */
function bandOf(score: number): HealthBand {
  if (score >= 80) return "healthy"
  if (score >= 50) return "degraded"
  return "critical"
}

const styles = StyleSheet.create({
  scoreRow: { flexDirection: "row", alignItems: "baseline", gap: spacing.sm },
  score: { fontSize: 28, fontWeight: "700", fontVariant: ["tabular-nums"] },
  scoreSm: { fontSize: 20 },
  band: { fontSize: 11, fontWeight: "600" },
  componentRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  componentLabel: { width: 86 },
  track: {
    flex: 1,
    height: 6,
    borderRadius: radius.full,
    backgroundColor: colors.border,
    overflow: "hidden",
  },
  fill: { height: "100%", borderRadius: radius.full },
  componentValue: { width: 56, textAlign: "right", fontVariant: ["tabular-nums"] },
})
