import { cn } from "@/lib/utils"
import type { EventStatus } from "@/types/alerts"

const LABEL: Record<EventStatus, string> = {
  firing: "Firing",
  resolved: "Resolved",
}

const DOT_CLASS: Record<EventStatus, string> = {
  firing: "bg-destructive",
  resolved: "bg-emerald-500",
}

export function AlertStatusBadge({ status }: { status: EventStatus }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
      <span className={cn("size-2 rounded-full", DOT_CLASS[status])} aria-hidden />
      {LABEL[status]}
    </span>
  )
}
