import { cn } from "@/lib/utils"
import type { Device } from "@/types/api"

const LABEL: Record<Device["status"], string> = {
  online: "Online",
  offline: "Offline",
  pending: "Awaiting agent",
}

const DOT_CLASS: Record<Device["status"], string> = {
  online: "bg-emerald-500",
  offline: "bg-muted-foreground",
  pending: "bg-amber-500",
}

export function DeviceStatusBadge({ status }: { status: Device["status"] }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
      <span className={cn("size-2 rounded-full", DOT_CLASS[status])} aria-hidden />
      {LABEL[status]}
    </span>
  )
}
