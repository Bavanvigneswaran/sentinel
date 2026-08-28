package com.sentinel.collector

/**
 * How the sample backlog is described where a person can see it.
 *
 * A bare "400 buffered" reads as healthy — a number going up looks like a
 * collector that is keeping its data. It is not: [SampleBuffer] is bounded, and
 * at the cap the oldest sample is *permanently discarded* on every new one.
 * Those two states look identical in a plain count and are completely different
 * facts, so the phrasing has to separate them.
 *
 * Found on a real phone: screen-off and idle for far longer than the buffer's
 * ~66 minutes of headroom, Doze cut network access despite the wake lock (a
 * limit [CollectorService] already documents the wake lock does not cover), the
 * buffer sat at exactly 400, history was being dropped — and the notification
 * said "400 buffered", which was read as fine.
 *
 * Loss is reported *after* it stops too. A backlog that drains looks like a
 * full recovery, and the samples that fell off the front are still gone; the
 * charts will have a hole in them that nothing else explains.
 *
 * Pure, so the wording is pinned by a JVM test. The React Native side phrases
 * the same three facts for the collector screen — it cannot import this, so
 * both surfaces render `buffered`, `capacity` and `dropped` rather than one of
 * them inventing a number the other does not have.
 */
object BufferPressure {

    /**
     * At or above this fraction of capacity, say how close the cap is instead
     * of only how much is waiting. The warning is worth having before data is
     * lost, not only once it already has been.
     */
    const val NEAR_FULL_FRACTION = 0.9

    /** The backlog phrase for the persistent notification, or null when there
     *  is nothing waiting and nothing has been lost this run. */
    fun describe(buffered: Int, capacity: Int, dropped: Int): String? {
        val lost = if (dropped > 0) " ($dropped lost)" else ""
        return when {
            capacity > 0 && buffered >= capacity ->
                "$buffered buffered — full, dropping oldest$lost"
            capacity > 0 && buffered >= capacity * NEAR_FULL_FRACTION ->
                "$buffered of $capacity buffered — nearly full$lost"
            buffered > 0 -> "$buffered buffered$lost"
            dropped > 0 -> "$dropped sample${plural(dropped)} lost while the buffer was full"
            else -> null
        }
    }

    private fun plural(count: Int) = if (count == 1) "" else "s"
}
