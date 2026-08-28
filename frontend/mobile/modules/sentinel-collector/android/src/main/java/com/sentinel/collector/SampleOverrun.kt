package com.sentinel.collector

/**
 * Say so when a sample costs more than the interval it is taken on — the
 * Kotlin counterpart of `agent/sentinel_agent/runner.py`'s
 * `_warn_if_overrunning`.
 *
 * This is the one failure mode that is invisible from every other vantage
 * point. A collector whose sample takes longer than its cadence still runs,
 * still handshakes and still pushes; it simply stamps its samples further apart
 * than the resolution they claim, and the only place that surfaces is a live
 * chart that advances in jumps — which reads as a slow network, or a server
 * problem, or a bug in the chart, and is none of them.
 *
 * A phone has a cause the desktop agent does not: live monitoring holds a wake
 * lock with the screen on at a 1s cadence, which is sustained thermal load, and
 * a throttled phone slows the sampling loop itself. So the warning carries the
 * thermal status when the platform reports one — the difference between "this
 * phone is slow" and "this phone is slow *because* it is hot" is the whole
 * diagnosis, and it is not derivable from the timing alone.
 *
 * Throttled to one line a minute after the first: a collector that is slow is
 * slow on every sample, and at the 1s live cadence a warning per sample would
 * bury the log it exists to make readable.
 *
 * Pure and clock-injected — no Android imports — so the throttling can be
 * tested on the JVM without an emulator, the same reason `Batching` and
 * `Backoff` are separate from the engine that drives them. The caller does the
 * logging.
 */
class SampleOverrun {

    /** Null while sampling keeps time; the stamp of the first sample of the
     *  current run of overruns otherwise. */
    private var overrunSinceMs: Long? = null
    private var lastWarningMs = 0L

    /**
     * The line to log for this sample, or null if there is nothing to say.
     *
     * [thermal] is a lambda rather than a value because it is only read when a
     * warning is actually going to be emitted: `thermalStatusLabel()` is a
     * system-service call, and this is on the sampling path at up to 1Hz for
     * a warning that fires at most once a minute.
     */
    fun warningFor(
        elapsedMs: Long,
        budgetSeconds: Int,
        nowMs: Long,
        thermal: () -> String? = { null },
    ): String? {
        val budgetMs = budgetSeconds * 1000L
        if (elapsedMs <= budgetMs) {
            // Back inside budget: re-arm, so a second episode is reported when
            // it happens rather than being silent for up to a minute. A burst
            // of slow samples that clears on its own is exactly the thing
            // somebody watching a chart wants the log to corroborate.
            overrunSinceMs = null
            return null
        }
        if (overrunSinceMs == null) {
            overrunSinceMs = nowMs
            lastWarningMs = nowMs
        } else if (nowMs - lastWarningMs < WARNING_INTERVAL_MS) {
            return null
        } else {
            lastWarningMs = nowMs
        }

        val label = thermal()
        val cause = if (label == null) "" else " The phone reports $label."
        return "a sample took ${elapsedMs}ms, longer than the ${budgetMs}ms budget — " +
            "this phone's samples are further apart than ${budgetSeconds}s and its live " +
            "charts will advance in jumps.$cause"
    }

    companion object {
        /**
         * How long a sustained overrun goes between lines. The first overrun of
         * a run is logged immediately — a phone that cannot keep its budget
         * cannot keep it once, so the point is to be visible without filling
         * the log. Matches `runner.py`'s OVERRUN_WARNING_INTERVAL_SECONDS.
         */
        const val WARNING_INTERVAL_MS = 60_000L
    }
}
