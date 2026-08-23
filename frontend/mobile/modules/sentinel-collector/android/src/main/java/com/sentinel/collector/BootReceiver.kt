package com.sentinel.collector

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * Restart the collector after a reboot or an app update.
 *
 * Only when the user had it running: [CollectorConfig.enabled] is set by an
 * explicit start and cleared by an explicit stop. A background service that
 * resurrected itself after the user turned it off would be malware behaviour,
 * and "it was on when the phone went down" is the only defensible trigger.
 *
 * ACTION_BOOT_COMPLETED is delivered after the user unlocks, which is what
 * [TokenStore] needs — the Keystore is unavailable before first unlock, so an
 * earlier direct-boot start could not read the agent token anyway.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action
        if (action != Intent.ACTION_BOOT_COMPLETED &&
            action != Intent.ACTION_MY_PACKAGE_REPLACED
        ) {
            return
        }
        val config = CollectorConfig(context)
        if (!config.enabled) return
        if (TokenStore(context).read() == null) {
            Log.w(TAG, "enabled but not enrolled; not starting")
            return
        }
        Log.i(TAG, "restarting the collector after $action")
        CollectorService.start(context)
    }

    private companion object {
        const val TAG = "SentinelBoot"
    }
}
