package nl.sander.aicockpit.jarvis.assistant

import android.app.KeyguardManager
import android.content.Context
import android.Manifest
import android.content.pm.PackageManager
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.graphics.Bitmap
import android.os.Bundle
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.service.voice.VoiceInteractionSession
import android.view.View
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
import androidx.lifecycle.setViewTreeLifecycleOwner
import androidx.lifecycle.ViewModelStore
import androidx.lifecycle.ViewModelStoreOwner
import androidx.lifecycle.setViewTreeViewModelStoreOwner
import androidx.savedstate.SavedStateRegistry
import androidx.savedstate.SavedStateRegistryController
import androidx.savedstate.SavedStateRegistryOwner
import androidx.savedstate.setViewTreeSavedStateRegistryOwner
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.ComposeView
import androidx.compose.ui.platform.ViewCompositionStrategy
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import nl.sander.aicockpit.jarvis.JarvisApplication
import nl.sander.aicockpit.jarvis.data.CockpitApi
import nl.sander.aicockpit.jarvis.data.CredentialStore
import nl.sander.aicockpit.jarvis.notifications.JarvisEventStream
import nl.sander.aicockpit.jarvis.live.LiveController
import nl.sander.aicockpit.jarvis.live.LiveEventIdentity
import nl.sander.aicockpit.jarvis.audio.JarvisAudioControls
import nl.sander.aicockpit.jarvis.audio.JarvisAudioService
import nl.sander.aicockpit.jarvis.audio.AudioAction
import nl.sander.aicockpit.jarvis.web.JarvisWebViewActivity
import org.json.JSONObject
import java.lang.ref.WeakReference
import java.util.UUID

