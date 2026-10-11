package nl.sander.aicockpit.jarvis.audio

import android.Manifest
import android.app.KeyguardManager
import android.app.Service
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.IBinder
import android.util.Log
import androidx.core.content.ContextCompat
import androidx.core.app.ServiceCompat
import kotlinx.coroutines.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import nl.sander.aicockpit.jarvis.JarvisApplication
import nl.sander.aicockpit.jarvis.assistant.JarvisVoiceInteractionService
import nl.sander.aicockpit.jarvis.data.CockpitApi
import nl.sander.aicockpit.jarvis.data.CredentialStore
import nl.sander.aicockpit.jarvis.live.LiveController
import nl.sander.aicockpit.jarvis.live.LiveEventIdentity
import nl.sander.aicockpit.jarvis.notifications.JarvisNotifications
import nl.sander.aicockpit.jarvis.wake.WakeMicrophone
import org.json.JSONObject
import java.util.UUID

/** The only Android owner of wake recording, WebRTC capture and playback. */
class JarvisAudioService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val transitionLock = Mutex()
    private lateinit var store: CredentialStore
    private lateinit var machine: AudioControlMachine
    private val application get() = applicationContext as JarvisApplication
    private var wakeJob: Job? = null
    private var connectionJob: Job? = null
    private var live: LiveController? = null
    private var removing = false
    private var foreground = false
    private var previousRelease: CompletableDeferred<Unit>? = null
    private val eventListener: (JSONObject) -> Unit = { event -> scope.launch {
        val call = live?.currentCall()
        if (LiveEventIdentity.matches(event, call?.sessionId, call?.generation)) {
            when (event.optString("event_type")) {
                "provider.disconnected", "session.superseded" -> idle(machine.wakePreferred)
                "provider.provider_error", "session.connection_failed" -> failLive(machine.epoch)
                "brain.tool.result" -> {
                    nl.sander.aicockpit.jarvis.live.AcceptanceAudioInput.observeAdmission(
                        event.optJSONObject("payload")?.optJSONObject("output") ?: JSONObject(), event)
                    live?.observeAcceptanceTransport()
                }
                "provider.assistant_transcript", "provider.assistant_response" -> live?.observeAcceptanceTransport()
            }
        }
    } }

    override fun onCreate() {
        super.onCreate()
        store = CredentialStore(this)
        machine = AudioControlMachine(!store.sleep)
        previousRelease = JarvisAudioControls.previousRelease
        JarvisAudioControls.owner = this
        application.eventStream.addListener(eventListener)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (store.credential() == null) { shutdown(); return START_NOT_STICKY }
        when (intent?.action) {
            AudioAction.STOP.wire -> {
                if (machine.permitsStop(intent.getStringExtra("activation_id"), intent.getStringExtra("session_id"),
                    intent.getIntExtra("generation", 0).takeIf { it > 0 })) pause()
                else if (live == null && wakeJob == null) stopSelf()
            }
            AudioAction.LISTEN.wire -> listen(intent.getStringExtra("activation_id") ?: UUID.randomUUID().toString(),
                intent.takeIf { it.hasExtra("wake_score") }?.getFloatExtra("wake_score", 0f),
                intent.getBooleanExtra("assistant_visible", false))
            else -> {
                if (!machine.state.liveActive && machine.state.mode !in setOf(AudioMode.WAITING, AudioMode.WAKE_STARTING))
                    idle(!store.sleep || intent?.action == AudioAction.WAKE.wire)
                else if (intent?.action == AudioAction.WAKE.wire && machine.state.liveActive) idle(true)
            }
        }
        return START_NOT_STICKY // Reboot/assistant readiness may rearm wake; never restart a Live call.
    }

    private fun promote(): Boolean {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            error("Microphone permission needed"); return false
        }
        if (foreground) return true
        return try {
            ServiceCompat.startForeground(this, JarvisNotifications.CONTROL_NOTIFICATION_ID,
                JarvisNotifications.audioControls(this, machine.state), ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
            foreground = true
            true
        } catch (_: RuntimeException) { error("Microphone permission needed"); false }
    }

    private fun publish() {
        JarvisAudioControls.state.value = machine.state
        JarvisAudioControls.showIdle(this)
        Log.i("JarvisAudio", "mode=${machine.state.mode.name.lowercase()} activation=${machine.state.activationId}")
    }

    private fun error(message: String) {
        machine.fail(machine.epoch, message)
        publish()
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_DETACH)
        stopSelf()
    }

    private suspend fun releaseAudio() {
        previousRelease?.await(); previousRelease = null
        val previous = connectionJob
        previous?.cancel()
        live?.stopAudio()
        wakeJob?.cancelAndJoin(); wakeJob = null
        previous?.join(); connectionJob = null
        live?.let { it.disconnect(); it.close() }
        live = null
    }

    private fun listen(activationId: String, wakeScore: Float?, assistantVisible: Boolean) {
        if (!machine.listen(activationId)) return
        val token = machine.epoch
        publish()
        if (!promote()) return
        scope.launch {
            transitionLock.withLock {
                releaseAudio()
                if (token != machine.epoch) return@withLock
                if (!assistantVisible) JarvisVoiceInteractionService.invokeListening(activationId)
                val api = CockpitApi(store)
                val controller = application.liveControllerFactory(this@JarvisAudioService, api)
                live = controller
                connectionJob = scope.launch {
                    try {
                        runCatching { api.updateDevice(if (getSystemService(KeyguardManager::class.java).isDeviceLocked) "locked" else "unlocked") }
                        val call = controller.connect(wakeScore, activationId) { value ->
                            scope.launch {
                                if (token == machine.epoch) {
                                    controller.currentCall()?.let {
                                        machine.accepted(token, it.sessionId, it.generation)
                                        application.screenCapture.bindSession(activationId, it.sessionId, it.generation)
                                        publish()
                                    }
                                    if (value == "session.closed") idle(machine.wakePreferred)
                                    else if (value in setOf("ice:FAILED", "ice:DISCONNECTED", "peer:FAILED", "peer:DISCONNECTED", "data:CLOSED")) failLive(token)
                                }
                            }
                        }
                        if (token == machine.epoch) {
                            machine.accepted(token, call.sessionId, call.generation)
                            machine.ready(token)
                            application.screenCapture.bindSession(activationId, call.sessionId, call.generation)
                            publish()
                        }
                    } catch (failure: Throwable) {
                        if (failure is CancellationException) throw failure
                        Log.e("JarvisAudio", "Live connection failed", failure)
                        if (token == machine.epoch) failLive(token)
                    }
                }
            }
        }
    }

    private fun failLive(token: Long) {
        if (token != machine.epoch) return
        machine.fail(token, "Unable to connect"); publish()
        live?.stopAudio()
        scope.launch { transitionLock.withLock {
            releaseAudio()
            if (token == machine.epoch) {
                ServiceCompat.stopForeground(this@JarvisAudioService, ServiceCompat.STOP_FOREGROUND_DETACH)
                stopSelf()
            }
        } }
    }

    internal fun pause() = idle(false)

    private fun idle(wake: Boolean) {
        store.sleep = !wake
        machine.idle(wake)
        val token = machine.epoch
        connectionJob?.cancel()
        live?.stopAudio()
        wakeJob?.cancel()
        if (!wake) machine.stopping(token)
        publish()
        if (wake && !promote()) return
        scope.launch { transitionLock.withLock {
            releaseAudio()
            if (token != machine.epoch) return@withLock
            if (!wake) {
                machine.paused(token); publish()
                ServiceCompat.stopForeground(this@JarvisAudioService, ServiceCompat.STOP_FOREGROUND_DETACH)
                stopSelf(); return@withLock
            }
            wakeJob = scope.launch(Dispatchers.Default) {
                try {
                    WakeMicrophone(this@JarvisAudioService, onState = { value -> scope.launch {
                        if (token == machine.epoch) {
                            if (value == "listening") { machine.waiting(token); publish() }
                            else if (value == "unavailable") error("Microphone permission needed")
                        }
                    } }, onWake = { score, activation ->
                        ContextCompat.startForegroundService(this@JarvisAudioService,
                            JarvisAudioControls.intent(this@JarvisAudioService, AudioAction.LISTEN,
                                AudioControlState(activationId = activation)).putExtra("wake_score", score))
                        true
                    }).run()
                } catch (failure: Throwable) {
                    if (failure is CancellationException) throw failure
                    scope.launch { if (token == machine.epoch) error("Microphone unavailable") }
                }
            }
        } }
    }

    internal fun currentCall() = live?.currentCall()
    internal fun audioMetrics() = live?.audioMetrics()
    internal fun observeHealth() = live?.observeAcceptanceTransport(checkpoint = true)
    internal fun continueAudio(): Boolean = live?.observeAcceptanceContinuation() == true
    internal fun shutdown() {
        removing = true
        machine.idle(false)
        connectionJob?.cancel(); wakeJob?.cancel(); live?.stopAudio()
        stopSelf()
    }
    override fun onDestroy() {
        if (JarvisAudioControls.owner === this) JarvisAudioControls.owner = null
        application.eventStream.removeListener(eventListener)
        live?.stopAudio()
        wakeJob?.cancel(); connectionJob?.cancel()
        val controller = live
        val released = CompletableDeferred<Unit>()
        JarvisAudioControls.previousRelease = released
        scope.launch { withContext(NonCancellable) {
            try {
                previousRelease?.await()
                wakeJob?.join(); connectionJob?.join()
                controller?.disconnect(); controller?.close()
            } finally { released.complete(Unit); scope.cancel() }
        } }
        if (removing) getSystemService(android.app.NotificationManager::class.java).cancel(JarvisNotifications.CONTROL_NOTIFICATION_ID)
        super.onDestroy()
    }
    override fun onBind(intent: Intent?): IBinder? = null
}
