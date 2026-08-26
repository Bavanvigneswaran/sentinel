import type { ReactNode } from "react"
import { Link, useNavigate } from "react-router"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { useAuth } from "@/stores/auth"

interface AppLayoutProps {
  children: ReactNode
  /** Highlights the active nav item. Undefined renders neither as active. */
  active?:
    | "devices"
    | "add-device"
    | "alerts"
    | "anomalies"
    | "forecasts"
    | "incidents"
    | "reports"
    | "settings"
}

const NAV_ITEMS = [
  { key: "devices", to: "/", label: "Devices" },
  { key: "add-device", to: "/devices/new", label: "Add a device" },
  { key: "alerts", to: "/alerts", label: "Alerts" },
  { key: "anomalies", to: "/anomalies", label: "Anomalies" },
  { key: "forecasts", to: "/forecasts", label: "Forecasts" },
  { key: "incidents", to: "/incidents", label: "Incidents" },
  { key: "reports", to: "/reports", label: "Reports" },
  { key: "settings", to: "/settings", label: "Settings" },
] as const

export function AppLayout({ children, active }: AppLayoutProps) {
  const user = useAuth((s) => s.user)
  const logout = useAuth((s) => s.logout)
  const navigate = useNavigate()

  const handleLogout = async () => {
    await logout()
    navigate("/login", { replace: true })
  }

  return (
    <div className="min-h-svh">
      {/* Eight nav items plus an email address and a button outgrow a laptop
          header well before they outgrow a phone. The rules below are what
          keep that from becoming page-level horizontal scroll:
          `min-w-0` lets the left group actually shrink (a flex item's default
          min-width is auto, i.e. its content, which is why the old row pushed
          the page sideways instead), `overflow-x-auto` moves the excess into
          the nav's own scroll region, and `gap-x-6` is a real gap rather than
          the incidental space `justify-between` leaves — which is none once
          the two groups overflow, so "Settings" ran straight into the email. */}
      <header className="flex items-center justify-between gap-x-6 border-b px-6 py-4">
        <div className="flex min-w-0 flex-1 items-center gap-6">
          <span className="shrink-0 font-semibold tracking-tight">Sentinel</span>
          <nav className="flex min-w-0 items-center gap-4 overflow-x-auto">
            {NAV_ITEMS.map((item) => (
              <Link
                key={item.key}
                to={item.to}
                className={cn(
                  "shrink-0 whitespace-nowrap text-sm text-muted-foreground transition-colors hover:text-foreground",
                  active === item.key && "font-medium text-foreground",
                )}
              >
                {item.label}
              </Link>
            ))}
          </nav>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          {/* Truncated rather than dropped wherever there is room: which account
              you are signed into is worth keeping on screen, and an unbounded
              address would push the sign-out button off the edge on its own.
              Below `sm` there is no such room, and signing out is the one thing
              you still need to be able to reach. */}
          <span className="hidden max-w-56 truncate text-sm text-muted-foreground sm:inline">
            {user?.email}
          </span>
          <Button variant="outline" size="sm" onClick={handleLogout}>
            Sign out
          </Button>
        </div>
      </header>

      <main className="mx-auto flex max-w-5xl flex-col gap-6 p-6">{children}</main>
    </div>
  )
}
