/**
 * Wires a device's incoming Sample objects — live or from the recent-samples
 * primer — into the RingBuffers each LiveChart panel reads from.
 */

import type { RefObject } from "react"

import { RingBuffer } from "@/lib/ringBuffer"
import type { RecentSamplesResponse } from "@/types/live"
import type { Sample } from "@/types/protocol"

// 15 minutes at 1s live resolution — generous for a chart window, small
// enough that four of these cost nothing worth measuring.
const CAPACITY = 900

export interface StreamBuffers {
  system: RingBuffer
  net: RingBuffer
  diskIo: RingBuffer
  latency: RingBuffer
}

export function createStreamBuffers(): StreamBuffers {
  return {
    system: new RingBuffer(CAPACITY),
    net: new RingBuffer(CAPACITY),
    diskIo: new RingBuffer(CAPACITY),
    latency: new RingBuffer(CAPACITY),
  }
}

/** A thin proxy onto one of a StreamBuffers ref's fields, so a chart can hold
 * a stable `RefObject<RingBuffer>` even though the parent StreamBuffers
 * object itself gets replaced wholesale on every device switch (see
 * useDeviceStream). Reads `.current` fresh every time, so it always reflects
 * whichever StreamBuffers instance is live. */
export function subBufferRef<K extends keyof StreamBuffers>(
  parent: RefObject<StreamBuffers>,
  key: K,
): RefObject<StreamBuffers[K]> {
  return {
    get current() {
      return parent.current[key]
    },
  } as RefObject<StreamBuffers[K]>
}

function toEpochSeconds(ts: string): number {
  return new Date(ts).getTime() / 1000
}

export function pushSample(buffers: StreamBuffers, sample: Sample): void {
  const ts = toEpochSeconds(sample.ts)

  buffers.system.push(ts, {
    cpu_percent: sample.system.cpu_percent,
    cpu_user_percent: sample.system.cpu_user_percent,
    cpu_system_percent: sample.system.cpu_system_percent,
    mem_percent: sample.system.mem_percent,
    swap_percent: sample.system.swap_percent,
  })

  const net: Record<string, number | null> = {}
  for (const n of sample.net) {
    net[`${n.nic}:tx`] = n.tx_bytes_per_s
    net[`${n.nic}:rx`] = n.rx_bytes_per_s
  }
  buffers.net.push(ts, net)

  const diskIo: Record<string, number | null> = {}
  for (const d of sample.disk_io) {
    diskIo[`${d.disk}:read`] = d.read_bytes_per_s
    diskIo[`${d.disk}:write`] = d.write_bytes_per_s
  }
  buffers.diskIo.push(ts, diskIo)

  const latency: Record<string, number | null> = {}
  for (const l of sample.latency) {
    // Unreachable is a gap, not a zero-latency reading — a "perfect
    // connection" line for a target that is actually down would be exactly
    // the kind of synthesized number this product refuses to show.
    latency[l.target] = l.reachable ? l.rtt_ms_avg : null
  }
  buffers.latency.push(ts, latency)
}

/** Same-timestamp rows are adjacent because the backend orders by `ts`; this
 * regroups them back into per-tick entries so they can be pushed the same
 * way a live Sample's nested arrays are. */
function groupByTs<T extends { ts: string }>(rows: T[]): { ts: string; items: T[] }[] {
  const groups: { ts: string; items: T[] }[] = []
  for (const row of rows) {
    const last = groups[groups.length - 1]
    if (last && last.ts === row.ts) {
      last.items.push(row)
    } else {
      groups.push({ ts: row.ts, items: [row] })
    }
  }
  return groups
}

export function primeBuffers(buffers: StreamBuffers, recent: RecentSamplesResponse): void {
  for (const p of recent.system) {
    buffers.system.push(toEpochSeconds(p.ts), {
      cpu_percent: p.cpu_percent,
      cpu_user_percent: p.cpu_user_percent,
      cpu_system_percent: p.cpu_system_percent,
      mem_percent: p.mem_percent,
      swap_percent: p.swap_percent,
    })
  }

  for (const { ts, items } of groupByTs(recent.net)) {
    const values: Record<string, number | null> = {}
    for (const n of items) {
      values[`${n.nic}:tx`] = n.tx_bytes_per_s
      values[`${n.nic}:rx`] = n.rx_bytes_per_s
    }
    buffers.net.push(toEpochSeconds(ts), values)
  }

  for (const { ts, items } of groupByTs(recent.disk_io)) {
    const values: Record<string, number | null> = {}
    for (const d of items) {
      values[`${d.disk}:read`] = d.read_bytes_per_s
      values[`${d.disk}:write`] = d.write_bytes_per_s
    }
    buffers.diskIo.push(toEpochSeconds(ts), values)
  }

  for (const { ts, items } of groupByTs(recent.latency)) {
    const values: Record<string, number | null> = {}
    for (const l of items) {
      values[l.target] = l.reachable ? l.rtt_ms_avg : null
    }
    buffers.latency.push(toEpochSeconds(ts), values)
  }
}
