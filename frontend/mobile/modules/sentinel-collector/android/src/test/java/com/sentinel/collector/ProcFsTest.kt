package com.sentinel.collector

import com.sentinel.collector.collectors.ProcFs
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * The probed `/proc` and `/sys` readings — category 2 in
 * docs/ANDROID_METRICS.md.
 *
 * The CPU-frequency cases are the reason this file exists. A readable file is
 * not the same as a real measurement: the Android emulator's `scaling_cur_freq`
 * is a stub that returns a literal `2`, which divides cleanly into 0.002 MHz
 * and reaches the database looking like a perfectly ordinary float. It was
 * caught by reading the row back after the first real push, not by any type
 * checker — the same way CLAUDE.md's psutil `cpu_freq == 4` on Apple Silicon
 * was found.
 */
class ProcFsTest {

    @Test
    fun `parses a real load average line`() {
        val load = ProcFs.parseLoadAverages("0.18 0.24 0.33 1/2712 4546")
        assertEquals(listOf(0.18, 0.24, 0.33), load)
    }

    @Test
    fun `an unreadable loadavg is null, not zeroes`() {
        // SELinux denies this on plenty of builds. Nobody's phone is idle at
        // exactly 0.00 just because we could not look.
        assertNull(ProcFs.parseLoadAverages(null))
        assertNull(ProcFs.parseLoadAverages("garbage"))
    }

    @Test
    fun `parses zram out of meminfo`() {
        val swap = ProcFs.parseSwap(
            """
            MemTotal:        2532040 kB
            MemFree:          143120 kB
            SwapTotal:       1898896 kB
            SwapFree:         995328 kB
            """.trimIndent()
        )!!
        assertEquals(1_898_896L * 1024, swap.totalBytes)
        assertEquals((1_898_896L - 995_328L) * 1024, swap.usedBytes)
        assertEquals(47.58, swap.percent!!, 0.01)
    }

    @Test
    fun `swap that is genuinely disabled is zero bytes with an undefined percent`() {
        val swap = ProcFs.parseSwap("SwapTotal:             0 kB\nSwapFree:              0 kB")!!
        assertEquals(0L, swap.totalBytes)
        // Zero of zero is not "0% full", it is undefined — and a chart showing
        // a healthy 0% would be a claim we cannot support.
        assertNull(swap.percent)
    }

    @Test
    fun `meminfo without swap lines reports nothing rather than zero`() {
        assertNull(ProcFs.parseSwap("MemTotal: 2532040 kB"))
        assertNull(ProcFs.parseSwap(null))
    }

    @Test
    fun `a real cpu frequency is converted from kHz to MHz`() {
        assertEquals(2_400.0, ProcFs.parseCpuFrequencyMhz("2400000")!!, 1e-9)
        assertEquals(300.0, ProcFs.parseCpuFrequencyMhz(" 300000 \n")!!, 1e-9)
    }

    @Test
    fun `the emulator's stub frequency is rejected rather than reported as 0 point 002 MHz`() {
        // The exact value the Android emulator returns. It is readable, it
        // parses, and it is not a frequency.
        assertNull(ProcFs.parseCpuFrequencyMhz("2"))
        assertNull(ProcFs.parseCpuFrequencyMhz("1"))
        assertNull(ProcFs.parseCpuFrequencyMhz("0"))
    }

    @Test
    fun `an unreadable cpufreq is null`() {
        assertNull(ProcFs.parseCpuFrequencyMhz(null))
        assertNull(ProcFs.parseCpuFrequencyMhz("unknown"))
    }
}
