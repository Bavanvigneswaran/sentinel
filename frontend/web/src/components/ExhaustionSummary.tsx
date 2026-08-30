/**
 * "Time to exhaustion" for memory/disk — app/analysis/forecast.py's robust
 * linear projection of when a metric reaches its ceiling, read from
 * GET /forecasts/exhaustion. A null `projected_at` is a first-class state,
 * not an error: it means the metric isn't trending toward capacity on any
 * horizon worth acting on, same posture as HealthScore's null score.
 */

import { formatDaysUntil } from "@/lib/formatters"
import type { ExhaustionForecast } from "@/types/forecast"

const METRIC_LABEL: Record<ExhaustionForecast["metric"], string> = {
  mem_percent: "Memory",
  disk_percent: "Disk",
}

export function ExhaustionSummary({ estimates }: { estimates: ExhaustionForecast[] }) {
  if (estimates.length === 0) return null

  return (
    <div className="flex flex-col gap-2">
      <span className="text-xs text-muted-foreground">Time to capacity</span>
      {estimates.map((e) => (
        <div key={e.metric} className="flex items-baseline justify-between gap-3 text-sm">
          {/* Only the name truncates, and the reading never does. An entity is
              a real mount path and can be arbitrarily long (APFS gives us
              /System/Volumes/Update/SFR/mnt1); letting it set this panel's
              width crushed the health breakdown beside it. The full path stays
              available on hover rather than being dropped. */}
          <span className="flex min-w-0 items-baseline">
            <span className="truncate" title={e.entity ?? undefined}>
              {METRIC_LABEL[e.metric]}
              {e.entity ? ` (${e.entity})` : ""}
            </span>
            <span className="shrink-0 text-muted-foreground">
              {" "}· {e.current_value.toFixed(0)}%
            </span>
          </span>
          <span
            className={`shrink-0 ${e.projected_at ? "font-medium" : "text-muted-foreground"}`}
          >
            {e.projected_at
              ? `full ${formatDaysUntil(e.projected_at)}`
              : "not trending toward capacity"}
          </span>
        </div>
      ))}
    </div>
  )
}
