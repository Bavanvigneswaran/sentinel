package com.sentinel.collector

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The ring buffer's contract, mirroring `agent/tests/test_buffer.py`.
 *
 * The behaviour worth guarding is the ack-then-discard cycle: everything else
 * is a deque, but discarding by count rather than clearing is what stops a push
 * from swallowing samples the collector took while that push was in flight.
 */
class SampleBufferTest {

    private fun sample(tag: String, resolution: Int? = null) =
        Sample(ts = tag, system = fieldsOf("mem_percent" to 1.0), resolutionSeconds = resolution)

    @Test
    fun `keeps the most recent samples when it overflows`() {
        val buffer = SampleBuffer(maxLen = 3)
        listOf("a", "b", "c", "d").forEach { buffer.add(sample(it)) }

        // Under a long outage we keep the most recent window, not the first.
        assertEquals(listOf("b", "c", "d"), buffer.peek().map { it.ts })
        assertEquals(3, buffer.size)
    }

    @Test
    fun `overflowing is counted, because it is loss and not delay`() {
        val buffer = SampleBuffer(maxLen = 3)
        assertEquals(0, buffer.droppedSamples)

        listOf("a", "b", "c").forEach { buffer.add(sample(it)) }
        assertEquals(0, buffer.droppedSamples) // full, but nothing lost yet

        listOf("d", "e").forEach { buffer.add(sample(it)) }
        // Without this count, a buffer pinned at its cap is indistinguishable
        // from one that is merely large — which is how a real phone reported
        // "400 buffered" for hours while discarding history.
        assertEquals(2, buffer.droppedSamples)
        assertEquals(3, buffer.capacity)
    }

    @Test
    fun `an acked discard is not counted as loss`() {
        val buffer = SampleBuffer(maxLen = 3)
        listOf("a", "b").forEach { buffer.add(sample(it)) }
        buffer.discard(2)

        // The server has these; they left the buffer because it was told to
        // let them go, which is the opposite of dropping them.
        assertEquals(0, buffer.droppedSamples)
    }

    @Test
    fun `stopping the collector clears the loss count with the samples`() {
        val buffer = SampleBuffer(maxLen = 1)
        listOf("a", "b").forEach { buffer.add(sample(it)) }
        assertEquals(1, buffer.droppedSamples)

        buffer.clear()
        // A runtime fact about one run, like everything else CollectorState
        // resets when the service stops.
        assertEquals(0, buffer.droppedSamples)
    }

    @Test
    fun `discard drops only the acked count, keeping samples taken during the push`() {
        val buffer = SampleBuffer(maxLen = 10)
        listOf("a", "b").forEach { buffer.add(sample(it)) }

        val inFlight = buffer.peek()          // what the push actually sent
        buffer.add(sample("c"))               // collected while it was in flight
        buffer.discard(inFlight.size)

        assertEquals(listOf("c"), buffer.peek().map { it.ts })
    }

    @Test
    fun `discarding more than is buffered empties it rather than throwing`() {
        val buffer = SampleBuffer(maxLen = 10)
        buffer.add(sample("a"))
        buffer.discard(5)
        assertEquals(0, buffer.size)
    }
}
