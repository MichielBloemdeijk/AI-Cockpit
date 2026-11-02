package nl.sander.aicockpit.jarvis.live

import android.content.Context
import android.content.pm.ApplicationInfo
import android.app.ActivityManager
import android.app.ApplicationExitInfo
import android.media.AudioFormat
import android.media.AudioTrack
import android.os.Handler
import android.os.HandlerThread
import android.os.SystemClock
import android.util.Log
import android.view.FrameMetrics
import android.view.Window
import org.json.JSONObject
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** Keep receipt evidence below logcat's per-entry limit; full spans live on the server. */
internal fun admissionTimingSummary(timing: JSONObject?): JSONObject? = timing?.let { source ->
    JSONObject().also { summary ->
        for (key in listOf("clock", "clock_source", "intent_id", "commit_is_original", "shell_started_ns",
            "authority_ready_ns", "gateway_started_ns", "capture_ready_ns", "admission_commit_ns",
            "gateway_finished_ns", "gateway_duration_ms")) {
            if (source.has(key)) summary.put(key, source.get(key))
        }
    }
}

/** Input seam before production wake inference and WebRTC RTP. Installation is debug only. */
object AcceptanceAudioInput {
    private var samples: ShortArray? = null
    private var cursor = 0
    private var commandEnd = 0
    private var sampleId = ""
    private var commandLastSampleNs: Long? = null
    private var playbackObserved = false
    private var holdUntilReady = false
    private var commandStart = 0
    private var ready = false
    private var installedAtMs = 0L
    private var frameWindowStartNs = 0L
    private const val FRAME_WINDOW_NS = 20_000_000_000L
    private var metricsWindow: Window? = null
    private var metricsThread: HandlerThread? = null
    private val frames = AcceptanceFrameMetrics()
    private val frameListener = Window.OnFrameMetricsAvailableListener { window, metrics, dropped ->
        synchronized(this) {
            if (samples != null && window === metricsWindow && SystemClock.elapsedRealtimeNanos() < frameWindowStartNs + FRAME_WINDOW_NS) {
                frames.add(metrics.getMetric(FrameMetrics.TOTAL_DURATION),
                    metrics.getMetric(FrameMetrics.DEADLINE), dropped, metrics.getMetric(FrameMetrics.FIRST_DRAW_FRAME))
            }
        }
    }

    @Synchronized
    fun install(context: Context, id: String, pcm: ShortArray, commandEndSample: Int,
        commandStartSample: Int = 0, readyPath: Boolean = false) {
        check(context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0)
        require(id.matches(Regex("[a-zA-Z0-9_-]{1,80}")))
        require(pcm.size in 1..(16_000 * 60) && commandEndSample in 1..pcm.size)
        clear()
        samples = pcm.copyOf()
        commandEnd = commandEndSample
        require(commandStartSample in 0 until commandEndSample)
        commandStart = commandStartSample; holdUntilReady = readyPath
        sampleId = id
        installedAtMs = System.currentTimeMillis()
        frameWindowStartNs = SystemClock.elapsedRealtimeNanos()
        observe("installed", JSONObject().put("sample_count", pcm.size))
    }

    /** AudioRecord already clocks these frames. Do not skip the microphone read. */
    @Synchronized
    fun replaceWakeFrame(frame: ShortArray, count: Int) {
        if (samples == null) {
            if (AcceptanceAudioStream.active) AcceptanceAudioStream.fill(frame, count)
            return
        }
        fill(frame, count)
    }

    @Synchronized
    fun replaceWebRtcFrame(buffer: ByteBuffer, audioFormat: Int, channels: Int, rate: Int, count: Int) {
        val streaming = samples == null && AcceptanceAudioStream.active
        if (samples == null && !streaming) return
        check(audioFormat == AudioFormat.ENCODING_PCM_16BIT && channels == 1 && rate == 16_000)
        val shorts = ShortArray(count / 2)
        if (streaming) AcceptanceAudioStream.fill(shorts, shorts.size) else fill(shorts, shorts.size)
        val view = buffer.duplicate().order(ByteOrder.LITTLE_ENDIAN)
        view.position(0)
        shorts.forEach(view::putShort)
        shorts.fill(0)
    }

    private fun fill(frame: ShortArray, count: Int) {
        val source = samples ?: return
        val before = cursor
        repeat(count) { frame[it] = if (holdUntilReady && !ready && cursor >= commandStart) 0
            else if (cursor < source.size) source[cursor++] else 0 }
        if (before < commandEnd && cursor >= commandEnd && commandLastSampleNs == null) {
            // Last-command sample may be within this 10/80 ms frame. Subtract its trailing samples.
            commandLastSampleNs = SystemClock.elapsedRealtimeNanos() - (cursor - commandEnd) * 1_000_000_000L / 16_000
            observe("command_last_input_sample", JSONObject().put("command_last_sample_ns", commandLastSampleNs))
        }
    }

    @Synchronized fun observeReady(sessionId: String, generation: Int) {
        ready = true
        if (samples != null) observe("live_ready", JSONObject().put("session_id", sessionId).put("generation", generation))
    }

    @Synchronized
    fun observePlayback(bytes: ByteArray) {
        if (samples == null || playbackObserved || bytes.none { it != 0.toByte() }) return
        playbackObserved = true
        observe("assistant_playback", JSONObject().put("byte_count", bytes.size))
    }

    @Synchronized fun observeRtp(bytes: Long, packets: Long) {
        if (samples != null) observe("outbound_rtp", JSONObject().put("bytes_sent", bytes).put("packets_sent", packets))
    }

