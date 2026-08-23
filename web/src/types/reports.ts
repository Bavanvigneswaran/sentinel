/**
 * Wire types for the analytics/reports API. Mirrors app/schemas/reports.py.
 */

export type ReportMetric =
  | "cpu_percent"
  | "mem_percent"
  | "swap_percent"
  | "disk_percent"
  | "packet_loss_percent"
  | "cpu_iowait_percent"

export type ReportCadence = "weekly" | "monthly"
export type ReportFormat = "pdf" | "csv"

export interface PeriodStats {
  avg: number | null
  min: number | null
  max: number | null
}

export interface MetricTrend {
  metric: ReportMetric
  /** The mount (disk_percent) or target (packet_loss_percent) this trend was
   * computed against — null for a single-entity metric. */
  entity: string | null
  current: PeriodStats
  previous: PeriodStats
  /** Percent change of current.avg vs previous.avg. Null whenever either
   * average is unmeasured, or previous.avg is exactly zero. */
  delta_percent: number | null
}

export interface Availability {
  /** Null only for a zero-width period; otherwise always a number — 0 for a
   * device that reported nothing at all in the period. */
  uptime_percent: number | null
  reporting_seconds: number
  period_seconds: number
}

export interface Reliability {
  incident_count: number
  resolved_incident_count: number
  /** Null when no incident in the period has resolved yet. */
  mean_time_to_resolve_seconds: number | null
  alert_fired_count: number
}

export interface DeviceAnalytics {
  device_id: string
  device_name: string
  availability: Availability
  reliability: Reliability
  trends: MetricTrend[]
}

export interface AnalyticsReport {
  generated_at: string
  period_start: string
  period_end: string
  period_days: number
  devices: DeviceAnalytics[]
}

export interface ReportSchedule {
  id: string
  /** Null reports on the whole fleet. */
  device_id: string | null
  name: string
  period_days: number
  cadence: ReportCadence
  day_of_week: number | null
  day_of_month: number | null
  format: ReportFormat
  /** Empty means "the account's own email". */
  recipients: string[]
  enabled: boolean
  last_sent_at: string | null
  created_at: string
  updated_at: string
}
