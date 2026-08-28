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
    /** The cadence the sample loop is actually running at, which is not always
     *  derivable from `mode`: a phone opted into high-frequency sampling runs
     *  at 1s in normal mode too. */
    val sampleIntervalSeconds: Int = CollectorConfig.NORMAL_SAMPLE_SECONDS,
    val pushIntervalSeconds: Int = CollectorConfig.DEFAULT_PUSH_SECONDS,
    val bufferedSamples: Int = 0,
    /** The ring buffer's cap. Reported rather than assumed by the surfaces that
     *  render the backlog, so "how close to losing data" is one subtraction and
     *  not a constant duplicated in Kotlin and TypeScript. */
    val bufferCapacity: Int = CollectorConfig.BUFFER_SIZE,
    /** Samples permanently discarded because the buffer was full, this run.
     *  A backlog is a delay; this is loss, and the two must not read alike. */
    val droppedSamples: Int = 0,
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
            droppedSamples = 0,
        )
    }
}
