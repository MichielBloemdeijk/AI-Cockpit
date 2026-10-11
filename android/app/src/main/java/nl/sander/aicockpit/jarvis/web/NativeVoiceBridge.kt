package nl.sander.aicockpit.jarvis.web

import android.content.Context
import android.webkit.WebView
import androidx.webkit.JavaScriptReplyProxy
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import kotlinx.coroutines.*
import nl.sander.aicockpit.jarvis.audio.*
import org.json.JSONObject

internal object NativeVoicePolicy {
    fun permits(origin: String, source: String, mainFrame: Boolean, command: String): Boolean =
        mainFrame && ResultNavigation.destination(origin, source) == ResultNavigation.Destination.INTERNAL &&
            command in setOf("get_state", "listen_now", "stop_live", "hey_jarvis")
}

/** Exact-origin main-frame control messages; no credentials, audio or pixels cross this bridge. */
internal class NativeVoiceBridge(private val context: Context, private val webView: WebView,
    private val origin: String) : AutoCloseable {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var reply: JavaScriptReplyProxy? = null
    private var installed = false
    fun install() {
        if (!WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) return
        WebViewCompat.addWebMessageListener(webView, "JarvisPhoneVoice", setOf(origin)) { _, message, source, mainFrame, proxy ->
            val text = message.data ?: return@addWebMessageListener
            if (text.length > 2048) return@addWebMessageListener
            val body = runCatching { JSONObject(text) }.getOrNull() ?: return@addWebMessageListener
            val command = body.optString("command")
            if (!NativeVoicePolicy.permits(origin, source.toString(), mainFrame, command)) return@addWebMessageListener
            reply = proxy
            if (command != "get_state") {
                val control = AudioAction.entries.first { it.wire == command }
                val snapshot = if (control == AudioAction.STOP) AudioControlState(
                    activationId = body.optString("activation_id").takeIf { it.isNotBlank() },
                    sessionId = body.optString("session_id").takeIf { it.isNotBlank() },
                    generation = body.optInt("generation").takeIf { it > 0 }) else JarvisAudioControls.state.value
                JarvisAudioControls.command(context, control, snapshot)
            }
            proxy.postMessage(JarvisAudioControls.json().toString())
        }
        installed = true
        scope.launch { JarvisAudioControls.state.collect {
            if (ResultNavigation.destination(origin, webView.url.orEmpty()) == ResultNavigation.Destination.INTERNAL)
                runCatching { reply?.postMessage(JarvisAudioControls.json().toString()) }
        } }
    }
    fun navigationStarted() { reply = null }
    override fun close() {
        reply = null; scope.cancel()
        if (installed) WebViewCompat.removeWebMessageListener(webView, "JarvisPhoneVoice")
    }
}
