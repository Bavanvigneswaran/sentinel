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

      {rules !== null && rules.length === 0 && !creating && (
        <Card>
          <CardHeader>
            <CardTitle>No rules yet</CardTitle>
            <CardDescription>Create one to start getting alerted on real metrics.</CardDescription>
          </CardHeader>
        </Card>
      )}

      {rules !== null && rules.length > 0 && (
        <div className="flex flex-col gap-3">
          {rules.map((rule) =>
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
            ),
          )}
        </div>
      )}
    </AppLayout>
  )
}
