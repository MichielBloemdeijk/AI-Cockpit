package nl.sander.aicockpit.jarvis.assistant

import android.app.KeyguardManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import nl.sander.aicockpit.jarvis.data.CockpitApi
import nl.sander.aicockpit.jarvis.data.CredentialStore
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.MediaType.Companion.toMediaType
import org.json.JSONObject
import java.util.UUID

/** Debug-only commands; never supplies pixels or grants capture permissions. */
class CaptureProbeReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val pending = goAsync()
        scope.launch {
            try {
                val id = intent.getStringExtra("capture_id") ?: captureId
                when (intent.getStringExtra("command")) {
                    "consent" -> withContext(Dispatchers.Main) {
                        CredentialStore(context).assistStructureOptIn = intent.getBooleanExtra("enabled", false)
                        JarvisVoiceInteractionService.restoreFixtureContextPolicy()
                    }
                    "wake" -> {
                        val locked = context.getSystemService(KeyguardManager::class.java).isDeviceLocked
                        CockpitApi(CredentialStore(context)).updateDevice(if (locked) "locked" else "unlocked")
                        withContext(Dispatchers.Main) {
                            check(JarvisVoiceInteractionService.invokeWakeFixture(activationId, id, sessionId, generation))
                        }
                    }
                    "refresh" -> withContext(Dispatchers.Main) {
                        check(JarvisVoiceInteractionSession.refreshFixture(activationId, id))
                    }
                    "submit" -> {
                        val arguments = JSONObject().put("goal", "Describe the visible screen marker")
                            .put("definition", "screen_document@1").put("capture_id", id)
                            .put("screen_context_mode", intent.getStringExtra("capture_mode") ?: "activation")
                        val body = JSONObject().put("generation", generation).put("event_id", UUID.randomUUID().toString())
                            .put("kind", "shell_call").put("payload", JSONObject().put("call_id", UUID.randomUUID().toString())
                                .put("name", "start_workflow").put("arguments", arguments))
                        OkHttpClient().newCall(Request.Builder().url("$serverUrl/api/jarvis/fake/sessions/$sessionId/events")
                            .header("Authorization", "Bearer $credential")
                            .post(body.toString().toRequestBody("application/json".toMediaType())).build()).execute().use {
                                check(it.isSuccessful) { "Probe submission failed: ${it.code}" }
                        }
                    }
                    "hide" -> withContext(Dispatchers.Main) { JarvisVoiceInteractionSession.stopActive() }
                    else -> error("Unknown probe command")
                }
                Log.i("JarvisCaptureFixture", "probe_command=${intent.getStringExtra("command")} capture_id=$id complete")
            } catch (error: Exception) {
                Log.e("JarvisCaptureFixture", "Probe command failed", error)
            } finally { pending.finish() }
        }
    }

    companion object {
        private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        private var serverUrl = ""
        private var credential = ""
        private var activationId = ""
        private var captureId = ""
        private var sessionId = ""
        private var generation = 0
        fun configure(url: String, token: String, activation: String, capture: String, session: String, gen: Int) {
            serverUrl = url; credential = token; activationId = activation
            captureId = capture; sessionId = session; generation = gen
        }
    }
}
