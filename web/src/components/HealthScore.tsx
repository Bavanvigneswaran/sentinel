/**
 * The health score badge and its breakdown.
 *
 * A null score is a first-class state, not an error and not a zero. It means
 * the device is offline or reported nothing measurable, and it renders as "—"
 * with the server's own reason beside it — see app/analysis/health.py.
 *
 * The breakdown exists because a bare number is not actionable. Every component
 * shows its measured value, and the ones the platform could not measure are
 * listed as unavailable rather than quietly dropped, so a score of 88 on macOS
 * is visibly a score computed without iowait.
 */

import { cn } from "@/lib/utils"
import type { Health, HealthBand } from "@/types/fleet"

const BAND_TEXT: Record<HealthBand, string> = {
  healthy: "text-emerald-500",
  degraded: "text-amber-500",
  critical: "text-red-500",
  unknown: "text-muted-foreground",
}

const BAND_BAR: Record<HealthBand, string> = {
  healthy: "bg-emerald-500",
  degraded: "bg-amber-500",
  critical: "bg-red-500",
  unknown: "bg-muted-foreground/40",
}

const BAND_LABEL: Record<HealthBand, string> = {
  healthy: "Healthy",
  degraded: "Degraded",
  critical: "Critical",
  unknown: "Unknown",
}

export function HealthScore({ health, size = "md" }: { health: Health; size?: "sm" | "md" }) {
  const known = health.score !== null
  return (
    <div className="flex items-baseline gap-2">
      <span
        className={cn(
          "font-semibold tabular-nums",
          size === "md" ? "text-3xl" : "text-xl",
          BAND_TEXT[health.band],
        )}
      >
        {known ? health.score : "—"}
      </span>
      <span className={cn("text-xs font-medium", BAND_TEXT[health.band])}>
        {BAND_LABEL[health.band]}
      </span>
    </div>
  )
}

export function HealthBreakdown({ health }: { health: Health }) {
  const scored = health.components.filter((c) => c.score !== null)

  if (health.score === null) {
    return (
      <p className="text-xs text-muted-foreground">
        {health.reason ?? "Nothing measurable has been reported for this device."}
      </p>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      {scored.map((component) => (
        <div key={component.key} className="flex items-center gap-3 text-xs">
          <span className="w-24 shrink-0 text-muted-foreground">{component.label}</span>
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
            <div
              className={cn("h-full rounded-full", BAND_BAR[bandOf(component.score ?? 0)])}
              style={{ width: `${Math.max(2, component.score ?? 0)}%` }}
            />
          </div>
          <span className="w-14 shrink-0 text-right tabular-nums">
            {component.value === null
              ? "—"
              : `${component.value.toFixed(component.value < 10 ? 1 : 0)}${component.unit}`}
          </span>
        </div>
      ))}

      {health.unavailable.length > 0 && (
        <p className="pt-1 text-xs text-muted-foreground">
          Not measurable on this platform: {health.unavailable.join(", ")}. Excluded from the
          score rather than counted as healthy.
        </p>
      )}
    </div>
  )
}

/** Mirrors band_for() in app/analysis/health.py — the same two thresholds. */
function bandOf(score: number): HealthBand {
  if (score >= 80) return "healthy"
  if (score >= 50) return "degraded"
  return "critical"
}