class JarvisVoiceInteractionSession(context: Context) : VoiceInteractionSession(context),
    LifecycleOwner, SavedStateRegistryOwner, ViewModelStoreOwner {
    private val lifecycleRegistry = LifecycleRegistry(this)
    private val savedStateController = SavedStateRegistryController.create(this)
    private val sessionViewModelStore = ViewModelStore()
    override val lifecycle: Lifecycle get() = lifecycleRegistry
    override val savedStateRegistry: SavedStateRegistry get() = savedStateController.savedStateRegistry
    override val viewModelStore: ViewModelStore get() = sessionViewModelStore
    private val store = CredentialStore(context)
    private val api = CockpitApi(store)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val main = Handler(Looper.getMainLooper())
    private val application = context.applicationContext as JarvisApplication
    private var status by mutableStateOf("Ready")
    private var userTranscript by mutableStateOf("")
    private var assistantTranscript by mutableStateOf("")
    private var foregroundApp by mutableStateOf<String?>(null)
    private var pendingWakeScore: Float? = null
    private var pendingActivationId: String? = null
    private var captureOverlay: ComposeView? = null
    private var fixtureSession = false
    private var initialCaptureStaged = false
    private var stagingInitialCapture = false
    private var phoneCaptureId: String? = null
    private var activationCaptureOnly = false
    private var showEpoch = 0L

    private val eventListener: (JSONObject) -> Unit = { event ->
        main.post { handleEvent(event) }
    }

    override fun onCreate() {
        super.onCreate()
        savedStateController.performAttach()
        savedStateController.performRestore(null)
        lifecycleRegistry.currentState = Lifecycle.State.CREATED
        active = WeakReference(this)
        scope.launch { JarvisAudioControls.state.collect { value ->
            if (phoneCaptureId == null && !fixtureSession) status = value.text
            if (value.activationId != null && value.activationId == pendingActivationId && value.sessionId != null && value.generation != null)
                application.screenCapture.bindSession(value.activationId, value.sessionId, value.generation)
        } }
    }

    override fun onCreateContentView(): View = ComposeView(context).apply {
        // Keep the assistant sheet out of the invocation screenshot. The callback
        // normally arrives within a frame; a bounded fallback covers null/no callback.
        alpha = 0f
        captureOverlay = this
        post { if (stagingInitialCapture) stageInitialCaptureWhenVisible() }
        setViewTreeLifecycleOwner(this@JarvisVoiceInteractionSession)
        setViewTreeViewModelStoreOwner(this@JarvisVoiceInteractionSession)
        setViewTreeSavedStateRegistryOwner(this@JarvisVoiceInteractionSession)
        setViewCompositionStrategy(ViewCompositionStrategy.DisposeOnDetachedFromWindow)
        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.BottomCenter) {
                    Surface(
                        modifier = Modifier.fillMaxWidth(),
                        shape = RoundedCornerShape(topStart = 28.dp, topEnd = 28.dp),
                        tonalElevation = 8.dp,
                    ) {
                        Column(Modifier.padding(22.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                            Text("Jarvis", style = MaterialTheme.typography.titleLarge)
                            Text(status, color = MaterialTheme.colorScheme.primary)
                            foregroundApp?.let { Text("Context: $it", style = MaterialTheme.typography.labelMedium) }
                            if (userTranscript.isNotBlank()) Text(userTranscript, style = MaterialTheme.typography.bodyMedium)
                            if (assistantTranscript.isNotBlank()) Text(assistantTranscript, style = MaterialTheme.typography.bodyLarge)
                            Spacer(Modifier.height(4.dp).weight(1f, fill = false))
                            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                                OutlinedButton(onClick = { openCockpit() }, modifier = Modifier.weight(1f)) { Text("Open") }
                                Button(onClick = { finish() }, modifier = Modifier.weight(1f)) { Text("Hide") }
                            }
                        }
                    }
                }
            }
        }
    }

    override fun onShow(args: Bundle?, showFlags: Int) {
        super.onShow(args, showFlags)
        nl.sander.aicockpit.jarvis.live.AcceptanceAudioInput.observeWindow(window?.window)
        nl.sander.aicockpit.jarvis.live.AcceptanceAudioInput.observeDiagnostic("session_show", JSONObject()
            .put("instance", System.identityHashCode(this)).put("refresh", args?.getBoolean("context_refresh") == true)
            .put("show_flags", showFlags))
        val activationArgs = JarvisVoiceInteractionService.consumeFixtureArguments(args)
        captureOverlay?.alpha = 0f
        main.postDelayed({ if (phoneCaptureId == null && !activationCaptureOnly) captureOverlay?.alpha = 1f }, 250)
        if (activationArgs?.getBoolean("context_refresh") == true) {
            if (isLocked()) application.screenCapture.unavailable("locked")
            else if (showFlags and SHOW_WITH_SCREENSHOT == 0)
                application.screenCapture.unavailable("platform_or_app_policy")
            Log.i("JarvisCaptureFixture", "refresh_show_flags=$showFlags activation_id=$pendingActivationId")
            return
        }
        showEpoch++
        if (activationArgs?.getString("source") == "phone_capture") {
            // A short transparent assistant invocation only. The shared audio owner keeps
            // its microphone and backend session throughout this capture.
            phoneCaptureId = activationArgs.getString("phone_capture_id")
            pendingActivationId = activationArgs.getString("activation_id")
            application.voiceSessionVisible = true
            application.voiceSessionHandlesCapture = false
            lifecycleRegistry.currentState = Lifecycle.State.RESUMED
            initialCaptureStaged = false
            stagingInitialCapture = Build.VERSION.SDK_INT >= 37
            initialScreenshotPending = true
            if (isLocked() || (!stagingInitialCapture && showFlags and SHOW_WITH_SCREENSHOT == 0)) {
                application.screenCapture.unavailable(if (isLocked()) "locked" else "platform_or_app_policy")
                finish()
            } else if (stagingInitialCapture) stageInitialCaptureWhenVisible()
            return
        }
        phoneCaptureId = null
        activationCaptureOnly = activationArgs?.getString("source") == "shared_live"
        application.voiceSessionHandlesCapture = !activationCaptureOnly
        if (activationArgs?.getBoolean("fixture_skip_live") == true) {
            Log.i("JarvisCaptureFixture", "show_flags=$showFlags user_disabled_context=$userDisabledShowContext")
        }
        lifecycleRegistry.currentState = Lifecycle.State.RESUMED
        application.voiceSessionVisible = true
        application.eventStream.addListener(eventListener)
        captureQueue.clear(); requestedCaptureIds.clear(); refreshCaptureId = null
        pendingWakeScore = activationArgs?.takeIf { it.getString("source") == "local_wake" }?.getFloat("wake_score")
        pendingActivationId = activationArgs?.getString("activation_id")
            ?: JarvisAudioControls.state.value.takeIf { it.liveActive }?.activationId ?: UUID.randomUUID().toString()
        initialCaptureStaged = false
        stagingInitialCapture = Build.VERSION.SDK_INT >= 37
        val fixture = activationArgs?.getBoolean("fixture_skip_live") == true &&
            (context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0
        fixtureSession = fixture
        if (!application.screenCapture.matchesActivation(pendingActivationId!!)) application.screenCapture.startActivation(
            pendingActivationId!!,
            if (fixture) activationArgs?.getString("capture_id") ?: UUID.randomUUID().toString()
            else UUID.randomUUID().toString(),
        )
        if (isLocked()) application.screenCapture.unavailable("locked")
        else if (!stagingInitialCapture && showFlags and SHOW_WITH_SCREENSHOT == 0)
            application.screenCapture.unavailable("platform_or_app_policy")
        initialScreenshotPending = !isLocked() && (stagingInitialCapture || showFlags and SHOW_WITH_SCREENSHOT != 0)
        if (activationCaptureOnly) {
            val captureShow = showEpoch
            main.postDelayed({
                if (activationCaptureOnly && phoneCaptureId == null && showEpoch == captureShow && application.voiceSessionVisible) {
                    if (initialScreenshotPending) application.screenCapture.unavailable("callback_timeout")
                    finish()
                }
            }, 6_000) // Overall bound includes cold window creation; capture callback has its own bound below.
        }
        if (stagingInitialCapture) stageInitialCaptureWhenVisible()
        status = if (isLocked()) "Listening while locked (read-only)" else "Connecting…"
        if (fixture) {
            activationArgs?.getString("fixture_session_id")?.let {
                application.screenCapture.bindSession(pendingActivationId!!, it,
                    activationArgs.getInt("fixture_generation"))
            }
            status = "Fixture capture active"
            return
        }
        val command = JarvisAudioControls.intent(context, AudioAction.LISTEN,
            nl.sander.aicockpit.jarvis.audio.AudioControlState(activationId = pendingActivationId))
            .putExtra("assistant_visible", true)
        pendingWakeScore?.let { command.putExtra("wake_score", it) }
        runCatching { ContextCompat.startForegroundService(context, command) }
            .onFailure { status = "Microphone permission needed" }
    }

    override fun onHide() {
        nl.sander.aicockpit.jarvis.live.AcceptanceAudioInput.stopObservingWindow(window?.window)
        nl.sander.aicockpit.jarvis.live.AcceptanceAudioInput.observeDiagnostic("session_hide", JSONObject()
            .put("instance", System.identityHashCode(this)))
        lifecycleRegistry.currentState = Lifecycle.State.CREATED
        if (fixtureSession) JarvisVoiceInteractionService.restoreFixtureContextPolicy()
        if (active.get() === this) { application.voiceSessionVisible = false; application.voiceSessionHandlesCapture = false }
        application.eventStream.removeListener(eventListener)
        super.onHide()
    }

    override fun onDestroy() {
        nl.sander.aicockpit.jarvis.live.AcceptanceAudioInput.stopObservingWindow(window?.window)
        nl.sander.aicockpit.jarvis.live.AcceptanceAudioInput.observeDiagnostic("session_destroy", JSONObject()
            .put("instance", System.identityHashCode(this)))
        if (fixtureSession) JarvisVoiceInteractionService.restoreFixtureContextPolicy()
        lifecycleRegistry.currentState = Lifecycle.State.DESTROYED
        sessionViewModelStore.clear()
        if (active.get() === this) { application.voiceSessionVisible = false; application.voiceSessionHandlesCapture = false }
        application.eventStream.removeListener(eventListener)
        phoneCaptureId?.let { application.phoneCapture.completed(it) }
        scope.cancel()
        if (active.get() === this) active.clear()
        super.onDestroy()
    }

    override fun onHandleAssist(state: VoiceInteractionSession.AssistState) {
        if (!state.isFocused) return
        foregroundApp = state.assistContent?.intent?.component?.packageName ?:
            if (store.assistStructureOptIn) state.assistStructure?.activityComponent?.packageName else null
    }

    override fun onHandleScreenshot(screenshot: Bitmap?) {
        if (stagingInitialCapture && !initialCaptureStaged) return
        Log.i("JarvisCaptureFixture", "screenshot_callback=${if (screenshot == null) "null" else "bitmap"}")
        val captureId = phoneCaptureId ?: refreshCaptureId ?: application.screenCapture.currentCaptureId()
        initialScreenshotPending = false
        application.screenCapture.onScreenshot(screenshot, isLocked(), captureId)
        if (phoneCaptureId != null || activationCaptureOnly) { finish(); return }
        refreshCaptureId = null
        main.post { drainCaptureQueue() }
        captureOverlay?.alpha = 1f
    }

    private fun handleEvent(event: JSONObject) {
        val currentCall = JarvisAudioControls.owner?.currentCall()
        if (event.optString("source_domain") == "session" &&
            !LiveEventIdentity.matches(event, currentCall?.sessionId, currentCall?.generation)) return
        val payload = event.optJSONObject("payload") ?: JSONObject()
        when (event.optString("event_type")) {
            "brain.tool.result" -> {
                nl.sander.aicockpit.jarvis.live.AcceptanceAudioInput.observeAdmission(
                    payload.optJSONObject("output") ?: JSONObject(), event)
                JarvisAudioControls.owner?.observeHealth()
            }
            "capture.fresh_requested" -> {
                if (activationCaptureOnly) return
                val identity = payload.optString("activation_id")
                val session = JarvisAudioControls.owner?.currentCall()
                if (session != null && session.sessionId == payload.optString("session_id") &&
                    session.generation == payload.optInt("generation")) {
                    requestFreshCapture(identity, payload.optString("capture_id"),
                        java.time.Instant.parse(payload.getString("expires_at")))
                }
            }
            "provider.user_transcript_delta" -> userTranscript += payload.optString("delta", payload.optString("transcript"))
            "provider.user_transcript" -> {
                val transcript = payload.optString("transcript")
                pendingWakeScore = null
                userTranscript = transcript
            }
            "provider.assistant_transcript_delta" -> assistantTranscript += payload.optString("delta", payload.optString("transcript"))
            "provider.assistant_transcript" -> JarvisAudioControls.owner?.observeHealth()
            "provider.assistant_response", "provider.assistant_interrupted" -> {
                JarvisAudioControls.owner?.observeHealth()
                assistantTranscript = payload.optString("transcript", assistantTranscript)
                status = "Listening"
            }
            "provider.user_speaking" -> status = "Listening"
            "provider.assistant_generating" -> status = "Thinking"
            "provider.assistant_speaking" -> status = "Speaking"
            "session.superseded" -> {
                val call = JarvisAudioControls.owner?.currentCall()
                if (LiveEventIdentity.matches(event, call?.sessionId, call?.generation)) finish()
            }
        }
    }

    private fun openCockpit() {
        context.startActivity(Intent(context, JarvisWebViewActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    private fun isLocked(): Boolean = PhoneScreenPrivacy.locked(context)

    private data class PendingCapture(val activation: String, val id: String, val expiry: java.time.Instant?)
    private val captureQueue = java.util.ArrayDeque<PendingCapture>()
    private val requestedCaptureIds = mutableSetOf<String>()
    private var refreshCaptureId: String? = null
    private var initialScreenshotPending = false

    private fun requestFreshCapture(activationId: String, captureId: String, expiresAt: java.time.Instant? = null): Boolean {
        if (pendingActivationId != activationId || !application.voiceSessionVisible || isLocked()) return false
        if (!requestedCaptureIds.add(captureId)) return true
        captureQueue.addLast(PendingCapture(activationId, captureId, expiresAt))
        drainCaptureQueue()
        return true
    }

    private fun drainCaptureQueue() {
        if (refreshCaptureId != null || initialScreenshotPending || captureQueue.isEmpty()) return
        val request = captureQueue.removeFirst()
        if (request.activation != pendingActivationId || isLocked() ||
            request.expiry?.isBefore(java.time.Instant.now()) == true) {
            main.post { drainCaptureQueue() }
            return
        }
        if (!application.screenCapture.startFreshCapture(request.activation, request.id, request.expiry)) return
        refreshCaptureId = request.id
        val overlay = captureOverlay ?: return
        overlay.alpha = 0f
        overlay.postOnAnimation { overlay.postOnAnimation {
            if (pendingActivationId == request.activation && application.voiceSessionVisible && !isLocked()) {
                if (!JarvisVoiceInteractionService.requestContextRefresh(this))
                    application.screenCapture.unavailable("session_unavailable")
            } else application.screenCapture.unavailable("session_unavailable")
        } }
    }

    private fun stageInitialCaptureWhenVisible() {
        val overlay = captureOverlay ?: return
        val activation = pendingActivationId
        // Window focus can arrive after the first pre-draw. In an otherwise idle
        // session it need not cause another draw, so observe both transitions.
        lateinit var drawListener: android.view.ViewTreeObserver.OnPreDrawListener
        lateinit var focusListener: android.view.ViewTreeObserver.OnWindowFocusChangeListener
        fun removeListeners() {
            if (overlay.viewTreeObserver.isAlive) {
                overlay.viewTreeObserver.removeOnPreDrawListener(drawListener)
                overlay.viewTreeObserver.removeOnWindowFocusChangeListener(focusListener)
            }
        }
        fun stageIfVisible() {
            if (pendingActivationId != activation || !application.voiceSessionVisible || initialCaptureStaged) {
                removeListeners()
            } else if (overlay.isAttachedToWindow && overlay.windowVisibility == View.VISIBLE && overlay.hasWindowFocus()) {
                initialCaptureStaged = true
                removeListeners()
                overlay.alpha = 0f
                overlay.postOnAnimation { overlay.postOnAnimation {
                    if (pendingActivationId == activation && application.voiceSessionVisible && !isLocked()) {
                        val captureShow = showEpoch
                        if (!JarvisVoiceInteractionService.requestContextRefresh(this))
                            application.screenCapture.unavailable("session_unavailable")
                        if (activationCaptureOnly && phoneCaptureId == null) main.postDelayed({
                            if (showEpoch == captureShow && phoneCaptureId == null && activationCaptureOnly && initialScreenshotPending) {
                                application.screenCapture.unavailable("callback_timeout"); finish()
                            }
                        }, 3_000)
                    } else application.screenCapture.unavailable("session_unavailable")
                } }
            }
        }
        drawListener = object : android.view.ViewTreeObserver.OnPreDrawListener {
            override fun onPreDraw(): Boolean {
                stageIfVisible()
                return true
            }
        }
        focusListener = android.view.ViewTreeObserver.OnWindowFocusChangeListener { stageIfVisible() }
        overlay.viewTreeObserver.addOnPreDrawListener(drawListener)
        overlay.viewTreeObserver.addOnWindowFocusChangeListener(focusListener)
    }

    private fun friendlyState(value: String): String = when {
        value == "connected" -> "Listening"
        value.startsWith("ice:CONNECTED") || value.startsWith("ice:COMPLETED") -> "Listening"
        value.startsWith("ice:FAILED") -> "Connection failed"
        else -> status
    }

    companion object {
        private var active = WeakReference<JarvisVoiceInteractionSession>(null)
        fun stopActive() { active.get()?.finish() }
        fun stopPhoneCapture(captureId: String) {
            active.get()?.takeIf { it.phoneCaptureId == captureId }?.finish()
        }
        fun snapshotAcceptanceHealth(): Boolean {
            val session = active.get() ?: return false
            if ((session.context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) == 0 ||
                !session.application.voiceSessionVisible || JarvisAudioControls.owner?.currentCall() == null) return false
            JarvisAudioControls.owner?.observeHealth()
            return true
        }
        fun continueAcceptanceAudio(): Boolean {
            val session = active.get() ?: return false
            if ((session.context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) == 0 ||
                !session.application.voiceSessionVisible) return false
            nl.sander.aicockpit.jarvis.live.AcceptanceAudioInput.observeWindow(session.window?.window)
            return JarvisAudioControls.owner?.continueAudio() == true
        }
        fun refreshFixture(activationId: String, captureId: String): Boolean {
            val session = active.get() ?: return false
            if ((session.context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) == 0) return false
            return session.requestFreshCapture(activationId, captureId)
        }
    }
}
