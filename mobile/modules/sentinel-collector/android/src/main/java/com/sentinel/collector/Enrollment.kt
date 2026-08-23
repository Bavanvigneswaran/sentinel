package com.sentinel.collector

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * Exchange a one-time enrollment code for a long-lived agent token, against the
 * same `POST /enroll` the desktop agent uses.
 *
 * The user's password is never involved — the code is the only credential, and
 * what comes back is opaque, scoped to this one device and revocable from the
 * web console. `platform: "android"` is already an accepted value there
 * (`app/api/routes/devices.py`), so this needed no backend change at all.
 *
 * Runs off the main thread; the caller is a coroutine on Dispatchers.IO.
 */
class EnrollmentError(message: String) : Exception(message)

object Enrollment {

    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .build()

    data class Result(val deviceId: String, val agentToken: String)

    fun enroll(serverUrl: String, code: String, deviceName: String): Result {
        val body = JSONObject()
            .put("code", code)
            .put("device_name", deviceName)
            .put("platform", "android")
            .toString()
            .toRequestBody("application/json".toMediaType())

        val request = Request.Builder()
            .url("${serverUrl.trimEnd('/')}/enroll")
            .post(body)
            .build()

        val response = try {
            client.newCall(request).execute()
        } catch (exc: Exception) {
            throw EnrollmentError("Could not reach $serverUrl: ${exc.message}")
        }

        response.use {
            val text = it.body?.string().orEmpty()
            when {
                it.code == 400 -> throw EnrollmentError(
                    "That enrollment code was rejected — it may be mistyped, expired or already used."
                )
                it.code == 429 -> throw EnrollmentError(
                    "Too many enrollment attempts. Wait a while and try again."
                )
                !it.isSuccessful -> throw EnrollmentError("Enrollment failed (${it.code}).")
            }
            return try {
                val payload = JSONObject(text)
                Result(
                    deviceId = payload.getString("device_id"),
                    agentToken = payload.getString("agent_token"),
                )
            } catch (exc: Exception) {
                throw EnrollmentError("The server's enrollment response could not be read.")
            }
        }
    }
}
