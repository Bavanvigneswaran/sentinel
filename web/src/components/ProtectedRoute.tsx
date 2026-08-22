import { Navigate, Outlet, useLocation } from "react-router"

import { FullPageLoader } from "@/components/FullPageLoader"
import { useAuth } from "@/stores/auth"

export function ProtectedRoute() {
  const status = useAuth((s) => s.status)
  const location = useLocation()

  // The bootstrapping branch is what makes hard-refresh work. A naive
  // `if (!user) redirect` fires before the refresh resolves and bounces the
  // user to /login on every single page load.
  if (status === "idle" || status === "bootstrapping") return <FullPageLoader />

  if (status !== "authenticated") {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }

  return <Outlet />
}
