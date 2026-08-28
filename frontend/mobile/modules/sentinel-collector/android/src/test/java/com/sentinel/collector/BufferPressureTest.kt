package com.sentinel.collector

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The backlog has to read as what it is.
 *
 * "400 buffered" and "12 buffered" look like the same kind of fact — a number
 * that went up. They are not: the first is a buffer at its cap discarding the
 * oldest sample on every new one, and the second is a delay that will resolve
 * itself. The real incident this comes from was read as healthy.
 */
class BufferPressureTest {

    private val capacity = CollectorConfig.BUFFER_SIZE

    @Test
    fun `an empty buffer with nothing lost says nothing at all`() {
        assertNull(BufferPressure.describe(buffered = 0, capacity = capacity, dropped = 0))
    }

    @Test
    fun `an ordinary backlog is just a count`() {
        assertEquals("12 buffered", BufferPressure.describe(12, capacity, dropped = 0))
    }

    @Test
    fun `a full buffer says it is losing data, not just how much it holds`() {
        val text = BufferPressure.describe(capacity, capacity, dropped = 57)

        assertTrue(text!!, text.contains("full"))
        assertTrue(text, text.contains("dropping oldest"))
        assertTrue(text, text.contains("57"))
    }

    @Test
    fun `the cap is named before it is reached, not only after`() {
        // The warning is worth having while there is still something to do
        // about it — the exemption to ask for, the phone to wake.
        val text = BufferPressure.describe((capacity * 0.95).toInt(), capacity, dropped = 0)

        assertTrue(text!!, text.contains("nearly full"))
        assertTrue(text, text.contains("$capacity"))
    }

    @Test
    fun `approaching is not the same as arriving`() {
        val text = BufferPressure.describe((capacity * 0.5).toInt(), capacity, dropped = 0)

        assertEquals("200 buffered", text)
    }

    @Test
    fun `loss is still reported once the backlog has drained`() {
        // A buffer that empties looks like a full recovery. The samples that
        // fell off the front are still gone, and the charts will have a hole
        // in them that nothing else on this screen explains.
        val text = BufferPressure.describe(buffered = 0, capacity = capacity, dropped = 57)

        assertTrue(text!!, text.contains("57 samples lost"))
    }

    @Test
    fun `one lost sample is not reported as one samples`() {
        val text = BufferPressure.describe(buffered = 0, capacity = capacity, dropped = 1)

        assertEquals("1 sample lost while the buffer was full", text)
    }
}
