/**
 * Wire types for the live forecast/exhaustion state app/workers/forecast_worker.py
 * maintains. Mirrors app/schemas/forecast.py.
 */

export type ForecastMetric =
  | "cpu_percent"
  | "mem_percent"
  | "swap_percent"
  | "disk_percent"
  | "packet_loss_percent"
  | "cpu_iowait_percent"

export type ExhaustionMetric = "mem_percent" | "disk_percent"

export interface ForecastPoint {
  /** Seconds after the forecast's own computed_at. */
  offset_seconds: number
  predicted: number
  lower: number
  upper: number
}

export interface MetricForecast {
  device_id: string
  metric: ForecastMetric
  /** Which mount (disk_percent) or target (packet_loss_percent) this was
   * actually fit against — null for a single-entity metric. */
  entity: string | null
  computed_at: string
  horizon_seconds: number
  bucket_seconds: number
  /** Empty when there wasn't enough history to fit one — still present so
   * computed_at can say when that was last checked. */
  points: ForecastPoint[]
}

export interface ExhaustionForecast {
  device_id: string
  metric: ExhaustionMetric
  /** Which mount this was fit against for disk_percent; null for mem_percent. */
  entity: string | null
  computed_at: string
  current_value: number
  slope_per_day: number
  /** null means not projected to reach capacity on any actionable horizon —
   * either not trending upward, or too slowly to matter. */
  projected_at: string | null
}
