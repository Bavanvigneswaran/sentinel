import { useEffect, useState } from "react"
import { Link } from "react-router"

import { AppLayout } from "@/components/AppLayout"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { apiFetch, ApiError } from "@/lib/api"
import { cn } from "@/lib/utils"
import {
  disableWebPush,
  enableWebPush,
  webPushSupport,
  WebPushPermissionDeniedError,
  WebPushUnavailableError,
} from "@/lib/webPush"
import type { NotificationSettings, Sensitivity } from "@/types/notifications"

const SENSITIVITIES: { value: Sensitivity; label: string }[] = [
  { value: "low", label: "Low — only large, sustained deviations" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High — flag smaller deviations sooner" },
]

function selectClassName(className?: string): string {
  return cn(
    "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
    className,
  )
}

export function SettingsPage() {
  const [settings, setSettings] = useState<NotificationSettings | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pushError, setPushError] = useState<string | null>(null)
  const [pushBusy, setPushBusy] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    apiFetch<NotificationSettings>("/notifications/settings")
      .then(setSettings)
      .catch(() => setError("Could not load your notification settings."))
  }, [])

  const patch = async (update: Partial<NotificationSettings>) => {
    if (!settings) return
    setSaving(true)
    setError(null)
    try {
      const next = await apiFetch<NotificationSettings>("/notifications/settings", {
        method: "PATCH",
        body: update,
      })
      setSettings(next)
    } catch {
      setError("Could not save that change.")
    } finally {
      setSaving(false)
    }
  }

  const handleEnablePush = async () => {
    setPushError(null)
    setPushBusy(true)
    try {
      await enableWebPush()
      await patch({ web_push_enabled: true })
    } catch (err) {
      if (err instanceof WebPushPermissionDeniedError) {
        setPushError("Notification permission was not granted in the browser.")
      } else if (err instanceof WebPushUnavailableError) {
        setPushError(err.message)
      } else if (err instanceof ApiError) {
        setPushError(err.message)
      } else {
        setPushError("Could not enable push notifications.")
      }
    } finally {
      setPushBusy(false)
    }
  }

  const handleDisablePush = async () => {
    setPushError(null)
    setPushBusy(true)
    try {
      await disableWebPush()
      await patch({ web_push_enabled: false })
    } catch {
      setPushError("Could not disable push notifications.")
    } finally {
      setPushBusy(false)
    }
  }

  const support = webPushSupport()

  return (
    <AppLayout active="settings">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground">Notification channels and alert defaults.</p>
      </div>

      {error && (
        <Card>
          <CardContent className="pt-6 text-sm text-destructive">{error}</CardContent>
        </Card>
      )}

      {settings && (
        <>
          <Card>
            <CardHeader>
              <CardTitle>Email</CardTitle>
              <CardDescription>
                Sent when an alert fires or resolves, unless it's silenced.
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  className="size-4 rounded border-input"
                  checked={settings.email_enabled}
                  disabled={saving}
                  onChange={(e) => void patch({ email_enabled: e.target.checked })}
                />
                Email me on firing and resolved alerts
              </label>
              <div className="flex flex-col gap-2">
                <Label htmlFor="email-override">Send to (optional override)</Label>
                <Input
                  id="email-override"
                  type="email"
                  placeholder="Defaults to your account email"
                  defaultValue={settings.email_address ?? ""}
                  disabled={saving}
                  onBlur={(e) =>
                    void patch({ email_address: e.target.value === "" ? null : e.target.value })
                  }
                />
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Web push</CardTitle>
              <CardDescription>Browser notifications, delivered even when this tab is closed.</CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              {pushError && (
                <Alert variant="destructive">
                  <AlertDescription>{pushError}</AlertDescription>
                </Alert>
              )}
              {support === "unsupported" && (
                <p className="text-sm text-muted-foreground">
                  This browser does not support push notifications.
                </p>
              )}
              {support === "denied" && (
                <p className="text-sm text-muted-foreground">
                  Notifications are blocked for this site in your browser settings.
                </p>
              )}
              {support === "ready" && (
                <div>
                  {settings.web_push_enabled ? (
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={pushBusy}
                      onClick={() => void handleDisablePush()}
                    >
                      {pushBusy ? "Working…" : "Disable push notifications"}
                    </Button>
                  ) : (
                    <Button size="sm" disabled={pushBusy} onClick={() => void handleEnablePush()}>
                      {pushBusy ? "Working…" : "Enable push notifications"}
                    </Button>
                  )}
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Anomaly sensitivity</CardTitle>
              <CardDescription>
                How far a metric has to drift from its own recent normal before an anomaly rule
                fires. Applies to every anomaly rule on this account — see{" "}
                <Link to="/alerts/rules" className="underline underline-offset-2">
                  alert rules
                </Link>
                .
              </CardDescription>
            </CardHeader>
            <CardContent>
              <select
                className={selectClassName("max-w-xs")}
                value={settings.anomaly_sensitivity}
                disabled={saving}
                onChange={(e) => void patch({ anomaly_sensitivity: e.target.value as Sensitivity })}
              >
                {SENSITIVITIES.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
            </CardContent>
          </Card>
        </>
      )}
    </AppLayout>
  )
}
