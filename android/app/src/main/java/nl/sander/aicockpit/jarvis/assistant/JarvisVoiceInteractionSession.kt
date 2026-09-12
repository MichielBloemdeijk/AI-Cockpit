package nl.sander.aicockpit.jarvis.assistant

import android.app.KeyguardManager
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.service.voice.VoiceInteractionSession
import android.view.View
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
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import nl.sander.aicockpit.jarvis.JarvisApplication
import nl.sander.aicockpit.jarvis.data.CockpitApi
import nl.sander.aicockpit.jarvis.data.CredentialStore
import nl.sander.aicockpit.jarvis.data.VoiceEngine
import nl.sander.aicockpit.jarvis.notifications.JarvisEventStream
import nl.sander.aicockpit.jarvis.realtime.RealtimeController
import nl.sander.aicockpit.jarvis.realtime.LiveController
import nl.sander.aicockpit.jarvis.wake.WakeForegroundService
import nl.sander.aicockpit.jarvis.wake.JarvisAddressGate
import nl.sander.aicockpit.jarvis.web.JarvisWebViewActivity
import org.json.JSONObject
import java.lang.ref.WeakReference

class JarvisVoiceInteractionSession(context: Context) : VoiceInteractionSession(context) {
    private val store = CredentialStore(context)
    private val api = CockpitApi(store)
    private val realtime = RealtimeController(context, api)
    private val live = LiveController(context, api)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val main = Handler(Looper.getMainLooper())
    private val application = context.applicationContext as JarvisApplication
    private var status by mutableStateOf("Ready")
    private var userTranscript by mutableStateOf("")
    private var assistantTranscript by mutableStateOf("")
    private var foregroundApp by mutableStateOf<String?>(null)
    private var pendingWakeScore: Float? = null
    private var wakeDetectedAt: Long = 0L
    private var activeVoiceEngine: VoiceEngine = VoiceEngine.REALTIME

    private val eventListener: (JSONObject) -> Unit = { event ->
        main.post { handleEvent(event) }
    }

    override fun onCreate() {
        super.onCreate()
        active = WeakReference(this)
    }

    override fun onCreateContentView(): View = ComposeView(context).apply {
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
                                Button(onClick = { finish() }, modifier = Modifier.weight(1f)) { Text("Done") }
                            }
                        }
                    }
                }
            }
        }
    }

    override fun onShow(args: Bundle?, showFlags: Int) {
        super.onShow(args, showFlags)
        application.voiceSessionVisible = true
        application.eventStream.addListener(eventListener)
        pendingWakeScore = args?.takeIf { it.getString("source") == "local_wake" }?.getFloat("wake_score")
        activeVoiceEngine = store.voiceEngine
        wakeDetectedAt = android.os.SystemClock.elapsedRealtime()
        status = if (isLocked()) "Listening while locked (read-only)" else "Connecting…"
        scope.launch {
            runCatching { api.updateDevice(if (isLocked()) "locked" else "unlocked", store.eventCursor) }
            runCatching {
                if (activeVoiceEngine == VoiceEngine.GPT_LIVE) live.connect(pendingWakeScore) { status = friendlyState(it) }
                else realtime.connect(pendingWakeScore) { status = friendlyState(it) }
            }
                .onFailure { status = it.message ?: "Unable to connect" }
        }
    }

    override fun onHide() {
        disconnect()
        super.onHide()
    }

    override fun onDestroy() {
        application.voiceSessionVisible = false
        application.eventStream.removeListener(eventListener)
        realtime.close()
        live.close()
        scope.cancel()
        if (active.get() === this) active.clear()
        if (!store.sleep && store.credential() != null) {
            runCatching {
                context.startForegroundService(Intent(context, WakeForegroundService::class.java)
                    .setAction(WakeForegroundService.ACTION_START))
            }
        }
        super.onDestroy()
    }

    override fun onHandleAssist(state: VoiceInteractionSession.AssistState) {
        if (!store.assistStructureOptIn || !state.isFocused) return
        foregroundApp = state.assistStructure?.activityComponent?.packageName
    }

    override fun onHandleScreenshot(screenshot: Bitmap?) {
        // Screenshots remain disabled even when assist structure is enabled.
    }

    private fun handleEvent(event: JSONObject) {
        val payload = event.optJSONObject("payload") ?: JSONObject()
        when (event.optString("event_type")) {
            "provider.user_transcript_delta" -> userTranscript += payload.optString("delta", payload.optString("transcript"))
            "provider.user_transcript" -> {
                val transcript = payload.optString("transcript")
                val wakeScore = pendingWakeScore
                pendingWakeScore = null
                if (wakeScore != null && activeVoiceEngine != VoiceEngine.GPT_LIVE) {
                    val decision = JarvisAddressGate.classify(
                        transcript,
                        wakeScore,
                        android.os.SystemClock.elapsedRealtime() - wakeDetectedAt,
                    )
                    if (!decision.accepted) {
                        status = "Wake heard, but Jarvis was not addressed"
                        finish()
                        return
                    }
                }
                userTranscript = transcript
            }
            "provider.assistant_transcript_delta" -> assistantTranscript += payload.optString("delta", payload.optString("transcript"))
            "provider.assistant_response", "provider.assistant_interrupted" -> {
                assistantTranscript = payload.optString("transcript", assistantTranscript)
                status = "Listening"
            }
            "provider.user_speaking" -> status = "Listening"
            "provider.assistant_generating" -> status = "Thinking"
            "provider.assistant_speaking" -> status = "Speaking"
            "session.rotation_requested" -> scope.launch {
                if (activeVoiceEngine == VoiceEngine.GPT_LIVE) return@launch
                status = "Refreshing voice…"
                runCatching { realtime.disconnect(); realtime.connect { status = friendlyState(it) } }
            }
            "session.superseded" -> finish()
        }
    }

    private fun disconnect() {
        application.voiceSessionVisible = false
        application.eventStream.removeListener(eventListener)
        scope.launch {
            if (activeVoiceEngine == VoiceEngine.GPT_LIVE) live.disconnect()
            else realtime.disconnect()
        }
    }

    private fun openCockpit() {
        context.startActivity(Intent(context, JarvisWebViewActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    private fun isLocked(): Boolean = context.getSystemService(KeyguardManager::class.java).isDeviceLocked

    private fun friendlyState(value: String): String = when {
        value == "connected" -> "Listening"
        value.startsWith("ice:CONNECTED") || value.startsWith("ice:COMPLETED") -> "Listening"
        value.startsWith("ice:FAILED") -> "Connection failed"
        else -> status
    }

    companion object {
        private var active = WeakReference<JarvisVoiceInteractionSession>(null)
        fun stopActive() { active.get()?.finish() }
    }
}
