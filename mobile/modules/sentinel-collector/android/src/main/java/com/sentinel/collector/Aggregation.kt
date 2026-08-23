package com.sentinel.collector

/**
 * Collapsing a window of raw samples into one aggregate, ported field-for-field
 * from `agent/sentinel_agent/buffer.py`'s `aggregate()`.
 *
 * The rules, unchanged from the reference implementation:
 *
 *  * gauges (memory %, load, temperature) — mean over the window
 *  * `mem_percent` additionally carries min and max, because a spike inside the
 *    window is exactly the signal Phase 5's alerting and Phase 6's anomaly
 *    detection need, and a mean would erase it
 *  * rates (bytes/s, packets/s) — mean, the collector having already
 *    differentiated the counters
 *  * per-entity series (mounts, NICs, targets) — latest wins for capacity,
 *    mean for rates
 *
 * `cpu_percent` is in [SPREAD_METRICS] alongside `mem_percent` even though
 * Android never measures it. Dropping it would make this a different function
 * from the one it is a port of, for no gain: with every value null the min/max
 * come out null too, which is the correct answer.
 *
 * NULL is skipped, never coerced to zero, at every step — a window where a
 * metric was never measured aggregates to "not measured", not to 0.
 */
object Aggregation {

    private val SPREAD_METRICS = listOf("cpu_percent", "mem_percent")

    /** Capacity-style fields where the latest reading is the truth, not a mean. */
    private val LATEST_WINS = setOf(
        "total_bytes", "used_bytes", "free_bytes", "percent", "filesystem", "reachable",
    )

    /**
     * Suffixes whose mean is rounded back to a whole number. Averaging a byte
     * count into a float renders as "8589934592.4 bytes".
     */
    private val INTEGER_SUFFIXES = listOf(
        "_bytes", "_seconds", "_users", "_count", "_connections",
    )

    // No `!is Boolean` guard, unlike the Python original: there, `bool` is a
    // subclass of `int` and `battery_plugged` would silently average to 0.5.
    // Kotlin's Boolean is not a Number, so `is Number` already excludes it.
    private fun numeric(values: List<Any?>): List<Double> =
        values.mapNotNull { (it as? Number)?.toDouble() }

    private fun mean(values: List<Double>): Double? =
        if (values.isEmpty()) null else values.sum() / values.size

    fun aggregate(samples: List<Sample>, resolutionSeconds: Int): Sample? {
        if (samples.isEmpty()) return null
        val latest = samples.last()

        val system = Fields()
        for (key in latest.system.keys) {
            val values = samples.map { it.system[key] }
            val nums = numeric(values)
            if (nums.isEmpty()) {
                // Booleans (battery_plugged) and unmeasurable metrics: carry the
                // most recent reading rather than inventing an average of nothing.
                system[key] = latest.system[key]
                continue
            }
            val m = mean(nums)
            system[key] = if (INTEGER_SUFFIXES.any { key.endsWith(it) }) m?.toLong() else m
        }

        for (metric in SPREAD_METRICS) {
            val nums = numeric(samples.map { it.system[metric] })
            system["${metric}_min"] = nums.minOrNull()
            system["${metric}_max"] = nums.maxOrNull()
        }

        return Sample(
            // The window's end, so a sample's timestamp is never in the future.
            ts = latest.ts,
            system = system,
            resolutionSeconds = resolutionSeconds,
            diskUsage = aggregateEntities(samples.map { it.diskUsage }, "mount"),
            diskIo = aggregateEntities(samples.map { it.diskIo }, "disk"),
            net = aggregateEntities(samples.map { it.net }, "nic"),
            latency = aggregateEntities(samples.map { it.latency }, "target"),
            // A top-N list cannot be averaged across a window whose membership
            // changes; the most recent ranking is the only coherent answer.
            // Android sends none at all — see docs/ANDROID_METRICS.md.
            processes = latest.processes,
        )
    }

    private fun aggregateEntities(windows: List<List<Fields>>, key: String): List<Fields> {
        val byEntity = LinkedHashMap<String, MutableList<Fields>>()
        for (entries in windows) {
            for (entry in entries) {
                val id = entry[key] as? String ?: continue
                byEntity.getOrPut(id) { mutableListOf() }.add(entry)
            }
        }

        return byEntity.map { (entity, entries) ->
            val latest = entries.last()
            val merged = fieldsOf(key to entity)
            for (field in latest.keys) {
                if (field == key) continue
                if (field in LATEST_WINS) {
                    merged[field] = latest[field]
                    continue
                }
                val nums = numeric(entries.map { it[field] })
                merged[field] = if (nums.isEmpty()) latest[field] else mean(nums)
            }
            merged
        }
    }
}
