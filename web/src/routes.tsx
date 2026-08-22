import { createBrowserRouter } from "react-router"

import { ProtectedRoute } from "@/components/ProtectedRoute"
import { PublicOnlyRoute } from "@/components/PublicOnlyRoute"
import { AlertRulesPage } from "@/pages/AlertRulesPage"
import { AlertsPage } from "@/pages/AlertsPage"
import { DashboardPage } from "@/pages/DashboardPage"
import { DeviceHistoryPage } from "@/pages/DeviceHistoryPage"
import { DevicesPage } from "@/pages/DevicesPage"
import { LiveMonitoringPage } from "@/pages/LiveMonitoringPage"
import { LoginPage } from "@/pages/LoginPage"
import { NotFoundPage } from "@/pages/NotFoundPage"
import { SettingsPage } from "@/pages/SettingsPage"
import { SignupPage } from "@/pages/SignupPage"

export const router = createBrowserRouter([
  {
    element: <PublicOnlyRoute />,
    children: [
      { path: "/login", element: <LoginPage /> },
      { path: "/signup", element: <SignupPage /> },
    ],
  },
  {
    element: <ProtectedRoute />,
    children: [
      { path: "/", element: <DashboardPage /> },
      { path: "/devices", element: <DevicesPage /> },
      { path: "/devices/:deviceId/history", element: <DeviceHistoryPage /> },
      { path: "/devices/:deviceId/live", element: <LiveMonitoringPage /> },
      { path: "/alerts", element: <AlertsPage /> },
      { path: "/alerts/rules", element: <AlertRulesPage /> },
      { path: "/settings", element: <SettingsPage /> },
    ],
  },
  { path: "*", element: <NotFoundPage /> },
])
