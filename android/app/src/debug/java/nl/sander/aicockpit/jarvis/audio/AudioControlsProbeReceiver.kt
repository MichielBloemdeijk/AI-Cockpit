package nl.sander.aicockpit.jarvis.audio

import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.media.AudioManager
import android.util.Log
import nl.sander.aicockpit.jarvis.assistant.JarvisVoiceInteractionSession
import nl.sander.aicockpit.jarvis.data.CredentialStore
import nl.sander.aicockpit.jarvis.notifications.JarvisNotifications
import org.json.JSONObject

/** Read-only observations and replay of the actual captured notification PendingIntent. */
class AudioControlsProbeReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (runCatching { java.net.URI(CredentialStore(context).serverUrl).host }.getOrNull() == "10.0.2.2") fixtureProcess = true
        if (!fixtureProcess) return
        when (intent.getStringExtra("probe")) {
            "remember_stop" -> stop = context.getSystemService(NotificationManager::class.java).activeNotifications
                .firstOrNull { it.id == JarvisNotifications.CONTROL_NOTIFICATION_ID }?.notification?.actions
                ?.firstOrNull { it.title.toString() == "Stop Live" }?.actionIntent
            "replay_stop" -> stop?.send()
            "hide_panel" -> JarvisVoiceInteractionSession.stopActive()
            "unlink" -> { JarvisAudioControls.unlink(context); CredentialStore(context).clear() }
        }
        val notifications = context.getSystemService(NotificationManager::class.java).activeNotifications
        val notification = notifications.firstOrNull { it.id == JarvisNotifications.CONTROL_NOTIFICATION_ID }
        Log.i("JarvisLocalAudio", JSONObject().put("probe", intent.getStringExtra("probe"))
            .put("state", JarvisAudioControls.json()).put("owner", JarvisAudioControls.owner != null)
            .put("metrics", JarvisAudioControls.owner?.audioMetrics())
            .put("recorders", context.getSystemService(AudioManager::class.java).activeRecordingConfigurations.size)
            .put("notification", notification != null).put("sleep", CredentialStore(context).sleep).toString())
    }
    companion object { private var stop: PendingIntent? = null; private var fixtureProcess = false }
}
