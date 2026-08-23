package com.sentinel.collector.collectors

import com.sentinel.collector.Fields
import com.sentinel.collector.fieldsOf
import java.net.InetSocketAddress
import java.net.Socket

/**
 * Round-trip latency, the same technique as the Python agent's
 * `collectors/latency.py`: a TCP handshake to a known-open port, not ICMP.
 * Raw sockets need root on every platform and Android will never grant it.
 *
 * An unreachable target reports `reachable=false` with a **null** RTT — never
 * zero, which would draw as a perfect connection on every chart.
 *
 * This is the collector that gives an Android device a `packet_loss_percent`,
 * which is what earns it the health score's `network` component. Without it a
 * phone would be scored on memory and disk alone.
 */
class LatencyCollector(private val targets: List<Target>) {

    data class Target(val host: String, val port: Int = 443) {
        val label: String get() = "$host:$port"

        companion object {
            /** Accepts "example.com" or "example.com:8443". */
            fun parse(raw: String): Target {
                val index = raw.lastIndexOf(':')
                if (index <= 0) return Target(raw)
                val port = raw.substring(index + 1).toIntOrNull() ?: return Target(raw)
                return Target(raw.substring(0, index), port)
            }
        }
    }

    /** One TCP handshake. Milliseconds, or null if it did not connect. */
    private fun probe(target: Target, timeoutMs: Int): Double? {
        val started = System.nanoTime()
        return try {
            Socket().use { socket ->
                socket.connect(InetSocketAddress(target.host, target.port), timeoutMs)
                (System.nanoTime() - started) / 1_000_000.0
            }
        } catch (_: Throwable) {
            null
        }
    }

    fun measure(probes: Int = PROBES, timeoutMs: Int = TIMEOUT_MS): List<Fields> =
        targets.map { target ->
            val results = (0 until probes).map { probe(target, timeoutMs) }
            val ok = results.filterNotNull()
            val loss = 100.0 * (results.size - ok.size) / results.size

            if (ok.isEmpty()) {
                fieldsOf(
                    "target" to target.label,
                    "reachable" to false,
                    "rtt_ms_avg" to null,
                    "rtt_ms_min" to null,
                    "rtt_ms_max" to null,
                    "packet_loss_percent" to 100.0,
                )
            } else {
                fieldsOf(
                    "target" to target.label,
                    "reachable" to true,
                    "rtt_ms_avg" to ok.average(),
                    "rtt_ms_min" to ok.min(),
                    "rtt_ms_max" to ok.max(),
                    "packet_loss_percent" to loss,
                )
            }
        }

    companion object {
        const val PROBES = 3
        const val TIMEOUT_MS = 2000
    }
}
