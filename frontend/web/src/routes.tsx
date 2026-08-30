import { Navigate, createBrowserRouter } from "react-router"

import { ProtectedRoute } from "@/components/ProtectedRoute"
import { PublicOnlyRoute } from "@/components/PublicOnlyRoute"
import { AddDevicePage } from "@/pages/AddDevicePage"
import { AlertRulesPage } from "@/pages/AlertRulesPage"
import { AlertsPage } from "@/pages/AlertsPage"
import { AnomaliesPage } from "@/pages/AnomaliesPage"
import { DashboardPage } from "@/pages/DashboardPage"
import { DeviceHistoryPage } from "@/pages/DeviceHistoryPage"
import { ForecastsPage } from "@/pages/ForecastsPage"
import { ForgotPasswordPage } from "@/pages/ForgotPasswordPage"
import { IncidentDetailPage } from "@/pages/IncidentDetailPage"
import { IncidentsPage } from "@/pages/IncidentsPage"
import { LiveMonitoringPage } from "@/pages/LiveMonitoringPage"
import { LoginPage } from "@/pages/LoginPage"
import { NotFoundPage } from "@/pages/NotFoundPage"
import { ReportsPage } from "@/pages/ReportsPage"
import { ResetPasswordPage } from "@/pages/ResetPasswordPage"
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
      { path: "/devices/new", element: <AddDevicePage /> },
      // Downloading the agent and minting an enrollment code were two pages, and
      // neither was usable without the other — /download's own instructions told
      // the user to go and mint a code, and the mint panel linked back here for a
      // binary. They are one page now; the old path redirects rather than 404s.
      { path: "/download", element: <Navigate to="/devices/new" replace /> },
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
  // Outside PublicOnlyRoute on purpose. Both are reached from an email, which
  // is as likely to be opened on a machine that is already signed in as not,
  // and bouncing that click to the dashboard would look like the link was
  // broken. A reset ends the signed-in session anyway.
  { path: "/forgot-password", element: <ForgotPasswordPage /> },
  { path: "/reset-password", element: <ResetPasswordPage /> },
  { path: "*", element: <NotFoundPage /> },
])
