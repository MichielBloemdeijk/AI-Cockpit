package nl.sander.aicockpit.jarvis.web

import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.util.Log
import java.time.Instant
import nl.sander.aicockpit.jarvis.assistant.JarvisVoiceInteractionSession
import nl.sander.aicockpit.jarvis.assistant.PhoneScreenPrivacy
import nl.sander.aicockpit.jarvis.notifications.JarvisNotifications
import org.json.JSONObject

internal data class OpenRequest(val eventId: String, val path: String, val title: String, val expiresAt: Instant) {
    companion object {
        private val PATH = Regex("^/(results|tasks)/[A-Za-z0-9_-]{1,64}$")

        fun from(event: JSONObject): OpenRequest? = runCatching {
            require(event.optString("event_type") == "display.open_requested")
            val payload = event.getJSONObject("payload")
            val path = payload.getString("path")
            require(PATH.matches(path))
            OpenRequest(event.getString("id"), path, payload.optString("title").take(120),
                Instant.parse(payload.getString("expires_at")))
        }.getOrNull()
    }
}

/** Brings Jarvis to the front on a result page when the user asks Jarvis to show it.
 * Android lets the default assistant start its own activity over other apps; a
 * locked phone gets the result notification instead of a hidden launch. */
class PhoneDisplay(private val context: Context) {
    private val seen = LinkedHashSet<String>()

    fun onEvent(event: JSONObject) {
        val request = OpenRequest.from(event) ?: return
        synchronized(seen) {
            if (!seen.add(request.eventId)) return
            while (seen.size > 64) seen.remove(seen.first())
        }
        if (Instant.now().isAfter(request.expiresAt)) return
        if (PhoneScreenPrivacy.locked(context) || !open(request.path)) notify(request)
    }

    private fun open(path: String): Boolean = try {
        // Its own document task: Back returns to the app the user was in, not to Jarvis's home screen.
        context.startActivity(Intent(context, JarvisWebViewActivity::class.java)
            .putExtra(JarvisWebViewActivity.EXTRA_PATH, path)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NEW_DOCUMENT))
        // Like tapping Hide: the sheet would cover the page, the conversation goes on.
        android.os.Handler(android.os.Looper.getMainLooper()).post { JarvisVoiceInteractionSession.stopActive() }
        Log.i("JarvisPhoneDisplay", "opened $path")
        true
    } catch (error: RuntimeException) {
        Log.w("JarvisPhoneDisplay", "could not open $path", error)
        false
    }

    private fun notify(request: OpenRequest) {
        val id = JarvisNotifications.UPDATE_NOTIFICATION_ID + 50_000 + (request.eventId.hashCode() and 0xffff)
        context.getSystemService(NotificationManager::class.java).notify(id, JarvisNotifications.update(
            context, request.title.ifBlank { "Jarvis result" }, "Tap to open what you asked Jarvis to show.",
            id, request.path))
    }
}
