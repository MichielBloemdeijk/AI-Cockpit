package nl.sander.aicockpit.jarvis.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.ResponseBody.Companion.toResponseBody
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import org.json.JSONObject
import java.time.Instant
import java.time.ZoneId
import java.security.MessageDigest
import java.util.UUID
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

data class PairingResult(val deviceId: String, val credential: String, val eventCursor: Long)
class CockpitRequestException(val status: Int, message: String) : IllegalStateException(message)
data class LiveCall(val sessionId: String, val generation: Int, val sdpAnswer: String)
data class LiveSessionState(val sessionId: String, val generation: Int, val lifecycleState: String,
    val controllerId: String? = null, val activationId: String? = null)
data class JarvisComponentIssue(val name: String, val status: String, val detail: String?)
data class JarvisAssistantStatus(
    val workerStatus: String,
    val waitingForWorkerCount: Int,
    val overdueReminderCount: Int,
    val componentIssues: List<JarvisComponentIssue>,
)

internal fun parseJarvisAssistantStatus(
    body: JSONObject,
    now: Instant = Instant.now(),
): JarvisAssistantStatus {
    val tasks = body.optJSONArray("tasks")
    var waitingForWorker = 0
    var overdueReminders = 0
    if (tasks != null) {
        for (index in 0 until tasks.length()) {
            val task = tasks.optJSONObject(index) ?: continue
            val status = task.optString("status")
            if (status == "waiting_for_worker") waitingForWorker += 1
            val scheduled = task.optJSONObject("scheduled_delivery")
            val dueAt = task.optString("resume_at").takeIf { it.isNotBlank() }
                ?: task.optString("waiting_until").takeIf { it.isNotBlank() }
            val overdue = dueAt?.let { value ->
                runCatching { !Instant.parse(value).isAfter(now) }.getOrDefault(false)
            } ?: false
            if (status == "waiting_until" && scheduled?.optString("kind") == "reminder" && overdue) {
                overdueReminders += 1
            }
        }
    }
    val components = body.optJSONObject("component_health") ?: JSONObject()
    val issues = buildList {
        val names = components.keys()
        while (names.hasNext()) {
            val name = names.next()
            if (name == "worker") continue
            val component = components.optJSONObject(name) ?: continue
            val status = component.optString("status")
            if (status !in setOf("healthy", "online")) {
                add(
                    JarvisComponentIssue(
                        name = name,
                        status = status,
                        detail = component.optString("detail").takeIf { it.isNotBlank() },
                    ),
                )
            }
        }
    }.sortedBy { it.name }
    return JarvisAssistantStatus(
        workerStatus = body.optJSONObject("worker_health")?.optString("status", "offline") ?: "offline",
        waitingForWorkerCount = waitingForWorker,
        overdueReminderCount = overdueReminders,
        componentIssues = issues,
    )
}

class CockpitApi(private val store: CredentialStore, private val client: OkHttpClient = OkHttpClient.Builder().followRedirects(false).followSslRedirects(false).build()) {
    private val json = "application/json; charset=utf-8".toMediaType()

    suspend fun pair(serverUrl: String, code: String, label: String): PairingResult {
        val origin = PairingLink.serverOrigin(serverUrl)
        val payload = JSONObject().put("code", code).put("label", label)
        val response = execute(Request.Builder().url("$origin/api/jarvis/devices/pair")
            .post(payload.toString().toRequestBody(json)).build())
        val body = JSONObject(response.body?.string().orEmpty())
        return PairingResult(
            body.getString("device_id"),
            body.getString("credential"),
            body.optLong("event_cursor", 0L),
        )
    }

    suspend fun webSessionCookie(): String {
        return execute(authenticated("/api/jarvis/devices/web-session")
            .post("{}".toRequestBody(json)).build()).use { response ->
            response.header("Set-Cookie") ?: error("Server did not return a phone session.")
        }
    }

    suspend fun unlink() {
        execute(authenticated("/api/jarvis/devices/unlink").post("{}".toRequestBody(json)).build()).close()
    }

