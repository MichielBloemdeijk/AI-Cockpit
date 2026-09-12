package nl.sander.aicockpit.jarvis.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import org.json.JSONObject
import java.time.Instant
import java.time.ZoneId
import java.time.temporal.ChronoUnit
import java.util.UUID
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

data class PairingResult(val deviceId: String, val credential: String, val eventCursor: Long)
data class RealtimeCall(val sessionId: String, val generation: Int, val sdpAnswer: String)
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

class CockpitApi(private val store: CredentialStore, private val client: OkHttpClient = OkHttpClient()) {
    private val json = "application/json; charset=utf-8".toMediaType()

    suspend fun pair(serverUrl: String, code: String, label: String): PairingResult {
        store.serverUrl = serverUrl
        val payload = JSONObject().put("code", code).put("label", label)
        val response = execute(Request.Builder().url("${store.serverUrl}/api/jarvis/devices/pair")
            .post(payload.toString().toRequestBody(json)).build())
        val body = JSONObject(response.body?.string().orEmpty())
        return PairingResult(
            body.getString("device_id"),
            body.getString("credential"),
            body.optLong("event_cursor", 0L),
        )
    }

    suspend fun updateDevice(lockState: String, cursor: Long? = null) {
        val payload = JSONObject().put("lock_state", lockState)
        cursor?.let { payload.put("event_cursor", it) }
        execute(authenticated("/api/jarvis/devices/${store.deviceId}")
            .patch(payload.toString().toRequestBody(json)).build()).close()
    }

    suspend fun createRealtimeCall(offer: String, wakeScore: Float? = null, engine: VoiceEngine = VoiceEngine.REALTIME, activationId: String? = null): RealtimeCall {
        val observedAt = Instant.now()
        val timeZone = ZoneId.systemDefault()
        val clientContext = JSONObject()
            .put("time_zone", timeZone.id)
            .put("utc_offset_minutes", timeZone.rules.getOffset(observedAt).totalSeconds / 60)
            .put("observed_at", observedAt.toString())
        val payload = JSONObject()
            .put("controller_id", store.deviceId)
            .put("role", "controller")
            .put("provider_adapter", if (engine == VoiceEngine.GPT_LIVE) "openai_live" else "openai")
            .put("client_context", clientContext)
            .put("sdp_offer", offer)
        wakeScore?.let {
            payload.put(
                "activation_context",
                JSONObject()
                    .put("source", "wake_word")
                    .put("wake_score", it)
                    .put("activation_id", activationId ?: UUID.randomUUID().toString()),
            )
        }
        val response = execute(authenticated("/api/jarvis/realtime/calls")
            .post(payload.toString().toRequestBody(json)).build())
        val body = JSONObject(response.body?.string().orEmpty())
        val session = body.getJSONObject("session")
        return RealtimeCall(session.getString("id"), session.getInt("generation"), body.getString("sdp_answer"))
    }

    suspend fun closeRealtime(sessionId: String, generation: Int) {
        execute(authenticated("/api/jarvis/realtime/sessions/$sessionId?generation=$generation").delete().build()).close()
    }

    suspend fun snoozeByDescription(description: String, minutes: Long = 10) {
        val resolvePayload = JSONObject().put("reference", description).put("active_only", false)
        val resolvedResponse = execute(authenticated("/api/jarvis/tasks/resolve")
            .post(resolvePayload.toString().toRequestBody(json)).build())
        val resolved = JSONObject(resolvedResponse.body?.string().orEmpty())
        if (!resolved.optBoolean("resolved") || resolved.optBoolean("ambiguous")) {
            error(resolved.optString("clarification", "Unable to resolve that reminder"))
        }
        val internalTaskId = resolved.getString("internal_task_id")
        val payload = JSONObject()
            .put("due_at", Instant.now().plus(minutes, ChronoUnit.MINUTES).toString())
            .put("idempotency_key", "android-snooze-${System.currentTimeMillis()}")
        execute(authenticated("/api/jarvis/reminders/$internalTaskId/snooze")
            .post(payload.toString().toRequestBody(json)).build()).close()
    }

    suspend fun acknowledgeDelivery(deliveryId: String, state: String = "opened") {
        val payload = JSONObject().put("state", state)
        execute(authenticated("/api/notifications/deliveries/$deliveryId/ack")
            .post(payload.toString().toRequestBody(json)).build()).close()
    }

    suspend fun assistantStatus(): JarvisAssistantStatus {
        val response = execute(authenticated("/api/jarvis/state").get().build())
        return parseJarvisAssistantStatus(JSONObject(response.body?.string().orEmpty()))
    }

    fun liveEvents(cursor: Long, listener: (JSONObject) -> Unit, onFailure: (Throwable) -> Unit): EventSource {
        val request = authenticated("/api/jarvis/live?cursor=$cursor").get().build()
        return EventSources.createFactory(client).newEventSource(request, object : EventSourceListener() {
            override fun onEvent(source: EventSource, id: String?, type: String?, data: String) {
                if (type == "jarvis") runCatching { JSONObject(data) }.onSuccess(listener)
            }
            override fun onFailure(source: EventSource, t: Throwable?, response: Response?) {
                onFailure(t ?: IllegalStateException("Jarvis event stream closed: ${response?.code}"))
            }
        })
    }

    private fun authenticated(path: String): Request.Builder {
        val credential = store.credential() ?: error("Jarvis device is not paired")
        return Request.Builder().url("${store.serverUrl}$path").header("Authorization", "Bearer $credential")
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
                        continuation.resumeWithException(IllegalStateException("Cockpit $status: $detail"))
                    } else continuation.resume(response)
                }
            })
        }
    }
}
