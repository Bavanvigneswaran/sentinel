// Copied from web/src/types/ — the backend's Pydantic schemas are the single
// source of truth and both clients hand-write against them (see CLAUDE.md's
// Conventions). Kept byte-identical to the web copy so a schema change is one
// diff applied twice, not two divergent guesses.
/**
 * Mirrors backend/app/schemas/live.py — the viewer WebSocket protocol and
 * the two REST endpoints that support it.
 */

import type {
  DiskIoEntry,
  DiskUsageEntry,
  LatencyEntry,
  NetEntry,
  ProcessEntry,
  Sample,
  SystemSample,
} from "@/types/protocol"

// --- frames: browser → server ------------------------------------------------

export type ViewerClientFrame =
  | { type: "subscribe"; device_id: string }
  | { type: "unsubscribe"; device_id: string }
  | { type: "pong" }

// --- frames: server → browser ------------------------------------------------

export interface SubscribedFrame {
  type: "subscribed"
  device_id: string
  online: boolean
}

export interface SamplesFrame {
  type: "samples"
  device_id: string
  samples: Sample[]
}

export interface DeviceStatusFrame {
  type: "device_status"
  device_id: string
  status: "pending" | "online" | "offline"
}

export interface ViewerPingFrame {
  type: "ping"
}

export interface ViewerErrorFrame {
  type: "error"
  code: "unauthorized" | "invalid_frame" | "too_large" | "not_found" | "too_many_subscriptions"
  message: string
  device_id?: string | null
}

export type ViewerServerFrame =
  | SubscribedFrame
  | SamplesFrame
  | DeviceStatusFrame
  | ViewerPingFrame
  | ViewerErrorFrame

// --- REST: tickets and the recent-samples primer ------------------------------

export interface TicketResponse {
  /** Single-use, 30s TTL. Spent immediately as `?ticket=` on the /ws/viewer
   * connect and never reused — see lib/liveSocket.ts. */
  ticket: string
}

export interface RecentSamplesResponse {
  device_id: string
  since: string
  system: (SystemSample & { ts: string })[]
  disk_io: (DiskIoEntry & { ts: string })[]
  net: (NetEntry & { ts: string })[]
  latency: (LatencyEntry & { ts: string })[]
  /** A single latest snapshot, not a series — see the backend docstring on
   * RecentSamplesOut. */
  disk_usage: (DiskUsageEntry & { ts: string })[]
  processes: (ProcessEntry & { ts: string })[]
}
