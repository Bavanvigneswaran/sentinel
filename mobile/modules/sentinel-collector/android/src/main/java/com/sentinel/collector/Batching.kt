package com.sentinel.collector

/**
 * Turning a ring buffer into the frames of one push, split out of the engine so
 * it can be tested without an Android runtime — the same reason
 * `agent/sentinel_agent/buffer.py` keeps `aggregate()` out of `runner.py`.
 *
 * The one thing this does that the desktop agent does not have to: it groups
 * the buffer into runs of equal *stamped* resolution before doing anything
 * else. The Python agent samples at 1s always, so `resolution_seconds` is a
 * constant there. A phone's cadence changes with the mode, so a 10s sample
 * collected before a live upshift must not be relabelled a 1s point merely
 * because it happens to be pushed during live mode — that would claim a
 * resolution the reading never had, which is the same class of lie as
 * synthesising a value.
 */
object Batching {

    /**
     * Returns the frames to send and how many raw samples they consumed.
     *
     * The consumed count is what gets discarded once the server acks, so a
     * backlog larger than one batch is delivered over several pushes rather
     * than thrown away.
     */
    fun build(
        raw: List<Sample>,
        mode: String,
        pushIntervalSeconds: Int = CollectorConfig.DEFAULT_PUSH_SECONDS,
        maxSamples: Int = Protocol.MAX_SAMPLES_PER_BATCH,
    ): Pair<List<Sample>, Int> {
        val frames = mutableListOf<Sample>()
        var consumed = 0
        var index = 0

        while (index < raw.size && frames.size < maxSamples) {
            val resolution = resolutionOf(raw[index])
            var end = index
            while (end < raw.size && resolutionOf(raw[end]) == resolution) end++
            val run = raw.subList(index, end)

            // A run that reaches the end of the buffer may still grow: the next
            // sample at this cadence has not been taken yet. A run followed by
            // samples of a *different* resolution never will, so nothing may be
            // held back from it or those samples would sit here forever.
            val runMayGrow = end == raw.size

            // A sample already covering a full push interval is its own
            // aggregate, whatever mode we are in.
            if (resolution >= pushIntervalSeconds || mode == "live") {
                for (sample in run) {
                    if (frames.size >= maxSamples) return frames to consumed
                    frames.add(sample)
                    consumed++
                }
                index = end
                continue
            }

            // 1s samples left over from a live session, being pushed in normal
            // mode: collapse them into push-interval windows. Collapsing the
            // whole run into one row would claim resolution_seconds=10 for a
            // sample that actually spans the entire live session.
            var start = 0
            var heldBack = false
            while (start < run.size) {
                if (frames.size >= maxSamples) return frames to consumed
                val chunk = run.subList(start, minOf(start + pushIntervalSeconds, run.size))
                // Hold back a partial trailing window, or every push emits a
                // short, unrepresentative aggregate. The `start > 0` half is
                // what stops the buffer stalling: a run that is *only* a partial
                // window has to go out, or it would never be sent at all.
                if (chunk.size < pushIntervalSeconds && start > 0 && runMayGrow) {
                    heldBack = true
                    break
                }
                val collapsed = Aggregation.aggregate(chunk, pushIntervalSeconds) ?: break
                frames.add(collapsed)
                consumed += chunk.size
                start += chunk.size
            }
            if (start == 0) break
            index += start
            if (heldBack) return frames to consumed
        }
        return frames to consumed
    }

    private fun resolutionOf(sample: Sample): Int =
        sample.resolutionSeconds ?: CollectorConfig.NORMAL_SAMPLE_SECONDS
}
