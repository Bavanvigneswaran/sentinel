import { useEffect, useState } from "react"

import { AppLayout } from "@/components/AppLayout"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { apiFetch } from "@/lib/api"
import type { User } from "@/types/api"

/**
 * Placeholder dashboard for Phase 1–3.
 *
 * It exists to prove the whole chain end to end: an authenticated GET that
 * carries the in-memory access token and survives a hard refresh. The real
 * fleet overview (health scores, sparklines, time-range picker) arrives in
 * Phase 4 — Phase 3's device list and live charts live at /devices.
 */
export function DashboardPage() {
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

  return (
    <AppLayout active="dashboard">
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
    </AppLayout>
  )
}
