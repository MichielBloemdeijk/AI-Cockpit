package nl.sander.aicockpit.jarvis.wake

import android.Manifest
import android.app.Service
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.IBinder
import androidx.core.app.ActivityCompat
import androidx.core.app.ServiceCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import nl.sander.aicockpit.jarvis.assistant.JarvisVoiceInteractionService
import nl.sander.aicockpit.jarvis.data.CredentialStore
import nl.sander.aicockpit.jarvis.notifications.JarvisNotifications
import nl.sander.aicockpit.jarvis.live.LiveActivationAudioHandoff
import java.util.UUID

class WakeForegroundService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var capture: Job? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP || CredentialStore(this).sleep) {
            stopSelf(); return START_NOT_STICKY
        }
        ServiceCompat.startForeground(
            this, JarvisNotifications.WAKE_NOTIFICATION_ID,
            JarvisNotifications.wakeListening(this),
            android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE,
        )
        if (capture == null) capture = scope.launch { capture() }
        return START_STICKY
    }

    private suspend fun capture() {
        if (ActivityCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            stopSelf(); return
        }
        val minimum = AudioRecord.getMinBufferSize(
            OnnxWakeEngine.SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
        )
        val recorder = AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            OnnxWakeEngine.SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            maxOf(minimum, OnnxWakeEngine.WINDOW_SAMPLES * 2),
        )
        val ring = FloatArray(OnnxWakeEngine.WINDOW_SAMPLES)
        val gate = WakeCandidateGate()
        var ringOffset = 0
        var collected = 0
        val pcm = ShortArray(OnnxWakeEngine.STRIDE_SAMPLES)
        var liveActivationId: String? = null
        try {
            OnnxWakeEngine(this).use { engine ->
                recorder.startRecording()
                while (
                    currentCoroutineContext().isActive &&
                    recorder.recordingState == AudioRecord.RECORDSTATE_RECORDING
                ) {
                    val count = recorder.read(pcm, 0, pcm.size, AudioRecord.READ_BLOCKING)
                    if (count <= 0) continue
                    val activeHandoff = liveActivationId
                    if (activeHandoff != null) {
                        val retained = LiveActivationAudioHandoff.append(activeHandoff, pcm, count)
                        if (!retained || LiveActivationAudioHandoff.shouldReleaseRecorder(activeHandoff)) {
                            return@use
                        }
                        continue
                    }
                    repeat(count) { index ->
                        ring[ringOffset] = pcm[index] / 32768f
                        ringOffset = (ringOffset + 1) % ring.size
                    }
                    collected = (collected + count).coerceAtMost(ring.size)
                    if (collected < ring.size) continue
                    val window = FloatArray(ring.size)
                    ring.copyInto(window, 0, ringOffset, ring.size)
                    ring.copyInto(window, ring.size - ringOffset, 0, ringOffset)
                    val result = engine.analyze(window)
                    val acceptedWakeScore = gate.evaluate(result)
                    if (acceptedWakeScore != null) {
                        val activationId = UUID.randomUUID().toString()
                        val startup = ShortArray(window.size) { index ->
                            (window[index].coerceIn(-1f, 1f) * Short.MAX_VALUE).toInt().toShort()
                        }
                        if (!LiveActivationAudioHandoff.begin(activationId, startup)) {
                            startup.fill(0)
                            window.fill(0f)
                            break
                        }
                        startup.fill(0)
                        if (JarvisVoiceInteractionService.invokeFromWake(acceptedWakeScore, activationId)) {
                            window.fill(0f)
                            liveActivationId = activationId
                            continue
                        }
                        LiveActivationAudioHandoff.clear(activationId)
                    }
                    window.fill(0f)
                }
            }
        } finally {
            runCatching { recorder.stop() }
            liveActivationId?.let(LiveActivationAudioHandoff::markRecorderReleased)
            recorder.release(); pcm.fill(0); ring.fill(0f)
            stopSelf()
        }
    }

    override fun onDestroy() {
        capture?.cancel(); capture = null; scope.cancel(); super.onDestroy()
    }
    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        const val ACTION_START = "nl.sander.aicockpit.jarvis.wake.START"
        const val ACTION_STOP = "nl.sander.aicockpit.jarvis.wake.STOP"
    }
}
