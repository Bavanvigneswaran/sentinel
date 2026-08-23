package com.sentinel.collector

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Aggregation, against the same rules `agent/sentinel_agent/buffer.py`
 * documents — and, more importantly, against CLAUDE.md's hard rule: a metric
 * the platform never measured must aggregate to "not measured", never to zero.
 */
class AggregationTest {

    private fun systemSample(mem: Double?, cpu: Double? = null, plugged: Boolean? = null) =
        Sample(
            ts = "2026-08-23T10:00:00Z",
            system = fieldsOf(
                "cpu_percent" to cpu,
                "mem_percent" to mem,
                "mem_used_bytes" to 1_000L,
                "battery_plugged" to plugged,
            ),
        )

    @Test
    fun `averages gauges and carries min and max for the spread metrics`() {
        val window = listOf(systemSample(10.0), systemSample(20.0), systemSample(60.0))
        val out = Aggregation.aggregate(window, resolutionSeconds = 10)!!

        assertEquals(30.0, out.system["mem_percent"] as Double, 1e-9)
        assertEquals(10.0, out.system["mem_percent_min"] as Double, 1e-9)
        // The spike inside the window is exactly what alerting and anomaly
        // detection need; a mean alone would erase it.
        assertEquals(60.0, out.system["mem_percent_max"] as Double, 1e-9)
        assertEquals(10, out.resolutionSeconds)
    }

    @Test
    fun `a metric that was never measured stays null, and its min and max too`() {
        val out = Aggregation.aggregate(
            listOf(systemSample(10.0), systemSample(20.0)),
            resolutionSeconds = 10,
        )!!

        // cpu_percent is null on every Android sample by design.
        assertNull(out.system["cpu_percent"])
        assertNull(out.system["cpu_percent_min"])
        assertNull(out.system["cpu_percent_max"])
    }

    @Test
    fun `nulls are skipped rather than counted as zero`() {
        val out = Aggregation.aggregate(
            listOf(systemSample(40.0), systemSample(null), systemSample(60.0)),
            resolutionSeconds = 10,
        )!!

        // 50, not 33.3: the missing sample is absent from the mean, not a zero
        // dragging it down.
        assertEquals(50.0, out.system["mem_percent"] as Double, 1e-9)
    }

    @Test
    fun `booleans carry the latest reading instead of being averaged`() {
        val out = Aggregation.aggregate(
            listOf(systemSample(1.0, plugged = false), systemSample(1.0, plugged = true)),
            resolutionSeconds = 10,
        )!!
        assertEquals(true, out.system["battery_plugged"])
    }

    @Test
    fun `byte counts stay whole numbers`() {
        val out = Aggregation.aggregate(
            listOf(systemSample(1.0), systemSample(1.0)),
            resolutionSeconds = 10,
        )!!
        // Averaging into a float renders as "1000.0 bytes" downstream.
        assertTrue(out.system["mem_used_bytes"] is Long)
    }

    @Test
    fun `per-entity series take the latest capacity and the mean rate`() {
        fun mount(percent: Double, rate: Double) = Sample(
            ts = "t",
            system = fieldsOf(),
            diskUsage = listOf(
                fieldsOf("mount" to "/data", "percent" to percent, "used_bytes" to 5L)
            ),
            net = listOf(fieldsOf("nic" to "device-total", "rx_bytes_per_s" to rate)),
        )

        val out = Aggregation.aggregate(
            listOf(mount(50.0, 100.0), mount(70.0, 300.0)),
            resolutionSeconds = 10,
        )!!

        // Capacity is a level: the newest reading is the truth.
        assertEquals(70.0, out.diskUsage.single()["percent"] as Double, 1e-9)
        // A rate is already differentiated, so the window's mean is right.
        assertEquals(200.0, out.net.single()["rx_bytes_per_s"] as Double, 1e-9)
    }

    @Test
    fun `an empty window aggregates to nothing at all`() {
        assertNull(Aggregation.aggregate(emptyList(), resolutionSeconds = 10))
    }
}
