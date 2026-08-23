import { useEffect, useState } from "react"

import { AppLayout } from "@/components/AppLayout"
import { ReportScheduleForm } from "@/components/ReportScheduleForm"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { apiDownload, apiFetch, ApiError } from "@/lib/api"
import { formatDurationSeconds } from "@/lib/formatters"
import { cn } from "@/lib/utils"
import type { Device } from "@/types/api"
import type { AnalyticsReport, ReportMetric, ReportSchedule } from "@/types/reports"

const PERIODS = [7, 30, 90] as const

const METRIC_LABELS: Record<ReportMetric, string> = {
  cpu_percent: "CPU",
  mem_percent: "Memory",
  swap_percent: "Swap",
  disk_percent: "Disk usage",
  packet_loss_percent: "Packet loss",
  cpu_iowait_percent: "IO wait",
}

const CADENCE_LABEL: Record<ReportSchedule["cadence"], (s: ReportSchedule) => string> = {
  weekly: (s) =>
    `Weekly on ${["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][s.day_of_week ?? 0]}`,
  monthly: (s) => `Monthly on day ${s.day_of_month}`,
}

function selectClassName(className?: string): string {
  return cn(
    "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
    className,
  )
}

function fmt(value: number | null, digits = 1): string {
  return value === null ? "—" : value.toFixed(digits)
}

function fmtPct(value: number | null): string {
  return value === null ? "—" : `${value.toFixed(1)}%`
}

