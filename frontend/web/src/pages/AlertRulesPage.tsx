import { useEffect, useState } from "react"
import { Link } from "react-router"

import { AppLayout } from "@/components/AppLayout"
import { RuleForm } from "@/components/RuleForm"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { apiFetch } from "@/lib/api"
import type { AlertRule } from "@/types/alerts"
import type { Device } from "@/types/api"
import type { NotificationSettings } from "@/types/notifications"

export function AlertRulesPage() {
  const [rules, setRules] = useState<AlertRule[] | null>(null)
  const [devices, setDevices] = useState<Device[]>([])
  const [sensitivity, setSensitivity] = useState<NotificationSettings["anomaly_sensitivity"] | null>(
    null,
  )
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)

  const load = () => {
    apiFetch<AlertRule[]>("/alerts/rules")
      .then((data) => {
        setRules(data)
        setError(null)
      })
      .catch(() => setError("Could not load your alert rules."))
  }

  useEffect(() => {
    load()
    apiFetch<Device[]>("/devices")
      .then(setDevices)
      .catch(() => {
        // Non-fatal: rule creation still works, just without a device picker.
      })
    // Display only — editing sensitivity stays on the Settings page. Shown
    // next to each anomaly rule so its firing behavior isn't a mystery.
    apiFetch<NotificationSettings>("/notifications/settings")
      .then((s) => setSensitivity(s.anomaly_sensitivity))
      .catch(() => {
        // Non-fatal: anomaly rules just won't show a sensitivity caption.
      })
  }, [])

  const handleDelete = async (id: string) => {
    await apiFetch(`/alerts/rules/${id}`, { method: "DELETE" })
    load()
  }

  const deviceName = (deviceId: string | null) =>
    deviceId === null ? "All devices" : (devices.find((d) => d.id === deviceId)?.name ?? deviceId)

  // Two groups rather than one list. Every account is seeded with a default
  // set (app/alerts/defaults.py) so it detects things before anybody
  // configures it — but a user who opens this page and finds eight rules they
  // did not write, mixed in with the ones they did, has no way to tell which
  // is which or why the unfamiliar ones exist. Separating them is what makes
  // "we set this up for you" readable as help rather than as clutter.
  const builtin = rules?.filter((r) => r.source === "builtin") ?? []
  const mine = rules?.filter((r) => r.source !== "builtin") ?? []

  const renderRule = (rule: AlertRule) =>
    editingId === rule.id ? (
      <Card key={rule.id}>
        <CardHeader>
          <CardTitle>Edit rule</CardTitle>
        </CardHeader>
        <CardContent>
          <RuleForm
            devices={devices}
            rule={rule}
            onSaved={() => {
              setEditingId(null)
              load()
            }}
            onCancel={() => setEditingId(null)}
          />
        </CardContent>
      </Card>
    ) : (
      <Card key={rule.id}>
        <CardContent className="flex items-center justify-between gap-4 pt-6">
          <div className="flex flex-col gap-1">
            <span className="font-medium">
              {rule.name}
              {!rule.enabled && (
                <span className="ml-2 text-xs font-normal text-muted-foreground">
                  (disabled)
                </span>
              )}
            </span>
            <span className="text-sm text-muted-foreground">
              {rule.rule_type === "anomaly" ? (
                <>
                  {deviceName(rule.device_id)} · {rule.metric} anomaly detection
                  {sensitivity && <> · sensitivity: {sensitivity}</>}
                </>
              ) : rule.rule_type === "multivariate" ? (
                <>
                  {/* `novelty_score` is a schema detail, not something to show
                      a person: every other rule type has a metric name that
                      means something on its own, and this one does not. The
                      threshold is a percentile, so it gets a /100 rather than
                      being left as a bare number. */}
                  {deviceName(rule.device_id)} · unusual combination of readings{" "}
                  {rule.comparison} {rule.threshold}/100 for {rule.for_duration_seconds}s
                </>
              ) : (
                <>
                  {deviceName(rule.device_id)} · {rule.metric}{" "}
                  {rule.rule_type === "forecast" ? "predicted " : ""}
                  {rule.comparison} {rule.threshold} for {rule.for_duration_seconds}s
                </>
              )}
            </span>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={() => setEditingId(rule.id)}>
              Edit
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="text-destructive"
              onClick={() => void handleDelete(rule.id)}
            >
              Delete
            </Button>
          </div>
        </CardContent>
      </Card>
    )

  return (
    <AppLayout active="alerts">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Alert rules</h1>
          <p className="text-sm text-muted-foreground">
            Static thresholds and adaptive anomaly rules that drive the alerts triage page.
          </p>
        </div>
        <div className="flex gap-2">
          <Button asChild variant="outline" size="sm">
            <Link to="/alerts">Back to alerts</Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link to="/anomalies">Anomalies</Link>
          </Button>
          {!creating && (
            <Button size="sm" onClick={() => setCreating(true)}>
              New rule
            </Button>
          )}
        </div>
      </div>

      {error && (
        <Card>
          <CardContent className="pt-6 text-sm text-destructive">{error}</CardContent>
        </Card>
      )}

      {creating && (
        <Card>
          <CardHeader>
            <CardTitle>New rule</CardTitle>
          </CardHeader>
          <CardContent>
            <RuleForm
              devices={devices}
              onSaved={() => {
                setCreating(false)
                load()
              }}
              onCancel={() => setCreating(false)}
            />
          </CardContent>
        </Card>
      )}

      {rules !== null && mine.length === 0 && !creating && (
        <Card>
          <CardHeader>
            <CardTitle>You haven't written any rules</CardTitle>
            <CardDescription>
              {builtin.length > 0
                ? "The automatic rules below are already watching every machine. Add your own for anything specific to your setup."
                : "Create one to start getting alerted on real metrics."}
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      {mine.length > 0 && (
        <div className="flex flex-col gap-3">
          <h2 className="text-sm font-medium text-muted-foreground">Your rules</h2>
          {mine.map(renderRule)}
        </div>
      )}

      {builtin.length > 0 && (
        <div className="flex flex-col gap-3">
          <div>
            <h2 className="text-sm font-medium text-muted-foreground">Automatic</h2>
            <p className="text-xs text-muted-foreground">
              Set up with your account so it detects problems without being configured. They
              apply to every machine you enrol, including ones added later. Edit or disable
              any of them — they are ordinary rules, not something locked.
            </p>
          </div>
          {builtin.map(renderRule)}
        </div>
      )}

    </AppLayout>
  )
}
