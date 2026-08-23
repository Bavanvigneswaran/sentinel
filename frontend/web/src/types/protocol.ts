/**
 * Mirrors backend/app/schemas/protocol.py — the agent↔server wire shape.
 * The viewer WebSocket reuses these same `Sample` objects (see types/live.ts),
 * and the recent-samples primer endpoint reuses the per-domain entry shapes
 * with a `ts` field attached.
 *
 * `null` is a real value here, not a placeholder: a metric the platform
 * cannot measure is `null`, never `0` and never omitted. Every chart that
 * consumes these types must render `null` as "unavailable" and draw a gap,
 * not a synthesized zero — see components/MetricValue.tsx and the `spanGaps:
 * false` setting on every uPlot series.
 */

export interface SystemSample {
  cpu_percent: number | null
  cpu_percent_min: number | null
  cpu_percent_max: number | null
  cpu_user_percent: number | null
  cpu_system_percent: number | null
  cpu_iowait_percent: number | null
  cpu_freq_mhz: number | null
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
  swap_total_bytes: number | null
  swap_used_bytes: number | null
  swap_percent: number | null
  uptime_seconds: number | null
  logged_in_users: number | null
  process_count: number | null
  active_connections: number | null
  temperature_celsius: number | null
  fan_rpm: number | null
  battery_percent: number | null
  battery_plugged: boolean | null
}

export interface DiskUsageEntry {
  mount: string
  filesystem: string | null
  total_bytes: number | null
  used_bytes: number | null
  free_bytes: number | null
  percent: number | null
}

export interface DiskIoEntry {
  disk: string
  read_bytes_per_s: number | null
  write_bytes_per_s: number | null
  read_iops: number | null
  write_iops: number | null
  busy_percent: number | null
}

export interface NetEntry {
  nic: string
  tx_bytes_per_s: number | null
  rx_bytes_per_s: number | null
  tx_packets_per_s: number | null
  rx_packets_per_s: number | null
  errors_in_per_s: number | null
  errors_out_per_s: number | null
  drops_in_per_s: number | null
  drops_out_per_s: number | null
}

export interface LatencyEntry {
  target: string
  reachable: boolean
  rtt_ms_avg: number | null
  rtt_ms_min: number | null
  rtt_ms_max: number | null
  packet_loss_percent: number | null
}

export interface ProcessEntry {
  rank_by: "cpu" | "memory"
  rank: number
  pid: number
  name: string
  username: string | null
  cpu_percent: number | null
  memory_bytes: number | null
}

export interface Sample {
  ts: string
  resolution_seconds: 1 | 10
  system: SystemSample
  disk_usage: DiskUsageEntry[]
  disk_io: DiskIoEntry[]
  net: NetEntry[]
  latency: LatencyEntry[]
  processes: ProcessEntry[]
}
