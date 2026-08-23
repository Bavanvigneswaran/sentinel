package com.sentinel.collector

/**
 * The sampling ring buffer, ported from `agent/sentinel_agent/buffer.py`.
 *
 * A batch stays here until the server acks it, so a connection that drops
 * mid-push loses nothing and the server's idempotent writes make redelivery
 * harmless. When the ring is full the *oldest* sample is dropped: under a long
 * outage we keep the most recent hour rather than the first hour.
 *
 * Synchronised because — unlike the Python agent's single event loop — the
 * sample loop and the push loop here are separate coroutines that may run on
 * different threads of the default dispatcher.
 */
class SampleBuffer(private val maxLen: Int) {
    private val samples = ArrayDeque<Sample>()

    val size: Int
        @Synchronized get() = samples.size

    @Synchronized
    fun add(sample: Sample) {
        while (samples.size >= maxLen) samples.removeFirst()
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

    @Synchronized
    fun clear() = samples.clear()
}
