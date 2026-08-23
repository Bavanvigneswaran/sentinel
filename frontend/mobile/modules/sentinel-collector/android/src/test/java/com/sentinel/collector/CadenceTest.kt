package com.sentinel.collector

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Which cadence the sample loop should run at, given the user's preference and
 * the server's mode.
 *
 * The rule this pins down: high-frequency is a *floor* on resolution, not a
 * ceiling. Live mode must never be slowed by it, and turning it off while a
 * viewer is watching must not drop that viewer back to 10s mid-session.
 */
class CadenceTest {

    /** Mirrors CollectorEngine.applyMode's choice, which needs a Context to
     *  exercise directly. */
    private fun wanted(mode: String, highFrequency: Boolean): Int =
        if (mode == "live") {
            CollectorConfig.LIVE_SAMPLE_SECONDS
        } else if (highFrequency) {
            CollectorConfig.LIVE_SAMPLE_SECONDS
        } else {
            CollectorConfig.NORMAL_SAMPLE_SECONDS
        }

    @Test
    fun `normal mode uses the slow cadence by default`() {
        assertEquals(10, wanted("normal", highFrequency = false))
    }

    @Test
    fun `high frequency makes normal mode sample every second`() {
        assertEquals(1, wanted("normal", highFrequency = true))
    }

    @Test
    fun `live mode is one second regardless of the preference`() {
        assertEquals(1, wanted("live", highFrequency = false))
        assertEquals(1, wanted("live", highFrequency = true))
    }

    @Test
    fun `the preference never slows live monitoring down`() {
        // The failure this guards: reading the preference *instead of* the
        // mode would drop a watching viewer to 10s the moment the user turned
        // high-frequency off.
        assertEquals(
            CollectorConfig.LIVE_SAMPLE_SECONDS,
            wanted("live", highFrequency = false),
        )
    }
}
