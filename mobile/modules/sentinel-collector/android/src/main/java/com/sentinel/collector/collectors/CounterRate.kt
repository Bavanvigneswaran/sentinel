package com.sentinel.collector.collectors

/**
 * Turning monotonic byte/packet counters into per-second rates.
 *
 * Split out of [NetworkCollector] so the rule that matters can be tested
 * without an Android runtime, because it is a Phase 2 invariant rather than an
 * implementation detail: **counters are differentiated on the agent, not the
 * server**, so a reboot or a counter wrap is detected and dropped here instead
 * of drawing a spike on somebody's chart that never happened.
 *
 * A counter that moved backwards means the interval is unknowable — the device
 * rebooted, or the kernel wrapped a 32-bit counter. The honest answer is null
 * for that one interval, with the next sample measured from the new baseline.
 * Reporting the raw difference would draw a huge negative rate; clamping it to
 * zero would claim the network went silent when it did not.
 */
object CounterRate {

    data class Snapshot(val values: Map<String, Long>, val atNanos: Long)

    /**
     * Rates for every key in [current], or all-null when there is no usable
     * previous snapshot. Keys missing from either side come back null rather
     * than being treated as having started at zero.
     */
    fun between(previous: Snapshot?, current: Snapshot): Map<String, Double?> {
        val elapsedSeconds = if (previous == null) 0.0
        else (current.atNanos - previous.atNanos) / 1_000_000_000.0

        val rewound = previous != null && current.values.any { (key, value) ->
            val before = previous.values[key] ?: return@any false
            value < before
        }

        return current.values.mapValues { (key, value) ->
            val before = previous?.values?.get(key)
            if (before == null || rewound || elapsedSeconds <= 0.0) null
            else (value - before) / elapsedSeconds
        }
    }
}