    @Synchronized fun observeCapture(stage: String, captureId: String, detail: JSONObject = JSONObject()) {
        if (samples != null) observe("capture_$stage", detail.put("capture_id", captureId))
    }

    @Synchronized fun observeDiagnostic(stage: String, detail: JSONObject = JSONObject()) {
        if (samples != null) observe("live_$stage", detail)
    }

    /** Enabled only while an explicitly installed debug audio sample exists. */
    @Synchronized fun observeWindow(window: Window?) {
        if (samples == null || window == null || window === metricsWindow) return
        releaseWindow()
        frames.reset()
        val thread = HandlerThread("jarvis-acceptance-frames").apply { start() }
        metricsThread = thread
        metricsWindow = window
        window.addOnFrameMetricsAvailableListener(frameListener, Handler(thread.looper))
    }

    fun observeHealth(context: Context, audioModule: Any, checkpoint: Boolean = false) {
        if (checkpoint) {
            val remaining = synchronized(this) { frameWindowStartNs + FRAME_WINDOW_NS - SystemClock.elapsedRealtimeNanos() }
            if (remaining > 0) {
                val expectedSample = synchronized(this) { sampleId }
                Handler(android.os.Looper.getMainLooper()).postDelayed({
                    if (synchronized(this) { samples != null && sampleId == expectedSample })
                        observeHealth(context, audioModule, true)
                }, (remaining / 1_000_000) + 1)
                return
            }
        }
        val snapshot = synchronized(this) {
            if (samples == null) return
            Triple(sampleId, installedAtMs, JSONObject().put("frame_window_start_ns", frameWindowStartNs)
                .put("frame_window_end_ns", frameWindowStartNs + FRAME_WINDOW_NS).put("checkpoint", checkpoint).put("frame_count", frames.count).put("janky_frames", frames.janky)
                .put("first_draw_frames", frames.firstDraws).put("first_draw_overruns", frames.firstDrawOverruns)
                .put("dropped_frame_observations", frames.dropped).put("invalid_frame_observations", frames.invalid))
        }
        val detail = snapshot.third
        // This is a pinned third-party implementation field, not an Android
        // hidden API. Unsupported/missing output stays unknown, never zero.
        runCatching {
            val output = audioModule.javaClass.getField("audioOutput").get(audioModule)
            val field = output.javaClass.getDeclaredField("audioTrack").apply { isAccessible = true }
            val track = field.get(output) as? AudioTrack ?: error("No active playout track")
            detail.put("audio_underruns", track.underrunCount)
        }.onFailure { detail.put("audio_counter_error", it.javaClass.simpleName) }
        runCatching {
            val manager = context.getSystemService(ActivityManager::class.java)
            val exits = manager.getHistoricalProcessExitReasons(context.packageName, 0, 0)
            val prior = exits.count { it.timestamp >= snapshot.second && it.reason == ApplicationExitInfo.REASON_ANR }
            val current = manager.processesInErrorState.orEmpty().count {
                it.processName == context.packageName && it.condition == ActivityManager.ProcessErrorStateInfo.NOT_RESPONDING
            }
            detail.put("anr_exits", prior).put("current_anrs", current)
        }.onFailure { detail.put("anr_counter_error", it.javaClass.simpleName) }
        synchronized(this) {
            if (samples != null && sampleId == snapshot.first && installedAtMs == snapshot.second) {
                observe("runtime_health", detail)
            }
        }
    }

    private fun releaseWindow() {
        metricsWindow?.removeOnFrameMetricsAvailableListener(frameListener)
        metricsWindow = null
        metricsThread?.quitSafely(); metricsThread = null
    }

    @Synchronized fun stopObservingWindow(window: Window?) {
        if (window === metricsWindow) releaseWindow()
    }

    @Synchronized
    fun observeAdmission(output: JSONObject, event: JSONObject = JSONObject()) {
        if (samples == null || !output.optBoolean("accepted") || !output.has("task_id") ||
            !output.has("turn_id") || !output.has("execution_run_id")) return
        val handled = SystemClock.elapsedRealtimeNanos()
        val received = event.optLong("client_stream_received_ns", handled)
        observe("durable_admission", JSONObject().put("task_id", output.getString("task_id"))
            .put("turn_id", output.getString("turn_id")).put("execution_run_id", output.getString("execution_run_id"))
            .put("command_last_sample_ns", commandLastSampleNs).put("client_received_ns", received)
            .put("client_dispatch_ms", (handled - received) / 1_000_000.0)
            .put("session_id", event.optJSONObject("payload")?.optString("session_id"))
            .put("generation", event.optJSONObject("payload")?.optInt("generation"))
            .put("server_admission_timing", admissionTimingSummary(output.optJSONObject("admission_timing")))
            .put("server_delivery_timing", event.optJSONObject("delivery_timing"))
            .put("latency_ms", commandLastSampleNs?.let { (received - it) / 1_000_000.0 }))
    }

    private fun observe(event: String, detail: JSONObject = JSONObject()) {
        Log.i("JarvisAudioAcceptance", detail.put("event", event).put("sample_id", sampleId)
            .put("clock", "android_elapsed_realtime_nanos").put("observed_ns", SystemClock.elapsedRealtimeNanos()).toString())
    }

    @Synchronized
    fun clear() {
        samples?.fill(0); samples = null; cursor = 0; commandEnd = 0
        commandLastSampleNs = null; playbackObserved = false; sampleId = ""
        holdUntilReady = false; commandStart = 0; ready = false
        installedAtMs = 0
        releaseWindow()
        frames.reset()
    }
}
