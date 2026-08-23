package com.sentinel.collector.collectors

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.Build
import android.os.Environment
import android.os.PowerManager
import android.os.StatFs
import com.sentinel.collector.Fields
import com.sentinel.collector.fieldsOf

/** Device-wide physical memory. `ActivityManager.MemoryInfo` is real, unprivileged and exact. */
class MemoryCollector(private val context: Context) {
    data class Reading(val totalBytes: Long, val availableBytes: Long) {
        val usedBytes: Long get() = (totalBytes - availableBytes).coerceAtLeast(0)
        val percent: Double? get() = if (totalBytes > 0) usedBytes * 100.0 / totalBytes else null
    }

    fun read(): Reading? = try {
        val am = context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        val info = ActivityManager.MemoryInfo()
        am.getMemoryInfo(info)
        if (info.totalMem > 0) Reading(info.totalMem, info.availMem) else null
    } catch (_: Throwable) {
        null
    }
}

/**
 * Battery level, charge state and temperature, from the sticky
 * ACTION_BATTERY_CHANGED broadcast.
 *
 * The temperature is the **battery** thermistor — the only thermometer an
 * unprivileged app can read, `HardwarePropertiesManager` being device-owner
 * only. It is a real sensor in a real place; it is not a CPU package
 * temperature, and the per-device screen labels it accordingly.
 */
class BatteryCollector(private val context: Context) {
    private companion object {
        /** Neither -1 nor 0 is a safe sentinel for a temperature: a cold phone
         *  can genuinely read below zero. */
        const val NO_TEMPERATURE = Int.MIN_VALUE
    }

    data class Reading(
        val percent: Double?,
        val plugged: Boolean?,
        val temperatureCelsius: Double?,
    )

    fun read(): Reading {
        val intent: Intent? = try {
            context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        } catch (_: Throwable) {
            null
        }
        if (intent == null) return Reading(null, null, null)

        val level = intent.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
        val scale = intent.getIntExtra(BatteryManager.EXTRA_SCALE, -1)
        val plugged = intent.getIntExtra(BatteryManager.EXTRA_PLUGGED, -1)
        val tenths = intent.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, NO_TEMPERATURE)

        return Reading(
            percent = if (level >= 0 && scale > 0) level * 100.0 / scale else null,
            plugged = if (plugged < 0) null else plugged != 0,
            temperatureCelsius = if (tenths == NO_TEMPERATURE) null else tenths / 10.0,
        )
    }

    /**
     * The device's thermal-throttling state, for the persistent notification
     * only. It has no protocol field and deliberately is not getting one — see
     * docs/ANDROID_METRICS.md.
     */
    fun thermalStatusLabel(): String? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return null
        return try {
            val pm = context.getSystemService(Context.POWER_SERVICE) as PowerManager
            when (pm.currentThermalStatus) {
                PowerManager.THERMAL_STATUS_NONE -> null
                PowerManager.THERMAL_STATUS_LIGHT -> "light throttling"
                PowerManager.THERMAL_STATUS_MODERATE -> "moderate throttling"
                PowerManager.THERMAL_STATUS_SEVERE -> "severe throttling"
                PowerManager.THERMAL_STATUS_CRITICAL -> "critical thermal"
                PowerManager.THERMAL_STATUS_EMERGENCY -> "emergency thermal"
                PowerManager.THERMAL_STATUS_SHUTDOWN -> "thermal shutdown imminent"
                else -> null
            }
        } catch (_: Throwable) {
            null
        }
    }
}

/**
 * Filesystem capacity via `StatFs`.
 *
 * Only `StatFs` numbers are used, never `StorageStatsManager.getTotalBytes()`:
 * the latter reports the *advertised* capacity (the "128 GB" on the box) while
 * the free-space figure comes from the partition, and mixing the two produces a
 * used-percentage that is wrong by the several gigabytes of difference. One
 * source, internally consistent.
 *
 * `filesystem` is null rather than guessed — naming the fs type needs
 * `/proc/mounts`, which is not readable.
 */
class StorageCollector {
    private fun statOf(path: java.io.File?, mount: String): Fields? {
        if (path == null || !path.exists()) return null
        return try {
            val stat = StatFs(path.absolutePath)
            val block = stat.blockSizeLong
            val total = stat.blockCountLong * block
            val free = stat.availableBlocksLong * block
            if (total <= 0) return null
            val used = (total - free).coerceAtLeast(0)
            fieldsOf(
                "mount" to mount,
                "filesystem" to null,
                "total_bytes" to total,
                "used_bytes" to used,
                "free_bytes" to free,
                "percent" to used * 100.0 / total,
            )
        } catch (_: Throwable) {
            null
        }
    }

    fun read(): List<Fields> {
        val out = mutableListOf<Fields>()
        val data = statOf(Environment.getDataDirectory(), "/data")
        if (data != null) out.add(data)

        // Shared storage is the same emulated volume as /data on almost every
        // modern device. Reporting it a second time under another name would
        // draw two mounts filling in lockstep and double-count the fullest-mount
        // health input, so it is only included when its block counts differ —
        // i.e. when it really is a separate volume, such as a physical SD card.
        val shared = statOf(
            @Suppress("DEPRECATION") Environment.getExternalStorageDirectory(),
            "/storage/emulated/0",
        )
        if (shared != null && data != null &&
            (shared["total_bytes"] != data["total_bytes"] || shared["free_bytes"] != data["free_bytes"])
        ) {
            out.add(shared)
        } else if (shared != null && data == null) {
            out.add(shared)
        }
        return out
    }
}
