package nl.sander.aicockpit.jarvis.notifications

import android.app.NotificationManager
import android.content.Context
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
class JarvisEventStream(private val context: Context) : AutoCloseable {
    private val store = CredentialStore(context)
    private val api = CockpitApi(store)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val listeners = CopyOnWriteArraySet<(JSONObject) -> Unit>()
    private var job: Job? = null
    @Volatile private var source: EventSource? = null

    fun start() {
        if (store.credential().isNullOrBlank() || store.serverUrl.isBlank()) return
        if (job?.isActive == true) return
        job = scope.launch {
            var backoff = 1_000L
            while (currentCoroutineContext().isActive) {
                val disconnected = CompletableDeferred<Unit>()
                source = api.liveEvents(
                    cursor = store.eventCursor,
                    listener = { event ->
                        handle(event)
                        backoff = 1_000L
                    },
                    onFailure = { disconnected.complete(Unit) },
                )
                disconnected.await()
                source?.cancel()
                source = null
                delay(backoff)
                backoff = (backoff * 2).coerceAtMost(30_000L)
            }
        }
    }

    fun addListener(listener: (JSONObject) -> Unit) { listeners += listener }
    fun removeListener(listener: (JSONObject) -> Unit) { listeners -= listener }

    private fun handle(event: JSONObject) {
        val sequence = event.optLong("sequence", store.eventCursor)
        if (sequence > store.eventCursor) {
            store.eventCursor = sequence
            scope.launch { runCatching { api.updateDevice(lockState(context), sequence) } }
        }
        listeners.forEach { listener -> runCatching { listener(event) } }
        val eventType = event.optString("event_type")
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
                summary,
                notificationId,
                taskPath,
                deliveryId,
            ),
        )
    }

    override fun close() {
        source?.cancel()
        job?.cancel()
        scope.cancel()
    }

    companion object {
        val NOTIFIABLE = setOf("notification.available")

        fun lockState(context: Context): String = if (
            context.getSystemService(android.app.KeyguardManager::class.java).isDeviceLocked
        ) "locked" else "unlocked"
    }
}
