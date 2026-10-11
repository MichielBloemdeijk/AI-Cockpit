package nl.sander.aicockpit.jarvis.audio

import android.app.Activity
import android.os.Bundle
import android.widget.TextView
import android.util.Log
import kotlinx.coroutines.*
import nl.sander.aicockpit.jarvis.JarvisApplication
import nl.sander.aicockpit.jarvis.data.*
import nl.sander.aicockpit.jarvis.live.*
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.net.URI

/** Debug-only local WebRTC peer; uses real recording/playback and a fake backend adapter. */
class LocalAudioFixtureActivity : Activity() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(TextView(this).apply { text = "Local audio fixture"; textSize = 30f })
        scope.launch {
            runCatching {
                val store = CredentialStore(this@LocalAudioFixtureActivity)
                val server = intent.getStringExtra("server_url") ?: store.serverUrl
                val peer = requireNotNull(intent.getStringExtra("peer_url"))
                for (value in listOf(server, peer)) {
                    val uri = URI(value)
                    require(uri.scheme == "http" && uri.host == "10.0.2.2" && uri.port > 0 && uri.userInfo == null)
                }
                intent.getStringExtra("pairing_code")?.let {
                    val paired = CockpitApi(store).pair(server, it, "Local audio emulator")
                    store.savePairing(server, paired)
                    store.sleep = true; store.setupComplete = true; store.wakeAfterBoot = true; store.assistStructureOptIn = true
                }
                val app = application as JarvisApplication
                app.liveControllerFactory = { context, api -> LiveController(context, api, LocalPeerTransport(store, api, peer)) }
                app.eventStream.start()
                JarvisAudioControls.resume(this@LocalAudioFixtureActivity)
                Log.i("JarvisLocalAudio", "fixture_ready")
            }.onFailure { Log.e("JarvisLocalAudio", "fixture_failed", it) }
        }
    }
    override fun onDestroy() { scope.cancel(); super.onDestroy() }
}

private class LocalPeerTransport(private val store: CredentialStore, private val api: CockpitApi,
    private val peer: String) : LiveCallTransport {
    private val client = OkHttpClient.Builder().callTimeout(java.time.Duration.ofSeconds(10)).build()
    override suspend fun createCall(offer: String, wakeScore: Float?, activationId: String): LiveCall = withContext(Dispatchers.IO) {
        val answer = post("$peer/offer", JSONObject().put("sdp", offer)).getString("sdp")
        ensureActive()
        val payload = JSONObject().put("controller_id", store.deviceId).put("provider_adapter", "fake")
            .put("activation_context", JSONObject().put("source", "system").put("activation_id", activationId))
        val session = post("${store.serverUrl}/api/jarvis/live/calls", payload, store.credential()).getJSONObject("session")
        LiveCall(session.getString("id"), session.getInt("generation"), answer)
    }
    private fun post(url: String, body: JSONObject, bearer: String? = null): JSONObject {
        val request = Request.Builder().url(url).post(body.toString().toRequestBody("application/json".toMediaType()))
        bearer?.let { request.header("Authorization", "Bearer $it") }
        return client.newCall(request.build()).execute().use {
            check(it.isSuccessful) { "Local peer HTTP ${it.code}" }
            JSONObject(it.body?.string().orEmpty())
        }
    }
    override suspend fun state() = api.liveSessionState()
    override suspend fun closeCall(sessionId: String, generation: Int) = api.closeLive(sessionId, generation)
}
