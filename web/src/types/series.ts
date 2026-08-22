/**
 * Wire types for the time-range query API. Mirrors app/schemas/series.py.
 *
 * A point is the same shape whether it came from raw 1-second samples or from
 * an hour-wide rollup — that is deliberate, and it is why `source` and
 * `resolution_seconds` travel with the response. A chart that does not say
 * which of the two it is drawing is making a claim it cannot support.
 */

export type SeriesSource = "raw" | "1m" | "5m" | "1h"

export type SeriesDomain = "system" | "disk_usage" | "disk_io" | "net" | "latency"

interface Bucket {
  ts: string
  /** Total measured seconds behind this point. */
  sample_weight: number
  /** Original samples behind it — 1 and 6 are both "a minute of CPU". */
  sample_count: number
}

export interface SystemPoint extends Bucket {
  cpu_percent: number | null
  cpu_percent_min: number | null
  cpu_percent_max: number | null
  cpu_user_percent: number | null
  cpu_system_percent: number | null
  cpu_iowait_percent: number | null
  ctx_switches_per_s: number | null
  load1: number | null
  load5: number | null
  load15: number | null
  mem_total_bytes: number | null
  mem_used_bytes: number | null
  mem_available_bytes: number | null
  mem_percent: number | null
  mem_percent_min: number | null
  mem_percent_max: number | null
  swap_percent: number | null
  process_count: number | null
  active_connections: number | null
  temperature_celsius: number | null
  temperature_celsius_max: number | null
  battery_percent: number | null
}

export interface DiskUsagePoint extends Bucket {
  mount: string
  filesystem: string | null
  total_bytes: number | null
  used_bytes: number | null
  free_bytes: number | null
  percent: number | null
  percent_max: number | null
}

export interface DiskIoPoint extends Bucket {
  disk: string
  read_bytes_per_s: number | null
  write_bytes_per_s: number | null
  read_bytes_per_s_max: number | null
  write_bytes_per_s_max: number | null
  read_iops: number | null
  write_iops: number | null
  busy_percent: number | null
}

export interface NetPoint extends Bucket {
  nic: string
  tx_bytes_per_s: number | null
  rx_bytes_per_s: number | null
  tx_bytes_per_s_max: number | null
  rx_bytes_per_s_max: number | null
  errors_in_per_s: number | null
  drops_in_per_s: number | null
}

export interface LatencyPoint extends Bucket {
  target: string
  reachable: boolean
  rtt_ms_avg: number | null
  rtt_ms_min: number | null
  rtt_ms_max: number | null
  packet_loss_percent: number | null
  /** Fraction of the bucket the target answered in — trust this over `reachable`. */
  reachable_ratio: number | null
}

export interface Series {
  device_id: string
  start: string
  end: string
  source: SeriesSource
  /** Bucket width the server actually used, which is not the source's own. */
  resolution_seconds: number
  truncated: boolean
  system: SystemPoint[]
  disk_usage: DiskUsagePoint[]
  disk_io: DiskIoPoint[]
  net: NetPoint[]
  latency: LatencyPoint[]
}
