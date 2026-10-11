package nl.sander.aicockpit.jarvis.assistant

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.util.Log
import android.view.Gravity
import android.webkit.CookieManager
import android.widget.TextView
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import nl.sander.aicockpit.jarvis.JarvisApplication
import nl.sander.aicockpit.jarvis.data.CockpitApi
import nl.sander.aicockpit.jarvis.data.CredentialStore
import nl.sander.aicockpit.jarvis.web.JarvisWebViewActivity
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.UUID

/** Debug-only foreground marker; image bytes still originate in onHandleScreenshot. */
class CaptureFixtureActivity : Activity() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val fixtureForeground = CompletableDeferred<Unit>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (openResult(intent)) return
        val marker = intent.getStringExtra("marker") ?: "FIXTURE-A-VISIBLE-MARKER"
        setContentView(TextView(this).apply {
            text = marker
            textSize = 32f
            gravity = Gravity.CENTER
            setTextColor(Color.BLACK)
            setBackgroundColor(Color.WHITE)
        })
        val activationId = intent.getStringExtra("activation_id") ?: UUID.randomUUID().toString()
        val captureId = intent.getStringExtra("capture_id") ?: UUID.randomUUID().toString()
        val serverUrl = intent.getStringExtra("server_url") ?: "http://10.0.2.2:8000"
        val pairingCode = intent.getStringExtra("pairing_code")
        Log.i("JarvisCaptureFixture", "activation_id=$activationId capture_id=$captureId")
        scope.launch {
            try {
            val store = CredentialStore(this@CaptureFixtureActivity)
            val api = CockpitApi(store)
            val pairing = api.pair(serverUrl, pairingCode ?: developmentPairingCode(serverUrl),
                "Agent emulator fixture")
            store.savePairing(serverUrl, pairing)
            (application as JarvisApplication).eventStream.start()
            api.updateDevice("unlocked")
            // Keep a different foreground app visible when Android captures assistant context.
            val chromeUrl = intent.getStringExtra("chrome_url")
            if (chromeUrl != null) startActivity(Intent(Intent.ACTION_VIEW, android.net.Uri.parse(chromeUrl))
                .setPackage("com.android.chrome"))
            else startActivity(Intent().setClassName(
                "nl.sander.aicockpit.screenfixture",
                "nl.sander.aicockpit.screenfixture.MarkerActivity",
            ).putExtra("subject", intent.getStringExtra("subject")))
            // A cold fixture app can take longer than a fixed delay to draw.
            // This activity stops only after the marker app is foreground.
            withTimeout(5_000) { fixtureForeground.await() }
            delay(250)
            if (intent.getBooleanExtra("browser_capture_probe", false)) {
                val cookie = api.webSessionCookie().substringBefore(';')
                val body = JSONObject().put("controller_id", "jarvis-browser-fixture")
                    .put("provider_adapter", "fake").put("activation_context",
                        JSONObject().put("source", "manual").put("activation_id", activationId))
                val session = withContext(Dispatchers.IO) {
                    OkHttpClient().newCall(Request.Builder().url("$serverUrl/api/jarvis/live/calls")
                        .header("Cookie", cookie)
                        .post(body.toString().toRequestBody("application/json; charset=utf-8".toMediaType()))
                        .build()).execute().use { response ->
                        check(response.isSuccessful) { "Browser capture fixture failed: ${response.code}" }
                        JSONObject(response.body?.string().orEmpty()).getJSONObject("session")
                    }
                }
                Log.i("JarvisCaptureFixture", "browser_probe_ready activation_id=$activationId session_id=${session.getString("id")}")
                return@launch
            }
            if (intent.getBooleanExtra("probe_mode", false)) {
                val live = startFakeSession(serverUrl, pairing.credential, pairing.deviceId, activationId)
                CaptureProbeReceiver.configure(serverUrl, pairing.credential, activationId,
                    captureId, live.first, live.second)
                Log.i("JarvisCaptureFixture", "probe_ready activation_id=$activationId session_id=${live.first}")
                return@launch
            }
            repeat(20) {
                if (JarvisVoiceInteractionService.invokeFixture(activationId, captureId)) {
                    val live = startFakeSession(serverUrl, pairing.credential,
                        pairing.deviceId, activationId)
                    (application as JarvisApplication).screenCapture.bindSession(
                        activationId, live.first, live.second,
                    )
                    requestScreenWorkflow(serverUrl, pairing.credential, live.first,
                        live.second, captureId)
                    return@launch
                }
                delay(250)
            }
            } catch (error: Exception) {
                Log.e("JarvisCaptureFixture", "Fixture setup failed", error)
            }
        }
    }

    /** Only the disposable passwordless backend used by this debug fixture can issue this code. */
    private suspend fun developmentPairingCode(serverUrl: String): String = withContext(Dispatchers.IO) {
        val client = OkHttpClient()
        val json = "application/json; charset=utf-8".toMediaType()
        val login = client.newCall(Request.Builder().url("$serverUrl/api/auth/login")
            .post(JSONObject().put("password", "").toString().toRequestBody(json))
            .build()).execute()
        val cookie = login.use { response ->
            check(response.isSuccessful) { "Fixture requires a passwordless disposable backend" }
            response.header("Set-Cookie")?.substringBefore(';') ?: error("Missing dev session cookie")
        }
        withContext(Dispatchers.Main) {
            CookieManager.getInstance().setCookie(serverUrl, cookie)
            CookieManager.getInstance().flush()
        }
        client.newCall(Request.Builder().url("$serverUrl/api/jarvis/devices/pairing-code")
            .header("Cookie", cookie).post("".toRequestBody(json)).build()).execute().use { response ->
            check(response.isSuccessful) { "Fixture pairing code unavailable: ${response.code}" }
            JSONObject(response.body?.string().orEmpty()).getString("code")
        }
    }

    private suspend fun startFakeSession(
        serverUrl: String, credential: String, deviceId: String, activationId: String,
    ): Pair<String, Int> {
        val body = JSONObject().put("controller_id", deviceId).put("provider_adapter", "fake")
            .put("activation_context", JSONObject().put("source", "system")
                .put("activation_id", activationId))
        val session = postJson(serverUrl, "/api/jarvis/live/calls", credential, body)
            .getJSONObject("session")
        return session.getString("id") to session.getInt("generation")
    }

    private suspend fun requestScreenWorkflow(
        serverUrl: String, credential: String, sessionId: String, generation: Int,
        captureId: String,
    ) {
        val arguments = JSONObject().put("goal", intent.getStringExtra("workflow_goal")
            ?: "Describe the visible screen marker and blue panel")
            .put("definition", intent.getStringExtra("workflow_definition") ?: "screen_document@1")
            .put("capture_id", captureId)
        val payload = JSONObject().put("call_id", UUID.randomUUID().toString())
            .put("name", "start_workflow").put("arguments", arguments)
        val body = JSONObject().put("generation", generation)
            .put("event_id", UUID.randomUUID().toString())
            .put("kind", "shell_call").put("payload", payload)
        postJson(serverUrl, "/api/jarvis/fake/sessions/$sessionId/events", credential, body)
    }

    private suspend fun postJson(
        serverUrl: String, path: String, credential: String, body: JSONObject,
    ): JSONObject = withContext(Dispatchers.IO) {
        OkHttpClient().newCall(Request.Builder().url("$serverUrl$path")
            .header("Authorization", "Bearer $credential")
            .post(body.toString().toRequestBody("application/json; charset=utf-8".toMediaType()))
            .build()).execute().use { response ->
            check(response.isSuccessful) { "Fixture request failed: $path (${response.code})" }
            JSONObject(response.body?.string().orEmpty())
        }
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    override fun onStop() {
        super.onStop()
        fixtureForeground.complete(Unit)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        openResult(intent)
    }

    private fun openResult(intent: Intent): Boolean {
        val path = intent.getStringExtra("open_result_path") ?: return false
        check(path.startsWith("/results/") || Regex("^/tasks/[0-9a-fA-F-]{36}$").matches(path))
        JarvisVoiceInteractionSession.stopActive()
        startActivity(Intent(this, JarvisWebViewActivity::class.java)
            .putExtra(JarvisWebViewActivity.EXTRA_PATH, path))
        finish()
        return true
    }
}
