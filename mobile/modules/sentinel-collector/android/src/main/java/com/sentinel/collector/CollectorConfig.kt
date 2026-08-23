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

    /**
     * Sample every second all the time, not only while someone is watching.
     *
     * Off by default, and that default is a real decision rather than
     * timidity: a 1s timer running all day is the collector's dominant battery
     * cost, and every metric Android actually lets an app read (memory,
     * storage, battery, throughput) moves far too slowly for 1s to tell you
     * anything 10s does not. Live mode already upshifts to 1s on demand, which
     * is where the resolution is genuinely useful.
     *
     * It is exposed anyway because "is this really real-time?" is a fair
     * question to want answered on your own phone, and the cost is the user's
     * to spend. The UI states it plainly rather than burying it.
     */
    var highFrequency: Boolean
        get() = prefs.getBoolean(KEY_HIGH_FREQUENCY, false)
        set(value) = prefs.edit().putBoolean(KEY_HIGH_FREQUENCY, value).apply()

    /** The cadence the sample loop uses outside live mode. */
    val normalSampleSeconds: Int
        get() = if (highFrequency) LIVE_SAMPLE_SECONDS else NORMAL_SAMPLE_SECONDS

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
        private const val KEY_HIGH_FREQUENCY = "high_frequency"

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
