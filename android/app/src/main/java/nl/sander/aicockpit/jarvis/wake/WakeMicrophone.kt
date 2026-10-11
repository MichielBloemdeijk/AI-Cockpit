package nl.sander.aicockpit.jarvis.wake

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import androidx.core.app.ActivityCompat
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.isActive
import nl.sander.aicockpit.jarvis.live.LiveActivationAudioHandoff
import java.util.UUID

/** The audio service owns this job; completion includes recorder release. */
internal class WakeMicrophone(private val context: Context,
    private val onState: (String) -> Unit,
    private val onWake: (Float, String) -> Boolean) {
    suspend fun run() {
        if (ActivityCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            onState("unavailable"); return
        }
        val minimum = AudioRecord.getMinBufferSize(
            OnnxWakeEngine.SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
        )
        val recorder = try { AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            OnnxWakeEngine.SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            maxOf(minimum, OnnxWakeEngine.WINDOW_SAMPLES * 2),
        ).also { check(it.state == AudioRecord.STATE_INITIALIZED) { "Microphone could not initialize" } }
        } catch (failure: Exception) {
            onState("unavailable")
            Log.e("JarvisMicrophone", "Native microphone initialization failed: ${failure.javaClass.simpleName}")
            return
        }
        val ring = FloatArray(OnnxWakeEngine.WINDOW_SAMPLES)
        val gate = WakeCandidateGate()
        var ringOffset = 0
        var collected = 0
        val pcm = ShortArray(OnnxWakeEngine.STRIDE_SAMPLES)
        var liveActivationId: String? = null
        try {
            OnnxWakeEngine(context).use { engine ->
                recorder.startRecording()
                check(recorder.recordingState == AudioRecord.RECORDSTATE_RECORDING) { "Microphone did not start" }
                onState("listening")
                Log.i("JarvisMicrophone", "Native wake microphone active")
                while (
                    currentCoroutineContext().isActive &&
                    recorder.recordingState == AudioRecord.RECORDSTATE_RECORDING
                ) {
                    val count = recorder.read(pcm, 0, pcm.size, AudioRecord.READ_BLOCKING)
                    if (count <= 0) continue
                    nl.sander.aicockpit.jarvis.live.AcceptanceAudioInput.replaceWakeFrame(pcm, count)
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
                        nl.sander.aicockpit.jarvis.live.AcceptanceAudioInput.observeDiagnostic("wake_detected",
                            org.json.JSONObject().put("activation_id", activationId))
                        val startup = ShortArray(window.size) { index ->
                            (window[index].coerceIn(-1f, 1f) * Short.MAX_VALUE).toInt().toShort()
                        }
                        if (!LiveActivationAudioHandoff.begin(activationId, startup)) {
                            startup.fill(0)
                            window.fill(0f)
                            break
                        }
                        startup.fill(0)
                        liveActivationId = activationId
                        if (onWake(acceptedWakeScore, activationId)) {
                            window.fill(0f)
                            liveActivationId = activationId
                            continue
                        }
                        LiveActivationAudioHandoff.clear(activationId)
                        liveActivationId = null
                    }
                    window.fill(0f)
                }
            }
        } catch (failure: Exception) {
            if (failure is kotlinx.coroutines.CancellationException) throw failure
            onState("unavailable")
            Log.e("JarvisMicrophone", "Native wake microphone failed: ${failure.javaClass.simpleName}")
        } finally {
            runCatching { recorder.stop() }
            liveActivationId?.let(LiveActivationAudioHandoff::markRecorderReleased)
            recorder.release(); pcm.fill(0); ring.fill(0f)
            Log.i("JarvisMicrophone", "Native wake microphone released")


        }
    }

}
