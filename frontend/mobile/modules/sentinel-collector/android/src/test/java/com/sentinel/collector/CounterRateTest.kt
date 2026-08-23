package com.sentinel.collector

import com.sentinel.collector.collectors.CounterRate
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Counter differentiation — a Phase 2 invariant, not an implementation detail:
 * counters are differentiated on the agent so a reboot or a wrap is detected
 * and dropped, instead of drawing a spike that never happened.
 */
class CounterRateTest {

    private val second = 1_000_000_000L

    private fun snapshot(rx: Long, tx: Long, atSeconds: Long) =
        CounterRate.Snapshot(mapOf("rx" to rx, "tx" to tx), atSeconds * second)

    @Test
    fun `the first sample has no rate at all`() {
        val rates = CounterRate.between(null, snapshot(1_000, 500, 10))
        // Null, not zero: one reading of a counter says nothing about a rate.
        assertNull(rates["rx"])
        assertNull(rates["tx"])
    }

    @Test
    fun `divides the delta by the real elapsed time`() {
        val rates = CounterRate.between(snapshot(1_000, 500, 10), snapshot(3_000, 900, 20))
        assertEquals(200.0, rates["rx"]!!, 1e-9)
        assertEquals(40.0, rates["tx"]!!, 1e-9)
    }

    @Test
    fun `a counter that moved backwards yields null for that interval`() {
        // What a reboot looks like: the counters restart from near zero.
        val rates = CounterRate.between(snapshot(9_000, 8_000, 10), snapshot(120, 40, 20))
        assertNull(rates["rx"])
        assertNull(rates["tx"])
    }

    @Test
    fun `one rewound counter invalidates the whole sample, not just its own key`() {
        // rx kept climbing, tx wrapped. They came from the same reboot, so rx's
        // "delta" spans an unknown amount of pre-reboot time too.
        val rates = CounterRate.between(snapshot(1_000, 8_000, 10), snapshot(2_000, 40, 20))
        assertNull(rates["rx"])
        assertNull(rates["tx"])
    }

    @Test
    fun `two samples at the same instant produce no rate`() {
        val rates = CounterRate.between(snapshot(1_000, 500, 10), snapshot(2_000, 900, 10))
        // Dividing by zero elapsed time would be an infinite rate.
        assertNull(rates["rx"])
    }
}
