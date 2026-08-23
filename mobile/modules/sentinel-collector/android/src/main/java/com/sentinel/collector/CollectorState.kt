package com.sentinel.collector

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/**
 * What the collector is doing right now, for the Expo module's `status()` and
 * for the persistent notification.
 *
 * An in-process singleton, which is coherent precisely because the service runs
 * in the app's own process (see the module's AndroidManifest). It is
 * *observed* state, never a claim: `lastPushAt` is null until a push is
 * actually acked, and `lastError` holds the real transport message rather than
 * a generic "disconnected".
 */
data class CollectorStatus(
    val enrolled: Boolean = false,
    val running: Boolean = false,
    val deviceId: String? = null,
    val deviceName: String? = null,
    val serverUrl: String = "",
    val connected: Boolean = false,
    /** "normal" or "live" — the cadence the server has asked for. */
    val mode: String = "normal",
    val pushIntervalSeconds: Int = CollectorConfig.DEFAULT_PUSH_SECONDS,
    val bufferedSamples: Int = 0,
    val lastPushAt: String? = null,
    val lastSampleAt: String? = null,
    val lastError: String? = null,
    /** Probes this particular device refuses, named for the UI. */
    val unavailableProbes: List<String> = emptyList(),
    val batteryOptimizationExempt: Boolean = false,
)

object CollectorState {
    private val _status = MutableStateFlow(CollectorStatus())
    val status: StateFlow<CollectorStatus> = _status

    fun current(): CollectorStatus = _status.value

    fun update(transform: (CollectorStatus) -> CollectorStatus) {
        _status.value = transform(_status.value)
    }

    /** Called when the service stops, so nothing claims a live connection that
     *  is gone. The enrollment facts survive; the runtime facts do not. */
    fun markStopped() = update {
        it.copy(
            running = false,
            connected = false,
            mode = "normal",
            bufferedSamples = 0,
        )
    }
}
