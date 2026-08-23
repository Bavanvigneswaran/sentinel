import { Navigate, Outlet } from "react-router"

import { FullPageLoader } from "@/components/FullPageLoader"
import { useAuth } from "@/stores/auth"

/** Keeps an already-authenticated user off /login and /signup. */
export function PublicOnlyRoute() {
  const status = useAuth((s) => s.status)

  if (status === "idle" || status === "bootstrapping") return <FullPageLoader />
  if (status === "authenticated") return <Navigate to="/" replace />

  return <Outlet />
}
