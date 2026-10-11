package nl.sander.aicockpit.jarvis.assistant

import android.content.Context
import android.app.KeyguardManager
import android.util.Log
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import nl.sander.aicockpit.jarvis.data.CockpitApi
import nl.sander.aicockpit.jarvis.data.CredentialStore
import nl.sander.aicockpit.jarvis.data.LiveSessionState
import org.json.JSONObject
import java.time.Instant

internal data class PhoneCaptureRequest(val captureId: String, val activationId: String,
    val sessionId: String, val generation: Int, val mode: String, val expiresAt: Instant) {
    fun matches(session: LiveSessionState?, deviceId: String, now: Instant = Instant.now()): Boolean =
        session != null && session.sessionId == sessionId && session.generation == generation &&
            session.controllerId == deviceId && session.activationId == activationId &&
            session.lifecycleState in setOf("connecting", "ready") && now.isBefore(expiresAt)

    companion object {
        fun from(event: JSONObject, allowNative: Boolean = false): PhoneCaptureRequest? = runCatching {
            val kind = event.optString("event_type")
            require(kind in setOf("capture.activation_requested", "capture.fresh_requested"))
            val payload = event.getJSONObject("payload")
            require(payload.getString("capture_transport") == "phone_browser" ||
                allowNative && payload.optString("capture_transport") == "native_voice")
            val mode = payload.getString("capture_mode")
            require(mode == if (kind == "capture.activation_requested") "activation" else "fresh")
            PhoneCaptureRequest(payload.getString("capture_id"), payload.getString("activation_id"),
                payload.getString("session_id"), payload.getInt("generation"), mode,
                Instant.parse(payload.getString("expires_at"))).also {
                require(it.captureId.isNotBlank() && it.captureId.length <= 64 &&
                    it.activationId.isNotBlank() && it.activationId.length <= 64 &&
                    it.sessionId.isNotBlank() && it.generation > 0)
            }
        }.getOrNull()
    }
}

/** Native bearer stream bridges paired browser voice to Android assistant context.
 * It never opens a provider call or uses a browser cookie to upload pixels. */
class PhoneCaptureCoordinator(private val context: Context, private val capture: ScreenCaptureManager) {
    private val store = CredentialStore(context)
    private val api = CockpitApi(store)
    private val keyguard = context.getSystemService(KeyguardManager::class.java)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val requests = Channel<PhoneCaptureRequest>(64)
    private val seen = LinkedHashSet<String>()
    private var waiting: Pair<String, CompletableDeferred<Unit>>? = null

    init {
        scope.launch {
            for (request in requests) {
                val current = runCatching { api.liveSessionState() }.getOrNull()
                if (!request.matches(current, store.deviceId)) continue
                capture.startPhoneCapture(request)
                if (PhoneScreenPrivacy.locked(context)) { capture.unavailable("locked"); continue }
                val done = CompletableDeferred<Unit>()
                waiting = request.captureId to done
                Log.i("JarvisPhoneCapture", "request mode=${request.mode} session=${request.sessionId} capture=${request.captureId}")
                if (!JarvisVoiceInteractionService.invokePhoneCapture(request) ||
                    withTimeoutOrNull(3_000) { done.await(); true } != true) {
                    capture.unavailable("session_unavailable", request.captureId)
                    JarvisVoiceInteractionSession.stopPhoneCapture(request.captureId)
                }
                waiting = null
            }
        }
    }

    fun onEvent(event: JSONObject) {
        val local = nl.sander.aicockpit.jarvis.audio.JarvisAudioControls.state.value
        // Visible native sheets handle their own refresh. Hidden sessions use a
        // short capture-only invocation and must not lose their ongoing audio.
        val request = PhoneCaptureRequest.from(event, allowNative = local.liveActive &&
            !((context.applicationContext as nl.sander.aicockpit.jarvis.JarvisApplication).voiceSessionHandlesCapture)) ?: return
        scope.launch {
            if (request.captureId in seen || Instant.now().isAfter(request.expiresAt)) return@launch
            if (requests.trySend(request).isSuccess) {
                seen.add(request.captureId)
                while (seen.size > 256) seen.remove(seen.first())
            }
        }
    }

    fun completed(captureId: String) {
        waiting?.takeIf { it.first == captureId }?.second?.complete(Unit)
    }
}
