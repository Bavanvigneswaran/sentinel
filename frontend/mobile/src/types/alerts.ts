// Copied from web/src/types/ — the backend's Pydantic schemas are the single
// source of truth and both clients hand-write against them (see CLAUDE.md's
// Conventions). Kept byte-identical to the web copy so a schema change is one
// diff applied twice, not two divergent guesses.
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
/** The reserved metric a multivariate ("combination") rule judges: layer 4's
 *  novelty percentile, not a column anything measures. Kept out of `Metric`
 *  so the other three rule types cannot be pointed at it — the API rejects
 *  that pairing in both directions. */
export type RuleMetric = Metric | "novelty_score"

export type RuleType = "threshold" | "anomaly" | "forecast" | "multivariate"
/** Where a rule came from. "builtin" rules are seeded for every account so it
 * detects things without being configured (app/alerts/defaults.py); they are
 * ordinary rules otherwise, and are edited and deleted like any other. */
export type RuleSource = "user" | "builtin"
export type Severity = "watch" | "warning" | "critical"
export type AlertRuleState = "ok" | "pending" | "firing"
export type EventStatus = "firing" | "resolved"

export interface AlertRule {
  id: string
  /** null applies the rule to every one of the caller's devices. */
  device_id: string | null
  name: string
  rule_type: RuleType
  metric: RuleMetric
  /** null for an anomaly rule — judged against an adaptive baseline instead. */
  comparison: Comparison | null
  threshold: number | null
  for_duration_seconds: number
  enabled: boolean
  source: RuleSource
  created_at: string
  updated_at: string
}

export interface AlertRuleCreate {
  device_id?: string | null
  name: string
  rule_type: RuleType
  metric: RuleMetric
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
  /** The incident this event was correlated into — see
   * app/alerts/incident_apply.py. Always populated once an event fires. */
  incident_id: string | null
  /** Snapshotted from the rule at fire time — correct even after the rule
   * is later edited or deleted. `rule_type` is the reliable discriminator
   * for which evidence fields below are populated (comparison/threshold are
   * set for both threshold and forecast events, so comparison alone no
   * longer distinguishes every kind). */
  rule_name: string
  rule_type: RuleType
  metric: RuleMetric
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
  metric: RuleMetric
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
