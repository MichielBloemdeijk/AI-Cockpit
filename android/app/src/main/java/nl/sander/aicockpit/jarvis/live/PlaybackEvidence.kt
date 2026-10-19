package nl.sander.aicockpit.jarvis.live

import android.content.Context
import android.media.AudioManager
import android.media.AudioTrack
import android.os.SystemClock
import kotlinx.coroutines.*
import nl.sander.aicockpit.jarvis.data.CockpitApi
import nl.sander.aicockpit.jarvis.data.LiveCall
import org.json.JSONObject

/** Production metadata only; never retains samples or claims physical hearing. */
class PlaybackEvidence(private val context: Context, private val api: CockpitApi, private val module: Any,
    private val statistics: suspend () -> org.webrtc.RTCStatsReport?) {
    private var job: Job? = null
    @Volatile var originLower: Long? = null
    @Volatile var originUpper: Long? = null
    @Volatile var lastCallback = 0L

    fun start(scope: CoroutineScope, call: LiveCall) {
        job?.cancel()
        job = scope.launch(Dispatchers.IO) {
            var previousAt = 0L; var previousHead = 0L; var previousTrack = 0; var sequence = 0L
            var previousReceived = -1L; var previousConcealed = -1L
            var previousInputId: String? = null
            var previousInputEnergy = 0.0; var previousInputDuration = 0.0
            var lost = false
            val manager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
            while (isActive) {
                delay(250)
                val at = SystemClock.elapsedRealtime()
                val track = runCatching {
                    val output = module.javaClass.getField("audioOutput").get(module)
                    val field = output.javaClass.getDeclaredField("audioTrack").apply { isAccessible = true }
                    field.get(output) as AudioTrack
                }.getOrNull()
                if (track == null) { previousAt = 0; continue }
                val head = track.playbackHeadPosition.toLong() and 0xffffffffL
                val identity = System.identityHashCode(track)
                val volume = manager.getStreamVolume(AudioManager.STREAM_VOICE_CALL).toDouble() /
                    manager.getStreamMaxVolume(AudioManager.STREAM_VOICE_CALL).coerceAtLeast(1)
                val lower = originLower; val upper = originUpper
                val stats = statistics()?.statsMap?.values.orEmpty()
                val input = stats.filter { it.type == "media-source" && it.members["kind"] == "audio" &&
                    it.members["totalAudioEnergy"] is Number && it.members["totalSamplesDuration"] is Number }.singleOrNull()
                val inputEnergy = (input?.members?.get("totalAudioEnergy") as? Number)?.toDouble()
                val inputDuration = (input?.members?.get("totalSamplesDuration") as? Number)?.toDouble()
                val inputMeasured = input != null && input.id == previousInputId &&
                    inputEnergy != null && inputDuration != null && inputEnergy.isFinite() && inputDuration.isFinite() &&
                    inputEnergy >= previousInputEnergy && inputDuration > previousInputDuration
                val audio = stats.filter { it.type == "inbound-rtp" &&
                    (it.members["kind"] == "audio" || it.members["mediaType"] == "audio") }.orEmpty()
                val supported = audio.isNotEmpty() && audio.all { it.members["totalSamplesReceived"] is Number &&
                    it.members["concealedSamples"] is Number }
                val received = audio.sumOf { (it.members["totalSamplesReceived"] as? Number)?.toLong() ?: 0 }
                val concealed = audio.sumOf { (it.members["concealedSamples"] as? Number)?.toLong() ?: 0 }
                val receivedAudio = supported && previousReceived >= 0 && received > previousReceived &&
                    concealed >= previousConcealed && previousTrack == identity
                val concealedFraction = if (receivedAudio) ((concealed - previousConcealed).toDouble() /
                    (received - previousReceived)).coerceIn(0.0, 1.0) else 1.0
                if (previousAt != 0L) {
                    val report = JSONObject().put("track_id", "android-$identity")
                        .put("client_start_ms", previousAt).put("client_end_ms", at)
                        .put("playback_start_ms", previousHead * 1000.0 / track.sampleRate)
                        .put("playback_end_ms", head * 1000.0 / track.sampleRate)
                        .put("origin_lower_ms", lower).put("origin_upper_ms", upper)
                        .put("mapping_supported", lower != null && upper != null && upper >= lower)
                        .put("paused", track.playState != AudioTrack.PLAYSTATE_PLAYING)
                        .put("muted", volume == 0.0).put("volume", volume)
                        .put("track_live", at - lastCallback < 600)
                        .put("received_audio", receivedAudio)
                        .put("concealed_fraction", concealedFraction)
                        .put("input_source_id", if (inputMeasured) input.id else JSONObject.NULL)
                        .put("input_audio_energy", if (inputMeasured) inputEnergy - previousInputEnergy else JSONObject.NULL)
                        .put("input_audio_duration_ms", if (inputMeasured) (inputDuration - previousInputDuration) * 1000 else JSONObject.NULL)
                        .put("gap", lost || at - previousAt > 600 || previousTrack != identity || head < previousHead)
                    lost = runCatching { api.submitPlayback(call, ++sequence, report) }.isFailure
                }
                previousAt = at; previousHead = head; previousTrack = identity
                previousReceived = if (supported) received else -1; previousConcealed = concealed
                previousInputId = input?.id
                previousInputEnergy = inputEnergy ?: 0.0; previousInputDuration = inputDuration ?: 0.0
            }
        }
    }

    fun stop() { job?.cancel(); job = null; originLower = null; originUpper = null }
}
