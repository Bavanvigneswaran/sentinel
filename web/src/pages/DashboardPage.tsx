import { useEffect, useState } from "react"
import { useNavigate } from "react-router"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { apiFetch } from "@/lib/api"
import { useAuth } from "@/stores/auth"
import type { User } from "@/types/api"

/**
 * Placeholder dashboard for Phase 1.
 *
 * It exists to prove the whole chain end to end: an authenticated GET that
 * carries the in-memory access token and survives a hard refresh. The real
 * fleet overview arrives in Phase 4, and there is deliberately no fabricated
 * device or metric data here — every number in this product comes from a real
 * agent reading a real machine.
 */
export function DashboardPage() {
  const user = useAuth((s) => s.user)
  const logout = useAuth((s) => s.logout)
  const navigate = useNavigate()

  const [me, setMe] = useState<User | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    apiFetch<User>("/auth/me")
      .then((data) => {
        if (!cancelled) setMe(data)
      })
      .catch(() => {
        if (!cancelled) setError("Could not load your profile.")
      })
    return () => {
      cancelled = true
    }
  }, [])

  const handleLogout = async () => {
    await logout()
    navigate("/login", { replace: true })
  }

  return (
    <div className="min-h-svh">
      <header className="flex items-center justify-between border-b px-6 py-4">
        <span className="font-semibold tracking-tight">Sentinel</span>
        <div className="flex items-center gap-3">
          <span className="text-sm text-muted-foreground">{user?.email}</span>
          <Button variant="outline" size="sm" onClick={handleLogout}>
            Sign out
          </Button>
        </div>
      </header>

      <main className="mx-auto flex max-w-3xl flex-col gap-6 p-6">
        <Card>
          <CardHeader>
            <CardTitle>Signed in</CardTitle>
            <CardDescription>
              Verified against <code className="font-mono text-xs">GET /auth/me</code> using the
              in-memory access token.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {error ? (
              <p className="text-sm text-destructive">{error}</p>
            ) : me ? (
              <dl className="grid grid-cols-[8rem_1fr] gap-y-2 text-sm">
                <dt className="text-muted-foreground">Email</dt>
                <dd className="font-mono text-xs">{me.email}</dd>
                <dt className="text-muted-foreground">Name</dt>
                <dd>{me.display_name ?? "—"}</dd>
                <dt className="text-muted-foreground">User ID</dt>
                <dd className="font-mono text-xs">{me.id}</dd>
                <dt className="text-muted-foreground">Last sign-in</dt>
                <dd className="font-mono text-xs">
                  {me.last_login_at ? new Date(me.last_login_at).toISOString() : "—"}
                </dd>
              </dl>
            ) : (
              <p className="text-sm text-muted-foreground">Loading…</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>No devices yet</CardTitle>
            <CardDescription>
              Device enrollment and the agent arrive in Phase 2. Nothing is shown here until a real
              agent reports real metrics.
            </CardDescription>
          </CardHeader>
        </Card>
      </main>
    </div>
  )
}
