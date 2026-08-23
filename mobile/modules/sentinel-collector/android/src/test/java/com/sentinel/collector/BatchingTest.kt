package com.sentinel.collector

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Batching, and specifically the thing a phone has to get right that the
 * desktop agent never faces: the sample cadence changes with the mode, so a
 * sample's `resolution_seconds` is a fact about when it was taken, not about
 * what mode we are in when it is pushed.
 */
class BatchingTest {

    private fun sample(tag: String, resolution: Int) = Sample(
        ts = tag,
        system = fieldsOf("mem_percent" to 50.0),
        resolutionSeconds = resolution,
    )

    @Test
    fun `normal-mode 10s samples pass through as their own aggregates`() {
        val raw = List(3) { sample("t$it", 10) }
        val (frames, consumed) = Batching.build(raw, mode = "normal")

        assertEquals(3, frames.size)
        assertEquals(3, consumed)
        assertTrue(frames.all { it.resolutionSeconds == 10 })
    }

    @Test
    fun `live mode sends 1s samples raw`() {
        val raw = List(4) { sample("t$it", 1) }
        val (frames, consumed) = Batching.build(raw, mode = "live")

        assertEquals(4, frames.size)
        assertEquals(4, consumed)
        assertTrue(frames.all { it.resolutionSeconds == 1 })
    }

    @Test
    fun `a 10s sample is never relabelled as 1s just because live mode is on`() {
        // The buffer straddles a live upshift: ten-second samples taken before
        // it, one-second samples after.
        val raw = List(2) { sample("slow$it", 10) } + List(3) { sample("fast$it", 1) }
        val (frames, _) = Batching.build(raw, mode = "live")

        assertEquals(listOf(10, 10, 1, 1, 1), frames.map { it.resolutionSeconds })
    }

    @Test
    fun `leftover 1s samples collapse into whole 10s windows when live ends`() {
        // 23 one-second samples left over from a live session, now being pushed
        // in normal mode.
        val raw = List(23) { sample("t$it", 1) }
        val (frames, consumed) = Batching.build(raw, mode = "normal")

        // Two full windows; the trailing three are held back rather than sent
        // as a short, unrepresentative aggregate.
        assertEquals(2, frames.size)
        assertEquals(20, consumed)
        assertTrue(frames.all { it.resolutionSeconds == 10 })
    }

    @Test
    fun `a partial window that can never grow is sent rather than stranded`() {
        // The live session left three 1s samples behind and normal-mode samples
        // have started landing after them. That run of three can never reach ten,
        // so holding it back would strand it in the buffer permanently.
        val raw = List(3) { sample("fast$it", 1) } + List(2) { sample("slow$it", 10) }
        val (frames, consumed) = Batching.build(raw, mode = "normal")

        assertEquals(3, frames.size)
        assertEquals(5, consumed)
        assertTrue(frames.all { it.resolutionSeconds == 10 })
    }

    @Test
    fun `a backlog larger than one batch is delivered over several pushes`() {
        val raw = List(300) { sample("t$it", 10) }
        val (frames, consumed) = Batching.build(raw, mode = "normal")

        assertEquals(Protocol.MAX_SAMPLES_PER_BATCH, frames.size)
        // Only what this batch covered is reported as consumed, so the rest
        // stays in the buffer instead of being discarded on the ack.
        assertEquals(Protocol.MAX_SAMPLES_PER_BATCH, consumed)
    }

    @Test
    fun `an empty buffer produces nothing to send`() {
        val (frames, consumed) = Batching.build(emptyList(), mode = "normal")
        assertEquals(0, frames.size)
        assertEquals(0, consumed)
    }
}
