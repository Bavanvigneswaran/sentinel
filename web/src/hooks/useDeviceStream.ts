import { useEffect, useRef, useState } from "react"

import { apiFetch } from "@/lib/api"
import type { DeviceStatus } from "@/lib/liveSocket"
import { liveSocket } from "@/lib/liveSocket"
import { createStreamBuffers, primeBuffers, pushSample } from "@/lib/streamBuffers"
import type { StreamBuffers } from "@/lib/streamBuffers"
import type { RecentSamplesResponse } from "@/types/live"
import type { DiskUsageEntry, ProcessEntry } from "@/types/protocol"

const PRIME_WINDOW_SECONDS = 300

interface DeviceStreamResult {
  /** Mutated in place by incoming samples; charts read it directly on their
   * own rAF tick rather than through React state. */
  buffers: React.RefObject<StreamBuffers>
  /** True once the recent-samples primer request has settled (success or
   * failure) — lets the page distinguish "still loading" from "loaded and
   * empty". */
  primed: boolean
  online: boolean | null
  status: DeviceStatus | null
  diskUsage: DiskUsageEntry[]
  processes: ProcessEntry[]
}

/** Subscribes to one device's live stream and keeps its chart buffers fed.
 *
 * disk_usage and processes are point-in-time snapshots, not per-second
 * series, so they're plain React state — a bars panel and a 10-row table
 * re-rendering at up to 1Hz costs nothing worth avoiding, unlike the six
 * line charts driven off `buffers`.
 *
 * Callers should key their component by `deviceId` (see
 * LiveMonitoringPage.tsx) rather than expect this hook to reset itself if
 * `deviceId` changes under a stable component instance — a fresh mount gives
 * every piece of state a clean start for free, which is simpler than an
 * effect racing to reset five of them by hand.
 */
export function useDeviceStream(deviceId: string): DeviceStreamResult {
  const buffersRef = useRef<StreamBuffers>(createStreamBuffers())
  const [primed, setPrimed] = useState(false)
  const [online, setOnline] = useState<boolean | null>(null)
  const [status, setStatus] = useState<DeviceStatus | null>(null)
  const [diskUsage, setDiskUsage] = useState<DiskUsageEntry[]>([])
  const [processes, setProcesses] = useState<ProcessEntry[]>([])

  useEffect(() => {
    let cancelled = false

    apiFetch<RecentSamplesResponse>(
      `/devices/${deviceId}/samples/recent?seconds=${PRIME_WINDOW_SECONDS}`,
    )
      .then((recent) => {
        if (cancelled) return
        primeBuffers(buffersRef.current, recent)
        if (recent.disk_usage.length > 0) setDiskUsage(recent.disk_usage)
        if (recent.processes.length > 0) setProcesses(recent.processes)
      })
      .catch(() => {
        // Priming is a convenience for the ~10s upshift window (see
        // app/live/supervisor.py); the live subscription below populates the
        // chart regardless once the first batch arrives.
      })
      .finally(() => {
        if (!cancelled) setPrimed(true)
      })

    const unsubscribe = liveSocket.subscribeDevice(deviceId, {
      onOnline: (o) => {
        if (!cancelled) setOnline(o)
      },
      onStatus: (s) => {
        if (!cancelled) setStatus(s)
      },
      onSamples: (samples) => {
        if (cancelled) return
        for (const sample of samples) {
          pushSample(buffersRef.current, sample)
          if (sample.disk_usage.length > 0) setDiskUsage(sample.disk_usage)
          if (sample.processes.length > 0) setProcesses(sample.processes)
        }
      },
    })

    return () => {
      cancelled = true
      unsubscribe()
    }
  }, [deviceId])

  return { buffers: buffersRef, primed, online, status, diskUsage, processes }
}
