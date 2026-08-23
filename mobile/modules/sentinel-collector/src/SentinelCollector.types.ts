/**
 * The collector's JS-facing contract.
 *
 * Every field here is *observed* state read back from the Kotlin service, not a
 * mirror the JS side maintains. The service is the source of truth precisely
 * because it outlives the JS runtime: after the app is killed and reopened,
 * `status()` describes what has actually been happening in the meantime.
 */
export interface CollectorStatus {
  /** A sealed agent token exists on this device. */
  enrolled: boolean
  /** The foreground service is up. Not the same as connected. */
  running: boolean
  /** There is a live socket to the ingest endpoint right now. */
  connected: boolean
  deviceId: string | null
  deviceName: string | null
  serverUrl: string
  /** The cadence the server has asked for: "normal" or "live". */
  mode: "normal" | "live"
  pushIntervalSeconds: number
  /** Samples collected but not yet acked — a backlog means an outage. */
  bufferedSamples: number
  /** ISO-8601, or null if nothing has been acked yet this run. */
  lastPushAt: string | null
  lastSampleAt: string | null
  /** The real transport error, not a generic "disconnected". */
  lastError: string | null
  /**
   * Probes this particular device refuses, named for display — see
   * docs/ANDROID_METRICS.md. Distinct from the metrics Android can *never*
   * measure, which are constant and documented rather than reported.
   */
  unavailableProbes: string[]
  batteryOptimizationExempt: boolean
  /**
   * Sample every second even outside live mode. Off by default — a 1s timer
   * running all day is the collector's dominant battery cost, and every metric
   * Android lets an app read moves far too slowly for 1s to add anything.
   */
  highFrequency: boolean
  /** What the sample loop is actually running at right now. Not derivable from
   *  `mode`: a high-frequency phone runs at 1s in normal mode too. */
  sampleIntervalSeconds: number
}

export type CollectorStatusEvent = (status: CollectorStatus) => void

export interface SentinelCollectorEvents {
  onCollectorStatus: CollectorStatusEvent
}
