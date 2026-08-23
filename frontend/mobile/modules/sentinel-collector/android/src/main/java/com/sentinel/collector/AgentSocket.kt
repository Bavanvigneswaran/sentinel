package com.sentinel.collector

import android.util.Log
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume

/** The server told us not to bother retrying: a revoked token, or a protocol
 *  version it will never accept. Ported from FatalTransportError. */
class FatalTransportError(message: String) : Exception(message)

/**
 * One live socket to `/ws/agent`, the Kotlin counterpart of
 * `agent/sentinel_agent/transport/client.py`.
 *
 * OkHttp's WebSocket API is callback-driven, so incoming frames land in a
 * [Channel] and the send/ack cycle reads from it — which recovers the Python
 * agent's straight-line `push()` → `await ack` shape rather than scattering the
 * protocol across listener callbacks.
 *
 * The channel is deliberately unbounded. The only things the server sends are
 * acks, mode changes and pings; dropping one would mean either hanging on an
 * ack that already arrived or ignoring a live-mode upshift.
 */
class AgentSocket(private val socket: WebSocket, private val incoming: Channel<JSONObject>) {

    var mode: String = "normal"
        private set
    var pushIntervalSeconds: Int = CollectorConfig.DEFAULT_PUSH_SECONDS
        private set
    var deviceId: String? = null
        private set

    private suspend fun receive(): JSONObject = incoming.receive()

    private fun send(frame: JSONObject) {
        if (!socket.send(frame.toString())) {
            throw java.io.IOException("socket is closed")
        }
    }

    suspend fun handshake(host: Fields) {
        send(Protocol.helloFrame(host))
        val frame = withTimeoutOrNull(HANDSHAKE_TIMEOUT_MS) { receive() }
            ?: throw java.io.IOException("no welcome frame within ${HANDSHAKE_TIMEOUT_MS}ms")

        when (frame.optString("type")) {
            "error" -> raise(frame)
            "welcome" -> Unit
            else -> throw FatalTransportError(
                "expected welcome, got ${frame.optString("type")}"
            )
        }

        deviceId = frame.optString("device_id").takeIf { it.isNotBlank() }
        mode = frame.optString("mode", "normal")
        pushIntervalSeconds = frame.optInt("push_interval_seconds", CollectorConfig.DEFAULT_PUSH_SECONDS)
        Log.i(TAG, "connected device_id=$deviceId mode=$mode interval=${pushIntervalSeconds}s")
    }

    /** Send a batch and wait for its ack. Returns the accepted count. */
    suspend fun push(samples: List<Sample>): Int {
        val batchId = UUID.randomUUID().toString().replace("-", "")
        send(Protocol.metricsFrame(batchId, samples))

        while (true) {
            val frame = receive()
            when (frame.optString("type")) {
                "ack" -> {
                    // A mode frame can overtake an ack; ignore acks for other
                    // batches rather than treating the first ack as ours.
                    if (frame.optString("batch_id") != batchId) continue
                    val rejected = frame.optInt("rejected", 0)
                    if (rejected > 0) {
                        // Out-of-window samples. Retrying would never succeed.
                        Log.w(TAG, "server rejected $rejected stale sample(s)")
                    }
                    return frame.optInt("accepted", 0)
                }
                "mode" -> applyMode(frame)
                "ping" -> send(Protocol.pongFrame())
                "error" -> raise(frame)
            }
        }
    }

    /** Handle unsolicited server frames while idle between pushes. */
    suspend fun drainServerFrames(timeoutMs: Long = IDLE_DRAIN_MS) {
        while (true) {
            val frame = withTimeoutOrNull(timeoutMs) { receive() } ?: return
            when (frame.optString("type")) {
                "mode" -> applyMode(frame)
                "ping" -> send(Protocol.pongFrame())
                "error" -> raise(frame)
            }
        }
    }

    private fun applyMode(frame: JSONObject) {
        mode = frame.optString("mode", "normal")
        pushIntervalSeconds =
            frame.optInt("push_interval_seconds", pushIntervalSeconds)
        Log.i(TAG, "switched to $mode mode (${pushIntervalSeconds}s)")
    }

    private fun raise(frame: JSONObject): Nothing {
        val message = "${frame.optString("code")}: ${frame.optString("message")}"
        if (!frame.optBoolean("retryable", true)) throw FatalTransportError(message)
        throw java.io.IOException(message)
    }

    fun close() {
        try {
            socket.close(1000, null)
        } catch (_: Throwable) {
            // Already gone.
        }
        incoming.close()
    }

    companion object {
        private const val TAG = "SentinelSocket"
        private const val HANDSHAKE_TIMEOUT_MS = 15_000L
        private const val IDLE_DRAIN_MS = 50L

        private val client = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            // A WebSocket has no read timeout; OkHttp's own ping keeps a NAT
            // binding alive, which on a mobile network is not optional.
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .pingInterval(20, TimeUnit.SECONDS)
            .build()

        /**
         * Open an authenticated socket.
         *
         * The token travels in the handshake's Authorization header rather than
         * the URL, exactly as the desktop agent does it: query strings end up in
         * proxy logs and crash reports.
         */
        suspend fun connect(url: String, token: String): AgentSocket =
            suspendCancellableCoroutine { continuation ->
                val incoming = Channel<JSONObject>(Channel.UNLIMITED)
                val request = Request.Builder()
                    .url(url)
                    .addHeader("Authorization", "Bearer $token")
                    .build()

                val listener = object : WebSocketListener() {
                    private var settled = false

                    override fun onOpen(webSocket: WebSocket, response: Response) {
                        if (settled) return
                        settled = true
                        continuation.resume(AgentSocket(webSocket, incoming))
                    }

                    override fun onMessage(webSocket: WebSocket, text: String) {
                        try {
                            incoming.trySend(JSONObject(text))
                        } catch (exc: Throwable) {
                            Log.w(TAG, "unparseable server frame", exc)
                        }
                    }

                    override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                        incoming.close(java.io.IOException("server closed the socket ($code $reason)"))
                        webSocket.close(1000, null)
                    }

                    override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                        // 401/403 at the handshake means the token is dead.
                        // Reconnecting on a loop would be a slow brute force
                        // against our own server.
                        val status = response?.code
                        val error = if (status == 401 || status == 403) {
                            FatalTransportError(
                                "The server rejected this device's agent token — it may have been " +
                                    "revoked. Enrol the phone again to reconnect it."
                            )
                        } else {
                            t
                        }
                        incoming.close(error)
                        if (!settled) {
                            settled = true
                            continuation.resumeWith(Result.failure(error))
                        }
                    }
                }

                val webSocket = client.newWebSocket(request, listener)
                continuation.invokeOnCancellation {
                    webSocket.cancel()
                    incoming.close()
                }
            }
    }
}
