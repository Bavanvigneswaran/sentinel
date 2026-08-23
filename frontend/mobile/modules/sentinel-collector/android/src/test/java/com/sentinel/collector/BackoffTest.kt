package com.sentinel.collector

import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.random.Random

/** Reconnect backoff, matching `agent/sentinel_agent/transport/client.py`. */
class BackoffTest {

    @Test
    fun `grows exponentially and is capped`() {
        // random=0 removes the jitter, leaving the base curve.
        val zeroJitter = Random(0)
        val noJitter = object : Random() {
            override fun nextBits(bitCount: Int) = zeroJitter.nextBits(bitCount)
            override fun nextDouble() = 0.0
        }
        assertTrue(Backoff.nextSeconds(0, noJitter) == 1.0)
        assertTrue(Backoff.nextSeconds(3, noJitter) == 8.0)
        // Capped, or a phone that was offline overnight would wait hours.
        assertTrue(Backoff.nextSeconds(20, noJitter) == Backoff.MAX_SECONDS)
    }

    @Test
    fun `always applies some jitter downward and never returns zero`() {
        val random = Random(42)
        repeat(200) {
            val delay = Backoff.nextSeconds(5, random)
            val base = minOf(1.0 * Math.pow(2.0, 5.0), Backoff.MAX_SECONDS)
            // Full jitter: somewhere in (base/2, base]. Without it a fleet of
            // phones coming out of the same tunnel reconnects in lockstep.
            assertTrue(delay > base * (1 - Backoff.JITTER) - 1e-9)
            assertTrue(delay <= base)
        }
    }
}