    suspend fun updateDevice(lockState: String, cursor: Long? = null) {
        val payload = JSONObject().put("lock_state", lockState)
        cursor?.let { payload.put("event_cursor", it) }
        execute(authenticated("/api/jarvis/devices/${store.deviceId}")
            .patch(payload.toString().toRequestBody(json)).build()).close()
    }

    suspend fun createLiveCall(offer: String, wakeScore: Float? = null, activationId: String? = null): LiveCall {
        val observedAt = Instant.now()
        val timeZone = ZoneId.systemDefault()
        val clientContext = JSONObject()
            .put("time_zone", timeZone.id)
            .put("utc_offset_minutes", timeZone.rules.getOffset(observedAt).totalSeconds / 60)
            .put("observed_at", observedAt.toString())
        val payload = JSONObject()
            .put("controller_id", store.deviceId)
            .put("role", "controller")
            .put("provider_adapter", "openai_live")
            .put("client_context", clientContext)
            .put("sdp_offer", offer)
        val activation = JSONObject()
            .put("source", if (wakeScore == null) "system" else "wake_word")
            .put("activation_id", activationId ?: UUID.randomUUID().toString())
        wakeScore?.let { activation.put("wake_score", it) }
        payload.put("activation_context", activation)
        val response = execute(authenticated("/api/jarvis/live/calls")
            .post(payload.toString().toRequestBody(json)).build())
        val body = JSONObject(response.body?.string().orEmpty())
        val session = body.getJSONObject("session")
        return LiveCall(session.getString("id"), session.getInt("generation"), body.getString("sdp_answer"))
    }

    suspend fun closeLive(sessionId: String, generation: Int) {
        execute(authenticated("/api/jarvis/live/sessions/$sessionId?generation=$generation").delete().build()).close()
    }

    suspend fun liveSessionState(): LiveSessionState? {
        val response = execute(authenticated("/api/jarvis/state").get().build())
        val session = JSONObject(response.body?.string().orEmpty()).optJSONObject("session") ?: return null
        return LiveSessionState(
            session.getString("id"),
            session.getInt("generation"),
            session.optString("lifecycle_state"),
            session.optString("controller_id").takeIf { it.isNotBlank() },
            session.optString("activation_id").takeIf { it.isNotBlank() },
        )
    }

    suspend fun submitLiveDiagnostic(
        traceId: String,
        eventType: String,
        activationId: String? = null,
        sessionId: String? = null,
        generation: Int? = null,
        detail: JSONObject = JSONObject(),
    ) {
        val event = JSONObject()
            .put("trace_id", traceId)
            .put("source", "android")
            .put("event_type", eventType)
            .put("observed_at", Instant.now().toString())
            .put("monotonic_ms", android.os.SystemClock.elapsedRealtime().toDouble())
            .put("detail", detail)
        activationId?.let { event.put("activation_id", it) }
        sessionId?.let { event.put("session_id", it) }
        generation?.let { event.put("generation", it) }
        val payload = JSONObject().put("events", org.json.JSONArray().put(event))
        execute(authenticated("/api/jarvis/live/telemetry")
            .post(payload.toString().toRequestBody(json)).build()).close()
    }

    suspend fun acknowledgeDelivery(deliveryId: String, state: String = "opened") {
        val payload = JSONObject().put("state", state)
        execute(authenticated("/api/notifications/deliveries/$deliveryId/ack")
            .post(payload.toString().toRequestBody(json)).build()).close()
    }

    suspend fun reportCapture(captureId: String, report: JSONObject) {
        execute(authenticated("/api/jarvis/captures/$captureId/status")
            .put(report.toString().toRequestBody(json)).build()).close()
    }

    suspend fun pendingContextRequests(): List<JSONObject> {
        val response = execute(authenticated("/api/jarvis/context-requests/pending").get().build())
        val array = JSONObject(response.body?.string().orEmpty()).getJSONArray("requests")
        return (0 until array.length()).map { array.getJSONObject(it) }
    }

