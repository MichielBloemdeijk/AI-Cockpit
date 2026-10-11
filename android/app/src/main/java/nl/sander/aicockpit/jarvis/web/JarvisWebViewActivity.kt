package nl.sander.aicockpit.jarvis.web

import android.content.Intent
import android.content.ActivityNotFoundException
import android.os.Bundle
import android.graphics.Color
import android.widget.FrameLayout
import android.widget.TextView
import android.view.View
import android.webkit.CookieManager
import android.webkit.PermissionRequest
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume
import nl.sander.aicockpit.jarvis.data.CockpitApi
import nl.sander.aicockpit.jarvis.data.CredentialStore
import nl.sander.aicockpit.jarvis.notifications.JarvisNotifications
import nl.sander.aicockpit.jarvis.MainActivity

open class JarvisWebViewActivity : ComponentActivity() {
    private lateinit var webView: WebView
    private lateinit var store: CredentialStore
    private lateinit var connectionMessage: TextView
    private var pendingState: Bundle? = null
    private var refreshInProgress = false
    private var targetUrl = ""
    private var voiceBridge: NativeVoiceBridge? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = CredentialStore(this)
        if (store.serverUrl.isBlank() || store.credential() == null) { finish(); return }
        pendingState = savedInstanceState
        val path = intent.getStringExtra(EXTRA_PATH)?.takeIf { it.startsWith("/") && !it.startsWith("//") } ?: "/jarvis"
        targetUrl = "${store.serverUrl}$path"
        webView = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.mediaPlaybackRequiresUserGesture = false
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            settings.userAgentString += " JarvisPhone/1"
            CookieManager.getInstance().setAcceptThirdPartyCookies(this, false)
            webViewClient = object : WebViewClient() {
                override fun onPageStarted(view: WebView, url: String, favicon: android.graphics.Bitmap?) {
                    voiceBridge?.navigationStarted()
                    if (ResultNavigation.destination(store.serverUrl, url) == ResultNavigation.Destination.INTERNAL && android.net.Uri.parse(url).path == "/login") {
                        view.stopLoading()
                        refreshSession()
                    } else if (ResultNavigation.destination(store.serverUrl, url) == ResultNavigation.Destination.INTERNAL) {
                        targetUrl = url
                    }
                }
                override fun onPageFinished(view: WebView, url: String) {
                    // Persist the login redirect's cookie before abrupt process
                    // death; don't rely on Chromium's delayed background flush.
                    CookieManager.getInstance().flush()
                }

                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                    val target = request.url
                    if (request.isForMainFrame && target.toString() == "jarvis://setup") {
                        startActivity(Intent(this@JarvisWebViewActivity, MainActivity::class.java))
                        finish()
                        return true
                    }
                    when (ResultNavigation.destination(store.serverUrl, target.toString())) {
                        ResultNavigation.Destination.INTERNAL -> return false
                        ResultNavigation.Destination.EXTERNAL -> if (request.isForMainFrame) {
                            try { startActivity(Intent(Intent.ACTION_VIEW, target)) }
                            catch (_: ActivityNotFoundException) { /* No browser installed. Keep the result. */ }
                        }
                        ResultNavigation.Destination.BLOCKED -> Unit
                    }
                    return true
                }
            }
            webChromeClient = object : WebChromeClient() {
                override fun onPermissionRequest(request: PermissionRequest) {
                    // Android voice belongs to the service, never a WebView recorder.
                    request.deny()
                }

            }
        }
        voiceBridge = NativeVoiceBridge(this, webView, store.serverUrl).also { it.install() }
        val container = FrameLayout(this).apply {
            setBackgroundColor(Color.BLACK)
            addView(webView, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT))
        }
        connectionMessage = TextView(this).apply {
            setTextColor(Color.WHITE)
            text = "Connecting to Jarvis…"
            setPadding(32, 64, 32, 32)
            setOnClickListener { refreshSession() }
        }
        webView.visibility = View.GONE
        container.addView(connectionMessage)
        // Android's enforced edge-to-edge layout must not put result links
        // beneath the status/navigation bars. Pad the parent, not WebView's
        // HTML content, so accessibility bounds and touch coordinates agree.
        ViewCompat.setOnApplyWindowInsetsListener(container) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            insets
        }
        setContentView(container)
        ViewCompat.requestApplyInsets(container)
        JarvisNotifications.acknowledgeOpen(this, intent)
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (webView.canGoBack()) webView.goBack() else finish()
            }
        })
    }

    override fun onResume() {
        super.onResume()
        nl.sander.aicockpit.jarvis.audio.JarvisAudioControls.resume(this)
        if (::webView.isInitialized) refreshSession()
    }

    private fun refreshSession() {
        if (refreshInProgress || !::webView.isInitialized) return
        refreshInProgress = true
        lifecycleScope.launch {
            try {
                val cookie = CockpitApi(store).webSessionCookie()
                val accepted = suspendCancellableCoroutine<Boolean> { continuation ->
                    CookieManager.getInstance().setCookie(store.serverUrl, cookie) { value ->
                        if (continuation.isActive) continuation.resume(value)
                    }
                }
                check(accepted) { "Phone session could not be saved." }
                CookieManager.getInstance().flush()
                connectionMessage.visibility = View.GONE
                webView.visibility = View.VISIBLE
                val saved = pendingState
                pendingState = null
                if (saved != null) webView.restoreState(saved)
                else if (webView.url == null || android.net.Uri.parse(webView.url).path == "/login") loadInitialPage(webView, targetUrl)
            } catch (failure: Exception) {
                if (failure is kotlinx.coroutines.CancellationException) throw failure
                webView.visibility = View.GONE
                connectionMessage.visibility = View.VISIBLE
                connectionMessage.text = "Unable to connect. Check Tailscale, then tap to retry. If this phone was unlinked, return to the Jarvis app and create a new link."
            } finally { refreshInProgress = false }
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        if (::webView.isInitialized) webView.saveState(outState)
        super.onSaveInstanceState(outState)
    }

    protected open fun loadInitialPage(view: WebView, url: String) { view.loadUrl(url) }

    override fun onStop() {
        CookieManager.getInstance().flush()
        super.onStop()
    }

    override fun onDestroy() {
        voiceBridge?.close(); voiceBridge = null
        if (::webView.isInitialized) { webView.stopLoading(); webView.destroy() }
        super.onDestroy()
    }

    companion object { const val EXTRA_PATH = "path" }
}
