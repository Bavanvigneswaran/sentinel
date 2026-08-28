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
        private set

    /**
     * Held for as long as the collector is collecting. See the WAKE_LOCK
     * comment in the module's AndroidManifest for what goes wrong without it.
     *
     * The cost is real and worth stating: a partial wake lock keeps the
     * application processor out of suspend, which is the single largest thing
     * an app can do to a phone's battery. It is the right trade here for one
     * reason — the alternative is not "a cheaper collector", it is a collector
     * that reports nothing for the entire time the screen is off, which is most
     * of a phone's day and precisely the stretch during which a problem goes
     * unnoticed. The manifest's own specialUse justification ("monitoring must
     * continue while the app is backgrounded or closed") is false without this.
     *
     * The cheaper mechanisms do not reach: AlarmManager's
     * setExactAndAllowWhileIdle is throttled to roughly one firing per app per
     * minute awake and far less under Doze, so it cannot drive a 10s cadence at
     * all, let alone the 1s one live monitoring asks for.
     *
     * What this does NOT buy is immunity from Doze. Doze ignores wake locks
     * outright once the device has been stationary and unused long enough, and
     * the answer there is the battery-optimisation exemption the collector
     * screen already offers when [isIgnoringBatteryOptimizations] is false —
     * these two are halves of the same guarantee, not alternatives.
     */
    private var wakeLock: PowerManager.WakeLock? = null

    override fun onCreate() {
        super.onCreate()
        instance = this
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
        acquireWakeLock()
        engine = CollectorEngine(this, config, tokens, onStatusChanged = ::refreshNotification)
            .also { it.start() }
    }

    private fun stopCollecting() {
        engine?.stop()
        engine = null
        releaseWakeLock()
    }

    /**
     * No timeout on the acquire: the lock's lifetime is the collector's, and
     * every path that stops collecting releases it (an explicit stop, an
     * unenroll, onDestroy). A timeout would reintroduce exactly the bug this
     * exists to fix, just later and harder to reproduce.
     *
     * Not reference counted, so a second start on an already-running service —
     * which onStartCommand permits, since START_STICKY can redeliver — cannot
     * leave a surplus acquisition that the single release never balances.
     *
     * Failing to get the lock is logged and survived rather than fatal: a
     * collector that samples only while the screen is on is degraded, and a
     * collector that refuses to start is useless.
     */
    @Suppress("WakelockTimeout")
    private fun acquireWakeLock() {
        if (wakeLock?.isHeld == true) return
        try {
            val manager = getSystemService(Context.POWER_SERVICE) as PowerManager
            wakeLock = manager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, WAKE_LOCK_TAG).apply {
                setReferenceCounted(false)
                acquire()
            }
            Log.i(TAG, "holding a partial wake lock; sampling continues with the screen off")
        } catch (exc: Throwable) {
            Log.w(TAG, "could not hold a wake lock; sampling will stall while the screen is off", exc)
            wakeLock = null
        }
    }

    private fun releaseWakeLock() {
        try {
            wakeLock?.takeIf { it.isHeld }?.release()
        } catch (exc: Throwable) {
            Log.w(TAG, "could not release the wake lock", exc)
        }
        wakeLock = null
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
        releaseWakeLock()
        if (instance === this) instance = null
        super.onDestroy()
    }

    /** Not a bound service: the Expo module talks to it with intents and reads
     *  [CollectorState], which is coherent because they share a process. */
    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        private const val TAG = "SentinelService"

        /** `package:reason` is the convention battery attribution reports use;
         *  a lock this long-lived should be identifiable in `dumpsys power`. */
        private const val WAKE_LOCK_TAG = "sentinel:collector"

        /**
         * The running service, so the Expo module can nudge a live engine
         * without a restart. Safe as a plain reference because the service and
         * the module share one process by design (Phase 10b: a `:collector`
         * process would put incoherent SharedPreferences between them), and it
         * is cleared in onDestroy.
         */
        @Volatile
        private var instance: CollectorService? = null
        const val ACTION_START = "com.sentinel.collector.START"
        const val ACTION_STOP = "com.sentinel.collector.STOP"

        fun start(context: Context) {
            val intent = Intent(context, CollectorService::class.java).setAction(ACTION_START)
            context.startForegroundService(intent)
        }

        /** Push a changed cadence preference into a running collector.
         *  A no-op when nothing is running — the next start seeds from config. */
        fun applyCadence(@Suppress("UNUSED_PARAMETER") context: Context) {
            instance?.engine?.applyCadencePreference()
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
