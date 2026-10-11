package nl.sander.aicockpit.jarvis.audio

import android.content.Context
import android.content.Intent
import androidx.core.content.ContextCompat
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.CompletableDeferred
import nl.sander.aicockpit.jarvis.MainActivity
import nl.sander.aicockpit.jarvis.data.CredentialStore
import nl.sander.aicockpit.jarvis.notifications.JarvisNotifications
import org.json.JSONObject

object JarvisAudioControls {
    val state = MutableStateFlow(AudioControlState())
    internal var owner: JarvisAudioService? = null
    internal var previousRelease: CompletableDeferred<Unit>? = null
    fun json(): JSONObject = state.value.let { value -> JSONObject()
        .put("type", "audio_state").put("mode", value.mode.name.lowercase())
        .put("text", value.text).put("activation_id", value.activationId)
        .put("session_id", value.sessionId).put("generation", value.generation) }

    fun intent(context: Context, action: AudioAction, snapshot: AudioControlState = state.value): Intent =
        Intent(context, JarvisAudioService::class.java).setAction(action.wire)
            .putExtra("activation_id", snapshot.activationId).putExtra("session_id", snapshot.sessionId)
            .putExtra("generation", snapshot.generation ?: 0)

    fun command(context: Context, action: AudioAction, snapshot: AudioControlState = state.value) {
        CredentialStore(context).controlsDismissed = false
        if (action == AudioAction.STOP && owner == null) {
            CredentialStore(context).sleep = true
            state.value = AudioControlState()
            showIdle(context)
            return
        }
        runCatching { ContextCompat.startForegroundService(context, intent(context, action, snapshot)) }
            .onFailure {
                state.value = AudioControlState(AudioMode.ERROR, error = "Microphone permission needed")
                showIdle(context)
            }
    }

    fun resume(context: Context) {
        val store = CredentialStore(context)
        if (store.credential() == null || !store.setupComplete) return
        store.controlsDismissed = false
        if (owner != null) { showIdle(context); return }
        if (ContextCompat.checkSelfPermission(context, android.Manifest.permission.RECORD_AUDIO) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            state.value = AudioControlState(AudioMode.ERROR, error = "Microphone permission needed")
            showIdle(context)
            return
        }
        if (store.sleep) { state.value = AudioControlState(); showIdle(context) }
        else command(context, AudioAction.WAKE)
    }

    fun pause(context: Context) {
        CredentialStore(context).sleep = true
        owner?.pause() ?: run { state.value = AudioControlState(); showIdle(context) }
    }

    fun unlink(context: Context) {
        owner?.shutdown()
        (context.applicationContext as nl.sander.aicockpit.jarvis.JarvisApplication).screenCapture.clear()
        state.value = AudioControlState()
        context.getSystemService(android.app.NotificationManager::class.java)
            .cancel(JarvisNotifications.CONTROL_NOTIFICATION_ID)
    }

    fun showIdle(context: Context) {
        val store = CredentialStore(context)
        if (store.credential() != null && !store.controlsDismissed)
            context.getSystemService(android.app.NotificationManager::class.java)
                .notify(JarvisNotifications.CONTROL_NOTIFICATION_ID, JarvisNotifications.audioControls(context, state.value))
    }

    fun setup(context: Context) = Intent(context, MainActivity::class.java).putExtra("finish_audio_setup", true)
}
