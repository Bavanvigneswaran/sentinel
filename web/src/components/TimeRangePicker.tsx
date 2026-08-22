import { cn } from "@/lib/utils"
import { TIME_RANGES, type TimeRange } from "@/lib/timeRanges"

/**
 * Picks a window, never a resolution. Which source answers — raw or a 1m/5m/1h
 * rollup — is the server's decision, because only the server knows what has
 * survived retention. The chart reports what it got back.
 */
export function TimeRangePicker({
  value,
  onChange,
}: {
  value: TimeRange
  onChange: (range: TimeRange) => void
}) {
  return (
    <div className="inline-flex items-center rounded-lg border border-border p-0.5" role="group">
      {TIME_RANGES.map((range) => (
        <button
          key={range.key}
          type="button"
          onClick={() => onChange(range)}
          aria-pressed={range.key === value.key}
          className={cn(
            "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
            range.key === value.key
              ? "bg-accent text-accent-foreground"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {range.label}
        </button>
      ))}
    </div>
  )
}
