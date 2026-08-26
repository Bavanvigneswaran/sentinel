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
      <header className="flex items-center justify-between border-b px-6 py-4">
        <div className="flex items-center gap-6">
          <span className="font-semibold tracking-tight">Sentinel</span>
          <nav className="flex items-center gap-4">
            {NAV_ITEMS.map((item) => (
              <Link
                key={item.key}
                to={item.to}
                className={cn(
                  "text-sm text-muted-foreground transition-colors hover:text-foreground",
                  active === item.key && "font-medium text-foreground",
                )}
              >
                {item.label}
              </Link>
            ))}
          </nav>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm text-muted-foreground">{user?.email}</span>
          <Button variant="outline" size="sm" onClick={handleLogout}>
            Sign out
          </Button>
        </div>
      </header>

      <main className="mx-auto flex max-w-5xl flex-col gap-6 p-6">{children}</main>
    </div>
  )
}
