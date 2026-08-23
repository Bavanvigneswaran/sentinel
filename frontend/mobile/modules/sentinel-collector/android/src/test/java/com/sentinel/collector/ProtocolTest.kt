package com.sentinel.collector

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The wire format against `backend/app/schemas/protocol.py`.
 *
 * The first test is the one that matters. `JSONObject.put(key, null)` *removes*
 * the key, and because every optional field in the Pydantic schema defaults to
 * None, an omitted field would validate cleanly and store as NULL — the bug
 * would never surface as an error, only as a frame that has quietly stopped
 * being explicit about what it could not measure. CLAUDE.md's rule is that a
 * platform which cannot measure something reports *null*, not nothing.
 */
class ProtocolTest {

    private val androidSystem = fieldsOf(
        "cpu_percent" to null,
        "mem_percent" to 41.5,
        "battery_percent" to 88.0,
        "battery_plugged" to true,
    )

    @Test
    fun `unmeasurable fields are explicit nulls, not missing keys`() {
        val encoded = Protocol.encodeFields(androidSystem)

        assertTrue("cpu_percent must survive encoding", encoded.has("cpu_percent"))
        assertEquals(JSONObject.NULL, encoded.get("cpu_percent"))
        assertEquals(41.5, encoded.getDouble("mem_percent"), 1e-9)
    }

    @Test
    fun `a sample carries every entity list, empty ones included`() {
        val sample = Sample(
            ts = "2026-08-23T10:00:00Z",
            system = androidSystem,
            diskUsage = listOf(fieldsOf("mount" to "/data", "percent" to 62.0)),
            resolutionSeconds = 10,
        )
        val encoded = Protocol.encodeSample(sample)

        assertEquals("2026-08-23T10:00:00Z", encoded.getString("ts"))
        assertEquals(10, encoded.getInt("resolution_seconds"))
        assertEquals(1, encoded.getJSONArray("disk_usage").length())
        // Android measures none of these. The keys are still present, as empty
        // arrays, so the frame states the absence rather than omitting it.
        assertEquals(0, encoded.getJSONArray("disk_io").length())
        assertEquals(0, encoded.getJSONArray("processes").length())
        assertEquals(0, encoded.getJSONArray("net").length())
    }

    @Test
    fun `hello announces protocol version 1 and the android platform`() {
        val host = fieldsOf(
            "hostname" to "Pixel 9",
            "os" to "Android",
            "agent_version" to "0.1.0",
            "platform" to "android",
            "kernel_version" to null,
        )
        val frame = Protocol.helloFrame(host)

        assertEquals("hello", frame.getString("type"))
        // Android needed no new field, so the version does not move. If this
        // ever fails, PROTOCOL_VERSION moved on one side only.
        assertEquals(1, frame.getInt("protocol_version"))
        assertEquals("android", frame.getJSONObject("host").getString("platform"))
        assertEquals(JSONObject.NULL, frame.getJSONObject("host").get("kernel_version"))
    }

    @Test
    fun `a metrics frame echoes its batch id`() {
        val frame = Protocol.metricsFrame(
            "abc123",
            listOf(Sample(ts = "t", system = androidSystem, resolutionSeconds = 1)),
        )
        assertEquals("metrics", frame.getString("type"))
        assertEquals("abc123", frame.getString("batch_id"))
        assertEquals(1, frame.getJSONArray("samples").length())
    }
}
