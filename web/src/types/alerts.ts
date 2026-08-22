/**
 * Wire types for alert rules, firing history, and silences. Mirrors
 * app/schemas/alerts.py.
 */

export type Metric =
  | "cpu_percent"
  | "mem_percent"
  | "swap_percent"
  | "disk_percent"
  | "packet_loss_percent"
  | "cpu_iowait_percent"

export type Comparison = ">" | ">=" | "<" | "<=" | "=="
export type RuleType = "threshold" | "anomaly" | "forecast"
export type Severity = "watch" | "warning" | "critical"
export type AlertRuleState = "ok" | "pending" | "firing"
export type EventStatus = "firing" | "resolved"

export interface AlertRule {
  id: string
  /** null applies the rule to every one of the caller's devices. */
  device_id: string | null
  name: string
  rule_type: RuleType
  metric: Metric
  /** null for an anomaly rule — judged against an adaptive baseline instead. */
  comparison: Comparison | null
  threshold: number | null
  for_duration_seconds: number
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface AlertRuleCreate {
  device_id?: string | null
  name: string
  rule_type: RuleType
  metric: Metric
  comparison?: Comparison | null
  threshold?: number | null
  for_duration_seconds: number
  enabled?: boolean
}

export type AlertRuleUpdate = Partial<AlertRuleCreate>

export interface AlertEvent {
  id: string
  rule_id: string | null
  device_id: string
  /** Snapshotted from the rule at fire time — correct even after the rule
   * is later edited or deleted. `rule_type` is the reliable discriminator
   * for which evidence fields below are populated (comparison/threshold are
   * set for both threshold and forecast events, so comparison alone no
   * longer distinguishes every kind). */
  rule_name: string
  rule_type: RuleType
  metric: Metric
  comparison: Comparison | null
  threshold: number | null
  status: EventStatus
  value_at_fire: number
  /** null only for an event whose owning rule was deleted before its first
   * re-evaluation tick; otherwise always present once fired. */
  last_value: number | null
  fired_at: string
  resolved_at: string | null
  resolved_value: number | null
  /** null means never notified — either not yet attempted, or silenced. */
  notified_at: string | null
  /** The four below are populated only for an anomaly-sourced event. */
  observed_value: number | null
  baseline_mean: number | null
  /** Unscaled EWMA absolute deviation — see lib/anomaly.ts's scaledSpread(). */
  baseline_mad: number | null
  z_score: number | null
  /** Derived from z_score at read time, not stored — null exactly when
   * z_score is null. */
  severity: Severity | null
  /** Populated only for a forecast-sourced event. */
  predicted_breach_at: string | null
  predicted_value: number | null
}

export interface AnomalyBaseline {
  device_id: string
  metric: Metric
  mean: number
  /** Unscaled EWMA absolute deviation — see lib/anomaly.ts's scaledSpread(). */
  mad: number
  sample_count: number
  updated_at: string
}

export interface AlertSilence {
  id: string
  /** null silences every device / every rule respectively. */
  device_id: string | null
  rule_id: string | null
  starts_at: string
  ends_at: string
  reason: string | null
  created_at: string
}

export interface AlertSilenceCreate {
  device_id?: string | null
  rule_id?: string | null
  starts_at: string
  ends_at: string
  reason?: string | null
}
