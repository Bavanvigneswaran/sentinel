import { useState } from "react"
import type { FormEvent } from "react"

import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { apiFetch, ApiError } from "@/lib/api"
import { cn } from "@/lib/utils"
import type { ReportCadence, ReportFormat, ReportSchedule } from "@/types/reports"
import type { Device } from "@/types/api"

const PERIODS = [7, 30, 90] as const

const WEEKDAYS = [
  { value: 0, label: "Monday" },
  { value: 1, label: "Tuesday" },
  { value: 2, label: "Wednesday" },
  { value: 3, label: "Thursday" },
  { value: 4, label: "Friday" },
  { value: 5, label: "Saturday" },
  { value: 6, label: "Sunday" },
]

function selectClassName(className?: string): string {
  return cn(
    "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
    "disabled:cursor-not-allowed disabled:opacity-50",
    className,
  )
}

interface ReportScheduleFormProps {
  devices: Device[]
  /** Present when editing an existing schedule; absent when creating one. */
  schedule?: ReportSchedule
  onSaved: () => void
  onCancel: () => void
}

export function ReportScheduleForm({ devices, schedule, onSaved, onCancel }: ReportScheduleFormProps) {
  const [name, setName] = useState(schedule?.name ?? "")
  const [deviceId, setDeviceId] = useState(schedule?.device_id ?? "")
  const [periodDays, setPeriodDays] = useState(schedule?.period_days ?? 30)
  const [cadence, setCadence] = useState<ReportCadence>(schedule?.cadence ?? "weekly")
  const [dayOfWeek, setDayOfWeek] = useState(schedule?.day_of_week ?? 0)
  const [dayOfMonth, setDayOfMonth] = useState(schedule?.day_of_month ?? 1)
  const [format, setFormat] = useState<ReportFormat>(schedule?.format ?? "pdf")
  const [recipients, setRecipients] = useState(schedule?.recipients.join(", ") ?? "")
  const [enabled, setEnabled] = useState(schedule?.enabled ?? true)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const body = {
        name,
        device_id: deviceId === "" ? null : deviceId,
        period_days: periodDays,
        cadence,
        day_of_week: cadence === "weekly" ? dayOfWeek : null,
        day_of_month: cadence === "monthly" ? dayOfMonth : null,
        format,
        recipients: recipients
          .split(",")
          .map((r) => r.trim())
          .filter((r) => r.length > 0),
        enabled,
      }
      if (schedule) {
        await apiFetch(`/reports/schedules/${schedule.id}`, { method: "PATCH", body })
      } else {
        await apiFetch("/reports/schedules", { method: "POST", body })
      }
      onSaved()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save the schedule.")
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="flex flex-col gap-2">
        <Label htmlFor="schedule-name">Name</Label>
        <Input
          id="schedule-name"
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Weekly fleet summary"
        />
      </div>

      <div className="flex flex-col gap-2">
        <Label htmlFor="schedule-device">Covers</Label>
        <select
          id="schedule-device"
          className={selectClassName()}
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

      <div className="grid grid-cols-2 gap-4">
        <div className="flex flex-col gap-2">
          <Label htmlFor="schedule-period">Trailing window</Label>
          <select
            id="schedule-period"
            className={selectClassName()}
            value={periodDays}
            onChange={(e) => setPeriodDays(Number(e.target.value))}
          >
            {PERIODS.map((p) => (
              <option key={p} value={p}>
                {p} days
              </option>
            ))}
          </select>
        </div>

        <div className="flex flex-col gap-2">
          <Label htmlFor="schedule-format">Format</Label>
          <select
            id="schedule-format"
            className={selectClassName()}
            value={format}
            onChange={(e) => setFormat(e.target.value as ReportFormat)}
          >
            <option value="pdf">PDF</option>
            <option value="csv">CSV</option>
          </select>
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <Label>Repeats</Label>
        <div className="flex gap-2">
          <Button
            type="button"
            size="sm"
            variant={cadence === "weekly" ? "default" : "outline"}
            onClick={() => setCadence("weekly")}
          >
            Weekly
          </Button>
          <Button
            type="button"
            size="sm"
            variant={cadence === "monthly" ? "default" : "outline"}
            onClick={() => setCadence("monthly")}
          >
            Monthly
          </Button>
        </div>
      </div>

      {cadence === "weekly" ? (
        <div className="flex flex-col gap-2">
          <Label htmlFor="schedule-weekday">On</Label>
          <select
            id="schedule-weekday"
            className={selectClassName("max-w-xs")}
            value={dayOfWeek}
            onChange={(e) => setDayOfWeek(Number(e.target.value))}
          >
            {WEEKDAYS.map((d) => (
              <option key={d.value} value={d.value}>
                {d.label}
              </option>
            ))}
          </select>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <Label htmlFor="schedule-monthday">On day of month</Label>
          <Input
            id="schedule-monthday"
            type="number"
            min={1}
            max={28}
            required
            className="max-w-xs"
            value={dayOfMonth}
            onChange={(e) => setDayOfMonth(Number(e.target.value))}
          />
          <p className="text-xs text-muted-foreground">
            Capped at 28 so it never silently skips February.
          </p>
        </div>
      )}

      <div className="flex flex-col gap-2">
        <Label htmlFor="schedule-recipients">Recipients (comma-separated)</Label>
        <Input
          id="schedule-recipients"
          value={recipients}
          onChange={(e) => setRecipients(e.target.value)}
          placeholder="Defaults to your account email"
        />
      </div>

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
          className="size-4 rounded border-input"
        />
        Enabled
      </label>

      <div className="flex gap-2">
        <Button type="submit" disabled={submitting}>
          {submitting ? "Saving…" : schedule ? "Save changes" : "Create schedule"}
        </Button>
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  )
}
