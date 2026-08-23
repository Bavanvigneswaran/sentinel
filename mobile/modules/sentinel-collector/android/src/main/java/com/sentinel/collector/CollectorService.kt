package com.sentinel.collector

import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log

/**
 * The foreground service that owns the collector.
 *
 * This is the whole reason Phase 10b exists as native code: a React Native
 * runtime is torn down when the app is backgrounded and gone entirely when it
 * is closed, which is exactly when a device problem goes unnoticed. Everything
 * below runs in Kotlin on its own coroutine dispatchers and never touches the
 * JS bridge.
 *
 * START_STICKY plus `android:stopWithTask="false"` in the manifest: if the
 * system reclaims the process, Android restarts the service with a null intent,
 * and [onStartCommand] treats that as "resume what the user had running" by
 * consulting the persisted [CollectorConfig.enabled] flag rather than the
 * intent's action.
 */
class CollectorService : Service() {

    private lateinit var config: CollectorConfig
    private lateinit var tokens: TokenStore
    private var engine: CollectorEngine? = null

    override fun onCreate() {
        super.onCreate()
        config = CollectorConfig(this)
        tokens = TokenStore(this)
        CollectorNotification.ensureChannel(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            config.enabled = false
            stopCollecting()
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }

        // A null intent is a system restart after the process was reclaimed.
        // The user's own decision, not the intent, is what says whether we
        // should be running.
        if (intent == null && !config.enabled) {
            stopSelf()
            return START_NOT_STICKY
        }

        if (tokens.read() == null) {
            // Nothing to authenticate with. Refusing to start is better than a
            // permanent notification for a service that can never connect.
            Log.w(TAG, "not enrolled; refusing to start")
            CollectorState.update {
                it.copy(running = false, enrolled = false, lastError = "This phone is not enrolled yet.")
            }
            stopSelf()
            return START_NOT_STICKY
        }

        config.enabled = true
        startInForeground()
        startCollecting()
        return START_STICKY
    }

    private fun startInForeground() {
        val notification = CollectorNotification.build(this, CollectorState.current(), null)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(
                CollectorNotification.NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE,
            )
        } else {
            startForeground(CollectorNotification.NOTIFICATION_ID, notification)
        }
    }

    private fun startCollecting() {
        if (engine != null) return
        CollectorState.update {
            it.copy(batteryOptimizationExempt = isIgnoringBatteryOptimizations(this))
        }
        engine = CollectorEngine(this, config, tokens, onStatusChanged = ::refreshNotification)
            .also { it.start() }
    }

    private fun stopCollecting() {
        engine?.stop()
        engine = null
    }

    private fun refreshNotification() {
        try {
            val manager = getSystemService(android.app.NotificationManager::class.java) ?: return
            manager.notify(
                CollectorNotification.NOTIFICATION_ID,
                CollectorNotification.build(
                    this,
                    CollectorState.current(),
                    engine?.thermalStatusLabel(),
                ),
            )
        } catch (exc: Throwable) {
            // A notification that cannot be updated must never take the
            // collector down with it — same best-effort posture the backend
            // applies to alert notifications.
            Log.w(TAG, "could not refresh the notification", exc)
        }
    }

    override fun onDestroy() {
        stopCollecting()
        super.onDestroy()
    }

    /** Not a bound service: the Expo module talks to it with intents and reads
     *  [CollectorState], which is coherent because they share a process. */
    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        private const val TAG = "SentinelService"
        const val ACTION_START = "com.sentinel.collector.START"
        const val ACTION_STOP = "com.sentinel.collector.STOP"

        fun start(context: Context) {
            val intent = Intent(context, CollectorService::class.java).setAction(ACTION_START)
            context.startForegroundService(intent)
        }

        fun stop(context: Context) {
            val intent = Intent(context, CollectorService::class.java).setAction(ACTION_STOP)
            // startService, not startForegroundService: the latter promises a
            // startForeground() call within five seconds, and this path is
            // stopping the service rather than promoting it.
            try {
                context.startService(intent)
            } catch (_: IllegalStateException) {
                // Background start restrictions when the service is already
                // gone. Nothing to stop.
                CollectorState.markStopped()
            }
        }

        fun isIgnoringBatteryOptimizations(context: Context): Boolean = try {
            val pm = context.getSystemService(Context.POWER_SERVICE) as PowerManager
            pm.isIgnoringBatteryOptimizations(context.packageName)
        } catch (_: Throwable) {
            false
        }
    }
}
