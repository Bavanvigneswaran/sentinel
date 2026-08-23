package com.sentinel.collector.collectors

import java.io.File

/**
 * The handful of `/proc` and `/sys` files an app may or may not be allowed to
 * read, depending on OEM, kernel and API level.
 *
 * These are category 2 in docs/ANDROID_METRICS.md — *probed*. A plain file read
 * either succeeds with a true system-wide value or throws, so attempting it is
 * itself the measurement; there is nothing to synthesise either way.
 *
 * What is deliberately absent is `/proc/stat`. Global CPU% is denied to apps
 * from API 26 and there is no replacement, so the collector never opens it
 * rather than trying and falling back — a fallback that fires on every real
 * device but not on an emulator is a code path that never gets tested, and a
 * CPU column that means "readable on this particular kernel" is worse than one
 * that is honestly empty everywhere. See docs/ANDROID_METRICS.md.
 */
object ProcFs {

    private const val LOADAVG = "/proc/loadavg"
    private const val MEMINFO = "/proc/meminfo"
    private const val CPUFREQ = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"

    data class Availability(
        val loadavg: Boolean,
        val meminfo: Boolean,
        val cpufreq: Boolean,
    ) {
        /** Names of the probes that this device refuses, for the status surface. */
        val unavailable: List<String>
            get() = buildList {
                if (!loadavg) add("load average")
                if (!meminfo) add("swap")
                if (!cpufreq) add("CPU frequency")
            }
    }

    private fun read(path: String): String? = try {
        val file = File(path)
        if (file.canRead()) file.readText() else null
    } catch (_: Throwable) {
        // SecurityException, IOException, and on some builds an SELinux denial
        // that surfaces as neither. Unreadable is unreadable.
        null
    }

    fun probe(): Availability = Availability(
        loadavg = loadAverages() != null,
        meminfo = swap() != null,
        cpufreq = cpuFrequencyMhz() != null,
    )

    /** `[load1, load5, load15]`, or null if the kernel will not tell us. */
    fun loadAverages(): List<Double>? = parseLoadAverages(read(LOADAVG))

    internal fun parseLoadAverages(text: String?): List<Double>? {
        val parts = text?.trim()?.split(Regex("\\s+")) ?: return null
        if (parts.size < 3) return null
        return parts.take(3).map { it.toDoubleOrNull() ?: return null }
    }

    data class Swap(val totalBytes: Long, val usedBytes: Long, val percent: Double?)

    /**
     * Swap from `/proc/meminfo`. On Android this is zram rather than a swap
     * partition, which is still swap — pages compressed out of RAM under
     * pressure — and reads the same way on the chart.
     *
     * A device with swap genuinely disabled reports SwapTotal: 0 kB. That is a
     * measurement, not a gap, so it is reported as 0 bytes with a null percent:
     * zero of zero is not 0% full, it is undefined.
     */
    fun swap(): Swap? = parseSwap(read(MEMINFO))

    internal fun parseSwap(text: String?): Swap? {
        if (text == null) return null
        var total: Long? = null
        var free: Long? = null
        for (line in text.lineSequence()) {
            when {
                line.startsWith("SwapTotal:") -> total = kilobytes(line)
                line.startsWith("SwapFree:") -> free = kilobytes(line)
            }
            if (total != null && free != null) break
        }
        val t = total ?: return null
        val f = free ?: return null
        val used = (t - f).coerceAtLeast(0)
        return Swap(
            totalBytes = t,
            usedBytes = used,
            percent = if (t > 0) used * 100.0 / t else null,
        )
    }

    private fun kilobytes(line: String): Long? =
        line.split(Regex("\\s+")).getOrNull(1)?.toLongOrNull()?.times(1024)

    /**
     * The lowest reading that can be a real CPU clock, in kHz — 100 MHz.
     *
     * `scaling_cur_freq` is not always a frequency. The Android emulator's is a
     * stub that returns single digits: a literal `2`, which divides into
     * 0.002 MHz and reaches the database looking like a plausible float. That
     * is the same bug class CLAUDE.md already records for the desktop agent,
     * where psutil reports `cpu_freq` as `4` on Apple Silicon — an API that
     * answers with something other than what it claims to answer with.
     *
     * A readable file is not the same as a real measurement, so the value is
     * bounded as well as read. Anything under 100 MHz is a stub, not a CPU that
     * is running that slowly, and it is reported as unmeasurable.
     */
    private const val MIN_PLAUSIBLE_KHZ = 100_000L

    /** Current core-0 frequency in MHz. `scaling_cur_freq` is in kHz. */
    fun cpuFrequencyMhz(): Double? = parseCpuFrequencyMhz(read(CPUFREQ))

    internal fun parseCpuFrequencyMhz(text: String?): Double? {
        val khz = text?.trim()?.toLongOrNull() ?: return null
        return if (khz >= MIN_PLAUSIBLE_KHZ) khz / 1000.0 else null
    }
}
