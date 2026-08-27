import { useState } from "react"
import type { FormEvent } from "react"
import { Link } from "react-router"

import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { apiFetch, ApiError } from "@/lib/api"
import { cn } from "@/lib/utils"
import type { AlertRule, Comparison, Metric, RuleType } from "@/types/alerts"
import type { Device } from "@/types/api"

const METRICS: { value: Metric; label: string }[] = [
  { value: "cpu_percent", label: "CPU %" },
  { value: "mem_percent", label: "Memory %" },
  { value: "swap_percent", label: "Swap %" },
  { value: "disk_percent", label: "Disk usage % (fullest mount)" },
  { value: "packet_loss_percent", label: "Packet loss % (worst target)" },
  { value: "cpu_iowait_percent", label: "CPU IO wait %" },
]

const COMPARISONS: { value: Comparison; label: string }[] = [
  { value: ">", label: "is above" },
  { value: ">=", label: "is at or above" },
  { value: "<", label: "is below" },
  { value: "<=", label: "is at or below" },
  { value: "==", label: "equals" },
]

/** Styled like ui/input.tsx's Input — there is no shared Select primitive
 * yet, and a native <select> with matching classes is enough for this
 * form's four dropdowns. */
function selectClassName(className?: string): string {
  return cn(
    "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
    "disabled:cursor-not-allowed disabled:opacity-50",
    className,
  )
}

interface RuleFormProps {
  devices: Device[]
  /** Present when editing an existing rule; absent when creating one. */
  rule?: AlertRule
  onSaved: () => void
  onCancel: () => void
}

