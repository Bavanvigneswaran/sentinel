/**
 * Wire types for the dashboard. Mirrors app/schemas/fleet.py.
 *
 * `null` is load-bearing in every one of these, and never interchangeable with
 * `0`. A health score of null means the device is offline or reported nothing
 * measurable; a null inside a sparkline array means that bucket had no reading.
 * Rendering either as zero would state something the agent never measured.
 */

import type { Device } from "@/types/api"

export type HealthBand = "healthy" | "degraded" | "critical" | "unknown"

export interface HealthComponent {
  key: string
  label: string
  /** null when the platform did not report this metric. */
  value: number | null
  unit: string
  /** null whenever `value` is null — the component is then excluded from the score. */
  score: number | null
  weight: number
}

export interface Health {
  /** null for an offline device, or one with nothing measurable. Not zero. */
  score: number | null
  band: HealthBand
  components: HealthComponent[]
  /** Keys of the components with no reading on this platform. */
  unavailable: string[]
  reason: string | null
}

export interface MountUsage {
  mount: string
  filesystem: string | null
  total_bytes: number | null
  used_bytes: number | null
  free_bytes: number | null
  percent: number | null
}

export interface LatestReadings {
  ts: string
  cpu_percent: number | null
  mem_percent: number | null
  mem_used_bytes: number | null
  mem_total_bytes: number | null
  swap_percent: number | null
  load1: number | null
  uptime_seconds: number | null
  process_count: number | null
  /** Null on a desktop, real on a phone. The Android temperature is the
   *  battery sensor — see docs/ANDROID_METRICS.md. */
  battery_percent: number | null
  battery_plugged: boolean | null
  temperature_celsius: number | null
  net_rx_bytes_per_s: number | null
  net_tx_bytes_per_s: number | null
  disk_read_bytes_per_s: number | null
  disk_write_bytes_per_s: number | null
  packet_loss_percent: number | null
  rtt_ms_avg: number | null
}

export interface Sparkline {
  resolution_seconds: number
  ts: string[]
  cpu_percent: (number | null)[]
  mem_percent: (number | null)[]
}

export interface DeviceSummary {
  device: Device
  health: Health
  /** null when nothing landed inside the server's freshness window. */
  latest: LatestReadings | null
  /** Fullest mount first. */
  disks: MountUsage[]
  sparkline: Sparkline
}

export interface FleetTotals {
  devices: number
  online: number
  offline: number
  pending: number
  healthy: number
  degraded: number
  critical: number
  unknown: number
}

export interface FleetOverview {
  generated_at: string
  window_seconds: number
  totals: FleetTotals
  devices: DeviceSummary[]
}
