package com.sentinel.collector

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.os.Build

/**
 * The persistent notification. On Android this is not decoration — it is the
 * legal and technical price of running in the background, and it is also the
 * only honest way to tell the person holding the phone that something is
 * sampling it.
 *
 * IMPORTANCE_LOW: it must be visible and non-dismissable, but it must never
 * make a sound or push itself in front of anything. It is a status line.
 *
 * The text says what is actually happening — connected or retrying, the current
 * cadence, and how much is buffered — rather than a permanent "Running". A
 * collector that has been unable to reach the server for an hour should say so
 * on the device itself, not only in a console the user may not be looking at.
 */
object CollectorNotification {

    const val CHANNEL_ID = "sentinel-collector"
    const val NOTIFICATION_ID = 4711

    fun ensureChannel(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        if (manager.getNotificationChannel(CHANNEL_ID) != null) return
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Device monitoring",
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = "Shown while this phone is reporting its own metrics to Sentinel."
            setShowBadge(false)
            enableVibration(false)
            setSound(null, null)
        }
        manager.createNotificationChannel(channel)
    }

    fun build(context: Context, status: CollectorStatus, thermal: String?): Notification {
        val title = when {
            !status.running -> "Monitoring stopped"
            status.connected && status.mode == "live" -> "Monitoring · live (1s)"
            status.connected -> "Monitoring this device"
            else -> "Monitoring · reconnecting"
        }

        val detail = buildString {
            if (status.connected) {
                append(status.deviceName ?: "This device")
                append(" · every ")
                append(status.pushIntervalSeconds)
                append("s")
            } else {
                append(status.lastError ?: "Not connected")
            }
            if (status.bufferedSamples > 0) {
                append(" · ")
                append(status.bufferedSamples)
                append(" buffered")
            }
            // Thermal throttling has no protocol field and deliberately is not
            // getting one (docs/ANDROID_METRICS.md). It is genuinely useful to
            // the person holding the phone, so it is shown here, where it costs
            // nothing.
            if (thermal != null) {
                append(" · ")
                append(thermal)
            }
        }

        val launch = context.packageManager.getLaunchIntentForPackage(context.packageName)
        val contentIntent = launch?.let {
            PendingIntent.getActivity(
                context,
                0,
                it,
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            )
        }

        return Notification.Builder(context, CHANNEL_ID)
            .setContentTitle(title)
            .setContentText(detail)
            .setStyle(Notification.BigTextStyle().bigText(detail))
            // A platform icon: the module ships no drawables of its own, and a
            // missing resource id crashes startForeground rather than degrading.
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setCategory(Notification.CATEGORY_SERVICE)
            .apply { contentIntent?.let { setContentIntent(it) } }
            .build()
    }
}
