import { Navigate, createBrowserRouter } from "react-router"

import { ProtectedRoute } from "@/components/ProtectedRoute"
import { PublicOnlyRoute } from "@/components/PublicOnlyRoute"
import { AlertRulesPage } from "@/pages/AlertRulesPage"
import { AlertsPage } from "@/pages/AlertsPage"
import { AnomaliesPage } from "@/pages/AnomaliesPage"
import { DashboardPage } from "@/pages/DashboardPage"
import { DeviceHistoryPage } from "@/pages/DeviceHistoryPage"
import { DownloadPage } from "@/pages/DownloadPage"
import { ForecastsPage } from "@/pages/ForecastsPage"
import { IncidentDetailPage } from "@/pages/IncidentDetailPage"
import { IncidentsPage } from "@/pages/IncidentsPage"
import { LiveMonitoringPage } from "@/pages/LiveMonitoringPage"
import { LoginPage } from "@/pages/LoginPage"
import { NotFoundPage } from "@/pages/NotFoundPage"
import { ReportsPage } from "@/pages/ReportsPage"
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
      // /devices existed as a separate, thinner list before the two merged into one
      // page at "/" — redirected rather than dropped so old links and bookmarks land
      // somewhere real instead of the 404 page.
      { path: "/devices", element: <Navigate to="/" replace /> },
      { path: "/devices/:deviceId/history", element: <DeviceHistoryPage /> },
      { path: "/devices/:deviceId/live", element: <LiveMonitoringPage /> },
      { path: "/download", element: <DownloadPage /> },
      { path: "/alerts", element: <AlertsPage /> },
      { path: "/alerts/rules", element: <AlertRulesPage /> },
      { path: "/anomalies", element: <AnomaliesPage /> },
      { path: "/forecasts", element: <ForecastsPage /> },
      { path: "/incidents", element: <IncidentsPage /> },
      { path: "/incidents/:incidentId", element: <IncidentDetailPage /> },
      { path: "/reports", element: <ReportsPage /> },
      { path: "/settings", element: <SettingsPage /> },
    ],
  },
  { path: "*", element: <NotFoundPage /> },
])
