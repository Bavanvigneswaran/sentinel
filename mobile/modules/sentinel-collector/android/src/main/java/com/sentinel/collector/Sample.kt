package com.sentinel.collector

/**
 * One point in time for this device, in the shape `app/schemas/protocol.py`'s
 * `Sample` expects.
 *
 * Untyped field maps rather than a data class per metric group, deliberately:
 * this is a port of the Python agent's `buffer.py`, which aggregates over
 * dicts, and keeping the same shape keeps the aggregation code recognisable
 * against its reference implementation. The typed, checked part of the system
 * is the collectors that *produce* these maps and the Pydantic schema that
 * validates them on arrival.
 *
 * `LinkedHashMap` and not `Map` because field order is preserved, which makes a
 * frame captured off the wire readable next to the schema that defines it.
 */
typealias Fields = LinkedHashMap<String, Any?>

fun fieldsOf(vararg pairs: Pair<String, Any?>): Fields = LinkedHashMap<String, Any?>().apply {
    pairs.forEach { (key, value) -> put(key, value) }
}

data class Sample(
    /** ISO-8601 UTC, generated here; the server records its own receive time. */
    val ts: String,
    val system: Fields,
    /**
     * The window this sample describes: 10 for a normal-mode aggregate, 1 for a
     * live-mode raw sample. Null while the sample is still sitting raw in the
     * ring buffer — it only becomes true of a sample once it has been either
     * aggregated into a window or claimed as a 1s point at push time, and the
     * protocol accepts nothing but 1 or 10.
     */
    val resolutionSeconds: Int? = null,
    val diskUsage: List<Fields> = emptyList(),
    val diskIo: List<Fields> = emptyList(),
    val net: List<Fields> = emptyList(),
    val latency: List<Fields> = emptyList(),
    val processes: List<Fields> = emptyList(),
)
