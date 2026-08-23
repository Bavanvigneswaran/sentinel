import { cn } from "@/lib/utils"
import type { Severity } from "@/types/alerts"

const LABEL: Record<Severity, string> = {
  watch: "Watch",
  warning: "Warning",
  critical: "Critical",
}

const DOT_CLASS: Record<Severity, string> = {
  watch: "bg-muted-foreground",
  warning: "bg-amber-500",
  critical: "bg-destructive",
}

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
      <span className={cn("size-2 rounded-full", DOT_CLASS[severity])} aria-hidden />
      {LABEL[severity]}
    </span>
  )
}
