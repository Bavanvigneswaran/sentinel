package com.sentinel.collector

import org.json.JSONArray
import org.json.JSONObject

/**
 * The wire format, against `backend/app/schemas/protocol.py`.
 *
 * That module is the contract and it is imported by both the server and the
 * Python agent; this is the third implementation of it and the only one that
 * cannot import it, so every constant below is mirrored deliberately and the
 * unit tests assert the shape rather than trusting the mirror.
 *
 * PROTOCOL_VERSION stays at 1. Android needed no new field: everything it can
 * measure already has a home in `SystemSample`, and everything it cannot is
 * already nullable. See docs/ANDROID_METRICS.md for the one field that was
 * considered and deliberately not added (thermal status).
 */
object Protocol {
    const val VERSION = 1

    /** Mirrors MAX_SAMPLES_PER_BATCH; a longer backlog goes over several pushes. */
    const val MAX_SAMPLES_PER_BATCH = 240

    /**
     * `JSONObject.put(key, null)` *removes* the key. That is the single most
     * dangerous line of Android JSON in this whole module, because an omitted
     * field and a null field are the same thing to Pydantic's defaults today —
     * so the bug would not show up as an error, it would show up as a metric
     * that silently stops being explicit about being unmeasurable.
     *
     * Everything goes through here, and a missing value is written as an
     * explicit JSON `null`. CLAUDE.md: never omitted-and-defaulted.
     */
    fun put(target: JSONObject, key: String, value: Any?): JSONObject =
        target.put(key, value ?: JSONObject.NULL)

    fun encodeFields(fields: Fields): JSONObject {
        val out = JSONObject()
        for ((key, value) in fields) put(out, key, value)
        return out
    }

    private fun encodeEntities(entries: List<Fields>): JSONArray {
        val array = JSONArray()
        entries.forEach { array.put(encodeFields(it)) }
        return array
    }

    fun encodeSample(sample: Sample): JSONObject {
        val out = JSONObject()
        out.put("ts", sample.ts)
        out.put("resolution_seconds", sample.resolutionSeconds ?: 1)
        out.put("system", encodeFields(sample.system))
        out.put("disk_usage", encodeEntities(sample.diskUsage))
        out.put("disk_io", encodeEntities(sample.diskIo))
        out.put("net", encodeEntities(sample.net))
        out.put("latency", encodeEntities(sample.latency))
        out.put("processes", encodeEntities(sample.processes))
        return out
    }

    fun helloFrame(host: Fields): JSONObject = JSONObject()
        .put("type", "hello")
        .put("protocol_version", VERSION)
        .put("host", encodeFields(host))

    fun metricsFrame(batchId: String, samples: List<Sample>): JSONObject {
        val array = JSONArray()
        samples.forEach { array.put(encodeSample(it)) }
        return JSONObject()
            .put("type", "metrics")
            .put("batch_id", batchId)
            .put("samples", array)
    }

    fun pongFrame(): JSONObject = JSONObject().put("type", "pong")
}