export function RuleForm({ devices, rule, onSaved, onCancel }: RuleFormProps) {
  const [name, setName] = useState(rule?.name ?? "")
  const [deviceId, setDeviceId] = useState(rule?.device_id ?? "")
  const [ruleType, setRuleType] = useState<RuleType>(rule?.rule_type ?? "threshold")
  // The picker is hidden for a combination rule, whose metric is fixed, so an
  // existing one falls back to the default rather than trying to select a
  // value the list does not contain.
  const [metric, setMetric] = useState<Metric>(
    rule && rule.metric !== "novelty_score" ? rule.metric : "cpu_percent",
  )
  const [comparison, setComparison] = useState<Comparison>(rule?.comparison ?? ">")
  const [threshold, setThreshold] = useState(rule?.threshold != null ? String(rule.threshold) : "90")
  const [forDuration, setForDuration] = useState(
    rule ? String(rule.for_duration_seconds) : "60",
  )
  const [enabled, setEnabled] = useState(rule?.enabled ?? true)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      // A combination rule's metric is fixed by the API on both sides of the
      // constraint, so it is sent explicitly rather than taken from a picker
      // the form does not show for this type.
      const body =
        ruleType === "threshold" || ruleType === "forecast" || ruleType === "multivariate"
          ? {
              name,
              device_id: deviceId === "" ? null : deviceId,
              rule_type: ruleType,
              metric: ruleType === "multivariate" ? "novelty_score" : metric,
              comparison,
              threshold: Number(threshold),
              for_duration_seconds: Number(forDuration),
              enabled,
            }
          : {
              name,
              device_id: deviceId === "" ? null : deviceId,
              rule_type: ruleType,
              metric,
              for_duration_seconds: Number(forDuration),
              enabled,
            }
      if (rule) {
        await apiFetch(`/alerts/rules/${rule.id}`, { method: "PATCH", body })
      } else {
        await apiFetch("/alerts/rules", { method: "POST", body })
      }
      onSaved()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save the rule.")
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
        <Label htmlFor="rule-name">Name</Label>
        <Input
          id="rule-name"
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="High CPU"
        />
      </div>

      <div className="flex flex-col gap-2">
        <Label>Type</Label>
        <div className="flex gap-2">
          <Button
            type="button"
            size="sm"
            variant={ruleType === "threshold" ? "default" : "outline"}
            onClick={() => setRuleType("threshold")}
          >
            Threshold
          </Button>
          <Button
            type="button"
            size="sm"
            variant={ruleType === "anomaly" ? "default" : "outline"}
            onClick={() => setRuleType("anomaly")}
          >
            Anomaly
          </Button>
          <Button
            type="button"
            size="sm"
            variant={ruleType === "forecast" ? "default" : "outline"}
            onClick={() => setRuleType("forecast")}
          >
            Forecast
          </Button>
          <Button
            type="button"
            size="sm"
            variant={ruleType === "multivariate" ? "default" : "outline"}
            onClick={() => setRuleType("multivariate")}
          >
            Combination
          </Button>
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <Label htmlFor="rule-device">Applies to</Label>
        <select
          id="rule-device"
          className={selectClassName()}
          value={deviceId}
          onChange={(e) => setDeviceId(e.target.value)}
        >
          <option value="">All devices</option>
          {devices.map((device) => (
            <option key={device.id} value={device.id}>
              {device.name}
            </option>
          ))}
        </select>
      </div>

      <div className="grid grid-cols-2 gap-4">
        {ruleType === "multivariate" ? (
          <div className="flex flex-col gap-2">
            <Label>Metric</Label>
            {/* Not a picker: a combination rule judges the joint vector, and
                the API rejects any metric but the reserved one. Stating that
                beats an empty control or a select with one option. */}
            <p className="pt-2 text-sm text-muted-foreground">
              All of them at once — the novelty score.
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            <Label htmlFor="rule-metric">Metric</Label>
            <select
              id="rule-metric"
              className={selectClassName()}
              value={metric}
              onChange={(e) => setMetric(e.target.value as Metric)}
            >
              {METRICS.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </div>
        )}

        {(ruleType === "threshold" ||
          ruleType === "forecast" ||
          ruleType === "multivariate") && (
          <div className="flex flex-col gap-2">
            <Label htmlFor="rule-comparison">Condition</Label>
            <select
              id="rule-comparison"
              className={selectClassName()}
              value={comparison}
              onChange={(e) => setComparison(e.target.value as Comparison)}
            >
              {COMPARISONS.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 gap-4">
        {(ruleType === "threshold" ||
          ruleType === "forecast" ||
          ruleType === "multivariate") && (
          <div className="flex flex-col gap-2">
            <Label htmlFor="rule-threshold">Threshold</Label>
            <Input
              id="rule-threshold"
              type="number"
              step="any"
              required
              value={threshold}
              onChange={(e) => setThreshold(e.target.value)}
            />
          </div>
        )}

        <div className="flex flex-col gap-2">
          <Label htmlFor="rule-duration">For (seconds)</Label>
          <Input
            id="rule-duration"
            type="number"
            min={0}
            required
            value={forDuration}
            onChange={(e) => setForDuration(e.target.value)}
          />
        </div>
      </div>
      {ruleType === "threshold" && (
        <p className="text-xs text-muted-foreground">
          The condition must hold continuously for this long before the rule fires. A rule can
          never fire on a single sample, even with 0 seconds — it always takes at least one more
          evaluation tick.
        </p>
      )}
      {ruleType === "anomaly" && (
        <p className="text-xs text-muted-foreground">
          Fires when this metric drifts unusually far from its own recent normal for this long,
          adapting automatically as that normal changes over time. How far counts as unusual is
          set once for the whole account under{" "}
          <Link to="/settings" className="underline underline-offset-2">
            Settings → Anomaly sensitivity
          </Link>
          .
        </p>
      )}
      {ruleType === "forecast" && (
        <p className="text-xs text-muted-foreground">
          Fires when the device's 24h-ahead forecast — fit from its last ~two weeks of history —
          is predicted to cross this threshold, not when the live reading does. A device with too
          little history for a trustworthy forecast simply never fires this rule until it has
          enough.
        </p>
      )}
      {ruleType === "multivariate" && (
        <p className="text-xs text-muted-foreground">
          Fires on the <em>combination</em> of this machine's readings rather than any single one
          — scored 0–100 against a model trained on its own history, so it can catch a state
          where every individual metric looks ordinary. 95 means "more unusual than 95% of
          everything this machine has done before", not "95%" of anything. A device with no
          trained model never fires this rule.
        </p>
      )}

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
          {submitting ? "Saving…" : rule ? "Save changes" : "Create rule"}
        </Button>
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  )
}