    suspend fun uploadContext(request: JSONObject, jpeg: ByteArray, capturedAt: Instant, monotonicMs: Double) {
        val checksum = MessageDigest.getInstance("SHA-256").digest(jpeg)
            .joinToString("") { "%02x".format(it) }
        val requestId = request.getString("request_id")
        execute(authenticated("/api/jarvis/context-requests/$requestId/content")
            .header("X-Upload-Token", request.getString("upload_token"))
            .header("X-Content-SHA256", checksum)
            .header("X-Capture-Id", request.getString("capture_id"))
            .header("X-Activation-Id", request.getString("activation_id"))
            .header("X-Generation", request.getInt("generation").toString())
            .header("X-Captured-At", capturedAt.toString())
            .header("X-Capture-Monotonic-Ms", monotonicMs.toString())
            .put(jpeg.toRequestBody("image/jpeg".toMediaType())).build()).close()
    }

    suspend fun failContext(requestId: String, reason: String) {
        execute(authenticated("/api/jarvis/context-requests/$requestId/unavailable")
            .post(JSONObject().put("reason", reason).toString().toRequestBody(json)).build()).close()
    }

    suspend fun assistantStatus(): JarvisAssistantStatus {
        val response = execute(authenticated("/api/jarvis/state").get().build())
        return parseJarvisAssistantStatus(JSONObject(response.body?.string().orEmpty()))
    }

    suspend fun liveEvents(cursor: Long, listener: (JSONObject) -> Unit, onFailure: (Throwable) -> Unit,
                   onOpen: () -> Unit = {}): EventSource {
        val request = authenticated("/api/jarvis/live?cursor=$cursor").get().build()
        return EventSources.createFactory(client).newEventSource(request, object : EventSourceListener() {
            override fun onOpen(source: EventSource, response: Response) { onOpen() }
            override fun onEvent(source: EventSource, id: String?, type: String?, data: String) {
                if (type == "jarvis") runCatching { JSONObject(data) }.onSuccess(listener)
            }
            override fun onFailure(source: EventSource, t: Throwable?, response: Response?) {
                onFailure(if (response?.code == 401) CockpitRequestException(401, "Phone was unlinked")
                    else t ?: IllegalStateException("Jarvis event stream closed: ${response?.code}"))
            }
        })
    }

    private suspend fun authenticated(path: String): Request.Builder = withContext(Dispatchers.IO) {
        // AndroidKeyStore decryption can block on Binder. Callers include the
        // visible assistant on Main; move authentication as well as HTTP off it.
        val credential = store.credential() ?: error("Jarvis device is not paired")
        Request.Builder().url("${store.serverUrl}$path").header("Authorization", "Bearer $credential")
    }

    private suspend fun execute(request: Request): Response = withContext(Dispatchers.IO) {
        suspendCancellableCoroutine { continuation ->
            val call = client.newCall(request)
            continuation.invokeOnCancellation { call.cancel() }
            call.enqueue(object : okhttp3.Callback {
                override fun onFailure(call: okhttp3.Call, error: java.io.IOException) {
                    if (continuation.isActive) continuation.resumeWithException(error)
                }
                override fun onResponse(call: okhttp3.Call, response: Response) {
                    if (!continuation.isActive) {
                        response.close()
                        return
                    }
                    if (!response.isSuccessful) {
                        val status = response.code
                        val detail = response.body?.string().orEmpty()
                        response.close()
                        val message = if (status == 401) "Phone link is unavailable. In Jarvis Settings, choose Link phone and open a new link."
                        else runCatching { JSONObject(detail).optString("detail") }.getOrNull()
                            ?.takeIf { it.isNotBlank() && !it.startsWith("[") }
                            ?: "Jarvis could not complete the request. Check the host status and try again."
                        continuation.resumeWithException(CockpitRequestException(status, message))
                    } else {
                        try {
                            val contentType = response.body?.contentType()
                            val buffered = (response.body?.bytes() ?: ByteArray(0)).toResponseBody(contentType)
                            val detached = response.newBuilder().body(buffered).build()
                            response.close()
                            continuation.resume(detached)
                        } catch (error: Exception) {
                            response.close()
                            if (continuation.isActive) continuation.resumeWithException(error)
                        }
                    }
                }
            })
        }
    }
}
