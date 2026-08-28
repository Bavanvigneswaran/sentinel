package com.sentinel.collector

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * A sample that costs more than its own interval must say so, mirroring
 * `agent/tests/test_sample_overrun.py`.
 *
 * The collector connects, handshakes and pushes exactly as a healthy one does;
 * the only difference is that its samples are stamped further apart than the
 * resolution they claim, and the loop's own drift correction is what hides it —
 * it stops waiting and carries on. On a phone the usual cause is thermal: live
 * monitoring is a 1s cadence with the screen on and a wake lock held, and a
 * throttled phone slows the sampling itself. The chart goes static, and without
 * this there is nothing in the log that explains why.
 */
class SampleOverrunTest {

    private val overrun = SampleOverrun()

    @Test
    fun `a sample inside its budget says nothing`() {
        assertNull(overrun.warningFor(elapsedMs = 900, budgetSeconds = 1, nowMs = 1_000))
    }

    @Test
    fun `an overrunning sample warns with both numbers`() {
        val message = overrun.warningFor(elapsedMs = 2_500, budgetSeconds = 1, nowMs = 1_000)

        assertNotNull(message)
        // The measured cost and the budget it missed, not just "slow": the
        // whole point is to be actionable on a phone nobody can attach a
        // profiler to.
        assertTrue(message!!, message.contains("2500ms"))
        assertTrue(message, message.contains("1000ms"))
    }

    @Test
    fun `the thermal status is named when the platform reports one`() {
        val message = overrun.warningFor(
            elapsedMs = 2_500,
            budgetSeconds = 1,
            nowMs = 1_000,
            thermal = { "moderate throttling" },
        )

        // "Slow" and "slow because it is hot" are different diagnoses, and the
        // second is not derivable from the timing.
        assertTrue(message!!, message.contains("moderate throttling"))
    }

    @Test
    fun `the thermal status is not read on a sample that keeps time`() {
        var reads = 0

        overrun.warningFor(elapsedMs = 900, budgetSeconds = 1, nowMs = 1_000) {
            reads++
            "light throttling"
        }

        // thermalStatusLabel() is a system-service call on a path that runs at
        // 1Hz in live mode, for a line that is emitted at most once a minute.
        assertEquals(0, reads)
    }

    @Test
    fun `a sustained overrun warns once a minute, not once a sample`() {
        var now = 1_000L
        var warnings = 0

        // Thirty consecutive slow samples over thirty seconds: one line.
        repeat(30) {
            if (overrun.warningFor(2_500, budgetSeconds = 1, nowMs = now) != null) warnings++
            now += 1_000
        }
        assertEquals(1, warnings)

        now += SampleOverrun.WARNING_INTERVAL_MS
        assertNotNull(overrun.warningFor(2_500, budgetSeconds = 1, nowMs = now))
    }

    @Test
    fun `recovering re-arms the warning`() {
        // A phone that goes slow, recovers, and goes slow again is two events.
        // Without the reset the second episode would be silent for up to a
        // minute — and a burst of slow samples that clears on its own is
        // exactly what somebody watching a chart wants corroborated.
        assertNotNull(overrun.warningFor(2_500, budgetSeconds = 1, nowMs = 1_000))
        assertNull(overrun.warningFor(200, budgetSeconds = 1, nowMs = 2_000))
        assertNotNull(overrun.warningFor(2_500, budgetSeconds = 1, nowMs = 3_000))
    }

    @Test
    fun `the budget follows the cadence the loop is actually running at`() {
        // 2.5s is fine at the 10s normal cadence and a serious overrun at the
        // 1s live one, which is the cadence where this actually bites.
        assertNull(overrun.warningFor(2_500, budgetSeconds = 10, nowMs = 1_000))
        assertNotNull(overrun.warningFor(2_500, budgetSeconds = 1, nowMs = 2_000))
    }
}
