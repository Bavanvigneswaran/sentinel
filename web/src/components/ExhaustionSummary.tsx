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
          <span>
            {METRIC_LABEL[e.metric]}
            {e.entity ? ` (${e.entity})` : ""}
            <span className="text-muted-foreground"> · {e.current_value.toFixed(0)}%</span>
          </span>
          <span className={e.projected_at ? "font-medium" : "text-muted-foreground"}>
            {e.projected_at
              ? `full ${formatDaysUntil(e.projected_at)}`
              : "not trending toward capacity"}
          </span>
        </div>
      ))}
    </div>
  )
}
