/**
 * The viewer WebSocket client. Ported from web/src/lib/liveSocket.ts.
 *
 * One shared connection for the whole app, module-scoped the same way
 * lib/api.ts single-flights /auth/refresh: every subscriber shares it rather
 * than opening a socket per screen. subscribeDevice() ref-counts, so two
 * components watching the same device_id produce one `subscribe` frame, not
 * two.
 *
 * Reconnects with exponential backoff plus jitter, mirroring the agent's own
 * agent/sentinel_agent/transport/client.py.
 *
 * Two mobile-specific differences from the web copy:
 *
 * - The URL comes from src/config.ts, not window.location, because there is no
 *   page this bundle was served by.
 * - `suspend()`/`resume()` exist. A browser tab that goes to the background
 *   keeps its socket; a phone app that goes to the background is suspended by
 *   the OS with the socket still nominally open, and Phase 3's live registry
 *   would keep the agent pushing 1s samples until the 30s lease expired.
 *   Dropping the socket on background makes that downshift immediate and
 *   deliberate rather than something we wait on a timeout for.
 */

import { WS_BASE_URL } from "@/config"
import { apiFetch } from "@/lib/api"
import type { TicketResponse, ViewerServerFrame } from "@/types/live"
import type { Sample } from "@/types/protocol"

const INITIAL_BACKOFF_MS = 1000
const MAX_BACKOFF_MS = 30_000
const BACKOFF_FACTOR = 2
const JITTER = 0.5

export type DeviceStatus = "pending" | "online" | "offline"

export interface DeviceListener {
  onSamples?: (samples: Sample[]) => void
  /** Fired once, right after a successful `subscribe`. */
  onOnline?: (online: boolean) => void
  /** Fired whenever the server pushes a status change for this device. */
  onStatus?: (status: DeviceStatus) => void
}

/** Full jitter, not a security-sensitive value — Math.random() is fine here. */
function nextBackoffMs(attempt: number): number {
  const base = Math.min(INITIAL_BACKOFF_MS * BACKOFF_FACTOR ** attempt, MAX_BACKOFF_MS)
  return base * (1 - JITTER * Math.random())
}

class LiveSocket {
  private ws: WebSocket | null = null
  private connecting = false
  private attempt = 0
  private reconnectTimer: ReturnType<typeof setTimeout> | undefined
  private readonly listeners = new Map<string, Set<DeviceListener>>()
  private readonly subscribedOnSocket = new Set<string>()
  /** Set while the app is backgrounded, so a stray onclose does not immediately
   * reconnect the socket we just deliberately dropped. */
  private suspended = false

  /** Subscribe to a device's live stream. Returns an unsubscribe function. */
  subscribeDevice(deviceId: string, listener: DeviceListener): () => void {
    let set = this.listeners.get(deviceId)
    if (!set) {
      set = new Set()
      this.listeners.set(deviceId, set)
    }
    set.add(listener)

    this.ensureConnected()
    this.sendSubscribeIfNeeded(deviceId)

    return () => {
      set.delete(listener)
      if (set.size === 0) {
        this.listeners.delete(deviceId)
        this.sendUnsubscribe(deviceId)
      }
    }
  }

  /** Drop the connection without forgetting who was listening. Called when the
   * app backgrounds; `resume()` reconnects and re-subscribes everyone. */
  suspend(): void {
    this.suspended = true
    this.clearReconnectTimer()
    const ws = this.ws
    this.ws = null
    this.subscribedOnSocket.clear()
    ws?.close()
  }

  resume(): void {
    if (!this.suspended) return
    this.suspended = false
    this.attempt = 0
    if (this.listeners.size > 0) this.ensureConnected()
  }

  private ensureConnected(): void {
    if (this.ws || this.connecting || this.suspended) return
    void this.connect()
  }

  private async connect(): Promise<void> {
    this.connecting = true
    try {
      // A fresh, single-use ticket every connection attempt — a stale one
      // from a previous attempt was already spent or has expired. The access
      // JWT is never put in this query string; see app/live/tickets.py.
      const { ticket } = await apiFetch<TicketResponse>("/ws/tickets", { method: "POST" })
      if (this.suspended) return

      const ws = new WebSocket(`${WS_BASE_URL}/ws/viewer?ticket=${encodeURIComponent(ticket)}`)

      ws.onopen = () => {
        this.attempt = 0
        this.subscribedOnSocket.clear()
        for (const deviceId of this.listeners.keys()) {
          this.sendSubscribeIfNeeded(deviceId)
        }
      }

      ws.onmessage = (event) => {
        this.handleFrame(JSON.parse(event.data as string) as ViewerServerFrame)
      }

      ws.onclose = () => {
        if (this.ws === ws) this.ws = null
        this.scheduleReconnect()
      }

      // RN surfaces a failed connect as onerror followed by onclose; this
      // handler exists only to keep the default error log quiet.
      ws.onerror = () => {}

      this.ws = ws
    } catch {
      this.ws = null
      this.scheduleReconnect()
    } finally {
      this.connecting = false
    }
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer !== undefined) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = undefined
    }
  }

  private scheduleReconnect(): void {
    // Nothing to watch, or deliberately suspended: let the socket stay closed
    // until the next subscribeDevice() / resume() reopens it.
    if (this.suspended) return
    if (this.listeners.size === 0) return
    if (this.reconnectTimer !== undefined) return

    const delay = nextBackoffMs(this.attempt)
    this.attempt += 1
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = undefined
      void this.connect()
    }, delay)
  }

  private sendSubscribeIfNeeded(deviceId: string): void {
    if (this.ws?.readyState !== WebSocket.OPEN) return
    if (this.subscribedOnSocket.has(deviceId)) return
    this.subscribedOnSocket.add(deviceId)
    this.ws.send(JSON.stringify({ type: "subscribe", device_id: deviceId }))
  }

  private sendUnsubscribe(deviceId: string): void {
    this.subscribedOnSocket.delete(deviceId)
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: "unsubscribe", device_id: deviceId }))
    }
    if (this.listeners.size === 0 && this.ws) {
      this.ws.close()
      this.ws = null
    }
  }

  private handleFrame(frame: ViewerServerFrame): void {
    switch (frame.type) {
      case "samples":
        this.listeners.get(frame.device_id)?.forEach((l) => l.onSamples?.(frame.samples))
        return
      case "subscribed":
        this.listeners.get(frame.device_id)?.forEach((l) => l.onOnline?.(frame.online))
        return
      case "device_status":
        this.listeners.get(frame.device_id)?.forEach((l) => l.onStatus?.(frame.status))
        return
      case "ping":
        this.ws?.send(JSON.stringify({ type: "pong" }))
        return
      case "error":
        // Scoped errors (not_found, too_many_subscriptions) are the only
        // kind that leave the socket open; fatal ones are followed by the
        // server closing the connection, which onclose already handles.
        console.warn("viewer socket error", frame.code, frame.message)
    }
  }
}

export const liveSocket = new LiveSocket()
