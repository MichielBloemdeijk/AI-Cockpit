package nl.sander.aicockpit.jarvis

import android.Manifest
import android.app.role.RoleManager
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import nl.sander.aicockpit.jarvis.data.CockpitApi
import nl.sander.aicockpit.jarvis.data.CredentialStore
import nl.sander.aicockpit.jarvis.data.PairingLink
import nl.sander.aicockpit.jarvis.data.JarvisAssistantStatus
import nl.sander.aicockpit.jarvis.notifications.JarvisEventStream
import nl.sander.aicockpit.jarvis.notifications.JarvisNotifications
import nl.sander.aicockpit.jarvis.audio.JarvisAudioService
import nl.sander.aicockpit.jarvis.web.JarvisWebViewActivity

/** Linking starts one guided setup. Only Android's own consent dialogs intervene. */
class MainActivity : ComponentActivity() {
    private lateinit var store: CredentialStore
    private lateinit var api: CockpitApi
    private var paired by mutableStateOf(false)
    private var assistantSelected by mutableStateOf(false)
    private var health by mutableStateOf<JarvisAssistantStatus?>(null)
    private var connected by mutableStateOf(false)
    private var busy by mutableStateOf(false)
    private var message by mutableStateOf("")
    private var pairingLink by mutableStateOf<PairingLink?>(null)
    private var setupComplete by mutableStateOf(false)
    private var screenContextAllowed by mutableStateOf(true)
    private var voicePermissionsReady by mutableStateOf(false)
    private var sleeping by mutableStateOf(false)
    private var setupPending = false
    private var networkAction: (() -> Unit)? = null

