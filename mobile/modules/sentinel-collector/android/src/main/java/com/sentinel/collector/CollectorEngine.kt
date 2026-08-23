package com.sentinel.collector

import android.content.Context
import android.util.Log
import com.sentinel.collector.collectors.LatencyCollector
import com.sentinel.collector.collectors.SystemCollector
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import java.time.Instant

/**
 * The collector's main loop, the Kotlin counterpart of
 * `agent/sentinel_agent/runner.py`.
 *
 * Sample at a fixed cadence into a ring buffer; push on the interval the server
 * asked for. Sampling continues while the socket is down, so a reconnect
 * delivers the gap rather than a hole in the chart.
 *
 * Nothing here touches the React Native runtime. The engine is started by
 * [CollectorService] and runs on its own dispatchers, which is what lets
 * monitoring continue while the app is backgrounded, swiped away, or has never
 * been opened since boot.
 */
class CollectorEngine(
    context: Context,
    private val config: CollectorConfig,
    private val tokens: TokenStore,
    private val onStatusChanged: () -> Unit = {},
) {
    private val appContext = context.applicationContext
    private val collector = SystemCollector(appContext)
    private val latency = LatencyCollector(DEFAULT_TARGETS.map { LatencyCollector.Target.parse(it) })
    private val buffer = SampleBuffer(CollectorConfig.BUFFER_SIZE)

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var jobs = mutableListOf<Job>()

    /**
     * The cadence the sample loop is running at right now: 10s normally, 1s
     * while a viewer is watching. Stamped onto every sample as it is collected,
     * because a sample's resolution is a fact about when it was taken and not
     * about what mode we happen to be in when we push it.
     */
    @Volatile
    private var sampleIntervalSeconds: Int = CollectorConfig.NORMAL_SAMPLE_SECONDS

    /**
     * Wakes the sample loop when the cadence changes underneath it.
     *
     * Without this, opening Live Monitoring shows nothing for up to ten
     * seconds: the sampler is already inside `delay(10_000)` when the mode
     * frame lands, and changing the interval does not shorten a sleep that has
     * already started. Found by watching the real upshift — the mode switched
     * instantly and the first 1s sample still arrived ten seconds later.
     *
     * CONFLATED because only the latest cadence matters; a backlog of wake-ups
     * would just spin the loop.
     */
    private val cadenceChanged = Channel<Unit>(Channel.CONFLATED)

    /**
     * The most recent real latency measurement, carried between probes so every
     * sample has a value rather than a gap. Written from the probe's own
     * coroutine on Dispatchers.IO and read from the sampling dispatcher, hence
     * @Volatile.
     */
    @Volatile
    private var latestLatency: List<Fields> = emptyList()
    private var lastLatencyAtNanos = 0L
    private var latencyProbe: Job? = null

    fun start() {
        // Seed from config, not the constant: a phone the user has opted into
        // 1s sampling must start at 1s rather than spending its first push
        // interval at 10s and only correcting once the server states a mode.
        sampleIntervalSeconds = config.normalSampleSeconds
        CollectorState.update {
            it.copy(
                running = true,
                sampleIntervalSeconds = sampleIntervalSeconds,
                enrolled = tokens.read() != null,
                deviceId = config.deviceId,
                deviceName = config.deviceName,
                serverUrl = config.serverUrl,
                unavailableProbes = collector.probes.unavailable,
                lastError = null,
            )
        }
        onStatusChanged()
        jobs += scope.launch { sampleLoop() }
        jobs += scope.launch { connectionLoop() }
    }

    fun stop() {
        jobs.forEach { it.cancel() }
        jobs.clear()
        scope.cancel()
        buffer.clear()
        CollectorState.markStopped()
        onStatusChanged()
    }

    fun thermalStatusLabel(): String? = collector.thermalStatusLabel()

    // --- sampling ----------------------------------------------------------

    private fun sampleOnce(): Sample =
        collector.collect(latestLatency).copy(resolutionSeconds = sampleIntervalSeconds)

    /**
     * Kick off a latency probe if one is due, without waiting for it.
     *
     * Three TCP handshakes at a 2s timeout each is six seconds in the worst
     * case — a half-open network where the server is reachable but the probe
     * target is silently dropped. Awaiting that inside the sample loop would
     * stall sampling for those six seconds, which in live mode is a six-second
     * hole in a chart that is supposed to be showing 1s resolution.
     *
     * So the probe runs on its own coroutine and publishes into [latestLatency]
     * when it finishes; samples keep being taken at their own cadence in the
     * meantime, carrying the last real measurement. The value is always
     * something that was actually measured — never interpolated forward in
     * time, just repeated until a newer one exists, exactly as the Python
     * agent carries its own between probes.
     */
    private fun maybeStartLatencyProbe() {
        val now = System.nanoTime()
        if (lastLatencyAtNanos != 0L && now - lastLatencyAtNanos < LATENCY_INTERVAL_NANOS) return
        // A probe still running from last time means the network is slow, not
        // that another one is due; starting a second would pile them up.
        if (latencyProbe?.isActive == true) return

        lastLatencyAtNanos = now
        latencyProbe = scope.launch(Dispatchers.IO) {
            try {
                latestLatency = latency.measure()
            } catch (exc: CancellationException) {
                throw exc
            } catch (exc: Throwable) {
                Log.w(TAG, "latency probe failed; keeping the last measurement", exc)
            }
        }
    }

    private suspend fun sampleLoop() {
        while (scope.isActive) {
            val startedNanos = System.nanoTime()
            try {
                maybeStartLatencyProbe()
                buffer.add(sampleOnce())
                CollectorState.update {
                    it.copy(
                        bufferedSamples = buffer.size,
                        lastSampleAt = Instant.now().toString(),
                    )
                }
            } catch (exc: CancellationException) {
                throw exc
            } catch (exc: Throwable) {
                Log.w(TAG, "sampling failed; continuing", exc)
            }
            // Subtract the work already done so the cadence does not drift.
            val elapsedMs = (System.nanoTime() - startedNanos) / 1_000_000
            val waitMs = (sampleIntervalSeconds * 1000L - elapsedMs).coerceAtLeast(0L)
            // Interruptible: a live upshift must take effect now, not at the
            // end of the ten-second sleep this loop is already inside.
            withTimeoutOrNull(waitMs) { cadenceChanged.receive() }
        }
    }

    // --- pushing -----------------------------------------------------------

    private suspend fun pushLoop(socket: AgentSocket) {
        applyMode(socket)
        while (scope.isActive) {
            delay(socket.pushIntervalSeconds * 1000L)
            // Drain first, then act: a mode frame that arrived during the sleep
            // has to be applied *before* this batch is built, or a live upshift
            // spends one whole push interval still sending 10s aggregates.
            socket.drainServerFrames()
            applyMode(socket)

            val raw = buffer.peek()
            if (raw.isEmpty()) continue

            val (batch, consumed) = Batching.build(raw, socket.mode)
            if (batch.isEmpty()) continue

            val accepted = socket.push(batch)
            // Discard only after the ack, and only what this batch covered.
            buffer.discard(consumed)
            CollectorState.update {
                it.copy(
                    bufferedSamples = buffer.size,
                    lastPushAt = Instant.now().toString(),
                    lastError = null,
                )
            }
            onStatusChanged()
            Log.d(
                TAG,
                "pushed ${batch.size} frame(s) from $consumed sample(s), " +
                    "$accepted accepted; ${buffer.size} still buffered",
            )
        }
    }

    /**
     * Re-read the user's cadence preference on a running collector.
     *
     * Only meaningful outside live mode — while a viewer is watching, 1s is
     * already in force and the server's mode wins. Wakes the sample loop
     * rather than waiting out the current sleep, for the same reason applyMode
     * does (Phase 10b: changing the interval does not shorten a sleep that has
     * already started).
     */
    fun applyCadencePreference() {
        if (CollectorState.current().mode == "live") return
        val wanted = config.normalSampleSeconds
        if (wanted == sampleIntervalSeconds) return
        sampleIntervalSeconds = wanted
        cadenceChanged.trySend(Unit)
        CollectorState.update { it.copy(sampleIntervalSeconds = wanted) }
        Log.i(TAG, "sampling every ${wanted}s (user preference)")
    }

    /** Follow the server's cadence, including the sample loop's own interval:
     *  live monitoring is only meaningful if the samples are actually 1s apart. */
    private fun applyMode(socket: AgentSocket) {
        val wanted = if (socket.mode == "live") {
            CollectorConfig.LIVE_SAMPLE_SECONDS
        } else {
            // Not the constant: the user can opt this phone into sampling at
            // 1s all the time. See CollectorConfig.highFrequency for why that
            // is off by default rather than on.
            config.normalSampleSeconds
        }
        if (wanted != sampleIntervalSeconds) {
            sampleIntervalSeconds = wanted
            cadenceChanged.trySend(Unit)
            CollectorState.update { it.copy(sampleIntervalSeconds = wanted) }
            Log.i(TAG, "sampling every ${wanted}s (${socket.mode} mode)")
        }
        val status = CollectorState.current()
        if (status.mode != socket.mode || status.pushIntervalSeconds != socket.pushIntervalSeconds) {
            CollectorState.update {
                it.copy(mode = socket.mode, pushIntervalSeconds = socket.pushIntervalSeconds)
            }
            onStatusChanged()
        }
    }

    // --- supervision -------------------------------------------------------

    private suspend fun connectionLoop() {
        val token = tokens.read()
        if (token == null) {
            CollectorState.update {
                it.copy(lastError = "This phone is not enrolled yet.", enrolled = false)
            }
            onStatusChanged()
            return
        }
        val host = collector.hostInfo()
        var attempt = 0

        while (scope.isActive) {
            var socket: AgentSocket? = null
            try {
                socket = AgentSocket.connect(config.webSocketUrl, token)
                socket.handshake(host)
                socket.deviceId?.let { config.deviceId = it }
                attempt = 0 // a successful handshake resets the backoff
                CollectorState.update {
                    it.copy(
                        connected = true,
                        deviceId = socket.deviceId ?: it.deviceId,
                        mode = socket.mode,
                        pushIntervalSeconds = socket.pushIntervalSeconds,
                        lastError = null,
                    )
                }
                onStatusChanged()
                pushLoop(socket)
            } catch (exc: CancellationException) {
                throw exc
            } catch (exc: FatalTransportError) {
                Log.e(TAG, "fatal: ${exc.message}")
                CollectorState.update { it.copy(connected = false, lastError = exc.message) }
                onStatusChanged()
                return
            } catch (exc: Throwable) {
                val seconds = Backoff.nextSeconds(attempt)
                attempt++
                Log.w(TAG, "connection lost (${exc.message}); reconnecting in ${"%.1f".format(seconds)}s")
                CollectorState.update {
                    it.copy(connected = false, lastError = exc.message ?: exc.javaClass.simpleName)
                }
                onStatusChanged()
                delay((seconds * 1000).toLong())
            } finally {
                socket?.close()
            }
        }
    }

    private companion object {
        const val TAG = "SentinelCollector"
        val LATENCY_INTERVAL_NANOS =
            CollectorConfig.LATENCY_INTERVAL_SECONDS * 1_000_000_000L

        /** The same default the desktop agent ships with. */
        val DEFAULT_TARGETS = listOf("1.1.1.1:443")
    }
}
