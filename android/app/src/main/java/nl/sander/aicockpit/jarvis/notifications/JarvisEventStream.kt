package nl.sander.aicockpit.jarvis.notifications

import android.app.NotificationManager
import android.content.Context
import android.util.Log
import android.os.SystemClock
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import nl.sander.aicockpit.jarvis.data.CockpitApi
import nl.sander.aicockpit.jarvis.data.CredentialStore
import okhttp3.sse.EventSource
import org.json.JSONObject
import java.util.concurrent.CopyOnWriteArraySet

/** One resumable device stream for notifications and the visible assistant overlay. */
class JarvisEventStream(private val context: Context, private val onConnected: () -> Unit = {}) : AutoCloseable {
    private val store = CredentialStore(context)
    private val api = CockpitApi(store)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val listeners = CopyOnWriteArraySet<(JSONObject) -> Unit>()
    private var job: Job? = null
    private var activeConnectionKey: String? = null
    @Volatile private var source: EventSource? = null

    @Synchronized
    fun start() {
        val credential = store.credential() ?: return
        if (credential.isBlank() || store.serverUrl.isBlank()) return
        val connectionKey = "${store.serverUrl}|$credential"
        if (job?.isActive == true && activeConnectionKey == connectionKey) return
        source?.cancel()
        job?.cancel()
        source = null
        activeConnectionKey = connectionKey
        job = scope.launch {
            var backoff = 1_000L
            while (currentCoroutineContext().isActive) {
                val disconnected = CompletableDeferred<Unit>()
                val activeSource = api.liveEvents(
                    cursor = store.eventCursor,
                    listener = { event ->
                        handle(event)
                        backoff = 1_000L
                    },
                    onFailure = { error ->
                        Log.w("JarvisEventStream", "Live event stream disconnected", error)
                        if (error is nl.sander.aicockpit.jarvis.data.CockpitRequestException && error.status == 401) unlinked()
                        disconnected.complete(Unit)
                    },
                    onOpen = {
                        Log.i("JarvisEventStream", "Live event stream connected")
                        onConnected()
                    },
                )
                source = activeSource
                disconnected.await()
                activeSource.cancel()
                if (source === activeSource) source = null
                delay(backoff)
                backoff = (backoff * 2).coerceAtMost(30_000L)
            }
        }
    }

    fun addListener(listener: (JSONObject) -> Unit) { listeners += listener }
    fun removeListener(listener: (JSONObject) -> Unit) { listeners -= listener }

    private fun handle(event: JSONObject) {
        if (store.credential() == null) return
        if (event.optString("event_type") == "device.unlinked" && event.optJSONObject("payload")?.optString("device_id") == store.deviceId) {
            unlinked(); return
        }
        event.put("client_stream_received_ns", SystemClock.elapsedRealtimeNanos())
        val streamEventKind = event.optString("event_type")
        if (streamEventKind in setOf("provider.delegation_received", "provider.delegation_origin_resolved", "provider.admission_stage")) {
            val payload = event.optJSONObject("payload") ?: JSONObject()
            nl.sander.aicockpit.jarvis.live.AcceptanceAudioInput.observeDiagnostic("stage_received",
                JSONObject().put("stage", payload.optString("stage", streamEventKind))
                    .put("session_id", payload.optString("session_id"))
                    .put("generation", payload.optInt("generation"))
                    .put("origin_correlation_id", payload.optString("origin_correlation_id"))
                    .put("server_observed_ns", payload.optLong("server_monotonic_ns"))
                    .put("client_stream_received_ns", event.optLong("client_stream_received_ns")))
        }
        val sequence = event.optLong("sequence", store.eventCursor)
        if (sequence > store.eventCursor) {
            store.eventCursor = sequence
            scope.launch { runCatching { api.updateDevice(lockState(context), sequence) } }
        }
        listeners.forEach { listener -> runCatching { listener(event) } }
        val eventType = event.optString("event_type")
        if (eventType == "context.requested") {
            Log.i("JarvisEventStream", "Run-bound screen context requested")
        }
        if (eventType !in NOTIFIABLE) return
        val payload = event.optJSONObject("payload") ?: JSONObject()
        val summary = payload.optString("summary", "There is a new Jarvis update.")
        val eventKind = payload.optString("event_kind")
        val title = when (eventKind) {
            "task.reminder.due" -> "Reminder"
            "task.requires_input" -> "Jarvis needs your input"
            "task.completed" -> "Task completed"
            "task.failed", "task.interrupted" -> "Task needs attention"
            else -> "Jarvis task update"
        }
        val notificationId = JarvisNotifications.UPDATE_NOTIFICATION_ID + (sequence % 100_000).toInt()
        val taskPath = payload.optString("url").takeIf { it.isNotBlank() }
            ?: payload.optString("task_id").takeIf { it.isNotBlank() }?.let { "/tasks/$it" }
        val deliveryId = payload.optString("delivery_id").takeIf { it.isNotBlank() }
        context.getSystemService(NotificationManager::class.java).notify(
            notificationId,
            JarvisNotifications.update(
                context,
                title,
                summary,
                notificationId,
                taskPath,
                deliveryId,
            ),
        )
    }

    @Synchronized
    fun stop() {
        source?.cancel()
        job?.cancel()
        activeConnectionKey = null
        source = null
        job = null
    }
    private fun unlinked() {
        android.os.Handler(android.os.Looper.getMainLooper()).post {
            nl.sander.aicockpit.jarvis.audio.JarvisAudioControls.unlink(context)
            store.clear(); stop()
        }
    }

    override fun close() {
        stop()
        scope.cancel()
    }

    companion object {
        val NOTIFIABLE = setOf("notification.available")

        fun lockState(context: Context): String = if (
            context.getSystemService(android.app.KeyguardManager::class.java).isDeviceLocked
        ) "locked" else "unlocked"
    }
}