    private val networkPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        val action = networkAction
        networkAction = null
        if (granted) action?.invoke() else {
            busy = false
            message = "Allow Nearby devices in Android app settings, then try setup again."
        }
    }
    private val permissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        voicePermissionsReady = missingPermissions().isEmpty()
        if (voicePermissionsReady) continueSetup() else {
            setupPending = false; busy = false
            message = "Setup paused. Allow microphone and notifications in Android app settings, then select Finish setup."
        }
    }
    private val assistantRole = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        assistantSelected = isAssistantSelected()
        if (assistantSelected) continueSetup() else {
            setupPending = false; busy = false
            message = "Select Jarvis as Android's assistant to finish setup."
        }
    }
    private val screenSettings = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        screenContextAllowed = hasScreenContext()
        if (screenContextAllowed) continueSetup() else {
            setupPending = false; busy = false
            message = "Phone link saved. Android screen context is still disabled. Enable screen access to finish all features."
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = CredentialStore(this)
        api = CockpitApi(store)
        paired = store.credential() != null
        setupComplete = store.setupComplete
        sleeping = store.sleep
        paired = store.credential() != null
        setupPending = savedInstanceState?.getBoolean("setup_pending") ?: false
        assistantSelected = isAssistantSelected()
        screenContextAllowed = hasScreenContext()
        readLink(intent)
        JarvisNotifications.acknowledgeOpen(this, intent)
        if (intent.getBooleanExtra("finish_audio_setup", false)) beginSetup()
        setContent { MaterialTheme(colorScheme = darkColorScheme()) {
            Surface(Modifier.fillMaxSize()) { Setup() }
        } }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        readLink(intent)
        if (intent.getBooleanExtra("finish_audio_setup", false)) beginSetup()
    }

    private fun readLink(intent: Intent) {
        if (intent.action != Intent.ACTION_VIEW || intent.data?.host == "setup") return
        pairingLink = intent.dataString?.let(PairingLink::parse)
        if (pairingLink == null) message = "Invalid link. Create a new link from Jarvis on your computer."
    }

    override fun onResume() {
        super.onResume()
        sleeping = store.sleep
        paired = store.credential() != null
        assistantSelected = isAssistantSelected()
        screenContextAllowed = hasScreenContext()
        voicePermissionsReady = missingPermissions().isEmpty()
        if (paired && setupComplete && assistantSelected && voicePermissionsReady && !store.sleep &&
            !nl.sander.aicockpit.jarvis.audio.JarvisAudioControls.state.value.liveActive) {
            runCatching { ContextCompat.startForegroundService(this,
                Intent(this, JarvisAudioService::class.java).setAction(nl.sander.aicockpit.jarvis.audio.AudioAction.WAKE.wire)) }
                .onFailure { message = "Wake listening could not resume. Check Android permissions." }
        }
        if (paired) {
            nl.sander.aicockpit.jarvis.audio.JarvisAudioControls.resume(this)
            refreshStatus()
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putBoolean("setup_pending", setupPending)
        super.onSaveInstanceState(outState)
    }

    private fun refreshStatus() = lifecycleScope.launch {
        runCatching {
            api.updateDevice(JarvisEventStream.lockState(this@MainActivity), store.eventCursor)
            api.assistantStatus()
        }.onSuccess { connected = true; health = it }
            .onFailure { connected = false; message = it.message ?: "Link saved. Check Tailscale and the Jarvis computer." }
    }

    @Composable private fun Setup() {
        var server by remember { mutableStateOf(store.serverUrl) }
        var code by remember { mutableStateOf("") }
        var advanced by remember { mutableStateOf(false) }
        val listeningStatus by nl.sander.aicockpit.jarvis.audio.JarvisAudioControls.state.collectAsState()
        LaunchedEffect(listeningStatus) { paired = store.credential() != null }
        LaunchedEffect(pairingLink) {
            pairingLink?.let {
                server = it.server; code = it.code
                if (!paired) pair(server, code) else message = "This phone is already linked. No new link is needed."
            }
        }
        val ready = paired && setupComplete && assistantSelected && screenContextAllowed && voicePermissionsReady
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Text("Jarvis", style = MaterialTheme.typography.headlineMedium)
            Text(if (paired) "This phone is linked" else "Connect Tailscale, then open the link from your computer.",
                style = MaterialTheme.typography.titleMedium)
            if (paired) {
                Text(store.serverUrl)
                Text(if (connected) "Connected" else "Link saved · checking connection or offline")
                Text(when {
                    !ready -> "Finish Android permissions to enable all supported features."
                    listeningStatus.mode == nl.sander.aicockpit.jarvis.audio.AudioMode.WAITING -> "Ready · wake listening active · notifications enabled"
                    listeningStatus.mode == nl.sander.aicockpit.jarvis.audio.AudioMode.ERROR -> "Setup saved · microphone unavailable. Close other microphone apps and try again."
                    else -> listeningStatus.text
                })
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    listeningStatus.actions.forEach { action ->
                        OutlinedButton(onClick = {
                            if (!ready || listeningStatus.error == "Microphone permission needed") beginSetup()
                            else nl.sander.aicockpit.jarvis.audio.JarvisAudioControls.command(this@MainActivity, action, listeningStatus)
                        }) { Text(action.label) }
                    }
                }
                health?.let {
                    Text("Task worker: ${it.workerStatus}")
                    it.componentIssues.forEach { issue -> Text("${issue.name.replace('_', ' ')}: ${issue.detail ?: issue.status}") }
                }
            }
            if (message.isNotBlank()) Text(message)
            if (!paired) {
                OutlinedTextField(server, { server = it }, label = { Text("AI Cockpit URL") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(code, { code = it.filter(Char::isDigit).take(6) }, label = { Text("Pairing code") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                Button(enabled = !busy && code.length == 6 && server.isNotBlank(), onClick = { pair(server, code) }, modifier = Modifier.fillMaxWidth()) {
                    Text(if (busy) "Setting up…" else "Set up Jarvis")
                }
            } else {
                Button(enabled = !busy, onClick = {
                    if (ready) startActivity(Intent(this@MainActivity, JarvisWebViewActivity::class.java)) else beginSetup()
                }, modifier = Modifier.fillMaxWidth()) { Text(if (busy) "Setting up…" else if (ready) "Open AI Cockpit" else "Finish setup") }
                Text("Setup enables microphone, notifications, wake listening, restart after reboot and screen context. Android may ask for permission or assistant selection. Protected screens and lock privacy still apply.", style = MaterialTheme.typography.bodySmall)
                TextButton(onClick = { advanced = !advanced }) { Text(if (advanced) "Hide settings" else "Advanced settings") }
                if (advanced) {
                    OutlinedButton(onClick = {
                        sleeping = !sleeping; store.sleep = sleeping
                        if (sleeping) nl.sander.aicockpit.jarvis.audio.JarvisAudioControls.pause(this@MainActivity) else beginSetup()
                    }) { Text(if (sleeping) "Resume wake listening" else "Pause wake listening") }
                    OutlinedButton(onClick = { startActivity(Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                        android.net.Uri.parse("package:$packageName"))) }) { Text("Android permissions") }
                    OutlinedButton(onClick = { startActivity(Intent(android.provider.Settings.ACTION_VOICE_INPUT_SETTINGS)) }) { Text("Android screen context settings") }
                    OutlinedButton(enabled = !busy, onClick = { refreshStatus() }) { Text("Refresh status") }
                    OutlinedButton(enabled = !busy, onClick = { unlink() }) { Text("Unlink this phone") }
                }
            }
        }
    }

    private fun pair(server: String, code: String) {
        if (busy || paired) return
        busy = true
        val action = {
            lifecycleScope.launch {
                runCatching { api.pair(server, code, "${Build.MANUFACTURER} ${Build.MODEL}") }
                    .onSuccess {
                        store.savePairing(server, it)
                        paired = true; pairingLink = null
                        (application as JarvisApplication).eventStream.start()
                        refreshStatus()
                        beginSetup()
                    }.onFailure { busy = false; message = it.message ?: "Unable to link. Check Tailscale and try a fresh link." }
            }
            Unit
        }
        if (Build.VERSION.SDK_INT >= 37 && checkSelfPermission(Manifest.permission.ACCESS_LOCAL_NETWORK) != PackageManager.PERMISSION_GRANTED) {
            networkAction = action
            networkPermission.launch(Manifest.permission.ACCESS_LOCAL_NETWORK)
        } else action()
    }

    private fun beginSetup() {
        setupPending = true; busy = true
        store.wakeAfterBoot = true
        store.assistStructureOptIn = true
        // AppFunctions is only an unavailable experimental probe, not a supported capability.
        continueSetup()
    }

    private fun continueSetup() {
        if (!paired || !setupPending) return
        val missing = missingPermissions()
        if (missing.isNotEmpty()) { permissions.launch(missing.toTypedArray()); return }
        if (!isAssistantSelected()) {
            val manager = getSystemService(RoleManager::class.java)
            if (manager.isRoleAvailable(RoleManager.ROLE_ASSISTANT)) {
                assistantRole.launch(manager.createRequestRoleIntent(RoleManager.ROLE_ASSISTANT)); return
            }
            busy = false; setupPending = false
            message = "Android does not offer the assistant role on this device. Microphone and app access remain available."
            return
        }
        assistantSelected = true
        if (!hasScreenContext()) {
            screenSettings.launch(Intent(android.provider.Settings.ACTION_VOICE_INPUT_SETTINGS))
            return
        }
        nl.sander.aicockpit.jarvis.assistant.JarvisVoiceInteractionService.refreshContextPolicy()
        store.sleep = false
        sleeping = false
        runCatching { ContextCompat.startForegroundService(this,
            Intent(this, JarvisAudioService::class.java).setAction(nl.sander.aicockpit.jarvis.audio.AudioAction.WAKE.wire)) }
            .onSuccess {
                store.setupComplete = true; setupComplete = true
                message = "Ready. Say Jarvis or open AI Cockpit."
            }.onFailure { message = "Wake listening could not start. ${it.message ?: "Check Android permissions."}" }
        busy = false; setupPending = false
    }

    private fun missingPermissions() = buildList {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) add(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) add(Manifest.permission.POST_NOTIFICATIONS)
        if (Build.VERSION.SDK_INT >= 37 && checkSelfPermission(Manifest.permission.ACCESS_LOCAL_NETWORK) != PackageManager.PERMISSION_GRANTED) add(Manifest.permission.ACCESS_LOCAL_NETWORK)
    }

    private fun isAssistantSelected(): Boolean = getSystemService(RoleManager::class.java).let {
        it.isRoleAvailable(RoleManager.ROLE_ASSISTANT) && it.isRoleHeld(RoleManager.ROLE_ASSISTANT)
    }

    private fun hasScreenContext(): Boolean =
        android.provider.Settings.Secure.getInt(contentResolver, "assist_screenshot_enabled", 1) != 0 &&
        android.provider.Settings.Secure.getInt(contentResolver, "assist_structure_enabled", 1) != 0

    private fun unlink() {
        busy = true
        lifecycleScope.launch {
            runCatching { api.unlink() }.onSuccess {
                nl.sander.aicockpit.jarvis.audio.JarvisAudioControls.unlink(this@MainActivity)
                (application as JarvisApplication).eventStream.stop()
                store.clear(); android.webkit.CookieManager.getInstance().removeAllCookies(null)
                paired = false; connected = false; setupComplete = false; health = null
                message = "Unlinked. Create a new link from Jarvis on your computer."
            }.onFailure { message = "Unable to unlink. Reconnect Tailscale and try again." }
            busy = false
        }
    }
}
