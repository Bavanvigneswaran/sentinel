package com.sentinel.collector

import android.content.Context

/**
 * The non-secret half of the collector's persisted state: where the backend is,
 * which device we enrolled as, and whether the user wants the service running.
 *
 * Plain SharedPreferences on purpose — none of this is a credential. The token
 * lives in [TokenStore] behind the Keystore.
 *
 * `enabled` is what survives a reboot: [BootReceiver] starts the service only
 * when the user had it running when the phone went down. A collector that
 * restarted itself after the user had explicitly stopped it would be malware
 * behaviour.
 */
class CollectorConfig(context: Context) {

    private val prefs = context.applicationContext
        .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    var serverUrl: String
        get() = prefs.getString(KEY_SERVER_URL, "") ?: ""
        set(value) = prefs.edit().putString(KEY_SERVER_URL, value.trimEnd('/')).apply()

    var deviceId: String?
        get() = prefs.getString(KEY_DEVICE_ID, null)
        set(value) = prefs.edit().putString(KEY_DEVICE_ID, value).apply()

    var deviceName: String?
        get() = prefs.getString(KEY_DEVICE_NAME, null)
        set(value) = prefs.edit().putString(KEY_DEVICE_NAME, value).apply()

    var enabled: Boolean
        get() = prefs.getBoolean(KEY_ENABLED, false)
        set(value) = prefs.edit().putBoolean(KEY_ENABLED, value).apply()

    fun clear() {
        prefs.edit().clear().apply()
    }

    /**
     * The ingest socket URL. Same rule the desktop agent's config applies:
     * https → wss, http → ws. There is no proxy on a device, so the base is
     * absolute and carries no `/api` prefix — FastAPI mounts `/ws/agent` at the
     * root, and the web app's `/api` is purely a Vite artefact (Phase 1).
     */
    val webSocketUrl: String
        get() = when {
            serverUrl.startsWith("https://") -> "wss://" + serverUrl.removePrefix("https://") + WS_PATH
            serverUrl.startsWith("http://") -> "ws://" + serverUrl.removePrefix("http://") + WS_PATH
            else -> throw IllegalStateException("server URL must start with http:// or https://")
        }

    val enrollUrl: String get() = "$serverUrl/enroll"

    companion object {
        const val WS_PATH = "/ws/agent"
        private const val PREFS_NAME = "sentinel_collector"
        private const val KEY_SERVER_URL = "server_url"
        private const val KEY_DEVICE_ID = "device_id"
        private const val KEY_DEVICE_NAME = "device_name"
        private const val KEY_ENABLED = "enabled"

        /** Normal-mode cadence. See docs/ANDROID_METRICS.md on why a phone does
         *  not sample at 1s the way the desktop agent does. */
        const val NORMAL_SAMPLE_SECONDS = 10
        const val LIVE_SAMPLE_SECONDS = 1
        const val DEFAULT_PUSH_SECONDS = 10

        /** Latency probes cost a TCP handshake, so they run slower than the
         *  sample loop — but still comfortably inside the server's 90s
         *  freshness window, or the phone would lose its packet-loss health
         *  component between probes. */
        const val LATENCY_INTERVAL_SECONDS = 30

        /** ~1 hour of normal-mode samples, matching the server's accepted
         *  sample age. Buffering longer would only produce rows it rejects. */
        const val BUFFER_SIZE = 400
    }
}