function fmtDelta(value: number | null): string {
  if (value === null) return "—"
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`
}

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement("a")
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

export function ReportsPage() {
  const [devices, setDevices] = useState<Device[]>([])
  const [deviceId, setDeviceId] = useState("")
  const [periodDays, setPeriodDays] = useState<number>(30)
  const [report, setReport] = useState<AnalyticsReport | null>(null)
  const [reportError, setReportError] = useState<string | null>(null)
  const [downloading, setDownloading] = useState<"pdf" | "csv" | null>(null)

  const [schedules, setSchedules] = useState<ReportSchedule[] | null>(null)
  const [schedulesError, setSchedulesError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [sendingId, setSendingId] = useState<string | null>(null)

  useEffect(() => {
    apiFetch<Device[]>("/devices")
      .then(setDevices)
      .catch(() => {
        // Non-fatal: the fleet-wide report still works without a picker.
      })
  }, [])

  const loadReport = () => {
    const params: Record<string, string> = { period_days: String(periodDays) }
    if (deviceId) params.device_id = deviceId
    apiFetch<AnalyticsReport>(`/reports/analytics?${new URLSearchParams(params).toString()}`)
      .then((data) => {
        setReport(data)
        setReportError(null)
      })
      .catch(() => setReportError("Could not load analytics for this period."))
  }

  useEffect(loadReport, [deviceId, periodDays])

  const loadSchedules = () => {
    apiFetch<ReportSchedule[]>("/reports/schedules")
      .then((data) => {
        setSchedules(data)
        setSchedulesError(null)
      })
      .catch(() => setSchedulesError("Could not load scheduled reports."))
  }

  useEffect(loadSchedules, [])

  const handleDownload = async (format: "pdf" | "csv") => {
    setDownloading(format)
    try {
      const params: Record<string, string> = { period_days: String(periodDays) }
      if (deviceId) params.device_id = deviceId
      const { blob, filename } = await apiDownload(`/reports/export.${format}`, params)
      triggerDownload(blob, filename)
    } catch {
      setReportError(`Could not generate the ${format.toUpperCase()}.`)
    } finally {
      setDownloading(null)
    }
  }

  const handleDeleteSchedule = async (id: string) => {
    await apiFetch(`/reports/schedules/${id}`, { method: "DELETE" })
    loadSchedules()
  }

  const handleSendNow = async (id: string) => {
    setSendingId(id)
    try {
      await apiFetch(`/reports/schedules/${id}/send-now`, { method: "POST" })
      loadSchedules()
    } catch (err) {
      setSchedulesError(err instanceof ApiError ? err.message : "Could not send that report.")
    } finally {
      setSendingId(null)
    }
  }

  const deviceName = (id: string | null) =>
    id === null ? "Whole fleet" : (devices.find((d) => d.id === id)?.name ?? id)

  return (
    <AppLayout active="reports">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Reports</h1>
        <p className="text-sm text-muted-foreground">
          Historical trends, availability and reliability, plus PDF/CSV export and scheduled email
          delivery.
        </p>
      </div>

      <Card>
        <CardContent className="flex flex-wrap items-end gap-4 pt-6">
          <div className="flex flex-col gap-2">
            <label className="text-xs text-muted-foreground" htmlFor="report-device">
              Device
            </label>
            <select
              id="report-device"
              className={selectClassName("min-w-48")}
              value={deviceId}
              onChange={(e) => setDeviceId(e.target.value)}
            >
              <option value="">Whole fleet</option>
              {devices.map((device) => (
                <option key={device.id} value={device.id}>
                  {device.name}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-2">
            <label className="text-xs text-muted-foreground" htmlFor="report-period">
              Period
            </label>
            <select
              id="report-period"
              className={selectClassName()}
              value={periodDays}
              onChange={(e) => setPeriodDays(Number(e.target.value))}
            >
              {PERIODS.map((p) => (
                <option key={p} value={p}>
                  Last {p} days
                </option>
              ))}
            </select>
          </div>
          <div className="ml-auto flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={downloading !== null}
              onClick={() => void handleDownload("csv")}
            >
              {downloading === "csv" ? "Preparing…" : "Download CSV"}
            </Button>
            <Button
              size="sm"
              disabled={downloading !== null}
              onClick={() => void handleDownload("pdf")}
            >
              {downloading === "pdf" ? "Preparing…" : "Download PDF"}
            </Button>
          </div>
        </CardContent>
      </Card>

      {reportError && (
        <Card>
          <CardContent className="pt-6 text-sm text-destructive">{reportError}</CardContent>
        </Card>
      )}

      {report !== null && report.devices.length === 0 && !reportError && (
        <Card>
          <CardHeader>
            <CardTitle>No devices in scope</CardTitle>
            <CardDescription>Enroll a device to start seeing analytics here.</CardDescription>
          </CardHeader>
        </Card>
      )}

      {report?.devices.map((da) => (
        <Card key={da.device_id}>
          <CardHeader>
            <CardTitle className="text-base">{da.device_name}</CardTitle>
            <CardDescription>
              {new Date(report.period_start).toLocaleDateString()} –{" "}
              {new Date(report.period_end).toLocaleDateString()}
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <div className="rounded-md border p-3">
                <div className="text-xs text-muted-foreground">Uptime</div>
                <div className="text-lg font-semibold">{fmtPct(da.availability.uptime_percent)}</div>
              </div>
              <div className="rounded-md border p-3">
                <div className="text-xs text-muted-foreground">Incidents</div>
                <div className="text-lg font-semibold">{da.reliability.incident_count}</div>
              </div>
              <div className="rounded-md border p-3">
                <div className="text-xs text-muted-foreground">Mean time to resolve</div>
                <div className="text-lg font-semibold">
                  {formatDurationSeconds(da.reliability.mean_time_to_resolve_seconds)}
                </div>
              </div>
              <div className="rounded-md border p-3">
                <div className="text-xs text-muted-foreground">Alerts fired</div>
                <div className="text-lg font-semibold">{da.reliability.alert_fired_count}</div>
              </div>
            </div>

            {da.trends.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-muted-foreground">
                      <th className="py-1 pr-4">Metric</th>
                      <th className="px-2 py-1 text-right">Avg</th>
                      <th className="px-2 py-1 text-right">Min</th>
                      <th className="px-2 py-1 text-right">Max</th>
                      <th className="px-2 py-1 text-right">Prev. avg</th>
                      <th className="py-1 pl-2 text-right">Change</th>
                    </tr>
                  </thead>
                  <tbody>
                    {da.trends.map((t) => (
                      <tr key={`${t.metric}:${t.entity ?? ""}`} className="border-t">
                        <td className="py-1.5 pr-4">
                          {METRIC_LABELS[t.metric]}
                          {t.entity && <span className="text-muted-foreground"> ({t.entity})</span>}
                        </td>
                        <td className="px-2 py-1.5 text-right">{fmt(t.current.avg)}</td>
                        <td className="px-2 py-1.5 text-right">{fmt(t.current.min)}</td>
                        <td className="px-2 py-1.5 text-right">{fmt(t.current.max)}</td>
                        <td className="px-2 py-1.5 text-right text-muted-foreground">
                          {fmt(t.previous.avg)}
                        </td>
                        <td
                          className={cn(
                            "py-1.5 pl-2 text-right font-medium",
                            t.delta_percent !== null &&
                              (t.delta_percent >= 0 ? "text-destructive" : "text-emerald-600"),
                          )}
                        >
                          {fmtDelta(t.delta_percent)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No metrics available for this period.</p>
            )}
          </CardContent>
        </Card>
      ))}

      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium text-muted-foreground">Scheduled reports</h2>
        {!creating && (
          <Button size="sm" onClick={() => setCreating(true)}>
            New schedule
          </Button>
        )}
      </div>

      {schedulesError && (
        <Card>
          <CardContent className="pt-6 text-sm text-destructive">{schedulesError}</CardContent>
        </Card>
      )}

      {creating && (
        <Card>
          <CardHeader>
            <CardTitle>New schedule</CardTitle>
          </CardHeader>
          <CardContent>
            <ReportScheduleForm
              devices={devices}
              onSaved={() => {
                setCreating(false)
                loadSchedules()
              }}
              onCancel={() => setCreating(false)}
            />
          </CardContent>
        </Card>
      )}

      {schedules !== null && schedules.length === 0 && !creating && (
        <Card>
          <CardHeader>
            <CardTitle>No scheduled reports yet</CardTitle>
            <CardDescription>
              Create one to have a PDF or CSV emailed automatically, weekly or monthly.
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      {schedules !== null && schedules.length > 0 && (
        <div className="flex flex-col gap-3">
          {schedules.map((schedule) =>
            editingId === schedule.id ? (
              <Card key={schedule.id}>
                <CardHeader>
                  <CardTitle>Edit schedule</CardTitle>
                </CardHeader>
                <CardContent>
                  <ReportScheduleForm
                    devices={devices}
                    schedule={schedule}
                    onSaved={() => {
                      setEditingId(null)
                      loadSchedules()
                    }}
                    onCancel={() => setEditingId(null)}
                  />
                </CardContent>
              </Card>
            ) : (
              <Card key={schedule.id}>
                <CardContent className="flex items-center justify-between gap-4 pt-6">
                  <div className="flex flex-col gap-1">
                    <span className="font-medium">
                      {schedule.name}
                      {!schedule.enabled && (
                        <span className="ml-2 text-xs font-normal text-muted-foreground">
                          (disabled)
                        </span>
                      )}
                    </span>
                    <span className="text-sm text-muted-foreground">
                      {deviceName(schedule.device_id)} · {CADENCE_LABEL[schedule.cadence](schedule)} ·{" "}
                      {schedule.format.toUpperCase()} · last {schedule.period_days} days
                      {schedule.last_sent_at &&
                        ` · last sent ${new Date(schedule.last_sent_at).toLocaleDateString()}`}
                    </span>
                  </div>
                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={sendingId === schedule.id}
                      onClick={() => void handleSendNow(schedule.id)}
                    >
                      {sendingId === schedule.id ? "Sending…" : "Send now"}
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => setEditingId(schedule.id)}>
                      Edit
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-destructive"
                      onClick={() => void handleDeleteSchedule(schedule.id)}
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
