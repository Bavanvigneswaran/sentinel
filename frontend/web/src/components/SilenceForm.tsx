import { useState } from "react"
import type { FormEvent } from "react"

import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { apiFetch, ApiError } from "@/lib/api"

const PRESETS = [
  { label: "1 hour", minutes: 60 },
  { label: "4 hours", minutes: 4 * 60 },
  { label: "24 hours", minutes: 24 * 60 },
] as const

interface SilenceFormProps {
  /** Silences this one alert's device + rule pair — the triage page's
   * "Silence" action always scopes to the specific alert it's attached to,
   * never to "every device" or "every rule". */
  deviceId: string
  ruleId: string | null
  onSaved: () => void
  onCancel: () => void
}

export function SilenceForm({ deviceId, ruleId, onSaved, onCancel }: SilenceFormProps) {
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState<number | null>(null)

  const silenceFor = async (minutes: number) => {
    setError(null)
    setSubmitting(minutes)
    try {
      const now = new Date()
      await apiFetch("/alerts/silences", {
        method: "POST",
        body: {
          device_id: deviceId,
          rule_id: ruleId,
          starts_at: now.toISOString(),
          ends_at: new Date(now.getTime() + minutes * 60_000).toISOString(),
        },
      })
      onSaved()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create the silence.")
      setSubmitting(null)
    }
  }

  const handleSubmit = (event: FormEvent) => event.preventDefault()

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-3">
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <p className="text-sm text-muted-foreground">
        Silencing stops notifications for this alert — it keeps showing as firing here until it
        actually clears.
      </p>
      <div className="flex flex-wrap gap-2">
        {PRESETS.map((preset) => (
          <Button
            key={preset.minutes}
            type="button"
            variant="secondary"
            size="sm"
            disabled={submitting !== null}
            onClick={() => void silenceFor(preset.minutes)}
          >
            {submitting === preset.minutes ? "Silencing…" : `Silence for ${preset.label}`}
          </Button>
        ))}
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  )
}
