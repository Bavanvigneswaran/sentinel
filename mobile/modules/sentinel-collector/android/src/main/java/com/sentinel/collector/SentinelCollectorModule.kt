package com.sentinel.collector

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import expo.modules.kotlin.exception.Exceptions
import expo.modules.kotlin.functions.Coroutine
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * The JS-facing surface: enrol, start, stop, status.
 *
 * This is the *only* class in the module that knows React Native exists. Every
 * other file — the service, the engine, the socket, the collectors — runs
 * without it, which is what lets monitoring continue when the JS runtime is
 * torn down. The module is a remote control, not a dependency.
 *
 * `status()` reads [CollectorState], an in-process singleton, and the
 * `onCollectorStatus` event republishes it so a screen can follow the service
 * live instead of polling it.
 */
class SentinelCollectorModule : Module() {

    private val context
        get() = appContext.reactContext ?: throw Exceptions.ReactContextLost()

    /** Only ever used to mirror CollectorState into JS. The collector itself
     *  runs on the service's own scopes and does not know this exists. */
    private val moduleScope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var observer: Job? = null

    override fun definition() = ModuleDefinition {
        Name("SentinelCollector")

        Events(EVENT_STATUS)

        /**
         * Exchange a one-time enrollment code for an agent token and seal it in
         * the Keystore. Does not start the service — enrolling and running are
         * separate decisions, and a user who enrols from Settings should still
         * get to choose when sampling begins.
         */
        AsyncFunction("enroll") Coroutine { serverUrl: String, code: String, deviceName: String ->
            val result = withContext(Dispatchers.IO) {
                Enrollment.enroll(serverUrl, code, deviceName)
            }
            val config = CollectorConfig(context)
            config.serverUrl = serverUrl
            config.deviceId = result.deviceId
            config.deviceName = deviceName
            TokenStore(context).write(result.agentToken)

            CollectorState.update {
                it.copy(
                    enrolled = true,
                    deviceId = result.deviceId,
                    deviceName = deviceName,
                    serverUrl = config.serverUrl,
                    lastError = null,
                )
            }
            emitStatus()
            result.deviceId
        }

        /**
         * Forget the token and stop.
         *
         * Local only: the device row and its history stay on the server, and the
         * agent token stays valid until it is revoked from the web console.
         * Silently revoking somebody's device from a phone-side toggle would
         * throw away metric history that the enrollment flow deliberately
         * preserves on re-enrolment.
         */
        AsyncFunction("unenroll") {
            CollectorService.stop(context)
            TokenStore(context).clear()
            CollectorConfig(context).clear()
            CollectorState.update { CollectorStatus() }
            emitStatus()
        }

        AsyncFunction("start") {
            val config = CollectorConfig(context)
            if (TokenStore(context).read() == null) {
                throw NotEnrolledException()
            }
            if (config.serverUrl.isBlank()) {
                throw NotEnrolledException()
            }
            CollectorService.start(context)
        }

        AsyncFunction("stop") {
            CollectorService.stop(context)
            emitStatus()
        }

        Function("status") { statusPayload(unsealToken = true) }

        /**
         * Android kills or defers background work under Doze unless the app is
         * exempt. The collector runs without the exemption — less reliably, with
         * pushes bunching up after an idle period — so this is offered, never
         * required, and the app explains the trade rather than nagging.
         */
        AsyncFunction("requestBatteryOptimizationExemption") {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return@AsyncFunction false
            if (CollectorService.isIgnoringBatteryOptimizations(context)) return@AsyncFunction true
            val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
                .setData(Uri.parse("package:${context.packageName}"))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            context.startActivity(intent)
            false
        }

        OnStartObserving(EVENT_STATUS) {
            // The service updates CollectorState from its own coroutines; this
            // republishes those transitions to JS so a screen can render them
            // without polling. Nothing in the collector depends on anyone
            // listening.
            observer?.cancel()
            observer = moduleScope.launch {
                CollectorState.status.collectLatest { emitStatus() }
            }
        }

        OnStopObserving(EVENT_STATUS) {
            observer?.cancel()
            observer = null
        }

        OnDestroy {
            observer?.cancel()
            observer = null
        }
    }

    /**
     * [enrolled] is read authoritatively — an actual Keystore unseal — only
     * when JS asks for `status()`. The event stream passes `false`, so the
     * cached [CollectorState] value is used instead.
     *
     * The service updates its state on every sample, and every update is
     * republished here; unsealing the token each time would put a Keystore
     * decrypt on the main thread once a second while the collector screen is
     * open, on a device whose battery is the whole reason the sampling cadence
     * was slowed down in the first place.
     */
    private fun statusPayload(unsealToken: Boolean): Map<String, Any?> {
        val status = CollectorState.current()
        val config = CollectorConfig(context)
        return mapOf(
            "enrolled" to if (unsealToken) TokenStore(context).read() != null else status.enrolled,
            "running" to status.running,
            "connected" to status.connected,
            "deviceId" to (status.deviceId ?: config.deviceId),
            "deviceName" to (status.deviceName ?: config.deviceName),
            "serverUrl" to config.serverUrl,
            "mode" to status.mode,
            "pushIntervalSeconds" to status.pushIntervalSeconds,
            "bufferedSamples" to status.bufferedSamples,
            "lastPushAt" to status.lastPushAt,
            "lastSampleAt" to status.lastSampleAt,
            "lastError" to status.lastError,
            "unavailableProbes" to status.unavailableProbes,
            "batteryOptimizationExempt" to CollectorService.isIgnoringBatteryOptimizations(context),
        )
    }

    private fun emitStatus() {
        try {
            sendEvent(EVENT_STATUS, statusPayload(unsealToken = false))
        } catch (_: Throwable) {
            // No JS runtime listening — which is the normal state for most of
            // this service's life. Never a reason to fail an operation.
        }
    }

    private companion object {
        const val EVENT_STATUS = "onCollectorStatus"
    }
}

class NotEnrolledException :
    expo.modules.kotlin.exception.CodedException("This phone is not enrolled yet.")
