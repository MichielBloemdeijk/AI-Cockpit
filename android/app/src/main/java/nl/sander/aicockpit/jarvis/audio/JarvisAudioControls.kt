package nl.sander.aicockpit.jarvis.audio

import android.content.Context
import android.content.Intent
import androidx.core.content.ContextCompat
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.CompletableDeferred
import nl.sander.aicockpit.jarvis.MainActivity
import nl.sander.aicockpit.jarvis.data.CredentialStore
import nl.sander.aicockpit.jarvis.notifications.JarvisNotifications
import org.json.JSONObject

/**
 * The public face of Jarvis audio. `JarvisAudioService` alone owns the microphone,
 * wake and WebRTC; everyone else reads [state] (including the live call identity)
 * and sends commands through intents. Nothing else can write the state or reach
 * the service.
 */
object JarvisAudioControls {
    private val mutableState = MutableStateFlow(AudioControlState())
    val state: StateFlow<AudioControlState> = mutableState.asStateFlow()
    private var owner: JarvisAudioService? = null
    // A replacement service waits for its predecessor to release audio first.
    private var previousRelease: CompletableDeferred<Unit>? = null

    /** Only `JarvisAudioService` attaches; it receives its predecessor's release to wait for. */
    internal fun attach(service: JarvisAudioService): CompletableDeferred<Unit>? {
        owner = service
        return previousRelease
    }

    internal fun detach(service: JarvisAudioService, released: CompletableDeferred<Unit>) {
        if (owner === service) owner = null
        previousRelease = released
    }

    internal fun publish(value: AudioControlState) { mutableState.value = value }

    val running: Boolean get() = owner != null

    /** Acceptance runs only (gated by `Acceptance.hooks.active`): transport and health evidence. */
    internal fun observeAcceptanceHealth() { owner?.observeHealth() }
    internal fun continueAcceptanceAudio(): Boolean = owner?.continueAudio() == true
    internal fun acceptanceMetrics(): JSONObject? = owner?.audioMetrics()
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
            mutableState.value = AudioControlState()
            showIdle(context)
            return
        }
        runCatching { ContextCompat.startForegroundService(context, intent(context, action, snapshot)) }
            .onFailure {
                mutableState.value = AudioControlState(AudioMode.ERROR, error = "Microphone permission needed")
                showIdle(context)
            }
    }

    fun resume(context: Context) {
        val store = CredentialStore(context)
        if (store.credential() == null || !store.setupComplete) return
        store.controlsDismissed = false
        if (owner != null) { showIdle(context); return }
        if (ContextCompat.checkSelfPermission(context, android.Manifest.permission.RECORD_AUDIO) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            mutableState.value = AudioControlState(AudioMode.ERROR, error = "Microphone permission needed")
            showIdle(context)
            return
        }
        if (store.sleep) { mutableState.value = AudioControlState(); showIdle(context) }
        else command(context, AudioAction.WAKE)
    }

    fun pause(context: Context) {
        CredentialStore(context).sleep = true
        owner?.pause() ?: run { mutableState.value = AudioControlState(); showIdle(context) }
    }

    fun unlink(context: Context) {
        owner?.shutdown()
        (context.applicationContext as nl.sander.aicockpit.jarvis.JarvisApplication).screenCapture.clear()
        mutableState.value = AudioControlState()
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
