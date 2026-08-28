package com.sentinel.collector

/**
 * The sampling ring buffer, ported from `agent/sentinel_agent/buffer.py`.
 *
 * A batch stays here until the server acks it, so a connection that drops
 * mid-push loses nothing and the server's idempotent writes make redelivery
 * harmless. When the ring is full the *oldest* sample is dropped: under a long
 * outage we keep the most recent hour rather than the first hour.
 *
 * That drop is counted, not just performed. Discarding the oldest is the right
 * behaviour under a bounded buffer, but it is *loss* rather than delay, and a
 * size that has stopped growing because it is pinned at the cap is
 * indistinguishable from one that is merely large. [droppedSamples] is what
 * lets [BufferPressure] tell the two apart on the notification and the
 * collector screen.
 *
 * Synchronised because — unlike the Python agent's single event loop — the
 * sample loop and the push loop here are separate coroutines that may run on
 * different threads of the default dispatcher.
 */
class SampleBuffer(private val maxLen: Int) {
    private val samples = ArrayDeque<Sample>()
    private var dropped = 0

    val size: Int
        @Synchronized get() = samples.size

    /** The cap, carried with the buffer so no display surface has to hardcode
     *  [CollectorConfig.BUFFER_SIZE] to know how close to it we are. */
    val capacity: Int get() = maxLen

    /**
     * Samples permanently discarded to make room, since this run started.
     *
     * Cumulative rather than a flag: it keeps meaning something after the
     * backlog drains, which is exactly when a bare size would report a full
     * recovery over a hole in the history.
     */
    val droppedSamples: Int
        @Synchronized get() = dropped

    @Synchronized
    fun add(sample: Sample) {
        while (samples.size >= maxLen) {
            samples.removeFirst()
            dropped++
        }
        samples.addLast(sample)
    }

    @Synchronized
    fun peek(): List<Sample> = samples.toList()

    /**
     * Drop the oldest [count] samples, once the server has acked them.
     *
     * Counted rather than cleared: samples collected while a push was in flight
     * must survive, or every push would silently lose whatever the sample loop
     * produced during it.
     */
    @Synchronized
    fun discard(count: Int) {
        repeat(minOf(count, samples.size)) { samples.removeFirst() }
    }

    /** Called when the service stops. The loss count goes with the samples:
     *  it describes one run of the collector, like every other runtime fact in
     *  [CollectorStatus]. */
    @Synchronized
    fun clear() {
        samples.clear()
        dropped = 0
    }
}
