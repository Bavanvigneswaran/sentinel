import { createBrowserRouter } from "react-router"

import { ProtectedRoute } from "@/components/ProtectedRoute"
import { PublicOnlyRoute } from "@/components/PublicOnlyRoute"
import { DashboardPage } from "@/pages/DashboardPage"
import { DevicesPage } from "@/pages/DevicesPage"
import { LiveMonitoringPage } from "@/pages/LiveMonitoringPage"
import { LoginPage } from "@/pages/LoginPage"
import { NotFoundPage } from "@/pages/NotFoundPage"
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
      { path: "/devices/:deviceId/live", element: <LiveMonitoringPage /> },
    ],
  },
  { path: "*", element: <NotFoundPage /> },
])
