/**
 * Create or edit one alert rule. Ported from web/src/components/RuleForm.tsx
 * and kept deliberately recognisable against it — same fields, same three
 * rule types, same explanation under each.
 *
 * What genuinely differs is the input primitives. There is no `<select>` on a
 * phone; every choice here is a wrapping row of chips (`Segmented`), which is
 * why the metric list is capped at the six `METRICS` the evaluator actually
 * reads rather than being an open list. Threshold and duration are numeric
 * text inputs with `keyboardType="numeric"` — a phone keyboard is the closest
 * thing to `<input type="number">` that exists here.
 */

import { useState } from "react"
import { StyleSheet, Switch, Text, View } from "react-native"

import { Button, ErrorNote, Field, Segmented } from "@/components/ui"
import { ApiError, apiFetch } from "@/lib/api"
import { colors, spacing, text } from "@/theme"
import type { AlertRule, Comparison, Metric, RuleType } from "@/types/alerts"
import type { Device } from "@/types/api"

const METRICS: { value: Metric; label: string }[] = [
  { value: "cpu_percent", label: "CPU %" },
  { value: "mem_percent", label: "Memory %" },
  { value: "swap_percent", label: "Swap %" },
  { value: "disk_percent", label: "Disk %" },
  { value: "packet_loss_percent", label: "Loss %" },
  { value: "cpu_iowait_percent", label: "IO wait %" },
]

const COMPARISONS: { value: Comparison; label: string }[] = [
  { value: ">", label: "above" },
  { value: ">=", label: "≥" },
  { value: "<", label: "below" },
  { value: "<=", label: "≤" },
  { value: "==", label: "equals" },
]

const RULE_TYPES: { value: RuleType; label: string }[] = [
  { value: "threshold", label: "Threshold" },
  { value: "anomaly", label: "Anomaly" },
  { value: "forecast", label: "Forecast" },
  { value: "multivariate", label: "Combination" },
]

const EXPLANATION: Record<RuleType, string> = {
  threshold:
    "The condition must hold continuously for this long before the rule fires. A rule can " +
    "never fire on a single sample, even with 0 seconds — it always takes at least one more " +
    "evaluation tick.",
  anomaly:
    "Fires when this metric drifts unusually far from its own recent normal for this long, " +
    "adapting as that normal changes. How far counts as unusual is set once for the whole " +
    "account, under Settings → Anomaly sensitivity.",
  multivariate:
    "Fires on the combination of this machine's readings rather than any single one \u2014 " +
    "scored 0\u2013100 against a model trained on its own history, so it can catch a state " +
    "where every individual metric looks ordinary. 95 means \u201cmore unusual than 95% of " +
    "everything this machine has done before\u201d, not 95% of anything. A device with no " +
    "trained model never fires this rule.",
  forecast:
    "Fires when the device's forecast is predicted to cross this threshold, not when the live " +
    "reading does. A device with too little history for a trustworthy forecast simply never " +
    "fires this rule until it has enough.",
}

export function RuleForm({
  devices,
  rule,
  onSaved,
  onCancel,
}: {
  devices: Device[]
  /** Present when editing an existing rule; absent when creating one. */
  rule?: AlertRule
  onSaved: () => void
  onCancel: () => void
}) {
  const [name, setName] = useState(rule?.name ?? "")
  // "" is the wire's null — one rule applied to every device the caller owns.
  const [deviceId, setDeviceId] = useState(rule?.device_id ?? "")
  const [ruleType, setRuleType] = useState<RuleType>(rule?.rule_type ?? "threshold")
  // The picker is hidden for a combination rule, whose metric is fixed, so an
  // existing one falls back to the default rather than selecting a value the
  // list does not contain.
  const [metric, setMetric] = useState<Metric>(
    rule && rule.metric !== "novelty_score" ? rule.metric : "cpu_percent",
  )
  const [comparison, setComparison] = useState<Comparison>(rule?.comparison ?? ">")
  const [threshold, setThreshold] = useState(
    rule?.threshold != null ? String(rule.threshold) : "90",
  )
  const [forDuration, setForDuration] = useState(rule ? String(rule.for_duration_seconds) : "60")
  const [enabled, setEnabled] = useState(rule?.enabled ?? true)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const needsThreshold =
    ruleType === "threshold" || ruleType === "forecast" || ruleType === "multivariate"
  // A combination rule judges the joint vector; the API pairs that rule type
  // exclusively with the reserved metric, in both directions.
  const isCombination = ruleType === "multivariate"

  const submit = async () => {
    if (name.trim() === "") {
      setError("Give the rule a name.")
      return
    }
    // Validated here as well as by the server: `Number("")` is 0, so an empty
    // threshold would silently become a rule that fires on everything.
    if (needsThreshold && !Number.isFinite(Number(threshold))) {
      setError("Threshold has to be a number.")
      return
    }
    if (!Number.isFinite(Number(forDuration)) || Number(forDuration) < 0) {
      setError("Duration has to be zero or more seconds.")
      return
    }

    setError(null)
    setSubmitting(true)
    try {
      // An anomaly rule leaves comparison/threshold null by design — the DB's
      // rule_type_fields CHECK enforces it, and sending them anyway is a 422.
      const body = needsThreshold
        ? {
            name,
            device_id: deviceId === "" ? null : deviceId,
            rule_type: ruleType,
            metric: isCombination ? "novelty_score" : metric,
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
    <View style={styles.form}>
      {error && <ErrorNote message={error} />}

      <Field label="Name" value={name} onChangeText={setName} placeholder="High CPU" />

      <Labelled label="Type">
        <Segmented options={RULE_TYPES} value={ruleType} onChange={setRuleType} />
      </Labelled>

      <Labelled label="Applies to">
        <Segmented
          options={[
            { value: "", label: "All devices" },
            ...devices.map((d) => ({ value: d.id, label: d.name })),
          ]}
          value={deviceId}
          onChange={setDeviceId}
        />
      </Labelled>

      <Labelled label="Metric">
        {isCombination ? (
          <Text style={text.small}>All of them at once — the novelty score.</Text>
        ) : (
          <Segmented options={METRICS} value={metric} onChange={setMetric} />
        )}
      </Labelled>

      {needsThreshold && (
        <>
          <Labelled label="Condition">
            <Segmented options={COMPARISONS} value={comparison} onChange={setComparison} />
          </Labelled>
          <Field
            label="Threshold"
            value={threshold}
            onChangeText={setThreshold}
            keyboardType="numeric"
          />
        </>
      )}

      <Field
        label="For (seconds)"
        value={forDuration}
        onChangeText={setForDuration}
        keyboardType="numeric"
      />

      <Text style={text.tiny}>{EXPLANATION[ruleType]}</Text>

      <View style={styles.switchRow}>
        <Text style={text.body}>Enabled</Text>
        <Switch
          value={enabled}
          onValueChange={setEnabled}
          trackColor={{ true: colors.primary, false: colors.border }}
        />
      </View>

      <View style={styles.actions}>
        <Button
          title={rule ? "Save changes" : "Create rule"}
          busy={submitting}
          onPress={() => void submit()}
        />
        <Button title="Cancel" variant="outline" onPress={onCancel} />
      </View>
    </View>
  )
}

function Labelled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <View style={{ gap: spacing.xs }}>
      <Text style={text.small}>{label}</Text>
      {children}
    </View>
  )
}

const styles = StyleSheet.create({
  form: { gap: spacing.md },
  switchRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  actions: { gap: spacing.sm },
})
