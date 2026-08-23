package com.sentinel.collector.collectors

import android.net.TrafficStats
import com.sentinel.collector.Fields
import com.sentinel.collector.fieldsOf

/**
 * Device-wide network throughput from `TrafficStats`.
 *
 * **One entity, named `device-total`, not a NIC.** `TrafficStats`' totals are
 * the honest permission-free source but they are not broken down per interface.
 * Calling them `wlan0` would claim traffic went over Wi-Fi when it may have
 * gone over cellular; per-interface `TrafficStats.getRxBytes(iface)` is not
 * used because enumerating interface names is itself restricted from API 30,
 * which puts us back to guessing. `device-total` describes exactly what was
 * counted.
 *
 * Differentiation — and the reboot/wrap rule that goes with it — lives in
 * [CounterRate], which is where it is tested.
 */
class NetworkCollector {

    private var previous: CounterRate.Snapshot? = null

    private fun readCounters(nowNanos: Long): CounterRate.Snapshot? {
        val rxBytes = TrafficStats.getTotalRxBytes()
        val txBytes = TrafficStats.getTotalTxBytes()
        // UNSUPPORTED (-1) on a device with no usable counters. That is "we
        // cannot measure this", not "no traffic".
        val unsupported = TrafficStats.UNSUPPORTED.toLong()
        if (rxBytes == unsupported || txBytes == unsupported) return null

        return CounterRate.Snapshot(
            values = mapOf(
                "rx_bytes_per_s" to rxBytes,
                "tx_bytes_per_s" to txBytes,
                "rx_packets_per_s" to TrafficStats.getTotalRxPackets(),
                "tx_packets_per_s" to TrafficStats.getTotalTxPackets(),
            ),
            atNanos = nowNanos,
        )
    }

    fun read(nowNanos: Long): List<Fields> {
        val current = readCounters(nowNanos) ?: return emptyList()
        val rates = CounterRate.between(previous, current)
        previous = current

        return listOf(
            fieldsOf(
                "nic" to "device-total",
                "rx_bytes_per_s" to rates["rx_bytes_per_s"],
                "tx_bytes_per_s" to rates["tx_bytes_per_s"],
                "rx_packets_per_s" to rates["rx_packets_per_s"],
                "tx_packets_per_s" to rates["tx_packets_per_s"],
                // Per-interface error and drop counters live in /proc/net, which
                // is denied to apps from API 28. Not zero — unmeasurable.
                "errors_in_per_s" to null,
                "errors_out_per_s" to null,
                "drops_in_per_s" to null,
                "drops_out_per_s" to null,
            )
        )
    }
}
