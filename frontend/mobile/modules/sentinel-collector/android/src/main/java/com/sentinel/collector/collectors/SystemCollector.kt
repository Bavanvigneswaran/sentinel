package com.sentinel.collector.collectors

import android.content.Context
import android.os.Build
import android.os.SystemClock
import android.provider.Settings
import com.sentinel.collector.BuildConfig
import com.sentinel.collector.Fields
import com.sentinel.collector.Sample
import com.sentinel.collector.fieldsOf
import java.time.Instant

/**
 * Assembles one [Sample] in the shape `app/schemas/protocol.py`'s `SystemSample`
 * expects.
 *
 * **Every field of the schema appears below, including the ones Android cannot
 * measure.** They are written out as explicit nulls rather than left out, so
 * this file reads as the full inventory and a field that is missing from the
 * protocol's schema is missing here too — the alternative, building a map of
 * only what we have, hides the constraint instead of stating it.
 *
 * docs/ANDROID_METRICS.md is the authority for which category each field is in.
 * The short version: no CPU of any kind, no process enumeration, no per-block
 * device IO, no open-connection count.
 */
class SystemCollector(context: Context) {

    private val appContext = context.applicationContext
    private val memory = MemoryCollector(appContext)
    private val battery = BatteryCollector(appContext)
    private val storage = StorageCollector()
    private val network = NetworkCollector()

    /** Probed once at startup so the status surface can say what this particular
     *  device refuses, rather than silently reporting fewer metrics than another
     *  phone with no explanation. */
    val probes: ProcFs.Availability = ProcFs.probe()

    fun thermalStatusLabel(): String? = battery.thermalStatusLabel()

    fun collectSystem(): Fields {
        val mem = memory.read()
        val bat = battery.read()
        val swap = if (probes.meminfo) ProcFs.swap() else null
        val load = if (probes.loadavg) ProcFs.loadAverages() else null

        return fieldsOf(
            // --- CPU: none of it. /proc/stat is denied from API 26 and there is
            // no replacement API. Not attempted; see docs/ANDROID_METRICS.md.
            "cpu_percent" to null,
            "cpu_percent_min" to null,
            "cpu_percent_max" to null,
            "cpu_user_percent" to null,
            "cpu_system_percent" to null,
            "cpu_iowait_percent" to null,
            "cpu_freq_mhz" to if (probes.cpufreq) ProcFs.cpuFrequencyMhz() else null,
            "ctx_switches_per_s" to null,

            "load1" to load?.getOrNull(0),
            "load5" to load?.getOrNull(1),
            "load15" to load?.getOrNull(2),

            // --- Memory: real, device-wide, and the anchor of an Android
            // device's health score now that CPU is absent.
            "mem_total_bytes" to mem?.totalBytes,
            "mem_used_bytes" to mem?.usedBytes,
            "mem_available_bytes" to mem?.availableBytes,
            "mem_percent" to mem?.percent,
            "mem_percent_min" to null,
            "mem_percent_max" to null,
            "swap_total_bytes" to swap?.totalBytes,
            "swap_used_bytes" to swap?.usedBytes,
            "swap_percent" to swap?.percent,

            "uptime_seconds" to SystemClock.elapsedRealtime() / 1000,

            // No concept of a login session, no way to enumerate processes since
            // API 26, and /proc/net is denied from API 28.
            "logged_in_users" to null,
            "process_count" to null,
            "active_connections" to null,

            // The battery thermistor — a real sensor, not a CPU package temp.
            "temperature_celsius" to bat.temperatureCelsius,
            "fan_rpm" to null,
            "battery_percent" to bat.percent,
            "battery_plugged" to bat.plugged,
        )
    }

    /**
     * One raw sample. [latency] is carried in from the slower probe loop so
     * every sample has the most recent real measurement rather than a gap; the
     * value itself is always something that was actually measured.
     */
    fun collect(latency: List<Fields>): Sample = Sample(
        ts = Instant.now().toString(),
        system = collectSystem(),
        diskUsage = storage.read(),
        // /proc/diskstats is denied; there is no per-block-device counter
        // available to an app. An empty list, not entries full of zeroes.
        diskIo = emptyList(),
        net = network.read(System.nanoTime()),
        latency = latency,
        // Fillable with exactly one entry — this process — and the protocol's
        // processes[] means "top N on this machine". A true number under a false
        // heading is worse than no number. See docs/ANDROID_METRICS.md.
        processes = emptyList(),
    )

    fun hostInfo(): Fields {
        val mem = memory.read()
        return fieldsOf(
            "hostname" to hostname(),
            "os" to "Android",
            "os_version" to (Build.VERSION.RELEASE ?: Build.VERSION.SDK_INT.toString()),
            // The real uname release string, which is what a desktop's
            // kernel_version carries too.
            "kernel_version" to System.getProperty("os.version"),
            "arch" to Build.SUPPORTED_ABIS.firstOrNull(),
            "cpu_cores" to Runtime.getRuntime().availableProcessors(),
            "total_memory_bytes" to mem?.totalBytes,
            "agent_version" to BuildConfig.COLLECTOR_VERSION,
            "platform" to "android",
        )
    }

    /**
     * A phone has no hostname. The user-set device name is the closest real
     * equivalent — it is what the same device calls itself over Bluetooth and
     * Wi-Fi Direct — with the model as the fallback.
     */
    private fun hostname(): String {
        val name = try {
            Settings.Global.getString(appContext.contentResolver, Settings.Global.DEVICE_NAME)
        } catch (_: Throwable) {
            null
        }
        val resolved = name?.takeIf { it.isNotBlank() }
            ?: listOf(Build.MANUFACTURER, Build.MODEL)
                .filter { !it.isNullOrBlank() }
                .joinToString(" ")
                .ifBlank { "android-device" }
        return resolved.take(255)
    }
}
