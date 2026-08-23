package com.sentinel.collector

import kotlin.math.min
import kotlin.random.Random

/**
 * Reconnect backoff, ported from `agent/sentinel_agent/transport/client.py`.
 *
 * The jitter is the point. Without it every agent that lost the same backend
 * retries in lockstep and knocks it back over the moment it recovers — and a
 * phone fleet is worse than a server fleet here, because phones come back from
 * a tunnel or a flaky cell handover all at once.
 */
object Backoff {
    const val INITIAL_SECONDS = 1.0
    const val MAX_SECONDS = 60.0
    const val FACTOR = 2.0

    /** Full jitter, same as the Python agent. */
    const val JITTER = 0.5

    fun nextSeconds(attempt: Int, random: Random = Random.Default): Double {
        val base = min(INITIAL_SECONDS * Math.pow(FACTOR, attempt.toDouble()), MAX_SECONDS)
        return base * (1 - JITTER * random.nextDouble())
    }
}
